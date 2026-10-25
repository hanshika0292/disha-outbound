"""ApolloSource — stub. Wire when you choose Apollo as the provider.

Apollo's People Search maps cleanly onto SourceQuery:
  titles          -> person_titles[]
  industries      -> q_organization_industry_tag_ids / industry keywords
  countries       -> person_locations[]
  funding_stages  -> organization funding stage filters
  employee_min/max-> organization_num_employees_ranges[]
  limit           -> per_page

Implement search() to call Apollo (via the Apollo integration / API key),
map each result into SourceCandidate, and put Apollo's raw record in `raw`
so the orchestrator can seed signals from it.
"""

from __future__ import annotations

from disha.sourcing.base import LeadSource
from disha.sourcing.schemas import SourceCandidate, SourceQuery


class ApolloSource(LeadSource):
    name = "apollo"

    async def search(self, query: SourceQuery) -> list[SourceCandidate]:
        raise NotImplementedError(
            "ApolloSource is not wired yet. Choose Apollo as the provider to "
            "implement search() against the Apollo People Search API."
        )
