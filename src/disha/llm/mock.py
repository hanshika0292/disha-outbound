"""MockProvider — deterministic, rules-based responses for tests.

Fixtures shape:
    {"rules": [
        {"match": {"purpose": "score.role_fit", "user_contains": "Aditya"},
         "response": {"content": {...}, "tokens_in": 80, "tokens_out": 50}},
        ...
    ]}

The first rule whose `match` predicates ALL hold wins. `match` supports:
- "purpose": exact match on request.purpose
- "user_contains": substring match on request.user

If no rule matches, raises MockProviderError loudly so tests can't silently
fall through to "looks generic enough" responses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from disha.llm.base import LLMProvider
from disha.llm.errors import LLMError
from disha.llm.pricing import cost_usd
from disha.llm.schemas import LLMRequest, LLMResponse, TokenUsage


class MockProviderError(LLMError):
    """Raised when no rule matches the request."""


def _rule_matches(match: dict[str, Any], request: LLMRequest) -> bool:
    if "purpose" in match and match["purpose"] != request.purpose:
        return False
    if "user_contains" in match and match["user_contains"] not in request.user:
        return False
    return True


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, fixtures_path: Path):
        self._fixtures_path = fixtures_path
        self._rules: list[dict[str, Any]] | None = None

    def _load(self) -> list[dict[str, Any]]:
        if self._rules is None:
            data = json.loads(self._fixtures_path.read_text())
            self._rules = data.get("rules", [])
        return self._rules

    async def complete(
        self,
        request: LLMRequest,
        model: str,
        api_key: str,
    ) -> LLMResponse:
        del api_key
        for rule in self._load():
            if _rule_matches(rule["match"], request):
                recorded = rule["response"]
                usage = TokenUsage(
                    tokens_in=recorded.get("tokens_in", 50),
                    tokens_out=recorded.get("tokens_out", 50),
                )
                return LLMResponse(
                    content=recorded["content"],
                    model_used=model,
                    provider="mock",
                    usage=usage,
                    cost_usd=cost_usd("mock", "mock-model", usage),
                    latency_ms=recorded.get("latency_ms", 10),
                    raw_response={"mock": True, "match": rule["match"]},
                )
        raise MockProviderError(
            f"No mock rule matched purpose={request.purpose!r}. "
            f"user starts: {request.user[:120]!r}. "
            f"Add a rule to {self._fixtures_path.name}."
        )
