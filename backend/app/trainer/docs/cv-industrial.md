# Industrial classes, tracking, and forklift training

Computer vision settings now filter raw detector classes before tracking and rules. Industrial focus selects people and configured vehicle members; irrelevant COCO classes are unchecked. Per-class confidence replaces the default detector threshold for that class. This reduces irrelevant outputs but does not improve learned recognition, inference speed, or establish precision/recall. AI scene review still sees original scene images.

Rules can target `group:vehicles` or `group:industrial_vehicles`. Groups expand explicit raw labels and preserve specific labels in detection, track, and AI metadata. A truck remains a truck; it never becomes a forklift. Members not present in the active detector cannot produce detections. A proximity rule needs available, enabled classes on both sides.

Tracking associates each raw class separately and assigns globally unique IDs. This prevents cross-class association, but same-class objects can still swap IDs or receive new IDs after occlusion. IDs are temporary observations, not person identities. Only confirmed, currently observed boxes enter rules. Lost-track retention never produces ghost observations or proves clearance. Confirmation counts successful associations after the initial detection, so a value of 1 requires at least two observations.

Class selection, group membership, detector, FPS, or tracker changes start a new generation and discard live track/rule state. Confidence-only changes preserve the generation. Saved disk history remains available. All adjustments have (?) help in the UI.

## Prepared footage

`data/training/industrial-v1/labeling-bundle.zip` contains six JPEG frames from the public factory-floor example, sampled every approximately 3 source seconds. `manifest.json` records source SHA256, filename, timestamp and frame ID. `annotations.coco.json` supplies category definitions and image geometry, with **no object annotations**. These are unlabeled frames, not verified negative images, and are not ready for training.

Import the COCO file and images into a COCO-compatible labeling tool. Label every visible instance of the declared categories, including people. Distinguish forklift, reach truck and pallet jack using visible evidence; do not invent labels for ambiguous equipment. Review empty images explicitly. Preserve filenames and export bounding-box annotations. If the sampled clips lack forklift examples, collect footage containing forklifts under relevant lighting, viewpoints, distances and occlusions. This extraction makes no claim that forklifts occur in the selected frames.

Fine-tuning replaces the pretrained category set, so retain labeled people and all classes that your rules need. See the [RF-DETR FAQ](https://rfdetr.roboflow.com/latest/faq/) and [training documentation](https://rfdetr.roboflow.com/latest/learn/train/).

## Export reviewed labels

Create a JSON file mapping each complete source SHA256 from the manifest to `train`, `valid`, or `test`. Never randomly split neighboring frames. This prepared dataset has one source; add at least two independent sources before creating separate training, validation and held-out test partitions. Different files can still show the same scene or event, so manually separate related recordings as well.

```sh
.venv/bin/python backend/app/trainer/cli/dataset.py split \
  --dataset data/training/industrial-v1 \
  --annotations /absolute/path/to/reviewed-coco.json \
  --splits /absolute/path/to/source-splits.json \
  --output data/training/industrial-reviewed-v1 \
  --confirm-reviewed
```

The exporter rejects empty annotations, malformed boxes, unknown references, missing files, filename drift, incomplete source assignments, and train/validation partitions missing person or forklift positives. The confirmation attests that every frame was human reviewed; software cannot verify annotation truth or completeness. Output uses RF-DETR's `train`, `valid`, and optional `test` COCO directory format with split provenance.

To prepare more source frames, use a new output directory:

```sh
.venv/bin/python backend/app/trainer/cli/dataset.py prepare \
  --input data/input --output data/training/industrial-v2 \
  --interval-s 3 --max-per-clip 100
```

## Train and evaluate locally

Training/evaluation require RF-DETR's optional training dependencies. The pinned training extra is now included in this project and installed by `./scripts/uv sync --frozen`. Training itself has not been run on real labeled forklift footage. Training may download pretrained assets and uses substantial local compute; no hosted AI service is needed.

First validate without launching training:

```sh
.venv/bin/python backend/app/trainer/cli/train.py \
  --dataset data/training/industrial-reviewed-v1 \
  --output data/artifacts/training/industrial-v1
```

Add `--run-training --device mps --epochs 30 --batch-size 2` to launch on this Mac, or choose CPU/CUDA as appropriate. Those values are starting settings, not validated hyperparameters. After training, score the selected checkpoint on an independent test source:

```sh
.venv/bin/python backend/app/trainer/cli/train.py \
  --dataset data/training/industrial-reviewed-v1 \
  --output data/artifacts/evaluation/industrial-v1 --device cpu \
  --evaluate-weights /absolute/path/to/checkpoint.pth
```

Inspect per-class performance, missed/false forklifts, person retention, identity switches and actual rule-trigger recall on representative footage. COCO metrics alone do not validate a safety rule or operational deployment. No forklift accuracy has been measured in this implementation.

## Register a trained checkpoint

Use the **exact class names and order embedded in the checkpoint**, including spaces where relevant. Registration safely loads the checkpoint without pickle fallback, checks its architecture and class map without resizing the trained head, and performs blank-image inference before publishing a checksum manifest.

```sh
.venv/bin/python backend/app/trainer/cli/register.py \
  --weights /absolute/path/to/checkpoint.pth \
  --classes person forklift 'reach truck' 'pallet jack' car truck bus \
  --id industrial-v1 --name 'Industrial detector v1' \
  --license 'Your checkpoint license' --device cpu
```

The class arguments above are an example, not a promise about a future checkpoint's metadata. A mismatch fails registration. Model manifests and checkpoint paths are confined to `data/artifacts/models/`. Reload the app to discover the registered model, select it in Computer vision, and save. Activation preflights custom inference before committing the selection. Registration establishes compatibility, not site accuracy. Legacy checkpoints without compatible metadata must be re-exported properly; unsafe pickle fallback is not enabled.

For the built-in UI with separate footage and stricter frozen test isolation, use [the labeling workspace](labeling-workspace.md).
