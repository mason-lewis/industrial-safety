from safety_app.domain.base import StrictModel as BaseModel
from fastapi import APIRouter,Depends,Query
from safety_app.api.auth import operator,Operator
from safety_app.domain.models import AuditEvent,BudgetStatus,AiStatus
from safety_app.infrastructure.runtime import runtime
router=APIRouter(prefix='/api')
@router.get('/audit',response_model=list[AuditEvent])
def audit(run_id:str|None=None,agent_name:str|None=None,job_id:str|None=None,event_id:str|None=None,status:str|None=None,event_type:str|None=None,since:str|None=None,until:str|None=None,limit:int=Query(200,ge=1,le=1000),offset:int=Query(0,ge=0)):
    return runtime().audit.rows(limit,offset,run_id=run_id,agent_name=agent_name,job_id=job_id,event_id=event_id,status=status,event_type=event_type,since=since,until=until)
@router.get('/budgets',response_model=BudgetStatus)
def budgets():
    rt=runtime();return rt.budgets.status()|{'coverage_gaps':rt.db.list('coverage_gap'),'audit_degraded':rt.audit.degraded,'snapshot_error':rt.audit.export_error}
class StopInput(BaseModel):stopped:bool
@router.get('/ai',response_model=AiStatus)
def ai(user:Operator=Depends(operator)):
    from safety_app.infrastructure.ai_control import ai_status
    return ai_status(runtime())

@router.post('/budgets/stop')
async def stop(body:StopInput,user:Operator=Depends(operator)):
    rt=runtime();rt.budgets.switch(body.stopped)
    if body.stopped:
        from app.main import pipeline
        if pipeline.review:pipeline.review.set_enabled(False,False)
        from safety_app.modules.safety_review.review_runs import ReviewRuns
        reviews=ReviewRuns(rt)
        handed={row['case_id'] for row in rt.db.list('safety_handoff')}
        pending_parents={row['parent_job_id'] for row in rt.db.list('video_safety_case')
                         if row['escalation_enabled'] and row['id'] not in handed}
        for run in rt.db.list('review_run'):
            if rt.jobs.get(run['job_id']).status in ('queued','running') or run['job_id'] in pending_parents:
                reviews.cancel(run['id'],run['site_id'])
        # Also stop undispatched teams belonging to completed legacy analyses.
        # Re-enabling AI must not silently recover this old outbox work.
        for id in pending_parents:rt.jobs.cancel(id)
        if pipeline.course_review:pipeline.playing=False
        for job in rt.jobs.list():
            if job.kind in ('event_review','facility_review','periodic_review','annotation_assistance','safety_chat','video_analysis','video_safety_review','live_review','live_screening') and job.status in ('queued','running'):rt.jobs.cancel(job.id)
    else:
        from app.main import pipeline
        if pipeline.review and pipeline.review.always_on and not pipeline.course_review:pipeline.review.set_enabled(True,True)
    return rt.budgets.status()
class BudgetInput(BaseModel):
    day_usd:float
    month_usd:float
    workflow_usd:float
    agent_usd:float
@router.put('/budgets/settings')
def settings(body:BudgetInput,user:Operator=Depends(operator)):
    import json,math,os,tempfile
    values=body.model_dump()
    if any(not math.isfinite(v) or v<0 for v in values.values()):raise ValueError('Budgets must be finite and nonnegative')
    rt=runtime();rt.config['limits'].update(values)
    path=rt.root/'configs/agents.yaml';fd,name=tempfile.mkstemp(dir=path.parent,prefix='.agents-')
    with os.fdopen(fd,'w') as stream:json.dump(rt.config,stream,indent=2)
    os.replace(name,path);rt.audit.write('budget_settings_changed',arguments=values);return rt.budgets.status()
class ReconcileInput(BaseModel):
    actual_usd:float|None=None
@router.post('/budgets/reservations/{id}/reconcile')
def reconcile(id:str,body:ReconcileInput,user:Operator=Depends(operator)):
    runtime().budgets.reconcile(id,body.actual_usd)
    runtime().audit.write('operator_reconciliation',provenance={'reservation_id':id,'reviewer_id':user.id,'actual_usd':body.actual_usd})
    return runtime().budgets.status()

from safety_app.domain.models import TrialResetPlan,TrialArchive,TrialResetRequest
from safety_app.infrastructure.trials import Trials,TrialConflict
@router.get('/trials/reset-preview',response_model=TrialResetPlan)
def trial_preview(user:Operator=Depends(operator)):
    plan=Trials(runtime()).preview()
    from app.trainer.api import tasks
    if tasks:
        plan.running_job_ids.extend(tasks);plan.can_reset=False
    return plan
@router.get('/trials/archives',response_model=list[TrialArchive])
def trial_archives(user:Operator=Depends(operator)):return Trials(runtime()).list()

async def pause_trial_player():
    """Join active player work before changing trial visibility. Keep its configuration."""
    import asyncio
    from app.main import pipeline
    pipeline.playing=False
    if pipeline.review:
        pipeline.review.set_enabled(False,False)
        await pipeline.review.close()
    tasks=[task for task in (pipeline.task,pipeline.cv_task) if task is not None]
    for task in tasks:task.cancel()
    if tasks:await asyncio.gather(*tasks,return_exceptions=True)
    if pipeline.source:await pipeline.source_work(pipeline.source.close)
    pipeline.task=None;pipeline.cv_task=None;pipeline.source=None;pipeline.session=None
    pipeline.reset();pipeline.health={'state':'idle','message':'Trial reset. Choose a source to start again.'}
    from app.agents.service import ReviewService
    pipeline.review=ReviewService(pipeline.evidence,pipeline.repository,lambda:pipeline.config,pipeline.current)
    await pipeline.emit({'type':'clear'})
    await pipeline.emit({'type':'state',**pipeline.state()})

@router.post('/trials/reset',response_model=TrialArchive)
async def trial_reset(body:TrialResetRequest,user:Operator=Depends(operator)):
    from fastapi import HTTPException
    trials=Trials(runtime())
    plan=trial_preview(user)
    if not plan.can_reset:raise HTTPException(409,'Stop running jobs before resetting trials')
    if body.plan_id!=plan.plan_id:raise HTTPException(409,'Trial data changed. Refresh the preview')
    await pause_trial_player()
    try:return trials.reset(body.plan_id,body.name,user.id)
    except TrialConflict as exc:raise HTTPException(409,str(exc))

@router.post('/trials/archives/{id}/restore',response_model=TrialArchive)
async def trial_restore(id:str,user:Operator=Depends(operator)):
    from fastapi import HTTPException
    trials=Trials(runtime())
    if not trial_preview(user).can_reset:raise HTTPException(409,'Stop running jobs before restoring trial views')
    await pause_trial_player()
    try:return trials.restore(id,user.id)
    except TrialConflict as exc:raise HTTPException(409,str(exc))
