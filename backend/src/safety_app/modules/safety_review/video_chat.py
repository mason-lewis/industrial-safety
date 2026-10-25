"""Durable conversations bound to one review, with immutable inputs per submission."""
import copy
import json
from pathlib import Path
from safety_app.domain.models import Job
from safety_app.domain.video_chat import VideoChatContext, VideoChatState, VideoChatTurn
from safety_app.domain.chat_lab import LabAnswer, LabCitation
from safety_app.infrastructure.exact_cache import fingerprint
from safety_app.modules.safety_review.chat import SafetyChat
from safety_app.modules.safety_review.chat_activity import ChatActivity
from safety_app.modules.safety_review.review_runs import ReviewRuns
from safety_app.modules.safety_review.lab_knowledge import freeze
from safety_app.modules.safety_review.lab_service import hosted_available


class VideoChat:
    def __init__(self,rt):self.rt=rt

    def review(self,id,site_id):
        run=ReviewRuns(self.rt).get(id,site_id)
        if run.kind!='review':raise ValueError('Ask is available for recorded Review and its History')
        return run

    def state(self,id,site_id):
        self.review(id,site_id)
        session=SafetyChat(self.rt).for_review(id,site_id)
        with self.rt.db.connect() as db:
            rows=db.execute("SELECT id FROM jobs WHERE kind='video_chat' AND json_extract(body,'$.site_id')=? AND json_extract(body,'$.payload.review_run_id')=? ORDER BY rowid DESC LIMIT 100",(site_id,id)).fetchall()
        return VideoChatState(review_run_id=id,session_id=session.id if session else None,
            turns=[self.get(id,row[0],site_id) for row in reversed(rows)],hosted_available=hosted_available(self.rt))

    def get(self,run_id,id,site_id):
        self.review(run_id,site_id)
        job=self.rt.jobs.get(id)
        if job.kind!='video_chat' or job.site_id!=site_id or job.payload.get('review_run_id')!=run_id:raise KeyError('Question not found in this review')
        try:value=VideoChatTurn.model_validate(self.rt.db.get('video_chat_turn',id))
        except KeyError:
            value=VideoChatTurn(id=id,session_id=job.payload['session_id'],review_run_id=run_id,job=job,
                message=job.payload['question'],mode=job.payload['mode'],context=VideoChatContext.model_validate(job.payload['video_context']),versions=job.payload['versions'])
        value.job=job.model_copy(update={'payload':{'review_run_id':run_id,'session_id':value.session_id,'mode':value.mode},
            'error':job.error.model_copy(update={'message':'Question did not finish; see its outcome and activity issue code.'}) if job.error else None})
        value.usage=ChatActivity(self.rt).usage(job,self.events(run_id,id,site_id))
        if job.status not in ('queued','running') and value.answer is None:
            value.answer=LabAnswer(answer='This question was stopped. Saved evidence remains available.' if job.status=='cancelled' else 'This question did not finish. Check Activity before submitting another question.',outcome='unavailable',completion='incomplete')
        return value

    def events(self,run_id,id,site_id,after=0):
        self.review(run_id,site_id)
        job=self.rt.jobs.get(id)
        if job.kind!='video_chat' or job.site_id!=site_id or job.payload.get('review_run_id')!=run_id:raise KeyError('Question not found in this review')
        return ChatActivity(self.rt).events(id,site_id,after)

    def submit(self,run_id,body,user):
        run=self.review(run_id,user.site_id)
        request=body.model_dump(mode='json');key='video-chat:'+user.site_id+':'+run_id+':'+body.idempotency_key
        with self.rt.db.connect() as db:old=db.execute('SELECT body FROM jobs WHERE key=?',(key,)).fetchone()
        if old:
            previous=Job.model_validate_json(old[0])
            if previous.payload['request']!=request:raise ValueError('Idempotency key has a different request')
            return self.get(run_id,previous.id,user.site_id)
        if body.mode=='hosted' and not hosted_available(self.rt):raise ValueError('AI is unavailable. Enable AI and configure provider access before sending.')
        if body.mode=='hosted' and run.mode=='fixture':raise ValueError('Simulated reviews support fixture questions only')
        status=ReviewRuns(self.rt).status(run_id,user.site_id)
        cases={item.case.id for item in status.safety.cases}
        if body.focus.finding_id and body.focus.finding_id not in cases:raise ValueError('Finding is outside this review')
        if body.focus.timestamp_ms is not None and not run.start_ms<=body.focus.timestamp_ms<=run.end_ms:raise ValueError('Moment is outside this review')
        session=SafetyChat(self.rt).for_review(run_id,user.site_id,create=True)
        chunks=[]
        for window in status.windows:
            if window.chunk_id:
                try:chunks.append(self.rt.db.get('video_chunk',window.chunk_id))
                except KeyError:pass
        records=[LabCitation(id=c['id'],title='Reviewed interval',kind='record',text=json.dumps(c),version=fingerprint(c),provenance={'record_kind':'video_chunk'}) for c in chunks]
        prior_turns=self.state(run_id,user.site_id).turns[-4:]
        prior_evidence=[]
        for turn in prior_turns:
            if not turn.answer or turn.answer.outcome not in ('answered','partial') or turn.context.source_hash!=run.source_hash:continue
            frames=[e for e in turn.evidence if e.origin!='crop']
            prior_evidence.extend(e.model_dump(mode='json') for e in turn.evidence)
            if not frames:continue
            key='chat-answer:'+turn.id
            record={'question':turn.message,'answer':turn.answer.model_dump(mode='json'),'notice':'Previous checked chat answer, not new visual evidence.'}
            records.append(LabCitation(id=key,title='Previous question and retained evidence',text=json.dumps(record),kind='record',version=fingerprint(record),provenance={'record_kind':'video_chat_answer'}))
            chunks.append({'id':key,'summary':turn.message,'observations':[],
                'intervals':[{'start_ms':min(e.frame.timestamp_ms for e in frames),'end_ms':max(e.frame.timestamp_ms for e in frames)}],
                'frames':[e.frame.model_dump() for e in frames]})
        summary={'review_run_id':run_id,'completed_intervals':status.completed_windows,'total_intervals':status.total_windows,
                 'coverage_complete':status.coverage_complete,'pending_specialists':status.pending_specialists,
                 'limitations':run.limitations,'human_decisions':[d.model_dump(mode='json') for d in status.decisions]}
        records.append(LabCitation(id='review-context:'+run_id,title='Review coverage and human decisions',kind='record',text=json.dumps(summary),version=fingerprint(summary),provenance={'record_kind':'review_context'}))
        record_scope={run_id,*cases,*[s.id for s in status.safety.screenings],*[a.assessment.id for a in status.safety.cases if a.assessment]}
        permissions={'operator':user.id,'site_id':user.site_id,'review_run_id':run_id,'access':'review-chat-1'}
        snapshot=freeze(self.rt,user.site_id,permissions,record_scope,records)
        config=json.loads((self.rt.root/'configs/video-chat.json').read_text())
        agents=copy.deepcopy(self.rt.config)
        agents['limits'].update(workflow_usd=min(.05,agents['limits']['workflow_usd']),agent_usd=min(.05,agents['limits']['agent_usd']),
            day_usd=min(2,agents['limits']['day_usd']),month_usd=min(20,agents['limits']['month_usd']),requests=12,tools=16,workflow_tools=16,
            workflow_seconds=180,queue_seconds=120,frames=6,clip_ms=10000,media_bytes=6000000,output_per_request=1800)
        agents['review_team']['max_workflow_requests']=12
        # Images are counted by the provider before dispatch, then priced from reported tokens.
        agents['pricing']['gpt-6-luna']['image_usd']=0
        code=Path(__file__).parents[2]
        prompts={folder+'/'+path.name:path.read_text() for folder in ('safety_reviewer','challenge_reviewer','video_observer') for path in (code/'agents'/folder).glob('lab_*.md')}
        prompts['safety_reviewer/lab_scope.md']=(code/'agents/safety_reviewer/video_scope.md').read_text()
        prompts['safety_reviewer/lab_prompt.md']=prompts['safety_reviewer/lab_prompt.md'].replace('start analyses,','start full reviews,')+'\n'+(code/'agents/safety_reviewer/video_chat_prompt.md').read_text()
        prompts['challenge_reviewer/lab_prompt.md']+='\n'+(code/'agents/challenge_reviewer/video_chat_prompt.md').read_text()
        prompts['video_observer/video_chat_prompt.md']=(code/'agents/video_observer/video_chat_prompt.md').read_text()
        context=VideoChatContext(review_run_id=run_id,source_hash=run.source_hash,media_id=run.media_id,snapshot_id=snapshot['id'],focus=body.focus,
            start_ms=run.start_ms,end_ms=run.end_ms,review_version=fingerprint(status.model_dump(mode='json')))
        # Prior answers are pointers with whole approved blocks, never chopped prose or new evidence.
        history=[];history_bytes=0
        for turn in prior_turns:
            if turn.answer and turn.answer.outcome in ('answered','partial'):
                entry={'question':turn.message,'claims':[c.model_dump() for c in turn.answer.claims],'missing_context':turn.answer.missing_context}
                size=len(json.dumps(entry).encode())
                if history_bytes+size<=8000:history.append(entry);history_bytes+=size
        versions={'workflow':config['version'],'policy':config['policy_version'],'prompts':fingerprint(prompts),'sources':snapshot['id'],
            'implementation':fingerprint({str(p.relative_to(code)):p.read_text() for p in [code/'workflows/answering.py',code/'workflows/video_chat.py',code/'modules/safety_review/video_evidence.py',code/'agents/shared/factory.py',code/'infrastructure/decisions.py',code/'infrastructure/token_preflight.py',code/'infrastructure/request_queue.py',code/'agents/shared/lab_release.py']})}
        payload={'request':request,'question':body.message.strip(),'mode':body.mode,'session_id':session.id,'review_run_id':run_id,
            'snapshot':snapshot,'config':config,'agents_config':agents,'prompts':prompts,'versions':versions,'media_ids':[run.media_id],
            'video_context':context.model_dump(),'review_snapshot':summary,'chunks':chunks,'people':[p.model_dump() for p in status.people],
            'history':history,'prior_evidence':prior_evidence,'review_request':run.request}
        try:job=self.rt.jobs.start('video_chat',key,payload,user.site_id,exclusive_session=session.id)
        except ValueError:
            # Concurrent duplicate sends may freeze at different instants. The first committed
            # request owns the context and allowance; match only the original submission.
            with self.rt.db.connect() as db:row=db.execute('SELECT body FROM jobs WHERE key=?',(key,)).fetchone()
            if not row:raise
            job=Job.model_validate_json(row[0])
            if job.payload['request']!=request:raise
        return self.get(run_id,job.id,user.site_id)
