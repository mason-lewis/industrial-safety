"""Deterministic, escaped, offline-readable reports for Live and Review runs, including incomplete runs.

HTML and JSON carry the same content: summary, coverage, findings with embedded evidence, the agent
team (built from the run's audit events), Ask conversations, person detection, usage and sources.
The HTML uses the app's Daylight Hi-Vis styling with system fonts only, so it stays readable offline.
"""
import base64
import json
from datetime import datetime
from html import escape
from urllib.parse import urlparse
from safety_app.modules.safety_review.review_runs import ReviewRuns

CATEGORIES={'ppe':'Protective equipment','blocked_access':'Access concern','unstable_stack':'Unstable stack','slip_trip':'Slip or trip hazard',
            'struck_by':'Struck-by concern','fall':'Fall concern','fire_burn':'Fire or burn concern'}
DECISIONS={'accepted':'Accepted','rejected':'Dismissed','uncertain':'Needs more evidence'}
ROLE_NAMES={'coordinator':'Coordinator','observer':'Video observer','detail':'Detail observer','verifier':'Visual verifier','lead':'Safety lead',
            'specialist':'Specialist','challenger':'Independent challenger'}
ROLE_PARENTS={'observer':['coordinator'],'detail':['observer'],'verifier':['detail'],'lead':['observer','coordinator'],
              'specialist':['lead'],'challenger':['lead','observer','coordinator']}
ROLE_ORDER=['coordinator','observer','detail','verifier','lead','specialist','challenger']


def thumbnail(raw,width=480):
    """A small JPEG for the HTML page; the JSON keeps the full retained frame."""
    try:
        import cv2,numpy as np
        image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
        if image is None:return None
        h,w=image.shape[:2]
        if w>width:image=cv2.resize(image,(width,int(h*width/w)),interpolation=cv2.INTER_AREA)
        ok,buf=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,72])
        return 'data:image/jpeg;base64,'+base64.b64encode(buf.tobytes()).decode() if ok else None
    except Exception:
        return None


def readable(value):
    return str(value or '').replace('_',' ')


def category_name(value):
    return CATEGORIES.get(value,readable(value).capitalize())


def category(value):
    return CATEGORIES.get(value,readable(value).capitalize())


def clock(ms,precise=False):
    """Matches the app's formatTime: m:ss, or m:ss.mmm when precise."""
    ms=max(0,int(ms or 0));whole=f'{ms//60000}:{ms//1000%60:02d}'
    return f'{whole}.{ms%1000:03d}'.rstrip('0') if precise and ms%1000 else whole


def local_time(value):
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).astimezone().strftime('%d %b %Y, %H:%M %Z')
    except ValueError:return str(value or '')


def case_state(item):
    if item.get('assessment'):
        findings=item['assessment'].get('findings') or []
        if item.get('unassessed_screening_ids'):return 'Partly reviewed'
        return 'Needs your review' if findings and all(f.get('status')=='challenged' for f in findings) else 'Team reviewed'
    if item.get('status')=='running':return 'Team reviewing'
    if item.get('status') in ('queued','awaiting_video_completion'):return 'Waiting for team'
    return 'Review unfinished'


def run_events(rt,run_id,job_id,site_id,after=0,limit=None):
    """Audit events for a run's own job and its Live screening / specialist child jobs (same scope as the Activity API)."""
    with rt.db.connect() as db:
        jobs=[r[0] for r in db.execute("SELECT id FROM jobs WHERE id=? OR ((json_extract(body,'$.payload.analysis_id')=? OR json_extract(body,'$.payload.review_run_id')=?) AND json_extract(body,'$.site_id')=?)",(job_id,run_id,run_id,site_id))]
    if not jobs:return []
    with rt.audit.db.connect() as db:
        rows=db.execute(f"SELECT sequence,body FROM audit WHERE sequence>? AND json_extract(body,'$.job_id') IN ({','.join('?' for _ in jobs)}) ORDER BY sequence"+(' LIMIT ?' if limit else ''),
                        [after,*jobs,*([limit] if limit else [])]).fetchall()
    return [json.loads(r['body'])|{'sequence':r['sequence']} for r in rows]


def team_summary(events):
    """Role-level agent tree with per-role runs, requests, tools, cost and status (mirrors the app's agent map)."""
    nodes={};runs={}
    for row in events:
        run_id=row.get('run_id');kind=row.get('event_type');name=row.get('agent_name') or ''
        if kind=='run_started' and name:
            why=(row.get('provenance') or {}).get('role')
            if name=='video_observer':role,key,label=('detail','detail',ROLE_NAMES['detail']) if why=='detail_observer' else ('observer','observer',ROLE_NAMES['observer'])
            elif name=='challenge_reviewer':role,key,label=('verifier','verifier',ROLE_NAMES['verifier']) if why=='visual_verifier' else ('challenger','challenger',ROLE_NAMES['challenger'])
            elif name=='safety_reviewer':role,key,label='lead','lead',ROLE_NAMES['lead']
            elif name.startswith('specialist_'):role,key,label='specialist',name,name[11:].capitalize()+' specialist'
            else:continue
            node=nodes.setdefault(key,{'id':key,'role':role,'label':label,'runs':0,'completed':0,'failed':0,'running':0,'requests':0,'tools':0,'cost_usd':0.0})
            node['runs']+=1;runs[run_id]=node;continue
        node=runs.get(run_id)
        if not node:continue
        if kind=='run_finished':node['completed']+=1
        elif kind=='run_failed':node['failed']+=1
        elif kind=='model_request':node['requests']+=1
        elif kind=='tool_started':node['tools']+=1
        elif kind=='model_response':
            cost=(row.get('resources') or {}).get('cost_usd')
            if isinstance(cost,(int,float)) and cost>=0:node['cost_usd']+=cost
    ordered=sorted(nodes.values(),key=lambda n:(ROLE_ORDER.index(n['role']),n['label']))
    for n in ordered:
        n['running']=n['runs']-n['completed']-n['failed'];n['cost_usd']=round(n['cost_usd'],6)
        n['status']='running' if n['running'] else 'failed' if n['failed'] else 'completed'
    present={n['role']:n['id'] for n in ordered}
    edges=[[next((present[r] for r in ROLE_PARENTS[n['role']] if r in present),'coordinator'),n['id']] for n in ordered]
    return {'nodes':ordered,'edges':edges,'events':len(events)}


