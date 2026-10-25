"""Offline language-agent image review; fictional pixels and mocked provider only."""
import asyncio
import json
from pathlib import Path
import shutil
import cv2
import numpy as np
import pytest
from pydantic import ValidationError
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse,ToolCallPart
from safety_app.infrastructure.runtime import Runtime
from safety_app.agents.annotation_assistant.models import ImageReviewResult
from app.trainer.workspace import LearningWorkspace
from app.trainer import image_review

@pytest.fixture
def setup(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    rt=Runtime(tmp_path);rt.config.pop('learning_image_review',None);ws=LearningWorkspace(tmp_path);p=ws.create('unsafe forklift title',['person','forklift','pallet'])
    folder=ws.folder(p['id'])/'images';folder.mkdir()
    image=np.zeros((48,64,3),np.uint8);image[10:35,20:45]=100;cv2.imwrite(str(folder/'frame.jpg'),image)
    p['clips']=[dict(id='clip',name='expected_forklift.mp4',group='unsafe',split='train')]
    p['frames']=[dict(id='frame',clip_id='clip',file_name='frame.jpg',width=64,height=48,timestamp_ms=0,
        boxes=[dict(id='human',label='person',bbox=[.1,.1,.1,.3])],reviewed=True,verified_empty=False,revision=2,
        proposals=[dict(id='detector',label='person',bbox=[.3,.2,.4,.5],score=.8),dict(id='rejected',label='person',bbox=[.5,.5,.1,.1],score=.5)],
        proposal_decisions={'detector':'accepted','rejected':'rejected'})]
    ws.write(p);return ws,rt,p

def answer():
    return dict(decisions=[dict(target_id='human',decision='accept',reason='Visible fixture candidate'),
        dict(target_id='detector',decision='adjust',reason='Fixture class correction',replacement=dict(label='forklift',bbox=[.3,.2,.4,.5]))],
        additions=[dict(label='pallet',bbox=[.6,.6,.2,.2])],limitations=['Fictional output, not an accuracy check'])

def model(response,seen=None):
    async def run(messages,info):
        if seen is not None:seen.extend(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,response)])
    return FunctionModel(run)

@pytest.mark.asyncio
async def test_image_reviewer_corrects_adds_and_preserves_human_labels_and_history(setup):
    ws,rt,p=setup;seen=[]
    before=ws.read(p['id'])['frames'][0]
    reviewed=await image_review.review(ws,rt,p['id'],'frame',2,model(answer(),seen));f=reviewed['frames'][0]
    assert f['boxes']==before['boxes'] and f['reviewed'] and f['revision']==2
    assert f['proposals']==before['proposals'] and f['proposal_decisions']==before['proposal_decisions']
    assert f['ai_review']['added_boxes'][0]['label']=='pallet'
    assert f['ai_review']['result']['decisions'][1]['replacement']['label']=='forklift'
    assert len(reviewed['ai_review_history'])==1
    assert image_review.candidates(f)==[before['boxes'][0],{k:before['proposals'][0][k] for k in ('id','label','bbox')}]
    # Same revision and packet returns persisted recommendations without another provider run.
    async def forbidden(*a):raise AssertionError('must use existing result')
    cached=await image_review.review(ws,rt,p['id'],'frame',2,FunctionModel(forbidden))
    assert cached['frames'][0]['ai_review']['id']==f['ai_review']['id']
    rows=rt.audit.rows(1000)
    assert all(r['configuration']['fake_provider'] for r in rows if r['event_type']=='model_request')
    assert all('source metadata excluded' in r['resources']['image_mode'] for r in rows if r['event_type']=='model_request')
    assert not rt.budgets.status()['outstanding']
    approved=ws.label(p['id'],'frame',[before['boxes'][0],dict(id='detector',label='forklift',bbox=[.3,.2,.4,.5]),*f['ai_review']['added_boxes']],True,False,
        {'detector':'adjusted','rejected':'rejected'},2,f['ai_review']['id'])
    assert approved['review_history'][-1]['ai_review_id']==f['ai_review']['id']
    assert approved['frames'][0]['revision']==3
    with pytest.raises(ValueError,match='stale'):ws.label(p['id'],'frame',approved['frames'][0]['boxes'],True,False,{'detector':'adjusted','rejected':'rejected'},3,f['ai_review']['id'])

def test_pixel_packet_excludes_titles_roles_categories_and_strips_metadata(setup):
    ws,rt,p=setup
    first=image_review.prepare(ws,p['id'],'frame',2)
    p['name']='bias';p['clips'][0].update(name='no_person.mp4',group='safe_category',split='test',intake_source={'source_category':'forklift'})
    p['frames'][0]['timestamp_ms']=9999;ws.write(p)
    second=image_review.prepare(ws,p['id'],'frame',2)
    assert first==second
    assert set(first[0])=={'classes','candidates'}
    assert not any(v in first[1] for v in ('unsafe','source_category','train','file_name','score'))
    assert max(cv2.imdecode(np.frombuffer(first[2],np.uint8),cv2.IMREAD_COLOR).shape[:2])<=1024

