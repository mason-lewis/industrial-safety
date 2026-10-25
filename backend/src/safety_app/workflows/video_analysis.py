"""Python-owned scan/plan/observe loop; agents never delegate or expand scope."""
import asyncio
import copy
import json
import math
from types import SimpleNamespace

from pydantic_ai import BinaryContent

from safety_app.agents.challenge_reviewer.video_agent import create as verifier
from safety_app.agents.video_observer.video_agent import observer, planner
from safety_app.agents.video_observer.video_models import VideoObservationRequest, VideoPlanRequest
from safety_app.domain.models import FrameRef, Interval, MediaRef, uid
from safety_app.domain.video_analysis import VideoAnalysis, VideoAnalysisRequest, VideoChunk
from safety_app.infrastructure.budgets import BudgetLedger
from safety_app.modules.monitoring.recordings import describe, resolve, sample
from safety_app.modules.safety_review.video_memory import VideoMemory
from safety_app.workflows.agent_stage import TaskInput, run_stage
from safety_app.modules.safety_review.video_cases import screening_policy, record_screenings


def policy(rt):
    settings = rt.config.get('video_analysis', {})
    chunks = settings.get('overview_chunks', 4)
    steps = settings.get('adaptive_steps', 2)
    if type(chunks) is not int or not 1 <= chunks <= 8:
        raise ValueError('video_analysis.overview_chunks must be 1..8')
    if type(steps) is not int or not 0 <= steps <= 4:
        raise ValueError('video_analysis.adaptive_steps must be 0..4')
    frame_cap = min(6, rt.config['limits']['frames'])
    if frame_cap < 2 or rt.config['limits']['clip_ms'] <= 0:
        raise ValueError('Video analysis needs at least two frames and a positive clip bound')
    return chunks, steps, frame_cap


def scoped_runtime(rt):
    """Recorded Monitoring footage reuses its existing scoped image allowance."""
    config = copy.deepcopy(rt.config)
    allowance = config.get('monitoring_image_review', {}).get('image_usd')
    if allowance is not None:
        if type(allowance) not in (int, float) or not math.isfinite(allowance) or allowance < 0:
            raise ValueError('Monitoring image allowance must be finite and nonnegative')
        for price in config['pricing'].values():
            price['image_usd'] = allowance
            price['version'] += '-recorded-video-allowance'
    return SimpleNamespace(**(vars(rt) | {
        'config': config, 'budgets': BudgetLedger(rt.db, rt.audit, config),
        'camera': rt.camera, 'classes': rt.classes,
    }))


async def local_work(fn, *args):
    """Join native work on cancellation so a thread cannot keep writing afterward."""
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


