"""Versioned wire records. Source time is independent of processing wall time."""
from __future__ import annotations
from typing import Any, Literal
from uuid import uuid4
import re
from app.cv.classes import DEFAULT_GROUPS,INDUSTRIAL_LABELS
from pydantic import BaseModel, Field, ConfigDict, model_validator

Point = tuple[float, float]
Box = tuple[float, float, float, float]

class Context(BaseModel):
    source_id: str
    camera_id: str
    session_id: str
    run_generation: int = 0
    schema_version: str = '1.0'

class ZoneConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    points: list[Point] = Field(min_length=3, max_length=128)
    version: int = 1
    @model_validator(mode='after')
    def normalized(self):
        if any(not 0 <= v <= 1 for p in self.points for v in p):
            raise ValueError('Zone points must be normalized')
        points=self.points
        if len(set(points)) != len(points): raise ValueError('Zone vertices must be distinct; the polygon closes automatically')
        area=sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points,points[1:]+points[:1]))
        if abs(area)<1e-8: raise ValueError('Zone must enclose a nonzero area')
        def orient(a,b,c): return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        def on_segment(a,b,c): return min(a[0],b[0])<=c[0]<=max(a[0],b[0]) and min(a[1],b[1])<=c[1]<=max(a[1],b[1])
        edges=list(zip(points,points[1:]+points[:1]))
        for i,(a,b) in enumerate(edges):
            for j,(c,d) in enumerate(edges):
                if j<=i+1 or (i==0 and j==len(edges)-1): continue
                x,y,u,v=orient(a,b,c),orient(a,b,d),orient(c,d,a),orient(c,d,b)
                if (x*y<0 and u*v<0) or (x==0 and on_segment(a,b,c)) or (y==0 and on_segment(a,b,d)) or (u==0 and on_segment(c,d,a)) or (v==0 and on_segment(c,d,b)):
                    raise ValueError('Zone edges must not intersect; draw vertices around its perimeter')
        return self

class RuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal['zone_dwell','zone_entry','line_crossing','zone_occupancy','proximity']
    label: str = Field(default='', max_length=120)
    enabled: bool = True
    ai_review: bool = True
    review_instruction: str = Field(default='', max_length=2000)
    target_labels: list[str] = Field(default_factory=lambda: ['person'], min_length=1, max_length=30)
    secondary_labels: list[str] = Field(default_factory=lambda: ['car','truck','bus'], min_length=1, max_length=30)
    required_ppe: list[str] = Field(default_factory=list, max_length=20)
    tolerance: float = Field(default=.01, ge=0, le=.2)
    max_count: int = Field(default=2, ge=0, le=1000)
    distance_threshold: float = Field(default=.1, gt=0, le=1)
    zone_id: str | None = None
    duration_ms: int = Field(default=2000, ge=0)
    clear_ms: int = Field(default=1000, ge=0)
    line: list[Point] | None = None
    direction: Literal['positive','negative','both'] = 'both'
    version: int = 1
    @model_validator(mode='after')
    def shape(self):
        if any(not item.strip() or len(item)>80 for item in self.required_ppe): raise ValueError('PPE items must be nonempty and at most 80 characters')
        if self.kind in ('zone_dwell','zone_entry','zone_occupancy') and not self.zone_id:
            raise ValueError(f'{self.kind} needs zone_id')
        if any(not label.strip() or len(label)>80 for label in [*self.target_labels,*self.secondary_labels]):
            raise ValueError('Target labels must be nonempty detector class names')
        if self.kind == 'line_crossing' and (not self.line or len(self.line) != 2 or any(not 0 <= v <= 1 for p in self.line for v in p) or self.line[0] == self.line[1]):
            raise ValueError('line_crossing needs two distinct normalized points')
        return self

class CameraConfig(BaseModel):
    id: str = 'camera-1'
    name: str = 'Local camera'

