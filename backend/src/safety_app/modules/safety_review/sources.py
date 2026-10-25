"""Indexed local discovery and bounded exact evidence packets. No model/network calls."""
import re
from collections import OrderedDict
from datetime import date
from pydantic import Field
from safety_app.domain.base import StrictModel
from safety_app.domain.models import SourceExcerpt
from .catalog import owner_for

_STOP = frozenset('a an and are as at be by for from in is it of on or that the this to was were with which what why how assess review check event'.split())
_ALIASES = {'forklift':'powered industrial truck','loto':'lockout tagout','egress':'exit','sds':'safety data sheet','beneath':'under','raised':'elevated','pedestrian':'person'}

class SourceCandidate(StrictModel):
    id:str
    title:str
    section:str
    clause:str|None=None
    source_type:str
    version:str
    specialist:str
    preview:str
    effective_date:str|None=None
    effective_until:str|None=None

class SourceDiscovery(StrictModel):
    candidates:list[SourceCandidate]=Field(default_factory=list)
    gaps:list[str]=Field(default_factory=list)

class EvidencePacket(StrictModel):
    excerpts:list['EvidenceExcerpt']=Field(default_factory=list)
    gaps:list[str]=Field(default_factory=list)
    complete:bool=False

class EvidenceExcerpt(StrictModel):
    id:str
    title:str
    section:str
    clause:str|None=None
    text:str
    source_type:str
    version:str
    jurisdiction:str

def _terms(value):
    terms=list(dict.fromkeys(t for t in re.findall(r'[a-z0-9]+(?:\.[0-9]+)*',value.lower()) if t not in _STOP))
    for alias,expansion in _ALIASES.items():
        if alias in terms:terms.extend(t for t in expansion.split() if t not in terms)
    return terms[:24]

def _date(value):return date.fromisoformat(value[:10]).isoformat() if value else None

def _jurisdiction(value):
    # Normalize only equivalent federal OSHA labels, never a state jurisdiction.
    if re.sub(r'[^a-z0-9]','',value.lower()) in ('usfederalosha','federalosha','unitedstatesfederalosha'):
        return 'US-federal-OSHA'
    return value

