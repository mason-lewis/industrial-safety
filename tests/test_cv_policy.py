"""Adversarial counterexamples for class filtering, semantic groups, and tracking identity."""
import numpy as np
import pytest
import supervision as sv
from app.cv.classes import filtered_detections,inference_floor,matches,groups_for
from app.cv.detector import ByteTrackAdapter,RFDETRDetector
from app.models.contracts import LabConfig,RuleConfig


def detections(classes,scores,boxes=None):
    return sv.Detections(xyxy=np.array(boxes or [[10,10,30,30] for _ in classes],dtype=float).reshape(-1,4),
                         class_id=np.array(classes,dtype=int),confidence=np.array(scores,dtype=float))


def test_irrelevant_classes_are_dropped_and_low_per_class_override_reaches_filter():
    config=LabConfig(confidence=.8,class_confidence={'person':.3},enabled_labels=['person','truck'])
    labels={0:'person',1:'truck',2:'pizza'}
    result=filtered_detections(detections([0,0,1,2,99],[.4,.2,.9,.99,.99]),labels,config)
    assert result.class_id.tolist()==[0,1]
    assert inference_floor(config)==.3
    config.enabled_labels=[];assert len(filtered_detections(result,labels,config))==0
    config.enabled_labels=None;assert len(filtered_detections(detections([2],[.99]),labels,config))==1


def test_groups_are_explicit_not_free_text_and_preserve_specific_labels():
    config=LabConfig()
    assert matches('truck',['group:vehicles'],config.class_groups)
    assert matches('forklift',['group:industrial_vehicles'],config.class_groups)
    assert not matches('pizza',['group:vehicles'],config.class_groups)
    assert not matches('forklift',['vehicles'],config.class_groups)
    assert groups_for('forklift',config.class_groups)==['vehicles','industrial_vehicles']
    assert groups_for('truck',config.class_groups)==['vehicles']
    # Group expansion does not fabricate a forklift detection from a truck.
    original=filtered_detections(detections([1],[.9]),{1:'truck'},config)
    assert original.class_id.tolist()==[1]


def test_group_configuration_rejects_unknown_groups_and_nested_members():
    with pytest.raises(ValueError):LabConfig(rules=[RuleConfig(id='r',kind='proximity',target_labels=['group:unknown'])])
    with pytest.raises(ValueError):LabConfig(class_groups={'vehicles':['group:other']})
    with pytest.raises(ValueError):LabConfig(class_confidence={'person':float('nan')})
    with pytest.raises(ValueError):LabConfig(enabled_labels=['person','person'])


def test_track_ids_do_not_cross_classes_even_for_identical_overlapping_boxes():
    config=LabConfig(tracker_min_frames=1,tracker_activation=.3,tracker_high_confidence=.3)
    tracker=ByteTrackAdapter(config=config)
    assert len(tracker.update(detections([0,1],[.9,.9]),timestamp=0))==0
    first=tracker.update(detections([0,1],[.9,.9]),timestamp=.1)
    assert len(first)==2 and len(set(first.tracker_id))==2
    ids={int(class_id):int(id) for class_id,id in zip(first.class_id,first.tracker_id)}
    second=tracker.update(detections([1,0],[.9,.9]),timestamp=.2)
    assert {int(c):int(i) for c,i in zip(second.class_id,second.tracker_id)}==ids
    third=tracker.update(detections([1],[.9]),timestamp=.4)
    assert third.tracker_id.tolist()==[ids[1]]


def test_retained_track_returns_same_id_without_emitting_ghost_boxes():
    config=LabConfig(tracker_min_frames=1,tracker_lost_seconds=1,tracker_high_confidence=.3)
    tracker=ByteTrackAdapter(config=config)
    tracker.update(detections([0],[.9]),timestamp=0)
    first=tracker.update(detections([0],[.9]),timestamp=.1);id=int(first.tracker_id[0])
    assert len(tracker.update(detections([],[]),timestamp=.2))==0
    assert tracker.update(detections([0],[.9]),timestamp=.4).tracker_id.tolist()==[id]
    tracker.update(detections([],[]),timestamp=2)
    assert len(tracker.update(detections([0],[.9]),timestamp=2.2))==0
    assert int(tracker.update(detections([0],[.9]),timestamp=2.4).tracker_id[0])!=id


def test_tracker_thresholds_can_still_prevent_low_detector_scores_from_starting_tracks():
    tracker=ByteTrackAdapter(config=LabConfig(tracker_min_frames=1,tracker_activation=.5,tracker_high_confidence=.6))
    assert len(tracker.update(detections([0],[.4]),timestamp=0))==0
    assert len(tracker.update(detections([0],[.9]),timestamp=.2))==0
    assert len(tracker.update(detections([0],[.9]),timestamp=.4))==1


def test_custom_checkpoint_class_map_mismatch_fails_without_resizing_head(monkeypatch,tmp_path):
    import rfdetr
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(rfdetr.RFDETR,'from_checkpoint',lambda *args,**kwargs:(calls.append(kwargs) or SimpleNamespace(class_names=['person','forklift'])))
    detector=RFDETRDetector('custom',str(tmp_path/'weights.pth'),{0:'person',1:'truck'},device='cpu')
    with pytest.raises(ValueError,match='exactly match'):detector.load()
    assert calls==[{'device':'cpu','trust_checkpoint':False}]
    assert 'num_classes' not in calls[0]
