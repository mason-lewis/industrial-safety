import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import time
import sys
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import FileResponse,StreamingResponse
from pydantic import BaseModel,Field
from app.trainer.workspace import LearningWorkspace,CLASSES
from app.trainer.models import (LearningBox as Box, LearningLabelInput as LabelInput,
    LearningAssistInput, LearningIntakeClip, LearningIntakeInput, LearningValidationInput, LearningValidationReport, LearningClassesInput,
    LearningJobInput as JobInput, LearningImageReviewInput, LearningImageReviewSettings, LearningImageReviewStatus, LearningAIActivity, LearningSampleInput)
from app.trainer import assistance
from app.trainer import image_review

start_lock=asyncio.Lock()
assistance_lock=asyncio.Lock()
image_review_lock=asyncio.Lock()
router=APIRouter(prefix='/api/learning');spaces={};tasks={};processes={}
def workspace():
    from app.main import pipeline
    root=pipeline.root.resolve()
    if root not in spaces:spaces[root]=LearningWorkspace(root)
    return spaces[root]
async def work(fn,*args):
    try:return await asyncio.to_thread(fn,*args)
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(400,str(exc)) from exc
async def local_work(fn,*args):
    task=asyncio.create_task(work(fn,*args))
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task,return_exceptions=True)
        raise
def learning_audit(ws):
    from safety_app.infrastructure.audit import Audit
    if not hasattr(ws,'audit'):ws.audit=Audit(ws.root/'data/audit')
    return ws.audit
class ProjectInput(BaseModel):
    name:str=Field(max_length=100,min_length=1)
    classes:list[str]=Field(default_factory=lambda:list(CLASSES),max_length=40)
SampleInput=LearningSampleInput
class AssignInput(BaseModel):
    group:str=Field(min_length=1,max_length=120)
    split:Literal['train','valid','test']

@router.get('/projects')
async def projects():
    return [{k:p[k] for k in ('id','name','frozen','classes')} for p in await work(workspace().catalog)]
@router.post('/projects')
async def create(body:ProjectInput):return await work(workspace().create,body.name,body.classes)
@router.put('/projects/{id}/classes')
async def classes(id:str,body:LearningClassesInput):return await work(workspace().update_classes,id,body.classes)
@router.get('/environment')
async def environment():return workspace().dependencies()
@router.get('/image-review/status',response_model=LearningImageReviewStatus)
async def image_review_status():
    from safety_app.infrastructure.runtime import runtime
    return image_review.status(runtime())
@router.put('/image-review/settings',response_model=LearningImageReviewStatus)
async def image_review_settings(body:LearningImageReviewSettings):
    from safety_app.infrastructure.runtime import runtime
    return await work(image_review.save_settings,runtime(),body.image_usd)
@router.post('/projects/{id}/frames/{fid}/image-review')
async def image_review_frame(id:str,fid:str,body:LearningImageReviewInput):
    from safety_app.infrastructure.runtime import runtime
    from safety_app.infrastructure.budgets import BudgetBlocked
    async with image_review_lock:
        try:return await image_review.review(workspace(),runtime(),id,fid,body.expected_revision)
        except (ValueError,OSError,KeyError) as exc:raise HTTPException(400,str(exc)) from exc
        except BudgetBlocked as exc:raise HTTPException(409,str(exc)) from exc
        except Exception as exc:raise HTTPException(503,f'AI image review failed ({type(exc).__name__}). Saved labels remain intact; inspect Audit for details.') from exc
@router.get('/projects/{id}/frames/{fid}/image-review/activity',response_model=LearningAIActivity)
async def image_review_activity(id:str,fid:str,run_id:str|None=None):
    from safety_app.infrastructure.runtime import runtime
    return await work(image_review.activity,workspace(),runtime(),id,fid,run_id)
@router.get('/projects/{id}/frames/{fid}/image-review/audit/download')
async def image_review_audit_download(id:str,fid:str,run_id:str):
    from safety_app.infrastructure.runtime import runtime
    lines=image_review.audit_download(workspace(),runtime(),id,fid,run_id)
    return StreamingResponse(lines,media_type='application/x-ndjson',headers={'Content-Disposition':'attachment; filename="image-review-audit.ndjson"'})
