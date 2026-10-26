"""Assemble the narrated, subtitled demo video from recordings, cards and TTS lines.

Usage: ./scripts/uv run --no-sync python scripts/demo_video/edit.py BUILD_DIR OUTPUT.mp4 [--preview] [--reuse-video]
--preview renders at 960x540 (about 4x faster) to OUTPUT-preview.mp4 for quick review before the final 1080p render.
BUILD_DIR holds live/ and walk/ (capture.mjs output), cards/ (cards.py), tts/ + tts-manifest.json (tts.py).
Writes OUTPUT.mp4, OUTPUT.srt and BUILD_DIR/timeline.json. Offline; no model calls.
"""
import bisect
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
PREVIEW = '--preview' in sys.argv
REUSE_VIDEO = '--reuse-video' in sys.argv  # keep the existing silent render; redo audio, subtitles and mux only
W, H, FPS = (960, 540, 30) if PREVIEW else (1920, 1080, 30)
S = W / 1920  # scale for subtitle sizes
LEAD, TAIL, XFADE = 0.55, 0.55, 0.4
SCRIPT = json.loads(Path(__file__).with_name('script.json').read_text())


def wav_seconds(path):
    # OpenAI streams WAV with a placeholder (max) frame count, so use the file size instead.
    with wave.open(str(path)) as w:
        bytes_per_second = w.getframerate() * w.getnchannels() * w.getsampwidth()
    return (Path(path).stat().st_size - 44) / bytes_per_second


class Recording:
    """Variable-rate screencast frames -> image at any recording time."""
    def __init__(self, folder):
        self.folder = Path(folder)
        self.frames = json.loads((self.folder / 'frames.json').read_text())
        self.times = [f['t'] for f in self.frames]
        labels = json.loads((self.folder / 'labels.json').read_text())
        self.labels = {l['label']: l['wall'] - labels['t0'] for l in labels['labels']}
        self.cache = {}

    def at(self, t):
        i = max(0, bisect.bisect_right(self.times, t) - 1)
        if i not in self.cache:
            self.cache = {i: fit(cv2.imread(str(self.folder / 'frames' / self.frames[i]['file'])))}
        return self.cache[i]

    def end(self):
        return self.times[-1]


def fit(img):
    if img.shape[1] != W or img.shape[0] != H:
        img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
    return img


def load_rgba(path):
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA) if img.shape[:2] != (H, W) else img
    return img if img.shape[2] == 4 else np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])


def over(base, rgba, alpha=1.0):
    a = rgba[:, :, 3:4].astype(np.float32) / 255.0 * alpha
    return (base.astype(np.float32) * (1 - a) + rgba[:, :, :3].astype(np.float32) * a).astype(np.uint8)


def kenburns(img, u, zoom=0.05, pan=(0.0, 0.0)):
    """Slow push-in: u in [0,1] across the scene."""
    z = 1 + zoom * u
    w, h = W / z, H / z
    x = (W - w) / 2 + pan[0] * u * (W - w) / 2
    y = (H - h) / 2 + pan[1] * u * (H - h) / 2
    m = np.float32([[z, 0, -x * z], [0, z, -y * z]])
    return cv2.warpAffine(img, m, (W, H), flags=cv2.INTER_LINEAR)


def ease(u):
    return u * u * (3 - 2 * u)


def retime(rec, a, b):
    """Map scene progress u in [0,1] onto recording time [a,b]."""
    return lambda u: rec.at(a + (b - a) * u)


def footage(path, start_s):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    state = {'idx': -1, 'img': None}

    def frame(local_t):
        want = int((start_s + local_t) * fps)
        if want < state['idx']:
            cap.set(cv2.CAP_PROP_POS_FRAMES, want); state['idx'] = want - 1
        while state['idx'] < want:
            ok, img = cap.read()
            if not ok: break
            state['idx'] += 1; state['img'] = img
        img = state['img']
        h = int(img.shape[0] * W / img.shape[1])
        img = cv2.resize(img, (W, h)) if h >= H else cv2.resize(img, (int(img.shape[1] * H / img.shape[0]), H))
        y, x = (img.shape[0] - H) // 2, (img.shape[1] - W) // 2
        return img[y:y + H, x:x + W]
    return frame


