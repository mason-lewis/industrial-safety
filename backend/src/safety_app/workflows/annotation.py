"""Bounded annotation assistant. Results are drafts; human approval uses another route."""
import asyncio
import os
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from openai import AsyncOpenAI
from pydantic_ai.usage import UsageLimits
from safety_app.agents.shared.deps import Scope,AgentDeps
from safety_app.agents.annotation_assistant.agent import create
from safety_app.agents.annotation_assistant.models import AnnotationRequest
from safety_app.modules.safety_review.sources import Sources
from safety_app.modules.safety_review.rules import Rules
from safety_app.modules.labeling.service import Labeling
from safety_app.domain.models import uid
async def assist(rt,job,model_factory=None):
    ids=job.payload['frame_ids'];frames=[rt.db.get('frame',id) for id in ids];mids={f['media_id'] for f in frames};media=[rt.db.get('media',id) for id in mids]
    deps=AgentDeps(rt.media,Sources(rt.db),Rules(rt.db),rt.audit,rt.budgets,Scope(job.site_id,frozenset(mids),{m['id']:(m['start_ms'],m['end_ms']) for m in media},frozenset(m['camera_id'] for m in media),frozenset(ids),class_registry_version=job.payload['class_registry_version']),uid(),job.run_id,job.id,rt.camera,rt.classes,Labeling(rt).propose_boxes,lambda:rt.jobs.cancelled(job.id),agent_name='annotation_assistant')
    client=None
    if model_factory:model=model_factory('annotation_assistant')
    else:
        if os.getenv('SAFETY_PAID_REVIEW')!='1':raise ValueError('Paid review requires explicit opt-in')
        client=AsyncOpenAI(api_key=os.environ.get('OPENAI_API_KEY'),max_retries=0);model=OpenAIResponsesModel(rt.config['aliases'][rt.config['agents']['annotation_assistant']],provider=OpenAIProvider(openai_client=client))
    try:
        from safety_app.agents.annotation_assistant.tools import get_label_frames
        from types import SimpleNamespace
        packet=get_label_frames(SimpleNamespace(deps=deps),ids)
        async with asyncio.timeout(rt.config['limits']['agent_seconds']):
            result=await create(model,deps).run([AnnotationRequest(**job.payload).model_dump_json(),*packet.content],deps=deps,usage_limits=UsageLimits(request_limit=3,tool_calls_limit=8,input_tokens_limit=12000,output_tokens_limit=2000))
        if rt.jobs.cancelled(job.id):raise asyncio.CancelledError()
        refs=[]
        for draft in result.output.drafts:Labeling(rt).annotate(draft);refs.append(draft.id)
        rt.audit.write('run_finished',job_id=job.id,run_id=deps.run_id,agent_name='annotation_assistant',output=result.output.model_dump(mode='json'));return refs
    finally:
        if client:await client.close()
