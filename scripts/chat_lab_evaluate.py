#!/usr/bin/env python3
"""Explicit operator-run evaluation; dry-run by default. No request retry or auto-rating."""
import argparse
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4
import httpx

ROOT=Path(__file__).resolve().parents[1]
TERMINAL={'completed','failed','cancelled','blocked','budget_exceeded','incomplete'}

def micros(value):return int((Decimal(str(value))*1000000).to_integral_value(rounding=ROUND_CEILING))
def schedule(corpus):return [{'case':case,'order':order} for order in corpus['orders'] for case in corpus['cases']]
def admissible(allowance,committed,parent,unknown):return not unknown and allowance-committed>=parent

def ledger_usage(db_path,workflow_ids):
    if not workflow_ids:return {'committed_micros':0,'actual_micros':0,'reserved_micros':0,'unknown_requests':0}
    with sqlite3.connect('file:'+str(db_path)+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT amount,actual,state FROM reservations WHERE workflow_id IN ('+','.join('?' for _ in workflow_ids)+')',workflow_ids).fetchall()
    return {'committed_micros':sum(actual if actual is not None else amount for amount,actual,_ in rows),'actual_micros':sum(actual or 0 for _,actual,_ in rows),'reserved_micros':sum(amount for amount,actual,_ in rows if actual is None),'unknown_requests':sum(state=='unknown' for _,_,state in rows)}

def save(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(path)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true',help='Make paid comparisons under the explicitly authorized cap.')
    parser.add_argument('--allowance-usd',type=Decimal,default=Decimal('0.50'))
    parser.add_argument('--output',type=Path)
    parser.add_argument('--corpus',type=Path,default=ROOT/'configs/chat-lab-evaluation.json')
    args=parser.parse_args();corpus=json.loads(args.corpus.read_text());planned=schedule(corpus)
    if args.allowance_usd<=0 or args.allowance_usd>Decimal(str(corpus['allowance_usd'])):parser.error('Allowance must be positive and no greater than the versioned corpus cap.')
    if not args.execute:
        print(json.dumps({'paid_requests':0,'comparisons':len(planned),'allowance_usd':str(args.allowance_usd),'corpus_version':corpus['version'],'note':'Use --execute only with explicit operator authorization.'},indent=2));return
    if not args.output:parser.error('--output is required for paid execution.')
    # A new directory prevents accidentally resetting the allowance or replaying a batch.
    args.output.mkdir(parents=True,exist_ok=False)
    receipt=args.output/'receipt.json';manifest={'id':str(uuid4()),'corpus':corpus,'corpus_sha256':hashlib.sha256(args.corpus.read_bytes()).hexdigest(),'allowance_usd':str(args.allowance_usd),'runs':[],'pending':None,'stop_reason':None,'human_review':'pending'}
    save(receipt,manifest)
    config=json.loads((ROOT/'configs/agents.yaml').read_text());parent=micros(config['limits']['workflow_usd']);workflow_ids=[]
    try:
        with httpx.Client(base_url='http://127.0.0.1:8000',timeout=20,trust_env=False) as client:
            client.post('/api/auth/session',json={}).raise_for_status()
            cap=client.get('/api/chat-lab/capabilities');cap.raise_for_status();cap=cap.json()
            if not cap['hosted_available'] or cap['workflow_version']!=corpus['workflow_version']:raise RuntimeError('Expected hosted workflow is not available; no comparison submitted.')
            cases=client.get('/api/chat-lab/cases');cases.raise_for_status()
            case_ids={case['question']:case['id'] for case in cases.json()}
            for index,item in enumerate(planned):
                if (args.output/'STOP').exists():
                    manifest['stop_reason']='operator_requested_stop_between_comparisons';break
                usage=ledger_usage(ROOT/'data/app.sqlite',workflow_ids);manifest['ledger']=usage
                if not admissible(micros(args.allowance_usd),usage['committed_micros'],parent,usage['unknown_requests']):
                    manifest['stop_reason']='unknown_charges' if usage['unknown_requests'] else 'remaining_allowance_below_full_parent_limit';break
                body={'question':item['case']['question'],'order':item['order'],'mode':'hosted','idempotency_key':manifest['id']+':'+str(index)}
                if body['question'] in case_ids:body['test_case_id']=case_ids[body['question']]
                manifest['pending']={'index':index,**item,'request':body};save(receipt,manifest)
                # Persist the idempotency key before POST. An ambiguous failure stops the batch;
                # do not automatically resend or reset its allowance on a subsequent invocation.
                response=client.post('/api/chat-lab/comparisons',json=body);response.raise_for_status();value=response.json()
                workflow_ids.append(value['job']['run_id']);manifest['pending']['comparison_id']=value['id'];save(receipt,manifest)
                print(json.dumps({'started':index+1,'case':item['case']['id'],'id':value['id'],'order':item['order']}),flush=True)
                deadline=time.monotonic()+240
                while value['job']['status'] in ('queued','running'):
                    if time.monotonic()>deadline:
                        client.post('/api/chat-lab/comparisons/'+value['id']+'/cancel').raise_for_status()
                        raise RuntimeError('Evaluation wait expired; cancellation requested. Check retained reservations before any more testing.')
                    time.sleep(1)
                    response=client.get('/api/chat-lab/comparisons/'+value['id']);response.raise_for_status();value=response.json()
                export=client.get('/api/chat-lab/comparisons/'+value['id']+'/export');export.raise_for_status()
                save(args.output/(f'{index+1:02d}-'+item['case']['id']+'.json'),export.json())
                manifest['runs'].append({'index':index,**item,'comparison_id':value['id'],'workflow_id':value['job']['run_id'],'job_status':value['job']['status'],'outcomes':[b.get('answer',{}).get('outcome') for b in value['branches']],'usage':value['usage'],'versions':value['versions'],'snapshot':value['snapshot']['id'],'assistant_review':None,'human_rating':None})
                manifest['pending']=None;manifest['ledger']=ledger_usage(ROOT/'data/app.sqlite',workflow_ids);save(receipt,manifest)
                print(json.dumps({'completed':index+1,'case':item['case']['id'],'outcomes':manifest['runs'][-1]['outcomes'],'usage_usd':value['usage']['calculated_usd'],'batch_committed_usd':manifest['ledger']['committed_micros']/1e6}),flush=True)
                if manifest['ledger']['unknown_requests'] or manifest['ledger']['reserved_micros']:
                    manifest['stop_reason']='unknown_or_unsettled_charges';break
                if any(b['answer'] and b['answer'].get('outcome')=='unavailable' for b in value['branches']):
                    manifest['stop_reason']='technical_failure_requires_inspection';break
            else:manifest['stop_reason']='planned_comparisons_completed'
    except Exception as exc:
        manifest['stop_reason']=type(exc).__name__+': evaluation interrupted; inspect the pending request before further paid work.'
        raise
    finally:
        manifest['ledger']=ledger_usage(ROOT/'data/app.sqlite',workflow_ids)
        manifest['unfinished_comparisons']=len(planned)-len(manifest['runs']);save(receipt,manifest)
        print(json.dumps({'receipt':str(receipt),'stop_reason':manifest['stop_reason'],'unfinished':manifest['unfinished_comparisons'],'ledger':manifest['ledger']}),flush=True)

if __name__=='__main__':main()