def build_scenes(build):
    cards = build / 'cards'
    live, walk = Recording(build / 'live'), Recording(build / 'walk')
    lab = walk.labels
    card = {p.stem: load_rgba(p)[:, :, :3] for p in cards.glob('*.png') if not p.stem.startswith(('overlay', 'badge'))}
    cold_overlay, badge = load_rgba(cards / 'overlay-cold-open.png'), load_rgba(cards / 'badge-live.png')
    fire = footage(ROOT / 'data/input/11-restaurant-kitchen-fire.mp4', 3.0)
    click_at = live.labels.get('click-video', 2.0)

    def cold(t, d):
        img = fire(t * 0.85)
        img = (img.astype(np.float32) * 0.62).astype(np.uint8)
        return over(img, cold_overlay, min(1, max(0, (t - 1.2) / 0.8)))

    def still(name, zoom=0.045, pan=(0, 0), reveal_at=None):
        """Card with a slow push-in; with reveal_at, crossfade from the '-a' first beat to the full card."""
        def frame(t, d):
            img = card[name]
            if reveal_at is not None and name + '-a' in card:
                mix = min(1.0, max(0.0, (t - reveal_at * d) / 0.5))
                img = cv2.addWeighted(card[name + '-a'], 1 - mix, card[name], mix, 0)
            return kenburns(img, ease(t / d), zoom, pan)
        return frame

    def start(t, d):
        split = d * 0.55
        if t < split:
            return kenburns(card['terminal'], ease(t / split), 0.03)
        return walk.at(lab['welcome'] + (t - split) * (lab['history'] - lab['welcome']) / (d - split))

    def live_scene(t, d):
        real, before = 3.2, 2.4  # library + click at real speed, then time-lapse the run
        if t < real:
            rt = click_at - before + t
        else:
            rt = click_at - before + real + (t - real) / (d - real) * (live.end() - (click_at - before + real))
        return over(live.at(rt), badge, 1.0 if t > real else 0.0)

    def span(a, b):
        return lambda t, d: walk.at(lab[a] + (lab[b] - lab[a]) * t / d)

    def report(t, d):
        a, b = lab['to-report'], min(walk.end(), lab['report-end'])
        return walk.at(a + (b - a) * t / d)

    visuals = {'cold-open': cold, 'title': still('title', 0.06), 'problem': still('cost-gap', 0.04, reveal_at=0.68), 'start': start,
               'live': live_scene, 'team': span('team-start', 'to-finding'), 'evidence': span('to-finding', 'to-report'),
               'report': report, 'safety': still('injection', 0.04), 'cost': still('cost', 0.05, reveal_at=0.38),
               'honest': still('limits', 0.04), 'end': still('end', 0.03)}
    return visuals


# Spoken forms are spelled out for the voice; captions show them the way people read them.
DISPLAY = SCRIPT.get('display', {})
FONT = '/System/Library/Fonts/HelveticaNeue.ttc'
MAX_LINE = 40


def display_text(text):
    for spoken, shown in DISPLAY.items():
        text = re.sub(spoken, shown, text, flags=re.I)
    return text


def balance(text):
    """One line if it fits, else two lines of similar length, never leaving a single word alone."""
    if len(text) <= MAX_LINE:
        return [text]
    words = text.split()
    best = None
    for i in range(1, len(words)):
        a, b = ' '.join(words[:i]), ' '.join(words[i:])
        if len(a) > MAX_LINE or len(b) > MAX_LINE:
            continue
        score = abs(len(a) - len(b)) - (12 if a.endswith((',', ';', ':')) else 0) + (40 if i == len(words) - 1 or i == 1 else 0)
        if best is None or score < best[0]:
            best = (score, [a, b])
    return best[1] if best else None


def chunks_of(sentence):
    """Split a sentence into the fewest caption chunks that each fit in two balanced lines."""
    if balance(sentence):
        return [sentence]
    words = sentence.split()
    # Prefer a split after a comma near the middle, else at the word nearest the middle.
    ok = lambda i: min(len(' '.join(words[:i])), len(' '.join(words[i:]))) >= 24
    cands = [i for i in range(2, len(words) - 1) if words[i - 1].endswith(',') and ok(i)] or [i for i in range(2, len(words) - 1) if ok(i)] or list(range(2, len(words) - 1))
    mid = len(sentence) / 2
    i = min(cands, key=lambda i: abs(len(' '.join(words[:i])) - mid))
    return chunks_of(' '.join(words[:i])) + chunks_of(' '.join(words[i:]))


