# Scene review implementation report

## Confirmed original path and limitations

The original path was PyAV BGR decoding using presentation timestamps → source-PTS FPS sampling → RF-DETR (BGR to RGB, confidence 0.5) → ByteTrack (activation 0.5) → configured person zone/line rules → retained original JPEGs → candidate scheduler → Pydantic AI Responses agents → SQLite records and React/WebSocket display.

Actual image bytes already reached the provider: `BinaryContent(image/jpeg)` becomes inline image data in Responses. There was no confirmed filename-only input fault, color conversion error, reversed frame order, or future-evidence tool-access fault. The existing tests checked these boundaries. The original default agent model was already `gpt-6-luna`, with reasoning omitted.

Confirmed constraints: live AI was ordinarily gated on CV candidates; periodic scenes were independently off and spaced 30 seconds apart. Initial packets used six frames from 12 seconds. A detector exception aborted `process_one` before retaining evidence and stopped file playback. Standard CV rules only considered people; the missing specialized classes were correctly declared unavailable. Four agents were available, and delegation tools were exposed by default. The queue held four older candidates rather than a newest-window slot. No persistent cross-scene event memory or price implementation existed.

High thresholds, broken tracks, sparse sampling missing specific incidents, and a different model/frame count/future footage in the user's direct-video comparison remain hypotheses. No supplied incident labels establish those causes. No detector training was performed.

## Implemented

- Separate CV only / AI scene review / AI + CV selection and explicit AI/hosted-image gates. Startup forces AI off even if `AI_ENABLED=true` or credentials exist. CV only blocks manual and retrospective language-model calls as well.
- Scheduled primary visual analyst with experimental defaults: 5 source seconds between reviews, preceding 15 seconds, approximately 1 full-scene image/second, at most 15 initial images. Source-time target selection, missing targets/gaps, startup coverage and actual input order are recorded. Full unannotated JPEGs remain separate from previews and derived crops.
- CV runs in its own one-active/newest-pending worker in AI modes. Failures and slow inference do not block playback/evidence. Frozen AI + CV packets expose per-frame detections/tracks, measured source timestamps, missing/stale CV status, zones and rule snapshots. AI scene input excludes CV, including tool access.
- Optional bounded extra frames from a frozen pool and crops; exact crop bytes, parent reference, bounds and hashes are retained. The same pool/policy is available to A/B arms.
- Typed observable findings, inferred risks, citations, severity, uncertainty, alternatives, missing evidence, change status and human-review reason. Explicit no-finding and inconclusive outcomes. Citation IDs and supporting timestamp bounds are validated before commit.
- Backend-owned stable event IDs; at most 40 hypotheses in memory, at most 20 sent per review. Overlapping identical subject/category findings update one event; explicit scoped prior IDs also continue events. Original assessments and append-only review updates remain stored. Leaving the frame is `no_longer_visible`, never resolved. Association with a CV candidate requires matching category, overlapping times and shared cited tracks; CV results persist independently.
- One active scheduled live analyst and one newest pending window. Scheduled/CV requests at the same cutoff coalesce; older windows are skipped. Seek/restart/source replacement and configuration changes invalidate pending scene work; generation/epoch checks reject late results. Pausing does not submit duplicate footage. Enabling does not drain a backlog.
- Responses adapter, no SDK retries, bounded analyst retries, output maximum 3,000 tokens, per-review and per-generation request/token caps, default delegation off. Configured-price estimates separate cached inputs and count output (including reasoning) once. Missing provider usage after cancellation/error leaves cost unknown. Dispatched attempts and reported usage are separate.
- UI keeps CV overlays/debugging and candidates; adds scene event/review inspection, exact prepared images/derived bytes, coverage, input CV, observations/risk/uncertainty, memory and previous/update states, settings, model, usage, latency and errors. Feedback is saved. Comparison export produces a ZIP of frozen packets and images without decoding/CV/model calls.
- `scripts/compare_scenes.py`: locally prepare/calculate CV once, or consume exported live cache; run A/B with independent answers/memory, fixed model/reasoning/schema/context/cutoffs/pools and image hashes. CV-triggered windows are excluded from base A/B and can form a third independent arm. Human labels feed the existing event evaluator (recall, false positives per camera-hour, duplicates and warning lead time); runner additionally reports inconclusive reviews, latency, usage and configured cost.

## Verification and limits

