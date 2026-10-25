"""Operator AI control and library retirement, with no provider traffic."""
import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from safety_app.infrastructure.runtime import Runtime
from safety_app.infrastructure.ai_control import ai_status, require_hosted
from safety_app.infrastructure.demo import prepare_demo
from safety_app.modules.monitoring.recordings import library, sources, resolve


@pytest.fixture
def rt(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    return Runtime(tmp_path)


def test_ai_readiness_and_stop_survive_new_runtime_without_exposing_key(rt, monkeypatch):
    monkeypatch.setenv('SAFETY_PAID_REVIEW','1')
    monkeypatch.setenv('OPENAI_API_KEY','offline-secret-not-a-provider-key')
    assert ai_status(rt).enabled
    assert 'offline-secret' not in ai_status(rt).model_dump_json()
    rt.budgets.switch(True)
    assert not ai_status(Runtime(rt.root)).enabled
    with pytest.raises(ValueError,match='AI is off'):require_hosted(rt,'hosted')
    rt.budgets.switch(False)
    assert ai_status(Runtime(rt.root)).enabled
    monkeypatch.setenv('OPENAI_API_KEY','')
    with pytest.raises(ValueError,match='OPENAI_API_KEY'):require_hosted(rt,'hosted')
    monkeypatch.setenv('SAFETY_PAID_REVIEW','0')
    with pytest.raises(ValueError,match='offline'):require_hosted(rt,'hosted')


def test_global_switch_cancels_work_preserves_allowances_and_unknown_reservations(rt, monkeypatch):
    from safety_app.api.main import app
    from safety_app.infrastructure.budgets import BudgetBlocked
    monkeypatch.setattr('safety_app.infrastructure.runtime._default',rt)
    monkeypatch.setenv('SAFETY_PAID_REVIEW','1')
    monkeypatch.setenv('OPENAI_API_KEY','offline-only')
    job=rt.jobs.start('video_analysis','queued',{'mode':'hosted'},'local')
    reservation=rt.budgets.reserve('r','w','gpt-6-luna',100,100,0,0)
    rt.budgets.reconcile(reservation,failed=True)
    before=rt.budgets.status()
    client=TestClient(app)
    assert client.get('/api/ai').status_code==401
    assert client.post('/api/auth/session',json={}).status_code==200
    assert client.get('/api/ai').json()['enabled']
    assert client.post('/api/budgets/stop',json={'stopped':True}).status_code==200
    assert not client.get('/api/ai').json()['enabled']
    assert rt.jobs.get(job.id).status=='cancelled'
    with pytest.raises(BudgetBlocked,match='stopped'):
        rt.budgets.reserve('another','another','gpt-6-luna',100,100,0,0)
    assert client.post('/api/budgets/stop',json={'stopped':False}).status_code==200
    after=rt.budgets.status()
    assert before['limits']==after['limits']
    assert before['windows']==after['windows']
    assert before['outstanding']==after['outstanding']
    assert rt.jobs.get(job.id).status=='cancelled'  # Switching on does not replay old work.


async def test_switch_off_cancels_inflight_transport_and_retains_unknown_charge(rt):
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.models import ModelRequestParameters
    from safety_app.agents.shared.factory import GuardedModel
    started=asyncio.Event();cancelled=asyncio.Event()
    async def blocked_provider(messages, info):
        started.set()
        try:await asyncio.Future()
        finally:cancelled.set()
    guard=GuardedModel(FunctionModel(blocked_provider,model_name='gpt-6-luna'),rt.budgets,rt.audit,'r','w',paid=True)
    task=asyncio.create_task(guard.request([],{},ModelRequestParameters()))
    await asyncio.wait_for(started.wait(),1)
    rt.budgets.switch(True)
    with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,1)
    assert cancelled.is_set()
    with rt.db.connect() as db:
        rows=db.execute('SELECT state,actual,amount FROM reservations').fetchall()
    assert len(rows)==1 and rows[0]['state']=='unknown' and rows[0]['actual'] is None and rows[0]['amount']>0


def test_retired_samples_leave_both_libraries_but_historical_paths_resolve(rt):
    from app.pipeline import Pipeline
    folder=rt.root/'data/input';folder.mkdir(parents=True)
    old=folder/'sample.mp4';old.write_bytes(b'historical video')
    source_id=next(iter(library(rt.root)))
    (rt.root/'data/library-hidden.json').write_text(json.dumps({'source_ids':[source_id]}))
    assert sources(rt.root)==[]
    assert resolve(rt.root,source_id).read_bytes()==b'historical video'
    # Exercise discovery without initializing a detector.
    pipeline=SimpleNamespace(root=rt.root,sources={})
    assert not any(row['id']==source_id for row in Pipeline.discover(pipeline))
    (folder/'uploaded-new.mp4').write_bytes(b'new video')
    assert [s.name for s in sources(rt.root)]==['uploaded-new.mp4']
    assert any(row['name']=='uploaded-new.mp4' for row in Pipeline.discover(pipeline))


def test_startup_does_not_reimport_bundled_videos(tmp_path):
    project=Path(__file__).resolve().parents[2]
    rt=prepare_demo(tmp_path,project)
    assert sources(rt.root)==[]
    assert rt.db.list('source')  # Curated references remain available to the agents.
