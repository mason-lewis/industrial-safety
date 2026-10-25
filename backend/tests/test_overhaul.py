"""Deterministic contract and failure tests. All prices here are fictional fixtures."""
import asyncio
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse,ToolCallPart
from safety_app.domain.models import MediaRef,Annotation,CandidateEvent,ToolFailure,uid
from safety_app.infrastructure.runtime import Runtime
from safety_app.infrastructure.audit import Audit
from safety_app.infrastructure.budgets import BudgetLedger,BudgetBlocked
from safety_app.agents.shared.deps import AgentDeps,Scope
from safety_app.agents.video_observer.tools import sample_frames,get_camera_context
from safety_app.agents.safety_reviewer.tools import get_site_rules,read_source
from safety_app.agents.annotation_assistant.tools import get_label_frames,propose_boxes
from safety_app.modules.safety_review.sources import Sources
from safety_app.modules.safety_review.rules import Rules
from safety_app.modules.labeling.service import Labeling
from safety_app.workers.runner import execute

@pytest.fixture
def rt(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    r=Runtime(tmp_path)
    r.config['pricing']['gpt-6-luna']={'version':'fictional-fixture-v1','input_per_million':1,'output_per_million':1,'image_usd':.0001}
    return r

def footage(rt,mid='m',source='s',stamp=100):
    rt.db.save('media',MediaRef(id=mid,media_id=mid,source_id=source,camera_id='camera-1',start_ms=0,end_ms=stamp,content_hash='hash-'+source,storage_key='fixture'))
    ok,raw=cv2.imencode('.jpg',np.zeros((20,20,3),dtype=np.uint8));assert ok
    return rt.media.add_frame(mid,'f',stamp,raw.tobytes())

def deps(rt,frame):
    return AgentDeps(rt.media,Sources(rt.db),Rules(rt.db),rt.audit,rt.budgets,Scope('local',frozenset(['m']),{'m':(0,100)},frozenset(['camera-1']),frozenset([frame.id])),uid(),uid(),uid(),rt.camera,rt.classes,lambda *a:(_ for _ in ()).throw(ValueError('Detector unavailable')))

def reserve(rt,run='r',workflow='w',**kw):return rt.budgets.reserve(run,workflow,'gpt-6-luna',100,100,**kw)

def test_atomic_reservations_restart_and_tightest_cap(rt):
    rt.config['limits'].update(concurrency=20,day_usd=.001,month_usd=10,agent_usd=1,workflow_usd=1)
    def attempt(i):
        try:return reserve(rt,str(i),str(i))
        except BudgetBlocked:return None
    with ThreadPoolExecutor(max_workers=12) as pool:ids=list(pool.map(attempt,range(12)))
    assert len([i for i in ids if i])==5
    restarted=BudgetLedger(rt.db,rt.audit,rt.config)
    assert restarted.status()['windows']['day']['remaining_usd']==0
    with pytest.raises(BudgetBlocked):reserve(rt,'next','next')
    assert rt.audit.rows(event_type='budget_blocked')

@pytest.mark.parametrize('scope,cap',[('agent_usd',.0001),('workflow_usd',.0001),('day_usd',.0001),('month_usd',.0001)])
def test_each_budget_blocks_before_dispatch(rt,scope,cap):
    rt.config['limits'][scope]=cap
    with pytest.raises(BudgetBlocked):reserve(rt)
    with rt.db.connect() as db:assert db.execute('SELECT count(*) FROM reservations').fetchone()[0]==0

@pytest.mark.parametrize('change',[{'frames':7},{'media_bytes':6000001},{'clip_ms':10001}])
def test_media_bound_blocks(rt,change):
    with pytest.raises(BudgetBlocked):reserve(rt,**change)

def test_unknown_prices_upgrades_stop_concurrency_and_attempts(rt):
    rt.config['limits']['concurrency']=1  # this test exercises the single-slot boundary
    rt.config['pricing']['gpt-6-luna']['input_per_million']=None
    with pytest.raises(BudgetBlocked):reserve(rt)
    rt.config['pricing']['gpt-6-luna']['input_per_million']=1
    with pytest.raises(BudgetBlocked):rt.budgets.reserve('r','w','upgraded-model',1,1)
    rt.budgets.switch(True)
    with pytest.raises(BudgetBlocked):reserve(rt)
    rt.budgets.switch(False);id=reserve(rt)
    with pytest.raises(BudgetBlocked):reserve(rt,'second')
    rt.config['limits']['concurrency']=3  # a wider slot admits parallel requests from other agents
    parallel=reserve(rt,'second');rt.budgets.reconcile(parallel,0);rt.config['limits']['concurrency']=1
    rt.budgets.reconcile(id,None,failed=True)
    assert rt.budgets.status()['outstanding'][0]['state']=='unknown'
    with pytest.raises(BudgetBlocked,match='unresolved charge'):reserve(rt)
    # Attempt limits on a separate, fully settled workflow still apply.
    for _ in range(rt.config['limits']['requests']-1):
        id=reserve(rt,'clean','clean');rt.budgets.reconcile(id,0)
    id=reserve(rt,'clean','clean');rt.budgets.reconcile(id,0)
    with pytest.raises(BudgetBlocked):reserve(rt,'clean','clean')

@pytest.mark.asyncio
async def test_text_only_paid_accounting_without_image_price_blocks_pixels(rt):
    from pydantic_ai import BinaryContent
    from pydantic_ai.messages import ModelRequest,UserPromptPart,TextPart
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.usage import RequestUsage
    from safety_app.agents.shared.factory import GuardedModel
    rt.config['pricing']['gpt-6-luna']['image_usd']=None
    called=[]
    async def model_call(messages,info):
        called.append(True)
        return ModelResponse(parts=[TextPart('OK')],usage=RequestUsage(input_tokens=10,output_tokens=5),model_name='gpt-6-luna')
    model=FunctionModel(model_call,model_name='gpt-6-luna')
    guard=GuardedModel(model,rt.budgets,rt.audit,'text','workflow',paid=True)
    await guard.request([ModelRequest(parts=[UserPromptPart('A short text question')])],{},ModelRequestParameters())
    with rt.db.connect() as db:
        row=db.execute("SELECT actual,state FROM reservations WHERE run_id='text'").fetchone()
        assert row['actual']==15 and row['state']=='settled'
    request=rt.audit.rows(event_type='model_request')[0]
    assert request['resources']['reserved_usd']>0
    assert request['resources']['output_token_cap']==rt.config['limits']['output_per_request']
    _,raw=cv2.imencode('.jpg',np.zeros((20,20,3),dtype=np.uint8))
    with pytest.raises(BudgetBlocked,match='Unknown pricing'):
        await guard.request([ModelRequest(parts=[UserPromptPart([BinaryContent(raw.tobytes(),media_type='image/jpeg')])])],{},ModelRequestParameters())
    assert called==[True]
    with rt.db.connect() as db:assert db.execute('SELECT count(*) FROM reservations').fetchone()[0]==1

@pytest.mark.asyncio
async def test_reasoning_only_response_is_accounted_without_logging_hidden_text(rt):
    from pydantic_ai.messages import ModelRequest,UserPromptPart,ThinkingPart
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.usage import RequestUsage
    from safety_app.agents.shared.factory import GuardedModel
    async def model_call(messages,info):
        return ModelResponse(parts=[ThinkingPart('Fictional hidden reasoning must not be logged.')],usage=RequestUsage(input_tokens=10,output_tokens=2000,details={'reasoning_tokens':2000}),model_name='gpt-6-luna',finish_reason='length')
    guard=GuardedModel(FunctionModel(model_call,model_name='gpt-6-luna'),rt.budgets,rt.audit,'reasoning','workflow',paid=True)
    await guard.request([ModelRequest(parts=[UserPromptPart('A bounded fictional question')])],{'openai_reasoning_effort':'low'},ModelRequestParameters())
    row=rt.audit.rows(event_type='model_response')[0]
    assert row['output']==[]
    assert row['resources']['reasoning_tokens']==2000 and row['resources']['finish_reason']=='length'
    assert row['resources']['output_tokens']==2000 and row['resources']['cost_usd']>0
    assert 'Fictional hidden reasoning' not in json.dumps(rt.audit.rows())
    assert not rt.budgets.status()['outstanding']

def test_timeouts_retain_reservations_and_circuit_breaker(rt):
    for i in range(3):rt.budgets.reconcile(reserve(rt,str(i),str(i)),None,failed=True)
    assert len(rt.budgets.status()['outstanding'])==3
    with pytest.raises(BudgetBlocked,match='circuit'):reserve(rt,'four','four')
    assert rt.audit.rows(event_type='circuit_breaker_open')

def test_utc_day_month_rollover(rt):
    clock=[datetime(2026,10,31,23,59,59,tzinfo=timezone.utc)]
    rt.budgets.now=lambda:clock[0];id=reserve(rt);rt.budgets.reconcile(id,None)
    assert rt.budgets.status()['windows']['month']['reserved_usd']==.0002
    clock[0]+=timedelta(seconds=2)
    status=rt.budgets.status();assert status['windows']['day']['window_utc']=='2026-11-01'
    assert status['windows']['month']['reserved_usd']==0
    assert len(status['outstanding'])==1

def test_audit_concurrent_export_restart_redaction_append_only(rt):
    def emit(i):
        other=Audit(rt.root/'data/audit');other.write('tool_finished',arguments={'api_key':'private','note':'Bearer secret sk-abcdef rtsp://user:password@camera/path','hidden_reasoning':'private'},output={'i':i})
    with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(emit,range(15)))
    restarted=Audit(rt.root/'data/audit');rows=restarted.rows();assert len(rows)==15
    snapshot=json.loads((rt.root/'data/audit/audit.json').read_text());assert len(snapshot)==15
    assert [r['sequence'] for r in snapshot]==list(range(1,16))
    assert 'private' not in json.dumps(snapshot) and 'sk-abcdef' not in json.dumps(snapshot)
    with pytest.raises(Exception):
        with rt.audit.db.connect() as db:db.execute('DELETE FROM audit')