@router.get('/intake',response_model=list[LearningIntakeClip])
async def intake():return await work(assistance.intake_catalog,workspace())
@router.post('/projects/{id}/intake')
async def intake_import(id:str,body:LearningIntakeInput,request:Request):
    ws=workspace();audit=learning_audit(ws);run_id=uuid4().hex
    audit.write('learning_intake_import_requested',run_id=run_id,arguments=body.model_dump(mode='json'),
        provenance={'project_id':id,'reviewer_id':getattr(getattr(request.state,'operator',None),'id',None)})
    try:
        result=await local_work(assistance.import_intake,ws,id,body.intake_id,body.group,body.split)
        audit.write('learning_intake_import_completed',run_id=run_id,provenance={'project_id':id,'intake_id':body.intake_id,'clip_id':result['clips'][-1]['id']})
        return result
    except BaseException as exc:
        from safety_app.domain.models import ToolFailure
        audit.write('learning_intake_import_failed',run_id=run_id,status='cancelled' if isinstance(exc,asyncio.CancelledError) else 'failed',
            error=ToolFailure(code=type(exc).__name__,message=str(getattr(exc,'detail',exc))[:1000]))
        raise

def proposal_detector():
    # A separate cached instance avoids changing the active monitoring detector's device/model state.
    from app.main import pipeline
    ws=workspace();key=(pipeline.config.detector,pipeline.config.custom_model_id,pipeline.config.custom_weights,
                       json.dumps(pipeline.config.custom_class_map,sort_keys=True))
    if getattr(ws,'proposal_detector_key',None)!=key:
        ws.proposal_detector=pipeline.make_detector(pipeline.config)
        ws.proposal_detector_key=key
    return ws.proposal_detector

@router.post('/projects/{id}/frames/{fid}/assist')
async def assist(id:str,fid:str,body:LearningAssistInput):
    async with assistance_lock:
        try:return await local_work(assistance.assist_frame,workspace(),id,fid,proposal_detector(),body.confidence)
        except HTTPException:raise
        except Exception as exc:raise HTTPException(503,f'Local detector proposals failed ({type(exc).__name__}). Human labeling remains available.') from exc

@router.post('/projects/{id}/validate',response_model=LearningValidationReport)
async def validate(id:str,body:LearningValidationInput):
    async with assistance_lock:
        try:return await local_work(assistance.validate_current,workspace(),id,proposal_detector(),body.confidence,body.max_frames)
        except HTTPException:raise
        except Exception as exc:raise HTTPException(503,f'Local validation failed ({type(exc).__name__}); no accuracy result was saved.') from exc
@router.get('/projects/{id}')
async def project(id:str):
    ws=workspace();p=await work(ws.read,id)
    # A dev-server restart may interrupt an in-process monitor. Never pretend it succeeded.
    changed=False
    for job in p['jobs']:
        if job['status'] in ('queued','running') and job['id'] not in tasks:
            job.update(status='interrupted',error='Server restarted; inspect local outputs. No success or evaluation is assumed.');changed=True
    if changed:
        with ws.lock:ws.write(p)
    checkpoints=[]
    for job in p['jobs']:
        if job['kind']=='train' and job['status']=='completed':
            base=ws.folder(id)/'jobs'/job['id']/'output'
            checkpoints.extend({'id':str(path.relative_to(ws.folder(id))),'name':path.name,'job_id':job['id']} for path in sorted(base.rglob('*.pth')) if path.resolve().is_relative_to(base.resolve()))
    return p|{'readiness':await work(ws.check,p),'checkpoints':checkpoints,'validation_snapshot_sha256':assistance.validation_signature(p)}
