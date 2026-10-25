#!/usr/bin/env python3
"""Validate/export or add a federal library to one site's curated sources. No paid calls.

Original files and records are preserved. Inferred ambiguous clause labels are left null;
context/effective-date verification remains false/unknown until supplied by a reviewer.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_safety_library import check,blocks
from safety_app.domain.models import SourceExcerpt
from safety_app.modules.safety_review.catalog import owner_for

def clause_labels(units,number):
    stack=[];labels=[];appendix=False
    for text in units:
        if re.match(r'^Appendix\s+[A-Z]',text):appendix=True
        # Appendices have their own numbering. Do not attach it to main-section clauses.
        if appendix:labels.append(None);continue
        match=re.match(r'^\(([^)]+)\)',text)
        if not match:labels.append(None);continue
        label=match[1]
        if re.fullmatch('[a-z]',label) and label not in ('i','v','x'):
            level=0
        elif label.isdigit() and len(stack)<=3:level=1
        elif re.fullmatch('[ivx]+',label) and len(stack)>=2:
            # i/v/x can be top-level letters or nested roman numerals. Never guess.
            labels.append(None);continue
        elif re.fullmatch('[A-Z]',label) and len(stack)>=3:level=3
        else:labels.append(None);continue
        if level>len(stack):labels.append(None);continue
        stack=stack[:level]+[label]
        labels.append(number+''.join('('+part+')' for part in stack))
    # Repeated candidate labels indicate ambiguous nesting; remove all collisions.
    repeated={label for label in labels if label and labels.count(label)>1}
    return [None if label in repeated else label for label in labels]

def records(snapshot,site_id):
    manifest=check(snapshot);result=[]
    for doc in manifest['documents']:
        if doc['raw_path'].endswith('.xml'):
            units=blocks(ET.fromstring((snapshot/doc['raw_path']).read_bytes()))
            clauses=clause_labels(units,doc['section'])
        else:
            body=(snapshot/doc['markdown_path']).read_text().split('\n---\n\n',1)[1]
            units=[line for line in body.splitlines() if line.strip()];clauses=[None]*len(units)
            if doc['id']=='usc-29-654':clauses=['29 USC 654(a)','29 USC 654(a)(1)','29 USC 654(a)(2)','29 USC 654(b)']
        ids=[f'indexed-{doc["id"]}-{doc["raw_sha256"][:16]}-{hashlib.sha256(site_id.encode()).hexdigest()[:12]}-b{i:04d}' for i in range(1,len(units)+1)]
        by_clause={clause:ids[i] for i,clause in enumerate(clauses) if clause}
        for i,unit in enumerate(units):
            if unit.startswith('## '):continue
            clause=clauses[i]
            ancestors=[]
            if clause:
                parent=clause
                while '(' in parent:
                    parent=parent[:parent.rfind('(')]
                    if parent in by_clause:ancestors.append(by_clause[parent])
            related=[ids[j] for j in (i-1,i+1) if 0<=j<len(units) and not units[j].startswith('## ')]
            scope_id=by_clause.get(doc['section']+'(a)')
            if scope_id and scope_id!=ids[i] and scope_id not in ancestors:related.append(scope_id)
            data={'id':ids[i],'source_id':doc['id'],'title':doc['title'],'section':f'{doc["section"]} / source block {i+1:04d}',
                'clause':clause,'text':unit,'url':doc['url'],'retrieved_at':doc['retrieved_at'],
                'effective_date':None,'source_type':doc['source_type'],'version':doc['version'],
                'jurisdiction':doc['jurisdiction'],'site_id':site_id,'topics':doc['topics'].split(),
                'parent_ids':list(dict.fromkeys(ancestors)),'related_ids':list(dict.fromkeys(related)),
                'context_complete':False}
            owner=owner_for(data)
            data['specialist']=None if owner=='unmapped' else owner
            result.append(SourceExcerpt.model_validate(data))
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot',type=Path);parser.add_argument('--site-id',required=True)
    parser.add_argument('--export',type=Path);parser.add_argument('--apply',action='store_true')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);args=parser.parse_args()
    rows=records(args.snapshot,args.site_id)
    print(f'Prepared {len(rows)} complete blocks; {sum(r.clause is not None for r in rows)} conservatively identified clauses. Effective dates/context remain unverified.')
    if args.export:
        with args.export.open('x') as handle:handle.write(''.join(row.model_dump_json()+'\n' for row in rows))
    if args.apply:
        from safety_app.infrastructure.repositories import Database
        from safety_app.infrastructure.audit import Audit
        from safety_app.modules.safety_review.sources import Sources
        db=Database(args.root/'data/app.sqlite');audit=Audit(args.root/'data/audit')
        Sources(db)
        sites={c['site_id'] for c in db.list('camera')}
        sites.update(json.loads(p.read_text())['site_id'] for p in (args.root/'configs/cameras').glob('*.yaml'))
        if args.site_id not in sites:raise ValueError('Unknown configured site')
        # Preflight every immutable ID before the single transaction; no partial overwrite.
        by_id={row.id:row for row in rows}
        with db.connect() as conn:
            old_rows=conn.execute("SELECT id,body FROM objects WHERE kind='source' AND id IN ("+','.join('?' for _ in by_id)+')',list(by_id)).fetchall()
        for old in old_rows:
            if json.loads(old['body'])!=by_id[old['id']].model_dump(mode='json'):raise ValueError('Existing immutable source differs: '+old['id'])
        audit.write('source_library_import_started',provenance={'site_id':args.site_id,'snapshot':str(args.snapshot),'count':len(rows)})
        db.save_many([('source',row) for row in rows])
        audit.write('source_library_imported',provenance={'site_id':args.site_id,'snapshot':str(args.snapshot),'count':len(rows)})
        print('Imported additive immutable sources and updated FTS index. No original record replaced.')
    elif not args.export:print('Preview only. Use --export for review or --apply for additive import.')

if __name__=='__main__':main()
