# APEX login access

APEX requires a live JANUS bearer session on all operational APIs, including NOVA KPI routes and API documentation. Only the login screen and health endpoint are public. JANUS is contacted for every request; inactive sessions and authority outages fail closed. No tokens or passwords are persisted in the browser. Sign-out revokes the JANUS session.

Read operations require apex.read; mutations require apex.write. Existing platform-admin identities or ung.admin permission allow both. Integrations must send an authorized JANUS bearer token. Do not rely on client-supplied permission headers.

JANUS_BASE_URL defaults to https://ung-iam-production.up.railway.app and must use HTTPS. The configured shared JANUS administrator uses the Warehouse master authority. Other users retain their JANUS credentials.

Verification: python -m unittest test_security. Shipment persistence remains memory-based.
