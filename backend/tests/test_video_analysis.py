"""Recorded-video harness checks use synthetic MP4s and deterministic models."""
import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from safety_app.domain.models import FrameRef, Interval
from safety_app.domain.video_analysis import VideoAnalysisRequest, VideoAnswer, VideoClaim, VideoClaimCheck, VideoVerification
from safety_app.infrastructure.runtime import Runtime
from safety_app.modules.monitoring.recordings import sources
from safety_app.modules.safety_review.video_analysis import VideoAnalysisService
from safety_app.modules.safety_review.video_memory import VideoMemory
from safety_app.workers.runner import execute
from safety_app.workflows.video_analysis import coverage_limits, local_work, overview_intervals, scoped_runtime, validate_observations, validate_plan, validate_verification
from safety_app.workflows.video_fixture import models


@pytest.fixture
def rt(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[2] / 'configs', tmp_path / 'configs')
    return Runtime(tmp_path)


def recording(rt, seconds=30):
    folder = rt.root / 'data/input'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'fixture.mp4'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (32, 32))
    assert writer.isOpened()
    for i in range(seconds * 10):
        writer.write(np.full((32, 32, 3), i % 255, dtype=np.uint8))
    writer.release()
    return sources(rt.root)[0].id, path


def submit(rt, source, key='analysis', mode='fixture'):
    body = VideoAnalysisRequest(source_id=source, question='What changes in the clip?', mode=mode, idempotency_key=key)
    return VideoAnalysisService(rt).request(body, 'local')


async def test_recorded_mp4_scan_adaptive_compare_memory_verification_and_restart(rt):
    source, path = recording(rt)
    original = path.read_bytes()
    job = submit(rt, source)
    result = await execute(rt, job)
    assert result.status == 'completed', result.error
    analysis = VideoAnalysisService(rt).get(result.result_refs[0], 'local')
    assert analysis.status == 'needs_review' and analysis.mode == 'fixture'
    chunks = VideoMemory(rt.db, job.run_id).chunks()
    assert len(chunks) == 5  # Three broad windows, one inspection, one comparison.
    assert len(chunks[-1].intervals) == 2
    assert min(frame.timestamp_ms for frame in chunks[0].frames) == 0
    assert max(frame.timestamp_ms for frame in chunks[2].frames) >= 29000
    assert analysis.verification.checks[0].claim_id == analysis.answer.claims[0].id
    rows = rt.audit.rows(1000, job_id=job.id)
    assert all(row['resources']['frames'] <= 6 for row in rows if row['event_type'] == 'model_request')
    assert all(row['configuration']['fake_provider'] for row in rows if row['event_type'] == 'model_request')
    assert any(row['tool_name'] == 'search_video_memory' for row in rows)
    assert any(row['agent_name'] == 'challenge_reviewer' for row in rows)
    assert not rt.budgets.status()['outstanding']
    assert not rt.db.list('finding') and not rt.db.list('annotation')
    assert path.read_bytes() == original
    reopened = Runtime(rt.root)
    assert VideoAnalysisService(reopened).get(analysis.id, 'local') == analysis
    assert VideoMemory(reopened.db, job.run_id).search('fixed fixture')
    assert not VideoMemory(reopened.db, 'foreign-analysis').search('fixed fixture')
    assert not VideoMemory(reopened.db, 'foreign-analysis').search('')


def test_long_video_overview_and_explicit_gaps():
    scope = Interval(start_ms=1000, end_ms=3601000)
    windows = overview_intervals(scope, 10000, 4)
    assert len(windows) == 4 and windows[0].start_ms == 1000 and windows[-1].end_ms == scope.end_ms
    assert all(window.end_ms - window.start_ms == 10000 for window in windows)
    chunks = [SimpleNamespace(intervals=[window], limitations=[]) for window in windows]
    gaps = coverage_limits(scope, chunks)
    assert len([gap for gap in gaps if gap.startswith('Uninspected')]) == 3
    assert len(overview_intervals(Interval(start_ms=0, end_ms=500), 10000, 4)) == 1


async def test_failed_verifier_retains_chunks_without_successful_answer(rt):
    source, _ = recording(rt, 2)
    job = submit(rt, source)
    ordinary = models()

    def factory(name):
        if name != 'challenge_reviewer':
            return ordinary(name)
        return FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart(
            info.output_tools[0].name, {'checks': [], 'answer_supported': True, 'limitations': []})]))

    result = await execute(rt, job, factory)
    assert result.status == 'failed'
    assert 'every claim exactly once' in result.error.message
    assert VideoMemory(rt.db, job.run_id).chunks()
    assert not rt.db.list('video_analysis')
    assert rt.audit.rows(event_type='validation_failed', job_id=job.id)


