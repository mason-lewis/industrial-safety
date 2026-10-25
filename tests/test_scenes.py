import asyncio
import json
import numpy as np
import pytest
from pydantic_ai import BinaryContent
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.messages import ModelResponse,ToolCallPart
from app.agents.scenes import make_packet,analyze,EventMemory,ctx,estimate_cost
from app.agents.service import ReviewService
from app.agents.settings import ModelConfig
from app.evidence.store import EvidenceStore
from app.storage.repository import Repository
from app.models.contracts import FramePacket,LabConfig,SceneReview,SceneResult,SceneFinding

CTX=dict(source_id='clip',camera_id='cam',session_id='s',run_generation=0,schema_version='1.0')


def setup(tmp_path,mode='ai_scene'):
    store=EvidenceStore(tmp_path/'evidence');repo=Repository(tmp_path/'records.sqlite');config=LabConfig(analysis_mode=mode,person_focused=False)
    frames=[]
    for i in range(21):
        f=FramePacket(**CTX,frame_id=i,timestamp_ms=i*1000,width=10,height=10,evidence_cutoff_ms=i*1000)
        store.add(f,np.full((10,10,3),i,np.uint8));frames.append(f)
    return store,repo,config,frames


def no_finding():return dict(findings=[],no_finding=True,summary='No supported finding',limitations=[])


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ai_scene','ai_cv'])
async def test_modes_receive_binary_images_cv_only_in_enriched_prompt(tmp_path,mode):
    store,repo,config,frames=setup(tmp_path,mode)
    packet=make_packet(store,repo,frames[15],config)
    packet.cv_metadata['sentinel']='ONLY_CV_TEST'
    seen=[]
    def model(messages,info):
        contents=[p.content for m in messages for p in m.parts if hasattr(p,'content')]
        seen.extend(contents)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,no_finding())])
    review=SceneReview(**CTX,packet=packet,analysis_mode=mode)
    await analyze(store,review,ModelConfig(),lambda _:FunctionModel(model))
    assert review.status=='reviewed',review.error
    flat=[v for c in seen if isinstance(c,list) for v in c]
    assert len([v for v in flat if isinstance(v,BinaryContent)])==15
    assert ('ONLY_CV_TEST' in str(seen))==(mode=='ai_cv')
    assert len(review.received_images)==15
    assert [r['ref']['timestamp_ms'] for r in review.received_images]==sorted(r.timestamp_ms for r in packet.refs)
    assert max(r['ref']['timestamp_ms'] for r in review.received_images)<=15000


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['future','session','metadata','citation'])
async def test_invalid_evidence_and_findings_rejected(tmp_path,bad):
    store,repo,config,frames=setup(tmp_path);packet=make_packet(store,repo,frames[5],config)
    if bad=='future': packet.refs[0].timestamp_ms=6000
    elif bad=='session': packet.refs[0].session_id='other'
    elif bad=='metadata': packet.refs[0].camera_id='other'
    output=no_finding()
    if bad=='citation':
        output={'no_finding':False,'summary':'Invalid','findings':[dict(category='hazard',subject='object',status='new',observed_condition='x',inferred_risk='x',first_ms=0,last_ms=1,evidence_ids=['invented'],severity='low',human_review_reason='x')]}
    review=SceneReview(**CTX,packet=packet,analysis_mode='ai_scene')
    await analyze(store,review,ModelConfig(),lambda _:TestModel(call_tools=[],custom_output_args=output))
    assert review.status=='error' and review.result is None


def result(packet,status='new',prior=None):
    refs=packet.refs
    return SceneResult(no_finding=False,summary='Fixture',findings=[SceneFinding(prior_event_id=prior,category='obstructed_route',subject='box by doorway',status=status,observed_condition='Visible box',inferred_risk='Possible obstruction',first_ms=refs[0].timestamp_ms,last_ms=refs[-1].timestamp_ms,evidence_ids=[r.id for r in refs],severity='low',human_review_reason='Check route')])


