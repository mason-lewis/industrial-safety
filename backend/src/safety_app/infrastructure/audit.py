"""Append-only SQLite audit. JSON is a bounded, atomic inspection snapshot of recent events.

audit.sqlite is the complete record. audit.json holds at most SNAPSHOT_ROWS recent events;
large audits refresh it at most every SNAPSHOT_SECONDS, never copying the full history.
"""
import json
import logging
import os
import re
import tempfile
import time
from safety_app.domain.models import AuditEvent
from safety_app.infrastructure.repositories import Database
SNAPSHOT_ROWS=5000
SNAPSHOT_SECONDS=5
SENSITIVE=re.compile(r'(authorization|api.?key|password|secret|access.?token|signed.?url|reasoning|chain_of_thought)',re.I)
def safe_usage_metadata(key,value):
    if key=='reasoning_tokens':return value is None or (type(value) is int and value>=0)
    if key=='openai_reasoning_effort':return isinstance(value,str) and value in ('none','low','medium','high','xhigh','max')
    return False
def redact(value):
    if isinstance(value,dict): return {k:'[REDACTED]' if SENSITIVE.search(k) and not safe_usage_metadata(k,v) else redact(v) for k,v in value.items()}
    if isinstance(value,list): return [redact(v) for v in value]
    if isinstance(value,str):
        value=re.sub(r'(?i)(Bearer\s+)[^\s]+',r'\1[REDACTED]',value)
        value=re.sub(r'sk-[A-Za-z0-9_-]+','[REDACTED]',value)
        value=re.sub(r'(?i)(https?|rtsp)://[^/\s@]+@',r'\1://[REDACTED]@',value)
        value=re.sub(r'(?i)(https?://[^\s?]+)\?[^\s]+',r'\1?[REDACTED]',value)
        value=re.sub(r'(?i)((?:api[_-]?key|password|secret|token)\s*[=:]\s*)[^\s,;]+',r'\1[REDACTED]',value)
        return value[:24000]
    return value
class Audit:
    def __init__(self,folder):
        self.folder=folder;folder.mkdir(parents=True,exist_ok=True)
        self.db=Database(folder/'audit.sqlite');self.degraded=False;self.export_error=None;self.exported_at=0
        with self.db.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS audit(sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,body TEXT)')
            db.execute("CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'append only'); END")
            db.execute("CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'append only'); END")
        self.degraded=(folder/'.audit-gap').exists()
        self.export(force=True)
    def write(self,event_type,status='ok',**fields):
        if self.degraded and event_type!='audit_gap':
            self.write('audit_gap',status='degraded',provenance={'note':'Audit persistence failed. Some operations may have no audit record.'})
            self.degraded=False;(self.folder/'.audit-gap').unlink(missing_ok=True)
        fields.setdefault('trace_id',fields.get('parent_run_id') or fields.get('run_id') or fields.get('job_id') or __import__('uuid').uuid4().hex)
        event=AuditEvent(event_type=event_type,status=status,**fields)
        event.audit_event_id=event.id
        body=redact(event.model_dump(mode='json'))
        try:
            with self.db.connect() as db: db.execute('INSERT INTO audit(id,body) VALUES(?,?)',(event.id,json.dumps(body)))
        except Exception:
            self.degraded=True
            try:(self.folder/'.audit-gap').write_text('Audit write failed')
            except OSError:pass
            logging.exception('Audit write failed; stop the audited operation');raise
        self.export();return event.id
    def rows(self,limit=200,offset=0,**filters):
        allowed={'run_id','parent_run_id','agent_name','job_id','event_id','status','event_type','since','until'}
        if set(filters)-allowed: raise ValueError('Unknown audit filter')
        query='SELECT sequence,body FROM audit WHERE 1=1';args=[]
        for k,v in filters.items():
            if v is None or v=='':continue
            if k in ('since','until'):
                query+=f" AND json_extract(body,'$.timestamp_utc') {'>=' if k=='since' else '<='} ?"
            else: query+=f" AND json_extract(body,'$.{k}')=?"
            args.append(v)
        query+=' ORDER BY sequence DESC LIMIT ? OFFSET ?';args.extend([min(limit,1000),offset])
        with self.db.connect() as db: rows=db.execute(query,args).fetchall()
        with self.db.connect() as db:annotations=db.execute("SELECT body FROM audit WHERE json_extract(body,'$.event_type')='test_run_annotation'").fetchall()
        fixtures={id for a in annotations for id in json.loads(a['body'])['provenance'].get('audit_event_ids',[])}
        result=[json.loads(r['body'])|{'sequence':r['sequence']} for r in rows]
        for row in result:
            if row['id'] in fixtures:row['provenance']|={'data_kind':'fictional_fixture_execution','notice':'Historical verification record. No provider call occurred.'}
        return result
    def export(self,force=False):
        # Serialize exporters with a cross-process SQLite write lock. A stale export cannot win.
        try:
            with self.db.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                total=db.execute('SELECT count(*) FROM audit').fetchone()[0]
                # Small audits stay exact on every write; large ones are throttled.
                if not force and total>1000 and time.monotonic()-self.exported_at<SNAPSHOT_SECONDS:return
                rows=db.execute('SELECT sequence,body FROM (SELECT sequence,body FROM audit ORDER BY sequence DESC LIMIT ?) ORDER BY sequence',(SNAPSHOT_ROWS,)).fetchall()
                body=[json.loads(r['body'])|{'sequence':r['sequence']} for r in rows]
                fd,name=tempfile.mkstemp(prefix='.audit-',suffix='.json',dir=self.folder)
                try:
                    with os.fdopen(fd,'w') as stream: json.dump(body,stream,indent=2);stream.flush();os.fsync(stream.fileno())
                    os.replace(name,self.folder/'audit.json')
                finally:
                    if os.path.exists(name):os.unlink(name)
            self.exported_at=time.monotonic();self.export_error=None
        except Exception as exc:
            self.export_error=type(exc).__name__;logging.exception('Audit snapshot export failed; committed rows are retained')
