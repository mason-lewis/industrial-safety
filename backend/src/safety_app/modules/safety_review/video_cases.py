"""Durable evidence-linked cases. Routing is Python-owned, with no delivery adapter."""
import hashlib
from datetime import datetime,timedelta
from safety_app.domain.models import CandidateEvent, Observation
from safety_app.domain.video_safety import (
    SafetyScreening,VideoSafetyCase,SafetyCaseEvidence,SafetyHandoff,ManagerAttention,
    VideoSafetyAssessment,VideoSafetyCaseView,VideoSafetyOverview,
)

# Scene-wide hazards (fire, smoke) spread and move, so their boxes rarely overlap from one check to the
# next: one case per category per interval, joined across adjacent intervals regardless of box.
SCENE_WIDE={'fire_burn'}
DOMAINS={'ppe':'ppe','blocked_access':'emergency','unstable_stack':'surfaces','slip_trip':'surfaces','struck_by':'vehicles','fall':'surfaces','fire_burn':'emergency'}


def stable(*parts):
    return hashlib.sha256('|'.join(map(str,parts)).encode()).hexdigest()[:32]


def screening_policy(rt):
    policy=rt.config.get('video_screening',{})
    values={key:policy.get(key,default) for key,default in [('frames',2),('detail_crops',1),('max_escalations',2),('output_per_request',1200),('input_tokens',16000)]}
    if type(values['frames']) is not int or not 2<=values['frames']<=6:raise ValueError('Screening frames must be 2..6')
    if type(values['detail_crops']) is not int or not 0<=values['detail_crops']<=2:raise ValueError('Detail crops must be 0..2')
    if type(values['max_escalations']) is not int or not 0<=values['max_escalations']<=4:raise ValueError('Safety escalations must be 0..4')
    for name in ('input_tokens','output_per_request'):
        if type(values[name]) is not int or values[name]<256:raise ValueError('Screening token caps must be integers >=256')
    return values


