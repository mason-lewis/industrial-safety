"""Synthetic two-layer screening, durable handoff, cost boundaries and reporting inputs."""
import copy
import shutil
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
import pytest
from pydantic_ai import ToolReturn
from safety_app.infrastructure.runtime import Runtime
from safety_app.domain.models import MediaRef,Interval
from safety_app.domain.video_analysis import VideoAnalysisRequest
from safety_app.domain.video_safety import VisualSafetyCheck,ReviewTriage
from safety_app.modules.monitoring.recordings import sources
from safety_app.modules.safety_review.video_analysis import VideoAnalysisService
from safety_app.modules.safety_review.video_cases import overview,dispatch_ready_cases
from safety_app.modules.safety_review.safety_metrics import metrics
from safety_app.workers.runner import execute
from safety_app.workflows.video_fixture import models
from safety_app.workflows.video_analysis import validate_screening
from safety_app.workflows.agent_stage import dependencies
from safety_app.agents.video_observer.video_models import VideoObservationResult
from safety_app.agents.video_observer.video_tools import inspect_safety_detail,get_safety_checklist

@pytest.fixture
def rt(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    return Runtime(tmp_path)

def video(rt,seconds=2):
    folder=rt.root/'data/input';folder.mkdir(parents=True,exist_ok=True)
    path=folder/'synthetic.mp4';writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),10,(32,32))
    assert writer.isOpened()
    for index in range(seconds*10):writer.write(np.full((32,32,3),index%255,np.uint8))
    writer.release();return sources(rt.root)[0].id

def job(rt,source,key='two-layers',**kwargs):
    return VideoAnalysisService(rt).request(VideoAnalysisRequest(source_id=source,question='Screen visible safety concerns.',mode='fixture',idempotency_key=key,**kwargs),'local')

async def finish_children(rt):
    children=[child for child in rt.jobs.list() if child.kind=='video_safety_review']
    for child in children:
        result=await execute(rt,child);assert result.status=='completed',result.error
    return children


async def test_validated_team_stages_survive_finalization_failure_without_new_requests(rt,monkeypatch):
    import safety_app.workflows.event_review as workflow
    parent=job(rt,video(rt));await execute(rt,parent)
    child=next(c for c in rt.jobs.list() if c.kind=='video_safety_review')
    original=workflow._validate_challenge
    def interrupted(safety,challenge):
        original(safety,challenge)
        raise RuntimeError('Fixture finalization interrupted after validation')
    monkeypatch.setattr(workflow,'_validate_challenge',interrupted)
    first=await execute(rt,child);assert first.status=='failed'
    before=len(rt.audit.rows(1000,job_id=child.id,event_type='model_request'))
    from safety_app.agents.safety_reviewer.models import SafetyRequest
    starts=rt.audit.rows(1000,job_id=child.id,event_type='run_started')
    inputs=SafetyRequest.model_validate(next(e['arguments'] for e in starts if e['agent_name']=='safety_reviewer'))
    assert workflow._retained_finalization(rt,child,inputs)
    inputs.context+=' Changed context must not reuse a prior judgment.'
    assert workflow._retained_finalization(rt,child,inputs) is None
    monkeypatch.setattr(workflow,'_validate_challenge',original)
    rt.jobs.resume(child.id)
    def forbidden(_):pytest.fail('Validated stages must not be purchased again')
    second=await execute(rt,rt.jobs.get(child.id),forbidden)
    assert second.status=='completed',second.error
    assert len(rt.audit.rows(1000,job_id=child.id,event_type='model_request'))==before
    assert rt.audit.rows(event_type='validated_stages_reused',job_id=child.id)


