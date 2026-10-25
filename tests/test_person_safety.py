import asyncio
import json
import numpy as np
import pytest
from pydantic import ValidationError
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.messages import ModelResponse,ToolCallPart
from app.models.contracts import PPECheck,TrackObservation,RuleConfig,LabConfig,SceneReview,SceneResult,SceneFinding
from app.agents.scenes import make_packet,analyze,EventMemory
from app.agents.settings import ModelConfig
from app.agents.service import ReviewService
from app.video.ffmpeg_media import image,run
from app.evidence.clips import event_clip,rolling_segment
from test_rules import frame,zone_engine,line_engine
from test_scenes import setup,CTX,no_finding,result


def person(packet,id=7):
    packet.cv_status='available'
    packet.tracks=[TrackObservation(**CTX,track_id=id,label='person',score=.9,box=(.2,.2,.5,.8),timestamp_ms=packet.timestamp_ms,frame_id=packet.frame_id)]


def test_zone_hysteresis_and_explicit_interrupted_dwell():
    engine=zone_engine(duration=100)
    engine.process(frame(0,x=.5));[c]=engine.process(frame(100))
    assert engine.process(frame(200,x=.801))==[]
    assert engine.process(frame(300,x=.795))==[]
    assert engine.process(frame(400,x=.82))[0].state=='clearing'
    engine=zone_engine(duration=100)
    engine.process(frame(0));engine.process(frame(50,present=False));engine.process(frame(100))
    [c]=engine.process(frame(200))
    assert c.start_ms==100 and c.measurements['interrupted_start_ms']==0
    assert 'interrupted' in c.limitations[-1]
    assert c.measurements['detector_confidence']=={'1':.9}


def test_new_track_does_not_inherit_dwell_or_clear_old_person():
    engine=zone_engine(duration=100);engine.process(frame(0));[old]=engine.process(frame(100))
    packet=frame(200);packet.tracks[0].track_id=2
    changed=engine.process(packet)
    assert old.state=='active' and any('clearance unknown' in l for l in old.limitations)
    assert not any(c.track_ids==[2] for c in changed)
    packet=frame(300);packet.tracks[0].track_id=2
    [new]=engine.process(packet)
    assert new.start_ms==200 and new.id!=old.id


def test_line_tolerance_is_distance_independent_of_line_length():
    for line in [[(.2,.5),(.8,.5)],[(.4,.5),(.6,.5)]]:
        engine=line_engine();engine.config.rules[0].line=line;engine.config.rules[0].tolerance=.03
        engine.process(frame(0,bottom=.4))
        assert engine.process(frame(100,bottom=.52))==[]
        assert engine.process(frame(200,bottom=.6))[0].measurements['direction']=='positive'


@pytest.mark.parametrize('visibility',['small','obscured','outside_image','unclear'])
def test_ppe_missing_requires_clear_visibility(visibility):
    with pytest.raises(ValidationError,match='clear visibility'):
        PPECheck(item='helmet',status='missing',visibility=visibility,evidence_ids=['e'],explanation='Unknown')
    assert PPECheck(item='helmet',status='not_assessable',visibility=visibility,evidence_ids=['e'],explanation='Unknown').status=='not_assessable'


def test_person_selection_reveals_excluded_hazards_and_keeps_original_scene(tmp_path):
    store,repo,config,frames=setup(tmp_path,'ai_cv');config.person_focused=True;config.scene_max_images=3
    person(frames[10]);person(frames[15]);frames[20].cv_status='available'
    packet=make_packet(store,repo,frames[20],config)
    assert [r.timestamp_ms for r in packet.refs]==[10000,15000]
    assert packet.coverage['person_focused'] and packet.coverage['missing_ranges_ms']
    assert 'Hazards without confirmed people' in packet.coverage['outside_person_coverage']
    assert packet.cv_metadata['frames'][0]['tracks'][0]['score']==.9
    assert store.packets[packet.refs[0].id].width==10  # Selection retains the scene, not only person boxes.


