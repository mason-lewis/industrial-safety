"""Run one durable job at a time. No external calls unless explicit opt-in is set."""
import argparse
import asyncio
from safety_app.infrastructure.runtime import runtime
from safety_app.infrastructure.budgets import BudgetBlocked
from safety_app.domain.models import ToolFailure,uid
KINDS={'monitoring':['replay','live'],'review':['event_review','facility_review','periodic_review','annotation_assistance','safety_chat','video_analysis','video_safety_review','live_screening','team_summary'],'training':['train','evaluate','register']}
KINDS['chat']=['chat_comparison']
KINDS['video']=['video_analysis','video_safety_review','live_screening','team_summary']
KINDS['video-chat']=['video_chat']
async def execute(rt,job,model_factory=None,detector=None):
    try:
        if job.kind in ('replay','live'):
            from safety_app.workflows.monitoring import monitor
            refs=await monitor(rt,job,detector)
        elif job.kind=='chat_comparison':
            from safety_app.modules.safety_review.lab_service import enabled
            if not enabled():raise ValueError('Chat testing worker is disabled')
            from safety_app.workflows.chat_lab import compare
            refs=await compare(rt,job)
        elif job.kind=='video_chat':
            from safety_app.workflows.video_chat import chat
            refs=await chat(rt,job)
        elif job.kind=='safety_chat':
            from safety_app.workflows.safety_chat import chat
            refs=await chat(rt,job,model_factory)
        elif job.kind=='video_analysis':
            from safety_app.workflows.video_analysis import analyze
            refs=await analyze(rt,job,model_factory)
        elif job.kind=='live_screening':
            from safety_app.workflows.full_review import live_window
            refs=await live_window(rt,job,model_factory)
        elif job.kind=='video_safety_review':
            from safety_app.workflows.video_safety import assess
            refs=await assess(rt,job,model_factory)
        elif job.kind=='team_summary':
            from safety_app.workflows.team_combiner import combine
            refs=await combine(rt,job,model_factory)
        elif job.kind=='annotation_assistance':
            from safety_app.workflows.annotation import assist
            refs=await assist(rt,job,model_factory)
        elif job.kind=='periodic_review':
            from safety_app.workflows.agent_stage import run_stage
            from safety_app.agents.video_observer.models import ObserverRequest
            from safety_app.domain.models import CandidateEvent
            from safety_app.modules.monitoring.events import merge_event
            result,deps=await run_stage(rt,job,'video_observer',ObserverRequest(media_ids=job.payload['media_ids'],context=job.payload.get('context','')),model_factory)
            refs=[]
            for o in result.observations:rt.db.save('observation',o,immutable=True);refs.append(o.id)
            for event_type in result.possible_events:
                mid=job.payload['media_ids'][0];media=rt.db.get('media',mid)
                event=merge_event(rt.db,CandidateEvent(site_id=job.site_id,camera_id=media['camera_id'],event_type=event_type,start_ms=job.payload['start_ms'],end_ms=job.payload['end_ms'],media_ids=[mid],observation_ids=refs,producer_version='periodic-observer'))
                rt.jobs.start('event_review',f'{job.id}:event:{event.id}',{'event_id':event.id,'media_ids':[mid],'start_ms':event.start_ms,'end_ms':event.end_ms},job.site_id);refs.append(event.id)
        elif job.kind in ('event_review','facility_review'):
            from safety_app.workflows.event_review import review
            refs=await review(rt,job,model_factory)
        else:
            from safety_app.workflows.dataset_cycle import dataset_cycle
            refs=await dataset_cycle(rt,job)
        finished=rt.jobs.finish(job,'completed',refs)
        if job.kind in ('video_analysis','live_screening') and finished.status=='completed':
            recover_video_handoffs(rt,job.payload.get('parent_job_id',job.id))
        if job.kind in ('video_analysis','live_screening','video_safety_review'):
            start_summaries(rt,job)
        return finished
    except asyncio.CancelledError:
        return rt.jobs.finish(job,'cancelled',error=ToolFailure(code='cancelled',message='Work cancelled; partial evidence is retained'))
    except Exception as exc:
        from pydantic_ai.exceptions import UsageLimitExceeded
        status='budget_exceeded' if isinstance(exc,(BudgetBlocked,UsageLimitExceeded)) else 'failed'
        if job.kind in KINDS['review']:
            rt.db.save('coverage_gap',{'id':uid(),'job_id':job.id,'media_ids':job.payload.get('media_ids',[]),'start_ms':job.payload.get('start_ms'),'end_ms':job.payload.get('end_ms'),'reason':str(exc)})
        return rt.jobs.finish(job,status,error=ToolFailure(code=type(exc).__name__,message=str(exc)))
async def worker(rt,kind,once=False):
    if kind=='chat':
        from safety_app.modules.safety_review.lab_service import enabled
        if not enabled():raise ValueError('Set SAFETY_CHAT_TESTING=1 to start this worker')
    import time
    last_handoff_check=-float('inf');running=set()
    try:
     while True:
        import json
        rt.config.update(json.loads((rt.root/'configs/agents.yaml').read_text()))
        from app.agents.model_policy import validate_agent_models
        validate_agent_models(rt.config)
        if kind in ('review','video') and time.monotonic()-last_handoff_check>=5:
            recover_video_handoffs(rt);start_summaries(rt);last_handoff_check=time.monotonic()
        # Several jobs (for example independent case reviews) may run at once; claims stay atomic, the
        # provider request slot (limits.concurrency) and shared budget ledger still bound the work.
        parallel=1 if once else max(1,int(rt.config['limits'].get('worker_parallel',1)))
        running={t for t in running if not t.done()}
        while len(running)<parallel:
            job=rt.jobs.claim(KINDS[kind],rt.config.get('review_team',{}).get('live_parallel_windows',1))
            if not job:break
            running.add(asyncio.create_task(execute(rt,job)))
        if once:
            await asyncio.gather(*running);return
        if asyncio.current_task().cancelling():
            for t in running:t.cancel()
            await asyncio.gather(*running,return_exceptions=True);return
        await asyncio.sleep(.5)
    finally:
        # A stopped worker must not leave claimed jobs running unattended.
        for t in running:t.cancel()
        if running:await asyncio.gather(*running,return_exceptions=True)

def start_summaries(rt,job=None):
    """Queue the team combiner once a run's case reviews are all terminal. Never blocks the finished job."""
    from safety_app.workflows.team_combiner import start_ready_summaries
    try:
        run_ids=None if job is None else {job.payload.get('review_run_id') or job.payload.get('analysis_id') or job.run_id}
        start_ready_summaries(rt,run_ids)
    except Exception as exc:
        rt.audit.write('team_summary_deferred',status='failed',job_id=job.id if job else None,error=ToolFailure(code=type(exc).__name__,message=str(exc)))

def recover_video_handoffs(rt,parent_job_id=None):
    from safety_app.modules.safety_review.video_cases import dispatch_ready_cases
    try:dispatch_ready_cases(rt,parent_job_id)
    except Exception as exc:
        rt.audit.write('video_handoff_deferred',status='failed',job_id=parent_job_id,error=ToolFailure(code=type(exc).__name__,message=str(exc)))
def main(default=None):
    parser=argparse.ArgumentParser();parser.add_argument('--kind',choices=KINDS,default=default or 'review');parser.add_argument('--once',action='store_true');args=parser.parse_args();asyncio.run(worker(runtime(),args.kind,args.once))
if __name__=='__main__':main()
