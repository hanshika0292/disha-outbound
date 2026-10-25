"""LinkedIn message generation: a connection-invite note + a post-accept message.

LinkedIn outreach is two beats, not one:
  1. invite_note   — attached to the connection request. Hard 300-char limit
                     (LinkedIn truncates beyond it). Must earn the accept on its
                     own: specific, warm, no pitch.
  2. connected_note — sent AFTER they accept. More room (~600 chars) to give
                     context and a soft CTA toward a call.

Both are written in the tenant's voice and grounded in the lead + any
enrichment hook, mirroring the email drafter.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.db.models import Account, Lead, TenantSettings, VoiceGuide
from disha.llm import LLMRequest, LLMRouter

logger = logging.getLogger(__name__)

INVITE_MAX = 300

_SYSTEM = """\
You write LinkedIn outreach for a consultant. Produce TWO messages as JSON:

{
  "invite_note": "<connection-request note, MAX 280 characters, no pitch — a
     specific, warm reason to connect referencing them or their company.
     First name only. No links. No 'I help X do Y' boilerplate.>",
  "connected_note": "<message to send AFTER they accept, max ~600 chars.
     Warmer, gives one line of relevant context about why you reached out,
     ends with ONE soft CTA toward a short call. Sign off with the sender's
     first name.>"
}

Rules:
- Sound human and peer-to-peer, never salesy or fawning.
- Ground every claim in the provided context; invent nothing.
- invite_note MUST be ≤280 characters. Count carefully.
- Return ONLY the JSON object.
"""


def _strip(raw: str) -> str:
    return re.sub(r"^```[a-z]*\n?", "", raw.strip()).rstrip("` \n")


async def generate_linkedin_messages(
    session: AsyncSession,
    tenant_id: UUID,
    lead_id: UUID,
    router: LLMRouter | None = None,
) -> dict[str, str]:
    """Return {invite_note, connected_note}. invite_note is hard-capped to 300."""
    used_router = router or LLMRouter(tenant_id)

    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id))
    ).scalar_one()
    account = (
        await session.execute(select(Account).where(Account.id == lead.account_id))
    ).scalar_one_or_none()

    settings_row = (
        await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    config = dict(settings_row.config) if settings_row else {}

    voice = (
        await session.execute(
            select(VoiceGuide).where(
                VoiceGuide.tenant_id == tenant_id, VoiceGuide.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    voice_summary = json.dumps(voice.config)[:600] if voice and voice.config else "warm, direct, peer-to-peer"

    raw = lead.raw or {}
    enrichment = raw.get("enrichment") or {}
    hook = enrichment.get("hook") or raw.get("public_activity") or ""
    company_summary = enrichment.get("company_summary") or (account.raw or {}).get("summary", "") if account else ""

    sender_name = config.get("sender_name", "")
    first_name = sender_name.split(" ")[0] if sender_name else "the sender"

    user_prompt = f"""\
Sender: {sender_name} — {config.get("sender_title", "consultant")}, {config.get("sender_city", "")}
Voice guide: {voice_summary}

Prospect: {lead.first_name} {lead.last_name or ""} — {lead.role_title or ""} at {account.name if account else ""}
Company: {company_summary[:300]}
Personalization hook (if any): {hook[:300]}

Write the two messages. Sign the connected_note as {first_name}.
"""

    resp = await used_router.call(
        LLMRequest(
            purpose="linkedin_messages",
            prompt_version=1,
            system=_SYSTEM,
            user=user_prompt,
            max_tokens=1024,
            temperature=0.5,
        )
    )

    parsed: Any = resp.content
    if not isinstance(parsed, dict):
        try:
            parsed = json.loads(_strip(str(resp.content)))
        except json.JSONDecodeError:
            parsed = {}
    # The model occasionally wraps the object in a list — unwrap it.
    if isinstance(parsed, list):
        parsed = next((x for x in parsed if isinstance(x, dict)), {})
    if not isinstance(parsed, dict):
        parsed = {}

    invite = str(parsed.get("invite_note", "")).strip()
    connected = str(parsed.get("connected_note", "")).strip()

    # Hard-enforce the LinkedIn invite limit.
    if len(invite) > INVITE_MAX:
        invite = invite[: INVITE_MAX - 1].rstrip() + "…"

    return {"invite_note": invite, "connected_note": connected}
