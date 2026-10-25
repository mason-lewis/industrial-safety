"""Causal adapter for the existing paced player. Only arrived pixels can be sealed."""
import asyncio
import base64
import time
import cv2
from safety_app.modules.monitoring.temporal_sampling import TemporalSampler
from safety_app.domain.review_runs import ReviewWindow
from safety_app.domain.models import utc
from safety_app.modules.safety_review.review_runs import ReviewRuns, update_record


class LiveReview:
    def __init__(self, rt, pipeline):
        self.rt=rt;self.pipeline=pipeline;self.run=None;self.windows=[];self.index=0
        self.sampler=TemporalSampler();self.last_progress=-1000
        self.cv_pending=None;self.cv_retry_at=0

    async def start(self, body, site_id, restart=False):
        from safety_app.infrastructure.ai_control import require_hosted
        require_hosted(self.rt, body.mode)
        if body.start_ms!=0 or body.end_ms is not None:
            raise ValueError('Camera simulation uses the complete recording; choose Review for a selected interval')
        body=body.model_copy(update={'review_strategy':'full','safety_screening':True})
        parent=self.rt.jobs.start('live_review',body.idempotency_key,body.model_dump(mode='json'),site_id)
        service=ReviewRuns(self.rt)
        try:existing=service.get(parent.run_id,site_id)
        except KeyError:existing=None
        if existing:
            # A retried creation request must not cancel or restart its own run.
            return service.status(existing.id,site_id)
        try:run=service.prepare(parent,body,'live')
        except Exception:
            from safety_app.domain.models import ToolFailure
            self.rt.jobs.finish(parent,'failed',error=ToolFailure(code='invalid_source',message='Cannot prepare Live source'))
            raise
        if self.run and ReviewRuns(self.rt).get(self.run.id,site_id).state not in ('ended','cancelled'):
            ReviewRuns(self.rt).cancel(self.run.id,site_id)
        self.pipeline.course_review=None
        if self.pipeline.review:
            self.pipeline.review.set_enabled(False,False)
            await self.pipeline.review.close()
        self.pipeline.discover()
        try:
            if restart and self.pipeline.session:
                session=await self.pipeline.playback('restart')
            else:
                session=await self.pipeline.start(body.source_id,'live_simulation',body.camera_id,persist_config=False)
        except BaseException:
            ReviewRuns(self.rt).cancel(run.id,site_id)
            raise
        run.session_id=session['session_id'];run.generation=session['run_generation'];run.state='paused'
        self.rt.db.save('review_run',run)
        self.rt.jobs.finish(parent,'running')
        self.run=run;self.windows=ReviewRuns(self.rt).windows(run.id);self.index=0
        self.reset_samples();self.last_progress=-1000
        self.cv_pending=None;self.cv_retry_at=0
        self.pipeline.course_review=self
        if body.mode=='hosted' and hasattr(self.rt.people.detector,'warmup'):
            try:
                async with asyncio.timeout(20):
                    await asyncio.to_thread(self.rt.people.detector.warmup)
            except (Exception,TimeoutError):
                # CV is optional. The live result reports failure, never zero people.
                pass
        return ReviewRuns(self.rt).status(run.id,site_id)

    def on_frame(self, packet, image):
        run=self.run
        if not run or packet.session_id!=run.session_id or packet.run_generation!=run.generation:
            return
        if self.rt.jobs.cancelled(run.job_id):
            self.pipeline.playing=False
            return
        cutoff=min(packet.timestamp_ms,run.end_ms)
        self.run.cutoff_ms=cutoff
        # Every boundary is explicit, including skipped source-time ranges.
        while self.index<len(self.windows) and packet.timestamp_ms>=self.windows[self.index].end_ms:
            self.seal(self.windows[self.index],cutoff)
            self.index+=1;self.reset_samples()
        if self.index<len(self.windows) and packet.timestamp_ms>=self.windows[self.index].start_ms:
            self.sampler.add(packet.frame_id,packet.timestamp_ms,image)
        if cutoff-self.last_progress>=1000:
            update_record(self.rt,'review_run',run.id,cutoff_ms=cutoff,state='running')
            self.rt.jobs.heartbeat(run.job_id);self.last_progress=cutoff

    def seal(self, window, cutoff):
        run=self.run
        update_record(self.rt,'review_run',run.id,cutoff_ms=cutoff)
        if not self.sampler.samples:
            update_record(self.rt,'review_window',window.id,status='skipped',gaps=['No arrived frames in this source interval'])
            return
        frames=self.sampler.retain(self.rt.media,run.media_id)
        if any(frame.timestamp_ms>cutoff for frame in frames):raise ValueError('Future frame rejected')
        if run.mode=='hosted':
            from safety_app.infrastructure.budgets import BudgetBlocked
            try:self.rt.budgets.require_settled(run.id)
            except BudgetBlocked as exc:
                update_record(self.rt,'review_window',window.id,status='skipped',
                              frame_ids=[f.id for f in frames],gaps=[str(exc)])
                self.rt.audit.write('live_window_paused',run_id=run.id,
                    output={'window_id':window.id,'reason':'unresolved_charge','generation_dispatched':False})
                return
        # Windows queue oldest-first up to the run's frozen backlog; beyond it the
        # oldest pending window is displaced atomically. Runs created before the
        # backlog setting keep one newest pending slot. The worker claims with the
        # same SQLite write lock; an already active job wins.
        backlog=max(1,int(run.configuration.get('video_screening',{}).get('live_pending_windows',1)))
        with self.rt.db.connect() as db:
            from safety_app.domain.models import Job,ToolFailure
            db.execute('BEGIN IMMEDIATE')
            pending=db.execute("SELECT body FROM jobs WHERE kind='live_screening' AND status='queued' AND json_extract(body,'$.payload.review_run_id')=? ORDER BY rowid",(run.id,)).fetchall()
            displaced=[]
            for row in pending[:max(0,len(pending)-backlog+1)]:
                old=Job.model_validate_json(row['body']);old.status='cancelled';old.updated_at=utc()
                old.error=ToolFailure(code='live_backpressure',message=f'Displaced after {backlog} sealed camera window(s) were waiting')
                db.execute("UPDATE jobs SET status='cancelled',body=? WHERE id=?",(old.model_dump_json(),old.id))
                displaced.append(old.payload['window_id'])
        for id in displaced:
            update_record(self.rt,'review_window',id,status='skipped',gaps=[f'Live processing fell more than {backlog} window(s) behind; newer windows retained'])
            self.rt.audit.write('live_window_displaced',run_id=run.id,output={'window_id':id})
        payload={'review_run_id':run.id,'parent_job_id':run.job_id,'window_id':window.id,'budget_workflow_id':run.id}
        child=self.rt.jobs.start('live_screening','live-window:'+window.id,payload,run.site_id)
        update_record(self.rt,'review_window',window.id,status='queued',frame_ids=[f.id for f in frames],job_id=child.id)

    def reset_samples(self):
        from safety_app.modules.monitoring.temporal_sampling import VERSION, VERSIONS
        version=self.run.configuration.get('video_screening',{}).get('temporal_sampling') if self.run else None
        self.sampler=TemporalSampler(max_frames=min(6,self.run.configuration['limits']['frames']) if version in VERSIONS else 2,
                                    version=version if version in VERSIONS else VERSION)

    def schedule_people(self,packet,image):
        # Never seek ahead, queue an unbounded backlog, or run CV in an offline fixture.
        if not self.run or self.run.mode=='fixture' or time.monotonic()<self.cv_retry_at:return
        if packet.session_id!=self.run.session_id or packet.run_generation!=self.run.generation:return
        if self.rt.jobs.cancelled(self.run.job_id):return
        h,w=image.shape[:2];width=min(w,960)
        self.cv_pending=(packet,cv2.resize(image,(width,round(h*width/w))),self.run)
        if self.pipeline.cv_task is None or self.pipeline.cv_task.done():
            self.pipeline.cv_task=asyncio.create_task(self.people_worker())

    async def people_worker(self):
        from safety_app.modules.monitoring.people import detect_people
        from safety_app.domain.people import LivePersonSample
        while self.cv_pending:
            packet,image,run=self.cv_pending;self.cv_pending=None
            begin=time.monotonic()
            result=await asyncio.to_thread(detect_people,self.rt.people.detector,image,
                run.request.get('person_confidence',.5),str(packet.frame_id))
            if (self.pipeline.course_review is not self or self.run is not run
                or not self.pipeline.current(packet.session_id,packet.run_generation)
                or self.rt.jobs.cancelled(run.job_id)):
                self.cv_pending=None;return
            ok,jpeg=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,75])
            sample=LivePersonSample(**result.model_dump(),review_run_id=run.id,source_id=run.source_id,
                session_id=packet.session_id,run_generation=packet.run_generation,frame_id=str(packet.frame_id),
                position_ms=packet.timestamp_ms,timestamp_ms=packet.timestamp_ms,
                elapsed_ms=round((time.monotonic()-begin)*1000),
                image_data_url='data:image/jpeg;base64,'+base64.b64encode(jpeg).decode() if ok else '')
            await self.pipeline.emit({'type':'review_people','sample':sample.model_dump(mode='json')})
            if result.status!='available':
                self.cv_pending=None;self.cv_retry_at=time.monotonic()+5

    def ended(self):
        if not self.run or self.rt.jobs.cancelled(self.run.job_id):return
        from safety_app.modules.monitoring.recordings import describe,resolve
        if describe(resolve(self.rt.root,self.run.source_id))[0]!=self.run.source_hash:
            update_record(self.rt,'review_run',self.run.id,state='failed',source_verified=False,
                          limitations=[*self.run.limitations,'Source changed during camera replay; provenance verification failed.'])
            raise ValueError('Source changed during camera replay')
        if self.index<len(self.windows):
            # EOF seals the tail; its retained pixels still stop at the last arrived frame.
            self.seal(self.windows[self.index],self.run.end_ms)
            self.index+=1
        for window in self.windows[self.index:]:
            update_record(self.rt,'review_window',window.id,status='skipped',gaps=['Source ended before this interval arrived'])
        update_record(self.rt,'review_run',self.run.id,state='ended',cutoff_ms=self.run.end_ms,source_verified=True)
        self.rt.jobs.finish(self.rt.jobs.get(self.run.job_id),'completed',[self.run.id])

    async def control(self,id,action,site_id):
        saved=ReviewRuns(self.rt).get(id,site_id)
        # Leaving a saved/stopped run must not prevent History navigation or
        # touch a different camera that is now attached to this controller.
        if action=='pause' and saved.kind=='live' and (
            saved.state in ('ended','cancelled','failed','interrupted') or
            self.rt.jobs.get(saved.job_id).status not in ('queued','running')):
            return ReviewRuns(self.rt).status(id,site_id)
        if not self.run or self.run.id!=id or self.run.site_id!=site_id:
            raise ValueError('This live run is no longer attached to the player')
        if action=='restart':
            from safety_app.domain.video_analysis import VideoAnalysisRequest
            from safety_app.domain.models import uid
            body=VideoAnalysisRequest.model_validate(self.run.request).model_copy(update={'idempotency_key':uid()})
            return await self.start(body,site_id,restart=True)
        if self.rt.jobs.cancelled(self.run.job_id):raise ValueError('This live run was stopped; start a new run')
        if ReviewRuns(self.rt).get(id,site_id).state=='ended':
            if action=='pause':return ReviewRuns(self.rt).status(id,site_id)
            raise ValueError('The source has ended. Restart creates a new run.')
        await self.pipeline.playback(action)
        if action=='pause':
            with self.rt.db.connect() as db:db.execute('UPDATE jobs SET lease=NULL WHERE id=?',(self.run.job_id,))
        update_record(self.rt,'review_run',id,state='running' if action=='play' else 'paused')
        return ReviewRuns(self.rt).status(id,site_id)
