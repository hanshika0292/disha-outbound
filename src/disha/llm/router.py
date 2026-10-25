"""LLMRouter: picks provider+model, runs the call under retry, logs to llm_calls.

Audit logging uses a fresh admin_session so the audit row is persisted
regardless of whether the caller's transaction commits or rolls back. That
means we always know what was called, even on failure.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import ClassVar
from uuid import UUID, uuid4

from disha.db import admin_session
from disha.db.models import LLMCall
from disha.llm.base import LLMProvider
from disha.llm.credentials import resolve_api_key
from disha.llm.errors import LLMError
from disha.llm.providers import AnthropicProvider, GeminiProvider, OpenAIProvider
from disha.llm.retry import llm_retry
from disha.llm.schemas import LLMRequest, LLMResponse, ModelPreference

DEFAULT_MODEL_MAP: dict[ModelPreference, tuple[str, str]] = {
    "fast": ("gemini", "gemini-2.5-flash"),
    "balanced": ("gemini", "gemini-2.5-flash"),
    "smart": ("gemini", "gemini-2.5-pro"),
}


class LLMRouter:
    """Per-tenant router. Stateless apart from a small provider registry."""

    _PROVIDERS: ClassVar[dict[str, type[LLMProvider]]] = {
        "gemini": GeminiProvider,
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
    }

    def __init__(
        self,
        tenant_id: UUID,
        *,
        provider_override: LLMProvider | None = None,
    ) -> None:
        """provider_override forces calls through one provider (used by MockProvider tests)."""
        self.tenant_id = tenant_id
        self._override = provider_override

    def _resolve(self, request: LLMRequest) -> tuple[LLMProvider, str, str]:
        if self._override is not None:
            return self._override, self._override.name, "mock-model"
        provider_name, model = DEFAULT_MODEL_MAP[request.model_preference]
        provider_cls = self._PROVIDERS[provider_name]
        return provider_cls(), provider_name, model

    async def call(self, request: LLMRequest) -> LLMResponse:
        provider, provider_name, model = self._resolve(request)

        if self._override is not None:
            api_key = "mock"
        else:
            api_key = await resolve_api_key(self.tenant_id, provider_name)

        started = time.perf_counter()
        response: LLMResponse | None = None
        error: Exception | None = None
        try:
            async for attempt in llm_retry():
                with attempt:
                    response = await provider.complete(request, model, api_key)
        except LLMError as exc:
            error = exc
        except Exception as exc:
            error = exc

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await self._write_audit(
            request=request,
            provider_name=provider_name if response is None else response.provider,
            model=model if response is None else response.model_used,
            response=response,
            error=error,
            elapsed_ms=elapsed_ms,
        )

        if error is not None:
            raise error
        assert response is not None
        return response

    async def _write_audit(
        self,
        *,
        request: LLMRequest,
        provider_name: str,
        model: str,
        response: LLMResponse | None,
        error: Exception | None,
        elapsed_ms: int,
    ) -> None:
        tokens_in = response.usage.tokens_in if response else 0
        tokens_out = response.usage.tokens_out if response else 0
        cost = response.cost_usd if response else Decimal("0")
        latency = response.latency_ms if response else elapsed_ms

        row = LLMCall(
            id=uuid4(),
            tenant_id=self.tenant_id,
            purpose=request.purpose,
            prompt_version=request.prompt_version,
            provider=provider_name,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            latency_ms=latency,
            entity_type=request.entity_type,
            entity_id=request.entity_id,
            success=error is None,
            error_message=str(error)[:1000] if error else None,
        )
        async with admin_session() as session:
            session.add(row)
