# Two-layer video safety review

This workflow is implemented for recorded-video analysis in **Video sandbox**, inside the safety-review frontend module. It keeps PydanticAI, the pinned model and four bounded agent folders. Existing interactive Monitoring and event/facility review remain available. No new detector model, framework, external search, cloud training or reporting delivery service was added.

## Layer one: inexpensive visual screening

The video observer prioritizes visible acute events (`struck_by`, `fall`), then checks four routine categories: PPE, blocked access, unstable stacks and slip/trip conditions. Each typed check records source interval, category, visible condition, cited frame IDs, clear/suspected/unknown result, visibility, judgment need, priority, exposure and limits. A clear check means no issue observed in those samples; it is not proof that the clip or site is safe.

Default `video_screening` settings in `configs/agents.yaml` are:

| Setting | Default | Boundary |
| --- | --- | --- |
| frames | 2 | 2–6, reduced by the shared image allowance |
| detail_crops | 1 | 0–2; reduced to fit the shared physical image cap |
| input_tokens | 16,000 | At most the existing shared input allowance |
| output_per_request | 1,200 | At most the existing physical output allowance |
| max_escalations | 2 | 0–4 handoffs per analysis, frozen at completion |

Screening uses explicit low reasoning, at most four model requests/tools and 2,400 total output tokens per observer stage, further bounded by global configuration. Each pass can return up to twelve screening checks. Planner and independent video-answer verification retain their existing limits. The defaults reduce frames and output capacity; actual cost and detection quality need real evaluation.

Full Review/Live additionally freeze `temporal_sampling=motion-context-1`: up to six chronological frames from two separated motion bursts, with a 24,000 input-token stage limit. The six-image shared cap disables further crops when all six slots are used. Legacy adaptive analysis retains the two-frame defaults above. Sampling scores do not establish hazards or continuity; the [harness](../harness.md) documents selection and limitations. Sequence-event checks need two distinct cited timestamps, and suspected blocked access needs `route_basis=visible_route`; unsupported checks stay unknown and do not escalate.

The observer receives its fixed checklist and requested PPE in its initial instructions. `get_safety_checklist()` remains a legacy helper but is not exposed to this agent. Its remaining tools are sequential and audited:

- `get_person_detections(frame_id)` reads saved, scope-checked person boxes and provenance; it performs no new inference or seeking.
- `inspect_safety_detail(frame_id, box)` crops an already attached still using an exactly four-number array of finite normalized XYWH coordinates and returns a JPEG up to 512 pixels on its longest side. It cannot read another timestamp or file. At most one crop is available by default; original pixels remain retained. The added image enters the same physical-request cost guard. Two initial images plus one crop remain three images in history.

