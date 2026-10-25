"""The coordinator supplies immutable scope. The model cannot grant permissions."""
from dataclasses import dataclass,field
from types import MappingProxyType
from typing import Callable
from safety_app.infrastructure.media_store import MediaStore
from safety_app.infrastructure.audit import Audit
from safety_app.infrastructure.budgets import BudgetLedger
from safety_app.modules.safety_review.sources import Sources
from safety_app.modules.safety_review.rules import Rules
from safety_app.domain.models import CameraProfile,ClassRegistry,BoxProposalResult,Annotation
@dataclass(frozen=True)
class Scope:
    site_id:str
    media_ids:frozenset[str]
    intervals:dict[str,tuple[int,int]]
    camera_ids:frozenset[str]
    frame_ids:frozenset[str]=frozenset()
    source_ids:frozenset[str]=frozenset()
    class_registry_version:str='1'
    def __post_init__(self):object.__setattr__(self,'intervals',MappingProxyType(dict(self.intervals)))
@dataclass
class AgentDeps:
    media:MediaStore
    sources:Sources
    rules:Rules
    audit:Audit
    budgets:BudgetLedger
    scope:Scope
    run_id:str
    workflow_id:str
    job_id:str
    camera_context:Callable[[str],CameraProfile]
    classes:Callable[[str],ClassRegistry]
    proposals:Callable[[str,list[int]],BoxProposalResult]
    cancelled:Callable[[],bool]=lambda:False
    seen_frames:set[str]=field(default_factory=set)
    seen_sources:set[str]=field(default_factory=set)
    seen_rules:set[str]=field(default_factory=set)
    agent_name:str=''
    delegate:Callable|None=None
    output_check:Callable|None=None
    specialist:str|None=None
    event_date:str|None=None
    source_bytes:int=2400
    fetched_source_ids:set[str]=field(default_factory=set)
    complete_source_ids:set[str]=field(default_factory=set)
    source_gaps:set[str]=field(default_factory=set)
    prepared_sources:object|None=None
    workflow_kind:str=''
    budget_omitted_source_ids:set[str]=field(default_factory=set)
    video_memory:Callable|None=None
    person_detections:Callable|None=None
    safety_guide:dict=field(default_factory=dict)
    detail_frame_ids:set[str]=field(default_factory=set)
    detail_calls:int=0
    detail_limit:int=0
    budget_workflow_id:str|None=None
    frame_alias_targets:dict[str,set[str]]=field(default_factory=dict)
    def record_source_packet(self,packet):
        self.source_gaps.update(packet.gaps)
        self.seen_sources.update(s.id for s in packet.excerpts)
        self.fetched_source_ids.update(s.id for s in packet.excerpts)
        # These service-generated warnings prove the scoped source exists,
        # but its exact text was not delivered. Missing/foreign IDs do not qualify.
        for gap in packet.gaps:
            for suffix in (': complete excerpt exceeds remaining evidence budget',': omitted to fit packet budget'):
                if gap.endswith(suffix):self.budget_omitted_source_ids.add(gap.removesuffix(suffix))
        self.budget_omitted_source_ids.difference_update(self.seen_sources)
        if packet.complete:self.complete_source_ids.update(s.id for s in packet.excerpts)
    def interval(self,media_id,start_ms,end_ms):
        if media_id not in self.scope.media_ids:raise ValueError('Media outside assigned scope')
        lo,hi=self.scope.intervals[media_id]
        if not lo<=start_ms<=end_ms<=hi:raise ValueError('Interval outside assigned scope')
        if end_ms-start_ms>self.budgets.config['limits']['clip_ms']:raise ValueError('Clip exceeds run limit')
