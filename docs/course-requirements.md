# Requirements and completion evidence

Source: [MGT 409 Fall 2026 project requirements](https://zlisto.github.io/mgt_409_fa26/project_requirements.html), accessed 2026-10-23. Deadline: **Monday, October 26, 2026, 6 p.m.** Status meanings: implemented = local code exists and has offline evidence; prepared = artifact/protocol ready but measurements missing; open = required external evidence or action remains. A fixture is never a real-user or hosted-model result.

Goals and the offline checker for this table: [submission-goals.md](submission-goals.md), `scripts/check_submission.py` (updated 2026-10-25).

| ID | Requirement | Implementation / evidence | State |
|---|---|---|---|
| I1 | Novelty and value, 21 points | Report §3 compares Intenseye, Protex AI, viAct, ChatGPT and manual review; [research record](course-research.md) | Comparison done; value evidence is the self-trial (§7) |
| A1 | Working application, 15 points | Live/Review, agent team, manager report; real hosted runs 24–25 Oct ([token-usage.json](../submission/evidence/token-usage.json)) | Implemented and run hosted; labeled accuracy open |
| A2 | Staff can run it in <10 minutes, 5 points | README Quick start (first 40 lines), `./start`, `.env` read from repo then parent folder; cold clone 62.4 s on 2026-10-24 | Re-run fresh-clone check on the published package (G6) |
| A3 | Look and feel, 10 points | Hi-Vis UI, welcome guide, one-tap decisions, in-app report; screenshots in report §4 | Implemented; outside-user usability open |
| R1 | Motivation and what is new, 8 points | Report §2 (BLS injury and wage data) and §3 (named products) | Done |
| R2 | Agent design and trace, 10 points | Report §4: SVG diagram, agent/tool table, model rationale, six-screenshot trace of kitchen-fire run 46f88eee | Done |
| R3 | Tokenomics, 8 points | Report §5: input/cached/cache-write/output tokens per run from the audit log, per user, 100 and 10,000 users, before/after cost cuts (0d129747 → 46f88eee, 9e886270 → 56cf46ca) | Done; invoice not reconciled |
| R4 | Safety, 8 points | Report §6: risk table, new prompt-injection tests, captured `pytest` output ([safety-tests.txt](../submission/evidence/safety-tests.txt)), real-run defenses | Done |
| R5 | Impact, 5 points | Report §7 renders [self-trial.json](../submission/evidence/self-trial.json) using the [self-trial protocol](../submission/pilot/self-trial.md) | **Open: Mason must record the timed self-trial** |
| R6 | Report design, 2 points | `report.html`, 14 letter pages, embedded images, no relative links; built by `scripts/build_course_artifacts.py` | Done |
| S1 | Slides, 8 points | `slides.html`, 10 slides, reveal.js 6.0.2 from jsDelivr, embedded screenshots and diagram | Done; impact slide fills in with the self-trial |
| P1 | Public repo, README, `.env.example`, no secrets | [github.com/mason-lewis/industrial-safety](https://github.com/mason-lewis/industrial-safety), built from the `scripts/package_submission.py` allowlist (581 files); G4 secret scan | Published 2026-10-25; GitHub clone healthy in 63 s with cold caches and `.env` in the parent folder |
| P2 | Canvas (files only, per the 25 Oct instructor update): report.html, slides.html, optional video, nothing else; the GitHub URL and public/private status must be inside both HTML files | Report cover and every slide footer carry `github.com/mason-lewis/industrial-safety` and state it is public; title slide and cover give the clone command. [Demo video script](../submission/demo-video-script.md) | Open (manual upload) |

The 2026-10-24 report and slides are archived in `submission/evidence/archive-20261024/`.

## Engineering acceptance map

| Requirement | Evidence | State / limitation |
|---|---|---|
| Consecutive 10-second windows and final tails, ≤10 min | [Review run tests](../backend/tests/test_review_runs.py); [Ten-minute fixture](../scripts/course_fixture.py) | 60-window/120-frame fixture passed; sampling is not every frame |
| Separate coverage, specialist completion and human disposition | `ReviewRunStatus`, run UI and deterministic export | Implemented; outside-packet occurrences remain unassessed |
| Cancellation, restart recovery, idempotency, original allowances | Shared durable manifests/Jobs, existing ledger; review tests | No automatic retry of uncertain paid requests; operator must reconcile |
| Full vs adaptive / fixture vs hosted | Additive request fields; existing adaptive default preserved | Full selected by primary Review |
| Live 1×, pause, restart generation, EOF, backpressure and causal pixels | Live controller and existing source-time player; tests; browser capture | One active/one pending screening; dropped intervals retained |
| Priority specialists with no fixed two-case limit | Full dispatch path; bounded retained packets; queue priority | No preemption inside an active specialist task; remaining work visible |
| Conservative episode grouping | Source/camera/category/item and localized box IoU ≥0.5 tests | Unknown boxes remain separate; spatial overlap does not prove identity |
| PPE uncertainty, exact evidence/source/applicability checks | Existing video safety/citation/source tests | Model perception and site applicability need actual evaluation |
| Correct cached-token and image-cost handling | [Cost accounting](../backend/src/safety_app/infrastructure/usage_cost.py); provider-shaped and ledger tests | Billed cost remains null until external invoice reconciliation |
| Local startup without paid calls or training packages | [Launcher](../start); cold-install receipt; disabled paid flag by default | Python bootstrap, Node and network prerequisites disclosed |
| Append-only server human decisions | Decision endpoint, report test and browser check | Local operator identity is not multiuser enterprise authentication |
| Embedded HTML/JSON export after budget exhaustion | [Report export](../backend/src/safety_app/modules/safety_review/run_report.py); report and XSS tests | All stored concerns retained, including pending ones |
| Generated contracts / regression checks | `scripts/generate_contracts.py --check`; documented test commands and receipts | Final check receipts in submission/evidence |
| 24 independently labeled clips, 8/8/8 | [Candidate manifest](../demo/manifest.json), pilot form | 24 candidates prepared; balance, independent labels and category gaps open |
| Matched baseline/full comparison, temporal IoU ≥0.5 | [Evaluation script](../scripts/evaluate_course.py), evaluation protocol | No actual accuracy, latency or cost result yet |
| Counterbalanced manual/assisted pilot | [Pilot protocol](../submission/pilot/README.md), local form | Contact must perform tasks; no fabricated timings |

## Final release gates

- [x] Report and slides rebuilt from measured evidence (2026-10-25): token counts from the audit log, six-screenshot trace, prompt-injection test output, cost before/after.
- [ ] Timed self-trial recorded in `submission/evidence/self-trial.json`, then report and slides rebuilt.
- [x] Public GitHub repository published from the allowlist package; GitHub clone started with `.env` one folder up in 63 s (`submission/evidence/fresh-clone.json`).
- [ ] `scripts/check_submission.py` passes G1–G7.
- [ ] Upload only report.html, slides.html and the optional 2–3 minute video to Canvas before Monday 6 p.m. (no URL field, no other files).
- [ ] Still open after submission: independent 8/8/8 labels and a pilot with an outside safety coordinator.
