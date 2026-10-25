"""Testing-only sequential comparison of the shared answer workflow."""
import asyncio
import os
import time
from datetime import datetime,timezone
from openai import AsyncOpenAI
from safety_app.workflows.answering import BranchRunner,public_failure,validate_answer,fixture_route
from safety_app.domain.chat_lab import LabAnswer
from safety_app.domain.models import utc
from safety_app.infrastructure.budgets import BudgetBlocked
from safety_app.infrastructure.request_queue import queue_seconds
from safety_app.modules.safety_review.lab_service import ChatLab,CASES
from safety_app.agents.safety_reviewer.lab_agent import LabOutputRejected
from safety_app.agents.shared.lab_release import claim_errors

async def compare(rt,job):
    lab=ChatLab(rt);value=lab.get(job.id,job.site_id);client=None
    if job.payload['mode']=='hosted':
        if os.getenv('SAFETY_PAID_REVIEW')!='1' or not os.getenv('OPENAI_API_KEY'):raise ValueError('Hosted provider unavailable')
        client=AsyncOpenAI(api_key=os.environ['OPENAI_API_KEY'],max_retries=0)
    async def heartbeat():
        while True:rt.jobs.heartbeat(job.id);await asyncio.sleep(20)
    heart=asyncio.create_task(heartbeat());deadline=time.monotonic()+job.payload['agents_config']['limits']['workflow_seconds']
    try:
        async with asyncio.timeout(job.payload['agents_config']['limits']['workflow_seconds']):
            for branch in value.order:
                current=next(b for b in value.branches if b.branch==branch)
                if rt.jobs.cancelled(job.id):raise asyncio.CancelledError()
                current.state='running';current.started_at=utc();current.queue_ms=max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(job.created_at)).total_seconds()*1000)
                rt.db.save('lab_comparison',value);begin=time.monotonic();runner=BranchRunner(rt,job,branch,client);runner.deadline=deadline
                waited_before=queue_seconds(rt.db,job.run_id)
                try:
                    route,answer,challenge,citations=await runner.run()
                    current.routing=route;current.answer=answer;current.challenge=challenge;current.citations=citations
                    current.state='blocked' if route.scope=='block' else 'completed'
                    case=next((c for c in CASES if c.id==job.payload['request'].get('test_case_id') and c.question==job.payload['question']),None)
                    cited={id for claim in answer.claims for id in claim.citation_ids}
                    source_text=' '.join(c.title+' '+(c.url or '')+' '+(c.clause or '')+' '+str(c.provenance.get('sources',[])) for c in citations if c.id in cited)
                    expected=case.expected_sources if case else []
                    current.issue_codes=challenge.failure_codes if challenge else []
                    current.quality={'citation_validity':not claim_errors(answer,runner.knowledge.seen,None,'product') if route.task=='product' else bool(answer.claims),'claim_count':len(answer.claims),'expected_source_coverage':sum(s in source_text for s in expected)/len(expected) if expected else None,'scope_matches_expected':route.scope==case.expected_scope if case else None,'unresolved_claims':challenge.unresolved_claims if challenge else [],'has_substantive_answer':any(c.kind!='uncertain' for c in answer.claims),'usefulness_rating':None,'outcome':answer.outcome,'fixture_only':job.payload['mode']=='fixture'}
                except BaseException as exc:
                    code,message=public_failure(exc)
                    current.state='cancelled' if isinstance(exc,asyncio.CancelledError) else 'blocked' if isinstance(exc,BudgetBlocked) else 'failed'
                    current.error=type(exc).__name__+': '+code;current.issue_codes=[code]
                    current.answer=LabAnswer(answer=message,outcome='unavailable',completion='incomplete')
                    # Only a fully accounted local output error is independent of the next branch.
                    if not isinstance(exc,LabOutputRejected) or not runner.known_usage():
                        for pending in value.branches:
                            if pending.state=='queued':
                                pending.state='cancelled' if isinstance(exc,asyncio.CancelledError) else 'blocked';pending.not_started=True;pending.issue_codes=['not_started'];pending.answer=LabAnswer(answer='This workflow was not started because the comparison stopped at a shared provider, cancellation, or budget boundary.',outcome='unavailable',completion='incomplete')
                        raise
                finally:
                    runner.close_recovery();current.recovery=runner.recovery
                    waited=max(0,queue_seconds(rt.db,job.run_id)-waited_before)*1000
                    current.ended_at=utc();current.execution_ms=max(0,(time.monotonic()-begin)*1000-waited);current.queue_ms+=waited
                    rt.db.save('lab_comparison',value)
                    rt.audit.write('branch_finished',status=current.state,job_id=job.id,run_id=job.run_id,provenance={'branch':branch},duration_ms=current.execution_ms,output={'outcome':current.answer.outcome if current.answer else None,'recovery':current.recovery.model_dump()})
            webs=[{(c.text,c.retrieved_at,str(c.provenance.get('sources'))) for c in branch.citations if c.kind=='web_summary'} for branch in value.branches]
            if webs[0]!=webs[1]:value.live_web_differences=['Live web evidence differs between branches. Do not attribute evidence-coverage differences to routing efficiency.']
            rt.db.save('lab_comparison',value);rt.db.save('lab_result',value,immutable=True)
            return [job.id]
    finally:
        heart.cancel();await asyncio.gather(heart,return_exceptions=True)
        if client:await client.close()
