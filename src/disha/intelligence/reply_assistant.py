"""Reply assistant: draft a response to an inbound reply, aimed at booking a call.

suggest_reply() loads the reply + its classification + lead context + the
tenant's voice guide, and asks the LLM for a short response that handles the
prospect's intent and moves toward ONE call to action — the user's booking
link. The user edits/approves in the Inbox UI before anything is sent.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.db.models import Account, Lead, Reply, TenantSettings, Thread, Touch, VoiceGuide
from disha.llm import LLMRequest, LLMRouter

logger = logging.getLogger(__name__)

_SYSTEM = """\
You write replies to prospects who responded to a consultant's cold outreach.
Goal: book a call. One CTA only — the booking link, offered naturally.

Rules:
- Match the prospect's energy and brevity. Never longer than their reply unless
  they asked a question that needs a real answer.
- interested / meeting_request → thank briefly, propose the call, give the link.
- question → answer it directly and confidently, then offer the call to go deeper.
- objection / not_now → acknowledge gracefully, give one light reframe, offer a
  no-pressure option ("happy to share a 2-line summary" or a future check-in).
- referral → thank them, ask for the intro, keep it effortless for them.
- Never grovel, never use "just following up", no exclamation marks spam.
- Plain text only. No subject line. Sign off with the sender's first name.

Return JSON: {"body": "<the reply text>"}
"""


def _strip_fences(raw: str) -> str:
    return re.sub(r"^```[a-z]*\n?", "", raw.strip()).rstrip("` \n")


async def suggest_reply(
    session: AsyncSession,
    tenant_id: UUID,
    reply_id: UUID,
    router: LLMRouter | None = None,
) -> dict[str, Any]:
    """Return {subject, body, to_email} — a draft response ready for user edit."""
    used_router = router or LLMRouter(tenant_id)

    reply = (
        await session.execute(select(Reply).where(Reply.id == reply_id))
    ).scalar_one()
    touch = (
        await session.execute(select(Touch).where(Touch.id == reply.touch_id))
    ).scalar_one()
    thread = (
        await session.execute(select(Thread).where(Thread.id == touch.thread_id))
    ).scalar_one()
    lead = (
        await session.execute(select(Lead).where(Lead.id == thread.lead_id))
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
    voice_summary = json.dumps(voice.config)[:800] if voice and voice.config else "professional, warm, direct"

    label = (reply.classification or {}).get("label") or (
        reply.classification or {}
    ).get("classification", "unknown")
    sender_name = config.get("sender_name", "")
    first_name = sender_name.split(" ")[0] if sender_name else ""
    cal_link = config.get("cal_link") or "(no booking link configured — suggest times instead)"

    user_prompt = f"""\
Sender: {sender_name} — {config.get("sender_title", "consultant")}
Booking link: {cal_link}
Voice guide: {voice_summary}

Prospect: {lead.first_name} {lead.last_name or ""} — {lead.role_title or ""} at {account.name if account else ""}
Classified intent: {label}

Our original email (step {touch.step_number}):
Subject: {touch.subject}
{touch.body[:800]}

Their reply:
{reply.body[:1500]}

Write the response. Sign off as {first_name or "the sender"}.
"""

    resp = await used_router.call(
        LLMRequest(
            purpose="reply_assist",
            prompt_version=1,
            system=_SYSTEM,
            user=user_prompt,
            max_tokens=1024,
            temperature=0.4,
        )
    )
    raw = _strip_fences(resp.content)
    try:
        body = json.loads(raw).get("body", raw)
    except json.JSONDecodeError:
        body = raw  # model returned plain text — fine, use as-is

    subject = touch.subject or reply.subject or ""
    if subject and not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    return {
        "subject": subject,
        "body": body,
        "to_email": reply.sender_email,
        "intent": label,
    }
