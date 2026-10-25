"""Operator-created test limits and bounded detail passes, enforced inside reservation locks."""
import json
import math


def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS review_test_budgets(id TEXT PRIMARY KEY,job_key TEXT UNIQUE,body TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS review_detail_members(id TEXT,workflow_id TEXT,run_id TEXT PRIMARY KEY,role TEXT,UNIQUE(id,role))')


def test_record(db, workflow_id):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():return None
    # Jobs keep the workflow identity in their typed JSON body.
    row = db.execute("SELECT t.body FROM review_test_budgets t JOIN jobs j ON j.key=t.job_key WHERE json_extract(j.body,'$.run_id')=?", (workflow_id,)).fetchone()
    return json.loads(row[0]) if row else None


def usage(db, ids):
    return db.execute(f'SELECT COALESCE(sum(COALESCE(actual,amount)),0) FROM reservations WHERE workflow_id IN ({",".join("?" for _ in ids)})', ids).fetchone()[0]


def check(db, workflow_id, run_id, amount, require_probe=True):
    from safety_app.infrastructure.budgets import BudgetBlocked
    from safety_app.infrastructure.review_batches import check as batch_check
    batch_check(db,workflow_id,amount)
    record = test_record(db, workflow_id)
    if record:
        if db.execute("SELECT 1 FROM objects WHERE kind='review_test_closed' AND id=?",(record['id'],)).fetchone():
            raise BudgetBlocked('The single funded test is finished; no further paid requests are authorized by this allowance')
        ids = [*record['previous_run_ids'], workflow_id]
        unknown = db.execute(f"SELECT id FROM reservations WHERE workflow_id IN ({','.join('?' for _ in ids)}) AND state IN ('unknown','active')", ids).fetchall()
        if any(row[0] not in record['allowed_unknown_reservation_ids'] for row in unknown):
            raise BudgetBlocked('A new unresolved request blocks this test allowance')
        if require_probe and not db.execute("SELECT 1 FROM objects WHERE kind='review_connection_probe' AND id=?", (workflow_id,)).fetchone():
            raise BudgetBlocked('Worker connection check must pass before this test can generate')
        if usage(db, ids) + amount > record['total_cap_micro'] or usage(db, [workflow_id]) + amount > record['new_run_cap_micro']:
            raise BudgetBlocked('Cumulative test allowance exhausted; another submission cannot renew it')
    member = db.execute('SELECT workflow_id FROM review_detail_members WHERE run_id=?', (run_id,)).fetchone()
    if member:
        used = db.execute('SELECT COALESCE(sum(COALESCE(r.actual,r.amount)),0) FROM reservations r JOIN review_detail_members d ON d.run_id=r.run_id WHERE d.workflow_id=?', (workflow_id,)).fetchone()[0]
        if used + amount > 10000:
            raise BudgetBlocked('Temporal detail allowance exhausted')
        # Preserve $0.02 for mandatory screening/team completion in the test.
        if record and usage(db, [workflow_id]) + amount > record['new_run_cap_micro'] - 20000:
            raise BudgetBlocked('Temporal detail skipped to preserve completion capacity')


def create_test_budget(ledger, id, job_key, previous_run_ids, allowed_unknown_ids, total_cap=.15, new_run_cap=.07):
    from safety_app.infrastructure.budgets import BudgetBlocked
    if not 0 < new_run_cap <= total_cap <= .15 or len(set(previous_run_ids)) != len(previous_run_ids):
        raise ValueError('Invalid bounded test allowance')
    value = {'id': id, 'job_key': job_key, 'previous_run_ids': previous_run_ids,
             'allowed_unknown_reservation_ids': allowed_unknown_ids,
             'total_cap_micro': math.floor(total_cap*1e6), 'new_run_cap_micro': math.floor(new_run_cap*1e6)}
    with ledger.db.connect() as db:
        db.execute('BEGIN IMMEDIATE'); tables(db)
        old = db.execute('SELECT body FROM review_test_budgets WHERE id=?', (id,)).fetchone()
        if old:
            if json.loads(old[0]) != value:raise ValueError('Test allowance already exists with different terms')
            return value
        for prior in previous_run_ids:
            if not db.execute('SELECT 1 FROM reservations WHERE workflow_id=?', (prior,)).fetchone():
                raise ValueError('Unknown prior workflow')
        rows = db.execute(f"SELECT id,state FROM reservations WHERE workflow_id IN ({','.join('?' for _ in previous_run_ids)}) AND actual IS NULL", previous_run_ids).fetchall()
        if {r[0] for r in rows} != set(allowed_unknown_ids) or any(r[1] != 'unknown' for r in rows):
            raise ValueError('Continuation must name exactly the historical unknown reservations; no active calls allowed')
        if usage(db, previous_run_ids) + value['new_run_cap_micro'] > value['total_cap_micro']:
            raise BudgetBlocked('The full new allowance does not fit the cumulative test budget')
        if db.execute('SELECT 1 FROM jobs WHERE key=?', (job_key,)).fetchone():raise ValueError('Register the allowance before submission')
        db.execute('INSERT INTO review_test_budgets VALUES(?,?,?)', (id,job_key,json.dumps(value)))
    ledger.audit.write('review_test_allowance_created', output=value)
    return value


def register_detail(ledger, id, workflow_id, run_id, role='observer'):
    from safety_app.infrastructure.budgets import BudgetBlocked
    with ledger.db.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT run_id FROM review_detail_members WHERE id=? AND role=?', (id,role)).fetchone()
        if old:
            if old[0] != run_id:raise BudgetBlocked('This temporal detail pass was already attempted; it is not automatically repeated')
            return
        exists=db.execute('SELECT 1 FROM review_detail_members WHERE id=? AND workflow_id=?',(id,workflow_id)).fetchone()
        if not exists and db.execute('SELECT count(DISTINCT id) FROM review_detail_members WHERE workflow_id=?', (workflow_id,)).fetchone()[0] >= 2:
            raise BudgetBlocked('Two temporal detail passes already attempted')
        db.execute('INSERT INTO review_detail_members VALUES(?,?,?,?)', (id,workflow_id,run_id,role))


def snapshot(ledger, workflow_id):
    with ledger.db.connect() as db:
        record=test_record(db, workflow_id)
        if not record:return None
        closed=db.execute("SELECT body FROM objects WHERE kind='review_test_closed' AND id=?",(record['id'],)).fetchone()
        return record | {'closed':bool(closed),'committed_usd':usage(db,[*record['previous_run_ids'],workflow_id])/1e6,
                         'new_run_committed_usd':usage(db,[workflow_id])/1e6}


def close_test(ledger,workflow_id,reason):
    from safety_app.domain.models import utc
    with ledger.db.connect() as db:
        db.execute('BEGIN IMMEDIATE');record=test_record(db,workflow_id)
        if not record:raise ValueError('No funded test for this workflow')
        if db.execute("SELECT 1 FROM reservations WHERE workflow_id=? AND state='active'",(workflow_id,)).fetchone():
            raise ValueError('Cannot close a test with an active provider request')
        if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') AND (json_extract(body,'$.run_id')=? OR json_extract(body,'$.payload.analysis_id')=?)",(workflow_id,workflow_id)).fetchone():
            raise ValueError('Wait for this test to finish before closing its allowance')
        body={'id':record['id'],'workflow_id':workflow_id,'reason':reason,'closed_at':utc()}
        db.execute("INSERT OR IGNORE INTO objects VALUES('review_test_closed',?,?)",(record['id'],json.dumps(body)))
    ledger.audit.write('review_test_closed',run_id=workflow_id,output=body)
