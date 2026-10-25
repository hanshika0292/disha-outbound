from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

ModelPreference = Literal["fast", "balanced", "smart"]
Provider = Literal["gemini", "anthropic", "openai", "mock"]
EntityType = Literal["lead", "reply", "tenant", "account"]


class TokenUsage(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0


class LLMRequest(BaseModel):
    purpose: str = Field(
        description='Prompt key, e.g. "score.role_fit". Used as the audit row label.'
    )
    system: str
    user: str
    output_schema: dict[str, Any] | None = None
    model_preference: ModelPreference = "balanced"
    temperature: float = 0.0
    max_tokens: int = 1024
    prompt_version: int = Field(
        description="Version of the prompt_pack row that produced this call."
    )
    entity_type: EntityType | None = None
    entity_id: UUID | None = None


class LLMResponse(BaseModel):
    content: dict[str, Any] | str
    model_used: str
    provider: Provider
    usage: TokenUsage
    cost_usd: Decimal
    latency_ms: int
    raw_response: dict[str, Any] = Field(default_factory=dict)
