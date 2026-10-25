"""Immutable, site-scoped inputs and bounded read-only retrieval for Chat Lab."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from safety_app.domain.chat_lab import LabCitation
from safety_app.infrastructure.exact_cache import fingerprint

PRODUCT_FILES=('README.md','docs/video-chat.md','docs/chat-lab.md','docs/safety-agent-workflows.md','docs/agent-output-contracts.md')
RECORD_KINDS=('review_run','video_safety_case','video_safety_assessment','safety_screening')


def freeze(rt,site_id,permissions,record_scope=None,extra_records=()):
    entries={}
    indexes=sorted((rt.root/'data/resources/safety/indexes').glob('title29-osha-v2-*.sqlite'))
    for path in indexes:
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
            for key,body in db.execute('SELECT id,body FROM documents'):entries[key]=json.loads(body)
    with rt.db.connect() as db:
        rows=db.execute("SELECT kind,id,body FROM objects WHERE json_extract(body,'$.site_id')=? AND kind IN ('source','rule','review_run','video_safety_case','video_safety_assessment','safety_screening') ORDER BY kind,id",(site_id,)).fetchall()
        decisions=db.execute("SELECT id,body FROM objects WHERE kind='decision' ORDER BY id").fetchall()
    if record_scope is not None:
        rows=[row for row in rows if row['kind'] not in RECORD_KINDS or row['id'] in record_scope]
    record_ids={row['id'] for row in rows if row['kind'] in RECORD_KINDS}
    for row in rows:
        item=json.loads(row['body']);kind=row['kind']
        if kind=='source':
            citation=LabCitation(id=item['id'],title=item['title'],text=item['text'],kind=item['source_type'],version=item['version'],url=item.get('url'),retrieved_at=item.get('retrieved_at'),clause=item.get('clause'),context_complete=item.get('context_complete',False),jurisdiction=item.get('jurisdiction'),effective_date=item.get('effective_date'),provenance={'source_id':item['source_id'],'neighbors':item.get('related_ids',[]),'parents':item.get('parent_ids',[])})
        else:
            # Preserve the whole structured record, including uncertainty and human status.
            citation=LabCitation(id=item['id'],title=f'{kind}: {item.get("title",item["id"])}',text=json.dumps(item,ensure_ascii=False),kind='site_rule' if kind=='rule' else 'record',version=fingerprint(item),provenance={'record_kind':kind,'original_id':item['id']})
        entries[citation.id]=citation.model_dump(mode='json')
    for row in decisions:
        item=json.loads(row['body'])
        # Decisions without their own site must be linked to an authorized target.
        if (record_scope is None and item.get('site_id')==site_id) or any(item.get(k) in record_ids for k in ('target_id','review_id','case_id','run_id')):
            entries[row['id']]=LabCitation(id=row['id'],title='Human decision',text=json.dumps(item),kind='record',version=fingerprint(item),provenance={'record_kind':'decision'}).model_dump(mode='json')
    for citation in extra_records:entries[citation.id]=citation.model_dump(mode='json')
    for value in list(entries.values()):
        if value['kind']!='record':continue
        linked=[entry['id'] for entry in entries.values() if entry['kind']=='record' and entry['provenance'].get('record_kind')=='decision' and json.loads(entry['text']).get('target_id')==value['id']]
        if linked:value['provenance']['human_decision_ids']=linked
    for filename in PRODUCT_FILES:
        path=rt.root/filename
        if not path.exists():continue
        text=path.read_text();digest=hashlib.sha256(text.encode()).hexdigest()
        for i,section in enumerate(re.split(r'(?m)(?=^## )',text)):
            # Whole paragraphs only; oversized individual paragraphs remain a visible gap.
            for j,paragraph in enumerate(section.split('\n\n')):
                if not paragraph.strip():continue
                key=f'product:{filename}:{i}:{j}'
                entries[key]=LabCitation(id=key,title=filename,text=paragraph,kind='product',version=digest,context_complete=True,provenance={'file':filename}).model_dump(mode='json')
    digest=fingerprint({'site':site_id,'permissions':permissions,'entries':entries})
    folder=rt.root/'data/chat-lab/snapshots';folder.mkdir(parents=True,exist_ok=True);path=folder/(digest+'.sqlite')
    if not path.exists():
        temporary=folder/(digest+'.'+__import__('uuid').uuid4().hex+'.building')
        with sqlite3.connect(temporary) as db:
            db.execute('CREATE TABLE documents(id TEXT PRIMARY KEY,body TEXT)')
            db.executemany('INSERT INTO documents VALUES(?,?)',((key,json.dumps(value)) for key,value in entries.items()))
            db.execute('CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED,title,text,tokenize="porter unicode61")')
            db.executemany('INSERT INTO search VALUES(?,?,?)',((key,value['title'],value['text']) for key,value in entries.items()))
        temporary.replace(path)
    return {'id':digest,'site_id':site_id,'permissions':permissions,'documents':len(entries),'expanded_osha':bool(indexes),'index_versions':[p.stem for p in indexes]}


class LabKnowledge:
    def __init__(self,root,snapshot,site_id):
        if snapshot['site_id']!=site_id:raise ValueError('Snapshot outside site')
        if not re.fullmatch('[a-f0-9]{64}',snapshot['id']):raise ValueError('Invalid snapshot')
        self.path=Path(root)/'data/chat-lab/snapshots'/(snapshot['id']+'.sqlite')
        self.seen={}
    def connection(self):return sqlite3.connect(f'file:{self.path}?mode=ro',uri=True)
    def search(self,query,kind='all',limit=5):
        if kind not in ('all','references','product','records','site_rules'):raise ValueError('Unknown search collection')
        if not 1<=limit<=5 or len(query)>500:raise ValueError('Search bounds exceeded')
        from safety_app.modules.safety_review.sources import _terms
        words=_terms(query)[:24]
        if not words:return {'matches':[],'gaps':['No searchable terms']}
        expression=' OR '.join('"'+w.replace('"','')+'"' for w in words)
        kinds={'references':['requirement','guidance','procedure','site_rule'],'product':['product'],'records':['record'],'site_rules':['site_rule']}.get(kind)
        with self.connection() as db:
            sql="SELECT d.body FROM search s JOIN documents d ON d.id=s.id WHERE search MATCH ?"
            args=[expression]
            if kinds:sql+=" AND json_extract(d.body,'$.kind') IN ("+','.join('?' for _ in kinds)+')';args+=kinds
            rows=db.execute(sql+' ORDER BY bm25(search) LIMIT ?',args+[limit]).fetchall()
        return {'matches':[{'id':x['id'],'title':x['title'],'kind':x['kind'],'clause':x.get('clause'),'preview':x['text'][:180]} for x in (json.loads(r[0]) for r in rows)],'gaps':[] if rows else ['No match in frozen authorized inputs']}
    def read_for_task(self,ids,collection):
        """Return at most five whole authorized sources; invalid requests produce gaps."""
        allowed={'product':{'product'},'records':{'record'},'references':{'requirement','guidance','procedure','site_rule'}}[collection]
        requested=list(dict.fromkeys(ids));selected=[];gaps=[]
        if not requested:return {'sources':[],'gaps':['No source IDs supplied.']}
        if len(requested)>5:gaps.append('Only the first five requested source IDs were considered; additional IDs were not read.')
        with self.connection() as db:
            for id in requested[:5]:
                row=db.execute('SELECT body FROM documents WHERE id=?',(id,)).fetchone()
                if not row or json.loads(row[0])['kind'] not in allowed:
                    gaps.append('A requested source is unavailable in the assigned authorized collection.');continue
                selected.append(id)
        result=self.read(selected) if selected else {'sources':[],'gaps':[]}
        return {'sources':result['sources'],'gaps':list(dict.fromkeys([*gaps,*result['gaps']]))}
    def read(self,ids):
        if not 1<=len(ids)<=5:raise ValueError('Read one to five sources')
        results=[];gaps=[]
        with self.connection() as db:
            for id in dict.fromkeys(ids):
                row=db.execute('SELECT body FROM documents WHERE id=?',(id,)).fetchone()
                if not row:raise ValueError('Source outside frozen permissions')
                value=LabCitation.model_validate_json(row[0])
                if len(value.text.encode())>8000:gaps.append(f'{id}: whole source exceeds 8,000-byte bound; not truncated or available as evidence');continue
                results.append(value.model_dump(mode='json'));self.seen[id]=value
                for decision_id in value.provenance.get('human_decision_ids',[])[:10]:
                    linked=db.execute('SELECT body FROM documents WHERE id=?',(decision_id,)).fetchone()
                    if linked:
                        decision=LabCitation.model_validate_json(linked[0]);results.append(decision.model_dump(mode='json'));self.seen[decision.id]=decision
                if len(value.provenance.get('human_decision_ids',[]))>10:gaps.append('Additional human decisions exceed the bounded packet; record interpretation remains incomplete')
        return {'sources':results,'gaps':gaps}
