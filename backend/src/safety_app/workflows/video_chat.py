"""Recorded-review questions using shared answer checks and scoped visual tools."""
import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Annotated
from types import SimpleNamespace
from pydantic import Field
from pydantic_ai import BinaryContent, ToolReturn
from openai import AsyncOpenAI
from safety_app.domain.models import uid, Interval
from safety_app.domain.chat_lab import (LabAnswer,LabDraft,LabClaim,LabRouting,LabScopeResult,LabApplicability,
    LabChallengeAssessment,LabClaimCheck,LabPlan,LabAssignment)
from safety_app.domain.video_chat import VideoInspectionPlan,VideoChatContext
from safety_app.agents.safety_reviewer.lab_agent import VisualClaim,UncertainClaim,LabOutputRejected
from safety_app.agents.shared.lab_release import render_answer,claim_errors,release_blocks
from safety_app.infrastructure.budgets import BudgetLedger,BudgetBlocked,CompletionHeadroomReached
from safety_app.infrastructure.decisions import choice,ROUTING_QUESTIONS,FINAL_QUESTIONS
from safety_app.infrastructure.exact_cache import fingerprint
from safety_app.infrastructure.request_queue import queue_seconds,RequestQueueTimeout
from safety_app.modules.safety_review.video_chat import VideoChat
from safety_app.modules.safety_review.video_evidence import VideoEvidence
from safety_app.workflows.answering import BranchRunner,public_failure
from safety_app.workflows.video_analysis import local_work


class ObservationDraft(LabDraft):
    claims: list[VisualClaim | UncertainClaim] = Field(max_length=6)


VIDEO_ROUTING=[
    choice('scope','Use the video product policy. Inspecting the selected recording within the question allowance is allowed; starting a full review or modifying records is not.',
        {'allow':'Video observation, bounded inspection, safety, product help or authorized saved review','block':'Unrelated work, secrets, identity identification, record mutation, full new analyses or boundary overrides','clarify':'Genuinely ambiguous or mixed allowed and unrelated work'}),
    choice('task','Choose the main task.',{'video':'Visible footage, temporal questions, or explaining a finding using images','records':'What the saved review or human decided','product':'Documented product operation','safety':'Safety guidance or requirements without new visual claims','mixed':'Multiple allowed task types','unclear':'Question intent is missing'}),
    *ROUTING_QUESTIONS[2:]]
VIDEO_FINAL=[choice('scope','Check the proposed reply under the video policy. Evidence gaps and useful partial answers are allowed. Bounded inspection is allowed; identity claims, record mutation, secrets or off-topic replies are not.',
    {'allow':'On-topic video, product, saved-review or safety answer with preserved uncertainty','block':'Crosses product boundaries','clarify':'Cannot determine product scope'})]