@pytest.mark.asyncio
async def test_invalid_agent_output_and_failure_do_not_replace_labels(setup):
    ws,rt,p=setup;bad=answer();bad['decisions'][0]['target_id']='foreign'
    before=ws.read(p['id'])
    with pytest.raises(Exception):await image_review.review(ws,rt,p['id'],'frame',2,model(bad))
    assert ws.read(p['id'])['frames']==before['frames']
    bad=answer();bad['additions'][0]['label']='invented class'
    with pytest.raises(ValueError,match='exact requested'):image_review.validate_result(ImageReviewResult.model_validate(bad),image_review.prepare(ws,p['id'],'frame',2)[0])
    bad=answer();bad['additions'][0]['bbox']=[.8,.2,.4,.4]
    with pytest.raises(ValidationError):ImageReviewResult.model_validate(bad)

@pytest.mark.asyncio
async def test_changed_labels_classes_frozen_and_cancellation_keep_results_unapplied(setup):
    ws,rt,p=setup
    async def mutate(messages,info):
        state=ws.read(p['id']);state['frames'][0]['revision']=3;ws.write(state)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,answer())])
    with pytest.raises(ValueError,match='changed during'):await image_review.review(ws,rt,p['id'],'frame',2,FunctionModel(mutate))
    assert 'ai_review' not in ws.read(p['id'])['frames'][0]
    p=ws.read(p['id']);p['frozen']=True;ws.write(p)
    with pytest.raises(ValueError,match='frozen'):await image_review.review(ws,rt,p['id'],'frame',3,model(answer()))

@pytest.mark.asyncio
async def test_hosted_setup_is_explicit_and_missing_allowance_blocks_without_dispatch(setup,monkeypatch):
    ws,rt,p=setup
    monkeypatch.setenv('SAFETY_PAID_REVIEW','1');monkeypatch.setenv('OPENAI_API_KEY','fixture-never-dispatched')
    assert not image_review.status(rt)['ready']
    with pytest.raises(image_review.BudgetBlocked,match='image cost allowance'):await image_review.review(ws,rt,p['id'],'frame',2)
    assert 'ai_review' not in ws.read(p['id'])['frames'][0]
    state=image_review.save_settings(rt,.001)
    assert state['ready'] and rt.config['pricing'][state['model']]['image_usd'] is None
    assert json.loads((rt.root/'configs/agents.yaml').read_text())['learning_image_review']['image_usd']==.001
    rt.budgets.switch(True);assert not image_review.status(rt)['ready']
    activity=image_review.activity(ws,rt,p['id'],'frame')
    assert activity['attempts'][-1]['status']=='blocked'
    assert any(r['event_type']=='run_failed' for r in activity['audit'])

@pytest.mark.asyncio
async def test_cancelled_image_review_keeps_all_original_labels(setup):
    ws,rt,p=setup;started=asyncio.Event()
    async def waiting(messages,info):
        started.set();await asyncio.Event().wait()
    before=ws.read(p['id'])
    task=asyncio.create_task(image_review.review(ws,rt,p['id'],'frame',2,FunctionModel(waiting)))
    await started.wait()
    state=image_review.activity(ws,rt,p['id'],'frame')
    assert state['attempts'][-1]['status']=='running' and state['attempts'][-1]['stage']=='waiting_for_model'
    assert any(r['event_type']=='model_request' for r in state['audit'])
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert ws.read(p['id'])['frames']==before['frames']
    assert any(r['event_type']=='run_failed' and r['status']=='cancelled' for r in rt.audit.rows(1000))

