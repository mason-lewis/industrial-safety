"""Coordinate observer, safety delegation, challenge, and atomic review storage."""
import asyncio

from safety_app.agents.challenge_reviewer.models import ChallengeRequest
from safety_app.agents.safety_reviewer.models import SafetyRequest
from safety_app.agents.video_observer.models import ObserverRequest
# Keep the original import paths available to external callers.
from safety_app.workflows.agent_stage import (
    AgentOutputRejected, AgentStageTimeout, dependencies, run_stage,
)
from safety_app.workflows.review_cache import cache_key
from safety_app.workflows.safety_team import SafetyTeam


def _validate_challenge(safety, challenge):
    finding_ids = {finding.id for finding in safety.findings}
    if set(challenge.checked_finding_ids) != finding_ids:
        raise ValueError('Challenge review did not check every finding')
    if not {issue.finding_id for issue in challenge.issues} <= finding_ids:
        raise ValueError('Challenge refers to an unknown finding')
    hypothesis_ids = {hypothesis.id for hypothesis in safety.investigation.hypotheses}
    if set(challenge.checked_hypothesis_ids) != hypothesis_ids:
        raise ValueError('Challenge did not check every causal hypothesis')
    if not {issue.hypothesis_id for issue in challenge.causal_issues} <= hypothesis_ids:
        raise ValueError('Challenge refers to an unknown causal hypothesis')
    if safety.triage:
        if challenge.triage_supported is None:raise ValueError('Challenge must explicitly check manager urgency')
        if challenge.triage_supported and set(safety.triage.finding_ids)&{issue.finding_id for issue in challenge.issues}:
            challenge.triage_supported=False
            challenge.triage_explanation='Urgency support withheld because a dependent finding was challenged.'
            challenge.limitations.append(challenge.triage_explanation)
        if not challenge.triage_supported and not challenge.triage_explanation:raise ValueError('Unsupported urgency needs an explanation')


def _challenged_investigation(safety, challenge):
    investigation = safety.investigation.model_copy(deep=True)
    for issue in challenge.causal_issues:
        for hypothesis in investigation.hypotheses:
            if hypothesis.id == issue.hypothesis_id:
                hypothesis.status = 'unresolved'
                hypothesis.missing_facts.append(issue.problem + ' Alternative: ' + issue.alternative)
        investigation.cause_status = 'not_established'
        investigation.limitations.append(issue.problem)
    return investigation


def _retained_finalization(rt,job,request):
    """Reuse already validated stages only for an identical, settled handoff.

    This completes Python persistence/consistency checks, not another model judgment.
    The provider output, exact input, prompt identity and linked job must all match.
    """
    if job.kind!='video_safety_review':return None
    import hashlib
    from pathlib import Path
    from safety_app.agents.safety_reviewer.models import SafetyResult,SpecialistAssessment
    from safety_app.agents.challenge_reviewer.models import ChallengeResult
    rt.budgets.require_settled(job.payload.get('budget_workflow_id') or job.run_id)
    rows=rt.audit.rows(limit=1000,job_id=job.id)
    if len(rows)>=1000:return None
    def stage(name,arguments,before=None):
        prompt=(Path(__file__).parents[1]/f'agents/{name}/prompt.md').read_text()
        if name=='safety_reviewer':
            import json
            from safety_app.modules.safety_review.catalog import PROFILES
            prompt+='\nSpecialist ownership registry: '+json.dumps(PROFILES)
        expected=hashlib.sha256(prompt.encode()).hexdigest()
        for finish in rows:
            if finish['event_type']!='run_finished' or finish['agent_name']!=name or (before and finish['sequence']>=before):continue
            run_id=finish['run_id']
            start=next((e for e in rows if e['run_id']==run_id and e['event_type']=='run_started'),None)
            calls=[e for e in rows if e['run_id']==run_id and e['event_type']=='model_request']
            if start and start['arguments']==arguments and calls and all(e['configuration'].get('prompt_sha256')==expected for e in calls):return finish,start
        return None
    boss=stage('safety_reviewer',request.model_dump(mode='json'))
    if not boss:return None
    safety=SafetyResult.model_validate(boss[0]['output'])
    challenge_input=ChallengeRequest(findings=safety.findings,investigation=safety.investigation,triage=safety.triage)
    checked=stage('challenge_reviewer',challenge_input.model_dump(mode='json'))
    if not checked or checked[1]['sequence']<=boss[0]['sequence']:return None
    if any(f.event_id!=job.payload.get('event_id') for f in safety.findings):return None
    delegations=[SpecialistAssessment.model_validate(e['output']) for e in reversed(rows)
        if e['event_type']=='specialist_completed' and boss[1]['sequence']<e['sequence']<boss[0]['sequence']]
    if not delegations:return None
    return safety,ChallengeResult.model_validate(checked[0]['output']),delegations,{
        'boss_run_id':boss[0]['run_id'],'challenge_run_id':checked[0]['run_id'],'model_requests':0,
        'purpose':'Complete persistence and conservative consistency checks from exact retained validated output'}


async def review(rt, job, model_factory=None):
    return await review_with_evidence(rt,job,model_factory)