def captions(text, start, duration):
    """Cues of <=2 balanced lines (<=40 chars), timed by character share, each at least 1.2 s."""
    sentences = re.split(r'(?<=[.!?])\s+', display_text(text).strip())
    pieces = []
    for sentence in sentences:
        for chunk in chunks_of(sentence):
            if pieces and (len(chunk) < 22 or len(pieces[-1]) < 22) and balance(pieces[-1] + ' ' + chunk):
                pieces[-1] = pieces[-1] + ' ' + chunk   # merge fragments that would flash by
            else:
                pieces.append(chunk)
    total = sum(len(c) for c in pieces)
    changed = True
    while changed:   # fold any cue shorter than 1.2 s into a neighbour if two lines still fit
        changed = False
        for i, chunk in enumerate(pieces):
            if duration * len(chunk) / total >= 1.2 or len(pieces) == 1:
                continue
            for j in (i - 1, i + 1):
                if 0 <= j < len(pieces):
                    a, b = sorted((i, j))
                    merged = pieces[a] + ' ' + pieces[b]
                    if balance(merged):
                        pieces[a:b + 1] = [merged]; changed = True; break
            if changed: break
    cues, t = [], start
    for chunk in pieces:
        d = duration * len(chunk) / total
        cues.append((t, t + d, balance(chunk)))
        t += d
    return cues


def stamp(t):
    h, rem = divmod(t, 3600); m, s = divmod(rem, 60)
    ms = min(999, int(round((s % 1) * 1000)))
    return f'{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}'


def write_srt(cues, srt):
    srt.write_text('\n'.join(f'{i}\n{stamp(a)} --> {stamp(b)}\n' + '\n'.join(lines) + '\n' for i, (a, b, lines) in enumerate(cues, 1)))


class CaptionPainter:
    """Draws one rounded dark panel per cue in a fixed bottom band, white semibold text centered."""
    def __init__(self, cues):
        from PIL import ImageFont
        self.cues = cues
        self.font = ImageFont.truetype(FONT, round(46 * S), index=10)  # Helvetica Neue Medium
        self.cache = {}
        self.boxes = {}

    def overlay(self, lines):
        from PIL import Image, ImageDraw
        key = tuple(lines)
        if key in self.cache:
            return self.cache[key]
        pad_x, pad_y, gap = round(34 * S), round(18 * S), round(12 * S)
        boxes = [self.font.getbbox(l) for l in lines]
        line_h = round(56 * S)
        width = max(b[2] - b[0] for b in boxes) + 2 * pad_x
        height = line_h * len(lines) + gap * (len(lines) - 1) + 2 * pad_y
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=round(16 * S), fill=(14, 14, 14, 214))
        for i, (line, box) in enumerate(zip(lines, boxes)):
            x = (width - (box[2] - box[0])) // 2 - box[0]
            y = pad_y + i * (line_h + gap) + (line_h - round(46 * S)) // 2 - round(4 * S)
            draw.text((x, y), line, font=self.font, fill=(255, 255, 255, 255))
        arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGRA)
        self.cache[key] = arr
        return arr

    def paint(self, frame, now):
        cue = next((c for c in self.cues if c[0] <= now < c[1]), None)
        if not cue:
            return frame
        o = self.overlay(cue[2])
        h, w = o.shape[:2]
        x, y = (W - w) // 2, H - round(64 * S) - h
        self.boxes[cue[0]] = (x / W, y / H, w / W, h / H)
        region = frame[y:y + h, x:x + w]
        a = o[:, :, 3:4].astype(np.float32) / 255.0
        frame[y:y + h, x:x + w] = (region * (1 - a) + o[:, :, :3] * a).astype(np.uint8)
        return frame


def music(path, seconds):
    """Soft ambient pad: four detuned chord tones with slow swell, low-passed."""
    expr = '+'.join(f'0.05*sin(2*PI*{f}*t)*(0.6+0.4*sin(2*PI*{lfo}*t))' for f, lfo in
                    ((110, 0.05), (164.81, 0.07), (220.5, 0.06), (277.18, 0.045), (329.63, 0.08)))
    subprocess.run([FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    f'aevalsrc={expr}|{expr.replace("sin(2*PI*1", "sin(2*PI*1.003*1")}:s=48000:d={seconds}',
                    '-af', f'lowpass=f=1400,afade=t=in:d=3,afade=t=out:st={seconds - 4}:d=4,volume=0.8', str(path)], check=True)


