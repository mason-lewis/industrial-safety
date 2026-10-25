from pathlib import Path
from pydantic_ai import Agent
from safety_app.agents.shared.deps import AgentDeps
from safety_app.agents.shared.factory import build
from .models import ChallengeResult
from .tools import get_evidence, get_site_rules, read_source
def create(model,deps:AgentDeps):
    tools=[get_evidence, get_site_rules, read_source]
    if deps.workflow_kind=='video_safety_review' and deps.seen_frames:
        # One original-pixel close-up of an assigned frame, only while the six-image cap has room.
        from safety_app.agents.video_observer.video_tools import inspect_safety_detail
        deps.detail_frame_ids=set(deps.seen_frames)
        deps.detail_limit=min(1,max(0,deps.budgets.config['limits']['frames']-len(deps.seen_frames)))
        if deps.detail_limit:tools.append(inspect_safety_detail)
    return build('challenge_reviewer',model,deps,ChallengeResult,Path(__file__).with_name('prompt.md'),tools)
