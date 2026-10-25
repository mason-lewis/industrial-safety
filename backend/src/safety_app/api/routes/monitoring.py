from safety_app.domain.base import StrictModel as BaseModel
from fastapi import APIRouter,Depends,Request
from safety_app.api.auth import operator,Operator
from safety_app.domain.models import ReplayRequest,Job,CameraProfile,CandidateEvent,MediaRef,FrameRef
from safety_app.infrastructure.runtime import runtime
from safety_app.modules.monitoring.service import Monitoring
router=APIRouter(prefix='/api')
@router.post('/monitoring/replays',response_model=Job)
def replay(body:ReplayRequest,user:Operator=Depends(operator)):
    if runtime().camera(body.profile_id).site_id!=user.site_id:raise ValueError('Camera outside site')
    return Monitoring(runtime()).replay_video(body)
class StartRequest(BaseModel):idempotency_key:str
@router.post('/monitoring/cameras/{id}/start',response_model=Job)
def start(id:str,body:StartRequest,user:Operator=Depends(operator)):
    if runtime().camera(id).site_id!=user.site_id:raise ValueError('Camera outside site')
    return Monitoring(runtime()).start_monitoring(id,body.idempotency_key)
@router.get('/monitoring/cameras',response_model=list[CameraProfile])
def cameras():return runtime().db.list('camera')
@router.get('/events',response_model=list[CandidateEvent])
@router.get('/monitoring/events',response_model=list[CandidateEvent])
def events(status:str|None=None,offset:int=0,limit:int=100):
    return [r for r in runtime().db.list('event') if not status or r['status']==status][max(0,offset):max(0,offset)+min(max(limit,1),500)]
@router.get('/media',response_model=list[MediaRef])
def media():return runtime().db.list('media')
@router.get('/media/{id}/frames',response_model=list[FrameRef])
def frames(id:str):return [r for r in runtime().db.list('frame') if r['media_id']==id]
@router.get('/media/frames/{id}')
def frame(id:str):
    from fastapi.responses import Response
    return Response(runtime().media.image(id),media_type='image/jpeg')
@router.get('/jobs',response_model=list[Job])
def jobs():return runtime().jobs.list()
@router.get('/jobs/{id}',response_model=Job)
def job(id:str):return runtime().jobs.get(id)
@router.post('/jobs/{id}/cancel',response_model=Job)
def cancel(id:str,user:Operator=Depends(operator)):
    if runtime().jobs.get(id).site_id!=user.site_id:raise ValueError('Job outside site')
    return runtime().jobs.cancel(id)

@router.post('/monitoring/uploads')
async def upload(request:Request,name:str,user:Operator=Depends(operator)):
    """Stream a recorded clip into the existing library. Never overwrite another clip."""
    import os
    from pathlib import Path
    from safety_app.domain.models import uid
    from app.main import pipeline
    suffix=Path(name).suffix.lower()
    if suffix not in ('.mp4','.mov','.mkv','.avi','.webm','.m4v'):raise ValueError('Unsupported video format')
    folder=runtime().root/'data/input';folder.mkdir(parents=True,exist_ok=True);dest=folder/f'uploaded-{uid()}{suffix}';temp=dest.with_suffix('.upload');size=0
    try:
        with temp.open('wb') as stream:
            async for chunk in request.stream():
                size+=len(chunk)
                if size>2*1024**3:raise ValueError('Video upload limit is 2 GB')
                stream.write(chunk)
        if not size:raise ValueError('Upload is empty')
        os.replace(temp,dest)
        runtime().audit.write('media_uploaded',provenance={'file_name':dest.name,'bytes':size,'operator':user.id})
        return pipeline.discover()
    finally:temp.unlink(missing_ok=True)

# Interactive scene reviews have their own stable run IDs and redacted audit scope.
from fastapi import Query
from safety_app.domain.models import AuditEvent
from app.models.contracts import SceneReview

@router.get('/scene-reviews',response_model=list[SceneReview])
def scene_history(limit:int=Query(30,ge=1,le=100),user:Operator=Depends(operator)):
    from app.main import pipeline
    return pipeline.repository.list('scene_review',limit=limit)

def scene_record(id):
    from app.main import pipeline
    return pipeline.repository.get('scene_review',id)

@router.get('/scene-reviews/{id}/audit',response_model=list[AuditEvent])
def scene_audit(id:str,limit:int=Query(200,ge=1,le=1000),offset:int=Query(0,ge=0),user:Operator=Depends(operator)):
    scene_record(id)
    return runtime().audit.rows(limit,offset,run_id=id)

@router.get('/scene-reviews/{id}/audit/download')
def scene_audit_download(id:str,user:Operator=Depends(operator)):
    import json
    from fastapi.responses import StreamingResponse
    from app.main import pipeline
    record=scene_record(id)
    active=pipeline.review.scenes.running if pipeline.review else None
    if record['status'] in ('queued','running') or (active and active.id==id):raise ValueError('Wait for this review to finish before downloading its full trace')
    rt=runtime()
    with rt.audit.db.connect() as db:
        last=db.execute("SELECT COALESCE(MAX(sequence),0) FROM audit WHERE json_extract(body,'$.run_id')=?",(id,)).fetchone()[0]
    def lines():
        cursor=0
        while True:
            with rt.audit.db.connect() as db:
                rows=db.execute("SELECT sequence,body FROM audit WHERE sequence>? AND sequence<=? AND json_extract(body,'$.run_id')=? ORDER BY sequence LIMIT 200",(cursor,last,id)).fetchall()
            if not rows:break
            for row in rows:yield json.dumps(json.loads(row['body'])|{'sequence':row['sequence']})+'\n'
            cursor=rows[-1]['sequence']
    return StreamingResponse(lines(),media_type='application/x-ndjson',headers={'Content-Disposition':'attachment; filename="video-review-audit.ndjson"'})
