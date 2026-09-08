from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from uuid import uuid4
import json, os, urllib.error, urllib.parse, urllib.request

VERSION='0.2.0'
UGAMAP_BASE_URL=os.getenv('UGAMAP_BASE_URL','https://uganda-grid-api-clean-production.up.railway.app').rstrip('/')
app = FastAPI(title='UNG-APEX', version=VERSION)

MODES = {'road','rail','ocean','air'}
DOC_BY_MODE = {'road':'e-CMR','rail':'e-CMR','ocean':'e-BL','air':'e-AWB'}
EMISSIONS_KG_CO2E_PER_TON_KM = {'road':0.062,'rail':0.022,'ocean':0.010,'air':0.602}
COST_USD_PER_TON_KM = {'road':0.11,'rail':0.06,'ocean':0.035,'air':0.82}
shipments = {}

def now(): return datetime.now(timezone.utc)

class LegIn(BaseModel):
    mode: str
    origin: str = Field(min_length=2, max_length=120)
    destination: str = Field(min_length=2, max_length=120)
    distance_km: float = Field(gt=0, le=50000)
    cargo_tons: float = Field(gt=0, le=100000)

class ShipmentIn(BaseModel):
    reference: str = Field(min_length=3, max_length=80)
    cargo: str = Field(min_length=2, max_length=200)
    legs: list[LegIn]

class DisruptionIn(BaseModel):
    leg_index: int = Field(ge=0)
    reason: str = Field(min_length=2, max_length=300)

class RerouteIn(BaseModel):
    leg_index: int = Field(ge=0)
    new_mode: str
    new_origin: str | None = None
    new_destination: str | None = None
    new_distance_km: float | None = Field(default=None, gt=0, le=50000)

class ZipRouteIn(BaseModel):
    reference: str = Field(min_length=3,max_length=80)
    cargo: str = Field(min_length=2,max_length=200)
    origin_lat: float = Field(ge=-90,le=90)
    origin_lon: float = Field(ge=-180,le=180)
    destination_zip: str = Field(min_length=1,max_length=5)
    cargo_tons: float = Field(gt=0,le=100000)
    mode: str = 'road'


def normalize_mode(mode: str) -> str:
    mode = mode.strip().lower()
    if mode not in MODES: raise HTTPException(422, f'invalid_mode:{mode}')
    return mode

def enrich_leg(leg: LegIn | dict):
    data = leg.model_dump() if hasattr(leg, 'model_dump') else dict(leg)
    mode = normalize_mode(data['mode']); ton_km=float(data['distance_km'])*float(data['cargo_tons'])
    return {**data,'mode':mode,'document':DOC_BY_MODE[mode],'estimated_cost_usd':round(ton_km*COST_USD_PER_TON_KM[mode],2),'estimated_co2e_kg':round(ton_km*EMISSIONS_KG_CO2E_PER_TON_KM[mode],2),'status':data.get('status','planned'),'disrupted':data.get('disrupted',False),'disruption_reason':data.get('disruption_reason')}

def ugamap_route_to_zip(origin_lat:float,origin_lon:float,code:str):
    code=str(code or '').strip()
    if not code.isdigit(): raise HTTPException(422,'destination_zip_must_be_numeric')
    code=code.zfill(5)
    qs=urllib.parse.urlencode({'start_lat':origin_lat,'start_lon':origin_lon,'zip':code})
    req=urllib.request.Request(f'{UGAMAP_BASE_URL}/routing/to-zip?{qs}',headers={'Accept':'application/json','User-Agent':f'UNG-APEX/{VERSION}'})
    try:
        with urllib.request.urlopen(req,timeout=15) as r:return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:detail=json.loads(e.read().decode('utf-8'))
        except Exception:detail={'error':f'ugamap_http_{e.code}'}
        raise HTTPException(e.code if e.code<500 else 502,detail)
    except Exception as e: raise HTTPException(503,f'ugamap_unavailable:{type(e).__name__}')

def summarize(row: dict):
    total_cost=round(sum(x['estimated_cost_usd'] for x in row['legs']),2);total_co2=round(sum(x['estimated_co2e_kg'] for x in row['legs']),2)
    return {**row,'total_estimated_cost_usd':total_cost,'total_estimated_co2e_kg':total_co2,'has_disruption':any(x['disrupted'] for x in row['legs'])}

