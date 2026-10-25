"""In-process token-bucket rate limiter, keyed by tenant_id.

TODO(week-8): replace with Redis token bucket for multi-worker deployments.
"""

from __future__ import annotations

import time
from typing import Final
from uuid import UUID

from fastapi import HTTPException

PLAN_RATE_LIMITS: Final[dict[str, float]] = {
    "starter": 60.0,
    "growth": 300.0,
    "pro": 1000.0,
    "scale": 5000.0,
    # legacy aliases
    "agency": 300.0,
    "enterprise": 5000.0,
}


# tenant_id (str) → (last_refill_ts, tokens_available)
_buckets: dict[str, tuple[float, float]] = {}


def check_rate_limit(tenant_id: UUID, plan: str) -> None:
    """Raise HTTPException(429) with Retry-After if bucket empty.

    Token capacity = plan limit (req/min). Refill = capacity tokens per 60s.
    """
    capacity = PLAN_RATE_LIMITS.get(plan, PLAN_RATE_LIMITS["starter"])
    refill_per_sec = capacity / 60.0
    key = str(tenant_id)
    now = time.monotonic()
    last_refill, tokens = _buckets.get(key, (now, capacity))

    elapsed = now - last_refill
    tokens = min(capacity, tokens + elapsed * refill_per_sec)
    if tokens < 1.0:
        retry_after = max(1, int((1.0 - tokens) / refill_per_sec))
        _buckets[key] = (now, tokens)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({plan} plan: {int(capacity)}/min)",
            headers={"Retry-After": str(retry_after)},
        )
    _buckets[key] = (now, tokens - 1.0)


def reset_buckets() -> None:
    """Test helper — clears all in-process state."""
    _buckets.clear()