def test_challenged_finding_withholds_verified_urgency_without_losing_review():
    from safety_app.workflows.event_review import _validate_challenge
    from safety_app.agents.safety_reviewer.models import SafetyResult,AgentFinding
    from safety_app.agents.challenge_reviewer.models import ChallengeResult
    finding=AgentFinding(id='f',review_id='review',condition='Possible visual hazard',evidence_refs=['frame'],uncertainty=['Unknown exposure'])
    safety=SafetyResult(findings=[finding],triage={'priority':'high','reason':'Proposed urgency','finding_ids':['f'],'evidence_refs':['frame']})
    challenge=ChallengeResult(checked_finding_ids=['f'],issues=[{'finding_id':'f','problem':'Exposure is uncertain','alternative':'Inspect exposure'}],triage_supported=True)
    _validate_challenge(safety,challenge)
    assert challenge.triage_supported is False and challenge.triage_explanation
    assert challenge.issues and safety.findings==[finding]
    challenge.checked_finding_ids=[]
    with pytest.raises(ValueError,match='every finding'):_validate_challenge(safety,challenge)

async def test_two_layers_reuse_pixels_delegate_and_store_reportable_assessments(rt):
    parent=job(rt,video(rt));finished=await execute(rt,parent);assert finished.status=='completed',finished.error
    before=overview(rt,parent.run_id,'local');assert before.screenings and len(before.cases)==1
    assert before.cases[0].status=='queued' and before.cases[0].report_condition=='immediate'
    assert before.cases[0].manager_attention[0].evidence_status=='provisional'
    children=await finish_children(rt);assert len(children)==1
    after=overview(rt,parent.run_id,'local');case=after.cases[0]
    assert case.assessment and case.status=='needs_review'
    assert case.assessment.findings[0].classification=='insufficient_evidence'
    assert case.assessment.specialist_assessments and case.assessment.challenge['checked_finding_ids']
    rows=rt.audit.rows(1000,job_id=children[0].id)
    assert not any(row['agent_name']=='video_observer' and row['event_type']=='model_request' for row in rows)
    assert any(row['event_type']=='layer1_evidence_reused' for row in rows)
    assert {row['agent_name'] for row in rows if row['event_type']=='model_request'}>={'safety_reviewer','specialist_applicability','specialist_emergency','challenge_reviewer'}
    assert all(row['parent_run_id']==parent.run_id for row in rows if row['event_type']=='model_request')
    assert all(row['configuration']['fake_provider'] for row in rows if row['event_type']=='model_request')
    assert all(attention['delivery_status']=='not_configured' for attention in rt.db.list('manager_attention'))
    counts=metrics(rt,'local');assert counts.screening_checks==0 and counts.model_requests==0
    demo=metrics(rt,'local',include_fixtures=True)
    assert demo.suspected_episodes==1 and demo.screening_checks==1 and demo.assessed_cases==1
    assert demo.estimated_cost_usd==0 and demo.unknown_cost_requests==0 and demo.model_requests>0
    assert demo.findings_by_classification=={'insufficient_evidence':1}
    assert not rt.budgets.status()['outstanding']
    assert overview(Runtime(rt.root),parent.run_id,'local').cases[0].assessment==case.assessment

async def test_escalation_cap_and_restart_recovery_do_not_repeat_paid_jobs(rt):
    rt.config['video_screening']['max_escalations']=1
    parent=job(rt,video(rt,30));result=await execute(rt,parent);assert result.status=='completed'
    views=overview(rt,parent.run_id,'local');assert len(views.cases)==3
    assert sum(case.status=='queued' for case in views.cases)==1
    assert sum(case.status=='deferred' for case in views.cases)==2
    child=next(child for child in rt.jobs.list() if child.kind=='video_safety_review')
    with rt.db.connect() as conn:conn.execute("DELETE FROM objects WHERE kind='safety_handoff'")
    dispatch_ready_cases(rt,parent.id)
    assert [j.id for j in rt.jobs.list() if j.kind=='video_safety_review']==[child.id]
    rt.config['video_screening']['max_escalations']=4
    dispatch_ready_cases(rt,parent.id)
    assert len([j for j in rt.jobs.list() if j.kind=='video_safety_review'])==1
    assert len(rt.db.list('manager_attention'))>=3