def ask_summary(rt,id,site_id):
    from safety_app.modules.safety_review.video_chat import VideoChat
    try:state=VideoChat(rt).state(id,site_id)
    except (KeyError,ValueError):return {'turns':[]}
    turns=[]
    for turn in state.turns:
        frames=[]
        for ref in turn.evidence:
            frame={'id':ref.id,'timestamp_ms':ref.frame.timestamp_ms,'origin':ref.origin}
            try:
                raw=rt.media.image(ref.id);frame['embedded_image']='data:image/jpeg;base64,'+base64.b64encode(raw).decode();frame['thumbnail']=thumbnail(raw,360)
            except (ValueError,OSError,KeyError):frame['image_error']='Evidence image unavailable'
            frames.append(frame)
        answer=turn.answer
        turns.append({'id':turn.id,'question':turn.message,'mode':turn.mode,'status':turn.job.status,'outcome':answer.outcome if answer else None,
            'answer':answer.answer if answer else None,'claims':[c.model_dump(mode='json') for c in (answer.claims if answer else [])],
            'missing_context':answer.missing_context if answer else [],'citations':[{'id':c.id,'title':c.title,'kind':c.kind,'version':c.version,'url':c.url} for c in turn.citations],
            'frames':frames,'usage':turn.usage.model_dump(mode='json'),'limit_usd':turn.limit_usd,'created_at':turn.job.created_at})
    return {'turns':turns}


def report_data(rt, id, site_id):
    reviews=ReviewRuns(rt)
    status=reviews.status(id,site_id)
    result=status.model_dump(mode='json')
    refs={ref for signal in status.safety.screenings for ref in signal.check.evidence_refs}
    refs.update(person.frame_id for person in status.people)
    source_ids={ref for case in status.safety.cases if case.assessment for finding in case.assessment.findings for ref in finding.source_refs}
    result['frames']=[];result['sources']=[]
    for ref in sorted(refs):
        frame=rt.db.get('frame',ref)
        try:
            raw=rt.media.image(ref);frame['embedded_image']='data:image/jpeg;base64,'+base64.b64encode(raw).decode()
            frame['thumbnail']=thumbnail(raw)
        except (ValueError,OSError):frame['image_error']='Retained evidence is missing or its hash does not match'
        result['frames'].append(frame)
    for ref in sorted(source_ids):
        try:source=rt.db.get('source',ref)
        except KeyError:
            result['sources'].append({'id':ref,'text':'Source unavailable; citation cannot be verified in this export.'})
            continue
        if source.get('site_id')!=site_id:raise ValueError('Report source outside site')
        result['sources'].append(source)
    from safety_app.modules.monitoring.recordings import library
    path=library(rt.root).get(status.run.source_id)
    result['source_name']=path.name if path else None
    result['team']=team_summary(run_events(rt,status.run.id,status.run.job_id,site_id))
    result['ask']=ask_summary(rt,id,site_id)
    cases=status.safety.cases;usage=result['usage'];ask_cost=sum(t['usage'].get('calculated_usd',0) for t in result['ask']['turns'])
    decided={d.target_id for d in status.decisions}
    result['summary']={'kind':status.run.kind,'state':status.run.state,'coverage_complete':status.coverage_complete,
        'completed_windows':status.completed_windows,'total_windows':status.total_windows,
        'skipped_windows':sum(1 for w in status.windows if w.status in ('skipped','failed','cancelled')),
        'findings':len(cases),'high_priority':sum(1 for c in cases if c.priority=='high'),'team_reviewed':sum(1 for c in cases if c.assessment),
        'pending_specialists':status.pending_specialists,'decided':sum(1 for c in cases if c.case.id in decided),
        'unknown_checks':sum(1 for s in status.safety.screenings if s.check.result=='unknown'),
        'review_cost_usd':usage.get('usage_cost_usd',0),'ask_cost_usd':round(ask_cost,6),
        'unresolved_charges':int(usage.get('unknown_cost_requests',0) or 0)+sum(int(t['usage'].get('unknown_requests',0) or 0) for t in result['ask']['turns']),
        'ask_questions':len(result['ask']['turns']),'agents':sum(n['runs'] for n in result['team']['nodes'])}
    try:result['team_brief']=rt.db.get('team_brief',id)
    except KeyError:result['team_brief']=None
    result['executive']=executive(result)
    return result



