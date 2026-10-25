"""Layer two reuses validated visual evidence, the existing boss, specialists and challenge."""
import asyncio
from safety_app.domain.models import Observation
from safety_app.domain.video_safety import VideoSafetyCase,SafetyScreening,VideoSafetyAssessment,ManagerAttention
from safety_app.agents.video_observer.models import ObserverResult
from safety_app.workflows.event_review import review_with_evidence
from safety_app.workflows.video_analysis import scoped_runtime
from safety_app.modules.safety_review.video_cases import DOMAINS,stable


def fixture_models(job,case,observations):
    from safety_app.workflows.fixtures import team_fixture_models
    finding_id='fixture-safety-'+case.id
    output={'safety_reviewer':{'findings':[{'id':finding_id,'event_id':case.id,'review_id':job.run_id,
        'conclusion':'Fixture specialist assessment; no real safety judgment performed.',
        'evidence_refs':observations[0].evidence_refs,'uncertainty':['Fixed fixture responses; not a real violation assessment.']} ]},
        'challenge_reviewer':{'checked_finding_ids':[finding_id],'issues':[],'limitations':['Fixture only; no legal or safety accuracy established.']}}
    return team_fixture_models(output,specialist=DOMAINS[case.category])


async def assess(rt,job,model_factory=None):
    case=VideoSafetyCase.model_validate(rt.db.get('video_safety_case',job.payload['case_id']))
    parent=rt.jobs.get(case.parent_job_id)
    if case.site_id!=job.site_id or parent.run_id!=job.payload.get('budget_workflow_id') or case.parent_job_id!=job.payload.get('parent_job_id'):
        raise ValueError('Safety handoff parent/site/budget scope mismatch')
    full=parent.payload.get('review_strategy')=='full'
    if parent.status!='completed' and not (full and parent.status=='running'):
        raise ValueError('Video review must complete before specialist dispatch')
    expected={'analysis_id':case.analysis_id,'media_ids':[case.media_id],'event_id':case.id,
        'mode':case.mode}
    if not full:expected.update(start_ms=case.start_ms,end_ms=case.end_ms)
    elif not case.start_ms<=job.payload['start_ms']<=job.payload['end_ms']<=case.end_ms or job.payload['end_ms']-job.payload['start_ms']>10000:
        raise ValueError('Specialist packet is outside its episode or exceeds ten seconds')
    if parent.run_id!=case.analysis_id or any(job.payload.get(key)!=value for key,value in expected.items()):
        raise ValueError('Safety handoff has modified media, interval, event or mode')
    media=rt.db.get('media',case.media_id)
    if media['content_hash']!=case.source_hash or media['camera_id']!=case.camera_id or rt.camera(case.camera_id).site_id!=case.site_id:
        raise ValueError('Safety handoff media provenance has changed')
    link_ids={row['screening_id'] for row in rt.db.list('safety_case_evidence') if row['case_id']==case.id}
    ids=job.payload['screening_ids']
    if not ids or not set(ids)<=link_ids:raise ValueError('Screening evidence outside assigned case')
    observations=[];limits=[]
    for id in ids:
        signal=SafetyScreening.model_validate(rt.db.get('safety_screening',id))
        check=signal.check
        if signal.site_id!=job.site_id or signal.analysis_id!=case.analysis_id or signal.media_id!=case.media_id:
            raise ValueError('Foreign screening record in handoff')
        if full and case.category in ('struck_by','fall'):
            from safety_app.workflows.temporal_detail import refine_assigned_case
            check=await refine_assigned_case(rt,job,case,signal,model_factory)
        observation=Observation(id='visual-'+id,media_id=case.media_id,start_ms=check.start_ms,end_ms=check.end_ms,
            description=check.condition,evidence_refs=check.evidence_refs,method='ai',uncertainty=['Layer-one suspected condition, not confirmed.','Motion between sampled stills is unknown.',*check.limitations])
        # A closer inspection appends a new observation version; it cannot overwrite
        # the immutable observation saved by an earlier assessment attempt.
        try:
            previous=rt.db.get('observation',observation.id)
            if previous!=observation.model_dump(mode='json'):
                import hashlib,json
                observation.id+='-'+hashlib.sha256(json.dumps(observation.model_dump(mode='json',exclude={'id'}),sort_keys=True).encode()).hexdigest()[:16]
        except KeyError:pass
        observations.append(observation)
        limits.extend(check.limitations)
    if job.payload.get('omitted_screening_ids'):limits.append('Additional screening observations omitted from the bounded specialist packet; not independently assessed in this handoff.')
    if case.mode=='fixture' and model_factory is None:model_factory=fixture_models(job,case,observations)
    scoped=scoped_runtime(rt)
    if full:
        from safety_app.workflows.full_review import frozen_runtime
        from safety_app.modules.safety_review.review_runs import ReviewRuns
        scoped=frozen_runtime(rt,ReviewRuns(rt).get(parent.run_id,parent.site_id))
    current=asyncio.current_task()
    async def watch():
        ticks=0
        while True:
            await asyncio.sleep(1)
            if rt.jobs.cancelled(job.id):current.cancel();return
            ticks+=1
            if ticks%20==0:rt.jobs.heartbeat(job.id)
    watcher=asyncio.create_task(watch())
    def completed(review):
        snapshot=VideoSafetyAssessment(id=case.id,case_id=case.id,analysis_id=case.analysis_id,site_id=job.site_id,
            review_job_id=job.id,review_id=job.run_id,mode=case.mode,findings=review['safety']['findings'],
            specialist_assessments=review['delegations'],challenge=review['challenge'],investigation=review['investigation'],
            limitations=review['inspection_questions'],triage=review['safety'].get('triage'),
            triage_supported=review['challenge'].get('triage_supported'))
        records=[('video_safety_assessment',snapshot)]
        if snapshot.triage and snapshot.triage.priority=='high' and snapshot.triage_supported:
            records.append(('manager_attention',ManagerAttention(id=stable(case.id,job.id,'reviewed-attention'),case_id=case.id,
                screening_id=ids[0],episode_key=case.episode_key,site_id=case.site_id,mode=case.mode,
                reason=snapshot.triage.reason,evidence_status='specialist_supported',event_time_utc=case.event_time_utc)))
        return records
    try:
        refs=await review_with_evidence(scoped,job,model_factory,ObserverResult(observations=observations,visibility_limits=list(dict.fromkeys(limits))),completed_record=completed)
        return [*refs,case.id]
    finally:
        watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
