"""Frame aliases resolve exactly within supplied evidence; failures stay strict."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
import pytest
from pydantic_ai.models.test import TestModel
from safety_app.infrastructure.runtime import Runtime
from safety_app.domain.models import MediaRef,Interval
from safety_app.agents.video_observer.video_models import VideoObservationRequest,VideoObservationResult
from safety_app.agents.video_observer.video_agent import observer
from safety_app.agents.shared.evidence_refs import register_frame,resolve_frame
from safety_app.workflows.agent_stage import run_stage,dependencies,AgentOutputRejected
from safety_app.workflows.video_analysis import content_for,validate_observations,validate_screening

@pytest.fixture
def packet(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    rt=Runtime(tmp_path)
    rt.db.save('media',MediaRef(id='m',media_id='m',source_id='fixture',camera_id='camera-1',start_ms=0,end_ms=1000,content_hash='fixture',storage_key='fixture'))
    ok,raw=cv2.imencode('.jpg',np.zeros((256,256,3),np.uint8));assert ok
    frame=rt.media.add_frame('m','0',0,raw.tobytes())
    job=rt.jobs.start('video_analysis','citation',{'media_ids':['m'],'start_ms':0,'end_ms':1000})
    return rt,job,frame

@pytest.mark.parametrize('reference',['0','f1',' m-0 '])
async def test_exact_supplied_references_are_canonicalized_before_scope_checks(packet,reference):
    rt,job,frame=packet;request=VideoObservationRequest(question='Fixture',purpose='Inspect',media_id='m',intervals=[Interval(start_ms=0,end_ms=1000)],safety_screening=True)
    output={'summary':'Fixture only.','observations':[{'media_id':'m','start_ms':0,'end_ms':0,'description':'Fixture observation.','evidence_refs':[reference]}],
        'screening_checks':[{'category':'ppe','result':'unknown','visibility':'not_assessable','condition':'No identifiable person or PPE item.','start_ms':0,'end_ms':0,'evidence_refs':[reference],'ppe_item':None,'ppe_state':'not_assessable'}]}
    def setup(deps):
        def check(result):
            validate_observations(result,[frame],request.intervals)
            validate_screening(result,[frame],request.intervals,True,[],rt.media)
        deps.output_check=check
    result,deps=await run_stage(rt,job,'video_observer',request,lambda _:TestModel(call_tools=[],custom_output_args=output),
        setup=setup,agent_builder=observer,content_builder=content_for([frame]))
    assert result.observations[0].evidence_refs==[frame.id]
    assert result.screening_checks[0].evidence_refs==[frame.id]
    assert result.screening_checks[0].ppe_item is None
    assert deps.seen_frames=={frame.id}
    assert rt.audit.rows(event_type='frame_references_normalized',job_id=job.id)

@pytest.mark.parametrize('reference,media,start,end,reason',[
    ('foreign-0','m',0,0,'allowed evidence IDs'),('f2','m',0,0,'allowed evidence IDs'),
    ('f1','foreign',0,0,'Media outside'),('f1','m',500,500,'timestamps'),
])
async def test_mapping_never_allows_foreign_invented_or_wrong_time_evidence(packet,reference,media,start,end,reason):
    rt,job,frame=packet;request=VideoObservationRequest(question='Fixture',purpose='Inspect',media_id='m',intervals=[Interval(start_ms=0,end_ms=1000)])
    output={'summary':'Fixture','observations':[{'media_id':media,'start_ms':start,'end_ms':end,'description':'Invalid fixture claim.','evidence_refs':[reference]}]}
    with pytest.raises(AgentOutputRejected,match=reason):
        await run_stage(rt,job,'video_observer',request,lambda _:TestModel(call_tools=[],custom_output_args=output),
            setup=lambda deps:setattr(deps,'output_check',lambda result:validate_observations(result,[frame],request.intervals)),
            agent_builder=observer,content_builder=content_for([frame]))
    assert not rt.audit.rows(event_type='frame_references_normalized',job_id=job.id)

def test_alias_collision_is_not_guessed_and_packet_hides_decoder_id(packet):
    rt,job,frame=packet;deps=dependencies(rt,job,'video_observer')
    request=VideoObservationRequest(question='Fixture',purpose='Inspect',media_id='m',intervals=[Interval(start_ms=0,end_ms=1000)])
    content=content_for([frame])(rt,job,'video_observer',request,deps,False)
    metadata=json.loads(content[1]);assert metadata['citation_id']=='f1' and metadata['evidence_id']==frame.id
    assert 'frame_id' not in metadata
    other=frame.model_copy(update={'id':'other-0','media_id':'other'})
    deps.seen_frames.add(other.id);register_frame(deps,other)
    assert resolve_frame(deps,'0')=='0'  # Ambiguous source ID remains rejected.
    assert resolve_frame(deps,'f1')==frame.id
    assert resolve_frame(deps,'invented')=='invented'
    unseen=frame.model_copy(update={'id':'unseen'})
    with pytest.raises(ValueError,match='unseen'):register_frame(deps,unseen)

def test_crop_accepts_only_a_supplied_alias(packet):
    from safety_app.agents.video_observer.video_tools import inspect_safety_detail
    rt,job,frame=packet;deps=dependencies(rt,job,'video_observer');deps.seen_frames.add(frame.id)
    register_frame(deps,frame,'f1');deps.detail_frame_ids={frame.id};deps.detail_limit=1
    result=inspect_safety_detail(SimpleNamespace(deps=deps),'f1',[0,0,1,1])
    assert result.return_value['parent_frame_id']==frame.id

def test_sampling_leaves_a_frame_period_inside_interval(packet,monkeypatch,tmp_path):
    from safety_app.modules.monitoring.recordings import sample,signature
    rt,job,_=packet;path=tmp_path/'timing.mp4';path.write_bytes(b'fake decoder fixture')
    rt.db.save('media',MediaRef(id='timing',media_id='timing',source_id='timing',camera_id='camera-1',start_ms=0,end_ms=1000,content_hash='timing',storage_key='fixture'))
    class Decoder:
        fps=30
        def __init__(self,path):pass
        def open(self):pass
        def close(self):pass
        def seek(self,target):
            # Forward seeking rounds up to a frame boundary. 999 ms would jump to 1001 ms.
            timestamp=round(np.ceil(target/33.367)*33.367)
            return SimpleNamespace(timestamp_ms=timestamp,frame_id=timestamp*1000,image=np.zeros((20,20,3),np.uint8))
    monkeypatch.setattr('safety_app.modules.monitoring.recordings.FileReplaySource',Decoder)
    frames,gaps=sample(path,signature(path),rt.media,'timing',[Interval(start_ms=0,end_ms=1000)],2,lambda:False)
    assert len(frames)==2 and 900<=frames[-1].timestamp_ms<=1000
    assert not any('No in-scope' in gap for gap in gaps)
