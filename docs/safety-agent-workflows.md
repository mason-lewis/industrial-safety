# Delegated safety review and investigation

## Sequential Live sample testing (2026-10-24)

**Final batch outcome: stopped on video 06, before the $1 cap.** Video 05 reached EOF but inspected 4/10 intervals (five skipped by Live backpressure, one preflight failure); its 12 generation requests cost $0.018253. Video 06 inspected 1/20 intervals and stopped near 46 seconds after an SSL generation failure; four completed requests cost $0.005343 and one $0.009001 reservation remains unknown. The sole queued safety assessment was cancelled before dispatch. No video passed complete review acceptance, and 09/11/13/14/18/19 plus the unnumbered forklift video were not reached in this paid batch. Contact sheets for all nine are local decoding checks, not hosted accuracy results. No new balcony-impact accuracy result exists.

New testing committed **$0.032597** ($0.023596 calculated + $0.009001 unknown). Including previous balcony commitments, **$0.167405** is committed under $1; **$0.832595** is unspent. The historical $0.009500 reservation remains unchanged. The batch is closed and all jobs/calls are terminal. There was no paid retry, estimated reconciliation or model fallback. Six subsequent read-only model-access/image-count probes passed; they do not establish a generation connection repair. The precise historical SSL cause remains unconfirmed. New sanitized TLS reason/library diagnostics are backward compatible; API types were regenerated.

The browser test also found that attempting to pause a cancelled Live run blocked History navigation. Pause is now a read-only no-op for authorized terminal Live runs, without affecting another attached camera. Navigation passed after reload. Activity uses light-theme colors and includes real observer calls. The coverage control sought and paused at exactly 40 seconds; all 156 retained frame hashes/timestamps matched fresh source decoding. Specialist completion and supported-finding navigation were not established by this batch. Final offline verification: **560 Python tests, 101 frontend tests, contract check, production build and all five isolated fixtures passed**, with zero fixture provider or training calls.

[HTML handoff](../data/artifacts/reports/numbered-live-20261024-2/index.html), [verification](../data/artifacts/reports/numbered-live-20261024-2/verification.json), [request receipt](../data/artifacts/reports/numbered-live-20261024-2/execution-receipt.json). The handoff preserves exact request/reservation IDs, source hashes, evidence reports, failures, coverage gaps, costs and prioritized repair actions. Resolve request-specific accounting and transport reliability before a newly funded continuation; do not reopen this closed batch or reset unknown charges.

The creator authorized sequential paid sample-video testing under $1. `scripts/sample_live_batch.py` freezes numeric order and source hashes, registers idempotent Live jobs, and exports offline-readable HTML/JSON. The batch includes $0.134808 of prior balcony commitments, leaving $0.865192 for new work. Only the unchanged $0.009500 historical unknown is included; any new unknown stops the batch. Atomic reservation checks enforce both aggregate and per-member allowances across restarts, alongside existing daily/monthly/workflow limits. Enrollment waits for prior root/observer/team jobs. No automatic paid transport retry or model substitution was added.

Live Activity now includes review-scoped `live_screening` jobs, while excluding another site's jobs. Thus real observer requests/tools appear alongside safety-team activity. The operator script defaults to local status/report actions; submit creates a paused camera and play explicitly starts causal playback. New testing results and limitations will be appended here. Initial verification: 557 backend tests (plus the added Activity scope regression), 101 frontend tests, contract check, production build and all five isolated fixtures passed without provider calls.

The safety reviewer is now a PydanticAI boss with a `delegate_specialist` tool.
The observer establishes visible evidence first. The boss delegates applicability
and focused technical questions, leads causal investigation, and synthesizes typed
assessments. An independent challenger checks every finding and causal hypothesis.
A human makes the final review decision.

