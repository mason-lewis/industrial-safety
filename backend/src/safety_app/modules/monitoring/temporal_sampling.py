"""Bounded local motion sampling. Scores select pixels, never classify an incident."""
from dataclasses import dataclass
import cv2
import numpy as np

VERSION = 'motion-sequence-3'
LEGACY_VERSION = 'motion-context-1'
SEQUENCE_VERSIONS = ('motion-sequence-2', VERSION)
VERSIONS = (LEGACY_VERSION, *SEQUENCE_VERSIONS)

@dataclass
class Sample:
    frame_id: str
    timestamp_ms: int
    jpeg: bytes
    score: float

class TemporalSampler:
    """At most 101 candidates; keep up to two six-image temporal packets."""
    def __init__(self, max_frames=6, cadence_ms=None, version=VERSION):
        cadence_ms=cadence_ms or (100 if version==VERSION else 200)
        if not 2 <= max_frames <= 6 or cadence_ms < (100 if version==VERSION else 200):
            raise ValueError('Temporal sampling requires 2–6 frames and a bounded cadence')
        if version not in VERSIONS:raise ValueError('Unknown temporal sampling version')
        self.max_frames=max_frames; self.cadence_ms=cadence_ms; self.version=version
        self.samples=[]; self.previous=None

    def add(self, frame_id, timestamp_ms, image):
        if self.samples and timestamp_ms-self.samples[-1].timestamp_ms<self.cadence_ms:return
        if len(self.samples)>=min(101,10000//self.cadence_ms+1):return
        gray=cv2.cvtColor(cv2.resize(image,(192,108)),cv2.COLOR_BGR2GRAY)
        score=0.
        if self.previous is not None:
            diff=cv2.absdiff(gray,self.previous)
            # Local tile changes retain small actions that a whole-image mean hides.
            cells=diff.reshape(9,12,16,12).transpose(0,2,1,3).mean((2,3)).ravel()
            score=float(np.sort(cells)[-4:].mean())
        ok,raw=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,85])
        if not ok:raise ValueError('Could not encode temporal evidence')
        self.samples.append(Sample(str(frame_id),timestamp_ms,raw.tobytes(),score));self.previous=gray

    def selected(self):
        rows=self.samples
        if len(rows)<=self.max_frames:return rows
        if self.max_frames<6 or max(s.score for s in rows)<1:
            indices={round(i*(len(rows)-1)/(self.max_frames-1)) for i in range(self.max_frames)}
        else:
            ranked=sorted(range(1,len(rows)),key=lambda i:-rows[i].score)
            peak=ranked[0]
            # A single strongest burst can be unrelated motion. Keep a second,
            # separated burst when present, without labeling either as a hazard.
            secondary=next((i for i in ranked if abs(rows[i].timestamp_ms-rows[peak].timestamp_ms)>=1500
                            and rows[i].score>=rows[peak].score*.5
                            and (self.version==LEGACY_VERSION or abs(max(0,min(i-3,len(rows)-6))-max(0,min(peak-3,len(rows)-6)))>=6)),None)
            if self.version in SEQUENCE_VERSIONS:
                # Peaks can describe a reaction/aftermath. Keep the lead-in too,
                # without choosing a timestamp based on a filename or known event.
                indices=set()
                for center in [peak,*([secondary] if secondary is not None else [])]:
                    start=max(0,min(center-3,len(rows)-6))
                    indices.update(range(start,start+6))
                return [rows[i] for i in sorted(indices)]
            indices=set()
            for center in [peak,*([secondary] if secondary is not None else [])]:
                time=rows[center].timestamp_ms
                before=min(range(center),key=lambda i:abs(rows[i].timestamp_ms-(time-200)))
                after=min(range(center+1,len(rows)),key=lambda i:abs(rows[i].timestamp_ms-(time+200))) if center<len(rows)-1 else center
                indices.update([before,center,after])
            for i in [0,len(rows)-1,len(rows)//2,*ranked]:
                if len(indices)>=self.max_frames:break
                indices.add(i)
        return [rows[i] for i in sorted(indices)]

    def retain(self, media, media_id):
        return [media.add_frame(media_id,s.frame_id,s.timestamp_ms,s.jpeg) for s in self.selected()]


def sample_motion(path, expected_signature, media, media_id, interval, cancelled, max_frames=6, version=VERSION):
    from safety_app.modules.monitoring.recordings import signature
    from safety_app.modules.monitoring.video import FileReplaySource
    if signature(path)!=expected_signature:raise ValueError('Recording changed before sampling')
    decoder=FileReplaySource(path);sampler=TemporalSampler(max_frames=max_frames,version=version)
    exhausted=False;last_ms=None
    try:
        decoder.open();frame=decoder.seek(interval.start_ms)
        while frame.timestamp_ms<interval.end_ms:
            if cancelled():raise InterruptedError('Sampling cancelled')
            last_ms=frame.timestamp_ms
            if frame.timestamp_ms>=interval.start_ms:sampler.add(frame.frame_id,frame.timestamp_ms,frame.image)
            try:frame=decoder.read()
            except StopIteration:
                exhausted=True;break
        if signature(path)!=expected_signature:raise ValueError('Recording changed during sampling')
        if not sampler.samples:raise ValueError('No decoded frames in this interval')
        gaps=[
            ('Sampled still frames: up to two six-image sequences retain motion lead-in and aftermath; separate sequences are not continuous footage. Scores are not hazard detections; events elsewhere can be missed.'
             if version in SEQUENCE_VERSIONS else 'Sampled still frames: local motion scores select up to two before/during/after bursts. Scores are not hazard detections; brief events elsewhere can be missed.'),
            'Sampled still frames: scene changes do not establish continuity. Audio is not assessed.',
        ]
        if exhausted and last_ms is not None and interval.end_ms-last_ms>max(500,2000/max(decoder.fps,1)):
            gaps.append(f'Missing decoded tail: last frame at {last_ms} ms, interval ends at {interval.end_ms} ms.')
        return sampler.retain(media,media_id),gaps
    finally:decoder.close()
