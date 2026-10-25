"""Weekly optimization tick: analyze the week, adjust targeting, email a digest.

What it does, once a week (driven by the worker):
  1. Pull the last 7 days of activity: sends, replies (by intent), new leads,
     leads reaching replied/meeting_booked.
  2. Segment reply performance by country × funding stage × industry.
  3. Ask the LLM for 2-3 insights and a sourcing reweight (which segments to
     lean into next week). If auto-sourcing is enabled, apply the reweight to
     its saved-search overrides.
  4. Email the user a digest: numbers, what was learned, what was changed.

State (last run timestamp) lives in tenant_settings.config['optimizer'] as a
JSON string — same pattern as auto_source.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.config import get_settings
from disha.db.models import (
    Account,
    Lead,
    LeadStateRow,
    Reply,
    TenantSettings,
    Thread,
    Touch,
)
from disha.db.models.enums import Channel
from disha.llm import LLMRequest, LLMRouter

logger = logging.getLogger(__name__)

_INSIGHT_SYSTEM = """\
You are a cold-outreach performance analyst. Given a week of send/reply data
segmented by country, funding stage, and industry, return JSON:

{
  "insights": ["<2-3 short, concrete observations>"],
  "reweight": {"countries": [...], "funding_stages": [...]} or null
}

Only propose a reweight when a segment has at least 2 replies AND clearly
outperforms; otherwise return null (small samples lie). Keep insights honest —
"not enough data yet" is a valid insight in early weeks.
Return ONLY JSON.
"""


async def _collect_week_metrics(
    session: AsyncSession, tenant_id: UUID, since: datetime
) -> dict[str, Any]:
    sends = (
        await session.execute(
            select(Touch, Thread, Lead, Account)
            .join(Thread, Thread.id == Touch.thread_id)
            .join(Lead, Lead.id == Thread.lead_id)
            .outerjoin(Account, Account.id == Lead.account_id)
            .where(
                Touch.tenant_id == tenant_id,
                Touch.channel == Channel.EMAIL,
                Touch.sent_at >= since,
            )
        )
    ).all()

    replies = (
        await session.execute(
            select(Reply, Touch, Lead, Account)
            .join(Touch, Touch.id == Reply.touch_id)
            .join(Thread, Thread.id == Touch.thread_id)
            .join(Lead, Lead.id == Thread.lead_id)
            .outerjoin(Account, Account.id == Lead.account_id)
            .where(Reply.tenant_id == tenant_id, Reply.received_at >= since)
        )
    ).all()

    new_leads = (
        await session.execute(
            select(Lead).where(Lead.tenant_id == tenant_id, Lead.created_at >= since)
        )
    ).scalars().all()

    booked = (
        await session.execute(
            select(LeadStateRow).where(
                LeadStateRow.tenant_id == tenant_id,
                LeadStateRow.state == "meeting_booked",
            )
        )
    ).scalars().all()

    def seg_key(account: Account | None) -> str:
        if account is None:
            return "unknown"
        return f"{account.hq_country or '??'}|{account.funding_stage or '??'}|{account.industry or '??'}"

    sends_by_seg: dict[str, int] = {}
    for _t, _th, _l, account in sends:
        k = seg_key(account)
        sends_by_seg[k] = sends_by_seg.get(k, 0) + 1

    replies_by_seg: dict[str, int] = {}
    replies_by_intent: dict[str, int] = {}
    for reply, _t, _l, account in replies:
        k = seg_key(account)
        replies_by_seg[k] = replies_by_seg.get(k, 0) + 1
        intent = (reply.classification or {}).get("intent", "unclassified")
        replies_by_intent[intent] = replies_by_intent.get(intent, 0) + 1

    segments = [
        {
            "segment": k,
            "sends": n,
            "replies": replies_by_seg.get(k, 0),
            "reply_rate": round(replies_by_seg.get(k, 0) / n, 3) if n else 0,
        }
        for k, n in sorted(sends_by_seg.items(), key=lambda kv: -kv[1])
    ]

    return {
        "sends": len(sends),
        "replies": len(replies),
        "replies_by_intent": replies_by_intent,
        "new_leads": len(new_leads),
        "meetings_booked_total": len(booked),
        "segments": segments,
    }


async def _llm_analyze(
    metrics: dict[str, Any], tenant_id: UUID
) -> dict[str, Any]:
    if metrics["sends"] == 0 and metrics["new_leads"] == 0:
        return {"insights": ["No activity this week."], "reweight": None}
    router = LLMRouter(tenant_id)
    try:
        resp = await router.call(
            LLMRequest(
                purpose="weekly_optimizer",
                prompt_version=1,
                system=_INSIGHT_SYSTEM,
                user=json.dumps(metrics, default=str),
                max_tokens=1024,
                temperature=0.2,
            )
        )
        raw = resp.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").rstrip("` \n")
        out = json.loads(raw)
        return out if isinstance(out, dict) else {"insights": [], "reweight": None}
    except Exception:  # noqa: BLE001
        logger.exception("optimizer LLM analysis failed")
        return {"insights": ["(analysis failed this week)"], "reweight": None}


def _apply_reweight(config: dict[str, Any], reweight: dict[str, Any]) -> bool:
    """Mutate auto_source overrides in `config` (in place). Returns True if changed."""
    raw = config.get("auto_source")
    if not raw:
        return False
    try:
        auto = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        return False
    if not auto.get("enabled"):
        return False
    overrides = dict(auto.get("overrides") or {})
    changed = False
    for key in ("countries", "funding_stages"):
        vals = reweight.get(key)
        if vals and isinstance(vals, list):
            overrides[key] = vals
            changed = True
    if changed:
        auto["overrides"] = overrides
        config["auto_source"] = json.dumps(auto)
    return changed


async def run_weekly_optimization(tenant_id: UUID) -> dict[str, Any]:
    """Full weekly cycle. Returns a summary (also used by the digest email)."""
    from disha.adapters.gmail import GmailAdapter
    from disha.db import admin_session

    since = datetime.now(UTC) - timedelta(days=7)

    async with admin_session() as session:
        metrics = await _collect_week_metrics(session, tenant_id, since)

    analysis = await _llm_analyze(metrics, tenant_id)
    insights: list[str] = analysis.get("insights") or []
    reweight = analysis.get("reweight")

    applied = False
    async with admin_session() as session:
        row = (
            await session.execute(
                select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        config = dict(row.config or {}) if row else {}
        if reweight:
            applied = _apply_reweight(config, reweight)
        config["optimizer"] = json.dumps(
            {"last_run_at": datetime.now(UTC).isoformat()}
        )
        if row is not None:
            row.config = config
        report_email = config.get("report_email")

    # Digest email (best-effort).
    if report_email and (metrics["sends"] or metrics["new_leads"] or metrics["replies"]):
        rate = (
            f"{metrics['replies'] / metrics['sends'] * 100:.1f}%"
            if metrics["sends"]
            else "—"
        )
        seg_lines = "\n".join(
            f"  {s['segment']}: {s['replies']}/{s['sends']} replies ({s['reply_rate']*100:.0f}%)"
            for s in metrics["segments"][:6]
        )
        body = (
            f"Your week in outbound:\n\n"
            f"  Sent: {metrics['sends']}  ·  Replies: {metrics['replies']} ({rate})\n"
            f"  New leads sourced: {metrics['new_leads']}\n"
            f"  Replies by intent: {json.dumps(metrics['replies_by_intent'])}\n\n"
            f"Segments:\n{seg_lines or '  (no sends yet)'}\n\n"
            f"Insights:\n" + "\n".join(f"  • {i}" for i in insights) + "\n\n"
            + (
                "Sourcing reweighted toward responsive segments for next week.\n"
                if applied
                else "No targeting changes this week.\n"
            )
            + f"\nDashboard: {get_settings().disha_ui_url}\n"
        )
        try:
            async with admin_session() as session:
                await GmailAdapter().send_raw(
                    session,
                    tenant_id,
                    to_addr=str(report_email),
                    subject=f"Disha weekly: {metrics['sends']} sent, {metrics['replies']} replies",
                    body=body,
                )
        except Exception:  # noqa: BLE001
            logger.exception("digest email failed (non-fatal)")

    summary = {"metrics": metrics, "insights": insights, "reweight_applied": applied}
    logger.info("weekly optimization complete", extra={"summary": str(summary)[:500]})
    return summary
