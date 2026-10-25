# Chat Lab

Start `./start --chat-testing` and open `/test/chat`. This explicitly sets `SAFETY_CHAT_TESTING=1` and starts a dedicated chat worker alongside the video worker. Without the flag, the testing page and API are unavailable. Use `--offline --chat-testing` for development fixtures. Opening the page, selecting a case or opening a saved comparison starts no work. Hosted Run requires explicit selection plus the existing credentials, AI switch and budget gates.

## Execution and task boundaries

Current versions are `chat-lab-13`, `boss-first-13`, `decisions-routed-13`, and `scope-4`. PydanticAI 2.54.0 owns execution; OpenAI SDK 3.26.0 supplies Responses and the supported Decisions interface. Every hosted language call uses `gpt-6-luna`. The three frontend modules and four bounded agent folders remain unchanged.

Both workflows use the same corrected roles and block validation. Typed execution context separates product help, saved records, general safety and regulatory assessment. Product/record agents receive no safety profile. Tools bind the permitted collection in Python; the model cannot redirect a product search to regulations through a collection argument. Read cache keys include the assigned collection. Oversized or unavailable requests return bounded evidence gaps rather than a tool crash; extra IDs are not read. Safety reference retrieval includes site rules. Planning assigns work without replacing the original question.

Current uses Responses scope/routing, task coordination, assigned answering agents, synthesis, local validation, independent review and final Decisions scope. Optimized batches scope/task/domain/freshness in Decisions and directly answers straightforward product or record questions. Multi-domain safety questions use coordination and at most three specialists. Applicability assessment runs for regulatory questions; ordinary safety education does not require it. The scope threshold remains 0.8. Uncertain task/domain uses bounded assignment; uncertain freshness requires discovery. Typos, criticism, quoted attacks and legitimate emergencies are not automatic rejections. Mixed or genuinely ambiguous requests ask for clarification.

## Answers, evidence and recovery

The model supplies ordered answer blocks, not a separate narrative plus claim list. Python renders only independently approved blocks. Each block has an evidence category, citations and any essential dependencies. Product facts require product documentation; saved-review facts require authorized records and linked human decisions. Requirements require an exact stored clause, complete dated regulatory context and established applicability. Specific medical-treatment instructions require appropriate supporting evidence.

Clearly labeled **General guidance · not verified against retrieved sources** can use model knowledge for ordinary safety education. It cannot assert legal obligations, product/record facts, site-specific guarantees, machine-operation/repair procedures or specific treatment. Basic escalation to trained responders and keeping clear of active hazards are allowed. Missing jurisdiction limits legal assertions, not all useful guidance. Uncertain blocks express actual gaps rather than unsupported instructions.

Independent review checks every exact block ID and whether it can stand alone. Local category/citation checks and dependency closure remove defective blocks and anything needing their qualifications. Global scope/uncertainty defects withhold the affected answer. Wording preferences and missing optional context do not block release. Limitations are deduplicated, with the first three visible and the remainder expandable in pages. Useful reviewed portions may be released as partial; no substantive blocks produces a clarification/evidence limitation rather than a software-crash message. Final scope still gates substantive release.

Each branch permits **one recovery cycle** for malformed output or concrete validation/review defects. The same model receives bounded relevant evidence and exact feedback. It may make one corrective generation, one independent recheck when needed and the final scope check. No new web search or local tool call occurs in the corrective generation. SDK and PydanticAI automatic output retries remain zero. There is no automatic retry after provider refusal, authentication failure, cancellation, ambiguous transport, unknown accounting or budget exhaustion. An initial user-scope rejection is never retried for permission.

Recovery reserves $0.01 inside the existing $0.15 parent allowance before starting. A held ledger reservation funds corrective requests atomically; unused funds are released and unknown child charges remain reserved. Recovery requires at least 45 seconds remaining on the shared 180-second comparison deadline. Global day/month/request/time limits still apply. Recovery attempts, issue codes, duration and actual accounted cost are visible in live telemetry and exports. A fully accounted local output failure permits the second branch to continue; shared provider, cancellation and financial failures stop it and label it **Not started**.

Outcomes (`answered`, `partial`, `clarification`, `declined`, `unavailable`) are separate from execution status. Raw exceptions do not become answer prose. Technical details are sanitized and expandable. Old saved results use additive defaults and retain their original content.

