from contextlib import asynccontextmanager
import asyncio
from typing import Literal
from fastapi import FastAPI,HTTPException,WebSocket,WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel,Field
from safety_app.infrastructure.environment import load_environment,workspace_root
load_environment()
from app.pipeline import Pipeline
from app.models.contracts import *

pipeline=Pipeline(workspace_root())

@asynccontextmanager
async def lifespan(app):
    from app.agents.service import ReviewService
    pipeline.review=ReviewService(pipeline.evidence,pipeline.repository,lambda:pipeline.config,pipeline.current)
    pipeline.discover()
    yield
    from app.trainer.api import shutdown
    await shutdown()
    if pipeline.task: pipeline.task.cancel(); await asyncio.gather(pipeline.task,return_exceptions=True)
    if pipeline.cv_task: pipeline.cv_task.cancel(); await asyncio.gather(pipeline.cv_task,return_exceptions=True)
    if pipeline.review: await pipeline.review.close()
    if pipeline.segment_task: await asyncio.gather(pipeline.segment_task,return_exceptions=True)
    if pipeline.source: pipeline.source.close()

app=FastAPI(title='Industrial Video Safety Lab',lifespan=lifespan)
from app.trainer.api import router as learning_router
app.include_router(learning_router)

class SessionRequest(BaseModel):
    source_id:str
    mode:Literal['live_simulation','offline']='live_simulation'
    camera_id:str | None=Field(default=None,pattern=r'^[a-zA-Z0-9_-]{1,80}$')
class PlaybackRequest(BaseModel):
    action:Literal['play','pause','step','seek','restart','speed']
    speed:float | None=None
    timestamp_ms:int | None=None
class AIRequest(BaseModel):
    enabled:bool
    hosted_images_allowed:bool=False
class IncidentRequest(BaseModel):
    timestamp_ms:int=Field(ge=0)
    pre_ms:int=Field(default=15000,ge=0,le=120000)
    post_ms:int=Field(default=30000,ge=0,le=120000)
class FeedbackRequest(BaseModel):
    disposition:Literal['accepted','rejected','uncertain']
    note:str=Field(default='',max_length=4000)

@app.get('/api/sources')
async def sources(): return [{k:v for k,v in s.items() if k!='path'} for s in pipeline.sources.values()]
@app.post('/api/sources/refresh')
async def refresh(): return pipeline.discover()
@app.post('/api/sessions')
async def session(body:SessionRequest):
    try: return await pipeline.start(body.source_id,body.mode,body.camera_id)
    except ValueError as exc: raise HTTPException(400,str(exc))
    except Exception: raise HTTPException(503,'Cannot open source; check decode support and camera connectivity')
@app.get('/api/state')
async def state(): return pipeline.state()
@app.get('/api/health')
async def health(): return {'status':'ok','cv':pipeline.health,'ai':pipeline.review.state() if pipeline.review else {'enabled':False}}
@app.post('/api/playback')
async def playback(body:PlaybackRequest):
    try: return await pipeline.playback(body.action,body.speed,body.timestamp_ms)
    except ValueError as exc: raise HTTPException(400,str(exc))
@app.put('/api/config')
async def config(body:dict):
    allowed={'ppe_min_person_height_px','required_ppe','person_focused','person_crop_cap','review_cooldown_s','api_target_per_camera_hour','review_budget_usd','review_request_bound_usd','evidence_pre_s','evidence_post_s','zones','rules','confidence','target_fps','scene_review_enabled','scene_review_interval_s','scene_window_s','scene_sample_s','scene_max_images','scene_extra_images','analysis_mode','cv_triggered_reviews','site_context','detector','enabled_labels','class_confidence','class_groups','tracker_activation','tracker_high_confidence','tracker_match_iou','tracker_min_frames','tracker_lost_seconds','custom_model_id'}
    if set(body)-allowed: raise HTTPException(400,'Unknown configuration field')
    if 'detector' in body and body['detector'] not in ('small','nano','custom'): raise HTTPException(400,'Choose small/nano or a registered local custom detector')
    try: return await pipeline.update_config(body)
    except ValueError as exc:
        from pydantic import ValidationError
        if isinstance(exc,ValidationError):
            messages=[error['msg'].removeprefix('Value error, ') for error in exc.errors(include_url=False,include_context=False)]
            raise HTTPException(400,'; '.join(messages))
        raise HTTPException(400,str(exc))
@app.get('/api/cv/models')
async def cv_models():
    from app.cv.models import model_catalog
    return await pipeline.source_work(model_catalog,pipeline.root)

