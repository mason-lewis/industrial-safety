"""Read-only reporting inputs; sampled observations are not compliance rates."""
from collections import Counter
from datetime import datetime,timezone
from safety_app.domain.video_safety import SafetyScreening, SafetyMetrics
from safety_app.modules.safety_review.video_cases import overview


def timestamp(value):
    if value is None:return None
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Metric window timestamps require an explicit timezone')
    return result.astimezone(timezone.utc)


def metrics(rt,site_id,since=None,until=None,include_fixtures=False):
    lo,hi=timestamp(since),timestamp(until)
    if lo and hi and hi<lo:raise ValueError('Metric end precedes start')
    screenings=[SafetyScreening.model_validate(row) for row in rt.db.list('safety_screening')
        if row['site_id']==site_id and (include_fixtures or row['mode']!='fixture')
        and (lo is None or timestamp(row['created_at'])>=lo) and (hi is None or timestamp(row['created_at'])<hi)]
    # Same recording/camera/category/PPE item and cited PTS is one observed check.
    # Later interpretations replace earlier ones only in this derived read; records remain immutable.
    latest={};frame_keys=set()
    for signal in sorted(screenings,key=lambda signal:signal.created_at):
        times=tuple(sorted(rt.db.get('frame',ref)['timestamp_ms'] for ref in signal.check.evidence_refs))
        key=(signal.camera_id,signal.source_hash,signal.check.category,signal.check.ppe_item,times)
        latest[key]=signal
        frame_keys.update((signal.camera_id,signal.source_hash,stamp) for stamp in times)
    signals=list(latest.values());selected_ids={signal.id for signal in screenings}
    views=[view for analysis_id in {signal.analysis_id for signal in screenings}
           for view in overview(rt,analysis_id,site_id).cases if any(signal.id in selected_ids for signal in view.screenings)]
    episodes={view.case.episode_key for view in views}
    categories=Counter()
    for episode in episodes:
        view=next(view for view in views if view.case.episode_key==episode);categories[view.case.category]+=1
    # Reruns share a coarse source-time episode key; take the latest completed assessment per episode.
    assessed={}
    for view in views:
        if view.assessment:
            old=assessed.get(view.case.episode_key)
            if old is None or old.created_at<view.assessment.created_at:assessed[view.case.episode_key]=view.assessment
    findings=Counter();recommendations=[]
    for assessment in assessed.values():
        for finding in assessment.findings:
            findings[finding.classification]+=1
            for action in finding.corrective_actions:
                recommendations.append({'case_id':assessment.case_id,'finding_id':finding.id,'status':finding.status,
                    'classification':finding.classification,**action.model_dump(mode='json')})
    pending={view.case.episode_key for view in views if view.status in ('queued','running','awaiting_video_completion')}
    deferred={view.case.episode_key for view in views if view.status=='deferred'}
    failed={view.case.episode_key for view in views if view.status in ('failed','interrupted','budget_exceeded','cancelled')}
    requests={};responses={}
    job_ids={signal.job_id for signal in screenings}|{view.review_job.id for view in views if view.review_job}
    if job_ids:
        with rt.audit.db.connect() as conn:
            # Site scope comes from validated screening/case records, never a caller job ID.
            import json
            rows=conn.execute("SELECT body FROM audit WHERE json_extract(body,'$.event_type') IN ('model_request','model_response') AND json_extract(body,'$.job_id') IN (SELECT value FROM json_each(?))",(json.dumps(sorted(job_ids)),)).fetchall()
        import json
        for row in rows:
            value=json.loads(row['body'])
            if value.get('job_id') not in job_ids:continue
            id=value['resources'].get('model_request_id')
            if value['event_type']=='model_request':requests[id]=value
            else:responses[id]=value
    cost=0;reserved=0;unknown=0;inputs=0;outputs=0
    for id,request in requests.items():
        resources=responses.get(id,{}).get('resources',{})
        inputs+=resources.get('input_tokens') or 0;outputs+=resources.get('output_tokens') or 0
        if request.get('configuration',{}).get('fake_provider'):continue
        if resources.get('cost_usd') is None:
            unknown+=1;reserved+=request['resources'].get('reserved_usd') or 0
        else:cost+=resources['cost_usd']
    current={}
    for view in views:
        old=current.get(view.case.episode_key)
        stamp=view.assessment.created_at if view.assessment else view.case.created_at
        old_stamp=(old.assessment.created_at if old.assessment else old.case.created_at) if old else ''
        if old is None or stamp>old_stamp:current[view.case.episode_key]=view
    return SafetyMetrics(site_id=site_id,since=since,until=until,includes_fixtures=include_fixtures,
        screening_checks=len(signals),assessed_checks=sum(signal.check.result!='unknown' for signal in signals),
        unknown_checks=sum(signal.check.result=='unknown' for signal in signals),suspected_episodes=len(episodes),
        by_category=dict(categories),high_priority_episodes=sum(view.priority=='high' for view in current.values()),
        assessed_cases=len(assessed),pending_cases=len(pending-assessed.keys()),deferred_cases=len(deferred-assessed.keys()),
        failed_cases=len(failed-assessed.keys()),findings_by_classification=dict(findings),recommendations=recommendations,
        sampled_frame_count=len(frame_keys),model_requests=len(requests),input_tokens=inputs,output_tokens=outputs,
        estimated_cost_usd=cost,unknown_cost_requests=unknown,unresolved_reserved_usd=reserved,limitations=[
            'Counts describe sampled screening and current specialist state, not continuous monitoring, injury incidence or regulatory compliance rates.',
            'Repeated source/camera/category/time buckets are grouped as suspected episodes; semantic incident identity is not established.',
            'Unknown visibility is excluded from assessed-check counts; clear checks do not certify safety.',
            'Windows use screening-record UTC time; actual event calendar dates may be unknown.',
            'No report, notification or scheduled delivery has been sent. Fixtures are excluded unless explicitly requested.',
            'Usage is cumulative for selected video analyses and linked judgments, including nonsafety video reasoning. Estimates are not provider invoices; unreported tokens and charges may be incomplete.',
        ])
