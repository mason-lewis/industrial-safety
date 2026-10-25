"""Local pre-labeling and validation. Proposals never become human truth automatically."""
import hashlib
import json
import math
from pathlib import Path
import shutil
import time
from uuid import uuid4
import cv2
from app.trainer.models import LearningIntakeClip, LearningValidationReport

EXTENSIONS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.mpeg', '.m4v'}


def intake_catalog(ws):
    root = (ws.root / 'data/cv-intake/incoming').resolve()
    if not root.is_dir():
        return []
    usage={}
    with ws.lock:
        for project in ws.catalog():
            for clip in project['clips']:
                intake_id=clip.get('intake_source',{}).get('id')
                if intake_id:
                    frames=[f for f in project['frames'] if f['clip_id']==clip['id']]
                    usage.setdefault(intake_id,[]).append(dict(project_id=project['id'],clip_id=clip['id'],split=clip['split'],
                        frames=len(frames),reviewed=sum(bool(f['reviewed']) for f in frames)))
    rows = []
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS or not path.resolve().is_relative_to(root):
            continue
        relative = path.relative_to(root).as_posix()
        hints = {part.casefold() for part in path.relative_to(root).parts[:-1]}
        split = 'test' if 'test' in hints else 'valid' if hints & {'valid', 'val', 'validation'} else 'train' if 'train' in hints else None
        # Keep the entire intake together conservatively; names/categories never define sessions.
        collection_id=hashlib.sha256(str(root).encode()).hexdigest()[:12]
        suggested_split=split or 'train'
        rows.append(LearningIntakeClip(id=hashlib.sha256(relative.encode()).hexdigest(), name=path.name,
                    relative_path=relative, bytes=path.stat().st_size, source_split=split,
                    source_category=path.parent.name,collection_id=collection_id,suggested_split=suggested_split,
                    suggested_group=f'collection-{collection_id}-{suggested_split}',imported_in=usage.get(hashlib.sha256(relative.encode()).hexdigest(),[])).model_dump(mode='json'))
    # Opaque ordering avoids presenting behavior folders as contiguous labeling batches.
    return sorted(rows, key=lambda row:row['id'])


def import_intake(ws, project_id, intake_id, group=None, split=None):
    row = next((r for r in intake_catalog(ws) if r['id'] == intake_id), None)
    if row is None:
        raise ValueError('Intake clip no longer exists; refresh the list')
    split=split or row['suggested_split']
    automatic=group is None
    group=f"collection-{row['collection_id']}-{split}" if group is None else group
    if row['source_split'] == 'test' and split != 'test':
        raise ValueError('Supplied test footage stays held out. Choose Held-out test for this clip.')
    if row['bytes'] > 2*1024**3:
        raise ValueError('Import limit is 2 GB per clip')
    source = (ws.root / 'data/cv-intake/incoming' / row['relative_path']).resolve()
    root = (ws.root / 'data/cv-intake/incoming').resolve()
    if not source.is_relative_to(root):
        raise ValueError('Invalid intake source')
    # import_clip moves its input. Copy to a private upload first; never move originals.
    folder = ws.folder(project_id) / 'uploads'
    folder.mkdir(exist_ok=True)
    staged = folder / (uuid4().hex + source.suffix.lower())
    try:
        shutil.copy2(source, staged)
        with ws.lock:
            p = ws.import_clip(project_id, staged, row['name'], group, split)
            p['clips'][-1]['intake_source'] = row
            p['clips'][-1]['group_basis'] = 'automatic_collection' if automatic else 'human'
            ws.write(p)
        return p
    finally:
        staged.unlink(missing_ok=True)


def predict_boxes(ws, project_id, frame, classes, detector, confidence):
    path = (ws.folder(project_id) / 'images' / frame['file_name']).resolve()
    if not path.is_relative_to((ws.folder(project_id) / 'images').resolve()):
        raise ValueError('Invalid frame image')
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError('Frame image is missing or cannot be decoded')
    detections = detector.predict(image, confidence)
    height, width = image.shape[:2]
    boxes = []
    for xyxy, class_id, score in zip(detections.xyxy, detections.class_id, detections.confidence):
        label = detector.labels.get(int(class_id))
        if label not in classes or not math.isfinite(float(score)):
            continue
        x1, y1, x2, y2 = [float(v) for v in xyxy]
        if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
            continue
        x1, x2 = max(0., min(1., x1 / width)), max(0., min(1., x2 / width))
        y1, y2 = max(0., min(1., y1 / height)), max(0., min(1., y2 / height))
        if x2 > x1 and y2 > y1:
            boxes.append(dict(id=uuid4().hex, label=label, bbox=[x1, y1, x2-x1, y2-y1], score=float(score)))
    if len(boxes) > 200:
        raise ValueError('More than 200 proposals; raise the confidence threshold or review manually')
    return boxes, hashlib.sha256(path.read_bytes()).hexdigest()


