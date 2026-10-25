from safety_app.domain.base import StrictModel as BaseModel
"""Shared records. Source positions use milliseconds; capture time uses UTC."""
from datetime import datetime, timezone, date
from typing import Literal
from uuid import uuid4
from pydantic import  Field, ConfigDict, model_validator

def uid(): return str(uuid4())
def utc(): return datetime.now(timezone.utc).isoformat()
class BaseRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(default_factory=uid)
class Interval(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    @model_validator(mode='after')
    def ordered(self):
        if self.end_ms < self.start_ms: raise ValueError('End precedes start')
        return self
class CameraProfile(BaseRecord):
    camera_id: str
    site_id: str = 'local'
    view_type: Literal['fixed','driver_pov'] = 'fixed'
    zones: list[dict] = Field(default_factory=list)
    calibration_version: str | None = None
    limitations: list[str] = Field(default_factory=lambda:['Image coordinates only. Physical distance and speed are unknown.'])
class MediaRef(BaseRecord, Interval):
    media_id: str
    source_id: str
    camera_id: str
    content_hash: str
    storage_key: str
    capture_time_utc: str | None = None
class FrameRef(BaseRecord):
    media_id: str
    frame_id: str
    timestamp_ms: int = Field(ge=0)
    content_hash: str
    image_url: str
class FrameBatch(BaseModel):
    frames: list[FrameRef] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
class EvidenceBundle(BaseModel):
    media_ref: MediaRef
    observations: list['Observation'] = Field(default_factory=list)
    frame_refs: list[FrameRef] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
class Detection(BaseRecord):
    frame_id: str
    timestamp_ms: int = Field(ge=0)
    class_id: int
    bbox: tuple[float,float,float,float]
    score: float = Field(ge=0,le=1)
    detector_version: str
class Track(BaseRecord):
    track_id: str
    camera_id: str
    detections: list[Detection]
    tracker_version: str
class Observation(BaseRecord, Interval):
    media_id: str
    description: str
    evidence_refs: list[str] = Field(min_length=1)
    method: Literal['cv','ai','human']
    uncertainty: list[str] = Field(default_factory=list)
class CandidateEvent(BaseRecord, Interval):
    site_id: str = 'local'
    camera_id: str
    event_type: str
    observation_ids: list[str] = Field(default_factory=list)
    media_ids: list[str] = Field(default_factory=list)
    producer_version: str
    track_ids:list[str]=Field(default_factory=list)
    status: str = 'candidate'
    legacy_id: str | None = None
class SourceExcerpt(BaseRecord):
    source_id: str
    url: str | None = None
    title: str
    section: str
    text: str
    retrieved_at: str = Field(default_factory=utc)
    effective_date: str | None = None
    source_type: Literal['requirement','guidance','site_rule','procedure']
    version: str
    jurisdiction: str = 'site'
    site_id: str = 'local'
    specialist: str | None = None
    clause: str | None = None
    parent_ids: list[str] = Field(default_factory=list)
    related_ids: list[str] = Field(default_factory=list)
    effective_until: str | None = None
    topics: list[str] = Field(default_factory=list)
    context_complete: bool = False
class SiteRule(BaseRecord):
    rule_id: str
    version: str
    site_id: str
    description: str
    thresholds: dict = Field(default_factory=dict)
    units: str = 'normalized_image'
    source_refs: list[str] = Field(default_factory=list)
    applicability: str
class Finding(BaseRecord):
    event_id: str | None = None
    review_id: str
    conclusion: str
    evidence_refs: list[str] = Field(min_length=1)
    rule_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    status: Literal['draft','challenged','reviewed'] = 'draft'
    classification: Literal['supported_noncompliance','potential_violation','site_infraction','general_hazard','insufficient_evidence'] = 'insufficient_evidence'
    question_id: str | None = None
    specialist: str | None = None
    condition: str = ''
    exact_clause: str | None = None
    element_checks: list['ElementCheck'] = Field(default_factory=list)
    corrective_actions: list['CorrectiveAction'] = Field(default_factory=list)

class ElementCheck(BaseModel):
    element: str = Field(min_length=1,max_length=500)
    status: Literal['supported','contradicted','unknown']
    evidence_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    rule_refs: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1,max_length=1000)

class CorrectiveAction(BaseModel):
    action: str = Field(min_length=1,max_length=700)
    failed_control: str = Field(min_length=1,max_length=500)
    verification: str = Field(min_length=1,max_length=500)
    evidence_refs: list[str] = Field(min_length=1)

class CausalHypothesis(BaseModel):
    id: str = Field(min_length=1,max_length=100)
    explanation: str = Field(min_length=1,max_length=700)
    status: Literal['supported','contradicted','unresolved']
    supporting_refs: list[str] = Field(default_factory=list)
    contradicting_refs: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    distinguishing_test: str = Field(min_length=1,max_length=700)

class TimelineEntry(Interval):
    media_id: str
    description: str = Field(min_length=1,max_length=700)
    evidence_refs: list[str] = Field(min_length=1)

class Investigation(BaseModel):
    sequence: list[TimelineEntry] = Field(default_factory=list,max_length=12)
    hypotheses: list[CausalHypothesis] = Field(default_factory=list,max_length=6)
    cause_status: Literal['not_established','supported_contributors'] = 'not_established'
    limitations: list[str] = Field(default_factory=list)
class HumanDecision(BaseRecord):
    target_id: str
    reviewer_id: str
    decision: Literal['accepted','rejected','uncertain']
    reason: str = Field(max_length=4000)
    created_at: str = Field(default_factory=utc)
