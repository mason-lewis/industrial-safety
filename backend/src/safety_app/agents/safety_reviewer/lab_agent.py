"""Bounded PydanticAI lab agents; specialist profiles remain in this agent folder."""
from pathlib import Path
from typing import Literal
from pydantic import Field,ValidationError
from pydantic_ai import Agent,ToolOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior
from safety_app.agents.shared.factory import GuardedModel
from safety_app.domain.chat_lab import LabAnswer,LabClaim,LabDraft

PROMPT=Path(__file__).with_name('lab_prompt.md')


class EvidenceClaim(LabClaim):
    kind: Literal['requirement','guidance','product','record']
    citation_ids: list[str] = Field(min_length=1,max_length=6,description='At least one exact source ID already retrieved or supplied for this claim.')


class NonRequirementClaim(EvidenceClaim):
    kind: Literal['guidance','product','record']


class UncertainClaim(LabClaim):
    kind: Literal['uncertain']


class SafetyClaim(EvidenceClaim):
    kind: Literal['requirement','guidance']


class GuidanceClaim(EvidenceClaim):
    kind: Literal['guidance']


class SafetyAnswer(LabAnswer):
    claims: list[SafetyClaim | UncertainClaim] = Field(default_factory=list,max_length=16)


class GuidanceAnswer(LabAnswer):
    claims: list[GuidanceClaim | UncertainClaim] = Field(default_factory=list,max_length=16)


class EvidenceAnswer(LabAnswer):
    claims: list[EvidenceClaim | UncertainClaim] = Field(default_factory=list,max_length=16)


class NonRequirementAnswer(LabAnswer):
    """Provider output contract while applicability is unresolved."""
    claims: list[NonRequirementClaim | UncertainClaim] = Field(default_factory=list,max_length=16)


def answer_contract(output_type, applicability, task=None):
    if output_type is LabDraft:return draft_contract(task,applicability)
    if output_type is LabAnswer:
        if task=='safety':
            return SafetyAnswer if (applicability or {}).get('requirements_allowed',False) else GuidanceAnswer
        return EvidenceAnswer if (applicability or {}).get('requirements_allowed',False) else NonRequirementAnswer
    return output_type


class ProductClaim(EvidenceClaim):
    kind: Literal['product']


class RecordClaim(EvidenceClaim):
    kind: Literal['record']


class GeneralClaim(LabClaim):
    kind: Literal['general']
    citation_ids: list[str] = Field(default_factory=list, max_length=0, description='General model guidance has no invented source citations.')


class ProductDraft(LabDraft):
    claims: list[ProductClaim | UncertainClaim] = Field(max_length=12)


class RecordDraft(LabDraft):
    claims: list[RecordClaim | UncertainClaim] = Field(max_length=12)


class GuidanceDraft(LabDraft):
    claims: list[GuidanceClaim | GeneralClaim | UncertainClaim] = Field(max_length=12)


class SafetyDraft(LabDraft):
    claims: list[SafetyClaim | GeneralClaim | UncertainClaim] = Field(max_length=12)


class VisualClaim(EvidenceClaim):
    kind: Literal['visual']


class VideoDraft(LabDraft):
    claims: list[VisualClaim | RecordClaim | GuidanceClaim | GeneralClaim | UncertainClaim] = Field(max_length=12)


class VideoRegulatoryDraft(LabDraft):
    claims: list[VisualClaim | RecordClaim | SafetyClaim | GeneralClaim | UncertainClaim] = Field(max_length=12)


def draft_contract(task, applicability):
    if task=='product':return ProductDraft
    if task=='records':return RecordDraft
    if task=='video':return VideoRegulatoryDraft if (applicability or {}).get('requirements_allowed') else VideoDraft
    return SafetyDraft if (applicability or {}).get('requirements_allowed') else GuidanceDraft


class LabOutputRejected(RuntimeError):
    """A failed output contract, without exposing its unchecked draft."""


def output_failure(name, error):
    """Unwrap PydanticAI's retry wrapper without making another provider request."""
    if not isinstance(error, UnexpectedModelBehavior) or 'maximum output retries' not in str(error):
        return error, []
    cause = error
    visited = set()
    while cause is not None and id(cause) not in visited:
        visited.add(id(cause))
        if isinstance(cause, ValidationError):
            # ValidationError.__str__ includes rejected input. Keep only schema metadata.
            details = cause.errors(include_input=False, include_url=False, include_context=False)
            issues = [{'field': '.'.join(map(str, item['loc']))[:120] or 'output',
                       'code': item['type'],
                       'message': item['msg'] if item['type'] != 'value_error' else 'Output constraint was not satisfied'}
                      for item in details[:5]]
            summary = '; '.join(f"{item['field']}: {item['message']}" for item in issues)
            return LabOutputRejected(f'{name}: {summary}. Agent-level output retries are disabled.'), issues
        cause = cause.__cause__ or cause.__context__
    return LabOutputRejected(f'{name}: the provider did not return a valid structured result. Agent-level output retries are disabled.'), []


def create(model,context,output_type,tools=(),prompt=PROMPT,native=False):
    kind=getattr(context,'workflow_kind','chat_comparison')
    config={'workflow_kind':kind,'observable_stream':context.paid,'prompt_version':context.config['version'],'image_preflight':kind=='video_chat'}
    if kind=='video_chat' and (context.name.startswith(('Inspect frames','Choose relevant','Current source','Applicability','Safety team coordination')) or context.name.endswith(' specialist')):
        config['completion_headroom']=5
    if native:config.update(native_input_allowance=context.config['web_input_allowance_tokens'],native_fee_allowance=context.config['web_search_usd'],native_search_price=context.config['web_search_usd'])
    guarded=GuardedModel(model,context.ledger,context.audit,context.node,context.job.run_id,context.job.id,context.name,context.paid,context.cancelled,config)
    output=output_type if output_type is str else ToolOutput(output_type,strict=True)
    agent=Agent(guarded,name=context.name,output_type=output,instructions=context.prompts.get(prompt.parent.name+'/'+prompt.name,prompt.read_text())+'\nAssigned profile: '+getattr(context,'profile',''),retries=0,tool_timeout=10)
    for tool in tools:agent.tool_plain(tool,sequential=True)
    return agent
