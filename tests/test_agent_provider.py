import pytest
from app.agents.reviewers import ReviewDeps, build_agents
from app.agents.settings import AGENT_NAMES, ModelConfig
from app.evidence.store import EvidenceScope, EvidenceStore
from app.models.contracts import LabConfig
from app.storage.repository import Repository

@pytest.mark.asyncio
async def test_provider_clients_disable_unaccounted_sdk_retries(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','not-a-real-key')
    deps=ReviewDeps(EvidenceStore(tmp_path/'evidence'),Repository(tmp_path/'runs.sqlite'),LabConfig(),EvidenceScope('s',0,0))
    config=ModelConfig()
    agents=build_agents(config,deps)
    assert set(agents)==set(AGENT_NAMES)
    assert deps.provider_client.max_retries==0
    assert all(agent.model.model_name=='gpt-6-luna' for agent in agents.values())
    assert all(agent.model_settings=={} for agent in agents.values())
    assert deps.budget.requests==0
    await deps.provider_client.close()
