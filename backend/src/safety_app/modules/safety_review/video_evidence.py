"""Read-only review evidence plus append-only, tightly bounded visual inspection."""
import hashlib
import json
import math
from safety_app.domain.models import FrameRef, Interval, uid
from safety_app.domain.people import PersonFrame
from safety_app.domain.chat_lab import LabCitation
from safety_app.domain.video_chat import VideoEvidenceRef, VideoInspection, VideoChatContext
from safety_app.modules.monitoring.recordings import describe, resolve, sample
from safety_app.infrastructure.exact_cache import fingerprint


class VideoEvidence:
    def __init__(self,rt,job):
        self.rt,self.job=rt,job
        self.context=VideoChatContext.model_validate(job.payload['video_context'])
        self.chunks=job.payload['chunks'];self.frames={};self.inspections=[];self.detail_calls=0
        for chunk in self.chunks:
            for raw in chunk['frames']:
                frame=FrameRef.model_validate(raw)
                if frame.media_id!=self.context.media_id or not self.context.start_ms<=frame.timestamp_ms<=self.context.end_ms:
                    raise ValueError('Frozen evidence is outside this review')
                self.frames[frame.id]=VideoEvidenceRef(id=frame.id,frame=frame,source_hash=self.context.source_hash,origin='saved')
        for raw in job.payload.get('prior_evidence',[]):
            evidence=VideoEvidenceRef.model_validate(raw)
            if evidence.frame.media_id!=self.context.media_id or evidence.source_hash!=self.context.source_hash:raise ValueError('Previous chat evidence is outside this recording')
            evidence.inspected=False # Every new visual claim still requires a current pixel check.
            self.frames[evidence.id]=evidence
        self.seen=set()
        for frame in self.frames.values():
            frame.people=[PersonFrame.model_validate(p) for p in job.payload['people'] if p['frame_id']==frame.id]

    def catalog(self,query,limit=3):
        from safety_app.modules.safety_review.sources import _terms
        terms=set(_terms(query));focus=self.context.focus.timestamp_ms
        def score(chunk):
            text=json.dumps({'summary':chunk['summary'],'observations':chunk['observations']}).lower()
            matches=sum(term in text for term in terms)
            distance=min((abs(f['timestamp_ms']-focus) for f in chunk['frames']),default=10**9) if focus is not None else 0
            return (-matches,distance,chunk['intervals'][0]['start_ms'])
        selected=sorted(self.chunks,key=score)[:limit]
        return {'matches':[{'id':c['id'],'summary':c['summary'],'intervals':c['intervals'],
                           'frames':[{'id':f['id'],'timestamp_ms':f['timestamp_ms']} for f in c['frames']]} for c in selected],
                'gaps':['Search covers saved observations, not every frame or every event.']}

    def read_frames(self,ids):
        if not 1<=len(ids)<=6 or len(set(ids))!=len(ids):raise ValueError('Read one to six distinct frame IDs')
        refs=[]
        for id in ids:
            if id not in self.frames:raise ValueError('Frame is outside this question evidence')
            item=self.frames[id]
            self.rt.media.image(id) # Verify retained bytes before authorizing any citation.
            refs.append(item.model_dump());self.seen.add(id)
        return {'frames':refs,'gaps':['Each image is a sampled instant. Audio and unsampled motion are not evidence.']}

    def citation(self,id):
        item=self.frames[id]
        return LabCitation(id=id,title=f'Video frame · {item.frame.timestamp_ms} ms',kind='visual',
            text=json.dumps({'timestamp_ms':item.frame.timestamp_ms,'origin':item.origin,'parent_frame_id':item.parent_frame_id,'box':item.box}),
            version=item.frame.content_hash,provenance={'frame_id':id,'media_id':item.frame.media_id,
                'source_hash':self.context.source_hash,'inspected':item.inspected,'parent_frame_id':item.parent_frame_id})

    def mark_inspected(self,ids):
        for id in ids:
            if id not in self.seen:raise ValueError('Cannot mark unseen pixels as inspected')
            self.frames[id].inspected=True

    def inspect(self,start_ms,end_ms,cancelled):
        if len(self.inspections)>=2:raise ValueError('Two inspection intervals have already been used')
        if not self.context.start_ms<=start_ms<end_ms<=self.context.end_ms or end_ms-start_ms>10000:
            raise ValueError('Inspection must be at most ten seconds inside this recording')
        run=self.rt.db.get('review_run',self.context.review_run_id)
        path=resolve(self.rt.root,run['source_id'])
        digest,_,_,signature=describe(path)
        if digest!=self.context.source_hash:raise ValueError('Source changed; only saved evidence can be used')
        interval=Interval(start_ms=start_ms,end_ms=end_ms)
        frames,gaps=sample(path,signature,self.rt.media,self.context.media_id,[interval],5,cancelled)
        for frame in frames:
            self.frames.setdefault(frame.id,VideoEvidenceRef(id=frame.id,frame=frame,source_hash=digest,origin='inspection'))
        inspection=VideoInspection(id=uid(),interval=interval,frame_ids=[f.id for f in frames],gaps=gaps)
        self.inspections.append(inspection)
        self.rt.db.save('video_chat_inspection',{'id':inspection.id,'job_id':self.job.id,'review_run_id':self.context.review_run_id,
            'site_id':self.job.site_id,'source_hash':digest,**inspection.model_dump()},immutable=True)
        return {'inspection':inspection.model_dump(),'gaps':gaps}

    def crop(self,id,box,assigned):
        import cv2
        import numpy as np
        if id not in assigned or id not in self.seen:raise ValueError('Crop parent is not assigned to this stage')
        if self.detail_calls>=1:raise ValueError('The stage has already used its one detail crop')
        if len(box)!=4 or any(not math.isfinite(v) for v in box):raise ValueError('Use four finite normalized XYWH coordinates')
        x,y,w,h=box
        if min(x,y)<0 or min(w,h)<=0 or x+w>1 or y+h>1:raise ValueError('Crop must stay inside the frame')
        parent=self.frames[id];raw=self.rt.media.image(id)
        pixels=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
        if pixels is None:raise ValueError('Retained image cannot be decoded')
        ph,pw=pixels.shape[:2]
        crop=pixels[int(y*ph):max(int(y*ph)+1,round((y+h)*ph)),int(x*pw):max(int(x*pw)+1,round((x+w)*pw))]
        if not crop.size:raise ValueError('Crop has no pixels')
        ok,jpeg=cv2.imencode('.jpg',crop,[cv2.IMWRITE_JPEG_QUALITY,85])
        if not ok:raise ValueError('Crop cannot be encoded')
        key='crop-'+fingerprint({'parent':parent.frame.content_hash,'box':box})[:24]
        frame=self.rt.media.add_frame(parent.frame.media_id,key,parent.frame.timestamp_ms,jpeg.tobytes())
        self.frames[frame.id]=VideoEvidenceRef(id=frame.id,frame=frame,source_hash=self.context.source_hash,
            origin='crop',parent_frame_id=id,box=box)
        self.seen.add(frame.id);self.detail_calls+=1
        return self.frames[frame.id]

    def people(self,id):
        if id not in self.seen:raise ValueError('Person detections require an assigned frame')
        rows=[p for p in self.job.payload['people'] if p['frame_id']==id]
        self.frames[id].people=[PersonFrame.model_validate(row) for row in rows]
        return {'frame_id':id,'records':rows,'gaps':[] if rows else ['No saved detector result for this frame; this does not mean zero people.']}
