"""Tenacity retry policy for LLM provider calls.

Retry only on transient classes (rate limit, 5xx/network). Never retry on
auth, schema parse, credential, or content-filter errors — those need user
or code action, not waiting.
"""

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from disha.llm.errors import RateLimitError, TransientError


def llm_retry() -> AsyncRetrying:
    return AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        retry=retry_if_exception_type((TransientError, RateLimitError)),
        reraise=True,
    )
