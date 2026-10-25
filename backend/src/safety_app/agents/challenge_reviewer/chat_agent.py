from pathlib import Path
from safety_app.agents.shared.factory import build
from safety_app.agents.safety_reviewer.chat_models import ChatChallenge
from .tools import read_source

def create(model,deps):
    return build('challenge_reviewer',model,deps,ChatChallenge,Path(__file__).with_name('chat_prompt.md'),[read_source])
