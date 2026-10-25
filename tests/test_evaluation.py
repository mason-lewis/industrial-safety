import pytest

from backend.app.evals.events import evaluate


def event(id, start=1000, end=2000, **kwargs):
    return dict(id=id, source_id="clip", camera_id="cam", session_id="s1",
                event_type="zone_dwell", start_ms=start, end_ms=end, **kwargs)


def test_event_matching_counts_duplicate_miss_and_negative_coverage():
    result = evaluate({"labels": [event("l1"), event("l2", 10000, 12000)],
                       "predictions": [event("p1"), event("dup", 1100), event("false", 20000, 21000)],
                       "coverage": [{"duration_ms": 3600000}]}, 100)
    assert result["true_positives"] == 1
    assert result["precision"] == 1 / 3
    assert result["recall"] == 0.5
    assert result["duplicate_ids"] == ["dup"]
    assert result["missed_events"] == ["l2"]
    assert result["false_positives_per_camera_hour"] == 2


def test_session_track_and_uncertainty_isolation():
    label = event("l", track_ids=[1])
    other = event("p", track_ids=[2])
    uncertain = event("u", 8000, 9000, uncertain=True)
    ignored = event("ignored", 8000, 9000)
    wrong_session = event("wrong", session_id_override="ignored")
    wrong_session["session_id"] = "s2"
    result = evaluate({"labels": [label, uncertain], "predictions": [other, ignored, wrong_session]}, 0)
    assert result["true_positives"] == 0
    assert result["excluded_predictions"] == ["ignored"]
    assert result["predictions"] == 2


def test_warning_requires_preincident_window_and_advance_timestamp():
    incident = dict(id="i", source_id="clip", camera_id="cam", session_id="s1", timestamp_ms=5000)
    result = evaluate({"predictions": [event("after", 6000, 7000)], "incidents": [incident]})
    assert result["warning_lead_times"] == []
    incident["pre_window_ms"] = 3000
    result = evaluate({"predictions": [event("before", 3000, 4000), event("after", 6000, 7000)], "incidents": [incident]})
    assert result["warning_lead_times"] == [{"incident_id": "i", "warning_lead_ms": 2000}]


def test_empty_metrics_are_unknown_and_invalid_ranges_rejected():
    result = evaluate({})
    assert result["precision"] is None
    assert result["recall"] is None
    assert result["review_cost_usd"] is None
    with pytest.raises(ValueError):
        evaluate({"labels": [event("bad", 2000, 1000)]})
    with pytest.raises(ValueError):
        evaluate({}, -1)
