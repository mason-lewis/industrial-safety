# Recorded-video Ask

`video-chat-1` adds evidence-linked questions to recorded Review and History. It is assisted review, with visual sampling limits. It has not passed a human-labeled real-video acceptance study. Audio is not assessed. The original full review, its findings and human decisions are unchanged.

## Use

Open a saved recorded review and choose **Ask** beside **Findings**. **Ask about this moment** pauses the player and adds a removable source timestamp. **Ask about this finding** preserves the selected finding. Suggestions only fill the composer. **Send** creates one durable question and a separate allowance of at most **$0.05**, within the shared **$2 daily / $20 monthly** limits. Opening the panel, restoring History, seeking, inspecting thumbnails and exporting make no hosted calls.

The submitted question appears immediately. Progress reflects only actual work: Find evidence, Inspect, Check sources and Verify. The answer is withheld until validation. Approved blocks identify observations in frames, saved-review statements, source-backed or general guidance, and uncertainty; legal references are separate. Select a thumbnail to seek/pause playback and open the retained image. Available crop parents and frame-local person boxes remain attached. Detector scores are not safety-confidence scores.

**Activity** displays recorded assignments, handoffs, agent states and tool arguments/results, with execution and waiting time, token categories, calculated costs, reservations, unknown charges and correction metadata. Technical details are collapsed. Stop cancels waiting work and in-flight client requests; already dispatched work may incur charges. Conversations and evidence persist locally. The browser retains an ambiguous submission ID, so retrying it reuses the same turn. Restart the API and workers together after upgrading code or contracts.

## Workflow and scope

The production runner is `workflows/video_chat.py`; shared routing, bounded PydanticAI execution, local source access, web provenance, block validation, correction and accounting are in `workflows/answering.py`, `agents/shared/lab_release.py`, and `modules/safety_review/chat_activity.py`. Chat Lab's optimized path uses these same services (`chat-lab-14` after extraction); comparison execution stays separate and testing-only. There is no extra agent framework, model fallback or annotation-assistant participation.

Each turn freezes the authorized review, source hash, original findings/human decisions, reference snapshot, configuration, prompts, recent bounded conversation and selected focus. Prior answers remain saved records, not new observations or semantically cached final safety answers. Retained images from recent approved turns can be selected again; a new visual statement still requires fresh pixel inspection/checking.

1. Luna Decisions batches scope, task, domain and freshness checks. Product help and saved-record lookups take the shared direct answer path. Ambiguous or out-of-scope questions receive clarification or decline without an inspection.
2. The coordinator reads the selected review's context, saved observation search and exact authorized records, including linked human decisions. The inspection planner chooses saved frames first. If insufficient, at most **two intervals of ten seconds each** are decoded locally, with up to **five ordered frames each**. Missing targets remain gaps. Broad questions do not trigger a full-video rerun.
3. The video observer receives actual JPEG pixels and source presentation timestamps. It can request one bounded XYWH crop per stage and read existing frame-local person detections. Crops count toward the **six-image request maximum**. Chat does not run extra person inference. Missing detection records never mean zero people.
4. The safety reviewer explains observations and uses local references first. Multi-domain safety questions get bounded coordination assignments. Legal applicability must be established before a requirement can be released; missing jurisdiction still permits labeled general guidance. One native OpenAI `web_search` stage is available for freshness, missing sources or conflicts, when completion capacity fits. Search queries use a controlled generic topic vocabulary; no filenames, private scene details, person descriptions or site records are included. Source-attributed citation spans remain in a labeled source-summary bundle; they are not copied under every URL as though independently verified.
5. The independent challenger checks whole blocks and essential dependencies. Visual packets include actual cited images and crop parents, with at most six images each. Python rejects wrong/foreign citations, unsupported categories and unresolved requirements. Supported, independently publishable portions can survive other rejected blocks. A final Decisions scope check precedes release.

No tool changes a review finding, human decision, annotation or model. No tool starts training or another full review. Text inside images, filenames, documents, web pages and conversation history is untrusted evidence. Python enforces review/site scope, source hashes, frame membership, collection-specific access and resource limits. Prompts prohibit identity, intent, negligence, injury diagnosis, invented continuity, unsupported physical speed/distance and audio inference. These controls reduce exposure; they are not proof against all multimodal injection or semantic model errors.

