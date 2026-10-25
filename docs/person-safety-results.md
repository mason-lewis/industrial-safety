# Validation results — 2026-10-08

The implementation passes local tests and browser checks. Safety accuracy and the $0.01 API target remain unverified. No paid provider request or training was used.

## Person threshold

Seven human-saved validation frames contain 14 labeled people. Their cached local RF-DETR Small proposals and image hashes were checked against the current labels with one-to-one matching at IoU 0.5.

| Threshold | Matched people | False boxes | Missed people |
| --- | ---: | ---: | ---: |
| 0.50 | 14 | 0 | 0 |
| 0.60 | 14 | 0 | 0 |
| **0.65** | **14** | **0** | **0** |
| 0.70 | 13 | 0 | 1 |
| 0.80 | 10 | 0 | 4 |

The new default is 0.65. Existing explicit settings remain; the CV settings button applies 0.65 to a draft. This small, correlated sample contains no baseline false positives, so it cannot prove false-positive reduction. Labels were assisted and human saved; independent correction may be incomplete. No held-out test footage was used.

The [manifest](../data/evals/person-selection/manifest.json) includes source timestamps, images, hashes and person boxes for manual review. Full threshold results and false/missed examples are in `data/artifacts/reports/person-threshold-audit.json`.

## Rules, evidence and review

Python fixtures check irregular timestamp dwell, finite directional crossing, configurable tolerance/hysteresis, cooldowns, occupancy, lost tracks, changed IDs, camera isolation/restart, duplicate findings and escalation. PydanticAI mocks check ordered images, wider scene/person crops, scoped follow-ups, evidence citations, typed PPE visibility, tiny-person rejection and unknown usage reservations. Mock HTTP responses check request serialization and that transport retries cannot bypass camera budgeting. These tests establish implementation behavior, not real PPE or hazard accuracy.

Browser checks used an isolated server and fictional footage. Endpoint dragging initially exposed an extra click; the fix preserves the second endpoint. Saving and reload retained the moved point (x=0.25, y≈0.39949). Show rules removed visible lines without modifying the stored rule. Fullscreen retained the source aspect ratio. Proof: `data/artifacts/reports/person-rule-editor.png`.

## Processing and cost

A short CPU replay of real factory footage processed 16.833 source seconds in 10.437 wall seconds, including cold model load: 52 retained analysis frames and one complete rolling segment, with no encoder gaps. Eight passes of that clip processed 416 analysis frames / 134.664 repeated source seconds in 45.723 wall seconds on CPU, including cold load. FFmpeg wrote 16 segments including the flushed tails, with zero encoder gaps. Mean inference was 83.73 ms. The report is `data/artifacts/reports/person-throughput-repeat.json`. This repeated footage is not independent evidence or a five-camera load test.

The free fixture replay completed two mocked specialist delegations with zero provider requests. Generated contracts and the production frontend build were checked. 276 Python tests and 48 frontend tests passed. The fixture replay, generated-contract check and production build passed. Existing non-fatal Torch/FFmpeg and frontend bundle-size notices remain.

Actual billed analyzer cost, real PPE precision/recall, labeled geometric timing error, event-level hazard recall, and five-camera throughput have not been measured. Compute and storage dollar costs are also unmeasured. The UI exposes these limits, sampling exclusions, budget blocks and unknown charges. Provider usage and Standard-rate estimates can be recorded during an explicitly enabled bounded review; they do not establish invoice compliance or safety accuracy.

Person-only selection can miss hazards without confirmed people. Broader scene mode provides sampling outside that selection. Neither mode continuously inspects every instant, resolves occluded PPE, measures physical distances without calibration, or proves whether a person is authorized. New IDs do not automatically inherit dwell. Rolling video built from retained samples is visibly identified as sampled evidence.
