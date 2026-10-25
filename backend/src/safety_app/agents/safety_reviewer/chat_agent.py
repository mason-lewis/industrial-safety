from pathlib import Path
import json
from pydantic_ai import RunContext
from safety_app.agents.shared.deps import AgentDeps
from safety_app.agents.shared.factory import build
from safety_app.agents.shared.tools import audited
from safety_app.modules.safety_review.catalog import PROFILES
from .tools import search_safety_sources,fetch_sources,get_site_rules
from .chat_models import ChatTask,ChatAssessment,ChatAnswer

@audited
async def delegate_chat_specialist(ctx:RunContext[AgentDeps],task:ChatTask)->ChatAssessment:
    """Assign a bounded safety question to a named specialist; explicit follow-ups only."""
    if ctx.deps.delegate is None:raise ValueError('Delegation unavailable')
    return await ctx.deps.delegate(task,ctx.deps)

def create(model,deps):
    return build('safety_reviewer',model,deps,ChatAnswer,Path(__file__).with_name('chat_prompt.md'),[delegate_chat_specialist],instructions='\nOwnership: '+json.dumps(PROFILES),tool_timeout=deps.budgets.config['limits']['agent_seconds']+5)

def create_specialist(model,deps,specialist):
    if specialist not in PROFILES:raise ValueError('Unknown specialist')
    deps.specialist=specialist
    return build('specialist_'+specialist,model,deps,ChatAssessment,Path(__file__).with_name('chat_specialist_prompt.md'),[search_safety_sources,fetch_sources,get_site_rules],instructions='\nAssigned domain: '+specialist+'. '+PROFILES[specialist])
