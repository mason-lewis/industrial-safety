"""JPEG evidence with server-enforced immutable review scope and bounded retention."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import cv2
import sqlite3
import json
from threading import RLock
from functools import wraps

def synchronized(method):
    @wraps(method)
    def guarded(self,*args,**kwargs):
        with self.lock: return method(self,*args,**kwargs)
    return guarded
from app.models.contracts import EvidenceRef,EvidenceBundle,FramePacket,Context

@dataclass(frozen=True)
class EvidenceScope:
    session_id: str
    run_generation: int
    as_of_ms: int
    start_ms: int = 0
    mode: str = 'live'

class EvidenceStore:
    def __init__(self,root,max_bytes=256*1024*1024,rolling_ms=45000):
        self.lock=RLock()
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.max_bytes=max_bytes; self.rolling_ms=rolling_ms
        self.records={}; self.packets={}; self.pins=set(); self.total_bytes=0
        self.db=sqlite3.connect(self.root/'index.sqlite',check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, ref TEXT, packet TEXT, size INTEGER, pinned INTEGER)')
        for id,ref,packet,size,pinned in self.db.execute('SELECT * FROM evidence'):
            if (self.root/(id+'.jpg')).is_file():
                self.records[id]=(EvidenceRef.model_validate_json(ref),size); self.packets[id]=FramePacket.model_validate_json(packet); self.total_bytes+=size
                if pinned: self.pins.add(id)
    @synchronized
    def add(self,packet:FramePacket,image):
        # Originals are unannotated source frames, JPEG compression only.
        ok,data=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,80])
        if not ok: raise ValueError('JPEG encoding failed')
        ref=EvidenceRef(**{k:getattr(packet,k) for k in Context.model_fields},id=str(uuid4()),frame_id=packet.frame_id,timestamp_ms=packet.timestamp_ms,config_version=packet.config_version,processing_time=datetime.now(timezone.utc).isoformat())
        raw=data.tobytes(); (self.root/(ref.id+'.jpg')).write_bytes(raw)
        self.records[ref.id]=(ref,len(raw)); self.packets[ref.id]=packet; self.total_bytes+=len(raw)
        self._persist(ref.id)
        self._trim(packet.timestamp_ms,packet.session_id,packet.run_generation)
        return ref
    def _persist(self,id):
        ref,size=self.records[id]
        self.db.execute('INSERT OR REPLACE INTO evidence VALUES (?,?,?,?,?)',(id,ref.model_dump_json(),self.packets[id].model_dump_json(),size,int(id in self.pins)))
        self.db.commit()
    @synchronized
    def pin(self,id):
        if id in self.records: self.pins.add(id); self._persist(id)
    def _trim(self,now,session,generation):
        for id,(ref,size) in list(self.records.items()):
            old=ref.session_id!=session or ref.run_generation!=generation or now-ref.timestamp_ms>self.rolling_ms
            if (old and id not in self.pins) or self.total_bytes>self.max_bytes or len(self.records)>3000:
                (self.root/(id+'.jpg')).unlink(missing_ok=True)
                self.total_bytes-=size; self.records.pop(id); self.packets.pop(id); self.pins.discard(id)
                self.db.execute('DELETE FROM evidence WHERE id=?',(id,)); self.db.commit()
    @synchronized
    def validate(self,ref,scope):
        if ref.session_id!=scope.session_id or ref.run_generation!=scope.run_generation:
            raise ValueError('Evidence belongs to another session or generation')
        if not scope.start_ms<=ref.timestamp_ms<=scope.as_of_ms:
            raise ValueError('Evidence outside authorized time window')
    @synchronized
    def save_derived(self,id,raw):
        path=self.root/(id+'.jpg');path.write_bytes(raw)
        # A separate small quota bounds inspection copies without changing original evidence.
        files=sorted(self.root.glob('*-crop-*.jpg'),key=lambda p:p.stat().st_mtime)
        size=sum(p.stat().st_size for p in files)
        while files and (size>min(self.max_bytes,32*1024*1024) or len(files)>512):
            old=files.pop(0);size-=old.stat().st_size;old.unlink(missing_ok=True)
        return path.stem

    @synchronized
    def image(self,id,scope=None):
        if id not in self.records: raise KeyError(id)
        ref=self.records[id][0]
        if scope: self.validate(ref,scope)
        return (self.root/(id+'.jpg')).read_bytes()
    @synchronized
    def frames(self,scope,start_ms,end_ms,max_frames=8):
        if start_ms<scope.start_ms or end_ms>scope.as_of_ms or end_ms<start_ms: raise ValueError('Requested range outside review scope')
        if not 1<=max_frames<=16: raise ValueError('Frame count must be 1..16')
        refs=[r for r,_ in self.records.values() if r.session_id==scope.session_id and r.run_generation==scope.run_generation and start_ms<=r.timestamp_ms<=end_ms]
        refs.sort(key=lambda r:r.timestamp_ms)
        if len(refs)>max_frames:
            refs=[refs[round(i*(len(refs)-1)/(max_frames-1))] for i in range(max_frames)] if max_frames>1 else refs[-1:]
        return [(ref,self.image(ref.id,scope)) for ref in refs]
    @synchronized
    def tracks(self,scope,ids,start_ms,end_ms):
        refs=self.frames(scope,start_ms,end_ms,16)
        return [track for ref,_ in refs for track in self.packets[ref.id].tracks if not ids or track.track_id in ids]
    @synchronized
    def bundle(self,candidate):
        scope=EvidenceScope(candidate.session_id,candidate.run_generation,candidate.evidence_cutoff_ms,max(0,candidate.evidence_cutoff_ms-12000))
        refs=[r for r,_ in self.frames(scope,scope.start_ms,scope.as_of_ms,8)]
        return EvidenceBundle(**{k:getattr(candidate,k) for k in Context.model_fields},candidate_id=candidate.id,evidence_cutoff_ms=scope.as_of_ms,refs=refs,coverage_start_ms=refs[0].timestamp_ms if refs else None,coverage_end_ms=refs[-1].timestamp_ms if refs else None,limitations=['Sampled JPEG frames; gaps are not observations'])
    @synchronized
    def pin_candidate(self,candidate):
        scope=EvidenceScope(candidate.session_id,candidate.run_generation,candidate.evidence_cutoff_ms,max(0,candidate.evidence_cutoff_ms-15000))
        for ref,_ in self.frames(scope,scope.start_ms,scope.as_of_ms,16): self.pin(ref.id)
    @synchronized
    def coverage(self,incident):
        start=max(0,incident.timestamp_ms-incident.pre_ms); end=incident.timestamp_ms+incident.post_ms
        refs=sorted([r for r,_ in self.records.values() if r.session_id==incident.session_id and r.run_generation==incident.run_generation and start<=r.timestamp_ms<=end],key=lambda r:r.timestamp_ms)
        missing=[]
        if not refs: missing=[[start,end]]
        else:
            if refs[0].timestamp_ms>start: missing.append([start,refs[0].timestamp_ms])
            if refs[-1].timestamp_ms<end: missing.append([refs[-1].timestamp_ms,end])
            for left,right in zip(refs,refs[1:]):
                if right.timestamp_ms-left.timestamp_ms>1000: missing.append([left.timestamp_ms,right.timestamp_ms])
        return {'requested_start_ms':start,'requested_end_ms':end,'retained_start_ms':refs[0].timestamp_ms if refs else None,'retained_end_ms':refs[-1].timestamp_ms if refs else None,'missing_ranges':missing,'sampled':True,'gap_threshold_ms':1000,'frame_gaps_ms':[b.timestamp_ms-a.timestamp_ms for a,b in zip(refs,refs[1:])],'frame_timestamps_ms':[r.timestamp_ms for r in refs]}
