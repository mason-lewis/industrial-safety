"""Preserve reported cache-write accounting lost by the pinned PydanticAI mapper."""
from pydantic_ai.models.openai import OpenAIResponsesModel


class UsageResponsesModel(OpenAIResponsesModel):
    def _process_provider_details(self, response):
        # This documented subclass hook receives a completed SDK response. Keep
        # the provider's split, including malformed data, for strict costing.
        # Never infer a TTL from the model name or the provider's default.
        details = response.usage.input_tokens_details if response.usage else None
        split = getattr(details, 'cache_write_tokens_details', None)
        result = {'http_request_id': getattr(response, '_request_id', None)}
        if split is None:
            return result
        if hasattr(split, 'model_dump'):
            split = split.model_dump()
        return result | {'cache_write_tokens_details': split}
