"""Live Gemini integration test. Off by default; opt in with DISHA_RUN_LIVE_LLM=1.

Verifies the GeminiProvider actually works against the real API end-to-end.
Costs ~$0.0001 per run on gemini-2.5-flash. Skip if you don't have GEMINI_API_KEY.

  DISHA_RUN_LIVE_LLM=1 uv run pytest tests/test_live_gemini.py
"""

from __future__ import annotations

import os

import pytest

from disha.llm.providers.gemini import GeminiProvider
from disha.llm.schemas import LLMRequest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("DISHA_RUN_LIVE_LLM") != "1",
        reason="live LLM tests require DISHA_RUN_LIVE_LLM=1",
    ),
    pytest.mark.skipif(
        not os.getenv("GEMINI_API_KEY"),
        reason="GEMINI_API_KEY is unset",
    ),
]


async def test_real_gemini_structured_output() -> None:
    provider = GeminiProvider()
    req = LLMRequest(
        purpose="test.live_gemini",
        system="You return a JSON object with a single integer field 'answer'.",
        user="What is 2 + 2?",
        output_schema={
            "type": "object",
            "properties": {"answer": {"type": "integer"}},
            "required": ["answer"],
        },
        model_preference="fast",
        prompt_version=1,
    )
    response = await provider.complete(req, "gemini-2.5-flash", os.environ["GEMINI_API_KEY"])
    assert response.provider == "gemini"
    assert isinstance(response.content, dict)
    assert response.content["answer"] == 4
    assert response.usage.tokens_in > 0
    assert response.usage.tokens_out > 0
    assert response.cost_usd > 0