def test_export_failure_does_not_rollback_audit(rt,monkeypatch):
    monkeypatch.setattr('safety_app.infrastructure.audit.os.replace',lambda *a:(_ for _ in ()).throw(OSError('Fixture export failure')))
    rt.audit.write('run_started');assert len(rt.audit.rows())==1;assert rt.audit.export_error=='OSError'

def test_tools_scope_images_failures_and_tool_limit(rt):
    frame=footage(rt);d=deps(rt,frame);ctx=SimpleNamespace(deps=d)
    packet=sample_frames(ctx,'m',0,100,1);assert packet.content[1].data.startswith(b'\xff\xd8')
    for call in [lambda:sample_frames(ctx,'other',0,100,1),lambda:sample_frames(ctx,'m',0,101,1),lambda:get_camera_context(ctx,'other'),lambda:get_site_rules(ctx,'other','forklift'),lambda:get_label_frames(ctx,['other']),lambda:propose_boxes(ctx,frame.id,[999])]:
        with pytest.raises(ValueError):call()
    with pytest.raises(BudgetBlocked):
        for _ in range(rt.config['limits']['tools']+1):get_camera_context(ctx,'camera-1')
    assert any(r['status']=='failed' for r in rt.audit.rows(event_type='tool_finished'))

def models_for(rt,job,frame,bad=False):
    observation={'id':'observation-1','media_id':'m','start_ms':100,'end_ms':100,'description':'Visible fixture','evidence_refs':[frame.id],'method':'ai','uncertainty':['Synthetic plumbing fixture']}
    finding={'id':'finding-1','review_id':job.run_id,'event_id':job.payload.get('event_id'),'conclusion':'Condition needs site inspection','evidence_refs':['outside' if bad else frame.id],'uncertainty':['No sourced requirement supplied']}
    results={'video_observer':{'observations':[observation],'possible_events':[],'visibility_limits':['Sampled stills']},'safety_reviewer':{'findings':[finding],'missing_context':['Site requirements absent']},'challenge_reviewer':{'checked_finding_ids':['finding-1'],'issues':[],'limitations':['No requirement supplied']}}
    from safety_app.workflows.fixtures import team_fixture_models
    return team_fixture_models(results)