def case_description(item):
    """Same plain-language line the app shows: the reviewed conclusion, else the screening condition."""
    assessment=item.get('assessment') or {}
    reviewed=next((f['conclusion'] for f in assessment.get('findings',[]) if f.get('status')=='reviewed' and f.get('conclusion')),None)
    if reviewed:return reviewed
    if any(f.get('status')=='challenged' for f in assessment.get('findings',[])):
        return 'Uncertain after the independent check. Open the evidence and qualifications.'
    return (item.get('screenings') or [{}])[0].get('check',{}).get('condition') or 'Review the visual evidence for this concern.'


def first_sentence(text,limit=200):
    text=' '.join(str(text or '').split())
    for stop in ('. ','; '):
        k=text.find(stop)
        if 0<k<limit:return text[:k+1].rstrip(';')+('' if text[k]=='.' else '.')
    return text if len(text)<=limit else text[:limit].rsplit(' ',1)[0]+'…'


def executive(data):
    """Manager summary, grouped by concern type: verdict, brief, attention groups, next steps, confidence.
    Uses only recorded findings, decisions and coverage; the combiner brief (team_brief) leads when present."""
    cases=data['safety']['cases'];brief=data.get('team_brief') or {}
    decisions={}
    for d in sorted(data['decisions'],key=lambda d:d.get('created_at','')):decisions[d['target_id']]=d['decision']
    chosen=list(brief.get('priority_case_ids',[]))
    state=data['run'].get('state');high=[c for c in cases if c['priority']=='high']
    stopped=state in ('failed','cancelled','budget_exceeded','interrupted')
    complete=data['coverage_complete'] and not data['pending_specialists']
    if stopped:level,headline='partial','Review stopped early: results cover only the checked footage'
    elif high:level,headline='risk',f'{len(high)} priority risk{"s" if len(high)!=1 else ""} need{"" if len(high)!=1 else "s"} attention'
    elif cases:level,headline='watch',f'{len(cases)} concern{"s" if len(cases)!=1 else ""} to review'
    elif complete:level,headline='clear','No concerns found in the checked footage'
    else:level,headline='partial','Review in progress: no concerns found so far'
    groups={}
    for c in cases:groups.setdefault(c['case']['category'],[]).append(c)
    def rank(c):return (c['case']['id'] not in chosen,chosen.index(c['case']['id']) if c['case']['id'] in chosen else 0,c['priority']!='high',not c.get('assessment'),c['case']['start_ms'])
    out=[]
    for category,items in groups.items():
        items=sorted(items,key=rank)
        reviewed=[f for c in items for f in (c.get('assessment') or {}).get('findings',[]) if f.get('status')=='reviewed' and f.get('conclusion')]
        line=first_sentence(reviewed[0]['conclusion']) if reviewed else first_sentence((items[0].get('screenings') or [{}])[0].get('check',{}).get('condition') or 'Review the visual evidence.')
        actions=[a['action'] for c in items for f in (c.get('assessment') or {}).get('findings',[]) for a in f.get('corrective_actions',[])]
        actions+=[r for c in items for f in (c.get('assessment') or {}).get('findings',[]) for r in f.get('recommendations',[])]
        frames=[]
        for c in items:
            refs=[r for s_ in c['screenings'] for r in s_['check']['evidence_refs']]
            if refs and refs[0] not in frames:frames.append(refs[0])
        times=sorted(c['case']['start_ms'] for c in items)
        decided=[decisions[c['case']['id']] for c in items if c['case']['id'] in decisions]
        out.append({'category':category,'title':category_name(category),'count':len(items),'high':sum(1 for c in items if c['priority']=='high'),
            'start':clock(times[0]),'end':clock(max(c['case']['end_ms'] for c in items)),'times':[clock(t) for t in times],
            'priority_times':sorted(set(clock(c['case']['start_ms']) for c in items if c['priority']=='high'),key=lambda t:[int(x) for x in t.split(':')]),
            'summary':line,'uncertain':not reviewed,'action':actions[0] if actions else None,'frames':frames[:3],
            'reviewed':sum(1 for c in items if c.get('assessment')),'pending':sum(1 for c in items if not c.get('assessment')),
            'accepted':decided.count('accepted'),'dismissed':decided.count('rejected'),'needs_evidence':decided.count('uncertain'),
            'undecided':len(items)-len(decided),'case_ids':[c['case']['id'] for c in items]})
    out.sort(key=lambda g:(-g['high'],-g['count'],g['start']))
    seen=set();actions=[]
    for a in [*brief.get('actions',[]),*(g['action'] for g in out if g['action'])]:
        k=a.strip().lower()
        if k and k not in seen:seen.add(k);actions.append(a.strip())
    steps=actions[:2]
    undecided=sum(g['undecided'] for g in out)
    first=list(dict.fromkeys(t for g in out for t in g['priority_times']))[:3] or [g['start'] for g in out][:2]
    if undecided:steps.append(f'Decide on {undecided} open finding{"s" if undecided!=1 else ""}, starting with {", ".join(first)}.')
    unfinished=sum(g['pending'] for g in out)
    if unfinished:steps.append(f'{unfinished} team review{"s are" if unfinished!=1 else " is"} still unfinished; treat those findings as provisional.')
    # A long episode (such as a continuing fire) hands the team at most a bounded packet of moments.
    left=sorted({s_['check']['start_ms'] for c in cases for s_ in c.get('screenings',[]) if s_['id'] in set(c.get('unassessed_screening_ids') or [])})
    if left:steps.append(f'Watch {", ".join(clock(ms) for ms in left[:3])}{" …" if len(left)>3 else ""} yourself: the team did not review {"these moments" if len(left)!=1 else "this moment"}.')
    unchecked=[w for w in data['windows'] if w['status']!='completed']
    if unchecked:
        spans=', '.join(f'{clock(w["start_ms"])}–{clock(w["end_ms"])}' for w in unchecked[:3])+(' …' if len(unchecked)>3 else '')
        steps.append(f'Watch {spans} yourself: {"these intervals were" if len(unchecked)!=1 else "this interval was"} not checked.')
    unknown=sum(1 for s_ in data['safety']['screenings'] if s_['check']['result']=='unknown')
    confidence=brief.get('confidence_note') or (f'{data["completed_windows"]} of {data["total_windows"]} intervals checked from sampled frames'
        +(f'; {unknown} checks unclear from the footage' if unknown else '')+'.')
    seconds=round((data['run']['end_ms']-data['run']['start_ms'])/1000)
    kind='Live' if data['run']['kind']=='live' else 'Recorded'
    context=f'{seconds}-second {kind.lower()} review' if seconds<120 else f'{seconds//60}-minute {kind.lower()} review'
    context+=f' · {len(cases)} finding{"s" if len(cases)!=1 else ""} across {len(out)} concern type{"s" if len(out)!=1 else ""}' if cases else ' · no findings'
    summary=brief.get('summary') or ''
    # Back-compat for callers that read individual attention items.
    attention=[{'case_id':g['case_ids'][0],'title':g['title'],'priority':'high' if g['high'] else 'routine','summary':g['summary'],'action':g['action'],
                'frame_id':g['frames'][0] if g['frames'] else None} for g in out]
    return {'level':level,'headline':headline,'summary':summary,'context':context,'groups':out,'attention':attention,'actions':steps[:4],
            'confidence':confidence,'unknown_checks':unknown}

