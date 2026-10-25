from pathlib import Path
import json

from safety_app.agents.shared.factory import build
from safety_app.agents.video_observer.video_models import VideoObservationResult, VideoPlan
from safety_app.agents.video_observer.video_tools import search_video_memory, inspect_safety_detail

HERE = Path(__file__).parent


def observer(model, deps):
    return build('video_observer', model, deps, VideoObservationResult,
                 HERE / 'video_observation_prompt.md',
                 [inspect_safety_detail] if deps.safety_guide and deps.detail_limit else [],
                 instructions='\nCoordinator screening checklist (already supplied; no retrieval needed):\n'+json.dumps(deps.safety_guide) if deps.safety_guide else '',
                 mode='recorded_video_observer')


def planner(model, deps):
    return build('video_observer', model, deps, VideoPlan,
                 HERE / 'video_plan_prompt.md', [search_video_memory], mode='recorded_video_planner')
