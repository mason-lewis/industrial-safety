"""PydanticAI 2.54 adapter. Every physical provider request passes this guard."""
import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from dataclasses import fields,is_dataclass
from pydantic_ai import Agent,BinaryContent,ModelRetry
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.usage import UsageLimits
from safety_app.domain.models import ToolFailure,uid
from safety_app.infrastructure.budgets import BudgetBlocked

def input_bound(messages,model_request_parameters=None):
    frames=0;size=0;text_bytes=0
    # The pinned Responses adapter uses get_instructions once, not once per
    # historical ModelRequest. Match its selection without dropping evidence.
    from pydantic_ai._instrumentation import get_instructions
    instructions=get_instructions(messages,model_request_parameters)
    if instructions:text_bytes+=len(instructions.encode())
    def visit(v):
        nonlocal frames,size,text_bytes
        if isinstance(v,BinaryContent):
            if not v.media_type.startswith('image/'):raise BudgetBlocked('Native video/audio charge is not bounded. Use configured frames')
            frames+=1;size+=len(v.data)
            import cv2,numpy as np
            image=cv2.imdecode(np.frombuffer(v.data,np.uint8),cv2.IMREAD_COLOR)
            if image is None or max(image.shape[:2])>1024:raise BudgetBlocked('Image must decode and fit within 1024 pixels per side')
        elif isinstance(v,str):text_bytes+=len(v.encode())
        elif isinstance(v,dict):
            for k,x in v.items():visit(str(k));visit(x)
        elif isinstance(v,(list,tuple)):
            for x in v:visit(x)
        elif is_dataclass(v):
            for f in fields(v):
                if f.name not in ('provider_details','timestamp','instructions'):visit(getattr(v,f.name))
    visit(messages)
    if model_request_parameters:
        for name in ('function_tools','output_tools','output_object','builtin_tools'):
            visit(getattr(model_request_parameters,name,None))
    # UTF-8 byte count is a conservative text token bound. Bound each resized image too.
    return text_bytes+frames*1000,frames,size
