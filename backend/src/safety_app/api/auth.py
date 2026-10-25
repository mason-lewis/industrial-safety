from safety_app.domain.base import StrictModel as BaseModel
"""Local operator session. Identity and site scope come from backend settings."""
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from fastapi import Request,HTTPException,APIRouter
from safety_app.infrastructure.runtime import runtime
router=APIRouter(prefix='/api/auth')
@dataclass(frozen=True)
class Operator:
    id:str='local-operator'
    site_id:str='local'
def token():
    rt=runtime();path=rt.root/'data/.operator-secret'
    if not path.exists():
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as stream:stream.write(secrets.token_hex(32))
        except FileExistsError:pass
    return hmac.new(path.read_bytes(),b'local-operator',hashlib.sha256).hexdigest()
def origin(request):
    value=request.headers.get('origin')
    allowed={'http://127.0.0.1:5173','http://localhost:5173','http://127.0.0.1:8000','http://localhost:8000'}
    if request.url.hostname in ('127.0.0.1','localhost') and request.url.scheme=='http':allowed.add(str(request.base_url).rstrip('/'))
    if value and value not in allowed:raise HTTPException(403,'Untrusted request origin')
def operator(request:Request):
    origin(request)
    if not hmac.compare_digest(request.cookies.get('safety_session',''),token()):raise HTTPException(401,'Open a local operator session')
    return Operator()
class Login(BaseModel):
    operator_token:str|None=None
@router.post('/session')
def session(request:Request,body:Login):
    origin(request)
    if request.client and request.client.host not in ('127.0.0.1','::1','localhost','testclient'):raise HTTPException(403,'Local sessions require a loopback connection')
    configured=os.getenv('SAFETY_OPERATOR_TOKEN')
    if configured and not secrets.compare_digest(body.operator_token or '',configured):raise HTTPException(401,'Operator token is required')
    from fastapi.responses import JSONResponse
    response=JSONResponse({'reviewer_id':'local-operator','site_id':'local'});response.set_cookie('safety_session',token(),httponly=True,samesite='strict');return response