The initial [authorized balcony test](../harness.md#balcony-hosted-test-acceptance-failed-2026-10-24) failed detection and full team completion for $0.011804. No unchecked assessment was released; original failures and charges remain recorded. The subsequent [repair](../harness.md#temporal-sequence-and-specialist-contract-repair-2026-10-24) adds separately checkpointed six-image temporal packets at 100 ms candidate cadence with high-detail images and a new agent finding contract. New specialist/boss answers leave legacy `recommendations` empty, put concrete evidence-backed safety actions in `corrective_actions`, and put questions for missing facts in `missing_context`. Applicability can complete with checks and gaps without inventing a finding. The boss now selects validated finding IDs; Python copies the approved content without paraphrasing it. Omitted event IDs are bound to the assigned event, with foreign IDs rejected. A missing duplicate condition/conclusion is filled verbatim from its counterpart. Causal hypotheses with missing decisive facts remain unresolved and do not establish causation. Old stored findings remain readable. Source, legal, action-specificity and independent-challenge guards remain enforced. Latest verification passed 537 Python tests, 97 frontend tests, contracts, build and five no-provider fixtures. The real balcony retest remains incomplete: a later provider connection error left $0.009500 unresolved after $0.050304 known usage across the new task. Paid work stopped. A possible struck-by concern is retained, but accurate head-impact recognition and final hosted team completion are not claimed.

The [Harry Potter reference](https://github.com/zlisto/mult_agent_hp/tree/7d23b90c06f0396d7f8609088afc278c4fd087f1)
is a starter with placeholder agent/retrieval instructions. This implementation uses
its boss/specialist/chunk-retrieval pattern while keeping this project's four bounded
agent folders. Specialist profiles live inside `agents/safety_reviewer`; there is
no replacement framework, model fallback, autonomous specialist delegation, or frontend change.

## Ownership

| Profile | Primary questions |
| --- | --- |
| applicability | Activity, employer/worker coverage, jurisdiction, event-date text, General Duty Clause, reporting/recordkeeping, unmapped requirements |
| vehicles | Forklifts/mobile equipment, pedestrian interactions, cranes, rigging, loads, storage and related training |
| surfaces | Walking surfaces, slips/trips, ladders/stairs, platforms, collective fall protection, dockboards, excavation stability |
| machinery | Guarding, entanglement, pinch/cut/crush points, machine and tool operation |
| energy | Electrical installation/work, shock/arc hazards, servicing isolation and lockout/tagout for every energy source |
| health | Chemical/biological exposure, dust, noise, heat, radiation, ergonomics, hazard communication, process hazards, confined-space entry requirements |
| emergency | Fire/ignition controls, extinguishers, exits, alarms, evacuation, rescue readiness, first-aid readiness |
| ppe | PPE assessment, selection, fit/rating, condition/use, respiratory/electrical/personal fall protection |

`modules/safety_review/catalog.py` contains the shared ownership and citation registry.
One `question_id` has one primary owner. An event can have multiple distinct questions.
Training belongs to the equipment/task domain. Technical profiles can read supporting
text from another domain after explicitly broadening search, but a regulatory finding
must cite a mandatory clause assigned to that profile. Unknown standards stay unmapped
and discoverable; the applicability specialist identifies coverage gaps. This starter
mapping does not establish complete coverage of every industry or state.

## Delegation sequence

1. Observer returns scoped observations, frame citations and visibility limits.
2. Boss delegates applicability before technical review. The request can supply
   `activity`, `event_date`, `jurisdiction`, `state`, `employer_type`, free-text context
   and specific curated excerpt IDs. Supplied context is not independent proof.
3. Boss assigns selected observation IDs and one precise question to each needed
   specialist. Do not call every profile by default. Python rejects foreign
   observations, owner changes and repeated dispatches.
4. Python prepares one bounded exact source packet from local indexed search when
   there is a candidate. Specialists can discover/fetch more evidence within limits.
   No raw image packet or entire conversation is forwarded to technical specialists;
   they receive observer evidence references and relevant observations.
5. Boss may request a targeted `follow_up` with the same owner/question and latest
   `previous_call_id`. Latest completed assessments supersede older answers to that
   question; all attempts remain audited.
6. Boss synthesizes returned findings. It cannot invent or substantively rewrite a
   specialist finding, discard a distinct finding, or hide outstanding source gaps.
7. Challenger checks every finding ID and hypothesis ID. Causal issues produce a
   final unresolved hypothesis and `not_established` cause status. Proposed safety
   results remain separately recorded alongside final `review.investigation`.
8. Coordinator commits completed results/cache together. Failed required delegation,
   cancellation, missing evidence or exhausted budgets cannot publish a completed review.

Limits are configured under `review_team` in `configs/agents.yaml`: eight total
specialist calls, two calls per question, 64 physical workflow requests, and 2,400
serialized bytes per exact source packet. Existing per-run tools, requests, tokens,
USD caps, 30-second physical request timeout, and 90-second workflow timeout still
apply. Boss orchestration shares the remaining workflow deadline; each delegated call
is bounded by the agent/tool deadline. Calls are sequential under the existing project
request slot. No automatic budget increase or provider/model fallback is allowed.

## Efficient source tools

| Tool | Purpose |
| --- | --- |
| `delegate_specialist(task)` | Boss only: typed question/owner/observation IDs, initial assessment or targeted follow-up |
| `search_safety_sources(query, jurisdiction, topic, limit, broaden)` | Specialist: at most five discovery previews, exact-citation lookup first, otherwise FTS5/BM25; no citation permission from previews |
| `fetch_sources(source_ids)` | Specialist: exact clauses plus parent context and one-hop related context, without truncating requirements |
| `get_site_rules(site_id, topic)` | Specialist/challenger: matching versioned site rules; no invented thresholds |
| `read_source(source_id, section)` | Challenger: assigned exact excerpt subject to evidence size limit |
| `get_evidence` / `sample_frames` / `get_camera_context` | Observer/challenger evidence functions retain their existing scope and camera limits |

Source index tables and triggers are derived SQLite data. Original source objects are
unchanged. Inserts/updates/deletes refresh the index and revision; caches are keyed by
revision, site, assigned IDs, jurisdiction, domain, date and query/packet budget. Cache
objects are copied to prevent callers changing shared evidence. Runtime searches do
not load/re-tokenize the full source table. The local FTS5 index uses Porter/Unicode
normalization and a small reviewed alias map, including forklift, LOTO, egress and SDS.

Discovery previews are capped at 160 characters and are not exact evidence. Only
prepared/fetched text enters the seen-source set. Empty results stay gaps; explicitly
broaden domain search without broadening site/document permissions. Date filtering
excludes known out-of-range versions and preserves unknown effective dates as gaps.
No state or historical rule resolver is implemented.

Exact evidence packets keep complete paragraph/table units and stable original
versions. Parent dependencies can recurse; neighboring context is one hop so a read
does not expand through the entire document. Missing, oversized, out-of-scope or
unverified context becomes a gap. The tool never cuts off an exception to fit a token
budget. Source relationships and verbose document metadata remain on the backend;
model packets contain compact citation/provenance/text fields.

## Specific findings and genuine investigation

New `Finding` fields record classification, question/owner, the event-specific
condition, exact clause, element checks and corrective actions. Classifications are:
`supported_noncompliance`, `potential_violation`, `site_infraction`, `general_hazard`,
and `insufficient_evidence`. Existing records receive conservative defaults when read;
original historical JSON is not rewritten. Workflow status remains draft/reviewed/challenged.

A regulatory claim needs an exact fetched mandatory clause. Supported noncompliance
also requires established applicability, complete verified source context/date, and
supported requirement/condition/exposure/employer-knowledge checks. A claimed exposure
exception must be explained with the applicable source. Unknown necessary elements
make the assessment potential/unresolved. Guidance alone cannot establish a violation.
Site infractions need actual site-rule/procedure evidence. No findings does not certify safety.

`Investigation` records a frame-grounded source-time sequence, competing hypotheses,
supporting/contradicting references, missing decisive facts and distinguishing tests.
A regulation alone cannot prove an incident cause. Supported causal contributors must
come from specialist investigation; the boss must compare at least one alternative.
If evidence is insufficient, use `not_established` and specific inspection/record questions.
The system cannot establish hidden training, load weight, verified isolation, diagnosis,
employer knowledge or unobserved actions merely from footage. Root-cause accuracy still
requires reviewed site evidence, records and held-out evaluation; fixtures verify workflow
behavior, not real-world safety judgment.

Corrective actions identify the demonstrated failed control, observed evidence,
specific action and verification. Recommendations without these records are rejected.
Prompts and the challenger check boilerplate and unsupported blame. Deterministic
validation cannot prove semantic truth or eliminate every generic phrase.

Within an episode, duplicate condition/classification/clause/rule/frame combinations
must be consolidated. Distinct requirements and evidence intervals remain separate.
Nothing suppresses a genuinely recurring hazard in later episodes. Review caches include
source/rule/frame versions, registry, specialist prompts and workflow version.

## Recorded incidents and monitoring

Both paths use the delegated review after evidence retention. Monitoring still queues
CV/rule candidates and independent periodic observation. Sampling, occlusion, failed
stages and dropped windows remain coverage gaps. This is not a continuous safety interlock.
Retrospective review may need multiple bounded windows and records/interviews; complete
cross-window incident reconstruction is not implemented. Physical speed/distance needs
recorded calibration. No external messaging, machine stop, training or deployment tool
was added to the review team.

## Corpus and state extension

See [source library](../data/resources/safety/README.md). The additive importer builds clause
records and graph metadata from preserved originals, leaving ambiguous labels null.
Unknown clause effective dates and unverified scope/definition/exception context are
intentional gaps. An operator must establish them before supported legal conclusions.

Federal baseline assessment must disclose unevaluated state obligations until coverage
and an appropriate state overlay are supplied. State source records use their own
jurisdiction, effective dates and ownership; the federal originals remain unchanged.
The boss/applicability profile must distinguish federal baseline from governing state
requirements and coverage exceptions. This corpus is a starter, not every applicable law.

## Local checks

Run source integrity/conversion fixtures, indexed search scope/ranking/date/context
fixtures, adversarial evidence/causal/ownership tests, and `scripts/safety fixture-replay`.
The fixture now executes two real delegation tool calls using mocked PydanticAI models,
then independent challenge and human decision. No paid calls or cloud training are used.

## Chat Lab design selection

The creator reviewed three interactive mockups and selected Balanced comparison. The connected implementation below supersedes that design checkpoint. Original illustrative mockups remain as historical design artifacts; their metrics are not hosted measurements. See [the implementation record](chat-lab.md) for current behavior and verification.

## Text questions in Safety Chat

The Safety Chat tab uses a separate text-only mode inside the existing safety-review and challenge folders. The boss delegates applicability first through `delegate_chat_specialist`, then assigns focused questions to the same technical domains. Source search/fetch and site-rule tools keep the existing scope and evidence limits. Specialists return advisory claims and gaps, rather than incident findings. The independent challenger checks every claim ID; the UI displays issues beside the proposed answer. User descriptions remain unverified. No observations, incident findings or human decisions are created.

Conversations and completed responses persist separately from event reviews. `safety_chat` jobs run on the review worker with existing paid opt-in, deadlines, cost limits and append-only agent/model/tool auditing. One pending turn per conversation keeps history ordered. The tab shows actual run IDs, separate follow-up calls, tool inputs/results, source excerpts, errors, and a full completed-turn audit download. The explicit local fixture mode uses fixed mocked responses and real tools to demonstrate the plumbing without paid requests. See [the harness](../harness.md#safety-chat) for API routes, schema and history limits.

The selected turn's token/cost monitor updates every two seconds, showing reported usage, recorded charges, known pending/unresolved reservations and per-agent totals. Unknown data and partial traces are disclosed. The shared per-agent request allowance is ten; the workflow allowance is 64. Applicability questions stay separate from technical requirements. Existing output, token, tool, deadline and USD limits still bound every request.

[Output contracts](agent-output-contracts.md) separate structural schemas from evidence checks and Python bookkeeping. Python binds validated chat claim IDs and attaches all latest specialist claims and warnings to boss results. Cosmetic citation/reference formatting is normalized; unknown evidence, semantic claim changes and conflicting ownership remain errors. The complete final/challenge capacity matches 32 claims from eight bounded assessments. Substantive output failures expose the recorded validation reason after the bounded correction.

Hosted chat specialist requests preserve six completion slots. Reaching this boundary produces a clearly marked partial assessment with unresolved domain questions and no fabricated claims for deferred work. Boss synthesis and independent challenge still run under all remaining hard limits; completion is not guaranteed when another limit or provider error blocks them.

## Implemented Chat Lab workflow

Balanced comparison is selected and implemented behind `--chat-testing`. The dedicated `chat` worker executes one comparison job containing sequential Current (`boss-first-7`) and Optimized (`decisions-routed-7`) branches with one parent allowance. Current uses Responses routing and boss assignment/synthesis; Optimized batches independent scope, task, domain and freshness questions with the supported Decisions interface. Simple product/record tasks use one answering agent; safety resolves applicability, assigns bounded specialists and independently challenges the answer. Both branches validate citations and final scope before publishing text. Strict structured output contracts require citations for substantive claims, exclude requirements while applicability is unresolved, and exclude product/record categories from safety-only answers. The final challenger receives citations from the synthesized answer. Explicit 30-minute cache writes remain priced when the adapter retains aggregate writes but drops their lifetime split; audits mark the request-setting basis. Unknown or inconsistent charges still retain reservations.

Lab implementations remain inside `safety_reviewer` and `challenge_reviewer`; no fifth agent folder/framework/fallback is added. The coordinator records actual task handoffs, stage and read-only tool lifecycle in the existing append-only audit, exposed through authenticated reconnectable SSE. Native web search is a bounded Responses stage; final prose stays buffered. Frozen source/record inputs and prompt contents make saved comparisons explainable. Source, rule, product and saved-record retrieval cannot start analyses or modify findings/decisions.

See [Chat Lab](chat-lab.md) for exact cache keys/TTLs, pricing categories, source-index provenance, fixture behavior, operator evaluation and the retrieval-token reservation limitation. Historical mockups are retained separately; the design review checkpoint is complete.

## Recorded Review Ask (video-chat-1)

Recorded Review and History now share a durable evidence-linked question workflow with Chat Lab’s optimized execution services. The observer inspects bounded pixels, the safety reviewer uses scoped references, and the challenger sees actual cited images. Decisions handles routing and final scope. Production Ask starts only on Send, with $0.05 per turn and unchanged global daily/monthly caps. Chat cannot mutate reviews, human decisions or annotations. Audio is excluded. See [the implemented workflow, tool boundaries, research and acceptance gate](video-chat.md). No hosted video acceptance has been completed.
