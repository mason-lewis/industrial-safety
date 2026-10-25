import asyncio
from types import SimpleNamespace
import cv2
import numpy as np
import pytest
from safety_app.modules.monitoring.temporal_sampling import TemporalSampler
from safety_app.domain.video_safety import VisualSafetyCheck
from safety_app.domain.models import Interval
from safety_app.workflows.video_analysis import validate_screening


def test_brief_local_motion_retains_before_during_after_and_context():
    sampler=TemporalSampler()
    for ms in range(0,10000,100):
        image=np.zeros((108,192,3),np.uint8)
        if 5400<=ms<5800:image[35:75,50:90]=255
        sampler.add(str(ms),ms,image)
    times=[s.timestamp_ms for s in sampler.selected()]
    assert len(sampler.samples)==100 and len(times)==6
    assert times[0]<5400 and times[-1]>5400
    assert 5400 in times and any(4800<=t<5400 for t in times) and any(5400<t<=6000 for t in times)
    assert times==sorted(set(times))


def test_sampling_stationary_short_and_nonzero_pts():
    sampler=TemporalSampler()
    image=np.zeros((60,80,3),np.uint8)
    for ms in range(1234,11234,100):sampler.add(str(ms),ms,image)
    assert [s.timestamp_ms for s in sampler.selected()]==[1234,3234,5234,7134,9134,11134]
    assert len(TemporalSampler().selected())==0
    sampler=TemporalSampler();sampler.add('one',1234,image)
    assert [s.timestamp_ms for s in sampler.selected()]==[1234]
    with pytest.raises(ValueError):TemporalSampler(max_frames=1)


def check_result(category,refs,**kwargs):
    check=VisualSafetyCheck(category=category,result='suspected',visibility='clear',condition='Synthetic test claim',
        evidence_refs=refs,start_ms=0,end_ms=1000,needs_judgment=True,**kwargs)
    result=SimpleNamespace(screening_checks=[check])
    frames=[SimpleNamespace(id='a',timestamp_ms=0),SimpleNamespace(id='b',timestamp_ms=1000)]
    validate_screening(result,frames,[Interval(start_ms=0,end_ms=1000)],True,[],None)
    return check


def test_background_clutter_is_not_a_route_and_does_not_escalate():
    check=check_result('blocked_access',['a'])
    assert check.result=='unknown' and not check.needs_judgment
    assert 'route' in check.limitations[0]
    assert check_result('blocked_access',['a'],route_basis='visible_route').result=='suspected'


def test_incident_category_keeps_temporal_evidence_without_inventing_it():
    assert check_result('struck_by',['a','b'],temporal_basis='sequence_event').result=='suspected'
    unsupported=check_result('struck_by',['a'],temporal_basis='sequence_event')
    assert unsupported.result=='unknown' and not unsupported.needs_judgment
    from safety_app.modules.safety_review.video_cases import DOMAINS
    from safety_app.modules.safety_review.catalog import PROFILES
    assert DOMAINS['struck_by'] in PROFILES and DOMAINS['fall'] in PROFILES


async def test_live_cv_coalesces_arrived_frames_and_keeps_image_scope():
    from safety_app.modules.monitoring.live_review import LiveReview
    emitted=[];calls=[]
    class Detector:
        def predict(self,image,confidence):
            import time
            calls.append(int(image[0,0,0]));time.sleep(.03)
            return [([0,0,10,10],.8)],{}
    async def emit(message):emitted.append(message)
    pipeline=SimpleNamespace(cv_task=None,emit=emit,current=lambda s,g:s=='session' and g==2)
    rt=SimpleNamespace(people=SimpleNamespace(detector=Detector()),jobs=SimpleNamespace(cancelled=lambda id:False))
    live=LiveReview(rt,pipeline);pipeline.course_review=live
    live.run=SimpleNamespace(id='run',mode='hosted',source_id='clip',session_id='session',generation=2,job_id='job',request={})
    def packet(ms,generation=2):return SimpleNamespace(session_id='session',run_generation=generation,timestamp_ms=ms,frame_id=str(ms))
    live.schedule_people(packet(0),np.zeros((20,20,3),np.uint8))
    await asyncio.sleep(.005)
    for i in range(1,4):live.schedule_people(packet(i*200),np.full((20,20,3),i,np.uint8))
    live.schedule_people(packet(900,3),np.full((20,20,3),99,np.uint8))
    await pipeline.cv_task
    assert calls==[0,3]
    assert [m['sample']['timestamp_ms'] for m in emitted]==[0,600]
    assert all(m['sample']['review_run_id']=='run' and m['sample']['run_generation']==2 for m in emitted)
    import base64
    image=cv2.imdecode(np.frombuffer(base64.b64decode(emitted[-1]['sample']['image_data_url'].split(',')[1]),np.uint8),cv2.IMREAD_COLOR)
    assert image[0,0,0]==3
    assert len(emitted[-1]['sample']['people'])==1
    live.run.mode='fixture';live.schedule_people(packet(1000),image)
    assert live.cv_pending is None