@pytest.mark.asyncio
async def test_review_evidence_findings_human_decisions_cache_and_restart(rt):
    frame=footage(rt);rt.db.save('event',CandidateEvent(id='e',camera_id='camera-1',event_type='walkway_entry',start_ms=0,end_ms=100,media_ids=['m'],producer_version='fixture'))
    job=rt.jobs.start('event_review','review-e',{'media_ids':['m'],'event_id':'e'})
    result=await execute(rt,job,models_for(rt,job,frame));assert result.status=='completed',result.error
    from safety_app.modules.safety_review.service import SafetyReview
    decision=SafetyReview(rt).save_human_decision('finding-1','uncertain','Inspect the site','operator','local');assert decision.reviewer_id=='operator'
    rt.audit.export();assert rt.db.get('finding','finding-1')['status']=='reviewed'
    job2=rt.jobs.start('event_review','review-e2',{'media_ids':['m'],'event_id':'e'})
    result2=await execute(rt,job2,lambda _:(_ for _ in ()).throw(AssertionError('Cache must avoid a model call')));assert result2.status=='completed'
    assert rt.audit.rows(event_type='cache_hit')
    reopened=Runtime(rt.root);assert reopened.db.get('decision',decision.id)['reason']=='Inspect the site'
    from safety_app.workflows.event_review import cache_key
    before=cache_key(rt,job);rt.config['agents']['video_observer']='changed';assert cache_key(rt,job)!=before

