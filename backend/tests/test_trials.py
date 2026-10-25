"""Resets affect temporary views and cache. Labels, media bytes and costs survive."""
import json
import shutil
from pathlib import Path
import cv2
import numpy as np
import pytest
from safety_app.infrastructure.runtime import Runtime
from safety_app.infrastructure.trials import Trials,TrialConflict
from safety_app.domain.models import Annotation,MediaRef
from safety_app.modules.labeling.service import Labeling
@pytest.fixture
def rt(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    return Runtime(tmp_path)
def frame(rt,id):
    rt.db.save('media',MediaRef(id=id,media_id=id,source_id=id,camera_id='camera-1',start_ms=0,end_ms=0,content_hash='source-'+id,storage_key='fixture'))
    ok,encoded=cv2.imencode('.jpg',np.zeros((16,16,3),np.uint8));assert ok
    return rt.media.add_frame(id,'f',0,encoded.tobytes())
def test_reset_protects_all_labels_and_training_dependencies_and_is_reversible(rt):
    labeled=frame(rt,'labeled');temporary=frame(rt,'temporary');frozen=frame(rt,'frozen')
    label=Labeling(rt).annotate(Annotation(frame_id=labeled.id,source_id='labeled',class_id=0,bbox=(.1,.1,.9,.9)))
    rt.db.save('dataset',{'id':'dataset','provenance':{'annotations':[{'frame_id':frozen.id}]}})
    rt.db.save('model',{'id':'weights','dataset_id':'dataset'})
    rt.db.save('model_evaluation',{'id':'metrics','model_id':'weights'})
    rt.db.save('event',{'id':'event','media_ids':['temporary']})
    rt.db.save('review',{'id':'review'})
    rt.db.save('review_cache',{'id':'cache','result_refs':['review']},immutable=True)
    training=rt.jobs.start('train','train-key',{'dataset_id':'dataset'})
    replay=rt.jobs.start('replay','repeatable-key',{'source_id':'temporary'})
    rt.budgets.switch(True)
    source=rt.root/'data/input/source.mp4';source.parent.mkdir(parents=True);source.write_bytes(b'keep original')
    weights=rt.root/'data/models/trained.pth';weights.parent.mkdir(parents=True);weights.write_bytes(b'keep weights')
    trials=Trials(rt);plan=trials.preview();assert plan.reset_counts['event']==1
    archive=trials.reset(plan.plan_id,'First test','operator')
    assert not rt.db.list('event') and not rt.db.list('review')
    assert {m['id'] for m in rt.db.list('media')}=={'labeled','frozen'}
    assert rt.db.get('annotation',label.id)['approval_status']=='draft'
    assert rt.db.list('dataset') and rt.db.list('model') and rt.db.list('model_evaluation')
    assert [j.id for j in rt.jobs.list()]==[training.id]
    assert rt.media.image(temporary.id).startswith(b'\xff\xd8')
    assert source.read_bytes()==b'keep original' and weights.read_bytes()==b'keep weights'
    assert rt.budgets.status()['stopped'] and rt.audit.rows(event_type='trial_reset')
    assert rt.jobs.get(replay.id).status=='cancelled'
    # Reset invalidates the cache and permits another immutable cached result at this key.
    with pytest.raises(KeyError):rt.db.get('review_cache','cache')
    rt.db.save('review_cache',{'id':'cache','result_refs':['new-review']},immutable=True)
    assert rt.db.get('review_cache','cache')['result_refs']==['new-review']
    new=rt.jobs.start('replay','repeatable-key',{'source_id':'temporary'})
    assert new.id!=replay.id
    restored=trials.restore(archive.id,'operator')
    assert restored.status=='restored' and rt.db.list('event')
    assert {m['id'] for m in rt.db.list('media')}=={'temporary','labeled','frozen'}
    assert rt.jobs.get(replay.id).status=='cancelled' # Restore never launches old work.
    assert trials.restore(archive.id,'operator').status=='restored'
    reopened=Runtime(rt.root);assert Trials(reopened).list()[0].status=='restored'

def test_reset_blocks_running_jobs_and_stale_preview_without_changes(rt):
    frame(rt,'media');trials=Trials(rt);plan=trials.preview()
    rt.db.save('event',{'id':'changed'})
    with pytest.raises(TrialConflict,match='changed'):trials.reset(plan.plan_id,'Test','operator')
    assert not trials.list() and rt.db.list('event')
    job=rt.jobs.start('train','running-training',{})
    rt.jobs.claim(['train'])
    plan=trials.preview();assert not plan.can_reset and plan.running_job_ids==[job.id]
    with pytest.raises(TrialConflict,match='running'):trials.reset(plan.plan_id,'Test','operator')
    assert not trials.list() and rt.db.list('event')

def test_archived_queued_trials_cannot_be_claimed_and_budget_ledger_remains(rt):
    rt.config['pricing']['gpt-6-luna']={'version':'fixture','input_per_million':1,'output_per_million':1,'image_usd':0}
    reservation=rt.budgets.reserve('r','w','gpt-6-luna',100,100)
    rt.budgets.reconcile(reservation,None)
    before=rt.budgets.status()['windows'];job=rt.jobs.start('event_review','review',{'media_ids':[]})
    trials=Trials(rt);archive=trials.reset(trials.preview().plan_id,'Test','operator')
    assert rt.jobs.claim(['event_review']) is None
    trials.restore(archive.id,'operator');assert rt.jobs.claim(['event_review']) is None
    assert rt.jobs.get(job.id).status=='cancelled'
    assert rt.budgets.status()['windows']==before and len(rt.budgets.status()['outstanding'])==1

def test_label_added_to_archived_evidence_protects_source_on_next_reset(rt):
    f=frame(rt,'historical');trials=Trials(rt);trials.reset(trials.preview().plan_id,'Test','operator')
    assert not rt.db.list('media')
    label=Labeling(rt).annotate(Annotation(frame_id=f.id,source_id='historical',class_id=0,bbox=(.1,.1,.9,.9)))
    assert rt.db.list('media') and rt.db.list('frame')
    trials.reset(trials.preview().plan_id,'Next test','operator')
    assert rt.db.list('media') and rt.db.get('annotation',label.id)

def test_legacy_history_stays_archived_after_migration_and_restart(rt):
    from app.storage.repository import Repository
    from app.evidence.store import EvidenceStore
    from app.models.contracts import RuleCandidate,FramePacket
    from safety_app.infrastructure.migration import migrate
    repo=Repository(rt.root/'data/artifacts/runs/lab.sqlite');store=EvidenceStore(rt.root/'data/artifacts/evidence')
    ctx=dict(source_id='legacy',camera_id='camera-1',session_id='s',run_generation=0,schema_version='1.0')
    repo.save('candidate',RuleCandidate(**ctx,id='event',kind='zone_dwell',timestamp_ms=0,start_ms=0,evidence_cutoff_ms=0))
    original=store.add(FramePacket(**ctx,frame_id=1,timestamp_ms=0,width=16,height=16,evidence_cutoff_ms=0),np.zeros((16,16,3),np.uint8))
    migrate(rt,repo,store);trials=Trials(rt);archive=trials.reset(trials.preview().plan_id,'Legacy test','operator')
    migrate(rt,repo,store)
    assert not repo.list('candidate') and not rt.db.list('event') and not rt.db.list('frame')
    assert repo.get('candidate','event') and store.image(original.id)
    trials.restore(archive.id,'operator');assert repo.list('candidate') and rt.db.list('event')

def test_trial_api_requires_session_and_preserves_labels(rt,monkeypatch):
    from fastapi.testclient import TestClient
    from safety_app.api.main import app
    from app import main as legacy
    from app.pipeline import Pipeline
    monkeypatch.setattr('safety_app.infrastructure.runtime._default',rt)
    monkeypatch.setattr(legacy,'pipeline',Pipeline(rt.root))
    f=frame(rt,'trial');label=Labeling(rt).annotate(Annotation(frame_id=f.id,source_id='trial',class_id=0,bbox=(.1,.1,.9,.9)))
    rt.db.save('event',{'id':'test-event'})
    with TestClient(app) as client:
        assert client.get('/api/trials/reset-preview').status_code==401
        assert client.post('/api/auth/session',json={}).status_code==200
        plan=client.get('/api/trials/reset-preview').json()
        assert client.post('/api/trials/reset',json={'plan_id':'stale','name':'Test'}).status_code==409
        response=client.post('/api/trials/reset',json={'plan_id':plan['plan_id'],'name':'UI test'})
        assert response.status_code==200,response.text
        archive=response.json();assert not rt.db.list('event') and rt.db.get('annotation',label.id)
        assert legacy.pipeline.session is None and not legacy.pipeline.review.enabled
        restored=client.post('/api/trials/archives/'+archive['id']+'/restore',json={})
        assert restored.status_code==200 and rt.db.list('event')
