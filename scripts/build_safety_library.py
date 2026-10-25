#!/usr/bin/env python3
"""Build a local federal source snapshot from official GPO bulk XML. No model calls.

Network acquisition is separate: supply downloaded original files. Existing snapshots
are never overwritten. Markdown is a whitespace-normalized transcription, not a summary.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

CATALOG = {
    '1910.5': 'applicability', '1910.6': 'incorporation by reference',
    '1910.12': 'construction applicability', '1910.21': 'walking surfaces definitions',
    '1910.22': 'housekeeping walking surfaces spills', '1910.23': 'ladders',
    '1910.25': 'stairs', '1910.26': 'dockboards', '1910.28': 'fall protection duties',
    '1910.29': 'fall protection systems', '1910.30': 'fall training',
    '1910.36': 'exit routes design', '1910.37': 'exit routes obstructions',
    '1910.38': 'emergency action plans', '1910.39': 'fire prevention plans',
    '1910.95': 'noise exposure', '1910.106': 'flammable liquids',
    '1910.119': 'process safety highly hazardous chemicals',
    '1910.120': 'hazardous waste emergency response',
    '1910.132': 'PPE hazard assessment', '1910.133': 'eye face protection',
    '1910.134': 'respiratory protection', '1910.135': 'head protection',
    '1910.136': 'foot protection', '1910.137': 'electrical protective equipment',
    '1910.138': 'hand protection', '1910.140': 'personal fall protection',
    '1910.141': 'sanitation', '1910.146': 'permit required confined spaces',
    '1910.147': 'lockout tagout hazardous energy',
    '1910.151': 'medical first aid eyewash', '1910.157': 'fire extinguishers',
    '1910.176': 'materials storage aisles', '1910.178': 'forklift powered industrial trucks',
    '1910.179': 'overhead gantry cranes', '1910.184': 'slings rigging',
    '1910.212': 'machine guarding', '1910.219': 'power transmission guarding',
    '1910.242': 'hand portable tools compressed air',
    '1910.303': 'electrical installation', '1910.305': 'electrical wiring cords',
    '1910.333': 'electrical work practices deenergizing',
    '1910.1200': 'hazard communication labels SDS',
    '1904.7': 'injury illness recordability', '1904.39': 'fatality severe injury reporting',
    '1926.20': 'construction general safety', '1926.21': 'construction training',
    '1926.28': 'construction PPE', '1926.250': 'construction materials storage',
    '1926.416': 'construction electrical exposure',
    '1926.417': 'construction lockout tagging circuits',
    '1926.451': 'construction scaffolds', '1926.500': 'construction fall definitions scope',
    '1926.501': 'construction fall protection duties',
    '1926.502': 'construction fall protection systems',
    '1926.503': 'construction fall training',
    '1926.602': 'construction material handling equipment',
    '1926.651': 'construction excavation', '1926.652': 'construction excavation protection',
    '1926.1053': 'construction ladders',
}
BULK_URL = 'https://www.govinfo.gov/bulkdata/ECFR/title-29/ECFR-title29.xml'
ACT_URL = 'https://www.govinfo.gov/content/pkg/USCODE-2024-title29/html/USCODE-2024-title29-chap15-sec654.htm'


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def text_of(element: ET.Element) -> str:
    return re.sub(r'\s+', ' ', ''.join(element.itertext())).strip()


def blocks(element: ET.Element) -> list[str]:
    """Keep each paragraph; retain table cell structure and other non-prose data."""
    result = []
    for child in element:
        if child.tag in ('TABLE', 'GPOTABLE'):
            result.append('```xml\n' + ET.tostring(child, encoding='unicode').strip() + '\n```')
        elif child.tag in ('HEAD', 'HD', 'P', 'FP', 'CITA', 'SOURCE', 'AUTH'):
            value = text_of(child)
            if value:
                result.append(('## ' if child.tag in ('HEAD', 'HD') else '') + value)
        elif list(child):
            result.extend(blocks(child))
        else:
            # Includes graphics references: preserve the element, never invent pixels.
            value = text_of(child)
            result.append(value or '```xml\n' + ET.tostring(child, encoding='unicode').strip() + '\n```')
    return result


def build(xml_path: Path, act_path: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    (output / 'raw').mkdir()
    (output / 'markdown').mkdir()
    raw = xml_path.read_bytes()
    root = ET.fromstring(raw)
    amended = root.find('.//AMDDATE')
    if amended is None:
        raise ValueError('Missing official eCFR snapshot date')
    snapshot_date = text_of(amended)
    sections = {e.get('N', '').replace('§ ', ''): e for e in root.iter('DIV8') if e.get('TYPE') == 'SECTION'}
    missing = set(CATALOG) - sections.keys()
    if missing:
        raise ValueError(f'Missing required sections: {sorted(missing)}')
    shutil.copyfile(xml_path, output / 'raw' / 'ECFR-title29.xml')
    retrieved = datetime.now(timezone.utc).isoformat()
    manifest = {'format_version': 1, 'retrieved_at': retrieved,
                'snapshot_date': snapshot_date, 'bulk_url': BULK_URL,
                'bulk_sha256': digest(raw), 'documents': []}
    for number, topics in CATALOG.items():
        element = sections[number]
        title = text_of(element.find('HEAD'))
        source_id = 'cfr-' + number.replace('.', '-')
        xml_name = f'raw/{source_id}.xml'
        md_name = f'markdown/{source_id}.md'
        section_raw = ET.tostring(element, encoding='utf-8', xml_declaration=True)
        (output / xml_name).write_bytes(section_raw)
        body = '\n\n'.join(blocks(element)) + '\n'
        canonical = f'https://www.osha.gov/laws-regs/regulations/standardnumber/{number.split(".")[0]}/{number}'
        metadata = {'id': source_id, 'title': title, 'topics': topics,
                    'source_type': 'requirement', 'jurisdiction': 'US-federal-OSHA',
                    'applicability': 'construction' if number.startswith('1926.') else
                        'recordkeeping' if number.startswith('1904.') else 'general_industry',
                    'url': canonical, 'download_url': BULK_URL, 'section': number,
                    'snapshot_date': snapshot_date, 'effective_date': None,
                    'version': 'sha256:' + digest(section_raw), 'retrieved_at': retrieved,
                    'raw_path': xml_name, 'markdown_path': md_name,
                    'raw_sha256': digest(section_raw), 'text_sha256': digest(body.encode()),
                    'graphics_require_original': any(e.tag.lower() in ('gph', 'img', 'graphic', 'math') for e in element.iter()),
                    'conversion': 'XML text, whitespace normalized; tables retained as XML; original snapshot preserved'}
        md = f'# {title}\n\n' + '\n'.join(f'- {k}: {metadata[k]}' for k in
            ('url', 'download_url', 'source_type', 'jurisdiction', 'applicability', 'snapshot_date', 'effective_date', 'version', 'retrieved_at', 'topics'))
        md += '\n\nSnapshot date is not a clause effective date. Resolve applicability and event-date history before making a finding. '
        md += 'Embedded nonmandatory notes remain guidance. Graphics are references unless separately retained.\n\n---\n\n' + body
        (output / md_name).write_text(md)
        metadata['markdown_sha256'] = digest(md.encode())
        manifest['documents'].append(metadata)
    act_raw = act_path.read_bytes()
    act_html = act_raw.decode('utf-8')
    if 'field-start:statute' not in act_html or '&sect;654.' not in act_html:
        raise ValueError('General Duty Clause download is not the official statute')
    # Only the marked statutory text; source credit retained in original HTML.
    statute = act_html.split('<!-- field-start:statute -->')[1].split('<!-- field-end:statute -->')[0]
    paragraphs = [html.unescape(re.sub('<[^>]+>', '', p)).strip()
                  for p in re.findall(r'<p[^>]*>(.*?)</p>', statute, re.S)]
    body = '\n\n'.join(paragraphs) + '\n'
    shutil.copyfile(act_path, output / 'raw' / 'usc-29-654.html')
    title = '29 USC 654 — Duties of employers and employees (OSH Act section 5)'
    md = f'# {title}\n\n- url: {ACT_URL}\n- source_type: requirement\n- edition: 2024 US Code, current through 2025-01-06\n\n'
    md += 'Section 5(a)(1) is the General Duty Clause. It is not a catch-all for every unsafe condition. '
    md += 'Establish the required enforcement elements and check specific applicable standards first.\n\n---\n\n' + body
    (output / 'markdown' / 'usc-29-654.md').write_text(md)
    manifest['documents'].append({'id': 'usc-29-654', 'title': title, 'topics': 'general duty applicability',
        'section': '29 USC 654', 'source_type': 'requirement', 'jurisdiction': 'US-federal-OSHA',
        'url': ACT_URL, 'download_url': ACT_URL, 'snapshot_date': '2024 edition; current through 2025-01-06',
        'effective_date': None, 'version': 'sha256:' + digest(act_raw), 'retrieved_at': retrieved,
        'raw_path': 'raw/usc-29-654.html', 'markdown_path': 'markdown/usc-29-654.md',
        'raw_sha256': digest(act_raw), 'text_sha256': digest(body.encode()), 'markdown_sha256': digest(md.encode())})
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Built {len(manifest["documents"])} complete government source documents in {output}')


def check(snapshot: Path) -> dict:
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    assert digest((snapshot / 'raw' / 'ECFR-title29.xml').read_bytes()) == manifest['bulk_sha256']
    original = ET.parse(snapshot / 'raw' / 'ECFR-title29.xml').getroot()
    sections = {e.get('N', '').replace('§ ', ''): e for e in original.iter('DIV8') if e.get('TYPE') == 'SECTION'}
    ids = set()
    for doc in manifest['documents']:
        assert doc['id'] not in ids, f'Duplicate: {doc["id"]}'
        ids.add(doc['id'])
        assert digest((snapshot / doc['raw_path']).read_bytes()) == doc['raw_sha256'], doc['id']
        for part in doc.get('raw_parts', []):
            assert digest((snapshot / part['path']).read_bytes()) == part['sha256'], part['path']
        md = (snapshot / doc['markdown_path']).read_text()
        assert digest(md.encode()) == doc['markdown_sha256'], doc['id']
        body = md.split('\n---\n\n', 1)[1]
        assert digest(body.encode()) == doc['text_sha256'], doc['id']
        if doc['raw_path'].endswith('.xml'):
            section = ET.fromstring((snapshot / doc['raw_path']).read_bytes())
            parent_section = sections[doc['section']]
            # Whitespace following the root belongs to the parent document.
            section.tail = parent_section.tail = None
            assert ET.tostring(section) == ET.tostring(parent_section), doc['id']
            assert body == '\n\n'.join(blocks(section)) + '\n', doc['id']
        assert len(body) > 100, doc['id']
    print(f'Verified original, converted text and Markdown hashes for {len(ids)} documents')
    return manifest


WEB_SOURCES = {
    'osha-incident-investigation': ('Incident Investigation', 'https://www.osha.gov/incident-investigation', 'incident near miss root cause', 'guidance'),
    'osha-hazard-identification': ('Hazard Identification and Assessment', 'https://www.osha.gov/safety-management/hazard-Identification', 'proactive hazards incident investigation', 'guidance'),
    'osha-hazard-prevention': ('Hazard Prevention and Control', 'https://www.osha.gov/safety-management/hazard-prevention', 'corrective actions hierarchy of controls', 'guidance'),
    'osha-state-plans': ('State Plans', 'https://www.osha.gov/stateplans', 'jurisdiction state plan coverage', 'guidance'),
    'osha-fom-chapter-4': ('Field Operations Manual — Chapter 4: Violations', 'https://www.osha.gov/fom/chapter-4', 'enforcement applicability exposure knowledge general duty', 'guidance'),
}


def convert_web(snapshot: Path) -> None:
    """Convert retained browser text captures; reject incomplete page line coverage."""
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    if any(d['id'] in WEB_SOURCES for d in manifest['documents']):
        raise ValueError('Web documents already converted; use a new snapshot to refresh')
    pending = []
    for source_id, (title, url, topics, kind) in WEB_SOURCES.items():
        captures = sorted((snapshot / 'raw').glob(source_id + '*.web.txt'))
        if not captures:
            raise ValueError(f'Missing browser capture: {source_id}')
        lines = {}
        totals = set()
        raw_parts = []
        for path in captures:
            raw = path.read_bytes()
            raw_parts.append({'path': str(path.relative_to(snapshot)), 'sha256': digest(raw)})
            capture = raw.decode()
            total = re.search(r'Total lines: (\d+)', capture)
            if total is None:
                raise ValueError(f'Missing page length: {path}')
            totals.add(int(total[1]))
            for match in re.finditer(r'(?:^|\s)L(\d+): ?(.*?)(?=(?:^|\s)L\d+:|\Z)', capture, re.S):
                index, value = int(match[1]), match[2].rstrip()
                # Citation markers are browser formatting; keep their visible labels.
                value = re.sub('cite([^]*)', lambda m: m[1].split('†')[1] if '†' in m[1] else '', value)
                if index in lines and lines[index] != value:
                    raise ValueError(f'Page changed during capture: {source_id}, line {index}')
                lines[index] = value
        if len(totals) != 1 or set(lines) != set(range(next(iter(totals)))):
            raise ValueError(f'Incomplete page capture: {source_id}')
        body = '\n'.join(lines[i] for i in range(26, len(lines))) + '\n'
        version = 'sha256:' + digest(json.dumps(raw_parts, sort_keys=True).encode())
        md_name = f'markdown/{source_id}.md'
        md = f'# {title}\n\n- url: {url}\n- source_type: {kind}\n- version: {version}\n'
        md += '\nOSHA web text captured in full through the browser retrieval service. '
        md += 'Original HTML was unavailable. Navigation lines 0–25 were removed; visible link labels retained. '
        md += 'Linked documents, images and external references are not included. Enforcement guidance does not create a new standard.\n\n---\n\n' + body
        pending.append((md_name, md, {'id': source_id, 'title': title, 'topics': topics,
            'section': title, 'source_type': kind, 'jurisdiction': 'US-federal-OSHA',
            'url': url, 'download_url': url, 'snapshot_date': 'web capture at retrieval',
            'effective_date': None, 'version': version,
            'retrieved_at': datetime.fromtimestamp(min((snapshot / part['path']).stat().st_mtime for part in raw_parts), timezone.utc).isoformat(),
            'raw_path': raw_parts[0]['path'], 'raw_sha256': raw_parts[0]['sha256'], 'raw_parts': raw_parts,
            'capture_line_count': len(lines), 'capture_complete': True,
            'markdown_path': md_name, 'text_sha256': digest(body.encode()),
            'markdown_sha256': digest(md.encode()), 'conversion': 'complete browser text capture; link labels retained'}))
    for md_name, md, doc in pending:
        with (snapshot / md_name).open('x') as handle:
            handle.write(md)
        manifest['documents'].append(doc)
    (snapshot / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Converted {len(pending)} complete OSHA web captures')


def export(snapshot: Path, site_id: str, output: Path, selected: list[str], max_chars: int = 3000) -> None:
    """Export existing SourceExcerpt API bodies; no production database writes."""
    manifest = check(snapshot)
    docs = [d for d in manifest['documents'] if d['id'] in selected]
    if set(selected) != {d['id'] for d in docs}:
        raise ValueError('Every --document must identify an existing source')
    rows, omitted = [], []
    if max_chars < 100:
        raise ValueError('max_chars must be at least 100')
    report = output.with_suffix(output.suffix + '.report.json')
    if output.exists() or report.exists():
        raise FileExistsError('Export and report paths must both be new')
    for doc in docs:
        body = (snapshot / doc['markdown_path']).read_text().split('\n---\n\n', 1)[1]
        # Section blocks keep the complete wording; oversized tables stay intact.
        # Block identifiers are retrieval locators, not invented legal paragraphs.
        # Browser lines retain complete rendered paragraphs/list items. XML tables
        # remain whole: an oversized table is a gap, never a severed requirement.
        units = [line for line in body.splitlines() if line.strip()]
        if doc['raw_path'].endswith('.xml'):
            units = blocks(ET.fromstring((snapshot / doc['raw_path']).read_bytes()))
        for index, unit in enumerate(units, 1):
            if unit.startswith('## '):
                continue
            if len(unit) > max_chars:
                omitted.append({'source_id': doc['id'], 'source_block': index,
                                'characters': len(unit), 'reason': 'requires bounded extraction with full context'})
                continue
            locator = f'{doc["section"]} / source block {index:04d}'
            record = {'id': f'{doc["id"]}-{doc["raw_sha256"][:16]}-{digest(site_id.encode())[:12]}-b{index:04d}',
                'source_id': doc['id'], 'url': doc['url'],
                'title': doc['title'] + ' | ' + doc['topics'], 'section': locator,
                'text': unit, 'retrieved_at': doc['retrieved_at'], 'effective_date': None,
                'source_type': doc['source_type'], 'version': doc['version'],
                'jurisdiction': doc['jurisdiction'], 'site_id': site_id}
            from safety_app.domain.models import SourceExcerpt
            rows.append(SourceExcerpt.model_validate(record).model_dump(mode='json'))
    with output.open('x') as handle:
        handle.write(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
    report.write_text(json.dumps({'max_chars': max_chars, 'exported': len(rows), 'omitted': omitted}, indent=2) + '\n')
    print(f'Exported {len(rows)} API-ready excerpts to {output}; {len(omitted)} oversized blocks in {report}. Not imported into the app')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    build_parser = sub.add_parser('build')
    build_parser.add_argument('--xml', type=Path, required=True)
    build_parser.add_argument('--act', type=Path, required=True)
    build_parser.add_argument('--output', type=Path, required=True)
    check_parser = sub.add_parser('check')
    check_parser.add_argument('snapshot', type=Path)
    web_parser = sub.add_parser('convert-web')
    web_parser.add_argument('snapshot', type=Path)
    export_parser = sub.add_parser('export')
    export_parser.add_argument('snapshot', type=Path)
    export_parser.add_argument('--site-id', required=True)
    export_parser.add_argument('--document', action='append', required=True)
    export_parser.add_argument('--output', type=Path, required=True)
    export_parser.add_argument('--max-chars', type=int, default=3000)
    args = parser.parse_args()
    if args.command == 'build':
        build(args.xml, args.act, args.output)
    elif args.command == 'check':
        check(args.snapshot)
    elif args.command == 'convert-web':
        convert_web(args.snapshot)
    else:
        export(args.snapshot, args.site_id, args.output, args.document, args.max_chars)


if __name__ == '__main__':
    main()