class GuardedModel(WrapperModel):
    def __init__(self,wrapped,ledger,audit,run_id,workflow_id,job_id=None,agent_name=None,paid=True,cancelled=lambda:False,configuration=None):
        super().__init__(wrapped);self.ledger=ledger;self.audit=audit;self.run_id=run_id;self.workflow_id=workflow_id;self.job_id=job_id;self.agent_name=agent_name;self.paid=paid;self.cancelled=cancelled;self.attempts=0;self.configuration=configuration or {}
    @asynccontextmanager
    async def request_stream(self,messages,model_settings,model_request_parameters,run_context=None):
        # Both public entry points use the same reservation/cancellation boundary.
        # Answers are buffered; observable native-tool lifecycle is streamed below.
        from pydantic_ai.models import CompletedStreamedResponse
        response=await self.request(messages,model_settings,model_request_parameters)
        yield CompletedStreamedResponse(response,model_request_parameters=model_request_parameters,replay_events=True)

    async def provider_request(self,messages,settings,parameters):
        async def dispatch():
            if not self.configuration.get('observable_stream'):
                return await self.wrapped.request(messages,settings,parameters)
            seen=set();native_started={}
            async with self.wrapped.request_stream(messages,settings,parameters) as stream:
                async for event in stream:
                    part=getattr(event,'part',None)
                    kind=getattr(part,'part_kind','')
                    if kind not in ('builtin-tool-call','builtin-tool-return'):continue
                    key=(kind,getattr(part,'tool_call_id',None),getattr(event,'event_kind',''))
                    if key in seen:continue
                    seen.add(key)
                    is_end=getattr(event,'event_kind','')=='part_end'
                    arguments=getattr(part,'args',None)
                    if isinstance(arguments,str):
                        try:arguments=json.loads(arguments)
                        except ValueError:arguments={'partial':arguments}
                    event_name='native_tool_arguments' if kind=='builtin-tool-call' and is_end else 'native_tool_started' if kind=='builtin-tool-call' else 'native_tool_finished'
                    if kind=='builtin-tool-return' and is_end:continue
                    call_id=getattr(part,'tool_call_id',None)
                    if event_name=='native_tool_started':native_started[call_id]=time.monotonic()
                    duration_ms=(time.monotonic()-native_started[call_id])*1000 if event_name=='native_tool_finished' and call_id in native_started else None
                    self.audit.write(event_name,duration_ms=duration_ms,status='running' if event_name=='native_tool_started' else 'completed',run_id=self.run_id,job_id=self.job_id,agent_name=self.agent_name,tool_name=getattr(part,'tool_name','web_search'),tool_call_id=getattr(part,'tool_call_id',None),arguments=arguments if isinstance(arguments,dict) else None,output={'observable':getattr(part,'content',None)} if kind=='builtin-tool-return' else None)

                response=stream.get()
                if getattr(response,'state','complete')!='complete':raise RuntimeError('Provider stream ended before terminal usage')
                return response
        task=asyncio.create_task(dispatch())
        try:
            while not task.done():
                if self.cancelled() or (self.paid and self.ledger.state('stopped')=='1'):
                    raise asyncio.CancelledError('AI stopped by operator')
                await asyncio.wait({task},timeout=.2)
            return await task
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)
    async def request(self,messages,model_settings,model_request_parameters):
        import httpx
        from openai import APITimeoutError,APIConnectionError,RateLimitError,InternalServerError
        transient=(TimeoutError,httpx.TimeoutException,APITimeoutError,APIConnectionError,RateLimitError,InternalServerError)
        for retry in range(self.ledger.config['limits']['retry']+1):
            try:return await self.attempt(messages,model_settings,model_request_parameters)
            except BudgetBlocked as exc:
                self.audit.write('budget_blocked',status='budget_exceeded',run_id=self.run_id,parent_run_id=self.workflow_id,job_id=self.job_id,agent_name=self.agent_name,resources={'block_reason':str(exc),'request_count':self.attempts})
                raise
            except RuntimeError as exc:
                basis=getattr(exc,'provider_unsent',None)
                if not (self.paid and basis) or retry>=self.ledger.config['limits']['retry'] or self.attempts>=self.ledger.config['limits']['requests']:raise
                self.audit.write('retry_scheduled',status='transient',run_id=self.run_id,parent_run_id=self.workflow_id,job_id=self.job_id,agent_name=self.agent_name,attempt=self.attempts,error=ToolFailure(code='ProviderRequestNotReceived',message=str(exc),retryable=True),resources={'previous_model_request_id':exc.model_request_id,'unsent_basis':basis,'policy_version':'unsent-generation-one-retry-1','previous_charge_usd':0})
                await asyncio.sleep(.5)
            except transient as exc:
                if self.paid or retry>=self.ledger.config['limits']['retry'] or self.attempts>=self.ledger.config['limits']['requests']:raise
                self.audit.write('retry_scheduled',status='transient',run_id=self.run_id,parent_run_id=self.workflow_id,job_id=self.job_id,agent_name=self.agent_name,attempt=self.attempts,error=ToolFailure(code=type(exc).__name__,message=str(exc),retryable=True))
                await asyncio.sleep(.1)
    async def attempt(self,messages,model_settings,model_request_parameters):
        if self.cancelled():raise asyncio.CancelledError()
        limits=self.ledger.config['limits']
        if self.attempts>=limits['requests']:raise BudgetBlocked('Physical request limit reached')
        include_schemas=(self.paid and self.configuration.get('image_preflight')) or self.configuration.get('workflow_kind') in ('chat_comparison','video_chat')
        tokens,frames,size=input_bound(messages,model_request_parameters if include_schemas else None)
        if frames>limits['frames']:raise BudgetBlocked(f'Image limit reached: {frames} images supplied; maximum {limits["frames"]}.')
        if size>limits['media_bytes']:raise BudgetBlocked('Media byte limit reached')
        if self.paid and self.ledger.state('stopped')=='1':raise BudgetBlocked('Paid requests stopped by operator')
        tokens+=self.configuration.get('native_input_allowance',0)
        if frames>limits['frames']:raise BudgetBlocked(f'Image limit reached: {frames} images supplied; maximum {limits["frames"]}. Reduce scene images, person crops or extra images.')
        if size>limits['media_bytes']:raise BudgetBlocked(f'Media limit reached: {size} bytes supplied; maximum {limits["media_bytes"]}. Reduce image count or size.')
        if not (self.paid and frames and self.configuration.get('image_preflight')) and tokens>limits['input_tokens']:raise BudgetBlocked(f'Input token limit reached: estimated {tokens}; maximum {limits["input_tokens"]}. Reduce request context.')
        output_cap=limits['output_tokens']
        if self.paid:
            self.ledger.require_settled(self.workflow_id)
            with self.ledger.db.connect() as db:row=db.execute('SELECT COALESCE(sum(output_tokens),0),COALESCE(sum(input_tokens),0) FROM reservations WHERE run_id=?',(self.run_id,)).fetchone()
            output_cap-=row[0]
            if not (frames and self.configuration.get('image_preflight')) and tokens+row[1]>limits['input_tokens']:raise BudgetBlocked('Total input token limit reached')
        if output_cap<=0:raise BudgetBlocked('Total output token limit reached')
        settings=dict(model_settings or {});settings['max_tokens']=min(settings.get('max_tokens',output_cap),output_cap,limits.get('output_per_request',1000))
        headroom=self.configuration.get('completion_headroom',self.ledger.config.get('review_team',{}).get('completion_request_reserve',6) if self.configuration.get('workflow_kind')=='safety_chat' and (self.agent_name or '').startswith('specialist_') else 0)
        reservation=None
        if self.paid:
            from safety_app.infrastructure.request_queue import request_slot
            async with request_slot(self.ledger,self.workflow_id,self.run_id,self.audit,self.cancelled):
                if frames and self.configuration.get('image_preflight'):
                    from safety_app.infrastructure.token_preflight import count_input
                    tokens=await count_input(self.wrapped,messages,settings,model_request_parameters,self.ledger,self.audit,self.run_id,self.cancelled,job_id=self.job_id,workflow_id=self.workflow_id)
                    tokens+=self.configuration.get('native_input_allowance',0)
                    if tokens+row[1]>limits['input_tokens']:
                        raise BudgetBlocked(f'Input allowance exhausted: {row[1]} used + {tokens} counted; maximum {limits["input_tokens"]}. This request was not sent for generation.')
                reservation=self.ledger.reserve(self.run_id,self.workflow_id,self.model_name,tokens,settings['max_tokens'],frames,size,completion_headroom=headroom,extra_usd=self.configuration.get('native_fee_allowance',0))
        reserved_usd=None
        if reservation:
            with self.ledger.db.connect() as db:reserved_usd=db.execute('SELECT amount FROM reservations WHERE id=?',(reservation,)).fetchone()[0]/1e6
        from safety_app.infrastructure.provider_transport import RequestTrace, current_trace, connection_message
        self.attempts+=1;request_id=uid();begin=time.monotonic()
        trace=RequestTrace(client_request_id=request_id)
        image_mode='Single image, longest side at most 1024; source metadata excluded' if self.configuration.get('workflow_kind')=='learning_image_review' else 'Timestamped still frames, longest side at most 1024; motion between samples unknown'
        self.audit.write('model_request',status='running',run_id=self.run_id,job_id=self.job_id,parent_run_id=self.workflow_id,agent_name=self.agent_name,attempt=self.attempts,requested_model=self.model_name,resolved_model=self.model_name,provider=self.system,configuration=self.configuration|{'model_settings':settings,'fake_provider':not self.paid,'requested_settings':model_settings or {}},resources={'model_request_id':request_id,'reservation_id':reservation,'reserved_usd':reserved_usd,'input_token_bound':tokens,'output_token_cap':settings['max_tokens'],'frames':frames,'media_bytes':size,'image_mode':image_mode})
        reconciled=False
        trace_token=current_trace.set(trace)
        try:
            async with asyncio.timeout(limits['agent_seconds']):
                response=await self.provider_request(messages,settings,model_request_parameters)
            if not self.paid:
                # TestModel/FunctionModel estimate tokens from a stringified JPEG.
                # Use explicit synthetic counters for fixture control flow, never
                # report them as measured provider usage. Physical guards above
                # still validate real image bounds, scope and request counts.
                from pydantic_ai.usage import RequestUsage
                response.usage=RequestUsage(input_tokens=1,output_tokens=1)
            usage=response.usage
            input_tokens=getattr(usage,'input_tokens',None);output_tokens=getattr(usage,'output_tokens',None)
            from safety_app.infrastructure.usage_cost import token_cost
            # These coordinators use no per-block TTL overrides. A configured
            # request lifetime can price total-only writes; reported splits win.
            uniform_ttl='30m' if (self.configuration.get('workflow_kind') in UNIFORM_TTL_KINDS
                and settings.get('openai_prompt_cache_options',{}).get('ttl')=='30m'
                and not {'input','prompt','prompt_cache_options','tools'} & (settings.get('extra_body') or {}).keys()) else None
            costing=token_cost(usage,self.ledger.config['pricing'].get(self.model_name,{}),getattr(response,'provider_details',None),single_cache_write_ttl=uniform_ttl)
            search_count=0
            if self.configuration.get('native_fee_allowance'):
                search_count=getattr(usage,'details',{}).get('web_search_requests',getattr(usage,'web_searches',0)) or 0
                # A complete terminal response is required before treating zero as known.
                if getattr(response,'state','complete')!='complete':costing['usage_cost_usd']=None
            costing['search_usd']=search_count*self.configuration.get('native_search_price',0)
            if costing['usage_cost_usd'] is not None:costing['usage_cost_usd']+=costing['search_usd']
            actual=costing['usage_cost_usd'] if reservation else None
            if reservation:
                self.ledger.reconcile(reservation,actual,input_tokens,output_tokens);reconciled=True
                with self.ledger.db.connect() as db:r=db.execute('SELECT amount,actual FROM reservations WHERE id=?',(reservation,)).fetchone()
                if r['actual'] is not None and r['actual']>r['amount']:
                    self.ledger.switch(True)
                    self.audit.write('pricing_bound_exceeded',status='stopped',run_id=self.run_id,resources={'reserved_usd':r['amount']/1e6,'actual_usd':r['actual']/1e6})
                from app.agents.model_policy import HOSTED_MODEL
                if response.model_name and response.model_name!=HOSTED_MODEL:raise BudgetBlocked('Provider returned an unapproved model ID')
            # Store observable text and tool calls. Exclude ThinkingPart and binary content.
            parts=[]
            for part in response.parts:
                if getattr(part,'part_kind','') in ('text','tool-call'):
                    parts.append({'kind':part.part_kind,'text':getattr(part,'content',None),'tool':getattr(part,'tool_name',None),'arguments':getattr(part,'args',None),'tool_call_id':getattr(part,'tool_call_id',None)})
            self.audit.write('model_response',run_id=self.run_id,job_id=self.job_id,parent_run_id=self.workflow_id,agent_name=self.agent_name,attempt=self.attempts,resolved_model=self.model_name,output=parts,duration_ms=(time.monotonic()-begin)*1000,resources={'model_request_id':request_id,'reservation_id':reservation,'provider_request_id':getattr(response,'provider_response_id',None),'transport':trace.diagnostics(),'input_tokens':input_tokens,'output_tokens':output_tokens,'reasoning_tokens':getattr(usage,'details',{}).get('reasoning_tokens'),'finish_reason':getattr(response,'finish_reason',None),'cost_usd':actual,**costing,'usage_cost_usd':actual,'usage_is_synthetic':not self.paid})
            if self.paid and actual is None:raise BudgetBlocked('Provider usage could not be priced; reservation retained. Further requests for this review are paused until accounting is resolved.')
            return response
        except BaseException as exc:
            unsent=trace.unsent_basis(exc)
            if reservation and not reconciled:
                # The provider received nothing, so it consumed neither dollars nor stage token allowance.
                if unsent:self.ledger.reconcile(reservation,actual=0,input_tokens=0,output_tokens=0,failed=True)
                else:self.ledger.reconcile(reservation,actual=None,failed=True)
            message=connection_message(exc)
            self.audit.write('model_response',status='cancelled' if isinstance(exc,asyncio.CancelledError) else 'failed',run_id=self.run_id,job_id=self.job_id,parent_run_id=self.workflow_id,agent_name=self.agent_name,attempt=self.attempts,error=ToolFailure(code=type(exc).__name__,message=message or str(exc)[:1000]),duration_ms=(time.monotonic()-begin)*1000,resources={'model_request_id':request_id,'reservation_id':reservation,'actual_usage':0 if unsent else None,'transport':trace.diagnostics(exc)})
            if message:
                failure=RuntimeError(message)
                # Only a request the provider provably never received may be sent again.
                failure.provider_unsent=unsent if reservation and not isinstance(exc,asyncio.CancelledError) else None
                failure.model_request_id=request_id
                raise failure from exc
            raise
        finally:
            current_trace.reset(trace_token)

