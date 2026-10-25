# Detector manifest

Pinned RF-DETR 1.11.1 uses the standard Small/Nano components. Code and weights are designated Apache-2.0 by [RF-DETR](https://github.com/roboflow/rf-detr). Plus/XL/2XL are never selected.

| Variant | Official weights | Registry MD5 | Locally computed SHA256 |
| --- | --- | --- | --- |
| Small | https://storage.googleapis.com/rfdetr/small_coco/checkpoint_best_regular.pth | fb37061c1af7bace359c91b723a8d5c1 | d81979a9213a2109345158ce9232668df4c1ae52e9b8db3f2ec0a8cbad959b33 |
| Nano | https://storage.googleapis.com/rfdetr/nano_coco/checkpoint_best_regular.pth | fb6504cce7fbdc783f7a46991f07639f | Not downloaded; computed on load |

The exact COCO class map is persisted in each run after model loading, and in `data/artifacts/runs/detector-smoke.json`. Person is class 1; truck is class 8 and never relabeled forklift. These weights have no forklift, PPE, fire, smoke, spill, fall, or injury classes.

Local 2026-10-03 smoke on Apple M5 (10 CPU cores, 16 GiB RAM, macOS 27.0, arm64) executed actual Small weights against a 640×360 blank image: MPS first inference 2.450 seconds; CPU first inference 0.160 seconds. Both returned zero detections. This verifies adapter execution and CPU fallback availability; it does not measure person accuracy, live throughput, or safety performance. MPS startup/compilation overhead makes a single first frame unsuitable for hardware comparison.

Run `uv run python scripts/smoke_cv.py --variant small --device cpu` explicitly. Weights are lazily downloaded into `data/artifacts/models/`; ordinary tests and API startup do not download weights or invoke hosted models. CUDA and Nano remain unverified on this machine. Custom weights require an explicit class map and license; no training is included.
