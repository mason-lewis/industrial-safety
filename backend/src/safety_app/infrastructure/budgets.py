"""Atomic USD reservations. Unknown usage remains reserved across restarts."""
import json
import math
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from safety_app.domain.models import ToolFailure
from safety_app.infrastructure.repositories import Database
class BudgetBlocked(RuntimeError): pass
class CompletionHeadroomReached(BudgetBlocked):pass
class BudgetLedger:
    def __init__(self,db: Database,audit,config,now=None):
        self.db=db;self.audit=audit;self.config=config
        self.pool_id=None
        self.now=now or (lambda:datetime.now(timezone.utc))
        with db.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS reservations(id TEXT PRIMARY KEY,run_id TEXT,workflow_id TEXT,day TEXT,month TEXT,amount INTEGER,actual INTEGER,state TEXT,input_tokens INTEGER,output_tokens INTEGER,created TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS reservation_pool_members(reservation_id TEXT PRIMARY KEY,pool_id TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS reservation_pools(id TEXT PRIMARY KEY)')
            conn.execute('INSERT OR IGNORE INTO reservation_pools SELECT DISTINCT pool_id FROM reservation_pool_members')
            conn.execute("INSERT OR IGNORE INTO reservation_pools SELECT id FROM reservations WHERE state='held'")
            conn.execute('CREATE TABLE IF NOT EXISTS budget_state(key TEXT PRIMARY KEY,value TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS run_tools(run_id TEXT PRIMARY KEY,count INTEGER)')
            conn.execute('CREATE TABLE IF NOT EXISTS workflow_tools(workflow_id TEXT PRIMARY KEY,count INTEGER)')
            from safety_app.infrastructure.review_allowances import tables
            tables(conn)
            from safety_app.infrastructure.review_batches import tables as batch_tables
            batch_tables(conn)
    def state(self,key,default='0'):
        with self.db.connect() as db: row=db.execute('SELECT value FROM budget_state WHERE key=?',(key,)).fetchone()
        return row['value'] if row else default
    def switch(self,stopped):
        with self.db.connect() as db: db.execute("INSERT INTO budget_state VALUES('stopped',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",('1' if stopped else '0',))
        self.audit.write('paid_stop_switch',status='stopped' if stopped else 'enabled')
    def tool(self,run_id):
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT count FROM run_tools WHERE run_id=?',(run_id,)).fetchone();count=row['count'] if row else 0
            if count>=self.config['limits']['tools']:raise BudgetBlocked('Tool call limit reached')
            workflow=getattr(self,'workflow_id',None)
            if workflow and self.config['limits'].get('workflow_tools'):
                row=db.execute('SELECT count FROM workflow_tools WHERE workflow_id=?',(workflow,)).fetchone()
                total=row[0] if row else 0
                if total>=self.config['limits']['workflow_tools']:raise BudgetBlocked('Question tool allowance exhausted')
                db.execute('INSERT INTO workflow_tools VALUES(?,?) ON CONFLICT(workflow_id) DO UPDATE SET count=excluded.count',(workflow,total+1))
            db.execute('INSERT INTO run_tools VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET count=excluded.count',(run_id,count+1))
    def reserve(self,run_id,workflow_id,model,input_tokens,output_tokens,frames=0,media_bytes=0,clip_ms=0,completion_headroom=0,extra_usd=0):
        try:return self._reserve(run_id,workflow_id,model,input_tokens,output_tokens,frames,media_bytes,clip_ms,completion_headroom,extra_usd)
        except BudgetBlocked as exc:
            self.audit.write('budget_blocked',status='budget_exceeded',run_id=run_id,parent_run_id=workflow_id,
                resources={'block_reason':str(exc),'requested_input_tokens':input_tokens,'requested_output_tokens':output_tokens},requested_model=model)
            raise
    def require_settled(self,workflow_id):
        with self.db.connect() as db:
            from safety_app.infrastructure.review_allowances import check
            check(db,workflow_id,'',0)
            unknown=db.execute("SELECT 1 FROM reservations WHERE workflow_id=? AND state='unknown' LIMIT 1",(workflow_id,)).fetchone()
        if unknown:raise BudgetBlocked('An earlier request in this review has an unresolved charge. No further generation was sent; resolve accounting before continuing.')
    def _reserve(self,run_id,workflow_id,model,input_tokens,output_tokens,frames,media_bytes,clip_ms,completion_headroom,extra_usd,hold=False):
        from app.agents.model_policy import require_hosted_model,validate_agent_models
        try:
            validate_agent_models(self.config);require_hosted_model(model)
        except ValueError as exc:raise BudgetBlocked(str(exc)) from exc
        limits=self.config['limits'];now=self.now();day=now.strftime('%Y-%m-%d');month=day[:7]
        if model not in self.config['aliases'].values():raise BudgetBlocked('Model upgrade or unknown model blocked')
        price=self.config['pricing'].get(model)
        required_rates=('input_per_million','output_per_million')+ (('image_usd',) if frames else ())
        if not price or any(price.get(k) is None for k in required_rates):raise BudgetBlocked('Unknown pricing. Configure bounded token and media rates first')
        if any(not isinstance(price[k],(int,float)) or not math.isfinite(price[k]) or price[k]<0 for k in required_rates):raise BudgetBlocked('Invalid pricing')
        if min(input_tokens,output_tokens,frames,media_bytes,clip_ms)<0:raise BudgetBlocked('Invalid resource estimate')
        if frames>limits['frames'] or media_bytes>limits['media_bytes'] or clip_ms>limits['clip_ms']:raise BudgetBlocked('Media limit reached')
        if not isinstance(extra_usd,(int,float)) or not math.isfinite(extra_usd) or extra_usd<0:raise BudgetBlocked('Invalid additional charge')
        media_charge=(frames*price['image_usd'] if frames else 0)+extra_usd
        amount=math.ceil((input_tokens*max(price['input_per_million'],price.get('cache_write_30m_per_million',0),price.get('cache_write_12h_per_million',0))/1e6+output_tokens*price['output_per_million']/1e6+media_charge)*1e6)
        id=str(uuid4())
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            states={r['key']:r['value'] for r in db.execute('SELECT * FROM budget_state')}
            if states.get('stopped')=='1':raise BudgetBlocked('Paid requests stopped by operator')
            if states.get('circuit_until','')>now.isoformat():raise BudgetBlocked('Provider circuit breaker is open')
            if db.execute("SELECT 1 FROM reservations WHERE workflow_id=? AND state='unknown' LIMIT 1",(workflow_id,)).fetchone():
                raise BudgetBlocked('An earlier request in this review has an unresolved charge. No further generation was sent; resolve accounting before continuing.')
            active=db.execute("SELECT count(*) FROM reservations WHERE state='active'").fetchone()[0]
            if not hold and active>=limits['concurrency']:raise BudgetBlocked('Project request slot is busy')
            from safety_app.infrastructure.review_allowances import check
            check(db,workflow_id,run_id,amount)
            attempts=db.execute("SELECT count(*),COALESCE(sum(input_tokens),0),COALESCE(sum(output_tokens),0) FROM reservations WHERE run_id=? AND id NOT IN (SELECT id FROM reservation_pools)",(run_id,)).fetchone()
            workflow_attempts=db.execute("SELECT count(*) FROM reservations WHERE workflow_id=? AND id NOT IN (SELECT id FROM reservation_pools)",(workflow_id,)).fetchone()[0]
            if not hold and workflow_attempts>=self.config.get('review_team',{}).get('max_workflow_requests',64):raise BudgetBlocked('Workflow request allowance exhausted')
            if completion_headroom and workflow_attempts>=self.config.get('review_team',{}).get('max_workflow_requests',64)-completion_headroom:raise CompletionHeadroomReached('Investigation stopped to preserve synthesis and challenge request capacity')
            if attempts[0]>=limits['requests']:raise BudgetBlocked('Physical request limit reached')
            if attempts[1]+input_tokens>limits['input_tokens'] or attempts[2]+output_tokens>limits['output_tokens']:raise BudgetBlocked('Run token limit reached')
            if self.pool_id:
                pool=db.execute("SELECT * FROM reservations WHERE id=? AND state='held' AND actual IS NULL",(self.pool_id,)).fetchone()
                if not pool or pool['workflow_id']!=workflow_id or pool['amount']<amount:raise BudgetBlocked('Recovery allowance exhausted')
                db.execute('UPDATE reservations SET amount=amount-? WHERE id=?',(amount,self.pool_id))
            for scope,value,cap in [('day',day,limits['day_usd']),('month',month,limits['month_usd']),('workflow_id',workflow_id,limits['workflow_usd']),('run_id',run_id,limits['agent_usd'])]:
                used=db.execute(f'SELECT COALESCE(sum(COALESCE(actual,amount)),0) FROM reservations WHERE {scope}=?',(value,)).fetchone()[0]
                if used+amount>math.floor(cap*1e6):raise BudgetBlocked(f'{scope} budget exhausted')
            db.execute('INSERT INTO reservations VALUES(?,?,?,?,?,?,NULL,?,?,?,?)',(id,run_id,workflow_id,day,month,amount,'held' if hold else 'active',input_tokens,output_tokens,now.isoformat()))
            if hold:db.execute('INSERT INTO reservation_pools VALUES(?)',(id,))
            if self.pool_id:db.execute('INSERT INTO reservation_pool_members VALUES(?,?)',(id,self.pool_id))
        try:
            self.audit.write('budget_reserved',run_id=run_id,parent_run_id=workflow_id,resources={'reservation_id':id,'estimated_request_usd':amount/1e6,'pricing_version':price['version']})
        except Exception:
            # Audit failed before dispatch. Release the request slot and record a known zero charge.
            with self.db.connect() as db:db.execute("UPDATE reservations SET state='settled',actual=0 WHERE id=?",(id,))
            raise
        return id
    def open_pool(self,run_id,workflow_id,cap):
        # Hold an explicit USD allowance, without inventing tokens or a provider request.
        id=self._reserve(run_id,workflow_id,'gpt-6-luna',0,0,0,0,0,0,cap,hold=True)
        return id
    def close_pool(self,id):
        with self.db.connect() as db:
            db.execute("UPDATE reservations SET state='settled',actual=0 WHERE id=? AND state='held'",(id,))
        self.audit.write('recovery_allowance_released',resources={'reservation_id':id})

    def reconcile(self,id,actual=None,input_tokens=None,output_tokens=None,failed=False):
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM reservations WHERE id=?',(id,)).fetchone()
            if not row:raise KeyError(id)
            if row['actual'] is not None: raise ValueError('Reservation already reconciled')
            if actual is not None and (not math.isfinite(actual) or actual<0):raise ValueError('Invalid actual charge')
            db.execute('UPDATE reservations SET actual=?,state=?,input_tokens=?,output_tokens=? WHERE id=?',
                (None if actual is None else math.ceil(actual*1e6),'unknown' if actual is None else 'settled',input_tokens if input_tokens is not None else row['input_tokens'],output_tokens if output_tokens is not None else row['output_tokens'],id))
            member=db.execute('SELECT pool_id FROM reservation_pool_members WHERE reservation_id=?',(id,)).fetchone()
            if member and actual is not None:
                returned=row['amount']-math.ceil(actual*1e6)
                db.execute("UPDATE reservations SET amount=max(0,amount+?) WHERE id=? AND state='held'",(returned,member['pool_id']))
            old=db.execute("SELECT value FROM budget_state WHERE key='failures'").fetchone();count=(int(old['value']) if old else 0)+1 if failed else 0
            db.execute("INSERT INTO budget_state VALUES('failures',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(count),))
            if count>=3:
                until=(self.now()+timedelta(seconds=self.config['limits']['circuit_seconds'])).isoformat()
                db.execute("INSERT INTO budget_state VALUES('circuit_until',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(until,))
        self.audit.write('budget_reconciled',run_id=row['run_id'],parent_run_id=row['workflow_id'],resources={'reservation_id':id,'actual_usd':actual,'reserved_usd':row['amount']/1e6 if actual is None else 0})
        if failed and count>=3:self.audit.write('circuit_breaker_open',status='deferred',resources={'until':until})
    def status(self):
        now=self.now();day=now.strftime('%Y-%m-%d');month=day[:7];result={}
        with self.db.connect() as db:
            for window,value in [('day',day),('month',month)]:
                row=db.execute(f'SELECT COALESCE(sum(actual),0),COALESCE(sum(CASE WHEN actual IS NULL THEN amount ELSE 0 END),0) FROM reservations WHERE {window}=?',(value,)).fetchone()
                cap=self.config['limits'][window+'_usd'];result[window]={'window_utc':value,'limit_usd':cap,'spent_usd':row[0]/1e6,'reserved_usd':row[1]/1e6,'remaining_usd':max(0,cap-(row[0]+row[1])/1e6)}
            outstanding=[dict(r) for r in db.execute("SELECT id,run_id,workflow_id,amount,state,created FROM reservations WHERE actual IS NULL")]
        return {'windows':result,'outstanding':outstanding,'stopped':self.state('stopped')=='1','circuit_until':self.state('circuit_until',''),'pricing':self.config['pricing'],'limits':self.config['limits'],'training':self.config['training']}
