"""Pixel-only language-agent review. Never writes human labels or runs training."""
import asyncio
import copy
import hashlib
import json
import math
import os
import time
from uuid import uuid4
import cv2
from pydantic_ai import BinaryContent
from pydantic_ai.usage import UsageLimits
from safety_app.agents.shared.deps import AgentDeps,Scope
from safety_app.agents.annotation_assistant.agent import create_image_reviewer
from safety_app.agents.annotation_assistant.models import ImageReviewResult
from safety_app.agents.annotation_assistant.image_tools import ImageDraftTools,TOOL_NAMES
from safety_app.infrastructure.budgets import BudgetLedger,BudgetBlocked
from safety_app.domain.models import ToolFailure
from safety_app.modules.safety_review.rules import Rules
from app.agents.model_policy import HOSTED_MODEL,validate_agent_models

def status(rt):
    validate_agent_models(rt.config)
    blockers=[]
    if os.getenv('SAFETY_PAID_REVIEW')!='1':blockers.append('Hosted AI requires SAFETY_PAID_REVIEW=1 in the backend environment.')
    if not os.getenv('OPENAI_API_KEY'):blockers.append('The backend API key is missing.')
    bound=rt.config.get('learning_image_review',{}).get('image_usd')
    if not isinstance(bound,(int,float)) or not math.isfinite(bound) or not 0<bound<=.05:
        bound=None;blockers.append('Enable an image cost allowance below before hosted image review.')
    rates=rt.config['pricing'].get(HOSTED_MODEL,{})
    if any(not isinstance(rates.get(k),(int,float)) or not math.isfinite(rates[k]) or rates[k]<0 for k in ('input_per_million','output_per_million')):
        blockers.append('Configure valid token pricing in configs/agents.yaml.')
    if rt.budgets.state('stopped')=='1':blockers.append('AI is stopped in Costs & Coverage.')
    return dict(ready=not blockers,model=HOSTED_MODEL,blockers=blockers,image_usd=bound,always_on=os.getenv('AI_ALWAYS_ON','false').lower()=='true')

def save_settings(rt,image_usd):
    # This is an operator-supplied conservative allowance, not a verified image price.
    import tempfile
    path=rt.root/'configs/agents.yaml'
    config=json.loads(path.read_text())
    config['learning_image_review']={'image_usd':image_usd,'pricing_basis':'operator_image_allowance'}
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.agents-')
    with os.fdopen(fd,'w') as stream:json.dump(config,stream,indent=2)
    os.replace(name,path);rt.config.update(config)
    rt.audit.write('learning_image_review_enabled',arguments={'image_usd':image_usd},configuration={'model':HOSTED_MODEL})
    return status(rt)

def candidates(frame):
    boxes=copy.deepcopy(frame['boxes']);known={b['id'] for b in boxes}
    for b in frame.get('proposals',[]):
        if b['id'] not in known and frame.get('proposal_decisions',{}).get(b['id'])!='rejected':
            boxes.append({k:b[k] for k in ('id','label','bbox')})
    if len(boxes)>200:raise ValueError('Review supports at most 200 candidate boxes per frame')
    return boxes

def prepare(ws,pid,fid,revision):
    with ws.lock:
        p=ws.read(pid);ws.editable(p)
        f=next((f for f in p['frames'] if f['id']==fid),None)
        if f is None:raise ValueError('Unknown frame')
        if f.get('revision',0)!=revision:raise ValueError('Frame changed; refresh before AI review')
        classes=list(p['classes']);boxes=candidates(f)
        path=(ws.folder(pid)/'images'/f['file_name']).resolve()
        if not path.is_relative_to((ws.folder(pid)/'images').resolve()):raise ValueError('Invalid frame image')
    raw=path.read_bytes()
    import numpy as np
    image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
    if image is None:raise ValueError('Cannot decode review image')
    h,w=image.shape[:2];scale=min(1,1024/max(h,w))
    image=cv2.resize(image,(max(1,round(w*scale)),max(1,round(h*scale))))
    ok,encoded=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,90])
    if not ok:raise ValueError('Cannot encode review image')
    # Re-encoding strips container/file metadata. Only classes, candidate geometry and pixels leave the host.
    packet={'classes':classes,'candidates':boxes}
    text=json.dumps(packet,sort_keys=True)
    return packet,text,encoded.tobytes(),hashlib.sha256(raw).hexdigest(),hashlib.sha256(b'image-draft-tools-v1'+text.encode()+encoded.tobytes()).hexdigest()