@pytest.mark.asyncio
async def test_malformed_output_incomplete_review_no_completed_finding(rt):
    frame=footage(rt);job=rt.jobs.start('facility_review','bad',{'media_ids':['m']})
    result=await execute(rt,job,models_for(rt,job,frame,bad=True));assert result.status=='failed'
    assert not rt.db.list('finding');assert rt.audit.rows(event_type='validation_failed');assert rt.db.list('coverage_gap')

@pytest.mark.asyncio
async def test_missing_footage_and_cancellation_visible(rt):
    f=footage(rt);(rt.root/'data/clips'/f'{f.content_hash}.jpg').unlink()
    job=rt.jobs.start('facility_review','missing',{'media_ids':['m']})
    result=await execute(rt,job,lambda _:(_ for _ in ()).throw(AssertionError('No provider without footage')))
    assert result.status=='failed' and result.error.code=='FileNotFoundError'
    other=rt.jobs.start('facility_review','cancel',{'media_ids':['m']});rt.jobs.cancel(other.id)
    assert rt.jobs.get(other.id).status=='cancelled'
    rt.jobs.finish(other,'completed',['fake']);assert rt.jobs.get(other.id).status=='cancelled'

def test_job_idempotency_atomic_claim_and_expired_lease(rt):
    job=rt.jobs.start('event_review','key',{'media_ids':['m']});assert rt.jobs.start('event_review','key',{'media_ids':['m']}).id==job.id
    with pytest.raises(ValueError):rt.jobs.start('event_review','key',{'media_ids':['other']})
    with ThreadPoolExecutor(max_workers=6) as pool:claims=list(pool.map(lambda _:rt.jobs.claim(['event_review']),range(6)))
    assert sum(j is not None for j in claims)==1
    with rt.db.connect() as db:db.execute("UPDATE jobs SET lease='2000' WHERE id=?",(job.id,))
    rt.jobs.claim(['event_review']);assert rt.jobs.get(job.id).status=='interrupted'

