"""Offline characterization of the shared stage boundary and retained evidence."""
import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai import BinaryContent

from safety_app.agents.video_observer.models import ObserverRequest, ObserverResult
from safety_app.domain.models import MediaRef
from safety_app.infrastructure.runtime import Runtime
from safety_app.workflows.agent_stage import run_stage


@pytest.fixture
def rt(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[2] / 'configs', tmp_path / 'configs')
    return Runtime(tmp_path)


@pytest.mark.parametrize('name,pixel_count', [
    ('video_observer', 6), ('challenge_reviewer', 6), ('safety_reviewer', 0),
])
async def test_stage_caps_frames_and_limits_pixels_to_visual_agents(rt, monkeypatch, name, pixel_count):
    media_ids = ['m1', 'm2', 'm3']
    for media_id in media_ids:
        rt.db.save('media', MediaRef(
            id=media_id, media_id=media_id, source_id=media_id, camera_id='camera-1',
            start_ms=0, end_ms=100, content_hash=media_id, storage_key='fictional',
        ))
    job = rt.jobs.start('event_review', 'bounded-stage', {'media_ids': media_ids})
    samples = []
    captured = {}

    def frames(media_id, start_ms, end_ms, count):
        samples.append((media_id, start_ms, end_ms, count))
        # Deliberately return extra records to test the coordinator's global cap.
        return SimpleNamespace(frames=[SimpleNamespace(
            id=f'{media_id}-{i}', frame_id=f'pts-{i}', model_dump_json=lambda: '{"frame":"fictional"}',
        ) for i in range(3)], coverage_gaps=['Fictional sampling gap'])

    async def run(content, **kwargs):
        captured.update(content=content, **kwargs)
        return SimpleNamespace(output=ObserverResult())

    monkeypatch.setattr(rt.media, 'frames', frames)
    monkeypatch.setattr(rt.media, 'model_image', lambda frame_id: b'fictional-pixels')
    _, deps = await run_stage(
        rt, job, name, ObserverRequest(media_ids=media_ids),
        model_factory=lambda _: object(), agent_builder=lambda *_: SimpleNamespace(run=run),
    )
    assert samples == [(media_id, 0, 100, 2) for media_id in media_ids]
    assert len(deps.seen_frames) == 6
    assert sum(isinstance(item, BinaryContent) for item in captured['content']) == pixel_count
    assert captured['model_settings'] == {'openai_store': False}
    assert captured['usage_limits'].request_limit == rt.config['limits']['requests']
    assert captured['usage_limits'].tool_calls_limit == rt.config['limits']['tools']
    assert rt.audit.rows(event_type='run_finished', run_id=deps.run_id)


async def test_missing_footage_stops_before_model_construction(rt):
    job = rt.jobs.start('facility_review', 'no-footage', {'media_ids': []})
    calls = []
    with pytest.raises(FileNotFoundError, match='No footage available'):
        await run_stage(rt, job, 'video_observer', ObserverRequest(media_ids=[]),
                        model_factory=lambda name: calls.append(name))
    assert not calls
    assert not rt.audit.rows(event_type='run_started')


async def test_text_stage_preserves_settings_and_audits_cancellation(rt):
    job = rt.jobs.start('safety_chat', 'cancelled-stage', {'media_ids': []})
    captured = {}

    async def run(content, **kwargs):
        captured.update(kwargs)
        rt.jobs.cancel(job.id)
        return SimpleNamespace(output=ObserverResult())

    with pytest.raises(asyncio.CancelledError):
        await run_stage(
            rt, job, 'safety_reviewer', ObserverRequest(media_ids=[]),
            model_factory=lambda _: object(), agent_builder=lambda *_: SimpleNamespace(run=run),
            text_only=True,
        )
    assert captured['model_settings'] == {
        'openai_store': False,
        'openai_reasoning_effort': rt.config['review_team']['chat_reasoning_effort'],
    }
    failures = rt.audit.rows(event_type='run_failed', run_id=captured['deps'].run_id)
    assert len(failures) == 1 and failures[0]['status'] == 'cancelled'
    assert failures[0]['error']['code'] == 'CancelledError'
    assert not rt.audit.rows(event_type='run_finished', job_id=job.id)
