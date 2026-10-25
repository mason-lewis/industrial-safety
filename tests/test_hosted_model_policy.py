"""Offline enforcement tests; changing aliases cannot permit another hosted model."""
import json
import shutil
from pathlib import Path
import pytest
from app.agents.model_policy import HOSTED_MODEL,validate_agent_models
from app.agents.settings import AGENT_NAMES,ModelConfig
from safety_app.infrastructure.runtime import Runtime
from safety_app.infrastructure.budgets import BudgetBlocked

@pytest.fixture
def policy_rt(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[1]/'configs',tmp_path/'configs')
    return Runtime(tmp_path)

@pytest.mark.parametrize('model',['gpt-6','gpt-6-astra','gpt-6-sol','gpt-5'])
def test_legacy_model_overrides_and_environment_are_rejected(model,monkeypatch):
    with pytest.raises(ValueError,match='pinned'):ModelConfig(default_model=model)
    with pytest.raises(ValueError,match='pinned'):ModelConfig(overrides={AGENT_NAMES[0]:model})
    monkeypatch.setenv('AI_DEFAULT_MODEL',model)
    with pytest.raises(ValueError,match='pinned'):ModelConfig.from_env()
    monkeypatch.setenv('AI_DEFAULT_MODEL',HOSTED_MODEL)
    monkeypatch.setenv('AI_MODEL_POLICY_REVIEWER',model)
    with pytest.raises(ValueError,match='pinned'):ModelConfig.from_env()

def test_runtime_and_preflight_reject_conflicting_aliases_before_reservation(policy_rt):
    rt=policy_rt
    assert {rt.config['aliases'][a] for a in rt.config['agents'].values()}=={HOSTED_MODEL}
    rt.config['aliases']['other']='gpt-6-astra'
    with pytest.raises(ValueError,match='pinned'):validate_agent_models(rt.config)
    with pytest.raises(BudgetBlocked,match='pinned'):rt.budgets.reserve('run','workflow','gpt-6-astra',10,10)
    assert not rt.budgets.status()['outstanding']
    rows=rt.audit.rows(event_type='budget_blocked')
    assert rows and 'pinned' in rows[0]['resources']['block_reason']
    path=rt.root/'configs/agents.yaml';path.write_text(json.dumps(rt.config))
    with pytest.raises(ValueError,match='pinned'):Runtime(rt.root)

@pytest.mark.asyncio
async def test_foreign_hosted_model_is_blocked_before_transport_dispatch(policy_rt):
    import httpx
    from openai import AsyncOpenAI
    from pydantic_ai import Agent,models
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from safety_app.agents.shared.factory import GuardedModel
    called=[]
    def transport(request):called.append(request);raise AssertionError('Wrong model reached transport')
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client=AsyncOpenAI(api_key='fictional',base_url='https://mock.invalid/v1',http_client=http)
        foreign=OpenAIResponsesModel('gpt-6-astra',provider=OpenAIProvider(openai_client=client))
        agent=Agent(GuardedModel(foreign,policy_rt.budgets,policy_rt.audit,'run','workflow'))
        with models.override_allow_model_requests(True):
            with pytest.raises(BudgetBlocked,match='pinned'):await agent.run('Fictional offline fixture')
    assert not called and not policy_rt.budgets.status()['outstanding']

def test_unknown_alias_is_rejected(policy_rt):
    policy_rt.config['agents']['annotation_assistant']='missing'
    with pytest.raises(ValueError,match='unknown model alias'):validate_agent_models(policy_rt.config)

def test_growing_specialist_context_fits_configured_allowance_and_still_has_hard_cap(policy_rt):
    rt=policy_rt
    assert rt.config['limits']['input_tokens']==64000
    # The failed turn had 3,495 reported inputs before its enlarged third request.
    first=rt.budgets.reserve('specialist','workflow',HOSTED_MODEL,4551,1000)
    rt.budgets.reconcile(first,.0003282,1507,355)
    second=rt.budgets.reserve('specialist','workflow',HOSTED_MODEL,8328,1000)
    rt.budgets.reconcile(second,.0003488,1988,300)
    third=rt.budgets.reserve('specialist','workflow',HOSTED_MODEL,11000,1000)
    rt.budgets.reconcile(third,.0012,11000,200)
    with pytest.raises(BudgetBlocked,match='Run token limit'):
        rt.budgets.reserve('another','another',HOSTED_MODEL,64001,10)

def test_request_allowance_is_bounded_per_agent_and_shared_workflow(policy_rt):
    rt=policy_rt
    assert rt.config['limits']['requests']==10
    for i in range(10):
        id=rt.budgets.reserve('agent','workflow',HOSTED_MODEL,10,10)
        rt.budgets.reconcile(id,0,10,10)
    with pytest.raises(BudgetBlocked,match='Physical request limit'):
        rt.budgets.reserve('agent','workflow',HOSTED_MODEL,10,10)
    for i in range(rt.config['review_team']['max_workflow_requests']-10):
        id=rt.budgets.reserve('other-'+str(i),'workflow',HOSTED_MODEL,10,10)
        rt.budgets.reconcile(id,0,10,10)
    with pytest.raises(BudgetBlocked,match='Workflow request allowance'):
        rt.budgets.reserve('last','workflow',HOSTED_MODEL,10,10)

def test_tool_allowance_remains_hard_bounded(policy_rt):
    assert policy_rt.config['limits']['tools']==16
    for _ in range(16):policy_rt.budgets.tool('agent')
    with pytest.raises(BudgetBlocked,match='Tool call limit'):
        policy_rt.budgets.tool('agent')

def test_completion_headroom_leaves_requests_for_boss_and_challenge(policy_rt):
    from safety_app.infrastructure.budgets import CompletionHeadroomReached
    rt=policy_rt;rt.config['review_team']['max_workflow_requests']=8
    for i in range(2):
        id=rt.budgets.reserve(str(i),'workflow',HOSTED_MODEL,10,10,completion_headroom=6)
        rt.budgets.reconcile(id,0,10,10)
    with pytest.raises(CompletionHeadroomReached):rt.budgets.reserve('child','workflow',HOSTED_MODEL,10,10,completion_headroom=6)
    for i in range(6):
        id=rt.budgets.reserve('completion-'+str(i),'workflow',HOSTED_MODEL,10,10)
        rt.budgets.reconcile(id,0,10,10)
    with pytest.raises(BudgetBlocked,match='Workflow request allowance'):rt.budgets.reserve('excess','workflow',HOSTED_MODEL,10,10)
