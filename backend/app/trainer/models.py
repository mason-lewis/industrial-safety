"""Typed contracts for local detector-assisted learning and human review."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from safety_app.agents.annotation_assistant.models import ImageReviewResult
from safety_app.domain.models import AuditEvent


class LearningBox(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    bbox: tuple[float, float, float, float]


class LearningProposal(LearningBox):
    score: float = Field(ge=0, le=1)


class LearningLabelInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    boxes: list[LearningBox] = Field(max_length=200)
    reviewed: bool = False
    verified_empty: bool = False
    proposal_decisions: dict[str, Literal['accepted', 'adjusted', 'rejected']] = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=0)
    ai_review_id: str | None = None

class LearningImageReviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)

class LearningImageReviewSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image_usd: float = Field(gt=0, le=.05, allow_inf_nan=False)

class LearningImageReviewStatus(BaseModel):
    always_on: bool = False
    ready: bool
    model: str
    blockers: list[str]
    image_usd: float | None = None

class LearningAIReview(BaseModel):
    id: str
    created_at: float
    revision: int
    model: str
    image_sha256: str
    input_sha256: str
    result: ImageReviewResult
    added_boxes: list['LearningBox']

class LearningAIReviewAttempt(BaseModel):
    id: str
    frame_id: str
    created_at: float
    updated_at: float
    revision: int
    status: Literal['running','completed','failed','cancelled','blocked','cached','interrupted']
    stage: str
    model: str
    error: str | None = None
    result_run_id: str | None = None

class LearningAIActivity(BaseModel):
    attempts: list[LearningAIReviewAttempt]
    selected_run_id: str | None = None
    audit: list[AuditEvent]
    partial: bool = False
    audit_degraded: bool = False
    snapshot_error: str | None = None


class LearningAssistInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confidence: float = Field(default=.25, ge=.05, le=.95)


class LearningClassesInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    classes: list[str] = Field(min_length=1, max_length=40)


class LearningJobInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['train', 'evaluate', 'register']
    epochs: int = Field(default=30, ge=1, le=500)
    batch_size: int = Field(default=2, ge=1, le=64)
    device: Literal['cpu', 'mps', 'cuda'] = 'mps'
    checkpoint: str | None = None
    license: str = Field(default='', max_length=400)
    variant: Literal['nano', 'small'] = 'small'


class LearningIntakeUsage(BaseModel):
    project_id: str
    clip_id: str
    split: Literal['train','valid','test']
    frames: int
    reviewed: int

class LearningIntakeClip(BaseModel):
    id: str
    name: str
    relative_path: str
    bytes: int
    source_split: Literal['train', 'valid', 'test'] | None = None
    source_category: str | None = None
    collection_id: str
    suggested_group: str
    suggested_split: Literal['train', 'valid', 'test']
    imported_in: list[LearningIntakeUsage] = Field(default_factory=list)


class LearningIntakeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intake_id: str
    group: str | None = Field(default=None, min_length=1, max_length=120)
    split: Literal['train', 'valid', 'test'] | None = None


class LearningValidationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confidence: float = Field(default=.5, ge=.05, le=.95)
    max_frames: int = Field(default=25, ge=1, le=100)


class LearningClassResult(BaseModel):
    label: str
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float | None
    recall: float | None


class LearningValidationReport(BaseModel):
    id: str
    created_at: float
    frame_ids: list[str]
    skipped_frames: int
    confidence: float
    iou_threshold: float
    detector_provenance: dict
    label_snapshot_sha256: str
    frame_image_sha256: dict[str, str]
    classes: list[LearningClassResult]
    examples: list[dict]
    limitations: list[str]


class LearningSampleInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    interval_s: float = Field(default=5,ge=.5,le=60,allow_inf_nan=False)
    max_frames: int = Field(default=50,ge=1,le=200)
    mode: Literal['interval','coverage','every_frame'] = 'interval'
