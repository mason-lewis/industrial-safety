from safety_app.domain.base import StrictModel as BaseModel
from pydantic import Field
from safety_app.domain.models import Observation
class ObserverRequest(BaseModel):
    media_ids:list[str]
    context:str=''
class ObserverResult(BaseModel):
    observations:list[Observation]=Field(default_factory=list)
    possible_events:list[str]=Field(default_factory=list)
    visibility_limits:list[str]=Field(default_factory=list)
