#!/usr/bin/env python3
"""Add a versioned OSHA Chapter XVII index from the preserved full Title 29 XML."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
from build_safety_library import blocks
from import_safety_library import clause_labels


def build(root):
    raw=root/'data/resources/safety/snapshots/federal-2026-09-30/raw/ECFR-title29.xml'
    digest=hashlib.sha256(raw.read_bytes()).hexdigest()
    destination=root/'data/resources/safety/indexes'/('title29-osha-v2-'+digest[:16]+'.sqlite')
    if destination.exists():return destination
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_suffix('.building')
    if temporary.exists():raise RuntimeError('Index build already pending')
    count=0;sections=0
    with sqlite3.connect(temporary) as db:
        db.execute('CREATE TABLE documents(id TEXT PRIMARY KEY,body TEXT)')
        tree=ET.parse(raw)
        chapters=[x for x in tree.iter() if x.get('TYPE')=='CHAPTER' and x.get('N')=='XVII']
        for section in (section for chapter in chapters for section in chapter.iter()):
            if section.get('TYPE')!='SECTION':continue
            number=section.get('N');title=' '.join(section.findtext('HEAD','').split());units=blocks(section);labels=clause_labels(units,number)
            sections+=1
            ids=[f'osha-v2-{digest[:12]}-{number}-{i}' for i in range(len(units))]
            for i,text in enumerate(units):
                if text.startswith('## '):continue
                row=dict(id=ids[i],title=title,text=text,kind='requirement',version='title29-osha-v2:'+digest,url=f'https://www.ecfr.gov/current/title-29/section-{number}',retrieved_at='2026-09-30',clause=labels[i],context_complete=False,jurisdiction='US-federal-OSHA',effective_date=None,provenance={'snapshot_sha256':digest,'section':number,'original':'federal-2026-09-30/raw/ECFR-title29.xml','neighbors':[ids[j] for j in (i-1,i+1) if 0<=j<len(ids)],'gaps':['Historical applicability, effective date and complete context have not been verified.']})
                db.execute('INSERT INTO documents VALUES(?,?)',(row['id'],json.dumps(row)));count+=1
        db.execute('CREATE TABLE manifest(body TEXT)')
        db.execute('INSERT INTO manifest VALUES(?)',(json.dumps({'version':'title29-osha-v2','sha256':digest,'sections':sections,'blocks':count}),))
    temporary.rename(destination)
    return destination

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);args=parser.parse_args()
    path=build(args.root)
    with sqlite3.connect(path) as db:print(json.loads(db.execute('SELECT body FROM manifest').fetchone()[0]))
    print(path)
