"""Replay worker retains frames before CV and schedules AI independently."""
import asyncio
import hashlib
import json
import os
import time
import cv2
from app.pipeline import Pipeline
from app.models.contracts import FramePacket,Context
from safety_app.domain.models import MediaRef,CandidateEvent,ToolFailure,uid
from safety_app.modules.monitoring.events import merge_event

async def monitor(rt,job,detector=None):
    profile=rt.camera(job.payload['profile_id']);lab=Pipeline(rt.root,detector=detector);lab.discover()
    deployments=rt.db.list('deployment')
    if deployments and detector is None:
        matching=[d for d in deployments if profile.camera_id in d['camera_scope']]
        if matching:
            registered=rt.db.get('model_registration',matching[-1]['model_id']);lab.config.detector='custom';lab.config.custom_model_id=registered['registered_ref']
        else:
            baseline=rt.db.get('detector_default','baseline');lab.config.detector=baseline['detector'];lab.config.custom_model_id=baseline['custom_model_id']
        lab.detector=lab.make_detector(lab.config)
    if profile.view_type=='driver_pov':lab.config.zones=[];lab.config.rules=[]
    if profile.zones:
        from app.models.contracts import ZoneConfig
        lab.config.zones=[ZoneConfig.model_validate(z) for z in profile.zones]
    lab.config.analysis_mode='cv_only'
    start=job.payload['start_ms'];end=job.payload['end_ms'];mid=job.run_id
    try:
        await lab.start(job.payload['source_id'],'offline',profile.camera_id,persist_config=False,rule_layout={'zones':[],'rules':[],'required_ppe':lab.config.required_ppe} if profile.view_type=='driver_pov' else None)
        # A moving-camera profile must not inherit fixed geometry from persisted editor layouts.
        if profile.view_type=='driver_pov':lab.config.zones=[];lab.config.rules=[]
        elif profile.zones and not lab.config.zones:
            from app.models.contracts import ZoneConfig
            lab.config.zones=[ZoneConfig.model_validate(z) for z in profile.zones]
        from app.rules.engine import RuleEngine
        lab.rules=RuleEngine(lab.config)
        if start:lab.pending=await lab.source_work(lab.source.seek,start)
        source=lab.sources[job.payload['source_id']]
        def source_hash():
            with open(source['path'],'rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
        digest=await asyncio.to_thread(source_hash) if source['kind']=='file' else hashlib.sha256(mid.encode()).hexdigest()
        live=job.kind=='live'
        rt.db.save('media',MediaRef(id=mid,media_id=mid,source_id=source['id'],camera_id=profile.camera_id,start_ms=start,end_ms=end if not live else start,content_hash=digest,storage_key='source:'+source['id']))
        results=[mid];last_sample=-1e9;last_periodic=start-rt.config['limits']['periodic_seconds']*1000;beg=time.monotonic()
        while not rt.jobs.cancelled(job.id):
            decoded=lab.pending or await lab.source_work(lab.read);lab.pending=None
            if decoded is None:break
            ms=decoded.timestamp_ms
            if not live and ms>end:break
            if ms<start or ms-last_sample<1000/lab.config.target_fps:continue
            last_sample=ms;rt.jobs.heartbeat(job.id)
            ok,jpeg=cv2.imencode('.jpg',decoded.image,[cv2.IMWRITE_JPEG_QUALITY,80])
            if not ok:raise ValueError('Cannot encode evidence')
            if live:
                m=rt.db.get('media',mid);m['end_ms']=ms;rt.db.save('media',m)
            rt.media.add_frame(mid,str(decoded.frame_id),ms,jpeg.tobytes())
            # AI cadence is independent of detections and CV errors. Reduce periodic work first.
            if ms-last_periodic>=rt.config['limits']['periodic_seconds']*1000:
                last_periodic=ms
                if len([j for j in rt.jobs.list() if j.kind=='periodic_review' and j.status in ('queued','running')])<2:
                    rt.jobs.start('periodic_review',f'{job.id}:periodic:{ms}',{'media_ids':[mid],'start_ms':max(start,ms-10000),'end_ms':ms,'context':'Independent periodic view'},job.site_id)
                else:
                    rt.db.save('coverage_gap',{'id':uid(),'job_id':job.id,'media_id':mid,'start_ms':max(start,ms-10000),'end_ms':ms,'reason':'Periodic queue backpressure'})
                    rt.audit.write('coverage_gap',status='skipped',job_id=job.id,run_id=job.run_id,provenance={'start_ms':max(start,ms-10000),'end_ms':ms})
            context={k:lab.session[k] for k in Context.model_fields}
            try:tracks,detections=await lab.infer(decoded,context)
            except Exception as exc:
                rt.db.save('label_queue',{'id':uid(),'media_id':mid,'frame_id':f'{mid}-{decoded.frame_id}','reason':'Detector failed. Inspect missed objects.','status':'draft'})
                rt.audit.write('cv_failed',status='unknown',job_id=job.id,run_id=job.run_id,error=ToolFailure(code=type(exc).__name__,message='Detector failed. Evidence retained.'));continue
            h,w=decoded.image.shape[:2];packet=FramePacket(**context,frame_id=decoded.frame_id,timestamp_ms=ms,width=w,height=h,tracks=tracks,detections=detections,cv_status='available',evidence_cutoff_ms=ms)
            rt.db.save('cv_frame',{'id':f'{mid}-{decoded.frame_id}',**packet.model_dump(mode='json'),'media_id':mid,'detector_provenance':lab.detector.provenance})
            for candidate in lab.rules.process(packet):
                event=CandidateEvent(id=candidate.id,site_id=job.site_id,camera_id=profile.camera_id,event_type=candidate.kind,start_ms=candidate.start_ms,end_ms=candidate.evidence_cutoff_ms,media_ids=[mid],producer_version='legacy-rule-engine',track_ids=[str(i) for i in candidate.track_ids],status=candidate.state)
                event=merge_event(rt.db,event)
                if event.id not in results:
                    results.append(event.id);rt.audit.write('candidate_event',job_id=job.id,event_id=event.id,output=event.model_dump(mode='json'))
                    rt.jobs.start('event_review',f'{job.id}:event:{event.id}',{'event_id':event.id,'media_ids':[mid],'start_ms':max(start,ms-10000),'end_ms':ms,'context':'CV candidate; verify visible evidence'},job.site_id)
            if live:await asyncio.sleep(max(0,1/lab.config.target_fps))
            if time.monotonic()-beg>rt.config['training']['max_seconds']:raise TimeoutError('Monitoring run duration limit reached')
        if rt.jobs.cancelled(job.id):raise asyncio.CancelledError()
        if last_sample<start:raise FileNotFoundError('No decoded footage in the requested interval')
        if not live and last_sample<end:
            gap={'id':uid(),'job_id':job.id,'media_id':mid,'start_ms':last_sample,'end_ms':end,'reason':'Source ended before the requested interval. Coverage is unknown.'}
            rt.db.save('coverage_gap',gap);rt.audit.write('coverage_gap',status='unknown',job_id=job.id,run_id=job.run_id,provenance=gap)
            m=rt.db.get('media',mid);m['end_ms']=last_sample;rt.db.save('media',m)
        return results
    finally:
        if lab.task:lab.task.cancel();await asyncio.gather(lab.task,return_exceptions=True)
        if lab.source:await lab.source_work(lab.source.close)
        lab.repository.db.close();lab.evidence.db.close()
