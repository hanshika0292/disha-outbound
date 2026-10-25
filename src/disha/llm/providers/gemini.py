"""Gemini provider using google-genai native structured output.

Uses `generation_config.response_schema` for JSON outputs when output_schema is set.
Falls back to plain text otherwise. Token usage comes from `usage_metadata` on the
provider response — we never estimate locally.
"""

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


def _parse_structured(raw: str) -> dict[str, Any] | None:
    """Parse a JSON object from the model. Strict first, then salvage: recover
    complete top-level string/number fields from output truncated mid-stream
    (the residual Gemini-truncation case if thinking-budget=0 isn't enough)."""
    import re

    text = re.sub(r"^```[a-z]*\n?", "", raw.strip()).rstrip("` \n")
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start == -1:
        return None

    # Balanced-brace scan honouring string escapes — find the first complete obj.
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    out = json.loads(text[start : i + 1])
                    return out if isinstance(out, dict) else None
                except json.JSONDecodeError:
                    break

    # Truncated mid-object: regex out the complete "key": "value" string pairs.
    salvaged: dict[str, Any] = {}
    for m in re.finditer(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"', text):
        salvaged[m.group(1)] = m.group(2).replace('\\"', '"').replace("\\n", "\n")
    for m in re.finditer(r'"(\w+)"\s*:\s*(-?\d+(?:\.\d+)?)', text):
        if m.group(1) not in salvaged:
            num = m.group(2)
            salvaged[m.group(1)] = float(num) if "." in num else int(num)
    return salvaged or None


def _classify_gemini_error(exc: Exception) -> Exception:
    msg = str(exc).lower()
    if "permission" in msg or "api key" in msg or "unauthorized" in msg or "401" in msg:
        return AuthError(str(exc))
    if "rate" in msg or "quota" in msg or "429" in msg:
        return RateLimitError(str(exc))
    if "safety" in msg or "blocked" in msg or "filter" in msg:
        return ContentFilterError(str(exc))
    if "timeout" in msg or "unavailable" in msg or "502" in msg or "503" in msg or "504" in msg:
        return TransientError(str(exc))
    return TransientError(str(exc))


class GeminiProvider(LLMProvider):
    name = "gemini"

    async def complete(
        self,
        request: LLMRequest,
        model: str,
        api_key: str,
    ) -> LLMResponse:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        gen_config: dict[str, Any] = {
            "temperature": request.temperature,
            "max_output_tokens": request.max_tokens,
            "system_instruction": request.system,
        }
        if request.output_schema is not None:
            gen_config["response_mime_type"] = "application/json"
            gen_config["response_schema"] = request.output_schema
        # Gemini 2.5 models "think" by default, spending the output-token budget
        # on hidden reasoning before emitting the answer — which truncates JSON
        # mid-string (the recurring SchemaError). Disable thinking so the full
        # budget goes to the actual structured output.
        try:
            gen_config["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:  # noqa: BLE001 — older SDKs lack ThinkingConfig
            pass

        started = time.perf_counter()
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=request.user,
                config=types.GenerateContentConfig(**gen_config),
            )
        except Exception as exc:
            raise _classify_gemini_error(exc) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        usage = TokenUsage(
            tokens_in=getattr(response.usage_metadata, "prompt_token_count", 0) or 0,
            tokens_out=getattr(response.usage_metadata, "candidates_token_count", 0) or 0,
        )

        raw_text = response.text or ""
        content: dict[str, Any] | str
        if request.output_schema is not None:
            content = _parse_structured(raw_text)
            if content is None:
                raise SchemaError(
                    f"Gemini returned non-JSON despite response_schema: {raw_text[:200]}"
                )
        else:
            content = raw_text

        return LLMResponse(
            content=content,
            model_used=model,
            provider="gemini",
            usage=usage,
            cost_usd=cost_usd("gemini", model, usage),
            latency_ms=latency_ms,
            raw_response={"text": raw_text},
        )