## Knowledge and provenance

Each comparison freezes site-authorized references, rules, allowlisted product documentation and saved reviews with original uncertainty/human decisions. Search returns bounded matches; read returns at most five whole blocks, omitting a block over 8,000 bytes with an explicit gap. Up to ten linked human decisions accompany records. Tools cannot start analysis, change findings, approve decisions, write records, read arbitrary files or access credentials.

`./scripts/uv run --no-sync python scripts/build_chat_index.py` builds additive `title29-osha-v2` from the preserved full Title 29 XML: 1,475 OSHA Chapter XVII sections and 49,109 whole blocks across all four segments. The original snapshot hash is retained. Effective dates, complete context and ambiguous paragraph labels are not inferred. Missing licensed standards, historical versions and jurisdiction remain explicit gaps.

Native OpenAI `web_search` runs through Responses for current information and missing/incomplete safety references, preferring configured primary authorities. There is at most one native search stage per branch. Explicit latest/current questions bypass the five-minute discovery cache. Provider URLs, retrieval dates and valid citation spans are retained. An inseparable multi-source summary remains one labeled `web_summary` bundle; copying it under each individual URL would imply false attribution. Citation spans identify only the text they actually cover. The summary is never exact regulatory text. Differences in live web evidence are recorded and must not be described as routing-efficiency gains.

## Live activity and costs

One durable comparison job owns the shared $0.15 allowance; existing $2 daily and $20 monthly limits remain. Branches run sequentially in the selected order. Queue time and execution time are separate. The balanced page shows live agent starts/finishes, coordinator handoffs, Responses versus Decisions, native/local tools, correction and rechecking. Links show observable task/context handoffs, not hidden reasoning. Selecting a step opens arguments/results, time, token/cache categories and cost. Draft output is held until release checks complete.

Authenticated SSE reconnects by audit sequence and never submits work. Saved IDs remain in URLs; JSON export includes versions, usage, outcomes and sanitized events. Ratings append new records without rewriting findings. Cancellation reaches provider tasks; incomplete usage stays reserved. Historical data, footage, annotations, models and credentials are preserved.

Stable instructions/tool definitions precede changing context with supported explicit 30-minute cache boundaries. Prompts are never padded. Exact local caches include site, permissions, source snapshot, policy, workflow, model, context, date and fixture/hosted mode. Retrieval/classification TTL is 30 minutes; ordinary web discovery is five minutes. Final answers are not semantically cached. Each specialist sees its assignment and relevant evidence.

Pricing basis checked 2026-10-24: Luna input $0.10/M, cached input $0.01/M, 30-minute cache writes $0.125/M, 12-hour writes $0.25/M, Responses output $0.50/M; Decisions output is uncharged. Above 272,000 input tokens, input categories use 2× rates and output 1.5×. Native search costs $0.01/search action plus content input tokens. Open/find actions are not extra search fees. PydanticAI's omitted cache-write split can use the known uniform request lifetime, explicitly labeled in audit. Unknown tiers, lifetimes or inconsistent usage remain unpriced.

Native search reserves a fee plus an estimated 32,000-token retrieval allowance. This is not a provider-enforced retrieval cap; an actual overrun triggers the existing stop and reconciliation. Calculated usage, outstanding reservations, unknown charges and unavailable provider invoice totals stay distinct. Limits govern admission and cannot reverse already incurred provider charges. No prewarming, background comparison or model fallback is added.

