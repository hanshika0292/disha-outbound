"""ICP rubric → SourceQuery.

Discovery and scoring share one definition of 'good fit': the categorical
rubric dimensions (funding / size / geo) become search filters, so candidates
we pull are the same ones the scorer would rate highly. Titles/industries
aren't in the rubric (role fit is LLM-scored), so they default sensibly and
are overridable from the UI or tenant settings.
"""

from __future__ import annotations

from typing import Any

from disha.sourcing.schemas import SourceQuery

_FUNDING_KEEP_THRESHOLD = 0.6
_GEO_KEEP_THRESHOLD = 0.5
_SIZE_KEEP_THRESHOLD = 0.8

_DEFAULT_TITLES = [
    "Founder",
    "CEO",
    "CTO",
    "VP Engineering",
    "Head of Product",
    "Head of Data",
]
_DEFAULT_INDUSTRIES = ["Artificial Intelligence", "SaaS", "Software"]


def _dimension(config: dict[str, Any], key: str) -> dict[str, Any] | None:
    for dim in config.get("dimensions", []):
        if dim.get("key") == key:
            return dim
    return None


def _keep_categorical(config: dict[str, Any], key: str, threshold: float) -> list[str]:
    dim = _dimension(config, key)
    if not dim or dim.get("scoring") != "categorical":
        return []
    rules: dict[str, float] = dim.get("rules", {})
    return [k for k, v in rules.items() if isinstance(v, (int, float)) and v >= threshold]


def _employee_range(config: dict[str, Any]) -> tuple[int | None, int | None]:
    """Parse the highest-scoring size buckets ('11-50', '500+') into a min/max."""
    dim = _dimension(config, "fit_size")
    if not dim or dim.get("scoring") != "categorical":
        return None, None
    rules: dict[str, float] = dim.get("rules", {})
    kept = [k for k, v in rules.items() if isinstance(v, (int, float)) and v >= _SIZE_KEEP_THRESHOLD]
    lows: list[int] = []
    highs: list[int] = []
    for bucket in kept:
        b = bucket.replace(" ", "")
        if b.endswith("+"):
            lows.append(int(b[:-1]))
        elif "-" in b:
            lo, hi = b.split("-", 1)
            lows.append(int(lo))
            highs.append(int(hi))
    return (min(lows) if lows else None, max(highs) if highs else None)


def icp_to_query(
    config: dict[str, Any],
    *,
    overrides: dict[str, Any] | None = None,
    limit: int = 25,
) -> SourceQuery:
    """Build a SourceQuery from an ICP rubric config dict. `overrides` (UI fields
    or saved settings) win over the derived defaults for any provided key."""
    overrides = overrides or {}

    funding = _keep_categorical(config, "fit_funding", _FUNDING_KEEP_THRESHOLD)
    geo = [c for c in _keep_categorical(config, "fit_geo", _GEO_KEEP_THRESHOLD) if c != "other"]
    emp_min, emp_max = _employee_range(config)

    keywords = overrides.get("keywords")
    if not keywords:
        name = config.get("name", "")
        keywords = [name] if name else []

    query = SourceQuery(
        titles=overrides.get("titles") or _DEFAULT_TITLES,
        industries=overrides.get("industries") or _DEFAULT_INDUSTRIES,
        countries=overrides.get("countries") or geo,
        funding_stages=overrides.get("funding_stages") or funding,
        employee_min=overrides.get("employee_min", emp_min),
        employee_max=overrides.get("employee_max", emp_max),
        keywords=keywords,
        limit=overrides.get("limit") or limit,
    )
    return query
