"""Summarize real provider-reported token usage per hosted run from the local audit log.

Read-only: opens data/app.sqlite and data/audit/audit.sqlite in SQLite read-only mode and
writes submission/evidence/token-usage.json. Makes no model calls.
"""
import collections
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_tokens', 'uncached_input_tokens',
          'output_tokens', 'reasoning_tokens')


def readonly(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def source_names():
    names = {}
    for path in sorted((ROOT / 'data/input').glob('*.mp4')):
        names[hashlib.sha256(path.read_bytes()).hexdigest()] = path.name
    return names


def main():
    app = readonly(ROOT / 'data/app.sqlite')
    audit = readonly(ROOT / 'data/audit/audit.sqlite')
    hashes = source_names()
    media = {}
    for (body,) in app.execute("select body from objects where kind='media'"):
        record = json.loads(body)
        media[record['id']] = record
    runs = {}
    for (body,) in app.execute("select body from objects where kind='review_run'"):
        record = json.loads(body)
        runs[record['id']] = record

    totals = collections.defaultdict(collections.Counter)
    agents = collections.defaultdict(collections.Counter)
    started = {}
    for (body,) in audit.execute("select body from audit where body like '%\"model_response\"%' order by sequence"):
        event = json.loads(body)
        if event['event_type'] != 'model_response':
            continue
        usage = event.get('resources') or {}
        trace = event['trace_id']
        if trace not in runs or usage.get('usage_is_synthetic'):
            continue
        for field in FIELDS:
            totals[trace][field] += usage.get(field) or 0
        totals[trace]['usage_usd'] += usage.get('usage_cost_usd') or 0
        totals[trace]['requests'] += 1
        agents[trace][event.get('agent_name') or 'unknown'] += 1
        started.setdefault(trace, event['timestamp_utc'])

    result = []
    for trace in sorted(totals, key=started.get):
        run = runs[trace]
        source = media.get(run.get('media_id') or trace, {})
        count = totals[trace]
        if not count['input_tokens']:
            continue
        result.append({
            'run_id': trace, 'started_utc': started[trace], 'kind': run.get('kind'),
            'source': hashes.get(source.get('content_hash'), source.get('source_id')),
            'duration_s': round((run.get('end_ms') or 0) / 1000, 1),
            **{field: count[field] for field in FIELDS},
            'requests': count['requests'], 'usage_usd': round(count['usage_usd'], 6),
            'requests_by_agent': dict(agents[trace]),
        })
    output = ROOT / 'submission/evidence/token-usage.json'
    output.write_text(json.dumps({
        'basis': 'provider-reported tokens from data/audit model_response events; usage_usd is local calculated cost, not an invoice',
        'model': 'gpt-6-luna', 'runs': result}, indent=2) + '\n')
    print(json.dumps({'runs': len(result), 'output': str(output.relative_to(ROOT))}))


if __name__ == '__main__':
    main()
