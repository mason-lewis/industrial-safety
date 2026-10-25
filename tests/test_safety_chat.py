"""Offline chat, delegation/source trace, authorization and failure fixtures."""
import shutil
from pathlib import Path
import pytest
from safety_app.infrastructure.runtime import Runtime
from safety_app.modules.safety_review.chat import SafetyChat
from safety_app.agents.safety_reviewer.chat_models import ChatMessageInput,ChatAnswer,ChatAssessment,ChatClaim,ChatTask
from safety_app.domain.models import SourceExcerpt
from safety_app.workers.runner import execute

@pytest.fixture
def chat_rt(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[1]/'configs',tmp_path/'configs')
    rt=Runtime(tmp_path)
    for id,section,text in [('app','1910.5','Federal employer coverage fixture.'),('truck','1910.178','No person shall stand under the elevated portion of any truck.')]:
        rt.db.save('source',SourceExcerpt(id=id,source_id=id,title='Fictional source '+id,section=section,clause='1910.178(m)(2)' if id=='truck' else '1910.5',text=text,source_type='requirement',version='fictional-v1',site_id='local',jurisdiction='US-federal-OSHA'),immutable=True)
    return rt

@pytest.mark.asyncio
async def test_fixture_chat_persists_real_tools_delegations_challenge_without_findings(chat_rt):
    rt=chat_rt;service=SafetyChat(rt);session=service.create('Fixture test','local')
    body=ChatMessageInput(message='Explain this forklift scenario.',mode='fixture',idempotency_key='first')
    job=service.send(session.id,body,'local');assert service.send(session.id,body,'local').id==job.id
    claimed=rt.jobs.claim(['safety_chat']);assert claimed.id==job.id
    done=await execute(rt,claimed)
    assert done.status=='completed',done.error
    state=service.state(session.id,'local');result=state.turns[0].result
    assert result and len(result.delegations)==2 and result.mode=='fixture'
    assert {d.specialist for d in result.delegations}=={'applicability','vehicles'}
    assert set(result.challenge.checked_claim_ids)=={c.id for c in result.response.claims}
    assert {c.id for c in result.citations}=={'app','truck'}
    assert 'does not answer' in result.response.answer
    assert not rt.db.list('finding') and not rt.db.list('observation')
    rows=rt.audit.rows(1000,job_id=job.id)
    assert len([r for r in rows if r['event_type']=='specialist_dispatched'])==2
    assert {'delegate_chat_specialist','search_safety_sources','fetch_sources'}<={r['tool_name'] for r in rows if r['event_type']=='tool_finished'}
    assert all(r['configuration']['fake_provider'] for r in rows if r['event_type']=='model_request')
    assert all(r['configuration']['model_settings']['openai_reasoning_effort']=='low' for r in rows if r['event_type']=='model_request'),[r['configuration']['model_settings'] for r in rows if r['event_type']=='model_request']
    assert not rt.budgets.status()['outstanding']
    # Same request after completion returns the saved job; it never duplicates a turn.
    assert service.send(session.id,body,'local').id==job.id
    next_job=service.send(session.id,ChatMessageInput(message='A follow-up question',mode='fixture',idempotency_key='second'),'local')
    assert next_job.payload['history'][0]['mode']=='fixture'
    assert len(service.state(session.id,'local').turns)==2

def test_session_scope_parallel_turns_idempotency_and_cancel(chat_rt):
    service=SafetyChat(chat_rt);session=service.create('Test','local')
    with pytest.raises(ValueError,match='outside site'):service.state(session.id,'foreign')
    with pytest.raises(ValueError,match='outside site'):service.send(session.id,ChatMessageInput(message='test',idempotency_key='a'),'foreign')
    job=service.send(session.id,ChatMessageInput(message='test',idempotency_key='a'),'local')
    with pytest.raises(ValueError,match='different request'):service.send(session.id,ChatMessageInput(message='changed',idempotency_key='a'),'local')
    with pytest.raises(ValueError,match='pending'):service.send(session.id,ChatMessageInput(message='new',idempotency_key='b'),'local')
    other=service.create('Other','local')
    with pytest.raises(ValueError,match='outside conversation'):service.job(other.id,job.id,'local')
    chat_rt.jobs.cancel(job.id)
    assert service.send(session.id,ChatMessageInput(message='new',idempotency_key='b'),'local').status=='queued'

