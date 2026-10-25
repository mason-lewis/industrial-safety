"""Bounded FFmpeg transforms. Inputs come from server-owned media, never model paths."""
from pathlib import Path
import subprocess
import tempfile
import math


def executable():
    import shutil
    found=shutil.which('ffmpeg')
    if found: return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args, data=None):
    result=subprocess.run([executable(),'-hide_banner','-loglevel','error',*args],input=data,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
    if result.returncode: raise ValueError('FFmpeg media transform failed')
    return result.stdout


def image(raw, box=None, max_side=1024):
    filters=[]
    if box is not None:
        a,b,c,d=box
        if not all(math.isfinite(v) for v in box) or not 0<=a<c<=1 or not 0<=b<d<=1: raise ValueError('Invalid crop')
        filters.append(f'crop=iw*{c-a}:ih*{d-b}:iw*{a}:ih*{b}')
    filters.append(f"scale=w='min({max_side},iw)':h='min({max_side},ih)':force_original_aspect_ratio=decrease")
    return run(['-i','pipe:0','-vf',','.join(filters),'-frames:v','1','-f','image2pipe','-vcodec','mjpeg','pipe:1'],raw)


def clip(source, output, start_ms, end_ms):
    if start_ms<0 or end_ms<=start_ms or end_ms-start_ms>240000: raise ValueError('Invalid clip interval')
    # Decode through the requested start rather than claiming keyframe-copy precision.
    run(['-i',str(source),'-ss',str(start_ms/1000),'-t',str((end_ms-start_ms)/1000),
         '-an','-vf',"scale=w='min(1280,iw)':h=-2",'-c:v','libx264','-preset','ultrafast',
         '-movflags','+faststart','-n',str(output)])
    return Path(output)


def retained_clip(store, refs, output):
    """Variable source intervals retained in a sidecar; gaps remain visibly sampled."""
    if len(refs)<2: raise ValueError('Need at least two retained source frames')
    with tempfile.TemporaryDirectory(prefix='safety-segment-') as directory:
        root=Path(directory); lines=[]
        for i,ref in enumerate(refs):
            path=root/f'{i}.jpg';path.write_bytes(store.image(ref.id))
            lines.append(f"file '{path}'")
            if i+1<len(refs): lines.append(f'duration {(refs[i+1].timestamp_ms-ref.timestamp_ms)/1000:.6f}')
        manifest=root/'frames.txt';manifest.write_text('\n'.join(lines)+'\n')
        run(['-f','concat','-safe','0','-i',str(manifest),'-an','-vf','scale=trunc(iw/2)*2:trunc(ih/2)*2',
             '-fps_mode','vfr','-c:v','libx264','-preset','ultrafast','-movflags','+faststart','-n',str(output)])
    return Path(output)


def extract_frame(source, timestamp_ms, max_side=1024):
    if timestamp_ms<0: raise ValueError('Negative source timestamp')
    return run(['-i',str(source),'-ss',str(timestamp_ms/1000),'-frames:v','1','-vf',
        f"scale=w='min({max_side},iw)':h='min({max_side},ih)':force_original_aspect_ratio=decrease",
        '-f','image2pipe','-vcodec','mjpeg','pipe:1'])