def test_memory_updates_one_event_preserves_original_no_longer_visible_not_resolved(tmp_path):
    store,repo,config,frames=setup(tmp_path);memory=EventMemory(repo)
    first=make_packet(store,repo,frames[15],config);review=SceneReview(**CTX,packet=first,analysis_mode='ai_scene',result=result(first))
    updates=memory.apply(review);id=updates[0]['event_id'];original=updates[0]['update']
    second=make_packet(store,repo,frames[20],config);later=SceneReview(**CTX,packet=second,analysis_mode='ai_scene',result=result(second,'continuing',id))
    updates=memory.apply(later)
    assert len(memory.events)==1 and updates[0]['event_id']==id
    assert updates[0]['previous']==original and original['last_ms']==15000
    later.result=result(second,'no_longer_visible',id);memory.apply(later)
    assert memory.events[id]['state']=='no_longer_visible'
    assert memory.history(first)==[]  # Newer conclusions do not leak into an older cutoff.


@pytest.mark.asyncio
async def test_scheduler_disabled_no_cv_dependency_newest_slot_and_late_rejection(tmp_path,monkeypatch):
    store,repo,config,frames=setup(tmp_path)
    generation={'value':0};started=asyncio.Event();release=asyncio.Event();calls=0
    async def slow(messages,info):
        nonlocal calls
        calls+=1;started.set();await release.wait()
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,no_finding())])
    monkeypatch.setenv('AI_ENABLED','true')
    service=ReviewService(store,repo,lambda:config,lambda s,g:g==generation['value'],lambda _:FunctionModel(slow))
    service.scenes.submit(frames[0]);assert calls==0 and not service.scenes.pending
    service.set_enabled(True,True);assert service.scenes.pending is None
    service.scenes.submit(frames[0]);await asyncio.wait_for(started.wait(),1)
    service.scenes.submit(frames[5]);service.scenes.submit(frames[10]);service.scenes.submit(frames[15])
    assert service.scenes.pending.packet.cutoff_ms==15000 and calls==1
    assert service.scenes.skipped==2
    generation['value']=1;release.set();await asyncio.wait_for(service.scenes.task,2)
    assert not any(r['status']=='reviewed' for r in repo.list('scene_review'))
    await service.close()


@pytest.mark.asyncio
async def test_off_cancels_and_reenable_processes_new_frame_without_backlog(tmp_path):
    store,repo,config,frames=setup(tmp_path);started=asyncio.Event()
    async def slow(messages,info):started.set();await asyncio.sleep(5)
    service=ReviewService(store,repo,lambda:config,lambda *args:True,lambda _:FunctionModel(slow));service.set_enabled(True,True)
    service.scenes.submit(frames[0]);await asyncio.wait_for(started.wait(),1)
    service.scenes.submit(frames[5]);service.set_enabled(False,True)
    await asyncio.wait_for(service.scenes.task,1)
    assert service.scenes.pending is None
    assert all(r['status']=='cancelled' for r in repo.list('scene_review'))
    service.model_factory=lambda _:TestModel(call_tools=[],custom_output_args=no_finding())
    service.set_enabled(True,True);service.scenes.submit(frames[10]);await asyncio.wait_for(service.scenes.task,1)
    assert repo.list('scene_review')[-1]['status']=='reviewed'
    await service.close()


def test_source_time_sampling_gaps_and_cost_no_double_reasoning(tmp_path):
    store,repo,config,frames=setup(tmp_path)
    packet=make_packet(store,repo,frames[20],config)
    assert packet.start_ms==5000 and len(packet.refs)==15
    for id,(ref,_) in list(store.records.items()):
        if 7000<ref.timestamp_ms<14000:store.records.pop(id)
    packet=make_packet(store,repo,frames[20],config)
    assert packet.coverage['missing_ranges_ms'] and packet.coverage['missing_targets_ms']
    assert estimate_cost({'input_tokens':100,'cached_input_tokens':20,'output_tokens':50,'reasoning_tokens':40},{'input':1,'cached_input':.1,'output':2})==pytest.approx(.000182)
    assert estimate_cost({},None) is None