@pytest.mark.asyncio
async def test_specialist_can_complete_after_four_model_requests(chat_rt):
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,ToolCallPart
    from safety_app.workflows.chat_fixture import models
    base=models();counts={}
    def factory(name):
        original=base(name)
        async def respond(messages,info):
            counts[name]=counts.get(name,0)+1
            if name=='specialist_applicability' and counts[name]==1:
                return ModelResponse(parts=[ToolCallPart('get_site_rules',{'site_id':'local','topic':'coverage'},tool_call_id='extra-context')])
            return await original.function(messages,info)
        return FunctionModel(respond)
    service=SafetyChat(chat_rt);session=service.create('Four-request fixture','local')
    job=service.send(session.id,ChatMessageInput(message='Offline delegation regression',idempotency_key='four'),'local')
    done=await execute(chat_rt,job,factory)
    assert done.status=='completed',done.error
    assert counts['specialist_applicability']==4
    assert service.state(session.id,'local').turns[0].result.challenge.checked_claim_ids

@pytest.mark.asyncio
async def test_child_deadline_surfaces_as_timeout_without_delegation_retry(chat_rt):
    import asyncio
    from pydantic_ai.models.function import FunctionModel
    from safety_app.workflows.chat_fixture import models
    base=models();calls=[]
    chat_rt.config['limits'].update(agent_seconds=.02,workflow_seconds=1)
    def factory(name):
        original=base(name)
        async def respond(messages,info):
            if name=='specialist_applicability':
                calls.append(name)
                await asyncio.sleep(.1)
            return await original.function(messages,info)
        return FunctionModel(respond)
    service=SafetyChat(chat_rt);session=service.create('Deadline fixture','local')
    job=service.send(session.id,ChatMessageInput(message='Offline timeout regression',idempotency_key='timeout'),'local')
    done=await execute(chat_rt,job,factory)
    assert done.status=='failed' and done.error.code=='AgentStageTimeout'
    assert 'specialist_applicability' in done.error.message
    assert calls==['specialist_applicability']
    assert not service.state(session.id,'local').turns[0].result
    rows=chat_rt.audit.rows(100,job_id=job.id)
    assert any(r['tool_name']=='delegate_chat_specialist' and r['event_type']=='tool_finished' and r['error']['code']=='AgentStageTimeout' for r in rows if r['error'])

@pytest.mark.asyncio
async def test_specialist_batches_more_than_eight_tools_then_finishes_with_challenge(chat_rt):
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,ToolCallPart
    from safety_app.workflows.chat_fixture import models
    base=models();calls={};instructions=[]
    def factory(name):
        original=base(name)
        async def respond(messages,info):
            calls[name]=calls.get(name,0)+1
            if name=='specialist_vehicles':
                instructions.extend(m.instructions for m in messages if getattr(m,'instructions',None))
                if calls[name]<=3:
                    size=4 if calls[name]<3 else 3
                    return ModelResponse(parts=[ToolCallPart('get_site_rules',{'site_id':'local','topic':f'fictional-scope-{calls[name]}-{i}'},tool_call_id=f'scope-{calls[name]}-{i}') for i in range(size)])
            return await original.function(messages,info)
        return FunctionModel(respond)
    service=SafetyChat(chat_rt);session=service.create('Batched-tool fixture','local')
    job=service.send(session.id,ChatMessageInput(message='Offline batched tool regression',idempotency_key='batches'),'local')
    done=await execute(chat_rt,job,factory)
    assert done.status=='completed',done.error
    rows=chat_rt.audit.rows(300,job_id=job.id)
    assert len([r for r in rows if r['event_type']=='tool_started' and r['agent_name']=='specialist_vehicles'])==13
    assert any('5 tool calls' in s for s in instructions)
    assert service.state(session.id,'local').turns[0].result.challenge.checked_claim_ids

