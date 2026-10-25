"""Bounded text-only boss, source specialists and independent challenge."""
import asyncio
from safety_app.agents.safety_reviewer.chat_models import ChatCitation,ChatResult
from safety_app.agents.safety_reviewer import chat_agent
from safety_app.agents.challenge_reviewer import chat_agent as challenge_agent
from safety_app.modules.safety_review.catalog import owner_for
from safety_app.infrastructure.budgets import BudgetBlocked,CompletionHeadroomReached
from safety_app.workflows.agent_stage import TaskInput, run_stage
from safety_app.agents.shared.contracts import retain_source_gaps,comparison_record,clause_id
import json
import hashlib

class ChatTeam:
    def __init__(self,rt,job,model_factory):
        self.rt=rt;self.job=job;self.model_factory=model_factory;self.results=[];self.owners={};self.calls=[];self.failed=False
        self.limits=rt.config.get('review_team',{})
        self.deferred_questions=set()
    def active(self):return list({r.question_id:r for r in self.results}.values())
    async def delegate(self,task,boss):
        if boss.cancelled():raise asyncio.CancelledError()
        if len(self.calls)>=self.limits.get('max_delegations',8):raise BudgetBlocked('Delegation allowance exhausted')
        if task.specialist!='applicability' and not any(r.specialist=='applicability' for r in self.results):raise ValueError('Delegate applicability first')
        if task.question_id in self.owners and self.owners[task.question_id]!=task.specialist:raise ValueError('Question already owned by another specialist')
        prior=[r for r in self.results if r.question_id==task.question_id]
        if task.phase=='follow_up':
            if not prior or task.previous_call_id!=prior[-1].call_id:raise ValueError('Follow-up must cite the latest prior call')
        elif prior:raise ValueError('Existing question requires explicit follow-up')
        if len(prior)>=self.limits.get('max_calls_per_question',2):raise BudgetBlocked('Question follow-up allowance exhausted')
        self.owners[task.question_id]=task.specialist;self.calls.append(task)
        call_id='chat-delegation-'+str(len(self.calls))
        self.rt.audit.write('specialist_dispatched',run_id=boss.run_id,parent_run_id=self.job.run_id,job_id=self.job.id,agent_name='specialist_'+task.specialist,arguments=task.model_dump(mode='json'),provenance={'call_id':call_id})
        def setup(d):
            d.specialist=task.specialist;d.output_check=lambda r:self.validate_child(r,d,task)
            discovery=d.sources.discover(task.question,'US-federal-OSHA','',3,d.scope.site_id,d.scope.source_ids,task.specialist,d.event_date)
            if discovery.candidates:
                packet=d.sources.fetch_packet([discovery.candidates[0].id],d.scope.site_id,d.scope.source_ids,d.source_bytes,d.event_date)
                d.prepared_sources=packet;d.record_source_packet(packet)
                self.rt.audit.write('source_packet_prepared',run_id=d.run_id,parent_run_id=boss.run_id,job_id=self.job.id,agent_name=d.agent_name,output=packet.model_dump(mode='json'))
        inputs=TaskInput({'task':task.model_dump(mode='json'),'message':self.job.payload['message'],'history':self.job.payload.get('history',[]),'event_date':self.job.payload.get('event_date'),'prior_assessment':prior[-1].model_dump(mode='json') if prior else None})
        try:
            result,d=await run_stage(self.rt,self.job,'specialist_'+task.specialist,inputs,self.model_factory,setup=setup,parent_run_id=boss.run_id,agent_builder=lambda m,d:chat_agent.create_specialist(m,d,task.specialist),text_only=True)
        except CompletionHeadroomReached:
            # No completed specialist assessment exists; do not manufacture claims.
            self.deferred_questions.add(task.question_id)
            from safety_app.agents.safety_reviewer.chat_models import ChatAssessment
            result=ChatAssessment(call_id=call_id,question_id=task.question_id,specialist=task.specialist,answer='Assessment incomplete: workflow request capacity was preserved for synthesis and independent challenge.',missing_context=[f'{task.specialist}/{task.question_id}: assessment incomplete due to request allowance; this domain was not fully assessed.'])
            self.results.append(result)
            self.rt.audit.write('specialist_deferred',run_id=boss.run_id,job_id=self.job.id,agent_name='safety_reviewer',output=result.model_dump(mode='json'))
            return result
        except BaseException:self.failed=True;raise
        result.call_id=call_id;result.question_id=task.question_id;self.results.append(result)
        boss.seen_sources.update(d.seen_sources);boss.seen_rules.update(d.seen_rules)
        boss.fetched_source_ids.update(d.fetched_source_ids)
        self.rt.audit.write('specialist_completed',run_id=d.run_id,parent_run_id=boss.run_id,job_id=self.job.id,agent_name=d.agent_name,output=result.model_dump(mode='json'))
        return result
    def validate_child(self,result,d,task):
        if not result.question_id:result.question_id=task.question_id
        if result.specialist!=task.specialist or result.question_id!=task.question_id:raise ValueError('Specialist identity/question mismatch')
        result.missing_context=retain_source_gaps(result.missing_context,d.source_gaps)
        candidates=[c.model_copy(deep=True) for c in result.claims]
        moved=[]
        for i,c in enumerate(candidates):
            if not c.id:c.id=f'claim-slot-{i+1}'
            omitted=set(c.source_refs)-d.seen_sources
            if (c.kind=='uncertain' and c.exact_clause is None and not (set(c.rule_refs)-d.seen_rules)
                    and omitted and omitted<=getattr(d,'budget_omitted_source_ids',set())):
                c.source_refs=[id for id in c.source_refs if id not in omitted]
                moved.append({'claim_slot':i+1,'source_ids':sorted(omitted)})
        validate_claims(candidates,d,task.specialist)
        result.claims=candidates
        for change in moved:
            for id in change['source_ids']:
                result.missing_context=retain_source_gaps(result.missing_context,{f'{id}: exact text was omitted by the evidence budget; referenced only as a retrieval gap, not supporting evidence.'})
        if moved:
            self.rt.audit.write('chat_gap_reference_retained',run_id=d.run_id,job_id=d.job_id,agent_name=d.agent_name,output={'moved_references':moved})
        for c in result.claims:
            payload=json.dumps(comparison_record(c.model_dump(exclude={'id'})),sort_keys=True)
            c.id='claim-'+hashlib.sha256((task.question_id+'\n'+payload).encode()).hexdigest()[:24]
    def validate_boss(self,result,d):
        if self.failed:raise ValueError('Required delegation failed')
        if not any(r.specialist=='applicability' for r in self.results):raise ValueError('Boss must delegate applicability')
        active=self.active();claims=[c for r in active for c in r.claims]
        allowed={json.dumps(comparison_record(c.model_dump()),sort_keys=True) for c in claims}
        if any(json.dumps(comparison_record(c.model_dump()),sort_keys=True) not in allowed for c in result.claims):raise ValueError('Boss invented or changed specialist claims')
        # Narration is model-owned; the complete evidence-bearing claim list is code-owned.
        result.claims=[c.model_copy(deep=True) for c in claims]
        result.missing_context=retain_source_gaps(result.missing_context,{g for r in active for g in r.missing_context})
        result.completion='partial' if self.deferred_questions else 'complete'
        validate_claims(result.claims,d)

