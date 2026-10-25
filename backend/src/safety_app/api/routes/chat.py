from fastapi import APIRouter,Depends,Query
from fastapi.responses import StreamingResponse
import json
from safety_app.api.auth import operator,Operator
from safety_app.infrastructure.runtime import runtime
from safety_app.modules.safety_review.chat import SafetyChat
from safety_app.agents.safety_reviewer.chat_models import ChatSession,ChatSessionInput,ChatMessageInput,ChatState
from safety_app.domain.models import Job,AuditEvent
router=APIRouter(prefix='/api/chat')

@router.get('/sessions',response_model=list[ChatSession])
def sessions(user:Operator=Depends(operator)):return SafetyChat(runtime()).sessions(user.site_id)
@router.post('/sessions',response_model=ChatSession)
def create(body:ChatSessionInput,user:Operator=Depends(operator)):return SafetyChat(runtime()).create(body.title,user.site_id)
@router.get('/sessions/{id}',response_model=ChatState)
def state(id:str,user:Operator=Depends(operator)):return SafetyChat(runtime()).state(id,user.site_id)
@router.post('/sessions/{id}/messages',response_model=Job)
def send(id:str,body:ChatMessageInput,user:Operator=Depends(operator)):return SafetyChat(runtime()).send(id,body,user.site_id)
@router.get('/sessions/{id}/turns/{job_id}/audit',response_model=list[AuditEvent])
def trace(id:str,job_id:str,limit:int=Query(200,ge=1,le=1000),offset:int=Query(0,ge=0),user:Operator=Depends(operator)):
    rt=runtime();SafetyChat(rt).job(id,job_id,user.site_id)
    return rt.audit.rows(limit,offset,job_id=job_id)
@router.get('/sessions/{id}/turns/{job_id}/audit/download')
def download(id:str,job_id:str,user:Operator=Depends(operator)):
    rt=runtime();job=SafetyChat(rt).job(id,job_id,user.site_id)
    if job.status in ('queued','running'):raise ValueError('Wait for this turn to finish before downloading its full trace')
    with rt.audit.db.connect() as db:
        last=db.execute("SELECT COALESCE(MAX(sequence),0) FROM audit WHERE json_extract(body,'$.job_id')=?",(job_id,)).fetchone()[0]
    def lines():
        cursor=0
        while True:
            with rt.audit.db.connect() as db:
                rows=db.execute("SELECT sequence,body FROM audit WHERE sequence>? AND sequence<=? AND json_extract(body,'$.job_id')=? ORDER BY sequence LIMIT 200",(cursor,last,job_id)).fetchall()
            if not rows:break
            for row in rows:
                yield json.dumps(json.loads(row['body'])|{'sequence':row['sequence']})+'\n'
            cursor=rows[-1]['sequence']
    return StreamingResponse(lines(),media_type='application/x-ndjson',headers={'Content-Disposition':f'attachment; filename="safety-chat-{job.id}.ndjson"'})
@router.post('/sessions/{id}/turns/{job_id}/cancel',response_model=Job)
def cancel(id:str,job_id:str,user:Operator=Depends(operator)):
    rt=runtime();job=SafetyChat(rt).job(id,job_id,user.site_id)
    if job.status not in ('queued','running'):raise ValueError('This turn is already finished')
    return rt.jobs.cancel(job.id)