@pytest.mark.parametrize('result,visibility',[('clear','clear'),('unknown','not_assessable')])
async def test_clear_or_unknown_samples_never_dispatch_safety_team(rt,result,visibility):
    parent=job(rt,video(rt));normal=models()
    def factory(name):
        model=normal(name)
        if name!='video_observer':return model
        original=model.function
        def response(messages,info):
            value=original(messages,info)
            if 'observations' in info.output_tools[0].parameters_json_schema['properties']:
                for check in value.parts[0].args.get('screening_checks',[]):
                    check.update(result=result,visibility=visibility,needs_judgment=False,priority='routine')
            return value
        from pydantic_ai.models.function import FunctionModel
        return FunctionModel(response)
    finished=await execute(rt,parent,factory);assert finished.status=='completed',finished.error
    assert rt.db.list('safety_screening') and not rt.db.list('video_safety_case')
    assert not rt.db.list('manager_attention') and not any(child.kind=='video_safety_review' for child in rt.jobs.list())
    counts=metrics(rt,'local',include_fixtures=True)
    assert counts.unknown_checks==(1 if result=='unknown' else 0)
    assert counts.assessed_checks==(1 if result=='clear' else 0)

async def test_disabled_screening_and_child_cancellation_preserve_boundaries(rt):
    source=video(rt);parent=job(rt,source,safety_screening=False,escalate_to_safety_team=False)
    assert (await execute(rt,parent)).status=='completed'
    assert not rt.db.list('safety_screening') and not rt.db.list('video_safety_case')
    parent=job(rt,source,key='screen-without-judgment',escalate_to_safety_team=False)
    assert (await execute(rt,parent)).status=='completed'
    assert overview(rt,parent.run_id,'local').cases[0].status=='deferred'
    parent=job(rt,source,key='cancel-child')
    assert (await execute(rt,parent)).status=='completed'
    child=next(child for child in rt.jobs.list() if child.kind=='video_safety_review')
    rt.jobs.cancel(child.id);finished=await execute(rt,child)
    assert finished.status=='cancelled' and not rt.db.list('video_safety_assessment')
    assert overview(rt,parent.run_id,'local').cases[0].status=='cancelled'

async def test_foreign_or_modified_handoff_fails_before_model_dispatch(rt):
    parent=job(rt,video(rt));await execute(rt,parent)
    child=next(child for child in rt.jobs.list() if child.kind=='video_safety_review')
    altered=child.model_copy(update={'payload':child.payload|{'budget_workflow_id':'other-workflow'}})
    finished=await execute(rt,altered,lambda name:pytest.fail('No model for a foreign handoff'))
    assert finished.status=='failed' and 'scope mismatch' in finished.error.message
    assert not rt.db.list('video_safety_assessment')

async def test_reporting_deduplicates_repeated_source_checks_and_is_site_scoped(rt):
    source=video(rt)
    for index in range(2):
        parent=job(rt,source,key=str(index));assert (await execute(rt,parent)).status=='completed'
    await finish_children(rt)
    counts=metrics(rt,'local',include_fixtures=True)
    assert counts.suspected_episodes==1 and counts.sampled_frame_count==1 and counts.assessed_cases==1
    assert metrics(rt,'foreign',include_fixtures=True).screening_checks==0
    assert metrics(rt,'local',since='2099-01-01T00:00:00Z',include_fixtures=True).screening_checks==0
    with pytest.raises(ValueError):metrics(rt,'local',since='2026-01-01')
    with pytest.raises(ValueError):metrics(rt,'local',since='2026-02-01T00:00:00Z',until='2026-01-01T00:00:00Z')

async def test_screening_frame_token_and_reasoning_allowances_are_reduced(rt):
    original=copy.deepcopy(rt.config);parent=job(rt,video(rt));await execute(rt,parent)
    rows=rt.audit.rows(1000,job_id=parent.id)
    visual=[row for row in rows if row['event_type']=='model_request' and row['configuration'].get('agent_mode')=='recorded_video_observer']
    assert visual and all(row['resources']['frames']<=2 for row in visual)
    assert all(row['resources']['output_token_cap']<=1200 for row in visual)
    assert all(row['configuration']['model_settings']['openai_reasoning_effort']=='low' for row in visual)
    assert rt.config==original and rt.config['pricing']['gpt-6-luna']['image_usd'] is None