@pytest.mark.asyncio
async def test_run_budget_exhaustion_is_visible(tmp_path,monkeypatch):
    store,repo,config,frames=setup(tmp_path);monkeypatch.setenv('AI_RUN_REQUEST_LIMIT','1')
    service=ReviewService(store,repo,lambda:config,lambda *args:True,lambda _:TestModel(call_tools=[],custom_output_args=no_finding()));service.set_enabled(True,True)
    service.scenes.submit(frames[0]);await service.scenes.task
    service.scenes.submit(frames[5]);await service.scenes.task
    assert repo.list('scene_review')[-1]['status']=='blocked_request'
    assert service.scenes.state()['requests']==1
    await service.close()


@pytest.mark.asyncio
async def test_pipeline_cv_failure_or_slow_cv_does_not_stop_ai_playback(tmp_path,monkeypatch):
    from test_pipeline import pipeline
    lab=pipeline(tmp_path,monkeypatch);lab.config.analysis_mode='ai_cv';lab.config.person_focused=False
    lab.review=ReviewService(lab.evidence,lab.repository,lambda:lab.config,lab.current,lambda _:TestModel(call_tools=[],custom_output_args=no_finding()))
    lab.review.set_enabled(True,True)
    def broken(*args):raise RuntimeError('CV unavailable')
    lab.detector.predict=broken
    for _ in range(3):await lab.process_one()
    await lab.cv_task
    await lab.review.scenes.task
    assert lab.last_timestamp==1000 and len(lab.evidence.records)==3
    assert lab.repository.list('scene_review')[0]['status']=='reviewed'
    assert lab.metrics.errors>0
    await lab.review.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ai_scene','ai_cv'])
