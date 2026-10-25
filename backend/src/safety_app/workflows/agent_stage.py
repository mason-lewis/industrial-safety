"""Shared scoped execution for review, chat, and periodic observer stages."""
import asyncio
import importlib
import json
import os

from openai import AsyncOpenAI
from pydantic_ai import BinaryContent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from safety_app.infrastructure.responses_model import UsageResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from safety_app.agents.shared.deps import AgentDeps, Scope
from safety_app.domain.models import ToolFailure, uid
from safety_app.modules.safety_review.rules import Rules


class AgentStageTimeout(RuntimeError):
    """Terminal stage deadline; not a retryable tool-argument failure."""


class AgentOutputRejected(RuntimeError):
    """Output failed a substantive check after the bounded correction attempt."""


class TaskInput:
    """Serialize coordinator-owned context without adding an agent output schema."""

    def __init__(self, value):
        self.value = value

    def model_dump(self, **kwargs):
        return self.value

    def model_dump_json(self):
        return json.dumps(self.value)


def _unavailable_label_proposals(*args):
    raise ValueError('Label tools unavailable in a safety review')


def dependencies(rt, job, name):
    media_ids = job.payload['media_ids']
    media = [rt.db.get('media', media_id) for media_id in media_ids]
    intervals = {
        item['id']: (
            max(item['start_ms'], job.payload.get('start_ms', item['end_ms'] - 10000)),
            min(item['end_ms'], job.payload.get('end_ms', item['end_ms'])),
        )
        for item in media
    }
    return AgentDeps(
        media=rt.media,
        sources=rt.sources,
        rules=Rules(rt.db),
        audit=rt.audit,
        budgets=rt.budgets,
        scope=Scope(
            site_id=job.site_id,
            media_ids=frozenset(media_ids),
            intervals=intervals,
            camera_ids=frozenset(item['camera_id'] for item in media),
            source_ids=frozenset(job.payload.get('document_ids', [])),
        ),
        run_id=uid(),
        workflow_id=job.run_id,
        job_id=job.id,
        camera_context=rt.camera,
        classes=rt.classes,
        proposals=_unavailable_label_proposals,
        cancelled=lambda: rt.jobs.cancelled(job.id),
        agent_name=name,
        event_date=job.payload.get('event_date'),
        source_bytes=rt.config.get('review_team', {}).get('source_bytes', 2400),
        budget_workflow_id=job.payload.get('budget_workflow_id') if job.kind=='video_safety_review' else None,
    )


