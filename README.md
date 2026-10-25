# Industrial Safety

Industrial Safety is an AI review desk for workplace video: drop in a recording and a team of AI agents checks every 10-second interval for struck-by, fall, fire/burn, PPE, blocked-access, unstable-stack and slip/trip concerns. It shows the frames behind each concern and cites safety guidance. A person makes the final call, and the app exports a one-page report for managers. Spending is capped at $0.15 per run.

**Course deliverables:** [report.html](report.html) · [slides.html](slides.html) · [submission goals](docs/submission-goals.md)

## Quick start

You need macOS or Linux with `python3`, Node 22.12+ with npm, Git and internet access for the first install. The launcher downloads everything else: uv, Python 3.12, dependencies and the stock RF-DETR person detector. A first install takes about 1–3 minutes.

1. Clone the repository and enter it:
   ```sh
   git clone https://github.com/mason-lewis/industrial-safety.git && cd industrial-safety
   ```
2. Provide an OpenAI key for `gpt-6-luna`. The app reads `.env` from this folder first, then from the folder one level above it (the staff `.env` location). Only `OPENAI_API_KEY` is needed; see [.env.example](.env.example). Without a key the app still opens: Preview and person detection work, and AI review shows as unavailable.
3. Start everything with one command:
   ```sh
   ./start
   ```
4. Open **http://127.0.0.1:8000**. Click **Review**, then **Open video** and pick a clip from `demo/videos/`. `clip-001.mp4` is a good first choice. The review starts right away. Watch the **Findings** feed fill in, click a finding to see its frames and the team's assessment, record a decision, then click **View report**.
5. Stop with Ctrl+C.

**Test data:** `demo/videos/` holds 24 short CC BY 4.0 industrial clips (sources in [demo/README.md](demo/README.md)). To use your own footage, drop any MP4 on the **Drop footage** card or copy files into `data/input/`; no setting needs to change.
**No-cost mode:** `./start --offline` disables all hosted AI calls.
**Spending limits:** $0.15 per run, $2 per day and $20 per month, enforced before each model request.
**Different port:** `./start --port 8010`.

## Change log (newest first)

### Kitchen fire follow-up (2026-10-25)

Restart `./start` to load the parallel team: the kitchen fire run used the old backend, so its 9 case reviews ran one after another (777 s) and **View report** fell back to a download. Team reviews now start as soon as each episode is closed instead of after the whole recording, Live screens three sealed intervals at once (published in order; a failed interval no longer blocks later ones, and waiting intervals keep their lease), and read-only lookups can run together in one turn, with each role told which tools add value. The report summary now groups findings by concern type with a filmstrip, the clearest line and decision counts, lists concrete next steps, and uses thumbnails (the kitchen fire report went from 21.5 MB to about 1 MB). Live player time is now white on the dark player. A re-run took 271 s instead of 777 s. Since then, agents stop calling tools when another round would exceed their token allowance and answer instead, the combiner uses the run's own request cap, and up to 8 jobs and 8 AI requests run at once, so case reviews start as soon as they are ready. A continuing fire is now one case (joined across adjacent intervals whatever its box) instead of one per box, so the team reviews it once instead of ten times; the review starts when the fire stops or the run ends. An interval whose token count fails on a connection error, before anything was sent or charged, is retried once automatically. [Details](harness.md#kitchen-fire-follow-up-pipelined-team-live-concurrency-concise-report-2026-10-25).

### Faster team, manager report and team tools (2026-10-25)

**Report for managers.** **View report** opens the report inside the app (no download needed); **Download** and **Data (JSON)** remain. It now opens on a verdict ("2 priority risks need attention"), a short team brief, key numbers, "What needs attention" cards with evidence and the next action, recommended actions, and confidence and limits. Everything else (findings, agent team, Ask, coverage, sources) sits under **Full details for auditors**.

**Faster reviews.** Independent specialists now run in parallel once applicability is answered; up to three case reviews and three recorded-review intervals run at once (provider request slots raised from 1 to 3). Intervals are still published in time order. A new **team combiner** writes the manager brief after every case review finishes.

**New team tools.** *Case history* (earlier findings and human decisions of the same kind at this site), *person motion compare* (deterministic person-box movement and hazard-region overlap across frames) and a one-time *close-up crop* for the independent challenger.