class LabConfig(BaseModel):
    version: int = 1
    zones: list[ZoneConfig] = Field(default_factory=list, max_length=100)
    rules: list[RuleConfig] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=.5, ge=0, le=1)
    required_ppe: list[str] = Field(default_factory=list, max_length=20)
    person_focused: bool = True
    ppe_min_person_height_px: int = Field(default=120, ge=20, le=1000)
    person_crop_cap: int = Field(default=2, ge=0, le=6)
    review_cooldown_s: float = Field(default=30, ge=0, le=3600)
    api_target_per_camera_hour: float = Field(default=.01, gt=0)
    review_budget_usd: float | None = Field(default=None, gt=0)
    review_request_bound_usd: float | None = Field(default=None, gt=0)
    evidence_pre_s: float = Field(default=10, ge=0, le=120)
    evidence_post_s: float = Field(default=10, ge=0, le=120)
    enabled_labels: list[str] | None = Field(default_factory=lambda:list(INDUSTRIAL_LABELS), max_length=200)
    class_confidence: dict[str,float] = Field(default_factory=lambda: {'person': .65})
    class_groups: dict[str,list[str]] = Field(default_factory=lambda:{k:list(v) for k,v in DEFAULT_GROUPS.items()})
    tracker_activation: float = Field(default=.5, ge=0, le=1)
    tracker_high_confidence: float = Field(default=.6, ge=0, le=1)
    tracker_match_iou: float = Field(default=.1, gt=0, le=1)
    tracker_min_frames: int = Field(default=2, ge=1, le=30)
    tracker_lost_seconds: float = Field(default=1, ge=0, le=30)
    custom_model_id: str | None = None
    target_fps: float = Field(default=5, gt=0, le=60)
    scene_review_enabled: bool = False
    analysis_mode: Literal['cv_only','ai_scene','ai_cv'] = 'cv_only'
    scene_review_interval_s: float = Field(default=5, ge=1, le=300)
    scene_window_s: float = Field(default=15, ge=1, le=120)
    scene_sample_s: float = Field(default=1, ge=.1, le=30)
    scene_max_images: int = Field(default=15, ge=1, le=15)
    scene_extra_images: int = Field(default=0, ge=0, le=6)
    cv_triggered_reviews: bool = False
    site_context: dict[str, Any] = Field(default_factory=dict)
    detector: Literal['small','nano','custom'] = 'small'
    custom_weights: str | None = None
    custom_class_map: dict[int,str] | None = None
    custom_weights_license: str | None = None
    @model_validator(mode='after')
    def custom_config(self):
        if any(not item.strip() or len(item)>80 for item in self.required_ppe): raise ValueError('PPE items must be nonempty and at most 80 characters')
        if self.detector=='custom' and not self.custom_model_id and (not self.custom_weights or not self.custom_class_map or not self.custom_weights_license):
            raise ValueError('Custom detector needs weights, class map, and declared license')
        if self.custom_class_map and any(k<0 or not v.strip() for k,v in self.custom_class_map.items()):
            raise ValueError('Custom class map needs nonnegative IDs and explicit labels')
        if len({z.id for z in self.zones}) != len(self.zones) or len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError('Zone and rule IDs must be unique')
        if len(self.class_groups)>30 or any(not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',k) or not members or len(members)>200 or any(not m.strip() or m.startswith('group:') for m in members) for k,members in self.class_groups.items()):
            raise ValueError('Class groups need simple unique names and nonempty raw detector labels')
        if any(not 0<=value<=1 for value in self.class_confidence.values()):
            raise ValueError('Per-class confidence must be between 0 and 1')
        if self.enabled_labels is not None and (len(set(self.enabled_labels))!=len(self.enabled_labels) or any(not label.strip() or label.startswith('group:') for label in self.enabled_labels)):
            raise ValueError('Enabled classes must be distinct raw detector labels')
        if any(target.startswith('group:') and target[6:] not in self.class_groups for r in self.rules for target in [*r.target_labels,*r.secondary_labels]):
            raise ValueError('Rule references an unknown class group')
        zones={z.id for z in self.zones}
        if any(r.zone_id and r.zone_id not in zones for r in self.rules):
            raise ValueError('Rule references unknown zone')
        return self

class DetectorCapabilities(BaseModel):
    id: str
    state: Literal['supported','experimental','unavailable']
    reason: str

class Detection(BaseModel):
    label: str
    score: float
    groups: list[str] = Field(default_factory=list)
    box: Box

class TrackObservation(Context):
    track_id: int
    label: str
    score: float
    groups: list[str] = Field(default_factory=list)
    box: Box
    trail: list[Point] = Field(default_factory=list)
    timestamp_ms: int
    frame_id: int

class FramePacket(Context):
    frame_id: int
    timestamp_ms: int
    width: int
    height: int
    tracks: list[TrackObservation] = Field(default_factory=list)
    detections: list[Detection] = Field(default_factory=list)
    cv_status: str = 'unavailable'
    processing_ms: float = 0
    config_version: int = 1
    evidence_cutoff_ms: int

class SceneSnapshot(FramePacket):
    limitations: list[str] = Field(default_factory=list)

class RuleCandidate(Context):
    id: str
    kind: str
    rule_id: str | None = None
    zone_ids: list[str] = Field(default_factory=list)
    track_ids: list[int] = Field(default_factory=list)
    timestamp_ms: int
    start_ms: int
    evidence_cutoff_ms: int
    updated_ms: int | None = None
    closed_ms: int | None = None
    closure_reason: str | None = None
    episode: int = 1
    state: Literal['candidate','active','clearing','closed'] = 'candidate'
    ai_status: Literal['disabled','queued','running','reviewed','timeout','error','skipped'] = 'disabled'
    measurements: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    config_version: int = 1
    processing_time: str = ''

class EvidenceRef(Context):
    id: str
    frame_id: int
    timestamp_ms: int
    kind: Literal['original','crop','annotated'] = 'original'
    config_version: int = 1
    processing_time: str = ''

class EvidenceBundle(Context):
    candidate_id: str
    evidence_cutoff_ms: int
    refs: list[EvidenceRef] = Field(default_factory=list)
    coverage_start_ms: int | None = None
    coverage_end_ms: int | None = None
    limitations: list[str] = Field(default_factory=list)

class RiskAssessment(Context):
    id: str = Field(default_factory=lambda: str(uuid4()))
    candidate_id: str
    review_mode: Literal['live','retrospective'] = 'live'
    evidence_cutoff_ms: int
    assessment: Literal['supported_risk','not_supported','uncertain']
    severity: Literal['info','low','medium','high','unknown']
    risk_type: str
    explanation: str
    observed_facts: list[str] = Field(default_factory=list)
    inferred_risks: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommended_human_review: bool = True
    model_id: str = 'gpt-6-luna'
    prompt_version: str = '1.0'
    token_usage: dict[str, int] = Field(default_factory=dict)
    tool_usage: list[dict[str, Any]] = Field(default_factory=list)
    elapsed_ms: float = 0
    uncalibrated_confidence: float | None = None

class ReviewFeedback(Context):
    id: str
    candidate_id: str
    disposition: Literal['accepted','rejected','uncertain']
    note: str = ''
    processing_time: str

class IncidentMarker(Context):
    id: str
    timestamp_ms: int
    pre_ms: int = Field(default=15000, ge=0, le=120000)
    post_ms: int = Field(default=30000, ge=0, le=120000)
    config_version: int = 1
    anchor_kind: str = 'user_marked'
    processing_time: str = ''

class ResponseTimeline(BaseModel):
    label: str
    timestamp_ms: int | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    explanation: str = ''

class ResponseReport(Context):
    id: str = Field(default_factory=lambda: str(uuid4()))
    incident_id: str
    timeline: list[ResponseTimeline] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    observation_evidence: dict[str, list[EvidenceRef]] = Field(default_factory=dict)
    criteria_comparison: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    improvement_suggestions: list[str] = Field(default_factory=list)
    intervals_ms: dict[str,int | None] = Field(default_factory=dict)
    model_id: str = 'gpt-6-luna'
    prompt_version: str = '1.0'
    token_usage: dict[str,int] = Field(default_factory=dict)
    tool_usage: list[dict[str,Any]] = Field(default_factory=list)
    elapsed_ms: float = 0

class AgentRunRecord(Context):
    id: str
    candidate_id: str | None = None
    incident_id: str | None = None
    status: str
    model_id: str = 'gpt-6-luna'
    error: str | None = None
    tool_usage: list[dict[str,Any]] = Field(default_factory=list)
    token_usage: dict[str,int] = Field(default_factory=dict)
    elapsed_ms: float = 0

class PipelineMetrics(BaseModel):
    source_fps: float | None = None
    processed_fps: float = 0
    dropped_frames: int = 0
    frame_age_ms: float | None = None
    inference_latency_ms: float | None = None
    active_tracks: int = 0
    queue_depth: int = 0
    rule_counts: int = 0
    errors: int = 0
    model_requests: int = 0
    tokens: int = 0
    cost_estimate: float | None = None

class EventDetail(BaseModel):
    candidate: RuleCandidate
    assessments: list[RiskAssessment] = Field(default_factory=list)
    evidence: EvidenceBundle
    feedback: list[ReviewFeedback] = Field(default_factory=list)

class IncidentDetail(BaseModel):
    incident: IncidentMarker
    report: ResponseReport | None = None
    coverage: dict[str,Any] = Field(default_factory=dict)

class ApiState(BaseModel):
    rule_coverage: list[dict[str,Any]] = Field(default_factory=list)
    segment_gaps: list[dict[str,Any]] = Field(default_factory=list)
    session: dict[str,Any] | None = None
    config: LabConfig
    capabilities: list[DetectorCapabilities] = Field(default_factory=list)
    metrics: PipelineMetrics
    ai: dict[str,Any] = Field(default_factory=dict)
    health: dict[str,Any] = Field(default_factory=dict)
    events: list[RuleCandidate] = Field(default_factory=list)
    incidents: list[IncidentMarker] = Field(default_factory=list)
    agent_runs: list[AgentRunRecord] = Field(default_factory=list)


class PPECheck(BaseModel):
    item: str
    track_id: int | None = None
    status: Literal['present','missing','not_assessable']
    visibility: Literal['clear','small','obscured','outside_image','unclear']
    evidence_ids: list[str] = Field(min_length=1)
    explanation: str
    person_box: Box | None = None
    @model_validator(mode='after')
    def assessable(self):
        if self.person_box and not (0<=self.person_box[0]<self.person_box[2]<=1 and 0<=self.person_box[1]<self.person_box[3]<=1):
            raise ValueError('PPE person box must be normalized and ordered')
        if self.status == 'missing' and self.visibility != 'clear':
            raise ValueError('PPE can be missing only with clear visibility')
        return self

class SceneFinding(BaseModel):
    # ID is a suggestion referring to memory; the application assigns event IDs.
    prior_event_id: str | None = None
    finding_type: Literal['observed_rule_violation','observed_potential_hazard','uncertain_finding'] = 'uncertain_finding'
    track_ids: list[int] = Field(default_factory=list)
    rule_id: str | None = None
    ai_confidence: float | None = Field(default=None, ge=0, le=1)
    corrective_action: str = ''
    ppe: list[PPECheck] = Field(default_factory=list, max_length=20)
    category: str
    subject: str = Field(description='Visible subject and scene location; no identity or intent')
    status: Literal['new','continuing','escalating','no_longer_visible']
    observed_condition: str
    inferred_risk: str
    first_ms: int = Field(ge=0)
    last_ms: int = Field(ge=0)
    evidence_ids: list[str] = Field(min_length=1)
    severity: Literal['info','low','medium','high','unknown']
    uncertainty: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    human_review_reason: str

    @model_validator(mode='after')
    def interval(self):
        if self.last_ms < self.first_ms: raise ValueError('Reversed finding interval')
        if self.finding_type == 'observed_potential_hazard' and (not self.observed_condition.strip() or not self.inferred_risk.strip()):
            raise ValueError('Potential hazards require visible condition and possible consequence')
        if self.finding_type == 'observed_rule_violation' and not self.rule_id:
            raise ValueError('Observed rule violation requires a rule')
        return self

class SceneResult(BaseModel):
    findings: list[SceneFinding] = Field(default_factory=list, max_length=20)
    no_finding: bool
    inconclusive: bool = False
    summary: str
    limitations: list[str] = Field(default_factory=list)
    @model_validator(mode='after')
    def consistent(self):
        if self.no_finding != (not self.findings):
            raise ValueError('no_finding must explicitly agree with findings')
        return self

class ScenePacket(Context):
    id: str = Field(default_factory=lambda: str(uuid4()))
    cutoff_ms: int
    start_ms: int
    trigger: Literal['scheduled','cv_trigger','manual','comparison'] = 'scheduled'
    refs: list[EvidenceRef] = Field(max_length=15)
    extra_refs: list[EvidenceRef] = Field(default_factory=list, max_length=6)
    coverage: dict[str, Any]
    cv_metadata: dict[str, Any] = Field(default_factory=dict)
    site_context: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    triggered_candidate_ids: list[str] = Field(default_factory=list)
    config_version: int

class SceneReview(Context):
    id: str = Field(default_factory=lambda: str(uuid4()))
    packet: ScenePacket
    analysis_mode: Literal['ai_scene','ai_cv']
    status: str = 'queued'
    model: str = 'gpt-6-luna'
    settings: dict[str, Any] = Field(default_factory=dict)
    memory: list[dict[str, Any]] = Field(default_factory=list)
    received_images: list[dict[str, Any]] = Field(default_factory=list)
    result: SceneResult | None = None
    event_updates: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    cost_estimate: float | None = None
    cost_rates: dict[str, float] | None = None
    elapsed_ms: float = 0
    error: str | None = None