class VideoChatRunner(BranchRunner):
    def __init__(self,rt,job,client):
        super().__init__(rt,job,None,client)
        self.video=VideoEvidence(rt,job);self.context=self.video.context
        self.routing_questions=VIDEO_ROUTING;self.final_questions=VIDEO_FINAL
        self.cache.scope['conversation']=fingerprint(job.payload['history'])
        self.cache.scope['review']=self.context.review_version
        self.completion_pool=None;self.recovery_hold=None;self.current_pixels=set()
        self.search_text='';self.initial_wait=queue_seconds(rt.db,job.run_id)

    def active_seconds(self):return time.monotonic()-self.started-max(0,queue_seconds(self.rt.db,self.job.run_id)-self.initial_wait)

    def check(self):
        super().check()
        if self.active_seconds()>180:
            raise TimeoutError('Question execution allowance exhausted')

    def prepare_allowances(self):
        if self.paid:
            self.recovery_hold=self.ledger.open_pool(self.job.run_id,self.job.run_id,.01)
            self.completion_pool=self.ledger.open_pool(self.job.run_id,self.job.run_id,.01)

    def start_recovery(self,codes):
        if self.recovery.attempts or not self.known_usage():return False
        if self.active_seconds()>135:return False
        if self.paid and not self.recovery_hold:return False
        self.recovery.attempts=1;self.recovery.issue_codes=list(dict.fromkeys(codes))
        self.pool=self.recovery_hold
        self.recovery_ledger=BudgetLedger(self.rt.db,self.audit,self.job.payload['agents_config'])
        self.recovery_ledger.pool_id=self.pool;self.recovery_ledger.workflow_id=self.job.run_id;self.recovery_ledger.site_id=self.job.site_id
        if self.completion_pool:self.ledger.close_pool(self.completion_pool);self.completion_pool=None
        self.audit.write('recovery_started',resources={'attempt':1,'limit_usd':.01,'issue_codes':self.recovery.issue_codes})
        return True

    def close_recovery(self):
        super().close_recovery()
        if self.recovery_hold and self.recovery_hold!=self.pool:self.ledger.close_pool(self.recovery_hold)
        if self.completion_pool:self.ledger.close_pool(self.completion_pool)

    async def decisions(self,final=None):
        if not self.paid:
            node,audit,start=self.begin('Final scope check' if final else 'Scope · route · freshness','decisions')
            question=self.job.payload['question'].lower()
            if final:value=LabScopeResult(scope='allow',confidence=1)
            else:
                # Explicit fixture behavior; never used as production classification or quality evidence.
                scope='block' if any(x in question for x in ('reveal your api key','write a romantic poem','identify this person')) else 'allow'
                task='product' if 'preview' in question else 'records' if 'human decide' in question or 'saved review' in question else 'video'
                value=LabRouting(scope=scope,task=task,domain='vehicles',freshness='required' if 'latest' in question else 'unneeded',confidence=1,policy_version=self.config['policy_version'])
            audit.write('fixture_classification',run_id=node,output=value.model_dump(),resources={'usage_is_synthetic':True})
            self.end(node,audit,start,'Scope check');return value
        previous=self.ledger.pool_id
        if final and not self.pool:self.ledger.pool_id=self.completion_pool
        try:return await super().decisions(final)
        finally:self.ledger.pool_id=previous

    def reference_tools_allowed(self,payload):return not payload.get('bounded_evidence_only') and payload.get('question_type')!='video' and payload.get('role')!='observer'

    def create_agent(self,model,context,contract,tools,native,challenge,payload):
        if payload.get('role')=='observer':
            from safety_app.agents.video_observer.chat_agent import create
            context.profile='Visible evidence only. No regulatory assessment.'
            return create(model,context,contract,tools)
        return super().create_agent(model,context,contract,tools,native,challenge,payload)

    def stage_content(self,payload):
        from safety_app.modules.safety_review.chat_activity import BranchAudit
        ids=payload.get('frame_ids',[])
        if not ids:return []
        if any(id not in self.video.frames for id in ids):raise ValueError('Frame is outside this question evidence')
        ids=sorted(ids,key=lambda id:(self.video.frames[id].frame.timestamp_ms,id))
        node=self.previous;audit=BranchAudit(self.rt.audit,self.job,None,node,'local')
        self.ledger.tool(node);call=uid();start=time.monotonic()
        audit.write('tool_started',status='running',run_id=node,tool_name='read_video_frames',tool_call_id=call,arguments={'frame_ids':ids})
        try:
            result=self.video.read_frames(ids)
            content=[]
            for id in ids:
                item=self.video.frames[id]
                pixels=self.rt.media.model_image(id);item.model_image_hash=hashlib.sha256(pixels).hexdigest()
                content.extend([f'Evidence ID: {id}; source PTS: {item.frame.timestamp_ms} ms; origin: {item.origin}; parent: {item.parent_frame_id or "none"}',BinaryContent(data=pixels,media_type='image/jpeg')])
                self.current_pixels.add(id)
            audit.write('tool_finished',run_id=node,tool_name='read_video_frames',tool_call_id=call,output=result,duration_ms=(time.monotonic()-start)*1000)
            return content
        except BaseException:
            audit.write('tool_finished',status='failed',run_id=node,tool_name='read_video_frames',tool_call_id=call,duration_ms=(time.monotonic()-start)*1000)
            raise

    def stage_tools(self,payload,node,audit):
        if payload.get('role')!='observer':return []
        assigned=set(payload.get('frame_ids',[]));self.video.detail_calls=0
        async def inspect_frame_detail(frame_id:str,box:Annotated[list[float],Field(min_length=4,max_length=4)]) -> ToolReturn:
            """Inspect one XYWH crop of an assigned still; same timestamp, no extra time coverage."""
            if len(self.current_pixels)>=6:return ToolReturn(return_value={'gaps':['Six images are already attached; no additional crop fits.']})
            try:
                async def perform():
                    item=await local_work(self.video.crop,frame_id,box,assigned)
                    return {'frame':item.model_dump(),'gaps':['Crop adds detail, not time coverage.']}
                result=await self.tool(node,audit,'inspect_frame_detail',{'frame_id':frame_id,'box':box},perform,fresh=True)
                id=result['frame']['id'];self.current_pixels.add(id)
                pixels=self.rt.media.model_image(id);self.video.frames[id].model_image_hash=hashlib.sha256(pixels).hexdigest()
                return ToolReturn(return_value=result,content=[f'Crop evidence ID: {id}',BinaryContent(data=pixels,media_type='image/jpeg')])
            except (ValueError,FileNotFoundError):return ToolReturn(return_value={'gaps':['The requested crop is unavailable or outside the assigned bounds.']})
        async def get_person_detections(frame_id:str)->dict:
            """Read saved frame-local person detections. Absence of a record is not zero people."""
            if frame_id not in assigned:return {'gaps':['Frame is outside this stage.']}
            return await self.tool(node,audit,'get_person_detections',{'frame_id':frame_id},lambda:self.video.people(frame_id),fresh=True)
        return [inspect_frame_detail,get_person_detections]

    async def _stage_once(self,name,output_type,payload,fixture,collection=None,native=False,challenge=False,correction=False):
        self.current_pixels=set()
        if not native:
            summary={k:v for k,v in self.job.payload['review_snapshot'].items() if k!='human_decisions'}
            summary['human_decisions_notice']='Read selected saved records and linked decisions before asserting review facts; metadata is not a substitute.'
            payload={**payload,'history':self.job.payload['history'],'review_context':summary,
                     'selected_focus':self.context.focus.model_dump()}
        result=await super()._stage_once(name,output_type,payload,fixture,collection,native,challenge,correction)
        self.video.mark_inspected(self.current_pixels)
        for id in self.current_pixels:self.knowledge.seen[id]=self.video.citation(id)
        return result

    async def web(self,fresh):
        # Reserve a real tool call and conservatively admit optional discovery only while
        # completion and correction money/time/request capacity remain protected.
        if self.paid:
            with self.rt.db.connect() as db:
                spent=db.execute('SELECT COALESCE(sum(COALESCE(actual,amount)),0) FROM reservations WHERE workflow_id=?',(self.job.run_id,)).fetchone()[0]/1e6
                requests=db.execute("SELECT count(*) FROM reservations WHERE workflow_id=? AND id NOT IN (SELECT id FROM reservation_pools)",(self.job.run_id,)).fetchone()[0]
            if spent+.022>.05 or requests>=7 or self.active_seconds()>100:
                self.gaps.append('Current web discovery did not fit the remaining question allowance; current requirements remain unverified.')
                self.audit.write('optional_stage_omitted',resources={'stage':'web_search','reason':'completion_capacity_reserved'});return
        self.ledger.tool(self.previous)
        try:return await super().web(fresh)
        except CompletionHeadroomReached:self.gaps.append('Web discovery was omitted to preserve verification request capacity.')

    def search_question(self):
        if self.search_text:return self.search_text
        # Search receives a controlled topic vocabulary, never filenames, people, site records or quotes.
        domain=self.route.domain if self.route else 'multiple'
        topics={'vehicles':'powered industrial trucks pedestrian separation','ppe':'personal protective equipment',
                'surfaces':'walking working surfaces slips trips exits','machinery':'machine guarding',
                'energy':'control hazardous energy lockout tagout','health':'occupational health heat exposure',
                'emergency':'workplace emergency medical assistance first aid','applicability':'regulatory applicability',
                'multiple':'workplace hazard assessment'}
        self.search_text='OSHA federal '+topics[domain]+' guidance requirements'
        if self.route and self.route.freshness=='required':self.search_text='current latest '+self.search_text
        return self.search_text

    async def review(self,answer,applicability,task):
        if task!='video':
            previous=self.ledger.pool_id
            if not self.pool:self.ledger.pool_id=self.completion_pool
            try:return await super().review(answer,applicability,task)
            finally:self.ledger.pool_id=previous
        checks=[];issues=[];observations=[]
        # Partition by the exact pixels required by each claim, retaining whole blocks.
        packets=[]
        for claim in answer.claims:
            pixels={id for id in claim.citation_ids if id in self.video.frames}
            for id in list(pixels):
                parent=self.video.frames[id].parent_frame_id
                if parent:pixels.add(parent)
            if len(pixels)>6:
                checks.append(LabClaimCheck(claim_id=claim.id,verdict='unsupported',independently_publishable=False,note='Too many cited images for one bounded claim check.'));continue
            if packets and len(packets[-1][1]|pixels)<=6:packets[-1][0].append(claim);packets[-1][1].update(pixels)
            else:packets.append(([claim],pixels))
        previous=self.ledger.pool_id
        if not self.pool:self.ledger.pool_id=self.completion_pool
        try:
            for number,(claims,pixels) in enumerate(packets):
                ids={id for c in claims for id in c.citation_ids}|set(applicability.citation_ids if applicability else [])
                # All retrieved human decisions stay with saved-record checks, even if not explicitly cited.
                evidence=[c.model_dump() for c in self.knowledge.seen.values() if c.id in ids or c.kind=='record']
                fixture=LabChallengeAssessment(claim_checks=[LabClaimCheck(claim_id=c.id,verdict='uncertain' if c.kind=='uncertain' else 'general' if c.kind=='general' else 'supported',independently_publishable=True,note='Synthetic fixture judgment; not a quality measurement.') for c in claims])
                checked=await self.stage('Verify evidence'+(f' · {number+1}' if len(packets)>1 else ''),LabChallengeAssessment,
                    {'question_type':'video','task':'Independently check exactly these answer blocks against their pixels and sources.',
                     'answer':{'claims':[c.model_dump() for c in claims],'missing_context':answer.missing_context},
                     'evidence':evidence,'frame_ids':sorted(pixels),'applicability':applicability.model_dump() if applicability else None},fixture,challenge=True)
                checks.extend(checked.claim_checks);issues.extend(checked.blocking_issues);observations.extend(checked.observations)
        finally:self.ledger.pool_id=previous
        # Aggregate packets without dropping a late blocking defect to fit a display schema.
        assessment=SimpleNamespace(claim_checks=checks,blocking_issues=issues,observations=observations)
        errors=claim_errors(answer,self.knowledge.seen,applicability,'video')
        self.repair_feedback={'local_errors':errors,'review':{'claim_checks':[c.model_dump() for c in checks],'blocking_issues':[i.model_dump() for i in issues]}}
        provisional={c.id for c in answer.claims if c.kind=='guidance' and any(self.knowledge.seen[id].kind=='web_summary' for id in c.citation_ids if id in self.knowledge.seen)}
        kept,challenge=release_blocks(answer,assessment,errors,provisional);self.last_challenge=challenge
        self.audit.write('challenge_checked',run_id=self.previous,status='ok' if challenge.approved else 'blocked',output={'published_claim_ids':challenge.published_claim_ids,'omitted_claim_ids':challenge.omitted_claim_ids,'failure_codes':challenge.failure_codes})
        return kept,challenge

    async def run(self):
        route=await self.decisions();self.route=route
        if route.scope!='allow':return await super().run(route)
        if route.task=='unclear':
            return route,LabAnswer(answer='Select a moment or finding, and tell me what you want to understand about it.',outcome='clarification',completion='clarification'),None,[]
        self.prepare_allowances()
        if route.task in ('product','records','safety'):return await super().run(route)
        node,audit,start=self.begin('Find evidence','local')
        await self.tool(node,audit,'get_review_context',{},lambda:{'context':self.job.payload['review_snapshot'],'gaps':[]},fresh=True)
        found=await self.tool(node,audit,'search_video_evidence',{'query':self.job.payload['question']},lambda:self.video.catalog(self.job.payload['question']))
        ids=['review-context:'+self.context.review_run_id,*([self.context.focus.finding_id] if self.context.focus.finding_id else []),*[c['id'] for c in found['matches']]][:5]
        if ids:await self.tool(node,audit,'read_sources',{'ids':ids,'collection':'records'},lambda:self.knowledge.read_for_task(ids,'records'))
        self.end(node,audit,start,'Find evidence')
        focus=self.context.focus.timestamp_ms
        if focus is None and self.context.focus.finding_id:
            case=self.rt.db.get('video_safety_case',self.context.focus.finding_id);focus=case['start_ms']
        focus=focus if focus is not None else self.context.start_ms
        lo=max(self.context.start_ms,focus-2500);hi=min(self.context.end_ms,lo+5000)
        available=[f['id'] for c in found['matches'] for f in c['frames']][:5]
        fixture=VideoInspectionPlan(frame_ids=available,intervals=[Interval(start_ms=lo,end_ms=hi)] if lo<hi else [],reason='Fixture bounded inspection of the selected moment.')
        plan=await self.stage('Choose relevant moments',VideoInspectionPlan,{'question_type':'video','task':'Use saved evidence first; request at most two short intervals only if needed to answer the original question.',
            'catalog':found,'source_bounds':[self.context.start_ms,self.context.end_ms],'saved_evidence':[c.model_dump() for c in self.knowledge.seen.values()]},fixture)
        packets=[]
        for interval in plan.intervals[:2]:
            node,audit,start=self.begin('Inspect moment','local')
            try:
                result=await self.tool(node,audit,'inspect_interval',interval.model_dump(),lambda:local_work(self.video.inspect,interval.start_ms,interval.end_ms,self.cancelled),fresh=True)
                packets.append(result['inspection']['frame_ids'])
            except (ValueError,OSError):self.gaps.append('A requested interval could not be inspected; saved evidence remains available.')
            finally:self.end(node,audit,start,'Inspect moment')
        assigned=[id for id in plan.frame_ids if id in self.video.frames]
        if assigned and not packets:packets.append(assigned)
        if not packets and available:packets.append(available)
        observed=[]
        for index,frames in enumerate(packets):
            if not frames:continue
            fixture=ObservationDraft(claims=[VisualClaim(id=f'fixture-frame-{index}',text='Fixture demonstration: this block links to a sampled frame. It is not an assessment of what the real footage shows.',kind='visual',citation_ids=[frames[0]])])
            try:
                observation=await self.stage('Inspect frames'+(f' · {index+1}' if len(packets)>1 else ''),ObservationDraft,
                    {'question_type':'video','role':'observer','task':'Describe visible facts relevant to the original question, with exact frame citations.','frame_ids':frames},fixture)
            except CompletionHeadroomReached:
                self.gaps.append('Further visual inspection stopped to preserve verification capacity.');break
            observed.extend(observation.claims)
        applicability=None
        regulatory=bool(re.search(r'\b(osha|regulat\w*|legal|law|requirements?|standards?|compliance)\b',self.job.payload['question'],re.I))
        needs_safety=regulatory or bool(re.search(r'\b(safe|safety|hazard|risk|should|injur\w*|emergency)\b',self.job.payload['question'],re.I))
        if needs_safety:
            node,audit,start=self.begin('Check sources','local')
            await self.retrieve(node,audit,self.job.payload['question'],'references');self.end(node,audit,start,'Check sources')
            if route.freshness=='required' or not any(c.kind in ('guidance','requirement','procedure') for c in self.knowledge.seen.values()):await self.web(route.freshness=='required')
        assignments=[]
        if needs_safety and route.domain=='multiple':
            try:
                plan=await self.stage('Safety team coordination',LabPlan,{'question_type':'video','task':'Assign only the relevant safety domains for this original video question. Keep visual observations separate from safety interpretation; do not replace the question with a conclusion.','observations':[c.model_dump() for c in observed]},LabPlan(assignments=[LabAssignment(domain='vehicles',question=self.job.payload['question'][:700]),LabAssignment(domain='ppe',question=self.job.payload['question'][:700])]))
                assignments=plan.assignments
            except CompletionHeadroomReached:self.gaps.append('Additional domain coordination did not fit the remaining request allowance.')
        if regulatory:
            try:
                applicability=await self.stage('Applicability specialist',LabApplicability,{'question_type':'safety','task':'Assess applicability only; unknown jurisdiction does not prevent visual explanation or general guidance.','jurisdiction':self.job.payload['review_request'].get('jurisdiction','unknown')},LabApplicability(basis='Fixture: legal applicability is unresolved.'),collection='references')
            except CompletionHeadroomReached:applicability=LabApplicability(basis='Applicability could not be established within the request allowance.')
            if not set(applicability.citation_ids)<=self.knowledge.seen.keys() or applicability.requirements_allowed and (applicability.jurisdiction=='unknown' or not applicability.citation_ids):
                applicability=LabApplicability(basis='The supplied evidence did not establish legal applicability.')
        delegates=[]
        for assignment in assignments:
            try:
                result=await self.stage(assignment.domain.title()+' specialist',LabDraft,
                    {'question_type':'safety','domain':assignment.domain,'assigned_question':assignment.question,'bounded_evidence_only':True,
                     'task':'Address only your assigned safety domain using supplied sources and observations. Do not generate new visual observations or replace the original question.',
                     'observations':[c.model_dump() for c in observed],'evidence':[c.model_dump() for c in self.knowledge.seen.values()],
                     'applicability':applicability.model_dump() if applicability else None},
                    LabDraft(claims=[LabClaim(id='fixture-domain-'+assignment.domain,kind='uncertain',text='Fixture specialist: no real safety conclusion was measured.')]))
                delegates.append(result.model_dump())
            except CompletionHeadroomReached:
                self.gaps.append('Some assigned safety domains remain unassessed to preserve verification capacity.');break
        fixture=LabDraft(claims=observed or [LabClaim(id='gap',text='No visual evidence could be inspected for this question.',kind='uncertain')],missing_context=['Fixture judgments do not establish hosted accuracy.'])
        draft=await self.stage('Explain the evidence',LabDraft,{'question_type':'video','task':'Answer directly with grounded blocks. Keep saved-review statements separate from new visual observations.',
            'observations':[c.model_dump() for c in observed],'evidence':[c.model_dump() for c in self.knowledge.seen.values()],
            'domain':route.domain if route.domain!='multiple' else None,'domain_assignments':[a.model_dump() for a in assignments],'specialist_results':delegates,
            'applicability':applicability.model_dump() if applicability else None},fixture)
        answer=render_answer(draft.claims,[*draft.missing_context,*self.gaps,'Audio was not assessed; actions between sampled frames may be missed.'])
        kept,challenge=await self.review(answer,applicability,'video')
        if not challenge.approved and self.start_recovery(challenge.failure_codes):
            try:
                correction=await self._stage_once('Correct answer blocks',LabDraft,{'question_type':'video','task':'Correct only the named defects using existing evidence; preserve all qualifications and human decisions.',
                    'draft':draft.model_dump(),'feedback':self.repair_feedback,'evidence':self.correction_evidence(draft,'video',applicability),
                    'applicability':applicability.model_dump() if applicability else None},fixture,correction=True)
                corrected=render_answer(correction.claims,[*correction.missing_context,*self.gaps])
                corrected_kept,corrected_check=await self.review(corrected,applicability,'video')
                kept,challenge,answer=corrected_kept,corrected_check,corrected
                self.recovery.succeeded=any(c.kind!='uncertain' for c in kept)
            except LabOutputRejected:
                self.audit.write('recovery_exhausted',status='blocked',resources={'issue_codes':['output_contract']})
        released=render_answer(kept,[*(answer.missing_context if kept else []),*challenge.issues,*challenge.observations],omitted=not challenge.approved)
        if not kept:released.answer='I could not verify this from the available frames. Select a specific moment or finding, or ask what the saved review recorded.'
        if kept and (await self.decisions(released)).scope!='allow':
            released=LabAnswer(answer='Please narrow the question to this video, its saved findings, product operation or workplace safety.',outcome='clarification',completion='clarification')
        return route,released,challenge,list(self.knowledge.seen.values())


