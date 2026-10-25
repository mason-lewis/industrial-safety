from safety_app.domain.base import StrictModel as BaseModel
from pydantic import Field
from safety_app.domain.models import Annotation
from typing import Literal
import math
from pydantic import model_validator
class AnnotationRequest(BaseModel):
    frame_ids:list[str]=Field(max_length=6)
    class_registry_version:str
class AnnotationResult(BaseModel):
    drafts:list[Annotation]=Field(default_factory=list)
    class_gaps:list[str]=Field(default_factory=list)
    limitations:list[str]=Field(default_factory=list)

class ImageObjectBox(BaseModel):
    label:str=Field(min_length=1,max_length=80)
    bbox:tuple[float,float,float,float]
    @model_validator(mode='after')
    def bounds(self):
        x,y,w,h=self.bbox
        if not all(math.isfinite(v) for v in self.bbox) or not (x>=0 and y>=0 and w>0 and h>0 and x+w<=1.000001 and y+h<=1.000001):
            raise ValueError('Use normalized XYWH boxes within the image')
        return self

class ImageLabelDecision(BaseModel):
    target_id:str
    decision:Literal['accept','reject','adjust','uncertain']
    reason:str=Field(min_length=1,max_length=400)
    replacement:ImageObjectBox|None=None
    @model_validator(mode='after')
    def replacement_rule(self):
        if (self.decision=='adjust') != (self.replacement is not None):
            raise ValueError('Only an adjustment requires a replacement box')
        return self

class ImageReviewResult(BaseModel):
    decisions:list[ImageLabelDecision]=Field(max_length=200)
    additions:list[ImageObjectBox]=Field(default_factory=list,max_length=200)
    limitations:list[str]=Field(default_factory=list,max_length=20)
