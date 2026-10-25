"""Layer-one observations, durable judgment handoffs and future reporting inputs."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from safety_app.domain.base import StrictModel
from safety_app.domain.models import BaseRecord, Interval, Finding, Job, utc

SafetyCategory = Literal['ppe', 'blocked_access', 'unstable_stack', 'slip_trip', 'struck_by', 'fall', 'fire_burn']
ROUTINE_CATEGORIES = ('ppe', 'blocked_access', 'unstable_stack', 'slip_trip')
NormalizedBox = Annotated[list[float], Field(min_length=4,max_length=4)]


class VisualSafetyCheck(Interval):
    media_id: str | None = Field(default=None, min_length=1,
        description='Optional recording ID; when supplied, must match every cited frame. Never expands scope.')
    category: SafetyCategory
    result: Literal['clear', 'suspected', 'unknown']
    visibility: Literal['clear', 'partial', 'not_assessable']
    condition: str = Field(min_length=1, max_length=350)
    evidence_refs: list[str] = Field(min_length=1, max_length=6)
    needs_judgment: bool = False
    priority: Literal['routine', 'high'] = 'routine'
    exposure: str = Field(default='', max_length=250)
    immediate_reason: str = Field(default='', max_length=250)
    ppe_item: str | None = Field(default=None, max_length=60)
    ppe_state: Literal['present', 'not_visible', 'not_assessable'] | None = None
    person_box: NormalizedBox | None = None
    subject_box: NormalizedBox | None = None
    route_basis: Literal['visible_route', 'unknown'] = 'unknown'
    temporal_basis: Literal['still_condition', 'sequence_event', 'uncertain'] = 'still_condition'
    limitations: list[str] = Field(default_factory=list)
    coordinator_filled: bool = Field(default=False,
        description='Set only by Python for a required category the observer omitted; never set this.')

    @model_validator(mode='after')
    def conservative(self):
        if self.category=='ppe' and self.ppe_state=='not_assessable':
            self.result='unknown';self.needs_judgment=False;self.priority='routine'
        if self.needs_judgment and self.result != 'suspected':
            raise ValueError('Only suspected visible conditions can request safety judgment')
        if self.result != 'unknown' and self.visibility == 'not_assessable':
            raise ValueError('Unassessable footage must remain unknown')
        if self.priority == 'high' and (self.result != 'suspected' or self.visibility != 'clear' or not self.exposure.strip() or not self.immediate_reason.strip()):
            raise ValueError('High priority needs clear visible concern, exposure and immediate-action reason')
        unknown_item=self.result=='unknown' and self.ppe_state=='not_assessable'
        if self.category == 'ppe' and (self.ppe_state is None or (not self.ppe_item and not unknown_item)):
            raise ValueError('PPE checks must name the item and its visible state')
        return self


class SafetyScreening(BaseRecord):
    screening_version: str = 'visual-screen-2.0'
    analysis_id: str
    job_id: str
    chunk_id: str
    site_id: str
    media_id: str
    source_hash: str
    camera_id: str
    mode: Literal['hosted', 'fixture']
    check: VisualSafetyCheck
    required_ppe: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc)


class VideoSafetyCase(BaseRecord, Interval):
    routing_version: str = 'video-safety-1.0'
    episode_bucket_ms: int = Field(default=10000,ge=1)
    analysis_id: str
    parent_job_id: str
    site_id: str
    media_id: str
    camera_id: str
    source_hash: str
    episode_key: str
    category: SafetyCategory
    mode: Literal['hosted', 'fixture']
    escalation_enabled: bool
    subject_box: NormalizedBox | None = None
    ppe_item: str | None = None
    event_time_utc: str | None = None
    created_at: str = Field(default_factory=utc)


class SafetyCaseEvidence(BaseRecord):
    case_id: str
    screening_id: str


class SafetyHandoff(BaseRecord):
    case_id: str
    parent_job_id: str
    review_job_id: str
    screening_ids: list[str]
    omitted_screening_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc)


class ManagerAttention(BaseRecord):
    case_id: str
    screening_id: str
    episode_key: str
    site_id: str
    mode: Literal['hosted', 'fixture']
    reason: str
    priority: Literal['high'] = 'high'
    evidence_status: Literal['provisional','specialist_supported'] = 'provisional'
    report_condition: Literal['immediate'] = 'immediate'
    delivery_status: Literal['not_configured'] = 'not_configured'
    event_time_utc: str | None = None
    event_recency: Literal['unverified'] = 'unverified'
    created_at: str = Field(default_factory=utc)


class ReviewTriage(StrictModel):
    priority: Literal['routine','high']
    reason: str = Field(min_length=1,max_length=700)
    finding_ids: list[str] = Field(default_factory=list,max_length=6)
    evidence_refs: list[str] = Field(default_factory=list,max_length=6)


class VideoSafetyAssessment(BaseRecord):
    case_id: str
    analysis_id: str
    site_id: str
    review_job_id: str
    review_id: str
    mode: Literal['hosted', 'fixture']
    findings: list[Finding]
    specialist_assessments: list[dict]
    challenge: dict
    investigation: dict
    limitations: list[str]
    triage: ReviewTriage | None = None
    triage_supported: bool | None = None
    created_at: str = Field(default_factory=utc)


class VideoSafetyCaseView(StrictModel):
    case: VideoSafetyCase
    screenings: list[SafetyScreening]
    handoff: SafetyHandoff | None = None
    review_job: Job | None = None
    assessment: VideoSafetyAssessment | None = None
    unassessed_screening_ids: list[str] = Field(default_factory=list)
    priority: Literal['routine', 'high']
    manager_attention: list[ManagerAttention]
    status: Literal['awaiting_video_completion', 'queued', 'running', 'assessed', 'needs_review', 'deferred', 'failed', 'cancelled', 'interrupted', 'budget_exceeded']
    report_condition: Literal['immediate', 'scheduled']
    delivery_status: Literal['not_configured'] = 'not_configured'


class VideoSafetyOverview(StrictModel):
    analysis_id: str
    screenings: list[SafetyScreening]
    cases: list[VideoSafetyCaseView]


class SafetyMetrics(StrictModel):
    site_id: str
    since: str | None
    until: str | None
    period_basis: Literal['screening_recorded_utc'] = 'screening_recorded_utc'
    judgment_basis: Literal['current_state'] = 'current_state'
    generated_at: str = Field(default_factory=utc)
    includes_fixtures: bool
    screening_checks: int
    assessed_checks: int
    unknown_checks: int
    suspected_episodes: int
    by_category: dict[str, int]
    high_priority_episodes: int
    assessed_cases: int
    pending_cases: int
    deferred_cases: int
    failed_cases: int
    findings_by_classification: dict[str, int]
    recommendations: list[dict]
    sampled_frame_count: int
    model_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0
    unknown_cost_requests: int = 0
    unresolved_reserved_usd: float = 0
    limitations: list[str]
