"""Planner and perceptual modes inside the existing video observer boundary."""
from typing import Literal

from pydantic import Field, model_validator

from safety_app.domain.base import StrictModel
from safety_app.domain.models import Interval
from safety_app.domain.video_analysis import VideoAnswer, VideoObservation
from safety_app.domain.video_safety import VisualSafetyCheck


class VideoObservationRequest(StrictModel):
    question: str
    purpose: str
    media_id: str
    intervals: list[Interval]
    safety_screening: bool = False
    required_ppe: list[str] = Field(default_factory=list)


class VideoObservationResult(StrictModel):
    summary: str = Field(min_length=1, max_length=1000)
    observations: list[VideoObservation] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=12)
    screening_checks: list[VisualSafetyCheck] = Field(default_factory=list, max_length=12)


class VideoPlanRequest(StrictModel):
    question: str
    media_id: str
    scope: Interval
    catalog: list[dict]
    inspections_remaining: int
    max_interval_ms: int
    max_answer_frames: int


class VideoPlan(StrictModel):
    action: Literal['inspect', 'compare', 'answer']
    purpose: str = Field(min_length=1, max_length=1000)
    intervals: list[Interval] = Field(default_factory=list, max_length=2)
    answer: VideoAnswer | None = None

    @model_validator(mode='after')
    def action_shape(self):
        expected = {'inspect': 1, 'compare': 2, 'answer': 0}[self.action]
        if len(self.intervals) != expected:
            raise ValueError(f'{self.action} requires {expected} intervals')
        if (self.answer is not None) != (self.action == 'answer'):
            raise ValueError('Only the answer action supplies an answer')
        return self