CSS="""
:root{--bg:#f4f3ef;--card:#fff;--ink:#111;--ink2:#3a3934;--muted:#6b6a65;--line:#e4e2dc;--soft:#efede7;--y:#ffd400;--ys:#fff6c2;--r:#ff3d2e;--rs:#ffe7e4;--g:#16a34a;--gs:#e3f6e9;
--font:"Space Grotesk","SF Pro Display",-apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif;--mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--font)}
main{max-width:1040px;margin:0 auto;padding:36px 28px 60px}
h1,h2,h3{font-weight:700;letter-spacing:-.03em;text-transform:uppercase;line-height:1.05;margin:0}
h1{font-size:44px}h2{font-size:22px;margin:38px 0 14px;display:flex;align-items:center;gap:10px}h2 .n{font:700 13px var(--mono);background:var(--y);border-radius:99px;padding:2px 10px;letter-spacing:0}
h3{font-size:18px}h4{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:16px 0 6px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;text-transform:uppercase;margin-bottom:22px}.brand b{width:34px;height:34px;border-radius:10px;background:var(--y);display:grid;place-items:center}
.sub{color:var(--ink2);font-weight:600;margin-top:10px}.mono,time,code{font-family:var(--mono)}
.chip{display:inline-block;font:700 11px var(--font);letter-spacing:.04em;text-transform:uppercase;border-radius:99px;padding:3px 9px;background:var(--soft)}
.chip.y{background:var(--y)}.chip.r{background:var(--r);color:#fff}.chip.g{background:var(--gs);color:#11713a}.chip.k{background:var(--ink);color:#fff}
.banner{margin-top:22px;border-radius:20px;padding:16px 20px;font-weight:700;display:flex;gap:12px;align-items:center;background:var(--gs)}.banner.partial{background:var(--ys)}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}.tile{background:var(--card);border-radius:18px;padding:14px 16px}
.tile small{display:block;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}.tile strong{font-size:26px;letter-spacing:-.03em}
.card{background:var(--card);border-radius:22px;padding:20px 22px;margin-top:12px;break-inside:avoid}
.finding{display:grid;grid-template-columns:8px 1fr;padding:0;overflow:hidden}.finding>.sev{background:var(--y)}.finding>.sev.high{background:var(--r)}.finding>div{padding:18px 20px}
.meta{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:8px 0}
.images{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}.images figure:first-child{grid-column:1/-1}
figure{margin:0;background:#0d0d0c;border-radius:12px;overflow:hidden}figure img{display:block;width:100%;aspect-ratio:16/9;object-fit:contain}
figcaption{font:11px var(--mono);color:#ddd;padding:6px 9px}
.action{border-left:5px solid var(--y);background:var(--soft);border-radius:8px;padding:8px 12px;margin:6px 0}
.uncertain{background:var(--ys);border-radius:8px;padding:8px 12px;margin:6px 0;font-size:14px}
.decision{background:var(--soft);border-radius:10px;padding:8px 12px;margin:6px 0}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:16px;overflow:hidden}th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:top}
th{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.track{display:flex;gap:2px;height:22px;padding:3px;border-radius:8px;background:var(--soft);margin-top:10px}.track i{flex:1;border-radius:3px;background:#cfccc4}
.track i.completed{background:var(--ink)}.track i.running{background:var(--y)}.track i.failed,.track i.skipped,.track i.cancelled{background:var(--r)}.track i.hit{box-shadow:inset 0 -5px 0 var(--y)}
.tree{display:flex;flex-direction:column;align-items:center;gap:0;padding:18px;background:var(--soft);border-radius:18px}
.tree .row{display:flex;flex-wrap:wrap;justify-content:center;gap:10px}.tree .link{width:2px;height:18px;background:#bdb9ae}
.node{background:var(--card);border-radius:14px;padding:8px 12px;min-width:150px;border-left:6px solid var(--ink)}.node.running{border-color:var(--y)}.node.failed{border-color:var(--r)}
.node strong{display:block;font-size:13px}.node small{font:11px var(--mono);color:var(--ink2)}.node.root{background:var(--ink);color:#fff;border-color:var(--y)}.node.root small{color:#ddd}
.q{background:var(--ink);color:#fff;border-radius:16px 16px 4px 16px;padding:12px 16px;font-weight:600}
.claim{border-top:1px solid var(--line);padding:10px 0}.claim .chip{margin-bottom:6px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 var(--mono);background:var(--soft);border-radius:10px;padding:10px;margin:6px 0}
a{color:var(--ink);font-weight:700}small,.muted{color:var(--muted)}ul{margin:6px 0;padding-left:20px}
footer{margin-top:40px;font-size:12px;color:var(--muted)}
.verdict{margin-top:20px;border-radius:24px;padding:22px 24px;display:flex;align-items:center;gap:16px;background:var(--gs)}.verdict b{font-size:28px;letter-spacing:-.03em;text-transform:uppercase;line-height:1.05}
.verdict i{width:52px;height:52px;flex:none;border-radius:16px;display:grid;place-items:center;font-style:normal;font-size:26px;font-weight:800;background:var(--g);color:#fff}
.verdict small{display:block;margin-top:6px;font-size:13px;font-weight:600;color:var(--ink2);text-transform:none;letter-spacing:0}.verdict.risk{background:var(--rs)}.verdict.risk i{background:var(--r)}.verdict.watch,.verdict.partial{background:var(--ys)}.verdict.watch i,.verdict.partial i{background:var(--y);color:var(--ink)}
.brief{font-size:17px;line-height:1.55;margin:14px 0 0}
.attn{display:grid;grid-template-columns:200px 1fr;gap:0;padding:0;overflow:hidden}.attn img{width:100%;height:100%;min-height:130px;object-fit:cover;display:block;background:#0d0d0c}
.attn>div{padding:16px 18px;border-left:7px solid var(--y)}.attn.high>div{border-left-color:var(--r)}.attn h3{font-size:19px}.attn p{margin:6px 0}
.do{display:flex;gap:8px;align-items:flex-start;font-weight:600}.do:before{content:'→';color:var(--r);font-weight:800}
.actions li{margin:6px 0;font-weight:600}.conf li{margin:4px 0;color:var(--ink2)}
.tiles.three{grid-template-columns:repeat(3,1fr)}
.group{display:grid;grid-template-columns:300px 1fr;padding:0;overflow:hidden}.group>.gbody{padding:16px 20px;border-left:7px solid var(--y)}.group.high>.gbody{border-left-color:var(--r)}
.group .strip{display:grid;gap:2px;background:#0d0d0c}.group .strip.s2{grid-template-rows:1fr 1fr}.group .strip.s3{grid-template-columns:1fr 1fr}.group .strip.s3 img:first-child{grid-column:1/-1}
.group .strip img{width:100%;height:100%;min-height:90px;object-fit:cover;display:block}.group .noimg{min-height:150px}
.group h3{font-size:21px}.when{margin:4px 0 8px;font-size:13px;color:var(--ink2)}.line{font-size:16px;line-height:1.5;margin:0 0 8px}.line em{color:var(--muted);font-style:normal;font-weight:700}
.steps li{margin:6px 0;font-weight:600}.conf{margin:14px 2px 0;font-size:13px;color:var(--muted)}
details.appendix{margin-top:40px;background:var(--card);border-radius:22px;padding:6px 22px 22px}details.appendix>summary{cursor:pointer;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:16px 0;list-style:none}
details.appendix>summary:after{content:' +';color:var(--muted)}details.appendix[open]>summary:after{content:' −'}
@media(max-width:700px){.group{grid-template-columns:1fr}.tiles.three{grid-template-columns:1fr 1fr}}
@media print{details.appendix{display:block}details.appendix>*{display:block}}
@media(max-width:700px){.tiles{grid-template-columns:repeat(2,1fr)}.images{grid-template-columns:1fr 1fr}h1{font-size:32px}}
@media print{body{background:#fff}main{padding:0}.card,.tile{border:1px solid var(--line)}}
"""


