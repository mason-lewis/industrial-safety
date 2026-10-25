"""Durable local queue with atomic claim, heartbeat, recovery and cancellation."""
import json
from datetime import datetime,timezone,timedelta
from safety_app.domain.models import Job,utc,ToolFailure
class Jobs:
    def __init__(self,db,audit):
        self.db=db;self.audit=audit
        with db.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS trial_hidden_jobs(id TEXT PRIMARY KEY,reset_id TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,key TEXT UNIQUE,kind TEXT,status TEXT,body TEXT,lease TEXT)')
    def start(self,kind,key,payload,site_id='local',exclusive_session=None):
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM jobs WHERE key=?',(key,)).fetchone()
            if row:
                job=Job.model_validate_json(row['body'])
                if job.kind!=kind or job.payload!=payload or job.site_id!=site_id:raise ValueError('Idempotency key has a different request')
                return job
            if exclusive_session and db.execute("SELECT 1 FROM jobs WHERE json_extract(body,'$.site_id')=? AND kind=? AND status IN ('queued','running') AND json_extract(body,'$.payload.session_id')=?",(site_id,kind,exclusive_session)).fetchone():
                raise ValueError('This conversation already has a pending message')
            job=Job(kind=kind,idempotency_key=key,payload=payload,site_id=site_id)
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,NULL)',(job.id,key,kind,job.status,job.model_dump_json()))
        self.audit.write('job_queued',job_id=job.id,run_id=job.run_id,workflow_name=kind,arguments=payload);return job
    def get(self,id):
        with self.db.connect() as db:row=db.execute('SELECT body FROM jobs WHERE id=?',(id,)).fetchone()
        if not row:raise KeyError(id)
        return Job.model_validate_json(row['body'])
    def list(self):
        with self.db.connect() as db:rows=db.execute('SELECT body FROM jobs WHERE NOT EXISTS (SELECT 1 FROM trial_hidden_jobs h WHERE h.id=jobs.id) ORDER BY rowid DESC LIMIT 200').fetchall()
        return [Job.model_validate_json(r['body']) for r in rows]
    def claim(self,kinds,live_parallel=1):
        # Sealed Live windows of one run may be screened concurrently (up to live_parallel); they publish in order.
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            expired=db.execute("SELECT body FROM jobs WHERE status='running' AND lease<?",(utc(),)).fetchall()
            for row in expired:
                job=Job.model_validate_json(row['body']);job.status='interrupted';self.audit.write('worker_lost',status='interrupted',job_id=job.id,run_id=job.run_id);job.error=ToolFailure(code='worker_lost',message='Worker lease expired. Results may be partial.');job.updated_at=utc()
                db.execute('UPDATE jobs SET status=?,body=? WHERE id=?',(job.status,job.model_dump_json(),job.id))
                if job.kind=='video_chat':
                    # Worker loss cannot establish zero provider usage. Retain active
                    # charges as unknown; undispatched completion holds can be released.
                    db.execute("UPDATE reservations SET state='unknown' WHERE workflow_id=? AND state='active'",(job.run_id,))
                    db.execute("UPDATE reservations SET state='settled',actual=0 WHERE workflow_id=? AND state='held'",(job.run_id,))
            row=db.execute(f"SELECT body FROM jobs WHERE NOT EXISTS (SELECT 1 FROM trial_hidden_jobs h WHERE h.id=jobs.id) AND status='queued' AND (kind!='live_screening' OR EXISTS (SELECT 1 FROM objects w WHERE w.kind='review_window' AND w.id=json_extract(jobs.body,'$.payload.window_id') AND json_extract(w.body,'$.job_id')=jobs.id AND json_extract(w.body,'$.status')='queued')) AND (kind!='live_screening' OR (SELECT count(*) FROM jobs active WHERE active.kind='live_screening' AND active.status='running' AND json_extract(active.body,'$.payload.review_run_id')=json_extract(jobs.body,'$.payload.review_run_id'))<{max(1,int(live_parallel))}) AND kind IN ({','.join('?' for _ in kinds)}) ORDER BY CASE WHEN kind='live_screening' THEN -1 WHEN kind='video_safety_review' AND json_extract(body,'$.payload.priority')='high' THEN 0 ELSE 1 END,rowid LIMIT 1",kinds).fetchone()
            if not row:return None
            job=Job.model_validate_json(row['body']);job.status='running';job.updated_at=utc()
            lease=(datetime.now(timezone.utc)+timedelta(seconds=120)).isoformat()
            db.execute('UPDATE jobs SET status=?,body=?,lease=? WHERE id=?',(job.status,job.model_dump_json(),lease,job.id))
        self.audit.write('run_started',job_id=job.id,run_id=job.run_id,workflow_name=job.kind,status='running');return job
    def finish(self,job,status,refs=None,error=None):
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE');current=db.execute('SELECT status FROM jobs WHERE id=?',(job.id,)).fetchone()
            if current['status']=='cancelled' and status!='cancelled':return self.get(job.id)
            job.review_run_id=job.review_run_id or self.get(job.id).review_run_id
            job.status=status;job.result_refs=refs or [];job.error=error;job.updated_at=utc()
            db.execute('UPDATE jobs SET status=?,body=?,lease=NULL WHERE id=?',(status,job.model_dump_json(),job.id))
            if job.kind=='video_analysis' and job.payload.get('review_strategy')=='full':
                state='ended' if status=='completed' else status
                db.execute("UPDATE objects SET body=json_set(body,'$.state',?,'$.updated_at',?) WHERE kind='review_run' AND id=?",(state,utc(),job.run_id))
        self.audit.write('run_finished' if status=='completed' else 'run_failed',job_id=job.id,run_id=job.run_id,workflow_name=job.kind,status=status,error=error,output={'result_refs':job.result_refs});return job
    def heartbeat(self,id):
        with self.db.connect() as db:db.execute("UPDATE jobs SET lease=? WHERE id=? AND status='running'",((datetime.now(timezone.utc)+timedelta(seconds=120)).isoformat(),id))
    def resume(self,id):
        """Requeue the same identity; neither usage nor historical audit is reset."""
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM jobs WHERE id=?',(id,)).fetchone()
            if not row:raise KeyError(id)
            job=Job.model_validate_json(row['body'])
            if job.status in ('queued','running'):raise ValueError('Job is already active')
            if db.execute('SELECT 1 FROM reservations WHERE workflow_id=? AND actual IS NULL',(job.run_id,)).fetchone():
                raise ValueError('Unresolved request charge prevents automatic replay')
            job.status='queued';job.error=None;job.updated_at=utc()
            db.execute("UPDATE jobs SET status='queued',body=?,lease=NULL WHERE id=?",(job.model_dump_json(),id))
        self.audit.write('job_resumed',job_id=id,run_id=job.run_id,output={'same_budget_workflow':True})
        return job
    def cancelled(self,id):return self.get(id).status=='cancelled'
    def cancel(self,id):return self.finish(self.get(id),'cancelled',error=ToolFailure(code='cancelled',message='User cancelled this job. Dispatched calls may incur cost.'))
