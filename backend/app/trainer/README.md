# Industrial detector trainer — start here

This folder owns the **label → split → train → evaluate → register** workflow. It is a separate component inside the existing project, with one dependency environment and one application. Live video inference remains in `backend/app/cv/`.

**COCO is the annotation format; RF-DETR is the detector being trained.** The trainer does not modify the original COCO dataset. It fine-tunes a detector on your human-labeled industrial footage, exported as COCO JSON.

## File tree

```text
backend/app/trainer/
├── README.md                  ← this map
├── workspace.py               ← projects, clips, labels, split checks, frozen exports
├── api.py                     ← UI endpoints, local job runner, logs, model handoff
├── cli/
│   ├── dataset.py             ← prepare frames and export reviewed COCO datasets
│   ├── train.py               ← train RF-DETR or evaluate a saved checkpoint
│   └── register.py            ← check a trained checkpoint and publish its model manifest
└── docs/
    ├── labeling-workspace.md  ← step-by-step guide to the built-in UI
    └── cv-industrial.md       ← classes, tracking, and command-line training guide

frontend/src/trainer/
├── LearningWorkspace.tsx      ← labeling screen and training controls
├── labelGeometry.ts           ← drawing, moving and resizing boxes
├── labelGeometry.test.ts      ← bounds/drag regression tests
└── trainer.css                ← trainer-specific UI styling

tests/trainer/
├── test_workspace.py          ← isolation, labels, frozen snapshots and local jobs
└── test_dataset.py            ← COCO export and source-level split validation
```

## Follow the workflow

The Labeling module also provides recursive intake copying, blind detector-assisted frame review, explicit per-box decisions and a local validation report. See [the labeling guide](docs/labeling-workspace.md). The supplied behavior labels never become object-box labels.

1. **UI:** `frontend/src/trainer/LearningWorkspace.tsx` uploads separate footage and saves human boxes through `/api/learning/...`.
2. **Dataset:** `workspace.py` owns source hashes, recording groups, frame review and train/validation/test assignments. Freezing exports a checked snapshot.
3. **Jobs:** `api.py` starts a separate local Python process. Its training input contains only train and valid files; held-out test files are omitted.
4. **Training/evaluation:** `cli/train.py` calls RF-DETR. The test evaluation is a separate action and seals further training on the project.
5. **Model handoff:** `cli/register.py` uses the runtime detector's safe checkpoint loader, checks class metadata and creates a manifest under `data/artifacts/models/`. The review app then selects that registered model through its existing config API.

For the full workflow, read [the labeling guide](docs/labeling-workspace.md). For terminal commands, read [the industrial CV guide](docs/cv-industrial.md).

## Code versus generated data

The code above is separate from the files created by the tool:

| Location, relative to project root | What it contains |
|---|---|
| `data/input/` | Existing analysis videos; exact copies are refused as learning inputs |
| `data/learning/<project-id>/sources/` | Newly uploaded learning footage |
| `data/learning/<project-id>/images/` | Sampled frames for labeling |
| `data/learning/<project-id>/project.json` | Labels, review state, source groups, split assignments and job records |
| `data/learning/<project-id>/snapshot/` | Frozen COCO train/valid/test files |
| `data/learning/<project-id>/jobs/` | Per-run training inputs, checkpoints, evaluation outputs and logs |
| `data/training/` | Earlier command-line dataset preparations; separate from built-in learning projects |
| `data/artifacts/models/` | Runtime pretrained weights and registered custom checkpoint manifests |

This reorganization moves code and guides only. Existing clips, labels, snapshots and saved checkpoints keep their paths. Dataset isolation is enforced by source/group checks and frozen splits, not by putting code in another repository. It reduces known leakage but cannot guarantee absence of bias or verify human labels.

## Commands

Run from `/Users/masonlewis/Dev/video-analysis` (the repository root):

```sh
.venv/bin/python backend/app/trainer/cli/dataset.py --help
.venv/bin/python backend/app/trainer/cli/train.py --help
.venv/bin/python backend/app/trainer/cli/register.py --help
.venv/bin/python -m pytest tests/trainer -q
```

The old `scripts/industrial_dataset.py`, `scripts/train_industrial_detector.py`, and `scripts/register_industrial_model.py` commands are small compatibility wrappers, so existing instructions and commands still work. The API URLs remain unchanged. Old links under `docs/` point to the relocated guides.

## Should this become its own project?

Keep one root while the same local app labels footage, trains models and loads them for review. Shared source decoding, checkpoint loading, contracts and dependencies stay straightforward, while the folder boundary makes ownership clear.

A separate project becomes useful when training runs on another machine, has its own release cycle or dependency requirements, or needs to work with several unrelated review apps. At that point, export frozen COCO datasets to the training project and return versioned checkpoint manifests. A second root by itself would not make the dataset less biased.
