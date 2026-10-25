"""Resolve library IDs and sample local recordings without detection or live I/O."""
import hashlib
import math
from pathlib import Path

import cv2

from safety_app.domain.video_analysis import RecordedSource
from safety_app.modules.monitoring.video import FileReplaySource

SUFFIXES = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.mpeg', '.m4v'}


def library(root):
    folder = Path(root) / 'data/input'
    if not folder.exists():
        return {}
    return {
        hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]: path
        for path in sorted(folder.iterdir())
        if path.suffix.lower() in SUFFIXES and path.is_file()
        and path.resolve().is_relative_to(folder.resolve())
    }


def sources(root):
    from safety_app.infrastructure.library_visibility import hidden_sources
    hidden = hidden_sources(root)
    return [RecordedSource(id=source_id, name=path.name) for source_id, path in library(root).items()
            if source_id not in hidden]


def resolve(root, source_id):
    try:
        return library(root)[source_id]
    except KeyError:
        raise ValueError('Unknown recorded source; upload a clip or refresh the library') from None


def signature(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def describe(path):
    before = signature(path)
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    decoder = FileReplaySource(path)
    try:
        decoder.open()
        duration = decoder.provenance['duration_ms']
        first = decoder.read().timestamp_ms
        if duration is None or duration <= 0:
            raise ValueError('Recording has no bounded duration metadata')
        # Source PTS may have a nonzero origin. All requests retain source time.
        origin = round(float((decoder.stream.start_time or 0) * decoder.stream.time_base) * 1000)
        end = origin + duration
        if signature(path) != before:
            raise ValueError('Recording changed during inspection')
        return digest, max(0, first), end, before
    finally:
        decoder.close()


def sample(path, expected_signature, media, media_id, intervals, count, cancelled):
    """Seek bounded targets, preserve original JPEGs, and report missing samples."""
    if signature(path) != expected_signature:
        raise ValueError('Recording changed since analysis started')
    decoder = FileReplaySource(path)
    frames = {}
    gaps = ['Sampled still frames; motion, sound, and actions between samples are unknown.']
    try:
        decoder.open()
        per_interval = max(1, count // len(intervals))
        for interval in intervals:
            width = interval.end_ms - interval.start_ms
            fps=getattr(decoder,'fps',None)
            period=math.ceil(1000/fps) if fps and math.isfinite(fps) and fps>0 else 100
            targets = [interval.start_ms + round(i * max(0, width - period) / max(1, per_interval - 1))
                       for i in range(per_interval)]
            for target in targets:
                if cancelled():
                    raise InterruptedError('Video analysis cancelled during decoding')
                try:
                    frame = decoder.seek(target)
                except (ValueError, StopIteration):
                    gaps.append(f'No decoded frame at requested source time {target} ms.')
                    continue
                if not interval.start_ms <= frame.timestamp_ms <= interval.end_ms:
                    gaps.append(f'No in-scope decoded frame at requested source time {target} ms.')
                    continue
                ok, raw = cv2.imencode('.jpg', frame.image, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not ok:
                    raise ValueError('Cannot encode retained video evidence')
                ref = media.add_frame(media_id, str(frame.frame_id), frame.timestamp_ms, raw.tobytes())
                frames[ref.id] = ref
        if signature(path) != expected_signature:
            raise ValueError('Recording changed during sampling')
        if not frames:
            raise FileNotFoundError('No footage available in selected intervals')
        return sorted(frames.values(), key=lambda frame: frame.timestamp_ms), gaps
    finally:
        decoder.close()
