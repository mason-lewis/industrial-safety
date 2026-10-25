from pydantic_ai import RunContext,ToolReturn,BinaryContent
from safety_app.agents.shared.deps import AgentDeps
from safety_app.agents.shared.tools import audited
from safety_app.domain.models import EvidenceBundle,FrameBatch,CameraProfile
from safety_app.agents.shared.evidence_refs import register_frame
@audited
def get_evidence(ctx:RunContext[AgentDeps],media_id:str,start_ms:int,end_ms:int)->EvidenceBundle:
    """Read evidence metadata for an assigned interval. Returns a bundle with gaps. No writes. Raises ValueError for scope or interval errors; KeyError for missing media."""
    d=ctx.deps;d.interval(media_id,start_ms,end_ms)
    bundle=d.media.evidence(media_id,start_ms,end_ms);d.seen_frames.update(f.id for f in bundle.frame_refs)
    for frame in bundle.frame_refs:register_frame(d,frame)
    return bundle
@audited
def sample_frames(ctx:RunContext[AgentDeps],media_id:str,start_ms:int,end_ms:int,count:int=6)->ToolReturn:
    """Read 1..6 scoped frames and attach JPEG bytes to the model. Returns FrameBatch plus timestamped images. No inference. Raises ValueError for bounds; missing frames appear as gaps."""
    d=ctx.deps;d.interval(media_id,start_ms,end_ms)
    batch=d.media.frames(media_id,start_ms,end_ms,count);d.seen_frames.update(f.id for f in batch.frames)
    content=[]
    for f in batch.frames:
        register_frame(d,f)
        content.extend([f.model_dump_json(),BinaryContent(data=d.media.model_image(f.id),media_type='image/jpeg')])
    return ToolReturn(return_value=batch.model_dump(mode='json'),content=content)
@audited
def get_camera_context(ctx:RunContext[AgentDeps],camera_id:str)->CameraProfile:
    """Read one scoped camera profile. Returns zones, calibration and limits. No writes. Raises ValueError for scope; KeyError for missing profile."""
    if camera_id not in ctx.deps.scope.camera_ids:raise ValueError('Camera outside scope')
    return ctx.deps.camera_context(camera_id)
