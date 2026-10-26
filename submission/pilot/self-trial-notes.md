# Self-trial notes (fill in as you go)

**Participant:** self (Mason) · **Date:** ____ · **Start time:** ____

**Task, the same for every video:** list every safety concern you would raise with a supervisor, with a timestamp, what you see, and one next action. Stop the stopwatch when the list is something you would actually send.

**Rules**
- Use a stopwatch (phone is fine). Start it when the video or saved run is on screen. Stop it when the list is done.
- Do the videos in order. Never look at a video in both conditions.
- **Manual:** open the video in **Preview**, which uses no AI. Pause and scrub as you normally would.
- **Assisted:** open the saved run from **Live → History**. Read the findings, open each one and check its frames, record a decision with a reason, and open **View report**. Add anything the app missed as `MISSED`, and mark any finding you dismissed as `FALSE ALARM`.
- Write real numbers only. If something interrupts you, note it; don't fix the time.

---

## Trial 1 · manual · `13-warehouse-rack-collapse.mp4` (65 s)

- Review time (mm:ss): 1:54
- Interruptions / notes: N/A

| # | Time in video | What I see | Next action |
|---|---|---|---|
| 1 |0:34|Rack 13 collapses|Nobody is injured. Operator on forklift backs away from nearby racks|
| 2 | | | |
| 3 | | | |
| 4 | | | |

---

## Trial 2 · assisted · `11-restaurant-kitchen-fire.mp4` (78 s) · History → *restaurant kitchen fire, 10/25 4:43 PM*

- Review time (mm:ss): 2:52
- AI wait shown on the Agent team "Elapsed" tile (mm:ss): 1:48
- Interruptions / notes: N/A

| # | Time in video | Finding (app's or `MISSED`) | My decision: Accept / More evidence / Dismiss (`FALSE ALARM`) | Reason / next action |
|---|---|---|---|---|
| 1 | 0:10| FIRE OR BURN CONCERN| Accept| Large and uncontrolled fire. |
| 2 | 0:30| STRUCK-BY CONCERN| Dismiss| There was nothing that could hit any employees in this video|
| 3 | | | | |
| 4 | | | | |

---

## Trial 3 · manual · `05-warehouse-pallet-handling.mp4` (98 s)

- Review time (mm:ss): 1:37
- Interruptions / notes: N/A

| # | Time in video | What I see | Next action |
|---|---|---|---|
| 1 | 0:13|Operator crashes forklift into conveyor belt | Operator gets up and runs off. Seems to be fine and other nearby workers come to check on him|
| 2 | | | |
| 3 | | | |
| 4 | | | |

---

## Trial 4 · assisted · `06-truck-pallet-unloading.mp4` (197 s) · History → *truck pallet unloading, 10/25 5:22 PM*

- Review time (mm:ss): 5:12
- AI wait shown on the Agent team "Elapsed" tile (mm:ss): 3:59
- Interruptions / notes: N/A

| # | Time in video | Finding (app's or `MISSED`) | My decision: Accept / More evidence / Dismiss (`FALSE ALARM`) | Reason / next action |
|---|---|---|---|---|
| 1 | 0:02| SLIP OR TRIP HAZARD| Accept|Ground is wet and uneven |
| 2 | STRUCK BY CONCERN| 0:40|Dismiss | Nothing that will fall or hit a worker|
| 3 | 0:30| UNSTABLE STOCK|Accept |Leaning pile of boxes near worker that could fall and hit them |
| 4 | 1:00| FALL CONCERN|Accept |Leaning boxes may hit worker |
| 5| 3:02| MISSED| Boxes fell and could have hit a worker| Boxes fell off truck loading dock|

---

## Trial 5 · manual · `18-warehouse-forklift-traffic.mp4` (23 s)

- Review time (mm:ss): 0:50
- Interruptions / notes: N/A

| # | Time in video | What I see | Next action |
|---|---|---|---|
| 1 | 0:09| Worker hit by another worker operating a forklift from behind| worker hobbles off and sits down. Appears to be injured. Other workers come to check on them.|
| 2 | | | |
| 3 | | | |

---

## Trial 6 · assisted · `09-construction-balcony.mp4` (14 s) · History → *construction balcony, 10/25 3:15 PM*

- Review time (mm:ss): 2:06
- AI wait shown on the Agent team "Elapsed" tile (mm:ss): 1:34
- Interruptions / notes: N/A

| # | Time in video | Finding (app's or `MISSED`) | My decision: Accept / More evidence / Dismiss (`FALSE ALARM`) | Reason / next action |
|---|---|---|---|---|
| 1 | 0:01| SLIP OR TRIP HAZARD|Accept | Loose rubble and other debris on the ground|
| 2 |0:06 | Missed| Worker drop boards on top of another workers head| Worker may be injured but walked off.|
| 3 | | | | |

---

## Wrap-up

- In one or two sentences, how did the two ways compare? The agent review team was much quick and able to provide more thorough analysis on the videos. I was more accurate and able to pick up on every event that occured, but that required a high level of attention that could be devoted to other tasks.

- What slowed you down most in the app? Navigation could be more intuitive. The first few reviews I had to get aligned with where different buttons and videos were located. After I was accustomed, the app worked smoothly.

- Anything the app got clearly wrong? The app did not make up any information. It did, however, make very conservative judgements that were not worrisome in reality.

Add rows to any table if you need them. When you're done, tell Claude; it will convert this file into `submission/evidence/self-trial.json` and rebuild the report and slides.