**Agent tree.** Shows each agent's tool calls, how many agents work in parallel, packets moving along active handoffs, a live event feed, and tool/request ticks on the timeline. The player's time readout is now high-contrast. [Details](harness.md#faster-team-manager-report-and-team-tools-2026-10-25).

### Release sweep: agent team map and unified reports (2026-10-25)

**Agent team** (under the player in Live and Review, and inside each Ask turn's Activity) draws the live hierarchy: coordinator → video observer → detail observer / visual verifier → safety lead → specialists → independent challenger (Ask: planner → frame inspector → answer writer → verifier). Nodes pulse while working, active handoffs animate, repeated runs collapse into "×N", and timeline lanes show when each agent ran; click a node for its runs, tools, time and cost. Raw rows and JSON sit under **Technical log**.

**Reports** for Live and Review share one Hi-Vis HTML layout and a JSON file with the same content: summary tiles, coverage strip, findings with embedded evidence and decisions, the agent team tree, Ask questions and answers with cited frames, Live session state and skipped intervals, person detection, usage and sources. Use **Download report** (labelled "in progress" while a run is active) or **Data (JSON)**. A UI sweep fixed legacy styles leaking into the new screens, stopped-run and empty states, contrast, keyboard access and phone/tablet layouts. [Details](harness.md#release-sweep-agent-team-map-and-unified-reports-2026-10-25).

### Daylight Hi-Vis interface (2026-10-25)

The Preview, Live and Review screens use a new look chosen from a [mockup round](docs/design-mockups/index.html): bold uppercase type, safety yellow and red, on light cards. Every screen starts from a yellow **Drop footage** card (drag a file in or click **Open video**) and a grid of video thumbnails. Most explanatory text is gone or moved into the welcome guide, **Coverage details** and the finding dialog. Finding cards now show an evidence thumbnail, and decisions are one tap plus a required reason. Workflows, API calls, spending limits and saved records are unchanged. [Details](harness.md#daylight-hi-vis-interface-2026-10-25).

### Sequential Live sample testing (2026-10-24)

**Final batch outcome: stopped on video 06, before the $1 cap.** Video 05 reached EOF but inspected 4/10 intervals (five skipped by Live backpressure, one preflight failure); its 12 generation requests cost $0.018253. Video 06 inspected 1/20 intervals and stopped near 46 seconds after an SSL generation failure; four completed requests cost $0.005343 and one $0.009001 reservation remains unknown. The sole queued safety assessment was cancelled before dispatch. No video passed complete review acceptance, and 09/11/13/14/18/19 plus the unnumbered forklift video were not reached in this paid batch. Contact sheets for all nine are local decoding checks, not hosted accuracy results. No new balcony-impact accuracy result exists.

New testing committed **$0.032597** ($0.023596 calculated + $0.009001 unknown). Including previous balcony commitments, **$0.167405** is committed under $1; **$0.832595** is unspent. The historical $0.009500 reservation remains unchanged. The batch is closed and all jobs/calls are terminal. There was no paid retry, estimated reconciliation or model fallback. Six subsequent read-only model-access/image-count probes passed; they do not establish a generation connection repair. The precise historical SSL cause remains unconfirmed. New sanitized TLS reason/library diagnostics are backward compatible; API types were regenerated.

The browser test also found that attempting to pause a cancelled Live run blocked History navigation. Pause is now a read-only no-op for authorized terminal Live runs, without affecting another attached camera. Navigation passed after reload. Activity uses light-theme colors and includes real observer calls. The coverage control sought and paused at exactly 40 seconds; all 156 retained frame hashes/timestamps matched fresh source decoding. Specialist completion and supported-finding navigation were not established by this batch. Final offline verification: **560 Python tests, 101 frontend tests, contract check, production build and all five isolated fixtures passed**, with zero fixture provider or training calls.

[HTML handoff](data/artifacts/reports/numbered-live-20261024-2/index.html), [verification](data/artifacts/reports/numbered-live-20261024-2/verification.json), [request receipt](data/artifacts/reports/numbered-live-20261024-2/execution-receipt.json). The handoff preserves exact request/reservation IDs, source hashes, evidence reports, failures, coverage gaps, costs and prioritized repair actions. Resolve request-specific accounting and transport reliability before a newly funded continuation; do not reopen this closed batch or reset unknown charges.

The creator authorized sequential paid sample-video testing under $1. `scripts/sample_live_batch.py` freezes numeric order and source hashes, registers idempotent Live jobs, and exports offline-readable HTML/JSON. The batch includes $0.134808 of prior balcony commitments, leaving $0.865192 for new work. Only the unchanged $0.009500 historical unknown is included; any new unknown stops the batch. Atomic reservation checks enforce both aggregate and per-member allowances across restarts, alongside existing daily/monthly/workflow limits. Enrollment waits for prior root/observer/team jobs. No automatic paid transport retry or model substitution was added.

Live Activity now includes review-scoped `live_screening` jobs, while excluding another site's jobs. Thus real observer requests/tools appear alongside safety-team activity. The operator script defaults to local status/report actions; submit creates a paused camera and play explicitly starts causal playback. New testing results and limitations will be appended here. Initial verification: 557 backend tests (plus the added Activity scope regression), 101 frontend tests, contract check, production build and all five isolated fixtures passed without provider calls.

This local app is a single video safety workspace with **Live** and **Review** modes.
It uses React and TypeScript, FastAPI, PydanticAI 2.54.0, SQLite and local media.
It combines the video player, a standard pretrained person detector, an AI review team, retained evidence, incident reports and **Ask** inside recorded Review and History. There is no separate production chat, labeling, training or model-management workspace.

Start with [harness.md](harness.md). Then read [shared records](backend/src/safety_app/domain/models.py) and [the review workflow](backend/src/safety_app/workflows/event_review.py).

The developer-only [federal safety source library](data/resources/safety/README.md) contains original government
documents, Markdown transcriptions, provenance, and bounded curated-source export examples.
The [agent workflow guide](docs/safety-agent-workflows.md) describes source routing, finding
categories, tool purposes, post-event review, monitoring, and future state overlays.
The additive source importer builds indexed clause records from the library. Federal baseline review
must disclose unevaluated state applicability and missing event-date rule history.

### Connection stops and Live coverage (2026-10-24)

The SSL errors that stopped the paid Live batch come from this machine's network intermittently corrupting large (~800 KB) image uploads. Free token-count probes and curl reproduce them; small requests do not. When OpenAI's TLS layer reports a corrupted record (`bad_record_mac`) before any response, or the upload fails before it finishes, the request provably never arrived complete. It now settles at $0 and is resent once. Every other connection failure still keeps an unknown charge and stops the run. Live now queues up to 30 sealed windows oldest-first instead of keeping only the newest, so analysis can trail playback but no longer skips intervals. Use a wired or different network for paid testing, and check it first with free counts. Live batch 3 then completed full coverage on videos 06, 09 and 11. It recovered three such failures at $0 and stopped during video 14 on one unprovable broken-pipe charge ($0.586 of $1 committed). See [its report](data/artifacts/reports/numbered-live-20261024-3/index.html). See [the harness milestone](harness.md#corrupted-upload-diagnosis-unsent-request-proof-and-live-backlog-2026-10-24).

### Fire/burn screening and omitted categories (2026-10-24)

Reviews now have a **Fire or burn concern** category for visible flame flare-ups, smoke or burning material near people. Fire cases go to the existing fire-prevention (emergency) specialist; previously fire could only be filed as struck-by. When the observer omits a routine category, it is recorded as **not assessed** (unknown, marked as coordinator-filled) instead of losing the whole window. Neither change has been tested with a paid run yet. [Details](harness.md#omitted-category-fill-and-fireburn-screening-2026-10-24).

### Current recorded-review repair

Token-count preflight now recovers once from transient count-only failures, with exact counts, cancellation and a shared deadline. Paid-generation retries remain disabled. Six-frame packets support six local person lookups, evidence aliases remain scoped, original-pixel focused inspection can use saved person boxes, and validated stages can recover finalization without buying the same answers again. Activity shows actual requests and handoffs. See [the current harness milestone](harness.md#token-count-recovery-and-renewed-balcony-test-2026-10-24).

The renewed balcony review **finished both intervals and all five team assessments**, after repairs and explicit continuations of the same run. One struck-by card and one grouped trip card appear; the 5.3-second evidence control seeks and pauses correctly. **Impact-recognition acceptance still failed:** the final answer does not verify falling material hitting the worker's head/upper body. Challenged claims remain clearly uncertain.

This review used **$0.062057**. All six attempts total **$0.134808 committed**, including the unchanged **$0.009500** historical reservation, below $0.15. No new unknown charge was created. Its allowance is closed; no further paid test was started. Offline checks passed 555 Python tests, 101 frontend tests, contracts, build and five isolated fixtures. [Measured outcome and limitations](data/artifacts/reports/balcony-preflight-retest-20261024/results.md).

## Course app: Preview, Live and Review

The three destinations are **Preview**, **Live** and **Review**. Preview provides local playback with CV overlays and video thumbnails. Live plays a selected file as a causal 1× camera simulation. Review inspects consecutive 10-second windows of recordings up to ten minutes, including the final shorter window. Both preserve concerns, exact evidence, cited guidance, specialist/challenge state, server-side human decisions and downloadable HTML/JSON reports. The three frontend modules and four bounded agent folders remain separate. The three-dot menu and its workspaces have been removed. The launcher serves only the video API and starts a worker that accepts video review jobs. Historical compatibility code remains outside the shipped interface; footage, annotations, models and previous records are preserved.

### Open a video and go


The main interface starts on **Preview**. Choose a video thumbnail, drop a file on the yellow **Drop footage** card or use **Open video**, then play, pause and seek normally. Person boxes and confidence scores appear directly over the playing video, with one confidence slider beside it. Library thumbnails help identify each recording; they also appear in the Live/Review video picker. Preview works with AI off and does not start AI review, send footage to a provider or consume an AI allowance. Local CV checks run serially with a short lookahead buffer. Nearby, unambiguous boxes blend between sampled positions for smoother movement; new detections appear only at their observed time. Boxes clear on seeks, confidence changes or when older than half a second. This is sampled detection, not tracking or analysis of every frame. Browser playback requires a supported codec, such as H.264 MP4.

The **Person detection confidence** slider ranges from 5% to 95%, initially 50%. Lower values admit more detections and possible false positives. Changes update Preview and set the threshold for newly opened Live/Review runs. Paused frames are checked automatically. The overlay uses source timestamps and preserves the video aspect ratio, including letterboxing. Use the player’s **Full screen** button to keep overlays visible in fullscreen. Labels show confidence percentages without person numbers. Display smoothing does not establish identities or change saved detection evidence.

Choose **Live** or **Review** for AI analysis. A short visual welcome (Drop → Watch → Report, plus the three modes) appears on the first visit to this browser; AI, data and spending details sit under **AI, data and limits** inside it. Close it with **Get started**, the close button or Escape; reopen it anytime with **How to use**. Click **Open video**, drop a file on **Drop footage**, or click any thumbnail under **Your videos**. In Live or Review, uploading or clicking a video starts its AI review immediately (each thumbnail's tooltip says so); Live also starts playback. Optional review settings are visible below the video picker; no settings dialog or extra confirmation is required. **Review** checks the complete recording and lets you watch alongside it.

The player sits beside a **Findings** feed of image cards (red edge for priority, yellow for routine). Below the player, the coverage bar marks intervals that contain findings, and three tiles show intervals checked, team-reviewed findings and report readiness. Possible concerns go to the existing review team automatically. Click a finding to pause playback and see its exact evidence, readable assessment, recommended actions, sources and independent check. **Your decision** offers one-tap **Accept**, **Needs more evidence** or **Dismiss**, and still requires a written reason; it records an append-only server-side decision. **Download report** is always available; it includes unfinished coverage and pending assessments.

**Ask** sits beside **Findings** in recorded Review and History. Ask about a moment or finding, or send a question about the saved review. Only **Send** starts work, with a separate **$0.05 maximum per question** within daily/monthly limits. Answers link to retained timestamp thumbnails; Activity shows actual agents, tools and costs. Audio is not assessed. This is assisted review; real-video accuracy acceptance is pending. See [the Ask workflow and limits](docs/video-chat.md).

An explicitly authorized forklift smoke check completed with a qualified visual answer for **$0.00344** calculated usage. Live agent/tool activity, all four timestamp links and History restoration passed; the verifier corrected a draft contradiction before release. This single assistant-inspected check is separate from the pending human-labeled acceptance study. [Test receipt](data/artifacts/reports/forklift-ask-smoke-20261024/verification.json).

**History** reopens saved runs and their conversations. **Review settings** exposes required protective equipment, safety references and person detection confidence directly on the page. Preferences are remembered in this browser across Live/Review and reloads and apply only to newly opened videos. An existing review displays its original saved settings below the player; editing browser preferences never changes a saved run or establishes a safety finding. **Location not specified** remains the default. **U.S. OSHA references** (formerly “US federal guidance”) lets the AI consult federal workplace safety rules and guidance; it does not establish their applicability, confirm a violation or evaluate state-specific requirements. The inline explanation links to OSHA. Spending limits are always visible in the settings card.

**Real AI is the default** in Live and Review. The **AI on/off switch** at the top controls the server globally: off stops queued and active analysis, pauses Live, and preserves evidence and spending. Turning it on does not restart old work or reset allowances. Its state persists across reloads and app restarts. Historical fixture runs remain labeled as simulated. Costs, gaps and unclear views remain available under **Coverage details**. Leaving Live pauses it; recorded reviews keep running and their active count appears beside History. The main screen has no marketing headline, redundant “Real AI analysis” badge or bottom slogan. The start screen shows the workflow as three step cards: **Watch → AI review → Report**. Sampled-image transmission is explained in the welcome guide.

### One-command start

Prerequisites: macOS/Linux with `python3` for bootstrap, Node 22.12+ and npm, and internet access for the first dependency download. The launcher pins/downloads uv and Python 3.12 when needed. The launcher installs the person-detection inference dependencies and prepares the official RF-DETR Small COCO weights. This is pretrained inference; no custom CNN, tagging or training step is required.

```sh
./start
```

Open **http://127.0.0.1:8000**. This builds the frontend, prepares the standard person detector and curated guidance, then starts the API, video review worker and production video-chat worker together. The first setup needs internet for packages and stock weights. Ctrl+C stops the whole process group. Real AI is enabled for videos you open in Live or Review; Preview and startup make no language-model calls. No sample footage is imported. Upload your own video to begin. Use `./start --offline` to prohibit hosted analysis; fixtures skip person inference and make no paid calls. `./start --workspace /tmp/my-safety-trial` isolates records and reuses the verified project stock weights. A cold clone of the current allowlisted package reached a healthy app in 62.4 seconds on this Mac with empty caches, including the 386 MB detector weights ([receipt](submission/evidence/clean-install-2026-10-24.json)).

For real analysis, set `OPENAI_API_KEY` in the process environment or `.env`, start with `./start`, leave the top switch on, and open a video in Live or Review. `--allow-hosted` remains a compatible optional flag. A missing key or offline server is shown as unavailable; the main flows never substitute demo responses. The key-presence check does not establish provider/model access; the first user-submitted run does. Environment precedence is existing process values (including intentional empty values), then nonempty project-root `.env` values, then nonempty parent-folder `.env` values. No Portkey setup is required. Never put keys in the browser, artifacts or Git.

### Incident sampling and repeated concerns

New Live/Review runs retain up to two separate sequences of six ordered frames per ten-second interval. Each sequence includes three candidates before its selected motion peak, plus the peak and aftermath, at a 100 ms candidate cadence. Each model request stays within six high-detail images. New observer packets have 32,000 cumulative input tokens and 3,600 output tokens (1,800 per request), with exact input counting before dispatch. Completed sequences are checkpointed and reused on resume; the window completes only when all its sequences pass. Additional sequences can cost more within the same $0.15 cap. An operator may lower that frozen full-review cap using `max_cost_usd`; it cannot raise the configured allowance. Motion is not a hazard classifier, and unsampled events can still be missed. The observer checks apparent impacts and falls before routine clutter/PPE concerns. A blocked-access concern requires an identifiable visible route and obstruction. Cuts, occlusion and missing contact remain uncertain, and injury is not inferred. Historical manifests retain their original sampling version.

The issue list groups each category into one card. Open it to select an observation and inspect its own evidence, assessment and human decisions. Historical records remain unchanged; old reviews do not acquire a newly detected incident without a new explicit review. See [the current workflow and limitations](harness.md#temporal-incident-screening-grouped-concerns-and-live-cv-2026-10-24).

The latest balcony review completed both intervals and all five team assessments, but **impact-recognition acceptance remains failed**. It retains the 5.3-second evidence and a qualified struck-by concern; the independent check did not verify head contact. Repeated trip observations are grouped in one card. Cumulative commitment is **$0.134808**, including the unchanged **$0.009500** historical reservation. [Measured results and remaining limitations](data/artifacts/reports/balcony-preflight-retest-20261024/results.md).

Live displays local person boxes on their exact analyzed frame, with a compact person count. It keeps one active inference and the newest pending frame; delayed results show catching up, then hide after 1.5 seconds. First playback may wait for local detector warmup. Replaying a finished Live video also supports local boxes. CV confidence is not safety confidence, and these operations make no hosted request.

### Person detection inside the review

Each inspected window's retained stills (up to twelve in new full reviews, in two bounded packets) pass through the standard RF-DETR Small COCO detector. Only **person** detections are kept, at or above the run’s saved confidence threshold (default 0.5), with at most twelve per image; omitted detections are disclosed. The checkpoint must match the upstream stock checksum. Custom weights and custom class maps cannot enter this path. Local inference runs once per retained frame and is reused by the agents.

The observer, review boss, specialists and independent challenger receive a compact availability/count summary. Their scoped `get_person_detections(frame_id)` tool retrieves the saved boxes, scores, source timestamps and model provenance. It cannot seek forward, request a different recording or trigger extra inference. These records help locate people; they do not establish identity, PPE presence, motion between frames, or a safety violation. The AI still inspects the original images for acute struck-by/fall events and the four routine safety categories, including when person detection is empty or unavailable.

Person boxes appear on retained issue evidence. The coverage area reports detector availability, and HTML/JSON reports retain status and provenance. Historical runs without person records remain explicitly unprocessed by this detector. Missing dependencies, weights and inference errors remain visible; they never become “zero people.” No new tagging screen or custom training exists in the app.

### What completion means

Image-bearing recorded reviews now count the actual Responses payload before generation, including images and schemas. The fixed screening checklist is included up front. Reported cache-write lifetimes remain available for cost calculation; an unresolved charge stops further paid requests in that review. Later Live intervals keep their evidence but show an accounting gap. Existing failed runs and charge records remain unchanged. See [the token/accounting repair](harness.md#six-frame-review-token-counting-and-cache-accounting-repair-2026-10-24).

**All windows inspected** means every planned window has a validated result with at least two distinct retained stills and no reported decode/provenance gaps. It does not mean every frame was analyzed or every hazard was detected. Coverage, specialist completion and human disposition are separate. Unknown jurisdiction is the default; footage from Türkiye does not establish US applicability. Source excerpts remain references until applicability is established.

The original limits stay **$0.15 per run, $2 daily, $20 monthly**, shared by both screening and specialists. Missing provider usage remains reserved. Image allowances are reservation assumptions, not additional actual charges on reported image tokens. No automatic retry repeats a request with uncertain billing. The global AI switch cancels in-flight client requests; a request already received by the provider may still incur cost, so its unresolved reservation remains charged against the limits until reconciled. A stopped ten-minute job is still partial. Resume retains the original source hash, configuration, run ID and ledger. Full Review permits a 30-minute execution deadline and 256 physical requests while retaining bounded per-agent limits.

The Live service creates a paused 1× session; the video-first interface immediately requests play after an explicit video choice. It permits pause/resume/restart, flushes the final tail and stops at EOF without automatic replay. Forward seek, stepping and speed changes are disabled in camera simulation. Restart creates a new run/generation. Selecting a Live concern pauses the camera and displays its retained frame with the exact source timestamp; it does not seek into unseen footage. After EOF, the source is available for normal evidence playback and concern seeking. Leaving Live pauses playback. Recorded jobs continue in the worker and the screen shows their active count. One active screening and one newest pending window are retained; displaced pending windows become gaps. New screening has priority at queue boundaries; an active specialist is not preempted.

Suspected conditions become cases even when specialists cannot finish. Matching adjacent localized observations may form an episode, but each occurrence stays retained. Specialist packets remain bounded: omitted or later occurrences are explicitly unassessed. Resume can retry terminal unfinished specialist jobs within the original allowance. Server-side decisions require a reason and retain prior revisions; old sandbox notes remain browser-local and are not ground truth.

### Course evidence and checks

- [Requirements checklist](docs/course-requirements.md), [research record](docs/course-research.md), [attribution and provenance](demo/README.md).
- [Standalone course report](report.html), [ten-slide offline reveal.js deck](slides.html), [pilot protocol and worksheet](submission/pilot/README.md).
- [Measured install receipt](submission/evidence/clean-install.json), [engineering verification](submission/evidence/verification.json), [ten-minute fixture](submission/evidence/course-fixture.json).

The local fixture checks do not require model inference or the developer's complete source archive. The following commands remain offline; the standard detector can also be smoke-tested locally without provider calls:

```sh
AI_ALWAYS_ON=false AI_ENABLED=false HOSTED_IMAGES_ALLOWED=false SAFETY_PAID_REVIEW=0 OPENAI_API_KEY= ./scripts/uv run --no-sync pytest backend/tests -q
./scripts/uv run --no-sync python scripts/generate_contracts.py --check
npm --prefix frontend test
npm --prefix frontend run build
./scripts/uv run --no-sync python scripts/course_fixture.py
./scripts/uv run --no-sync python scripts/video_analysis_fixture.py
PYTHONPATH=backend:backend/src ./scripts/uv run --no-sync python scripts/safety.py fixture-replay
```

The full developer suite below also exercises optional CV/training integrations and the preserved 66-document source archive. Install optional packages with `./scripts/uv sync --extra cv`; never launch training or paid calls just to verify a change. The small submission includes a curated source bundle, not that complete developer archive.

Actual hosted accuracy, provider billing, 24 independent balanced labels and a real-user pilot are **open evidence requirements**. The 24 packaged clips are candidates, not labeled ground truth. Fixed fixture outputs are prominently labeled and are not safety findings. `scripts/run_course_pair.py --source YOUR_UPLOADED_FILENAME.mp4 --allow-paid` is an explicit operator-only evidence collection command; do not run it in offline verification. `scripts/evaluate_course.py` scores matched hosted exports with one-to-one temporal IoU ≥0.5.

The working directory has no Git history. `python scripts/package_submission.py /tmp/new-submission-folder` creates a new explicit-allowlist copy and hash manifest. It never copies developer `data/`, credentials, weights, caches or historical records and never initializes Git in this workspace. Publish only that reviewed copy. Private repository creation and zlisto access remain required release checks.

## Repository map

| Location | Responsibility |
| --- | --- |
| `backend/src/safety_app/domain/` | Shared records and request validation. |
| `backend/src/safety_app/workflows/` | Review, chat, monitoring and dataset coordination. `agent_stage.py` runs scoped agents; `review_cache.py` defines review cache identity. |
| `backend/src/safety_app/agents/` | Four bounded agent folders, with shared construction and tools. Specialist profiles stay inside `safety_reviewer/`. |
| `backend/src/safety_app/modules/` | Monitoring, safety-review and labeling services. |
| `backend/src/safety_app/infrastructure/`, `api/`, `workers/` | Persistence and budgets, HTTP adapters, and durable job execution. |
| `backend/app/` | Preserved player, CV, rules and learning integrations. |
| `frontend/src/features/` | Separate Monitoring, Safety Review and Labeling screens, plus shared operator screens in `operations/`. |
| `frontend/src/components/`, `trainer/` | Existing player components and learning workspace. |
| `tests/`, `backend/tests/` | Offline Python checks; frontend tests stay beside their modules. |
| `scripts/`, `configs/`, `docs/` | Commands, bounded-agent configuration and workflow guides. |
| `data/` | All project footage, datasets, records, models, evidence, reports, source documents and saved player settings. See [the data map](data/README.md). |

Add coordination to `workflows/`, domain-specific operations to `modules/`, and storage/provider effects to their existing adapters. Keep prompts and agent tools in their owning agent folder. Generated TypeScript contracts come from `scripts/generate_contracts.py`. Use temporary roots for fixture output; preserve existing data and historical artifacts.

All persistent project data lives under `data/`. The former root `artifacts/`, `evals/`, `resources/` and `config/` folders now live inside it. For an older checkout, stop the API, workers and database readers, then run `./scripts/uv run python scripts/consolidate_data.py` to preview and add `--apply` to move. The command refuses destination collisions and open databases, verifies every moved file hash, and retains a migration journal. This checkout has already been consolidated.

## Compatibility reference

The sections below describe preserved developer and historical components. They are not exposed by the launched video app. Use `./start` and the video workflow above for normal operation. Standalone chat, training, tagging and model-management routes are excluded from `safety_app.api.video_app`. The former full API remains available only as a compatibility entry point for historical regression tests.

### Historical setup

Run all Python commands from this repository root. Use Python 3.12 and Node 22.12 or later.
The root `uv.lock` is the development lock. `backend/pyproject.toml` describes the backend package.
No dependency or framework replacement was needed.

```sh
cp -n .env.example .env
./scripts/uv sync --frozen
cd frontend
npm ci
cd ..
```

Keep keys in `.env` or backend environment variables. Do not put them in the frontend or configuration files.
The launcher enables hosted review for explicitly opened videos; the top global switch can disable it persistently. Startup and fixture tests make no paid calls. Use `--offline` to prohibit provider traffic during local fixture verification.

## Development

Start the API in one terminal:

```sh
PYTHONPATH=backend:backend/src ./scripts/uv run uvicorn safety_app.api.video_app:app --host 127.0.0.1 --port 8000
```

Start the frontend in another terminal:

```sh
cd frontend
npm run dev -- --port 5173
```

Open [the app](http://127.0.0.1:5173) or [API docs](http://127.0.0.1:8000/docs).
The frontend opens a local operator session. Set `SAFETY_OPERATOR_TOKEN` in the backend environment if this session must require a token. The backend supplies reviewer identity and site scope.

The interactive player runs its existing decode and CV tasks in the API process.
The **Worker replay** panel queues durable jobs. Start its workers in separate terminals:

```sh
./scripts/safety worker monitoring
./scripts/safety worker review
./scripts/safety worker training
```

Each worker claims one job at a time. Multiple worker processes share SQLite claims and cost reservations.
The training worker stays idle until a user queues a local training, evaluation or registration job.

## First replay with no paid calls or downloads

This command writes a fictional clip and its audit records to a separate directory. It runs replay, a CV candidate, a mocked observer, a boss with real delegation tool calls to mocked applicability/vehicle specialists, independent challenge, a finding and a human decision.
It does not claim detector accuracy. If you omit `--output`, each run gets a fresh folder under `/tmp/safety-trials/`. An explicit output folder must be new.

```sh
./scripts/safety fixture-replay --output /tmp/safety-first-replay
cat /tmp/safety-first-replay/report.json
```

For real clips, put files in `data/input/` or use **Open video**. Only include footage authorized for storage in this project. The local library includes the public factory-floor example, `warehouse-forklifts.mp4` and ten selected workplace-compilation scenes. See [the input guide](data/input/README.md) for selection criteria and source records. The factory-floor example's prepared labeling bundle contains six unlabeled frames; the new demo clips have no independent safety labels.
Use **Open video**, the video thumbnails and the player controls for interactive review.
RF-DETR loads real weights on first inference. It may download missing weights. No fake detections replace a detector failure.

To queue a source-time replay from the terminal:

```sh
./scripts/safety sources
./scripts/safety replay --source-id SOURCE_ID --start-ms 0 --end-ms 10000 --profile camera-1 --key first-replay-v1
./scripts/safety worker monitoring --once
./scripts/safety worker review --once
```

Replace `SOURCE_ID` with a listed ID. Keep the review worker running to handle every queued job.
Without paid opt-in, reviews fail visibly and create coverage gaps. CV evidence remains available.
The fixture command above is the complete free review path.

## Secondary adaptive recorded-video questions

**Video sandbox** remains an adaptive question-answering tool under **Other tools**; the primary screen is now **Review**. Upload a video directly, or choose a library MP4. Use the suggested question or enter your own, choose **AI analysis** or **Free demo**, and run the test. The result and cited frames appear beside the controls. **Other tools** keeps Monitoring, Safety Review, Safety Chat, Labeling and operator screens available. Start the existing review worker with `./scripts/safety worker review`.

The workflow scans distributed short segments, lets a planner select denser inspection or before/after comparison, searches timestamped observation memory, and independently checks the answer against every cited frame. It works without playback, CV detections, or a live feed. The screen shows the proposed answer, checks, evidence images, inspected segments, actual agent activity, usage, errors and downloadable audit. See [the recorded-video guide](docs/recorded-video-analysis.md).

**AI analysis** uses the pinned model, existing paid opt-in and Monitoring image allowance, and shared spending limits. Opening the screen starts no analysis. **Free demo** uses fixed fixture responses and makes no paid calls. Previous tests and diagnostics are collapsed below the main workspace. **Your check** saves a personal verdict and optional notes in this browser, retaining earlier revisions; it does not approve labels or change the AI answer. **Download test report** exports the answer, evidence chunks and personal checks as JSON. These personal checks are not server records or accuracy metrics. A separate repeatable fixture creates a synthetic MP4 and all records in a fresh temporary folder:

```sh
./scripts/uv run python scripts/video_analysis_fixture.py
```

Only sampled stills are analyzed. Audio, identity continuity, complete event coverage, and real AI accuracy are not established. Long videos can have explicit uninspected intervals; increasing inspection limits does not establish continuous coverage.

### Two safety layers

The sandbox defaults to **Screen safety and involve specialists when needed**. Layer one makes compact visual checks for PPE, blocked access, unstable stacks and slip/trip conditions. It uses two frames per pass, one optional detail crop, low reasoning and smaller token caps. Unknown visibility stays unknown. Optional PPE items are requested checks; the safety team still determines applicability.

Suspected conditions needing judgment queue layer-two reviews with the existing safety boss, relevant specialists and independent challenge. They reuse the retained screening evidence. High-priority handoffs go first among queued work; active work is not preempted. The default is at most two handoffs per video analysis, with deferred concerns retained. Both layers share the original workflow's request/spending allowance and global budgets. Video review can finish while specialist judgments are still pending; both states appear in the sandbox.

The backend stores versioned screening records, stable case/evidence links, handoffs, specialist findings, challenge outcomes, corrective actions and provisional or independently supported manager-attention records. It retains source timestamps, optional recording clock and explicit gaps. High priority is an immediate-action **report input**, not a sent notification; recorded footage may be historical. Routine data supports later daily/weekly/custom summaries. No report schedule, recipient or delivery is configured by this feature.

Authenticated `GET /api/video-analysis/safety/metrics` supplies reporting inputs with optional timezone-aware `since`/`until` windows. Fixtures are excluded by default. Counts separate unknown checks, suspected source-time episode buckets, pending/deferred/failed judgments and finding classifications; they do not measure compliance or injury incidence. See [the two-layer guide](docs/two-layer-safety.md) for configuration, data boundaries and reporting limitations.

## Repeat trials and reset temporary results

Use **Trials & Reset** in the app. The preview shows which records will leave the working views.
Click **Reset trial data — keep labels and models** to archive trial events, observations, reviews, findings, decisions, gaps and replay jobs. Queued trials are cancelled. The player closes and AI is disabled.
You can use the same replay key for the next trial. Review caches are cleared so another review can run.

Reset keeps draft and approved labels, frozen datasets, class definitions, camera settings, training projects, model weights, model evaluations and original uploaded footage.
It also keeps footage linked to labels, datasets or label jobs. Audit history and spend reservations stay intact.
Trial records and unused evidence remain recoverable through **Restore trial history**. Restore does not replay old queued work or re-enable old review caches.
Reset changes working views; it does not delete media or reclaim disk space.

Stop running jobs before reset. For terminal resets, stop the API and workers first:

```sh
./scripts/safety reset-trials                 # Preview only
./scripts/safety reset-trials --apply --name "Walkway test 1"
./scripts/safety trial-archives
./scripts/safety restore-trial ARCHIVE_ID
```

For a quick free trial you can repeat without cleanup:

```sh
./scripts/safety fixture-replay
```

Each invocation uses a separate folder and reports its path. Your current project is unchanged.
No reset was applied to existing user data when this feature was tested.

## Hosted review and costs

The required four agents use `configs/agents.yaml`. Its `economy` alias preserves `gpt-6-luna`.
The interactive compatibility scene reviewer retains its existing model and cadence settings.
Every physical hosted request in either path uses the new durable cost preflight. SDK retries are disabled.

`configs/agents.yaml` now has Standard GPT-6 Luna text rates verified on 2026-10-06: USD 0.10/million input tokens and USD 0.50/million output tokens. See [OpenAI pricing](https://developers.openai.com/api/docs/models/gpt-6-luna). Cached input is conservatively charged at the uncached rate. The global per-image cost bound remains `null`; image requests remain blocked except for the explicitly enabled learning and Monitoring image workflow allowances described below. Text-only chat can run with these rates.
The UI shows USD day and month windows in UTC, outstanding reservations, failures and AI coverage gaps.
It also has a stop switch and editable day, month, workflow and agent budgets.
Unknown usage remains reserved. These local estimates cannot guarantee a provider invoice.

Set `SAFETY_PAID_REVIEW=1` and `OPENAI_API_KEY` in the backend environment or root `.env` before starting the review worker. `scripts/safety` loads root `.env` without overriding exported values. This local installation has paid opt-in enabled at the user's request; new installations remain disabled unless explicitly enabled. Restart the API and worker after changing credentials or opt-in.
The interactive scene reviewer keeps its separate **Enable AI** and **Allow sampled images** controls.
The global stop switch blocks both paths. There is no automatic model upgrade or fallback.
Existing interactive sampling settings are preserved. Reduce large packets to the configured six-frame limit before hosted use.

## Labeling and models

New footage can be staged in `data/cv-intake/incoming/`. Paste files there before assigning them to analysis, training, validation, or held-out evaluation. This folder is ignored by Git. Labeling lists its videos recursively, suggests dataset roles and automatic collection groups, and explicitly copies selected clips into a learning project; dropping footage there runs no jobs. See the [intake and correction guide](data/cv-intake/README.md) for dataset roles, object additions, camera validation, and the proposed agent-assisted workflow.

The Labeling screen includes retained Monitoring frame labels and the existing isolated learning workspace.
Monitoring labels have explicit draft and human approval operations. Assistance jobs cannot approve labels.

The learning workspace now suggests boxes with the local detector and supports **Accept**, **Reject**, **Adjust**, **Add missed object**, and save-and-next review. Source titles and behavior categories stay out of object review, detector input, and scoring. Projects support arbitrary object classes; adding classes reopens frame review. **Check current detector on validation frames** produces per-class precision/recall and inspectable misses/false detections from reviewed validation frames. Training supports Nano and Small, remains explicit, and excludes held-out test files. See the [human review guide](backend/app/trainer/docs/labeling-workspace.md).
The learning workspace keeps separate training footage, source-group splits, frozen COCO snapshots, local RF-DETR jobs and held-out evaluation.
It refuses exact copies of analysis footage. Keep related recordings and re-encoded copies in the same group.

The typed dataset API also freezes approved labels by source video and content hash.
It exports COCO and YOLO. It requires independent train, valid and test sources, with examples of every class.

```sh
./scripts/safety export DATASET_ID --format coco
./scripts/safety export DATASET_ID --format yolo
```

Local model jobs require a real dataset. Evaluation opens the held-out test and seals that dataset against further training.
Registration checks the checkpoint. Promotion requires completed evaluation and an explicit authenticated user action.
Previous model IDs and detector settings remain in deployment records. Remote training has no dispatch path.

## Migration and audit

API startup imports existing records and retained evidence without deleting originals.
The migration can also run again explicitly:

```sh
./scripts/safety migrate
./scripts/safety audit-export
```

Existing records use `data/artifacts/runs/lab.sqlite`; pretrained/registered detector weights use `data/artifacts/models/`, and learning projects use `data/learning/`. Consolidation preserves record contents, model bytes and source IDs.
New typed records and jobs use `data/app.sqlite`.
Audit records use the append-only `data/audit/audit.sqlite`. `audit.json` is an atomic inspection snapshot.
The Audit screen filters by run, agent, job, event, status and date. It shows inputs, results, failures, retries and budget blocks.
Credentials and hidden reasoning are excluded. Test and fixture records use separate temporary roots.

## Full developer checks and legacy evaluation

```sh
./scripts/uv run --no-sync pytest -q
./scripts/uv run python scripts/generate_contracts.py --check
cd frontend
npm run build
npm test
cd ..
```

Generate types after changing models:

```sh
./scripts/uv run python scripts/generate_contracts.py
```

Event evaluation uses human interval labels and coverage. Keep detection and finding quality separate:

```sh
./scripts/safety evaluate data/annotations/plumbing-example.json --output data/evals/reports/plumbing.json
./scripts/uv run python scripts/compare_scenes.py run --cache data/artifacts/comparisons/factory-smoke --mock
```

The plumbing example is fictional. The comparison command needs the named cache; prepare one with the commands in [the comparison guide](docs/scene-review-report.md).
Use the same held-out sources for CV-only, AI-only and combined measurements. Unlabeled footage cannot establish accuracy.

## Shutdown

Press **Ctrl+C** in the frontend, API and each worker terminal.
The API closes decoder and review tasks. Workers cancel supported local work and retain incomplete job records.
If a process dies abruptly, its running job becomes `interrupted` after its 120-second lease expires.
An unresolved paid reservation remains visible until actual usage is reconciled. Use `./scripts/safety reconcile RESERVATION_ID --actual-usd AMOUNT` only with actual provider usage. Use `--unknown` to retain funds and release a stale request slot. Do not delete a ledger to reset spending.

## Limits that still need external inputs

No paid provider call, cloud training or camera connection was used to verify this overhaul.
Real RTSP recovery needs a configured camera. Calibration needs site measurements. Forklift accuracy needs approved labels, weights and held-out footage.
Native video model input, remote training, managed multi-user authentication and automatic model rollback are not implemented.
Facility review supports retained clip samples and stored document excerpts; supplied photos can use `/api/media/photos`.
It cannot establish hidden conditions from images. Local training and evaluation adapters are implemented but were not run on a real dataset during this overhaul.

For preserved player and learning details, see [the legacy feature guide](docs/legacy-run-guide.md), [the trainer guide](backend/app/trainer/README.md) and [the evaluation guide](docs/evaluation.md).

## Delegated safety team and local source indexing

The safety reviewer now acts as a boss with eight named specialist profiles. It delegates applicability and focused technical questions, leads evidence-based causal investigation, then passes every finding/hypothesis to independent challenge. Specialist profiles stay inside the safety-review folder and cannot delegate. Details and source/tool boundaries are in the [workflow guide](docs/safety-agent-workflows.md).

The source service uses local SQLite FTS5/BM25, exact citation lookup, scope/domain/date filters, bounded previews and exact evidence packets. It never truncates a requirement to fit its packet budget. Unknown clause dates/context remain gaps. `review_team` configures delegation and evidence limits without replacing existing budgets.

To preview/export or add a source snapshot for a configured site:

```sh
./scripts/uv run python scripts/import_safety_library.py data/resources/safety/snapshots/federal-2026-09-30 --site-id local
./scripts/uv run python scripts/import_safety_library.py data/resources/safety/snapshots/federal-2026-09-30 --site-id local --apply
```

The apply operation preserves all originals and existing records, inserts immutable versioned blocks, and updates the derived search index. A reviewer still needs to establish effective dates and applicable context before supported regulatory conclusions.

## Safety Chat

Open **Safety Chat** to ask text questions using the boss and safety specialists. Start the review worker with `./scripts/safety worker review`. Create a chat, enter a question, then select **Safety team** to use the existing hosted-review configuration or **Local fixture · no paid calls** to test delegation and source tools with fixed demonstration answers.

The activity panel shows actual specialist runs, model requests, tool arguments/results and errors. Select **Inspect turn** for an earlier question. Completed answers include source excerpts, missing facts, specialist assessments and independent challenge issues. **Download audit** saves the completed turn's full redacted NDJSON trace. Conversations, failed turns and agent audit history survive page refreshes and restarts.

Hosted chat requires the same explicit paid opt-in, credentials, rates and budgets as review jobs. Fixture mode is a plumbing test and does not answer your safety question. Text descriptions remain unverified; chat does not create incident findings or establish that an OSHA violation occurred. See [harness.md](harness.md#safety-chat) for contracts, limits and persistence.

The per-agent input allowance is 64,000 tokens, including conservative preflight estimates for growing tool context. Federal searches accept equivalent spellings of `US-federal-OSHA`; use a bare clause ID for exact lookup. Specialist prompts limit repeated empty searches and keep questions within their domain. Failed turns remain in history; submit the question again after a fix to create a new audited turn.

Select **Inspect turn** to see its **Tokens & cost** monitor. It updates every two seconds with reported input/output tokens, recorded costs, pending/unresolved reservations and a per-agent breakdown. Usage arrives after each model response; unknown charges are flagged rather than treated as zero. The per-agent request cap is ten, with a shared 64-request workflow cap; existing USD caps remain. The selected turn's full audit download retains the debugging details.

Chat messages and agent explanations render Markdown: headings, emphasis, nested lists, code, tables and links. Known bare source citations link to the turn's evidence panels. Exact source excerpts and audit JSON stay literal. Raw HTML and unsafe links are disabled; remote image syntax displays its alt text without fetching images.

Structured answers have up to 4,000 output tokens per request and 8,000 per agent. Stages have 60 seconds and the workflow has 180 seconds. A specialist deadline reports `AgentStageTimeout` with the stage name. The delegation wrapper allows five extra seconds for that error to propagate; it does not automatically rerun a timed-out specialist. Spending caps still apply.

Agents have 16 tool calls per run. Every model request includes remaining tool/request/output allowances so agents can prioritize retrieval and leave room for an answer. Multiple tools in one batch each consume a call. Source text is still fetched whole within bounded packets; oversized packets may require individual clause fetches. Unresolved context stays explicit.

Source-service warnings are retained by Python in each specialist assessment and carried into the boss answer. Agents add scenario-specific missing facts; they do not have to copy warning strings exactly. Citation, ownership and claim checks remain strict. This avoids output retries caused only by warning labels or punctuation.

An uncertain claim that references a confirmed budget-omitted source without an exact clause keeps that reference as an audited missing-evidence warning. The unavailable text is excluded from evidence citations. Requirements, guidance, invented IDs and preview-only references still fail validation; agents should fetch central clauses individually when combined packets omit them.

Requirement citations must use the exact stored clause ID, even when the excerpt contains nested paragraphs. Validation feedback identifies every invalid claim and its retrieved citation metadata together, so one bounded retry can correct all known errors. Unsupported advice remains uncertain rather than being presented as sourced guidance.

## Pinned model and API key check

Every hosted language-agent path uses `gpt-6-luna`. Conflicting model aliases and legacy environment overrides fail before dispatch. This includes chat, monitoring, specialists, independent challenge, annotation assistance and comparison runs. Local detection/training models and historical records retain their existing identities.

To verify model access without a generation request:

```sh
./scripts/uv run python scripts/check_api_key.py
```

To explicitly authorize one small billable text response (64 output tokens maximum, no retries):

```sh
./scripts/uv run python scripts/check_api_key.py --allow-billable
```

The command keeps the key private and saves a redacted audit result. Its diagnostic token usage is separate from the application's reservation totals and has an unpriced cost record. A successful check does not enable hosted reviews; those still require configured rates, paid opt-in and available budgets.

## Agent output boundaries

[The contract guide](docs/agent-output-contracts.md) explains what models.py validates and what Python owns. Source warnings, canonical chat claim IDs and the boss's complete validated claim list are assembled in code. Safe citation/reference formatting differences are normalized; evidence, ownership, applicability and claim meaning remain strict. A substantive output failure names its actual validation reason after the bounded correction, rather than showing only a generic retry error.

Chat uses explicit low reasoning effort, configured by review_team.chat_reasoning_effort, to leave more output room for structured answers and independent challenge. Output accounting includes reasoning tokens. The trace records reasoning-token counts and finish reasons, never hidden reasoning text. Existing dollar spending caps remain enforced.

For multi-domain chat, the workflow allows up to 64 physical requests under existing spending caps. Six slots are reserved for boss synthesis and independent challenge. If specialist work reaches that boundary, the answer is marked **Partial assessment** and names the unfinished domain in missing context. Completed evidence is retained; missing investigations are not presented as completed. Other budget/provider failures still stop the turn.

The learning workspace also supports **Review image with AI** through the existing annotation assistant. It inspects pixels for all chosen classes, drafts missing boxes, and recommends accepting, correcting, or rejecting existing candidates. **Use AI draft** loads recommendations for human correction and save. Optional automatic review runs on each new frame as you navigate. Hosted calls require an explicit per-image allowance setup and use existing paid opt-in, token rates, audit and budgets; no hosted call is used for fixture verification. Titles, paths, source categories and recording metadata are excluded from the model packet. See the [human review guide](backend/app/trainer/docs/labeling-workspace.md#image-review-with-ai).

The intake list groups files by folder and supports multi-selection across pages. Imported files are marked with their role and review counts; select a page, a folder's available files, or all matching files and import them together. Imports show progress and individual failures. Folder names organize the source browser only; they remain excluded from AI input and training labels. The image-review activity panel shows actual stages, requests, tool counts, tokens/cost, errors, earlier attempts, and a downloadable audit. This mode has scoped draft-editing tools for adding boxes, correcting classes/bounds, rejecting candidates and inspecting its work; imports, sampling and AI-assisted human saves are also logged.


In Labeling, **Prepare frames and AI boxes in batches** samples all imported clips or a selected clip. **Spread samples across the whole clip** covers the full recording within the cap. **Every decoded frame** advances through unsampled frames in bounded batches. Sampling appends without replacing existing labels. Limits remain 200 new frames per clip per request and 1,000 active frames per project. **Prepare AI drafts for N frames** reviews unreviewed frames sequentially (default 25, cap 100), skips current saved drafts, and stops at the first error/budget block or after the current item when stopped. Fresh drafts appear on the canvas for human review and save. Closing the screen stops further submissions; this queue is not a background worker. No training or approval happens automatically.

## Person activity analysis and visual rules

See [the analyzer and rule editor guide](docs/person-safety-analyzer.md) for camera IDs, draggable rules, PPE visibility, person-focused sampling, evidence playback and budgets. [Validation results](docs/person-safety-results.md) distinguish local measurements from unverified safety/cost targets. [Example rules](configs/examples/person-safety-rules.json) are provided for drawing your camera layout. Restart the API after syncing the updated lock; no paid calls are needed for fixture checks.

Monitoring now uses a separate $0.0010 per-image operator allowance for budget checks. The local profile uses 10-second fresh review periods, three scenes, one person crop and one optional extra image, with a $0.1000 playback cap. This allowance is not verified provider image pricing; all shared budgets and explicit AI/image opt-ins still apply. Budget controls explain each scope and show dollar amounts to four decimals. See [the analyzer guide](docs/person-safety-analyzer.md#live-processing-and-non-overlapping-scene-periods).

This installation has user-authorized `AI_ALWAYS_ON=true` in `.env`: Monitoring starts enabled and Labeling defaults to automatic AI review of eligible new frames. The global stop switch and budgets still apply; other actions need their normal inputs. Set the flag to false to restore per-page opt-in. New installations default to false. Monitoring's right-hand inspector shows actual agent activity, tokens/costs, earlier saved reviews and downloadable audits. If a worker reports a connection error, check its environment's outbound network access; a failed turn remains in history and should be resubmitted after connectivity is restored.

## Chat Lab testing

Start `./start --chat-testing` and open [Chat Lab](http://127.0.0.1:8000/test/chat). Use `--offline --chat-testing` for local fixtures. Opening the balanced comparison page or a saved result starts no paid work. Explicit Run compares Current and Optimized sequentially under one $0.15 allowance, with live agent handoffs, tool calls, timelines and itemized accounting. Existing $2 daily/$20 monthly limits remain.

Version 13 fixes the recorded Preview routing error. Product help uses only product documentation and no regulatory profile; saved-record lookups use only authorized records and preserve human decisions. The original question survives planning. Current retains coordination and synthesis; Optimized directly answers ordinary product and record questions. Answering agents use `gpt-6-luna` through PydanticAI; Decisions uses the guarded OpenAI SDK with the same model. Preview, Live and video-review behavior are unchanged.

Answers are rendered from individually reviewed blocks. Product facts, saved records, legal requirements and specific medical treatment need appropriate sources. Clearly labeled general safety guidance can remain useful when evidence is incomplete. Legal applicability gaps do not block all guidance. Essential qualifications stay with each block; blocks that depend on withheld content are also withheld. Exact requirements still require dated, complete regulatory text and established applicability.

Each branch can make one corrective generation, one recheck when needed and a final scope check, within a held $0.01 allowance inside the parent budget. Correction reuses evidence and cannot search the web. SDK/transport retries remain disabled; authentication failures, cancellation, unknown usage and exhausted budgets stop progression. An isolated, fully accounted output failure does not prevent the other workflow from running. Outcomes distinguish answered, partial, clarification, declined and unavailable; a branch that never started is labeled accurately. Technical details stay expandable.

The UI shows evidence labels and live correction/rechecking steps with duration and cost. Useful answers are measured separately from generic limitations and technical failures; a complete fixture or execution is not an accuracy result. Retained limitations are deduplicated and expandable. Historical comparisons remain readable and unchanged.

A valid scope block or clarification ends work without requiring task, domain or freshness classifications that will not be used. Their refusals remain in diagnostics. A refused or ambiguous scope check, or incomplete allow route, cannot grant permission and is never retried automatically.

Read tools advertise and enforce a five-source bound, with explicit gaps for oversized or unavailable requests. Read-only tools search frozen references, site rules, product documentation and saved reviews. Native OpenAI web search supplies current discovery with retrieval dates and provenance. Multi-source summaries stay labeled bundles; provider citation spans are retained without claiming that every URL supports the whole summary. Classification and retrieval caches include policy, permissions and frozen source versions; final safety answers are not cached. Known uniform 30-minute cache-write lifetime is priced separately from unknown usage, which remains reserved.

Build the additive OSHA index with `./scripts/uv run --no-sync python scripts/build_chat_index.py`. The preserved Title 29 snapshot yields 1,475 OSHA sections and 49,109 whole blocks; original records remain unchanged. Missing licensed, historical, jurisdiction and complete-context evidence stays visible.

Run `./scripts/uv run --no-sync python scripts/chat_lab_fixture.py` for ten isolated no-provider cases, plus the documented backend/frontend tests, contracts, build and other fixtures. The [Chat Lab guide](docs/chat-lab.md) describes boundaries, pricing, evaluation and results. `scripts/chat_lab_evaluate.py` defaults to a dry run; paid execution requires operator authorization. The versioned evaluation set has 12 cases in both orders, with a $0.50 batch cap and full-parent-allowance admission. No reliability or savings claim follows from fixtures alone.

Version 13 passed 493 Python tests, 90 frontend tests, contract checks, production build and four isolated fixtures with zero provider requests. Hosted testing stopped on an OpenAI Decisions HTTP 503 with missing usage: $0.228057 is committed across all diagnostic versions, including a $0.001384 unknown reservation, within the $0.50 total allowance. The final matrix attempted 1/24 comparisons; 23 remain, and the 95% useful-response target is unverified. No retry or guessed billing reconciliation occurred. See the [measured report](data/artifacts/reports/chat-lab-reliability-v13-20261024/results.md) and [harness.md](harness.md).

Provisional guidance that cites a retrieved web-summary bundle can retain an uncertain review verdict when the reviewer explicitly finds the qualified block independently publishable and identifies no blocking defect. The source warning stays visible. This exception does not apply to legal requirements, product facts, saved-record facts, or specific medical-treatment instructions. Read-tool schemas advertise the five-source limit; an excessive or unavailable request yields bounded evidence gaps rather than a crash.

### Video-chat verification

Run `./scripts/uv run --no-sync python scripts/video_chat_fixture.py` in addition to the documented four fixtures. It tests real decoding, retained evidence, mocked answers, streaming and export without paid calls. `scripts/video_chat_evaluate.py` reports the versioned 12-case/two-repeat acceptance coverage; real-video human labels and funded hosted runs are still required. [Workflow and research](docs/video-chat.md).