def _stage_content(rt, job, name, request, deps, text_only):
    content = [request.model_dump_json(), json.dumps({
        'review_id': job.run_id,
        'event_id': job.payload.get('event_id'),
        'site_id': job.site_id,
        'scope': {media_id: list(interval) for media_id, interval in deps.scope.intervals.items()},
    })]
    count = max(1, 6 // max(1, len(deps.scope.media_ids)))
    attached = 0
    for media_id, (start_ms, end_ms) in deps.scope.intervals.items():
        batch = rt.media.frames(media_id, start_ms, end_ms, count)
        if batch.coverage_gaps:
            content.append(json.dumps({'coverage_gaps': batch.coverage_gaps}))
        for frame in batch.frames:
            if attached >= 6:
                break
            if name in ('video_observer', 'challenge_reviewer'):
                content.extend([
                    frame.model_dump_json(),
                    BinaryContent(data=rt.media.model_image(frame.id), media_type='image/jpeg'),
                ])
            deps.seen_frames.add(frame.id)
            from safety_app.agents.shared.evidence_refs import register_frame
            register_frame(deps,frame,f'f{attached+1}')
            attached += 1
    if not attached and not text_only:
        raise FileNotFoundError('No footage available. Review cannot complete')
    return content


def _create_agent(name, model, deps, agent_builder):
    if agent_builder:
        return agent_builder(model, deps)
    if name.startswith('specialist_'):
        from safety_app.agents.safety_reviewer.specialists import create
        return create(model, deps, name.removeprefix('specialist_'))
    return importlib.import_module(f'safety_app.agents.{name}.agent').create(model, deps)


def _record_failure(rt, job, name, deps, exc):
    """Audit the terminal error and return any translated exception to the caller."""
    error = exc
    if isinstance(exc, UnexpectedModelBehavior) and 'maximum output retries' in str(exc):
        failures = rt.audit.rows(1, event_type='validation_failed', run_id=deps.run_id)
        if failures:
            reason = failures[0]['error']['message']
            error = AgentOutputRejected(f'{name}: output rejected after bounded correction: {reason}')
    if isinstance(exc, TimeoutError):
        error = AgentStageTimeout(
            f'{name} exceeded its configured stage deadline; partial audit and uncertain charges are retained'
        )
    rt.audit.write(
        'run_failed',
        status='cancelled' if isinstance(exc, asyncio.CancelledError) else 'incomplete',
        run_id=deps.run_id,
        job_id=job.id,
        agent_name=name,
        error=ToolFailure(code=type(error).__name__, message=str(error)),
    )
    return error


CACHE_LIFETIME_KINDS=('video_analysis','live_screening','video_safety_review','team_summary')

async def run_stage(
    rt, job, name, request, model_factory=None, previous=None, setup=None,
    parent_run_id=None, agent_builder=None, text_only=False,
    content_builder=None,
    settings_override=None,
    audit_provenance=None,
):
    deps = dependencies(rt, job, name)
    deps.workflow_kind = job.kind
    content = (content_builder or _stage_content)(rt, job, name, request, deps, text_only)
    if previous:
        deps.seen_sources.update(previous.seen_sources)
        deps.seen_rules.update(previous.seen_rules)
        deps.complete_source_ids.update(previous.complete_source_ids)
    if setup:
        setup(deps)
    if job.kind in ('video_analysis','live_screening','video_safety_review') and hasattr(rt,'people') and not getattr(deps,'skip_person_tools',False):
        run_id=job.payload.get('budget_workflow_id') or job.run_id
        deps.person_detections=lambda frame_id:rt.people.get(run_id,frame_id)
        from safety_app.agents.shared.people import person_summary
        summary=person_summary(deps)
        if summary:content.append(json.dumps(summary))
    if deps.prepared_sources:
        content.append(deps.prepared_sources.model_dump_json())

    client = None
    config_name = 'safety_reviewer' if name.startswith('specialist_') or name == 'team_combiner' else name
    name_alias = rt.config['agents'][config_name]
    model_id = rt.config['aliases'][name_alias]
    if model_factory:
        model = model_factory(name)
    else:
        if rt.budgets.state('stopped') == '1':
            raise ValueError('Paid requests stopped')
        if os.getenv('SAFETY_PAID_REVIEW') != '1' or not os.getenv('OPENAI_API_KEY'):
            raise ValueError('Paid review requires SAFETY_PAID_REVIEW=1 and backend credentials')
        from safety_app.infrastructure.provider_transport import review_client
        client = review_client(rt.config['limits']['agent_seconds'])
        model = UsageResponsesModel(model_id, provider=OpenAIProvider(openai_client=client))

    parent_run_id = parent_run_id or job.run_id
    rt.audit.write(
        'run_started', status='running', run_id=deps.run_id,
        parent_run_id=parent_run_id, job_id=job.id, agent_name=name,
        arguments=request.model_dump(mode='json'),
        configuration={'requested_alias': name_alias, 'model_id': model_id},
        # Orchestration links (delegation call, detail inspection) for the live agent tree.
        provenance=audit_provenance or {},
    )
    try:
        agent = _create_agent(name, model, deps, agent_builder)
        limits = rt.config['limits']
        deadline = limits['workflow_seconds'] if name == 'safety_reviewer' else limits['agent_seconds']
        async with asyncio.timeout(deadline):
            settings = settings_override if settings_override is not None else {
                'openai_reasoning_effort': rt.config.get('review_team', {}).get('chat_reasoning_effort', 'low'),
            } if text_only else None
            settings = {'openai_store': False, **(settings or {})}
            # An explicit cache lifetime keeps reported cache writes priceable (otherwise the charge stays unknown).
            if job.kind in CACHE_LIFETIME_KINDS:
                settings.setdefault('openai_prompt_cache_options', {'mode': 'implicit', 'ttl': '30m'})
            result = await agent.run(
                content, deps=deps, model_settings=settings,
                usage_limits=UsageLimits(
                    request_limit=limits['requests'], input_tokens_limit=limits['input_tokens'],
                    output_tokens_limit=limits['output_tokens'], tool_calls_limit=limits['tools'],
                ),
            )
        if deps.cancelled():
            raise asyncio.CancelledError()
        rt.audit.write(
            'run_finished', run_id=deps.run_id, parent_run_id=parent_run_id,
            job_id=job.id, agent_name=name, output=result.output.model_dump(mode='json'),
        )
        return result.output, deps
    except BaseException as exc:
        error = _record_failure(rt, job, name, deps, exc)
        if isinstance(error, AgentOutputRejected):
            raise error from exc
        if error is not exc:
            raise error
        raise
    finally:
        if client:
            await client.close()
