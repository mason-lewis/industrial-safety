"""Application scheduler; model latency and failures never block frame processing."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import time
from uuid import uuid4

from app.agents.reviewers import PROMPT_VERSION, ReviewDeps, build_agents, initial_packet
from app.agents.settings import AGENT_NAMES, ModelConfig, SharedBudget
from app.evidence.store import EvidenceScope
from app.models.contracts import AgentRunRecord, Context, IncidentMarker, RuleCandidate


def context(record):
    return {key:getattr(record,key) for key in Context.model_fields}


def response_intervals(timeline):
    """Only established visible anchors are used; an absent action remains unknown."""
    stamps={item.label:item.timestamp_ms for item in timeline}
    pairs={'anchor_to_response':('incident_anchor','first_visible_response'),
           'anchor_to_control':('incident_anchor','first_visible_control'),
           'anchor_to_clearance':('incident_anchor','apparent_clearance')}
    return {key:b-a if (a:=stamps.get(left)) is not None and (b:=stamps.get(right)) is not None and b>=a else None for key,(left,right) in pairs.items()}


class ReviewService:
    def __init__(self,evidence_store,repository,get_config,generation_is_current,model_factory=None):
        self.store=evidence_store; self.repository=repository; self.get_config=get_config
        self.generation_is_current=generation_is_current; self.model_factory=model_factory
        self.model_config=ModelConfig.from_env()
        self.always_on=os.getenv('AI_ALWAYS_ON','false').lower()=='true'
        self.enabled=self.always_on
        self.hosted_images_allowed=self.always_on or os.getenv('HOSTED_IMAGES_ALLOWED','false').lower()=='true'
        if self.always_on:
            from safety_app.infrastructure.runtime import runtime
            if runtime().budgets.state('stopped')=='1':self.enabled=False
        self.queue=asyncio.Queue(maxsize=int(os.getenv('AI_QUEUE_LIMIT','4')))
        self.retrospective_queue=asyncio.Queue(maxsize=2)
        self.worker=None; self.retrospective_worker=None; self.running={}; self.pending=set()
        from app.agents.scenes import SceneService
        self.scenes=SceneService(self)
        self.skipped=0; self.errors=0; self.total_requests=0; self.total_tokens=0; self.last_latency_ms=None; self.provider_access='unverified'

    def state(self):
        key_present=bool(os.getenv('OPENAI_API_KEY')) or self.model_factory is not None
        available=self.enabled and self.hosted_images_allowed and key_present and self.get_config().analysis_mode!='cv_only'
        return {'enabled':self.enabled,'always_on':self.always_on,'hosted_images_allowed':self.hosted_images_allowed,
                'available':available,'credentials_present':key_present,
                'provider_access':self.provider_access,
                'status':('Ready for review; model access '+self.provider_access) if available else 'AI unavailable: missing credentials' if self.enabled and not key_present else 'CV-only' if not self.enabled else 'Hosted image permission required',
                'model':self.model_config.default_model,'default_model':self.model_config.default_model,
                'agent_models':{name:self.model_config.resolve(name) for name in AGENT_NAMES},
                'reasoning':self.model_config.reasoning_effort or 'model default','reasoning_effort':self.model_config.reasoning_effort,
                'queue_depth':self.queue.qsize()+int(self.scenes.pending is not None),'retrospective_queue_depth':self.retrospective_queue.qsize(),
                'model_requests':sum(self.scenes.dispatched.values()),'tokens':sum(self.scenes.tokens.values()),'errors':self.errors,
                'skipped_reviews':self.skipped,'agent_latency_ms':self.last_latency_ms,
                'scene':self.scenes.state(),'cost_estimate':None,'image_notice':'Scheduled reviews send up to 15 timestamped full-scene JPEGs; bounded extra images are optional. Those bytes and bounded track/policy data leave this machine only when AI and hosted images are enabled.'}

    def set_enabled(self,enabled,hosted_images_allowed):
        self.enabled=bool(enabled); self.hosted_images_allowed=bool(hosted_images_allowed)
        if not self.enabled or not self.hosted_images_allowed:
            for task in list(self.running.values()): task.cancel()
            self._drain(self.queue); self._drain(self.retrospective_queue)
            self.scenes.cancel()
        # Deliberately no provider validation request and no CV-only backlog replay.
        return self.state()

    def _drain(self,queue):
        while not queue.empty():
            item=queue.get_nowait(); self.pending.discard(item[0].id); queue.task_done()
            record=item[0]
            if isinstance(record,RuleCandidate):
                record.ai_status='disabled'; self._save_candidate_ai(record)

    def _require(self):
        if not self.enabled: raise ValueError('AI disabled. Enable AI and hosted image review before requesting a review.')
        if self.get_config().analysis_mode=='cv_only': raise ValueError('CV only does not submit language-model requests. Select an AI mode and Enable AI.')
        if not self.hosted_images_allowed: raise ValueError('Hosted image review is disabled; enable it explicitly.')
        if not os.getenv('OPENAI_API_KEY') and self.model_factory is None: raise ValueError('AI unavailable: backend OPENAI_API_KEY is missing. CV continues.')

    async def enqueue(self,candidate):
        if not self.enabled:
            candidate.ai_status='disabled'; self._save_candidate_ai(candidate); return
        try: self._require()
        except ValueError as error:
            candidate.ai_status='error'; self._save_candidate_ai(candidate)
            self._record_unavailable(candidate,str(error)); return
        await self._enqueue(candidate,False)

    async def manual_review(self,candidate):
        self._require()
        if not self.generation_is_current(candidate.session_id,candidate.run_generation):
            raise ValueError('This event is historical. Open its run or use a retrospective incident window.')
        await self._enqueue(candidate,True)
        return {'status':candidate.ai_status,'candidate_id':candidate.id}

    async def _enqueue(self,candidate,manual):
        if candidate.id in self.pending:
            self.skipped+=1; return
        if self.queue.full():
            self.skipped+=1; candidate.ai_status='skipped'; self._save_candidate_ai(candidate)
            self._record_unavailable(candidate,'Review queue full; candidate preserved','skipped'); return
        candidate.ai_status='queued'; self._save_candidate_ai(candidate)
        self.pending.add(candidate.id)
        snapshot=self.repository.candidate_at(candidate.id,candidate.evidence_cutoff_ms)
        scoped=RuleCandidate.model_validate(snapshot)
        scoped.ai_status='queued'
        self.queue.put_nowait((scoped,manual))
        if self.worker is None or self.worker.done(): self.worker=asyncio.create_task(self._worker(self.queue,False))

    async def review_incident(self,incident):
        self._require()
        if not self.generation_is_current(incident.session_id,incident.run_generation): raise ValueError('Incident belongs to a historical generation')
        if incident.id in self.pending: return {'status':'queued','incident_id':incident.id}
        if self.retrospective_queue.full(): raise ValueError('Retrospective queue full')
        self.pending.add(incident.id); self.retrospective_queue.put_nowait((incident.model_copy(deep=True),True))
        if self.retrospective_worker is None or self.retrospective_worker.done(): self.retrospective_worker=asyncio.create_task(self._worker(self.retrospective_queue,True))
        return {'status':'queued','incident_id':incident.id}

    async def _worker(self,queue,retrospective):
        while not queue.empty():
            record,manual=await queue.get()
            try:
                if not self.enabled or not self.hosted_images_allowed: continue
                if not self.generation_is_current(record.session_id,record.run_generation): continue
                if not retrospective and not manual:
                    stamps=[ref.timestamp_ms for ref,_ in self.store.records.values() if ref.session_id==record.session_id and ref.run_generation==record.run_generation]
                    if stamps and max(stamps)-record.evidence_cutoff_ms>5000:
                        record.ai_status='skipped'; self._save_candidate_ai(record)
                        self.skipped+=1; self._record_unavailable(record,'Queued packet stale; not submitted as live','skipped'); continue
                task=asyncio.create_task(self._run(record,retrospective)); self.running[record.id]=task
                await task
            except asyncio.CancelledError:
                pass
            finally:
                self.running.pop(record.id,None); self.pending.discard(record.id); queue.task_done()

    def cancel_generation(self,session_id,generation):
        self.scenes.reset()
        for id,task in list(self.running.items()):
            # There is one current session, and all running outputs also check generation before commit.
            task.cancel()
        for queue in (self.queue,self.retrospective_queue):
            kept=[]
            while not queue.empty():
                item=queue.get_nowait(); queue.task_done()
                if item[0].session_id==session_id and item[0].run_generation==generation:
                    self.pending.discard(item[0].id)
                else: kept.append(item)
            for item in kept: queue.put_nowait(item)

    async def close(self):
        await self.scenes.close()
        for task in list(self.running.values()): task.cancel()
        for worker in (self.worker,self.retrospective_worker):
            if worker: worker.cancel()
        await asyncio.gather(*list(self.running.values()),*[worker for worker in (self.worker,self.retrospective_worker) if worker],return_exceptions=True)

    def _save_candidate_ai(self, record):
        try: latest=RuleCandidate.model_validate(self.repository.get('candidate',record.id))
        except KeyError: latest=record
        latest.ai_status=record.ai_status
        self.repository.save('candidate',latest)

    def _record_unavailable(self,record,error,status='error'):
        run=AgentRunRecord(**context(record),id=str(uuid4()),status=status,error=error,
            candidate_id=record.id if isinstance(record,RuleCandidate) else None,
            incident_id=record.id if isinstance(record,IncidentMarker) else None,model_id=self.model_config.default_model)
        self.repository.save('agent_run',run)

    async def _run(self,record,retrospective):
        started=time.monotonic(); name='incident_response_analyst' if retrospective else 'safety_coordinator'
        run=AgentRunRecord(**context(record),id=str(uuid4()),status='running',model_id=self.model_config.resolve(name),candidate_id=None if retrospective else record.id,incident_id=record.id if retrospective else None)
        self.repository.save('agent_run',run)
        budget=SharedBudget()
        deps=ReviewDeps(self.store,self.repository,None,None,budget=budget)
        try:
            if not retrospective:
                record.ai_status='running'
                latest=RuleCandidate.model_validate(self.repository.get('candidate',record.id))
                latest.ai_status='running'; self.repository.save('candidate',latest)
            budget=SharedBudget(request_limit=int(os.getenv('AI_LIVE_REQUEST_LIMIT','8')) if not retrospective else 12,
                token_limit=int(os.getenv('AI_LIVE_TOKEN_LIMIT','16000')) if not retrospective else 24000,
                image_limit=int(os.getenv('AI_LIVE_IMAGE_LIMIT','32')) if not retrospective else 24)
            scope=EvidenceScope(record.session_id,record.run_generation,record.timestamp_ms+record.post_ms if retrospective else record.evidence_cutoff_ms,max(0,record.timestamp_ms-record.pre_ms) if retrospective else max(0,record.evidence_cutoff_ms-12000),'retrospective' if retrospective else 'live')
            config=self.get_config().model_copy(deep=True)
            from app.models.contracts import LabConfig
            try:
                saved=self.repository.get('config',f'config-{record.config_version}')
                config=LabConfig.model_validate(saved.get('config',saved))
            except KeyError:
                if config.version!=record.config_version: raise ValueError('Original configuration snapshot unavailable')
            procedures={}
            try:
                snapshot=self.repository.get('procedures',f'config-{record.config_version}')
                if not record.processing_time or snapshot.get('captured_at','9999')>record.processing_time:
                    raise KeyError('Procedure snapshot was created after the event')
                procedures=snapshot['documents']
            except KeyError:
                if not retrospective: record.limitations.append('Original procedure snapshot unavailable; policy evidence withheld')
            deps=ReviewDeps(self.store,self.repository,config,scope,candidate=None if retrospective else record,incident=record if retrospective else None,budget=budget,procedures=procedures)
            def reserve_request():
                self._require()
                if not self.generation_is_current(record.session_id,record.run_generation): raise ValueError('Generation changed')
                self.scenes.reserve((record.session_id,record.run_generation))
            deps.reserve_request=reserve_request
            provenance={'id':run.id,**context(record),'config':config.model_dump(mode='json'),'scope':vars(scope),'model_config':{'model':run.model_id,'settings':self.model_config.settings()},'prompt_version':PROMPT_VERSION,'procedures_sha256':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in procedures.items()}}
            self.repository.save('review_provenance',provenance)
            self._require()
            packet=initial_packet(deps)
            if not packet: raise ValueError('No retained images in authorized review window')
            agents=build_agents(self.model_config,deps,self.model_factory)
            prompt='Review this evidence packet and return a typed result. Context: '+str(context(record))+'\nRecord: '+record.model_dump_json()+'\nAuthorized scope: '+str(vars(scope))+'\nConfiguration: '+config.model_dump_json()
            if retrospective: prompt+='\nCoverage: '+str(self.store.coverage(record))+'\nTimeline labels: incident_anchor, first_visible_hazard, first_visible_response, first_visible_control, apparent_clearance. User anchor is supplied; all visible-action timestamps require a cited frame. No criteria are supplied unless tools find an explicit local procedure.'
            async with asyncio.timeout(60 if retrospective else float(os.getenv('AI_LIVE_DEADLINE_S','20'))):
                result=await agents[name].run([prompt,*packet],deps=deps,usage=deps.usage,usage_limits=budget.limits())
            if not self.generation_is_current(record.session_id,record.run_generation) or not self.enabled or self.get_config().analysis_mode=='cv_only':
                run.status='cancelled'; run.error='Generation changed; output discarded'; return
            output=result.output
            output.model_id=run.model_id; output.prompt_version=PROMPT_VERSION
            output.token_usage={'requests':deps.usage.requests,'input_tokens':deps.usage.input_tokens,'output_tokens':deps.usage.output_tokens,'total_tokens':deps.usage.total_tokens}
            output.tool_usage=budget.trace; output.elapsed_ms=(time.monotonic()-started)*1000
            if retrospective:
                from app.models.contracts import ResponseTimeline
                labels={item.label for item in output.timeline}
                if 'incident_anchor' not in labels:
                    output.timeline.insert(0,ResponseTimeline(label='incident_anchor',timestamp_ms=record.timestamp_ms,explanation='Explicit user marker; worker awareness is unknown'))
                for label in ('first_visible_hazard','first_visible_response','first_visible_control','apparent_clearance'):
                    if label not in labels: output.timeline.append(ResponseTimeline(label=label,explanation='Not established by retained visible evidence'))
                output.intervals_ms=response_intervals(output.timeline)
                coverage=self.store.coverage(record)
                output.uncertainty.extend(['Sampled-frame timestamp precision; gaps between retained frames are unobserved.']+(['Missing footage ranges: '+str(coverage['missing_ranges'])] if coverage['missing_ranges'] else []))
            payload=output.model_dump(mode='json'); payload['id']=str(uuid4())
            self.repository.save('response' if retrospective else 'assessment',payload)
            run.status='reviewed'; self.provider_access='verified'
            if not retrospective: record.ai_status='reviewed'
        except asyncio.CancelledError:
            run.status='cancelled'; run.error='AI disabled or playback generation changed';
            if not retrospective: record.ai_status='disabled' if not self.enabled else 'skipped'
        except TimeoutError:
            run.status='timeout'; run.error='Review deadline exceeded; candidate preserved'
            if not retrospective: record.ai_status='timeout'
            self.errors+=1
        except Exception as error:
            self.provider_access='error'
            run.status='error'; run.error=f'{type(error).__name__}: {str(error)[:600]}'
            if not retrospective: record.ai_status='error'
            self.errors+=1
        finally:
            if deps.provider_client is not None:
                try: await deps.provider_client.close()
                except Exception: run.error=(run.error or '')+' Provider cleanup failed'
            run.tool_usage=budget.trace; run.token_usage={'requests':deps.usage.requests,'attempts':budget.requests,'input_tokens':deps.usage.input_tokens,'output_tokens':deps.usage.output_tokens,'total_tokens':deps.usage.total_tokens,'images':budget.images,'transmitted_images':budget.transmitted_images,'delegations':budget.delegations}
            run.elapsed_ms=(time.monotonic()-started)*1000; self.last_latency_ms=run.elapsed_ms
            self.total_requests+=budget.requests; self.total_tokens+=deps.usage.total_tokens
            key=(record.session_id,record.run_generation)
            self.scenes.tokens[key]=self.scenes.tokens.get(key,0)+deps.usage.total_tokens
            self.repository.save('agent_run',run)
            if not retrospective and self.generation_is_current(record.session_id,record.run_generation):
                # Update AI status only: rule lifecycle may have advanced during review.
                try: latest=RuleCandidate.model_validate(self.repository.get('candidate',record.id)); latest.ai_status=record.ai_status; self.repository.save('candidate',latest)
                except KeyError: pass
