"""Scoped, audited edits to a run-local draft; no human-label or training writes."""
from pydantic_ai import RunContext
from safety_app.agents.shared.deps import AgentDeps
from safety_app.agents.shared.tools import audited
from .models import ImageLabelDecision,ImageObjectBox,ImageReviewResult

TOOL_NAMES=['decide_candidates','add_object_boxes','replace_added_boxes','inspect_draft']

class ImageDraftTools:
    def __init__(self,packet):
        self.packet=packet;self.decisions={};self.additions=[];self.used=False
        self.ids={b['id'] for b in packet['candidates']}

    def check(self,decisions,additions):
        if len(additions)>200 or sum(d.decision!='reject' for d in decisions.values())+len(additions)>200:
            raise ValueError('Maximum 200 resulting boxes')
        for d in decisions.values():
            if d.target_id not in self.ids:raise ValueError('Candidate is outside this image')
        for b in [*additions,*[d.replacement for d in decisions.values() if d.replacement]]:
            if b.label not in self.packet['classes']:raise ValueError('Unknown requested class')

    def snapshot(self):
        return {'decisions':[d.model_dump(mode='json') for d in self.decisions.values()],
                'additions':[b.model_dump(mode='json') for b in self.additions],
                'undecided_ids':sorted(self.ids-self.decisions.keys()),'approval_status':'draft'}

    def validate(self,value:ImageReviewResult):
        if self.used and ({d.target_id:d for d in value.decisions}!=self.decisions or value.additions!=self.additions):
            raise ValueError('Final decisions and additions must match the edited tool draft; inspect_draft and finish outstanding candidates')

    def tools(self):
        @audited
        async def decide_candidates(ctx:RunContext[AgentDeps],decisions:list[ImageLabelDecision])->dict:
            """Accept/reject/adjust/mark uncertain up to 200 candidates in this image draft. Replaces previous decisions by ID. Adjust includes replacement class and XYWH box. No human approval."""
            if not 1<=len(decisions)<=200 or len({d.target_id for d in decisions})!=len(decisions):raise ValueError('Use 1–200 unique candidate decisions')
            updated=self.decisions|{d.target_id:d for d in decisions};self.check(updated,self.additions)
            self.decisions=updated;self.used=True;return self.snapshot()
        @audited
        async def add_object_boxes(ctx:RunContext[AgentDeps],boxes:list[ImageObjectBox])->dict:
            """Add missing visible objects to this image draft. Exact requested classes and normalized XYWH required. Do not duplicate candidates or additions. Returns current draft."""
            if not 1<=len(boxes)<=200:raise ValueError('Use 1–200 boxes')
            updated=self.additions+boxes;self.check(self.decisions,updated)
            self.additions=updated;self.used=True;return self.snapshot()
        @audited
        async def replace_added_boxes(ctx:RunContext[AgentDeps],boxes:list[ImageObjectBox])->dict:
            """Replace the entire added-box list to correct geometry/classes or remove duplicates. Empty list removes all additions. Existing candidates use decide_candidates. Draft only."""
            self.check(self.decisions,boxes);self.additions=list(boxes);self.used=True;return self.snapshot()
        @audited
        async def inspect_draft(ctx:RunContext[AgentDeps])->dict:
            """Read current edited decisions, additions and undecided candidate IDs. No source metadata or label writes."""
            return self.snapshot()
        return [decide_candidates,add_object_boxes,replace_added_boxes,inspect_draft]
