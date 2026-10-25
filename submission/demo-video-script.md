# Demo video script (2–3 minutes, narrated screen recording)

The course rubric says the video can earn "It works" and "Look and feel" points if staff cannot run the app. Record the app running on this Mac from start to finish. Use QuickTime → File → New Screen Recording, with the microphone on, at 1440 × 900 or larger.

**Before recording:** run `./start`, open http://127.0.0.1:8000 in a fresh browser window so the welcome dialog shows, close other tabs, and turn notifications off. To avoid waiting on camera, show the saved kitchen-fire run from History for the agent and report steps. If you start a fresh Review instead (about $0.01–0.05), keep it to a short clip such as `09-construction-balcony.mp4`.

| Time | Screen | Say (roughly) |
|---|---|---|
| 0:00–0:15 | Welcome dialog | "This is Industrial Safety, an AI review desk for workplace video. A safety coordinator drops in footage, a team of AI agents checks every 10 seconds of it, and a person makes the final call." |
| 0:15–0:30 | Click **Get started**, show the video grid, hover **Preview / Live / Review** | "Preview is free and shows local person detection. Live reviews a video as if it were a camera. Review checks a whole recording." |
| 0:30–0:55 | **Live** → **History** → *restaurant kitchen fire, 4:43 PM* | "Here is a real run on a 78-second kitchen fire. All eight intervals were checked, shown by the bar under the player. Two findings came back: a priority fire concern grouped across the whole flare-up, and a possible struck-by." |
| 0:55–1:25 | Scroll to **Agent team** | "Behind it is a team. Python cuts the video into intervals and enforces the budget. A video observer fills a fixed checklist for each interval. A safety lead sends each case to specialists, here vehicles and fire, and an independent challenger re-checks the original frames. Thirty-four model requests, about four and a half cents." |
| 1:25–1:50 | Click the **fire** finding, then the **struck-by** finding, scroll to **Your decision** | "Every finding shows the exact frames the AI saw, with timestamps. The team is careful: it says what is visible and what is not established. I record a decision with a reason. Nothing is sent or acted on without a person." |
| 1:50–2:10 | **View report** | "The manager gets a one-page report: the verdict, a short brief, what was checked, and what needs attention, downloadable as a single file." |
| 2:10–2:35 | Back on the player: **Coverage details**; mention caps | "Every request is capped: 15 cents a run, $2 a day, $20 a month. If text in the footage tries to hijack the model, Python rejects any answer that cites frames or times it wasn't given." |
| 2:35–2:50 | Back to the home screen | "In my timed test, reviewing with the app took less of my time than scrubbing by hand. Next is a pilot with a real safety coordinator. Thanks." Update this line with the real self-trial numbers. |

Upload it to Canvas next to `report.html` and `slides.html`. Those three files are all Canvas should get. Keep it under 3 minutes.
