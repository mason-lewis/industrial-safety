"""Versioned contracts for the testing-only, read-only comparison lab."""
from typing import Literal
from pydantic import Field, model_validator, field_validator
from safety_app.domain.base import StrictModel
from safety_app.domain.models import Job, uid, utc

Branch = Literal['current', 'optimized']
LabState = Literal['queued', 'running', 'completed', 'blocked', 'cancelled', 'failed']
Domain = Literal['applicability', 'vehicles', 'surfaces', 'machinery', 'energy', 'health', 'emergency', 'ppe', 'multiple']
SpecialistDomain = Literal['applicability', 'vehicles', 'surfaces', 'machinery', 'energy', 'health', 'emergency', 'ppe']


class LabRouting(StrictModel):
    scope: Literal['allow', 'block', 'clarify']
    task: Literal['product', 'records', 'safety', 'video', 'mixed', 'unclear']
    domain: Domain = 'applicability'
    freshness: Literal['required', 'optional', 'unneeded'] = 'unneeded'
    confidence: float = Field(ge=0, le=1)
    policy_version: str = 'scope-1'


class LabAssignment(StrictModel):
    domain: SpecialistDomain
    question: str = Field(min_length=1,max_length=700)

class LabPlan(StrictModel):
    assignments: list[LabAssignment] = Field(min_length=1,max_length=3)
    missing_context: list[str] = Field(default_factory=list)


class LabClaim(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1200)
    kind: Literal['requirement', 'guidance', 'general', 'uncertain', 'product', 'record', 'visual']
    citation_ids: list[str] = Field(default_factory=list, max_length=6, description='Exact retrieved source IDs. Required for requirement, guidance, product and record claims. General guidance and uncertain gaps may be uncited; never invent source support.')
    exact_clause: str | None = None
    depends_on: list[str] = Field(default_factory=list, max_length=8, description='IDs of blocks whose qualifications or context are essential to this block. Prefer self-contained blocks.')


class LabExecutionContext(StrictModel):
    task: Literal['product', 'records', 'safety', 'video', 'regulatory', 'coordination']
    domain: Domain | None = None
    collections: list[Literal['product', 'records', 'references', 'site_rules']] = Field(default_factory=list)


class LabDraft(StrictModel):
    claims: list[LabClaim] = Field(max_length=12, description='Ordered self-contained answer blocks. These texts ARE the user-facing answer; do not write a second narrative. Include essential qualifications in the same block.')
    missing_context: list[str] = Field(default_factory=list)


class LabRecovery(StrictModel):
    attempts: int = 0
    limit_usd: float = .01
    calculated_usd: float = 0
    reserved_usd: float = 0
    issue_codes: list[str] = Field(default_factory=list)
    succeeded: bool = False


class LabAnswer(StrictModel):
    answer: str = Field(min_length=1, max_length=20000)
    claims: list[LabClaim] = Field(default_factory=list, max_length=16)
    missing_context: list[str] = Field(default_factory=list)
    outcome: Literal['answered', 'partial', 'clarification', 'declined', 'unavailable'] | None = None
    block_rendered: bool = False
    completion: Literal['complete', 'partial', 'clarification', 'incomplete'] = 'complete'


class LabApplicability(StrictModel):
    requirements_allowed: bool = False
    jurisdiction: str = Field(default='unknown', max_length=200, description='Established jurisdiction, or unknown when it has not been established.')
    basis: str = Field(max_length=2000, description='Brief applicability rationale, preferably 2–4 sentences; at most 2000 characters. Do not write the final safety answer. Put missing facts in missing_context.')
    citation_ids: list[str] = Field(default_factory=list, max_length=5)
    missing_context: list[str] = Field(default_factory=list)


class LabClaimCheck(StrictModel):
    claim_id: str = Field(min_length=1, max_length=80)
    verdict: Literal['supported', 'general', 'uncertain', 'unsupported', 'contradicted']
    independently_publishable: bool = Field(default=False, description='True only if this exact block is safe and useful on its own, with all necessary qualifications and saved human decisions preserved.')
    note: str = Field(min_length=1, max_length=350, description='Brief verification finding about this claim and the supplied evidence; no private reasoning or replacement advice.')


class LabReviewIssue(StrictModel):
    code: Literal['unsupported_advice', 'contradiction', 'missing_claim', 'unverified_requirement', 'scope_violation', 'citation_mismatch', 'record_uncertainty_lost']
    claim_id: str | None = Field(default=None, max_length=80, description='Exact claim ID, or null for a narrative-only defect.')
    detail: str = Field(min_length=1, max_length=500, description='Concrete blocking defect in the answer. Positive observations, style preferences and preserved evidence gaps are not defects.')


class LabChallengeAssessment(StrictModel):
    claim_checks: list[LabClaimCheck] = Field(max_length=16, description='Exactly one judgment for every answer.claims ID. Do not omit, duplicate or invent IDs.')
    blocking_issues: list[LabReviewIssue] = Field(default_factory=list, max_length=8)
    observations: list[str] = Field(default_factory=list, max_length=4, description='Optional nonblocking wording suggestions or positive findings. These do not control release.')