def main():
    build, output = Path(sys.argv[1]), Path(sys.argv[2])
    if PREVIEW:
        output = output.with_name(output.stem + '-preview.mp4')
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((build / 'tts-manifest.json').read_text())
    visuals = build_scenes(build)
    timeline, t = [], 0.0
    for scene in SCRIPT['scenes']:
        speech = wav_seconds(manifest[scene['id']])
        duration = LEAD + speech + TAIL + (1.8 if scene['id'] == 'end' else 0)
        timeline.append({'id': scene['id'], 'start': round(t, 3), 'duration': round(duration, 3),
                         'voice_at': round(t + LEAD, 3), 'speech': round(speech, 3), 'say': scene['say']})
        t += duration - XFADE
    total = timeline[-1]['start'] + timeline[-1]['duration']
    (build / 'timeline.json').write_text(json.dumps(timeline, indent=1))

    cues = [c for s in timeline for c in captions(s['say'], s['voice_at'], s['speech'])]
    painter = CaptionPainter(cues)

    # Video: one pass at 30 fps with crossfades; captions painted per frame.
    silent = build / ('video-silent-preview.mp4' if PREVIEW else 'video-silent.mp4')
    if not (REUSE_VIDEO and silent.exists()):
        enc = subprocess.Popen([FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                                '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-preset', 'medium',
                                '-crf', '18', '-pix_fmt', 'yuv420p', str(silent)], stdin=subprocess.PIPE)
        for k in range(int(total * FPS)):
            now = k / FPS
            active = [s for s in timeline if s['start'] <= now < s['start'] + s['duration']]
            frames = []
            for s in active[-2:]:
                local = now - s['start']
                frames.append((s, visuals[s['id']](local, s['duration'])))
            if len(frames) == 2:
                (_, a), (s2, b) = frames
                mix = min(1.0, (now - s2['start']) / XFADE)
                img = cv2.addWeighted(a, 1 - mix, b, mix, 0)
            else:
                img = frames[0][1]
            if now < 0.6: img = (img * (now / 0.6)).astype(np.uint8)
            if now > total - 0.8: img = (img * max(0, (total - now) / 0.8)).astype(np.uint8)
            img = painter.paint(np.ascontiguousarray(img), now)
            enc.stdin.write(np.ascontiguousarray(img).tobytes())
        enc.stdin.close(); enc.wait()

    # Audio: voice lines at their offsets, music bed ducked under voice, loudness normalized.
    bed = build / ('music-preview.wav' if PREVIEW else 'music.wav')
    music(bed, total + 0.5)
    inputs, filters, labels = [], [], []
    for i, s in enumerate(timeline):
        inputs += ['-i', manifest[s['id']]]
        filters.append(f'[{i}:a]aresample=48000,adelay={int(s["voice_at"] * 1000)}:all=1[v{i}]'); labels.append(f'[v{i}]')
    n = len(timeline)
    inputs += ['-i', str(bed)]
    filters.append(f'{"".join(labels)}amix=inputs={n}:normalize=0,apad=whole_dur={total}[voice]')
    filters.append('[voice]asplit[vmix][vkey]')
    filters.append(f'[{n}:a]volume=0.55[bed]')
    filters.append('[bed][vkey]sidechaincompress=threshold=0.02:ratio=8:attack=40:release=600[ducked]')
    filters.append(f'[vmix][ducked]amix=inputs=2:normalize=0,atrim=0:{total},loudnorm=I=-16:TP=-1.5:LRA=11[aout]')
    audio = build / ('audio-preview.m4a' if PREVIEW else 'audio.m4a')
    subprocess.run([FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', *inputs, '-filter_complex', ';'.join(filters),
                    '-map', '[aout]', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', str(audio)], check=True)

    # Captions are already painted into the frames; keep the SRT alongside and copy the video stream.
    write_srt(cues, output.with_suffix('.srt'))
    (build / ('captions-preview.json' if PREVIEW else 'captions.json')).write_text(json.dumps(
        [{'start': a, 'end': b, 'lines': l, 'box': painter.boxes.get(a)} for a, b, l in cues], indent=1))
    subprocess.run([FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(silent), '-i', str(audio),
                    '-c:v', 'copy', '-c:a', 'copy', '-movflags', '+faststart', '-shortest', str(output)], check=True)
    print(json.dumps({'output': str(output), 'seconds': round(total, 2), 'scenes': len(timeline), 'cues': len(cues)}))


if __name__ == '__main__':
    main()
