"""Offline tests for source fidelity, bounded exports, and scoped retrieval."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from scripts.build_safety_library import blocks, check, convert_web, export
from safety_app.domain.models import SourceExcerpt
from safety_app.modules.safety_review.sources import Sources
from safety_app.infrastructure.repositories import Database

SNAPSHOT = Path(__file__).resolve().parents[1] / 'data/resources/safety/snapshots/federal-2026-09-30'

@pytest.mark.parametrize('jurisdiction',['US federal OSHA','us-FEDERAL-osha','Federal OSHA','United States Federal OSHA'])
def test_federal_alias_exact_clause_lookup_preserves_all_scope_filters(tmp_path,jurisdiction):
    db=Database(tmp_path/'sources.sqlite');service=Sources(db)
    for id,site,state,owner in [('allowed','local','US-federal-OSHA','vehicles'),('foreign','other','US-federal-OSHA','vehicles'),('state','local','US-CA-state-plan','vehicles'),('wrong-owner','local','US-federal-OSHA','energy')]:
        db.save('source',SourceExcerpt(id=id,source_id=id,title='Fictional truck clause',section='1910.178',clause='1910.178(m)(2)',text='Fictional exact requirement.',version='fixture',source_type='requirement',site_id=site,jurisdiction=state,specialist=owner),immutable=True)
    found=service.discover('1910.178(m)(2)',jurisdiction,'',5,'local',frozenset({'allowed','foreign','state','wrong-owner'}),'vehicles')
    assert [c.id for c in found.candidates]==['allowed']
    assert not service.discover('1910.178(m)(2)',jurisdiction,'',5,'local',frozenset({'foreign','state'}),'vehicles').candidates
    assert [c.id for c in service.discover('1910.178(m)(2)','US-CA-state-plan','',5,'local',frozenset({'state'}),'vehicles').candidates]==['state']


def test_official_snapshot_integrity_and_required_clause():
    manifest = check(SNAPSHOT)
    assert len(manifest['documents']) == 66
    truck = (SNAPSHOT / 'markdown/cfr-1910-178.md').read_text()
    assert 'No person shall be allowed to stand or pass under the elevated portion of any truck, whether loaded or empty.' in truck
    assert '(m) Truck operations.' in truck
    docs = {d['id']: d for d in manifest['documents']}
    assert docs['osha-fom-chapter-4']['capture_line_count'] == 528
    assert docs['osha-fom-chapter-4']['source_type'] == 'guidance'
    assert docs['cfr-1910-95']['graphics_require_original']
    assert docs['cfr-1910-134']['graphics_require_original']
    assert docs['cfr-1910-178']['effective_date'] is None


def test_conversion_keeps_exceptions_tables_footnotes_and_graphic_references():
    element = ET.fromstring('<DIV8><HEAD>Test</HEAD><P>(a) Must guard <I>unless</I> exempt.</P>'
        '<DIV><TABLE><TR><TD rowspan="2">Category</TD><TD>Limit</TD></TR>'
        '<TR><TD>4</TD></TR></TABLE><P>Footnote: exception applies.</P></DIV>'
        '<MATH><img src="/graphics/formula.gif"/></MATH></DIV8>')
    result = '\n\n'.join(blocks(element))
    assert '(a) Must guard unless exempt.' in result
    assert '```xml\n<TABLE>' in result
    assert 'rowspan="2"' in result
    assert 'Footnote: exception applies.' in result
    assert '/graphics/formula.gif' in result


def test_partial_web_capture_rejected_before_markdown_write(tmp_path):
    (tmp_path / 'raw').mkdir()
    (tmp_path / 'markdown').mkdir()
    (tmp_path / 'manifest.json').write_text(json.dumps({'documents': []}))
    (tmp_path / 'raw/osha-incident-investigation.web.txt').write_text('Total lines: 55\nL0: menu\nL54: tail\n')
    with pytest.raises(ValueError, match='Incomplete page capture'):
        convert_web(tmp_path)
    assert not list((tmp_path / 'markdown').iterdir())


def test_export_preserves_whole_blocks_reports_oversized_tables_and_validates(tmp_path):
    output = tmp_path / 'sources.ndjson'
    export(SNAPSHOT, 'fixture-site', output, ['cfr-1910-178'])
    records = [SourceExcerpt.model_validate_json(line) for line in output.read_text().splitlines()]
    assert records
    assert all(r.site_id == 'fixture-site' and len(r.text) <= 3000 for r in records)
    assert len({r.id for r in records}) == len(records)
    assert any('No person shall be allowed to stand or pass' in r.text for r in records)
    report = json.loads(output.with_suffix('.ndjson.report.json').read_text())
    assert report['omitted'] and report['omitted'][0]['source_id'] == 'cfr-1910-178'
    with pytest.raises(FileExistsError):
        export(SNAPSHOT, 'fixture-site', output, ['cfr-1910-178'])


class MemorySources:
    def __init__(self, rows):
        self.rows = rows

    def list(self, kind):
        assert kind == 'source'
        return self.rows


def source(id, text, site_id='fixture-site', jurisdiction='US-federal-OSHA'):
    return SourceExcerpt(id=id, source_id='truck', title='Forklift', section='1910.178',
        text=text, version='fixture', source_type='requirement',
        site_id=site_id, jurisdiction=jurisdiction).model_dump(mode='json')


def test_retrieval_ranks_specific_clause_over_generic_topic_and_substrings():
    service = Sources(MemorySources([
        source('a', 'Standards for forklift construction.'),
        source('b', 'Forklift training requirements.'),
        source('z', 'No person shall stand or pass under the elevated portion of a truck.'),
    ]))
    found = service.search('stand under elevated portion', 'US-federal-OSHA', 'forklift', 1, 'fixture-site')
    assert [r.id for r in found] == ['z']
    assert service.search('the and', 'US-federal-OSHA', '', 1, 'fixture-site') == []


def test_retrieval_applies_site_jurisdiction_document_scope_before_limit():
    service = Sources(MemorySources([
        source('a', 'stand under elevated portion'),
        source('b', 'stand under elevated portion', site_id='foreign'),
        source('c', 'stand under elevated portion', jurisdiction='US-CA-state-plan'),
        source('z', 'stand under elevated portion'),
    ]))
    found = service.search('stand under elevated portion', 'US-federal-OSHA', '', 1, 'fixture-site', frozenset({'z'}))
    assert [r.id for r in found] == ['z']
    assert service.search('stand', 'US-federal-OSHA', '', 1, 'fixture-site', frozenset({'missing'})) == []
    with pytest.raises(ValueError):
        service.search('stand', 'US-federal-OSHA', '', 6, 'fixture-site')
