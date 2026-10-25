"""Person-only detections are supplemental evidence, never identity or safety findings."""
from typing import Literal
from pydantic import Field, model_validator
from safety_app.domain.base import StrictModel


class PersonDetection(StrictModel):
    id: str
    box: tuple[float, float, float, float]
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode='after')
    def bounds(self):
        import math
        x,y,w,h=self.box
        if not all(math.isfinite(v) for v in self.box) or min(x,y)<0 or min(w,h)<=0 or x+w>1.000001 or y+h>1.000001:
            raise ValueError('Person box must be normalized xywh inside the retained image')
        return self


class PersonDetectionResult(StrictModel):
    status: Literal['available','unavailable','failed','disabled','not_analyzed']
    people: list[PersonDetection] = Field(default_factory=list, max_length=12)
    omitted_count: int = Field(default=0, ge=0)
    confidence_threshold: float = Field(default=.5, ge=.05, le=.95)
    detector: str = 'RF-DETR Small · pretrained COCO · person only'
    provenance: dict = Field(default_factory=dict)
    detail: str = ''
    limitations: list[str] = Field(default_factory=lambda:[
        'Detections can miss people or contain false positives; an empty result does not prove nobody is present.',
        'IDs belong to this frame only. No identity recognition, cross-frame tracking, PPE or hazard classification.',
    ])


class PersonFrame(PersonDetectionResult):
    id: str
    review_run_id: str
    frame_id: str
    media_id: str
    timestamp_ms: int = Field(ge=0)
    content_hash: str


class PersonPreviewRequest(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    position_ms: int = Field(ge=0)
    confidence_threshold: float = Field(default=.5, ge=.05, le=.95)
    include_image: bool = True


class PersonPreview(PersonDetectionResult):
    source_id: str
    position_ms: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)
    image_data_url: str
    elapsed_ms: int = Field(ge=0)


class LivePersonSample(PersonPreview):
    """Transient CV on an arrived frame; image and boxes share the same timestamp."""
    review_run_id: str
    session_id: str
    run_generation: int
    frame_id: str