def record_screenings(rt,job,body,media,chunk):
    """Commit compact checks and outbox cases before further model work."""
    if body.review_strategy=='full':return record_full_screenings(rt,job,body,media,chunk)
    pending=[]
    existing=[VideoSafetyCase.model_validate(row) for row in rt.db.list('video_safety_case') if row['analysis_id']==job.run_id]
    clip_ms=rt.config['limits']['clip_ms']
    for index,check in enumerate(chunk.screening_checks):
        signal=SafetyScreening(id=stable(chunk.id,index),analysis_id=job.run_id,job_id=job.id,chunk_id=chunk.id,
            site_id=job.site_id,media_id=media.id,source_hash=media.content_hash,camera_id=media.camera_id,
            mode=body.mode,check=check,required_ppe=body.required_ppe)
        pending.append(('safety_screening',signal))
        if check.result!='suspected' or not (check.needs_judgment or check.priority=='high'):continue
        episode=stable(job.site_id,media.camera_id,media.content_hash,check.category,clip_ms,check.start_ms//clip_ms)
        candidates=[case for case in existing if case.category==check.category and case.episode_key==episode and case.start_ms<=check.start_ms<=check.end_ms<=case.end_ms]
        if candidates:case=candidates[0]
        else:
            lo=max(media.start_ms,(check.start_ms//clip_ms)*clip_ms)
            hi=min(media.end_ms,lo+clip_ms)
            if check.end_ms>hi:hi=check.end_ms;lo=max(media.start_ms,hi-clip_ms)
            case=VideoSafetyCase(id=stable(job.run_id,check.category,lo,hi),analysis_id=job.run_id,parent_job_id=job.id,
                site_id=job.site_id,media_id=media.id,camera_id=media.camera_id,source_hash=media.content_hash,
                episode_key=episode,category=check.category,mode=body.mode,start_ms=lo,end_ms=hi,
                escalation_enabled=body.escalate_to_safety_team,episode_bucket_ms=clip_ms)
            if media.capture_time_utc:case.event_time_utc=(datetime.fromisoformat(media.capture_time_utc)+timedelta(milliseconds=check.start_ms)).isoformat()
            pending.append(('video_safety_case',case));existing.append(case)
            pending.append(('event',CandidateEvent(id=case.id,site_id=job.site_id,camera_id=media.camera_id,
                event_type=check.category,start_ms=lo,end_ms=hi,media_ids=[media.id],producer_version='video-screening-1.0')))
        pending.append(('safety_case_evidence',SafetyCaseEvidence(id=stable(case.id,signal.id),case_id=case.id,screening_id=signal.id)))
        if check.priority=='high':
            pending.append(('manager_attention',ManagerAttention(id=stable(case.id,signal.id,'attention'),case_id=case.id,
                screening_id=signal.id,episode_key=case.episode_key,site_id=job.site_id,mode=body.mode,reason=check.immediate_reason,event_time_utc=case.event_time_utc)))
    if pending:rt.db.save_many(pending,job.id)
    return [case.id for case in existing]


def signals_for(rt,case_id):
    ids={row['screening_id'] for row in rt.db.list('safety_case_evidence') if row['case_id']==case_id}
    return [SafetyScreening.model_validate(rt.db.get('safety_screening',id)) for id in sorted(ids)]


def priority(signals):
    return 'high' if any(signal.check.priority=='high' for signal in signals) else 'routine'


def box_overlap(a,b):
    if a is None or b is None:return 0
    x=max(a[0],b[0]);y=max(a[1],b[1])
    intersection=max(0,min(a[0]+a[2],b[0]+b[2])-x)*max(0,min(a[1]+a[3],b[1]+b[3])-y)
    union=a[2]*a[3]+b[2]*b[3]-intersection
    return intersection/union if union>0 else 0


def record_full_screenings(rt,job,body,media,chunk):
    """Conservative spatial episode grouping, retaining every source occurrence."""
    existing=[VideoSafetyCase.model_validate(row) for row in rt.db.list('video_safety_case') if row['analysis_id']==job.run_id]
    for index,check in enumerate(chunk.screening_checks):
        signal_id=stable(chunk.id,index)
        # Recovery after a saved chunk is idempotent, including immutable timestamps.
        try:signal=SafetyScreening.model_validate(rt.db.get('safety_screening',signal_id))
        except KeyError:
            signal=SafetyScreening(id=signal_id,analysis_id=job.run_id,job_id=job.id,chunk_id=chunk.id,
                site_id=job.site_id,media_id=media.id,source_hash=media.content_hash,camera_id=media.camera_id,
                mode=body.mode,check=check,required_ppe=body.required_ppe)
        rt.db.save('safety_screening',signal,immutable=True)
        if check.result!='suspected':continue
        links=[r for r in rt.db.list('safety_case_evidence') if r['screening_id']==signal_id]
        if links:continue
        box=check.subject_box or check.person_box
        interval=chunk.intervals[0]
        candidates=[case for case in existing if case.category==check.category and case.ppe_item==check.ppe_item
            and case.source_hash==media.content_hash and case.camera_id==media.camera_id
            and case.start_ms<=interval.start_ms<=case.end_ms
            and (check.category in SCENE_WIDE or box_overlap(case.subject_box,box)>=.5)]
        if candidates:
            case=sorted(candidates,key=lambda c:(-box_overlap(c.subject_box,box),c.id))[0]
            case.end_ms=max(case.end_ms,interval.end_ms)
        else:
            id=stable(job.run_id,chunk.id,index,'episode-v2')
            case=VideoSafetyCase(id=id,analysis_id=job.run_id,parent_job_id=job.id,site_id=job.site_id,
                media_id=media.id,camera_id=media.camera_id,source_hash=media.content_hash,episode_key=id,
                category=check.category,mode=body.mode,start_ms=interval.start_ms,end_ms=interval.end_ms,
                escalation_enabled=body.escalate_to_safety_team,subject_box=box,ppe_item=check.ppe_item,
                routing_version='video-safety-2.0')
            if media.capture_time_utc:case.event_time_utc=(datetime.fromisoformat(media.capture_time_utc)+timedelta(milliseconds=check.start_ms)).isoformat()
            existing.append(case)
        records=[('video_safety_case',case),('event',CandidateEvent(id=case.id,site_id=job.site_id,camera_id=media.camera_id,
            event_type=case.category,start_ms=case.start_ms,end_ms=case.end_ms,media_ids=[media.id],producer_version='video-screening-2.0')),
            ('safety_case_evidence',SafetyCaseEvidence(id=stable(case.id,signal.id),case_id=case.id,screening_id=signal.id))]
        if check.priority=='high':
            records.append(('manager_attention',ManagerAttention(id=stable(case.id,signal.id,'attention'),case_id=case.id,
                screening_id=signal.id,episode_key=case.episode_key,site_id=job.site_id,mode=body.mode,
                reason=check.immediate_reason,event_time_utc=case.event_time_utc,created_at=signal.created_at)))
        # The link is the recovery marker: commit it with its episode and attention.
        with rt.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state=db.execute('SELECT status FROM jobs WHERE id=?',(job.id,)).fetchone()
            if state and state['status']=='cancelled':
                import asyncio
                raise asyncio.CancelledError()
            for kind,record in records:
                db.execute('INSERT INTO objects VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body',
                    (kind,record.id,record.model_dump_json()))
    return [case.id for case in existing]


def episode_closed(rt,run_id,case):
    """An episode can only grow into the next adjacent window. Once that window is published (or the run
    ended), the case is final and its team review can start while later windows are still screened."""
    try:run=rt.db.get('review_run',run_id)
    except KeyError:return True
    if run.get('state') in ('ended','cancelled','interrupted','failed','budget_exceeded'):return True
    windows=sorted((w for w in rt.db.list('review_window') if w.get('run_id')==run_id),key=lambda w:w['index'])
    boundary=next((w['end_ms'] for w in windows if w['end_ms']>=case.end_ms),None)
    if boundary is None:return False
    return any(w['start_ms']>=boundary and w.get('status') in ('completed','failed','skipped','cancelled') for w in windows)


def dispatch_ready_cases(rt,parent_job_id=None):
    """Recoverable outbox: idempotent jobs, capped per analysis, no failed-job reruns."""
    if rt.budgets.state('stopped')=='1':return []
    cases=[VideoSafetyCase.model_validate(row) for row in rt.db.list('video_safety_case')
           if parent_job_id is None or row['parent_job_id']==parent_job_id]
    grouped={}
    for case in cases:grouped.setdefault(case.parent_job_id,[]).append(case)
    queued=[]
    for parent_id,items in grouped.items():
        parent=rt.jobs.get(parent_id)
        full=parent.payload.get('review_strategy')=='full'
        try:
            existing_plan=rt.db.get('video_safety_dispatch_plan',parent_id)
            handed={row['case_id'] for row in rt.db.list('safety_handoff')}
            if not full and set(existing_plan['case_ids'])<=handed:continue
        except KeyError:pass
        running=parent.status!='completed'
        if running:
            # Full reviews (recorded or Live) hand off each episode as soon as it is closed, while screening continues.
            if not (full and parent.status=='running'):continue
        items.sort(key=lambda case:(priority(signals_for(rt,case.id))!='high',case.start_ms,case.id))
        enabled=[case for case in items if case.escalation_enabled]
        if full:plan={'case_ids':[case.id for case in enabled]}
        else:
            try:plan=rt.db.get('video_safety_dispatch_plan',parent_id)
            except KeyError:
                plan={'id':parent_id,'case_ids':[case.id for case in enabled[:screening_policy(rt)['max_escalations']]]}
                rt.db.save('video_safety_dispatch_plan',plan,immutable=True)
        for case in enabled:
            if case.id not in plan['case_ids']:continue
            if running and not episode_closed(rt,parent.run_id,case):continue
            try:rt.db.get('safety_handoff',case.id);continue
            except KeyError:pass
            if rt.jobs.cancelled(parent_id):break
            signals=signals_for(rt,case.id)
            signals.sort(key=lambda signal:(signal.check.priority!='high',signal.check.start_ms,signal.id))
            retained=[];refs=set();omitted=[]
            for signal in signals:
                added=set(signal.check.evidence_refs)
                times=[rt.db.get('frame',ref)['timestamp_ms'] for ref in refs|added]
                if len(refs|added)>min(6,rt.config['limits']['frames']) or len(retained)>=4 or (full and max(times)-min(times)>10000):
                    omitted.append(signal.id);continue
                retained.append(signal);refs.update(added)
            if not retained:continue
            payload={'case_id':case.id,'media_ids':[case.media_id],'start_ms':case.start_ms,'end_ms':case.end_ms,
                'priority':priority(signals),
                'event_id':case.id,'parent_job_id':parent_id,'analysis_id':case.analysis_id,'mode':case.mode,
                'screening_ids':[signal.id for signal in retained],'omitted_screening_ids':omitted,
                'budget_workflow_id':parent.run_id,'context':f'Layer-one suspected {case.category}. Delegate applicability, then {DOMAINS[case.category]} for judgment. Other domains only when needed. Visual signals are provisional; no legal violation is established.',
                'activity':'unknown','jurisdiction':parent.payload.get('jurisdiction','unknown')}
            if full:
                times=[rt.db.get('frame',ref)['timestamp_ms'] for ref in refs]
                payload.update(start_ms=min(min(times),min(s.check.start_ms for s in retained)),
                    end_ms=max(max(times),max(s.check.end_ms for s in retained)),review_run_id=parent.run_id)
            payload['event_date']=case.event_time_utc[:10] if case.event_time_utc else None
            child=rt.jobs.start('video_safety_review','video-safety:'+case.id,payload,case.site_id)
            handoff=SafetyHandoff(id=case.id,case_id=case.id,parent_job_id=parent_id,review_job_id=child.id,
                screening_ids=payload['screening_ids'],omitted_screening_ids=omitted)
            rt.db.save('safety_handoff',handoff,immutable=True)
            rt.audit.write('video_safety_handoff',job_id=child.id,run_id=child.run_id,parent_run_id=parent.run_id,
                output=handoff.model_dump(mode='json'),provenance={'category':case.category,'priority':priority(signals),'mode':case.mode,'shared_budget_workflow_id':parent.run_id})
            queued.append(child.id)
    return queued


def overview(rt,analysis_id,site_id):
    screenings=[SafetyScreening.model_validate(row) for row in rt.db.list('safety_screening') if row['analysis_id']==analysis_id and row['site_id']==site_id]
    cases=[]
    for row in rt.db.list('video_safety_case'):
        if row['analysis_id']!=analysis_id or row['site_id']!=site_id:continue
        case=VideoSafetyCase.model_validate(row);signals=signals_for(rt,case.id)
        try:handoff=SafetyHandoff.model_validate(rt.db.get('safety_handoff',case.id))
        except KeyError:handoff=None
        child=rt.jobs.get(handoff.review_job_id) if handoff else None
        try:assessment=VideoSafetyAssessment.model_validate(rt.db.get('video_safety_assessment',case.id))
        except KeyError:assessment=None
        parent=rt.jobs.get(case.parent_job_id)
        if assessment:
            status='needs_review' if any(f.status=='challenged' or f.classification=='insufficient_evidence' for f in assessment.findings) or assessment.challenge.get('issues') else 'assessed'
        elif child:status=child.status if child.status!='completed' else 'needs_review'
        elif parent.status in ('queued','running'):status='awaiting_video_completion'
        elif parent.status=='cancelled':status='cancelled'
        else:status='deferred'
        if status=='budget_deferred':status='deferred'
        urgency=priority(signals)
        if assessment and assessment.triage and assessment.triage_supported:
            urgency=assessment.triage.priority
        attention=[ManagerAttention.model_validate(item) for item in rt.db.list('manager_attention') if item['case_id']==case.id]
        cases.append(VideoSafetyCaseView(case=case,screenings=signals,handoff=handoff,review_job=child,assessment=assessment,
            unassessed_screening_ids=[s.id for s in signals if not assessment or not handoff or s.id not in handoff.screening_ids],
            priority=urgency,manager_attention=attention,status=status,report_condition='immediate' if urgency=='high' else 'scheduled'))
    return VideoSafetyOverview(analysis_id=analysis_id,screenings=screenings,cases=cases)