@pytest.mark.asyncio
async def test_hosted_chat_is_blocked_without_opt_in_and_audited(chat_rt,monkeypatch):
    monkeypatch.delenv('SAFETY_PAID_REVIEW',raising=False)
    service=SafetyChat(chat_rt);session=service.create('Hosted','local')
    job=service.send(session.id,ChatMessageInput(message='What does OSHA require?',idempotency_key='blocked'),'local')
    done=await execute(chat_rt,job)
    assert done.status=='failed' and 'SAFETY_PAID_REVIEW' in done.error.message
    assert not chat_rt.db.list('chat_result')
    assert any(r['event_type']=='run_failed' for r in chat_rt.audit.rows(job_id=job.id))

@pytest.mark.asyncio
async def test_cancelled_chat_never_commits_answer(chat_rt):
    service=SafetyChat(chat_rt);session=service.create('Cancel','local')
    job=service.send(session.id,ChatMessageInput(message='test',mode='fixture',idempotency_key='cancel'),'local')
    chat_rt.jobs.cancel(job.id);done=await execute(chat_rt,job)
    assert done.status=='cancelled' and not chat_rt.db.list('chat_result')

def test_chat_claim_and_boss_validation_reject_fabricated_sources_and_missing_gaps(chat_rt):
    from types import SimpleNamespace
    from safety_app.workflows.safety_chat import ChatTeam,validate_claims
    d=SimpleNamespace(seen_sources={'truck'},seen_rules=set(),sources=chat_rt.sources,scope=SimpleNamespace(site_id='local'))
    claim=ChatClaim(id='q:c',text='Hypothetical requirement.',kind='requirement',source_refs=['truck'],exact_clause='1910.178(m)(2)')
    validate_claims([claim],d,'vehicles')
    with pytest.raises(ValueError,match='domain'):validate_claims([claim],d,'energy')
    with pytest.raises(ValueError,match='unseen'):validate_claims([claim.model_copy(update={'source_refs':['invented']})],d)
    team=ChatTeam(chat_rt,SimpleNamespace(),None)
    team.results=[ChatAssessment(question_id='q',specialist='applicability',answer='Fixture',claims=[claim],missing_context=['Unverified scenario.'])]
    answer=ChatAnswer(answer='Fixture',claims=[claim])
    team.validate_boss(answer,d)
    assert answer.missing_context==['Unverified scenario.']
    supplied=ChatAnswer(answer='Fixture',claims=[claim.model_copy(update={'text':'Invented requirement.'})])
    with pytest.raises(ValueError,match='invented'):team.validate_boss(supplied,d)

def test_known_source_gaps_are_retained_with_prefixes_omissions_and_large_lists(chat_rt):
    from types import SimpleNamespace
    from safety_app.workflows.safety_chat import ChatTeam
    team=ChatTeam(chat_rt,SimpleNamespace(),None)
    known={f'source-{i}: event-date applicability not established' for i in range(45)}
    d=SimpleNamespace(source_gaps=known,seen_sources=set(),seen_rules=set())
    task=ChatTask(question_id='q',specialist='applicability',question='Check the scenario applicability.')
    result=ChatAssessment(question_id='q',specialist='applicability',answer='Unverified scenario.',missing_context=['Prepared packet gap: source-0: event-date applicability not established.','Fetched gap: source-1: event-date applicability not established.','Employer type is unknown.'])
    team.validate_child(result,d,task)
    assert set(result.missing_context)==known|{'Employer type is unknown.'}
    # Large trusted warning lists survive nested serialization and boss synthesis.
    result=ChatAssessment.model_validate_json(result.model_dump_json())
    team.results=[result]
    answer=ChatAnswer(answer='Unverified scenario.')
    team.validate_boss(answer,d)
    assert set(ChatAnswer.model_validate_json(answer.model_dump_json()).missing_context)==set(result.missing_context)

