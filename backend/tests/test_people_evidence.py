"""Person evidence provenance, scoped agent retrieval and video-only product boundary."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
import pytest
from safety_app.infrastructure.runtime import Runtime
from safety_app.domain.models import MediaRef
from safety_app.modules.monitoring.people import PeopleEvidence,StockPersonDetector,record_id
from safety_app.agents.shared.people import get_person_detections,person_summary
from safety_app.workflows.agent_stage import dependencies


@pytest.fixture
def rt(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    return Runtime(tmp_path)


def frame(rt,media_id='m',timestamp=500):
    rt.db.save('media',MediaRef(id=media_id,media_id=media_id,source_id='source',camera_id='camera-1',start_ms=0,end_ms=20000,content_hash='source-hash',storage_key='fixture'))
    _,raw=cv2.imencode('.jpg',np.zeros((200,400,3),np.uint8))
    return rt.media.add_frame(media_id,str(timestamp),timestamp,raw.tobytes())


class Detector:
    calls=0
    def predict(self,image,confidence=.5):
        self.calls+=1
        return [([40,20,120,180],.9),([200,0,400,200],.2),([0,0,float('nan'),1],.9)],{'weights_sha256':'fixture-only'}


def test_standard_adapter_discards_every_non_person_class(rt):
    detector=StockPersonDetector(rt.root)
    detector.model=SimpleNamespace(labels={1:'person',2:'bicycle',3:'car'},provenance={'class_map':{},'weights_sha256':'fixture'},
        predict=lambda *_:SimpleNamespace(xyxy=np.array([[0,0,10,10],[1,1,12,12],[2,2,13,13]]),confidence=[.8,.9,.95],class_id=[1,2,3]))
    rows,provenance=detector.predict(np.zeros((20,20,3),np.uint8))
    assert len(rows)==1 and rows[0][1]==.8 and 'class_map' not in provenance


def test_person_records_are_normalized_bounded_cached_and_hash_scoped(rt):
    engine=Detector();service=PeopleEvidence(rt.root,rt.db,rt.media,engine);f=frame(rt)
    record=service.capture('run',[f],True,lambda:False)[0]
    assert record.status=='available' and len(record.people)==1
    assert record.people[0].box==(.1,.1,.2,.8)
    assert record.timestamp_ms==500 and record.content_hash==f.content_hash
    cached=service.capture('run',[f],True,lambda:False,.15)[0]
    assert cached.confidence_threshold==.5 and len(cached.people)==1
    assert engine.calls==1
    assert service.get('different-run',f.id).status=='not_analyzed'
    broken=record.model_copy(update={'content_hash':'foreign'})
    rt.db.save('person_frame',broken)
    with pytest.raises(ValueError,match='provenance'):service.get('run',f.id)


def test_unavailable_detection_does_not_become_zero_people_or_block_visual_review(rt):
    f=frame(rt)
    class Missing:
        def predict(self,*_):raise FileNotFoundError('Stock weights missing')
    service=PeopleEvidence(rt.root,rt.db,rt.media,Missing())
    assert service.capture('run',[f],True,lambda:False)[0].status=='unavailable'
    job=rt.jobs.start('live_screening','scope',{'media_ids':['m'],'start_ms':0,'end_ms':1000})
    deps=dependencies(rt,job,'video_observer');deps.seen_frames={f.id};deps.person_detections=lambda id:service.get('run',id)
    assert person_summary(deps)['person_detection'][0]['detected_people'] is None
    assert 'Stock weights missing' in get_person_detections(SimpleNamespace(deps=deps),f.id).detail


def test_person_tool_rejects_foreign_unseen_and_future_frames(rt):
    f=frame(rt);future=frame(rt,timestamp=11000);foreign=frame(rt,'foreign')
    service=PeopleEvidence(rt.root,rt.db,rt.media,Detector())
    service.capture('run',[f,future,foreign],True,lambda:False)
    job=rt.jobs.start('live_screening','scope',{'media_ids':['m'],'start_ms':0,'end_ms':1000})
    deps=dependencies(rt,job,'video_observer');deps.person_detections=lambda id:service.get('run',id);deps.seen_frames={f.id}
    ctx=SimpleNamespace(deps=deps)
    assert get_person_detections(ctx,f.id).frame_id==f.id
    with pytest.raises(ValueError,match='assigned'):get_person_detections(ctx,foreign.id)
    deps.seen_frames.add(foreign.id)
    with pytest.raises(ValueError,match='Media outside'):get_person_detections(ctx,foreign.id)
    deps.seen_frames.add(future.id)
    with pytest.raises(ValueError,match='Interval outside'):get_person_detections(ctx,future.id)


def test_offline_fixture_skips_inference_and_cancellation_never_saves_results(rt):
    engine=Detector();service=PeopleEvidence(rt.root,rt.db,rt.media,engine);f=frame(rt)
    assert service.capture('fixture',[f],False,lambda:False)[0].status=='disabled'
    assert engine.calls==0
    with pytest.raises(InterruptedError):service.capture('cancelled',[f],True,lambda:True)
    with pytest.raises(KeyError):rt.db.get('person_frame',record_id('cancelled',f.id))


async def test_agent_receives_summary_and_can_pull_saved_person_data(rt):
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,ToolCallPart,TextPart,ToolReturnPart
    from safety_app.agents.video_observer.models import ObserverRequest,ObserverResult
    from safety_app.workflows.agent_stage import run_stage
    f=frame(rt);rt.people=PeopleEvidence(rt.root,rt.db,rt.media,Detector())
    job=rt.jobs.start('live_screening','tool-roundtrip',{'media_ids':['m'],'start_ms':0,'end_ms':1000,'budget_workflow_id':'run'})
    rt.people.capture('run',[f],True,lambda:False)
    seen=[]
    async def respond(messages,info):
        if not seen:
            assert 'person_detection' in str(messages)
            assert 'get_person_detections' in [t.name for t in info.function_tools]
            seen.append('called')
            return ModelResponse(parts=[ToolCallPart('get_person_detections',{'frame_id':f.id})])
        returned=[p for m in messages for p in m.parts if isinstance(p,ToolReturnPart) and p.tool_name=='get_person_detections']
        assert returned and 'available' in str(returned[0].content)
        seen.append('read')
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,ObserverResult().model_dump(mode='json'))])
    await run_stage(rt,job,'video_observer',ObserverRequest(media_ids=['m']),model_factory=lambda _:FunctionModel(respond))
    assert seen==['called','read']
    assert any(r['tool_name']=='get_person_detections' for r in rt.audit.rows(event_type='tool_finished'))


def test_shipped_api_exposes_only_review_scoped_chat_without_training_or_tagging(rt,monkeypatch):
    from fastapi.testclient import TestClient
    from safety_app.api.video_app import app
    from safety_app.workers.runner import KINDS
    monkeypatch.setattr('safety_app.infrastructure.runtime._default',rt)
    client=TestClient(app)
    assert client.post('/api/auth/session',json={}).status_code==200
    paths=client.get('/openapi.json').json()['paths']
    assert '/api/review-runs/live' in paths and '/api/review-runs/{id}/report' in paths
    assert '/api/monitoring/uploads' in paths and '/api/ai' in paths
    assert '/api/review-runs/{run_id}/chat' in paths
    assert all('/api/review-runs/' in path for path in paths if 'chat' in path)
    assert not any(any(word in path for word in ('training','trainer','labels','datasets','trials','scene-reviews','cv/models')) for path in paths)
    assert client.get('/api/video-analysis/sources').json()==[]
    f=frame(rt)
    assert client.get(f'/api/media/frames/{f.id}').content.startswith(b'\xff\xd8')
    assert set(KINDS['video'])=={'video_analysis','video_safety_review','live_screening','team_summary'}


async def test_full_video_review_shares_person_records_with_status_and_report(rt,monkeypatch):
    from safety_app.domain.video_analysis import VideoAnalysisRequest
    from safety_app.modules.monitoring.recordings import sources
    from safety_app.modules.safety_review.video_analysis import VideoAnalysisService
    from safety_app.modules.safety_review.review_runs import ReviewRuns
    from safety_app.modules.safety_review.run_report import report_data,report_html
    from safety_app.workers.runner import execute
    from safety_app.workflows.video_fixture import models
    folder=rt.root/'data/input';folder.mkdir(parents=True)
    writer=cv2.VideoWriter(str(folder/'fixture.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),10,(400,200))
    assert writer.isOpened()
    for _ in range(20):writer.write(np.zeros((200,400,3),np.uint8))
    writer.release()
    monkeypatch.setenv('SAFETY_PAID_REVIEW','1');monkeypatch.setenv('OPENAI_API_KEY','never-sent-offline-test')
    engine=Detector();rt.people=PeopleEvidence(rt.root,rt.db,rt.media,engine)
    request=VideoAnalysisRequest(source_id=sources(rt.root)[0].id,question='Inspect visible safety conditions',review_strategy='full',mode='hosted',idempotency_key='people-full',person_confidence=.15)
    job=VideoAnalysisService(rt).request(request,'local')
    done=await execute(rt,job,model_factory=models())
    assert done.status=='completed',done.error
    status=ReviewRuns(rt).status(job.run_id,'local')
    assert status.coverage_complete and len(status.people)==6
    assert all(p.status=='available' and len(p.people)==2 and p.confidence_threshold==.15 for p in status.people)
    assert status.run.request['person_confidence']==.15
    data=report_data(rt,job.run_id,'local')
    assert len(data['people'])==6 and 'Person detection' in report_html(data)
    assert all(row['configuration']['fake_provider'] for row in rt.audit.rows(event_type='model_request'))


def test_custom_or_modified_checkpoint_is_rejected_before_inference(rt):
    pytest.importorskip('rfdetr')
    weights=rt.root/'data/artifacts/models/rf-detr-small.pth'
    weights.parent.mkdir(parents=True,exist_ok=True);weights.write_bytes(b'custom-checkpoint-not-stock')
    with pytest.raises(ValueError,match='stock COCO'):
        StockPersonDetector(rt.root).predict(np.zeros((200,400,3),np.uint8))
    assert weights.read_bytes()==b'custom-checkpoint-not-stock'
