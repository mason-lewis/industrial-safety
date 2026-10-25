"""Evaluate exported events against human labels, using source timestamps."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _key(event: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(event.get(k, "")) for k in ("source_id", "camera_id", "session_id", "event_type"))


def _validate(event: dict[str, Any]) -> None:
    if not all(event.get(k) for k in ("id", "source_id", "camera_id", "session_id", "event_type")):
        raise ValueError("Events require id, source_id, camera_id, session_id, and event_type")
    if event["start_ms"] < 0 or event["end_ms"] < event["start_ms"]:
        raise ValueError("Event source-time range must be nonnegative and ordered")


def temporal_iou(a, b):
    intersection=max(0,min(a['end_ms'],b['end_ms'])-max(a['start_ms'],b['start_ms']))
    union=max(a['end_ms'],b['end_ms'])-min(a['start_ms'],b['start_ms'])
    return intersection/union if union else float(a['start_ms']==b['start_ms'])


def evaluate(document: dict[str, Any], tolerance_ms: int = 1000) -> dict[str, Any]:
    """One-to-one matches by scoped event type and interval proximity.

    Intervals match if overlapping or separated by at most tolerance_ms.
    Eligible pairs are assigned nearest-start first (then IDs, deterministic).
    Uncertain human labels and predictions matching them are excluded.
    """
    if tolerance_ms < 0:
        raise ValueError("tolerance_ms must be nonnegative")
    threshold=document.get('temporal_iou_threshold')
    if threshold is not None and not 0<threshold<=1:raise ValueError('Temporal IoU threshold must be >0 and <=1')
    labels = document.get("labels", [])
    predictions = document.get("predictions", [])
    for event in labels + predictions:
        _validate(event)
    if len({e["id"] for e in labels}) != len(labels) or len({e["id"] for e in predictions}) != len(predictions):
        raise ValueError("IDs must be unique within labels and predictions")

    def compatible(a: dict, b: dict) -> bool:
        if _key(a) != _key(b):
            return False
        at, bt = set(a.get("track_ids", [])), set(b.get("track_ids", []))
        if at and bt and not at.intersection(bt):
            return False
        if threshold is not None:return temporal_iou(a,b)>=threshold
        gap = max(a["start_ms"], b["start_ms"]) - min(a["end_ms"], b["end_ms"])
        return gap <= tolerance_ms

    certain = [e for e in labels if not e.get("uncertain", False)]
    uncertain = [e for e in labels if e.get("uncertain", False)]
    ignored = [e for e in predictions if not any(compatible(e, l) for l in certain) and any(compatible(e, l) for l in uncertain)]
    ignored_ids = {e["id"] for e in ignored}
    scored = [e for e in predictions if e["id"] not in ignored_ids]
    pairs = sorted((-temporal_iou(p,l) if threshold is not None else abs(p["start_ms"] - l["start_ms"]), str(l["id"]), str(p["id"]), li, pi)
                   for li, l in enumerate(certain) for pi, p in enumerate(scored) if compatible(l, p))
    used_l, used_p, matches = set(), set(), []
    for _, _, _, li, pi in pairs:
        if li in used_l or pi in used_p:
            continue
        used_l.add(li)
        used_p.add(pi)
        l, p = certain[li], scored[pi]
        matches.append({"label_id": l["id"], "prediction_id": p["id"],
                        "detection_latency_ms": p.get("detected_at_ms", p["start_ms"]) - l["start_ms"]})
    missed = [l["id"] for i, l in enumerate(certain) if i not in used_l]
    false = [p for i, p in enumerate(scored) if i not in used_p]
    duplicates = [p["id"] for p in false if any(compatible(p, certain[i]) for i in used_l)]
    hours = sum(c["duration_ms"] for c in document.get("coverage", [])) / 3_600_000
    if any(c["duration_ms"] < 0 for c in document.get("coverage", [])):
        raise ValueError("Coverage durations must be nonnegative")
    lead_times = []
    for incident in document.get("incidents", []):
        if "pre_window_ms" not in incident or incident["pre_window_ms"] <= 0:
            continue
        eligible = [p for p in scored if _key(p)[:3] == _key(incident)[:3]
                    and incident["timestamp_ms"] - incident["pre_window_ms"] <= p.get("detected_at_ms", p["start_ms"]) < incident["timestamp_ms"]
                    and (not incident.get("warning_event_types") or p["event_type"] in incident["warning_event_types"])]
        first = min((p.get("detected_at_ms", p["start_ms"]) for p in eligible), default=None)
        lead_times.append({"incident_id": incident["id"], "warning_lead_ms": None if first is None else incident["timestamp_ms"] - first})
    reviews = [p for p in scored if p.get("review_latency_ms") is not None]
    costs = [p.get("review_cost_usd") for p in scored if p.get("reviewed", False)]
    return {"schema_version": "1", "mode": document.get("mode", "rules_only"),
            "data_kind": document.get("data_kind", "human_labeled_video"),
            "matching": {"tolerance_ms": tolerance_ms if threshold is None else None, 'temporal_iou_threshold':threshold,
                         "algorithm": "descending-overlap deterministic one-to-one match" if threshold is not None else "nearest-start one-to-one interval match"},
            "labels": len(certain), "predictions": len(scored), "true_positives": len(matches),
            "precision": len(matches) / len(scored) if scored else None,
            "recall": len(matches) / len(certain) if certain else None,
            "missed_events": missed, "false_positive_ids": [p["id"] for p in false],
            "false_positives_per_camera_hour": len(false) / hours if hours else None,
            "camera_hours": hours, "duplicate_ids": duplicates,
            "duplicate_rate": len(duplicates) / len(scored) if scored else None,
            "excluded_uncertain_labels": [l["id"] for l in uncertain], "excluded_predictions": sorted(ignored_ids),
            "matches": matches, "warning_lead_times": lead_times,
            "mean_review_latency_ms": sum(p["review_latency_ms"] for p in reviews) / len(reviews) if reviews else None,
            "review_cost_usd": sum(costs) if costs and all(c is not None for c in costs) else None,
            "limitations": ["Event matching evaluates supplied exports; it does not run inference or establish safety performance.",
                            "Latency is source-time delay unless explicit review wall-time measurements are supplied."]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tolerance-ms", type=int, default=1000)
    args = parser.parse_args()
    report = evaluate(json.loads(args.input.read_text()), args.tolerance_ms)
    encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
