"""Synthetic adapters test transport/state plumbing, never detector accuracy."""
import asyncio
from collections import defaultdict, deque
from types import SimpleNamespace
import numpy as np
import pytest
from app.models.contracts import LabConfig, RuleConfig, ZoneConfig
from app.pipeline import Pipeline
from app.video.sources import DecodedFrame


class FixtureTracker:
    def __init__(self, **kwargs):
        self.trails = defaultdict(lambda: deque(maxlen=24))

    def update(self, detections, timestamp=None):
        self.timestamp = timestamp
        return detections


class FixtureDetector:
    labels = {1: "person"}
    provenance = {"data_kind": "synthetic_plumbing_only"}

    def predict(self, image, confidence):
        from supervision import Detections
        return Detections(xyxy=np.array([[20,20,60,60]], dtype=float),
                               tracker_id=np.array([1]), class_id=np.array([1]), confidence=np.array([.9]))


class FixtureSource:
    fps = 30

    def __init__(self):
        self.frames = iter([0,137,1000])

    def read(self):
        ms = next(self.frames)
        return self.seek(ms)

    def seek(self, ms):
        return DecodedFrame(np.zeros((80,100,3), dtype=np.uint8), ms, ms + 1)


def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr("app.pipeline.ByteTrackAdapter", FixtureTracker)
    lab = Pipeline(tmp_path, detector=FixtureDetector())
    lab.session = dict(source_id="synthetic", camera_id="cam", session_id="s1", run_generation=0,
                       schema_version="1.0", mode="offline", timestamp_ms=0)
    lab.source = FixtureSource()
    lab.decode_lock = asyncio.Lock()
    lab.config = LabConfig(zones=[ZoneConfig(id="z", label="restricted", points=[(0,0),(1,0),(1,1),(0,1)])],
                           rules=[RuleConfig(id="r", kind="zone_dwell", zone_id="z", duration_ms=500)])
    lab.reset()
    return lab


@pytest.mark.asyncio
async def test_cv_only_processing_emits_matched_metadata_and_marks_incident_without_model(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "nonsecret-test-key-present")
    lab = pipeline(tmp_path, monkeypatch)
    queue = asyncio.Queue(maxsize=20)
    lab.subscribers.add(queue)
    for _ in range(3):
        await lab.process_one()
    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())
    frames = [m["frame"] for m in messages if m["type"] == "frame"]
    assert [f["timestamp_ms"] for f in frames] == [0,137,1000]
    assert all(f["tracks"][0]["timestamp_ms"] == f["timestamp_ms"] for f in frames)
    assert all(f["tracks"][0]["frame_id"] == f["frame_id"] for f in frames)
    assert frames[-1]["tracks"][0]["box"] == [.2,.25,.6,.75]
    assert len(lab.events) == 1
    assert next(iter(lab.events.values())).ai_status == "disabled"
    marker = lab.mark_incident(1000, 500, 1000)
    assert marker.id in lab.incidents
    assert lab.metrics.model_requests == 0
    assert lab.state()["ai"]["enabled"] is False
    saved = lab.repository.list("candidate")[0]
    assert all(key in saved for key in ["config_version", "processing_time", "evidence_cutoff_ms", "run_generation"])


@pytest.mark.asyncio
async def test_seek_increments_generation_cancels_reviews_and_clears_overlays(tmp_path, monkeypatch):
    lab = pipeline(tmp_path, monkeypatch)
    for _ in range(3):
        await lab.process_one()
    prior_ids = list(lab.events)
    cancellations = []
    lab.review = SimpleNamespace(cancel_generation=lambda *args: cancellations.append(args))
    queue = asyncio.Queue(maxsize=20)
    lab.subscribers.add(queue)
    await lab.playback("seek", timestamp_ms=200)
    assert lab.session["run_generation"] == 1
    assert cancellations == [("s1",0)]
    assert lab.events == {}
    assert lab.pending.timestamp_ms == 200
    assert lab.current("s1",0) is False
    assert lab.current("s1",1) is True
    assert queue.get_nowait() == {"type":"clear", "run_generation":1}
    assert all(lab.repository.get("candidate", id) for id in prior_ids)


@pytest.mark.asyncio
async def test_late_inference_from_old_generation_is_discarded(tmp_path, monkeypatch):
    lab = pipeline(tmp_path, monkeypatch)
    original = lab.detector.predict
    def late(image, confidence):
        lab.session["run_generation"] += 1
        return original(image, confidence)
    lab.detector.predict = late
    queue = asyncio.Queue(maxsize=20)
    lab.subscribers.add(queue)
    await lab.process_one()
    assert lab.events == {}
    assert lab.evidence.records == {}
    assert queue.empty()

@pytest.mark.asyncio
async def test_config_save_preserves_engine_and_tracker_receives_source_time(tmp_path,monkeypatch):
    lab=pipeline(tmp_path,monkeypatch)
    for _ in range(3):await lab.process_one()
    engine=lab.rules
    await lab.update_config({'confidence':.6})
    assert lab.rules is engine and len(lab.rules.states)==1
    assert lab.tracker.timestamp==1.0

@pytest.mark.asyncio
async def test_step_at_end_is_a_normal_playback_state(tmp_path,monkeypatch):
    lab=pipeline(tmp_path,monkeypatch)
    for _ in range(3):await lab.process_one()
    await lab.playback('step')
    assert lab.health['state']=='ended' and not lab.playing

