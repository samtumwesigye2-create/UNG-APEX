from scm_runtime import router as scm_runtime_router
from app import app, shipments
from nova_kpis import install_nova_kpi_routes

install_nova_kpi_routes(app, shipments)

app.include_router(scm_runtime_router)
