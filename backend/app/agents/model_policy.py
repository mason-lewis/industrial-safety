"""One hosted language model across current and compatibility workflows."""
HOSTED_MODEL = 'gpt-6-luna'

def require_hosted_model(model: str) -> str:
    if model != HOSTED_MODEL:
        raise ValueError(f'Project hosted model is pinned to {HOSTED_MODEL}; conflicting model {model!r} is blocked')
    return model

def validate_agent_models(config: dict):
    aliases=config.get('aliases',{})
    if not aliases:raise ValueError('Agent model aliases are missing')
    for model in aliases.values():require_hosted_model(model)
    for alias in config.get('agents',{}).values():
        if alias not in aliases:raise ValueError('Agent references an unknown model alias')
        require_hosted_model(aliases[alias])
