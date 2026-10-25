"""One ordered live pass, only after explicit operator funding of the durable batch."""
import json,subprocess,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/src')]
from safety_app.infrastructure.runtime import runtime
from safety_app.infrastructure.review_batches import snapshot,enroll
from safety_app.modules.monitoring.recordings import resolve,describe
import httpx
import os
rt=runtime();ID=os.environ.get('SAFETY_TEST_BATCH','numbered-live-20261024-3')
with httpx.Client(base_url='http://127.0.0.1:8000',timeout=60) as client:
 client.post('/api/auth/session',json={}).raise_for_status()
 for position,source in enumerate(snapshot(rt.budgets,ID)['sources']):
  state=snapshot(rt.budgets,ID)
  if state['closed'] or state['unknown_reservation_ids']:print('STOP: sealed allowance or new unknown charge',flush=True);break
  key=f'{ID}:p1:{position}'
  with rt.db.connect() as db:old=db.execute('SELECT body FROM jobs WHERE key=?',(key,)).fetchone()
  if old:run=json.loads(old[0])['run_id']
  else:
   cap=min(.15,state['remaining_usd'])
   if cap<.015:print('STOP: insufficient bounded allowance remains',flush=True);break
   if describe(resolve(ROOT,source['id']))[0]!=source['sha256']:raise ValueError('Source changed')
   # Free read-only gate against outages; this network's sporadic corruption (~4%) is tolerated.
   if subprocess.run([sys.executable,str(ROOT/'scripts/review_transport_probe.py'),'--count','40','--max-failures','4'],cwd=ROOT,stdout=subprocess.DEVNULL).returncode:
    print('STOP: free six-image upload check failed more than 10%; network outage or severe corruption',flush=True);break
   enroll(rt.budgets,ID,position,1,key,cap)
   response=client.post('/api/review-runs/live',json={'source_id':source['id'],'question':'Describe visible acute events first, then check PPE, access, stacks and slip/trip conditions. Cite retained evidence and disclose uncertainty.','review_strategy':'full','mode':'hosted','idempotency_key':key,'max_cost_usd':cap});response.raise_for_status();run=response.json()['run']['id']
   print('CREATED',source['filename'],run,'cap',cap,flush=True)
   response=client.post(f'/api/review-runs/{run}/playback',json={'action':'play'});response.raise_for_status()
  print('WATCH',position,source['filename'],run,flush=True)
  started=time.monotonic();last=0
  while True:
   state=snapshot(rt.budgets,ID)
   if state['unknown_reservation_ids']:
    client.post(f'/api/review-runs/{run}/cancel').raise_for_status()
    print('STOP: new unknown charge; generation halted',state['unknown_reservation_ids'],flush=True);sys.exit(2)
   with rt.db.connect() as db:
    jobs=[dict(x) for x in db.execute("SELECT id,kind,status FROM jobs WHERE json_extract(body,'$.run_id')=? OR json_extract(body,'$.payload.review_run_id')=? OR json_extract(body,'$.payload.analysis_id')=?",(run,run,run))]
   active=[j for j in jobs if j['status'] in ('queued','running')]
   if not active:print('FINISHED',source['filename'],json.dumps(jobs),'committed',state['committed_usd'],flush=True);break
   # Live analysis may trail playback; queued windows drain after EOF.
   if time.monotonic()-started>1800:
    client.post(f'/api/review-runs/{run}/cancel').raise_for_status();raise RuntimeError('30-minute per-source operator timeout; inspect saved work')
   if time.monotonic()-last>30:print('PROGRESS',source['filename'],'active',len(active),'committed',state['committed_usd'],flush=True);last=time.monotonic()
   time.sleep(2)
  subprocess.run([sys.executable,str(ROOT/'scripts/sample_live_batch.py'),'report'],cwd=ROOT,stdout=subprocess.DEVNULL,check=True)
print('ORDERED PASS STOPPED',flush=True)
