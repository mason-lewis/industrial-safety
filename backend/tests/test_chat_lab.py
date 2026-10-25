"""No-network tests for comparison permissions, financial guards and release gates."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import httpx
import pytest
from openai import AsyncOpenAI
from pydantic_ai.messages import ModelRequest,UserPromptPart,ModelResponse,TextPart,NativeToolCallPart,NativeToolReturnPart,ThinkingPart
from pydantic_ai.models import ModelRequestParameters,CompletedStreamedResponse
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage
from safety_app.infrastructure.runtime import Runtime
from safety_app.infrastructure.exact_cache import ExactCache
from safety_app.infrastructure.budgets import BudgetBlocked
from safety_app.infrastructure.decisions import classify,ROUTING_QUESTIONS,FINAL_QUESTIONS,DecisionClassificationError
from safety_app.agents.shared.factory import GuardedModel
from safety_app.agents.safety_reviewer.lab_agent import LabOutputRejected,answer_contract
from safety_app.api.auth import Operator
from safety_app.domain.chat_lab import LabComparisonInput,LabAnswer,LabClaim,LabCitation,LabApplicability
from safety_app.modules.safety_review.lab_service import ChatLab,CASES,BranchAudit
from safety_app.modules.safety_review.lab_knowledge import LabKnowledge,freeze
from safety_app.workflows.chat_lab import validate_answer,BranchRunner
from safety_app.workers.runner import execute
ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture
def rt(tmp_path,monkeypatch):
    shutil.copytree(ROOT/'configs',tmp_path/'configs');shutil.copy2(ROOT/'README.md',tmp_path/'README.md')
    monkeypatch.setenv('SAFETY_CHAT_TESTING','1');monkeypatch.setenv('SAFETY_PAID_REVIEW','0');monkeypatch.setenv('OPENAI_API_KEY','')
    value=Runtime(tmp_path)
    config=json.loads((tmp_path/'configs/chat-lab.json').read_text());config['fixture_step_seconds']=.001;(tmp_path/'configs/chat-lab.json').write_text(json.dumps(config))
    return value

def submit(rt,index=0,key='case',order=None):
    case=CASES[index]
    return ChatLab(rt).submit(LabComparisonInput(question=case.question,test_case_id=case.id,idempotency_key=key,**({'order':order} if order else {})),Operator())

@pytest.mark.parametrize('index',range(len(CASES)))
async def test_cases_sequential_frozen_no_paid_calls(rt,index):
    initial=submit(rt,index);lab=ChatLab(rt)
    assert not lab.events(initial.id,'local')[-1].result
    await execute(rt,rt.jobs.claim(['chat_comparison']))
    value=lab.get(initial.id,'local');events=lab.events(value.id,'local')
    assert value.job.status==('failed' if index==9 else 'completed')
    if index==9:
        assert value.branches[0].state=='failed' and value.branches[1].not_started
        return
    assert all(b.state in ('completed','blocked') for b in value.branches) if index!=9 else all(b.state=='failed' for b in value.branches)
    starts=[e for e in events if e.event_type=='agent_started'];assert starts
    first_optimized=next(i for i,e in enumerate(events) if e.branch=='optimized')
    assert any(e.event_type=='branch_finished' and e.branch=='current' for e in events[:first_optimized])
    assert value.snapshot==initial.snapshot and value.versions['prompts']
    with rt.db.connect() as db:assert db.execute('SELECT count(*) FROM reservations').fetchone()[0]==0
    assert not any(e.result for e in events if e.event_type=='model_response')
    assert any(e.event_type=='handoff' for e in events)

async def test_reverse_order_idempotence_and_cursor(rt):
    value=submit(rt,order=['optimized','current']);assert submit(rt,order=['optimized','current']).id==value.id
    await execute(rt,rt.jobs.claim(['chat_comparison']));events=ChatLab(rt).events(value.id,'local')
    assert next(e.branch for e in events if e.branch)=='optimized'
    assert ChatLab(rt).events(value.id,'local',events[4].sequence)==events[5:]
    with pytest.raises(KeyError):ChatLab(rt).events(value.id,'foreign')
    with pytest.raises(KeyError):ChatLab(rt).get(value.id,'foreign')
    with pytest.raises(ValueError):submit(rt,order=['current','optimized'])

async def test_cancel_preserves_job_and_does_not_start_next_branch(rt):
    value=submit(rt,1);job=rt.jobs.claim(['chat_comparison']);rt.jobs.cancel(job.id)
    done=await execute(rt,job);assert done.status=='cancelled'
    result=ChatLab(rt).get(value.id,'local');assert all(b.state=='cancelled' for b in result.branches)
    assert not any(e.event_type=='model_request' for e in ChatLab(rt).events(value.id,'local'))


def test_exact_cache_isolation_invalidation_and_expiry(rt):
    now=[100.];scope={'site':'one','permissions':'read','context':'a','policy':'1','source_version':'1'}
    cache=ExactCache(rt.db,scope,lambda:now[0]);cache.put('classification',{'q':'safe'}, {'scope':'allow'},10)
    assert cache.get('classification',{'q':'safe'})=={'scope':'allow'}
    assert cache.get('classification',{'q':'Safe'}) is None
    for key in scope:assert ExactCache(rt.db,scope|{key:'other'},lambda:now[0]).get('classification',{'q':'safe'}) is None
    now[0]=111;assert cache.get('classification',{'q':'safe'}) is None
    with pytest.raises(ValueError):cache.put('final_answer',{}, {},1)


def test_frozen_record_permissions_and_human_uncertainty(rt):
    rt.db.save('video_safety_case',{'id':'mine','site_id':'local','uncertainty':['occluded']})
    rt.db.save('video_safety_case',{'id':'theirs','site_id':'other','secret':'outside'})
    rt.db.save('decision',{'id':'human','target_id':'mine','decision':'uncertain','reason':'Need another angle'})
    rt.db.save('decision',{'id':'foreign-human','target_id':'theirs','decision':'accepted'})
    snap=freeze(rt,'local',{'operator':'local'});knowledge=LabKnowledge(rt.root,snap,'local')
    result=knowledge.read(['mine']);assert len(result['sources'])==2
    text=json.dumps(result);assert 'occluded' in text and 'Need another angle' in text
    with pytest.raises(ValueError):knowledge.read(['theirs'])
    with pytest.raises(ValueError):knowledge.read(['foreign-human'])
    rt.db.save('video_safety_case',{'id':'mine','site_id':'local','uncertainty':['changed']})
    assert 'occluded' in json.dumps(knowledge.read(['mine']))
    assert freeze(rt,'local',{'operator':'local'})['id']!=snap['id']


def test_claim_gate_does_not_promote_web_summary_or_incomplete_regulation():
    claim=LabClaim(id='a',text='Required',kind='requirement',citation_ids=['x'],exact_clause='1910.178(a)')
    answer=LabAnswer(answer='Required',claims=[claim]);source=LabCitation(id='x',title='test',text='exact',kind='web_summary',version='1',clause='1910.178(a)',context_complete=True,effective_date='2026-01-01')
    applicability=LabApplicability(requirements_allowed=True,jurisdiction='US',basis='test',citation_ids=['x'])
    assert validate_answer(answer,{'x':source},applicability)
    source.kind='requirement';assert not validate_answer(answer,{'x':source},applicability)
    assert validate_answer(answer,{'x':source},None)
    source.context_complete=False;assert validate_answer(answer,{'x':source},applicability)
    assert validate_answer(answer,{},applicability)

async def test_fresh_web_never_uses_discovery_cache(rt,monkeypatch):
    value=submit(rt,2);runner=BranchRunner(rt,value.job,'optimized',None)
    args={'query':value.question,'domains':runner.config['primary_domains']};runner.cache.put('web_discovery',args,[LabCitation(id='web:old',title='Old',text='old',kind='web_summary',version='1').model_dump()],300)
    await runner.web(True);assert not runner.web_evidence
    runner.web_calls=0;await runner.web(False);assert runner.web_evidence[0].id=='web:old'
    with pytest.raises(BudgetBlocked):await runner.web(True)


async def test_low_confidence_final_scope_stays_incomplete_even_for_allow(rt,monkeypatch):
    from safety_app.workflows import answering as chat_lab
    calls=[]
    async def classification(*args,**kwargs):
        calls.append(args)
        return {'scope':{'value':'allow','confidence':.63}}
    monkeypatch.setattr(chat_lab,'classify',classification)
    runner=BranchRunner(rt,submit(rt,6).job,'current',None);runner.paid=True
    result=await runner.decisions(LabAnswer(answer='An on-topic partial answer.',completion='partial'))
    assert result.scope=='clarify' and result.confidence==.63
    assert len(calls)==1 and calls[0][-2]==FINAL_QUESTIONS


@pytest.mark.parametrize('uncertain,scope,task,domain,freshness',[
    ('domain','allow','safety','multiple','unneeded'),
    ('task','allow','unclear','multiple','unneeded'),
    ('freshness','allow','safety','emergency','required'),
    ('scope','clarify','safety','emergency','unneeded'),
])
async def test_decisions_confidence_is_independent_by_question(rt,monkeypatch,uncertain,scope,task,domain,freshness):
    from safety_app.workflows import answering as chat_lab
    async def classification(*args,**kwargs):
        values={'scope':'allow','task':'safety','domain':'emergency','freshness':'unneeded'}
        return {key:{'value':value,'confidence':.26 if key==uncertain else .93} for key,value in values.items()}
    monkeypatch.setattr(chat_lab,'classify',classification)
    runner=BranchRunner(rt,submit(rt,6).job,'optimized',None);runner.paid=True
    value=await runner.decisions()
    assert (value.scope,value.task,value.domain,value.freshness)==(scope,task,domain,freshness)
    assert value.confidence==(.26 if uncertain=='scope' else .93)
    assert value.policy_version==runner.config['policy_version']

@pytest.mark.parametrize('naming',['named','omitted','null','mixed','reordered'])
async def test_decisions_sdk_wire_cache_and_usage(rt,naming):
    value=submit(rt);job=value.job;seen=[]
    def handler(request):
        assert request.url.host=='mock.invalid' and request.url.path.endswith('/decisions')
        body=json.loads(request.content);seen.append(body)
        assert body['model']=='gpt-6-luna' and len(body['questions'])==4
        assert body['input'][0]['content'][0]['prompt_cache_breakpoint']=={'mode':'explicit'}
        answers=[{'type':'choice','name':q['name'],'choice':q['choices'][0]['value'],'confidence':.99,'probabilities':[]} for q in body['questions']]
        if naming=='omitted':
            for answer in answers:answer.pop('name')
        elif naming=='null':
            for answer in answers:answer['name']=None
        elif naming=='mixed':answers[1].pop('name')
        elif naming=='reordered':answers.reverse()
        return httpx.Response(200,json=decision_response(answers))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=AsyncOpenAI(api_key='fixture',base_url='https://mock.invalid/v1',http_client=http,max_retries=0)
        result=await classify(client,rt.budgets,BranchAudit(rt.audit,job,'optimized',api='decisions'),job,'decision-node','policy'*300,'question',ROUTING_QUESTIONS,lambda:False)
    assert result['scope']['value']=='allow' and len(seen)==1
    with rt.db.connect() as db:row=db.execute('SELECT actual,state FROM reservations').fetchone()
    assert row['actual']==85 and row['state']=='settled' # 700*.1 + 200*.01 + 100*.125 microdollars
    event=next(e for e in ChatLab(rt).events(job.id,'local') if e.event_type=='model_response')
    assert event.resources['usage_cost_usd']==pytest.approx(.0000845)
    assert all(check['status']=='completed' for check in event.resources['classification_checks'])


def decision_response(answers):
    return {'model':'gpt-6-luna','answers':answers,'usage':{'input_tokens':1000,'input_tokens_details':{'cached_tokens':200,'cache_write_tokens':100},'output_tokens':0,'output_tokens_details':{'reasoning_tokens':0},'total_tokens':1000}}


@pytest.mark.parametrize('malformation,reason',[
    ('refusal','scope: provider refused'),
    ('missing','scope: provider omitted'),
    ('duplicate','duplicate question names'),
    ('unknown','unknown or duplicate question names'),
    ('short_unnamed','complete question order'),
    ('reordered_mixed','complete question order'),
    ('predicate','scope: provider returned a non-choice'),
    ('choice','scope: provider returned an unsupported choice'),
    ('confidence','scope: provider returned invalid confidence'),
])
async def test_decisions_invalid_response_retains_cost_and_identifies_check(rt,malformation,reason):
    job=submit(rt).job;requests=[]
    def handler(request):
        requests.append(request)
        answers=[{'type':'choice','name':q['name'],'choice':q['choices'][0]['value'],'confidence':.99,'probabilities':[]} for q in ROUTING_QUESTIONS]
        if malformation=='refusal':answers[0]={'type':'refusal','name':'scope'}
        elif malformation=='missing':answers.pop(0)
        elif malformation=='duplicate':answers[1]['name']='scope'
        elif malformation=='unknown':answers[0]['name']='not-requested'
        elif malformation=='short_unnamed':answers.pop();answers[0].pop('name')
        elif malformation=='reordered_mixed':answers.reverse();answers[0].pop('name')
        elif malformation=='predicate':answers[0]={'type':'predicate','name':'scope','probability':1}
        elif malformation=='choice':answers[0]['choice']=True
        elif malformation=='confidence':answers[0]['confidence']=1.01
        return httpx.Response(200,json=decision_response(answers))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=AsyncOpenAI(api_key='fixture',base_url='https://mock.invalid/v1',http_client=http,max_retries=0)
        with pytest.raises(DecisionClassificationError,match=reason):
            await classify(client,rt.budgets,BranchAudit(rt.audit,job,'optimized',api='decisions'),job,'decision-node','policy'*300,'question',ROUTING_QUESTIONS,lambda:False)
    assert len(requests)==1
    events=ChatLab(rt).events(job.id,'local');responses=[e for e in events if e.event_type=='model_response']
    assert len(responses)==1 and responses[0].state=='failed' and responses[0].result is None
    assert any(check['status']!='completed' for check in responses[0].resources['classification_checks'])
    usage=ChatLab(rt).get(job.id,'local').usage
    assert usage.decisions_usd==pytest.approx(.0000845) and usage.input_tokens==1000
    assert usage.cached_input_tokens==200 and usage.cache_write_tokens==100
    assert usage.reserved_usd==0 and usage.unknown_requests==0
    with rt.db.connect() as db:assert db.execute('SELECT actual,state FROM reservations').fetchone()['state']=='settled'


async def test_mixed_request_unnamed_decisions_clarifies_without_answering(rt):
    value=submit(rt,5);calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        values={'scope':'clarify','task':'mixed','domain':'applicability','freshness':'unneeded'}
        return httpx.Response(200,json=decision_response([{'type':'choice','choice':values[q['name']],'confidence':.99,'probabilities':[]} for q in calls[-1]['questions']]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=AsyncOpenAI(api_key='fixture',base_url='https://mock.invalid/v1',http_client=http,max_retries=0)
        runner=BranchRunner(rt,value.job,'optimized',client);runner.paid=True
        route,answer,challenge,citations=await runner.run()
    assert route.scope=='clarify' and answer.completion=='clarification'
    assert len(calls)==1 and runner.calls==0 and challenge is None and citations==[]


@pytest.mark.parametrize('final',[False,True])
async def test_refused_decisions_never_cached_or_retried(rt,final):
    value=submit(rt);calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        return httpx.Response(200,json=decision_response([{'type':'refusal','name':q['name']} for q in body['questions']]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=AsyncOpenAI(api_key='fixture',base_url='https://mock.invalid/v1',http_client=http,max_retries=0)
        runner=BranchRunner(rt,value.job,'optimized',client);runner.paid=True
        with pytest.raises(DecisionClassificationError,match='scope: provider refused'):
            await runner.decisions(LabAnswer(answer='Unchecked draft') if final else None)
    assert len(calls)==1 and len(calls[0]['questions'])==len(FINAL_QUESTIONS if final else ROUTING_QUESTIONS)
    events=ChatLab(rt).events(value.id,'local')
    assert not any(e.event_type=='classification_completed' for e in events)
    assert events[-1].state=='failed' and 'Unchecked draft' not in json.dumps([e.model_dump() for e in events])
    with rt.db.connect() as db:assert db.execute('SELECT count(*) FROM chat_exact_cache').fetchone()[0]==0


@pytest.mark.parametrize('scope,confidence,outcome',[
    ('block',.99,'declined'),('clarify',.99,'clarification'),('block',.3,'clarification'),
])
@pytest.mark.parametrize('task_response',['refused','missing'])
async def test_terminal_scope_does_not_require_unused_routing_checks(rt,scope,confidence,outcome,task_response):
    """Replay the recorded task refusal shape; the discarded scope value is simulated."""
    job=submit(rt,4).job;calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        answers=[{'type':'choice','name':'scope','choice':scope,'confidence':confidence,'probabilities':[]}]
        if task_response=='refused':answers.append({'type':'refusal','name':'task'})
        for name,value in [('domain','applicability'),('freshness','unneeded')]:
            answers.append({'type':'choice','name':name,'choice':value,'confidence':.99,'probabilities':[]})
        return httpx.Response(200,json=decision_response(answers))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=AsyncOpenAI(api_key='fixture',base_url='https://mock.invalid/v1',http_client=http,max_retries=0)
        runner=BranchRunner(rt,job,'optimized',client);runner.paid=True
        route,answer,challenge,citations=await runner.run()
    assert answer.outcome==outcome and route.scope!='allow'
    assert len(calls)==1 and runner.calls==0 and challenge is None and citations==[]
    assert runner.recovery.attempts==0
    events=ChatLab(rt).events(job.id,'local')
    response=next(e for e in events if e.event_type=='model_response')
    assert response.resources['routing_checks_not_needed'] is True
    assert response.resources['classification_checks'][1]['status'] in ('refused','missing')
    assert not any(e.event_type=='tool_started' for e in events)
    assert ChatLab(rt).get(job.id,'local').usage.unknown_requests==0


@pytest.mark.parametrize('scope_response',['allow','refusal','duplicate'])
async def test_incomplete_route_cannot_grant_permission(rt,scope_response):
    job=submit(rt).job;calls=[]
    def handler(request):
        calls.append(1)
        answers=[{'type':'choice','name':'scope','choice':'allow','confidence':.99,'probabilities':[]},
                 {'type':'refusal','name':'task'}]
        if scope_response=='refusal':answers[0]={'type':'refusal','name':'scope'}
        if scope_response=='duplicate':answers.append({'type':'choice','name':'scope','choice':'block','confidence':.99,'probabilities':[]})
        return httpx.Response(200,json=decision_response(answers))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        runner=BranchRunner(rt,job,'optimized',AsyncOpenAI(api_key='fixture',base_url='https://mock.invalid/v1',http_client=http,max_retries=0));runner.paid=True
        with pytest.raises(DecisionClassificationError):await runner.run()
    assert calls==[1] and runner.calls==0
    with rt.db.connect() as db:assert db.execute('SELECT count(*) FROM chat_exact_cache').fetchone()[0]==0

async def test_decisions_timeout_no_retry_holds_unknown(rt):
    job=submit(rt).job;calls=[]
    async def fail(**kwargs):calls.append(1);raise TimeoutError('fixture timeout')
    client=SimpleNamespace(decisions=SimpleNamespace(create=fail))
    with pytest.raises(TimeoutError):await classify(client,rt.budgets,BranchAudit(rt.audit,job,'optimized',api='decisions'),job,'dec','p','q',ROUTING_QUESTIONS,lambda:False)
    assert calls==[1]
    assert rt.budgets.status()['outstanding'][0]['state']=='unknown'

async def test_decisions_admission_503_stops_without_retry_or_invented_usage(rt):
    from openai import InternalServerError
    job=submit(rt,4).job;calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(503,json={'error':{'message':'Decision admission timed out.','type':'server_error','param':None,'code':None}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        runner=BranchRunner(rt,job,'optimized',AsyncOpenAI(api_key='fixture',base_url='https://mock.invalid/v1',http_client=http,max_retries=0));runner.paid=True
        with pytest.raises(InternalServerError,match='Decision admission timed out'):await runner.run()
    assert len(calls)==1 and runner.calls==0 and runner.recovery.attempts==0
    usage=ChatLab(rt).get(job.id,'local').usage
    assert usage.unknown_requests==1 and usage.reserved_usd>0 and usage.calculated_usd==0
    with rt.db.connect() as db:assert db.execute('SELECT count(*) FROM chat_exact_cache').fetchone()[0]==0

async def test_stream_entry_is_guarded_and_cancellation_holds_unknown(rt):
    calls=[]
    async def respond(messages,info):calls.append(1);return ModelResponse(parts=[TextPart('OK')],model_name='gpt-6-luna',usage=RequestUsage(input_tokens=10,output_tokens=2))
    guard=GuardedModel(FunctionModel(respond,model_name='gpt-6-luna'),rt.budgets,rt.audit,'node','parent',paid=True)
    async with guard.request_stream([ModelRequest(parts=[UserPromptPart('test')])],{},ModelRequestParameters()) as stream:
        async for _ in stream:pass
        assert stream.get().parts[0].content=='OK'
    assert calls==[1]
    rt.budgets.switch(True)
    with pytest.raises(BudgetBlocked):
        async with guard.request_stream([ModelRequest(parts=[UserPromptPart('test')])],{},ModelRequestParameters()):pass
    assert calls==[1]

async def test_native_stream_events_and_search_fee_accounted(rt):
    response=ModelResponse(parts=[ThinkingPart('PRIVATE CHAIN'),NativeToolCallPart('web_search',{'query':'safety'},tool_call_id='web1'),NativeToolReturnPart('web_search',{'sources':[{'url':'https://osha.gov/'}]},tool_call_id='web1'),TextPart('Source summary')],model_name='gpt-6-luna',usage=RequestUsage(input_tokens=100,output_tokens=20,details={'web_search_requests':1}))
    model=FunctionModel(lambda *_:response,model_name='gpt-6-luna')
    @asynccontextmanager
    async def stream(*args,**kwargs):yield CompletedStreamedResponse(response,model_request_parameters=ModelRequestParameters(),replay_events=True)
    model.request_stream=stream
    guard=GuardedModel(model,rt.budgets,rt.audit,'native','parent',paid=True,configuration={'observable_stream':True,'native_fee_allowance':.01,'native_search_price':.01,'native_input_allowance':32000})
    await guard.request([ModelRequest(parts=[UserPromptPart('search')])],{},ModelRequestParameters())
    events=rt.audit.rows();assert 'PRIVATE CHAIN' not in json.dumps(events)
    assert any(e['event_type']=='native_tool_started' for e in events) and any(e['event_type']=='native_tool_finished' for e in events)
    response_event=next(e for e in events if e['event_type']=='model_response');assert response_event['resources']['search_usd']==.01
    with rt.db.connect() as db:row=db.execute('SELECT actual FROM reservations').fetchone()
    assert row['actual']>=10020

async def test_parent_budget_shared_between_branches(rt):
    rt.config['limits']['workflow_usd']=.001
    a=rt.budgets.reserve('current','parent','gpt-6-luna',2000,0);rt.budgets.reconcile(a,.0006,2000,0)
    with pytest.raises(BudgetBlocked,match='workflow_id'):rt.budgets.reserve('optimized','parent','gpt-6-luna',2000,0)


def test_disabled_unauthenticated_foreign_site_and_export(rt,monkeypatch):
    from fastapi.testclient import TestClient
    from safety_app.api.video_app import app
    monkeypatch.setattr('safety_app.infrastructure.runtime._default',rt)
    client=TestClient(app);assert client.get('/api/chat-lab/cases').status_code==401
    assert client.post('/api/auth/session',json={}).status_code==200
    assert client.get('/api/chat-lab/cases').status_code==200
    with rt.db.connect() as db:assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    value=submit(rt);assert client.get(f'/api/chat-lab/comparisons/{value.id}/export').status_code==200
    assert client.get(f'/api/chat-lab/comparisons/{value.id}/events',headers={'Last-Event-ID':'bad'}).status_code==400
    monkeypatch.setenv('SAFETY_CHAT_TESTING','0');assert client.get('/test/chat').status_code==404
    assert client.get('/api/chat-lab/cases').status_code==404
    assert client.get('/api/chat-lab/capabilities').json()['enabled'] is False


def test_hosted_unavailable_never_queues(rt):
    with pytest.raises(ValueError,match='Hosted'):ChatLab(rt).submit(LabComparisonInput(question='Safety?',mode='hosted',idempotency_key='unavailable'),Operator())
    assert not rt.jobs.list()

async def test_midrequest_cancellation_no_paid_retry(rt):
    started=asyncio.Event();cancel=[False];calls=[]
    async def respond(*_):calls.append(1);started.set();await asyncio.sleep(10)
    guard=GuardedModel(FunctionModel(respond,model_name='gpt-6-luna'),rt.budgets,rt.audit,'cancel','parent',paid=True,cancelled=lambda:cancel[0])
    task=asyncio.create_task(guard.request([ModelRequest(parts=[UserPromptPart('test')])],{},ModelRequestParameters()))
    await started.wait();cancel[0]=True
    with pytest.raises(asyncio.CancelledError):await task
    assert calls==[1] and rt.budgets.status()['outstanding'][0]['state']=='unknown'

async def test_stream_without_terminal_usage_fails_closed(rt):
    response=ModelResponse(parts=[TextPart('Incomplete draft')],model_name='gpt-6-luna',state='interrupted')
    model=FunctionModel(lambda *_:response,model_name='gpt-6-luna')
    @asynccontextmanager
    async def stream(*args,**kwargs):yield CompletedStreamedResponse(response,model_request_parameters=ModelRequestParameters(),replay_events=True)
    model.request_stream=stream
    guard=GuardedModel(model,rt.budgets,rt.audit,'broken','parent',paid=True,configuration={'observable_stream':True})
    with pytest.raises(RuntimeError,match='terminal usage'):await guard.request([ModelRequest(parts=[UserPromptPart('test')])],{},ModelRequestParameters())
    assert rt.budgets.status()['outstanding'][0]['state']=='unknown'
    assert 'Incomplete draft' not in json.dumps(rt.audit.rows())

async def test_failed_final_scope_never_releases_answer(rt,monkeypatch):
    from safety_app.domain.chat_lab import LabScopeResult
    original=BranchRunner.decisions
    async def reject(self,final=None):
        if final:return LabScopeResult(scope='block',confidence=1)
        return await original(self,final)
    monkeypatch.setattr(BranchRunner,'decisions',reject)
    value=submit(rt);await execute(rt,rt.jobs.claim(['chat_comparison']))
    result=ChatLab(rt).get(value.id,'local')
    assert all(b.state=='completed' and b.answer.outcome=='clarification' and not b.answer.claims for b in result.branches)


@pytest.mark.parametrize('length',[970,2000])
async def test_applicability_explanation_preserved_within_bounded_contract(rt,length):
    value=submit(rt,6);runner=BranchRunner(rt,value.job,'current',None)
    basis=('Jurisdiction and the effective source version remain unknown. '*40)[:length]
    applicability=LabApplicability(basis=basis,missing_context=['Jurisdiction and work setting require confirmation.'])
    result=await runner.stage('Applicability specialist',LabApplicability,{'task':'Determine applicability.'},applicability)
    assert result.basis==basis and result.missing_context==applicability.missing_context
    assert result.requirements_allowed is False and result.jurisdiction=='unknown'


async def test_schema_failure_names_field_without_draft_after_one_correction(rt):
    value=submit(rt,6);runner=BranchRunner(rt,value.job,'current',None)
    unchecked='UNRELEASED-DRAFT '*150
    # Construct invalid fixture output to exercise PydanticAI's actual exception chain.
    fixture=LabApplicability.model_construct(basis=unchecked)
    with pytest.raises(LabOutputRejected,match='Applicability specialist: basis: String should have at most 2000 characters') as failure:
        await runner.stage('Applicability specialist',LabApplicability,{'task':'Determine applicability.'},fixture)
    assert 'UNRELEASED-DRAFT' not in str(failure.value)
    events=ChatLab(rt).events(value.id,'local')
    assert sum(e.event_type=='model_request' for e in events)==2
    assert runner.recovery.attempts==1
    event=next(e for e in events if e.event_type=='validation_failed')
    assert event.resources['validation_issues'][0]['field']=='basis'
    assert event.resources['validation_issues'][0]['code']=='string_too_long'
    assert event.resources['output_retry_limit']==0
    assert 'UNRELEASED-DRAFT' not in json.dumps([e.model_dump() for e in events])


@pytest.mark.parametrize('applicability',[None,{'requirements_allowed':False,'jurisdiction':'unknown'}])
async def test_unresolved_applicability_excludes_requirement_from_provider_schema(rt,monkeypatch,applicability):
    from pydantic_ai.messages import ToolCallPart
    from safety_app.workflows import answering as chat_lab
    seen=[]
    async def respond(messages,info):
        schema=info.output_tools[0].parameters_json_schema;seen.append(schema)
        assert info.output_tools[0].strict is True
        claim_schema=schema['$defs']['NonRequirementClaim']
        assert 'requirement' not in claim_schema['properties']['kind']['enum']
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,{'answer':'Applicability remains unresolved.','claims':[{'id':'gap','text':'Jurisdiction has not been established.','kind':'uncertain'}],'completion':'partial'})],model_name='gpt-6-luna')
    monkeypatch.setattr(chat_lab,'TestModel',lambda **_:FunctionModel(respond,model_name='gpt-6-luna'))
    runner=BranchRunner(rt,submit(rt,6).job,'current',None)
    value=await runner.stage('Emergency specialist',LabAnswer,{'applicability':applicability},LabAnswer(answer='Fixture'))
    assert len(seen)==1 and value.completion=='partial'
    allowed=answer_contract(LabAnswer,{'requirements_allowed':True}).model_json_schema()
    assert 'requirement' in allowed['$defs']['EvidenceClaim']['properties']['kind']['enum']


def test_answer_contract_requires_citations_for_guidance_even_in_emergencies():
    from pydantic import ValidationError
    for applicability in (None,{'requirements_allowed':False},{'requirements_allowed':True}):
        contract=answer_contract(LabAnswer,applicability)
        with pytest.raises(ValidationError):
            contract.model_validate({'answer':'Unchecked advice','claims':[{'id':'aid','text':'Uncited instruction','kind':'guidance'}]})
        value=contract.model_validate({'answer':'A condition is unknown.','claims':[{'id':'gap','text':'A condition is unknown.','kind':'uncertain'}]})
        assert value.claims[0].citation_ids==[]


@pytest.mark.parametrize('allowed',[False,True])
async def test_safety_stage_cannot_misclassify_reference_limits_as_saved_records(rt,monkeypatch,allowed):
    from pydantic_ai.messages import ToolCallPart
    from safety_app.workflows import answering as chat_lab
    async def respond(messages,info):
        schema=info.output_tools[0].parameters_json_schema
        definitions=schema['$defs']
        claim=definitions['SafetyClaim' if allowed else 'GuidanceClaim']['properties']['kind']
        assert set(claim.get('enum',[claim.get('const')]))==({'requirement','guidance'} if allowed else {'guidance'})
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,{'answer':'The source does not establish applicability.','claims':[{'id':'gap','text':'Applicability remains unresolved.','kind':'uncertain'}],'completion':'partial'})],model_name='gpt-6-luna')
    monkeypatch.setattr(chat_lab,'TestModel',lambda **_:FunctionModel(respond,model_name='gpt-6-luna'))
    runner=BranchRunner(rt,submit(rt,6).job,'current',None)
    value=await runner.stage('Emergency specialist',LabAnswer,{'question_type':'safety','applicability':{'requirements_allowed':allowed}},LabAnswer(answer='Fixture'))
    assert value.claims[0].kind=='uncertain'
    # Product and saved-review questions still retain their source-specific categories.
    assert 'record' in answer_contract(LabAnswer,None,'records').model_json_schema()['$defs']['NonRequirementClaim']['properties']['kind']['enum']


async def test_challenge_receives_final_synthesis_citations(rt,monkeypatch):
    from safety_app.domain.chat_lab import LabChallengeAssessment,LabClaimCheck
    runner=BranchRunner(rt,submit(rt,1).job,'current',None)
    original_stage=runner.stage
    async def stage(name,output_type,payload,fixture,**kwargs):
        if name=='Applicability specialist':
            runner.knowledge.seen['applicability-source']=LabCitation(id='applicability-source',title='Applicability evidence',text='Context requires verification.',kind='guidance',version='1')
            return LabApplicability(basis='Context requires verification.',citation_ids=['applicability-source'])
        if name=='Answer synthesis':
            runner.knowledge.seen['new']=LabCitation(id='new',title='Retrieved during synthesis',text='Supported advice.',kind='guidance',version='1')
            return LabAnswer(answer='Supported advice.',claims=[LabClaim(id='final',text='Supported advice.',kind='guidance',citation_ids=['new'])])
        if name=='Independent challenge':
            assert {'new','applicability-source'}<={item['id'] for item in payload['evidence']}
            return LabChallengeAssessment(claim_checks=[LabClaimCheck(claim_id='final',verdict='supported',independently_publishable=True,note='The cited source supports this claim.')])
        return await original_stage(name,output_type,payload,fixture,**kwargs)
    monkeypatch.setattr(runner,'stage',stage)
    _,answer,challenge,_=await runner.run()
    assert challenge.approved and answer.claims[0].citation_ids==['new']

async def test_sse_replays_only_sequences_after_cursor(rt,monkeypatch):
    from fastapi.testclient import TestClient
    from safety_app.api.video_app import app
    monkeypatch.setattr('safety_app.infrastructure.runtime._default',rt)
    value=submit(rt);await execute(rt,rt.jobs.claim(['chat_comparison']))
    all_events=ChatLab(rt).events(value.id,'local');cursor=all_events[-3].sequence
    client=TestClient(app);client.post('/api/auth/session',json={})
    response=client.get(f'/api/chat-lab/comparisons/{value.id}/events',headers={'Last-Event-ID':str(cursor)})
    assert response.status_code==200
    ids=[int(x[4:]) for x in response.text.splitlines() if x.startswith('id: ')]
    assert ids==[e.sequence for e in all_events if e.sequence>cursor] and 'event: settled' in response.text


def test_long_context_search_and_cache_cost_buckets():
    from safety_app.infrastructure.usage_cost import token_cost
    usage=RequestUsage(input_tokens=300000,output_tokens=100,cache_read_tokens=100000,cache_write_tokens=1000,details={'cache_write_30m_tokens':1000})
    price={'input_per_million':.1,'output_per_million':.5,'cached_input_per_million':.01,'cache_write_30m_per_million':.125,'cache_write_12h_per_million':.25}
    assert token_cost(usage,price)['usage_cost_usd']==pytest.approx(.042125)


def test_current_conflicting_references_remain_distinct(rt):
    from safety_app.domain.models import SourceExcerpt
    for id,text in [('one','Rule A'),('two','Conflicting Rule B')]:
        rt.db.save('source',SourceExcerpt(id=id,source_id=id,title='Conflicting rule',text=text,section='test',version=id,jurisdiction='US',site_id='local',source_type='guidance'),immutable=True)
    snap=freeze(rt,'local',{'operator':'one'});knowledge=LabKnowledge(rt.root,snap,'local')
    result=knowledge.read(['one','two']);assert len(result['sources'])==2 and result['sources'][0]['version']!=result['sources'][1]['version']

@pytest.mark.parametrize('cache_write_tokens',[0,4736])
async def test_responses_native_web_sdk_stream_wire_and_provenance(rt,monkeypatch,cache_write_tokens):
    from pydantic_ai import models,Agent
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.messages import CachePoint
    monkeypatch.setattr(models,'ALLOW_MODEL_REQUESTS',True) # transport below cannot reach a network
    captured=[]
    def handler(request):
        assert request.url.host=='mock.invalid';body=json.loads(request.content);captured.append(body)
        assert body['stream'] is True and body['max_tool_calls']==1
        assert body['tools'][0]['type']=='web_search'
        assert body['prompt_cache_options']=={'mode':'explicit','ttl':'30m'}
        assert body['input'][0]['content'][0]['prompt_cache_breakpoint']=={'mode':'explicit'}
        web={'type':'web_search_call','id':'ws_1','status':'completed','action':{'type':'search','query':'OSHA','sources':[{'type':'url','url':'https://www.osha.gov/laws-regs'}]}}
        message={'type':'message','id':'msg_1','role':'assistant','status':'completed','content':[{'type':'output_text','text':'Source summary','annotations':[]}]}
        base={'id':'resp_1','object':'response','created_at':0,'model':'gpt-6-luna','status':'in_progress','output':[]}
        events=[{'type':'response.created','response':base},
          {'type':'response.output_item.added','output_index':0,'item':{**web,'status':'in_progress'}},
          {'type':'response.output_item.done','output_index':0,'item':web},
          {'type':'response.output_item.added','output_index':1,'item':{**message,'content':[],'status':'in_progress'}},
          {'type':'response.output_text.delta','output_index':1,'content_index':0,'item_id':'msg_1','delta':'Source summary','logprobs':[]},
          {'type':'response.output_item.done','output_index':1,'item':message},
          {'type':'response.completed','response':{**base,'status':'completed','output':[web,message],'usage':{'input_tokens':8959,'output_tokens':482,'total_tokens':9441,'input_tokens_details':{'cached_tokens':0,'cache_write_tokens':cache_write_tokens,'cache_write_tokens_details':{'ttl_30m_tokens':cache_write_tokens,'ttl_12h_tokens':0}},'output_tokens_details':{'reasoning_tokens':174}},'tool_usage':{'web_search':{'num_requests':1}}}}]
        payload=''.join('event: '+e['type']+'\ndata: '+json.dumps(e|{'sequence_number':i})+'\n\n' for i,e in enumerate(events))
        return httpx.Response(200,content=payload,headers={'Content-Type':'text/event-stream'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=AsyncOpenAI(api_key='fixture',base_url='https://mock.invalid/v1',http_client=http,max_retries=0)
        model=OpenAIResponsesModel('gpt-6-luna',provider=OpenAIProvider(openai_client=client))
        guard=GuardedModel(model,rt.budgets,rt.audit,'wire','parent',paid=True,configuration={'workflow_kind':'chat_comparison','observable_stream':True,'native_fee_allowance':.01,'native_search_price':.01,'native_input_allowance':32000})
        agent=Agent(guard,retries=0)
        result=await agent.run(['Stable prefix',CachePoint(),'OSHA question'],model_settings={'openai_native_tools':[{'type':'web_search','search_context_size':'low','filters':{'allowed_domains':['osha.gov']}}],'openai_include_web_search_sources':True,'openai_include_raw_annotations':True,'openai_prompt_cache_options':{'mode':'explicit','ttl':'30m'},'extra_body':{'max_tool_calls':1,'tool_choice':'required'}})
    assert result.output=='Source summary' and len(captured)==1
    job=submit(rt,2).job;runner=BranchRunner(rt,job,'current',None);runner.capture_web(result)
    assert runner.web_evidence[0].url is None and runner.web_evidence[0].kind=='web_summary'
    assert runner.web_evidence[0].provenance['sources'][0]['url']=='https://www.osha.gov/laws-regs'
    assert any(e['event_type']=='native_tool_started' for e in rt.audit.rows())
    event=next(e for e in rt.audit.rows(event_type='model_response') if e['run_id']=='wire')
    resources=event['resources']
    assert resources['cache_write_tokens']==cache_write_tokens
    assert resources['cache_write_30m_tokens']==cache_write_tokens
    assert resources['usage_cost_usd']==pytest.approx(.01+((8959-cache_write_tokens)*.1+cache_write_tokens*.125+482*.5)/1e6)
    assert not rt.budgets.status()['outstanding']


def test_cache_write_lifetime_requires_evidence_and_preserves_invalid_breakdown():
    from safety_app.infrastructure.usage_cost import token_cost
    price={'input_per_million':.1,'output_per_million':.5,'cached_input_per_million':.01,'cache_write_30m_per_million':.125,'cache_write_12h_per_million':.25}
    usage=RequestUsage(input_tokens=1000,output_tokens=100,cache_write_tokens=500)
    assert token_cost(usage,price)['usage_cost_usd'] is None
    short=token_cost(usage,price,single_cache_write_ttl='30m')
    assert short['usage_cost_usd']==pytest.approx(.0001625)
    assert short['cache_write_breakdown_basis']=='uniform_request_ttl:30m'
    assert token_cost(usage,price,single_cache_write_ttl='12h')['usage_cost_usd']==pytest.approx(.000225)
    usage.details={'cache_write_30m_tokens':0,'cache_write_12h_tokens':500}
    assert token_cost(usage,price,single_cache_write_ttl='30m')['usage_cost_usd']==pytest.approx(.000225)
    usage.details={'cache_write_30m_tokens':0,'cache_write_12h_tokens':0}
    assert token_cost(usage,price,single_cache_write_ttl='30m')['usage_cost_usd'] is None


def recorded_review():
    return json.loads((Path(__file__).parent/'fixtures/chat_lab_review_wording.json').read_text())


def review_checks(answer):
    from safety_app.domain.chat_lab import LabChallengeAssessment,LabClaimCheck
    return LabChallengeAssessment(claim_checks=[LabClaimCheck(claim_id=c.id,verdict='uncertain' if c.kind=='uncertain' else 'supported',note='Simulated verification finding.') for c in answer.claims])


def test_recorded_wording_failure_has_separate_nonblocking_observations():
    from safety_app.agents.challenge_reviewer.lab_checks import evaluate_challenge
    recorded=recorded_review();answer=LabAnswer.model_validate(recorded['answer'])
    assert recorded['original_review']['approved'] is False
    assessment=review_checks(answer)
    assessment.observations=recorded['original_review']['issues'][:4]
    result=evaluate_challenge(answer,assessment)
    assert result.approved and not result.issues and not result.failure_codes
    assert result.checked_claim_ids==['g1','g2','u1','u2']
    assert result.unresolved_claims==['u1','u2']
    assert result.observations==assessment.observations


@pytest.mark.parametrize('failure',['missing','duplicate','unknown','unsupported','contradicted','uncertain_guidance','unknown_issue'])
def test_review_requires_complete_supported_claim_checks(failure):
    from safety_app.domain.chat_lab import LabReviewIssue
    from safety_app.agents.challenge_reviewer.lab_checks import evaluate_challenge
    answer=LabAnswer.model_validate(recorded_review()['answer']);assessment=review_checks(answer)
    if failure=='missing':assessment.claim_checks.pop()
    elif failure=='duplicate':assessment.claim_checks.append(assessment.claim_checks[0])
    elif failure=='unknown':assessment.claim_checks[0].claim_id='invented'
    elif failure=='unknown_issue':assessment.blocking_issues=[LabReviewIssue(code='contradiction',claim_id='invented',detail='Unchecked replacement advice.')]
    else:assessment.claim_checks[0].verdict='uncertain' if failure=='uncertain_guidance' else failure
    assessment.observations=['Unchecked replacement advice.']
    assessment.claim_checks[0].note='Unchecked replacement advice.'
    result=evaluate_challenge(answer,assessment)
    assert not result.approved and result.failure_codes and result.issues
    assert 'Unchecked replacement advice.' not in result.model_dump_json()
    assert not result.claim_checks and not result.observations


@pytest.mark.parametrize('code',['unsupported_advice','contradiction','missing_claim','unverified_requirement','scope_violation','citation_mismatch','record_uncertainty_lost'])
def test_review_blocks_substantive_narrative_defects_with_supported_claims(code):
    from safety_app.domain.chat_lab import LabReviewIssue
    from safety_app.agents.challenge_reviewer.lab_checks import evaluate_challenge
    answer=LabAnswer.model_validate(recorded_review()['answer']);assessment=review_checks(answer)
    assessment.blocking_issues=[LabReviewIssue(code=code,detail='Unsafe draft quote stays private.')]
    result=evaluate_challenge(answer,assessment)
    assert not result.approved and result.failure_codes==[code]
    assert 'Unsafe draft' not in result.model_dump_json()


async def test_challenge_assessment_uses_real_pydanticai_parser_once(rt,monkeypatch):
    from pydantic_ai.messages import ToolCallPart
    from safety_app.domain.chat_lab import LabChallengeAssessment
    from safety_app.workflows import answering as chat_lab
    from safety_app.agents.challenge_reviewer.lab_checks import evaluate_challenge
    answer=LabAnswer.model_validate(recorded_review()['answer']);assessment=review_checks(answer);calls=[]
    async def respond(messages,info):
        calls.append(1);schema=info.output_tools[0].parameters_json_schema
        assert 'approved' not in schema['properties']
        assert {'claim_checks','blocking_issues','observations'}<=schema['properties'].keys()
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,assessment.model_dump())],model_name='gpt-6-luna')
    monkeypatch.setattr(chat_lab,'TestModel',lambda **_:FunctionModel(respond,model_name='gpt-6-luna'))
    runner=BranchRunner(rt,submit(rt,6).job,'current',None)
    value=await runner.stage('Independent challenge',LabChallengeAssessment,{'answer':answer.model_dump()},assessment,challenge=True)
    assert evaluate_challenge(answer,value).approved and len(calls)==1


async def test_review_hold_stops_final_scope_and_exposes_only_safe_categories(rt,monkeypatch):
    from safety_app.domain.chat_lab import LabChallengeAssessment,LabReviewIssue
    original_stage=BranchRunner.stage
    async def stage(self,name,output_type,payload,fixture,**kwargs):
        if kwargs.get('challenge'):
            return LabChallengeAssessment(claim_checks=[],blocking_issues=[LabReviewIssue(code='unsupported_advice',detail='Private unchecked draft.')],observations=['Private unchecked draft.'])
        return await original_stage(self,name,output_type,payload,fixture,**kwargs)
    monkeypatch.setattr(BranchRunner,'stage',stage)
    initial=submit(rt,6);await execute(rt,rt.jobs.claim(['chat_comparison']))
    value=ChatLab(rt).get(initial.id,'local');events=ChatLab(rt).events(initial.id,'local')
    assert all(b.state=='completed' and b.challenge.approved is False for b in value.branches)
    assert all(b.answer.outcome=='clarification' and b.answer.missing_context for b in value.branches)
    assert all(not b.answer.claims and b.recovery.attempts==1 for b in value.branches)
    assert all(e.agent!='Final scope check' for e in events)
    assert any(e.event_type=='challenge_checked' and e.state=='blocked' for e in events)
    assert 'Private unchecked draft.' not in value.model_dump_json()+json.dumps([e.model_dump(mode='json') for e in events])
    with rt.db.connect() as db:assert db.execute('SELECT count(*) FROM reservations').fetchone()[0]==0


def test_saved_legacy_challenge_still_loads_without_new_fields():
    from safety_app.domain.chat_lab import LabChallenge
    value=LabChallenge.model_validate(recorded_review()['original_review'])
    assert not value.approved and not value.failure_codes and not value.claim_checks


def test_product_record_draft_schemas_reject_cross_category_claims():
    from safety_app.agents.safety_reviewer.lab_agent import draft_contract
    from pydantic import ValidationError
    for task,wrong in [('product','record'),('records','product')]:
        schema=draft_contract(task,None)
        with pytest.raises(ValidationError):schema.model_validate({'claims':[{'id':'x','text':'Wrong category','kind':wrong,'citation_ids':['x']}]})
        value=schema.model_validate({'claims':[{'id':'x','text':'Documented fact','kind':task if task=='product' else 'record','citation_ids':['x']}]})
        assert value.claims[0].citation_ids==['x']


def test_partial_release_closes_dependencies_and_never_displays_rejected_prose():
    from safety_app.agents.shared.lab_release import release_blocks,render_answer
    from safety_app.domain.chat_lab import LabClaimCheck,LabChallengeAssessment
    claims=[LabClaim(id='good',text='Supported content',kind='product',citation_ids=['doc']),LabClaim(id='bad',text='Unsafe content',kind='product',citation_ids=['doc']),LabClaim(id='depends',text='Depends on unsafe content',kind='product',citation_ids=['doc'],depends_on=['bad'])]
    answer=render_answer(claims)
    assessment=LabChallengeAssessment(claim_checks=[LabClaimCheck(claim_id=c.id,verdict='unsupported' if c.id=='bad' else 'supported',independently_publishable=c.id!='bad',note='Fixture judgment') for c in claims])
    kept,review=release_blocks(answer,assessment,{})
    result=render_answer(kept,review.issues,omitted=True)
    assert [c.id for c in result.claims]==['good'] and result.outcome=='partial'
    assert 'Unsafe' not in result.answer and 'Depends' not in result.answer
    assert set(review.omitted_claim_ids)=={'bad','depends'}


def test_general_guidance_has_visible_label_and_requires_explicit_general_review():
    from safety_app.agents.shared.lab_release import render_answer,release_blocks,claim_errors
    from safety_app.domain.chat_lab import LabChallengeAssessment,LabClaimCheck
    answer=render_answer([LabClaim(id='g',text='Seek trained assistance.',kind='general')])
    assert 'not verified against retrieved sources' in answer.answer and answer.outcome=='partial'
    assert not claim_errors(answer,{},None,'safety')
    checked=LabClaimCheck(claim_id='g',verdict='general',independently_publishable=True,note='General escalation guidance.')
    assert release_blocks(answer,LabChallengeAssessment(claim_checks=[checked]),{})[0]
    checked.independently_publishable=False
    assert not release_blocks(answer,LabChallengeAssessment(claim_checks=[checked]),{})[0]
    assert claim_errors(answer,{},None,'product')=={'g':['wrong_claim_category']}


def test_many_limitations_are_retained_and_deduplicated_without_answer_failure():
    from safety_app.agents.shared.lab_release import render_answer
    answer=render_answer([LabClaim(id='g',text='General guidance.',kind='general')],[str(i) for i in range(40)]*2)
    assert len(answer.missing_context)==40 and answer.outcome=='partial'


@pytest.mark.parametrize('code',['scope_violation','record_uncertainty_lost'])
def test_global_review_defect_prevents_partial_release(code):
    from safety_app.agents.shared.lab_release import render_answer,release_blocks
    from safety_app.domain.chat_lab import LabChallengeAssessment,LabClaimCheck,LabReviewIssue
    answer=render_answer([LabClaim(id='r',text='An accepted finding',kind='record',citation_ids=['r'])])
    assessment=LabChallengeAssessment(claim_checks=[LabClaimCheck(claim_id='r',verdict='supported',independently_publishable=True,note='Test')],blocking_issues=[LabReviewIssue(code=code,detail='Human uncertainty lost.')])
    assert release_blocks(answer,assessment,{})[0]==[]


def test_recovery_pool_is_reserved_atomically_and_does_not_double_count(rt):
    from safety_app.infrastructure.budgets import BudgetLedger
    pool=rt.budgets.open_pool('recovery','workflow',.01)
    recovery=BudgetLedger(rt.db,rt.audit,rt.config);recovery.pool_id=pool
    def committed():
        with rt.db.connect() as db:return db.execute('SELECT sum(COALESCE(actual,amount)) FROM reservations').fetchone()[0]
    assert committed()==10000
    request=recovery.reserve('corrector','workflow','gpt-6-luna',1000,1000)
    assert committed()==10000
    recovery.reconcile(request,.0005,1000,100)
    assert committed()==10000
    with pytest.raises(BudgetBlocked,match='Recovery allowance'):recovery.reserve('again','workflow','gpt-6-luna',40000,1000)
    assert committed()==10000
    rt.budgets.close_pool(pool)
    assert committed()==500


def test_unknown_recovery_request_remains_reserved_after_pool_close(rt):
    from safety_app.infrastructure.budgets import BudgetLedger
    pool=rt.budgets.open_pool('recovery','workflow',.01);recovery=BudgetLedger(rt.db,rt.audit,rt.config);recovery.pool_id=pool
    request=recovery.reserve('corrector','workflow','gpt-6-luna',1000,1000);recovery.reconcile(request,failed=True);rt.budgets.close_pool(pool)
    with rt.db.connect() as db:
        row=db.execute('SELECT * FROM reservations WHERE id=?',(request,)).fetchone()
        assert row['state']=='unknown' and row['actual'] is None and row['amount']==750
    with pytest.raises(BudgetBlocked):recovery.reserve('again','workflow','gpt-6-luna',10,10)


async def test_product_stage_uses_actual_preview_docs_and_no_safety_profile(rt,monkeypatch):
    from pydantic_ai.messages import ToolCallPart
    from safety_app.domain.chat_lab import LabDraft
    from safety_app.workflows import answering as chat_lab
    captured=[]
    async def respond(messages,info):
        packet=json.loads(next(p.content[-1] for m in messages for p in m.parts if isinstance(p,UserPromptPart)))
        captured.append(packet)
        assert packet['assignment']['execution']['domain'] is None
        assert packet['assignment']['execution']['task']=='product'
        assert 'Activity, employer/worker coverage' not in info.instructions
        assert all('collection' not in t.parameters_json_schema.get('properties',{}) for t in info.function_tools)
        sources=packet['evidence']['sources'];source=next(s for s in sources if '5% to 95%' in s['text'])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,{'claims':[{'id':'slider','text':'Use the Person detection confidence slider from 5% to 95%.','kind':'product','citation_ids':[source['id']]}]})],model_name='gpt-6-luna')
    monkeypatch.setattr(chat_lab,'TestModel',lambda **_:FunctionModel(respond,model_name='gpt-6-luna'))
    runner=BranchRunner(rt,submit(rt).job,'current',None)
    draft=await runner.stage('Product help',LabDraft,{'question_type':'product','domain':'applicability','assigned_question':'Wrong regulatory conclusion'},LabDraft(claims=[]),collection='product')
    assert draft.claims[0].kind=='product' and captured[0]['question']==CASES[0].question


async def test_output_contract_failure_continues_other_branch(rt,monkeypatch):
    original=BranchRunner.run
    async def run(self):
        if self.branch=='current':raise LabOutputRejected('Synthetic local contract failure')
        return await original(self)
    monkeypatch.setattr(BranchRunner,'run',run)
    initial=submit(rt);await execute(rt,rt.jobs.claim(['chat_comparison']))
    value=ChatLab(rt).get(initial.id,'local')
    assert value.branches[0].state=='failed' and value.branches[1].state=='completed'
    assert not value.branches[1].not_started and 'Synthetic' not in value.branches[0].answer.answer


async def test_unknown_usage_prevents_recovery_and_second_branch(rt,monkeypatch):
    async def run(self):
        reservation=self.ledger.reserve('unknown',self.job.run_id,'gpt-6-luna',10,10)
        self.ledger.reconcile(reservation,failed=True)
        assert self.start_recovery(['output_contract']) is False
        raise LabOutputRejected('Output contract')
    monkeypatch.setattr(BranchRunner,'run',run)
    initial=submit(rt);await execute(rt,rt.jobs.claim(['chat_comparison']))
    value=ChatLab(rt).get(initial.id,'local')
    assert value.branches[1].not_started and value.usage.unknown_requests==1


def test_recorded_preview_failure_is_rejected_with_real_product_evidence():
    from pydantic import ValidationError
    from safety_app.agents.safety_reviewer.lab_agent import ProductDraft
    from safety_app.agents.shared.lab_release import claim_errors
    replay=json.loads((ROOT/'backend/tests/fixtures/chat_lab_preview_failure.json').read_text())
    sources={c['id']:LabCitation.model_validate(c) for c in replay['retrieved_product_sources']}
    text=' '.join(c.text for c in sources.values())
    assert 'Preview' in text and '95%' in text and 'confidence' in text
    answer=LabAnswer.model_validate(replay['answer'])
    assert claim_errors(answer,sources,None,'product')['applicability']==['wrong_claim_category','wrong_source_category']
    with pytest.raises(ValidationError):ProductDraft.model_validate({'claims':replay['answer']['claims']})
    assert replay['route']['task']=='product' and 'jurisdiction' in replay['plan']['assignments'][0]['question']


def test_recovery_uses_shared_deadline_and_never_retries_after_cancel(rt):
    import time
    value=submit(rt);runner=BranchRunner(rt,rt.jobs.get(value.id),'optimized',None)
    runner.deadline=time.monotonic()+44
    assert not runner.start_recovery(['output_contract'])
    rt.jobs.cancel(value.id)
    with pytest.raises(asyncio.CancelledError):runner.start_recovery(['output_contract'])


async def test_failed_corrected_review_cannot_leak_unreviewed_gaps(rt,monkeypatch):
    from safety_app.domain.chat_lab import LabDraft,LabChallengeAssessment,LabClaimCheck
    from safety_app.workflows.chat_lab import fixture_route
    value=submit(rt,6);runner=BranchRunner(rt,rt.jobs.get(value.id),'optimized',None)
    async def decisions(final=None):return fixture_route(runner.job)
    async def stage(name,output_type,payload,fixture,*args,**kwargs):
        if kwargs.get('challenge'):
            if runner.recovery.attempts:raise LabOutputRejected('Malformed corrected review')
            return LabChallengeAssessment(claim_checks=[LabClaimCheck(claim_id='good',verdict='general',independently_publishable=True,note='Accept.'),LabClaimCheck(claim_id='bad',verdict='unsupported',note='Omit.')])
        return LabDraft(claims=[LabClaim(id='good',kind='general',text='Keep clear of active hazards.'),LabClaim(id='bad',kind='general',text='Defective statement.')],missing_context=['Original reviewed gap.'])
    async def corrective(*args,**kwargs):return LabDraft(claims=[LabClaim(id='good',kind='general',text='Keep clear of active hazards.')],missing_context=['UNREVIEWED CORRECTION TEXT'])
    monkeypatch.setattr(runner,'decisions',decisions);monkeypatch.setattr(runner,'stage',stage);monkeypatch.setattr(runner,'_stage_once',corrective)
    _,answer,challenge,_=await runner.run()
    assert [c.id for c in answer.claims]==['good']
    assert 'UNREVIEWED' not in answer.model_dump_json() and 'Original reviewed gap.' in answer.missing_context
    assert runner.recovery.attempts==1 and not challenge.approved and not runner.recovery.succeeded


def test_web_bundle_retains_only_actual_annotation_spans(rt):
    value=submit(rt);runner=BranchRunner(rt,rt.jobs.get(value.id),'optimized',None)
    summary='An attributed passage. A separate unassigned sentence.'
    result=SimpleNamespace(output=summary,all_messages=lambda:[ModelResponse(parts=[NativeToolReturnPart(tool_name='web_search',content={'url':'https://osha.gov/a'}),TextPart(summary,provider_details={'annotations':[{'url':'https://osha.gov/a','start_index':0,'end_index':22},{'url':'https://osha.gov/b','start_index':900,'end_index':1000}]})])])
    runner.capture_web(result)
    assert len(runner.web_evidence)==1
    item=runner.web_evidence[0]
    assert item.url is None and len(item.provenance['sources'])==2
    assert item.provenance['citation_spans'][0]['annotated_text']=='An attributed passage.'
    assert len(item.provenance['citation_spans'])==1


def test_versioned_evaluation_admission_and_reversed_order():
    import importlib.util
    spec=importlib.util.spec_from_file_location('chat_lab_evaluate',ROOT/'scripts/chat_lab_evaluate.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    corpus=json.loads((ROOT/'configs/chat-lab-evaluation.json').read_text());planned=module.schedule(corpus)
    assert len(planned)==24 and len({x['case']['id'] for x in planned})==12
    assert all(planned[i]['case']==planned[i+12]['case'] and planned[i]['order']==list(reversed(planned[i+12]['order'])) for i in range(12))
    assert module.admissible(500000,350000,150000,0)
    assert not module.admissible(500000,350001,150000,0)
    assert not module.admissible(500000,0,150000,1)
    assert module.micros(.0000001)==1


def test_correction_keeps_retrieved_document_for_a_misspelled_citation(rt):
    from safety_app.domain.chat_lab import LabDraft
    value=submit(rt);runner=BranchRunner(rt,rt.jobs.get(value.id),'optimized',None)
    source=LabCitation(id='product:correct',title='README',text='Confidence ranges from 5% to 95%.',kind='product',version='1')
    runner.knowledge.seen[source.id]=source
    runner.knowledge.seen['other']=LabCitation(id='other',title='Unrelated record',text='Not product evidence.',kind='record',version='1')
    draft=LabDraft(claims=[LabClaim(id='slider',kind='product',text='The confidence slider.',citation_ids=['product:typo'])])
    assert [c['id'] for c in runner.correction_evidence(draft,'product',None)]==['product:correct']


def test_web_bundle_keeps_qualifications_at_end_of_long_summary(rt):
    value=submit(rt);runner=BranchRunner(rt,rt.jobs.get(value.id),'optimized',None)
    summary=('Long sourced summary. '*320)+' ESSENTIAL QUALIFICATION: this is not an effective requirement.'
    result=SimpleNamespace(output=summary,all_messages=lambda:[ModelResponse(parts=[NativeToolReturnPart(tool_name='web_search',content={'url':'https://osha.gov/a'}),TextPart(summary)])])
    runner.capture_web(result)
    assert runner.web_evidence[0].text==summary


async def test_one_malformed_generation_can_recover_without_more_tools(rt,monkeypatch):
    from pydantic_ai.messages import ToolCallPart
    from safety_app.workflows import answering as chat_lab
    from safety_app.domain.chat_lab import LabDraft
    calls=[]
    def respond(messages,info):
        calls.append(info)
        args={'claims':[{'id':'one','text':'A relevant unknown.','kind':'uncertain','citation_ids':[],'exact_clause':None,'depends_on':[]}],'missing_context':[]}
        if len(calls)==1:args['claims'][0]['kind']='WRONG_CATEGORY'
        else:assert not info.function_tools
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,args)])
    monkeypatch.setattr(chat_lab,'TestModel',lambda **_:FunctionModel(respond,model_name='gpt-6-luna'))
    value=submit(rt);runner=BranchRunner(rt,rt.jobs.get(value.id),'optimized',None)
    draft=await runner.stage('Product help',LabDraft,{'question_type':'product'},LabDraft(claims=[]),collection='product')
    assert len(calls)==2 and runner.recovery.attempts==1 and runner.recovery.succeeded
    assert draft.claims[0].kind=='uncertain'
    events=ChatLab(rt).events(value.id,'local')
    assert sum(e.event_type=='agent_started' and e.agent=='Correction · Product help' for e in events)==1


def test_planner_wire_schema_excludes_multiple_and_replays_recorded_failure():
    from pydantic import ValidationError
    from safety_app.domain.chat_lab import LabPlan
    schema=LabPlan.model_json_schema()
    assert 'multiple' not in schema['$defs']['LabAssignment']['properties']['domain']['enum']
    replay=json.loads((ROOT/'backend/tests/fixtures/chat_lab_planner_multiple.json').read_text())
    rejected=[x for x in replay['outputs'] if any(a['domain']=='multiple' for a in x['output']['assignments'])]
    assert len(rejected)==2
    for value in rejected:
        with pytest.raises(ValidationError,match='literal_error'):LabPlan.model_validate(value['output'])
    assert LabClaim(id='qualified',kind='uncertain',text='x'*1000).text=='x'*1000


def test_record_tool_cardinality_is_bounded_without_crashing_or_crossing_permissions(rt):
    for i in range(6):rt.db.save('video_safety_case',{'id':f'r{i}','site_id':'local','uncertainty':['original uncertainty']})
    rt.db.save('video_safety_case',{'id':'foreign','site_id':'other','secret':'do not leak'})
    knowledge=LabKnowledge(rt.root,freeze(rt,'local',{'operator':'local'}),'local')
    result=knowledge.read_for_task([f'r{i}' for i in range(6)],'records')
    assert len(result['sources'])==5 and result['gaps'] and 'r5' not in knowledge.seen
    denied=knowledge.read_for_task(['r0','foreign','missing'],'product')
    assert not denied['sources'] and denied['gaps'] and 'secret' not in json.dumps(denied)
    assert knowledge.read_for_task([],'records')['gaps']


def test_provisional_guidance_can_be_released_but_legal_product_and_record_facts_stay_strict():
    from safety_app.agents.shared.lab_release import render_answer,release_blocks
    from safety_app.domain.chat_lab import LabChallengeAssessment,LabClaimCheck
    claims=[LabClaim(id=k,kind=k,text='Qualified source statement.',citation_ids=['source']) for k in ['guidance','requirement','product','record']]
    answer=render_answer(claims)
    assessment=LabChallengeAssessment(claim_checks=[LabClaimCheck(claim_id=c.id,verdict='uncertain',independently_publishable=True,note='Preserved provisional support.') for c in claims])
    kept,result=release_blocks(answer,assessment,{},['guidance'])
    assert [c.id for c in kept]==['guidance'] and result.observations
    kept,_=release_blocks(answer,assessment,{'guidance':['missing_citation']})
    assert not kept


def test_recorded_web_summary_review_keeps_provisional_guidance_with_warning():
    from safety_app.agents.shared.lab_release import render_answer,release_blocks,claim_errors
    from safety_app.domain.chat_lab import LabDraft,LabChallengeAssessment
    replay=json.loads((ROOT/'backend/tests/fixtures/chat_lab_provisional_summary.json').read_text())
    draft=LabDraft.model_validate(replay['draft']);answer=render_answer(draft.claims,draft.missing_context)
    evidence={x['id']:LabCitation.model_validate(x) for x in replay['citations']}
    provisional={c.id for c in answer.claims if c.kind=='guidance' and any(evidence[id].kind=='web_summary' for id in c.citation_ids if id in evidence)}
    kept,result=release_blocks(answer,LabChallengeAssessment.model_validate(replay['review']),claim_errors(answer,evidence,None,'safety'),provisional)
    assert result.approved and result.observations
    assert any(c.kind=='guidance' for c in kept)
    assert all('summary' in c.text.lower() or c.kind=='uncertain' for c in kept)


async def test_actual_record_tool_handles_six_ids_without_a_tool_retry(rt,monkeypatch):
    from pydantic_ai.messages import ToolCallPart
    from safety_app.workflows import answering as chat_lab
    from safety_app.domain.chat_lab import LabDraft
    for i in range(6):rt.db.save('video_safety_case',{'id':f'r{i}','site_id':'local','uncertainty':['unresolved']})
    calls=[]
    def respond(messages,info):
        calls.append(info)
        if len(calls)==1:
            schema=next(t for t in info.function_tools if t.name=='read_sources').parameters_json_schema
            assert schema['properties']['ids']['maxItems']==5
            return ModelResponse(parts=[ToolCallPart('read_sources',{'ids':[f'r{i}' for i in range(6)]})])
        assert 'first five' in str(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,{'claims':[{'id':'gap','kind':'uncertain','text':'Only the bounded subset was read.'}],'missing_context':[]})])
    monkeypatch.setattr(chat_lab,'TestModel',lambda **_:FunctionModel(respond,model_name='gpt-6-luna'))
    value=submit(rt,3);runner=BranchRunner(rt,value.job,'optimized',None)
    await runner.stage('Saved review lookup',LabDraft,{'question_type':'records'},LabDraft(claims=[]))
    assert len(calls)==2 and len(runner.knowledge.seen)==5 and not runner.recovery.attempts
