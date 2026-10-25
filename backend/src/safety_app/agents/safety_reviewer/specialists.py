"""Eight bounded PydanticAI profiles inside the safety-review boundary."""
from pathlib import Path
from safety_app.agents.shared.factory import build
from safety_app.modules.safety_review.catalog import PROFILES
from .models import SpecialistAssessment
from .tools import search_safety_sources,fetch_sources,get_site_rules

def create(model,deps,specialist):
    if specialist not in PROFILES:raise ValueError('Unknown specialist')
    deps.specialist=specialist
    return build('specialist_'+specialist,model,deps,SpecialistAssessment,Path(__file__).with_name('specialist_prompt.md'),[search_safety_sources,fetch_sources,get_site_rules],instructions='\nAssigned domain: '+specialist+'. '+PROFILES[specialist])
