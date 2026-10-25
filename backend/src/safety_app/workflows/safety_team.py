"""Bounded boss delegation, question ownership and evidence/causal validation."""
import hashlib
import json
import re
from safety_app.workflows.agent_stage import TaskInput
from safety_app.agents.safety_reviewer.models import SpecialistAssessment
from safety_app.modules.safety_review.catalog import PROFILES,owner_for
from safety_app.infrastructure.budgets import BudgetBlocked
from safety_app.agents.shared.contracts import retain_source_gaps,comparison_record,clause_id

class SafetyTeam:
    def __init__(self,rt,job,request,previous,model_factory):
        self.rt=rt;self.job=job;self.request=request;self.previous=previous;self.model_factory=model_factory
        self.observations={o.id:o.model_copy(deep=True) for o in request.observations}
        self.owners={};self.calls={};self.tasks={};self.results=[];self.failed=False
        self.limits=rt.config.get('review_team',{})
        self.applicability_pending=None  # asyncio.Event while an applicability call is in flight
    async def delegate(self,task,boss):
        if boss.cancelled():raise __import__('asyncio').CancelledError()
        if len(self.calls)>=self.limits.get('max_delegations',8):raise BudgetBlocked('Delegation allowance exhausted')
        if not set(task.observation_ids)<=self.observations.keys():raise ValueError('Task observation outside assigned scope')
        if task.specialist!='applicability' and not any(r.specialist=='applicability' for r in self.results):
            # Parallel dispatch: a technical question sent in the same turn waits for the in-flight applicability call.
            if self.applicability_pending is None:await __import__('asyncio').sleep(0)  # let a sibling applicability call from the same turn start
            if self.applicability_pending is None:raise ValueError('Delegate applicability before technical review')
            await self.applicability_pending.wait()
            if not any(r.specialist=='applicability' for r in self.results):raise ValueError('Applicability did not complete; technical review cannot start')
        if task.question_id in self.owners and self.owners[task.question_id]!=task.specialist:raise ValueError('Question already owned by another specialist')
        fingerprint=hashlib.sha256(json.dumps(task.model_dump(),sort_keys=True).encode()).hexdigest()
        if fingerprint in self.tasks:raise ValueError('Duplicate question dispatch; reuse the returned assessment')
        prior=[r for r in self.results if r.question_id==task.question_id]
        if task.phase=='follow_up':
            if not prior or task.previous_call_id!=prior[-1].call_id:raise ValueError('Follow-up must cite the latest prior call for this question')
        elif prior:raise ValueError('Existing question requires an explicit follow-up')
        if len(prior)>=self.limits.get('max_calls_per_question',2):raise BudgetBlocked('Question follow-up allowance exhausted')
        self.owners[task.question_id]=task.specialist;self.tasks[fingerprint]=task
        call_id='delegation-'+str(len(self.calls)+1);self.calls[call_id]=task
        gate=None
        if task.specialist=='applicability' and self.applicability_pending is None:
            gate=self.applicability_pending=__import__('asyncio').Event()
        from safety_app.workflows.agent_stage import run_stage
        observations=[self.observations[id] for id in task.observation_ids]
        inputs={'task':task.model_dump(mode='json'),'observations':[o.model_dump(mode='json') for o in observations],
            'review_context':self.request.review_context,'visibility_limits':self.request.visibility_limits,
            'context':self.request.context,'prior_assessment':prior[-1].model_dump(mode='json') if prior else None}
        self.rt.audit.write('specialist_dispatched',run_id=boss.run_id,parent_run_id=self.job.run_id,job_id=self.job.id,agent_name='specialist_'+task.specialist,arguments=task.model_dump(mode='json'),provenance={'call_id':call_id})
        try:
            result,deps=await run_stage(self.rt,self.job,'specialist_'+task.specialist,TaskInput(inputs),self.model_factory,self.previous,
                setup=lambda d:self.setup_child(d,task,observations),parent_run_id=boss.run_id,text_only=self.job.kind=='video_safety_review',
                audit_provenance={'call_id':call_id,'question_id':task.question_id})
        except BaseException:
            self.failed=True
            if gate:gate.set()
            raise
        result.call_id=call_id;result.question_id=task.question_id
        self.results.append(result)
        if gate:gate.set()
        boss.seen_sources.update(deps.seen_sources);boss.seen_rules.update(deps.seen_rules)
        boss.fetched_source_ids.update(deps.fetched_source_ids);boss.complete_source_ids.update(deps.complete_source_ids)
        self.rt.audit.write('specialist_completed',run_id=deps.run_id,parent_run_id=boss.run_id,job_id=self.job.id,agent_name=deps.agent_name,output=result.model_dump(mode='json'),provenance={'call_id':call_id})
        return result
    def setup_child(self,deps,task,observations):
        deps.delegate=None;deps.specialist=task.specialist
        deps.seen_frames=set(ref for o in observations for ref in o.evidence_refs)
        # The aliases in the content builder refer to its wider initial packet.
        # Replace them with the child's exact assigned evidence scope.
        from safety_app.agents.shared.evidence_refs import register_frame
        from safety_app.domain.models import FrameRef
        deps.frame_alias_targets={}
        for index,ref in enumerate(sorted(deps.seen_frames),1):
            register_frame(deps,FrameRef.model_validate(deps.media.db.get('frame',ref)),f'f{index}')
        jurisdiction=self.request.review_context.get('jurisdiction') or 'US-federal-OSHA'
        query=task.question if task.specialist=='applicability' else ' '.join(o.description for o in observations)+' '+task.question
        discovery=deps.sources.discover(query,jurisdiction,'',3,deps.scope.site_id,deps.scope.source_ids,task.specialist,deps.event_date)
        if discovery.candidates:
            packet=deps.sources.fetch_packet([discovery.candidates[0].id],deps.scope.site_id,deps.scope.source_ids,deps.source_bytes,deps.event_date)
            deps.prepared_sources=packet;deps.source_gaps.update(packet.gaps)
            deps.seen_sources.update(s.id for s in packet.excerpts);deps.fetched_source_ids.update(s.id for s in packet.excerpts)
            if packet.complete:deps.complete_source_ids.update(s.id for s in packet.excerpts)
            self.rt.audit.write('source_packet_prepared',run_id=deps.run_id,parent_run_id=self.job.run_id,job_id=self.job.id,agent_name=deps.agent_name,output=packet.model_dump(mode='json'),resources={'serialized_bytes':len(packet.model_dump_json().encode())})
        deps.output_check=lambda result:self.validate_child(result,deps,task)
    def validate_child(self,result,deps,task):
        if result.specialist!=task.specialist:raise ValueError('Specialist identity mismatch')
        if result.question_id and result.question_id!=task.question_id:raise ValueError('Specialist changed question identity')
        result.missing_context=retain_source_gaps(result.missing_context,deps.source_gaps)
        for finding in result.findings:
            if finding.question_id!=task.question_id or finding.specialist!=task.specialist:raise ValueError('Finding not assigned to this question/owner')
            if finding.event_id is None:finding.event_id=self.job.payload.get('event_id')
            if finding.event_id!=self.job.payload.get('event_id'):raise ValueError('Finding event mismatch')
            validate_finding(finding,deps)
            if finding.classification in ('supported_noncompliance','potential_violation'):
                requirements=[deps.sources.read(id,None,deps.scope.site_id) for id in finding.source_refs]
                if not any(s.source_type=='requirement' and owner_for(s.model_dump())==task.specialist for s in requirements):raise ValueError('Regulatory requirement belongs to another domain or is missing')
        for check in result.applicability_checks:validate_check(check,deps)
        validate_hypotheses(result.hypotheses,deps)
    def validate_boss(self,result,deps):
        if self.failed:raise ValueError('Required delegation failed; review incomplete')
        if self.observations and not any(r.specialist=='applicability' for r in self.results):raise ValueError('Boss must delegate applicability before completing review')
        active=list({r.question_id:r for r in self.results}.values())
        delegated=[f for r in active for f in r.findings]
        if result.finding_ids:
            canonical={f.id:f for f in delegated}
            if result.findings:raise ValueError('Select finding_ids and leave findings empty; Python preserves the approved records')
            if len(set(result.finding_ids))!=len(result.finding_ids):raise ValueError('Boss repeated a finding ID')
            if not set(result.finding_ids)<=canonical.keys():raise ValueError('Boss selected an unknown specialist finding')
            result.findings=[canonical[id].model_copy(deep=True) for id in result.finding_ids]
        if result.triage:
            ids={finding.id for finding in result.findings}
            if not set(result.triage.finding_ids)<=ids or not set(result.triage.evidence_refs)<=deps.seen_frames:raise ValueError('Triage cites unassigned finding or evidence')
            if result.triage.priority=='high':
                eligible={finding.id for finding in result.findings if finding.classification!='insufficient_evidence'}
                if not result.triage.finding_ids or not set(result.triage.finding_ids)<=eligible or not result.triage.evidence_refs:raise ValueError('High triage needs visible evidence and substantive specialist findings')
        allowed={signature(f) for f in delegated}
        for finding in result.findings:
            if signature(finding) not in allowed:raise ValueError('Boss invented or changed a finding instead of synthesizing delegated evidence')
            validate_finding(finding,deps)
            if finding.classification=='supported_noncompliance':
                checks=[c for r in active if r.specialist=='applicability' for c in r.applicability_checks]
                if not {'activity','coverage','jurisdiction','event_date'}<={c.element for c in checks if c.status=='supported'}:raise ValueError('Supported noncompliance lacks established applicability')
        if not {condition_key(f) for f in delegated}<={condition_key(f) for f in result.findings}:raise ValueError('Boss omitted a distinct delegated finding')
        if len({condition_key(f) for f in result.findings})!=len(result.findings):raise ValueError('Boss repeated the same condition/requirement in this episode')
        missing={gap for r in active for gap in r.missing_context}
        result.missing_context=retain_source_gaps(result.missing_context,missing)
        # Uncertainty must weaken the causal conclusion, not discard valid findings.
        # Only the new answer is changed; specialist and historical records are retained.
        for hypothesis in result.investigation.hypotheses:
            if hypothesis.status=='supported' and hypothesis.missing_facts:
                hypothesis.status='unresolved'
                result.investigation.cause_status='not_established'
                result.investigation.limitations.append('Causal explanation remains unresolved: '+'; '.join(hypothesis.missing_facts))
        validate_hypotheses(result.investigation.hypotheses,deps)
        for step in result.investigation.sequence:
            deps.interval(step.media_id,step.start_ms,step.end_ms)
            if not set(step.evidence_refs)<=deps.seen_frames:raise ValueError('Timeline cites unseen frames')
            for ref in step.evidence_refs:
                frame=deps.media.db.get('frame',ref)
                if frame['media_id']!=step.media_id or not step.start_ms<=frame['timestamp_ms']<=step.end_ms:raise ValueError('Timeline timestamp/frame mismatch')
        supported=[h for h in result.investigation.hypotheses if h.status=='supported']
        delegated_causes={json.dumps(comparison_record(h.model_dump()),sort_keys=True) for r in active for h in r.hypotheses if h.status=='supported'}
        if any(json.dumps(comparison_record(h.model_dump()),sort_keys=True) not in delegated_causes for h in supported):raise ValueError('Boss invented a supported cause not established by specialist investigation')
        if result.investigation.cause_status=='supported_contributors' and (not supported or len(result.investigation.hypotheses)<2):raise ValueError('Causal conclusion needs supported evidence and a competing explanation')
        if result.findings and not result.investigation.hypotheses and not result.investigation.limitations:raise ValueError('Investigation must test explanations or disclose why cause cannot be established')

