from safety_app.domain.base import StrictModel as BaseModel
from fastapi import APIRouter,Depends,Request
from pydantic import Field
from safety_app.api.auth import operator,Operator
from safety_app.domain.models import ReviewRequest,Finding,HumanDecision,Job,SourceExcerpt,SiteRule
from safety_app.infrastructure.runtime import runtime
from safety_app.modules.safety_review.service import SafetyReview
router=APIRouter(prefix='/api')
@router.post('/reviews',response_model=Job)
def review(body:ReviewRequest,user:Operator=Depends(operator)):return SafetyReview(runtime()).request_review(body,user.site_id)
@router.get('/findings',response_model=list[Finding])
def findings():return runtime().db.list('finding')
@router.get('/findings/{id}',response_model=Finding)
def finding(id:str):return runtime().db.get('finding',id)
@router.get('/reviews')
def reviews():return runtime().db.list('review')
class DecisionInput(BaseModel):
    target_id:str
    decision:str
    reason:str=Field(max_length=4000)
@router.post('/decisions',response_model=HumanDecision)
def decision(body:DecisionInput,user:Operator=Depends(operator)):return SafetyReview(runtime()).save_human_decision(body.target_id,body.decision,body.reason,user.id,user.site_id)
@router.get('/decisions',response_model=list[HumanDecision])
def decisions():return runtime().db.list('decision')
@router.post('/sources/curated',response_model=SourceExcerpt)
def source(body:SourceExcerpt,user:Operator=Depends(operator)):
    if body.site_id!=user.site_id:raise ValueError('Source outside site')
    runtime().db.save('source',body,immutable=True);runtime().audit.write('source_imported',provenance={'source_id':body.id,'version':body.version});return body
@router.get('/sources/curated',response_model=list[SourceExcerpt])
def sources():return runtime().db.list('source')
@router.post('/rules/site',response_model=SiteRule)
def rule(body:SiteRule,user:Operator=Depends(operator)):
    if body.site_id!=user.site_id:raise ValueError('Rule outside site')
    runtime().db.save('rule',body,immutable=True);runtime().audit.write('site_rule_created',output=body.model_dump(mode='json'));return body

@router.post('/media/photos')
async def photo(request:Request,name:str,camera_id:str='camera-1',user:Operator=Depends(operator)):
    import cv2,numpy as np,hashlib
    from safety_app.domain.models import MediaRef,uid
    rt=runtime()
    if rt.camera(camera_id).site_id!=user.site_id:raise ValueError('Camera outside site')
    chunks=[];size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>rt.config['limits']['media_bytes']:raise ValueError('Photo exceeds media byte limit')
        chunks.append(chunk)
    raw=b''.join(chunks);image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
    if image is None:raise ValueError('Cannot decode photo')
    h,w=image.shape[:2]
    if max(h,w)>1024:image=cv2.resize(image,(max(1,round(w*1024/max(h,w))),max(1,round(h*1024/max(h,w)))))
    ok,encoded=cv2.imencode('.jpg',image)
    if not ok:raise ValueError('Cannot encode photo')
    id=uid();media=MediaRef(id=id,media_id=id,source_id=hashlib.sha256(raw).hexdigest(),camera_id=camera_id,start_ms=0,end_ms=0,content_hash=hashlib.sha256(raw).hexdigest(),storage_key='photo:'+id)
    rt.db.save('media',media,immutable=True);rt.media.add_frame(id,'photo',0,encoded.tobytes());rt.audit.write('photo_imported',provenance={'media_id':id,'content_hash':media.content_hash,'original_dimensions':[w,h]});return media