def report_html(data):
    e=lambda value:escape(str(value),quote=True)
    run=data['run'];usage=data['usage'];summary=data.get('summary') or {}
    kind=run['kind'];kind_name='Live' if kind=='live' else 'Review'
    windows=data['windows'];cases=data['safety']['cases']
    hits=lambda w:any(c['case']['start_ms']<w['end_ms'] and c['case']['end_ms']>w['start_ms'] for c in cases)
    frames={f['id']:f for f in data['frames']}
    def figure(f,label=''):
        src=f.get('thumbnail') or f.get('embedded_image')
        if not src:return f'<p class="muted">{e(f.get("image_error","Image unavailable"))}</p>'
        return f'<figure><img src="{e(src)}" alt="Retained evidence at {e(clock(f["timestamp_ms"],True))}"><figcaption>{e(label or clock(f["timestamp_ms"],True))} · SHA-256 {e(str(f.get("content_hash",""))[:16])}…</figcaption></figure>'
    sections=[]
    for item in sorted(cases,key=lambda c:(c['priority']!='high',c['case']['start_ms'])):
        case=item['case'];assessment=item.get('assessment')
        checks=''.join(f'<li>{e(s["check"]["condition"])} <small>· {e(readable(s["check"]["visibility"]))} visibility</small></li>' for s in item['screenings'])
        body=''
        if assessment:
            for finding in assessment['findings']:
                body+=f'<span class="chip k">{e(readable(finding["classification"]))}</span> <span class="chip">{e(readable(finding["status"]))}</span><p>{e(finding["conclusion"])}</p>'
                if finding.get('recommendations'):body+='<h4>Recommended next steps</h4><ul>'+''.join(f'<li>{e(a)}</li>' for a in finding['recommendations'])+'</ul>'
                body+=''.join(f'<div class="action"><strong>{e(a["action"])}</strong><br><small>Verify: {e(a.get("verification",""))}</small></div>' for a in finding.get('corrective_actions',[]))
                body+=''.join(f'<div class="uncertain">{e(u)}</div>' for u in finding.get('uncertainty',[]))
                if finding.get('source_refs'):body+='<p><small>Sources: '+', '.join(f'<a href="#source-{e(ref)}">{e(ref)}</a>' for ref in finding['source_refs'])+'</small></p>'
            if not assessment['findings']:body+='<p>The team returned no supported findings.</p>'
            body+='<details><summary><small>Independent challenge</small></summary><pre>'+e(json.dumps(assessment['challenge'],indent=2))+'</pre></details>'
        else:body='<p>Specialist assessment pending. Visible concern remains provisional.</p>'
        if assessment and item.get('unassessed_screening_ids'):
            body+=f'<div class="uncertain">{len(item["unassessed_screening_ids"])} retained occurrences were outside this bounded assessment packet and still need review.</div>'
        decisions=[d for d in data['decisions'] if d['target_id']==case['id']]
        disposition=''.join(f'<div class="decision"><strong>{e(DECISIONS.get(d["decision"],readable(d["decision"])))}</strong> — {e(d["reason"])}<br><small>{e(d["reviewer_id"])} · {e(local_time(d["created_at"]))}</small></div>' for d in decisions) or '<p class="muted">No human disposition recorded.</p>'
        ids=sorted({ref for s in item['screenings'] for ref in s['check']['evidence_refs']},key=lambda r:frames.get(r,{}).get('timestamp_ms',0))
        images=''.join(figure(frames[r]) for r in ids[:3] if r in frames)
        high=item['priority']=='high'
        sections.append(f'<article class="card finding"><span class="sev{" high" if high else ""}"></span><div><h3>{e(category(case["category"]))}</h3>'
            f'<div class="meta"><time>{e(clock(case["start_ms"],True))}–{e(clock(case["end_ms"],True))}</time>{"<span class=\"chip r\">Priority</span>" if high else "<span class=\"chip y\">Routine</span>"}<span class="chip{" g" if assessment else ""}">{e(case_state(item))}</span><small class="mono">{e(case["id"])}</small></div>'
            f'<ul>{checks}</ul><div class="images">{images}</div>{body}<h4>Human decisions</h4>{disposition}</div></article>')
    track=''.join(f'<i class="{e(w["status"])}{" hit" if hits(w) else ""}" title="{e(clock(w["start_ms"],True))}–{e(clock(w["end_ms"],True))} · {e(w["status"])}"></i>' for w in windows)
    rows=''.join(f'<tr><td><time>{e(clock(w["start_ms"],True))}–{e(clock(w["end_ms"],True))}</time></td><td>{e(readable(w["status"]).capitalize())}</td><td>{len(w.get("frame_ids") or [])}</td><td>{e("; ".join(w["gaps"]) or w.get("error") or "")}</td></tr>' for w in windows)
    unknown=[s['check'] for s in data['safety']['screenings'] if s['check']['result']=='unknown']
    unknown_html=''.join(f'<tr><td>{e(category(c["category"]))}<br><time>{e(clock(c["start_ms"],True))}–{e(clock(c["end_ms"],True))}</time></td><td>{e(c["condition"])}<br><small>{e("; ".join(c["limitations"]))}</small></td><td class="mono">{e(", ".join(c["evidence_refs"]))}</td></tr>' for c in unknown)
    case_refs={ref for item in cases for s in item['screenings'] for ref in s['check']['evidence_refs']}
    extra_images=''.join(figure(f,f'{clock(f["timestamp_ms"],True)} · {f["id"]}') for f in [x for x in data['frames'] if x['id'] not in case_refs and x.get('embedded_image')][:8])
    person_rows=''.join(f'<tr><td><time>{e(clock(p["timestamp_ms"],True))}</time></td><td>{e(readable(p["status"]).capitalize())}</td><td>{len(p["people"]) if p["status"]=="available" else "Unknown"}</td><td>{e(p["detail"] or (str(p["omitted_count"])+" additional detections omitted" if p["omitted_count"] else ""))}</td></tr>' for p in data.get('people',[]))
    # Agent team tree
    team=data.get('team') or {'nodes':[],'edges':[]};by_parent={}
    for parent,child in team['edges']:by_parent.setdefault(parent,[]).append(child)
    nodes={n['id']:n for n in team['nodes']}
    def node_html(n):
        cost=f' · ${n["cost_usd"]:.4f}' if n['cost_usd'] else ''
        state='Working' if n['status']=='running' else 'Failed' if n['status']=='failed' else 'Done'
        return f'<div class="node {e(n["status"])}"><strong>{e(n["label"])}{" ×"+str(n["runs"]) if n["runs"]>1 else ""}</strong><small>{state} · {n["requests"]} req · {n["tools"]} tools{cost}</small></div>'
    levels=[];frontier=['coordinator'];seen=set()
    while frontier:
        nxt=[c for p in frontier for c in by_parent.get(p,[]) if c not in seen];seen.update(nxt)
        if nxt:levels.append(nxt)
        frontier=nxt
    tree=f'<div class="tree"><div class="row"><div class="node root"><strong>{kind_name} coordinator</strong><small>{e(summary.get("agents",0))} agent runs · {e(team.get("events",0))} events</small></div></div>'
    tree+=''.join('<div class="link"></div><div class="row">'+''.join(node_html(nodes[i]) for i in level)+'</div>' for level in levels)+'</div>'
    team_html=tree if team['nodes'] else '<p class="muted">No agent activity was recorded for this run.</p>'
    # Ask conversation
    ask=data.get('ask') or {'turns':[]};ask_html=''
    for turn in ask['turns']:
        claims=''.join(f'<div class="claim"><span class="chip">{e(readable(c["kind"]))}</span><p>{e(c["text"])}</p></div>' for c in turn['claims']) or (f'<p>{e(turn["answer"])}</p>' if turn.get('answer') else f'<p class="muted">No answer · {e(readable(turn["status"]))}</p>')
        imgs=''.join(figure(f) for f in turn['frames'][:6])
        u=turn['usage']
        ask_html+=(f'<article class="card"><div class="q">{e(turn["question"])}</div><div class="meta"><span class="chip{" y" if turn["mode"]=="fixture" else ""}">{"Demo" if turn["mode"]=="fixture" else "AI"}</span><span class="chip">{e(readable(turn.get("outcome") or turn["status"]))}</span><small>{e(local_time(turn.get("created_at")))}</small>'
            f'<small class="mono">${float(u.get("calculated_usd",0)):.4f} of ${float(turn.get("limit_usd",.05)):.2f} max</small></div>{claims}'
            +(f'<div class="images">{imgs}</div>' if imgs else '')
            +(''.join(f'<div class="uncertain">{e(m)}</div>' for m in turn.get('missing_context',[])))+'</article>')
    source_html=''
    for source in data['sources']:
        url=source.get('url','');link=f' · <a href="{e(url)}">Original source</a>' if urlparse(url).scheme in ('http','https') else ''
        source_html+=f'<article class="card" id="source-{e(source["id"])}"><h3>{e(source.get("title",source["id"]))}</h3><p class="muted">{e(source.get("clause",""))} · {e(source.get("version",""))}{link}</p><pre>{e(source.get("text",""))}</pre></article>'
    complete=data['coverage_complete']
    live_html=''
    if kind=='live':
        live_html=(f'<div class="card"><h3>Live session</h3><p>State: <strong>{e(readable(run.get("state") or "unknown").capitalize())}</strong> · watched to <time>{e(clock(run.get("cutoff_ms") or 0))}</time> of <time>{e(clock(run["end_ms"]))}</time>.'
            f' {summary.get("skipped_windows",0)} interval(s) were skipped, stopped or failed; Live keeps a bounded backlog, so analysis can trail playback and unchecked intervals stay listed below.</p></div>')
    ex=data.get('executive') or executive(data)
    icon={'risk':'!','watch':'!','partial':'…','clear':'✓'}[ex['level']]
    findings_n=summary.get('findings',len(cases));decided_n=summary.get('decided',0)
    tiles=(f'<div class="tiles three"><div class="tile"><small>Findings</small><strong>{findings_n}</strong>'
           +(f' <span class="chip r">{summary.get("high_priority",0)} priority</span>' if summary.get('high_priority') else '')+'</div>'
           f'<div class="tile"><small>Footage checked</small><strong>{data["completed_windows"]}/{data["total_windows"]}</strong> <span class="muted">intervals</span></div>'
           f'<div class="tile"><small>Decisions made</small><strong>{decided_n}/{findings_n}</strong></div></div>')
    title=f'{kind_name} report — {data.get("source_name") or run["source_id"]}'
    exec_html=(f'<div class="verdict {e(ex["level"])}"><i>{icon}</i><div><b>{e(ex["headline"])}</b><small>{e(ex.get("context",""))}</small></div></div>'
        +(f'<p class="brief">{e(ex["summary"])}</p>' if ex.get('summary') else ''))
    def thumb(frame_id):
        f=frames.get(frame_id)
        src=f and (f.get('thumbnail') or f.get('embedded_image'))
        return f'<img src="{e(src)}" alt="Evidence at {e(clock(f["timestamp_ms"],True))}">' if src else ''
    cards=''
    for g in ex['groups']:
        strip=''.join(thumb(fid) for fid in g['frames']) or '<span class="noimg"></span>'
        chips=(f'<span class="chip r">{g["high"]} priority</span> ' if g['high'] else '')+f'<span class="chip">{g["reviewed"]} team reviewed</span>'
        if g['pending']:chips+=f' <span class="chip y">{g["pending"]} pending</span>'
        for label,n in (('accepted',g['accepted']),('dismissed',g['dismissed']),('need evidence',g['needs_evidence'])):
            if n:chips+=f' <span class="chip k">{n} {label}</span>'
        if g['undecided']:chips+=f' <span class="chip">{g["undecided"]} to decide</span>'
        moments=f'{g["count"]} moment{"s" if g["count"]!=1 else ""} · {g["start"]}–{g["end"]}' if g['count']>1 else f'at {g["start"]}'
        cards+=(f'<article class="card group{" high" if g["high"] else ""}"><div class="strip s{len(g["frames"]) or 1}">{strip}</div><div class="gbody">'
            f'<h3>{e(g["title"])}</h3><p class="when mono">{e(moments)}{(" · priority at "+", ".join(g["priority_times"])) if g["priority_times"] else ""}</p>'
            f'<p class="line">{e(g["summary"])}{" <em>(uncertain)</em>" if g["uncertain"] else ""}</p>'
            +(f'<p class="do">{e(g["action"])}</p>' if g['action'] else '')+f'<div class="meta">{chips}</div></div></article>')
    attention_html=('<h2>What needs attention</h2>'+(cards or '<div class="card"><p>No concerns were recorded in the checked footage. Unchecked or unclear footage is not evidence of safety.</p></div>')
        +(('<h2>Next steps</h2><ol class="card steps">'+''.join(f'<li>{e(a)}</li>' for a in ex['actions'])+'</ol>') if ex['actions'] else '')
        +f'<p class="conf">{e(ex["confidence"])}</p>')
    demo='<div class="banner partial">DEMO · Fixed fixture responses. These findings do not assess this video.</div>' if run['mode']=='fixture' else ''
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'"><title>Industrial Safety — {e(title)}</title>
<style>{CSS}</style><main>
<div class="brand"><b>⚠</b>Industrial Safety</div>
<h1>{e(kind_name)} report</h1><p class="sub">Safety summary · {e(data.get("source_name") or run["source_id"])} · {e(local_time(run["created_at"]))} · {"Demo (fixture)" if run["mode"]=="fixture" else "AI analysis"}</p>
{demo}{exec_html}
{tiles}
<div class="track" aria-label="Coverage">{track}</div>
{attention_html}
<details class="appendix"><summary>Full details for auditors</summary>
<div class="banner{"" if complete else " partial"}">{"All planned intervals inspected" if complete else "PARTIAL COVERAGE"} · {data["completed_windows"]}/{data["total_windows"]} intervals · {data["pending_specialists"]} pending specialist assessments</div>
{live_html}
<h2>Findings <span class="n">{len(cases)}</span></h2>{''.join(sections) or '<div class="card"><p>No suspected concerns recorded. Uninspected or unassessable footage is not evidence of safety.</p></div>'}
<h2>Agent team</h2>{team_html}
<h2>Ask <span class="n">{len(ask["turns"])}</span></h2>{ask_html or '<p class="muted">No questions were asked about this run.</p>'}
<h2>Coverage</h2><table><thead><tr><th>Source interval</th><th>Outcome</th><th>Frames</th><th>Gap or error</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Unassessable checks <span class="n">{len(unknown)}</span></h2><p class="muted">{len(unknown)} checks remain unknown; they are not clear or safe findings.</p><table><thead><tr><th>Category and time</th><th>Condition and limitations</th><th>Retained evidence IDs</th></tr></thead><tbody>{unknown_html}</tbody></table><div class="images">{extra_images}</div>
<h2>Person detection</h2><p class="muted">Pretrained COCO person detection on retained sampled frames only. No custom classes, identity recognition or continuous tracks. Missing detections do not prove absence. Bounding boxes, confidence and model provenance are retained in the JSON report.</p><table><thead><tr><th>Source time</th><th>Status</th><th>Detections</th><th>Limits</th></tr></thead><tbody>{person_rows or '<tr><td colspan="4">No saved person detections for this historical run.</td></tr>'}</tbody></table>
<h2>Usage and budget</h2><div class="card"><p>Calculated usage: <span class="mono">${usage['usage_cost_usd']:.6f}</span>; outstanding reservations: <span class="mono">${usage['reserved_usd']:.6f}</span>; original limit: <span class="mono">${usage['limit_usd']:.2f}</span>. {e(usage['basis'])}</p><p>Input tokens: {usage['input_tokens']}; cached input: {usage['cached_input_tokens']}; output: {usage['output_tokens']}. Unresolved charges: {usage['unknown_cost_requests']}. Ask usage: <span class="mono">${float(summary.get("ask_cost_usd",0)):.6f}</span> across {summary.get("ask_questions",0)} question(s).</p></div>
<h2>Run details</h2><div class="card"><p class="mono">Run {e(run['id'])}<br>Source {e(run['source_id'])}<br>SHA-256 {e(run['source_hash'])}</p><ul>{''.join(f'<li>{e(limit)}</li>' for limit in run['limitations'])}</ul></div>
<h2>Cited source excerpts</h2>{source_html or '<p class="muted">No source-grounded assessment is available yet.</p>'}
</details>
<footer>Assisted review on sampled frames. Not a safety certification or legal determination.</footer>
</main></html>'''