@app.get('/')
def root(): return {'system':'UNG-APEX','status':'online','version':VERSION}
@app.get('/health')
def health(): return {'status':'ok','service':'UNG-APEX','version':VERSION}
@app.get('/v1/system')
def system():
    return {'system_id':'UNG-APEX','domain':'route-load-optimization','version':VERSION,'ugamap':UGAMAP_BASE_URL,'capabilities':['multimodal-legs','transport-documents','disruption-management','rerouting','cost-comparison','emissions-comparison','zip-destination-resolution','ugamap-routing']}

@app.post('/v1/routes/by-zip',status_code=201)
def route_by_zip(body:ZipRouteIn):
    mode=normalize_mode(body.mode)
    if mode!='road': raise HTTPException(422,'ugamap_zip_routing_currently_requires_road_mode')
    result=ugamap_route_to_zip(body.origin_lat,body.origin_lon,body.destination_zip)
    if not result.get('ok'): raise HTTPException(502,'ugamap_route_failed')
    destination=result.get('destination') or {}; route=result.get('route') or {}; trip=route.get('trip') or {}; summary=trip.get('summary') or {}
    try:distance=float(summary.get('length'))
    except Exception:raise HTTPException(502,'ugamap_route_distance_missing')
    if distance<=0:raise HTTPException(502,'ugamap_route_distance_invalid')
    leg=enrich_leg({'mode':'road','origin':f'{body.origin_lat:.6f},{body.origin_lon:.6f}','destination':str(destination.get('code') or body.destination_zip).zfill(5),'distance_km':distance,'cargo_tons':body.cargo_tons,'status':'planned','disrupted':False,'disruption_reason':None})
    sid=str(uuid4());row={'id':sid,'reference':body.reference,'cargo':body.cargo,'legs':[leg],'created_at':now(),'updated_at':now(),'destination':destination,'routing_source':result.get('source','UGAMAP'),'ugamap_route':{'distance_km':distance,'duration_s':summary.get('time'),'status_message':trip.get('status_message')}}
    shipments[sid]=row
    return summarize(row)

@app.post('/v1/shipments', status_code=201)
def create_shipment(body: ShipmentIn):
    if not body.legs: raise HTTPException(422, 'at_least_one_leg_required')
    sid=str(uuid4());legs=[enrich_leg(x) for x in body.legs];row={'id':sid,'reference':body.reference,'cargo':body.cargo,'legs':legs,'created_at':now(),'updated_at':now()};shipments[sid]=row;return summarize(row)
@app.get('/v1/shipments')
def list_shipments(): return [summarize(x) for x in shipments.values()]
@app.get('/v1/shipments/{shipment_id}')
def get_shipment(shipment_id: str):
    row=shipments.get(shipment_id)
    if not row: raise HTTPException(404,'shipment_not_found')
    return summarize(row)
@app.post('/v1/shipments/{shipment_id}/disrupt')
def disrupt(shipment_id:str,body:DisruptionIn):
    row=shipments.get(shipment_id)
    if not row:raise HTTPException(404,'shipment_not_found')
    if body.leg_index>=len(row['legs']):raise HTTPException(404,'leg_not_found')
    leg=row['legs'][body.leg_index];leg['disrupted']=True;leg['status']='disrupted';leg['disruption_reason']=body.reason;row['updated_at']=now();return summarize(row)
@app.post('/v1/shipments/{shipment_id}/reroute')
def reroute(shipment_id:str,body:RerouteIn):
    row=shipments.get(shipment_id)
    if not row:raise HTTPException(404,'shipment_not_found')
    if body.leg_index>=len(row['legs']):raise HTTPException(404,'leg_not_found')
    old=row['legs'][body.leg_index];mode=normalize_mode(body.new_mode);candidate={'mode':mode,'origin':body.new_origin or old['origin'],'destination':body.new_destination or old['destination'],'distance_km':body.new_distance_km or old['distance_km'],'cargo_tons':old['cargo_tons'],'status':'rerouted','disrupted':False,'disruption_reason':None};row['legs'][body.leg_index]=enrich_leg(candidate);row['legs'][body.leg_index]['status']='rerouted';row['updated_at']=now();return {'shipment':summarize(row),'replaced_leg':old,'new_leg':row['legs'][body.leg_index]}
@app.post('/v1/compare')
def compare_modes(leg: LegIn):
    results=[]
    for mode in sorted(MODES):results.append(enrich_leg(leg.model_copy(update={'mode':mode})))
    return {'origin':leg.origin,'destination':leg.destination,'distance_km':leg.distance_km,'cargo_tons':leg.cargo_tons,'alternatives':results}