def validate_claims(claims,d,specialist=None):
    if len({c.id for c in claims})!=len(claims):raise ValueError('Duplicate claim IDs')
    errors=[]
    for c in claims:
        if not set(c.source_refs)<=d.seen_sources or not set(c.rule_refs)<=d.seen_rules:
            errors.append(f'{c.id}: claim cites unseen source or rule. Unfetched source_refs: {sorted(set(c.source_refs)-d.seen_sources)}; unretrieved rule_refs: {sorted(set(c.rule_refs)-d.seen_rules)}. Only exact fetched excerpts belong in source_refs, even for uncertain claims. A discovery preview or omitted packet entry is not evidence. Fetch the individual source if needed; otherwise remove its reference and describe the missing evidence in missing_context. Do not replace it with an unrelated citation.')
            continue
        if c.kind!='uncertain' and not (c.source_refs or c.rule_refs):errors.append(f'{c.id}: safety claims need retrieved citations or uncertain classification; retrieve evidence or set kind uncertain')
        if c.kind=='requirement':
            sources=[d.sources.read(id,None,d.scope.site_id) for id in c.source_refs]
            if not c.rule_refs and not any(s.source_type=='requirement' and s.clause and clause_id(s.clause)==clause_id(c.exact_clause) and (not specialist or owner_for(s.model_dump())==specialist) for s in sources):
                available=[{'source_id':s.id,'stored_clause':s.clause,'source_type':s.source_type,'owner':owner_for(s.model_dump())} for s in sources]
                errors.append(f'{c.id}: requirement claim lacks exact mandatory clause in its domain. Requested {c.exact_clause!r}; retrieved metadata: {available}. exact_clause must equal an eligible stored_clause; do not infer a deeper indexed ID from paragraph text. Otherwise use kind uncertain and disclose the missing exact citation.')
    if errors:raise ValueError('\n'.join(errors))

async def chat(rt,job,model_factory=None):
    if job.payload['mode']=='fixture':
        if model_factory is not None:raise ValueError('Fixture supplies its own local models')
        from safety_app.workflows.chat_fixture import models
        model_factory=models()
    team=ChatTeam(rt,job,model_factory)
    def setup(d):d.delegate=team.delegate;d.output_check=lambda r:team.validate_boss(r,d)
    async with asyncio.timeout(rt.config['limits']['workflow_seconds']):
        response,boss=await run_stage(rt,job,'safety_reviewer',TaskInput({k:job.payload[k] for k in ('message','history','event_date','mode')}),model_factory,setup=setup,agent_builder=chat_agent.create,text_only=True)
        citations=[]
        for id in sorted({id for c in response.claims for id in c.source_refs}):
            s=rt.sources.read(id,None,job.site_id)
            citations.append(ChatCitation(id=s.id,title=s.title,section=s.section,clause=s.clause,url=s.url,version=s.version,text=s.text,source_type=s.source_type,jurisdiction=s.jurisdiction,effective_date=s.effective_date,effective_until=s.effective_until,context_complete=s.context_complete))
        challenge,_=await run_stage(rt,job,'challenge_reviewer',TaskInput({'response':response.model_dump(mode='json'),'citations':[c.model_dump(mode='json') for c in citations],'event_date':job.payload.get('event_date'),'mode':job.payload['mode'],'rules':[rt.db.get('rule',id) for id in sorted(boss.seen_rules)],'scenario':'Unverified user text; no observed footage.'}),model_factory,boss,agent_builder=challenge_agent.create,text_only=True)
        ids={c.id for c in response.claims}
        if set(challenge.checked_claim_ids)!=ids or len(challenge.checked_claim_ids)!=len(ids):raise ValueError('Challenger must check every claim exactly once')
        if not {i.claim_id for i in challenge.issues}<=ids:raise ValueError('Challenger cited an unknown claim')
        result=ChatResult(id=job.run_id,session_id=job.payload['session_id'],site_id=job.site_id,mode=job.payload['mode'],response=response,challenge=challenge,delegations=team.results,citations=citations)
        rt.db.save_many([('chat_result',result)],job.id)
        return [result.id]
