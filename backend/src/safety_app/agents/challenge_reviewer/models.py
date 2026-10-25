from safety_app.agents.shared.contracts import AgentOutput as BaseModel
from pydantic import Field
from safety_app.domain.models import Finding,Investigation
from safety_app.domain.video_safety import ReviewTriage
class ChallengeRequest(BaseModel):
    findings:list[Finding]
    investigation:Investigation=Field(default_factory=Investigation)
    triage:ReviewTriage|None=None
class ChallengeIssue(BaseModel):
    finding_id:str
    problem:str
    alternative:str
class CausalChallengeIssue(BaseModel):
    hypothesis_id:str
    problem:str
    alternative:str
class ChallengeResult(BaseModel):
    issues:list[ChallengeIssue]=Field(default_factory=list)
    checked_finding_ids:list[str]=Field(default_factory=list)
    limitations:list[str]=Field(default_factory=list)
    checked_hypothesis_ids:list[str]=Field(default_factory=list)
    causal_issues:list[CausalChallengeIssue]=Field(default_factory=list)
    triage_supported:bool|None=None
    triage_explanation:str=Field(default='',max_length=700)
