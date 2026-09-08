from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from uuid import uuid4

app = FastAPI(title='UNG-APEX', version='0.1.0')

MODES = {'road','rail','ocean','air'}
DOC_BY_MODE = {'road':'e-CMR','rail':'e-CMR','ocean':'e-BL','air':'e-AWB'}
EMISSIONS_KG_CO2E_PER_TON_KM = {'road':0.062,'rail':0.022,'ocean':0.010,'air':0.602}
COST_USD_PER_TON_KM = {'road':0.11,'rail':0.06,'ocean':0.035,'air':0.82}

shipments = {}


def now():
    return datetime.now(timezone.utc)


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


def normalize_mode(mode: str) -> str:
    mode = mode.strip().lower()
    if mode not in MODES:
        raise HTTPException(422, f'invalid_mode:{mode}')
    return mode


def enrich_leg(leg: LegIn | dict):
    data = leg.model_dump() if hasattr(leg, 'model_dump') else dict(leg)
    mode = normalize_mode(data['mode'])
    ton_km = float(data['distance_km']) * float(data['cargo_tons'])
    return {
        **data,
        'mode': mode,
        'document': DOC_BY_MODE[mode],
        'estimated_cost_usd': round(ton_km * COST_USD_PER_TON_KM[mode], 2),
        'estimated_co2e_kg': round(ton_km * EMISSIONS_KG_CO2E_PER_TON_KM[mode], 2),
        'status': data.get('status', 'planned'),
        'disrupted': data.get('disrupted', False),
        'disruption_reason': data.get('disruption_reason'),
    }


@app.get('/')
def root():
    return {'system':'UNG-APEX','status':'online','version':'0.1.0'}


@app.get('/health')
def health():
    return {'status':'ok','service':'UNG-APEX','version':'0.1.0'}


@app.get('/v1/system')
def system():
    return {'system_id':'UNG-APEX','domain':'route-load-optimization','capabilities':['multimodal-legs','transport-documents','disruption-management','rerouting','cost-comparison','emissions-comparison']}


@app.post('/v1/shipments', status_code=201)
def create_shipment(body: ShipmentIn):
    if not body.legs:
        raise HTTPException(422, 'at_least_one_leg_required')
    sid = str(uuid4())
    legs = [enrich_leg(x) for x in body.legs]
    row = {'id':sid,'reference':body.reference,'cargo':body.cargo,'legs':legs,'created_at':now(),'updated_at':now()}
    shipments[sid] = row
    return summarize(row)


def summarize(row: dict):
    total_cost = round(sum(x['estimated_cost_usd'] for x in row['legs']), 2)
    total_co2 = round(sum(x['estimated_co2e_kg'] for x in row['legs']), 2)
    return {**row,'total_estimated_cost_usd':total_cost,'total_estimated_co2e_kg':total_co2,'has_disruption':any(x['disrupted'] for x in row['legs'])}


@app.get('/v1/shipments')
def list_shipments():
    return [summarize(x) for x in shipments.values()]


@app.get('/v1/shipments/{shipment_id}')
def get_shipment(shipment_id: str):
    row = shipments.get(shipment_id)
    if not row: raise HTTPException(404, 'shipment_not_found')
    return summarize(row)


@app.post('/v1/shipments/{shipment_id}/disrupt')
def disrupt(shipment_id: str, body: DisruptionIn):
    row = shipments.get(shipment_id)
    if not row: raise HTTPException(404, 'shipment_not_found')
    if body.leg_index >= len(row['legs']): raise HTTPException(404, 'leg_not_found')
    leg = row['legs'][body.leg_index]
    leg['disrupted'] = True
    leg['status'] = 'disrupted'
    leg['disruption_reason'] = body.reason
    row['updated_at'] = now()
    return summarize(row)


@app.post('/v1/shipments/{shipment_id}/reroute')
def reroute(shipment_id: str, body: RerouteIn):
    row = shipments.get(shipment_id)
    if not row: raise HTTPException(404, 'shipment_not_found')
    if body.leg_index >= len(row['legs']): raise HTTPException(404, 'leg_not_found')
    old = row['legs'][body.leg_index]
    mode = normalize_mode(body.new_mode)
    candidate = {
        'mode': mode,
        'origin': body.new_origin or old['origin'],
        'destination': body.new_destination or old['destination'],
        'distance_km': body.new_distance_km or old['distance_km'],
        'cargo_tons': old['cargo_tons'],
        'status': 'rerouted',
        'disrupted': False,
        'disruption_reason': None,
    }
    row['legs'][body.leg_index] = enrich_leg(candidate)
    row['legs'][body.leg_index]['status'] = 'rerouted'
    row['updated_at'] = now()
    return {'shipment':summarize(row),'replaced_leg':old,'new_leg':row['legs'][body.leg_index]}


@app.post('/v1/compare')
def compare_modes(leg: LegIn):
    results = []
    for mode in sorted(MODES):
        candidate = leg.model_copy(update={'mode':mode})
        results.append(enrich_leg(candidate))
    return {'origin':leg.origin,'destination':leg.destination,'distance_km':leg.distance_km,'cargo_tons':leg.cargo_tons,'alternatives':results}