async def chat(rt,job):
    service=VideoChat(rt);value=service.get(job.payload['review_run_id'],job.id,job.site_id)
    client=None;runner=None;start=time.monotonic()
    queue_ms=max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(job.created_at)).total_seconds()*1000)
    queue_seconds(rt.db,job.run_id)
    with rt.db.connect() as db:
        db.execute('INSERT OR IGNORE INTO request_waits VALUES(?,?)',(job.run_id,queue_ms/1000))
    if job.payload['mode']=='hosted':
        if os.getenv('SAFETY_PAID_REVIEW')!='1' or not os.getenv('OPENAI_API_KEY'):raise ValueError('Hosted provider unavailable')
        client=AsyncOpenAI(api_key=os.environ['OPENAI_API_KEY'],max_retries=0)
    current=asyncio.current_task()
    active_timeout=False
    async def watch():
        nonlocal active_timeout
        while True:
            rt.jobs.heartbeat(job.id)
            if rt.jobs.cancelled(job.id):current.cancel();return
            waited=max(0,queue_seconds(rt.db,job.run_id)-queue_ms/1000)
            if time.monotonic()-start-waited>180:active_timeout=True;current.cancel();return
            await asyncio.sleep(.5)
    watcher=asyncio.create_task(watch())
    try:
        runner=VideoChatRunner(rt,job,client)
        if queue_seconds(rt.db,job.run_id)>=120:raise RequestQueueTimeout('Question queue allowance exhausted')
        async with asyncio.timeout(300):
            route,answer,challenge,citations=await runner.run()
        value.routing=route;value.answer=answer;value.challenge=challenge;value.citations=citations
        value.issue_codes=challenge.failure_codes if challenge else []
    except BaseException as exc:
        if active_timeout:exc=TimeoutError('Question active execution allowance exhausted')
        code,message=public_failure(exc)
        if isinstance(exc,RequestQueueTimeout):code,message='queue_timeout','The review team stayed busy for too long. This question stopped waiting; you can submit it again when the team is available.'
        elif isinstance(exc,BudgetBlocked):
            if 'preflight' in str(exc).lower():code,message='token_preflight_unavailable','The provider could not count this image request. No image generation request was sent. Try again after provider service is available.'
            elif 'priced' in str(exc).lower():code,message='unknown_charge','The provider returned usage that could not be priced. Its reservation is retained and work has stopped. Resolve the charge shown in Activity before another paid attempt.'
            else:message='This question reached its spending or request boundary. Check Activity for the completed work and any unresolved charge before asking again.'
        elif isinstance(exc,TimeoutError):code,message='execution_timeout','This question reached its time limit. Try a narrower moment or ask about a saved finding.'
        value.issue_codes=[code];value.answer=LabAnswer(answer=message.replace('comparison','question'),outcome='unavailable',completion='incomplete')
        rt.audit.write('question_unavailable',status='cancelled' if isinstance(exc,asyncio.CancelledError) else 'failed',job_id=job.id,run_id=job.run_id,resources={'issue_code':code,'exception_type':type(exc).__name__})
        raise exc
    finally:
        if runner:
            runner.close_recovery();value.recovery=runner.recovery
            value.evidence=[v for v in runner.video.frames.values() if v.id in runner.video.seen]
            value.inspections=runner.video.inspections
        waited=queue_seconds(rt.db,job.run_id)*1000
        value.queue_ms=waited;value.execution_ms=max(0,(time.monotonic()-start)*1000-max(0,waited-queue_ms))
        rt.db.save('video_chat_turn',value,immutable=True)
        rt.audit.write('question_finished',job_id=job.id,run_id=job.run_id,output={'outcome':value.answer.outcome if value.answer else 'unavailable'},duration_ms=value.execution_ms)
        watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
        if client:await client.close()
    return [job.id]