Recorded observer and challenge image calls now use the [Responses input-token counting endpoint](https://developers.openai.com/api/docs/guides/token-counting) through the pinned PydanticAI adapter. Counts include instructions and schemas, are cached by exact payload, and enforce the existing cumulative stage cap before generation. Count failures or over-cap payloads create no reservation. The completed-response adapter preserves reported cache-write lifetime categories for pricing; requests explicitly set the existing implicit cache mode to a 30-minute lifetime, so total-only writes can use that known rate. Reported splits take precedence; missing usage or inconsistent categories remain unknown and stop further paid requests within that review. Original image allowances reserve headroom only; actual cost uses reported token buckets without a duplicate image surcharge. All stage requests explicitly use `store=false`. Historical failures/charges are retained; they are not silently rerun or guessed away.

PPE uses present/not_visible/not_assessable states. A not-visible claim needs clear visibility and a localized person at least 120 pixels high in the bounded source still; coordinates remain model estimates. No supplied PPE requirement means applicability is missing. Screening must not infer absence from occlusion, required PPE from appearance alone, exit designation from any door, physical load balance from inferred weight, or spills from reflections alone. The existing detector is not presented as a PPE detector.

High priority requires a suspected condition, clear visibility, explicit visible exposure and an immediate-action reason. These signals can request manager attention before specialist review, but remain provisional. Unknown checks never escalate. Routine suspected checks escalate only when they request judgment. The observer cannot delegate; Python records and routes cases.

## Layer two: specialist judgment

Validated screening records create durable case/evidence links. The original video job must complete before its handoffs dispatch. High-priority cases are selected first, with at most two by default; others remain deferred. The selection plan is frozen so changing the cap does not silently replay old concerns. The review worker recovers unsent handoffs every five seconds using stable idempotency keys. A crash between job creation and link persistence recovers the same job. Failed, cancelled or completed child jobs are not automatically rerun.

Cases route PPE to the PPE specialist, access to emergency, and stack/slip/trip conditions to surfaces, with applicability delegated first. The boss may add another domain for a decisive uncertainty. The existing source-search, exact-clause, question ownership, applicability, causality and finding validation remain enforced. Without sufficient applicability or exact text, no supported regulatory conclusion is allowed.

The coordinator builds up to four observation records and at most six unique cited frames from the case's screening evidence. Omitted observations remain explicit gaps. Layer two skips another broad observer pass: the boss/specialists receive the compact visible observations, and independent challenge receives the exact retained pixels. A specialist assessment, challenged findings and reporting snapshot commit together. Cancelled jobs cannot commit a successful snapshot.

The boss can provide routine/high `triage`, linked to its finding IDs and visible evidence. High triage needs substantive specialist findings. The challenger must explicitly assess supplied triage; urgency cannot be supported by a finding it also challenges. Independently supported high triage creates a separate manager-attention record. Supported routine triage can downgrade the current case while preserving earlier provisional attention records. Omitted or disputed triage stays unresolved rather than creating a confirmed urgency claim.

High-priority child reviews are claimed before older routine queued jobs. Running work is not interrupted. Stage/workflow deadlines and project concurrency remain bounded. Source/parent/site/mode/budget linkage is checked before dispatch. The global stop switch cancels these child jobs; the sandbox also provides Stop specialist review.

Both layers use the existing Monitoring image allowance on copied configurations. All physical requests share the original video's durable workflow ID for spending and the 64-request cap, plus global day/month/concurrency and per-agent limits. Output review IDs remain the child workflow IDs. A handoff does not create a new independent spending allowance. Reservations and unknown charges remain durable. Headroom is not guaranteed: a safety review can fail or be deferred when the shared allowance is exhausted.

## Stored reporting inputs

All backend records use the existing SQLite store and retained media. Historical results are not rewritten; new fields have conservative defaults. Trial reset keeps case media; reporting snapshots remain separate from archived working-view findings.

| Record kind | Reporting purpose |
| --- | --- |
| safety_screening | Immutable compact check, category, visibility, source hash/camera, mode, requested PPE, screening version and recorded UTC time |
| video_safety_case | Stable analysis/parent/media links, source interval, category, coarse episode key, routing version and optional event calendar time |
| safety_case_evidence | Append-only links from a case to each screening observation |
| video_safety_dispatch_plan | Frozen automatic handoff selection and cap outcome |
| safety_handoff | Child job, selected screening IDs and explicitly omitted IDs |
| video_safety_assessment | Specialist findings, delegation results, independent challenge, investigation, triage and limits |
| manager_attention | Provisional or specialist-supported high-priority record, evidence link, immediate report condition and delivery status |

`recording_start_utc` is an optional timezone-aware analysis input mapping source PTS zero to a reported recording clock. Case event times derive from that clock plus the cited source offset. Without it, calendar event time stays unknown. Recency remains unverified even with a supplied clock. Old recorded footage must not be treated as a current emergency merely because it was analyzed today.

The two future report conditions are retained as data:

1. **Immediate:** high-priority conditions warranting manager review, with provisional/independently supported evidence status and explicit recency limits.
2. **Scheduled:** routine screening, unresolved concerns, judged findings, metrics and proposed corrective actions suitable for daily, weekly or custom windows.

`delivery_status=not_configured` is explicit. No recipient, schedule, email, external message, notification or report was sent. Report rendering, scheduling, acknowledgment and delivery are future work. Existing human decisions remain separate; model judgments and browser-local personal checks do not establish ground truth.

## APIs and metrics

- `GET /api/video-analysis/jobs/{id}/safety` returns both layers, including partial screening, handoffs, child statuses, assessments and manager-attention records.
- `GET /api/video-analysis/jobs/{id}/audit?include_safety=true` includes only the authorized parent and linked safety jobs. Full audit download accepts the same option and requires every linked job to be terminal.
- `GET /api/video-analysis/safety/metrics?since=...&until=...` uses timezone-aware, half-open windows on screening-record UTC time. Fixtures are excluded unless `include_fixtures=true`.

Metrics count observed checks, unknown checks, suspected episode buckets by category, high-priority buckets, completed/pending/deferred/failed judgments, finding classifications, corrective actions, sampled frame timestamps, model requests, reported tokens and configured cost estimates. Unknown charges and reservations stay visible. Current specialist state is used for selected screening records, so this is not historical as-of reconstruction. Model usage covers the selected video's whole pipeline, including nonsafety question reasoning.

Repeated source hash/camera/category/PPE-item/cited-PTS checks use the latest interpretation in derived counts. Source/camera/category/time buckets deduplicate repeated video tests. The bucket width is stored and part of identity. This is coarse grouping, not semantic incident tracking: a sustained condition can occupy several buckets, and multiple conditions can share one. Do not label these counts as injuries, true incident rates, employee exposure hours or PPE compliance percentages. Continuous coverage and independent ground truth are unavailable. Source time, analysis-record time and unverified event calendar time remain distinct.

The sandbox shows both layers and their actual states. Test-report export includes the latest safety snapshot with pending/failed/deferred status; it is not a delivered report. Pipeline diagnostics combine both layers' audit and usage. Optional PPE items are bounded to eight strings; turning safety off retains generic video question answering and starts no new screening/handoff.

## Verification

Run the documented regression suite and both fixtures. `scripts/video_analysis_fixture.py` generates synthetic video in a fresh temporary root and executes real delegation/memory tools with fixed FunctionModel/TestModel responses. It reports screening chunks, child reviews, stored assessments, attention records and zero provider requests. Tests cover clear/unknown non-escalation, PPE visibility/size/scope, real crop/checklist tool dialogue under the image cap, idempotent handoff recovery, frozen escalation selection, shared budget IDs, cancellation, urgency challenge, record clocks, priority claims, scope-filtered audits and metrics. These prove workflow boundaries, not real visual/safety accuracy or provider cost.


Provider schema regression: crop and PPE boxes use typed numeric arrays with exact four-coordinate validation, rather than tuple/prefixItems schemas. The actual Responses adapter/SDK wire format is tested with in-memory HTTP responses, a dummy key and zero real provider calls. Existing recorded arrays and scope/geometry limits remain unchanged.


Citation boundary: model image packets use evidence_id plus a short citation_id (f1, f2, etc.), while decoder IDs remain internal. Exact unambiguous supplied-frame aliases can be resolved to canonical IDs before the unchanged media/time/scope checks. Validated memory retrieval can register only references to its returned observations. Unknown or ambiguous aliases remain failures. Stored evidence always uses canonical IDs, and successful mappings are audited. Unknown PPE checks need no invented item name when no item/person can be assessed. End-window sampling uses a frame-period margin to avoid forward-seek overshoot; gaps remain explicit.

## Bounded original-pixel inspection and funded retests

New recorded manifests retain 100 ms motion-selected packets and may inspect a localized acute event using three context frames plus three original-resolution crops. The matching rectangles preserve temporal comparability. The observer's proposed refinement receives an independent pixel check; only approved text enters the refined aggregate. Both original packets and derived evidence remain saved. At most two detail passes share $0.01 within the review cap. Missing localization, uncertain motion, scene cuts and unobserved instants remain limitations. This does not guarantee impact recognition.

Review clients use verified TLS, explicit timeouts, zero transport retries and unique `X-Client-Request-Id` headers. Read-only model access is checked by the worker before the funded test. Sanitized transport phase/cause types distinguish proven pre-dispatch failures from unknown charges; generic connection errors cannot establish zero billing. Provider request-specific evidence is required for actual historical reconciliation.

`GET /api/review-runs/{id}/activity?after_sequence=0` returns authenticated, ordered activity and a reconnect cursor. `GET /api/review-runs/{id}/details/{detail_id}/crops/{index}` serves hash-verified crop bytes scoped to that review. Status and JSON exports include typed transport/crop metadata and the funded-test commitment. The Activity drawer reads these endpoints without hosted work. Full context and seek controls remain available.

`./scripts/uv run --no-sync python scripts/balcony_retest.py` inspects the one authorized test. Its explicit `--submit` originally queued the funded test, subject to hosted mode and the global switch. The fixed idempotency key survives restart and now returns that existing run. All prior balcony commitments, including the named unresolved $0.009500 reservation, count toward $0.15; the new workflow had $0.07. No other reservation is exempted. New ambiguous charges stop generation. A durable `review_test_closed` record now prevents further requests under this finished allowance and removes Continue from the saved review. No reset, retry loop, training request or model substitution is part of this test.

Token-count failures are provider-availability outcomes, separately from budget exhaustion. Counting retains sanitized transport phase/cause types and parent/job IDs; a failed count creates no generation reservation. Historical status remains intact, with an explanatory UI label. The failed hosted preflight predates these additional diagnostics, so its underlying service or transport cause is still unknown.

The one hosted retest (`3d0354ad-44b1-4d84-a8b9-07f751497a6c`) passed model access, settled one generation at $0.001143, performed two local detection lookups, then failed its next token-count preflight. It completed 0/2 intervals and did not reach the impact packet, focused inspection or specialists. No new unknown charge was created; combined commitment is $0.072751 including the unchanged $0.009500 historical reservation. No paid retry followed. Offline checks passed 546 backend tests, 98 frontend tests, contracts, build and five fixtures. Browser checks confirmed saved activity, seek/pause at 10 seconds, the corrected failure explanation and the closed allowance. Impact-link navigation, live running specialists, incident recognition and duplicate suppression were not established by this hosted run. [Measured report](../data/artifacts/reports/balcony-original-detail-retest-20261024/results.md).

## Renewed preflight test: completion recovered, impact recognition failed

The renewed authorization produced review `eae532bd-7b3c-43a3-8a67-399270fff17e`. Both intervals and all five assessments completed after repairs and explicit continuations of that same run. Generation usage was $0.062057 across 72 physical requests, with no new unknown charges. Combined commitment across six balcony runs is $0.134808, including the unchanged $0.009500 historical reservation. The new allowance is closed; `scripts/balcony_retest.py --preflight-retest` inspects the existing test rather than renewing it.

Token preflight may repeat its identical read-only count once after a transient failure, inside a shared 20-second deadline. SDK and paid-generation transport retries stay disabled. Invalid requests, authorization failures, cancellation and exhausted limits stop. A failed count dispatches no generation and creates no generation reservation. Every count gets a client ID and sanitized trace; there is no estimated-count fallback.

A six-frame packet allows six local person lookups. Stage/evidence tools register exact frame aliases, including raw PTS IDs; nested finding and hypothesis references normalize only those aliases. Child scopes cannot acquire another child's evidence. Missing separate visual uncertainty fields retain supplied unknown-element explanations and an explicit source gap; legal claims keep all source/applicability guards.

Inspection version `original-sequence-2` uses saved person boxes as a localization fallback and original pixels before resizing. A resumed acute handoff can append its missing inspection without changing an immutable screening. Changed derived observations receive content-based version IDs. Independent rejection retains the original provisional concern and a visible limitation. Conflicting urgency is withheld rather than published as independently supported. Exact validated boss/checker outputs can recover local finalization for the same request, prompt identity and settled job; two assessments recovered without new model calls.

The checker did not verify the falling-material/head-contact interpretation in this test. The focused packet retained 5.1, 5.3 and 5.6 seconds; this is not continuous footage or an accuracy pass. No expected incident time or answer was supplied to the production model. The UI shows one struck-by card and one trip card grouping four observations. Challenged claims stay uncertain; original screening text remains labeled in expanded evidence. Live activity and exact 5.3-second seek/pause passed. All 24 original frame hashes/timestamps and three crop hashes/parents passed fresh verification.

Final checks: 555 Python tests, 101 frontend tests, contracts, build and five isolated fixtures passed. Fixture success does not establish visual accuracy. Historical failures and the unresolved reservation remain. [Measured report](../data/artifacts/reports/balcony-preflight-retest-20261024/results.md), [receipt](../data/artifacts/reports/balcony-preflight-retest-20261024/verification.json).
