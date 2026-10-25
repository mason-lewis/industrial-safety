from pathlib import Path
from pydantic_ai import Agent
from safety_app.agents.shared.deps import AgentDeps
from safety_app.agents.shared.factory import build
from .models import AnnotationResult,ImageReviewResult
from .tools import get_label_frames, get_class_definitions, propose_boxes
def create(model,deps:AgentDeps):
    return build('annotation_assistant',model,deps,AnnotationResult,Path(__file__).with_name('prompt.md'),[get_label_frames, get_class_definitions, propose_boxes])

def create_image_reviewer(model,deps:AgentDeps,tools=None):
    return build('annotation_assistant',model,deps,ImageReviewResult,Path(__file__).with_name('image_prompt.md'),tools or [])
