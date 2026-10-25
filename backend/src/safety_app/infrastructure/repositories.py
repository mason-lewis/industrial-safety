"""Local records. Immutable artifacts use insert; mutable jobs use update."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
class Database:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS trial_hidden_records(kind TEXT,id TEXT,reset_id TEXT,PRIMARY KEY(kind,id))')
            db.execute('CREATE TABLE IF NOT EXISTS objects(kind TEXT,id TEXT,body TEXT,PRIMARY KEY(kind,id))')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30);db.row_factory=sqlite3.Row
        from safety_app.modules.safety_review.catalog import owner_for
        db.create_function('source_owner',1,owner_for,deterministic=True)
        try:
            yield db;db.commit()
        except BaseException: db.rollback();raise
        finally: db.close()
    def save(self,kind,record,immutable=False):
        body=record.model_dump(mode='json') if hasattr(record,'model_dump') else record
        with self.connect() as db:
            if immutable:
                old=db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(kind,body['id'])).fetchone()
                if old:
                    previous=json.loads(old['body'])
                    if kind=='source':
                        from safety_app.domain.models import SourceExcerpt
                        same=SourceExcerpt.model_validate(previous)==SourceExcerpt.model_validate(body)
                    else:same=previous==body
                    if not same: raise ValueError('Immutable record already exists')
                    return body
            db.execute('INSERT INTO objects VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body',(kind,body['id'],json.dumps(body)))
        return body
    def get(self,kind,id):
        with self.connect() as db: row=db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(kind,id)).fetchone()
        if not row: raise KeyError(id)
        if kind=='review_cache':
            with self.connect() as db:
                if db.execute('SELECT 1 FROM trial_hidden_records WHERE kind=? AND id=?',(kind,id)).fetchone():raise KeyError(id)
        return json.loads(row['body'])
    def list(self,kind):
        with self.connect() as db: rows=db.execute('SELECT body FROM objects WHERE kind=? AND NOT EXISTS (SELECT 1 FROM trial_hidden_records h WHERE h.kind=objects.kind AND h.id=objects.id) ORDER BY rowid',(kind,)).fetchall()
        return [json.loads(row['body']) for row in rows]
    def save_many(self,records,job_id=None):
        """Commit related immutable outputs together. Cancellation wins before commit."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if job_id:
                row=db.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
                if row and row['status']=='cancelled':
                    import asyncio
                    raise asyncio.CancelledError()
            for kind,record in records:
                body=record.model_dump(mode='json') if hasattr(record,'model_dump') else record
                old=db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(kind,body['id'])).fetchone()
                if old:
                    if json.loads(old['body'])!=body:raise ValueError('Immutable output already exists with a different value')
                else:db.execute('INSERT INTO objects VALUES(?,?,?)',(kind,body['id'],json.dumps(body)))
