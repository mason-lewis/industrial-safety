"""Offline full-coverage receipt and self-contained report. No credentials or calls."""
import argparse,asyncio,json,shutil,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/src')]

async def main():
    import cv2,numpy as np
    from safety_app.infrastructure.runtime import Runtime
    from safety_app.domain.video_analysis import VideoAnalysisRequest
    from safety_app.modules.monitoring.recordings import sources
    from safety_app.modules.safety_review.video_analysis import VideoAnalysisService
    from safety_app.modules.safety_review.review_runs import ReviewRuns
    from safety_app.modules.safety_review.run_report import report_data,report_html
    from safety_app.workers.runner import execute
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=600)
    parser.add_argument('--output',type=Path,default=ROOT/'submission/evidence/course-fixture.json')
    args=parser.parse_args()
    if not 1<=args.seconds<=600:parser.error('seconds must be 1..600')
    root=Path(tempfile.mkdtemp(prefix='industrial-course-fixture-'))
    shutil.copytree(ROOT/'configs',root/'configs');(root/'data/input').mkdir(parents=True)
    writer=cv2.VideoWriter(str(root/'data/input/fixture.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),2,(64,64))
    assert writer.isOpened()
    for i in range(args.seconds*2):writer.write(np.full((64,64,3),i%200,np.uint8))
    writer.release();rt=Runtime(root);begin=time.monotonic()
    request=VideoAnalysisRequest(source_id=sources(root)[0].id,question='Inspect the four visible-condition categories',review_strategy='full',mode='fixture',idempotency_key='course-fixture')
    job=VideoAnalysisService(rt).request(request,'local');done=await execute(rt,job)
    assert done.status=='completed',done.error
    status=ReviewRuns(rt).status(job.run_id,'local')
    assert status.coverage_complete and status.completed_windows==(args.seconds+9)//10
    # One complete specialist example; other jobs remain explicitly pending.
    child=status.safety.cases[0].review_job
    assert (await execute(rt,child)).status=='completed'
    status=ReviewRuns(rt).status(job.run_id,'local')
    events=rt.audit.rows(10000,job_id=job.id)+rt.audit.rows(10000,job_id=child.id)
    calls=[r for r in events if r['event_type']=='model_request']
    assert all(r['configuration']['fake_provider'] for r in calls)
    receipt={'data_kind':'synthetic_fixture_only','seconds':args.seconds,'elapsed_seconds':round(time.monotonic()-begin,3),
        'coverage_complete':status.coverage_complete,'completed_windows':status.completed_windows,'frames':sum(len(w.frame_ids) for w in status.windows),
        'concerns':len(status.safety.cases),'pending_specialists':status.pending_specialists,'fixture_model_calls':len(calls),
        'provider_requests':0,'provider_cost_usd':None,'root':str(root),'run_id':job.run_id,
        'trace':[{'agent':r['agent_name'],'event':r['event_type'],'timestamp':r.get('timestamp'),'job_id':r.get('job_id'),'fake_provider':r.get('configuration',{}).get('fake_provider')} for r in calls],
        'limitations':['Fixed FunctionModel responses; no visual-accuracy evidence.','Synthetic token counters excluded from provider-usage totals.','Remaining assessments intentionally left pending in this receipt.']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(receipt,indent=2)+'\n')
    args.output.with_suffix('.html').write_text(report_html(report_data(rt,job.run_id,'local')))
    print(json.dumps({k:v for k,v in receipt.items() if k!='trace'},indent=2))

if __name__=='__main__':asyncio.run(main())
