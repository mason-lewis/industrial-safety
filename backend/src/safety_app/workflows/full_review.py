"""Consecutive-window screening shared by recorded Review and causal Live."""
import asyncio
import copy
from types import SimpleNamespace
from safety_app.domain.models import FrameRef, Interval, MediaRef
from safety_app.domain.video_analysis import VideoAnalysisRequest, VideoChunk
from safety_app.domain.review_runs import ReviewWindow
from safety_app.infrastructure.budgets import BudgetLedger, BudgetBlocked
from safety_app.modules.monitoring.recordings import describe, resolve, sample
from safety_app.modules.safety_review.review_runs import ReviewRuns, update_record
from safety_app.modules.safety_review.video_cases import record_screenings, screening_policy
from safety_app.workflows.agent_stage import run_stage
from safety_app.agents.video_observer.video_agent import observer
from safety_app.agents.video_observer.video_models import VideoObservationRequest
from safety_app.workflows.video_analysis import content_for, local_work, scoped_runtime, validate_observations, validate_screening
from safety_app.modules.monitoring.temporal_sampling import VERSION, VERSIONS, SEQUENCE_VERSIONS


def frozen_runtime(rt, run):
    config = copy.deepcopy(run.configuration)
    # Operator reductions take effect immediately; configuration never raises a saved cap.
    for key in ('day_usd', 'month_usd', 'workflow_usd', 'agent_usd', 'concurrency'):
        config['limits'][key] = min(config['limits'][key], rt.config['limits'][key])
    frozen = SimpleNamespace(**(vars(rt) | {'config': config, 'budgets': BudgetLedger(rt.db, rt.audit, config),
                                            'camera': rt.camera, 'classes': rt.classes}))
    return scoped_runtime(frozen)


async def screen_window(rt, job, run, window, frames, gaps, model_factory=None, publish=True):
    """Checkpoint bounded packets; publish the window only after every packet passes."""
    if run.configuration.get('video_screening',{}).get('temporal_sampling') not in SEQUENCE_VERSIONS:
        chunk=await screen_packet(rt,job,run,window,frames,gaps,model_factory)
    else:
        if len(frames)>12:raise ValueError('Temporal window exceeds two six-image packets')
        packets=[]
        for index in range(0,len(frames),6):
            packet_frames=frames[index:index+6]
            packet_window=window.model_copy(update={'id':f'{window.id}:packet:{index//6}'})
            try:
                packet=VideoChunk.model_validate(rt.db.get('review_packet',packet_window.id))
                if [(f.id,f.content_hash) for f in packet.frames] != [(f.id,f.content_hash) for f in packet_frames]:
                    raise ValueError('Retained packet differs from this source/selection')
            except KeyError:
                packet=await screen_packet(rt,job,run,packet_window,packet_frames,gaps,model_factory,checkpoint_kind='review_packet')
            from safety_app.workflows.temporal_detail import refine_packet
            packets.append(await refine_packet(rt,job,run,packet,model_factory))
        if not packets:raise ValueError('Missing review pixels')
        chunk=VideoChunk(id=window.id,analysis_id=run.id,media_id=run.media_id,
            intervals=[Interval(start_ms=window.start_ms,end_ms=window.end_ms)],
            summary=packets[0].summary if len(packets)==1 else 'Two separate temporal evidence packets inspected. Timestamped observations and checks retain each result and its uncertainty.',
            observations=[o for p in packets for o in p.observations],frames=frames,
            limitations=list(dict.fromkeys(g for p in packets for g in p.limitations)),
            observer_run_id=packets[0].observer_run_id,
            screening_checks=[c for p in packets for c in p.screening_checks])
        rt.db.save_many([('video_chunk',chunk)],job.id)
    if publish:finish_window(rt, run, window, chunk, gaps)
    return chunk