@router.post('/projects/{id}/clips')
async def upload(id:str,request:Request,name:str,group:str,split:Literal['train','valid','test']):
    ws=workspace();await work(ws.read,id)
    if Path(name).suffix.lower() not in assistance.EXTENSIONS:raise HTTPException(400,'Choose a supported video format')
    folder=ws.folder(id)/'uploads';folder.mkdir(exist_ok=True);path=folder/f'{uuid4().hex}{Path(name).suffix.lower()}';size=0
    try:
        with path.open('wb') as stream:
            async for chunk in request.stream():
                size+=len(chunk)
                if size>2*1024**3:raise HTTPException(413,'Upload limit is 2 GB per clip')
                stream.write(chunk)
        return await work(ws.import_clip,id,path,name,group,split)
    finally:path.unlink(missing_ok=True)
@router.post('/projects/{id}/clips/{cid}/sample')
async def sample(id:str,cid:str,body:SampleInput):
    ws=workspace();audit=learning_audit(ws);run_id=uuid4().hex
    audit.write('learning_sampling_started',run_id=run_id,arguments=body.model_dump(mode='json'),provenance={'project_id':id,'clip_id':cid})
    try:
        result=await local_work(ws.sample,id,cid,body.interval_s,body.max_frames,body.mode)
        clip=next(c for c in result['clips'] if c['id']==cid)
        audit.write('learning_sampling_finished',run_id=run_id,output=clip['sampling_history'][-1],provenance={'project_id':id,'clip_id':cid})
        return result
    except BaseException as exc:
        from safety_app.domain.models import ToolFailure
        audit.write('learning_sampling_failed',run_id=run_id,status='failed',error=ToolFailure(code=type(exc).__name__,message=str(getattr(exc,'detail',exc))[:1000]))
        raise
@router.post('/projects/{id}/clips/{cid}/archive')
async def archive_clip(id:str,cid:str):return await work(workspace().archive_clip,id,cid)
@router.put('/projects/{id}/clips/{cid}/assignment')
async def assignment(id:str,cid:str,body:AssignInput):return await work(workspace().assign,id,cid,body.group,body.split)
@router.put('/projects/{id}/frames/{fid}')
async def label(id:str,fid:str,body:LabelInput,request:Request):
    ws=workspace()
    boxes=[b.model_dump(mode='json') for b in body.boxes]
    if body.ai_review_id:
        frame=next((f for f in ws.read(id)['frames'] if f['id']==fid),None)
        if not frame or frame.get('ai_review',{}).get('id')!=body.ai_review_id:raise HTTPException(400,'AI review does not belong to this frame')
        audit=learning_audit(ws)
        audit.write('human_review_save_requested',run_id=body.ai_review_id,arguments=body.model_dump(mode='json'),
            provenance={'project_id':id,'frame_id':fid,'reviewer_id':getattr(getattr(request.state,'operator',None),'id',None)})
    try:result=await work(ws.label,id,fid,boxes,body.reviewed,body.verified_empty,body.proposal_decisions,body.expected_revision,body.ai_review_id)
    except HTTPException as exc:
        if body.ai_review_id:
            from safety_app.domain.models import ToolFailure
            audit.write('human_review_save_failed',run_id=body.ai_review_id,status='failed',error=ToolFailure(code='invalid_review',message=str(exc.detail)))
        raise
    if body.ai_review_id:
        audit.write('human_review_saved',run_id=body.ai_review_id,provenance={'project_id':id,'frame_id':fid},
            resources={'reviewed':body.reviewed,'box_count':len(boxes),'verified_empty':body.verified_empty})
    return result
@router.get('/projects/{id}/frames/{fid}/image')
async def image(id:str,fid:str):
    ws=workspace();p=await work(ws.read,id);f=next((f for f in p['frames'] if f['id']==fid),None)
    if not f:raise HTTPException(404,'Unknown frame')
    path=(ws.folder(id)/'images'/f['file_name']).resolve()
    if not path.is_relative_to((ws.folder(id)/'images').resolve()):raise HTTPException(400,'Invalid image path')
    return FileResponse(path,media_type='image/jpeg')
