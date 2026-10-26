# Submission goals (MGT 409 final project)

Source: [project requirements](https://zlisto.github.io/mgt_409_fa26/project_requirements.html). Due **Monday, October 26, 2026, 6 p.m.** on Canvas. **Canvas takes files only** (instructor update, 25 Oct): submit `report.html`, `slides.html` and optionally the demo video, nothing else. Both HTML files must contain the GitHub URL so staff can clone it, and must say whether the repo is public or private.
`scripts/check_submission.py` checks gates G1–G7 offline (no model calls) and prints PASS/FAIL with the reason.

Loop prompt:

```
/loop Run ./scripts/uv run --no-sync python scripts/check_submission.py; fix the first failing gate in docs/submission-goals.md without paid model calls; rerun; stop when G1–G7 pass.
```

| Gate | Rubric row (points) | Passes when |
|---|---|---|
| G1 Report form | Report design (2); every report row | `report.html` has sections 1–8 in the required order; prints to 10–15 letter pages (headless Chrome print-to-PDF); every `<img>` is a `data:` URI; no relative links; repository URL, clone command and public/private status present; no "pending", "TBD" or "not captured" text |
| G2 Report evidence | Agent design (10), Tokenomics (8), Safety (8), Impact (5) | §4 has ≥4 screenshots tracing one run; §5 has input, cached-input and output token columns from `token-usage.json` plus 100- and 10,000-user rows and a measured cost-cutting comparison; §6 embeds real test output of a defense; §7 has a measured self-trial table from `submission/evidence/self-trial.json` |
| G3 Slides | Slides content (6) and design (2) | `slides.html` has ≤10 slides; loads reveal.js from a CDN; contains the GitHub URL and says the repo is public; embeds the agent diagram and ≥2 screenshots as `data:` URIs; shows cost per run and time saved |
| G4 Repository hygiene | It works (15): clean repo, no secrets | README "Quick start" within the first 40 lines; `.env.example` has key names with empty values; the allowlist package holds no `.env`, API key, SQLite file, model weights or file over 50 MB |
| G5 Product checks | It works (15) | The latest receipt in `submission/evidence/verification-*.json` is newer than every changed source file and records passing Python tests, frontend tests, build, contract check and fixtures |
| G6 Fresh clone | Staff can run it (5) | `submission/evidence/fresh-clone.json` shows a clone of the package started healthy in under 600 s with `.env` one folder above |
| G7 Documentation | Keep guides aligned (AGENTS.md) | `harness.md` and `docs/course-requirements.md` were updated after the latest report build |

**Re-publish after any rebuild** (the workspace has no Git history; the public repo is a separate clone):

```
python3 scripts/package_submission.py /tmp/pkg-NEW && cd PATH_TO_CLONE_OF/industrial-safety && rsync -a --delete --exclude .git /tmp/pkg-NEW/ ./ && git add -A && git commit -m "Update submission" && git push
```

Demo video loop: `/loop Run ./scripts/uv run --no-sync python scripts/demo_video/check_video.py SCRATCH/demo; fix the first failure (re-edit with --preview first; never start another paid run; TTS is cached); stop when all pass.`

G8 (manual, not looped): the public GitHub repository is pushed and matches the local build; upload **only** `report.html`, `slides.html` and the optional 2–3 minute demo video to Canvas. Any other file is penalized.

The self-trial in G2 needs a person: Mason times a few clips by hand and with the app using [the worksheet](../submission/pilot/label-and-time.html), then saves its JSON as `submission/evidence/self-trial.json`. The loop must not invent these numbers.
