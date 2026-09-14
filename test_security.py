import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from entrypoint import app

class SecurityTests(unittest.TestCase):
    def claims(self,permissions=(),roles=()):
        return {'active':True,'principal':{'id':'test-user','roles':list(roles),'permissions':list(permissions)}}
    def response(self,data):
        import io,json
        return io.BytesIO(json.dumps(data).encode())
    def test_read_scope_cannot_write(self):
        with patch('access_control.open_request',side_effect=lambda req:self.response(self.claims(['apex.read']))):
            client=TestClient(app);headers={'Authorization':'Bearer local-test-token'}
            self.assertEqual(client.get('/v1/shipments',headers=headers).status_code,200)
            self.assertEqual(client.post('/v1/shipments',headers=headers,json={}).status_code,403)
    def test_admin_can_create_shipment(self):
        with patch('access_control.open_request',side_effect=lambda req:self.response(self.claims(roles=['platform-admin']))):
            result=TestClient(app).post('/v1/shipments',headers={'Authorization':'Bearer local-test-token'},json={'reference':'test','cargo':'test cargo','legs':[{'mode':'road','origin':'AA','destination':'BB','distance_km':10,'cargo_tons':1}]})
            self.assertEqual(result.status_code,201)
    def test_revoked_session_denied(self):
        with patch('access_control.open_request',side_effect=[self.response(self.claims(['apex.read'])),self.response({'active':False})]):
            client=TestClient(app);headers={'Authorization':'Bearer local-test-token'}
            self.assertEqual(client.get('/v1/shipments',headers=headers).status_code,200)
            self.assertEqual(client.get('/v1/shipments',headers=headers).status_code,401)
    def test_outage_fails_closed(self):
        with patch('access_control.open_request',side_effect=OSError('offline')):
            self.assertEqual(TestClient(app).get('/v1/shipments',headers={'Authorization':'Bearer local-test-token'}).status_code,503)
    def test_unprivileged_identity_denied(self):
        with patch('access_control.open_request',return_value=self.response(self.claims())):
            self.assertEqual(TestClient(app).get('/v1/shipments',headers={'Authorization':'Bearer local-test-token'}).status_code,403)
    def test_operational_routes_require_login(self):
        client=TestClient(app)
        for path in ['/v1/shipments','/v1/system','/v1/kpis/supply-chain','/docs','/openapi.json','/api/status']:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code,401)
    def test_mutations_require_login(self):
        client=TestClient(app)
        for path in ['/v1/shipments','/v1/compare','/v1/routes/by-zip','/v1/kpis/supply-chain/publish']:
            self.assertEqual(client.post(path,json={}).status_code,401)
    def test_health_and_login_screen_are_public(self):
        client=TestClient(app)
        self.assertEqual(client.get('/health').status_code,200)
        self.assertEqual(client.get('/').status_code,200)
        self.assertIn('"auth": true',client.get('/').text)

if __name__=='__main__': unittest.main()
