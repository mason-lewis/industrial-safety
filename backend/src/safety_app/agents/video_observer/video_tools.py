"""Search only validated observations from this analysis; no provider call."""
from pydantic_ai import RunContext, ToolReturn, BinaryContent

from safety_app.agents.shared.deps import AgentDeps
from safety_app.agents.shared.tools import audited
from safety_app.domain.video_analysis import VideoChunk
from safety_app.domain.video_safety import NormalizedBox


@audited
def search_video_memory(ctx: RunContext[AgentDeps], query: str, limit: int = 3) -> list[VideoChunk]:
    """Search this run's timestamped observations. Returns up to three whole chunks.

    Empty queries retrieve recent chunks. Catalog previews alone are not evidence.
    This reads local memory, never decodes footage or runs a model.
    """
    if ctx.deps.video_memory is None:
        raise ValueError('Video memory is unavailable in this mode')
    chunks = ctx.deps.video_memory(query, limit)
    for chunk in chunks:
        for observation in chunk.observations:
            ctx.deps.seen_frames.update(observation.evidence_refs)
        from safety_app.agents.shared.evidence_refs import register_frame
        for frame in chunk.frames:
            if frame.id in ctx.deps.seen_frames:register_frame(ctx.deps,frame)
    return chunks


@audited
def get_safety_checklist(ctx: RunContext[AgentDeps]) -> dict:
    """Read compact visual checks and operator-supplied PPE items. No law search or inference."""
    if not ctx.deps.safety_guide:raise ValueError('Safety screening is not enabled for this stage')
    return ctx.deps.safety_guide


def valid_box(box):
    import math
    if len(box)!=4:raise ValueError('Detail box must contain exactly four XYWH numbers')
    x,y,w,h=box
    if any(not math.isfinite(v) for v in box) or min(x,y)<0 or min(w,h)<=0 or x+w>1 or y+h>1:
        raise ValueError('Detail box must be finite normalized XYWH inside the image')


@audited
def inspect_safety_detail(ctx: RunContext[AgentDeps], frame_id: str, box: NormalizedBox) -> ToolReturn:
    """Zoom an assigned still using normalized XYWH; one crop per stage by default.

    Returns a bounded JPEG and parent-frame reference. No detection, video decoding,
    law search or delegation. Image coordinates do not establish physical distances.
    """
    import cv2,numpy as np
    d=ctx.deps
    from safety_app.agents.shared.evidence_refs import resolve_frame
    frame_id=resolve_frame(d,frame_id)
    if frame_id not in d.detail_frame_ids or d.detail_calls>=d.detail_limit:
        raise ValueError('Frame outside detail scope or detail allowance exhausted')
    valid_box(box);d.detail_calls+=1
    from safety_app.infrastructure.frame_detail import original_crop
    metadata,jpeg=original_crop(d.media,frame_id,box)
    return ToolReturn(return_value=metadata,content=[str(metadata),BinaryContent(data=jpeg,media_type='image/jpeg')])
