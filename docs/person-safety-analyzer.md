# Person safety analyzer and visual rules

Monitoring extends the existing RF-DETR, ByteTrack and PydanticAI scene reviewer. It does not replace CV or add an agent framework. Safety Review and Labeling remain separate modules.

## Setup and first use

Run `./scripts/uv sync --frozen`, then start the API and frontend as described in README. The locked `imageio-ffmpeg` package supplies FFmpeg when it is absent from PATH. Restart the API after installing these changes; refresh the frontend.

1. Open a video in Monitoring. Enter a stable **Camera ID** before opening it to share rules across recordings from that physical camera. Leaving it blank assigns a camera per library video. Use another camera ID for a different viewpoint. The first camera imports the old single-camera layout once; subsequent cameras start empty.
2. Open **Computer vision**. Use **Use 0.65 person threshold in draft**, then **Save CV settings** to apply the checked starting point to an existing setup. Existing explicit detector settings are preserved. The threshold changes detection filtering, not AI confidence. ByteTrack's confirmation, association and lost-track settings still apply.
3. Turn on **Rule editing** beneath the player. Choose a template, draw endpoints or polygon vertices, then name the rule and write its safety meaning in **AI review context**. Configure direction, dwell, occupancy, PPE, tolerance and rearm duration. Restricted access uses the restricted-area entry template; geometric entry does not establish unauthorized access.
4. Use **Edit / redraw** and drag vertices, or redraw the shape. Save applies the draft. Enable, disable and delete operate independently for each rule. **Show rules** hides shapes without stopping their evaluation. Editing shows shapes even when Show rules is off.
5. Select **AI + CV** and **Person-focused selection** for person activity reviews. Wider timestamped images always accompany optional person crops. **AI scene review**, or AI + CV with person focus off, samples broader scenes and can inspect hazards without detected people. Set camera PPE separately from zone PPE.
6. Tune **Review cadence & sampling**. Rule-triggered reviews require the global routing switch and permission on the specific rule. Local rules continue when AI is off. Findings show types, severity, evidence, confidence, missing evidence and corrective actions. Filter by type, severity or human review. Mark **correct**, **false positive**, or **uncertain**; decisions are saved.

[Example rule definitions](../configs/examples/person-safety-rules.json) show a doorway, loading-bay dwell and occupancy limit. Coordinates are examples, not camera calibration. Recreate them in the editor for your footage. A PUT to `/api/config` with this JSON replaces the active camera's entire rule/zone set; do not apply it to an existing layout you want to retain.

## Hosted review and budgets

Hosted calls still require the existing model key, paid opt-in, image permission, Enable AI, known pricing and global budgets. No paid evaluation was run for this change. The existing pinned model remains `gpt-6-luna`.