## Accounting and reliability

All physical generations pass the same guarded provider boundary. Global hosted generation concurrency is one. A SQLite FIFO queue admits Responses, Decisions and image-token preflight, with cancellation and heartbeat expiry. Waiting is a visible state rather than a concurrency budget error. A turn has **120 seconds total waiting** (worker plus provider queue), **180 seconds active execution**, **12 physical model requests**, and **16 local/native tool calls**. Worker loss marks dispatched usage unknown, releases only undispatched completion holds and never automatically repeats the question.

A question retains its workflow ID and allowance across duplicates/reconnection. The runner holds $0.01 for checking/completion and $0.01 for one correction cycle, within $0.05, before optional inspection/search. Correction reuses existing evidence, without a new search. Recovery, malformed output, final-scope rejection, provider failure, timeout and unknown billing are explicit outcomes; no SDK/transport retry or model substitution occurs. If the service cannot finish safely within the allowance, the result is unavailable or an honest limitation, not an unchecked answer.

Image-bearing requests call PydanticAI 2.54's public `OpenAIResponsesModel.count_tokens`, which uses the SDK `responses.input_tokens.count` endpoint with the mapped image payload and tool/output schemas. Exact counts are cached for 30 minutes by payload hash, model/adapter and site. Failed preflight stops before generation. Python also checks image count, dimensions, bytes and total request limits. `store=false` and explicit 30-minute prompt-cache boundaries are set; no padding is added for caching. Exact retrieval/classification caches include site, permissions, review/context, source, policy, workflow and model versions. Explicit current/latest queries bypass web discovery reuse. Optional web work is omitted when it cannot fit conservatively alongside completion reserves.

Pricing is frozen with each request. Documented Luna Standard rates are **$0.10/M input and $0.50/M output**; configured cached-input and cache-write rates are recorded separately. Native search adds **$0.01/call**, plus reported content tokens. Search-content reservation is an estimate, not a provider-side hard cap. Actual image tokens are already part of provider input usage and are not charged a second image surcharge. Unknown/inconsistent usage remains reserved; if actual usage exceeds the reservation, the guard stops further paid work. The $0.05 admission limit is not a guarantee against provider misreporting or charges exceeding a provider-side estimate. Calculated accounting is not an invoice.

Only selected frames and relevant context leave local storage on Send. `store=false` does **not** guarantee zero provider retention; account-specific data controls and abuse-monitoring policies still apply. Original frames, model-sized image hashes, source hashes, decoded PTS, crop-parent links and inspection gaps are retained. Retained image hashes describe stored JPEGs, not raw uncompressed sensor data. Browser seeking follows the recorded timeline; retained pixels remain the authoritative evidence when browser playback cannot decode a format.

## Interfaces

Normal `scripts/start_app.py` starts the API, video review worker and dedicated `--kind video-chat` worker. `--chat-testing` additionally enables Chat Lab and its comparison worker; it is not needed for production Ask. Existing three frontend modules and four agent folders remain separate.

Authenticated routes under `/api/review-runs/{run_id}/chat`:

| Route | Purpose |
| --- | --- |
| `GET /` | Read the durable conversation; no job creation |
| `POST /turns` | Submit with an idempotency key and optional timestamp/finding focus |
| `GET /turns/{id}` | Typed status, validated blocks, evidence and accounting |
| `POST /turns/{id}/cancel` | Stop that turn |
| `GET /turns/{id}/events` | SSE with sequence IDs, `after` / `Last-Event-ID`, terminal drain |
| `GET /turns/{id}/activity` | Sanitized saved observable events |
| `GET /turns/{id}/frames/{frame_id}` | Exact authorized retained evidence |
| `GET /export` | Conversation, versions, evidence and sanitized events |

The API returns no credentials or hidden reasoning. Unchecked model output is excluded from activity responses. Append-only local audit remains available for investigation. Historical sessions, comparisons and records have additive defaults and are preserved.

## Verification and release gate

Run the documented root/backend tests, frontend tests, contract generation/check, production build and the four existing fixtures, plus:

