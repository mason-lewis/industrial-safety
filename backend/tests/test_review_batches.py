"""Funded sample tests use a durable aggregate cap, not a process counter."""
import shutil
from pathlib import Path
import pytest
from safety_app.infrastructure.runtime import Runtime
from safety_app.infrastructure.budgets import BudgetBlocked
from safety_app.infrastructure import review_batches as b

@pytest.fixture
def rt(tmp_path):
 shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
 return Runtime(tmp_path)

def setup(rt):
 old=rt.budgets.reserve('old','old','gpt-6-luna',100,100);rt.budgets.reconcile(old)
 b.create(rt.budgets,'batch',[{'id':'a'},{'id':'b'}],['old'],[old],.2)
 return old

def member(rt,pos=0):
 key=f'key{pos}';b.enroll(rt.budgets,'batch',pos,1,key,.1)
 return rt.jobs.start('live_review',key,{})

def test_numeric_order_and_unknown_halt_survive_restart(rt):
 old=setup(rt)
 with pytest.raises(ValueError,match='numeric'):member(rt,1)
 j=member(rt);r=rt.budgets.reserve('s',j.run_id,'gpt-6-luna',100,100);rt.budgets.reconcile(r)
 again=Runtime(rt.root)
 with pytest.raises(BudgetBlocked,match='unresolved'):again.budgets.reserve('t',j.run_id,'gpt-6-luna',100,100)
 with pytest.raises(BudgetBlocked,match='unresolved'):member(again,1)
 assert b.snapshot(again.budgets,'batch')['unknown_reservation_ids']==[r]
 with rt.db.connect() as db:assert db.execute('SELECT state FROM reservations WHERE id=?',(old,)).fetchone()[0]=='unknown'

def test_member_and_cumulative_limits_and_sealed_batch(rt):
 setup(rt);j=member(rt)
 with pytest.raises(BudgetBlocked,match='video test allowance'):check(rt,j.run_id,100001)
 with pytest.raises(BudgetBlocked,match='cumulative'):check(rt,j.run_id,200001)
 rt.jobs.finish(j,'completed');b.close(rt.budgets,'batch','done')
 with pytest.raises(BudgetBlocked,match='closed'):check(rt,j.run_id,0)

def check(rt,root,amount):
 with rt.db.connect() as db:b.check(db,root,amount)

def test_live_activity_includes_observer_and_excludes_other_site(rt,monkeypatch):
 from types import SimpleNamespace
 from safety_app.api.routes import review_runs as routes
 from safety_app.api.auth import Operator
 root=rt.jobs.start('live_review','root',{})
 child=rt.jobs.start('live_screening','window',{'review_run_id':root.run_id})
 foreign=rt.jobs.start('live_screening','foreign',{'review_run_id':root.run_id},site_id='other')
 rt.audit.write('model_request',job_id=child.id)
 rt.audit.write('tool_call',job_id=child.id)
 rt.audit.write('tool_call',job_id=foreign.id)
 monkeypatch.setattr(routes,'runtime',lambda:rt)
 monkeypatch.setattr(routes.ReviewRuns,'get',lambda *args:SimpleNamespace(job_id=root.id))
 events=routes.activity(root.run_id,0,Operator()).events
 assert [e.event_type for e in events][-2:]==['model_request','tool_call']
 assert all(e.job_id!=foreign.id for e in events)

def test_transport_keeps_only_symbolic_ssl_details():
 import ssl,json
 from safety_app.infrastructure.provider_transport import RequestTrace
 error=ssl.SSLError('private URL or credential');error.reason='BAD_RECORD_MAC';error.library='SSL'
 result=RequestTrace().diagnostics(error)
 assert result['tls_errors']==[{'reason':'BAD_RECORD_MAC','library':'SSL'}]
 assert 'private' not in json.dumps(result)
 error.reason='https://private.example/token';assert RequestTrace().diagnostics(error)['tls_errors']==[{'library':'SSL'}]