Standard short-context text rates were checked on 2026-10-08: input $0.10, cached input $0.01 and output $0.50 per million tokens. [Official model pricing](https://developers.openai.com/api/docs/models/gpt-6-luna). Image tokenization and provider surcharges affect the total; image requests need a configured conservative media allowance in the existing global budget configuration. The learning-only image allowance does not enable Monitoring. An unknown global image rate blocks dispatch and leaves a visible failed/budget-limited review. Keep credentials in the backend environment.

The global guard allows six images per physical request by default. Configure initial scenes plus person crops plus extra images within that allowance; for example, three scenes, one person crop, and up to one extra image. Tool follow-ups retransmit context and consume another request. This example reduces sampling coverage; choose it explicitly rather than treating it as an accuracy guarantee.

**API target** defaults to $0.01 per camera-hour. It measures a goal; it does not throttle automatically. **API cap for this playback generation** is an optional hard cap shared by that generation's scene reviews. **Maximum request allowance** must cover the conservative token/media estimate. Unknown dispatched charges retain reservations; a budget block skips the review and records missed coverage. It does not automatically retry or queue an unlimited backlog. Global day/month/workflow limits also apply. Provider transport retries are disabled on this route so each output-correction or tool request must pass the camera cap again.

The cost table uses source duration, not playback wall time. It reports provider requests, fixture requests, skipped/failed reviews and no-person windows. Per-review usage includes reported input/output/cached tokens, image details when reported, and transmitted image counts. Cost is calculated from reported tokens and configured Standard rates; reconcile against provider billing before claiming invoice compliance. Unknown usage stays unknown. Local compute and storage dollar costs are shown separately as unmeasured.

A text-only illustration with 1,000 input and 500 output tokens costs $0.00035 per request at these rates, before image input and retries. Twelve such requests per hour cost $0.0042; 720 cost $0.252. The default five-second cadence can exceed the target. Longer cadence, smaller packets, selective crops and local triggers reduce cost while leaving more footage unreviewed. No acceptable-quality/cost operating point has been demonstrated.

## Evidence and temporal boundaries

Rules use normalized coordinates, box bottom-centers and source milliseconds. Polygon membership uses inner/outer tolerance margins; line tolerance is normalized perpendicular distance. Rearm/clearance duration suppresses jitter. Track loss interrupts untriggered dwell and exposes the lost interval; active episodes remain uncleared. A different ID starts another observed interval. There is no automatic identity stitching or claim of continuous occupancy during occlusion.

FFmpeg resizes scenes/crops to at most 1024 pixels. PPE missing requires clear visibility and a localized person at least 120 pixels high in the resized scene by default; configure this limit in sampling controls. This size gate is conservative, not validated PPE accuracy. Obscured, small or off-image PPE must be **not assessable**.

Extra evidence requests are bounded to the frozen retained-frame pool and crops within the review cutoff. They cannot open arbitrary media or inspect future frames. Reviews do not claim coverage between images. Exact transmitted images are retained for inspection, subject to existing quotas.

The pipeline encodes sampled rolling segments at ten-second source intervals. Segment sidecars retain the actual timestamp list. If the encoder is busy or its 1 GiB segment cap is reached, the UI reports a gap and keeps existing evidence. Original recordings are preserved. These segments represent sampled frames, not a continuous raw camera recording.

Event playback uses FFmpeg clips from original recorded media when available, with configured before/after footage (default ten seconds each). Live/unavailable-source clips use retained frames and report partial coverage. Playback starts at zero; JSON sidecars map back to source time. Clips are capped at 240 seconds; long episodes have partial clip coverage. Playback may include post-event footage that was not in the original AI review. Evidence clips and sidecars are in `data/artifacts/clips`, rolling segments in `data/artifacts/segments`.

## Free validation

```sh
./scripts/uv run pytest -q
./scripts/uv run python scripts/generate_contracts.py --check
./scripts/safety fixture-replay
cd frontend
npm run build
npm test
```

Audit existing cached detections against saved validation labels without new model calls:

```sh
./scripts/uv run python scripts/evaluate_person_selection.py \
  data/learning/PROJECT_ID/project.json --output data/artifacts/reports/person-threshold-audit.json
```

The [evaluation manifest](../data/evals/person-selection/manifest.json) points to seven existing reviewed images and snapshots their person boxes/hashes for manual inspection. It has no hazard or PPE truth labels. Threshold reports list false detections and missed boxes. Test footage is excluded.

For a bounded real local replay with existing Small weights and downloads disabled:

```sh
./scripts/uv run python scripts/benchmark_person_pipeline.py PATH_TO_VIDEO \
  --frames 300 --passes 8 --output data/artifacts/reports/person-throughput-repeat.json
```

Each pass is capped. Repeated footage tests processing load, not independent accuracy or multi-camera capacity. No language-model client or training is started. Read the [validation results](person-safety-results.md) before using any measured numbers.

## Live processing and non-overlapping scene periods

Use **Live AI processing** below the player to follow the active and queued source ranges, request/tool activity, image count and reported tokens. **Playback AI budget (USD)** accepts a positive decimal cap; click **Apply budget** to save. Blank uses global caps alone. Expand **Global budgets & remaining funds** to change the shared USD limits in the same screen. Adjusting the playback cap does not cancel the active review. Image/token limit errors need sampling or request-limit changes, not larger USD caps.

New live scene periods contain only footage after the previous started review cutoff, for scheduled and rule-triggered reviews alike. The preceding window is a maximum; short cadences can shorten the actual packet. Extra images/crops obey the same boundary. Failed periods are not automatically retried. Skipped/coalesced periods can leave coverage gaps. Settings edits preserve this boundary within a generation; seek/restart starts a new generation. Historical review ranges and explicitly retrospective incident reviews remain as recorded.

The current user-authorized Monitoring installation now has a scoped **$0.0010 per-image operator allowance**, separate from Learning and the null global image rate. It is a conservative guard allowance, not verified provider image pricing. The active local profile uses a 10-second cadence/window, three scenes, one person crop and one optional extra image, under the six-image request cap, with a **$0.1000 playback cap**. Global limits and held reservations still apply. Review results and budget fields use four-decimal dollar display while retaining full accounting precision. Missing allowances and image/request-size blocks have distinct status labels and errors. Setup and regression checks made no paid calls; run the next live trial explicitly through the UI.
