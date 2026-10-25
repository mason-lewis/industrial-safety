"""Authenticated recorded-video questions and persisted evidence/checks."""
from fastapi import APIRouter, Depends, Query

from safety_app.api.auth import Operator, operator
from safety_app.domain.models import AuditEvent, Job
from safety_app.domain.video_analysis import RecordedSource, VideoAnalysis, VideoAnalysisRequest, VideoChunk
from safety_app.infrastructure.runtime import runtime
from safety_app.modules.monitoring.recordings import sources
from safety_app.modules.safety_review.video_analysis import VideoAnalysisService
from safety_app.modules.safety_review.video_memory import VideoMemory
from safety_app.domain.video_safety import VideoSafetyOverview,SafetyMetrics

router = APIRouter(prefix='/api/video-analysis')


@router.get('/sources', response_model=list[RecordedSource])
def recordings(user: Operator = Depends(operator)):
    return sources(runtime().root)


@router.get('/safety/metrics',response_model=SafetyMetrics)
def safety_metrics(since:str|None=None,until:str|None=None,include_fixtures:bool=False,user:Operator=Depends(operator)):
    from safety_app.modules.safety_review.safety_metrics import metrics
    return metrics(runtime(),user.site_id,since,until,include_fixtures)


@router.post('', response_model=Job)
def submit(body: VideoAnalysisRequest, user: Operator = Depends(operator)):
    return VideoAnalysisService(runtime()).request(body, user.site_id)


@router.get('', response_model=list[VideoAnalysis])
def analyses(user: Operator = Depends(operator)):
    return VideoAnalysisService(runtime()).list(user.site_id)[-100:][::-1]


def analysis_job(id, user):
    job = runtime().jobs.get(id)
    if job.site_id != user.site_id or job.kind != 'video_analysis':
        raise ValueError('Video analysis job outside scope')
    return job


@router.get('/jobs/{id}/chunks', response_model=list[VideoChunk])
def chunks(id: str, user: Operator = Depends(operator)):
    job = analysis_job(id, user)
    return VideoMemory(runtime().db, job.run_id).chunks()


@router.get('/jobs/{id}/safety',response_model=VideoSafetyOverview)
def safety_layers(id:str,user:Operator=Depends(operator)):
    from safety_app.modules.safety_review.video_cases import overview
    job=analysis_job(id,user)
    return overview(runtime(),job.run_id,user.site_id)


def pipeline_jobs(id,user):
    parent=analysis_job(id,user);ids=[parent.id]
    for row in runtime().db.list('safety_handoff'):
        if row['parent_job_id']==id:
            child=runtime().jobs.get(row['review_job_id'])
            if child.site_id==user.site_id and child.kind=='video_safety_review':ids.append(child.id)
    return ids


@router.get('/jobs/{id}/audit', response_model=list[AuditEvent])
def audit(id: str, limit: int = Query(200, ge=1, le=1000), include_safety:bool=False, user: Operator = Depends(operator)):
    ids=pipeline_jobs(id,user)
    if not include_safety:return runtime().audit.rows(limit, job_id=id)
    import json
    with runtime().audit.db.connect() as conn:
        rows=conn.execute("SELECT sequence,body FROM audit WHERE json_extract(body,'$.job_id') IN ("+','.join('?' for _ in ids)+") ORDER BY sequence DESC LIMIT ?",[*ids,limit]).fetchall()
    return [json.loads(row['body'])|{'sequence':row['sequence']} for row in rows]


@router.get('/{id}', response_model=VideoAnalysis)
def result(id: str, user: Operator = Depends(operator)):
    return VideoAnalysisService(runtime()).get(id, user.site_id)


@router.get('/jobs/{id}/audit/download')
def download(id: str, include_safety:bool=False,user: Operator = Depends(operator)):
    import json
    from fastapi.responses import StreamingResponse

    job = analysis_job(id, user)
    if job.status in ('queued', 'running'):
        raise ValueError('Wait for the analysis to finish before downloading its full trace')
    rt = runtime()
    ids=pipeline_jobs(id,user) if include_safety else [id]
    if any(rt.jobs.get(item).status in ('queued','running') for item in ids):raise ValueError('Wait for linked safety reviews to finish before downloading the full pipeline trace')
    clause="json_extract(body,'$.job_id') IN ("+','.join('?' for _ in ids)+')'
    with rt.audit.db.connect() as conn:
        last = conn.execute('SELECT COALESCE(MAX(sequence),0) FROM audit WHERE '+clause, ids).fetchone()[0]

    def lines():
        cursor = 0
        while True:
            with rt.audit.db.connect() as conn:
                rows = conn.execute('SELECT sequence,body FROM audit WHERE sequence>? AND sequence<=? AND '+clause+' ORDER BY sequence LIMIT 200', [cursor,last,*ids]).fetchall()
            if not rows:
                break
            for row in rows:
                yield json.dumps(json.loads(row['body']) | {'sequence': row['sequence']}) + '\n'
            cursor = rows[-1]['sequence']
    return StreamingResponse(lines(), media_type='application/x-ndjson',
                             headers={'Content-Disposition': 'attachment; filename="recorded-video-analysis.ndjson"'})
