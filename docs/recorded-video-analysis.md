# Recorded-video analysis

The shipped interface now opens **Preview**, with native local playback, video thumbnails, a person-detection confidence slider and live person boxes on the player. It starts no AI work and operates with the global AI switch off. **Live** and **Review** start full safety review when a video is opened; new runs save the chosen person threshold. See the [current user guide](../README.md#open-a-video-and-go) and [current harness](../harness.md#local-cv-preview-and-confidence-2026-10-23). The adaptive sandbox described below is preserved compatibility behavior and is not a current navigation destination.

## Historical adaptive sandbox

Use **Video sandbox**, the app's opening screen, for questions about an MP4. The screen belongs to the safety-review frontend module, with a separate workflow from event/facility safety review. Its optional second safety layer can create evidence-grounded safety findings; it does not create labels, server-side human decisions or training jobs. **Other tools** opens the existing modules and operator screens.

1. Select **Upload video** in the sandbox, choose from the library, or put a recording in `data/input/` and select **Refresh**. Uploads use the existing Monitoring upload route. A just-uploaded file has a browser-local preview when its codec is supported; unsupported preview does not block backend analysis. Existing library files show evidence frames after analysis rather than a full-video preview.
2. Start the API/frontend as described in the README, and run `./scripts/safety worker review`.
3. Use the suggested question, select **Summarize** or **Find changes**, or enter your own question.
4. Choose **AI analysis** and **Run analysis**, or **Free demo** and **Run free demo**. The demo tests the workflow with fixed responses; it does not assess AI accuracy.
5. Check the answer, independent checks and cited frames beside the controls. Select **Stop** to cancel the selected queued/running test; the global stop switch also cancels it. Coverage/limitations, previous tests and run diagnostics remain expandable. Saved backend results survive reloads and restarts.
6. Optionally select **Looks right**, **Something is wrong**, or **Cannot tell**, with notes. Personal checks append to this browser's local history by job ID. Reopening the test restores its latest check and notes. A storage error is shown instead of claiming a successful save; unreadable history is preserved. These checks do not approve labels, establish ground truth, alter AI results or enter the backend audit.
7. **Download test report** exports JSON with the completed analysis, evidence chunks and all personal-check revisions for the test. Clearing browser data or changing browser/origin loses access to browser-local checks; export them if you need a portable record. Backend results and audits remain on the server.

AI analysis uses the existing pinned model and paid-request guard. Recorded video uses the configured `monitoring_image_review.image_usd` allowance on a copied ledger configuration; day/month/workflow/agent spending, request/token/image limits, concurrency, cancellation and unresolved reservations remain shared. This allowance is an operator budget estimate, not verified image pricing. Missing pricing, missing credentials or disabled paid opt-in block dispatch. Submitting a question is the explicit trigger; playback, screen opening and always-on Monitoring do not submit recorded-video questions.

## Analysis method

Python first samples short windows distributed across the requested source-time interval. Each observation stage receives bounded timestamped stills (two by default with safety screening, at most six without it) and returns a compact summary, visible observations, frame references and uncertainty. These complete validated chunks are retained and indexed locally with SQLite FTS5/BM25.

The planner sees a compact catalog. Its audited `search_video_memory` tool retrieves up to three complete chunks from this analysis only. Catalog previews grant no citation permission. The planner can request one denser interval, compare two separated intervals, or finish with an answer. Python validates and executes these choices, then starts a fresh planner stage. Agents cannot delegate, open arbitrary files or expand the recording scope.

The final answer has at most six claims. All claims together can cite at most six distinct retrieved frames, further reduced by the configured frame limit. Their intervals must contain their cited source timestamps. An independent visual stage inspects every cited frame and checks every claim exactly once, plus the full answer narrative. Uncertain/unsupported checks remain visible. A completed result is marked **Supported within sampled evidence** only when the verifier supports the answer and every claim; this is a model assessment, not ground truth. Fixture results always need review. Failed verification saves no completed answer.

The method takes inspiration from [LensWalk's adaptive observation](https://github.com/likanchuan09171/LensWalk), [MMCTAgent's independent critique](https://github.com/microsoft/MMCTAgent), and [NVIDIA VSS's separation of video processing, retrieval and reasoning](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization). It is implemented in the existing PydanticAI folders. Those frameworks and service stacks are not dependencies.

## Configuration and limits

`configs/agents.yaml` sets `video_analysis.overview_chunks=4` and `adaptive_steps=2`. Python accepts 1–8 overview chunks and 0–4 adaptive actions. Short recordings use fewer overview windows. Every selected observation interval fits `limits.clip_ms` (10 seconds by default). A comparison uses two such windows and splits the frame allowance between them. Planner context resets between actions; visual frames do not accumulate across stages.

The existing 180-second workflow deadline covers decoding and agent stages, with worker lease heartbeats. Native local work is joined during cancellation, so shutdown can wait for an in-flight local operation to finish. Source PTS, file signatures and initial/final content hashes protect evidence provenance. Missing duration metadata, out-of-range intervals or changed sources fail visibly. Original footage and historical records are preserved. Derived frames are additive; trial reset retains media used by completed answers or partial chunks.

This uses sampled visual evidence, with explicit uninspected intervals on long recordings. It does not process every frame, analyze audio/transcripts, recognize identities, stitch tracks, build an entity graph, use embeddings, or search prior analyses. Sparse stills can miss short actions and cannot establish continuous movement or absence. Generic MP4 questions are supported, but semantic accuracy, latency and real provider costs need separate evaluation.

## Offline verification

```sh
./scripts/uv run pytest -q
./scripts/uv run python scripts/generate_contracts.py --check
./scripts/uv run python scripts/video_analysis_fixture.py
./scripts/safety fixture-replay
cd frontend
npm run build
npm test
```

The video fixture generates a synthetic 40-second MP4 in a new temporary root. It exercises broad scanning, inspection, comparison, real memory-tool calls and independent checking with fixed PydanticAI FunctionModel responses. It reports zero provider requests and does not answer the demonstration question or measure recognition accuracy. Production footage, labels, weights and credentials are not used.

## API

Authenticated routes are `GET /api/video-analysis/sources`, `POST /api/video-analysis`, `GET /api/video-analysis`, `GET /api/video-analysis/{id}`, and `GET /api/video-analysis/jobs/{id}/chunks`, `/audit`, `/audit/download`. Submissions take an opaque library `source_id`, `question`, optional `camera_id`, source-time `start_ms`/`end_ms`, `mode=hosted|fixture`, and `idempotency_key`. End defaults to the recording endpoint. Caller-supplied file paths and extra fields are rejected. Camera, job and result site scopes are enforced.

The review worker handles durable `video_analysis` jobs. Validated `video_chunk` and completed `video_analysis` records use `data/app.sqlite`; images use the existing content-addressed media store. The audit is append-only and redacted. Cancellation/failure retains chunks and traces; it does not publish an incomplete answer or automatically rerun a paid request. **Previous tests** shows the latest available video jobs among the existing 200-job listing. **Run details & troubleshooting** shows up to 1,000 audit rows and 200 inspection/tool detail entries, real agent phases, tokens/costs and worker help. Terminal-job audit download streams the full trace through a fixed sequence cutoff. New tests retain earlier runs rather than resetting them. A selected running test disables its run/setup controls to prevent accidental resubmission.


## Safety screening and specialist review

Safety screening is enabled by default in the sandbox. The observer makes compact PPE/access/stack/slip-trip checks; suspected conditions can queue the existing safety team after video review completes. The two layers share the original spending/request allowance. The video answer and safety judgments can finish separately. [The two-layer guide](two-layer-safety.md) describes crop/checklist tools, bounded routing, priority, source/urgency validation, reportable storage, metrics, recency and future report-delivery boundaries. The exported test JSON includes the current safety-layer snapshot and explicitly marks pending judgments. Full pipeline audit download waits for linked jobs to finish.


New full Review and Live runs use the versioned [temporal incident sampling](../harness.md#temporal-incident-screening-grouped-concerns-and-live-cv-2026-10-24) path: up to six frames around two separated local motion bursts per ten-second interval, an unchanged six-image maximum, and a 24,000 input-token stage cap within the original dollar allowance. Adaptive analysis retains the distributed two-frame screening defaults described above. The observer can report struck-by and fall concerns as well as routine conditions. Motion scores are not recognition, and exact contact, injury and scene continuity must not be invented. Live adds transient person boxes on exact arrived frames, while the issue feed groups repeated categories without altering evidence or decisions. Historical results are preserved and are not silently rerun.