class Annotation(BaseRecord):
    class_registry_version:str='1'
    frame_id: str
    source_id: str
    class_id: int = Field(ge=0)
    bbox: tuple[float,float,float,float]
    origin: Literal['human','assistant','detector'] = 'human'
    approval_status: Literal['draft','approved'] = 'draft'
    reviewer_id: str | None = None
    @model_validator(mode='after')
    def valid_box(self):
        a,b,c,d=self.bbox
        if not 0<=a<c<=1 or not 0<=b<d<=1: raise ValueError('Use ordered normalized box coordinates')
        return self
class DatasetVersion(BaseRecord):
    version: str
    class_registry_version: str
    sample_ids: list[str]
    split_manifest: dict[str,Literal['train','valid','test']]
    provenance: dict
class ModelVersion(BaseRecord):
    version: str
    weights_ref: str
    dataset_id: str
    detector_family: str = 'rfdetr'
    evaluation_id: str | None = None
class DeploymentRecord(BaseRecord):
    model_id: str
    evaluation_id: str
    camera_scope: list[str]
    reviewer_id: str
    previous_model_id: str | None = None
    previous_detector_config:dict=Field(default_factory=dict)
    created_at: str = Field(default_factory=utc)
class ToolFailure(BaseModel):
    code: str
    message: str
    retryable: bool = False
    missing_context: list[str] = Field(default_factory=list)
class Job(BaseRecord):
    review_run_id: str | None = None
    kind: str
    idempotency_key: str
    run_id: str = Field(default_factory=uid)
    site_id: str = 'local'
    status: Literal['queued','running','completed','failed','cancelled','interrupted','budget_exceeded','budget_deferred'] = 'queued'
    payload: dict
    result_refs: list[str] = Field(default_factory=list)
    error: ToolFailure | None = None
    created_at: str = Field(default_factory=utc)
    updated_at: str = Field(default_factory=utc)
class ReviewRequest(BaseModel):
    event_id: str | None = None
    media_ids: list[str] = Field(default_factory=list,max_length=12)
    context: str = Field(default='',max_length=12000)
    document_ids: list[str] = Field(default_factory=list,max_length=20)
    event_date: date | None = None
    activity: Literal['general_industry','construction','other','unknown'] = 'unknown'
    jurisdiction: str = Field(default='US-federal-OSHA',min_length=1,max_length=100)
    state: str | None = Field(default=None,max_length=80)
    employer_type: str | None = Field(default=None,max_length=100)
    idempotency_key: str = Field(min_length=1,max_length=200)
    @model_validator(mode='after')
    def has_evidence(self):
        if not self.event_id and not self.media_ids: raise ValueError('Supply an event or media')
        return self
class ReplayRequest(Interval):
    source_id: str
    profile_id: str = 'camera-1'
    idempotency_key: str = Field(min_length=1,max_length=200)
class ClassDefinition(BaseModel):
    class_id: int = Field(ge=0)
    name: str
    definition: str
    examples: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
class ClassRegistry(BaseModel):
    version: str
    classes: list[ClassDefinition]
    @model_validator(mode='after')
    def valid_classes(self):
        if not self.classes or [c.class_id for c in self.classes]!=list(range(len(self.classes))):raise ValueError('Class IDs must be consecutive from zero')
        if len({c.name for c in self.classes})!=len(self.classes):raise ValueError('Class names must be unique')
        return self
class BoxProposalResult(BaseModel):
    candidates: list[Annotation]
    scores:list[float]=Field(default_factory=list)
    detector_version: str
    warnings: list[str]
class AuditEvent(BaseRecord):
    audit_event_id:str|None=None
    schema_version: str = '2.0'
    sequence: int = 0
    timestamp_utc: str = Field(default_factory=utc)
    run_id: str | None = None
    job_id: str | None = None
    event_id: str | None = None
    parent_run_id: str | None = None
    trace_id: str = Field(default_factory=uid)
    event_type: str
    agent_name: str | None = None
    workflow_name: str | None = None
    status: str
    attempt: int | None = None
    duration_ms: float | None = None
    provider: str | None = None
    requested_model: str | None = None
    resolved_model: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments: dict | None = None
    output: dict | list | None = None
    error: ToolFailure | None = None
    resources: dict = Field(default_factory=dict)
    configuration: dict = Field(default_factory=dict)
    provenance: dict = Field(default_factory=dict)
class BudgetWindow(BaseModel):
    window_utc:str
    limit_usd:float
    spent_usd:float
    reserved_usd:float
    remaining_usd:float
class ReservationStatus(BaseModel):
    id:str
    run_id:str
    workflow_id:str
    amount:int
    state:str
    created:str
class AiStatus(BaseModel):
    enabled:bool
    stopped:bool
    hosted_allowed:bool
    key_configured:bool
    message:str

class BudgetStatus(BaseModel):
    windows:dict[str,BudgetWindow]
    outstanding:list[ReservationStatus]
    stopped:bool
    circuit_until:str
    pricing:dict[str,dict]
    limits:dict[str,float]
    training:dict
    coverage_gaps:list[dict]=Field(default_factory=list)
    audit_degraded:bool=False
    snapshot_error:str|None=None
class TrialResetPlan(BaseModel):
    plan_id:str
    reset_counts:dict[str,int]
    preserved_counts:dict[str,int]
    running_job_ids:list[str]
    can_reset:bool
    note:str='Trial data is archived. Labels, datasets, models, original footage, audit and budgets are preserved.'
class TrialArchive(BaseRecord):
    name:str
    created_at:str=Field(default_factory=utc)
    reviewer_id:str
    reset_counts:dict[str,int]
    preserved_counts:dict[str,int]
    status:Literal['archived','restored']='archived'
    restored_at:str|None=None
class TrialResetRequest(BaseModel):
    plan_id:str
    name:str=Field(default='Trial',min_length=1,max_length=120)