@app.get('/api/cv/datasets')
async def cv_datasets():
    from app.cv.datasets import dataset_catalog
    return dataset_catalog(pipeline.root)

@app.get('/api/cv/datasets/{id}/download')
async def cv_dataset_download(id:str):
    from app.cv.datasets import dataset_folder
    from fastapi.responses import FileResponse
    try:folder=dataset_folder(pipeline.root,id)
    except ValueError as exc:raise HTTPException(404,str(exc))
    path=(folder/'labeling-bundle.zip').resolve()
    if not path.is_relative_to(folder) or not path.is_file():raise HTTPException(404,'Labeling bundle unavailable')
    return FileResponse(path,media_type='application/zip',filename=f'{id}-labeling-bundle.zip')

@app.post('/api/ai')
async def ai(body:AIRequest):
    pipeline.review.set_enabled(body.enabled,body.hosted_images_allowed)
    pipeline.review.scenes.last_scheduled.clear()  # Next arriving source frame only; no backlog.
    return pipeline.review.state()

def candidate(id):
    try: return RuleCandidate.model_validate(pipeline.repository.get('candidate',id))
    except KeyError: raise HTTPException(404,'Unknown candidate')
@app.get('/api/events/{id}')
async def event_detail(id:str):
    c=candidate(id)
    return {'candidate':c,'assessments':[r for r in pipeline.repository.list('assessment') if r['candidate_id']==id],'evidence':pipeline.evidence.bundle(c),'feedback':[r for r in pipeline.repository.list('feedback') if r['candidate_id']==id]}
@app.post('/api/events/{id}/review')
async def event_review(id:str):
    c=candidate(id)
    if not pipeline.current(c.session_id,c.run_generation): raise HTTPException(409,'Candidate is from a prior run; live review requires the current generation')
    if pipeline.config.analysis_mode=='cv_only': raise HTTPException(400,'CV only does not submit language-model requests. Select an AI mode and Enable AI.')
    try:
        from app.models.contracts import FramePacket
        frame=FramePacket(**{k:getattr(c,k) for k in Context.model_fields},frame_id=0,timestamp_ms=c.evidence_cutoff_ms,width=0,height=0,evidence_cutoff_ms=c.evidence_cutoff_ms)
        pipeline.review._require(); pipeline.review.scenes.submit(frame,'manual')
        return {'status':'queued'}
    except ValueError as exc: raise HTTPException(400,str(exc))
@app.post('/api/events/{id}/feedback')
async def feedback(id:str,body:FeedbackRequest):
    from uuid import uuid4
    from datetime import datetime,timezone
    c=candidate(id)
    record=ReviewFeedback(**{k:getattr(c,k) for k in Context.model_fields},id=str(uuid4()),candidate_id=id,**body.model_dump(),processing_time=datetime.now(timezone.utc).isoformat())
    pipeline.repository.save('feedback',record); return record
@app.post('/api/incidents')
async def incident(body:IncidentRequest):
    try: return pipeline.mark_incident(**body.model_dump())
    except ValueError as exc: raise HTTPException(400,str(exc))
def incident_record(id):
    try: return IncidentMarker.model_validate(pipeline.repository.get('incident',id))
    except KeyError: raise HTTPException(404,'Unknown incident')
@app.get('/api/incidents/{id}')
async def incident_detail(id:str):
    marker=incident_record(id)
    return {'incident':marker,'report':next((r for r in reversed(pipeline.repository.list('response')) if r['incident_id']==id),None),'coverage':pipeline.evidence.coverage(marker)}
@app.post('/api/incidents/{id}/review')
async def incident_review(id:str):
    marker=incident_record(id)
    try:
        pipeline.review._require()
        if pipeline.config.analysis_mode=='cv_only': raise ValueError('CV only does not submit language-model requests. Select an AI mode and Enable AI.')
        pipeline.review._require()
        await pipeline.prepare_retrospective(marker)
        return await pipeline.review.review_incident(marker)
    except ValueError as exc: raise HTTPException(400,str(exc))
@app.get('/api/evidence/{id}/image')
async def evidence_image(id:str):
    try: return Response(pipeline.evidence.image(id),media_type='image/jpeg')
    except KeyError: raise HTTPException(404,'Evidence unavailable or expired')
