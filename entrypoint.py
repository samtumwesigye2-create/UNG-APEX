from app import app, shipments
from nova_kpis import install_nova_kpi_routes

install_nova_kpi_routes(app, shipments)