def test_ppe_visibility_scope_and_crop_allowance(rt):
    mid='media';rt.db.save('media',MediaRef(id=mid,media_id=mid,source_id='synthetic',camera_id='camera-1',start_ms=0,end_ms=1000,content_hash='h',storage_key='fixture'))
    ok,raw=cv2.imencode('.jpg',np.zeros((256,256,3),np.uint8));assert ok
    frame=rt.media.add_frame(mid,'1',500,raw.tobytes())
    parent=rt.jobs.start('video_analysis','tool-scope',{'media_ids':[mid],'start_ms':0,'end_ms':1000})
    deps=dependencies(rt,parent,'video_observer');deps.detail_frame_ids={frame.id};deps.detail_limit=1;deps.safety_guide={'required_ppe':['hard hat']}
    ctx=SimpleNamespace(deps=deps)
    assert get_safety_checklist(ctx)['required_ppe']==['hard hat']
    with pytest.raises(ValueError):inspect_safety_detail(ctx,'foreign',(.1,.1,.5,.5))
    crop=inspect_safety_detail(ctx,frame.id,(0,0,1,1));assert isinstance(crop,ToolReturn) and crop.return_value['parent_frame_id']==frame.id
    with pytest.raises(ValueError,match='allowance'):inspect_safety_detail(ctx,frame.id,(0,0,1,1))
    check=VisualSafetyCheck(category='ppe',result='suspected',visibility='clear',condition='PPE not visible in fixture.',start_ms=500,end_ms=500,evidence_refs=[frame.id],ppe_item='hard hat',ppe_state='not_visible',person_box=(0,0,.5,.2))
    output=VideoObservationResult(summary='Fixture',screening_checks=[check])
    with pytest.raises(ValueError,match='too small'):validate_screening(output,[frame],[Interval(start_ms=0,end_ms=1000)],True,[],rt.media)
    check.person_box=(0,0,1,1);validate_screening(output,[frame],[Interval(start_ms=0,end_ms=1000)],True,[],rt.media)
    check.media_id=mid;validate_screening(output,[frame],[Interval(start_ms=0,end_ms=1000)],True,[],rt.media)
    check.media_id='another-recording'
    with pytest.raises(ValueError,match='recording does not match'):
        validate_screening(output,[frame],[Interval(start_ms=0,end_ms=1000)],True,[],rt.media)
    check.media_id=None
    assert check.limitations and 'no supplied site requirement' in check.limitations[-1]
    with pytest.raises(ValueError,match='not assigned'):validate_screening(output,[frame],[Interval(start_ms=0,end_ms=1000)],False,[],rt.media)
    with pytest.raises(ValueError):VisualSafetyCheck(category='slip_trip',result='clear',visibility='not_assessable',condition='Unknown',start_ms=0,end_ms=0,evidence_refs=['x'])

async def test_api_combines_only_linked_pipeline_audit_and_blocks_unfinished_download(rt,monkeypatch):
    from fastapi.testclient import TestClient
    from safety_app.api.main import app
    parent=job(rt,video(rt));await execute(rt,parent)
    child=next(child for child in rt.jobs.list() if child.kind=='video_safety_review')
    monkeypatch.setattr('safety_app.infrastructure.runtime._default',rt);client=TestClient(app)
    assert client.get('/api/video-analysis/safety/metrics').status_code==401
    assert client.post('/api/auth/session',json={}).status_code==200
    endpoint=f'/api/video-analysis/jobs/{parent.id}'
    assert client.get(endpoint+'/safety').json()['cases'][0]['status']=='queued'
    assert client.get(endpoint+'/audit/download?include_safety=true').status_code==400
    await finish_children(rt)
    unrelated=rt.jobs.start('video_analysis','unrelated',{'source_id':'unrelated'})
    rt.audit.write('model_request',job_id=unrelated.id,run_id=unrelated.run_id,resources={'model_request_id':'other'})
    rows=client.get(endpoint+'/audit?include_safety=true&limit=1000').json()
    assert {row['job_id'] for row in rows}<={parent.id,child.id}
    assert child.id in {row['job_id'] for row in rows}
    exported=client.get(endpoint+'/audit/download?include_safety=true');assert exported.status_code==200
    assert client.get('/api/video-analysis/safety/metrics').json()['suspected_episodes']==0
    assert client.get('/api/video-analysis/safety/metrics?include_fixtures=true').json()['suspected_episodes']==1

