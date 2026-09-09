from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from fastapi import HTTPException

NOVA_BASE_URL=os.getenv('NOVA_BASE_URL','https://ung-nova-production.up.railway.app').rstrip('/')


def now(): return datetime.now(timezone.utc)


def _snapshot(shipments:dict):
    rows=list(shipments.values())
    if not rows:
        return {'source_system':'UNG-APEX','shipments':0,'observations':[],'generated_at':now()}
    total_cost=0.0; total_co2=0.0; total_ton_km=0.0
    for row in rows:
        for leg in row.get('legs') or []:
            total_cost += float(leg.get('estimated_cost_usd') or 0)
            total_co2 += float(leg.get('estimated_co2e_kg') or 0)
            total_ton_km += float(leg.get('distance_km') or 0)*float(leg.get('cargo_tons') or 0)
    observations=[
        {'kpi_key':'logistics_cost_per_order','value':round(total_cost/len(rows),6),'entity_id':'enterprise','source_system':'UNG-APEX','measured_at':now().isoformat()},
    ]
    if total_ton_km>0:
        observations.append({'kpi_key':'transport_emissions_intensity','value':round(total_co2/total_ton_km,9),'entity_id':'enterprise','source_system':'UNG-APEX','measured_at':now().isoformat()})
    return {'source_system':'UNG-APEX','shipments':len(rows),'total_estimated_cost_usd':round(total_cost,2),'total_estimated_co2e_kg':round(total_co2,2),'total_ton_km':round(total_ton_km,3),'observations':observations,'generated_at':now()}


def _publish(snapshot:dict):
    observations=snapshot.get('observations') or []
    if not observations:return {'status':'no-data','inserted':0,'snapshot':snapshot}
    body=json.dumps({'observations':observations}).encode()
    req=urllib.request.Request(NOVA_BASE_URL+'/v1/supply-chain/observations/bulk',data=body,method='POST',headers={'Content-Type':'application/json','X-UNG-Permissions':'nova.datasets.write','User-Agent':'UNG-APEX/0.3.0'})
    try:
        with urllib.request.urlopen(req,timeout=8) as r:
            return {'status':'published','response_code':r.status,'nova':json.loads(r.read().decode() or '{}'),'snapshot':snapshot}
    except urllib.error.HTTPError as e:
        raise HTTPException(502,f'nova_http_{e.code}')
    except Exception as e:
        raise HTTPException(503,f'nova_unavailable:{type(e).__name__}')


def install_nova_kpi_routes(app,shipments):
    @app.get('/v1/kpis/supply-chain')
    def supply_chain_kpis(): return _snapshot(shipments)

    @app.post('/v1/kpis/supply-chain/publish')
    def publish_supply_chain_kpis(): return _publish(_snapshot(shipments))
