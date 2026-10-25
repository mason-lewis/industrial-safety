"""Text-only safety questions; these outputs never create incident findings."""
from datetime import date
from typing import Literal
from pydantic import Field
from safety_app.agents.shared.contracts import AgentOutput as StrictModel
from safety_app.domain.models import Job, uid, utc
from .models import Specialty

class ChatSession(StrictModel):
    id: str = Field(default_factory=uid)
    site_id: str
    title: str = Field(min_length=1,max_length=100)
    created_at: str = Field(default_factory=utc)
    review_run_id: str | None = None

class ChatSessionInput(StrictModel):
    title: str = Field(default='Safety questions',min_length=1,max_length=100)

class ChatMessageInput(StrictModel):
    message: str = Field(min_length=1,max_length=1800)
    idempotency_key: str = Field(min_length=1,max_length=100)
    mode: Literal['hosted','fixture'] = 'hosted'
    event_date: date | None = None

class ChatTask(StrictModel):
    question_id: str = Field(min_length=1,max_length=80)
    specialist: Specialty
    question: str = Field(min_length=10,max_length=700)
    phase: Literal['assessment','follow_up'] = 'assessment'
    previous_call_id: str | None = None

class ChatClaim(StrictModel):
    id: str = Field(default='',max_length=80)
    text: str = Field(min_length=1,max_length=700)
    kind: Literal['requirement','guidance','uncertain']
    source_refs: list[str] = Field(default_factory=list,max_length=5)
    rule_refs: list[str] = Field(default_factory=list,max_length=5)
    exact_clause: str | None = None

class ChatAssessment(StrictModel):
    call_id: str = ''
    question_id: str = ''
    specialist: Specialty
    answer: str = Field(min_length=1,max_length=1800)
    claims: list[ChatClaim] = Field(default_factory=list,max_length=4)
    missing_context: list[str] = Field(default_factory=list)

class ChatAnswer(StrictModel):
    answer: str = Field(min_length=1,max_length=4000)
    completion: Literal['complete','partial'] = 'complete'
    claims: list[ChatClaim] = Field(default_factory=list,max_length=32)
    missing_context: list[str] = Field(default_factory=list)

class ChatChallengeIssue(StrictModel):
    claim_id: str
    explanation: str = Field(min_length=1,max_length=700)

class ChatChallenge(StrictModel):
    checked_claim_ids: list[str] = Field(default_factory=list,max_length=32)
    issues: list[ChatChallengeIssue] = Field(default_factory=list,max_length=32)
    limitations: list[str] = Field(default_factory=list,max_length=12)

class ChatCitation(StrictModel):
    id: str
    title: str
    section: str
    clause: str | None = None
    url: str | None = None
    version: str
    text: str
    source_type: str
    jurisdiction: str
    effective_date: str | None = None
    effective_until: str | None = None
    context_complete: bool = False

class ChatResult(StrictModel):
    id: str
    session_id: str
    site_id: str
    mode: Literal['hosted','fixture']
    response: ChatAnswer
    challenge: ChatChallenge
    delegations: list[ChatAssessment]
    citations: list[ChatCitation]
    created_at: str = Field(default_factory=utc)

class ChatTurn(StrictModel):
    job: Job
    result: ChatResult | None = None

class ChatState(StrictModel):
    session: ChatSession
    turns: list[ChatTurn]