def validate_result(value,packet):
    expected={b['id'] for b in packet['candidates']}
    actual=[d.target_id for d in value.decisions]
    if len(actual)!=len(set(actual)) or set(actual)!=expected:raise ValueError('Decide exactly once for every supplied candidate ID')
    for box in [*value.additions,*[d.replacement for d in value.decisions if d.replacement]]:
        if box.label not in packet['classes']:raise ValueError('Use only exact requested object classes')
    visible=sum(d.decision!='reject' for d in value.decisions)+len(value.additions)
    if visible>200:raise ValueError('Too many resulting boxes; maximum 200')

active_runs=set()

def update_attempt(ws,pid,attempt,**updates):
    attempt.update(updates,updated_at=time.time())
    with ws.lock:
        p=ws.read(pid)
        rows=p.setdefault('ai_review_attempts',[])
        existing=next((a for a in rows if a['id']==attempt['id']),None)
        if existing is None:rows.append(dict(attempt))
        else:existing.update(attempt)
        ws.write(p)

def stage(ws,rt,pid,attempt,name,**fields):
    rt.audit.write('image_review_stage',run_id=attempt['id'],agent_name='annotation_assistant',
        resources={'stage':name},**fields)
    update_attempt(ws,pid,attempt,stage=name)

def attempts_for(ws,pid,fid):
    p=ws.read(pid)
    if not any(f['id']==fid for f in p['frames']):raise ValueError('Unknown frame')
    rows=[dict(a) for a in p.get('ai_review_attempts',[]) if a['frame_id']==fid]
    known={a['id'] for a in rows}
    for r in p.get('ai_review_history',[]):
        if r['frame_id']==fid and r['id'] not in known:
            rows.append(dict(id=r['id'],frame_id=fid,created_at=r['created_at'],updated_at=r['created_at'],revision=r['revision'],
                status='completed',stage='recommendations_saved',model=r['model'],result_run_id=r['id']))
    return sorted(rows,key=lambda a:a['created_at'])

def activity(ws,rt,pid,fid,run_id=None):
    with ws.lock:
        rows=attempts_for(ws,pid,fid)
        for a in rows:
            if a['status']=='running' and a['id'] not in active_runs:
                rt.audit.write('run_interrupted',run_id=a['id'],agent_name='annotation_assistant',status='interrupted',resources={'stage':a['stage']})
                update_attempt(ws,pid,a,status='interrupted',error='Server restarted or review worker stopped. No completed result is assumed.')
        selected=run_id or (rows[-1]['id'] if rows else None)
        if run_id and not any(a['id']==run_id for a in rows):raise ValueError('Review run is outside this frame')
    audit=rt.audit.rows(1000,run_id=selected) if selected else []
    return dict(attempts=rows[-20:],selected_run_id=selected,audit=sorted(audit,key=lambda r:r['sequence']),partial=len(audit)==1000,
        audit_degraded=rt.audit.degraded,snapshot_error=rt.audit.export_error)

def audit_download(ws,rt,pid,fid,run_id):
    if not any(a['id']==run_id for a in attempts_for(ws,pid,fid)):raise ValueError('Review run is outside this frame')
    def lines():
        with rt.audit.db.connect() as db:
            db.execute('BEGIN')
            cursor=db.execute("SELECT sequence,body FROM audit WHERE json_extract(body,'$.run_id')=? ORDER BY sequence",(run_id,))
            while rows:=cursor.fetchmany(200):
                for row in rows:yield json.dumps(json.loads(row['body'])|{'sequence':row['sequence']})+'\n'
    return lines()

