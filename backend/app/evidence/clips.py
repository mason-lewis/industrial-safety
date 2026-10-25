"""Source-time video evidence and sampled rolling segments; originals are preserved."""
from pathlib import Path
from uuid import uuid4
import json
import hashlib
from app.video.ffmpeg_media import clip, retained_clip


def event_clip(pipeline, event):
    session=pipeline.repository.get('session',event['session_id'])
    config=session['config']
    if event.get('config_version') is not None:
        try: config=pipeline.repository.get('config',f"config-{event['config_version']}")
        except KeyError: pass
    first=event.get('first_ms',event.get('timestamp_ms',0));last=event.get('last_ms',first)
    start=max(0,first-round(config.get('evidence_pre_s',10)*1000))
    end=last+round(config.get('evidence_post_s',10)*1000)
    end=min(end,start+240000)
    source=pipeline.sources.get(event['source_id'])
    if source and source['kind']=='file':
        duration=session.get('duration_ms')
        if duration: end=min(end,duration)
        refs=None
    else:
        with pipeline.evidence.lock:
            refs=sorted([r for r,_ in pipeline.evidence.records.values() if r.session_id==event['session_id'] and
                         r.run_generation==event['run_generation'] and start<=r.timestamp_ms<=end],key=lambda r:r.timestamp_ms)
        if len(refs)<2: raise ValueError('Insufficient retained footage; post-event coverage may still be arriving')
        end=refs[-1].timestamp_ms;start=refs[0].timestamp_ms
    digest=hashlib.sha256(json.dumps([event['id'],start,end],sort_keys=True).encode()).hexdigest()
    folder=pipeline.root/'data/artifacts/clips';folder.mkdir(parents=True,exist_ok=True)
    output=folder/(digest+'.mp4')
    if output.exists(): return output
    temp=folder/(str(uuid4())+'.mp4')
    try:
        if refs is None: clip(source['path'],temp,start,end)
        else: retained_clip(pipeline.evidence,refs,temp)
        temp.replace(output)
    finally: temp.unlink(missing_ok=True)
    metadata={'id':digest,'event_id':event['id'],'source_id':event['source_id'],'camera_id':event['camera_id'],
              'source_start_ms':start,'source_end_ms':end,'requested_post_end_ms':last+round(config.get('evidence_post_s',10)*1000),
              'sampled':refs is not None,'timestamp_map_ms':[r.timestamp_ms for r in refs] if refs else [],
              'limitations':['Clip playback starts at zero; add source_start_ms for source time.']+(['Frames between retained samples are unobserved.'] if refs else [])}
    output.with_suffix('.json').write_text(json.dumps(metadata,indent=2));pipeline.repository.save('evidence_clip',metadata)
    return output


def rolling_segment(pipeline, packet, start_ms):
    with pipeline.evidence.lock:
        refs=sorted([r for r,_ in pipeline.evidence.records.values() if r.session_id==packet.session_id and
                     r.run_generation==packet.run_generation and start_ms<=r.timestamp_ms<=packet.timestamp_ms],key=lambda r:r.timestamp_ms)
    folder=pipeline.root/'data/artifacts/segments';folder.mkdir(parents=True,exist_ok=True)
    if sum(p.stat().st_size for p in folder.glob('*.mp4'))>1024**3:
        raise ValueError('Rolling segment storage cap reached; existing evidence preserved')
    id=str(uuid4());output=folder/(id+'.mp4')
    try: retained_clip(pipeline.evidence,refs,output)
    except BaseException: output.unlink(missing_ok=True);raise
    record={k:getattr(packet,k) for k in ('source_id','camera_id','session_id','run_generation')}
    record.update(id=id,start_ms=refs[0].timestamp_ms,end_ms=refs[-1].timestamp_ms,timestamps_ms=[r.timestamp_ms for r in refs],
                  sampled=True,storage_bytes=output.stat().st_size,path=str(output))
    pipeline.repository.save('rolling_segment',record)
    output.with_suffix('.json').write_text(json.dumps(record,indent=2))
    return record