def overview_intervals(scope, chunk_ms, count):
    width = scope.end_ms - scope.start_ms
    if width <= chunk_ms:
        return [scope]
    count = min(count, max(1, math.ceil(width / chunk_ms)))
    starts = [scope.start_ms + round(i * (width - chunk_ms) / max(1, count - 1)) for i in range(count)]
    if count == 1:
        starts = [scope.start_ms + (width - chunk_ms) // 2]
    return [Interval(start_ms=start, end_ms=start + chunk_ms) for start in starts]


def content_for(frames=(), gaps=(), image_detail=None):
    def build_content(rt, job, name, request, deps, text_only):
        content = [request.model_dump_json()]
        if gaps:
            content.append(json.dumps({'sampling_gaps': list(gaps)}))
        from safety_app.agents.shared.evidence_refs import register_frame
        for index,frame in enumerate(frames,1):
            deps.seen_frames.add(frame.id)
            register_frame(deps,frame,f'f{index}')
            metadata=frame.model_dump(mode='json',exclude={'frame_id'})|{'citation_id':f'f{index}','evidence_id':frame.id}
            content.extend([json.dumps(metadata),
                            BinaryContent(data=rt.media.model_image(frame.id), media_type='image/jpeg',
                                          vendor_metadata={'detail':image_detail} if image_detail else None)])
        return content
    return build_content


def within(interval, scopes):
    return any(scope.start_ms <= interval.start_ms <= interval.end_ms <= scope.end_ms for scope in scopes)


def validate_observations(result, frames, intervals):
    by_id = {frame.id: frame for frame in frames}
    for observation in result.observations:
        if not within(observation, intervals):
            raise ValueError('Observation spans unassigned or non-contiguous intervals')
        if not set(observation.evidence_refs) <= by_id.keys():
            raise ValueError('Observation cites unattached frames')
        if any(not observation.start_ms <= by_id[ref].timestamp_ms <= observation.end_ms
               for ref in observation.evidence_refs):
            raise ValueError('Observation interval must contain its cited frame timestamps')


def validate_screening(result,frames,intervals,enabled,required_ppe,media):
    from safety_app.agents.video_observer.video_tools import valid_box
    import cv2,numpy as np
    if result.screening_checks and not enabled:raise ValueError('Safety checks were not assigned')
    by_id={frame.id:frame for frame in frames}
    for check in result.screening_checks:
        if not within(check,intervals) or not set(check.evidence_refs)<=by_id.keys():raise ValueError('Safety check outside attached interval/frame scope')
        if check.media_id is not None and any(by_id[ref].media_id != check.media_id for ref in check.evidence_refs):
            raise ValueError('Safety check recording does not match its cited frames')
        if any(not check.start_ms<=by_id[ref].timestamp_ms<=check.end_ms for ref in check.evidence_refs):raise ValueError('Safety check must contain its cited timestamps')
        if check.person_box is not None:valid_box(check.person_box)
        if check.subject_box is not None:valid_box(check.subject_box)
        limitation = None
        if check.result=='suspected' and check.category=='blocked_access' and check.route_basis!='visible_route':
            limitation='No identifiable route and obstruction were established; background clutter alone does not establish blocked access.'
        if check.temporal_basis=='sequence_event' and len({by_id[ref].timestamp_ms for ref in check.evidence_refs})<2:
            limitation='The reported event lacks two distinct cited timestamps; motion or contact is unverified.'
        if limitation:
            check.result='unknown';check.needs_judgment=False;check.priority='routine'
            check.limitations=list(dict.fromkeys([*check.limitations,limitation]))
        if check.ppe_state=='not_visible':
            if check.visibility!='clear' or check.person_box is None:raise ValueError('PPE absence requires clear visibility and a localized person')
            for ref in check.evidence_refs:
                image=cv2.imdecode(np.frombuffer(media.model_image(ref),np.uint8),cv2.IMREAD_COLOR)
                if image.shape[0]*check.person_box[3]<120:raise ValueError('Person is too small to assess PPE absence')
        if check.category=='ppe' and check.ppe_item not in required_ppe:
            check.limitations=list(dict.fromkeys([*check.limitations,'This PPE item has no supplied site requirement; applicability needs safety judgment.']))


def validate_plan(plan, deps, scope, remaining, frame_cap):
    if not remaining and plan.action != 'answer':
        raise ValueError('Inspection allowance exhausted; answer with existing evidence and gaps')
    for interval in plan.intervals:
        deps.interval(next(iter(deps.scope.media_ids)), interval.start_ms, interval.end_ms)
        if interval.start_ms == interval.end_ms:
            raise ValueError('Inspection interval must have positive duration')
    if plan.action != 'answer':
        return
    claims = plan.answer.claims
    if len({claim.id for claim in claims}) != len(claims):
        raise ValueError('Claim IDs must be unique')
    refs = {ref for claim in claims for ref in claim.evidence_refs}
    if not refs <= deps.seen_frames:
        raise ValueError('Answer cites frames not retrieved from validated video memory')
    if len(refs) > frame_cap:
        raise ValueError('Answer citations exceed independent verification frame capacity')
    for claim in claims:
        if not within(claim, [scope]):
            raise ValueError('Claim interval outside assigned recording scope')
        for ref in claim.evidence_refs:
            frame = FrameRef.model_validate(deps.media.db.get('frame', ref))
            if frame.media_id not in deps.scope.media_ids or not claim.start_ms <= frame.timestamp_ms <= claim.end_ms:
                raise ValueError('Claim interval must contain its assigned cited frame timestamps')


def validate_verification(result, answer):
    ids = [check.claim_id for check in result.checks]
    if len(ids) != len(set(ids)) or set(ids) != {claim.id for claim in answer.claims}:
        raise ValueError('Independent verification must check every claim exactly once')
    if result.answer_supported and (not answer.claims or any(check.verdict != 'supported' for check in result.checks)):
        raise ValueError('Supported answer requires supported checks for every claim')


def coverage_limits(scope, chunks):
    intervals = sorted((interval for chunk in chunks for interval in chunk.intervals),
                       key=lambda interval: (interval.start_ms, interval.end_ms))
    gaps = []
    cursor = scope.start_ms
    for interval in intervals:
        if interval.start_ms > cursor:
            gaps.append(f'Uninspected source interval: {cursor}–{interval.start_ms} ms.')
        cursor = max(cursor, interval.end_ms)
    if cursor < scope.end_ms:
        gaps.append(f'Uninspected source interval: {cursor}–{scope.end_ms} ms.')
    return list(dict.fromkeys([
        'Sampled stills only; continuous motion, audio, identity continuity, and full event coverage are not established.',
        *gaps, *(limit for chunk in chunks for limit in chunk.limitations),
    ]))


async def analyze(rt, job, model_factory=None):
    body = VideoAnalysisRequest.model_validate(job.payload)
    if body.review_strategy == 'full':
        from safety_app.workflows.full_review import full_review
        return await full_review(rt, job, model_factory)
    if rt.camera(body.camera_id).site_id != job.site_id:
        raise ValueError('Camera outside site')
    overview_count, steps, frame_cap = policy(rt)
    if body.mode == 'fixture' and model_factory is None:
        from safety_app.workflows.video_fixture import models
        model_factory = models()
    scoped = scoped_runtime(rt)
    current = asyncio.current_task()

    async def watch():
        ticks = 0
        while True:
            await asyncio.sleep(1)
            if rt.jobs.cancelled(job.id):
                current.cancel()
                return
            ticks += 1
            if ticks % 20 == 0:
                rt.jobs.heartbeat(job.id)

    watcher = asyncio.create_task(watch())
    try:
        async with asyncio.timeout(rt.config['limits']['workflow_seconds']):
            return await _analyze(scoped, job, body, model_factory, overview_count, steps, frame_cap)
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


async def _analyze(rt, job, body, model_factory, overview_count, steps, frame_cap):
    if rt.jobs.cancelled(job.id):
        raise asyncio.CancelledError()
    path = resolve(rt.root, body.source_id)
    digest, first_ms, endpoint, source_signature = await local_work(describe, path)
    start = max(first_ms, body.start_ms)
    end = endpoint if body.end_ms is None else body.end_ms
    if not first_ms <= start < end <= endpoint:
        raise ValueError('Requested interval is outside the recorded source PTS range')
    scope = Interval(start_ms=start, end_ms=end)
    media = MediaRef(id=job.run_id, media_id=job.run_id, source_id=body.source_id,
                     camera_id=body.camera_id, start_ms=start, end_ms=end,
                     content_hash=digest, storage_key='source:' + body.source_id)
    if body.recording_start_utc:media.capture_time_utc=body.recording_start_utc.isoformat()
    rt.db.save('media', media, immutable=True)
    stage_job = job.model_copy(update={'payload': job.payload | {
        'media_ids': [media.id], 'start_ms': start, 'end_ms': end,
    }})
    memory = VideoMemory(rt.db, job.run_id)
    screening=screening_policy(rt)
    screen_frames=min(screening['frames'],frame_cap) if body.safety_screening else frame_cap
    crops=min(screening['detail_crops'],max(0,frame_cap-screen_frames)) if body.safety_screening else 0
    cheap=scoped_runtime(rt)
    if body.safety_screening:
        cheap.config['limits'].update(input_tokens=min(rt.config['limits']['input_tokens'],screening['input_tokens']),
            output_per_request=min(rt.config['limits']['output_per_request'],screening['output_per_request']),
            output_tokens=min(rt.config['limits']['output_tokens'],2400),requests=min(rt.config['limits']['requests'],4),tools=min(rt.config['limits']['tools'],4))
    rt.audit.write('video_analysis_started', job_id=job.id, run_id=job.run_id,
                   provenance={'media_id': media.id, 'content_hash': digest, 'mode': body.mode,
                               'overview_chunks': overview_count, 'adaptive_steps': steps,
                               'image_allowance_usd': rt.config.get('monitoring_image_review', {}).get('image_usd'),
                               'pricing_basis': 'operator_image_allowance'})

    async def observe(intervals, purpose):
        if rt.jobs.cancelled(job.id):
            raise asyncio.CancelledError()
        frames, gaps = await local_work(sample, path, source_signature, rt.media, media.id,
                                       intervals, screen_frames, lambda: rt.jobs.cancelled(job.id))
        request = VideoObservationRequest(question=body.question, purpose=purpose,
                                          media_id=media.id, intervals=intervals,safety_screening=body.safety_screening,required_ppe=body.required_ppe)

        def setup(deps):
            def check(result):
                validate_observations(result,frames,intervals)
                validate_screening(result,frames,intervals,body.safety_screening,body.required_ppe,rt.media)
            deps.output_check=check
            if body.safety_screening:
                deps.safety_guide={'ppe':'Check only visible body regions; absent PPE needs task/site context.',
                    'blocked_access':'Visible obstruction of a route; exit designation remains unverified.',
                    'unstable_stack':'Visible lean/displaced support; no inferred weight or center of gravity.',
                    'slip_trip':'Visible material/cables and exposure; reflections alone are not spills.',
                    'required_ppe':body.required_ppe,'max_detail_crops':crops}
                deps.detail_frame_ids={frame.id for frame in frames};deps.detail_limit=crops

        result, deps = await run_stage(cheap, stage_job, 'video_observer', request, model_factory,
                                      setup=setup, agent_builder=observer,
                                      settings_override={'openai_reasoning_effort':'low'} if body.safety_screening else None,
                                      content_builder=content_for(frames, gaps))
        chunk = VideoChunk(analysis_id=job.run_id, media_id=media.id, intervals=intervals,
                           summary=result.summary, observations=result.observations, frames=frames,
                           limitations=[*gaps, *result.limitations], observer_run_id=deps.run_id,screening_checks=result.screening_checks)
        rt.db.save_many([('video_chunk', chunk)], job.id)
        record_screenings(rt,job,body,media,chunk)
        rt.audit.write('video_chunk_observed', job_id=job.id, run_id=job.run_id,
                       output=chunk.model_dump(mode='json'))

    for interval in overview_intervals(scope, rt.config['limits']['clip_ms'], overview_count):
        await observe([interval], 'Broad scan of the recording for relevant visible evidence')

    for step in range(steps + 1):
        remaining = steps - step
        request = VideoPlanRequest(question=body.question, media_id=media.id, scope=scope,
                                   catalog=memory.catalog(), inspections_remaining=remaining,
                                   max_interval_ms=rt.config['limits']['clip_ms'], max_answer_frames=frame_cap)

        def setup(deps):
            deps.video_memory = memory.search
            deps.output_check = lambda result: validate_plan(result, deps, scope, remaining, frame_cap)

        plan, _ = await run_stage(rt, stage_job, 'video_observer', request, model_factory,
                                  setup=setup, agent_builder=planner, text_only=True,
                                  content_builder=content_for())
        rt.audit.write('video_inspection_planned', job_id=job.id, run_id=job.run_id,
                       output=plan.model_dump(mode='json'))
        if plan.action == 'answer':
            answer = plan.answer
            break
        await observe(plan.intervals, plan.purpose)
    else:
        raise ValueError('Planner did not produce a bounded final answer')

    chunks = memory.chunks()
    limits = coverage_limits(scope, chunks)
    if body.mode == 'fixture':
        limits.insert(0, 'Local fixture: fixed demonstration responses; this does not answer the video question or measure AI accuracy.')
    refs = {ref for claim in answer.claims for ref in claim.evidence_refs}
    # With no claims, inspect overview pixels to check the stated insufficiency.
    frames = ([FrameRef.model_validate(rt.db.get('frame', ref)) for ref in sorted(refs)]
              if refs else chunks[0].frames[:frame_cap])
    verification, _ = await run_stage(
        rt, stage_job, 'challenge_reviewer',
        TaskInput({'question': body.question, 'answer': answer.model_dump(mode='json'), 'coverage_limits': limits}),
        model_factory, setup=lambda deps: setattr(deps, 'output_check', lambda result: validate_verification(result, answer)),
        agent_builder=verifier, content_builder=content_for(frames),
    )
    final_digest, _, _, final_signature = await local_work(describe, path)
    if final_signature != source_signature or final_digest != digest:
        raise ValueError('Recording changed before the completed result was saved')
    result = VideoAnalysis(
        id=job.run_id, job_id=job.id, site_id=job.site_id, source_id=body.source_id,
        media_id=media.id, question=body.question, mode=body.mode, start_ms=start, end_ms=end,
        answer=answer, verification=verification,
        status='supported_within_samples' if body.mode != 'fixture' and verification.answer_supported else 'needs_review',
        chunk_ids=[chunk.id for chunk in chunks],
        inspected_intervals=[interval for chunk in chunks for interval in chunk.intervals],
        limitations=list(dict.fromkeys([*limits, *answer.limitations, *verification.limitations])),
        safety_case_ids=[case['id'] for case in rt.db.list('video_safety_case') if case['analysis_id']==job.run_id],
    )
    rt.db.save_many([('video_analysis', result)], job.id)
    rt.audit.write('video_analysis_completed', job_id=job.id, run_id=job.run_id,
                   output={'analysis_id': result.id, 'status': result.status})
    return [result.id]
