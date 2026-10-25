"""Check the course submission against docs/submission-goals.md (gates G1-G7). Offline; no model calls.

Usage: ./scripts/uv run --no-sync python scripts/check_submission.py [--skip-print]
Exit code 0 only when every gate passes.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'submission/evidence'
CHROME = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
REPORT_SECTIONS = ['Title', 'Motivation', 'What is new', 'agents', 'Tokenomics', 'Safety', 'Impact', 'Limits']
PLACEHOLDERS = re.compile(r'\b(pending|TBD|TODO|not captured|placeholder)\b', re.I)
SECRET = re.compile(r'sk-[A-Za-z0-9_-]{20,}|(?:PORTKEY|OPENAI)_API_KEY=[\'"]?[A-Za-z0-9_-]{12,}')
results = []


def gate(name, problems):
    results.append((name, problems))


def text_of(html):
    html = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.S)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))


def sections(html):
    """Split the report at its numbered <h1> page titles; returns {number: html}."""
    parts = {}
    for match in re.finditer(r'<section class="page"[^>]*data-section="(\d)"[^>]*>(.*?)</section>', html, re.S):
        parts.setdefault(int(match.group(1)), '')
        parts[int(match.group(1))] += match.group(2)
    return parts


def print_pages(path):
    if not CHROME.exists():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / 'out.pdf'
        subprocess.run([str(CHROME), '--headless=new', '--disable-gpu', '--no-pdf-header-footer',
                        f'--print-to-pdf={pdf}', path.as_uri()], capture_output=True, timeout=120)
        if not pdf.exists():
            return None
        return len(re.findall(rb'/Type\s*/Page[^s]', pdf.read_bytes()))


def check_report(skip_print):
    path = ROOT / 'report.html'
    html = path.read_text(encoding='utf-8')
    text = text_of(html)
    problems = []
    parts = sections(html)
    if sorted(parts) != list(range(1, 9)):
        problems.append(f'sections tagged data-section 1-8 expected, found {sorted(parts)}')
    for number, word in enumerate(REPORT_SECTIONS, 1):
        if number in parts and word.lower() not in text_of(parts[number]).lower():
            problems.append(f'section {number} does not mention "{word}"')
    for src in re.findall(r'<img[^>]+src="([^"]+)"', html):
        if not src.startswith('data:'):
            problems.append(f'image not embedded: {src[:60]}')
    for href in re.findall(r'href="([^"]+)"', html):
        if not href.startswith(('http://', 'https://', '#', 'data:', 'mailto:')):
            problems.append(f'relative link breaks after download: {href}')
    if not re.search(r'https://github\.com/[\w.-]+/[\w.-]+', html):
        problems.append('no GitHub repository URL')
    if found := sorted(set(m.group(0).lower() for m in PLACEHOLDERS.finditer(text))):
        problems.append(f'placeholder words present: {found}')
    if not skip_print:
        pages = print_pages(path)
        if pages is None:
            problems.append('could not print with headless Chrome')
        elif not 10 <= pages <= 15:
            problems.append(f'prints to {pages} pages; 10-15 required')
    gate('G1 Report form', problems)
    return html, parts


def check_evidence(parts):
    problems = []
    trace = parts.get(4, '')
    if len(re.findall(r'<img', trace)) < 4:
        problems.append(f'section 4 has {len(re.findall(r"<img", trace))} screenshots; 4+ needed for the traced run')
    tokens = text_of(parts.get(5, '')).lower()
    for needed in ('input tokens', 'cached', 'output tokens', '100 users', '10,000 users', 'before', 'after'):
        if needed not in tokens:
            problems.append(f'section 5 lacks "{needed}"')
    if not (EVIDENCE / 'token-usage.json').exists():
        problems.append('submission/evidence/token-usage.json missing (run scripts/extract_token_usage.py)')
    safety = parts.get(6, '')
    if 'class="terminal"' not in safety and '<img' not in safety:
        problems.append('section 6 does not embed test output or a screenshot of a defense')
    if not (EVIDENCE / 'safety-tests.txt').exists():
        problems.append('submission/evidence/safety-tests.txt missing')
    trial = EVIDENCE / 'self-trial.json'
    if not trial.exists():
        problems.append('submission/evidence/self-trial.json missing: Mason must run the timed self-trial (worksheet)')
    else:
        trials = json.loads(trial.read_text()).get('trials', [])
        conditions = {t.get('condition') for t in trials}
        if len(trials) < 4 or not {'manual', 'assisted'} <= conditions:
            problems.append('self-trial needs at least 4 timed trials covering manual and assisted')
        if 'data-self-trial' not in parts.get(7, ''):
            problems.append('section 7 does not render the self-trial table')
    gate('G2 Report evidence', problems)


def check_slides():
    html = (ROOT / 'slides.html').read_text(encoding='utf-8')
    problems = []
    count = len(re.findall(r'<section', html))
    if not 1 <= count <= 10:
        problems.append(f'{count} slides; 10 maximum')
    if not re.search(r'<script[^>]+src="https://cdn\.jsdelivr\.net/npm/reveal\.js@[^"]+"', html):
        problems.append('reveal.js is not loaded from a CDN <script src>')
    images = re.findall(r'<img[^>]+src="([^"]+)"', html)
    if any(not src.startswith('data:') for src in images):
        problems.append('a slide image is not embedded')
    if len(images) < 2:
        problems.append(f'{len(images)} embedded screenshots; 2+ needed')
    if 'class="diagram"' not in html:
        problems.append('no agent diagram (class="diagram")')
    text = text_of(html).lower()
    for needed in ('per run', 'time'):
        if needed not in text:
            problems.append(f'slides lack "{needed}"')
    gate('G3 Slides', problems)


def check_repository():
    problems = []
    readme = (ROOT / 'README.md').read_text(encoding='utf-8').splitlines()
    if not any('quick start' in line.lower() for line in readme[:40]):
        problems.append('README has no "Quick start" in its first 40 lines')
    for line in (ROOT / '.env.example').read_text().splitlines():
        if re.match(r'\s*[A-Z_]*(KEY|SECRET|TOKEN)\s*=\s*\S', line):
            problems.append(f'.env.example holds a value: {line.split("=")[0]}')
    sys.path.insert(0, str(ROOT / 'scripts'))
    from package_submission import selected
    for path in selected():
        relative = path.relative_to(ROOT)
        if path.name == '.env' or path.suffix in ('.sqlite', '.pt', '.pth', '.onnx', '.bundle'):
            problems.append(f'package includes {relative}')
        elif path.stat().st_size > 50_000_000:
            problems.append(f'package file over 50 MB: {relative}')
        elif path.suffix in ('.py', '.md', '.json', '.yaml', '.toml', '.ts', '.tsx', '.html', '.txt', '.ndjson'):
            if SECRET.search(path.read_text(errors='ignore')):
                problems.append(f'possible secret in {relative}')
    gate('G4 Repository hygiene', problems)


def newest_source_change():
    newest = 0.0
    for folder in ('backend/src', 'frontend/src', 'configs'):
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                newest = max(newest, path.stat().st_mtime)
    return newest


def check_product():
    problems = []
    receipts = sorted(EVIDENCE.glob('verification-*.json'), key=lambda p: p.stat().st_mtime)
    if not receipts:
        problems.append('no verification receipt')
    else:
        latest = receipts[-1]
        data = json.loads(latest.read_text())
        if latest.stat().st_mtime < newest_source_change():
            problems.append(f'{latest.name} is older than the latest source change; rerun the checks')
        if data.get('failed', 0) or data.get('contracts') != 'match' or data.get('production_build') != 'passed':
            problems.append(f'{latest.name} records a failure')
        if data.get('provider_requests_during_checks', 1) != 0:
            problems.append('checks made provider requests')
    gate('G5 Product checks', problems)


def check_fresh_clone():
    path = EVIDENCE / 'fresh-clone.json'
    problems = []
    if not path.exists():
        problems.append('submission/evidence/fresh-clone.json missing')
    else:
        data = json.loads(path.read_text())
        if not data.get('healthy') or data.get('seconds', 999) >= 600 or not data.get('env_in_parent'):
            problems.append('fresh clone receipt does not show a healthy start under 600 s with .env one folder up')
    gate('G6 Fresh clone', problems)


def check_docs():
    problems = []
    report_time = (ROOT / 'report.html').stat().st_mtime
    for name in ('harness.md', 'docs/course-requirements.md'):
        if (ROOT / name).stat().st_mtime < report_time:
            problems.append(f'{name} not updated since the last report build')
    gate('G7 Documentation', problems)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-print', action='store_true', help='Skip the headless Chrome page count')
    args = parser.parse_args()
    _, parts = check_report(args.skip_print)
    check_evidence(parts)
    check_slides()
    check_repository()
    check_product()
    check_fresh_clone()
    check_docs()
    for name, problems in results:
        print(('PASS ' if not problems else 'FAIL ') + name)
        for problem in problems:
            print('     - ' + problem)
    sys.exit(0 if all(not problems for _, problems in results) else 1)


if __name__ == '__main__':
    main()
