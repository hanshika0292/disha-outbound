"""Anthropic provider — structured output via tool-use forcing.

Anthropic's structured-output pattern: define a single tool whose input_schema
is the desired output, force the model to call it, then read the tool input as
the structured payload. More reliable than JSON-mode prompting.
"""

from __future__ import annotations

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

_STRUCTURED_TOOL_NAME = "return_structured_output"


def _classify_anthropic_error(exc: Exception) -> Exception:
    try:
        import anthropic
    except ImportError:
        return TransientError(str(exc))
    if isinstance(exc, anthropic.AuthenticationError):
        return AuthError(str(exc))
    if isinstance(exc, anthropic.RateLimitError):
        return RateLimitError(str(exc))
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return TransientError(str(exc))
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return TransientError(str(exc))
        if exc.status_code == 429:
            return RateLimitError(str(exc))
        return AuthError(str(exc)) if exc.status_code in (401, 403) else TransientError(str(exc))
    return TransientError(str(exc))


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    async def complete(
        self,
        request: LLMRequest,
        model: str,
        api_key: str,
    ) -> LLMResponse:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=api_key)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "temperature": request.temperature,
        }
        if request.output_schema is not None:
            kwargs["tools"] = [
                {
                    "name": _STRUCTURED_TOOL_NAME,
                    "description": "Return the structured output for this request.",
                    "input_schema": request.output_schema,
                }
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": _STRUCTURED_TOOL_NAME}

        started = time.perf_counter()
        try:
            response = await client.messages.create(**kwargs)
        except Exception as exc:
            raise _classify_anthropic_error(exc) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        usage = TokenUsage(
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
        )

        content: dict[str, Any] | str
        if request.output_schema is not None:
            tool_block = next(
                (
                    b
                    for b in response.content
                    if b.type == "tool_use" and b.name == _STRUCTURED_TOOL_NAME
                ),
                None,
            )
            if tool_block is None:
                if response.stop_reason in ("safety", "refusal"):
                    raise ContentFilterError(
                        f"Anthropic refused: stop_reason={response.stop_reason}"
                    )
                raise SchemaError(
                    f"Anthropic didn't call {_STRUCTURED_TOOL_NAME}; "
                    f"stop_reason={response.stop_reason}"
                )
            content = dict(tool_block.input) if isinstance(tool_block.input, dict) else {}
        else:
            text_block = next((b for b in response.content if b.type == "text"), None)
            content = text_block.text if text_block else ""

        return LLMResponse(
            content=content,
            model_used=model,
            provider="anthropic",
            usage=usage,
            cost_usd=cost_usd("anthropic", model, usage),
            latency_ms=latency_ms,
            raw_response={"stop_reason": response.stop_reason},
        )
