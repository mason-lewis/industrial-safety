# Adversary review: industrial CV implementation

Verdict: reliable within the tested scope for filtering, explicit group matching, and class-separated tracking. Forklift recognition and training quality remain unverified. This review covers configuration, association counterexamples, checkpoint handling, dataset export validation and UI behavior; it does not certify safety accuracy or a trained model.

| Claim | Status | Evidence or correction | Confidence |
|---|---|---|---|
| Existing custom loading could guess the classifier size from a class map | Incorrect approach, corrected | Loader now uses RF-DETR `from_checkpoint` and compares exact embedded names/zero-based IDs. Mismatch test rejects relabeling without head resizing. Installed RF-DETR 1.11.1 source and [official FAQ](https://rfdetr.roboflow.com/latest/faq/) support architecture inference. | High for code behavior; real forklift checkpoint not tested |
| Existing association could carry an ID across raw classes | Supported finding, corrected | Installed trackers 2.6.1 associates by IoU without class gating. Identical overlapping person/vehicle synthetic boxes now use separate trackers and unique IDs. | High for reproduced counterexample |
| Every tracker result is confirmed | Incorrect, corrected | Library returns unmatched/immature boxes with ID -1. Adapter discards these before rules; actual-library tests cover thresholds, confirmation and short loss. | High |
| Broad vehicles groups add forklift recognition | Incorrect | Groups only match configured labels. Raw labels remain unchanged; absent labels cannot be fabricated. [RF-DETR FAQ](https://rfdetr.roboflow.com/latest/faq/) explains trained category replacement. | High |
| Industrial filtering makes learned CV more accurate | Unverified | Tests establish removal of irrelevant outputs and per-class thresholds. No measured recognition improvement, recall or precision claim. | High confidence in this limitation |
| Tracking is stronger under all industrial conditions | Conditional | Class separation removes cross-class matching, but same-class swaps, label flicker, missed detections and long occlusions remain. [ByteTrack documentation](https://trackers.roboflow.com/latest/trackers/bytetrack/) describes threshold/retention controls. | Medium; synthetic tests do not establish site performance |
| The dataset is ready for training | Incorrect until labeled | 46 extracted images from 2 source clips have zero annotations. Export refuses unlabeled data and requires reviewed person/forklift examples in train and validation. | High |
| Clip splits establish generalization | Conditional | Export keeps a source hash in one partition. Related recordings, repeated scenes and incomplete annotation can still bias results; two sources lack a separate test partition. | High confidence in limitation |
| Forklift detection works now | Unverified | No trained checkpoint, human ground truth, held-out forklift evaluation or operational performance evidence exists. UI explicitly reports unavailable for COCO. | High confidence that evidence is missing |

No hosted AI requests or training run were performed. A blank-image checkpoint smoke test establishes execution compatibility only. The next consequential check is human labeling followed by held-out, per-class and rule-level evaluation on independently sourced footage.

## Separate labeling workspace review

Verdict: the isolation policy is supported within the tested software scope. Absence of all dataset bias is unverified and is not claimed.

| Claim | Status | Evidence or correction | Confidence |
|---|---|---|---|
| Analysis videos stay out of this training workspace | Supported for exact source copies | Upload, freeze and job checks compare source SHA256 with the analysis library; a renamed-copy counterexample is rejected. | High |
| Neighboring frames cannot cross splits through the tool | Supported | Whole source clips and declared recording groups have one split; group conflict tests fail. | High |
| Test images are excluded from training input | Supported | Training job builds a folder with train and valid only; test directory is absent; `run_test=False` is explicit. Job orchestration test inspects the actual generated input folder. | High |
| Test results cannot be used for repeated tuning through this project | Supported within the tool | Starting evaluation seals further training and locks the test to one checkpoint hash. Changed-checkpoint and post-test training attempts are rejected. External workflows remain outside this enforcement. | High |
| Frozen labels and splits remain unchanged | Supported through UI/API | Edit routes reject frozen projects; content hashes reject filesystem snapshot drift before jobs. | High |
| Human review proves labels are correct | Unverified | Explicit review and verified-empty controls record an attestation, not annotation quality verification. | High confidence in limitation |
| This guarantees an unbiased detector | Incorrect if stated absolutely | Re-encoded copies, mislabeled recording groups, repeated scenes, unrepresentative samples and faulty labels remain possible. See [Google's split guidance](https://developers.google.com/machine-learning/crash-course/overfitting/dividing-datasets). | High |
| The full forklift training outcome has been validated | Unverified | Training dependencies import, UI saves and software guardrails are tested. No new human-labeled forklift footage, real training run or measured accuracy exists. | High confidence in missing evidence |
