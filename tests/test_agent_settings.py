import pytest
from pydantic_ai.usage import RunUsage
from app.agents.settings import AGENT_NAMES, BudgetExceeded, ModelConfig, SharedBudget


def test_all_named_agents_default_luna_without_reasoning_parameter():
    config = ModelConfig()
    assert len(AGENT_NAMES) == 4
    assert all(config.resolve(name) == "gpt-6-luna" for name in AGENT_NAMES)
    assert config.settings() == {}
    config.reasoning_effort = "low"
    assert config.settings() == {"openai_reasoning_effort": "low"}
    config.reasoning_effort = "default"
    with pytest.raises(ValueError):
        config.settings()


def test_aggregate_images_and_delegations_stop_children():
    budget = SharedBudget(image_limit=8, delegation_limit=2)
    budget.reserve_images(6)
    budget.reserve_images(2)
    with pytest.raises(BudgetExceeded):
        budget.reserve_images(1)
    assert budget.images == 8
    budget.reserve_delegation()
    budget.reserve_delegation()
    with pytest.raises(BudgetExceeded):
        budget.reserve_delegation()
    assert budget.delegations == 2


def test_shared_usage_stops_request_token_overrun_and_reserves_final():
    budget = SharedBudget(request_limit=8, token_limit=100)
    budget.check_usage(RunUsage(requests=6), reserve_final=True)
    with pytest.raises(BudgetExceeded):
        budget.check_usage(RunUsage(requests=7), reserve_final=True)
    budget.check_usage(RunUsage(requests=7))
    with pytest.raises(BudgetExceeded):
        budget.check_usage(RunUsage(requests=8))
    with pytest.raises(BudgetExceeded):
        budget.check_usage(RunUsage(input_tokens=70, output_tokens=30))
