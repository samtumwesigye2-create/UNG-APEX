"""Live JANUS authorization for every APEX operational request."""
import json
import os
from urllib.request import Request
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from ui_portal import open_request

def principal(authorization):
    if not authorization.lower().startswith('bearer ') or not authorization[7:].strip():
        raise HTTPException(401, 'Sign in with UNG Identity')
    base=os.getenv('JANUS_BASE_URL','https://ung-iam-production.up.railway.app').rstrip('/')
    from urllib.parse import urlsplit
    url=urlsplit(base)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise HTTPException(503,'JANUS is not configured correctly')
    from urllib.error import HTTPError
    try:
        req=Request(base+'/v1/auth/introspect',data=b'',method='POST',headers={'Authorization':authorization,'Accept':'application/json'})
        with open_request(req) as response: data=json.loads(response.read(65536))
    except HTTPError as exc:
        raise HTTPException(401 if exc.code in (401,403) else 503,'JANUS session denied' if exc.code in (401,403) else 'JANUS unavailable') from None
    except (OSError, ValueError):
        raise HTTPException(503,'JANUS unavailable') from None
    if not isinstance(data,dict) or data.get('active') is not True or not isinstance(data.get('principal'),dict):
        raise HTTPException(401,'Invalid or expired JANUS session')
    claims=data['principal']
    if not claims.get('id'): raise HTTPException(401,'Invalid JANUS identity')
    return claims

def install_access_control(app):
    @app.middleware('http')
    async def protect(request,call_next):
        public=request.method in ('GET','HEAD') and request.url.path in ('/','/ui','/health')
        login=request.url.path=='/ui/session' and request.method in ('POST','DELETE')
        if not public and not login:
            try:
                from starlette.concurrency import run_in_threadpool
                claims=await run_in_threadpool(principal,request.headers.get('authorization',''))
                permission='apex.read' if request.method in ('GET','HEAD') else 'apex.write'
                roles=claims.get('roles',[]); permissions=claims.get('permissions',[])
                if 'platform-admin' not in roles and 'ung.admin' not in permissions and permission not in permissions:
                    raise HTTPException(403,'APEX permission required: '+permission)
                request.state.principal=claims
            except HTTPException as exc:
                return JSONResponse({'detail':exc.detail},status_code=exc.status_code,headers={'Cache-Control':'no-store'})
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        return response
