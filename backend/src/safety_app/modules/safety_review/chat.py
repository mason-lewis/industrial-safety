"""Durable conversations; session/site authorization is checked before any turn."""
import json
from safety_app.agents.safety_reviewer.chat_models import ChatSession,ChatState,ChatTurn,ChatResult
from safety_app.domain.models import Job

class SafetyChat:
    def __init__(self,rt):self.rt=rt
    def session(self,id,site_id):
        value=ChatSession.model_validate(self.rt.db.get('chat_session',id))
        if value.site_id!=site_id:raise ValueError('Conversation outside site')
        return value
    def create(self,title,site_id):
        value=ChatSession(title=title,site_id=site_id)
        self.rt.audit.write('chat_session_created',provenance={'session_id':value.id,'site_id':site_id})
        self.rt.db.save('chat_session',value,immutable=True);return value
    def for_review(self,run_id,site_id,create=False):
        id='review-chat:'+run_id
        try:return self.session(id,site_id)
        except KeyError:
            if not create:return None
        value=ChatSession(id=id,title='Video questions',site_id=site_id,review_run_id=run_id)
        with self.rt.db.connect() as db:
            db.execute('INSERT OR IGNORE INTO objects VALUES(?,?,?)',('chat_session',id,value.model_dump_json()))
        self.rt.audit.write('chat_session_created',provenance={'session_id':id,'site_id':site_id,'review_run_id':run_id})
        return self.session(id,site_id)
    def sessions(self,site_id):
        with self.rt.db.connect() as db:
            rows=db.execute("SELECT body FROM objects WHERE kind='chat_session' AND json_extract(body,'$.site_id')=? ORDER BY rowid DESC LIMIT 100",(site_id,)).fetchall()
        return [ChatSession.model_validate_json(r['body']) for r in rows]
    def state(self,id,site_id):
        session=self.session(id,site_id)
        with self.rt.db.connect() as db:
            rows=db.execute("SELECT body FROM jobs WHERE kind='safety_chat' AND json_extract(body,'$.payload.session_id')=? AND json_extract(body,'$.site_id')=? ORDER BY rowid DESC LIMIT 100",(id,site_id)).fetchall()
        turns=[]
        for row in reversed(rows):
            job=Job.model_validate_json(row['body']);result=None
            if job.status=='completed':result=ChatResult.model_validate(self.rt.db.get('chat_result',job.run_id))
            turns.append(ChatTurn(job=job,result=result))
        return ChatState(session=session,turns=turns)
    def send(self,id,body,site_id):
        state=self.state(id,site_id)
        if not body.message.strip():raise ValueError('Enter a safety question')
        # A fixed small context, saved with the job, keeps retries reproducible.
        history=[]
        for turn in state.turns[-4:]:
            if turn.result:
                history.append({'question':turn.job.payload['message'][:450],'answer':turn.result.response.answer[:450],'mode':turn.result.mode})
        payload={'session_id':id,'message':body.message.strip(),'mode':body.mode,'event_date':body.event_date.isoformat() if body.event_date else None,'media_ids':[],'history':history}
        key='safety-chat:'+id+':'+body.idempotency_key
        # Check an existing key before rebuilding history after completion.
        with self.rt.db.connect() as db:row=db.execute('SELECT body FROM jobs WHERE key=?',(key,)).fetchone()
        if row:
            old=Job.model_validate_json(row['body'])
            if old.site_id!=site_id or any(old.payload.get(k)!=payload[k] for k in ('session_id','message','mode','event_date')):raise ValueError('Idempotency key has a different request')
            return old
        return self.rt.jobs.start('safety_chat',key,payload,site_id,exclusive_session=id)
    def job(self,session_id,job_id,site_id):
        self.session(session_id,site_id);job=self.rt.jobs.get(job_id)
        if job.kind!='safety_chat' or job.site_id!=site_id or job.payload.get('session_id')!=session_id:raise ValueError('Chat turn outside conversation')
        return job