# Workflows that request one explicit 30-minute cache lifetime, so total-only cache writes can be priced.
UNIFORM_TTL_KINDS=('chat_comparison','video_chat','video_analysis','live_screening','video_safety_review','team_summary')

def input_gate(ctx,tool_def):
    """Each tool round resends the frames, so hide tools once the agent's remaining input allowance cannot
    fit another round plus the final answer; the model then answers instead of failing the window."""
    limit=ctx.deps.budgets.config['limits'].get('input_tokens')
    if not limit:return tool_def
    with ctx.deps.budgets.db.connect() as db:
        used=db.execute('SELECT COALESCE(sum(input_tokens),0) FROM reservations WHERE run_id=?',(ctx.deps.run_id,)).fetchone()[0]
        recent=[r[0] for r in db.execute('SELECT input_tokens FROM reservations WHERE run_id=? ORDER BY created DESC,rowid DESC LIMIT 2',(ctx.deps.run_id,))]
    if not recent:return tool_def
    # A tool round adds its results to the history (about 2k tokens in the kitchen fire run): one more
    # round needs last+growth, and the answer after it needs last+2*growth.
    last=recent[0];growth=max(2000,last-recent[1] if len(recent)>1 else 0)
    return None if limit-used<2*last+3*growth else tool_def


