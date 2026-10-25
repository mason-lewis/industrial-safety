"""Durable, operator-created spending caps shared by sequential video tests."""
import json
import math


def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS review_batches(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS review_batch_members(job_key TEXT PRIMARY KEY,batch_id TEXT NOT NULL,position INTEGER NOT NULL,attempt INTEGER NOT NULL,cap_micro INTEGER NOT NULL,UNIQUE(batch_id,position,attempt))')


def roots(db,batch):
    members=db.execute("SELECT j.body FROM jobs j JOIN review_batch_members m ON j.key=m.job_key WHERE m.batch_id=?",(batch['id'],)).fetchall()
    return [json.loads(x[0])['run_id'] for x in members]


def total(db,ids):
    if not ids:return 0
    return db.execute(f"SELECT COALESCE(sum(COALESCE(actual,amount)),0) FROM reservations WHERE workflow_id IN ({','.join('?' for _ in ids)})",ids).fetchone()[0]


def state(db,batch):
    run_ids=roots(db,batch);prior=total(db,batch['prior_run_ids'])
    spent=total(db,run_ids)
    unknown=[]
    if run_ids:
        unknown=[x[0] for x in db.execute(f"SELECT id FROM reservations WHERE workflow_id IN ({','.join('?' for _ in run_ids)}) AND state='unknown'",run_ids)]
    closed=bool(db.execute("SELECT 1 FROM objects WHERE kind='review_batch_closed' AND id=?",(batch['id'],)).fetchone())
    return {'id':batch['id'],'cap_usd':batch['cap_micro']/1e6,'committed_usd':(prior+spent)/1e6,
            'prior_committed_usd':prior/1e6,'batch_committed_usd':spent/1e6,
            'remaining_usd':max(0,batch['cap_micro']-prior-spent)/1e6,
            'unknown_reservation_ids':unknown,'closed':closed,'run_ids':run_ids}


def create(ledger,id,sources,prior_run_ids,allowed_historical_unknown,cap_usd=1):
    from safety_app.infrastructure.budgets import BudgetBlocked
    if not sources or not 0<cap_usd<=1:raise ValueError('A funded batch needs frozen sources and a cap at most $1')
    body={'id':id,'sources':sources,'prior_run_ids':prior_run_ids,
          'allowed_historical_unknown':allowed_historical_unknown,'cap_micro':math.floor(cap_usd*1e6),'site_id':'local'}
    with ledger.db.connect() as db:
        db.execute('BEGIN IMMEDIATE');tables(db)
        old=db.execute('SELECT body FROM review_batches WHERE id=?',(id,)).fetchone()
        if old:
            if json.loads(old[0])!=body:raise ValueError('Batch terms are immutable')
            return body
        if prior_run_ids:
            unknown=[x[0] for x in db.execute(f"SELECT id FROM reservations WHERE workflow_id IN ({','.join('?' for _ in prior_run_ids)}) AND actual IS NULL",prior_run_ids)]
            if set(unknown)!=set(allowed_historical_unknown):raise ValueError('Historical unknowns must match exactly; active calls cannot be ignored')
            if any(db.execute('SELECT state FROM reservations WHERE id=?',(id,)).fetchone()[0]!='unknown' for id in unknown):raise ValueError('Wait for active calls')
        if total(db,prior_run_ids)>=body['cap_micro']:raise BudgetBlocked('No batch allowance remains')
        db.execute('INSERT INTO review_batches VALUES(?,?)',(id,json.dumps(body)))
    ledger.audit.write('video_test_batch_created',output=body)
    return body


def enroll(ledger,id,position,attempt,job_key,cap_usd=.15):
    from safety_app.infrastructure.budgets import BudgetBlocked
    cap=math.floor(cap_usd*1e6)
    with ledger.db.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        batch=json.loads(db.execute('SELECT body FROM review_batches WHERE id=?',(id,)).fetchone()[0])
        if not 0<=position<len(batch['sources']) or attempt<1 or not 0<cap<=150000:raise ValueError('Invalid bounded batch member')
        old=db.execute('SELECT * FROM review_batch_members WHERE job_key=?',(job_key,)).fetchone()
        if old:
            if (old['batch_id'],old['position'],old['attempt'],old['cap_micro'])!=(id,position,attempt,cap):raise ValueError('Member terms changed')
            return
        current=state(db,batch)
        if current['closed'] or current['unknown_reservation_ids']:raise BudgetBlocked('Batch closed or has an unresolved request')
        if cap>round(current['remaining_usd']*1e6):raise BudgetBlocked('Full member allowance does not fit the remaining batch budget')
        for root in roots(db,batch):
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') AND (json_extract(body,'$.run_id')=? OR json_extract(body,'$.payload.review_run_id')=? OR json_extract(body,'$.payload.analysis_id')=?)",(root,root,root)).fetchone():
                raise BudgetBlocked('Wait for the previous video and its team before enrolling another')
        earlier=db.execute('SELECT position FROM review_batch_members WHERE batch_id=? AND attempt=?',(id,attempt)).fetchall()
        expected=0 if not earlier else max(x[0] for x in earlier)+1
        if position!=expected:raise ValueError('Each pass must follow the frozen numeric order')
        if attempt>1 and db.execute('SELECT count(DISTINCT position) FROM review_batch_members WHERE batch_id=? AND attempt=?',(id,attempt-1)).fetchone()[0]!=len(batch['sources']):
            raise ValueError('Finish the first ordered pass before another pass')
        db.execute('INSERT INTO review_batch_members VALUES(?,?,?,?,?)',(job_key,id,position,attempt,cap))
    ledger.audit.write('video_test_member_enrolled',output={'batch_id':id,'position':position,'attempt':attempt,'job_key':job_key,'cap_usd':cap/1e6})


def check(db,workflow_id,amount):
    from safety_app.infrastructure.budgets import BudgetBlocked
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():return
    row=db.execute("SELECT b.body,m.cap_micro FROM review_batches b JOIN review_batch_members m ON m.batch_id=b.id JOIN jobs j ON j.key=m.job_key WHERE json_extract(j.body,'$.run_id')=?",(workflow_id,)).fetchone()
    if not row:return
    batch=json.loads(row[0]);current=state(db,batch)
    if current['closed']:raise BudgetBlocked('The funded video batch is closed')
    if current['unknown_reservation_ids']:raise BudgetBlocked('A new unknown charge stops this entire video batch')
    if total(db,[*batch['prior_run_ids'],*current['run_ids']])+amount>batch['cap_micro']:raise BudgetBlocked('The $1 cumulative video test cap is exhausted')
    if total(db,[workflow_id])+amount>row[1]:raise BudgetBlocked('This video test allowance is exhausted')


def snapshot(ledger,id):
    with ledger.db.connect() as db:
        batch=json.loads(db.execute('SELECT body FROM review_batches WHERE id=?',(id,)).fetchone()[0])
        return batch|state(db,batch)


def close(ledger,id,reason):
    from safety_app.domain.models import utc
    with ledger.db.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        batch=json.loads(db.execute('SELECT body FROM review_batches WHERE id=?',(id,)).fetchone()[0])
        for root in roots(db,batch):
            if db.execute("SELECT 1 FROM reservations WHERE workflow_id=? AND state='active'",(root,)).fetchone():raise ValueError('Wait for active requests before closing')
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') AND (json_extract(body,'$.run_id')=? OR json_extract(body,'$.payload.review_run_id')=? OR json_extract(body,'$.payload.analysis_id')=?)",(root,root,root)).fetchone():raise ValueError('Stop or finish linked jobs before closing')
        db.execute("INSERT OR IGNORE INTO objects VALUES('review_batch_closed',?,?)",(id,json.dumps({'id':id,'reason':reason,'closed_at':utc()})))
    ledger.audit.write('video_test_batch_closed',output={'id':id,'reason':reason})