```sh
./scripts/uv run --no-sync python scripts/video_chat_fixture.py
./scripts/uv run --no-sync python scripts/video_chat_evaluate.py
```

The new fixture decodes an actual synthetic H.264 recording, runs scoped inspection with mocked PydanticAI results, independently checks linked frames, restores history, streams events and exports. A separate real-SDK `httpx.MockTransport` test confirms image token-count payloads and accounting. These checks use no hosted inference or cloud training. They prove functional behavior, not model interpretation accuracy.

`configs/video-chat-evaluation-v1.json` defines **12 cases, repeated twice** across visible facts, small details, brief events, scene cuts, findings, human decisions, safety/current sources, occlusion, image injection and unsupported identity/speed claims. Bind real review IDs/source hashes/focus, collect a human labeler and expected facts/time ranges/boundaries, and inspect the footage before any paid run. The demo and fixture outputs are not ground truth. No labels or measurements are invented.

Operator-run acceptance requires explicit funding and resolved accounting. Reserve the full $0.05 for each admitted turn within that funding, stop on unknown charges, retain every export, and reverse/repeat case order against frozen inputs. Note live-web evidence differences. Supply human-rated results to `scripts/video_chat_evaluate.py --results results.json`; the script is offline and reports missing coverage, supported claims, missed relevant events, timestamp errors, boundary handling, latency and calculated cost by category. Generic limitations do not count as useful answers. The gate requires at least **95% useful responses on answerable cases**, no unsupported critical assertions, no unexpected technical failures and correct boundaries across the full labeled set. Incomplete or unlabeled coverage cannot pass. Report the actual sample sizes; this is not a general accuracy guarantee.

**Human-labeled hosted video acceptance: 0/24 completed.** The earlier Chat Lab acceptance remains incomplete after a Decisions service failure, with its historical unknown reservation preserved. Its prior funding was not reused for Ask.

On 2026-10-24, the creator separately authorized a low-cost forklift Ask test. One question on `18-warehouse-forklift-traffic.mp4` completed in 55.5 seconds of execution plus 0.9 seconds waiting, for $0.003437955 calculated usage ($0.003442 rounded ledger), with eight model requests, seven tools, no web search and no outstanding/unknown charges for the turn. A fresh unreviewed six-second context avoided spending on a full review; its cancelled original job contains no invented findings. The planner inspected five frames between 2.5 and 3.5 seconds. A contradiction triggered the single correction cycle before two independently approved visual blocks were released. Assistant inspection found the qualified descriptions consistent with the cited pixels. All retained hashes matched fresh source decoding; all four released links sought/paused correctly, and History restored the result without another request. Live nodes/tools were observed before completion. Aggregate timing and correction totals update at completion; node timers run live. [Receipt and limitations](../data/artifacts/reports/forklift-ask-smoke-20261024/verification.json). This smoke check is not a human-labeled case in the acceptance matrix and establishes no general accuracy rate.

## Research basis

- [Luna model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna): image input supports frame-based inspection; raw video/audio are not this model's supported input path.
- [NOAH](https://arxiv.org/abs/2511.06475) and [VERHallu](https://arxiv.org/abs/2601.10010): reported temporal/continuity hallucinations motivate closer sampling and independent pixel checks. They do not validate this implementation, establish a safe sampling density, or prove Luna accuracy on these recordings.
- [PyAV timing](https://pyav.basswood.io/docs/stable/api/time.html): source PTS multiplied by time base underlies evidence timestamps, including variable-rate/nonzero-origin recordings.
- [PydanticAI advanced tools](https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/): `ToolReturn` supplies structured crop metadata plus `BinaryContent` pixels.
- [Native web search](https://developers.openai.com/api/docs/guides/tools-web-search), [token counting](https://developers.openai.com/api/docs/guides/token-counting), [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) and [pricing](https://developers.openai.com/api/docs/pricing): API and accounting basis; pinned SDK behavior is also checked offline.
- [OWASP prompt injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/): multimodal evidence needs enforceable tool boundaries beyond prompt instructions.
- [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data) and [evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices): retention statements and task-specific repeated acceptance checks.