async def review(ws,rt,pid,fid,revision,model=None):
    run_id=uuid4().hex;client=None;attempt=None
    active_runs.add(run_id)
    try:
        rt.audit.write('run_started',run_id=run_id,agent_name='annotation_assistant',configuration={'workflow_kind':'learning_image_review','registered_tools':TOOL_NAMES},
            provenance={'project_id':pid,'frame_id':fid},arguments={'expected_revision':revision})
        with ws.lock:
            p=ws.read(pid);ws.editable(p)
            if not any(f['id']==fid for f in p['frames']):raise ValueError('Unknown frame')
            attempt=dict(id=run_id,frame_id=fid,created_at=time.time(),updated_at=time.time(),revision=revision,status='running',stage='preparing_image',
                model='local_fixture' if model and model.__class__.__module__ in ('pydantic_ai.models.function','pydantic_ai.models.test') else HOSTED_MODEL)
            update_attempt(ws,pid,attempt)
        packet,text,image,digest,signature=await asyncio.to_thread(prepare,ws,pid,fid,revision)
        stage(ws,rt,pid,attempt,'image_prepared',arguments=packet,provenance={'image_sha256':digest,'input_sha256':signature})
        with ws.lock:
            p=ws.read(pid);f=next(f for f in p['frames'] if f['id']==fid)
            cached=f.get('ai_review')
            if cached and cached['input_sha256']==signature and cached['revision']==revision:
                rt.audit.write('cache_hit',run_id=run_id,agent_name='annotation_assistant',provenance={'result_run_id':cached['id']})
                update_attempt(ws,pid,attempt,status='cached',stage='saved_result_reused',result_run_id=cached['id'])
                return ws.read(pid)
        if model is None:
            stage(ws,rt,pid,attempt,'checking_access_and_budget')
            state=status(rt)
            if not state['ready']:raise BudgetBlocked(' '.join(state['blockers']))
            from openai import AsyncOpenAI
            from pydantic_ai.models.openai import OpenAIResponsesModel
            from pydantic_ai.providers.openai import OpenAIProvider
            client=AsyncOpenAI(api_key=os.environ['OPENAI_API_KEY'],max_retries=0)
            model=OpenAIResponsesModel(HOSTED_MODEL,provider=OpenAIProvider(openai_client=client))
        config=copy.deepcopy(rt.config)
        bound=config.get('learning_image_review',{}).get('image_usd')
        if bound is not None:config['pricing'][HOSTED_MODEL]=dict(config['pricing'][HOSTED_MODEL],image_usd=bound,version='operator-learning-image-allowance')
        ledger=BudgetLedger(rt.db,rt.audit,config)
        draft=ImageDraftTools(packet)
        def validate_output(value):
            validate_result(value,packet);draft.validate(value)
        deps=AgentDeps(rt.media,rt.sources,Rules(rt.db),rt.audit,ledger,
            Scope('local',frozenset(),{},frozenset(),frozenset({fid})),run_id,run_id,run_id,
            rt.camera,rt.classes,lambda *a:None,workflow_kind='learning_image_review',
            output_check=validate_output)
        stage(ws,rt,pid,attempt,'waiting_for_model')
        async with asyncio.timeout(config['limits']['agent_seconds']):
            result=await create_image_reviewer(model,deps,draft.tools()).run([text,BinaryContent(data=image,media_type='image/jpeg')],deps=deps,
                model_settings={'openai_reasoning_effort':'low'},usage_limits=UsageLimits(request_limit=min(6,config['limits']['requests']),tool_calls_limit=min(12,config['limits']['tools']),
                    input_tokens_limit=config['limits']['input_tokens'],output_tokens_limit=config['limits']['output_tokens']))
        stage(ws,rt,pid,attempt,'validating_boxes')
        value=ImageReviewResult.model_validate(result.output);validate_result(value,packet)
        added=[dict(id='ai-'+uuid4().hex,**b.model_dump(mode='json')) for b in value.additions]
        record=dict(id=run_id,created_at=time.time(),revision=revision,model=attempt['model'],image_sha256=digest,input_sha256=signature,
                    result=value.model_dump(mode='json'),added_boxes=added)
        with ws.lock:
            p=ws.read(pid);ws.editable(p);f=next((f for f in p['frames'] if f['id']==fid),None)
            if f is None or f.get('revision',0)!=revision or p['classes']!=packet['classes']:
                raise ValueError('Labels or classes changed during AI review; refresh before rerunning')
            path=ws.folder(pid)/'images'/f['file_name']
            if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Image changed during AI review')
            rt.audit.write('run_finished',run_id=run_id,agent_name='annotation_assistant',output=record)
            p.setdefault('ai_review_history',[]).append(dict(frame_id=fid,**record))
            f['ai_review']=record;ws.write(p)
            update_attempt(ws,pid,attempt,status='completed',stage='recommendations_saved',result_run_id=run_id)
        return ws.read(pid)
    except BaseException as exc:
        outcome='cancelled' if isinstance(exc,asyncio.CancelledError) else 'blocked' if isinstance(exc,BudgetBlocked) else 'failed'
        message='Review cancelled.' if outcome=='cancelled' else str(exc)[:1000]
        from safety_app.infrastructure.audit import redact
        rt.audit.write('run_failed',run_id=run_id,agent_name='annotation_assistant',status=outcome,
            error=ToolFailure(code=type(exc).__name__,message=message),resources={'stage':attempt['stage'] if attempt else 'preparing_image'})
        if attempt:update_attempt(ws,pid,attempt,status=outcome,error=redact(message))
        raise
    finally:
        active_runs.discard(run_id)
        if client:await client.close()