def test_dataset_approval_leakage_immutable_export(rt):
    service=Labeling(rt);ids=[]
    for i in range(3):
        f=footage(rt,str(i),str(i));a=service.annotate(Annotation(frame_id=f.id,source_id=str(i),class_id=0,bbox=(.1,.1,.5,.5)));ids.append(a.id)
    splits={'0':'train','1':'valid','2':'test'}
    with pytest.raises(ValueError):service.create_dataset_version(ids,splits)
    service.approve_annotations(ids,'operator');d=service.create_dataset_version(ids,splits)
    assert service.create_dataset_version(ids,splits).id==d.id
    from safety_app.modules.labeling.export import export_dataset
    for fmt in ('coco','yolo'):
        folder=export_dataset(rt,d.id,fmt);assert (folder/'train').is_dir() and (folder/'test').is_dir()
    duplicate=rt.db.get('media','1');duplicate['content_hash']='hash-0';rt.db.save('media',duplicate)
    with pytest.raises(ValueError,match='Duplicate'):service.create_dataset_version(ids,splits)

@pytest.mark.asyncio
async def test_no_promotion_without_completed_evaluation(rt):
    rt.db.save('model',{'id':'v','weights_ref':'registered','dataset_id':'d'})
    rt.db.save('model_evaluation',{'id':'eval','model_id':'v','status':'failed','passed':False})
    with pytest.raises(ValueError):await Labeling(rt).promote_model('v','eval',['camera-1'],'operator')
    assert not rt.db.list('deployment')

@pytest.mark.asyncio
async def test_budget_blocked_provider_is_never_called_and_timeouts_retained(rt):
    from safety_app.agents.shared.factory import GuardedModel
    from pydantic_ai.models import ModelRequestParameters
    called=[]
    async def model_call(messages,info):called.append(1);raise TimeoutError('Fixture timeout')
    model=FunctionModel(model_call,model_name='gpt-6-luna')
    guard=GuardedModel(model,rt.budgets,rt.audit,'r','w',paid=True)
    rt.config['limits']['day_usd']=0
    with pytest.raises(BudgetBlocked):await guard.request([],{},ModelRequestParameters())
    assert not called
    rt.config['limits']['day_usd']=2
    with pytest.raises(TimeoutError):await guard.request([],{},ModelRequestParameters())
    # A timed-out paid request may have been billed. Preserve one unknown charge,
    # rather than automatically submitting the same work again.
    assert called==[1] and len(rt.budgets.status()['outstanding'])==1
    assert not rt.audit.rows(event_type='retry_scheduled')

class FixtureDetector:
    labels={1:'person'}
    provenance={'data_kind':'synthetic_fixture_only','version':'fixture-v1'}
    def predict(self,image,confidence):
        from safety_app.workflows.fixtures import FixtureDetections
        return FixtureDetections()
class FixtureTracker:
    def __init__(self,**kwargs):
        from collections import defaultdict,deque
        self.trails=defaultdict(lambda:deque(maxlen=24))
    def update(self,detections,timestamp=None):return detections

def make_replay(rt,monkeypatch):
    monkeypatch.setattr('app.pipeline.ByteTrackAdapter',FixtureTracker)
    from app.models.contracts import LabConfig,ZoneConfig,RuleConfig
    config=LabConfig(target_fps=10,zones=[ZoneConfig(id='walkway',label='Fixture walkway',points=[(0,0),(1,0),(1,1),(0,1)])],rules=[RuleConfig(id='dwell',kind='zone_dwell',zone_id='walkway',duration_ms=400)])
    (rt.root/'data/config').mkdir(parents=True);(rt.root/'data/config/lab.json').write_text(config.model_dump_json())
    folder=rt.root/'data/input';folder.mkdir(parents=True)
    video=folder/'fixture.mp4';writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),10,(20,20))
    assert writer.isOpened()
    for i in range(10):writer.write(np.full((20,20,3),i*10,dtype=np.uint8))
    writer.release()
    from app.pipeline import Pipeline
    lab=Pipeline(rt.root,detector=FixtureDetector());sources=lab.discover();lab.repository.db.close();lab.evidence.db.close()
    return rt.jobs.start('replay','fixture-replay',{'source_id':sources[0]['id'],'profile_id':'camera-1','start_ms':0,'end_ms':900})

