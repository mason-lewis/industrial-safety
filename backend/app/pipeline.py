from __future__ import annotations
import asyncio
import base64
from datetime import datetime,timezone
import json
import hashlib
import os
from pathlib import Path
import time
from uuid import uuid4
import cv2
from app.models.contracts import *
from app.cv.detector import RFDETRDetector,ByteTrackAdapter,CAPABILITIES
from app.video.sources import FileReplaySource,RTSPSource
from app.rules.engine import RuleEngine
from app.cv.classes import filtered_detections,inference_floor,groups_for
from app.cv.models import resolve_model,model_catalog
from app.evidence.store import EvidenceStore,EvidenceScope
from app.storage.repository import Repository

ROOT=Path(__file__).resolve().parents[2]

class DecodeFailure(RuntimeError):
    """Redacted source decode failure; camera credentials never reach clients."""

class InferenceFailure(RuntimeError):
    """Real detector failed; no fixture substitute is available."""

class Pipeline:
    def __init__(self,root=ROOT,detector=None):
        self.course_review=None
        self.root=Path(root)
        config_path=self.root/'data/config/lab.json'
        self.config=LabConfig.model_validate_json(config_path.read_text()) if config_path.exists() else LabConfig()
        self.repository=Repository(self.root/'data/artifacts/runs/lab.sqlite')
        try: self.repository.get('config',f'config-{self.config.version}')
        except KeyError: self.repository.save('config',dict(self.config.model_dump(mode='json'),id=f'config-{self.config.version}'))
        self.snapshot_procedures(self.config.version)
        self.evidence=EvidenceStore(self.root/'data/artifacts/evidence')
        self.detector=detector or self.make_detector(self.config)
        self.tracker=None; self.rules=RuleEngine(self.config)
        self.session=None; self.source=None; self.sources={}; self.events={}; self.incidents={}; self.metrics=PipelineMetrics()
        self.decode_lock=asyncio.Lock(); self.subscribers=set(); self.playing=False; self.speed=1.; self.task=None; self.review=None; self.pending=None
        self.health={'state':'idle','message':'Choose a source'}
        self.last_timestamp=None; self.last_arrival=None; self.last_hash=None; self.freeze_count=0; self.started=time.monotonic(); self.count=0; self.last_scene_ms=-30000; self.processing_seconds=0; self.cv_task=None; self.cv_latest=None; self.cv_pending=None; self.cv_health='unavailable'; self.segment_task=None; self.segment_start=None
    def snapshot_procedures(self,version):
        try: self.repository.get('procedures',f'config-{version}'); return
        except KeyError: pass
        documents={}
        for path in sorted((self.root/'data/procedures').glob('*'))[:20]:
            if path.is_file() and path.suffix in ('.md','.txt') and path.name.lower()!='readme.md':
                with path.open() as stream: documents[path.name]=stream.read(12000)
        self.repository.save('procedures',{'id':f'config-{version}','documents':documents,'captured_at':datetime.now(timezone.utc).isoformat()})

    async def source_work(self,function,*args):
        # Cancellation must join native decoder/inference work before the lock is released.
        task=asyncio.create_task(asyncio.to_thread(function,*args))
        try: return await asyncio.shield(task)
        except asyncio.CancelledError:
            try: await task
            except Exception: pass
            raise

    def retain_records(self,timestamp_ms):
        for collection in (self.events,self.incidents):
            while len(collection)>500: collection.pop(next(iter(collection)))

    def make_detector(self,config):
        weights=None;class_map=config.custom_class_map if config.detector=='custom' else None;license=config.custom_weights_license
        if config.detector=='custom' and config.custom_model_id:
            manifest,weights,class_map=resolve_model(self.root,config.custom_model_id);license=manifest['license']
        elif config.detector=='custom' and config.custom_weights:
            weights=(self.root/'data/artifacts/models'/config.custom_weights).resolve()
            if not weights.is_relative_to((self.root/'data/artifacts/models').resolve()) or not weights.is_file():
                raise ValueError('Custom weights must be an existing file inside data/artifacts/models')
        adapter=RFDETRDetector(config.detector,str(weights) if weights else None,class_map)
        if license: adapter.custom_license=license
        return adapter
    def current(self,session_id,generation):
        return bool(self.session and self.session['session_id']==session_id and self.session['run_generation']==generation)
    def discover(self):
        folder=self.root/'data/input'; folder.mkdir(parents=True,exist_ok=True)
        self.sources={}
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in {'.mp4','.mov','.mkv','.avi','.webm','.mpeg','.m4v'} and path.is_file() and path.resolve().is_relative_to(folder.resolve()):
                id=hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]; self.sources[id]={'id':id,'name':path.name,'kind':'file','path':str(path)}
        rtsp=os.getenv('LAB_RTSP_URL') or os.getenv('RTSP_URL')
        if rtsp: self.sources['rtsp-1']={'id':'rtsp-1','name':'Configured RTSP camera','kind':'rtsp','path':rtsp}
        from safety_app.infrastructure.library_visibility import hidden_sources
        hidden=hidden_sources(self.root)
        return [{k:v for k,v in source.items() if k!='path'} for source in self.sources.values() if source['id'] not in hidden]
    async def start(self,source_id,mode,camera_id=None,persist_config=True,rule_layout=None):
        if source_id not in self.sources: raise ValueError('Unknown source ID; refresh sources')
        if self.course_review:
            from safety_app.modules.safety_review.review_runs import ReviewRuns
            previous=self.course_review
            if previous.run and previous.run.state not in ('ended','cancelled'):
                ReviewRuns(previous.rt).cancel(previous.run.id,previous.run.site_id)
            self.course_review=None
        if self.task: self.task.cancel(); await asyncio.gather(self.task,return_exceptions=True)
        if self.cv_task:
            self.cv_pending=None; self.cv_task.cancel(); await asyncio.gather(self.cv_task,return_exceptions=True)
        if self.session and self.review: self.review.cancel_generation(self.session['session_id'],self.session['run_generation'])
        if self.source: await self.source_work(self.source.close)
        description=self.sources[source_id]
        camera_id = camera_id or f'camera-{source_id}'
        try:
            layout=self.repository.get('camera_rules',camera_id)
        except KeyError:
            # Import the existing single-camera layout once; new cameras start empty.
            layouts=self.repository.list('camera_rules')
            layout={'zones':self.config.zones,'rules':self.config.rules,'required_ppe':self.config.required_ppe} if not layouts else {'zones':[],'rules':[],'required_ppe':[]}
        if rule_layout is not None: layout=rule_layout
        self.config=LabConfig.model_validate(self.config.model_dump() | {k:layout[k] for k in ('zones','rules','required_ppe')} )
        self.config=self.repository.snapshot_config(self.config)
        self.snapshot_procedures(self.config.version)
        if persist_config:
            path=self.root/'data/config/lab.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(self.config.model_dump_json(indent=2))
        if rule_layout is None: self.repository.save('camera_rules',dict(id=camera_id, zones=[z.model_dump(mode='json') for z in self.config.zones],rules=[r.model_dump(mode='json') for r in self.config.rules],required_ppe=self.config.required_ppe))
        self.source=(RTSPSource if description['kind']=='rtsp' else FileReplaySource)(description['path'])
        await self.source_work(self.source.open)
        self.session={'source_id':source_id,'camera_id':camera_id,'session_id':str(uuid4()),'run_generation':0,'schema_version':'1.0','mode':mode,'timestamp_ms':0,'playing':False,'speed':1.,'source_name':description['name'],'source_kind':description['kind'],'duration_ms':getattr(self.source,'provenance',{}).get('duration_ms')}
        self.repository.save('session',dict(self.session,id=self.session['session_id'],config=self.config.model_dump(mode='json')))
        if self.segment_task: await asyncio.gather(self.segment_task,return_exceptions=True)
        self.segment_start=None
        self.playing=False; self.speed=1.; self.reset(); self.health={'state':'ready','message':'Source open'}
        self.task=asyncio.create_task(self.loop()); return self.session
    def reset(self):
        self.cv_latest=None; self.cv_pending=None; self.cv_health='unavailable';self.segment_start=None
        self.tracker=ByteTrackAdapter(frame_rate=self.config.target_fps,config=self.config); self.rules=RuleEngine(self.config); self.events={}; self.incidents={}; self.pending=None; self.last_timestamp=None; self.last_arrival=None; self.freeze_count=0; self.last_hash=None; self.last_scene_ms=-30000
    async def playback(self,action,speed=None,timestamp_ms=None):
        if not self.session: raise ValueError('No active session')
        if self.course_review and (action in ('seek','step','speed','restart') or speed not in (None,1)):
            raise ValueError('Camera simulation permits play, pause and restart at 1x only')
        if speed is not None:
            if not .1<=speed<=8: raise ValueError('Speed must be .1..8')
            self.speed=speed
        if action in ('seek','restart'):
            self.playing=False
            old=self.session['run_generation']; self.session['run_generation']+=1
            if self.review: self.review.cancel_generation(self.session['session_id'],old)
            # Wait for frame worker to finish before changing decoder state.
            async with self.decode_lock:
                self.reset()
                target=0 if action=='restart' else max(0,timestamp_ms or 0)
                duration=self.session.get('duration_ms')
                if duration: target=min(target,max(0,duration-max(1,round(1000/(getattr(self.source,'fps',None) or 30)))))
                self.pending=await self.source_work(self.source.seek,target)
                self.session['timestamp_ms']=self.pending.timestamp_ms
            await self.emit({'type':'clear','run_generation':self.session['run_generation']})
        elif action=='speed': pass
        elif action=='play': self.playing=True
        elif action=='pause': self.playing=False
        elif action=='step':
            self.playing=False
            async with self.decode_lock:
                try: await self.process_one()
                except StopAsyncIteration: self.health={'state':'ended','message':'End of source'}
        else: raise ValueError('Unknown playback action')
        self.session.update(playing=self.playing,speed=self.speed); return self.session
    async def loop(self):
        backoff=1
        while True:
            if not self.playing: await asyncio.sleep(.05); continue
            begin=time.monotonic()
            previous_timestamp=self.last_timestamp
            try:
                async with self.decode_lock: await self.process_one()
                backoff=1
            except StopAsyncIteration:
                self.playing=False; self.health={'state':'ended','message':'End of source'}
            except Exception as exc:
                self.metrics.errors+=1
                self.health={'state':'disconnected' if isinstance(exc,DecodeFailure) and isinstance(self.source,RTSPSource) else 'error','message':f'{type(exc).__name__}: source or inference processing failed; video paused or reconnecting','decode_failure':getattr(self,'processing_stage','decode')=='decode','inference_failure':getattr(self,'processing_stage','decode')=='inference'}
                # Never publish credential-bearing source errors.
                if isinstance(self.source,RTSPSource):
                    await asyncio.sleep(backoff); backoff=min(30,backoff*2)
                    try: await self.source_work(self.source.close); await self.source_work(self.source.open)
                    except Exception: pass
                else: self.playing=False
            if self.session: self.session.update(playing=self.playing,speed=self.speed)
            if self.session and self.session['mode']=='live_simulation':
                source_delta=(self.last_timestamp-previous_timestamp)/1000 if self.last_timestamp is not None and previous_timestamp is not None else 1/self.config.target_fps
                await asyncio.sleep(max(0,source_delta/self.speed-(time.monotonic()-begin)))
    def read(self):
        try: return self.source.read()
        except StopIteration: return None
        except Exception as error: raise DecodeFailure(type(error).__name__) from None
    async def process_one(self):
        generation=self.session['run_generation']; context={k:self.session[k] for k in Context.model_fields}
        begin=time.monotonic()
        self.processing_stage='decode'
        decoded=self.pending or await self.source_work(self.read); self.pending=None
        decoded_at=time.monotonic()
        if decoded is None:
            await self.flush_segment()
            if self.course_review:self.course_review.ended()
            raise StopAsyncIteration
        if self.last_timestamp is not None and decoded.timestamp_ms<self.last_timestamp:
            self.session['run_generation']+=1
            if self.review: self.review.cancel_generation(context['session_id'],generation)
            self.reset(); context['run_generation']=self.session['run_generation']; generation=context['run_generation']
        # Sample source frames using their PTS. Target FPS is a processing cap, not a source-time clock.
        if self.session['mode']=='live_simulation' and self.last_timestamp is not None:
            while decoded.timestamp_ms-self.last_timestamp < 1000/self.config.target_fps:
                nxt=await self.source_work(self.read)
                if nxt is None: break
                decoded=nxt; decoded_at=time.monotonic(); self.metrics.dropped_frames+=1
        inference_begin=time.monotonic(); tracks=[]; detections_raw=[]; inference_ms=0
        cv_status='unavailable'
        if self.course_review:
            pass  # Course Live schedules independent stock-person CV after frame construction.
        elif self.config.analysis_mode=='cv_only':
            tracks,detections_raw=await self.infer(decoded,context)
            inference_ms=(time.monotonic()-inference_begin)*1000; cv_status='available'
        else:
            # CV has its own active task and newest pending frame. Source playback never awaits it.
            self.cv_pending=(decoded,context,self.config.version)
            if self.cv_task is None or self.cv_task.done(): self.cv_task=asyncio.create_task(self.cv_worker())
        if not self.current(context['session_id'],generation): return
        h,w=decoded.image.shape[:2]
        packet=FramePacket(**context,frame_id=decoded.frame_id,timestamp_ms=decoded.timestamp_ms,width=w,height=h,tracks=tracks,detections=detections_raw,cv_status=cv_status,processing_ms=(time.monotonic()-begin)*1000,config_version=self.config.version,evidence_cutoff_ms=decoded.timestamp_ms)
        ref=self.evidence.add(packet,decoded.image)
        self.schedule_segment(packet)
        if any(0 <= packet.timestamp_ms-c.timestamp_ms <= 30000 for c in self.events.values()) or any(0 <= packet.timestamp_ms-i.timestamp_ms <= i.post_ms for i in self.incidents.values()):
            self.evidence.pin(ref.id)
        if getattr(self.detector,'provenance',None):
            snapshot=dict(self.session,id=f"{self.session['session_id']}:{self.session['run_generation']}",config=self.config.model_dump(mode='json'),detector_provenance=self.detector.provenance,source_provenance=getattr(self.source,'provenance',{}))
            self.repository.save('run',snapshot)
        if self.config.analysis_mode=='cv_only' and not self.course_review: await self.save_candidates(self.rules.process(packet))
        if self.course_review:
            self.course_review.on_frame(packet,decoded.image)
            self.course_review.schedule_people(packet,decoded.image)
        if not self.course_review and self.review and hasattr(self.review,'scenes'): self.review.scenes.observe(packet)
        if not self.course_review and self.review and hasattr(self.review,'scenes') and not (self.config.analysis_mode=='ai_cv' and self.config.person_focused): self.review.scenes.submit(packet)
        while len(self.tracker.trails)>2048: self.tracker.trails.pop(next(iter(self.tracker.trails)))
        self.retain_records(decoded.timestamp_ms)
        self.count+=1; self.last_timestamp=decoded.timestamp_ms; self.session['timestamp_ms']=decoded.timestamp_ms
        self.metrics.source_fps=self.source.fps; self.processing_seconds=getattr(self,'processing_seconds',0)+packet.processing_ms/1000; self.metrics.processed_fps=self.count/max(self.processing_seconds,1e-9); self.metrics.inference_latency_ms=inference_ms; self.metrics.active_tracks=len(tracks); self.metrics.rule_counts=len(self.events)
        digest=cv2.resize(decoded.image,(32,32)).tobytes()
        self.freeze_count=self.freeze_count+1 if digest==self.last_hash else 0; self.last_hash=digest
        self.health={'state':'ready','message':'Processing source','overloaded':packet.processing_ms>1000/self.config.target_fps,'suspected_frozen':self.freeze_count>self.config.target_fps*10,'freeze_note':'Repeated identical sampled pixels; static scene alone is not proof of freeze'}
        self.last_arrival=decoded_at
        preview=cv2.resize(decoded.image,(min(w,960),round(h*min(w,960)/w)))
        ok,jpeg=cv2.imencode('.jpg',preview,[cv2.IMWRITE_JPEG_QUALITY,70])
        if ok: await self.emit({'type':'frame','frame':packet.model_dump(mode='json'),'jpeg':base64.b64encode(jpeg).decode()})
        await self.emit({'type':'state',**self.state()})
    async def flush_segment(self):
        if self.segment_task: await asyncio.gather(self.segment_task,return_exceptions=True)
        if self.segment_start is None or self.last_timestamp is None or self.last_timestamp<=self.segment_start: return
        with self.evidence.lock:
            packets=[self.evidence.packets[r.id] for r,_ in self.evidence.records.values() if self.session and r.session_id==self.session['session_id'] and r.run_generation==self.session['run_generation'] and r.timestamp_ms==self.last_timestamp]
        if packets:
            self.schedule_segment(packets[-1],force=True)
            if self.segment_task: await asyncio.gather(self.segment_task,return_exceptions=True)

    def schedule_segment(self,packet,force=False):
        if self.segment_start is None: self.segment_start=packet.timestamp_ms
        if packet.timestamp_ms<self.segment_start: self.segment_start=packet.timestamp_ms
        if not force and packet.timestamp_ms-self.segment_start<10000: return
        start=self.segment_start;self.segment_start=packet.timestamp_ms
        if self.segment_task and not self.segment_task.done():
            self.repository.save('segment_gap',dict(id=str(uuid4()),session_id=packet.session_id,start_ms=start,end_ms=packet.timestamp_ms,reason='Segment encoder busy'))
            return
        async def encode():
            from app.evidence.clips import rolling_segment
            try: await self.source_work(rolling_segment,self,packet,start)
            except Exception as error:
                self.repository.save('segment_gap',dict(id=str(uuid4()),session_id=packet.session_id,start_ms=start,end_ms=packet.timestamp_ms,reason=str(error)[:200]))
        self.segment_task=asyncio.create_task(encode())

    async def infer(self,decoded,context):
        self.processing_stage='inference'
        tracker=self.tracker; detector=self.detector
        try: detections=await self.source_work(detector.predict,decoded.image,inference_floor(self.config))
        except Exception as error: raise InferenceFailure(type(error).__name__) from None
        if not self.current(context['session_id'],context['run_generation']): return [],[]
        detections=filtered_detections(detections,detector.labels,self.config)
        tracked=tracker.update(detections,timestamp=decoded.timestamp_ms/1000)
        h,w=decoded.image.shape[:2]; tracks=[]; raw=[]
        for i,box in enumerate(detections.xyxy):
            label=detector.labels[int(detections.class_id[i])]
            raw.append(Detection(label=label,groups=groups_for(label,self.config.class_groups),score=float(detections.confidence[i]),box=tuple(float(v) for v in box/[w,h,w,h])))
        for i,box in enumerate(tracked.xyxy):
            if tracked.tracker_id is None: continue
            id=int(tracked.tracker_id[i])
            if id<0: continue
            label=detector.labels[int(tracked.class_id[i])]
            normalized=tuple(float(v) for v in box/[w,h,w,h]); center=((normalized[0]+normalized[2])/2,normalized[3])
            tracker.trails[id].append(center)
            tracks.append(TrackObservation(**context,track_id=id,label=label,groups=groups_for(label,self.config.class_groups),score=float(tracked.confidence[i]),box=normalized,trail=list(tracker.trails[id]),timestamp_ms=decoded.timestamp_ms,frame_id=decoded.frame_id))
        return tracks,raw

    async def save_candidates(self,changed):
        triggered=[]
        rules={r.id:r for r in self.config.rules}
        for candidate in changed:
            is_new=False
            try: candidate.ai_status=self.repository.get('candidate',candidate.id)['ai_status']
            except KeyError: is_new=True
            self.events[candidate.id]=candidate; self.evidence.pin_candidate(candidate); self.repository.save('candidate',candidate)
            rule=rules.get(candidate.rule_id)
            if is_new and rule and rule.enabled and rule.ai_review and candidate.closure_reason not in ('Track expired; clearance unknown','State retention limit reached; clearance unknown'):
                triggered.append(candidate.id)
        # Submit once after saving the complete batch. Lifecycle updates never retrigger AI.
        if triggered and self.config.analysis_mode=='ai_cv' and self.config.cv_triggered_reviews and self.review and hasattr(self.review,'scenes'):
            h=self.cv_latest
            if h: self.review.scenes.submit(h,'cv_trigger',triggered_candidate_ids=triggered)

    async def cv_worker(self):
        while self.cv_pending:
            decoded,context,version=self.cv_pending; self.cv_pending=None
            if not self.current(context['session_id'],context['run_generation']): continue
            begin=time.monotonic()
            try:
                tracks,raw=await self.infer(decoded,context)
                if not self.current(context['session_id'],context['run_generation']) or version!=self.config.version: continue
                h,w=decoded.image.shape[:2]
                packet=FramePacket(**context,frame_id=decoded.frame_id,timestamp_ms=decoded.timestamp_ms,width=w,height=h,
                    tracks=tracks,detections=raw,cv_status='available',config_version=version,evidence_cutoff_ms=decoded.timestamp_ms)
                self.cv_latest=packet; self.cv_health='available'
                self.metrics.inference_latency_ms=(time.monotonic()-begin)*1000
                # Update retained metadata only for this exact source frame, never newer images.
                with self.evidence.lock:
                    for ref,_ in self.evidence.records.values():
                        if ref.session_id==packet.session_id and ref.run_generation==packet.run_generation and ref.frame_id==packet.frame_id:
                            self.evidence.packets[ref.id]=packet; self.evidence._persist(ref.id)
                await self.save_candidates(self.rules.process(packet))
                if self.review and hasattr(self.review,'scenes') and self.config.analysis_mode=='ai_cv' and self.config.person_focused:
                    self.review.scenes.submit(packet)
                await self.emit({'type':'cv','frame':packet.model_dump(mode='json')})
            except Exception as error:
                self.cv_health='error: '+type(error).__name__; self.metrics.errors+=1
                if self.review and hasattr(self.review,'scenes') and self.config.analysis_mode=='ai_cv' and self.config.person_focused:
                    h,w=decoded.image.shape[:2]
                    unavailable=FramePacket(**context,frame_id=decoded.frame_id,timestamp_ms=decoded.timestamp_ms,width=w,height=h,evidence_cutoff_ms=decoded.timestamp_ms,cv_status='unavailable')
                    self.review.scenes.submit(unavailable)
            await asyncio.sleep(0)

    async def emit(self,message):
        for queue in list(self.subscribers):
            if queue.full(): queue.get_nowait()
            queue.put_nowait(message)
    def capabilities(self):
        capabilities=[dict(c) for c in CAPABILITIES]
        if self.config.detector=='custom':
            labels=set(getattr(self.detector,'labels',{}).values())
            for capability in capabilities:
                if capability['id'] in ('person_zone_dwell','directional_crossing'):
                    capability['state']='experimental' if 'person' in labels else 'unavailable'
                    capability['reason']='Custom person model requires site validation' if 'person' in labels else 'Custom detector has no person label; retain person training classes or use a separate detector'
                elif capability['id'] in labels:
                    capability['state']='experimental'; capability['reason']='Custom class declared; requires site validation and a dedicated rule before use'
        enabled=set(getattr(self.detector,'labels',{}).values()) if self.config.enabled_labels is None else set(self.config.enabled_labels)
        for capability in capabilities:
            required='person' if capability['id'] in ('person_zone_dwell','directional_crossing') else capability['id']
            if capability['state']!='unavailable' and required in ('person','forklift','hard_hat','safety_vest','fire','smoke','spill','fall','injury') and required not in enabled:
                capability['state']='unavailable';capability['reason']='Required detector class is filtered out in Computer vision settings'
        return capabilities
    def cv_profile(self):
        declared=set(getattr(self.detector,'labels',{}).values())
        enabled=declared if self.config.enabled_labels is None else declared.intersection(self.config.enabled_labels)
        return {'enabled_labels':sorted(enabled),'groups':{name:{'configured':members,'available':sorted(set(members)&declared),'enabled':sorted(set(members)&enabled)} for name,members in self.config.class_groups.items()},
                'forklift': 'declared by custom detector; site accuracy unverified' if 'forklift' in declared else 'unavailable: current detector has no forklift class',
                'tracking':'class-separated ByteTrack; IDs are not persistent identities',
                'note':'Filtering/grouping does not improve trained recognition or introduce new classes.'}

    def state(self):
        if self.review:
            ai=self.review.state()
            self.metrics.queue_depth=ai.get('queue_depth',0); self.metrics.model_requests=ai.get('model_requests',0); self.metrics.tokens=ai.get('tokens',0)
        for id,event in self.events.items():
            try: event.ai_status=self.repository.get('candidate',id)['ai_status']
            except KeyError: pass
        if self.last_arrival: self.metrics.frame_age_ms=(time.monotonic()-self.last_arrival)*1000
        return {'session':self.session,'config':self.config.model_dump(mode='json'),'capabilities':self.capabilities(),'detector_labels':sorted(set(getattr(self.detector,'labels',self.config.custom_class_map or {}).values())),
                'cv_profile':self.cv_profile(),'rule_coverage':[{'rule_id':k[0],'track_ids':list(k[1:]),'interrupted_start_ms':v.get('interrupted_start'),'last_seen_ms':v.get('last_seen'),'clearance_unknown':bool(v.get('candidate') and v['candidate'].limitations and any('not visible' in l for l in v['candidate'].limitations))} for k,v in self.rules.states.items() if v.get('interrupted_start') is not None or (v.get('candidate') and any('not visible' in l for l in v['candidate'].limitations))],'segment_gaps':self.repository.list('segment_gap',limit=20),'metrics':self.metrics.model_dump(),'ai':self.review.state() if self.review else {'enabled':False,'available':False},'scene_reviews':self.repository.list('scene_review',limit=30,filters={'session_id':self.session['session_id'],'run_generation':self.session['run_generation']}) if self.session else [],'scene_events':self.repository.list('scene_event',limit=40,filters={'session_id':self.session['session_id'],'run_generation':self.session['run_generation']}) if self.session else [],'health':self.health | {'cv':self.cv_health,'stale':bool(self.playing and self.last_arrival and time.monotonic()-self.last_arrival>10)},'events':[c.model_dump(mode='json') for c in self.events.values()],'incidents':[i.model_dump(mode='json') for i in self.incidents.values()],'agent_runs':self.repository.list('agent_run',limit=30)}
    async def update_config(self,changes):
        merged=self.config.model_dump()|changes
        config=LabConfig.model_validate(merged)
        config=self.repository.snapshot_config(config)
        zone_ids={z.id for z in config.zones}
        if any(r.zone_id and r.zone_id not in zone_ids for r in config.rules): raise ValueError('Rule references unknown zone')
        identity=lambda c:(c.detector,c.custom_weights,c.custom_class_map,c.custom_model_id)
        tracking=lambda c:(c.enabled_labels,c.class_groups,c.tracker_activation,c.tracker_high_confidence,c.tracker_match_iou,c.tracker_min_frames,c.tracker_lost_seconds,c.target_fps)
        reset=identity(config)!=identity(self.config) or tracking(config)!=tracking(self.config)
        if (reset or config.analysis_mode!=self.config.analysis_mode) and self.cv_task:
            self.cv_pending=None;self.cv_task.cancel();await asyncio.gather(self.cv_task,return_exceptions=True)
        async with self.decode_lock:
            if reset:
                detector=self.make_detector(config) if identity(config)!=identity(self.config) else self.detector
                if identity(config)!=identity(self.config) and config.detector=='custom':
                    # Fail before committing config if shape/architecture/class metadata do not agree.
                    import numpy as np
                    await self.source_work(detector.predict,np.zeros((256,256,3),dtype=np.uint8),.5)
                if self.session:
                    old=self.session['run_generation'];self.session['run_generation']+=1
                    if self.review:self.review.cancel_generation(self.session['session_id'],old)
                self.detector=detector;self.config=config;self.reset()
            else:
                for candidate in self.rules.reconfigure(config,self.last_timestamp or 0):
                    self.events[candidate.id]=candidate;self.repository.save('candidate',candidate)
                self.config=config
        if reset:await self.emit({'type':'clear','run_generation':self.session['run_generation'] if self.session else 0})
        if self.review and hasattr(self.review,'scenes'):
            budget_only=set(changes)<= {'review_budget_usd','review_request_bound_usd','api_target_per_camera_hour'}
            if budget_only:
                # Apply new caps at the next request without cancelling an in-flight review.
                self.review.scenes.exhausted=False
            else:
                self.review.scenes.reset(preserve_cutoff=not reset)
        self.snapshot_procedures(config.version)
        self.repository.save('config',dict(config.model_dump(mode='json'),id=f'config-{config.version}'))
        if self.session:
            self.repository.save('camera_rules',dict(id=self.session['camera_id'],zones=[z.model_dump(mode='json') for z in config.zones],rules=[r.model_dump(mode='json') for r in config.rules],required_ppe=config.required_ppe))
        path=self.root/'data/config/lab.json'; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(config.model_dump_json(indent=2))
        return config
    def mark_incident(self,timestamp_ms,pre_ms,post_ms):
        if not self.session: raise ValueError('No active session')
        marker=IncidentMarker(**{k:self.session[k] for k in Context.model_fields},id=str(uuid4()),timestamp_ms=timestamp_ms,pre_ms=pre_ms,post_ms=post_ms,processing_time=datetime.now(timezone.utc).isoformat(),config_version=self.config.version)
        self.incidents[marker.id]=marker; self.retain_records(timestamp_ms); self.repository.save('incident',marker)
        scope=EvidenceScope(marker.session_id,marker.run_generation,marker.timestamp_ms,max(0,marker.timestamp_ms-marker.pre_ms))
        for ref,_ in self.evidence.frames(scope,scope.start_ms,scope.as_of_ms,16): self.evidence.pin(ref.id)
        return marker
    async def prepare_retrospective(self,incident):
        """Read a separate file decoder in explicit retrospective mode; never advance replay."""
        if not self.current(incident.session_id,incident.run_generation):
            raise ValueError('Incident belongs to another session or generation')
        description=self.sources.get(incident.source_id)
        if not description or description['kind']!='file': return self.evidence.coverage(incident)
        start=max(0,incident.timestamp_ms-incident.pre_ms); end=incident.timestamp_ms+incident.post_ms
        def read_window():
            source=FileReplaySource(description['path']); source.open()
            frames=[]
            try:
                frame=source.seek(start)
                gap=max(500,(end-start)//16); last=-gap
                while frame.timestamp_ms<=end:
                    if frame.timestamp_ms-last>=gap:
                        frames.append(frame); last=frame.timestamp_ms
                    try: frame=source.read()
                    except StopIteration: break
            finally: source.close()
            return frames
        for decoded in await self.source_work(read_window):
            h,w=decoded.image.shape[:2]
            packet=FramePacket(**{k:getattr(incident,k) for k in Context.model_fields},frame_id=decoded.frame_id,timestamp_ms=decoded.timestamp_ms,width=w,height=h,evidence_cutoff_ms=end,config_version=self.config.version)
            ref=self.evidence.add(packet,decoded.image)
        self.schedule_segment(packet); self.evidence.pin(ref.id)
        return self.evidence.coverage(incident)
