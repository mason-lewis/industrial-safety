"""Stock person detection on retained stills; no custom models, labels or training."""
import hashlib
import importlib.util
import math
from pathlib import Path
import threading

from safety_app.domain.models import FrameRef
from safety_app.domain.people import PersonDetection, PersonDetectionResult, PersonFrame


def record_id(run_id, frame_id):
    return hashlib.sha256(f'people-v1:{run_id}:{frame_id}'.encode()).hexdigest()


class StockPersonDetector:
    def __init__(self, root):
        self.root=Path(root);self.model=None;self.lock=threading.Lock()

    def warmup(self):
        """Load verified local weights before camera playback; discard warmup output."""
        if self.model is None:
            import numpy as np
            self.predict(np.zeros((512,512,3),dtype=np.uint8),.95)

    def predict(self, image, confidence=.5):
        with self.lock:
            if self.model is None:
                if not importlib.util.find_spec('rfdetr'):
                    raise FileNotFoundError('Person detector unavailable: install the people extra and prepare the stock weights.')
                weights=self.root/'data/artifacts/models/rf-detr-small.pth'
                if not weights.exists():
                    weights=Path(__file__).resolve().parents[5]/'data/artifacts/models/rf-detr-small.pth'
                if not weights.is_file():
                    raise FileNotFoundError('Stock person weights are missing. Run scripts/prepare_people.py once; no custom weights are accepted.')
                from rfdetr.assets.model_weights import ModelWeights
                asset=ModelWeights.from_filename('rf-detr-small.pth')
                with weights.open('rb') as stream:checksum=hashlib.file_digest(stream,'md5').hexdigest()
                if not asset or not asset.md5_hash or checksum!=asset.md5_hash:
                    raise ValueError('Person weights do not match the stock COCO model; custom or modified weights are not accepted.')
                from app.cv.detector import RFDETRDetector
                model=RFDETRDetector('small',weights=str(weights))
                model.load()
                self.model=model
            found=self.model.predict(image,confidence)
            rows=[(list(box),float(confidence)) for box,confidence,class_id in zip(found.xyxy,found.confidence,found.class_id)
                  if self.model.labels.get(int(class_id))=='person']
            provenance={key:value for key,value in self.model.provenance.items() if key!='class_map'}
            return rows,provenance


def detect_people(detector, image, confidence=.5, key='preview'):
    """One person-only inference contract for transient preview and saved evidence."""
    result = PersonDetectionResult(status='available', confidence_threshold=confidence)
    try:
        height, width = image.shape[:2]
        rows, provenance = detector.predict(image, confidence)
        valid = []
        for box, score in rows:
            if len(box) != 4 or not all(math.isfinite(v) for v in [*box, score]) or not confidence <= score <= 1:
                continue
            x1, y1, x2, y2 = box
            x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
            y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
            if x2 > x1 and y2 > y1:
                valid.append(((x1/width, y1/height, (x2-x1)/width, (y2-y1)/height), score))
        valid.sort(key=lambda row: (-row[1], row[0]))
        result.people = [PersonDetection(id=f'{key[:12]}-p{i+1}', box=box, confidence=score)
                         for i, (box, score) in enumerate(valid[:12])]
        result.omitted_count = max(0, len(valid)-12)
        result.provenance = {**provenance, 'confidence_threshold': confidence,
                             'input_width': width, 'input_height': height}
    except FileNotFoundError as exc:
        result.status = 'unavailable'; result.detail = str(exc)
    except Exception as exc:
        result.status = 'failed'; result.detail = f'Local person detection failed ({type(exc).__name__}); inspect the original image.'
    return result


class PeopleEvidence:
    def __init__(self,root,db,media,detector=None):
        self.db=db;self.media=media
        self.detector=detector or StockPersonDetector(root)

    def get(self,run_id,frame_id):
        frame=FrameRef.model_validate(self.db.get('frame',frame_id))
        try:record=PersonFrame.model_validate(self.db.get('person_frame',record_id(run_id,frame_id)))
        except KeyError:
            return PersonFrame(id=record_id(run_id,frame_id),review_run_id=run_id,frame_id=frame.id,
                media_id=frame.media_id,timestamp_ms=frame.timestamp_ms,content_hash=frame.content_hash,
                status='not_analyzed',detail='No retained person detection for this frame. Do not infer an empty scene.')
        if (record.review_run_id,record.frame_id,record.media_id,record.content_hash,record.timestamp_ms)!=(run_id,frame.id,frame.media_id,frame.content_hash,frame.timestamp_ms):
            raise ValueError('Person evidence provenance does not match its retained frame')
        return record

    def capture(self,run_id,frames,enabled,cancelled,confidence=.5):
        import cv2,numpy as np
        records=[]
        for frame in frames:
            if cancelled():raise InterruptedError('Person detection cancelled')
            record=self.get(run_id,frame.id)
            if record.status!='not_analyzed':
                records.append(record);continue
            record.confidence_threshold=confidence
            if not enabled:
                record.status='disabled';record.detail='Local person detection disabled for this offline fixture.'
            else:
                try:
                    raw=self.media.model_image(frame.id)
                    image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
                    if image is None:raise ValueError('Cannot decode retained person evidence')
                    result=detect_people(self.detector,image,confidence,record.id)
                    record=record.model_copy(update={name:getattr(result,name) for name in PersonDetectionResult.model_fields})
                except FileNotFoundError as exc:record.status='unavailable';record.detail=str(exc)
                except Exception as exc:
                    record.status='failed';record.detail=f'Local person detection failed ({type(exc).__name__}); inspect the original image.'
            if cancelled():raise InterruptedError('Person detection cancelled')
            self.db.save('person_frame',record,immutable=True)
            records.append(record)
        return records

    def for_run(self,run):
        import json
        with self.db.connect() as db:
            rows=db.execute("SELECT body FROM objects WHERE kind='person_frame' AND json_extract(body,'$.review_run_id')=?",(run.id,)).fetchall()
        result=[]
        for row in rows:
            item=PersonFrame.model_validate(json.loads(row['body']))
            if item.media_id!=run.media_id or not run.start_ms<=item.timestamp_ms<=run.end_ms or (run.kind=='live' and item.timestamp_ms>run.cutoff_ms):
                raise ValueError('Person evidence outside review scope')
            result.append(self.get(run.id,item.frame_id))
        return sorted(result,key=lambda r:(r.timestamp_ms,r.frame_id))
