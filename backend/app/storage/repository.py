import json
import sqlite3
from pathlib import Path
from threading import RLock

class Repository:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db = sqlite3.connect(path,check_same_thread=False)
        self.lock = RLock()
        self.db.execute('CREATE TABLE IF NOT EXISTS trial_hidden_records(kind TEXT,id TEXT,reset_id TEXT,PRIMARY KEY(kind,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS candidate_snapshots (id TEXT, timestamp_ms INTEGER, body TEXT)')
        self.db.execute('CREATE INDEX IF NOT EXISTS candidate_snapshot_lookup ON candidate_snapshots(id,timestamp_ms)')
        self.db.commit()
    def save(self,kind,record):
        body = record.model_dump(mode='json') if hasattr(record,'model_dump') else record
        key = body.get('id') or body.get('candidate_id') or body.get('incident_id')
        with self.lock:
            if kind=='candidate':
                prior=self.db.execute('SELECT body FROM candidate_snapshots WHERE id=? ORDER BY timestamp_ms DESC,rowid DESC LIMIT 1',(key,)).fetchone()
                lifecycle=lambda b:{k:v for k,v in b.items() if k not in ('ai_status','processing_time')}
                if prior is None or lifecycle(json.loads(prior[0]))!=lifecycle(body):
                    stamp=body.get('updated_ms') if body.get('updated_ms') is not None else body['timestamp_ms']
                    self.db.execute('INSERT INTO candidate_snapshots VALUES (?,?,?)',(key,stamp,json.dumps(body)))
            self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',(kind,key,json.dumps(body)))
            self.db.commit()
        if kind in ('agent_run','scene_review','feedback','scene_feedback','evaluation_job'):
            # The durable compatibility record is committed first. Audit export is independent.
            from safety_app.infrastructure.runtime import runtime
            from pathlib import Path
            project=Path(__file__).resolve().parents[3]
            if Path(self.db.execute('PRAGMA database_list').fetchone()[2]).resolve().is_relative_to(project):
                rt=runtime()
                rt.audit.write('human_decision' if kind in ('feedback','scene_feedback') else 'legacy_record',status=str(body.get('status','ok')),run_id=body.get('id'),event_id=body.get('candidate_id') or body.get('event_id'),output=body,provenance={'legacy_kind':kind,'legacy_id':body.get('id')})
    def snapshot_config(self,config):
        # Multiple replay workers and the viewer share this history; never reuse a version.
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                highest=self.db.execute("SELECT COALESCE(MAX(CAST(json_extract(body,'$.version') AS INTEGER)),0) FROM records WHERE kind='config'").fetchone()[0]
                version=max(highest,config.version)+1
                result=config.model_copy(update={'version':version})
                body=result.model_dump(mode='json') | {'id':f'config-{version}'}
                self.db.execute('INSERT INTO records VALUES (?,?,?)',('config',body['id'],json.dumps(body)))
                self.db.commit()
                return result
            except BaseException:
                self.db.rollback();raise

    def get(self,kind,id):
        with self.lock: row = self.db.execute('SELECT body FROM records WHERE kind=? AND id=?',(kind,id)).fetchone()
        if not row: raise KeyError(id)
        return json.loads(row[0])
    def list(self,kind=None,limit=None,filters=None,before=None):
        query='SELECT kind,body FROM records WHERE NOT EXISTS (SELECT 1 FROM trial_hidden_records h WHERE h.kind=records.kind AND h.id=records.id)'+(' AND kind=?' if kind else '')
        args=(kind,) if kind else ()
        for field,value in (filters or {}).items():
            query+=' AND json_extract(body,?)=?'; args+=(f'$.{field}',value)
        if before:
            field,value=before; query+=' AND json_extract(body,?)<=?'; args+=(f'$.{field}',value)
        query+=' ORDER BY rowid'+(' DESC LIMIT ?' if limit is not None else '')
        if limit is not None: args+= (limit,)
        with self.lock: rows=self.db.execute(query,args).fetchall()
        if limit is not None: rows.reverse()
        return [json.loads(body) | ({'record_kind':k} if not kind else {}) for k,body in rows]

    def candidate_at(self,id,cutoff_ms):
        with self.lock:
            row=self.db.execute('SELECT body FROM candidate_snapshots WHERE id=? AND timestamp_ms<=? ORDER BY timestamp_ms DESC,rowid DESC LIMIT 1',(id,cutoff_ms)).fetchone()
        if row: return json.loads(row[0])
        # Legacy records have no recoverable lifecycle history. Do not expose their latest state.
        body=self.get('candidate',id)
        if body['timestamp_ms']>cutoff_ms: raise ValueError('Candidate outside evidence cutoff')
        body.update(state='candidate',updated_ms=body['timestamp_ms'],closed_ms=None,closure_reason=None)
        body['limitations']=body.get('limitations',[])+['Historical lifecycle snapshot unavailable; state unknown']
        body['measurements']={}
        return body

    def candidate_history(self,session_id,generation,start_ms,end_ms,limit=50):
        with self.lock:
            rows=self.db.execute("SELECT id FROM records WHERE kind='candidate' AND json_extract(body,'$.session_id')=? AND json_extract(body,'$.run_generation')=? AND json_extract(body,'$.timestamp_ms') BETWEEN ? AND ? ORDER BY rowid DESC LIMIT ?",(session_id,generation,start_ms,end_ms,limit)).fetchall()
        return [self.candidate_at(id,end_ms) for id, in reversed(rows)]