@app.get('/api/history')
async def history(): return pipeline.repository.list()
@app.websocket('/api/ws')
async def websocket(ws:WebSocket):
    await ws.accept(); queue=asyncio.Queue(maxsize=2); pipeline.subscribers.add(queue)
    async def send_updates():
        while True: await ws.send_json(await queue.get())
    async def watch_disconnect():
        while True: await ws.receive_text()
    tasks=[]
    try:
        await ws.send_json({'type':'state',**pipeline.state()})
        tasks=[asyncio.create_task(send_updates()),asyncio.create_task(watch_disconnect())]
        done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        for task in done: task.result()
    except (WebSocketDisconnect,asyncio.CancelledError): pass
    finally:
        for task in tasks: task.cancel()
        if tasks:
            try: await asyncio.gather(*tasks,return_exceptions=True)
            except asyncio.CancelledError: pass
        pipeline.subscribers.discard(queue)

class EvaluationRequest(BaseModel):
    annotations: list[dict] = Field(default_factory=list,max_length=10000)
    predictions: list[dict] = Field(default_factory=list,max_length=10000)
    coverage: list[dict] = Field(default_factory=list,max_length=1000)
    incidents: list[dict] = Field(default_factory=list,max_length=1000)
    tolerance_ms: int = Field(default=1000,ge=0,le=60000)
    mode: Literal['rules_only','coordinator','delegation','ai_scene','ai_cv','ai_cv_with_triggers'] = 'rules_only'
    data_kind: str = 'human_labeled_video'

@app.post('/api/evaluations')
async def evaluate_job(body:EvaluationRequest):
    from uuid import uuid4
    from app.evals.events import evaluate
    document=body.model_dump(exclude={'tolerance_ms','annotations'}) | {'labels':body.annotations}
    try: result=evaluate(document,body.tolerance_ms)
    except (ValueError,KeyError,TypeError) as exc: raise HTTPException(400,f'Invalid evaluation annotations: {exc}')
    record={'id':str(uuid4()),'status':'complete','result':result}
    pipeline.repository.save('evaluation_job',record)
    return record

@app.get('/api/evaluations/{id}')
async def evaluation_job(id:str):
    try: return pipeline.repository.get('evaluation_job',id)
    except KeyError: raise HTTPException(404,'Unknown evaluation job')


@app.get('/api/scene-reviews/{id}')
async def scene_review_detail(id:str):
    try: return pipeline.repository.get('scene_review',id)
    except KeyError: raise HTTPException(404,'Unknown scene review')

@app.get('/api/derived-evidence/{id}/image')
async def derived_image(id:str):
    import re
    if not re.fullmatch(r'[0-9a-f-]{36}-crop-[0-9]+',id): raise HTTPException(404,'Unknown derived image')
    path=pipeline.evidence.root/(id+'.jpg')
    if not path.is_file(): raise HTTPException(404,'Derived image unavailable')
    return Response(path.read_bytes(),media_type='image/jpeg')

@app.post('/api/scene-events/{id}/feedback')
async def scene_feedback(id:str,body:FeedbackRequest):
    from uuid import uuid4
    from datetime import datetime,timezone
    try: event=pipeline.repository.get('scene_event',id)
    except KeyError: raise HTTPException(404,'Unknown scene event')
    feedback={k:event[k] for k in Context.model_fields}
    feedback.update(id=str(uuid4()),event_id=id,**body.model_dump(),processing_time=datetime.now(timezone.utc).isoformat())
    pipeline.repository.save('scene_feedback',feedback)
    event['human_disposition']=body.disposition;event['human_note']=body.note
    pipeline.repository.save('scene_event',event)
    return feedback

@app.get('/api/comparison-cache')
async def comparison_cache():
    if not pipeline.session: raise HTTPException(400,'Open a session first')
    from app.evals.packets import export_cache
    try:
        raw=await asyncio.to_thread(export_cache,pipeline.evidence,pipeline.repository,dict(pipeline.session),pipeline.review.model_config)
        return Response(raw,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="scene-comparison-cache.zip"'})
    except ValueError as error: raise HTTPException(400,str(error))

@app.get('/api/events/{id}/clip')
async def candidate_clip(id:str):
    from app.evidence.clips import event_clip
    from fastapi.responses import FileResponse
    record=candidate(id).model_dump(mode='json')
    try: path=await pipeline.source_work(event_clip,pipeline,record)
    except (ValueError,KeyError): raise HTTPException(409,'Evidence clip unavailable; inspect coverage or wait for retained footage')
    return FileResponse(path,media_type='video/mp4')

@app.get('/api/scene-events/{id}/clip')
async def finding_clip(id:str):
    from app.evidence.clips import event_clip
    from fastapi.responses import FileResponse
    try:
        record=pipeline.repository.get('scene_event',id)
        path=await pipeline.source_work(event_clip,pipeline,record)
    except (ValueError,KeyError): raise HTTPException(409,'Evidence clip unavailable; inspect coverage or wait for retained footage')
    return FileResponse(path,media_type='video/mp4')
