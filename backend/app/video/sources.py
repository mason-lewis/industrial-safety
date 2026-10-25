"""PyAV decoding uses presentation timestamps. Missing PTS are rejected."""
from dataclasses import dataclass
import av

@dataclass
class DecodedFrame:
    image: object
    timestamp_ms: int
    frame_id: int

class FileReplaySource:
    def __init__(self,path):
        self.path = path; self.container = None; self.index = 0
    def open(self):
        self.container = av.open(str(self.path))
        self.stream = self.container.streams.video[0]
        self.decoder = iter(self.container.decode(self.stream))
        self.fps = float(self.stream.average_rate) if self.stream.average_rate else None
        self.provenance={'timestamp_basis':'presentation timestamp multiplied by frame time_base','frame_id_basis':'source PTS expressed in microseconds','stream_time_base':str(self.stream.time_base),'source_fps':self.fps,'duration_ms':round(float(self.stream.duration*self.stream.time_base)*1000) if self.stream.duration is not None else round(self.container.duration/1000) if self.container.duration else None}
    def read(self):
        frame = next(self.decoder)
        if frame.pts is None or frame.time_base is None: raise ValueError('Frame has no presentation timestamp')
        stamp = round(float(frame.pts*frame.time_base)*1000)
        self.index += 1
        # Deterministic presentation-time frame key also survives independent retrospective decoding.
        frame_id=round(float(frame.pts*frame.time_base)*1000000)
        return DecodedFrame(frame.to_ndarray(format='bgr24'),stamp,frame_id)
    def seek(self,timestamp_ms,*,at_or_before=False):
        self.container.seek(int(timestamp_ms/1000/self.stream.time_base),stream=self.stream,backward=True)
        self.decoder = iter(self.container.decode(self.stream)); self.index = 0
        last = None
        while True:
            try: result = self.read()
            except StopIteration:
                duration = getattr(self.stream,'duration',None)
                end_ms = round(float(((getattr(self.stream,'start_time',None) or 0)+duration)*self.stream.time_base)*1000) if duration is not None else None
                # A scrubber may land between the final PTS and the stream endpoint.
                if last is not None and end_ms is not None and timestamp_ms<=end_ms: return last
                raise ValueError("Seek target is beyond available video") from None
            if result.timestamp_ms >= timestamp_ms:
                return last if at_or_before and result.timestamp_ms>timestamp_ms and last is not None else result
            last = result
    def close(self):
        if self.container: self.container.close()

class RTSPSource(FileReplaySource):
    def open(self):
        self.container = av.open(str(self.path),options={'rtsp_transport':'tcp'},timeout=(5.0,5.0))
        self.stream = self.container.streams.video[0]
        self.decoder = iter(self.container.decode(self.stream)); self.fps = None
    def seek(self,timestamp_ms):
        raise ValueError('RTSP does not support seek')