@pytest.mark.parametrize('classification,supported,expected',[('general_hazard',True,'completed'),('insufficient_evidence',True,'failed'),('general_hazard',False,'completed')])
async def test_specialist_manager_priority_requires_evidence_and_independent_support(rt,classification,supported,expected):
    from safety_app.workflows.fixtures import team_fixture_models
    parent=job(rt,video(rt));await execute(rt,parent)
    child=next(child for child in rt.jobs.list() if child.kind=='video_safety_review')
    signal=rt.db.get('safety_screening',child.payload['screening_ids'][0]);ref=signal['check']['evidence_refs'][0]
    finding_id='specific-finding'
    outputs={'safety_reviewer':{'findings':[{'id':finding_id,'event_id':child.payload['case_id'],'review_id':child.run_id,
        'conclusion':'Fictional exposed route needs inspection.','condition':'Fictional blocked access.',
        'evidence_refs':[ref],'uncertainty':['Fixture only; requirements unknown.']}],
        'triage':{'priority':'high','reason':'Fictional immediate exposed danger.','finding_ids':[finding_id],'evidence_refs':[ref]}},
        'challenge_reviewer':{'checked_finding_ids':[finding_id],'issues':[],'triage_supported':supported,'triage_explanation':'Fixture urgency check.'}}
    factory=team_fixture_models(outputs,specialist='emergency',classification=classification)
    result=await execute(rt,child,factory);assert result.status==expected,result.error
    reviewed=[row for row in rt.db.list('manager_attention') if row['evidence_status']=='specialist_supported']
    assert bool(reviewed)==(expected=='completed' and supported)
    if expected=='failed':assert not rt.db.list('video_safety_assessment')
    else:
        snapshot=rt.db.get('video_safety_assessment',child.payload['case_id'])
        assert snapshot['triage_supported']==supported
        assert snapshot['findings'][0]['status']=='reviewed'

async def test_metrics_retain_unknown_charges_and_stop_cancels_specialist_jobs(rt,monkeypatch):
    from fastapi.testclient import TestClient
    from safety_app.api.main import app
    parent=job(rt,video(rt));await execute(rt,parent)
    child=next(child for child in rt.jobs.list() if child.kind=='video_safety_review')
    rt.audit.write('model_request',job_id=child.id,run_id='mock-charge',configuration={'fake_provider':False},resources={'model_request_id':'unknown','reserved_usd':.002})
    rt.audit.write('model_response',status='failed',job_id=child.id,run_id='mock-charge',resources={'model_request_id':'unknown','input_tokens':None,'output_tokens':None,'cost_usd':None})
    counts=metrics(rt,'local',include_fixtures=True)
    assert counts.unknown_cost_requests==1 and counts.unresolved_reserved_usd==.002
    monkeypatch.setattr('safety_app.infrastructure.runtime._default',rt);client=TestClient(app)
    assert client.post('/api/auth/session',json={}).status_code==200
    assert client.post('/api/budgets/stop',json={'stopped':True}).status_code==200
    assert rt.jobs.get(child.id).status=='cancelled'
    assert dispatch_ready_cases(rt,parent.id)==[]

async def test_recording_clock_is_retained_but_recency_is_unverified(rt):
    from datetime import datetime,timezone
    parent=job(rt,video(rt),recording_start_utc=datetime(2020,1,1,tzinfo=timezone.utc))
    assert (await execute(rt,parent)).status=='completed'
    case=overview(rt,parent.run_id,'local').cases[0]
    assert case.case.event_time_utc=='2020-01-01T00:00:00+00:00'
    assert case.manager_attention[0].event_recency=='unverified'
    assert case.manager_attention[0].event_time_utc.startswith('2020-01-01')
    assert case.review_job.payload['event_date']=='2020-01-01'
    with pytest.raises(ValueError,match='timezone'):
        VideoAnalysisRequest(source_id='s',question='Q',idempotency_key='k',recording_start_utc=datetime(2020,1,1))