@pytest.mark.asyncio
async def test_fixture_replay_to_cv_candidate_review_finding_and_decision(rt,monkeypatch):
    job=make_replay(rt,monkeypatch);result=await execute(rt,job,detector=FixtureDetector());assert result.status=='completed',result.error
    events=rt.db.list('event');assert len(events)==1
    reviewjob=next(j for j in rt.jobs.list() if j.kind=='event_review')
    frames=rt.db.list('frame');frame=next(f for f in frames if f['timestamp_ms']==400)
    outputs={'video_observer':{'observations':[{'media_id':job.run_id,'start_ms':400,'end_ms':400,'description':'Synthetic figure in fixture zone','evidence_refs':[frame['id']],'method':'ai','uncertainty':['Fixture only']}],'visibility_limits':['No real safety conclusion']},'safety_reviewer':{'findings':[{'id':'fixture-finding','event_id':events[0]['id'],'review_id':reviewjob.run_id,'conclusion':'Fixture plumbing needs human review','evidence_refs':[frame['id']],'uncertainty':['No site requirement']} ]},'challenge_reviewer':{'checked_finding_ids':['fixture-finding'],'issues':[]}}
    from safety_app.workflows.fixtures import team_fixture_models
    reviewed=await execute(rt,reviewjob,team_fixture_models(outputs));assert reviewed.status=='completed',reviewed.error
    from safety_app.modules.safety_review.service import SafetyReview
    decision=SafetyReview(rt).save_human_decision('fixture-finding','uncertain','Synthetic fixture only','operator','local')
    assert rt.db.get('decision',decision.id)['decision']=='uncertain'
    assert any(j.kind=='periodic_review' for j in rt.jobs.list())
    assert not rt.budgets.status()['outstanding']

@pytest.mark.asyncio
async def test_cv_failure_keeps_evidence_periodic_review_and_label_queue(rt,monkeypatch):
    job=make_replay(rt,monkeypatch)
    class FailedDetector(FixtureDetector):
        def predict(self,*args):raise RuntimeError('Fixture CV failure')
    result=await execute(rt,job,detector=FailedDetector());assert result.status=='completed'
    assert rt.db.list('frame') and rt.db.list('label_queue') and not rt.db.list('event')
    assert any(j.kind=='periodic_review' for j in rt.jobs.list())
    assert rt.audit.rows(event_type='cv_failed')

def test_new_api_session_scope_jobs_audit_cost_and_media(rt,monkeypatch):
    from fastapi.testclient import TestClient
    from safety_app.api.main import app
    from app.pipeline import Pipeline
    from app import main as legacy_main
    monkeypatch.setattr('safety_app.infrastructure.runtime._default',rt)
    monkeypatch.setattr(legacy_main,'pipeline',Pipeline(rt.root,detector=FixtureDetector()))
    f=footage(rt)
    with TestClient(app) as client:
        assert client.post('/api/budgets/stop',json={'stopped':True}).status_code==401
        assert client.post('/api/auth/session',json={},headers={'Origin':'https://foreign.invalid'}).status_code==403
        assert client.post('/api/auth/session',json={}).status_code==200
        response=client.post('/api/reviews',json={'media_ids':['m'],'idempotency_key':'api-review'});assert response.status_code==200,response.text
        jid=response.json()['id'];assert client.get('/api/jobs/'+jid).json()['status']=='queued'
        assert client.get('/api/media/frames/'+f.id).content.startswith(b'\xff\xd8')
        assert client.get('/api/audit?job_id='+jid).json()
        assert client.post('/api/budgets/stop',json={'stopped':True}).status_code==200
        assert client.get('/api/jobs/'+jid).json()['status']=='cancelled'
        assert client.get('/api/budgets').json()['stopped'] is True
        assert client.post('/api/reviews',json={'media_ids':['m'],'idempotency_key':'foreign','reviewer_id':'admin'}).status_code==422
        assert client.get('/openapi.json').status_code==200