async def test_scene_responses_serialization_images_default_reasoning_output_cap(tmp_path,monkeypatch,mode):
    import httpx
    from openai import AsyncOpenAI
    from pydantic_ai import models
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.models.openai import OpenAIResponsesModel
    monkeypatch.setattr(models,'ALLOW_MODEL_REQUESTS',True)
    store,repo,config,frames=setup(tmp_path,mode);packet=make_packet(store,repo,frames[5],config)
    captured=[]
    def handler(request):
        assert request.url.host=='mock.invalid'
        body=json.loads(request.content);captured.append(body)
        tool=next(t['name'] for t in body['tools'] if t['name'].startswith('final_result'))
        return httpx.Response(200,json={'id':'r','object':'response','created_at':0,'status':'completed','model':'gpt-6-luna',
            'output':[{'type':'function_call','id':'fc','call_id':'c','name':tool,'arguments':json.dumps(no_finding()),'status':'completed'}],
            'usage':{'input_tokens':100,'output_tokens':20,'total_tokens':120,'input_tokens_details':{'cached_tokens':30}}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=AsyncOpenAI(api_key='fake',base_url='https://mock.invalid/v1',http_client=http)
        provider=OpenAIProvider(openai_client=client)
        review=SceneReview(**CTX,packet=packet,analysis_mode=mode)
        await analyze(store,review,ModelConfig(),lambda _:OpenAIResponsesModel('gpt-6-luna',provider=provider))
    assert review.status=='reviewed',review.error
    images=[p for message in captured[0]['input'] if message.get('role')=='user' for p in message['content'] if p['type']=='input_image']
    assert len(images)==6 and all(p['image_url'].startswith('data:image/jpeg;base64,/9j/') for p in images)
    assert 'effort' not in captured[0].get('reasoning',{})
    assert captured[0]['max_output_tokens']==3000
    assert 'service_tier' not in captured[0]
    assert review.usage['cached_input_tokens']==30


@pytest.mark.asyncio
async def test_slow_cv_native_worker_keeps_frame_processing_responsive(tmp_path,monkeypatch):
    import threading
    from test_pipeline import pipeline
    lab=pipeline(tmp_path,monkeypatch);lab.config.analysis_mode='ai_scene'
    started=threading.Event();release=threading.Event();original=lab.detector.predict
    def slow(image,confidence):started.set();release.wait(2);return original(image,confidence)
    lab.detector.predict=slow
    await lab.process_one();await asyncio.to_thread(started.wait,1)
    await asyncio.wait_for(lab.process_one(),.5)
    assert lab.last_timestamp==137 and not lab.cv_task.done()
    release.set();await lab.cv_task


@pytest.mark.asyncio
async def test_scene_timeout_and_retry_ceiling_are_reported_without_losing_cv(tmp_path,monkeypatch):
    store,repo,config,frames=setup(tmp_path);monkeypatch.setenv('AI_LIVE_DEADLINE_S','.01')
    async def timeout(messages,info):await asyncio.sleep(1)
    review=SceneReview(**CTX,packet=make_packet(store,repo,frames[5],config),analysis_mode='ai_scene')
    await analyze(store,review,ModelConfig(),lambda _:FunctionModel(timeout))
    assert review.status=='timeout' and review.usage['requests']==1
    assert review.usage['usage_unknown_requests']==1 and review.cost_estimate is None

@pytest.mark.asyncio
async def test_live_cache_export_preserves_images_cutoffs_cv_and_extra_pool(tmp_path):
    import zipfile
    from io import BytesIO
    from app.evals.packets import export_cache
    store,repo,config,frames=setup(tmp_path);config.scene_extra_images=2;config.scene_max_images=3
    packet=make_packet(store,repo,frames[15],config);repo.save('scene_packet',packet)
    raw=export_cache(store,repo,CTX,ModelConfig())
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        assert manifest['packets']==[packet.model_dump(mode='json')]
        assert len(manifest['images_sha256'])==len(packet.refs)+len(packet.extra_refs)
        assert all(r['timestamp_ms']<=15000 for r in manifest['packets'][0]['extra_refs'])
        for id in manifest['images_sha256']:assert archive.read('evidence/'+id+'.jpg')==store.image(id)


def test_derived_inspection_images_have_a_retention_quota(tmp_path):
    store=EvidenceStore(tmp_path/'e',max_bytes=10)
    store.save_derived('one-crop-0',b'12345678');store.save_derived('two-crop-0',b'abcdefgh')
    assert not (store.root/'one-crop-0.jpg').exists()
    assert (store.root/'two-crop-0.jpg').exists()

@pytest.mark.asyncio
@pytest.mark.parametrize('crop',[False,True])
async def test_bounded_extra_requests_deliver_recorded_images_and_labeled_crops(tmp_path,crop):
    store,repo,config,frames=setup(tmp_path);config.scene_max_images=2;config.scene_extra_images=1
    packet=make_packet(store,repo,frames[5],config);calls=0
    ref=packet.refs[0] if crop else packet.extra_refs[0]
    def model(messages,info):
        nonlocal calls
        calls+=1
        if calls==1:
            return ModelResponse(parts=[ToolCallPart('request_extra',{'evidence_id':ref.id,'box':[0,0,.5,.5] if crop else None})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,no_finding())])
    review=SceneReview(**CTX,packet=packet,analysis_mode='ai_scene')
    await analyze(store,review,ModelConfig(),lambda _:FunctionModel(model))
    assert review.status=='reviewed',review.error
    assert len(review.received_images)==3 and calls==2
    extra=review.received_images[-1]
    assert extra['ref']['id']==ref.id
    if crop:
        assert extra['kind']=='derived_crop' and list(extra['box'])==[0,0,.5,.5]
        assert (store.root/(extra['derived_id']+'.jpg')).is_file()
    else: assert extra['kind']=='original'


@pytest.mark.asyncio
async def test_triggered_review_preserves_rule_context_and_candidate_lifecycle(tmp_path):
    from app.models.contracts import RuleConfig,RuleCandidate
    store,repo,config,frames=setup(tmp_path,'ai_cv')
    config.rules=[RuleConfig(id='door',label='Storeroom doorway',kind='line_crossing',line=[(.2,.5),(.8,.5)],review_instruction='Check visible entry hazards')]
    candidate=RuleCandidate(**CTX,id='entry',kind='line_crossing',rule_id='door',timestamp_ms=5000,start_ms=5000,evidence_cutoff_ms=5000,state='closed')
    repo.save('candidate',candidate);captured=[]
    def model(messages,info):
        captured.extend(p.content for m in messages for p in m.parts if hasattr(p,'content'))
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,no_finding())])
    service=ReviewService(store,repo,lambda:config,lambda *args:True,lambda _:FunctionModel(model))
    service.scenes.submit(frames[5],'cv_trigger',triggered_candidate_ids=['entry'])
    assert service.scenes.pending is None and repo.get('candidate','entry')['ai_status']=='disabled'
    service.set_enabled(True,True)
    service.scenes.submit(frames[5],'cv_trigger',triggered_candidate_ids=['entry'])
    assert repo.get('candidate','entry')['ai_status']=='queued'
    await service.scenes.task
    review=repo.list('scene_review')[-1]
    assert review['status']=='reviewed' and review['packet']['triggered_candidate_ids']==['entry']
    assert review['packet']['cv_metadata']['rules'][0]['label']=='Storeroom doorway'
    assert 'Check visible entry hazards' in str(captured)
    assert repo.get('candidate','entry')['ai_status']=='reviewed'
    await service.close()


