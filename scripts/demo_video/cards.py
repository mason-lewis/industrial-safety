"""Render the demo's title/stat cards and overlays as 1920x1080 PNGs with headless Chrome.

Usage: ./scripts/uv run --no-sync python scripts/demo_video/cards.py OUT_DIR
Numbers are read from submission/evidence (token-usage.json, self-trial.json), the same sources as the report.
"""
import base64
import json
import statistics
import subprocess
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'submission/evidence'
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
RUNS = ['686811e3', '46f88eee', '5caab826', '4093066f', 'dc940ad9', '01e678b7']   # same six runs as report §5
WAGE = 40.34
REPO = json.loads((EVIDENCE / 'repository.json').read_text())['url']


def numbers():
    usage = {r['run_id'][:8]: r for r in json.loads((EVIDENCE / 'token-usage.json').read_text())['runs']}
    per_minute = [usage[r]['usage_usd'] / (usage[r]['duration_s'] / 60) for r in RUNS]
    ai_hour = statistics.median(per_minute) * 60
    trials = json.loads((EVIDENCE / 'self-trial.json').read_text())['trials']
    manual = [t for t in trials if t['condition'] == 'manual']
    person_hour = sum(t['elapsed_seconds'] for t in manual) / sum(t['video_seconds'] for t in manual) * WAGE
    missed = sum(len(t.get('missed', [])) for t in trials)
    return {'ai': f'${ai_hour:.2f}', 'person': f'${person_hour:.0f}', 'ratio': f'{person_hour / ai_hour:.0f}×',
            'month': f'${ai_hour * 720:,.0f}', 'missed': f'{missed} of 3'}


BASE = '''<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800;900&family=JetBrains+Mono:wght@500&display=swap">
<style>*{box-sizing:border-box;margin:0}html,body{width:1920px;height:1080px;overflow:hidden}body{font-family:Inter,Helvetica,Arial,sans-serif;color:#fff;background:%s}
.y{color:#f2c500}.chip{align-self:flex-start;display:inline-block;background:#f2c500;color:#111;font-weight:800;font-size:30px;letter-spacing:.14em;padding:10px 20px;border-radius:8px;text-transform:uppercase}
.big{font-weight:900;letter-spacing:-.03em;line-height:.95}.mid{font-size:48px;font-weight:600;line-height:1.25}.muted{color:#9aa3ab}
.wrap{position:absolute;inset:0;padding:70px 150px 250px;display:flex;flex-direction:column;justify-content:center}
.stripe{position:absolute;left:0;right:0;top:0;height:22px;background:repeating-linear-gradient(-45deg,#f2c500 0 28px,#111 28px 56px)}
.logo{align-self:flex-start;display:inline-flex;align-items:center;gap:22px;font-weight:800;font-size:40px;letter-spacing:.02em}.logo i{display:grid;place-items:center;width:78px;height:78px;border-radius:18px;background:#f2c500;color:#111;font-style:normal;font-size:48px}
code{font-family:"JetBrains Mono",Menlo,monospace}</style><body>%s</body>'''


def img(path):
    return 'data:image/jpeg;base64,' + base64.b64encode(Path(path).read_bytes()).decode()