def test_high_priority_judgment_is_claimed_before_older_routine_work(rt):
    routine=rt.jobs.start('video_safety_review','routine',{'priority':'routine'})
    high=rt.jobs.start('video_safety_review','high',{'priority':'high'})
    assert rt.jobs.claim(['video_safety_review']).id==high.id
    assert rt.jobs.claim(['video_safety_review']).id==routine.id

async def test_real_screening_tool_dialogue_keeps_history_to_three_images(rt):
    import json
    from pydantic_ai.messages import ModelResponse,ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    ordinary=models()
    def factory(name):
        wrapped=ordinary(name)
        if name!='video_observer':return wrapped
        def respond(messages,info):
            visual='observations' in info.output_tools[0].parameters_json_schema['properties']
            cropped=any(part.part_kind=='tool-return' and part.tool_name=='inspect_safety_detail' for message in messages for part in message.parts)
            if visual and not cropped:
                contents=[part.content for message in messages for part in message.parts if part.part_kind=='user-prompt'][0]
                frame=next(json.loads(item) for item in contents if isinstance(item,str) and '"timestamp_ms"' in item and '"content_hash"' in item)
                assert 'Coordinator screening checklist' in info.instructions
                return ModelResponse(parts=[ToolCallPart('inspect_safety_detail',{'frame_id':frame['id'],'box':[0,0,1,1]},tool_call_id='detail')])
            return wrapped.function(messages,info)
        return FunctionModel(respond)
    parent=job(rt,video(rt));finished=await execute(rt,parent,factory)
    assert finished.status=='completed',finished.error
    rows=rt.audit.rows(1000,job_id=parent.id)
    assert 'inspect_safety_detail' in {row['tool_name'] for row in rows if row['event_type']=='tool_finished'}
    assert not any(row['tool_name']=='get_safety_checklist' for row in rows)
    visual=[row for row in rows if row['event_type']=='model_request' and row['configuration'].get('agent_mode')=='recorded_video_observer']
    assert max(row['resources']['frames'] for row in visual)==3