def test_rule_review_coalescing_and_cancellation_report_candidate_status(tmp_path):
    from app.models.contracts import RuleCandidate
    store,repo,config,frames=setup(tmp_path,'ai_cv')
    service=ReviewService(store,repo,lambda:config,lambda *args:True,lambda _:TestModel())
    service.set_enabled(True,True);service.scenes._kick=lambda:None
    for id,ms in [('old',0),('a',5000),('b',5000),('late',5000)]:
        repo.save('candidate',RuleCandidate(**CTX,id=id,kind='line_crossing',timestamp_ms=ms,start_ms=ms,evidence_cutoff_ms=ms))
    service.scenes.submit(frames[0],'cv_trigger',triggered_candidate_ids=['old'])
    service.scenes.submit(frames[5],'cv_trigger',triggered_candidate_ids=['a'])
    assert repo.get('candidate','old')['ai_status']=='skipped'
    service.scenes.submit(frames[5],'cv_trigger',triggered_candidate_ids=['b'])
    assert service.scenes.pending.packet.triggered_candidate_ids==['a','b']
    assert all(repo.get('candidate',id)['ai_status']=='queued' for id in ['a','b'])
    service.scenes.cancel()
    assert all(repo.get('candidate',id)['ai_status']=='skipped' for id in ['a','b'])
    service.scenes.last_started_cutoff=5000
    service.scenes.submit(frames[5],'cv_trigger',triggered_candidate_ids=['late'])
    assert repo.get('candidate','late')['ai_status']=='skipped'


def test_cv_packet_keeps_subtype_groups_and_filter_policy(tmp_path):
    from app.models.contracts import Detection,TrackObservation
    store,repo,config,frames=setup(tmp_path,'ai_cv')
    frame=frames[15];frame.cv_status='available'
    frame.detections=[Detection(label='truck',groups=['vehicles'],score=.9,box=(.1,.1,.2,.2))]
    frame.tracks=[TrackObservation(**CTX,track_id=7,label='truck',groups=['vehicles'],score=.9,box=(.1,.1,.2,.2),timestamp_ms=15000,frame_id=15)]
    packet=make_packet(store,repo,frame,config)
    row=next(r for r in packet.cv_metadata['frames'] if r['timestamp_ms']==15000)
    assert row['detections'][0]['label']=='truck' and row['tracks'][0]['groups']==['vehicles']
    assert packet.cv_metadata['class_policy']['groups']['vehicles']==config.class_groups['vehicles']
    assert 'pizza' not in packet.cv_metadata['class_policy']['enabled_labels']


@pytest.mark.asyncio
async def test_live_periods_never_reuse_previous_frames_including_triggers_and_extras(tmp_path):
    store,repo,config,frames=setup(tmp_path)
    config.scene_review_interval_s=1;config.scene_extra_images=2
    service=ReviewService(store,repo,lambda:config,lambda *args:True,lambda _:TestModel(call_tools=[],custom_output_args=no_finding()))
    service.set_enabled(True,True)
    for i,trigger in [(5,'scheduled'),(6,'cv_trigger'),(10,'scheduled'),(15,'cv_trigger')]:
        service.scenes.submit(frames[i],trigger)
        await service.scenes.task
    reviews=repo.list('scene_review')
    assert len(reviews)==4 and all(r['status']=='reviewed' for r in reviews)
    for prior,current in zip(reviews,reviews[1:]):
        cutoff=prior['packet']['cutoff_ms'];packet=current['packet']
        assert packet['start_ms']==cutoff+1
        assert packet['coverage']['previous_review_cutoff_ms']==cutoff
        assert all(ref['timestamp_ms']>cutoff for ref in packet['refs']+packet['extra_refs'])
        assert all(row['timestamp_ms']>cutoff for row in packet['cv_metadata']['frames'])
    count=len(reviews)
    service.scenes.submit(frames[15],'scheduled')
    assert service.scenes.pending is None and len(repo.list('scene_review'))==count
    # Turning AI off/on retains ownership of already-attempted footage.
    service.set_enabled(False,True);service.set_enabled(True,True)
    service.scenes.submit(frames[16],'cv_trigger');await service.scenes.task
    assert repo.list('scene_review')[-1]['packet']['start_ms']==15001
    service.scenes.reset()
    service.scenes.submit(frames[0]);await service.scenes.task
    assert repo.list('scene_review')[-1]['packet']['start_ms']==0
    await service.close()