def test_repeatable_migration_preserves_originals(rt):
    from app.storage.repository import Repository
    from app.evidence.store import EvidenceStore
    from app.models.contracts import RuleCandidate,FramePacket
    from safety_app.infrastructure.migration import migrate
    repo=Repository(rt.root/'data/artifacts/runs/lab.sqlite');store=EvidenceStore(rt.root/'data/artifacts/evidence')
    ctx=dict(source_id='legacy-source',camera_id='camera-1',session_id='legacy-session',run_generation=0,schema_version='1.0')
    event=RuleCandidate(**ctx,id='legacy-event',kind='zone_dwell',timestamp_ms=100,start_ms=0,evidence_cutoff_ms=100)
    repo.save('candidate',event);ref=store.add(FramePacket(**ctx,frame_id=1,timestamp_ms=100,width=20,height=20,evidence_cutoff_ms=100),np.zeros((20,20,3),np.uint8))
    migrate(rt,repo,store);before=(len(rt.db.list('event')),len(rt.db.list('frame')));migrate(rt,repo,store)
    assert before==(len(rt.db.list('event')),len(rt.db.list('frame')))
    assert repo.get('candidate','legacy-event')['id']=='legacy-event'
    assert store.image(ref.id).startswith(b'\xff\xd8')
    assert rt.db.get('event','legacy-event')['media_ids']==['legacy-session-0']

def test_source_and_class_tools_reject_scope(rt):
    from safety_app.domain.models import SourceExcerpt
    frame=footage(rt);d=deps(rt,frame);ctx=SimpleNamespace(deps=d)
    rt.db.save('source',SourceExcerpt(id='foreign',source_id='foreign',title='Foreign procedure',section='full',text='Exact text',source_type='procedure',version='1',site_id='other'),immutable=True)
    with pytest.raises(ValueError,match='site scope'):read_source(ctx,'foreign')
    from safety_app.agents.annotation_assistant.tools import get_class_definitions
    with pytest.raises(ValueError):get_class_definitions(ctx,'unassigned')
    with pytest.raises(TypeError):d.scope.intervals['m']=(0,999999)

def test_final_review_commit_is_atomic_and_cancelled_job_wins(rt):
    job=rt.jobs.start('event_review','atomic',{'media_ids':['m']});rt.jobs.cancel(job.id)
    with pytest.raises(asyncio.CancelledError):rt.db.save_many([('review',{'id':'x'}),('finding',{'id':'y'})],job.id)
    assert not rt.db.list('review') and not rt.db.list('finding')


def test_audit_snapshot_is_bounded_and_never_copies_history(tmp_path,monkeypatch):
    from safety_app.infrastructure import audit as audit_module
    monkeypatch.setattr(audit_module,'SNAPSHOT_ROWS',3)
    log=audit_module.Audit(tmp_path/'audit')
    ids=[log.write('fixture_event',output={'n':n}) for n in range(10)]
    snapshot=json.loads((tmp_path/'audit/audit.json').read_text())
    assert [row['id'] for row in snapshot]==ids[-3:]
    assert len(log.rows(limit=100))==10  # SQLite keeps the complete record.
    assert sorted(p.name for p in (tmp_path/'audit').iterdir() if p.suffix=='.json')==['audit.json']