@pytest.mark.asyncio
async def test_person_crops_and_ppe_typed_result_and_track_scope(tmp_path):
    store,repo,config,frames=setup(tmp_path,'ai_cv');config.person_focused=True;config.person_crop_cap=1
    person(frames[15]);packet=make_packet(store,repo,frames[15],config)
    ref=packet.refs[0]
    finding=dict(category='ppe',subject='person at workcell',status='new',finding_type='uncertain_finding',
        observed_condition='Helmet region obscured',inferred_risk='Protection cannot be established',first_ms=15000,last_ms=15000,
        evidence_ids=[ref.id],severity='unknown',human_review_reason='Inspect clearer image',track_ids=[7],
        ppe=[dict(item='helmet',track_id=7,status='not_assessable',visibility='obscured',evidence_ids=[ref.id],explanation='Occluded')])
    review=SceneReview(**CTX,packet=packet,analysis_mode='ai_cv')
    await analyze(store,review,ModelConfig(),lambda _:TestModel(call_tools=[],custom_output_args=dict(no_finding=False,summary='Uncertain PPE',findings=[finding])))
    assert review.status=='reviewed',review.error
    assert [i['kind'] for i in review.received_images]==['resized_scene','person_context_crop']
    finding['track_ids']=[999]
    review=SceneReview(**CTX,packet=packet,analysis_mode='ai_cv')
    await analyze(store,review,ModelConfig(),lambda _:TestModel(call_tools=[],custom_output_args=dict(no_finding=False,summary='Bad',findings=[finding])))
    assert review.status=='error'


@pytest.mark.asyncio
async def test_budget_keeps_unknown_reservation_and_exposes_skipped_review(tmp_path):
    store,repo,config,frames=setup(tmp_path);config.review_budget_usd=.001;config.review_request_bound_usd=.001
    calls=[]
    def fail(messages,info):calls.append(1);raise RuntimeError('Provider usage unknown')
    service=ReviewService(store,repo,lambda:config,lambda *a:True,lambda _:FunctionModel(fail));service.set_enabled(True,True)
    service.scenes.submit(frames[0]);await service.scenes.task
    service.scenes.submit(frames[5]);await service.scenes.task
    assert len(calls)==1
    assert repo.list('scene_review')[-1]['status']=='budget_exhausted'
    coverage=service.scenes.coverage_state()[0]
    assert coverage['budget_skipped_reviews']==1 and coverage['unknown_requests']==1
    assert coverage['cost_per_camera_hour'] is None and coverage['api_cost_usd'] is None
    await service.close()


@pytest.mark.asyncio
async def test_scheduler_records_empty_person_window_without_model_call(tmp_path):
    store,repo,config,frames=setup(tmp_path,'ai_cv');config.person_focused=True;frames[5].cv_status='available'
    service=ReviewService(store,repo,lambda:config,lambda *a:True,lambda _:TestModel())
    service.set_enabled(True,True);service.scenes.submit(frames[5])
    assert service.scenes.task is None and service.scenes.coverage_state()[0]['person_empty_windows']==1
    await service.close()


def test_cooldown_deduplicates_and_keeps_escalation_and_reload(tmp_path):
    store,repo,config,frames=setup(tmp_path);packet=make_packet(store,repo,frames[15],config)
    packet.settings['cooldown_s']=30;memory=EventMemory(repo)
    review=SceneReview(**CTX,packet=packet,analysis_mode='ai_scene',result=result(packet))
    id=memory.apply(review)[0]['event_id']
    later=make_packet(store,repo,frames[20],config);later.settings['cooldown_s']=30
    review=SceneReview(**CTX,packet=later,analysis_mode='ai_scene',result=result(later,'escalating'))
    review.result.findings[0].severity='high';memory.apply(review)
    assert len(memory.events)==1 and memory.events[id]['hypothesis']['severity']=='high'
    assert EventMemory(repo).events[id]['hypothesis']['severity']=='high'


def test_ffmpeg_rolling_and_clip_timestamps(tmp_path):
    from types import SimpleNamespace
    store,repo,config,frames=setup(tmp_path);root=tmp_path/'lab'
    source=tmp_path/'source.mp4'
    run(['-f','lavfi','-i','color=c=blue:s=64x48:r=5:d=4','-c:v','libx264','-n',str(source)])
    repo.save('session',dict(**CTX,id=CTX['session_id'],duration_ms=4000,config=config.model_dump(mode='json')))
    lab=SimpleNamespace(root=root,evidence=store,repository=repo,sources={'clip':dict(kind='file',path=str(source))})
    output=event_clip(lab,dict(**CTX,id='event',first_ms=1000,last_ms=2000))
    metadata=json.loads(output.with_suffix('.json').read_text())
    assert output.stat().st_size>0 and metadata['source_start_ms']==0 and metadata['source_end_ms']==4000
    assert event_clip(lab,dict(**CTX,id='event',first_ms=1000,last_ms=2000))==output
    segment=rolling_segment(lab,frames[10],0)
    assert segment['timestamps_ms']==list(range(0,11000,1000)) and segment['sampled']
    assert source.is_file()