@router.post('/projects/{id}/freeze')
async def freeze(id:str):return await work(workspace().freeze,id)
@router.post('/projects/{id}/recording-separation')
async def recording_separation(id:str):return await work(workspace().confirm_recording_separation,id)
@router.get('/projects/{id}/download')
async def download(id:str):
    ws=workspace();await work(ws.training_ready,id);return FileResponse(ws.folder(id)/'dataset.zip',media_type='application/zip',filename=f'isolated-{id[:8]}-dataset.zip')

def checkpoint_path(ws,id,p,relative):
    if not relative:raise ValueError('Select a checkpoint from a completed training run')
    path=(ws.folder(id)/relative).resolve()
    allowed=[(ws.folder(id)/'jobs'/j['id']/'output').resolve() for j in p['jobs'] if j['kind']=='train' and j['status']=='completed']
    if path.suffix!='.pth' or not path.is_file() or not any(path.is_relative_to(folder) for folder in allowed):raise ValueError('Checkpoint must belong to a completed training run in this project')
    return path
def stop_process(process):
    if process.returncode is None:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass

async def monitor(ws,id,job,command,cwd):
    jid=job['id'];log=cwd/'process.log'
    try:
        with ws.lock:
            p=ws.read(id);next(j for j in p['jobs'] if j['id']==jid).update(status='running',started_at=time.time());ws.write(p)
        env={k:v for k,v in os.environ.items() if not any(part in k.upper() for part in ('KEY','TOKEN','SECRET','PASSWORD'))};env['AI_ENABLED']='false';env['PYTHONUNBUFFERED']='1';env['MPLCONFIGDIR']=str(cwd/'cache/matplotlib');env['HF_HOME']=str(ws.root/'data/artifacts/models/huggingface');env['TORCH_HOME']=str(ws.root/'data/artifacts/models/torch');env['RF_HOME']=str(ws.root/'data/artifacts/models')
        with log.open('wb') as stream:
            process=await asyncio.create_subprocess_exec(*command,cwd=cwd,stdout=stream,stderr=asyncio.subprocess.STDOUT,env=env,start_new_session=True);processes[jid]=process
            code=await process.wait()
        with ws.lock:
            p=ws.read(id);row=next(j for j in p['jobs'] if j['id']==jid)
            if row['status']!='cancelled':row.update(status='completed' if code==0 else 'failed',exit_code=code,finished_at=time.time())
            ws.write(p)
    except asyncio.CancelledError:
        proc=processes.get(jid)
        if proc and proc.returncode is None:
            stop_process(proc)
            try:await asyncio.wait_for(proc.wait(),5)
            except asyncio.TimeoutError:
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await proc.wait()
        with ws.lock:
            p=ws.read(id);next(j for j in p['jobs'] if j['id']==jid)['status']='interrupted';ws.write(p)
        raise
    except Exception as exc:
        with ws.lock:
            p=ws.read(id);next(j for j in p['jobs'] if j['id']==jid).update(status='failed',error=str(exc));ws.write(p)
    finally:processes.pop(jid,None);tasks.pop(jid,None)
@router.post('/projects/{id}/jobs')
async def job_start(id:str,body:JobInput):
    async with start_lock:return await start_job(id,body)

