from safety_app.agents.shared.contracts import AgentOutput as BaseModel
from pydantic import Field,model_validator
from typing import Literal
from safety_app.domain.models import Finding,Observation,Investigation,ElementCheck,CausalHypothesis,CorrectiveAction
from safety_app.domain.video_safety import ReviewTriage

Specialty=Literal['applicability','vehicles','surfaces','machinery','energy','health','emergency','ppe']

class AgentFinding(Finding):
    """New answers use structured actions; legacy stored findings remain readable."""
    conclusion:str=Field(default='',description='Concise conclusion. If omitted, Python uses the condition verbatim; do not duplicate prose just to complete the form.')
    recommendations:list[str]=Field(default_factory=list,max_length=0,
        description='Legacy display field: leave empty. Put safety actions in corrective_actions and requests for missing facts in assessment missing_context. Never invent a failed control to request context.')

    @model_validator(mode='after')
    def substantive_text(self):
        if not self.condition.strip():self.condition=self.conclusion
        if not self.conclusion.strip():self.conclusion=self.condition
        if not self.conclusion.strip():raise ValueError('A finding needs a substantive condition or conclusion')
        return self

class SpecialistTask(BaseModel):
    question_id:str=Field(min_length=1,max_length=80)
    specialist:Specialty
    question:str=Field(min_length=10,max_length=700)
    observation_ids:list[str]=Field(min_length=1,max_length=12)
    phase:Literal['assessment','follow_up']='assessment'
    previous_call_id:str|None=None

class SpecialistAssessment(BaseModel):
    call_id:str=''
    question_id:str=''
    specialist:Specialty
    findings:list[AgentFinding]=Field(default_factory=list,max_length=3)
    applicability_checks:list[ElementCheck]=Field(default_factory=list,max_length=8)
    hypotheses:list[CausalHypothesis]=Field(default_factory=list,max_length=4)
    missing_context:list[str]=Field(default_factory=list,description='Missing facts and questions for the operator, including jurisdiction, event date, activity and coverage. These are evidence gaps, not corrective actions.')

class SafetyRequest(BaseModel):
    observations:list[Observation]
    context:str=''
    visibility_limits:list[str]=Field(default_factory=list)
    review_context:dict=Field(default_factory=dict)
class SafetyResult(BaseModel):
    finding_ids:list[str]=Field(default_factory=list,max_length=24,
        description='Select the IDs of validated specialist findings. Leave findings empty; Python copies the selected records without paraphrasing. Preserve every distinct finding.')
    findings:list[AgentFinding]=Field(default_factory=list)
    missing_context:list[str]=Field(default_factory=list)
    investigation:Investigation=Field(default_factory=Investigation)
    triage:ReviewTriage|None=None