async def test_screening_responses_wire_schemas_type_every_array_and_execute_crop(rt,monkeypatch):
    """Actual PydanticAI/SDK serialization, entirely on an in-memory HTTP transport."""
    import json,httpx
    from openai import AsyncOpenAI
    from pydantic_ai import BinaryContent,models as ai_models
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from safety_app.agents.video_observer.video_agent import observer
    from safety_app.workflows.video_analysis import scoped_runtime
    monkeypatch.setattr(ai_models,'ALLOW_MODEL_REQUESTS',True)
    rt=scoped_runtime(rt)
    rt.db.save('media',MediaRef(id='m',media_id='m',source_id='synthetic',camera_id='camera-1',start_ms=0,end_ms=1000,content_hash='synthetic',storage_key='fixture'))
    ok,raw=cv2.imencode('.jpg',np.zeros((256,256,3),np.uint8));assert ok
    frame=rt.media.add_frame('m','1',500,raw.tobytes())
    parent=rt.jobs.start('video_analysis','wire-schema',{'media_ids':['m'],'start_ms':0,'end_ms':1000})
    deps=dependencies(rt,parent,'video_observer');deps.workflow_kind='video_analysis'
    deps.detail_frame_ids={frame.id};deps.detail_limit=1;deps.safety_guide={'required_ppe':[]};deps.seen_frames.add(frame.id)
    captured=[];counted=[]
    def assert_arrays(node):
        if isinstance(node,dict):
            if node.get('type')=='array':assert isinstance(node.get('items'),dict) and node['items'],node
            assert 'prefixItems' not in node,node
            for value in node.values():assert_arrays(value)
        elif isinstance(node,list):
            for value in node:assert_arrays(value)
    def handler(request):
        assert request.url.host=='mock.invalid'
        body=json.loads(request.content)
        if request.url.path.endswith('/input_tokens'):
            counted.append(body)
            return httpx.Response(200,json={'object':'response.input_tokens','input_tokens':10})
        captured.append(body)
        for tool in body['tools']:assert_arrays(tool['parameters'])
        crop=next(tool for tool in body['tools'] if tool['name']=='inspect_safety_detail')
        box=crop['parameters']['properties']['box']
        assert box['type']=='array' and box['items']['type']=='number'
        if len(captured)==1:
            name='inspect_safety_detail';args={'frame_id':frame.id,'box':[0,0,1,1]}
        else:
            name=next(tool['name'] for tool in body['tools'] if tool['name'].startswith('final_result'))
            args={'summary':'Wire fixture completed.','observations':[],'screening_checks':[],'limitations':['Fixture only.']}
        return httpx.Response(200,json={'id':f'resp_{len(captured)}','object':'response','created_at':0,
            'status':'completed','model':'gpt-6-luna','output':[{'type':'function_call','id':f'fc_{len(captured)}',
            'call_id':f'call_{len(captured)}','name':name,'arguments':json.dumps(args),'status':'completed'}],
            'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=AsyncOpenAI(api_key='fixture-not-real',base_url='https://mock.invalid/v1',http_client=http,max_retries=0)
        model=OpenAIResponsesModel('gpt-6-luna',provider=OpenAIProvider(openai_client=client))
        result=await observer(model,deps).run(['Offline wire test',frame.model_dump_json(),BinaryContent(data=rt.media.model_image(frame.id),media_type='image/jpeg')],deps=deps)
    assert result.output.summary=='Wire fixture completed.' and len(captured)==2 and len(counted)==2 and deps.detail_calls==1
    assert any(row['tool_name']=='inspect_safety_detail' and row['status']=='ok' for row in rt.audit.rows(event_type='tool_finished'))
    assert not rt.budgets.status()['outstanding']

@pytest.mark.parametrize('box',[[],[0,0,1],[0,0,1,1,0]])
def test_normalized_box_keeps_exact_four_coordinate_validation(box):
    from pydantic import TypeAdapter,ValidationError
    from safety_app.domain.video_safety import NormalizedBox
    with pytest.raises(ValidationError):TypeAdapter(NormalizedBox).validate_python(box)


def test_omitted_routine_categories_become_marked_unknowns_not_rejections():
    from safety_app.workflows.full_review import fill_omitted_categories
    from safety_app.domain.video_safety import ROUTINE_CATEGORIES
    frames=[SimpleNamespace(id=f'frame-{t}',timestamp_ms=t) for t in (2600,200,1400)]
    observed=VisualSafetyCheck(category='fire_burn',result='suspected',visibility='clear',needs_judgment=True,
        condition='Flame flares beside the worker.',evidence_refs=['frame-200','frame-1400'],start_ms=200,end_ms=1400,
        temporal_basis='sequence_event',coordinator_filled=True)
    ppe=VisualSafetyCheck(category='ppe',result='unknown',visibility='partial',condition='Head occluded.',
        evidence_refs=['frame-200'],start_ms=200,end_ms=200,ppe_state='not_assessable')
    result=SimpleNamespace(screening_checks=[observed,ppe])
    fill_omitted_categories(result,frames,Interval(start_ms=0,end_ms=10000))
    filled=[c for c in result.screening_checks if c.coordinator_filled]
    # The model cannot claim a Python fill; its own checks stay unmarked.
    assert not observed.coordinator_filled and not ppe.coordinator_filled
    assert sorted(c.category for c in filled)==sorted(set(ROUTINE_CATEGORIES)-{'ppe'})
    for check in filled:
        assert check.result=='unknown' and check.visibility=='not_assessable' and not check.needs_judgment
        assert check.evidence_refs==['frame-200','frame-1400','frame-2600']
        assert (check.start_ms,check.end_ms)==(200,2600)
        assert 'not a clear result' in check.limitations[0]


def test_fire_burn_routes_to_fire_prevention_specialist():
    from safety_app.modules.safety_review.video_cases import DOMAINS
    from safety_app.modules.safety_review.catalog import PROFILES
    assert DOMAINS['fire_burn']=='emergency' and 'Fire' in PROFILES['emergency']