class Sources:
    def __init__(self,db):
        self.db=db;self.cache=OrderedDict()
        if hasattr(db,'connect'):self._index()
    def _index(self):
        # Derived tables/triggers only. Original source objects stay unchanged.
        with self.db.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS source_index_state(id INTEGER PRIMARY KEY,revision INTEGER)')
            conn.execute('INSERT OR IGNORE INTO source_index_state VALUES(1,0)')
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS safety_source_fts USING fts5(id UNINDEXED,title,section,text,topics,tokenize='porter unicode61')")
            conn.execute('CREATE TABLE IF NOT EXISTS safety_source_meta(id TEXT PRIMARY KEY,site_id TEXT,jurisdiction TEXT,owner TEXT,kind TEXT,clause TEXT,effective_date TEXT,effective_until TEXT)')
            conn.execute('CREATE INDEX IF NOT EXISTS safety_source_scope ON safety_source_meta(site_id,jurisdiction,owner)')
            conn.execute('CREATE INDEX IF NOT EXISTS safety_source_clause ON safety_source_meta(site_id,jurisdiction,clause)')
            fresh=conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='safety_source_insert'").fetchone() is None
            for action in ('INSERT','UPDATE'):
                conn.execute(f'''CREATE TRIGGER IF NOT EXISTS safety_source_{action.lower()} AFTER {action} ON objects WHEN new.kind='source' BEGIN
                    DELETE FROM safety_source_fts WHERE id=new.id;
                    DELETE FROM safety_source_meta WHERE id=new.id;
                    INSERT INTO safety_source_fts VALUES(new.id,json_extract(new.body,'$.title'),json_extract(new.body,'$.section'),json_extract(new.body,'$.text'),COALESCE(json_extract(new.body,'$.topics'),''));
                    INSERT INTO safety_source_meta VALUES(new.id,json_extract(new.body,'$.site_id'),json_extract(new.body,'$.jurisdiction'),source_owner(new.body),json_extract(new.body,'$.source_type'),json_extract(new.body,'$.clause'),json_extract(new.body,'$.effective_date'),json_extract(new.body,'$.effective_until'));
                    UPDATE source_index_state SET revision=revision+1 WHERE id=1;
                END''')
            conn.execute('''CREATE TRIGGER IF NOT EXISTS safety_source_delete AFTER DELETE ON objects WHEN old.kind='source' BEGIN
                DELETE FROM safety_source_fts WHERE id=old.id;
                DELETE FROM safety_source_meta WHERE id=old.id;
                UPDATE source_index_state SET revision=revision+1 WHERE id=1;
            END''')
            if fresh:
                conn.execute("INSERT INTO safety_source_fts SELECT id,json_extract(body,'$.title'),json_extract(body,'$.section'),json_extract(body,'$.text'),COALESCE(json_extract(body,'$.topics'),'') FROM objects WHERE kind='source'")
                conn.execute("INSERT INTO safety_source_meta SELECT id,json_extract(body,'$.site_id'),json_extract(body,'$.jurisdiction'),source_owner(body),json_extract(body,'$.source_type'),json_extract(body,'$.clause'),json_extract(body,'$.effective_date'),json_extract(body,'$.effective_until') FROM objects WHERE kind='source'")
                conn.execute('UPDATE source_index_state SET revision=revision+1 WHERE id=1')
    def _cached(self,key,make):
        with self.db.connect() as conn:revision=conn.execute('SELECT revision FROM source_index_state WHERE id=1').fetchone()[0]
        key=(revision,)+key
        if key in self.cache:
            self.cache.move_to_end(key);return self.cache[key].model_copy(deep=True)
        result=make();self.cache[key]=result.model_copy(deep=True)
        while len(self.cache)>64:self.cache.popitem(last=False)
        return result
    def revision(self):
        with self.db.connect() as conn:return conn.execute('SELECT revision FROM source_index_state WHERE id=1').fetchone()[0]
    def discover(self,query,jurisdiction,topic,limit,site_id,source_ids=None,specialist=None,event_date=None,source_type=None):
        jurisdiction=_jurisdiction(jurisdiction)
        if not 1<=limit<=5:raise ValueError('Limit must be 1..5')
        day=_date(event_date);terms=_terms(query+' '+topic)
        if not terms:return SourceDiscovery(gaps=['No specific search terms supplied'])
        def search():
            filters=['m.site_id=?','m.jurisdiction IN (?,?)'];values=[site_id,jurisdiction,'site']
            if source_ids:
                filters.append('m.id IN ('+','.join('?' for _ in source_ids)+')');values.extend(sorted(source_ids))
            if specialist:filters.append("m.owner IN (?,'unmapped')");values.append(specialist)
            if source_type:filters.append('m.kind=?');values.append(source_type)
            if day:
                filters.extend(['(m.effective_date IS NULL OR m.effective_date<=?)','(m.effective_until IS NULL OR m.effective_until>?)']);values.extend([day,day])
            sql="SELECT o.body,NULL AS preview FROM safety_source_meta m JOIN objects o ON o.kind='source' AND o.id=m.id WHERE "+' AND '.join(filters)+' AND m.clause=? ORDER BY m.id LIMIT ?'
            with self.db.connect() as conn:
                rows=conn.execute(sql,values+[query.strip().removeprefix('§').strip(),limit]).fetchall()
                if not rows:
                    match=' OR '.join('"'+term+'"' for term in terms)
                    sql="SELECT o.body,snippet(safety_source_fts,3,'[',']','…',24) AS preview FROM safety_source_fts JOIN safety_source_meta m ON m.id=safety_source_fts.id JOIN objects o ON o.kind='source' AND o.id=m.id WHERE safety_source_fts MATCH ? AND "+' AND '.join(filters)+' ORDER BY bm25(safety_source_fts,0,2,8,1,1),m.id LIMIT ?'
                    rows=conn.execute(sql,[match]+values+[limit]).fetchall()
            candidates=[]
            for row in rows:
                s=SourceExcerpt.model_validate_json(row['body'])
                candidates.append(SourceCandidate(id=s.id,title=s.title,section=s.section,clause=s.clause,source_type=s.source_type,version=s.version,specialist=owner_for(s.model_dump()),preview=re.sub(r'\s+',' ',row['preview'] or s.text)[:160],effective_date=s.effective_date,effective_until=s.effective_until))
            return SourceDiscovery(candidates=candidates,gaps=[] if candidates else ['No matching assigned source; broaden explicitly or request documents'])
        return self._cached(('search',query,jurisdiction,topic,limit,site_id,tuple(sorted(source_ids or [])),specialist,day,source_type),search)
    def search(self,query,jurisdiction,topic,limit,site_id,source_ids=None):
        jurisdiction=_jurisdiction(jurisdiction)
        # Compatibility interface; new agent tools discover previews then fetch exact text.
        if not hasattr(self.db,'connect'):
            if not 1<=limit<=5:raise ValueError('Limit must be 1..5')
            terms=set(_terms(query+' '+topic))
            rows=[SourceExcerpt.model_validate(s) for s in self.db.list('source') if s['site_id']==site_id and s['jurisdiction'] in (jurisdiction,'site') and (not source_ids or s['id'] in source_ids)]
            scores=[(3*len(terms&set(_terms(s.text)))+len(terms&set(_terms(s.title+' '+s.section))),s) for s in rows]
            return [s for score,s in sorted(scores,key=lambda item:(-item[0],item[1].id)) if score][:limit]
        result=self.discover(query,jurisdiction,topic,limit,site_id,source_ids)
        return [self.read(s.id,None,site_id) for s in result.candidates]
    def read(self,source_id,section,site_id):
        s=SourceExcerpt.model_validate(self.db.get('source',source_id))
        if s.site_id!=site_id:raise ValueError('Source outside site scope')
        if section is not None and section!=s.section:raise ValueError('Unknown source section')
        return s
    def fetch_packet(self,ids,site_id,source_ids=None,max_bytes=2400,event_date=None):
        if not 1<=len(ids)<=5:raise ValueError('Fetch 1..5 source IDs')
        if not 256<=max_bytes<=6000:raise ValueError('Evidence budget must be 256..6000 bytes')
        day=_date(event_date)
        def fetch():
            packet=EvidencePacket();queue=list(dict.fromkeys(ids));seen=set();selected=set(ids);labels={}
            while queue:
                id=queue.pop(0)
                if id in seen:continue
                seen.add(id)
                if len(seen)>20:packet.gaps.append('Context dependency limit reached');break
                if source_ids and id not in source_ids:packet.gaps.append(f'Required source {id} outside assigned scope');continue
                try:s=self.read(id,None,site_id)
                except KeyError:packet.gaps.append(f'Missing source {id}');continue
                labels[id]=s.clause or s.section
                if day and ((s.effective_date and _date(s.effective_date)>day) or (s.effective_until and _date(s.effective_until)<=day)):
                    packet.gaps.append(f'{id}: source outside event-date range');continue
                excerpt=EvidenceExcerpt.model_validate(s.model_dump(include=set(EvidenceExcerpt.model_fields)))
                proposal=packet.model_copy(update={'excerpts':packet.excerpts+[excerpt]})
                if id in selected:
                    proposal=proposal.model_copy(update={'gaps':['Source/context/date verification may be incomplete; see final packet gaps.']})
                if len(proposal.model_dump_json().encode())>max_bytes:
                    packet.gaps.append(f'{id}: complete excerpt exceeds remaining evidence budget');continue
                packet.excerpts.append(excerpt);queue.extend(s.parent_ids)
                # Related context is one hop, not a neighbor chain through the whole document.
                if id in selected:queue.extend(s.related_ids)
                if not s.context_complete:packet.gaps.append(f'{id}: scope/definitions/exceptions context not verified')
                if not s.effective_date:packet.gaps.append(f'{id}: event-date applicability not established')
            packet.gaps=list(dict.fromkeys(packet.gaps));packet.complete=bool(packet.excerpts) and not packet.gaps
            if len(packet.model_dump_json().encode())>max_bytes:
                # Repeated graph warnings must not evict requested exact clauses when
                # the clauses and concise, conservative gap descriptions can fit.
                compact=[];supporting={}
                for gap in packet.gaps:
                    matched=next((id for id in seen if gap.startswith(id+': ')),None)
                    if matched in selected:
                        reason=gap[len(matched)+2:]
                        # Keep budget-omission IDs exact for citation-permission tracking.
                        prefix=matched if 'evidence budget' in reason or 'packet budget' in reason else labels.get(matched,matched)
                        compact.append(f'{prefix}: {reason}')
                    elif matched:
                        reason=gap[len(matched)+2:];supporting[reason]=supporting.get(reason,0)+1
                    else:compact.append(gap)
                compact.extend(f'Supporting context: {count} source(s): {reason}' for reason,count in sorted(supporting.items()))
                packet.gaps=list(dict.fromkeys(compact))
            while len(packet.model_dump_json().encode())>max_bytes and packet.excerpts:
                removed=packet.excerpts.pop();packet.complete=False
                packet.gaps.append(f'{removed.id}: omitted to fit packet budget')
            if len(packet.model_dump_json().encode())>max_bytes:
                packet.gaps=['Incomplete packet: required source/context/date/budget gaps; inspect the source graph before claiming noncompliance']
                packet.complete=False
            return packet
        return self._cached(('packet',tuple(ids),site_id,tuple(sorted(source_ids or [])),max_bytes,day),fetch)
