"""Transient local video inspection. No agents, jobs, evidence writes or AI controls."""
import base64
import math
import threading
import time
from collections import OrderedDict

from safety_app.domain.people import PersonPreview
from safety_app.modules.monitoring.people import detect_people
from safety_app.modules.monitoring.recordings import resolve, signature, sources
from safety_app.modules.monitoring.video import FileReplaySource


class PreviewBusy(Exception):
    pass


def preview_path(root, source_id):
    if source_id not in {source.id for source in sources(root)}:
        raise KeyError('Unknown preview video')
    return resolve(root, source_id)


class PersonPreviewService:
    def __init__(self, root, people):
        self.root = root
        self.people = people
        self.lock = threading.Lock()
        self.thumbnail_lock = threading.Lock()
        self.thumbnails = OrderedDict()

    def thumbnail(self, source_id):
        """Bounded in-memory JPEG cache; no detector or retained evidence writes."""
        import cv2
        path = preview_path(self.root, source_id)
        with self.thumbnail_lock:
            before = signature(path)
            key = (source_id, before)
            if key in self.thumbnails:
                self.thumbnails.move_to_end(key)
                return self.thumbnails[key]
            decoder = FileReplaySource(path)
            try:
                decoder.open()
                origin = round(float((decoder.stream.start_time or 0) * decoder.stream.time_base) * 1000)
                duration = decoder.provenance['duration_ms'] or 0
                frame = decoder.seek(origin + min(1000, max(0, duration-100)), at_or_before=True)
            finally:
                decoder.close()
            height, width = frame.image.shape[:2]
            scale = min(1, 320/max(width,height))
            small = cv2.resize(frame.image, (max(1,round(width*scale)), max(1,round(height*scale))), interpolation=cv2.INTER_AREA)
            ok, raw = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 78])
            if not ok or before != signature(path):
                raise ValueError('Video changed or thumbnail could not be decoded')
            self.thumbnails[key] = raw.tobytes()
            while len(self.thumbnails) > 48:
                self.thumbnails.popitem(last=False)
            return self.thumbnails[key]

    def inspect(self, body):
        # Drop overlapping requests instead of building a queue behind playback.
        if not self.lock.acquire(blocking=False):
            raise PreviewBusy('Person detection is busy. Try another frame shortly.')
        try:
            return self._inspect(body)
        finally:
            self.lock.release()

    def _inspect(self, body):
        import cv2
        begin = time.monotonic()
        path = preview_path(self.root, body.source_id)
        before = signature(path)
        decoder = FileReplaySource(path)
        try:
            decoder.open()
            duration = decoder.provenance['duration_ms']
            playback_duration = max(duration or 0, math.ceil((decoder.container.duration or 0) / 1000))
            if duration is None or not 0 <= body.position_ms <= playback_duration:
                raise ValueError('Preview position is outside the recording')
            origin = round(float((decoder.stream.start_time or 0) * decoder.stream.time_base) * 1000)
            # Native players include audio tails. Show the last real video frame
            # there and report its actual PTS, without inventing a later image.
            frame = decoder.seek(origin + min(body.position_ms, duration), at_or_before=True)
        finally:
            decoder.close()
        result = detect_people(self.people.detector, frame.image, body.confidence_threshold)
        data_url = ''
        if body.include_image:
            ok, raw = cv2.imencode('.jpg', frame.image, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                raise ValueError('Cannot encode the preview frame')
            data_url = 'data:image/jpeg;base64,' + base64.b64encode(raw).decode('ascii')
        if before != signature(path):
            raise ValueError('Video changed during preview; reopen it')
        return PersonPreview(**result.model_dump(), source_id=body.source_id,
            position_ms=max(0, frame.timestamp_ms-origin), timestamp_ms=frame.timestamp_ms,
            image_data_url=data_url,
            elapsed_ms=round((time.monotonic()-begin)*1000))