async def review_with_evidence(rt,job,model_factory=None,seeded_observer=None,completed_record=None):
    key = cache_key(rt, job)
    try:
        cached = rt.db.get('review_cache', key)
        # Verify bytes still exist before a cache hit. Unknown intervals stay unknown.
        for media_id in job.payload['media_ids']:
            for frame in rt.db.list('frame'):
                if frame['media_id'] == media_id:
                    rt.media.image(frame['id'])
        rt.audit.write('cache_hit', job_id=job.id, run_id=job.run_id, provenance={'cache_key': key})
        return cached['result_refs']
    except KeyError:
        pass

    async with asyncio.timeout(rt.config['limits']['workflow_seconds']):
        review_content=None
        if seeded_observer is None:
            observer, observer_deps = await run_stage(
                rt, job, 'video_observer',
                ObserverRequest(media_ids=job.payload['media_ids'], context=job.payload.get('context', '')),
                model_factory,
            )
        else:
            from pydantic_ai import BinaryContent
            import json
            observer=seeded_observer
            observer_deps=dependencies(rt,job,'video_observer')
            refs={ref for observation in observer.observations for ref in observation.evidence_refs}
            if not refs or len(refs)>min(6,rt.config['limits']['frames']):raise ValueError('Handoff evidence must fit visual packet capacity')
            for observation in observer.observations:
                observer_deps.interval(observation.media_id,observation.start_ms,observation.end_ms)
                for ref in observation.evidence_refs:
                    frame=rt.db.get('frame',ref)
                    if frame['media_id']!=observation.media_id or not observation.start_ms<=frame['timestamp_ms']<=observation.end_ms:raise ValueError('Handoff frame scope or timestamp mismatch')
                    rt.media.image(ref)
            observer_deps.seen_frames.update(refs)
            def review_content(rt,job,name,request,deps,text_only):
                content=[request.model_dump_json(),json.dumps({'review_id':job.run_id,'event_id':job.payload['event_id'],'site_id':job.site_id})]
                for ref in sorted(refs):
                    deps.seen_frames.add(ref)
                    from safety_app.agents.shared.evidence_refs import register_frame
                    from safety_app.domain.models import FrameRef
                    register_frame(deps,FrameRef.model_validate(rt.db.get('frame',ref)),f'f{len(deps.seen_frames)}')
                    if name=='challenge_reviewer':content.extend([json.dumps(rt.db.get('frame',ref)),BinaryContent(data=rt.media.model_image(ref),media_type='image/jpeg')])
                return content
            rt.audit.write('layer1_evidence_reused',job_id=job.id,run_id=job.run_id,output={'observation_ids':[o.id for o in observer.observations],'frame_ids':sorted(refs)})
        for observation in observer.observations:
            rt.db.save('observation', observation, immutable=True)

        request = SafetyRequest(
            observations=observer.observations,
            context=job.payload.get('context', ''),
            visibility_limits=observer.visibility_limits,
            review_context={
                field: job.payload.get(field)
                for field in ('event_date', 'activity', 'jurisdiction', 'state', 'employer_type')
            }|({'review_layer':'video_safety_review'} if job.kind=='video_safety_review' else {}),
        )
        retained=_retained_finalization(rt,job,request)
        if retained:
            safety,challenge,delegations,provenance=retained
            rt.audit.write('validated_stages_reused',run_id=job.run_id,job_id=job.id,resources=provenance)
        else:
            team = SafetyTeam(rt, job, request, observer_deps, model_factory)

            def setup_boss(deps):
                deps.delegate = team.delegate
                deps.output_check = lambda result: team.validate_boss(result, deps)

            safety, safety_deps = await run_stage(
                rt, job, 'safety_reviewer', request, model_factory, observer_deps, setup=setup_boss,
                content_builder=review_content,
                text_only=seeded_observer is not None,
            )
            for finding in safety.findings:
                if finding.event_id != job.payload.get('event_id'):
                    raise ValueError('Finding event does not match review scope')
            challenge, _ = await run_stage(
                rt, job, 'challenge_reviewer',
                ChallengeRequest(findings=safety.findings, investigation=safety.investigation,triage=safety.triage),
                model_factory, safety_deps,
                content_builder=review_content,
            )
            delegations=team.results
        _validate_challenge(safety, challenge)
        investigation = _challenged_investigation(safety, challenge)
        if rt.jobs.cancelled(job.id):
            raise asyncio.CancelledError()

        refs = []
        pending = []
        for finding in safety.findings:
            issues = [issue for issue in challenge.issues if issue.finding_id == finding.id]
            finding.status = 'challenged' if issues else 'reviewed'
            finding.uncertainty.extend(issue.problem + ' Alternative: ' + issue.alternative for issue in issues)
            pending.append(('finding', finding))
            refs.append(finding.id)
        result = {
            'id': job.run_id,
            'site_id': job.site_id,
            'job_id': job.id,
            'event_id': job.payload.get('event_id'),
            'observer': observer.model_dump(mode='json'),
            'safety': safety.model_dump(mode='json'),
            'delegations': [assessment.model_dump(mode='json') for assessment in delegations],
            'challenge': challenge.model_dump(mode='json'),
            'status': 'completed',
            'visible_finding_ids': refs,
            'document_findings': [finding.id for finding in safety.findings if finding.source_refs],
            'inspection_questions': list(dict.fromkeys(
                observer.visibility_limits + safety.missing_context + challenge.limitations
            )),
            'context': job.payload.get('context', ''),
            'investigation': investigation.model_dump(mode='json'),
        }
        pending.append(('review', result))
        refs.append(result['id'])
        pending.append(('review_cache', {'id': key, 'result_refs': refs}))
        if completed_record:
            extra=completed_record(result)
            pending.extend(extra if isinstance(extra,list) else [extra])
        rt.db.save_many(pending, job.id)
        return refs