Verification completed: 85 Python tests, eight frontend tests, TypeScript/Vite production build, contract drift check, browser startup/mode-selection check, and six mocked A/B calls. Normal tests use mocked models. The real SDK/Responses HTTP serialization is tested with an in-memory transport for both modes: JPEG bytes, omitted default reasoning and 3,000 output-token cap. Tests cover disabled gates, no-CV scheduling, scoped/future/altered evidence rejection, overlapping deduplication, preserved original state, no false resolution, slow CV, detector errors, slow/coalesced reviews, cancellation, late generations, timeouts, sampling gaps, budgets and cache exports.

The available factory MP4 was decoded and RF-DETR Small ran locally on approximately one frame/second for ten source seconds. Cached weights were used; no detector download or model API call was needed. Three scheduled packets were reviewed separately in mocked A/B (six mocked calls). A fourth cached packet is a CV trigger, held out of base A/B. This checks the runnable pipeline, not accuracy. The factory and MOV clips have no incident/normal-footage ground truth supplied for this task. Accuracy and direct-video superiority remain unverified. No paid model test, live camera test or detector training ran.

Deduplication uses scoped prior IDs and conservative subject/category/time matching, not validated multi-object identity. A changed subject wording can split an event; visually distinct hazards need explicit separate subject descriptions. Memory eviction limits recall of older hypotheses, while saved assessments remain available for human inspection. Sampled gaps and CV cadence can miss short actions. Cached CV is synchronous at sample times; live CV may be missing/staler. Providers can report token usage above a limit on the last response; limits stop further requests, and the final overage is reported. Failed/dispatched calls with absent usage cannot have an exact cost estimate. Image retention can expire originals; inspection/export reports unavailable evidence.

## Run and compare

Start (from the repository root):

```sh
./scripts/uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
npm --prefix frontend run dev
```

Choose a source and analysis mode, read the displayed cadence, allow hosted images, then explicitly Enable AI. Mode selection alone starts no calls. Settings are in `data/config/lab.json`; local procedures in `data/procedures`; additional traffic routes/equipment/context in the versioned `site_context` config field. Configure price rates in `.env` only when you want estimates; absent rates remain unknown.

The prepared smoke cache already exists locally:

```sh
./scripts/uv run python scripts/compare_scenes.py run --cache data/artifacts/comparisons/factory-smoke --mock
```

For a fresh clip/cache (no language-model calls; `--with-cv` explicitly runs real local CV):

```sh
./scripts/uv run python scripts/compare_scenes.py prepare "data/input/Example of Hi-Definition Video Surveillance of a Factory Floor - by CCTVDOC.COM [8DJi4PrdFCg].mp4" --cache data/artifacts/comparisons/factory-new --seconds 30 --with-cv
./scripts/uv run python scripts/compare_scenes.py run --cache data/artifacts/comparisons/factory-new --mock
```

Alternatively, **Export comparison cache ZIP** from the UI and unzip it to a new directory. This preserves the exact packets/CV that live analysis could see. Both A/B arms see the same bytes and extra pool. No model answers are reused.

Explicit billable comparison (requires backend credentials in the environment; the runner does not silently load a private key):

```sh
./scripts/uv run python scripts/compare_scenes.py run --cache data/artifacts/comparisons/factory-new --allow-billable --allow-hosted-image --max-windows 3 --request-limit 18 --token-limit 100000
```

Add `--include-cv-triggers` for a separate third arm, with a corresponding explicit budget. The request/token limits cover all arms together; set enough budget for both base arms or results will visibly contain budget-exhausted windows.

Human labels go in cache `manifest.json` under `labels`, using the existing [event annotation format](evaluation.md): `id`, `source_id`, `camera_id`, `session_id`, `event_type`, `start_ms`, `end_ms`, optional `uncertain`. Copy scope IDs from packet records; use event categories consistent with the labeled taxonomy. Include representative normal footage and use actual source durations in `coverage`. Optional `incidents` specify an incident timestamp and pre-warning window. Feedback notes are review dispositions, not automatically treated as ground truth. Rerun comparison to score predictions, or evaluate an existing prediction export with the event evaluator without more model calls. Unknown-input whole-video reviews must be labeled uncontrolled retrospective baselines and cannot support a live A/B accuracy claim.

Verification commands:

```sh
./scripts/uv run pytest -q
./scripts/uv run python scripts/generate_contracts.py --check
npm --prefix frontend run build
npm --prefix frontend test
```

Provider capability reference checked: [GPT-6 Luna official model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna) lists image input and no native video input. The app therefore supplies timestamped images, never an MP4/RTSP URL.