async def screen_packet(rt, job, run, window, frames, gaps, model_factory=None, checkpoint_kind='video_chunk'):
    """Only supplied, retained pixels enter the observer. No seek is available to Live."""
    body = VideoAnalysisRequest.model_validate(run.request)
    scoped = frozen_runtime(rt, run)
    if body.mode=='hosted':scoped.budgets.require_settled(run.id)
    limits = scoped.config['limits']
    screening = screening_policy(scoped)
    if run.configuration.get('video_screening',{}).get('temporal_sampling') in VERSIONS:
        screening['input_tokens']=run.configuration['video_screening'].get('temporal_input_tokens',24000)
    dense=run.configuration.get('video_screening',{}).get('temporal_sampling')==VERSION
    if dense:screening['output_per_request']=run.configuration['video_screening'].get('temporal_output_per_request',1800)
    limits.update(input_tokens=min(limits['input_tokens'], screening['input_tokens']),
                  output_tokens=min(limits['output_tokens'], 3600 if dense else 2400),
                  output_per_request=min(limits['output_per_request'], screening['output_per_request']),
                  requests=min(limits['requests'], 4), tools=min(limits['tools'], max(4,len(frames))))
    interval = Interval(start_ms=window.start_ms, end_ms=window.end_ms)
    if not frames or len(frames)>6 or any(f.media_id != run.media_id or not window.start_ms <= f.timestamp_ms <= window.end_ms
                         or (run.kind == 'live' and f.timestamp_ms > run.cutoff_ms) for f in frames):
        raise ValueError('Missing or out-of-scope review pixels')
    # Both modes share the exact retained stills. Live never decodes ahead for CV.
    await local_work(rt.people.capture,run.id,frames,body.mode=='hosted',lambda:rt.jobs.cancelled(job.id) or rt.jobs.cancelled(run.job_id),body.person_confidence)
    stage = job.model_copy(update={'run_id': run.id, 'payload': job.payload | {
        'media_ids': [run.media_id], 'start_ms': window.start_ms, 'end_ms': window.end_ms,
        'budget_workflow_id': run.id}})
    request = VideoObservationRequest(question=body.question,
        purpose='Inspect this consecutive window as a chronological sequence. Describe any visible acute event FIRST, including falling objects/person contact (struck_by), a person falling (fall), or fire/flame/smoke exposure (fire_burn). Then check the four routine categories. Include before/during/after evidence when visible. Do not substitute background clutter for the main event. No injury diagnosis, intent or continuity across cuts.',
        media_id=run.media_id, intervals=[interval], safety_screening=True, required_ppe=body.required_ppe)

    def setup(deps):
        def check(result):
            validate_observations(result, frames, [interval])
            validate_screening(result, frames, [interval], True, body.required_ppe, rt.media)
            fill_omitted_categories(result, frames, interval)
        deps.output_check = check
        deps.safety_guide = {'ppe': 'Use visible body regions and supplied requirements; occlusion is unknown.',
            'blocked_access': 'Requires an identifiable visible route AND obstruction. Background building materials alone are not blocked access. Set route_basis.',
            'struck_by': 'Falling/projected objects, vehicle contact or apparent impact. Cite temporal evidence; contact uncertainty is not an injury diagnosis.',
            'fall': 'A person falling or losing support; separate observed motion from inferred injury.',
            'unstable_stack': 'Visible support/lean only, no inferred weight.',
            'slip_trip': 'Visible material/cables and exposure, not reflections alone.',
            'fire_burn': 'Visible flame, smoke, burning material, or a person at an open flame/flare-up or hot surface. Report only when supported by pixels; not routine cooking heat alone.',
            'required_ppe': body.required_ppe, 'max_detail_crops': min(1,max(0,limits['frames']-len(frames)))}
        deps.detail_frame_ids = {f.id for f in frames}
        deps.detail_limit = min(1, max(0, limits['frames'] - len(frames)))

    if body.mode == 'fixture' and model_factory is None:
        from safety_app.workflows.video_fixture import models
        model_factory = models()
    attempt = lambda: run_stage(scoped, stage, 'video_observer', request, model_factory,
        setup=setup, agent_builder=observer, settings_override={'openai_reasoning_effort':'low'},
        content_builder=content_for(frames, gaps, image_detail='high' if dense else None))
    sent = reservation_count(rt, run.id)
    try:
        result, deps = await attempt()
    except Exception as exc:
        # One automatic retry, only when the token count failed and this packet sent and reserved nothing.
        if not preflight_unavailable(exc) or reservation_count(rt, run.id) != sent:
            raise
        rt.audit.write('packet_preflight_retry', status='retrying', run_id=run.id, job_id=job.id,
            resources={'window_id': window.id, 'reason': str(exc)[:300], 'delay_s': PREFLIGHT_RETRY_DELAY_S})
        await asyncio.sleep(PREFLIGHT_RETRY_DELAY_S)
        result, deps = await attempt()
    chunk = VideoChunk(id=window.id, analysis_id=run.id, media_id=run.media_id, intervals=[interval],
        summary=result.summary, observations=result.observations, frames=frames,
        limitations=[*gaps, *result.limitations], observer_run_id=deps.run_id, screening_checks=result.screening_checks)
    rt.db.save_many([(checkpoint_kind, chunk)], job.id)
    return chunk


PREFLIGHT_RETRY_DELAY_S = 3


def reservation_count(rt, run_id):
    with rt.db.connect() as db:
        return db.execute('SELECT count(*) FROM reservations WHERE workflow_id=?', (run_id,)).fetchone()[0]


