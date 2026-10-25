"""Shared bounded answer execution, routing, source retrieval and block validation."""
import asyncio
import re
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Annotated
from pydantic import Field
from urllib.parse import urlsplit
from openai import AsyncOpenAI
from pydantic_ai.messages import CachePoint
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits
from safety_app.domain.chat_lab import LabAnswer,LabApplicability,LabChallengeAssessment,LabClaimCheck,LabRouting,LabScopeResult,LabCitation,LabPlan,LabAssignment,LabDraft,LabExecutionContext,LabRecovery,LabClaim
from safety_app.domain.models import uid,utc
from safety_app.infrastructure.budgets import BudgetLedger,BudgetBlocked
from safety_app.infrastructure.decisions import classify,ROUTING_QUESTIONS,FINAL_QUESTIONS
from safety_app.infrastructure.exact_cache import ExactCache,fingerprint
from safety_app.modules.safety_review.lab_knowledge import LabKnowledge
from safety_app.modules.safety_review.chat_activity import BranchAudit
from safety_app.modules.safety_review.lab_service import ChatLab,CASES
from safety_app.agents.safety_reviewer.lab_agent import create,output_failure,answer_contract
from safety_app.agents.challenge_reviewer.lab_agent import create as create_challenge
from safety_app.agents.challenge_reviewer.lab_checks import evaluate_challenge,LabReviewBlocked
from safety_app.agents.safety_reviewer.lab_agent import LabOutputRejected
from safety_app.agents.shared.lab_release import render_answer,claim_errors,release_blocks

SCOPE_POLICY=Path(__file__).parents[1]/'agents/safety_reviewer/lab_scope.md'


def fixture_route(job):
    case=next((c for c in CASES if c.id==job.payload['request'].get('test_case_id') and c.question==job.payload['question']),None)
    scope=case.expected_scope if case else 'clarify';tag=set(case.tags if case else [])
    return LabRouting(scope=scope,task='product' if 'product' in tag or case and case.id=='quoted' else 'records' if 'records' in tag else 'safety',domain='health' if 'freshness' in tag else 'emergency' if 'emergency' in tag else 'vehicles',freshness='required' if 'freshness' in tag else 'unneeded',confidence=1)


def validate_answer(answer,evidence,applicability,domain=None):
    errors=[]
    if len({c.id for c in answer.claims})!=len(answer.claims):errors.append('Duplicate claim IDs')
    for claim in answer.claims:
        if not set(claim.citation_ids)<=evidence.keys():errors.append(f'{claim.id}: citation not retrieved')
        citations=[evidence[id] for id in claim.citation_ids if id in evidence]
        if claim.kind not in ('uncertain',) and not citations:errors.append(f'{claim.id}: unsupported claim')
        if claim.kind=='requirement':
            from safety_app.modules.safety_review.catalog import owner_for
            owners={owner_for({'clause':c.clause or '', 'section':c.provenance.get('section','')}) for c in citations if c.kind=='requirement'}
            if domain and owners-{'unmapped','applicability',domain}:errors.append(f'{claim.id}: requirement outside assigned domain')
            if not applicability or not applicability.requirements_allowed:errors.append(f'{claim.id}: applicability unresolved')
            if not any(c.kind=='requirement' and c.context_complete and c.clause==claim.exact_clause and c.effective_date for c in citations):errors.append(f'{claim.id}: exact, dated, complete requirement context missing')
        if claim.kind=='record' and any(c.kind!='record' for c in citations):errors.append(f'{claim.id}: not a saved review source')
        if claim.kind=='product' and any(c.kind!='product' for c in citations):errors.append(f'{claim.id}: not product documentation')
    return errors


