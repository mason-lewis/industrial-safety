from pathlib import Path
from pydantic_ai import Agent
from safety_app.agents.shared.deps import AgentDeps
from safety_app.agents.shared.factory import build
from .models import ObserverResult
from .tools import get_evidence, sample_frames, get_camera_context
def create(model,deps:AgentDeps):
    return build('video_observer',model,deps,ObserverResult,Path(__file__).with_name('prompt.md'),[get_evidence, sample_frames, get_camera_context])
