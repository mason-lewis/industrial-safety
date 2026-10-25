"""Read-only person lookup shared by the observer, review team and challenger."""
from pydantic_ai import RunContext
from safety_app.agents.shared.deps import AgentDeps
from safety_app.agents.shared.evidence_refs import resolve_frame
from safety_app.agents.shared.tools import audited
from safety_app.domain.people import PersonFrame


@audited
def get_person_detections(ctx:RunContext[AgentDeps],frame_id:str)->PersonFrame:
    """Read saved person boxes, confidence, timestamp and detector provenance for ONE assigned frame. No new inference or images. Empty/unavailable results never prove absence. IDs are frame-local, not identities. Cite the original frame as visual evidence."""
    d=ctx.deps;frame_id=resolve_frame(d,frame_id)
    if frame_id not in d.seen_frames or d.person_detections is None:
        raise ValueError('Person lookup requires an assigned retained frame')
    result=d.person_detections(frame_id)
    d.interval(result.media_id,result.timestamp_ms,result.timestamp_ms)
    return result


get_person_detections.parallel_safe=True  # read-only lookup; may run alongside other lookups in one turn

def person_summary(deps):
    if deps.person_detections is None:return None
    rows=[]
    for frame_id in sorted(deps.seen_frames)[:6]:
        result=deps.person_detections(frame_id)
        deps.interval(result.media_id,result.timestamp_ms,result.timestamp_ms)
        rows.append({'frame_id':frame_id,'timestamp_ms':result.timestamp_ms,'status':result.status,
                     'detected_people':len(result.people) if result.status=='available' else None,
                     'omitted_count':result.omitted_count,'confidence_threshold':result.confidence_threshold})
    return {'person_detection':rows,'notice':'Supplemental pretrained person detections only. Use get_person_detections(frame_id) for boxes and provenance. Verify against pixels; missing detections are not absence. No identities or continuous tracks.'} if rows else None
