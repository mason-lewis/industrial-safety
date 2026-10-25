# Event evaluation

Run `uv run python -m backend.app.evals.events data/annotations/plumbing-example.json --output data/artifacts/reports/plumbing.json`.
This example is synthetic plumbing, not a detector validation or measured safety accuracy. No language model is invoked.

Create a JSON document with `schema_version`, `data_kind` (`human_labeled_video` for real annotations), `mode` (`rules_only`, `coordinator`, or `delegation`), `coverage`, `labels`, `predictions`, and optional `incidents`.
Every label and prediction needs `id`, `source_id`, `camera_id`, `session_id`, `event_type`, `start_ms`, and `end_ms`. Times are source presentation timestamps in milliseconds. Use optional `track_ids` only where track correspondence is known. Labels support `uncertain: true`; retain notes about visibility and annotation uncertainty in your export. Predictions can include `detected_at_ms`, `reviewed`, `review_latency_ms`, and `review_cost_usd`.

Each coverage entry records one camera/session duration in `duration_ms`, including quiet negative footage. Do not double count overlapping footage. Incident entries need scoped IDs, `timestamp_ms`, and an explicitly labeled `pre_window_ms`; optional `warning_event_types` restricts eligible warnings.

Matching requires the same source, camera, session, and event type. When both sides have known tracks, they must share a track. Intervals overlap or are separated by at most `--tolerance-ms` (default 1000 ms). Eligible pairs are assigned once in nearest-start order, with IDs breaking ties. This policy is deterministic; long predicted ranges can overmatch and should be inspected in the report. Predictions associated only with uncertain labels are excluded; uncertain labels do not count as misses. Unmatched predictions compatible with already matched labels count as duplicates and false positives.

Reports contain event precision/recall, missed IDs, false-positive IDs and rate per camera-hour, duplicate rate, source-time detection delay for each match, mean recorded review latency, and review cost. Missing denominators, timings, or configured prices produce null, not zero. Warning lead time is reported only inside an explicit pre-incident window and strictly before the incident anchor. A dwell violation detected after it starts is not an advance warning.

Evaluate the same held-out footage and annotations in all three modes. Keep clip/session/camera groups entirely in one split; adjacent frames must not cross splits. Include quiet negatives, occlusion, lighting changes, crowds, and unsupported objects when available. Record actual hardware, detector/weights hashes, rule configuration, thresholds, evidence coverage, and model/prompt settings with each export. Report throughput from local measured runs. No real-video accuracy or hardware throughput has been established by the included fixture.
