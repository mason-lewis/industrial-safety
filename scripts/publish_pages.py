"""Build the GitHub Pages site (landing page, report, slides, demo video page) into a folder.

Usage: ./scripts/uv run --no-sync python scripts/publish_pages.py SITE_DIR
Then commit SITE_DIR's contents to the gh-pages branch of github.com/mason-lewis/industrial-safety.
The demo video is included only when submission/video/industrial-safety-demo.mp4 exists; its .srt becomes a
WebVTT captions track. Offline; copies files only.
"""
import json
import re
import shutil
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = json.loads((ROOT / 'submission/evidence/repository.json').read_text())['url']
VIDEO = ROOT / 'submission/video/industrial-safety-demo.mp4'

STYLE = '''*{box-sizing:border-box}body{margin:0;font-family:Inter,"Helvetica Neue",Arial,sans-serif;color:#111;background:#f4f3ef}
a{color:inherit}.top{background:#f2c500;padding:56px 7vw 40px}.top h1{font-size:clamp(40px,6vw,76px);line-height:1;margin:14px 0 16px;letter-spacing:-.02em}
.top p{font-size:20px;max-width:760px;line-height:1.45;margin:0}.chip{display:inline-block;background:#111;color:#f2c500;font-weight:800;font-size:13px;letter-spacing:.12em;padding:6px 10px;border-radius:6px}
main{padding:36px 7vw 60px;display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px}
.card{display:block;background:#fff;border-radius:16px;padding:26px;text-decoration:none;box-shadow:0 2px 10px #0001;transition:transform .15s,box-shadow .15s}
.card:hover{transform:translateY(-3px);box-shadow:0 10px 24px #0002}.card b{display:block;font-size:24px;margin-bottom:8px}.card span{color:#5b6570;line-height:1.4}
.card.dark{background:#111;color:#fff}.card.dark span{color:#bbb}footer{padding:0 7vw 40px;color:#5b6570;font-size:14px}
.player{padding:30px 7vw 50px}video{width:100%;max-width:1200px;border-radius:14px;background:#000;display:block;box-shadow:0 10px 30px #0003}'''


def page(title, body):
    return (f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(title)}</title><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap">'
            f'<style>{STYLE}</style><body>{body}</body></html>')


def srt_to_vtt(srt):
    return 'WEBVTT\n\n' + re.sub(r'(\d\d:\d\d:\d\d),(\d\d\d)', r'\1.\2', srt.strip()) + '\n'


def main():
    site = Path(sys.argv[1])
    site.mkdir(parents=True, exist_ok=True)
    for name in ('report.html', 'slides.html'):
        shutil.copy2(ROOT / name, site / name)
    has_video = VIDEO.exists()
    if has_video:
        shutil.copy2(VIDEO, site / 'demo.mp4')
        try:  # poster frame from the title card (the video opens on a fade from black)
            import subprocess, imageio_ffmpeg
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-loglevel', 'error', '-ss', '13', '-i', str(VIDEO),
                            '-frames:v', '1', '-q:v', '3', str(site / 'demo-poster.jpg')], check=True)
        except Exception:
            pass
        srt = VIDEO.with_suffix('.srt')
        if srt.exists():
            (site / 'demo.vtt').write_text(srt_to_vtt(srt.read_text()))
        track = '<track kind="captions" src="demo.vtt" srclang="en" label="English">' if srt.exists() else ''
        (site / 'demo.html').write_text(page('Industrial Safety — demo video', f'''<header class="top"><span class="chip">DEMO VIDEO</span><h1>Industrial Safety</h1>
<p>A 2½-minute narrated demo of the app running on real workplace footage. Subtitles are burned in.</p></header>
<section class="player"><video controls preload="metadata" playsinline poster="demo-poster.jpg" src="demo.mp4">{track}</video></section>
<footer><a href="./">← All materials</a> · <a href="{REPO}">Source code on GitHub</a></footer>'''))
    demo_card = ('<a class="card dark" href="demo.html"><b>▶ Demo video</b><span>2½-minute narrated walkthrough of a real run.</span></a>'
                 if has_video else '')
    (site / 'index.html').write_text(page('Industrial Safety', f'''<header class="top"><span class="chip">MGT 409 · FINAL PROJECT</span><h1>Industrial Safety</h1>
<p>An AI safety reviewer for workplace camera footage: every 10 seconds checked, every concern backed by frames, every decision made by a person, for about $3.08/hour per camera.</p></header>
<main>{demo_card}<a class="card" href="report.html"><b>Report</b><span>The full write-up: problem, agents, cost, safety, impact and limits.</span></a>
<a class="card" href="slides.html"><b>Slides</b><span>10-slide deck. Use ← → keys or the buttons; full-screen button in the corner.</span></a>
<a class="card" href="{REPO}"><b>Source code</b><span>Public GitHub repo with setup instructions (<code>./start</code>).</span></a></main>
<footer>Mason Lewis · MGT 409 AI Foundations for Managers · Fall 2026</footer>'''))
    (site / '.nojekyll').write_text('')
    print(json.dumps({'site': str(site), 'files': sorted(p.name for p in site.iterdir()), 'video': has_video}))


if __name__ == '__main__':
    main()
