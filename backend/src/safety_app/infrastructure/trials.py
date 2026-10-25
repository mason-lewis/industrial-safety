"""Reversible trial resets. Hide records; never delete training data, media or costs."""
import hashlib
import json
from safety_app.domain.models import TrialResetPlan,TrialArchive,utc
TRIAL_KINDS=frozenset({'event','observation','finding','review','review_cache','decision','coverage_gap','cv_frame','label_queue'})
TRIAL_JOBS=frozenset({'replay','live','event_review','facility_review','periodic_review'})
LEGACY_KINDS=frozenset({'session','run','candidate','assessment','response','feedback','scene_feedback','incident','agent_run','scene_review','scene_event','review_provenance','evaluation_job'})
class TrialConflict(ValueError):pass
class Trials:
    def __init__(self,rt):
        self.rt=rt
        with rt.db.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS trial_hidden_records(kind TEXT,id TEXT,reset_id TEXT,PRIMARY KEY(kind,id))')
            db.execute('CREATE TABLE IF NOT EXISTS trial_hidden_jobs(id TEXT PRIMARY KEY,reset_id TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS trial_cache_archive(reset_id TEXT,id TEXT,body TEXT,PRIMARY KEY(reset_id,id))')
            db.execute('CREATE TABLE IF NOT EXISTS trial_job_snapshot(reset_id TEXT,id TEXT,body TEXT,PRIMARY KEY(reset_id,id))')
            db.execute('CREATE TABLE IF NOT EXISTS trial_archives(id TEXT PRIMARY KEY,body TEXT)')
        # ATTACH allows one transaction to archive both the new and compatibility views.
        from app.storage.repository import Repository
        self.legacy_path=rt.root/'data/artifacts/runs/lab.sqlite'
        repo=Repository(self.legacy_path);repo.db.close()
    def _attach(self,db):db.execute('ATTACH DATABASE ? AS legacy',(str(self.legacy_path),))
    def _plan(self,db):
        objects=[(r['kind'],r['id'],json.loads(r['body'])) for r in db.execute('SELECT kind,id,body FROM objects WHERE NOT EXISTS (SELECT 1 FROM trial_hidden_records h WHERE h.kind=objects.kind AND h.id=objects.id)')]
        # Protect draft labels as well as approved labels and frozen label copies.
        frame_ids={body['frame_id'] for kind,id,body in objects if kind=='annotation'}
        for kind,id,body in objects:
            if kind=='dataset':frame_ids.update(a['frame_id'] for a in body.get('provenance',{}).get('annotations',[]))
        jobs=[(r['id'],r['kind'],r['status'],json.loads(r['body'])) for r in db.execute('SELECT * FROM jobs WHERE NOT EXISTS (SELECT 1 FROM trial_hidden_jobs h WHERE h.id=jobs.id)')]
        for id,kind,status,body in jobs:
            if kind not in TRIAL_JOBS:frame_ids.update(body['payload'].get('frame_ids',[]))
        media_ids={body['media_id'] for kind,id,body in objects if kind=='frame' and id in frame_ids}
        # Saved video answers and partial inspected chunks keep their evidence visible.
        media_ids.update(body['media_id'] for kind,id,body in objects if kind in ('video_chunk','video_analysis','video_safety_case'))
        reset=[];keep={}
        for kind,id,body in objects:
            temporary=kind in TRIAL_KINDS or kind.startswith('legacy_') and kind[7:] in LEGACY_KINDS
            if kind=='media':temporary=id not in media_ids
            if kind=='frame':temporary=body['media_id'] not in media_ids
            if temporary:reset.append((kind,id))
            else:keep[kind]=keep.get(kind,0)+1
        legacy=[(r['kind'],r['id']) for r in db.execute('SELECT kind,id FROM legacy.records WHERE NOT EXISTS (SELECT 1 FROM legacy.trial_hidden_records h WHERE h.kind=records.kind AND h.id=records.id)') if r['kind'] in LEGACY_KINDS]
        job_ids=[id for id,kind,status,body in jobs if kind in TRIAL_JOBS]
        running=[id for id,kind,status,body in jobs if status=='running']
        counts={}
        for kind,id in reset:counts[kind]=counts.get(kind,0)+1
        counts['trial_jobs']=len(job_ids);counts['compatibility_records']=len(legacy)
        keep['label_and_model_jobs']=sum(kind not in TRIAL_JOBS for id,kind,status,body in jobs)
        fingerprint=hashlib.sha256(json.dumps({'records':sorted(reset),'jobs':sorted(job_ids),'legacy':sorted(legacy),'protected_media':sorted(media_ids),'running':sorted(running)},sort_keys=True).encode()).hexdigest()
        return TrialResetPlan(plan_id=fingerprint,reset_counts=counts,preserved_counts=keep,running_job_ids=running,can_reset=not running),reset,job_ids,legacy
    def preview(self):
        with self.rt.db.connect() as db:
            self._attach(db);db.execute('BEGIN IMMEDIATE');return self._plan(db)[0]
    def reset(self,plan_id,name,reviewer_id):
        with self.rt.db.connect() as db:
            self._attach(db);db.execute('BEGIN IMMEDIATE')
            plan,records,jobs,legacy=self._plan(db)
            if not plan.can_reset:raise TrialConflict('Stop running jobs before resetting trials. Labels and model jobs will not be cancelled by reset.')
            if plan_id!=plan.plan_id:raise TrialConflict('Trial data changed. Refresh the reset preview.')
            archive=TrialArchive(name=name,reviewer_id=reviewer_id,reset_counts=plan.reset_counts,preserved_counts=plan.preserved_counts)
            # Audit first. If persistence fails, no visibility changes commit.
            self.rt.audit.write('trial_reset',run_id=archive.id,output=archive.model_dump(mode='json'))
            for kind,id in records:
                if kind=='review_cache':
                    row=db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(kind,id)).fetchone()
                    db.execute('INSERT INTO trial_cache_archive VALUES(?,?,?)',(archive.id,id,row['body']))
                    db.execute('DELETE FROM objects WHERE kind=? AND id=?',(kind,id))
                else:db.execute('INSERT INTO trial_hidden_records VALUES(?,?,?)',(kind,id,archive.id))
            db.executemany('INSERT INTO legacy.trial_hidden_records VALUES(?,?,?)',[(kind,id,archive.id) for kind,id in legacy])
            for id in jobs:
                from safety_app.domain.models import Job,ToolFailure
                row=db.execute('SELECT body FROM jobs WHERE id=?',(id,)).fetchone();job=Job.model_validate_json(row['body'])
                db.execute('INSERT INTO trial_job_snapshot VALUES(?,?,?)',(archive.id,id,row['body']))
                if job.status=='queued':
                    job.status='cancelled';job.error=ToolFailure(code='trial_reset',message='Queued trial archived. Start a new replay to run it again.');job.updated_at=utc()
                    db.execute('UPDATE jobs SET status=?,body=?,lease=NULL WHERE id=?',(job.status,job.model_dump_json(),id))
                db.execute('INSERT INTO trial_hidden_jobs VALUES(?,?)',(id,archive.id))
                # Free the user key for another trial. Original job/body/ID remain intact.
                db.execute('UPDATE jobs SET key=? WHERE id=?',(f'archived:{archive.id}:{id}',id))
            db.execute('INSERT INTO trial_archives VALUES(?,?)',(archive.id,archive.model_dump_json()))
        return archive
    def list(self):
        with self.rt.db.connect() as db:rows=db.execute('SELECT body FROM trial_archives ORDER BY rowid DESC').fetchall()
        return [TrialArchive.model_validate_json(r['body']) for r in rows]
    def restore(self,id,reviewer_id):
        with self.rt.db.connect() as db:
            self._attach(db);db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM trial_archives WHERE id=?',(id,)).fetchone()
            if not row:raise KeyError(id)
            archive=TrialArchive.model_validate_json(row['body'])
            if archive.status=='restored':return archive
            if db.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone():raise TrialConflict('Stop running jobs before restoring trial views')
            self.rt.audit.write('trial_restored',run_id=id,provenance={'archive_id':id,'reviewer_id':reviewer_id})
            # Old review caches stay inactive so a reset always allows fresh model trials.
            db.execute("DELETE FROM trial_hidden_records WHERE reset_id=? AND kind!='review_cache'",(id,))
            db.execute('DELETE FROM trial_hidden_jobs WHERE reset_id=?',(id,))
            db.execute('DELETE FROM legacy.trial_hidden_records WHERE reset_id=?',(id,))
            archive.status='restored';archive.restored_at=utc()
            db.execute('UPDATE trial_archives SET body=? WHERE id=?',(archive.model_dump_json(),id))
        return archive
