"""Shared Live/Review records; completion is separate from assessment and disposition."""
from typing import Literal
from pydantic import Field
from safety_app.domain.base import StrictModel
from safety_app.domain.models import BaseRecord, Interval, HumanDecision, AuditEvent, utc
from safety_app.domain.video_safety import VideoSafetyOverview
from safety_app.domain.people import PersonFrame


class ReviewWindow(BaseRecord, Interval):
    run_id: str
    index: int
    status: Literal['pending', 'queued', 'running', 'completed', 'failed', 'skipped', 'cancelled'] = 'pending'
    frame_ids: list[str] = Field(default_factory=list)
    chunk_id: str | None = None
    job_id: str | None = None
    gaps: list[str] = Field(default_factory=list)
    error: str | None = None
    updated_at: str = Field(default_factory=utc)


class ReviewRun(BaseRecord, Interval):
    site_id: str
    job_id: str
    source_id: str
    source_hash: str
    media_id: str
    camera_id: str
    kind: Literal['live', 'review']
    mode: Literal['hosted', 'fixture']
    state: Literal['queued', 'running', 'paused', 'ended', 'cancelled', 'interrupted', 'failed', 'budget_exceeded'] = 'queued'
    configuration: dict
    request: dict
    session_id: str | None = None
    generation: int = 0
    cutoff_ms: int = 0
    source_verified: bool = False
    created_at: str = Field(default_factory=utc)
    updated_at: str = Field(default_factory=utc)
    limitations: list[str] = Field(default_factory=lambda: [
        'Each window uses sampled stills, not every decoded frame. Motion and events between samples can be missed.',
        'Visible concerns and model assessments require human review; this is not a safety certification.',
    ])


class ReviewRunStatus(StrictModel):
    run: ReviewRun
    windows: list[ReviewWindow]
    coverage_complete: bool
    covered_ms: int
    expected_ms: int
    completed_windows: int
    total_windows: int
    specialist_complete: bool
    pending_specialists: int
    human_disposition_complete: bool
    safety: VideoSafetyOverview
    decisions: list[HumanDecision]
    usage: dict
    resume_allowed: bool
    resume_reason: str
    people: list[PersonFrame] = Field(default_factory=list)
    transport_diagnostics: list['ReviewTransportDiagnostic'] = Field(default_factory=list)
    detail_inspections: list['TemporalDetailEvidence'] = Field(default_factory=list)
    test_budget: dict | None = None


class TLSFailure(StrictModel):
    reason: str | None = None
    library: str | None = None


class ReviewTransportDiagnostic(StrictModel):
    client_request_id: str
    transport_phase: str
    http_request_id: str | None = None
    provider_response_id: str | None = None
    reservation_id: str | None = None
    http_status: int | None = None
    proven_not_dispatched: bool = False
    unsent_basis: str | None = None
    failed_phase: str | None = None
    request_body_complete: bool | None = None
    exception_types: list[str] = Field(default_factory=list)
    tls_errors: list[TLSFailure] = Field(default_factory=list)


class TemporalDetailCrop(StrictModel):
    parent_frame_id: str
    parent_hash: str
    source_hash: str
    timestamp_ms: int
    box: list[float] = Field(min_length=4,max_length=4)
    pixel_box: list[int] = Field(min_length=4,max_length=4)
    original_detail_pixels: list[int] = Field(min_length=2,max_length=2)
    content_hash: str
    notice: str


class TemporalDetailEvidence(BaseRecord):
    run_id: str
    packet_id: str
    category: str
    frame_ids: list[str]
    crops: list[TemporalDetailCrop]
    status: Literal['completed','skipped']
    reason: str = ''
    observer_run_id: str | None = None
    verifier_run_id: str | None = None


ReviewRunStatus.model_rebuild()


class ReviewActivityPage(StrictModel):
    events: list[AuditEvent]
    cursor: int


class RunDecisionInput(StrictModel):
    target_id: str
    decision: Literal['accepted', 'rejected', 'uncertain']
    reason: str = Field(min_length=1, max_length=4000)


class LiveControl(StrictModel):
    action: Literal['play', 'pause', 'restart']
