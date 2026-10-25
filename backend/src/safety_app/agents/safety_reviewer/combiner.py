"""Run-level team combiner: one text-only request that turns reviewed findings into a manager brief."""
from pathlib import Path
from pydantic import Field
from safety_app.agents.shared.deps import AgentDeps
from safety_app.agents.shared.factory import build
from safety_app.domain.base import StrictModel


class TeamBrief(StrictModel):
    summary: str = Field(min_length=1, max_length=700, description='Three or four plain sentences for a site manager, using only supplied facts.')
    priority_case_ids: list[str] = Field(default_factory=list, max_length=5, description='Exact supplied case IDs, most urgent first.')
    actions: list[str] = Field(default_factory=list, max_length=5, description='Manager actions taken only from supplied corrective actions or recommendations.')
    confidence_note: str = Field(default='', max_length=300, description='One sentence on coverage and unfinished or unclear checks.')


def create(model, deps: AgentDeps):
    return build('team_combiner', model, deps, TeamBrief, Path(__file__).with_name('combiner_prompt.md'), [])
