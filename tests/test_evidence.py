from dataclasses import FrozenInstanceError
import numpy as np
import pytest
from app.evidence.store import EvidenceScope, EvidenceStore
from app.models.contracts import FramePacket, IncidentMarker, RuleCandidate, TrackObservation

CTX = dict(source_id="clip", camera_id="cam", session_id="s1", run_generation=0)


def packet(ms):
    track = TrackObservation(**CTX, track_id=1, label="person", score=.9,
                             box=(.1,.1,.3,.4), timestamp_ms=ms, frame_id=ms)
    return FramePacket(**CTX, timestamp_ms=ms, frame_id=ms, width=16, height=16,
                       evidence_cutoff_ms=ms, tracks=[track])


def test_live_tools_reject_future_ranges_ids_and_wrong_context(tmp_path):
    store = EvidenceStore(tmp_path)
    early = store.add(packet(100), np.zeros((16,16,3), dtype=np.uint8))
    future = store.add(packet(200), np.zeros((16,16,3), dtype=np.uint8))
    scope = EvidenceScope("s1", 0, 100)
    assert store.image(early.id, scope).startswith(b"\xff\xd8")
    assert [r.id for r, _ in store.frames(scope, 0, 100)] == [early.id]
    assert [t.timestamp_ms for t in store.tracks(scope, [1], 0, 100)] == [100]
    with pytest.raises(ValueError):
        store.frames(scope, 0, 200)
    with pytest.raises(ValueError):
        store.tracks(scope, [1], 0, 200)
    for wrong in [scope, EvidenceScope("s2", 0, 300), EvidenceScope("s1", 1, 300)]:
        with pytest.raises(ValueError):
            store.image(future.id, wrong)
    with pytest.raises(KeyError):
        store.image("../../secret", scope)
    with pytest.raises(FrozenInstanceError):
        scope.as_of_ms = 200


def test_bundle_is_frozen_at_candidate_cutoff_and_frame_count_bounded(tmp_path):
    store = EvidenceStore(tmp_path)
    for ms in range(0, 2100, 100):
        store.add(packet(ms), np.zeros((16,16,3), dtype=np.uint8))
    candidate = RuleCandidate(**CTX, id="event", kind="zone_dwell", timestamp_ms=1000,
                              start_ms=0, evidence_cutoff_ms=1000)
    bundle = store.bundle(candidate)
    assert len(bundle.refs) <= 8
    assert all(ref.timestamp_ms <= 1000 for ref in bundle.refs)
    with pytest.raises(ValueError):
        store.frames(EvidenceScope("s1", 0, 2000), 0, 2000, 17)


def test_retention_is_bounded_and_missing_coverage_explicit(tmp_path):
    store = EvidenceStore(tmp_path, rolling_ms=100)
    old = store.add(packet(0), np.zeros((16,16,3), dtype=np.uint8))
    store.add(packet(500), np.zeros((16,16,3), dtype=np.uint8))
    assert old.id not in store.records
    assert not (tmp_path / (old.id + ".jpg")).exists()
    incident = IncidentMarker(**CTX, id="i", timestamp_ms=500, pre_ms=500, post_ms=500)
    assert store.coverage(incident)["missing_ranges"] == [[0,500],[500,1000]]
    bounded = EvidenceStore(tmp_path / "bounded", max_bytes=100)
    bounded.add(packet(0), np.zeros((16,16,3), dtype=np.uint8))
    assert bounded.total_bytes <= 100


def test_retrospective_scope_has_explicit_lower_boundary(tmp_path):
    store = EvidenceStore(tmp_path)
    ref = store.add(packet(100), np.zeros((16,16,3), dtype=np.uint8))
    scope = EvidenceScope("s1", 0, 1000, start_ms=200, mode="retrospective")
    with pytest.raises(ValueError):
        store.image(ref.id, scope)
    with pytest.raises(ValueError):
        store.frames(scope, 0, 1000)
