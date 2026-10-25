"""Explicitly funded sequential Live tests. Status/report are local and never dispatch."""
import argparse,json,sys,re,html,base64
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/src')]
PRIOR=['aaafcdee-4c09-4e5e-b7e5-1aeb79a82ee8','740c2f75-7d1e-483b-95de-66952c17ab24','dc5fa4c1-d9a9-455d-8b9d-d5920f9c784d','6fe23a16-58f3-4334-9e90-cc65dc8be26c','3d0354ad-44b1-4d84-a8b9-07f751497a6c','eae532bd-7b3c-43a3-8a67-399270fff17e']
UNKNOWN='c480abe5-7889-4a38-a685-cc0c1f3afa34'
# Each batch's terms are immutable. A later batch counts every earlier commitment,
# and may only proceed past exactly these named unresolved historical reservations.
BATCHES={'numbered-live-20261024-2':{'prior':PRIOR,'unknown':[UNKNOWN],
  'blockers':'<p><b>Connection reliability:</b> an SSL/read failure stopped image preflight on 05, and a later ambiguous SSL generation failure stopped 06. The new $0.009001 reservation remains unresolved.</p><p><b>Live coverage:</b> the newest-only queue discarded five intervals of 05. Completed playback is not a complete safety review.</p>'},
 'numbered-live-20261024-3':{'prior':[*PRIOR,'952e6697-5ec3-404c-aafb-24b2c7c41ae7','84ca4707-e76b-46fb-8450-783c60ed9fac'],
  'unknown':[UNKNOWN,'519b2e58-13df-43f5-bd06-1b67d749ad06'],
  'blockers':'<p><b>Ambiguous network failure:</b> during video 14 a generation upload completed locally, then the connection broke with no TLS alert. It cannot be proven unsent, so $0.009028 stays reserved and the batch stopped by design. Three other corrupted uploads were proven unsent, settled at $0 and resent.</p><p><b>Lost windows:</b> an ~80-second network outage (no charge) and observer output rejections lost six windows of 05 and one of 13. No window was skipped by Live backpressure.</p>'}}
