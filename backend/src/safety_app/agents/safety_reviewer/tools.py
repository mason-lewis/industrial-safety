from pydantic import BaseModel
from pydantic_ai import RunContext
from safety_app.agents.shared.deps import AgentDeps
from safety_app.agents.shared.tools import audited
from safety_app.modules.safety_review.rules import SiteRuleSet
from safety_app.modules.safety_review.sources import SourceDiscovery,EvidencePacket
from safety_app.domain.models import SourceExcerpt
from .models import SpecialistTask,SpecialistAssessment
from typing import Literal

@audited
def get_site_rules(ctx:RunContext[AgentDeps],site_id:str,topic:str)->SiteRuleSet:
    """Read matching versioned rules at the assigned site; no writes or paid calls."""
    d=ctx.deps
    if site_id!=d.scope.site_id:raise ValueError('Site outside scope')
    result=d.rules.matching(site_id,topic);d.seen_rules.update(r.id for r in result.rules);return result

get_site_rules.parallel_safe=True  # read-only lookup; may run alongside other lookups in one turn

@audited
def search_safety_sources(ctx:RunContext[AgentDeps],query:str,jurisdiction:str,topic:str,limit:int=3,broaden:bool=False,source_type:Literal['requirement','guidance','site_rule','procedure']|None=None)->SourceDiscovery:
    """Discover up to five previews. Federal jurisdiction is US-federal-OSHA. For an exact clause use only its ID as query, e.g. 1910.178(m)(2), with topic empty. Otherwise use a short focused query. Previews are not evidence. Broaden explicitly for other domains; site/document scope never expands."""
    d=ctx.deps
    return d.sources.discover(query,jurisdiction,topic,limit,d.scope.site_id,d.scope.source_ids,None if broaden else d.specialist,d.event_date,source_type)

@audited
def fetch_sources(ctx:RunContext[AgentDeps],source_ids:list[str])->EvidencePacket:
    """Fetch 1..5 exact clauses plus assigned parent/related context in a fixed byte budget. Send at most five IDs; prioritize central clauses or use separate bounded calls. Invalid counts return a rejection gap with no evidence. Oversized/missing/context/date failures are gaps; text is never truncated. Only fetched excerpts can be cited."""
    d=ctx.deps
    if not 1<=len(source_ids)<=5:
        packet=EvidencePacket(gaps=[f'Source fetch rejected: received {len(source_ids)} IDs; supply 1 to 5 IDs per call. No sources were fetched. Prioritize central clauses, or split the remaining IDs into separate calls within the remaining tool budget.'])
        d.record_source_packet(packet)
        return packet
    packet=d.sources.fetch_packet(source_ids,d.scope.site_id,d.scope.source_ids,d.source_bytes,d.event_date)
    d.record_source_packet(packet)
    return packet

class SourceUnavailable(BaseModel):
    source_id:str
    available:bool=False
    note:str='No stored excerpt has this exact ID. It cannot be cited or used to support a conclusion; use an exact ID returned by the team.'


@audited
def read_source(ctx:RunContext[AgentDeps],source_id:str,section:str|None=None)->SourceExcerpt|SourceUnavailable:
    """Read an assigned stored exact excerpt. Used by the independent challenger. Unknown IDs return available=false."""
    d=ctx.deps
    if d.scope.source_ids and source_id not in d.scope.source_ids:raise ValueError('Document outside scope')
    # A parent-document or misremembered ID is a retrieval gap, not a reason to abandon the whole review.
    try:source=d.sources.read(source_id,section,d.scope.site_id)
    except KeyError:return SourceUnavailable(source_id=source_id)
    if len(source.model_dump_json().encode())>d.source_bytes:raise ValueError('Exact excerpt exceeds evidence budget; requires bounded extraction')
    d.seen_sources.add(source.id);return source

read_source.parallel_safe=True  # read-only lookup; may run alongside other lookups in one turn

@audited
async def delegate_specialist(ctx:RunContext[AgentDeps],task:SpecialistTask)->SpecialistAssessment:
    """Boss only: assign one owned evidence question or follow-up to a named specialist. Independent questions may be delegated in the same turn: they run in parallel, and technical specialists wait for applicability. Scope/budgets/ownership/cancellation remain enforced by Python. Specialists cannot delegate."""
    if ctx.deps.delegate is None:raise ValueError('Delegation unavailable in this agent')
    return await ctx.deps.delegate(task,ctx.deps)
delegate_specialist.parallel_safe=True  # each child run has its own deps; SafetyTeam merges results after completion
