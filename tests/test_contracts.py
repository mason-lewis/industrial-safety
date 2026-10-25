import pytest
from pydantic import ValidationError
from app.cv.detector import CAPABILITIES, RFDETRDetector
from app.models.contracts import RuleConfig, ZoneConfig


def test_unsupported_safety_classes_never_claim_coco_support():
    capabilities = {c["id"]: c for c in CAPABILITIES}
    for label in ["forklift", "hard_hat", "safety_vest", "fire", "smoke", "spill", "fall", "injury"]:
        assert capabilities[label]["state"] == "unavailable"
    with pytest.raises(ValueError):
        RFDETRDetector("xl")
    with pytest.raises(ValueError):
        RFDETRDetector("custom")


def test_geometry_requires_normalized_polygon_and_nondegenerate_line():
    with pytest.raises(ValidationError):
        ZoneConfig(id="z", label="bad", points=[(0,0),(2,0),(0,1)])
    with pytest.raises(ValidationError):
        RuleConfig(id="r", kind="line_crossing", line=[(.5,.5),(.5,.5)])
    with pytest.raises(ValidationError):
        RuleConfig(id="r", kind="zone_dwell")


def test_rule_tool_geometry_ids_and_thresholds_are_validated():
    from app.models.contracts import LabConfig
    for points in [[(0,0),(.5,0),(1,0)],[(0,0),(1,1),(0,1),(1,0)],[(0,0),(1,0),(1,1),(0,0)]]:
        with pytest.raises(ValidationError):ZoneConfig(id='z',label='invalid',points=points)
    with pytest.raises(ValidationError):RuleConfig(id='r',kind='zone_entry')
    with pytest.raises(ValidationError):RuleConfig(id='r',kind='proximity',distance_threshold=0)
    with pytest.raises(ValidationError):RuleConfig(id='r',kind='proximity',target_labels=[''])
    with pytest.raises(ValidationError):LabConfig(rules=[RuleConfig(id='r',kind='zone_dwell',zone_id='missing')])
    rule=RuleConfig(id='r',kind='proximity')
    with pytest.raises(ValidationError):LabConfig(rules=[rule,rule])
