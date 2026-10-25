from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from safety_app.api.auth import Operator, operator
from safety_app.domain.models import HumanDecision, SourceExcerpt
from safety_app.domain.review_runs import ReviewRun, ReviewRunStatus, RunDecisionInput, LiveControl
from safety_app.domain.review_runs import ReviewActivityPage
from safety_app.domain.video_analysis import VideoAnalysisRequest
from safety_app.infrastructure.runtime import runtime
from safety_app.modules.safety_review.review_runs import ReviewRuns

router=APIRouter(prefix='/api/review-runs')
_live=None


def live_controller():
    global _live
    from app.main import pipeline
    from safety_app.modules.monitoring.live_review import LiveReview
    if _live is None or _live.rt is not runtime():_live=LiveReview(runtime(),pipeline)
    return _live


@router.get('',response_model=list[ReviewRun])
def runs(user:Operator=Depends(operator)):
    return [r for r in runtime().db.list('review_run') if r['site_id']==user.site_id][-100:][::-1]


@router.post('/live',response_model=ReviewRunStatus)
async def start_live(body:VideoAnalysisRequest,user:Operator=Depends(operator)):
    if runtime().camera(body.camera_id).site_id!=user.site_id:raise ValueError('Camera outside site')
    return await live_controller().start(body,user.site_id)


@router.get('/{id}',response_model=ReviewRunStatus)
def status(id:str,user:Operator=Depends(operator)):
    return ReviewRuns(runtime()).status(id,user.site_id)


@router.get('/{id}/activity',response_model=ReviewActivityPage)
def activity(id:str,after_sequence:int=Query(0,ge=0),user:Operator=Depends(operator)):
    from safety_app.modules.safety_review.run_report import run_events
    rt=runtime();run=ReviewRuns(rt).get(id,user.site_id)
    events=run_events(rt,id,run.job_id,user.site_id,after_sequence,200)
    return ReviewActivityPage(events=events,cursor=events[-1]['sequence'] if events else after_sequence)


@router.get('/{id}/details/{detail_id}/crops/{index}')
def detail_crop(id:str,detail_id:str,index:int,user:Operator=Depends(operator)):
    import hashlib
    rt=runtime();run=ReviewRuns(rt).get(id,user.site_id)
    detail=rt.db.get('temporal_detail',detail_id)
    if detail['run_id']!=id or not 0<=index<len(detail['crops']):raise ValueError('Crop outside this review')
    crop=detail['crops'][index]
    parent=rt.db.get('frame',crop['parent_frame_id'])
    if parent['media_id']!=id or parent['content_hash']!=crop['parent_hash'] or crop['source_hash']!=run.source_hash:raise ValueError('Crop parent mismatch')
    path=rt.root/'data/clips'/f"{crop['content_hash']}.jpg"
    if hashlib.sha256(path.read_bytes()).hexdigest()!=crop['content_hash']:raise ValueError('Crop hash mismatch')
    return FileResponse(path,media_type='image/jpeg')


@router.post('/{id}/cancel',response_model=ReviewRunStatus)
async def cancel(id:str,user:Operator=Depends(operator)):
    run=ReviewRuns(runtime()).get(id,user.site_id)
    if run.kind=='live':
        controller=live_controller()
        if controller.run and controller.run.id==id:await controller.pipeline.playback('pause')
    return ReviewRuns(runtime()).cancel(id,user.site_id)


@router.post('/{id}/resume',response_model=ReviewRunStatus)
def resume(id:str,user:Operator=Depends(operator)):
    return ReviewRuns(runtime()).resume(id,user.site_id)


@router.post('/{id}/playback',response_model=ReviewRunStatus)
async def playback(id:str,body:LiveControl,user:Operator=Depends(operator)):
    ReviewRuns(runtime()).get(id,user.site_id)
    return await live_controller().control(id,body.action,user.site_id)


@router.post('/{id}/decisions',response_model=HumanDecision)
def decision(id:str,body:RunDecisionInput,user:Operator=Depends(operator)):
    return ReviewRuns(runtime()).decide(id,body,user)


@router.get('/{id}/sources',response_model=list[SourceExcerpt])
def cited_sources(id:str,user:Operator=Depends(operator)):
    status=ReviewRuns(runtime()).status(id,user.site_id)
    ids={ref for case in status.safety.cases if case.assessment
         for finding in case.assessment.findings for ref in finding.source_refs}
    result=[]
    for ref in sorted(ids):
        try:source=SourceExcerpt.model_validate(runtime().db.get('source',ref))
        except KeyError:continue  # The UI retains and labels unresolved citation IDs.
        if source.site_id!=user.site_id:raise ValueError('Cited source outside site')
        result.append(source)
    return result


@router.get('/{id}/report')
def report(id:str,format:str=Query('html',pattern='^(html|json)$'),inline:bool=Query(False,description='Show in the browser instead of downloading'),user:Operator=Depends(operator)):
    from safety_app.modules.safety_review.run_report import report_data,report_html
    data=report_data(runtime(),id,user.site_id)
    headers={'Content-Disposition':f'{"inline" if inline is True else "attachment"}; filename="industrial-safety-{data["run"]["kind"]}-{id}.{format}"','Cache-Control':'private, no-store'}
    return HTMLResponse(report_html(data),headers=headers) if format=='html' else JSONResponse(data,headers=headers)


@router.get('/{id}/video')
def video(id:str,user:Operator=Depends(operator)):
    from safety_app.modules.monitoring.recordings import resolve,describe
    run=ReviewRuns(runtime()).get(id,user.site_id)
    if run.kind=='live' and run.state!='ended':raise ValueError('Live source playback is available only after the camera reaches its end')
    path=resolve(runtime().root,run.source_id)
    if describe(path)[0]!=run.source_hash:raise ValueError('Recording hash changed; original evidence remains retained')
    return FileResponse(path)