async def test_live_cv_discards_late_result_after_run_switch():
    from safety_app.modules.monitoring.live_review import LiveReview
    emitted=[]
    class Detector:
        def predict(self,image,confidence):
            import time
            time.sleep(.03);return [],{}
    async def emit(message):emitted.append(message)
    pipeline=SimpleNamespace(cv_task=None,emit=emit,current=lambda s,g:True)
    rt=SimpleNamespace(people=SimpleNamespace(detector=Detector()),jobs=SimpleNamespace(cancelled=lambda id:False))
    live=LiveReview(rt,pipeline);pipeline.course_review=live
    live.run=SimpleNamespace(id='run',mode='hosted',source_id='clip',session_id='s',generation=1,job_id='job',request={})
    live.schedule_people(SimpleNamespace(session_id='s',run_generation=1,timestamp_ms=0,frame_id='0'),np.zeros((20,20,3),np.uint8))
    await asyncio.sleep(.005);pipeline.course_review=None
    await pipeline.cv_task
    assert not emitted


def test_two_separate_motion_bursts_keep_each_short_sequence():
    from safety_app.modules.monitoring.temporal_sampling import Sample
    sampler=TemporalSampler()
    sampler.samples=[Sample(str(t),t,b'pixels',20 if t==2400 else 19 if t==5600 else 0) for t in range(0,10000,200)]
    assert [s.timestamp_ms for s in sampler.selected()]==[1800,2000,2200,2400,2600,2800,5000,5200,5400,5600,5800,6000]
    sampler.version='motion-context-1'
    assert [s.timestamp_ms for s in sampler.selected()]==[2200,2400,2600,5400,5600,5800]


def test_dense_sequence_retains_in_between_contact_frame_without_event_label():
    from safety_app.modules.monitoring.temporal_sampling import Sample
    sampler=TemporalSampler()
    sampler.samples=[Sample(str(t),t,b'pixels',20 if t==5400 else 16 if t==2300 else 1) for t in range(0,10000,100)]
    times=[s.timestamp_ms for s in sampler.selected()]
    assert times==[2000,2100,2200,2300,2400,2500,5100,5200,5300,5400,5500,5600]
    assert 5300 in times and len(times)==12


def test_sparse_and_edge_candidates_keep_disjoint_bounded_packets():
    from safety_app.modules.monitoring.temporal_sampling import Sample
    for cadence in (200,500,1000):
        sampler=TemporalSampler(cadence_ms=cadence)
        sampler.samples=[Sample(str(t),t,b'pixels',30 if i==1 else 29 if i==5 else 1)
                         for i,t in enumerate(range(1234,11234,cadence))]
        selected=sampler.selected()
        assert len(selected) in (6,12)
        assert len({s.frame_id for s in selected})==len(selected)
        assert all(1234<=s.timestamp_ms<11234 for s in selected)


def test_real_decode_tail_gaps_and_cancellation(tmp_path):
    from safety_app.modules.monitoring.temporal_sampling import sample_motion
    from safety_app.modules.monitoring.recordings import signature
    path=tmp_path/'short.mp4'
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),10,(32,32))
    for i in range(10):writer.write(np.full((32,32,3),i,np.uint8))
    writer.release()
    stored=[]
    class Media:
        def add_frame(self,media_id,frame_id,ts,jpeg):
            stored.append(ts);return SimpleNamespace(timestamp_ms=ts)
    frames,gaps=sample_motion(path,signature(path),Media(),'m',Interval(start_ms=0,end_ms=5000),lambda:False,max_frames=2)
    assert len(frames)==2 and any(g.startswith('Missing decoded tail') for g in gaps)
    with pytest.raises(InterruptedError):
        sample_motion(path,signature(path),Media(),'m',Interval(start_ms=0,end_ms=1000),lambda:True)
    assert len(stored)==2
