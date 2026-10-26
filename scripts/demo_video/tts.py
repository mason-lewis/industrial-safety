"""Narration audio for the demo video via OpenAI text-to-speech (OPENAI_TTS_API_KEY, the creator's own key).

Usage: ./scripts/uv run --no-sync python scripts/demo_video/tts.py OUT_DIR [--only SCENE_ID]
Each line is cached as OUT_DIR/tts/<sha>.wav keyed by model, voice, instructions and text, so re-edits do not re-bill.
Usage is appended to OUT_DIR/tts-usage.json. The key is read from .env (repo, then parent) and never printed.
"""
import argparse
import hashlib
import json
import time
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = json.loads((Path(__file__).with_name('script.json')).read_text())
PRICE_PER_MILLION_CHARS_ESTIMATE = 15.0  # rough upper bound for logging only; billing is per token


def key():
    for path in (ROOT / '.env', ROOT.parent / '.env'):
        value = dotenv_values(path).get('OPENAI_TTS_API_KEY')
        if value:
            return value
    raise SystemExit('OPENAI_TTS_API_KEY is not set in .env')


def speak(text, out, voice):
    body = json.dumps({'model': voice['model'], 'voice': voice['voice'], 'input': text,
                       'instructions': voice['instructions'], 'response_format': 'wav'}).encode()
    request = urllib.request.Request('https://api.openai.com/v1/audio/speech', data=body, method='POST',
                                     headers={'Authorization': 'Bearer ' + key(), 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=120) as response:
        out.write_bytes(response.read())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--only')
    args = parser.parse_args()
    voice = SCRIPT['voice']
    folder = args.out / 'tts'
    folder.mkdir(parents=True, exist_ok=True)
    manifest, log = {}, []
    for scene in SCRIPT['scenes']:
        if args.only and scene['id'] != args.only:
            continue
        digest = hashlib.sha256(json.dumps([voice, scene['say']], sort_keys=True).encode()).hexdigest()[:16]
        out = folder / f'{digest}.wav'
        if not out.exists():
            speak(scene['say'], out, voice)
            log.append({'scene': scene['id'], 'chars': len(scene['say']), 'at': time.strftime('%Y-%m-%dT%H:%M:%S')})
        manifest[scene['id']] = str(out)
    (args.out / 'tts-manifest.json').write_text(json.dumps(manifest, indent=1))
    usage_path = args.out / 'tts-usage.json'
    usage = json.loads(usage_path.read_text()) if usage_path.exists() else []
    usage += log
    usage_path.write_text(json.dumps(usage, indent=1))
    chars = sum(entry['chars'] for entry in usage)
    print(json.dumps({'generated_now': len(log), 'lines': len(manifest), 'total_chars_billed': chars,
                      'rough_cost_usd_upper': round(chars * PRICE_PER_MILLION_CHARS_ESTIMATE / 1e6, 4)}))


if __name__ == '__main__':
    main()
