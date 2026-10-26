# Self-trial: manual review vs. app-assisted review (about 35 minutes, no new AI spend)

Mason is the participant, which is a known bias: he built the app and has seen these videos before. The report states this. Record real times only. Never estimate a missing trial.

**Task, the same in both conditions:** for one video, write down every safety concern you would raise with a supervisor. For each one, note the timestamp, what you see and one next action. Stop the timer when the list is something you would send.

**Videos (disjoint sets with a similar mix; never review the same video in both conditions):**

| Order | Condition | Video | Length |
|---|---|---|---|
| 1 | manual | `13-warehouse-rack-collapse.mp4` | 65 s |
| 2 | assisted | `11-restaurant-kitchen-fire.mp4` (History → Live, 25 Oct 4:43 PM run) | 78 s |
| 3 | manual | `05-warehouse-pallet-handling.mp4` | 98 s |
| 4 | assisted | `06-truck-pallet-unloading.mp4` (History → Live, 25 Oct 5:22 PM run) | 197 s |
| 5 | manual | `18-warehouse-forklift-traffic.mp4` | 23 s |
| 6 | assisted | `09-construction-balcony.mp4` (History → Live, latest balcony run) | 14 s |

**Manual:** open the video in **Preview**, which runs no AI. Play, pause and scrub as you normally would. Write the list in the worksheet's "Corrections / misses / useful findings" box.

**Assisted:** open the saved run from **History**. Read the findings, open each one, check the evidence frames, record a decision with a reason, and open **View report**. Add anything the app missed to the same box, prefixed with `MISSED:`. Note any finding you dismissed as a false alarm, prefixed with `FALSE ALARM:`. Enter the run's AI processing time (the Agent team "Elapsed" tile) as the AI wait.

**Easiest path:** fill in [self-trial-notes.md](self-trial-notes.md) with a phone stopwatch while you watch. Claude converts it into `submission/evidence/self-trial.json`, so you can skip the worksheet steps below.

**Steps (worksheet alternative)**

1. Open `submission/pilot/label-and-time.html` in a browser. Enter participant code `self`.
2. For each row above, fill in condition, block/order (e.g. `1`), video name, video length, then **Start timer** → do the task → **Stop timer**. Enter the concern count, then **Save actual trial**.
3. Click **Download human evidence JSON**. Save it as `submission/evidence/self-trial.json`.
4. Optional: one sentence on how the two conditions felt, typed into the last trial's interruptions box.

Then run `./scripts/uv run --no-sync python scripts/build_course_artifacts.py`. The report and slides read the file directly.
