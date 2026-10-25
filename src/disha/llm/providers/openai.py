"""OpenAI provider using native `response_format: json_schema` for structured output."""

from __future__ import annotations

import json
import time
from typing import Any

from disha.llm.base import LLMProvider
from disha.llm.errors import (
    AuthError,
    ContentFilterError,
    RateLimitError,
    SchemaError,
    TransientError,
)
from disha.llm.pricing import cost_usd
from disha.llm.schemas import LLMRequest, LLMResponse, TokenUsage


def _classify_openai_error(exc: Exception) -> Exception:
    try:
        import openai
    except ImportError:
        return TransientError(str(exc))
    if isinstance(exc, openai.AuthenticationError):
        return AuthError(str(exc))
    if isinstance(exc, openai.RateLimitError):
        return RateLimitError(str(exc))
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return TransientError(str(exc))
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code >= 500:
            return TransientError(str(exc))
        if exc.status_code == 429:
            return RateLimitError(str(exc))
        return AuthError(str(exc)) if exc.status_code in (401, 403) else TransientError(str(exc))
    return TransientError(str(exc))


def _normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI strict mode requires additionalProperties:false at every object level."""
    if schema.get("type") == "object" and "additionalProperties" not in schema:
        schema = {**schema, "additionalProperties": False}
    return schema


class OpenAIProvider(LLMProvider):
    name = "openai"

    async def complete(
        self,
        request: LLMRequest,
        model: str,
        api_key: str,
    ) -> LLMResponse:
        import openai

        client = openai.AsyncOpenAI(api_key=api_key)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_completion_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
        }
        if request.output_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "schema": _normalize_schema(request.output_schema),
                    "strict": False,
                },
            }

        started = time.perf_counter()
        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise _classify_openai_error(exc) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        choice = response.choices[0]
        if choice.finish_reason == "content_filter":
            raise ContentFilterError("OpenAI content filter triggered")

        raw_text = choice.message.content or ""
        usage = TokenUsage(
            tokens_in=response.usage.prompt_tokens if response.usage else 0,
            tokens_out=response.usage.completion_tokens if response.usage else 0,
        )

        content: dict[str, Any] | str
        if request.output_schema is not None:
            try:
                content = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise SchemaError(f"OpenAI returned non-JSON: {raw_text[:200]}") from exc
        else:
            content = raw_text

        return LLMResponse(
            content=content,
            model_used=model,
            provider="openai",
            usage=usage,
            cost_usd=cost_usd("openai", model, usage),
            latency_ms=latency_ms,
            raw_response={"finish_reason": choice.finish_reason},
        )