def assist_frame(ws, project_id, frame_id, detector, confidence):
    with ws.lock:
        p = ws.read(project_id)
        ws.editable(p)
        frame = next((f for f in p['frames'] if f['id'] == frame_id), None)
        if frame is None:
            raise ValueError('Unknown frame')
        if frame.get('proposal_run'):
            return p  # One immutable proposal set per frame. Retain human corrections.
        if frame['reviewed'] or frame['boxes']:
            raise ValueError('This frame already has human labels; review them without replacing them')
        revision = frame.get('revision', 0)
        classes = list(p['classes'])
    boxes, digest = predict_boxes(ws, project_id, frame, classes, detector, confidence)
    unsupported = [c for c in classes if c not in detector.labels.values()]
    with ws.lock:
        p = ws.read(project_id)
        ws.editable(p)
        frame = next((f for f in p['frames'] if f['id'] == frame_id), None)
        if frame is None or frame.get('revision', 0) != revision or frame.get('proposal_run'):
            raise ValueError('Frame changed during inference; refresh before reviewing')
        frame.update(proposals=boxes, proposal_decisions={}, revision=revision+1,
                     proposal_run={'id':uuid4().hex, 'created_at':time.time(), 'confidence':confidence,
                                   'image_sha256':digest, 'detector_provenance':dict(detector.provenance),
                                   'unsupported_classes':unsupported, 'supported_classes':list(detector.labels.values()), 'method':'local_detector'})
        ws.write(p)
        return p


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    intersection = max(0, min(ax+aw, bx+bw)-max(ax, bx)) * max(0, min(ay+ah, by+bh)-max(ay, by))
    union = aw*ah + bw*bh - intersection
    return intersection / union if union else 0


def validation_signature(p):
    clips={c['id']:c for c in p['clips']}
    reviewed=[{'id':f['id'],'boxes':f['boxes'],'verified_empty':f['verified_empty']} for f in p['frames']
              if f['reviewed'] and clips[f['clip_id']]['split']=='valid']
    return hashlib.sha256(json.dumps({'classes':p['classes'],'frames':reviewed},sort_keys=True).encode()).hexdigest()


def validate_current(ws, project_id, detector, confidence, maximum):
    with ws.lock:
        p = ws.read(project_id)
        clips = {c['id']:c for c in p['clips']}
        frames = [dict(f) for f in p['frames'] if f['reviewed'] and clips[f['clip_id']]['split'] == 'valid']
        classes = list(p['classes'])
    if not frames:
        raise ValueError('Review frames from a Validation recording first. Held-out test is not used for this check.')
    counts = {c:dict(true_positive=0, false_positive=0, false_negative=0) for c in classes}
    examples = []
    image_hashes = {}
    selected = frames[:maximum]
    for frame in selected:
        predictions, digest = predict_boxes(ws, project_id, frame, classes, detector, confidence)
        image_hashes[frame['id']] = digest
        for label in classes:
            truth = [b for b in frame['boxes'] if b['label'] == label]
            predicted = sorted([b for b in predictions if b['label'] == label], key=lambda b:-b['score'])
            unmatched = set(range(len(truth)))
            false = []
            for box in predicted:
                best = max(unmatched, key=lambda i:iou(box['bbox'], truth[i]['bbox']), default=None)
                if best is not None and iou(box['bbox'], truth[best]['bbox']) >= .5:
                    counts[label]['true_positive'] += 1
                    unmatched.remove(best)
                else:
                    counts[label]['false_positive'] += 1
                    false.append(box)
            counts[label]['false_negative'] += len(unmatched)
            if unmatched or false:
                examples.append(dict(frame_id=frame['id'], label=label, missed=[truth[i] for i in sorted(unmatched)], false_detections=false))
    results = []
    for label, row in counts.items():
        tp, fp, fn = row['true_positive'], row['false_positive'], row['false_negative']
        results.append(dict(label=label, **row, precision=tp/(tp+fp) if tp+fp else None, recall=tp/(tp+fn) if tp+fn else None))
    report = LearningValidationReport(id=uuid4().hex, created_at=time.time(), frame_ids=[f['id'] for f in selected],
        skipped_frames=len(frames)-len(selected), confidence=confidence, iou_threshold=.5,
        detector_provenance=dict(detector.provenance), label_snapshot_sha256=validation_signature(p), frame_image_sha256=image_hashes,
        classes=results, examples=examples,
        limitations=['Reviewed validation frames only; not held-out test or site-wide accuracy.',
                     'First reviewed validation frames up to the selected cap; not a representative sampling guarantee.',
                     'One-to-one score-ordered box matching at IoU 0.5; precision/recall at one threshold, not mAP.',
                     'Detector-assisted labels require careful independent human correction, including missed objects.']).model_dump(mode='json')
    with ws.lock:
        state = ws.read(project_id)
        # Do not publish a report against changed labels or split assignments.
        if state['clips'] != p['clips'] or state['frames'] != p['frames'] or state['classes'] != p['classes']:
            raise ValueError('Labels or splits changed during validation; rerun with the current version')
        state.setdefault('validation_runs', []).append(report)
        ws.write(state)
    return report