def test_claim_validation_reports_parent_clause_and_unsourced_advice_together(chat_rt):
    from types import SimpleNamespace
    from safety_app.workflows.safety_chat import validate_claims
    chat_rt.db.save('source',SourceExcerpt(id='training',source_id='training',title='Fictional training block',section='1910.178',clause='1910.178(l)',text='(l) Training. (1) Safe operation. (i) Fictional requirement.',source_type='requirement',version='fixture',site_id='local',jurisdiction='US-federal-OSHA'),immutable=True)
    d=SimpleNamespace(seen_sources={'training'},seen_rules=set(),sources=chat_rt.sources,scope=SimpleNamespace(site_id='local'))
    claims=[ChatClaim(id='q:training',text='Fictional requirement.',kind='requirement',source_refs=['training'],exact_clause='1910.178(l)(1)(i)'),ChatClaim(id='q:advice',text='Unverified advice.',kind='guidance')]
    with pytest.raises(ValueError) as error:validate_claims(claims,d,'vehicles')
    assert 'q:training' in str(error.value) and 'stored_clause' in str(error.value) and "1910.178(l)'" in str(error.value)
    assert 'q:advice' in str(error.value) and 'kind uncertain' in str(error.value)
    claims[0].exact_clause='1910.178(l)';claims[1].kind='uncertain'
    validate_claims(claims,d,'vehicles')

@pytest.mark.asyncio
async def test_parent_citation_and_unsourced_advice_repair_in_one_retry(chat_rt):
    from pydantic_ai.models.function import FunctionModel
    from safety_app.workflows.chat_fixture import models
    base=models();finals=[]
    def factory(name):
        original=base(name)
        async def respond(messages,info):
            response=await original.function(messages,info)
            for part in response.parts:
                if name=='specialist_vehicles' and getattr(part,'tool_name',None)==info.output_tools[0].name:
                    finals.append(True)
                    part.args['claims']=[{'id':'vehicle:requirement','text':'Fictional clause check.','kind':'requirement','source_refs':['truck'],'exact_clause':'1910.178(m)(2)(i)' if len(finals)==1 else '1910.178(m)(2)'},{'id':'vehicle:advice','text':'Fictional advice.','kind':'guidance' if len(finals)==1 else 'uncertain'}]
            return response
        return FunctionModel(respond)
    service=SafetyChat(chat_rt);session=service.create('Citation repair fixture','local')
    job=service.send(session.id,ChatMessageInput(message='Offline citation repair regression',idempotency_key='repair'),'local')
    done=await execute(chat_rt,job,factory)
    assert done.status=='completed',done.error
    failures=chat_rt.audit.rows(100,event_type='validation_failed',job_id=job.id)
    assert len(failures)==1 and 'vehicle:requirement' in failures[0]['error']['message'] and 'vehicle:advice' in failures[0]['error']['message']
    assert len(finals)==2 and service.state(session.id,'local').turns[0].result.challenge.checked_claim_ids

@pytest.mark.asyncio
async def test_substantive_output_failure_exposes_validation_reason_without_answer(chat_rt):
    from pydantic_ai.models.function import FunctionModel
    from safety_app.workflows.chat_fixture import models
    base=models()
    def factory(name):
        original=base(name)
        async def respond(messages,info):
            response=await original.function(messages,info)
            for part in response.parts:
                if name=='specialist_vehicles' and getattr(part,'tool_name',None)==info.output_tools[0].name:
                    part.args['claims']=[{'id':'bad-clause','text':'Fictional unsupported claim.','kind':'requirement','source_refs':['truck'],'exact_clause':'1910.178(m)(2)(i)'}]
            return response
        return FunctionModel(respond)
    service=SafetyChat(chat_rt);session=service.create('Rejected output fixture','local')
    job=service.send(session.id,ChatMessageInput(message='Offline rejection regression',idempotency_key='reject'),'local')
    done=await execute(chat_rt,job,factory)
    assert done.status=='failed' and done.error.code=='AgentOutputRejected'
    assert 'specialist_vehicles' in done.error.message and 'bad-clause' in done.error.message and 'stored_clause' in done.error.message
    assert not service.state(session.id,'local').turns[0].result
    assert len(chat_rt.audit.rows(100,event_type='validation_failed',job_id=job.id))==2