async def test_cancelled_and_invalid_range_stop_before_models(rt):
    source, _ = recording(rt, 2)
    job = submit(rt, source)
    rt.jobs.cancel(job.id)
    result = await execute(rt, job, lambda name: pytest.fail('No model after cancellation'))
    assert result.status == 'cancelled' and not rt.db.list('video_analysis')
    invalid = VideoAnalysisRequest(source_id=source, question='Question', end_ms=9000, mode='fixture', idempotency_key='bad-range')
    job = VideoAnalysisService(rt).request(invalid, 'local')
    result = await execute(rt, job, lambda name: pytest.fail('No model for an invalid source interval'))
    assert result.status == 'failed' and 'outside' in result.error.message


async def test_native_work_is_joined_after_cancellation():
    import threading
    started = threading.Event()
    release = threading.Event()
    finished = []

    def work():
        started.set()
        release.wait(2)
        finished.append(True)

    task = asyncio.create_task(local_work(work))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished == [True]


def test_plan_scope_citations_and_verification_are_strict(rt):
    from safety_app.agents.video_observer.video_models import VideoPlan
    from safety_app.domain.models import MediaRef
    from safety_app.agents.shared.deps import AgentDeps, Scope
    from safety_app.modules.safety_review.rules import Rules
    media = MediaRef(id='m', media_id='m', source_id='s', camera_id='camera-1', start_ms=0, end_ms=30000, content_hash='h', storage_key='fixture')
    rt.db.save('media', media)
    frame = FrameRef(id='f', media_id='m', frame_id='1', timestamp_ms=1000, content_hash='h', image_url='/api/media/frames/f')
    rt.db.save('frame', frame)
    deps = AgentDeps(rt.media, rt.sources, Rules(rt.db), rt.audit, rt.budgets,
                     Scope('local', frozenset(['m']), {'m': (0, 30000)}, frozenset(['camera-1'])),
                     'r', 'w', 'j', rt.camera, rt.classes, lambda *args: None)
    claim = VideoClaim(id='c', start_ms=1000, end_ms=1000, text='Claim', evidence_refs=['f'])
    answer = VideoAnswer(answer='Claim', claims=[claim])
    plan = VideoPlan(action='answer', purpose='Finish', answer=answer)
    scope = Interval(start_ms=0, end_ms=30000)
    with pytest.raises(ValueError, match='not retrieved'):
        validate_plan(plan, deps, scope, 0, 6)
    deps.seen_frames.add('f')
    validate_plan(plan, deps, scope, 0, 6)
    with pytest.raises(ValueError, match='capacity'):
        validate_plan(plan, deps, scope, 0, 0)
    claim.start_ms = 2000
    claim.end_ms = 2000
    with pytest.raises(ValueError, match='timestamps'):
        validate_plan(plan, deps, scope, 0, 6)
    inspect = VideoPlan(action='inspect', purpose='Look closer', intervals=[Interval(start_ms=0, end_ms=20000)])
    with pytest.raises(ValueError, match='Clip exceeds'):
        validate_plan(inspect, deps, scope, 1, 6)
    with pytest.raises(ValueError, match='exhausted'):
        validate_plan(inspect, deps, scope, 0, 6)
    check = VideoClaimCheck(claim_id='c', verdict='uncertain', explanation='Not enough pixels')
    with pytest.raises(ValueError, match='Supported answer'):
        validate_verification(VideoVerification(checks=[check], answer_supported=True), answer)
    with pytest.raises(ValueError, match='exactly once'):
        validate_verification(VideoVerification(checks=[check, check], answer_supported=False), answer)


def test_recorded_allowance_keeps_shared_prices_and_limits(rt):
    import copy
    before = copy.deepcopy(rt.config)
    scoped = scoped_runtime(rt)
    assert scoped.config['pricing']['gpt-6-luna']['image_usd'] == .001
    assert rt.config == before and rt.config['pricing']['gpt-6-luna']['image_usd'] is None
    assert scoped.budgets.db.path == rt.budgets.db.path
    assert scoped.config['limits'] == rt.config['limits']


