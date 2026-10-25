"""Additive review-bound conversation contracts; old reviews remain untouched."""
from typing import Literal
from pydantic import Field, model_validator
from safety_app.domain.base import StrictModel
from safety_app.domain.models import FrameRef, Interval, Job
from safety_app.domain.people import PersonFrame
from safety_app.domain.chat_lab import LabAnswer, LabChallenge, LabCitation, LabRecovery, LabUsage, LabRouting


class VideoChatFocus(StrictModel):
    timestamp_ms: int | None = Field(default=None, ge=0)
    finding_id: str | None = Field(default=None, max_length=200)


class VideoChatInput(StrictModel):
    message: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=100)
    focus: VideoChatFocus = Field(default_factory=VideoChatFocus)
    mode: Literal['hosted', 'fixture'] = 'hosted'

    @model_validator(mode='after')
    def nonblank(self):
        if not self.message.strip(): raise ValueError('Enter a question about this review')
        return self


class VideoChatContext(StrictModel):
    review_run_id: str
    source_hash: str
    media_id: str
    snapshot_id: str
    focus: VideoChatFocus
    start_ms: int
    end_ms: int
    review_version: str
    permissions_version: str = 'review-chat-1'


class VideoEvidenceRef(StrictModel):
    id: str
    frame: FrameRef
    source_hash: str
    origin: Literal['saved', 'inspection', 'crop']
    parent_frame_id: str | None = None
    box: list[float] | None = None
    inspected: bool = False
    model_image_hash: str | None = None
    people: list[PersonFrame] = Field(default_factory=list)


class VideoInspectionPlan(StrictModel):
    frame_ids: list[str] = Field(default_factory=list, max_length=6)
    intervals: list[Interval] = Field(default_factory=list, max_length=2)
    reason: str = Field(max_length=500)


class VideoInspection(StrictModel):
    id: str
    interval: Interval
    frame_ids: list[str]
    gaps: list[str] = Field(default_factory=list)


class VideoChatTurn(StrictModel):
    id: str
    session_id: str
    review_run_id: str
    job: Job
    message: str
    mode: Literal['hosted', 'fixture']
    context: VideoChatContext
    answer: LabAnswer | None = None
    routing: LabRouting | None = None
    challenge: LabChallenge | None = None
    citations: list[LabCitation] = Field(default_factory=list)
    evidence: list[VideoEvidenceRef] = Field(default_factory=list)
    inspections: list[VideoInspection] = Field(default_factory=list)
    recovery: LabRecovery = Field(default_factory=LabRecovery)
    usage: LabUsage = Field(default_factory=LabUsage)
    limit_usd: float = .05
    execution_ms: float = 0
    queue_ms: float = 0
    issue_codes: list[str] = Field(default_factory=list)
    versions: dict = Field(default_factory=dict)
    assisted_review: bool = True


class VideoChatState(StrictModel):
    review_run_id: str
    session_id: str | None = None
    turns: list[VideoChatTurn] = Field(default_factory=list)
    hosted_available: bool
    limit_usd: float = .05
    message: str = 'Assisted review · visual samples only; audio is not assessed.'