def build(name,model,deps,output,prompt_path,tools,instructions='',tool_timeout=10,mode=''):
    if deps.person_detections is not None:
        from safety_app.agents.shared.people import get_person_detections
        from safety_app.agents.shared.case_tools import compare_person_positions
        tools=[*tools,get_person_detections,compare_person_positions]
    if deps.workflow_kind=='video_safety_review' and (name in ('safety_reviewer','challenge_reviewer') or name.startswith('specialist_')):
        from safety_app.agents.shared.case_tools import get_case_history
        tools=[*tools,get_case_history]
    prompt=prompt_path.read_text()+instructions;deps.agent_name=name
    paid=model.__class__.__module__ not in ('pydantic_ai.models.test','pydantic_ai.models.function')
    guarded=GuardedModel(model,deps.budgets,deps.audit,deps.run_id,deps.budget_workflow_id or deps.workflow_id,deps.job_id,name,paid,deps.cancelled,{'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'pydantic_ai_version':'2.54.0','code_version':'0.2.0','workflow_kind':deps.workflow_kind})
    if mode:
        guarded.configuration['agent_mode']=mode
    if deps.workflow_kind in ('video_analysis','live_screening','video_safety_review'):
        guarded.configuration.update(image_preflight=True, accounting_version='video-token-count-2',
            image_allowance_usd=deps.budgets.config.get('monitoring_image_review',{}).get('image_usd'),
            pricing_basis='counted_tokens_with_operator_image_reservation',budget_workflow_id=deps.budget_workflow_id or deps.workflow_id)
    agent=Agent(guarded,name=name,output_type=output,deps_type=type(deps),instructions=prompt,retries={'tools':0,'output':deps.budgets.config['limits']['retry']},tool_timeout=tool_timeout)
    # Tools default to sequential because most write to deps; a tool marked parallel_safe (it only
    # coordinates child runs with their own deps) may run concurrently with others from the same response.
    for tool in tools:agent.tool(tool,sequential=not getattr(tool,'parallel_safe',False),prepare=input_gate)
    @agent.instructions
    def remaining_allowance(ctx):
        from safety_app.agents.shared.evidence_refs import permitted_aliases
        limits=ctx.deps.budgets.config['limits']
        with ctx.deps.budgets.db.connect() as db:used=db.execute('SELECT count(*) FROM reservations WHERE workflow_id=?',(ctx.deps.budget_workflow_id or ctx.deps.workflow_id,)).fetchone()[0]
        workflow_left=max(0,ctx.deps.budgets.config.get('review_team',{}).get('max_workflow_requests',64)-used)
        completion_reserve=ctx.deps.budgets.config.get('review_team',{}).get('completion_request_reserve',6)
        citations=''
        if ctx.deps.frame_alias_targets:
            citations=('Current frame citations: '+json.dumps(sorted(ctx.deps.seen_frames))+
                '; exact supplied aliases: '+json.dumps(permitted_aliases(ctx.deps))+
                '. Use these exact references only. Unknown/ambiguous IDs and mismatched media/timestamps are rejected. ')
        if name.startswith('specialist_'):
            citations+=('Current citation permissions from Python: source_refs may use only '+json.dumps(sorted(ctx.deps.seen_sources))+
                       '; rule_refs may use only '+json.dumps(sorted(ctx.deps.seen_rules))+
                       '. Sources omitted for budget and NOT available as evidence: '+json.dumps(sorted(ctx.deps.budget_omitted_source_ids))+
                       '. A requested or previewed source is not necessarily returned. Inspect excerpts, not gaps, before citing. '
                       'Individually fetch a central omitted clause before a requirement claim, or leave that requirement unresolved in missing_context. ')
        return (f"Shared workflow has {workflow_left} physical requests remaining; reserve {completion_reserve} for final synthesis and independent challenge. "
                f"Remaining run allowance: {max(0,limits['tools']-ctx.usage.tool_calls)} tool calls, "
                f"{max(0,limits['requests']-ctx.usage.requests)} model requests, "
                f"{max(0,limits['output_tokens']-ctx.usage.output_tokens)} output tokens. "
                "Leave a model request for your final structured answer. A batch consumes one call per tool, not one per batch. "
                "Prioritize unresolved facts that matter to the assigned question. When an evidence packet does not fit, "
                "fetch the most relevant individual clause; never truncate it. Do not refetch an identical failed packet. "
                "If missing context cannot be resolved within the allowance, finish with explicit gaps and uncertain claims. "
                "Do not omit known source gaps or invent evidence to finish. "+citations)
    @agent.output_validator
    def validate(ctx,value):
        d=ctx.deps
        try:
            from safety_app.agents.shared.evidence_refs import normalize_video_references,permitted_aliases
            changes=normalize_video_references(d,value) if d.frame_alias_targets else []
            for o in getattr(value,'observations',[]):
                d.interval(o.media_id,o.start_ms,o.end_ms)
                if not set(o.evidence_refs)<=d.seen_frames:raise ValueError('Observation cites unseen or out-of-scope frames: '+str(sorted(set(o.evidence_refs)-d.seen_frames))+'; allowed evidence IDs: '+str(sorted(d.seen_frames))+'; exact aliases: '+str(permitted_aliases(d)))
            for f in getattr(value,'findings',[]):
                if d.workflow_kind in ('video_analysis','live_screening','video_safety_review'):
                    from safety_app.agents.safety_reviewer.finding_evidence import preserve_visual_gaps
                    if preserve_visual_gaps(f):
                        d.audit.write('finding_uncertainty_preserved',run_id=d.run_id,job_id=d.job_id,agent_name=name,
                            output={'finding_id':f.id,'basis':'supplied_unknown_elements_and_empty_source_set','uncertainty':f.uncertainty})
                if f.review_id!=d.workflow_id:raise ValueError('Finding has a different review ID')
                if not set(f.evidence_refs)<=d.seen_frames or not set(f.source_refs)<=d.seen_sources or not set(f.rule_refs)<=d.seen_rules:raise ValueError('Finding cites unseen evidence, source or rule')
                if not f.source_refs and not f.uncertainty:raise ValueError('Unsourced finding must disclose missing requirements')
            for a in getattr(value,'drafts',[]):
                if a.frame_id not in d.scope.frame_ids or a.approval_status!='draft' or a.reviewer_id is not None:raise ValueError('Assistant cannot approve labels or expand frame scope')
                frame=d.media.db.get('frame',a.frame_id);media=d.media.db.get('media',frame['media_id'])
                if a.source_id!=media['source_id']:raise ValueError('Draft source provenance mismatch')
                if a.class_id not in {c.class_id for c in d.classes(d.scope.class_registry_version).classes}:raise ValueError('Unknown class ID')
            if d.output_check:d.output_check(value)
            if changes:d.audit.write('frame_references_normalized',run_id=d.run_id,job_id=d.job_id,agent_name=name,output={'references':changes})
            return value
        except ValueError as exc:
            d.audit.write('validation_failed',status='failed',run_id=d.run_id,job_id=d.job_id,agent_name=name,error=ToolFailure(code='invalid_output',message=str(exc)))
            d.audit.write('retry_scheduled',status='bounded',run_id=d.run_id,agent_name=name,resources={'output_retry_limit':d.budgets.config['limits']['retry']})
            raise ModelRetry(str(exc))
    return agent