@pytest.mark.asyncio
async def test_request_headroom_returns_explicit_partial_answer_with_challenge(chat_rt,monkeypatch):
    from safety_app.workflows import safety_chat
    from safety_app.workflows.chat_fixture import models
    from safety_app.infrastructure.budgets import CompletionHeadroomReached
    original=safety_chat.run_stage
    async def limited(*args,**kwargs):
        if args[2]=='specialist_vehicles':raise CompletionHeadroomReached('Completion capacity reserved')
        return await original(*args,**kwargs)
    monkeypatch.setattr(safety_chat,'run_stage',limited)
    service=SafetyChat(chat_rt);session=service.create('Partial assessment fixture','local')
    job=service.send(session.id,ChatMessageInput(message='Offline headroom regression',idempotency_key='partial'),'local')
    done=await execute(chat_rt,job,models())
    assert done.status=='completed',done.error
    result=service.state(session.id,'local').turns[0].result
    assert result.response.completion=='partial'
    assert any('not fully assessed' in gap for gap in result.response.missing_context)
    assert result.delegations[-1].claims==[]
    assert set(result.challenge.checked_claim_ids)=={c.id for c in result.response.claims}
    assert chat_rt.audit.rows(100,event_type='specialist_deferred',job_id=job.id)

@pytest.mark.asyncio
async def test_chat_retains_source_gaps_without_model_copy_or_output_retry(chat_rt):
    from pydantic_ai.models.function import FunctionModel
    from safety_app.workflows.chat_fixture import models
    base=models()
    def factory(name):
        original=base(name)
        async def respond(messages,info):
            response=await original.function(messages,info)
            for part in response.parts:
                if name!='challenge_reviewer' and getattr(part,'tool_name',None)==info.output_tools[0].name:
                    part.args['missing_context']=[]
                    if name=='safety_reviewer':part.args['claims']=[]
            return response
        return FunctionModel(respond)
    service=SafetyChat(chat_rt);session=service.create('Gap retention fixture','local')
    job=service.send(session.id,ChatMessageInput(message='Offline gap retention regression',idempotency_key='retain-gaps'),'local')
    done=await execute(chat_rt,job,factory)
    assert done.status=='completed',done.error
    result=service.state(session.id,'local').turns[0].result
    gaps={g for assessment in result.delegations for g in assessment.missing_context}
    assert gaps and gaps<=set(result.response.missing_context)
    assert not chat_rt.audit.rows(100,event_type='validation_failed',job_id=job.id)

@pytest.mark.asyncio
async def test_chat_delegate_ownership_followups_and_budget(chat_rt):
    from types import SimpleNamespace
    from safety_app.workflows.safety_chat import ChatTeam
    from safety_app.infrastructure.budgets import BudgetBlocked
    team=ChatTeam(chat_rt,SimpleNamespace(),None);boss=SimpleNamespace(cancelled=lambda:False)
    task=ChatTask(question_id='q',specialist='vehicles',question='Test a vehicle interaction.')
    with pytest.raises(ValueError,match='applicability'):await team.delegate(task,boss)
    team.results=[ChatAssessment(question_id='a',specialist='applicability',answer='Fixture')];team.owners['q']='energy'
    with pytest.raises(ValueError,match='owned'):await team.delegate(task,boss)
    team.owners.clear();task.phase='follow_up';task.previous_call_id='missing'
    with pytest.raises(ValueError,match='latest'):await team.delegate(task,boss)
    team.calls=[task]*8
    with pytest.raises(BudgetBlocked):await team.delegate(task,boss)

@pytest.mark.asyncio
async def test_chat_api_authentication_persistence_and_scoped_trace(chat_rt,monkeypatch):
    from fastapi.testclient import TestClient
    from safety_app.api.main import app
    monkeypatch.setattr('safety_app.infrastructure.runtime._default',chat_rt)
    # No lifespan: this test must not start the retained player or migrate history.
    client=TestClient(app)
    assert client.get('/api/chat/sessions').status_code==401
    assert client.post('/api/chat/sessions',json={'title':'API fixture'}).status_code==401
    assert client.post('/api/auth/session',json={}).status_code==200
    session=client.post('/api/chat/sessions',json={'title':'API fixture'}).json()
    id=session['id'];path=f'/api/chat/sessions/{id}'
    assert client.post(path+'/messages',json={'message':'test','idempotency_key':'a','reviewer_id':'fake'}).status_code==422
    response=client.post(path+'/messages',json={'message':'test question','idempotency_key':'a','mode':'fixture'})
    assert response.status_code==200,response.text
    job=chat_rt.jobs.get(response.json()['id'])
    assert client.get(f'{path}/turns/{job.id}/audit/download').status_code==400
    done=await execute(chat_rt,job)
    assert done.status=='completed',done.error
    assert client.get(path).json()['turns'][0]['result']['mode']=='fixture'
    assert client.get(f'{path}/turns/{job.id}/audit?limit=2').status_code==200
    assert len(client.get(f'{path}/turns/{job.id}/audit?limit=2').json())==2
    export=client.get(f'{path}/turns/{job.id}/audit/download')
    assert export.status_code==200 and 'attachment' in export.headers['content-disposition']
    import json
    lines=[json.loads(line) for line in export.text.splitlines()]
    assert len(lines)==len(chat_rt.audit.rows(1000,job_id=job.id))
    assert [r['sequence'] for r in lines]==sorted(r['sequence'] for r in lines)
    assert {r['job_id'] for r in lines}=={job.id}
    assert client.get(f'{path}/turns/{job.id}/audit?limit=1001').status_code==422
    other=client.post('/api/chat/sessions',json={'title':'other'}).json()['id']
    assert client.get(f'/api/chat/sessions/{other}/turns/{job.id}/audit').status_code==400
    assert client.get(f'/api/chat/sessions/{other}/turns/{job.id}/audit/download').status_code==400
    assert client.post(f'{path}/turns/{job.id}/cancel',json={}).status_code==400
    client.close()