API references: [Decisions](https://developers.openai.com/api/docs/guides/decisions), [web search](https://developers.openai.com/api/docs/guides/tools-web-search), [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching), [pricing](https://developers.openai.com/api/docs/pricing).

## Verification and evaluation

A valid scope block or clarification ends work without requiring task, domain or freshness classifications that will not be used. Their refusals remain in diagnostics. A refused or ambiguous scope check, or incomplete allow route, cannot grant permission and is never retried automatically. The 0.8 confidence threshold remains; a low-confidence terminal decision asks for clarification.

```sh
AI_ALWAYS_ON=false AI_ENABLED=false HOSTED_IMAGES_ALLOWED=false SAFETY_PAID_REVIEW=0 OPENAI_API_KEY= ./scripts/uv run --no-sync pytest -q
./scripts/uv run --no-sync python scripts/generate_contracts.py --check
npm --prefix frontend test
npm --prefix frontend run build
./scripts/uv run --no-sync python scripts/chat_lab_fixture.py
./scripts/uv run --no-sync python scripts/course_fixture.py --output /tmp/course-receipt.json
./scripts/uv run --no-sync python scripts/video_analysis_fixture.py
./scripts/safety fixture-replay --output /tmp/safety-replay
```

All four fixtures use isolated roots. They establish function, not hosted semantic quality. Regressions replay the actual Preview failure and earlier malformed/review/accounting failures, exercise real retrieved product documents and task profiles, source ownership, general labels, partial dependencies, human uncertainty, correction exhaustion, cancellation and branch continuation. Mock transports test actual SDK interfaces without network access. No cloud training is used.

`configs/chat-lab-evaluation.json` versions 12 cases: product help/paraphrases, emergencies, regulatory/current information, saved records, mixed requests, quoted attacks and prohibited requests. Run each in both orders. `scripts/chat_lab_evaluate.py` is dry-run by default. Only after operator authorization:

```sh
.venv/bin/python scripts/chat_lab_evaluate.py --execute --allowance-usd 0.50 --output data/artifacts/reports/NEW-UNIQUE-EVALUATION-DIRECTORY
```

The runner saves the corpus, pending idempotency key, every export and a receipt. It admits a comparison only when its full existing parent allowance fits the remaining batch budget, using actual rounded ledger commitments. It stops on unknown/unsettled charges, unexpected technical failures or the cap. It does not resend an ambiguous submission or automatically restart a stopped batch. Before any authorized continuation, inspect the receipt and reduce the remaining allowance by all previous commitments. Unfinished coverage is explicit.

Review task-specific usefulness and source support, not just terminal status. A generic limitation does not count as useful. Measure false rejection/acceptance, citation/source coverage, unresolved claims, human ratings, recoveries, latency, tool calls, token categories and cost separately. Human ratings are never inferred from fixture success or automated checks. Acceptance targets: no unexpected technical failures in the completed live set, correct boundaries, no unsupported legal/record assertions and at least 95% useful responses on answerable cases. Repeated evaluations are necessary; [OpenAI evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices) explains why task-specific evaluation matters.

Version 13 passed 493 Python tests (114 Chat Lab), 90 frontend tests, contract checks, production build and four isolated fixtures with zero provider or cloud-training requests. The first final-version hosted comparison declined correctly in Current, then Decisions returned HTTP 503, “Decision admission timed out,” before usage/classification. Testing stopped without retrying; 23 of 24 final-version comparisons and all reverse-order coverage remain unrun. The final 95% useful-response target and human ratings are unverified. Across versions 9–13, calculated usage is $0.226538785, rounded settled ledger cost $0.226673 and unknown reservation $0.001384, totaling $0.228057 committed against $0.50. Do not resume merely because funds remain: unknown usage requires authoritative reconciliation first. The [measured report](../data/artifacts/reports/chat-lab-reliability-v13-20261024/results.md) contains outcomes, cost and unfinished coverage; [JSON metrics](../data/artifacts/reports/chat-lab-reliability-v13-20261024/verification.json) include per-agent/API timings, token categories, recovery costs and source measures. Historical receipts remain intact. The preliminary passing examples do not establish final reliability or savings.

Provisional guidance that cites a retrieved web-summary bundle can retain an uncertain review verdict when the reviewer explicitly finds the qualified block independently publishable and identifies no blocking defect. The source warning stays visible. This exception does not apply to legal requirements, product facts, saved-record facts, or specific medical-treatment instructions. Read-tool schemas advertise the five-source limit; an excessive or unavailable request yields bounded evidence gaps rather than a crash.

## Shared answer services (version 14)

`chat-lab-14`, `boss-first-14` and `decisions-routed-14` use `workflows/answering.py` and `modules/safety_review/chat_activity.py`, shared with production [recorded-video Ask](video-chat.md). Provider admission now queues fairly; a busy slot is waiting rather than a budget error. Exact evidence-category checks keep visual citations separate from safety guidance. The Lab remains testing-only, with the existing shared $0.15 allowance. No new hosted acceptance was run; the version-13 report and unknown Decisions reservation remain historical evidence, not validation of version 14.
