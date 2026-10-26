"""Loop goal for the demo video: prints PASS/FAIL per check and writes a contact sheet. Offline.

Usage: ./scripts/uv run --no-sync python scripts/demo_video/check_video.py BUILD_DIR [VIDEO]
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SCRIPT = json.loads(Path(__file__).with_name('script.json').read_text())
results = []


def check(name, ok, detail=''):
    results.append((name, ok, detail))


def probe(video):
    err = subprocess.run([FFMPEG, '-hide_banner', '-i', str(video)], capture_output=True, text=True).stderr
    duration = re.search(r'Duration: (\d+):(\d+):([\d.]+)', err)
    seconds = int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3]) if duration else 0
    return err, seconds


def main():
    build = Path(sys.argv[1])
    video = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / 'submission/video/industrial-safety-demo.mp4'
    if not video.exists():
        print('FAIL video exists'); sys.exit(1)
    info, seconds = probe(video)
    check('duration 120-180 s', 120 <= seconds <= 180, f'{seconds:.1f} s')
    check('1920x1080 H.264', 'h264' in info and ('1920x1080' in info or '-preview' in video.name), 'preview' if '-preview' in video.name else '')
    check('AAC audio', 'Audio: aac' in info)
    check('file < 200 MB', video.stat().st_size < 200e6, f'{video.stat().st_size / 1e6:.1f} MB')
    decode = subprocess.run([FFMPEG, '-v', 'error', '-i', str(video), '-f', 'null', '-'], capture_output=True, text=True).stderr.strip()
    check('decodes cleanly', not decode, decode[:200])

    loud = subprocess.run([FFMPEG, '-hide_banner', '-i', str(video), '-af', 'ebur128=framelog=quiet', '-f', 'null', '-'],
                          capture_output=True, text=True).stderr
    integrated = float(re.findall(r'I:\s+(-?[\d.]+) LUFS', loud)[-1])
    check('loudness -16 ±1.5 LUFS', abs(integrated + 16) <= 1.5, f'{integrated} LUFS')

    timeline = json.loads((build / 'timeline.json').read_text())
    check('every scene present', [s['id'] for s in timeline] == [s['id'] for s in SCRIPT['scenes']])
    gaps = [round(b['voice_at'] - (a['voice_at'] + a['speech']), 2) for a, b in zip(timeline, timeline[1:])]
    check('no silence > 2.5 s between lines', max(gaps) <= 2.5, f'max gap {max(gaps)} s')

    srt = video.with_suffix('.srt').read_text()
    cues = [c for c in srt.strip().split('\n\n') if c.strip()]
    lines = [l for c in cues for l in c.splitlines()[2:]]
    spans = []
    for c in cues:
        a, b = re.findall(r'(\d+):(\d+):(\d+),(\d+)', c.splitlines()[1])
        to = lambda x: int(x[0]) * 3600 + int(x[1]) * 60 + int(x[2]) + int(x[3]) / 1000
        spans.append(to(b) - to(a))
    check('subtitle lines <= 42 chars', all(len(l) <= 42 for l in lines), f'max {max(map(len, lines))}')
    check('subtitle lines <= 2 per cue', all(len(c.splitlines()) <= 4 for c in cues))
    check('every cue >= 1 s', min(spans) >= 1.0, f'min {min(spans):.2f} s')
    def shown(text):
        for spoken, display in SCRIPT.get('display', {}).items():
            text = re.sub(spoken, display, text, flags=re.I)
        return text
    words = lambda s: re.sub(r'[^a-z0-9$.×/ ]', '', s.lower()).replace('. ', ' ').split()
    check('subtitles match narration', words(' '.join(lines)) == words(' '.join(shown(s['say']) for s in SCRIPT['scenes'])))
    check('no "we" in narration', not re.search(r"\b(we|we're|our|us)\b", ' '.join(s['say'] for s in SCRIPT['scenes']), re.I))
    texts = [c.splitlines()[2:] for c in cues]
    orphans = [t for t in texts if len(t) == 2 and (len(t[1].split()) == 1 or len(t[0].split()) == 1)]
    check('no single-word caption lines', not orphans, str(orphans[:2]))
    unbalanced = [t for t in texts if len(t) == 2 and min(map(len, t)) < 0.45 * max(map(len, t))]
    check('two-line captions balanced', not unbalanced, str(unbalanced[:2]))
    speeds = [sum(map(len, t)) / d for t, d in zip(texts, spans)]
    check('reading speed <= 21 chars/s', max(speeds) <= 21, f'max {max(speeds):.1f}')
    check('numbers shown as digits', not re.search(r'fifty-seven|eight cents|twenty-four seven|P P E', srt))

    numbers = json.loads((build / 'cards/numbers.json').read_text())
    said = ' '.join(s['say'] for s in SCRIPT['scenes']).lower()
    check('spoken numbers match evidence', all(x in said for x in ('fifty-seven dollars', 'three dollars and eight cents', 'eighteen times', 'two of three'))
          and numbers['ai'] == '$3.08' and numbers['person'] == '$57' and numbers['ratio'] == '18×' and numbers['missed'] == '2 of 3', json.dumps(numbers, ensure_ascii=False))
    live = json.loads((build / 'live-run.json').read_text())
    check('one live run, <= $0.15', live['usage_usd'] <= 0.15, f"{live['run_id'][:8]} ${live['usage_usd']}")

    # Caption style: geometry from the editor's log, then pixels sampled inside/around each box on the real frames.
    log = json.loads((build / ('captions-preview.json' if '-preview' in video.name else 'captions.json')).read_text())
    cap = cv2.VideoCapture(str(video)); bad = []
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)); fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    samples = []
    for c in log:
        if not c['box']:
            bad.append((round(c['start'], 1), 'never drawn')); continue
        x, y, w, h = c['box']
        if abs(x + w / 2 - 0.5) > 0.01: bad.append((round(c['start'], 1), 'off-center'))
        if not (0.80 < y + h < 0.97): bad.append((round(c['start'], 1), f'bottom edge {y + h:.2f}'))
        if w > 0.8: bad.append((round(c['start'], 1), f'too wide {w:.2f}'))
    for c in log[::3]:
        if not c['box']: continue
        mid = (c['start'] + c['end']) / 2
        cap.set(cv2.CAP_PROP_POS_MSEC, mid * 1000); ok, img = cap.read()
        if not ok: continue
        x, y, w, h = c['box']; X, Y, Wd, Hd = int(x * fw), int(y * fh), int(w * fw), int(h * fh)
        inside = cv2.cvtColor(img[Y:Y + Hd, X:X + Wd], cv2.COLOR_BGR2GRAY)
        textp, panelp = (inside > 200).mean(), (inside < 90).mean()
        if not (0.04 < textp < 0.40) or panelp < 0.45:
            bad.append((round(mid, 1), f'contrast text {textp:.2f} panel {panelp:.2f}'))
        # Overlap: on dark backgrounds, other bright content in the caption rows next to the box.
        rowband = cv2.cvtColor(img[Y:Y + Hd], cv2.COLOR_BGR2GRAY)
        side = np.concatenate([rowband[:, :max(1, X - 30)], rowband[:, X + Wd + 30:]], axis=1)
        if side.mean() < 90 and (side > 150).mean() > 0.01:
            bad.append((round(mid, 1), f'overlapping content {(side > 150).mean():.3f}'))
        samples.append(img[int(fh * 0.66):, :])
    check('caption panel style (dark, bright text, centered, bottom band)', not bad, str(bad[:4]))
    if samples:
        strip = np.vstack([cv2.resize(x, (960, int(x.shape[0] * 960 / x.shape[1]))) for x in samples[:10]])
        cv2.imwrite(str(build / 'caption-strip.jpg'), strip)

    # Contact sheet, 1 frame per 5 s; flag near-black frames (outside fades).
    cap = cv2.VideoCapture(str(video)); tiles, dark = [], []
    for t in np.arange(2.5, seconds - 1, 5.0):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000); ok, img = cap.read()
        if not ok: continue
        if img.mean() < 12: dark.append(round(float(t), 1))
        tile = cv2.resize(img, (480, 270)); cv2.putText(tile, f'{t:.0f}s', (8, 26), 0, .8, (0, 0, 255), 2); tiles.append(tile)
    while len(tiles) % 5: tiles.append(np.zeros_like(tiles[0]))
    cv2.imwrite(str(build / 'contact-sheet.jpg'), np.vstack([np.hstack(tiles[i:i + 5]) for i in range(0, len(tiles), 5)]))
    check('no black frames', not dark, str(dark))

    for name, ok, detail in results:
        print(('PASS ' if ok else 'FAIL ') + name + (f'  ({detail})' if detail else ''))
    print('contact sheet:', build / 'contact-sheet.jpg')
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == '__main__':
    main()