@pytest.mark.asyncio
async def test_incomplete_independent_chat_challenge_cannot_publish_answer(chat_rt):
    from pydantic_ai.models.test import TestModel
    from safety_app.workflows.chat_fixture import models
    from safety_app.workflows.safety_chat import chat
    service=SafetyChat(chat_rt);session=service.create('Incomplete challenge','local')
    job=service.send(session.id,ChatMessageInput(message='Fixture question',mode='hosted',idempotency_key='challenge'),'local')
    fixture=models()
    def model_factory(name):
        return TestModel(call_tools=[],custom_output_args={'checked_claim_ids':[]}) if name=='challenge_reviewer' else fixture(name)
    done=await execute(chat_rt,job,model_factory)
    assert done.status=='failed' and 'every claim' in done.error.message
    assert not chat_rt.db.list('chat_result')
    assert chat_rt.audit.rows(event_type='run_failed',job_id=job.id)

@pytest.mark.asyncio
async def test_chat_followup_supersedes_previous_claims_and_preserves_calls(chat_rt):
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,ToolCallPart
    from safety_app.workflows.chat_fixture import models,returns,data
    service=SafetyChat(chat_rt);session=service.create('Follow-up fixture','local')
    job=service.send(session.id,ChatMessageInput(message='Test bounded follow-up',mode='hosted',idempotency_key='follow'),'local')
    base=models();rounds=0
    async def boss(messages,info):
        nonlocal rounds
        rounds+=1
        if rounds==1:
            return ModelResponse(parts=[ToolCallPart('delegate_chat_specialist',{'task':{'question_id':'coverage','specialist':'applicability','question':'Check employer coverage for this text question.'}}),ToolCallPart('delegate_chat_specialist',{'task':{'question_id':'vehicle','specialist':'vehicles','question':'Find clause 1910.178(m)(2) and the remaining context gaps.'}})])
        if rounds==2:
            return ModelResponse(parts=[ToolCallPart('delegate_chat_specialist',{'task':{'question_id':'vehicle','specialist':'vehicles','question':'Recheck 1910.178(m)(2) and explain the unresolved date context.','phase':'follow_up','previous_call_id':'chat-delegation-2'}})])
        active=list({r['question_id']:r for r in [data(v) for v in returns(messages,'delegate_chat_specialist')]}.values())
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,{'answer':'Fixture answer with the latest specialist claims.','claims':[c for r in active for c in r['claims']],'missing_context':sorted({g for r in active for g in r['missing_context']})})])
    def model_factory(name):return FunctionModel(boss) if name=='safety_reviewer' else base(name)
    done=await execute(chat_rt,job,model_factory)
    assert done.status=='completed',done.error
    result=service.state(session.id,'local').turns[0].result
    assert result and len(result.delegations)==3 and result.delegations[-1].call_id=='chat-delegation-3'
    assert len(result.response.claims)==2
    vehicle_runs={r['run_id'] for r in chat_rt.audit.rows(job_id=job.id,agent_name='specialist_vehicles',event_type='run_started')}
    assert len(vehicle_runs)==2

