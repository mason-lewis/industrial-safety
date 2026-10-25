"""Cross-process FIFO admission. Waiting never repeats a paid call."""
import asyncio
from contextlib import asynccontextmanager
import time
from safety_app.domain.models import uid
from safety_app.infrastructure.budgets import BudgetBlocked

class RequestQueueTimeout(TimeoutError):pass

def tables(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS request_waits(workflow_id TEXT PRIMARY KEY,seconds REAL NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS provider_queue(id TEXT PRIMARY KEY,workflow_id TEXT,expires REAL,started REAL,waiting INTEGER DEFAULT 1)')
    columns={r[1] for r in conn.execute('PRAGMA table_info(provider_queue)')}
    if 'started' not in columns:conn.execute('ALTER TABLE provider_queue ADD COLUMN started REAL')
    if 'waiting' not in columns:conn.execute('ALTER TABLE provider_queue ADD COLUMN waiting INTEGER DEFAULT 1')

def queue_seconds(db,workflow_id):
    with db.connect() as conn:
        tables(conn)
        row=conn.execute('SELECT seconds FROM request_waits WHERE workflow_id=?',(workflow_id,)).fetchone()
        pending=conn.execute('SELECT started FROM provider_queue WHERE workflow_id=? AND waiting=1 AND expires>?',(workflow_id,time.time())).fetchall()
    return (row[0] if row else 0)+sum(max(0,time.time()-r[0]) for r in pending if r[0] is not None)

@asynccontextmanager
async def request_slot(ledger,workflow_id,run_id,audit,cancelled):
    ticket,started,announced=uid(),time.monotonic(),False
    previous=queue_seconds(ledger.db,workflow_id);limit=ledger.config['limits'].get('queue_seconds',120)
    with ledger.db.connect() as conn:
        tables(conn)
        conn.execute('INSERT INTO provider_queue(id,workflow_id,expires,started,waiting) VALUES(?,?,?,?,1)',(ticket,workflow_id,time.time()+10,time.time()))
    recorded=False;keeper=None
    def finish_wait():
        nonlocal recorded
        elapsed=time.monotonic()-started
        with ledger.db.connect() as conn:
            conn.execute('UPDATE provider_queue SET waiting=0 WHERE id=?',(ticket,))
            conn.execute('INSERT INTO request_waits VALUES(?,?) ON CONFLICT(workflow_id) DO UPDATE SET seconds=seconds+excluded.seconds',(workflow_id,elapsed))
        recorded=True
        if announced:audit.write('request_queue_finished',run_id=run_id,duration_ms=elapsed*1000,resources={'queue_ms':elapsed*1000})
    async def heartbeat():
        while True:
            with ledger.db.connect() as conn:conn.execute('UPDATE provider_queue SET expires=? WHERE id=?',(time.time()+10,ticket))
            await asyncio.sleep(1)
    try:
        while True:
            if cancelled():raise asyncio.CancelledError()
            if ledger.state('stopped')=='1':raise BudgetBlocked('Paid requests stopped by operator')
            if previous+time.monotonic()-started>=limit:raise RequestQueueTimeout('Provider queue wait allowance exhausted')
            with ledger.db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('DELETE FROM provider_queue WHERE expires<?',(time.time(),))
                conn.execute('UPDATE provider_queue SET expires=? WHERE id=?',(time.time()+10,ticket))
                head=conn.execute('SELECT id FROM provider_queue ORDER BY rowid LIMIT 1').fetchone()
                active=conn.execute("SELECT count(*) FROM reservations WHERE state='active'").fetchone()[0]
            if head and head[0]==ticket and active<ledger.config['limits']['concurrency']:
                finish_wait();keeper=asyncio.create_task(heartbeat())
                yield
                return
            if not announced:
                audit.write('request_queued',status='queued',run_id=run_id,resources={'reason':'shared_provider_slot'});announced=True
            await asyncio.sleep(.1)
    finally:
        if keeper:keeper.cancel();await asyncio.gather(keeper,return_exceptions=True)
        if not recorded:finish_wait()
        with ledger.db.connect() as conn:conn.execute('DELETE FROM provider_queue WHERE id=?',(ticket,))