@pytest.mark.asyncio
async def test_live_state_exposes_real_activity_and_disjoint_pending_period(tmp_path):
    store,repo,config,frames=setup(tmp_path);started=asyncio.Event();release=asyncio.Event()
    async def slow(messages,info):
        started.set();await release.wait()
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,no_finding())])
    service=ReviewService(store,repo,lambda:config,lambda *args:True,lambda _:FunctionModel(slow))
    service.set_enabled(True,True);service.scenes.submit(frames[5]);await started.wait()
    service.scenes.submit(frames[10])
    state=service.scenes.state();live=state['live_review'];pending=state['pending_review']
    assert live['settings']['live_phase']=='waiting_for_model'
    assert live['settings']['tool_trace'][0]['tool']=='model_request'
    assert live['usage']['requests']==1 and live['received_images']
    assert pending['packet']['start_ms']>live['packet']['cutoff_ms']
    release.set();await service.scenes.task
    assert service.scenes.state()['live_review'] is None
    assert repo.list('scene_review')[-1]['settings']['tool_trace'][-1]['tool']=='model_response'
    await service.close()


@pytest.mark.asyncio
async def test_budget_edits_keep_active_review_and_cutoff_other_edits_preserve_boundary(tmp_path,monkeypatch):
    from test_pipeline import pipeline
    lab=pipeline(tmp_path,monkeypatch);lab.config.analysis_mode='ai_scene'
    started=asyncio.Event();release=asyncio.Event()
    async def slow(messages,info):
        started.set();await release.wait()
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,no_finding())])
    lab.review=ReviewService(lab.evidence,lab.repository,lambda:lab.config,lab.current,lambda _:FunctionModel(slow))
    lab.review.set_enabled(True,True)
    await lab.process_one();await started.wait()
    running=lab.review.scenes.running;cutoff=running.packet.cutoff_ms;epoch=lab.review.scenes.epoch
    await lab.update_config({'review_budget_usd':.1})
    assert lab.review.scenes.running is running and lab.review.scenes.epoch==epoch
    assert lab.review.scenes.last_started_cutoff==cutoff
    release.set();await lab.review.scenes.task
    await lab.update_config({'scene_window_s':30})
    assert lab.review.scenes.last_started_cutoff==cutoff
    await lab.review.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('allowance,images,expected',[(None,3,'blocked_pricing'),(.001,3,'reviewed'),(.001,7,'blocked_media')])