@pytest.mark.asyncio
async def test_budget_omitted_uncertain_reference_is_gap_not_evidence(chat_rt):
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,ToolCallPart
    from safety_app.workflows.chat_fixture import models,returns
    chat_rt.db.save('source',SourceExcerpt(id='oversized',source_id='oversized',title='Fictional oversized source',section='1910.178',text='Exact fixture text. '*400,source_type='requirement',version='fixture',site_id='local',jurisdiction='US-federal-OSHA'),immutable=True)
    base=models();finals=[]
    def factory(name):
        original=base(name)
        async def respond(messages,info):
            response=await original.function(messages,info)
            for part in response.parts:
                if name=='specialist_vehicles' and getattr(part,'tool_name',None)==info.output_tools[0].name:
                    if len(returns(messages,'fetch_sources'))<2:
                        return ModelResponse(parts=[ToolCallPart('fetch_sources',{'source_ids':['oversized']})])
                    finals.append(True)
                    part.args['claims'].append({'text':'The additional source could not fit; exact requirements remain unresolved.','kind':'uncertain','source_refs':['oversized']})
            return response
        return FunctionModel(respond)
    service=SafetyChat(chat_rt);session=service.create('Omitted evidence fixture','local')
    job=service.send(session.id,ChatMessageInput(message='Offline omitted-source regression',idempotency_key='omitted'),'local')
    done=await execute(chat_rt,job,factory)
    assert done.status=='completed',done.error
    result=service.state(session.id,'local').turns[0].result
    gap_claim=next(c for c in result.response.claims if c.text.startswith('The additional source'))
    assert gap_claim.kind=='uncertain' and gap_claim.source_refs==[]
    assert 'oversized' not in {c.id for c in result.citations}
    assert any('oversized: exact text was omitted' in gap for gap in result.response.missing_context)
    assert set(result.challenge.checked_claim_ids)=={c.id for c in result.response.claims}
    assert len(finals)==1 and not chat_rt.audit.rows(event_type='validation_failed',job_id=job.id)
    changes=chat_rt.audit.rows(event_type='chat_gap_reference_retained',job_id=job.id)
    assert len(changes)==1 and changes[0]['output']['moved_references']==[{'claim_slot':2,'source_ids':['oversized']}]
    assert not chat_rt.budgets.status()['outstanding']

@pytest.mark.parametrize('kind,source,rule,clause',[
    ('requirement','omitted',None,'1910.120(q)'),
    ('guidance','omitted',None,None),
    ('uncertain','invented',None,None),
    ('uncertain','preview-only',None,None),
    ('uncertain','omitted','invented-rule',None),
    ('uncertain','omitted',None,'1910.120(q)'),
])
def test_gap_bookkeeping_never_accepts_unverified_support(chat_rt,kind,source,rule,clause):
    from types import SimpleNamespace
    from safety_app.workflows.safety_chat import ChatTeam
    team=ChatTeam(chat_rt,SimpleNamespace(),None)
    d=SimpleNamespace(source_gaps={'omitted: omitted to fit packet budget'},seen_sources=set(),seen_rules=set(),budget_omitted_source_ids={'omitted'})
    task=ChatTask(question_id='health-q',specialist='health',question='Check this hypothetical chemical spill.')
    claim=ChatClaim(text='Unverified claim.',kind=kind,source_refs=[source],rule_refs=[rule] if rule else [],exact_clause=clause)
    result=ChatAssessment(specialist='health',answer='Unverified.',claims=[claim])
    with pytest.raises(ValueError,match='unseen source or rule') as error:team.validate_child(result,d,task)
    assert source in str(error.value) and 'missing_context' in str(error.value)
    assert result.claims[0].source_refs==[source]