class LabChallenge(StrictModel):
    approved: bool
    checked_claim_ids: list[str] = Field(default_factory=list, max_length=16, description='Exact IDs from answer.claims that were checked. IDs only, without explanatory text.')
    issues: list[str] = Field(default_factory=list, max_length=16)
    unresolved_claims: list[str] = Field(default_factory=list, max_length=16, description='Exact IDs of uncertain claims whose facts remain unresolved. IDs only; explanations belong in issues. Approved unresolved claims must already have kind uncertain.')
    claim_checks: list[LabClaimCheck] = Field(default_factory=list, max_length=16)
    observations: list[str] = Field(default_factory=list, max_length=4)
    failure_codes: list[str] = Field(default_factory=list, max_length=40)
    published_claim_ids: list[str] = Field(default_factory=list)
    omitted_claim_ids: list[str] = Field(default_factory=list)


class LabScopeResult(StrictModel):
    scope: Literal['allow', 'block', 'clarify']
    confidence: float = Field(ge=0, le=1)


class LabCitation(StrictModel):
    id: str
    title: str
    text: str
    kind: Literal['requirement', 'guidance', 'site_rule', 'procedure', 'product', 'record', 'web_summary', 'visual']
    version: str
    url: str | None = None
    retrieved_at: str | None = None
    clause: str | None = None
    context_complete: bool = False
    jurisdiction: str | None = None
    effective_date: str | None = None
    provenance: dict = Field(default_factory=dict)


class LabTestCase(StrictModel):
    id: str
    version: str = '1'
    title: str
    question: str = Field(min_length=1, max_length=1800)
    expected_scope: Literal['allow', 'block', 'clarify']
    expected_sources: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class LabComparisonInput(StrictModel):
    question: str = Field(min_length=1, max_length=1800)
    session_id: str | None = None
    test_case_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=100)
    mode: Literal['fixture', 'hosted'] = 'fixture'
    order: list[Branch] = Field(default_factory=lambda: ['current', 'optimized'], min_length=2, max_length=2)
    event_date: str | None = None

    @model_validator(mode='after')
    def validate_order(self):
        if set(self.order) != {'current', 'optimized'}:
            raise ValueError('Run each workflow exactly once')
        if not self.question.strip():
            raise ValueError('Enter a question')
        if self.event_date:
            from datetime import date
            date.fromisoformat(self.event_date)
        return self


class LabUsage(StrictModel):
    calculated_usd: float = 0
    reserved_usd: float = 0
    unknown_requests: int = 0
    responses_usd: float = 0
    decisions_usd: float = 0
    search_usd: float = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    tool_calls: int = 0
    cache_hits: int = 0
    billed_usd: float | None = None
    synthetic: bool = False


class LabBranchResult(StrictModel):
    branch: Branch
    state: LabState = 'queued'
    not_started: bool = False
    recovery: LabRecovery = Field(default_factory=LabRecovery)
    issue_codes: list[str] = Field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None
    execution_ms: float = 0
    queue_ms: float = 0
    routing: LabRouting | None = None
    answer: LabAnswer | None = None
    challenge: LabChallenge | None = None
    citations: list[LabCitation] = Field(default_factory=list)
    usage: LabUsage = Field(default_factory=LabUsage)
    error: str | None = None
    quality: dict = Field(default_factory=dict)


class LabComparison(StrictModel):
    id: str
    session_id: str
    site_id: str
    job: Job
    question: str
    mode: Literal['fixture', 'hosted']
    order: list[Branch]
    snapshot: dict = Field(default_factory=dict)
    versions: dict = Field(default_factory=dict)
    branches: list[LabBranchResult]
    usage: LabUsage = Field(default_factory=LabUsage)
    parent_limit_usd: float = .15
    live_web_differences: list[str] = Field(default_factory=list)
    ratings: list[dict] = Field(default_factory=list)


class LabTelemetry(StrictModel):
    sequence: int
    timestamp_utc: str
    branch: Branch | None = None
    event_type: str
    state: LabState
    node_id: str | None = None
    parent_id: str | None = None
    agent: str | None = None
    api: Literal['responses', 'decisions', 'web_search', 'local', 'coordinator'] = 'coordinator'
    model: str | None = None
    tool: str | None = None
    tool_call_id: str | None = None
    duration_ms: float | None = None
    arguments: dict | None = None
    result: dict | list | None = None
    resources: dict = Field(default_factory=dict)
    raw: dict = Field(default_factory=dict)


class LabRatingInput(StrictModel):
    branch: Branch
    rating: int = Field(ge=1, le=5)
    note: str = Field(default='', max_length=1000)


class LabCapabilities(StrictModel):
    enabled: bool
    hosted_available: bool
    model: str = 'gpt-6-luna'
    workflow_version: str
    default_mode: Literal['fixture'] = 'fixture'
    execution: Literal['sequential'] = 'sequential'
