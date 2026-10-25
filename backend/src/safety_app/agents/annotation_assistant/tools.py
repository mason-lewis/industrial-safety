from pydantic_ai import RunContext,ToolReturn,BinaryContent
from safety_app.agents.shared.deps import AgentDeps
from safety_app.agents.shared.tools import audited
from safety_app.domain.models import ClassRegistry,BoxProposalResult,FrameBatch,FrameRef
@audited
def get_label_frames(ctx:RunContext[AgentDeps],frame_ids:list[str])->ToolReturn:
    """Read up to 6 assigned label frames. Returns FrameBatch with JPEG content. No label writes. Raises ValueError for scope or count, KeyError for missing frame."""
    d=ctx.deps
    if not 1<=len(frame_ids)<=6 or not set(frame_ids)<=d.scope.frame_ids:raise ValueError('Label frames outside scope or count limit')
    frames=[FrameRef.model_validate(d.media.db.get('frame',id)) for id in frame_ids];content=[]
    for f in frames:content.extend([f.model_dump_json(),BinaryContent(data=d.media.model_image(f.id),media_type='image/jpeg')])
    d.seen_frames.update(frame_ids);return ToolReturn(return_value=FrameBatch(frames=frames).model_dump(mode='json'),content=content)
@audited
def get_class_definitions(ctx:RunContext[AgentDeps],class_registry_version:str)->ClassRegistry:
    """Read assigned class version. Returns names, definitions, examples and exclusions. No writes. Raises ValueError for a different version."""
    if class_registry_version!=ctx.deps.scope.class_registry_version:raise ValueError('Class version outside scope')
    return ctx.deps.classes(class_registry_version)
@audited
def propose_boxes(ctx:RunContext[AgentDeps],frame_id:str,class_ids:list[int])->BoxProposalResult:
    """Run the local detector for assigned classes on one assigned frame. Returns unapproved drafts, scores and warnings. No remote inference or approval. Raises ValueError for scope/classes; detector errors propagate."""
    d=ctx.deps
    if frame_id not in d.scope.frame_ids:raise ValueError('Frame outside label job')
    known={c.class_id for c in d.classes(d.scope.class_registry_version).classes}
    if not class_ids or not set(class_ids)<=known:raise ValueError('Unknown or empty class IDs')
    return BoxProposalResult.model_validate(d.proposals(frame_id,class_ids))
