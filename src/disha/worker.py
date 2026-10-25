"""Background worker: the app's heartbeat.

Runs forever, on intervals (WORKER_SEQUENCE_INTERVAL and WORKER_POLL_INTERVAL
env vars, in seconds):

  • sequence tick  (default 15 min) — drafts + sends due cadence steps for
    approved/in-sequence leads, queues LinkedIn touches for manual send.
  • gmail poll     (default 5 min)  — fetches new replies, classifies them,
    and applies lead state transitions.

Design notes
────────────
- Each tick opens its own short transaction (same rationale as sourcing's
  source_and_score: never hold a connection across slow LLM/network calls
  longer than necessary; a failed tick never poisons the next one).
- If Gmail isn't connected yet, ticks log a one-line skip and keep looping —
  the worker is safe to deploy before OAuth is configured.
- Multi-tenant: every loop re-lists active tenants and ticks each one, so
  one tenant's failure never blocks the others.

Run: `disha worker`  (wired as a separate Fly process group).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from disha.config import get_settings

logger = logging.getLogger(__name__)



async def _active_tenant_ids() -> list[UUID]:
    """All active tenants — re-queried each loop pass so newly invited
    accounts start getting worker service without a restart."""
    from sqlalchemy import select

    from disha.db import admin_session
    from disha.db.models import Tenant

    async with admin_session() as session:
        rows = (
            await session.execute(select(Tenant.id).where(Tenant.active.is_(True)))
        ).scalars().all()
        return list(rows)


async def _gmail_connected(tenant_id: UUID) -> bool:
    from sqlalchemy import select

    from disha.db import admin_session
    from disha.db.models import Credential
    from disha.db.models.enums import CredentialStatus

    async with admin_session() as session:
        cred = (
            await session.execute(
                select(Credential).where(
                    Credential.tenant_id == tenant_id,
                    Credential.provider == "gmail",
                    Credential.status == CredentialStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()
        return cred is not None


async def _is_paused(tenant_id: UUID) -> bool:
    """Global kill switch — when the tenant set paused=true, the worker does no
    outbound work (sending or sourcing). Replies still get polled."""
    from sqlalchemy import select

    from disha.db import admin_session
    from disha.db.models import TenantSettings

    async with admin_session() as session:
        row = (
            await session.execute(
                select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        return bool(row and (row.config or {}).get("paused"))


async def sequence_tick(tenant_id: UUID) -> None:
    from disha.db import admin_session
    from disha.llm import LLMRouter
    from disha.sequencer.engine import run_sequence

    if await _is_paused(tenant_id):
        logger.info("sequence tick skipped: paused")
        return
    if not await _gmail_connected(tenant_id):
        logger.info("sequence tick skipped: gmail not connected")
        return

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        run = await run_sequence(session, tenant_id, router)
    logger.info(
        "sequence tick complete",
        extra={
            "processed": run.processed,
            "sent": run.sent,
            "queued_linkedin": run.queued_linkedin,
            "deferred": run.deferred,
            "errors": run.errors,
        },
    )


async def poll_tick(tenant_id: UUID) -> None:
    from disha.adapters.gmail_poller import GmailPoller
    from disha.db import admin_session
    from disha.llm import LLMRouter
    from disha.reply_handler import handle_new_reply

    if not await _gmail_connected(tenant_id):
        logger.info("gmail poll skipped: gmail not connected")
        return

    async with admin_session() as session:
        result = await GmailPoller().poll(session, tenant_id, max_threads=50)

    # Classify each fresh (non-bounce) reply in its OWN short transaction so a
    # single failure can't roll back the recorded replies or stall the rest.
    router = LLMRouter(tenant_id)
    classified = 0
    for reply_id in result.reply_ids:
        try:
            async with admin_session() as s2:
                await handle_new_reply(s2, tenant_id, reply_id, router=router)
            classified += 1
        except Exception:  # noqa: BLE001
            logger.exception("reply classification failed", extra={"reply_id": str(reply_id)})
    logger.info(
        "gmail poll complete",
        extra={
            "threads_checked": getattr(result, "threads_checked", None),
            "new_replies": getattr(result, "new_replies", None),
            "bounces": getattr(result, "bounces", None),
            "classified": classified,
        },
    )


async def auto_source_tick(tenant_id: UUID) -> None:
    """Weekly auto-sourcing from the saved search in tenant_settings.

    Config lives at tenant_settings.config['auto_source'] as a JSON string:
      {"enabled": true, "provider": "web", "overrides": {...},
       "weekly_limit": 50, "last_run_at": "<iso>"}

    The tick runs hourly but only fires when 7 days have passed since the
    last run — restarts and overlapping deploys can't double-source.
    """
    import json as _json

    from sqlalchemy import select

    from disha.db import admin_session
    from disha.db.models import TenantSettings
    from disha.sourcing import source_and_score

    async with admin_session() as session:
        row = (
            await session.execute(
                select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        config = dict(row.config) if row else {}

    if config.get("paused"):
        return  # global kill switch — no sourcing while paused
    raw = config.get("auto_source")
    if not raw:
        return
    try:
        auto = _json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        logger.warning("auto_source config is not valid JSON — skipping")
        return
    if not auto.get("enabled"):
        return

    last_run = auto.get("last_run_at")
    if last_run:
        try:
            last_dt = datetime.fromisoformat(last_run)
            if (datetime.now(UTC) - last_dt).days < 7:
                return
        except ValueError:
            pass  # unparseable timestamp — treat as never-run

    logger.info("auto-source: weekly run starting")
    result = await source_and_score(
        tenant_id,
        provider=auto.get("provider", "web"),
        overrides=auto.get("overrides") or {},
        limit=int(auto.get("weekly_limit", 50)),
        auto_score=True,
    )
    logger.info(
        "auto-source: weekly run complete",
        extra={"found": result.found, "added": result.added, "scored": result.scored},
    )

    # Persist last_run_at (fresh session/transaction).
    auto["last_run_at"] = datetime.now(UTC).isoformat()
    async with admin_session() as session:
        row = (
            await session.execute(
                select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if row is not None:
            new_config = dict(row.config or {})
            new_config["auto_source"] = _json.dumps(auto)
            row.config = new_config


async def linkedin_tick(tenant_id: UUID) -> None:
    """Auto-send queued LinkedIn invites via Unipile, capped per day.
    No-op unless the tenant enabled linkedin_auto and connected an account."""
    from disha.adapters.unipile import unipile_configured
    from disha.db import admin_session
    from disha.linkedin_auto import auto_send_pending

    if not unipile_configured():
        return
    async with admin_session() as session:
        result = await auto_send_pending(session, tenant_id)
    if result.get("sent"):
        logger.info("linkedin auto-send", extra=result)


async def optimize_tick(tenant_id: UUID) -> None:
    """Weekly optimization + digest. Hourly check, fires once per 7 days."""
    import json as _json

    from sqlalchemy import select

    from disha.db import admin_session
    from disha.db.models import TenantSettings
    from disha.optimizer import run_weekly_optimization

    async with admin_session() as session:
        row = (
            await session.execute(
                select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        config = dict(row.config) if row else {}

    raw = config.get("optimizer")
    if raw:
        try:
            last = _json.loads(raw).get("last_run_at")
            if last and (datetime.now(UTC) - datetime.fromisoformat(last)).days < 7:
                return
        except (ValueError, TypeError):
            pass

    await run_weekly_optimization(tenant_id)


async def _loop(name: str, interval: int, tick) -> None:
    """Run `tick` for EVERY active tenant, every `interval` seconds.
    One tenant's failure never blocks the others or kills the loop."""
    while True:
        started = datetime.now(UTC)
        try:
            tenant_ids = await _active_tenant_ids()
        except Exception:  # noqa: BLE001
            logger.exception("%s: tenant listing failed", name)
            tenant_ids = []
        for tenant_id in tenant_ids:
            try:
                await tick(tenant_id)
            except Exception:  # noqa: BLE001 — worker must survive any failure
                logger.exception(
                    "%s tick failed", name, extra={"tenant_id": str(tenant_id)}
                )
        elapsed = (datetime.now(UTC) - started).total_seconds()
        await asyncio.sleep(max(5.0, interval - elapsed))


async def run_worker() -> None:
    settings = get_settings()
    tenant_ids = await _active_tenant_ids()
    logger.info(
        "worker starting (multi-tenant)",
        extra={
            "active_tenants": len(tenant_ids),
            "sequence_interval": settings.worker_sequence_interval,
            "poll_interval": settings.worker_poll_interval,
        },
    )
    await asyncio.gather(
        _loop("sequence", settings.worker_sequence_interval, sequence_tick),
        _loop("gmail_poll", settings.worker_poll_interval, poll_tick),
        _loop("auto_source", 3600, auto_source_tick),
        _loop("optimizer", 3600, optimize_tick),
        _loop("linkedin", 900, linkedin_tick),
    )


def main() -> None:
    from disha.logging_config import configure_logging

    settings = get_settings()
    configure_logging(settings.disha_log_level, settings.disha_log_fmt)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