def test_image_review_api_rejects_metadata_and_links_human_save(setup,monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.trainer import api
    from app.pipeline import Pipeline
    from safety_app.infrastructure import runtime as runtime_module
    ws,rt,p=setup
    monkeypatch.setattr(main,'pipeline',Pipeline(ws.root))
    monkeypatch.setattr(runtime_module,'_default',rt)
    original=image_review.review
    async def offline(ws,rt,pid,fid,revision):return await original(ws,rt,pid,fid,revision,model(answer()))
    monkeypatch.setattr(image_review,'review',offline)
    with TestClient(main.app) as client:
        url=f"/api/learning/projects/{p['id']}/frames/frame/image-review"
        assert client.post(url,json={'expected_revision':2,'title':'unsafe'}).status_code==422
        assert client.put('/api/learning/image-review/settings',json={'image_usd':0}).status_code==422
        assert client.post(url,json={'expected_revision':1}).status_code==400
        response=client.post(url,json={'expected_revision':2})
        assert response.status_code==200
        frame=response.json()['frames'][0]
        assert frame['reviewed'] and frame['boxes']==p['frames'][0]['boxes']
        assert frame['ai_review']['added_boxes'][0]['label']=='pallet'
        state=client.get(url+'/activity').json()
        assert state['attempts'][-1]['status']=='completed'
        assert state['selected_run_id']==frame['ai_review']['id']
        assert client.get(url+'/activity?run_id=foreign').status_code==400
        download=client.get(url+'/audit/download',params={'run_id':frame['ai_review']['id']})
        assert download.status_code==200
        events=[json.loads(line) for line in download.text.splitlines()]
        assert {'run_started','model_request','model_response','run_finished'}<={e['event_type'] for e in events}
        assert not any(e['event_type']=='tool_started' for e in events)
        saved=dict(boxes=p['frames'][0]['boxes']+[dict(id='detector',label='forklift',bbox=[.3,.2,.4,.5]),*frame['ai_review']['added_boxes']],reviewed=True,
            proposal_decisions={'detector':'adjusted','rejected':'rejected'},expected_revision=2,ai_review_id=frame['ai_review']['id'])
        assert client.put(url.removesuffix('/image-review'),json=saved).status_code==200
        assert client.put(url.removesuffix('/image-review'),json=saved).status_code==400
        trace=client.get(url+'/activity').json()['audit']
        assert any(r['event_type']=='human_review_saved' for r in trace)
        assert any(r['event_type']=='human_review_save_failed' for r in trace)

def test_activity_marks_orphaned_attempts_interrupted_and_restores_legacy_reviews(setup):
    ws,rt,p=setup
    p['ai_review_attempts']=[dict(id='old-run',frame_id='frame',created_at=1,updated_at=1,revision=2,status='running',stage='waiting_for_model',model='local_fixture')]
    p['ai_review_history']=[dict(id='legacy',frame_id='frame',created_at=0,revision=1,model='local_fixture')];ws.write(p)
    state=image_review.activity(ws,rt,p['id'],'frame')
    assert state['attempts'][-1]['status']=='interrupted'
    assert state['attempts'][0]['id']=='legacy'
    assert any(r['event_type']=='run_interrupted' for r in state['audit'])
    second=image_review.activity(ws,rt,p['id'],'frame')
    assert len([r for r in second['audit'] if r['event_type']=='run_interrupted'])==1

@pytest.mark.asyncio
async def test_real_draft_tools_edit_and_audit_without_approving(setup):
    ws,rt,p=setup;before=p['frames'][0].copy();calls=0
    async def tools_model(messages,info):
        nonlocal calls
        calls+=1
        if calls==1:
            assert {t.name for t in info.function_tools}==set(image_review.TOOL_NAMES)
            return ModelResponse(parts=[ToolCallPart('decide_candidates',{'decisions':answer()['decisions']}),
                                       ToolCallPart('add_object_boxes',{'boxes':[dict(label='person',bbox=[.6,.6,.2,.2]) ]})])
        if calls==2:return ModelResponse(parts=[ToolCallPart('replace_added_boxes',{'boxes':answer()['additions']}),ToolCallPart('inspect_draft',{})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,answer())])
    p=await image_review.review(ws,rt,p['id'],'frame',2,FunctionModel(tools_model))
    assert p['frames'][0]['boxes']==before['boxes'] and p['frames'][0]['reviewed']==before['reviewed']
    assert p['frames'][0]['ai_review']['result']==ImageReviewResult.model_validate(answer()).model_dump(mode='json')
    rows=rt.audit.rows(1000)
    assert {r['tool_name'] for r in rows if r['event_type']=='tool_started'}==set(image_review.TOOL_NAMES)
    assert len([r for r in rows if r['event_type']=='tool_finished' and r['status']=='ok'])==4
    assert all(r['configuration']['fake_provider'] for r in rows if r['event_type']=='model_request')

@pytest.mark.asyncio
async def test_draft_tool_scope_and_final_consistency(setup):
    from safety_app.agents.annotation_assistant.image_tools import ImageDraftTools
    from safety_app.agents.annotation_assistant.models import ImageLabelDecision,ImageObjectBox
    ws,rt,p=setup;draft=ImageDraftTools(image_review.prepare(ws,p['id'],'frame',2)[0])
    with pytest.raises(ValueError,match='outside'):draft.check({'foreign':ImageLabelDecision(target_id='foreign',decision='reject',reason='fixture')},[])
    with pytest.raises(ValueError,match='Unknown'):draft.check({},[ImageObjectBox(label='unknown',bbox=[.1,.1,.2,.2])])
    draft.used=True
    with pytest.raises(ValueError,match='match'):draft.validate(ImageReviewResult.model_validate(answer()))
    calls=0
    async def invalid(messages,info):
        nonlocal calls
        calls+=1
        if calls==1:return ModelResponse(parts=[ToolCallPart('decide_candidates',{'decisions':[dict(target_id='foreign',decision='reject',reason='fixture')]})])
        raise AssertionError('Invalid tool must stop the run')
    with pytest.raises(Exception):await image_review.review(ws,rt,p['id'],'frame',2,FunctionModel(invalid))
    assert 'ai_review' not in ws.read(p['id'])['frames'][0]
    assert any(r['event_type']=='tool_finished' and r['status']=='failed' for r in rt.audit.rows(100))
