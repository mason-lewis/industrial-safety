"""Video-chat observation stays in the existing observer folder."""
from pathlib import Path
from safety_app.agents.safety_reviewer.lab_agent import create as build

def create(model,context,output_type,tools=()):
    return build(model,context,output_type,tools,prompt=Path(__file__).with_name('video_chat_prompt.md'))
