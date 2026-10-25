> This is the preserved feature guide from before the overhaul. Use the root [README](../README.md) for current setup, guarded review and shutdown commands. The default entry point is safety_app.api.main:app.

# Industrial Video Safety Lab

Local prototype for source-time person tracking, configured rule episodes, retained evidence, and optional hosted review. The startup mode is **CV only**. No language-model calls occur just because a key is present. This supports observation and human review; it has no machinery-control or notification tools.

## Where the trainer lives

The labeling and RF-DETR training workflow is grouped in [backend/app/trainer](../backend/app/trainer/README.md), with its UI in `frontend/src/trainer/` and regression tests in `tests/trainer/`. Start with the trainer README for a file-by-file map. COCO is the annotation format; RF-DETR is the model being trained. The trainer stays in this project root, and its footage remains separate from the analysis video library.

## Start

Requires `uv`, Python 3.12 (uv can install it), and Node 24 LTS. The prepared workspace has a local uv runtime via `./scripts/uv`; on a fresh checkout that wrapper uses your installed uv. The verified local setup uses Python 3.12.15 and Node 24.20.0. Run from this directory:

```sh
cp .env.example .env
./scripts/uv sync --frozen
./scripts/uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

In another terminal:

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Open http://127.0.0.1:5173. API docs: http://127.0.0.1:8000/docs. Development servers bind to localhost. Authentication must be added before a network deployment; deployment is outside this build.

Put clips in `data/input/`, select **Refresh directory**, then **Open session** and **Play**. RF-DETR Small downloads official weights on its first inference; this may take time and requires internet. Once cached, detection is local. Nano can be selected for speed experiments. CUDA/MPS is selected when available; CPU fallback is explicit and the actual device is saved in run provenance. No synthetic detections replace a model failure.

Use **Draw polygon** in the viewer, click vertices, and save. Bottom-center of each person's box is the image-plane rule anchor. The example restricted zone raises a candidate after 2,000 source milliseconds; 1,000 ms of observed absence from the zone clears it. Track disappearance records unknown clearance. Draw a directional line for crossing checks; direction is the sign of the oriented line's cross product, with a jitter deadband and cooldown. Frames, track IDs, and rule times are local to the session/generation.

Play/pause, step, source-time seek, restart and speed controls are available. Live simulation paces by source PTS and may run slower than 1× when inference is overloaded. Offline processing runs without pacing; it is separate from the annotation evaluator. Seek/restart and detector replacement invalidate overlays/tracks/rules and pending AI outputs. Old runs remain in saved history.

## Video player

Choose a clip from **Video library** and click **Open video** to preview its first frame. Use the integrated play/pause button, timeline scrubber, 10-second skip controls, elapsed/duration display, playback speed and fullscreen. Scrubbing resumes if the video was playing; speed changes preserve the paused state. Space or K toggles playback, arrow keys skip five seconds, and F toggles fullscreen when the player is focused. Double-click the image for fullscreen.

The viewer displays processed source frames with matching overlays, so its visual cadence follows processing throughput. It does not substitute independent browser playback for the source-time analysis clock. Seeking starts a new analysis generation. Live cameras show LIVE and disable seeking. Expand **Overlays & zone tools** to draw zones or adjust detection layers; advanced settings and diagnostics are under expandable sidebar panels.

## Download YouTube test footage

From the project directory, run:

```sh
./scripts/download_youtube "https://www.youtube.com/watch?v=VIDEO_ID"
```

The script automatically installs isolated downloader dependencies through `uv`, including bundled FFmpeg. It needs internet and Node.js 22+ (the project's Node 24 works) or Deno. It saves MP4 video with audio when available into `data/input/`, then prints the saved path. Click **Refresh directory** in the app to load it. Downloads default to at most 1080p, prefer H.264, skip existing files, and download only the selected video even when the URL includes a playlist. A source with no matching MP4 format fails explicitly.

Pass multiple quoted URLs to download several videos, or customize the resolution/destination:

```sh
./scripts/download_youtube --max-height 720 "https://youtu.be/VIDEO_ID"
./scripts/download_youtube --output-dir /path/to/clips "https://youtu.be/VIDEO_ID"
```

If YouTube changes break extraction, refresh the isolated dependencies with `./scripts/uv run --refresh --script scripts/download_youtube.py "URL"`. Downloaded footage has no evaluation labels; add annotations separately when measuring accuracy. Downloader reference: [yt-dlp documentation](https://github.com/yt-dlp/yt-dlp).

## Explicit AI opt-in and scene reviews

AI always starts disabled, even with a key or `AI_ENABLED=true`. Select **CV only**, **AI scene review**, or **AI + CV** independently of the **Enable AI** switch. Read the displayed cadence, allow hosted images, and explicitly enable AI. Selecting a mode starts no paid calls. Enabling does not replay a backlog.

Experimental defaults: review every 5 source seconds, preceding 15 seconds, approximately one full-scene JPEG/second, at most 15 initial images. This can mean up to 720 scheduled starts per source-video hour before coalescing/budgets. The controls expose interval, window, sample spacing, image caps and optional CV-triggered reviews. Short actions can be missed; these are prototype settings.

AI scene review excludes CV data from the analyst's input. AI + CV supplies timestamped detections, tracks, zone definitions and rule measurements, with missing/stale data labeled. CV candidates remain independently visible. CV runs asynchronously in both AI modes, so missing detections and detector failures cannot gate visual review or halt playback. CV-only mode submits no language-model requests, including manual reviews.

One primary Pydantic AI analyst uses `gpt-6-luna` through `OpenAIResponsesModel`. Model-default reasoning and standard mode are omitted from request fields. There is no automatic fallback. Specialists remain behind `AI_SPECIALISTS_ENABLED=true` for legacy/retrospective workflows; delegation is off by default. The scheduled analyst uses no delegation.

Inspect the exact prepared frames, timestamps, image hashes, CV metadata (when supplied), coverage gaps, observed conditions, inferred risks, uncertainty, model/settings, usage and previous/event updates. No-finding outcomes remain review records. Bounded memory treats earlier assessments as hypotheses; leaving the frame does not resolve an event. Generation cutoffs reject future and wrong-session evidence. There is one active scene review and one pending newest window; actual cadence and skipped windows are displayed.

Defaults bound each scene review to three requests, 16,000 tokens and 3,000 output tokens/request; the playback run has a shared 100-request/200,000-token ceiling, configurable through `.env`. Hosted errors/timeouts preserve CV results. Cancelling stops future requests, but dispatched calls can already incur cost. Actual input/output/cached tokens are reported when supplied by the provider. Cost is unknown unless all configured input/cached/output price rates are supplied. Output already includes reasoning tokens and is counted once.

Incident response review remains an explicit **retrospective** operation: its separate decoder may read the authorized pre/post window, and it never advances replay. Live scene tools only access the frozen packet/pool at or before their source cutoff. Markers establish incident time, not worker awareness.

Use **Export comparison cache ZIP** for frozen live packets, or prepare a local clip cache once with `scripts/compare_scenes.py prepare`. Base A/B uses the same scheduled packets with separate model answers and memory; optional CV-triggered reviews form a third arm. Mocked comparison on the locally prepared factory cache:

```sh
./scripts/uv run python scripts/compare_scenes.py run --cache data/artifacts/comparisons/factory-smoke --mock
```

See [the implementation report and complete comparison commands](scene-review-report.md). Billable runs require explicit `--allow-billable --allow-hosted-image` flags; no paid calls were used during this implementation. Human labels and representative normal footage are required before accuracy claims.

## Safety rule overlays

Open footage, choose a **Rule template** under the player, then **Draw new rule**. Drawing pauses playback. Click two endpoints for a doorway/line or polygon vertices around a zone. The canvas toolbar provides Undo, Save and Cancel; the editor below adds a site-specific name, detector class selection, direction, timing/count/distance thresholds, and AI review context. Saving adds an independent rule. Existing rules support editing/redrawing, disabling, and deletion; shared zones are preserved when a rule is removed or redrawn.

| Template | Deterministic observation |
| --- | --- |
| Doorway / directional crossing | A tracked anchor crosses the finite line in the selected geometric direction. `+` / `−` side labels follow endpoint order. |
| Restricted-area entry | A tracked anchor moves from observed outside to inside a polygon. Starting inside or reappearing after track loss is not an entry. |
| Machine / edge exclusion zone | A selected class is observed in a polygon for the configured duration. |
| Excessive dwell / loitering | The same zone-duration primitive, with a longer default. |
| Vehicle in pedestrian zone | An observed entry by selected supported vehicle classes. |
| Zone occupancy limit | Visible tracked objects exceed the configured maximum for the duration. |
| Object present in exit / aisle | A selected detectable object persists in the polygon; AI reviews whether it actually blocks passage. |
| Person–vehicle proximity | Selected tracked class pairs stay within a percentage of the image diagonal for the duration, scoped to a polygon or the whole image. |

Rules use bounding-box bottom-center anchors, source time and observed detections. A visible exit interrupts an untriggered duration; track loss resets untriggered state and leaves an existing episode's clearance unknown. Sustained rules require visible clearance before rearming. Entry/crossing rules use a repeat cooldown. Self-intersecting/zero-area polygons, degenerate lines, duplicate IDs and unknown zone references are rejected. Rule/geometry changes retire old episodes with explicit uncertainty. The current configured camera layout applies to opened sources; redraw overlays for a different view.

To route new episodes to AI, select **AI + CV**, enable **Rule-triggered AI reviews**, keep **Request AI for this rule** checked, allow hosted images, and explicitly **Enable AI**. Local rule evaluation needs none of the hosted gates. Each new eligible episode is submitted once; continuing, lost-track and clearance updates do not submit more requests. Multiple triggers from one frame share one review request. Scheduled reviews continue, busy windows coalesce, and request/token budgets remain in force. Prepared packets include triggering candidate IDs, named rule definitions, operator review questions, exact geometry and cutoff-scoped measurements. Candidate review status follows queued/running/completed review work.

Proximity is image-plane distance, not meters or a validated safe separation. Stock COCO classes include person/car/truck/bus, but not forklift, hard hat, vest, suspended load, spill or injury. The class selectors use the loaded detector's declared labels; those other classes require a validated custom detector before deterministic triggers can be built around them. A trigger records an observation for AI/human review, not an established hazard or policy breach.

## Verify and evaluate

```sh
./scripts/uv run pytest -q
./scripts/uv run python scripts/generate_contracts.py --check
cd frontend
npm run build
npm test
```

Normal tests prohibit real model requests; the Responses HTTP serialization test uses an in-memory transport. Pydantic schemas generate `frontend/src/types/contracts.ts`; regenerate with `./scripts/uv run python scripts/generate_contracts.py` after schema changes.

Real CV smoke (downloads weights if absent) and the billable model smoke are separate opt-in commands:

```sh
./scripts/uv run python scripts/smoke_cv.py --variant small
./scripts/uv run python scripts/smoke_ai.py --image /absolute/path/to/frame.jpg --allow-billable --allow-hosted-image
```

The second command sends the named image to the hosted provider and requires credentials. A failed/unavailable Luna request produces an explicit error; it never changes models. Neither command establishes site safety performance.

See [evaluation format and matching](evaluation.md). Example plumbing-only evaluation:

```sh
./scripts/uv run python -m backend.app.evals.events data/annotations/plumbing-example.json --output data/artifacts/reports/plumbing.json
```

Use camera/session/clip splits, quiet footage and difficult conditions; compare rules-only and AI scene / AI + CV on the same saved evidence, then evaluate CV-triggered reviews separately. Unlabeled runs produce no accuracy claims. Warning lead time is computed only for explicitly labeled pre-incident windows.

## Storage and configuration

`data/config/lab.json` stores normalized zones, rules, processing settings and detector selection. `.env` stores secrets and AI gates. `data/procedures/` accepts bounded local Markdown/text procedures. `data/artifacts/runs/lab.sqlite` retains sessions/configuration/candidates/assessments/feedback/agent traces. `data/artifacts/evidence/` stores bounded JPEGs and metadata. Retention can remove old evidence under the disk cap; the record remains and unavailable evidence is shown explicitly. `data/artifacts/models/` caches weights and model files. Treat these local artifacts as private footage.

See [architecture and tool matrix](architecture.md), [models and licenses](MODEL_MANIFEST.md), and [official sources](official-sources.md). The custom detector path is for future weights and label maps; specialized capability claims remain unavailable/experimental pending site validation. A forklift-only model is insufficient for person–forklift checks unless a separate person detector is retained.

## Troubleshooting

- No sources: check `data/input/`, supported extensions, and click Refresh. Paths are resolved server-side from discovered IDs; symlinks escaping the input root are excluded.
- No boxes: inspect source health and detector provenance. An empty real detection result is possible; rectangles in a plumbing video are not people.
- First frame slow: weights download/model warmup and CPU/MPS inference may be slow. Inspect measured processing age and inference latency. This build does not promise real-time throughput.
- AI unavailable: enable both gates and provide the backend key. Model access is checked by an explicit review request, not a startup or health request. Errors, deadlines, queue skips and token/image ceilings are visible in agent runs.
- RTSP: put the credential-bearing URL in backend `LAB_RTSP_URL`; refresh sources. Browser receives server JPEGs, never the camera URL. Reconnect uses bounded backoff. RTSP connectivity was not tested without a camera.
- Decode errors/missing PTS: supply a supported codec with presentation timestamps. The prototype rejects frames without PTS instead of guessing source time from FPS. Original clips are never transcoded or replaced.
- Apple AVFoundation duplicate-class warning: PyAV and OpenCV wheels both bundle FFmpeg components. Smoke tests completed on this host; investigate separate/headless environments if an affected host crashes.
- Cost: unknown unless all three configured price rates are supplied; cancelled/failed calls can lack usage. The model's self-rated confidence is uncalibrated and is separate from detector confidence/severity.

Computer vision now supports an industrial class allowlist, per-class confidence, explicit `vehicles` / `industrial_vehicles` rule groups, and class-separated ByteTrack controls. Groups retain raw subtype labels and do not create recognition for unsupported classes. See [industrial CV and labeling workflow](cv-industrial.md) and [adversary findings](cv-adversary-review.md). The app's Computer vision panel provides the prepared local footage labeling bundle and registered checkpoint selection; no trained forklift model or measured forklift accuracy is bundled.

Use **Label & train detector** above the video library for the built-in [labeling workspace](labeling-workspace.md). It uploads separate footage, supports draggable/resizable human boxes and explicit empty-frame review, freezes source-group train/validation/test partitions, launches local training jobs, evaluates a selected checkpoint and registers it for video review. Training receives no test files; opening test evaluation seals further training on that project. Exact analysis-video duplicates are rejected, but re-encoded copies and related scenes still require honest group assignments. No bias-free or forklift-accuracy guarantee is implied.