ID=__import__('os').environ.get('SAFETY_TEST_BATCH','numbered-live-20261024-3')
if ID not in BATCHES:raise SystemExit(f'Unknown test batch {ID}')
OUT=ROOT/'data/artifacts/reports'/ID

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','status','submit','play','report','close']);p.add_argument('--position',type=int,default=0);p.add_argument('--attempt',type=int,default=1);p.add_argument('--cap',type=float,default=.10);p.add_argument('--run');p.add_argument('--reason',default='Operator testing finished');a=p.parse_args()
 from safety_app.infrastructure.runtime import runtime
 from safety_app.infrastructure import review_batches as batch
 rt=runtime()
 if a.action=='prepare':
  from safety_app.modules.monitoring.recordings import sources,resolve,describe
  ss=[]
  for s in sources(ROOT):
   d=s.model_dump(mode='json');path=resolve(ROOT,d['id']);sha,start,end,_=describe(path)
   ss.append({'id':d['id'],'filename':path.name,'sha256':sha,'duration_ms':end-start})
  ss.sort(key=lambda s:(int(re.match(r'\d+',s['filename'])[0]) if re.match(r'\d+',s['filename']) else 100000,s['filename']))
  batch.create(rt.budgets,ID,ss,BATCHES[ID]['prior'],BATCHES[ID]['unknown'],1)
 if a.action=='submit':
  import httpx
  from safety_app.modules.monitoring.recordings import resolve,describe
  s=batch.snapshot(rt.budgets,ID)['sources'][a.position]
  if describe(resolve(ROOT,s['id']))[0]!=s['sha256']:raise ValueError('Frozen footage changed')
  key=f'{ID}:p{a.attempt}:{a.position}'
  batch.enroll(rt.budgets,ID,a.position,a.attempt,key,a.cap)
  with httpx.Client(base_url='http://127.0.0.1:8000',timeout=60) as client:
   auth=client.post('/api/auth/session',json={});auth.raise_for_status()
   response=client.post('/api/review-runs/live',json={'source_id':s['id'],'question':'Describe visible acute events first, then check PPE, access, stacks and slip/trip conditions. Cite retained evidence and disclose uncertainty.','review_strategy':'full','mode':'hosted','idempotency_key':key,'max_cost_usd':a.cap})
   response.raise_for_status();OUT.mkdir(parents=True,exist_ok=True);(OUT/f'{a.position}-start.json').write_text(json.dumps(response.json(),indent=2));print(json.dumps(response.json())[:1500])
 if a.action=='play':
  import httpx
  with httpx.Client(base_url='http://127.0.0.1:8000',timeout=30) as client:
   client.post('/api/auth/session',json={}).raise_for_status()
   response=client.post(f'/api/review-runs/{a.run}/playback',json={'action':'play'});response.raise_for_status();print('Playback started',a.run)
 if a.action=='close':batch.close(rt.budgets,ID,a.reason)
 state=batch.snapshot(rt.budgets,ID);rows=[]
 with rt.db.connect() as db:
  for m in db.execute('SELECT * FROM review_batch_members WHERE batch_id=? ORDER BY attempt,position',(ID,)):
   j=db.execute('SELECT body FROM jobs WHERE key=?',(m['job_key'],)).fetchone()
   if not j:continue
   job=json.loads(j[0]);rid=job['run_id'];children=[json.loads(x[0]) for x in db.execute("SELECT body FROM jobs WHERE json_extract(body,'$.payload.review_run_id')=? OR json_extract(body,'$.payload.analysis_id')=?",(rid,rid))]
   windows=[json.loads(x[0]) for x in db.execute("SELECT body FROM objects WHERE kind='review_window' AND json_extract(body,'$.run_id')=?",(rid,))]
   row={'position':m['position'],'attempt':m['attempt'],'source':state['sources'][m['position']]['filename'],'run_id':rid,'root_status':job['status'],'jobs':[{'id':x['id'],'kind':x['kind'],'status':x['status'],'error':x.get('error')} for x in [job,*children]],'windows':windows,'committed_usd':batch.total(db,[rid])/1e6};rows.append(row)
 print(json.dumps({'budget':state,'runs':rows},indent=2))
 if a.action=='report':
  from safety_app.modules.safety_review.run_report import report_data,report_html
  OUT.mkdir(parents=True,exist_ok=True)
  for r in rows:
   data=report_data(rt,r['run_id'],'local');r.update(completed_windows=data['completed_windows'],total_windows=data['total_windows'],coverage_complete=data['coverage_complete'],usage=data['usage'],case_count=len(data['safety']['cases']));(OUT/f"{r['position']}-review.json").write_text(json.dumps(data,indent=2,default=str));(OUT/f"{r['position']}-review.html").write_text(report_html(data))
  (OUT/'results.json').write_text(json.dumps({'budget':state,'runs':rows},indent=2))
  esc=lambda s:html.escape(str(s))
  cards=[]
  for i,s in enumerate(state['sources']):
   rr=[r for r in rows if r['position']==i]
   if not rr:cards.append(f'<article><h2>{esc(s["filename"])}</h2><p>NOT TESTED — batch stopped before this video. No new AI accuracy or execution result.</p></article>');continue
   for r in rr:
    contact=OUT/(s['filename']+'.jpg');thumb=('data:image/jpeg;base64,'+base64.b64encode(contact.read_bytes()).decode()) if contact.exists() else ''
    failed=[j for j in r['jobs'] if j['status'] not in ('completed',)]; skipped=sum(w.get('status')=='skipped' for w in r['windows']); checks=json.loads((OUT/'visual-checks.json').read_text()) if (OUT/'visual-checks.json').exists() else {}; visual=checks.get(str(i),'No task-specific accuracy label yet.')
    cards.append(f'<article><h2>{esc(s["filename"])}</h2><p>Root: {esc(r["root_status"])} · {r.get('completed_windows',0)}/{r.get('total_windows',len(r['windows']))} intervals inspected · ${r["committed_usd"]:.6f} committed</p><p><b>{'Incomplete coverage' if not r.get('coverage_complete') else 'All intervals inspected'}</b> · {skipped} skipped intervals · {r.get('case_count',0)} {'case' if r.get('case_count')==1 else 'cases'}.</p><p>Assistant pixel spot check: {esc(visual)}</p><p>Noncompleted jobs (including discarded/cancelled): {len(failed)}. Accuracy requires inspection of the linked evidence; completion alone is not an accuracy pass.</p><details><summary>Local footage contact sheet (spot check only)</summary><img style="width:100%;height:auto" src="{thumb}" alt="Timestamped local footage samples"></details><a href="{i}-review.html">Evidence and findings report</a><details><summary>Jobs / failures</summary><pre>{esc(json.dumps(r["jobs"],indent=2))}</pre></details><small>Run {esc(r["run_id"])}</small></article>')
  notes=(OUT/'operator-notes.md').read_text() if (OUT/'operator-notes.md').exists() else 'Testing in progress.'
  note_parts=[]
  for block in notes.split('\n\n'):
   if block.startswith('# '):continue
   if block.startswith('## '):note_parts.append('<h3>'+esc(block[3:])+'</h3>')
   elif block.startswith('1. '):note_parts.append('<ol>'+''.join('<li>'+esc(re.sub(r'^\d+\. ', '',line))+'</li>' for line in block.splitlines())+'</ol>')
   else:note_parts.append('<p>'+esc(block)+'</p>')
  notes_html=''.join(note_parts)
  attempted=len({r['position'] for r in rows})
  content=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Live sample video testing — handoff</title><style>body{{margin:0 auto;max-width:1100px;padding:36px 24px;background:#f4f3ef;color:#111;font:15px/1.6 "Space Grotesk","SF Pro Display",-apple-system,"Helvetica Neue",Arial,sans-serif}}article,header,section{{background:#fff;border-radius:22px;padding:22px 24px;margin:14px 0}}h1,h2,h3{{text-transform:uppercase;letter-spacing:-.03em;line-height:1.05}}h1{{font-size:38px}}h2{{font-size:22px}}h3{{font-size:18px;margin-top:26px}}header{{border-left:8px solid #ff3d2e}}header strong{{color:#b3261b}}summary{{cursor:pointer;padding:10px 0;font-weight:700}}a{{display:inline-block;margin:8px 0;color:#111;font-weight:700}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#efede7;border-radius:10px;padding:10px;font:12px/1.5 ui-monospace,Menlo,monospace}}small{{color:#6b6a65}}img{{max-width:100%;border-radius:12px}}@media(max-width:600px){{body{{padding:12px}}article,header,section{{padding:18px}}h1{{font-size:28px}}}}</style><header><h1>Live video testing stopped</h1><p><strong>Release result: NOT PASSED · {attempted}/{len(state['sources'])} videos attempted · {len(state['sources'])-attempted} not reached</strong></p><p>Sequential causal Live playback. Local footage, Luna through PydanticAI, existing checks retained. No footage is ground-truth labeled. Execution, coverage and visual accuracy are separate results.</p><p><b>${state['committed_usd']:.6f} / $1 committed</b> · ${state['batch_committed_usd']:.6f} new testing · ${state['prior_committed_usd']:.6f} prior balcony commitments · ${state['remaining_usd']:.6f} unspent.</p><p>Earlier unknown reservations named in this batch's terms remain reserved and unresolved: {len(BATCHES[ID]['unknown'])}. New unknown charges: {len(state['unknown_reservation_ids'])}. Batch closed: {state['closed']}.</p></header><section><h2>Blockers</h2>{BATCHES[ID].get('blockers','<p>See the operator notes below.</p>')}</section><section><h2>How to act on this report</h2><p>Open each evidence report. Check exact cited frames and timestamps before accepting any claim. Skipped Live windows are uninspected coverage, not a finding of safety. Preserve historical records and unknown reservations. Inspect failed job diagnostics before resuming; never resend an ambiguously billed request.</p><p>Read <code>harness.md</code> and <code>README.md</code>. Run <code>./scripts/uv run --no-sync python scripts/sample_live_batch.py status</code> for durable accounting. Paid work requires authorization and the shared cap; report/status do not dispatch. The final operator notes are in <code>operator-notes.md</code> alongside this report.</p></section>{''.join(cards)}<section><h2>Operator findings and next actions</h2>{notes_html}</section><section><h2>Frozen source and accounting manifest</h2><details><summary>Show versioned inputs and reservations</summary><pre>{esc(json.dumps(state,indent=2))}</pre></details><p>Calculated local usage is not a provider invoice. Original review allowances, $2/day and $20/month caps still apply. Live backpressure may omit intervals; source filenames do not establish what happens in the footage.</p></section></html>'''
  (OUT/'index.html').write_text(content);print(str(OUT/'index.html'))
if __name__=='__main__':main()