@pytest.mark.asyncio
async def test_cancellation_joins_native_worker_before_releasing_lock(tmp_path,monkeypatch):
    import threading
    lab=pipeline(tmp_path,monkeypatch)
    started=threading.Event();release=threading.Event();completed=threading.Event()
    def worker():started.set();release.wait(2);completed.set()
    async def locked():
        async with lab.decode_lock:await lab.source_work(worker)
    task=asyncio.create_task(locked())
    await asyncio.to_thread(started.wait,1)
    task.cancel();await asyncio.sleep(.01)
    assert lab.decode_lock.locked() and not task.done()
    release.set();await asyncio.gather(task,return_exceptions=True)
    assert completed.is_set() and not lab.decode_lock.locked()


def test_session_collections_are_bounded_and_disk_history_survives(tmp_path,monkeypatch):
    lab=pipeline(tmp_path,monkeypatch)
    lab.events={str(i):i for i in range(501)};lab.incidents={str(i):i for i in range(501)}
    lab.retain_records(0)
    assert len(lab.events)==len(lab.incidents)==500

@pytest.mark.asyncio
async def test_speed_control_does_not_start_a_paused_video(tmp_path,monkeypatch):
    lab=pipeline(tmp_path,monkeypatch)
    await lab.playback('speed',speed=1.5)
    assert lab.session['speed']==1.5 and not lab.playing and not lab.session['playing']
    lab.playing=True
    await lab.playback('speed',speed=.5)
    assert lab.playing and lab.session['playing']


@pytest.mark.asyncio
async def test_scrubbing_to_duration_clamps_to_last_frame_only_when_duration_known(tmp_path,monkeypatch):
    lab=pipeline(tmp_path,monkeypatch)
    await lab.playback('seek',timestamp_ms=200)
    assert lab.pending.timestamp_ms==200
    lab.session['duration_ms']=1000
    await lab.playback('seek',timestamp_ms=1000)
    assert lab.pending.timestamp_ms==967
    assert lab.session['run_generation']==2


@pytest.mark.asyncio
async def test_end_of_video_updates_play_pause_state(tmp_path,monkeypatch):
    lab=pipeline(tmp_path,monkeypatch);lab.playing=True;lab.session['playing']=True
    task=asyncio.create_task(lab.loop())
    try:
        async with asyncio.timeout(2):
            while lab.health['state']!='ended':await asyncio.sleep(.01)
        assert lab.session['playing'] is False
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True)


@pytest.mark.asyncio
async def test_rule_trigger_batches_only_new_eligible_episodes(tmp_path,monkeypatch):
    from app.models.contracts import RuleCandidate,FramePacket
    lab=pipeline(tmp_path,monkeypatch);lab.config.analysis_mode='ai_cv';lab.config.cv_triggered_reviews=True
    calls=[];lab.review=SimpleNamespace(scenes=SimpleNamespace(submit=lambda *args,**kwargs:calls.append((args,kwargs))))
    ctx={k:lab.session[k] for k in ['source_id','camera_id','session_id','run_generation','schema_version']}
    lab.cv_latest=FramePacket(**ctx,frame_id=1,timestamp_ms=1000,width=100,height=100,evidence_cutoff_ms=1000)
    make=lambda id:RuleCandidate(**ctx,id=id,kind='zone_dwell',rule_id='r',timestamp_ms=1000,start_ms=0,evidence_cutoff_ms=1000,state='active')
    a,b=make('a'),make('b');await lab.save_candidates([a,b])
    assert len(calls)==1 and calls[0][1]['triggered_candidate_ids']==['a','b']
    a.state='clearing';await lab.save_candidates([a]);assert len(calls)==1
    lab.config.rules[0].ai_review=False;await lab.save_candidates([make('c')]);assert len(calls)==1
    lab.config.rules[0].ai_review=True;lab.config.cv_triggered_reviews=False
    await lab.save_candidates([make('d')]);assert len(calls)==1
    lab.config.cv_triggered_reviews=True;lab.config.analysis_mode='cv_only'
    await lab.save_candidates([make('e')]);assert len(calls)==1


@pytest.mark.asyncio
async def test_filter_and_tracker_changes_reset_generation_but_preserve_saved_history(tmp_path,monkeypatch):
    lab=pipeline(tmp_path,monkeypatch)
    for _ in range(3):await lab.process_one()
    ids=list(lab.events);engine=lab.rules
    await lab.update_config({'enabled_labels':['person'],'tracker_activation':.6})
    assert lab.session['run_generation']==1 and lab.rules is not engine
    assert lab.events=={} and all(lab.repository.get('candidate',id) for id in ids)

@pytest.mark.asyncio
async def test_custom_preflight_failure_preserves_selected_model_and_generation(tmp_path,monkeypatch):
    lab=pipeline(tmp_path,monkeypatch);original=lab.detector;config=lab.config;generation=lab.session['run_generation']
    def fail(*args):raise ValueError('Checkpoint labels disagree')
    monkeypatch.setattr(lab,'make_detector',lambda c:SimpleNamespace(predict=fail))
    with pytest.raises(ValueError,match='labels disagree'):
        await lab.update_config({'detector':'custom','custom_model_id':'industrial-v1'})
    assert lab.detector is original and lab.config is config and lab.session['run_generation']==generation


def test_person_rules_report_unavailable_when_person_is_filtered(tmp_path,monkeypatch):
    lab=pipeline(tmp_path,monkeypatch);lab.config.enabled_labels=[]
    assert next(c for c in lab.capabilities() if c['id']=='person_zone_dwell')['state']=='unavailable'
