"""One explicitly funded balcony retest. Default is read-only; --submit admits one durable job."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/src')]

PRIOR=['aaafcdee-4c09-4e5e-b7e5-1aeb79a82ee8','740c2f75-7d1e-483b-95de-66952c17ab24',
       'dc5fa4c1-d9a9-455d-8b9d-d5920f9c784d','6fe23a16-58f3-4334-9e90-cc65dc8be26c']
UNKNOWN='c480abe5-7889-4a38-a685-cc0c1f3afa34'
KEY='balcony-original-detail-1-one-funded-retest'
PREFLIGHT_KEY='balcony-preflight-recovery-1-one-funded-retest'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submit',action='store_true')
    parser.add_argument('--preflight-retest',action='store_true',help='The separately authorized follow-up; counts all five previous attempts')
    args=parser.parse_args()
    key=PREFLIGHT_KEY if args.preflight_retest else KEY
    prior=[*PRIOR,'3d0354ad-44b1-4d84-a8b9-07f751497a6c'] if args.preflight_retest else PRIOR
    from safety_app.infrastructure.runtime import runtime
    from safety_app.infrastructure.environment import load_environment
    from safety_app.infrastructure.review_allowances import create_test_budget,usage
    from safety_app.modules.monitoring.recordings import resolve,describe
    load_environment();rt=runtime()
    with rt.db.connect() as db:
        previous=usage(db,prior)
        old=db.execute('SELECT body FROM jobs WHERE key=?',(key,)).fetchone()
    if old:
        job=json.loads(old[0]);print(json.dumps({'existing_job_id':job['id'],'review_run_id':job['run_id'],'status':job['status'],'submitted':False}));return
    print(json.dumps({'previous_committed_usd':previous/1e6,'new_cap_usd':.07,'total_cap_usd':.15,'will_submit':args.submit}))
    if not args.submit:return
    path=resolve(ROOT,'e69c5772ee2a5b40')
    if describe(path)[0]!='6718bf7c0284346cb9aa73963efec6478d776c26f7d433dd39081a908e1eaf62':
        raise ValueError('The authorized source has changed')
    with rt.db.connect() as db:
        if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone():raise ValueError('Wait for other jobs before this isolated test')
    create_test_budget(rt.budgets,key,key,prior,[UNKNOWN])
    from safety_app.domain.video_analysis import VideoAnalysisRequest
    from safety_app.modules.safety_review.video_analysis import VideoAnalysisService
    request=VideoAnalysisRequest(source_id='e69c5772ee2a5b40',question='Describe visible acute events first, then check PPE, access, stacks and slip/trip conditions. Cite retained evidence and disclose uncertainty.',
        review_strategy='full',mode='hosted',idempotency_key=key,max_cost_usd=.07)
    job=VideoAnalysisService(rt).request(request,'local')
    print(json.dumps({'job_id':job.id,'review_run_id':job.run_id,'submitted':True,'cap_usd':.07}))


if __name__=='__main__':main()