@pytest.mark.asyncio
async def test_camera_layout_switch_and_restart_preserves_rules(tmp_path,monkeypatch):
    from app.pipeline import Pipeline
    from test_pipeline import FixtureDetector,FixtureTracker,FixtureSource
    class Source(FixtureSource):
        def __init__(self,path):super().__init__()
        def open(self):pass
        def close(self):pass
    monkeypatch.setattr('app.pipeline.FileReplaySource',Source);monkeypatch.setattr('app.pipeline.ByteTrackAdapter',FixtureTracker)
    lab=Pipeline(tmp_path,FixtureDetector());lab.sources={'a':dict(name='a',kind='file',path='a'),'b':dict(name='b',kind='file',path='b')}
    async def idle():pass
    lab.loop=idle
    await lab.start('a','offline','east')
    rule=RuleConfig(id='line',kind='line_crossing',line=[(.1,.2),(.8,.2)])
    await lab.update_config({'rules':[rule.model_dump(mode='json')],'required_ppe':['helmet']})
    await lab.start('b','offline','west');assert lab.config.rules==[] and lab.config.required_ppe==[]
    await lab.start('a','offline','east');assert lab.config.rules[0].id=='line' and lab.config.required_ppe==['helmet']
    assert lab.session['camera_id']=='east'
    restarted=Pipeline(tmp_path,FixtureDetector());restarted.sources=lab.sources;restarted.loop=idle
    await restarted.start('a','offline','east');assert restarted.config.rules[0].line==[(.1,.2),(.8,.2)]
    await lab.task;await restarted.task


@pytest.mark.asyncio
async def test_clear_ppe_claim_on_tiny_person_is_rejected(tmp_path):
    store,repo,config,frames=setup(tmp_path,'ai_cv');config.person_focused=True
    person(frames[15]);packet=make_packet(store,repo,frames[15],config);ref=packet.refs[0]
    finding=dict(category='ppe',subject='person',status='new',finding_type='uncertain_finding',observed_condition='Small person',
        inferred_risk='Unknown PPE',first_ms=15000,last_ms=15000,evidence_ids=[ref.id],track_ids=[7],severity='unknown',human_review_reason='Inspect',
        ppe=[dict(item='helmet',track_id=7,status='missing',visibility='clear',evidence_ids=[ref.id],explanation='Claimed clear')])
    review=SceneReview(**CTX,packet=packet,analysis_mode='ai_cv')
    await analyze(store,review,ModelConfig(),lambda _:TestModel(call_tools=[],custom_output_args=dict(no_finding=False,summary='Bad visibility',findings=[finding])))
    assert review.status=='error' and 'PPE too small' in review.error


@pytest.mark.asyncio
async def test_camera_guard_serialization_disables_internal_paid_retries(tmp_path,monkeypatch):
    import httpx
    from openai import AsyncOpenAI
    from pydantic_ai import models
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.models.openai import OpenAIResponsesModel
    monkeypatch.setattr(models,'ALLOW_MODEL_REQUESTS',True)
    store,repo,config,frames=setup(tmp_path);config.scene_max_images=2;calls=[]
    def handler(request):
        assert request.url.host=='mock.invalid';calls.append(1)
        return httpx.Response(503,json={'error':{'message':'fixture failure','type':'server_error','code':'failure'}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=AsyncOpenAI(api_key='fake',base_url='https://mock.invalid/v1',http_client=http,max_retries=0)
        review=SceneReview(**CTX,packet=make_packet(store,repo,frames[5],config),analysis_mode='ai_scene')
        await analyze(store,review,ModelConfig(),lambda _:OpenAIResponsesModel('gpt-6-luna',provider=OpenAIProvider(openai_client=client)))
    assert len(calls)==1 and review.usage['provider_requests']==1
    assert review.usage['usage_unknown_requests']==1 and review.cost_estimate is None