def test_source_packet_tracks_only_confirmed_budget_omissions():
    from safety_app.agents.shared.deps import AgentDeps
    from safety_app.modules.safety_review.sources import EvidencePacket,EvidenceExcerpt
    # Exercise the production dependency method without constructing unrelated services.
    from types import SimpleNamespace
    d=SimpleNamespace(source_gaps=set(),seen_sources=set(),fetched_source_ids=set(),complete_source_ids=set(),budget_omitted_source_ids=set())
    packet=EvidencePacket(gaps=['budget-a: omitted to fit packet budget','budget-b: complete excerpt exceeds remaining evidence budget','Missing source invented','Required source foreign outside assigned scope','expired: source outside event-date range'])
    AgentDeps.record_source_packet(d,packet)
    assert d.budget_omitted_source_ids=={'budget-a','budget-b'}
    excerpt=EvidenceExcerpt(id='budget-a',title='Fixture',section='Fixture',text='Now fetched.',source_type='requirement',version='fixture',jurisdiction='US-federal-OSHA')
    AgentDeps.record_source_packet(d,EvidencePacket(excerpts=[excerpt],complete=True))
    assert d.budget_omitted_source_ids=={'budget-b'} and d.seen_sources=={'budget-a'} and d.complete_source_ids=={'budget-a'}


@pytest.mark.asyncio
@pytest.mark.parametrize('count',[0,6])
async def test_invalid_source_fetch_count_returns_gap_then_corrects_within_budget(chat_rt,count):
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,ToolCallPart
    from safety_app.workflows.chat_fixture import models,returns,data
    base=models()
    def factory(name):
        original=base(name)
        async def respond(messages,info):
            if name=='specialist_vehicles':
                found=returns(messages,'search_safety_sources');fetched=returns(messages,'fetch_sources')
                if found and not fetched:
                    return ModelResponse(parts=[ToolCallPart('fetch_sources',{'source_ids':['truck']+[f'not-fetched-{i}' for i in range(5)] if count else []})])
                if len(fetched)==1:
                    rejected=data(fetched[0]);assert rejected['excerpts']==[] and not rejected['complete']
                    assert f'received {count} IDs' in rejected['gaps'][0]
                    return ModelResponse(parts=[ToolCallPart('fetch_sources',{'source_ids':['truck']})])
            return await original.function(messages,info)
        return FunctionModel(respond)
    service=SafetyChat(chat_rt);session=service.create('Invalid fetch fixture','local')
    job=service.send(session.id,ChatMessageInput(message='Offline fetch-count regression',idempotency_key='count-test'),'local')
    done=await execute(chat_rt,job,factory)
    assert done.status=='completed',done.error
    result=service.state(session.id,'local').turns[0].result
    assert not any(c.id.startswith('not-fetched-') for c in result.citations)
    assert any('Source fetch rejected' in gap for gap in result.response.missing_context)
    rows=chat_rt.audit.rows(limit=1000,job_id=job.id)
    fetches=[r for r in rows if r['agent_name']=='specialist_vehicles' and r['event_type']=='tool_finished' and r['tool_name']=='fetch_sources']
    assert len(fetches)==2 and all(r['error'] is None for r in fetches)
    run=fetches[0]['run_id']
    with chat_rt.db.connect() as db:
        assert db.execute('SELECT count FROM run_tools WHERE run_id=?',(run,)).fetchone()['count']==3
    # Service callers still get a strict limit; only the model tool translates this argument error.
    with pytest.raises(ValueError,match='Fetch 1..5'):
        chat_rt.sources.fetch_packet(['truck']*count,'local')


@pytest.mark.asyncio
async def test_specialist_receives_current_exact_citation_permissions(chat_rt):
    import json
    from pydantic_ai.models.function import FunctionModel
    from safety_app.workflows.chat_fixture import models,returns,data
    base=models();checked=[]
    def factory(name):
        original=base(name)
        async def respond(messages,info):
            if name.startswith('specialist_'):
                fetched=returns(messages,'fetch_sources')
                if fetched:
                    allowed=json.loads(info.instructions.split('Current citation permissions from Python: source_refs may use only ')[1].split('; rule_refs')[0])
                    exact={s['id'] for packet in fetched for s in data(packet)['excerpts']}
                    assert exact<=set(allowed) and 'preview-only-unfetched' not in allowed
                    checked.append(name)
            return await original.function(messages,info)
        return FunctionModel(respond)
    service=SafetyChat(chat_rt);session=service.create('Citation permission fixture','local')
    job=service.send(session.id,ChatMessageInput(message='Offline citation instruction regression',idempotency_key='permission-test'),'local')
    done=await execute(chat_rt,job,factory)
    assert done.status=='completed',done.error
    assert set(checked)=={'specialist_applicability','specialist_vehicles'}