def preflight_unavailable(exc):
    """A token count that failed for a connection or availability reason (not, say, authorization)."""
    from safety_app.infrastructure.token_preflight import TokenPreflightUnavailable, transient_count_failure
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, TokenPreflightUnavailable):
            return transient_count_failure(exc.__cause__)
        seen.add(id(exc)); exc = exc.__cause__ or exc.__context__
    return False


def fill_omitted_categories(result, frames, interval):
    """An omitted routine category becomes an explicit, Python-marked unknown, never a rejection."""
    from safety_app.domain.video_safety import ROUTINE_CATEGORIES, VisualSafetyCheck
    for check in result.screening_checks:
        check.coordinator_filled = False  # Only Python may mark a fill.
    present = {c.category for c in result.screening_checks}
    cited = sorted(frames, key=lambda f: f.timestamp_ms)[:6]
    for category in ROUTINE_CATEGORIES:
        if category in present:
            continue
        result.screening_checks.append(VisualSafetyCheck(
            category=category, result='unknown', visibility='not_assessable',
            condition='The observer returned no check for this category; it was not assessed in this window.',
            evidence_refs=[f.id for f in cited], start_ms=cited[0].timestamp_ms, end_ms=cited[-1].timestamp_ms,
            ppe_state='not_assessable' if category == 'ppe' else None, coordinator_filled=True,
            limitations=['Not assessed: the observer omitted this required category. This is not a clear result.']))


def finish_window(rt, run, window, chunk, gaps=None):
    """Recover even if the worker died between chunk save and manifest update."""
    body = VideoAnalysisRequest.model_validate(run.request)
    parent = rt.jobs.get(run.job_id)
    if parent.status == 'cancelled': raise asyncio.CancelledError()
    record_screenings(rt, parent, body, MediaRef.model_validate(rt.db.get('media', run.media_id)), chunk)
    if gaps is None:
        gaps = [g for g in chunk.limitations if g.startswith(('No decoded', 'No in-scope', 'Missing'))]
    actual_gaps = [g for g in gaps if not g.startswith(('Sampled still frames;', 'Sampled still frames:'))]
    if len({f.timestamp_ms for f in chunk.frames})<2:
        actual_gaps.append('Missing second distinct sampled frame; temporal comparison is unavailable.')
    update_record(rt, 'review_window', window.id, status='completed', chunk_id=chunk.id,
                  frame_ids=[f.id for f in chunk.frames], gaps=actual_gaps, error=None)
    rt.audit.write('review_window_completed', job_id=parent.id, run_id=run.id,
                   output={'window_id': window.id, 'interval': [window.start_ms, window.end_ms], 'gaps': actual_gaps})
    if body.review_strategy == 'full':
        # Closed episodes go to the review team now instead of after the last window.
        from safety_app.modules.safety_review.video_cases import dispatch_ready_cases
        try: dispatch_ready_cases(rt, parent.id)
        except Exception as exc:
            rt.audit.write('video_handoff_deferred', status='failed', job_id=parent.id, error={'code': type(exc).__name__, 'message': str(exc)[:500]})