def cards(n):
    tests = [l.split('::', 1)[-1] for l in (EVIDENCE / 'safety-tests.txt').read_text().splitlines()[1:4]]
    return {
        'overlay-cold-open': ('transparent', '''<div style="position:absolute;left:150px;top:330px;right:150px">
<div class="big" style="font-size:150px;text-shadow:0 6px 40px #000c">2.5 million</div>
<div class="mid" style="font-size:60px;text-shadow:0 4px 24px #000c">U.S. workplace injuries a year <span class="muted" style="color:#ddd;font-size:34px">(BLS, 2024)</span></div></div>'''),
        'title': ('#111', f'''<div class="wrap"><div class="logo"><i><svg viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"><path d="M12 3 2 21h20L12 3z"/><path d="M12 10v5M12 18v.5" stroke-linecap="round"/></svg></i>MGT 409 · FINAL PROJECT</div>
<div class="big" style="font-size:190px;margin-top:60px">Industrial<br><span class="y">Safety</span></div>
<div class="mid" style="margin-top:50px;max-width:1500px">An AI safety reviewer for workplace camera footage.</div></div><div class="stripe"></div>'''),
        'cost-gap': ('#111', f'''<div class="wrap"><div class="chip">The problem</div>
<div style="display:flex;gap:140px;margin-top:70px;align-items:flex-end">
<div><div class="big" style="font-size:210px">{n["person"]}<span style="font-size:90px">/hr</span></div><div class="mid muted">a safety specialist<br>reviewing one camera</div></div>
<div class="reveal" style="font-size:120px;color:#555;padding-bottom:120px">→</div>
<div class="reveal"><div class="big y" style="font-size:210px">{n["ai"]}<span style="font-size:90px">/hr</span></div><div class="mid">Industrial Safety,<br>per camera</div></div></div></div><div class="stripe"></div>'''),
        'terminal': ('#0d0d0d', f'''<div class="wrap"><div class="chip">Runs on your computer</div>
<div style="margin-top:60px;background:#1b1b1b;border-radius:22px;padding:56px 64px;font-size:46px;line-height:1.7;box-shadow:0 30px 80px #000">
<code><span class="muted">$</span> git clone {escape(REPO.removeprefix("https://"))}<br><span class="muted">$</span> cd industrial-safety<br><span class="muted">$</span> <span class="y">./start</span><br><span class="muted">→ open http://127.0.0.1:8000</span></code></div></div>'''),
        'injection': ('#111', f'''<div class="wrap"><div class="chip">Safety test</div>
<div class="mid" style="margin-top:28px;font-size:50px">Instructions written on the video:</div>
<div style="position:relative;margin-top:26px;background:#fff;border-radius:16px;padding:26px 40px;width:1200px">
<img src="{img(ROOT / 'submission/images/injection-frame-text.jpg')}" style="width:100%;display:block">
<div style="position:absolute;right:-40px;top:-50px;transform:rotate(-8deg);border:10px solid #e5322d;color:#e5322d;font-weight:900;font-size:84px;padding:6px 30px;border-radius:14px;background:#fffffff0">REJECTED</div></div>
<div style="margin-top:34px;font-size:24px;line-height:1.5;color:#9fe3a8"><code>{"<br>".join(escape(t) for t in tests)}</code></div></div>'''),
        'cost': ('#111', f'''<div class="wrap"><div class="chip">Cost to run · live, 24/7</div>
<div class="big y" style="font-size:230px;margin-top:50px">{n["ai"]}<span style="font-size:100px">/hr</span></div>
<div class="mid" style="margin-top:10px">per camera · about {n["month"]} a month, around the clock</div>
<div class="big reveal" style="font-size:120px;margin-top:70px">{n["ratio"]} <span style="font-size:70px;font-weight:700">cheaper than a person</span></div>
<div class="mid muted" style="margin-top:30px;font-size:38px">Hard caps: $0.15 per run · $2 per day · $20 per month</div></div><div class="stripe"></div>'''),
        'limits': ('#111', f'''<div class="wrap"><div class="chip" style="background:#fff">Honest limits</div>
<div class="big" style="font-size:120px;margin-top:60px">Not perfect <span class="y">yet.</span></div>
<ul style="margin-top:60px;font-size:54px;line-height:1.6;font-weight:600;padding-left:60px">
<li>Slower per clip in my timed test</li><li>Missed {n["missed"]} fast accidents</li><li>Next: catch fast impacts, then a real pilot</li></ul></div>'''),
        'end': ('#f2c500', f'''<div class="wrap" style="color:#111;align-items:flex-start"><div class="logo" style="color:#111"><i style="background:#111;color:#f2c500"><svg viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"><path d="M12 3 2 21h20L12 3z"/><path d="M12 10v5M12 18v.5" stroke-linecap="round"/></svg></i>INDUSTRIAL SAFETY</div>
<div class="big" style="font-size:150px;margin-top:60px">Coverage nobody<br>could afford before.</div>
<div style="margin-top:70px;font-size:54px;font-weight:700"><code>{escape(REPO.removeprefix("https://"))}</code></div>
<div style="margin-top:20px;font-size:36px;font-weight:600">Mason Lewis · MGT 409 · Fall 2026</div></div>'''),
        'badge-live': ('transparent', '''<div style="position:absolute;left:800px;top:82px;display:flex;gap:16px;align-items:center">
<div style="background:#e5322d;color:#fff;font-weight:800;font-size:30px;padding:10px 20px;border-radius:10px;letter-spacing:.06em">● REAL RUN</div>
<div style="background:#111e;color:#fff;font-weight:700;font-size:30px;padding:10px 20px;border-radius:10px">SPED UP</div></div>'''),
    }


def render(out, name, background, body):
    html = out / f'{name}.html'
    html.write_text(BASE % (background, body))
    png = out / f'{name}.png'
    args = [CHROME, '--headless=new', '--hide-scrollbars', '--window-size=1920,1080', f'--screenshot={png}',
            '--virtual-time-budget=4000', html.as_uri()]
    if background == 'transparent':
        args.insert(3, '--default-background-color=00000000')
    subprocess.run(args, capture_output=True, timeout=60, check=True)
    return png


def main():
    out = Path(sys.argv[1]) / 'cards'
    out.mkdir(parents=True, exist_ok=True)
    n = numbers()
    for name, (background, body) in cards(n).items():
        render(out, name, background, body)
        if 'class="reveal' in body or 'class="big reveal' in body:  # first beat of a two-step reveal
            render(out, name + '-a', background, body.replace('<body>', '') + '<style>.reveal{visibility:hidden}</style>')
    (out / 'numbers.json').write_text(json.dumps(n, indent=1, ensure_ascii=False))
    print(json.dumps(n, ensure_ascii=False))


if __name__ == '__main__':
    main()
