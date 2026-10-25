from __future__ import annotations

import os
from dataclasses import dataclass, field
from app.agents.model_policy import HOSTED_MODEL,require_hosted_model

AGENT_NAMES = ('safety_coordinator', 'visual_evidence_analyst', 'policy_reviewer', 'incident_response_analyst')

@dataclass
class ModelConfig:
    default_model: str = HOSTED_MODEL
    overrides: dict[str, str] = field(default_factory=dict)
    reasoning_effort: str | None = None

    def __post_init__(self):
        require_hosted_model(self.default_model)
        for model in self.overrides.values():require_hosted_model(model)

    def resolve(self, name: str) -> str:
        if name not in AGENT_NAMES:
            raise ValueError('Unknown agent')
        return require_hosted_model(self.overrides.get(name) or self.default_model)

    def settings(self) -> dict:
        # Responses uses standard mode by omission; do not send "default".
        if self.reasoning_effort is None:
            return {}
        if self.reasoning_effort not in ('none', 'low', 'medium', 'high', 'xhigh', 'max'):
            raise ValueError('Unsupported reasoning effort override')
        return {'openai_reasoning_effort': self.reasoning_effort}

    @classmethod
    def from_env(cls):
        config=cls(
            default_model=os.getenv('AI_DEFAULT_MODEL', HOSTED_MODEL),
            overrides={name: value for name in AGENT_NAMES if (value := os.getenv(f'AI_MODEL_{name.upper()}'))},
            reasoning_effort=os.getenv('AI_REASONING_EFFORT') or None,
        )
        require_hosted_model(config.default_model)
        for model in config.overrides.values():require_hosted_model(model)
        return config

class BudgetExceeded(RuntimeError):
    pass

@dataclass
class SharedBudget:
    request_limit: int = 8
    token_limit: int = 16000
    image_limit: int = 32
    delegation_limit: int = 2
    images: int = 0
    delegations: int = 0
    requests: int = 0
    transmitted_images: int = 0
    trace: list[dict] = field(default_factory=list)

    def reserve_images(self, count: int):
        if count < 0 or self.images + count > self.image_limit:
            raise BudgetExceeded('Shared image budget exhausted')
        self.images += count

    def reserve_delegation(self):
        if self.delegations >= self.delegation_limit:
            raise BudgetExceeded('Shared specialist delegation budget exhausted')
        self.delegations += 1

    def check_usage(self, usage, reserve_final: bool = False):
        limit = self.request_limit - (1 if reserve_final else 0)
        if usage.requests >= limit or usage.total_tokens >= self.token_limit:
            raise BudgetExceeded('Shared model request/token budget exhausted')

    def limits(self, reserve_final: bool = False):
        from pydantic_ai.usage import UsageLimits
        return UsageLimits(request_limit=self.request_limit - int(reserve_final), total_tokens_limit=self.token_limit, tool_calls_limit=24)
