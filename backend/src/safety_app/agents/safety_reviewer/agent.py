from pathlib import Path
from safety_app.agents.shared.deps import AgentDeps
from safety_app.agents.shared.factory import build
from .models import SafetyResult
from .tools import delegate_specialist
from safety_app.modules.safety_review.catalog import PROFILES
import json

def create(model,deps:AgentDeps):
    return build('safety_reviewer',model,deps,SafetyResult,Path(__file__).with_name('prompt.md'),[delegate_specialist],instructions='\nSpecialist ownership registry: '+json.dumps(PROFILES),tool_timeout=deps.budgets.config['limits']['agent_seconds']+5)
