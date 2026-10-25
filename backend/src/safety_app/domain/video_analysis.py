"""Recorded-video questions, timestamped memory, and independent verification."""
from typing import Literal
from datetime import datetime,timezone

from pydantic import Field, model_validator

from safety_app.domain.base import StrictModel
from safety_app.domain.models import BaseRecord, FrameRef, Interval, utc


class RecordedSource(StrictModel):
    id: str
    name: str


class VideoAnalysisRequest(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    camera_id: str = 'camera-1'
    question: str = Field(min_length=1, max_length=4000)
    start_ms: int = Field(default=0, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    mode: Literal['hosted', 'fixture'] = 'hosted'
    idempotency_key: str = Field(min_length=1, max_length=200)
    review_strategy: Literal['full', 'adaptive'] = 'adaptive'
    max_cost_usd: float | None = Field(default=None, gt=0, le=.15,
        description='Optional lower allowance for a full Review/Live run. Never raises server limits.')
    jurisdiction: str = Field(default='unknown', min_length=1, max_length=100)
    safety_screening: bool = True
    escalate_to_safety_team: bool = True
    required_ppe: list[str] = Field(default_factory=list, max_length=8)
    person_confidence: float = Field(default=.5, ge=.05, le=.95)
    recording_start_utc: datetime | None = None

    @model_validator(mode='after')
    def ordered(self):
        if self.max_cost_usd is not None and self.review_strategy!='full':
            raise ValueError('A reduced run allowance requires full Review/Live')
        if self.recording_start_utc:
            if self.recording_start_utc.utcoffset() is None:raise ValueError('Recording start must include a timezone')
            self.recording_start_utc=self.recording_start_utc.astimezone(timezone.utc)
        if any(not item.strip() or len(item) > 60 for item in self.required_ppe):
            raise ValueError('PPE items must contain 1..60 characters')
        if not self.question.strip():
            raise ValueError('Supply a video question')
        if self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError('End must follow start')
        return self


class VideoObservation(Interval):
    media_id: str
    description: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(min_length=1, max_length=6)
    uncertainty: list[str] = Field(default_factory=list, max_length=8)


class VideoChunk(BaseRecord):
    analysis_id: str
    media_id: str
    intervals: list[Interval]
    summary: str = Field(max_length=1000)
    observations: list[VideoObservation]
    frames: list[FrameRef]
    limitations: list[str]
    observer_run_id: str
    screening_checks: list['VisualSafetyCheck'] = Field(default_factory=list)


class VideoClaim(Interval):
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(min_length=1, max_length=6)
    uncertainty: list[str] = Field(default_factory=list, max_length=8)


class VideoAnswer(StrictModel):
    answer: str = Field(min_length=1, max_length=6000)
    claims: list[VideoClaim] = Field(default_factory=list, max_length=6)
    limitations: list[str] = Field(default_factory=list, max_length=16)


class VideoClaimCheck(StrictModel):
    claim_id: str
    verdict: Literal['supported', 'unsupported', 'uncertain']
    explanation: str = Field(min_length=1, max_length=1500)


class VideoVerification(StrictModel):
    checks: list[VideoClaimCheck] = Field(default_factory=list, max_length=6)
    answer_supported: bool
    limitations: list[str] = Field(default_factory=list, max_length=16)


class VideoAnalysis(BaseRecord, Interval):
    job_id: str
    site_id: str
    source_id: str
    media_id: str
    question: str
    mode: Literal['hosted', 'fixture']
    answer: VideoAnswer
    verification: VideoVerification
    status: Literal['supported_within_samples', 'needs_review']
    chunk_ids: list[str]
    inspected_intervals: list[Interval]
    limitations: list[str]
    created_at: str = Field(default_factory=utc)
    safety_case_ids: list[str] = Field(default_factory=list)


from safety_app.domain.video_safety import VisualSafetyCheck
VideoChunk.model_rebuild()