async def test_monitoring_allowance_is_scoped_and_blocks_before_mock_dispatch(tmp_path,monkeypatch,allowance,images,expected):
    import httpx
    from openai import AsyncOpenAI
    from pydantic_ai import models
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from safety_app.infrastructure.runtime import runtime
    rt=runtime();rt.config.pop('monitoring_image_review',None)
    if allowance is not None:rt.config['monitoring_image_review']={'image_usd':allowance,'pricing_basis':'operator_image_allowance'}
    rt.config['pricing']['gpt-6-luna']['image_usd']=None
    monkeypatch.setattr(models,'ALLOW_MODEL_REQUESTS',True)
    store,repo,config,frames=setup(tmp_path);config.scene_max_images=images;config.review_budget_usd=.1
    calls=[]
    def handler(request):
        assert request.url.host=='mock.invalid'
        body=json.loads(request.content);calls.append(body)
        tool=next(t['name'] for t in body['tools'] if t['name'].startswith('final_result'))
        return httpx.Response(200,json={'id':'r','object':'response','created_at':0,'status':'completed','model':'gpt-6-luna',
            'output':[{'type':'function_call','id':'fc','call_id':'c','name':tool,'arguments':json.dumps(no_finding()),'status':'completed'}],
            'usage':{'input_tokens':100,'output_tokens':20,'total_tokens':120}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=AsyncOpenAI(api_key='fake',base_url='https://mock.invalid/v1',http_client=http)
        model=OpenAIResponsesModel('gpt-6-luna',provider=OpenAIProvider(openai_client=client))
        service=ReviewService(store,repo,lambda:config,lambda *args:True,lambda _:model)
        service.set_enabled(True,True);service.scenes.submit(frames[10]);await service.scenes.task
        review=repo.list('scene_review')[-1]
        assert review['status']==expected,review['error']
        assert len(calls)==int(expected=='reviewed')
        assert rt.config['pricing']['gpt-6-luna']['image_usd'] is None
        assert rt.config['learning_image_review']['image_usd']==.001
        if expected=='blocked_media':assert '7 images supplied; maximum 6' in review['error']
        if expected=='reviewed':
            records=rt.audit.rows(run_id=review['id'])
            request=next(r for r in records if r['event_type']=='model_request')
            assert request['configuration']['pricing_basis']=='operator_image_allowance'
            assert request['configuration']['image_allowance_usd']==.001
        await service.close()


def test_always_on_is_explicit_and_respects_global_stop(tmp_path,monkeypatch):
    from safety_app.infrastructure.runtime import runtime
    store,repo,config,frames=setup(tmp_path)
    monkeypatch.setenv('AI_ALWAYS_ON','true')
    service=ReviewService(store,repo,lambda:config,lambda *args:True,lambda _:TestModel())
    assert service.enabled and service.hosted_images_allowed and service.state()['always_on']
    assert service.scenes.task is None  # Startup does not submit footage or a paid probe.
    runtime().budgets.switch(True)
    stopped=ReviewService(store,repo,lambda:config,lambda *args:True,lambda _:TestModel())
    assert not stopped.enabled
    monkeypatch.setenv('AI_ALWAYS_ON','false')
    manual=ReviewService(store,repo,lambda:config,lambda *args:True,lambda _:TestModel())
    assert not manual.enabled


def test_scene_audit_requires_operator_and_exports_complete_scoped_trace(tmp_path,monkeypatch):
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import main
    from app.pipeline import Pipeline
    from safety_app.infrastructure.runtime import runtime
    from safety_app.api.routes.monitoring import router
    from safety_app.api.auth import router as auth_router
    lab=Pipeline(tmp_path);monkeypatch.setattr(main,'pipeline',lab)
    store,repo,config,frames=setup(tmp_path/'packet')
    lab.repository.save('scene_review',SceneReview(**CTX,id='review-one',packet=make_packet(store,repo,frames[5],config),analysis_mode='ai_scene',status='reviewed'))
    rt=runtime();monkeypatch.setattr(rt.audit,'export',lambda:None)
    for i in range(1002):rt.audit.write('model_request',run_id='review-one',resources={'model_request_id':str(i)})
    rt.audit.write('model_request',run_id='other-review')
    app=FastAPI();app.include_router(router);app.include_router(auth_router)
    with TestClient(app) as client:
        assert client.get('/api/scene-reviews/review-one/audit').status_code==401
        assert client.get('/api/scene-reviews').status_code==401
        assert client.post('/api/auth/session',json={}).status_code==200
        assert client.get('/api/scene-reviews').json()[0]['id']=='review-one'
        rows=client.get('/api/scene-reviews/review-one/audit?limit=1000').json()
        assert len(rows)==1000 and all(r['run_id']=='review-one' for r in rows)
        lines=[json.loads(line) for line in client.get('/api/scene-reviews/review-one/audit/download').text.splitlines()]
        assert len(lines)==1002 and [r['sequence'] for r in lines]==sorted(r['sequence'] for r in lines)
