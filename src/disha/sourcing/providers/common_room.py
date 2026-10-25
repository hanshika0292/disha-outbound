"""CommonRoomSource — stub. Wire when you choose Common Room as the provider.

Common Room's Prospector returns signal-rich contacts (job changes, hiring,
community/intent activity). Map each into SourceCandidate and carry the
triggering signal in `raw` so the orchestrator seeds it as a Signal — that
intent context then flows into the LLM pain-signal score.
"""

from __future__ import annotations

from disha.sourcing.base import LeadSource
from disha.sourcing.schemas import SourceCandidate, SourceQuery


class CommonRoomSource(LeadSource):
    name = "common_room"

    async def search(self, query: SourceQuery) -> list[SourceCandidate]:
        raise NotImplementedError(
            "CommonRoomSource is not wired yet. Choose Common Room as the "
            "provider to implement search() against the Common Room Prospector."
        )