def test_api_authentication_idempotency_sources_and_scope(rt, monkeypatch):
    from fastapi.testclient import TestClient
    from safety_app.api.main import app
    source, _ = recording(rt, 2)
    monkeypatch.setattr('safety_app.infrastructure.runtime._default', rt)
    client = TestClient(app)
    assert client.get('/api/video-analysis/sources').status_code == 401
    assert client.post('/api/auth/session', json={}).status_code == 200
    assert client.get('/api/video-analysis/sources').json()[0]['id'] == source
    payload = {'source_id': source, 'question': 'Question', 'mode': 'fixture', 'idempotency_key': 'api-question'}
    response = client.post('/api/video-analysis', json=payload)
    assert response.status_code == 200, response.text
    assert client.post('/api/video-analysis', json=payload).json()['id'] == response.json()['id']
    assert client.post('/api/video-analysis', json=payload | {'question': 'Different'}).status_code == 400
    assert client.post('/api/video-analysis', json=payload | {'path': '/tmp/secret'}).status_code == 422
    assert client.post('/api/video-analysis', json=payload | {'source_id': 'rtsp-1'}).status_code == 400
    job_id = response.json()['id']
    assert client.get(f'/api/video-analysis/jobs/{job_id}/chunks').json() == []
    assert client.get(f'/api/video-analysis/jobs/{job_id}/audit').json()
    assert client.get(f'/api/video-analysis/jobs/{job_id}/audit/download').status_code == 400
    assert client.post(f'/api/jobs/{job_id}/cancel', json={}).status_code == 200
    exported = client.get(f'/api/video-analysis/jobs/{job_id}/audit/download')
    assert exported.status_code == 200
    rows = [json.loads(line) for line in exported.text.splitlines()]
    assert rows and all(row['job_id'] == job_id for row in rows)
    foreign = rt.jobs.start('video_analysis', 'other-site', payload, site_id='other')
    assert client.get(f'/api/video-analysis/jobs/{foreign.id}/chunks').status_code == 400
    assert client.get(f'/api/video-analysis/jobs/{foreign.id}/audit/download').status_code == 400


async def test_saved_video_answers_and_partial_chunks_keep_evidence_during_trial_reset(rt):
    from safety_app.infrastructure.trials import Trials

    source, _ = recording(rt, 2)
    job = submit(rt, source)
    finished = await execute(rt, job)
    assert finished.status == 'completed'
    before = rt.db.list('frame')
    trials = Trials(rt)
    preview = trials.preview()
    trials.reset(preview.plan_id, 'Fixture reset', 'fixture-operator')
    assert rt.db.list('frame') == before
    assert rt.db.list('media') and rt.db.list('video_analysis') and rt.db.list('video_chunk')
    assert rt.jobs.get(job.id).status == 'completed'
    for frame in before:
        assert rt.media.image(frame['id']).startswith(b'\xff\xd8')


async def test_hosted_opt_in_failure_never_dispatches_and_global_stop_cancels_job(rt, monkeypatch):
    from fastapi.testclient import TestClient
    from safety_app.api.main import app

    source, _ = recording(rt, 2)
    monkeypatch.setenv('SAFETY_PAID_REVIEW', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-test-key-never-sent')
    job = submit(rt, source, mode='hosted')
    monkeypatch.setenv('SAFETY_PAID_REVIEW', '0')
    result = await execute(rt, job)
    assert result.status == 'failed' and 'Paid review requires' in result.error.message
    assert not rt.audit.rows(event_type='model_request', job_id=job.id)
    assert not rt.db.list('video_analysis')
    queued = submit(rt, source, 'stop-this-analysis')
    monkeypatch.setattr('safety_app.infrastructure.runtime._default', rt)
    client = TestClient(app)
    assert client.post('/api/auth/session', json={}).status_code == 200
    assert client.post('/api/budgets/stop', json={'stopped': True}).status_code == 200
    assert rt.jobs.get(queued.id).status == 'cancelled'


def test_comparison_observations_cannot_bridge_uninspected_gaps_or_cite_wrong_times():
    from safety_app.agents.video_observer.video_models import VideoObservationResult

    frame = FrameRef(id='frame', media_id='m', frame_id='1', timestamp_ms=2000, content_hash='h', image_url='/api/media/frames/frame')
    intervals = [Interval(start_ms=0, end_ms=10000), Interval(start_ms=20000, end_ms=30000)]
    result = VideoObservationResult(summary='Comparison', observations=[{
        'media_id': 'm', 'start_ms': 2000, 'end_ms': 25000,
        'description': 'A continuous action', 'evidence_refs': ['frame'],
    }])
    with pytest.raises(ValueError, match='non-contiguous'):
        validate_observations(result, [frame], intervals)
    result.observations[0].end_ms = 3000
    result.observations[0].start_ms = 2500
    with pytest.raises(ValueError, match='timestamps'):
        validate_observations(result, [frame], intervals)
