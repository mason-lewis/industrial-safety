"""Content-addressed local frames. Existing evidence files stay in their original store."""
import hashlib
from pathlib import Path
from safety_app.domain.models import MediaRef,FrameRef,FrameBatch,EvidenceBundle
class MediaStore:
    def __init__(self,root,db):self.root=Path(root);self.db=db;(self.root/'data/clips').mkdir(parents=True,exist_ok=True)
    def add_frame(self,media_id,frame_id,timestamp_ms,raw):
        digest=hashlib.sha256(raw).hexdigest();id=f'{media_id}-{frame_id}'
        path=self.root/'data/clips'/f'{digest}.jpg'
        if not path.exists():path.write_bytes(raw)
        ref=FrameRef(id=id,media_id=media_id,frame_id=str(frame_id),timestamp_ms=timestamp_ms,content_hash=digest,image_url=f'/api/media/frames/{id}')
        self.db.save('frame',ref,immutable=True);return ref
    def image(self,id):
        ref=FrameRef.model_validate(self.db.get('frame',id));path=self.root/'data/clips'/f'{ref.content_hash}.jpg'
        if not path.is_file():raise FileNotFoundError('Retained frame is missing')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ref.content_hash:raise ValueError('Evidence hash mismatch')
        return raw
    def model_image(self,id):
        """Resize model input to 1024 pixels. Preserve the original retained bytes."""
        import cv2,numpy as np
        raw=self.image(id);image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
        if image is None:raise ValueError('Cannot decode retained image')
        h,w=image.shape[:2]
        if max(h,w)<=1024:return raw
        scale=1024/max(h,w);small=cv2.resize(image,(max(1,round(w*scale)),max(1,round(h*scale))))
        ok,data=cv2.imencode('.jpg',small,[cv2.IMWRITE_JPEG_QUALITY,80])
        if not ok:raise ValueError('Cannot encode bounded model image')
        return data.tobytes()
    def frames(self,media_id,start_ms,end_ms,count=6):
        media=MediaRef.model_validate(self.db.get('media',media_id))
        if not media.start_ms<=start_ms<=end_ms<=media.end_ms:raise ValueError('Interval outside media')
        if not 1<=count<=6:raise ValueError('Count must be 1..6')
        frames=sorted([FrameRef.model_validate(f) for f in self.db.list('frame') if f['media_id']==media_id and start_ms<=f['timestamp_ms']<=end_ms],key=lambda f:f.timestamp_ms)
        if len(frames)>count:frames=[frames[round(i*(len(frames)-1)/(count-1))] for i in range(count)] if count>1 else frames[:1]
        gaps=['Sampled still frames. Motion between samples is unknown.']
        available=[]
        for f in frames:
            try:self.image(f.id);available.append(f)
            except (OSError,ValueError):gaps.append(f'Missing or invalid frame: {f.id}')
        if not available:gaps.append('No footage in this interval. Coverage is unknown.')
        else:
            if available[0].timestamp_ms>start_ms:gaps.append(f'Unknown leading interval: {start_ms}–{available[0].timestamp_ms} ms')
            if available[-1].timestamp_ms<end_ms:gaps.append(f'Unknown trailing interval: {available[-1].timestamp_ms}–{end_ms} ms')
            for left,right in zip(available,available[1:]):
                if right.timestamp_ms-left.timestamp_ms>1000:gaps.append(f'Unknown gap: {left.timestamp_ms}–{right.timestamp_ms} ms')
        return FrameBatch(frames=available,coverage_gaps=gaps)
    def evidence(self,media_id,start_ms,end_ms):
        frames=self.frames(media_id,start_ms,end_ms)
        return EvidenceBundle(media_ref=MediaRef.model_validate(self.db.get('media',media_id)),observations=[o for o in self.db.list('observation') if o['media_id']==media_id and o['start_ms']>=start_ms and o['end_ms']<=end_ms],frame_refs=frames.frames,coverage_gaps=frames.coverage_gaps)