def signature(finding):
    # Synthesis may choose a canonical ID/status; substantive evidence stays intact.
    return json.dumps(comparison_record(finding.model_dump(mode='json',exclude={'id','status'})),sort_keys=True)

def condition_key(finding):
    return (' '.join(finding.condition.lower().split()),finding.classification,clause_id(finding.exact_clause),tuple(sorted(set(finding.rule_refs))),tuple(sorted(set(finding.evidence_refs))))

def validate_check(check,deps):
    if not set(check.evidence_refs)<=deps.seen_frames or not set(check.source_refs)<=deps.seen_sources or not set(check.rule_refs)<=deps.seen_rules:raise ValueError('Element check references unseen evidence')
    if check.status=='supported' and not (check.evidence_refs or check.source_refs or check.rule_refs):raise ValueError('Supported element needs evidence')

def validate_finding(finding,deps):
    if not finding.condition:raise ValueError('Finding must describe this event condition')
    for check in finding.element_checks:validate_check(check,deps)
    for action in finding.corrective_actions:
        if not set(action.evidence_refs)<=set(finding.evidence_refs):raise ValueError('Corrective action lacks this finding evidence')
        if re.fullmatch(r'(improve awareness|provide training|retrain employees|follow procedures)[.! ]*',action.action.lower()):raise ValueError('Generic corrective action without event-specific action')
    if finding.classification in ('supported_noncompliance','potential_violation'):
        if not finding.exact_clause or not finding.source_refs:raise ValueError('Regulatory finding needs exact clause and fetched source')
        sources=[deps.sources.read(id,None,deps.scope.site_id) for id in finding.source_refs]
        if not any(s.source_type=='requirement' and clause_id(s.clause)==clause_id(finding.exact_clause) for s in sources):raise ValueError('Claimed clause is not an exact retrieved mandatory source')
    if finding.classification=='supported_noncompliance':
        required={'requirement','condition','exposure','employer_knowledge'}
        if not required<={c.element for c in finding.element_checks if c.status=='supported'} or any(c.status!='supported' for c in finding.element_checks):raise ValueError('Supported noncompliance has unresolved elements')
        if not deps.event_date or not set(finding.source_refs)<=deps.complete_source_ids:raise ValueError('Source context/date not verified for supported noncompliance')
    if finding.classification=='site_infraction' and not finding.rule_refs and not any(deps.sources.read(id,None,deps.scope.site_id).source_type in ('site_rule','procedure') for id in finding.source_refs):raise ValueError('Site infraction lacks an applicable site requirement')
    if finding.recommendations and not finding.corrective_actions:raise ValueError('Put requests for missing facts in missing_context and leave recommendations empty. Safety actions require evidence-linked corrective_actions with failed_control, action and verification; never invent a failed control for a context question.')

def validate_hypotheses(hypotheses,deps):
    allowed=deps.seen_frames|deps.seen_sources|deps.seen_rules
    if len({h.id for h in hypotheses})!=len(hypotheses):raise ValueError('Duplicate hypothesis IDs')
    for h in hypotheses:
        if not set(h.supporting_refs+h.contradicting_refs)<=allowed:raise ValueError('Causal hypothesis references unseen evidence')
        if h.status=='supported':
            if not h.supporting_refs or h.missing_facts:raise ValueError('Supported cause has missing causal evidence')
            factual=set(h.supporting_refs)&deps.seen_frames
            factual.update(id for id in set(h.supporting_refs)&deps.seen_sources if deps.sources.read(id,None,deps.scope.site_id).source_type in ('procedure','site_rule'))
            if not factual:raise ValueError('A regulation alone cannot establish an incident cause')
        if h.status=='contradicted' and not h.contradicting_refs:raise ValueError('Contradicted hypothesis needs contrary evidence')
        if h.status=='unresolved' and not h.missing_facts:raise ValueError('Unresolved cause must identify missing decisive facts')
