"""LeadSource ABC + provider registry.

Mirrors the LLMRouter pattern: one interface, swappable providers. The only
piece that changes between Apollo / Common Room / web research is the concrete
search() implementation — everything downstream (dedup, scoring, approval) is
shared. MockSource is the default so the whole flow is demoable with no
external account.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from disha.sourcing.schemas import SourceCandidate, SourceQuery

DEFAULT_PROVIDER = "mock"


class LeadSource(ABC):
    name: str

    @abstractmethod
    async def search(self, query: SourceQuery) -> list[SourceCandidate]:
        """Return candidates matching the query (best-effort, up to query.limit)."""
        raise NotImplementedError


def get_source(name: str | None = None, tenant_id: UUID | None = None) -> LeadSource:
    """Resolve a provider by name. Unknown/None falls back to the default.

    Real providers are stubbed until you deliberately wire one — choosing a
    provider is a one-file change, no plumbing.
    """
    provider = (name or DEFAULT_PROVIDER).strip().lower()

    if provider == "mock":
        from disha.sourcing.providers.mock import MockSource

        return MockSource()
    if provider == "apollo":
        from disha.sourcing.providers.apollo import ApolloSource

        return ApolloSource()
    if provider == "common_room":
        from disha.sourcing.providers.common_room import CommonRoomSource

        return CommonRoomSource()
    if provider == "web":
        from disha.sourcing.providers.web import WebResearchSource

        return WebResearchSource(tenant_id=tenant_id)

    raise ValueError(
        f"unknown sourcing provider {provider!r}; "
        "expected one of: mock, apollo, common_room, web"
    )