async def start_job(id:str,body:JobInput):
    ws=workspace();p=await work(ws.training_ready,id)
    if tasks:raise HTTPException(409,'Another local learning job is running. Wait or cancel it first.')
    if body.kind=='train' and p.get('test_revealed'):raise HTTPException(400,'Test results have been opened for this dataset. Training is sealed to prevent tuning against the test set; collect a new independent test group in a new project.')
    if body.kind in ('train','evaluate') and not ws.dependencies()['ready']:raise HTTPException(400,'Training dependencies are missing. See the environment instructions in the learning workspace.')
    weights=None
    if body.kind!='train':weights=await work(checkpoint_path,ws,id,p,body.checkpoint)
    digest=None
    if weights:
        with weights.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if body.kind=='evaluate' and p.get('test_checkpoint_sha256') not in (None,digest):raise HTTPException(400,'Held-out test is locked to the first evaluated checkpoint; do not compare tuned checkpoints against this test set.')
    if body.kind=='register':
        if not body.license.strip():raise HTTPException(400,'Declare the checkpoint license')
        if not any(j['kind']=='evaluate' and j['status']=='completed' and j.get('checkpoint_sha256')==digest for j in p['jobs']):raise HTTPException(400,'Evaluate this checkpoint on held-out footage before registering it')
    jid=uuid4().hex;folder=ws.folder(id)/'jobs'/jid;folder.mkdir(parents=True);script=ws.root/'backend/app/trainer/cli/train.py';output=folder/'output'
    if body.kind=='train':
        # Test files do not exist in the trainer's input directory.
        data=folder/'train-data';data.mkdir()
        for split in ('train','valid'):await asyncio.to_thread(shutil.copytree,ws.folder(id)/'snapshot'/split,data/split)
        provenance=json.loads((ws.folder(id)/'snapshot/split-provenance.json').read_text());provenance['test_present']=False;(data/'split-provenance.json').write_text(json.dumps(provenance))
        command=[sys.executable,str(script),'--dataset',str(data),'--output',str(output),'--device',body.device,'--epochs',str(body.epochs),'--batch-size',str(body.batch_size),'--variant',body.variant,'--required-classes',*p['classes'],'--run-training']
    elif body.kind=='evaluate':command=[sys.executable,str(script),'--dataset',str(ws.folder(id)/'snapshot'),'--output',str(output),'--device',body.device,'--required-classes',*p['classes'],'--evaluate-weights',str(weights)]
    else:
        model_id=f'industrial-{id[:8]}-{jid[:8]}'
        command=[sys.executable,str(ws.root/'backend/app/trainer/cli/register.py'),'--weights',str(weights),'--classes',*p['classes'],'--id',model_id,'--name',p['name'],'--license',body.license,'--device',body.device]
    job={'id':jid,'kind':body.kind,'status':'queued','created_at':time.time(),'checkpoint':body.checkpoint,'checkpoint_sha256':digest,'snapshot_sha256':p['snapshot_sha256'],'device':body.device,'epochs':body.epochs,'batch_size':body.batch_size,'variant':body.variant}
    if body.kind=='register':job['model_id']=model_id
    with ws.lock:
        p=ws.read(id)
        if body.kind=='evaluate':p.update(test_revealed=True,test_checkpoint_sha256=digest)
        p['jobs'].append(job);ws.write(p)
    tasks[jid]=asyncio.create_task(monitor(ws,id,job,command,folder));return job
@router.get('/projects/{id}/jobs/{jid}/log')
async def log(id:str,jid:str):
    ws=workspace();p=await work(ws.read,id)
    if not any(j['id']==jid for j in p['jobs']):raise HTTPException(404,'Unknown job')
    path=ws.folder(id)/'jobs'/jid/'process.log';text=''
    if path.is_file():
        with path.open('rb') as stream:stream.seek(max(0,path.stat().st_size-24000));text=stream.read().decode('utf8',errors='replace')
    return {'text':text}
@router.post('/projects/{id}/jobs/{jid}/cancel')
async def cancel(id:str,jid:str):
    ws=workspace();p=await work(ws.read,id);row=next((j for j in p['jobs'] if j['id']==jid),None)
    if not row:raise HTTPException(404,'Unknown job')
    proc=processes.get(jid)
    if not proc or proc.returncode is not None:raise HTTPException(400,'Job is not running')
    stop_process(proc)
    with ws.lock:p=ws.read(id);next(j for j in p['jobs'] if j['id']==jid)['status']='cancelled';ws.write(p)
    return {'status':'cancelled'}
@router.get('/projects/{id}/jobs/{jid}/evaluation')
async def evaluation(id:str,jid:str):
    ws=workspace();p=await work(ws.read,id)
    if not any(j['id']==jid and j['kind']=='evaluate' and j['status']=='completed' for j in p['jobs']):raise HTTPException(400,'Evaluation has not completed')
    return json.loads((ws.folder(id)/'jobs'/jid/'output/evaluation.json').read_text())

async def shutdown():
    for task in list(tasks.values()):task.cancel()
    await asyncio.gather(*list(tasks.values()),return_exceptions=True)