async def full_review(rt, job, model_factory=None):
    body = VideoAnalysisRequest.model_validate(job.payload)
    service = ReviewRuns(rt)
    run = service.prepare(job, body)
    if body.mode=='hosted':
        from safety_app.infrastructure.review_allowances import snapshot
        if snapshot(rt.budgets,run.id):
            from safety_app.infrastructure.provider_transport import check_review_connection
            rt.audit.write('connection_probe_started',run_id=run.id,job_id=job.id,status='running')
            probe=await check_review_connection()
            rt.db.save('review_connection_probe',{'id':run.id,**probe})
            rt.audit.write('connection_probe_completed',run_id=run.id,job_id=job.id,output=probe)
    path = resolve(rt.root, run.source_id)
    digest, _, _, signature = await local_work(describe, path)
    if digest != run.source_hash: raise ValueError('Source content changed since manifest creation')
    update_record(rt, 'review_run', run.id, state='running')
    current = asyncio.current_task()

    async def watch():
        while True:
            await asyncio.sleep(1)
            if rt.jobs.cancelled(job.id): current.cancel(); return
            rt.jobs.heartbeat(job.id)
    watcher = asyncio.create_task(watch())
    try:
        async with asyncio.timeout(1800):
            # Windows are screened in parallel batches (model work, checkpointed per packet), then published
            # strictly in source-time order so episode merging is identical to sequential review.
            width = max(1, int(rt.config.get('review_team', {}).get('parallel_windows', 1)))
            pending = [w for w in service.windows(run.id) if w.status != 'completed']

            async def prepare(window):
                try:
                    return VideoChunk.model_validate(rt.db.get('video_chunk', window.id)), None
                except KeyError:
                    pass
                update_record(rt, 'review_window', window.id, status='running', job_id=job.id, error=None)
                if run.configuration.get('video_screening',{}).get('temporal_sampling') in VERSIONS:
                    from safety_app.modules.monitoring.temporal_sampling import sample_motion
                    frames,gaps=await local_work(sample_motion,path,signature,rt.media,run.media_id,
                        Interval(start_ms=window.start_ms,end_ms=window.end_ms),lambda:rt.jobs.cancelled(job.id),
                        min(6,run.configuration['limits']['frames']),run.configuration['video_screening']['temporal_sampling'])
                else:
                    frames, gaps = await local_work(sample, path, signature, rt.media, run.media_id,
                        [Interval(start_ms=window.start_ms, end_ms=window.end_ms)], 2, lambda: rt.jobs.cancelled(job.id))
                return await screen_window(rt, job, run, window, frames, gaps, model_factory, publish=False), gaps

            for index in range(0, len(pending), width):
                if rt.jobs.cancelled(job.id): raise asyncio.CancelledError()
                group = pending[index:index + width]
                tasks = [asyncio.create_task(prepare(w)) for w in group]
                # Siblings already in flight finish normally: cancelling a sent request would only turn a known
                # charge into an unknown one. Billing checks still stop new packets after an unresolved charge.
                await asyncio.gather(*tasks, return_exceptions=True)
                results = [asyncio.CancelledError() if t.cancelled() else (t.exception() or t.result()) for t in tasks]
                failure = next((r for r in results if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError)), None)
                for window, result in zip(group, results):
                    if isinstance(result, BaseException):
                        from pydantic_ai.exceptions import UsageLimitExceeded
                        if result is failure and not isinstance(result, (BudgetBlocked, UsageLimitExceeded)):
                            update_record(rt, 'review_window', window.id, status='failed', error=str(result)[:1000])
                        # Decoder and model failures stop at a durable boundary; no hidden retry or skip.
                        raise failure or result
                    chunk, gaps = result
                    finish_window(rt, run, window, chunk, gaps)
            digest, _, _, _ = await local_work(describe, path)
            if digest != run.source_hash: raise ValueError('Source changed before review completion')
        update_record(rt, 'review_run', run.id, state='ended', cutoff_ms=run.end_ms, source_verified=True)
        return [run.id]
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


async def live_window(rt, job, model_factory=None):
    service = ReviewRuns(rt)
    run = service.get(job.payload['review_run_id'], job.site_id)
    if run.state in ('cancelled', 'interrupted') or rt.jobs.cancelled(run.job_id): raise asyncio.CancelledError()
    window = ReviewWindow.model_validate(rt.db.get('review_window', job.payload['window_id']))
    if window.run_id != run.id or window.job_id != job.id or window.end_ms > run.cutoff_ms:
        raise ValueError('Live window has stale or future scope')
    frames = [FrameRef.model_validate(rt.db.get('frame', id)) for id in window.frame_ids]
    update_record(rt, 'review_window', window.id, status='running')
    try:
        chunk = await screen_window(rt, job, run, window, frames, window.gaps, model_factory, publish=False)
    except asyncio.CancelledError:
        update_record(rt, 'review_window', window.id, status='cancelled'); raise
    except BaseException as exc:
        # A failed window must read as settled, or later windows would wait on it.
        update_record(rt, 'review_window', window.id, status='failed', error=str(exc)[:1000]); raise
    # Windows may be screened concurrently; publish in source order so episode merging stays identical.
    await wait_for_earlier_windows(rt, run, window, job)
    finish_window(rt, run, window, chunk, window.gaps)
    return [window.id]


async def wait_for_earlier_windows(rt, run, window, job, limit_s=300):
    settled = ('completed', 'failed', 'skipped', 'cancelled')
    loop = asyncio.get_running_loop(); deadline = loop.time() + limit_s
    terminal = ('completed', 'failed', 'cancelled', 'budget_exceeded', 'interrupted')
    def done(w):
        if w.status in settled or w.status == 'pending': return True
        # The window record can lag its job (e.g. a lost worker); a terminal job means nothing more will publish.
        try: return bool(getattr(w, 'job_id', None)) and rt.jobs.get(w.job_id).status in terminal
        except KeyError: return True
    while loop.time() < deadline:
        earlier = [w for w in ReviewRuns(rt).windows(run.id) if w.index < window.index]
        if all(done(w) for w in earlier):
            return
        if rt.jobs.cancelled(job.id): raise asyncio.CancelledError()
        rt.jobs.heartbeat(job.id)  # keep this job's lease while it waits to publish
        await asyncio.sleep(.5)
    rt.audit.write('window_publish_wait_expired', job_id=job.id, run_id=run.id, status='degraded',
                   output={'window_id': window.id, 'note': 'Earlier window did not settle; publishing to avoid a stall.'})
