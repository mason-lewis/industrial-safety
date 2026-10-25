"""Prepare immutable clip packets once; compare A/B with explicit opt-in calls.

Preparation is local; --with-cv explicitly invokes real detector inference.
--mock tests plumbing, never accuracy. Paid runs require both consent flags.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
from app.agents.scenes import make_packet, analyze, EventMemory, ctx
from app.agents.settings import ModelConfig, BudgetExceeded
from app.evidence.store import EvidenceStore
from app.storage.repository import Repository
from app.models.contracts import LabConfig, FramePacket, ScenePacket, SceneReview, Detection, TrackObservation
from app.video.sources import FileReplaySource
from app.evals.events import evaluate


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(args):
    out=args.cache.resolve()
    if (out/'manifest.json').exists(): raise ValueError('Cache already exists; choose a new directory')
    out.mkdir(parents=True,exist_ok=True)
    config=LabConfig.model_validate_json((ROOT/'data/config/lab.json').read_text())
    config.analysis_mode='ai_cv'; config.scene_review_interval_s=args.interval
    config.scene_window_s=args.window; config.scene_sample_s=args.sample; config.scene_extra_images=args.extra
    config.scene_max_images=args.images
    store=EvidenceStore(out/'evidence',max_bytes=512*1024*1024)
    repo=Repository(out/'records.sqlite')
    repo.save('procedures',{'id':f'config-{config.version}','documents':{p.name:p.read_text()[:12000] for p in (ROOT/'data/procedures').glob('*') if p.suffix in ('.md','.txt') and p.name!='README.md'}})
    context=dict(source_id=digest(args.clip),camera_id='comparison-camera',session_id=str(uuid4()),run_generation=0,schema_version='1.0')
    detector=tracker=rules=None
    if args.with_cv:
        from app.cv.detector import RFDETRDetector, ByteTrackAdapter
        from app.rules.engine import RuleEngine
        detector=RFDETRDetector(config.detector); tracker=ByteTrackAdapter(frame_rate=1/args.sample); rules=RuleEngine(config)
    source=FileReplaySource(args.clip); source.open(); last_sample=None; last_review=None; packets=[]; cv_errors=[]
    try:
        while True:
            try: decoded=source.read()
            except StopIteration: break
            if decoded.timestamp_ms>args.seconds*1000: break
            if last_sample is not None and decoded.timestamp_ms-last_sample<args.sample*1000: continue
            last_sample=decoded.timestamp_ms; h,w=decoded.image.shape[:2]
            packet=FramePacket(**context,frame_id=decoded.frame_id,timestamp_ms=decoded.timestamp_ms,width=w,height=h,
                               config_version=config.version,evidence_cutoff_ms=decoded.timestamp_ms)
            changed=[]
            if detector:
                try:
                    result=detector.predict(decoded.image,config.confidence)
                    for i,box in enumerate(result.xyxy):
                        packet.detections.append(Detection(label=detector.labels[int(result.class_id[i])],score=float(result.confidence[i]),box=tuple(float(v) for v in box/[w,h,w,h])))
                    tracked=tracker.update(result,timestamp=decoded.timestamp_ms/1000)
                    for i,box in enumerate(tracked.xyxy):
                        if tracked.tracker_id is not None and tracked.tracker_id[i]>=0:
                            packet.tracks.append(TrackObservation(**context,track_id=int(tracked.tracker_id[i]),label=detector.labels[int(tracked.class_id[i])],score=float(tracked.confidence[i]),box=tuple(float(v) for v in box/[w,h,w,h]),timestamp_ms=decoded.timestamp_ms,frame_id=decoded.frame_id))
                    packet.cv_status='available'; changed=rules.process(packet)
                    for candidate in changed: repo.save('candidate',candidate)
                except Exception as error:
                    packet.cv_status='error'; cv_errors.append({'timestamp_ms':decoded.timestamp_ms,'error':type(error).__name__})
            ref=store.add(packet,decoded.image); store.pin(ref.id)
            if last_review is None or decoded.timestamp_ms-last_review>=args.interval*1000:
                review_packet=make_packet(store,repo,packet,config); packets.append(review_packet.model_dump(mode='json')); last_review=decoded.timestamp_ms
            elif changed:
                packets.append(make_packet(store,repo,packet,config,'cv_trigger').model_dump(mode='json'))
    finally: source.close()
    hashes={id:digest(store.root/(id+'.jpg')) for id in store.records}
    manifest={'version':1,'clip':args.clip.name,'clip_sha256':context['source_id'],'packets':packets,'images_sha256':hashes,
              'model':'gpt-6-luna','reasoning_effort':args.reasoning,'extra_policy':'Frozen extra_refs and crops only; identical available images in every arm',
              'schema':'SceneResult','config':config.model_dump(mode='json'),'cv_errors':cv_errors,
              'cv_provenance':getattr(detector,'provenance',None),'duration_ms':last_sample or 0,
              'labels':[],'coverage':[{'duration_ms':last_sample or 0}],
              'limitations':['Unlabeled footage does not establish accuracy. Add normal-footage and incident labels.',
                             'Cached CV is synchronous at sampled times; live CV can have more missing/stale data.']}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(f'Cached {len(packets)} packets / {len(hashes)} images in {out}; no model calls')


async def compare(args):
    document=json.loads((args.cache/'manifest.json').read_text())
    store=EvidenceStore(args.cache/'evidence',max_bytes=512*1024*1024)
    for id,expected in document['images_sha256'].items():
        if digest(store.root/(id+'.jpg'))!=expected: raise ValueError('Cached image changed: '+id)
    config=ModelConfig(default_model=document['model'],reasoning_effort=document['reasoning_effort'])
    if not args.mock and not (args.allow_billable and args.allow_hosted_image): raise ValueError('Real calls require --allow-billable --allow-hosted-image')
    if not args.mock:
        from pydantic_ai import models
        models.ALLOW_MODEL_REQUESTS=True
    factory=None
    if args.mock:
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.messages import ModelResponse, ToolCallPart
        from pydantic_ai.usage import RequestUsage
        def mock_model(messages,info):
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,{'findings':[],'no_finding':True,'summary':'Mock plumbing; no accuracy evidence','limitations':['Mocked output']})],usage=RequestUsage(input_tokens=100,output_tokens=50))
        factory=lambda name:FunctionModel(mock_model)
    out=args.output or args.cache/('mock-results' if args.mock else 'results-'+str(uuid4()))
    out.mkdir(parents=True,exist_ok=True)
    repo=Repository(out/'results.sqlite'); arms=['ai_scene','ai_cv']+(['ai_cv_with_triggers'] if args.include_cv_triggers else [])
    summary={}; total_requests=0; total_tokens=0
    def reserve():
        nonlocal total_requests
        if total_requests>=args.request_limit or total_tokens>=args.token_limit: raise BudgetExceeded('Comparison budget exhausted')
        total_requests+=1
    for arm in arms:
        memory=EventMemory(repo,arm); reviews=[]
        for value in document['packets']:
            packet=ScenePacket.model_validate(value)
            if arm!='ai_cv_with_triggers' and packet.trigger!='scheduled': continue
            if args.max_windows and len(reviews)>=args.max_windows: break
            review=SceneReview(**ctx(packet),packet=packet,analysis_mode='ai_scene' if arm=='ai_scene' else 'ai_cv',
                              model=config.default_model,settings=config.settings(),memory=memory.history(packet))
            await analyze(store,review,config,factory,reserve_request=reserve)
            total_tokens+=review.usage.get('total_tokens',0)
            if review.status=='reviewed': review.event_updates=memory.apply(review)
            repo.save('scene_review',review); reviews.append(review.model_dump(mode='json'))
        predictions=[]
        for event in repo.list('scene_event',filters={'namespace':arm}):
            supporting=[r for r in reviews if any(u['event_id']==event['id'] for u in r['event_updates'])]
            predictions.append({**{k:event[k] for k in ('source_id','camera_id','session_id')},'id':event['id'],
                'event_type':event['category'],'start_ms':event['first_ms'],'end_ms':event['last_ms'],
                'detected_at_ms':min(r['packet']['cutoff_ms'] for r in supporting)})
        scored=evaluate({**document,'predictions':predictions,'mode':arm,'data_kind':'mock_plumbing' if args.mock else 'human_labeled_video'}) if document.get('labels') else None
        costs=[r['cost_estimate'] for r in reviews]
        summary[arm]={'reviews':reviews,'predictions':predictions,'evaluation':scored,
            'inconclusive_reviews':sum(r['status']!='reviewed' or bool((r['result'] or {}).get('inconclusive')) for r in reviews),
            'requests':sum(r['usage'].get('requests',0) for r in reviews),
            'tokens':sum(r['usage'].get('total_tokens',0) for r in reviews),
            'cost_estimate':sum(costs) if costs and all(c is not None for c in costs) else None,
            'mean_latency_ms':sum(r['elapsed_ms'] for r in reviews)/len(reviews) if reviews else None}
        (out/(arm+'.json')).write_text(json.dumps(summary[arm],indent=2))
    (out/'summary.json').write_text(json.dumps({'arms':{k:{a:b for a,b in v.items() if a!='reviews'} for k,v in summary.items()},
        'mock':args.mock,'controlled':True,'manifest_sha256':digest(args.cache/'manifest.json'),
        'accuracy_verified':False,'note':'Human labels and representative coverage required. A/B uses separate answers and memory. Whole-video future-aware baselines are retrospective and must be marked uncontrolled when inputs/model are unknown.'},indent=2))
    print(f'Comparison saved to {out}; {total_requests} requests ({"mock" if args.mock else "billable"})')


def main():
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare'); prep.add_argument('clip',type=Path); prep.add_argument('--cache',type=Path,required=True)
    prep.add_argument('--seconds',type=float,default=30); prep.add_argument('--with-cv',action='store_true')
    prep.add_argument('--interval',type=float,default=5); prep.add_argument('--window',type=float,default=15)
    prep.add_argument('--sample',type=float,default=1); prep.add_argument('--images',type=int,default=15)
    prep.add_argument('--extra',type=int,default=0); prep.add_argument('--reasoning',choices=['none','low','medium','high','xhigh','max'])
    run=sub.add_parser('run'); run.add_argument('--cache',type=Path,required=True);run.add_argument('--output',type=Path)
    run.add_argument('--mock',action='store_true');run.add_argument('--allow-billable',action='store_true');run.add_argument('--allow-hosted-image',action='store_true')
    run.add_argument('--max-windows',type=int,default=0);run.add_argument('--include-cv-triggers',action='store_true')
    run.add_argument('--request-limit',type=int,default=100);run.add_argument('--token-limit',type=int,default=200000)
    args=parser.parse_args()
    if args.command=='prepare' and args.seconds<=0: parser.error('--seconds must be positive')
    if args.command=='run' and (args.request_limit<=0 or args.token_limit<=0 or args.max_windows<0): parser.error('Comparison limits must be positive; max-windows nonnegative')
    if args.command=='prepare': prepare(args)
    else: asyncio.run(compare(args))

if __name__=='__main__': main()