class BranchRunner:
    def __init__(self,rt,job,branch,client):
        self.rt,self.job,self.branch,self.client=rt,job,branch,client
        self.policy=job.payload['prompts']['safety_reviewer/lab_scope.md']
        self.config=job.payload['config'];self.paid=job.payload['mode']=='hosted'
        self.audit=BranchAudit(rt.audit,job,branch)
        self.ledger=BudgetLedger(rt.db,self.audit,job.payload['agents_config'])
        self.ledger.workflow_id=job.run_id;self.ledger.site_id=job.site_id
        self.knowledge=LabKnowledge(rt.root,job.payload['snapshot'],job.site_id)
        self.cache=ExactCache(rt.db,{'site':job.site_id,'permissions':job.payload['snapshot']['permissions'],'context':job.payload['snapshot']['id'],'policy':self.config['policy_version'],'model':'gpt-6-luna','workflow':self.config['version'],'mode':job.payload['mode'],'event_date':job.payload['request'].get('event_date')})
        self.previous=job.run_id;self.calls=0;self.web_calls=0;self.web_evidence=[];self.messages=[];self.gaps=[]
        self.started=time.monotonic();self.recovery=LabRecovery();self.pool=None;self.recovery_ledger=None;self.route=None;self.last_challenge=None
        self.deadline=self.started+job.payload['agents_config']['limits']['workflow_seconds'];self.repair_feedback={}
        self.routing_questions=ROUTING_QUESTIONS;self.final_questions=FINAL_QUESTIONS
    def stage_content(self,payload):return []
    def stage_tools(self,payload,node,audit):return []
    def reference_tools_allowed(self,payload):return True
    def search_question(self):return self.job.payload['question']
    def create_agent(self,model,context,contract,tools,native,challenge,payload):
        return (create_challenge if challenge else create)(model,context,contract,tools,**({'native':native} if not challenge else {}))
    def cancelled(self):return self.rt.jobs.cancelled(self.job.id)
    def check(self):
        if self.cancelled():raise asyncio.CancelledError()
        if self.paid and self.ledger.state('stopped')=='1':raise BudgetBlocked('AI stopped')
    def begin(self,name,api='responses',assignment=None):
        self.check();node=uid();audit=BranchAudit(self.rt.audit,self.job,self.branch,node,api)
        audit.write('handoff',run_id=node,parent_run_id=self.previous,agent_name=name,arguments={'from_node':self.previous,'to_node':node,'message':'Assigned bounded task and relevant evidence; no hidden reasoning.','assignment':assignment or {}})
        audit.write('agent_started',status='running',run_id=node,parent_run_id=self.previous,agent_name=name,configuration={'model':None if api=='local' else 'gpt-6-luna','api':api})
        self.previous=node
        return node,audit,time.monotonic()
    def end(self,node,audit,start,name,state='completed'):
        audit.write('agent_finished',status=state,run_id=node,agent_name=name,duration_ms=(time.monotonic()-start)*1000)
    async def tool(self,node,audit,name,args,callback,category='retrieval',fresh=False):
        self.check();self.ledger.tool(node);call=uid();begin=time.monotonic()
        audit.write('tool_started',status='running',run_id=node,tool_name=name,tool_call_id=call,arguments=args)
        try:
            value=None if fresh else self.cache.get(category,{'tool':name,**args})
            if value is not None:audit.write('cache_hit',run_id=node,tool_name=name,resources={'category':category})
            else:
                value=callback()
                if hasattr(value,'__await__'):value=await value
                self.cache.put(category,{'tool':name,**args},value,self.config['retrieval_ttl_seconds'])
            if name=='read_sources':
                for item in value['sources']:self.knowledge.seen[item['id']]=LabCitation.model_validate(item)
            self.gaps.extend(value.get('gaps',[]))
            self.check()
            audit.write('tool_finished',run_id=node,tool_name=name,tool_call_id=call,output=value,duration_ms=(time.monotonic()-begin)*1000)
            return value
        except BaseException:
            audit.write('tool_finished',status='cancelled' if self.cancelled() else 'failed',run_id=node,tool_name=name,tool_call_id=call,duration_ms=(time.monotonic()-begin)*1000);raise
    async def retrieve(self,node,audit,question,kind):
        found=await self.tool(node,audit,'search_library',{'query':question[:500],'collection':kind},lambda:self.knowledge.search(question[:500],kind,3))
        ids=[x['id'] for x in found['matches']]
        if ids:return await self.tool(node,audit,'read_sources',{'ids':ids},lambda:self.knowledge.read(ids))
        return {'sources':[],'gaps':found['gaps']}
    async def decisions(self,final=None):
        name='Final scope check' if final else 'Scope · route · freshness';node,audit,start=self.begin(name,'decisions')
        try:
            args={'policy':fingerprint(self.policy),'question':self.job.payload['question'],'answer':final.model_dump() if final else None}
            cached=self.cache.get('classification',args)
            if cached is not None:
                audit.write('cache_hit',run_id=node,resources={'category':'classification'});result=cached
            elif not self.paid:
                await asyncio.sleep(self.config['fixture_step_seconds'])
                route=fixture_route(self.job)
                result={'scope':{'value':'allow' if final else route.scope,'confidence':1}} if final else {key:{'value':value,'confidence':1} for key,value in route.model_dump().items() if key in ('scope','task','domain','freshness')}
                audit.write('fixture_classification',run_id=node,output=result,resources={'usage_is_synthetic':True})
            else:
                result=await classify(self.client,self.recovery_ledger if final and self.pool else self.ledger,audit,self.job,node,self.policy,json.dumps(args),self.final_questions if final else self.routing_questions,self.cancelled,allow_terminal_scope=not final)
            self.check();self.cache.put('classification',args,result,self.config['classification_ttl_seconds'])
            threshold=self.config['classification_confidence']
            confidence=result['scope']['confidence'];scope=result['scope']['value'] if confidence>=threshold else 'clarify'
            if final:value=LabScopeResult(scope=scope,confidence=confidence)
            elif scope!='allow' and any(key not in result for key in ('task','domain','freshness')):
                value=LabRouting(scope=scope,confidence=confidence,task='unclear',domain='applicability',freshness='unneeded',policy_version=self.config['policy_version'])
            else:
                task=result['task']['value'] if result['task']['confidence']>=threshold else 'unclear'
                domain=result['domain']['value'] if result['domain']['confidence']>=threshold and task!='unclear' else 'multiple'
                freshness=result['freshness']['value'] if result['freshness']['confidence']>=threshold else 'required'
                value=LabRouting(scope=scope,confidence=confidence,task=task,domain=domain,freshness=freshness,policy_version=self.config['policy_version'])
            audit.write('classification_completed',run_id=node,output={'classifications':result,'effective_scope':scope,'confidence':confidence,'effective_routing':None if final else value.model_dump()})
            self.end(node,audit,start,name)
            return value
        except BaseException:
            self.end(node,audit,start,name,'cancelled' if self.cancelled() else 'failed');raise
    def known_usage(self):
        with self.rt.db.connect() as db:
            return not db.execute("SELECT 1 FROM reservations WHERE workflow_id=? AND state IN ('active','unknown') LIMIT 1",(self.job.run_id,)).fetchone()
    def start_recovery(self,codes):
        self.check()
        if self.recovery.attempts or not self.known_usage():return False
        if time.monotonic()+self.config.get('recovery_seconds',45)>self.deadline:return False
        node,audit,start=self.begin('Correction allowance','local')
        if self.paid:
            self.pool=self.ledger.open_pool(node,self.job.run_id,self.config.get('recovery_usd',.01))
            self.recovery_ledger=BudgetLedger(self.rt.db,self.audit,self.job.payload['agents_config'])
            self.recovery_ledger.pool_id=self.pool
            self.recovery_ledger.workflow_id=self.job.run_id;self.recovery_ledger.site_id=self.job.site_id
        self.recovery.attempts=1;self.recovery.issue_codes=list(dict.fromkeys(codes))
        audit.write('recovery_started',run_id=node,resources={'attempt':1,'limit_usd':.01,'issue_codes':self.recovery.issue_codes})
        self.end(node,audit,start,'Correction allowance')
        return True
    def close_recovery(self):
        if self.pool:
            with self.rt.db.connect() as db:
                row=db.execute('SELECT COALESCE(sum(r.actual),0),COALESCE(sum(CASE WHEN r.actual IS NULL THEN r.amount ELSE 0 END),0) FROM reservations r JOIN reservation_pool_members m ON r.id=m.reservation_id WHERE m.pool_id=?',(self.pool,)).fetchone()
            self.recovery.calculated_usd=row[0]/1e6;self.recovery.reserved_usd=row[1]/1e6
            self.ledger.close_pool(self.pool)
    async def stage(self,name,output_type,payload,fixture,collection=None,native=False,challenge=False,correction=False):
        try:
            return await self._stage_once(name,output_type,payload,fixture,collection,native,challenge,correction)
        except LabOutputRejected as exc:
            if not getattr(exc,'recoverable',False) or native or correction or not self.start_recovery(['output_contract']):raise
            # One new request, no tools or web. The exact evidence is reused.
            repaired={**payload,'correction':'Return a result matching the assigned schema. '+str(exc),'shared_evidence':[v.model_dump() for v in self.knowledge.seen.values()]}
            value=await self._stage_once('Correction · '+name,output_type,repaired,fixture,None,False,challenge,True)
            self.recovery.succeeded=True
            return value
    async def _stage_once(self,name,output_type,payload,fixture,collection=None,native=False,challenge=False,correction=False):
        self.calls+=1
        if self.calls>self.config['max_agent_calls_per_branch']+2*self.recovery.attempts:raise BudgetBlocked('Branch agent limit reached')
        node,audit,start=self.begin(name,assignment={k:payload[k] for k in ('task','domain','assigned_question') if k in payload})
        try:
            task=payload.get('question_type','safety')
            stage_question=self.search_question() if native else self.job.payload['question']
            bound_collection=collection or ('product' if task=='product' else 'records' if task=='records' else 'references')
            evidence=await self.retrieve(node,audit,stage_question,collection) if collection else {'sources':[],'gaps':[]}
            async def search_library(query:str)->dict:
                """Search frozen authorized references, site rules, product docs or saved review records; at most five matches."""
                return await self.tool(node,audit,'search_library',{'query':query,'collection':bound_collection},lambda:self.knowledge.search(query,bound_collection))
            async def read_sources(ids:Annotated[list[str],Field(json_schema_extra={'minItems':1,'maxItems':5})])->dict:
                """Read one to five whole authorized source blocks; oversized text is an explicit gap."""
                return await self.tool(node,audit,'read_sources',{'ids':ids,'collection':bound_collection},lambda:self.knowledge.read_for_task(ids,bound_collection))
            from safety_app.modules.safety_review.catalog import PROFILES
            domain=payload.get('domain') or ('applicability' if name=='Applicability specialist' else None)
            execution=LabExecutionContext(task='regulatory' if name=='Applicability specialist' or payload.get('regulatory') else task if task in ('product','records','safety','video') else 'coordination',domain=domain if task not in ('product','records') else None,collections=[bound_collection])
            profile=('Explain only the documented product operation. Do not assess laws or jurisdiction.' if task=='product' else 'Summarize only saved reviews and linked human decisions, preserving uncertainty.' if task=='records' else PROFILES.get(domain,''))
            payload={**payload,'execution':execution.model_dump(),'original_question':stage_question}
            context=SimpleNamespace(profile=profile,prompts=self.job.payload['prompts'],paid=self.paid,config=self.config,ledger=self.recovery_ledger if self.pool and (correction or challenge) else self.ledger,audit=audit,node=node,job=self.job,name=name,cancelled=self.cancelled,workflow_kind=self.job.kind)
            model=OpenAIResponsesModel('gpt-6-luna',provider=OpenAIProvider(openai_client=self.client)) if self.paid else TestModel(call_tools=[],custom_output_args=fixture.model_dump() if hasattr(fixture,'model_dump') else None,custom_output_text=fixture if isinstance(fixture,str) else None)
            contract=answer_contract(output_type,payload.get('applicability'),payload.get('question_type'))
            stage_tools=[] if native or challenge or correction or output_type==LabRouting else [*([search_library,read_sources] if self.reference_tools_allowed(payload) else []),*self.stage_tools(payload,node,audit)]
            agent=self.create_agent(model,context,contract,stage_tools,native,challenge,payload)
            settings={'max_tokens':self.config['max_output_tokens'],'openai_reasoning_effort':'low','openai_store':False,'openai_prompt_cache_options':{'mode':'explicit','ttl':'30m'}}
            if native:settings.update(openai_native_tools=[{'type':'web_search','search_context_size':'low','filters':{'allowed_domains':self.config['primary_domains']}}],openai_include_web_search_sources=True,openai_include_raw_annotations=True,extra_body={'max_tool_calls':1,'tool_choice':'required'})
            if not self.paid:await asyncio.sleep(self.config['fixture_step_seconds'])
            content=['Read-only safety assistance. Stable policy and tool definitions precede this cache boundary.',CachePoint(),json.dumps({'role':name,'question':stage_question,'assignment':payload,'evidence':evidence,'event_date':self.job.payload['request'].get('event_date')}),*self.stage_content(payload)]
            result=await agent.run(content,model_settings=settings,usage_limits=UsageLimits(request_limit=1 if native or correction or challenge else 4,tool_calls_limit=8,input_tokens_limit=self.ledger.config['limits']['input_tokens'],output_tokens_limit=self.ledger.config['limits']['output_tokens']))
            self.check()
            if native:self.capture_web(result)
            if output_type in (LabRouting,LabPlan):audit.write('classification_completed' if output_type==LabRouting else 'assignment_completed',run_id=node,output=result.output.model_dump())
            self.end(node,audit,start,name)
            return result.output
        except BaseException as exc:
            error,issues=output_failure(name,exc)
            if error is not exc:
                error.recoverable=bool(issues)
                audit.write('validation_failed',status='failed',run_id=node,agent_name=name,resources={'validation_issues':issues,'output_retry_limit':0})
            self.end(node,audit,start,name,'cancelled' if self.cancelled() else 'failed')
            if error is not exc:raise error from exc
            raise
    def capture_web(self,result):
        urls={};attributions=[]
        def walk(value):
            if isinstance(value,dict):
                url=value.get('url')
                if isinstance(url,str) and urlsplit(url).scheme=='https':urls[url]=str(value.get('title','Web source'))[:300]
                for x in value.values():walk(x)
            elif isinstance(value,list):
                for x in value:walk(x)
        count=0
        for message in result.all_messages():
            for part in message.parts:
                if getattr(part,'part_kind','') in ('builtin-tool-return','text'):
                    walk(getattr(part,'provider_details',None))
                    if getattr(part,'part_kind','')=='builtin-tool-return':walk(getattr(part,'content',None));count+=1
                    else:
                        content=getattr(part,'content','')
                        for annotation in (getattr(part,'provider_details',None) or {}).get('annotations',[]):
                            start,end=annotation.get('start_index'),annotation.get('end_index');url=annotation.get('url','')
                            if urlsplit(url).scheme=='https' and isinstance(start,int) and isinstance(end,int) and 0<=start<end<=len(content):
                                attributions.append({'url':url,'start_index':start,'end_index':end,'annotated_text':content[start:end],'basis':'Provider citation span only; no attribution beyond this span is inferred.'})
        if not urls or not count:raise ValueError('Native search returned no verifiable source provenance')
        sources=[{'url':url,'title':title} for url,title in list(urls.items())[:8]]
        item=LabCitation(id='web:'+fingerprint({'sources':sources,'text':str(result.output)})[:20],title='Web discovery · source summary bundle',text=str(result.output),kind='web_summary',version='live:'+utc(),retrieved_at=utc(),provenance={'provider':'OpenAI native web_search','verified_regulatory_text':False,'sources':sources,'citation_spans':attributions[:24],'attribution':'Summary bundle; individual source support is not inferred outside provider citation spans.','query':self.search_question()})
        self.knowledge.seen[item.id]=item;self.web_evidence.append(item)
    async def web(self,fresh):
        self.web_calls+=1
        if self.web_calls>self.config['max_web_searches_per_branch']:raise BudgetBlocked('Native search limit reached')
        args={'query':self.search_question(),'domains':self.config['primary_domains']}
        cached=None if fresh else self.cache.get('web_discovery',args)
        if cached:
            node,audit,start=self.begin('Web evidence reuse','local');audit.write('cache_hit',run_id=node,output={'sources':cached},resources={'category':'web_discovery'})
            for x in cached:
                item=LabCitation.model_validate(x);self.knowledge.seen[item.id]=item;self.web_evidence.append(item)
            self.end(node,audit,start,'Web evidence reuse');return
        if not self.paid:
            node,audit,start=self.begin('OpenAI web search · fixture','web_search');call=uid();audit.write('native_tool_started',status='running',run_id=node,tool_name='web_search',tool_call_id=call,arguments=args)
            await asyncio.sleep(self.config['fixture_step_seconds']);audit.write('native_tool_finished',run_id=node,tool_name='web_search',tool_call_id=call,duration_ms=(time.monotonic()-start)*1000,output={'notice':'Fixture: no web request made; current evidence remains missing.'});self.end(node,audit,start,'OpenAI web search · fixture');return
        await self.stage('Current source discovery',str,{'task':'Search primary authorities once for the actual information requested. For injury/emergency questions, prioritize immediate medical assistance and workplace first-aid guidance; equipment hazard lists alone do not answer an immediate-response question. Summarize conflicts and dates; no unsupported regulatory conclusions.'},'Fixture web summary',native=True)
        self.cache.put('web_discovery',args,[c.model_dump() for c in self.web_evidence],self.config['web_ttl_seconds'])
    async def review(self,answer,applicability,task):
        ids={id for c in answer.claims for id in c.citation_ids}|set(applicability.citation_ids if applicability else [])
        evidence=[c.model_dump() for c in self.knowledge.seen.values() if c.id in ids or task=='records' and c.kind=='record']
        checks=LabChallengeAssessment(claim_checks=[LabClaimCheck(claim_id=c.id,verdict='general' if c.kind=='general' else 'uncertain' if c.kind=='uncertain' else 'supported',independently_publishable=True,note='Fixture judgment.') for c in answer.claims])
        assessment=await self.stage('Independent challenge' if not self.recovery.attempts else 'Recheck corrected answer',LabChallengeAssessment,{'question_type':task,'answer':answer.model_dump(),'evidence':evidence,'applicability':applicability.model_dump() if applicability else None},checks,challenge=True)
        errors=claim_errors(answer,self.knowledge.seen,applicability,task)
        self.repair_feedback={'local_errors':errors,'review':assessment.model_dump()}
        provisional_ids={c.id for c in answer.claims if c.kind=='guidance' and any(self.knowledge.seen[id].kind=='web_summary' for id in c.citation_ids if id in self.knowledge.seen)}
        kept,challenge=release_blocks(answer,assessment,errors,provisional_ids)
        self.last_challenge=challenge
        self.audit.write('challenge_checked',run_id=self.previous,agent_name='Independent challenge',status='ok' if challenge.approved else 'blocked',output={'approved':challenge.approved,'published_claim_ids':challenge.published_claim_ids,'omitted_claim_ids':challenge.omitted_claim_ids,'failure_codes':challenge.failure_codes})
        return kept,challenge
    def correction_evidence(self,draft,task,applicability):
        ids={id for claim in draft.claims for id in claim.citation_ids}|set(applicability.citation_ids if applicability else [])
        evidence={id:c for id,c in self.knowledge.seen.items() if id in ids or task=='records' and c.kind=='record'}
        # A misspelled/nonexistent citation must not remove the actual retrieved document
        # from the correction packet. Reuse a bounded set; never fetch new evidence here.
        if ids-self.knowledge.seen.keys():
            allowed={'product':{'product'},'records':{'record'}}.get(task,{'requirement','guidance','procedure','site_rule','web_summary'})
            nearby=[c for c in self.knowledge.seen.values() if c.kind in allowed and c.id not in evidence]
            evidence.update((c.id,c) for c in nearby[:5])
        return [c.model_dump() for c in evidence.values()]
    async def run(self,route=None):
        if route is None:
            if self.branch=='optimized':route=await self.decisions()
            else:route=await self.stage('Current scope and routing',LabRouting,{'policy':self.policy,'task':'Classify scope, task, domain and freshness only.'},fixture_route(self.job))
        route.policy_version=self.config['policy_version'];self.route=route
        if route.confidence<self.config['classification_confidence']:route.scope='clarify'
        if route.scope!='allow':
            return route,LabAnswer(answer='I can help with workplace safety, product operation, or saved reviews. Please ask about one of those topics.' if route.scope=='block' else 'Please specify the safety, product, or saved-review question you want answered.',outcome='declined' if route.scope=='block' else 'clarification',completion='clarification'),None,[]
        if self.job.payload['request'].get('test_case_id')=='interrupted' and not self.paid:
            node,audit,start=self.begin('Interrupted provider fixture');audit.write('stream_interrupted',status='failed',run_id=node);self.end(node,audit,start,'Interrupted provider fixture','failed');raise RuntimeError('Fixture provider stream interrupted')
        task=route.task if route.task in ('product','records') else 'safety'
        collection='product' if task=='product' else 'records' if task=='records' else 'references'
        regulatory=task=='safety' and bool(re.search(r'\b(osha|regulat\w*|legal|law|requirements?|standards?|compliance)\b',self.job.payload['question'],re.I))
        assignments=[LabAssignment(domain=route.domain if route.domain!='multiple' else 'applicability',question=self.job.payload['question'][:700])]
        if self.branch=='current' or task=='safety' and route.domain=='multiple':
            plan=await self.stage('Task coordination',LabPlan,{'question_type':task,'task':'Assign at most three focused subtasks of the ORIGINAL question. Do not answer it or replace it with a conclusion. Product and record tasks use the applicability enum only as a placeholder, never a regulatory assessment. Use one assignment for product/records.','route':route.model_dump()},LabPlan(assignments=assignments),collection=collection)
            if task=='safety':assignments=plan.assignments
        applicability=None
        if regulatory:
            applicability=await self.stage('Applicability specialist',LabApplicability,{'question_type':'safety','task':'Assess legal applicability only. Missing facts do not prevent subsequent general guidance.'},LabApplicability(basis='Fixture: applicability unresolved.'),collection='references')
            if not set(applicability.citation_ids)<=self.knowledge.seen.keys() or applicability.requirements_allowed and (applicability.jurisdiction=='unknown' or not applicability.citation_ids):
                applicability=LabApplicability(basis='The assessment did not establish applicability.',missing_context=['Legal applicability remains unresolved.'])
        if task=='safety':
            if not self.knowledge.seen:
                node,audit,start=self.begin('Evidence lookup','local');await self.retrieve(node,audit,self.job.payload['question'],collection);self.end(node,audit,start,'Evidence lookup')
            if route.freshness=='required' or not self.knowledge.seen or any(c.kind=='requirement' and not c.context_complete for c in self.knowledge.seen.values()):await self.web(route.freshness=='required')
        fixture=LabDraft(claims=[LabClaim(id='fixture',text='Fixture demonstration only; this is not a safety assessment.',kind='uncertain')],missing_context=['Hosted quality requires live evaluation.'])
        delegates=[]
        for assignment in assignments:
            payload={'question_type':task,'regulatory':regulatory,'task':'Answer the original question with ordered self-contained blocks. The assigned subtask is a focus, not a replacement for the original question.','domain':assignment.domain if task=='safety' else None,'assigned_question':assignment.question if task=='safety' else self.job.payload['question'],'applicability':applicability.model_dump() if applicability else None,'shared_evidence':[c.model_dump() for c in self.web_evidence]}
            draft=await self.stage('Product help' if task=='product' else 'Saved review lookup' if task=='records' else assignment.domain.title()+' specialist',LabDraft,payload,fixture,collection=collection)
            delegates.append(draft)
        draft=delegates[0]
        if self.branch=='current' or len(delegates)>1:
            draft=await self.stage('Answer synthesis',LabDraft,{'question_type':task,'task':'Combine the answer blocks into a direct answer to the original question. Preserve source citations, limitations and human decisions. Do not add regulatory context to product help.','drafts':[d.model_dump() for d in delegates],'evidence':[c.model_dump() for c in self.knowledge.seen.values() if c.id in {id for d in delegates for claim in d.claims for id in claim.citation_ids} or task=='records' and c.kind=='record'],'applicability':applicability.model_dump() if applicability else None},fixture)
        answer=render_answer(draft.claims,[*draft.missing_context,*self.gaps,*(applicability.missing_context if applicability else [])])
        node,audit,start=self.begin('Citation validation','local');errors=claim_errors(answer,self.knowledge.seen,applicability,task);audit.write('citations_validated',run_id=node,output={'claim_count':len(answer.claims),'issue_codes':errors});self.end(node,audit,start,'Citation validation')
        kept,challenge=await self.review(answer,applicability,task)
        if not challenge.approved and self.start_recovery(challenge.failure_codes):
            reviewed_answer=answer
            try:
                corrected=await self._stage_once('Correct answer blocks',LabDraft,{'question_type':task,'task':'Correct only the identified defects. Remove unsupported blocks or replace them with permitted labeled general guidance; preserve qualifications and original human decisions.','draft':draft.model_dump(),'feedback':self.repair_feedback,'issue_codes':challenge.failure_codes,'omitted_claim_ids':challenge.omitted_claim_ids,'evidence':self.correction_evidence(draft,task,applicability),'applicability':applicability.model_dump() if applicability else None},fixture,correction=True)
                answer=render_answer(corrected.claims,[*corrected.missing_context,*self.gaps,*(applicability.missing_context if applicability else [])])
                kept,challenge=await self.review(answer,applicability,task)
                self.recovery.succeeded=any(c.kind!='uncertain' for c in kept)
            except LabOutputRejected:
                answer=reviewed_answer
                self.audit.write('recovery_exhausted',status='blocked',resources={'issue_codes':['output_contract']})
        answer=render_answer(kept,[*(answer.missing_context if kept else []),*challenge.issues,*challenge.observations],omitted=not challenge.approved)
        if not kept:
            answer.answer={'product':'I could not verify the requested operation in the available product documentation. Specify the screen or control you are using so the answer can be checked against the right instructions.','records':'I could not verify the requested facts from the authorized saved reviews. Specify the review or finding; I will preserve its recorded uncertainty and human decisions.','safety':'I could not release reliable guidance for this situation from the available information. Describe the activity and immediate hazards, or provide the relevant safety reference.'}[task]
        if kept:
            final=await self.decisions(answer)
            if final.scope!='allow':
                answer=LabAnswer(answer='I could not confirm that the proposed response stays within this product’s scope. Please narrow the question to workplace safety, product operation, or a saved review.',outcome='clarification',completion='clarification')
        self.recovery.succeeded=bool(self.recovery.succeeded and answer.outcome in ('answered','partial'))
        return route,answer,challenge,list(self.knowledge.seen.values())


def public_failure(exc):
    if isinstance(exc,BudgetBlocked):return 'budget_unavailable','The available spending or request allowance is exhausted. Review the budget details before starting another comparison.'
    if isinstance(exc,asyncio.CancelledError):return 'cancelled','The comparison was stopped. Completed results remain available.'
    status=getattr(exc,'status_code',None)
    if status in (401,403):return 'authentication','The provider could not authenticate this request. Update the API key and relaunch the app.'
    if isinstance(exc,LabOutputRejected):return 'output_contract','The model did not return a usable result within the correction allowance. Please narrow the question or provide the relevant source.'
    return 'provider_unavailable','This workflow could not complete. Check the activity details and provider availability before trying again.'


