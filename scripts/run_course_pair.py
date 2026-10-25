"""OPERATOR ONLY: matched hosted baseline/full runs, preserving all budget limits."""
import argparse,asyncio,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/src')]

async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,help='Neutral filename already in data/input, e.g. clip-001.mp4')
    parser.add_argument('--allow-paid',action='store_true',help='Explicitly authorize provider requests for this pair')
    parser.add_argument('--output',type=Path,default=ROOT/'data/artifacts/course-runs')
    args=parser.parse_args()
    if not args.allow_paid:parser.error('No calls made. Operator must explicitly supply --allow-paid.')
    from safety_app.infrastructure.environment import load_environment
    load_environment();os.environ['SAFETY_PAID_REVIEW']='1'
    from safety_app.infrastructure.runtime import Runtime
    from safety_app.infrastructure.demo import prepare_demo
    from safety_app.modules.monitoring.recordings import sources
    from safety_app.modules.safety_review.video_analysis import VideoAnalysisService
    from safety_app.modules.safety_review.run_report import report_data,report_html
    from safety_app.domain.video_analysis import VideoAnalysisRequest
    from safety_app.domain.models import uid,utc
    from safety_app.workers.runner import execute
    prepare_demo(ROOT,ROOT);rt=Runtime(ROOT)
    matches=[s for s in sources(ROOT) if s.name==args.source]
    if len(matches)!=1:raise ValueError('Choose one exact library filename; import/upload the candidate first')
    def claim_owned(job):
        with rt.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT status FROM jobs WHERE id=?',(job.id,)).fetchone()['status']!='queued':
                raise RuntimeError('Another worker owns this job. Stop this command; inspect its existing result.')
            job.status='running';job.updated_at=utc()
            db.execute("UPDATE jobs SET status='running',body=? WHERE id=?",(job.model_dump_json(),job.id))
        rt.jobs.heartbeat(job.id)
        return job
    for condition,escalate in [('screening_only',False),('full',True)]:
        if rt.budgets.status()['windows']['day']['remaining_usd']<=0:raise RuntimeError('Daily allowance exhausted; no new run started')
        request=VideoAnalysisRequest(source_id=matches[0].id,question='Inspect visible PPE, blocked access, unstable stacks, and slip/trip conditions. Cite the retained evidence and disclose uncertainty.',review_strategy='full',mode='hosted',jurisdiction='unknown',escalate_to_safety_team=escalate,idempotency_key=uid())
        job=VideoAnalysisService(rt).request(request,'local');begin=time.monotonic()
        await execute(rt,claim_owned(job))
        children=[j for j in rt.jobs.list() if j.payload.get('analysis_id')==job.run_id and j.kind=='video_safety_review']
        for child in children:await execute(rt,claim_owned(child))
        data=report_data(rt,job.run_id,'local');data['operator_measurement']={'condition':condition,'elapsed_seconds':time.monotonic()-begin,'code_version':'industrial-course-2026-10-23','source_filename':args.source}
        folder=args.output/condition;folder.mkdir(parents=True,exist_ok=True)
        base=folder/(Path(args.source).stem+'-'+job.run_id)
        base.with_suffix('.json').write_text(json.dumps(data,indent=2)+'\n')
        base.with_suffix('.html').write_text(report_html(data))
        rows=[r for id in [job.id,*[c.id for c in children]] for r in rt.audit.rows(10000,job_id=id)]
        audit=folder/'audits';audit.mkdir(exist_ok=True);(audit/(base.name+'.json')).write_text(json.dumps(rows,indent=2)+'\n')
        print(json.dumps({'condition':condition,'report':str(base.with_suffix('.json')),'coverage_complete':data['coverage_complete'],'usage':data['usage']},indent=2))
        if data['usage']['unknown_cost_requests']:raise RuntimeError('Unknown charges retained; reconcile before another run')

if __name__=='__main__':asyncio.run(main())
