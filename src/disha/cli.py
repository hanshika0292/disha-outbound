import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any
from uuid import UUID

import typer
from rich.console import Console

from disha.config import get_settings
from disha.logging_config import configure_logging

configure_logging(get_settings().disha_log_level, get_settings().disha_log_fmt)

app = typer.Typer(no_args_is_help=True, add_completion=False)
db_app = typer.Typer(no_args_is_help=True, help="Database lifecycle commands.")
tenant_app = typer.Typer(no_args_is_help=True, help="Tenant management commands.")
settings_app = typer.Typer(no_args_is_help=True, help="Per-tenant settings.")
byok_app = typer.Typer(no_args_is_help=True, help="Bring-your-own-key LLM creds.")
lead_app = typer.Typer(no_args_is_help=True, help="Lead scoring and drafting.")
reply_app = typer.Typer(no_args_is_help=True, help="Reply classification.")
cost_app = typer.Typer(no_args_is_help=True, help="LLM cost reporting.")
sequence_app = typer.Typer(no_args_is_help=True, help="Cadence sequencer.")
linkedin_app = typer.Typer(no_args_is_help=True, help="LinkedIn manual queue.")
gmail_app = typer.Typer(no_args_is_help=True, help="Gmail OAuth + send status.")
draft_app = typer.Typer(no_args_is_help=True, help="Draft approval queue.")
notion_app = typer.Typer(no_args_is_help=True, help="Notion lead sync.")
report_app = typer.Typer(no_args_is_help=True, help="Pipeline, activity, funnel reports.")
app.add_typer(db_app, name="db")
app.add_typer(tenant_app, name="tenant")
tenant_app.add_typer(settings_app, name="settings")
tenant_app.add_typer(byok_app, name="byok")
app.add_typer(lead_app, name="lead")
app.add_typer(reply_app, name="reply")
app.add_typer(cost_app, name="cost")
app.add_typer(sequence_app, name="sequence")
app.add_typer(linkedin_app, name="linkedin")
app.add_typer(gmail_app, name="gmail")
app.add_typer(draft_app, name="draft")
app.add_typer(notion_app, name="notion")
app.add_typer(report_app, name="report")

from disha.cli_sourcing import sourcing_app  # noqa: E402

app.add_typer(sourcing_app, name="sourcing")


@app.command("worker")
def worker_cmd() -> None:
    """Run the background worker (sequence ticks + Gmail reply polling)."""
    from disha.worker import run_worker

    console.print("[dim]worker starting — Ctrl-C to stop[/dim]")
    asyncio.run(run_worker())

console = Console()
err_console = Console(stderr=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _mask_password(url: str) -> str:
    return re.sub(r"(://[^:]+:)([^@]+)(@)", r"\1***\3", url)


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    subprocess.run(cmd, cwd=cwd or PROJECT_ROOT, check=True)  # noqa: S603  # trusted list-form invocation


@db_app.command("up")
def db_up() -> None:
    """Start Postgres via docker compose."""
    _run(["docker", "compose", "up", "-d", "postgres"])
    console.print("[green]Postgres is up.[/green]")


@db_app.command("down")
def db_down() -> None:
    """Stop Postgres."""
    _run(["docker", "compose", "down"])


@db_app.command("migrate")
def db_migrate() -> None:
    """Run Alembic migrations to head."""
    _run(["alembic", "upgrade", "head"])


@db_app.command("reset")
def db_reset(
    yes: bool = typer.Option(False, "--yes", help="Confirm destructive reset."),
) -> None:
    """Drop and recreate the database, then migrate and seed. Dev/test only."""
    settings = get_settings()
    if settings.disha_env == "production":
        err_console.print("[red]Refusing to reset in production.[/red]")
        raise typer.Exit(1)

    masked = _mask_password(settings.database_url_admin)
    console.print(f"About to drop and recreate: [yellow]{masked}[/yellow]")
    if not yes:
        err_console.print("Pass [bold]--yes[/bold] to proceed.")
        raise typer.Exit(1)

    db_name = settings.postgres_db
    pg_user = settings.postgres_user

    _run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            pg_user,
            "-d",
            "postgres",
            "-c",
            f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE);',
        ]
    )
    _run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            pg_user,
            "-d",
            "postgres",
            "-c",
            f'CREATE DATABASE "{db_name}" OWNER disha_admin;',
        ]
    )
    db_migrate()
    tenant_seed()


@tenant_app.command("seed")
def tenant_seed() -> None:
    """Seed the demo tenant (idempotent)."""
    from disha.tenant import seed_demo_tenant

    summary = asyncio.run(seed_demo_tenant())
    console.print(f"[green]Seeded tenant:[/green] {summary['slug']} ({summary['tenant_id']})")
    for config_name, action in summary.get("actions", {}).items():
        color = {"created": "green", "unchanged": "dim", "bumped": "yellow"}.get(
            action.split(":")[0], "white"
        )
        console.print(f"  [{color}]{action:>20}[/{color}]  {config_name}")


@tenant_app.command("show")
def tenant_show(slug: str = typer.Argument(..., help="Tenant slug, e.g. 'demo'")) -> None:
    """Print tenant + active configs as pretty JSON."""
    from disha.tenant import show_tenant

    result = asyncio.run(show_tenant(slug))
    if result is None:
        err_console.print(f"[red]No tenant with slug:[/red] {slug}")
        raise typer.Exit(1)
    console.print_json(json.dumps(result, default=str))


async def _run_score_lead(tenant_slug: str, lead_id: UUID) -> dict[str, object]:
    from disha.db import admin_session
    from disha.intelligence import score_lead
    from disha.llm import LLMRouter
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        router = LLMRouter(tenant.id)
        result = await score_lead(session, tenant.id, lead_id, router)
    return result.model_dump(mode="json")


async def _run_score_all_unscored(tenant_slug: str) -> list[dict[str, object]]:
    from sqlalchemy import or_, select

    from disha.db import admin_session
    from disha.db.models import Lead, LeadStateRow, PromptPack
    from disha.intelligence import score_lead
    from disha.llm import LLMRouter
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        prompts = (
            await session.execute(
                select(PromptPack).where(
                    PromptPack.tenant_id == tenant.id, PromptPack.is_active.is_(True)
                )
            )
        ).scalar_one()
        stmt = (
            select(Lead.id)
            .outerjoin(LeadStateRow, LeadStateRow.lead_id == Lead.id)
            .where(
                Lead.tenant_id == tenant.id,
                or_(
                    LeadStateRow.lead_id.is_(None),
                    LeadStateRow.scored_with_prompt_version.is_(None),
                    LeadStateRow.scored_with_prompt_version < prompts.version,
                ),
            )
        )
        lead_ids = list((await session.execute(stmt)).scalars().all())

        router = LLMRouter(tenant.id)
        results = []
        for lid in lead_ids:
            r = await score_lead(session, tenant.id, lid, router)
            results.append(r.model_dump(mode="json"))
    return results


@lead_app.command("score")
def lead_score(
    lead_id: UUID | None = typer.Argument(None, help="Lead UUID; omit if using --all-unscored."),
    all_unscored: bool = typer.Option(
        False, "--all-unscored", help="Score every lead missing a current-version score."
    ),
    tenant: str = typer.Option("demo", "--tenant"),
) -> None:
    """Score one lead, or all leads needing a (re)score at the current prompt version."""
    if all_unscored:
        results = asyncio.run(_run_score_all_unscored(tenant))
        if not results:
            console.print("[dim]0 unscored leads found.[/dim]")
            return
        for r in results:
            console.print(f"  {r['tier']:>4}  score={r['total_score']:.3f}  lead={r['lead_id']}")
        console.print(f"[green]Scored {len(results)} lead(s).[/green]")
        return
    if lead_id is None:
        err_console.print("Provide a lead_id or pass --all-unscored.")
        raise typer.Exit(1)
    result = asyncio.run(_run_score_lead(tenant, lead_id))
    console.print_json(json.dumps(result, default=str))


async def _run_draft(tenant_slug: str, lead_id: UUID, step: int) -> dict[str, Any] | None:
    from disha.db import admin_session
    from disha.intelligence import draft_touch
    from disha.llm import LLMRouter
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        router = LLMRouter(tenant.id)
        result = await draft_touch(session, tenant.id, lead_id, step, router)
    return result.model_dump(mode="json") if result is not None else None


@lead_app.command("draft")
def lead_draft(
    lead_id: UUID = typer.Argument(...),
    step: int = typer.Option(1, "--step"),
    tenant: str = typer.Option("demo", "--tenant"),
) -> None:
    """Draft a touch for a lead at the given cadence step (skips if prompt missing)."""
    result = asyncio.run(_run_draft(tenant, lead_id, step))
    if result is None:
        console.print(
            "[yellow]Skipped: cadence step references a prompt_key "
            "not in the active prompt_pack.[/yellow]"
        )
        return
    console.print_json(json.dumps(result, default=str))


async def _run_classify(tenant_slug: str, reply_id: UUID) -> dict[str, object]:
    from disha.db import admin_session
    from disha.intelligence import classify_reply
    from disha.llm import LLMRouter
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        router = LLMRouter(tenant.id)
        result = await classify_reply(session, tenant.id, reply_id, router)
    return result.model_dump(mode="json")


@reply_app.command("classify")
def reply_classify(
    reply_id: UUID = typer.Argument(...),
    tenant: str = typer.Option("demo", "--tenant"),
) -> None:
    """Classify a reply, update its classification jsonb, suppress on unsubscribe intent."""
    result = asyncio.run(_run_classify(tenant, reply_id))
    console.print_json(json.dumps(result, default=str))


async def _run_cost_summary(tenant_slug: str, days: int, by: str) -> list[dict[str, Any]]:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from disha.db import admin_session
    from disha.db.models import LLMCall
    from disha.tenant.service import get_tenant_by_slug

    cutoff = datetime.now(UTC) - timedelta(days=days)
    group_col = {
        "model": LLMCall.model,
        "prompt": LLMCall.purpose,
        "day": func.date_trunc("day", LLMCall.created_at),
    }.get(by)
    if group_col is None:
        raise RuntimeError(f"--by must be one of: model, prompt, day (got {by!r})")

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        stmt: Any = (
            select(
                group_col.label("group"),
                func.count().label("calls"),
                func.sum(LLMCall.tokens_in).label("tokens_in"),
                func.sum(LLMCall.tokens_out).label("tokens_out"),
                func.sum(LLMCall.cost_usd).label("cost_usd"),
            )
            .where(
                LLMCall.tenant_id == tenant.id,
                LLMCall.created_at >= cutoff,
                LLMCall.success.is_(True),
            )
            .group_by("group")
            .order_by(func.sum(LLMCall.cost_usd).desc())
        )
        rows = (await session.execute(stmt)).all()
    return [
        {
            "group": str(row.group),
            "calls": int(row.calls),
            "tokens_in": int(row.tokens_in or 0),
            "tokens_out": int(row.tokens_out or 0),
            "cost_usd": float(row.cost_usd or 0),
        }
        for row in rows
    ]


@cost_app.command("summary")
def cost_summary(
    days: int = typer.Option(7, "--days"),
    by: str = typer.Option("model", "--by", help="model | prompt | day"),
    tenant: str = typer.Option("demo", "--tenant"),
) -> None:
    """Aggregate LLM spend over the last N days."""
    rows = asyncio.run(_run_cost_summary(tenant, days, by))
    if not rows:
        console.print(f"[dim]No LLM calls in the last {days} day(s).[/dim]")
        return
    total = sum(float(r["cost_usd"]) for r in rows)
    console.print(f"[bold]LLM cost (last {days}d, by {by})[/bold]")
    for r in rows:
        console.print(
            f"  ${r['cost_usd']:>10.6f}  calls={r['calls']:<4}  "
            f"in={r['tokens_in']:>6}  out={r['tokens_out']:>6}  {r['group']}"
        )
    console.print(f"  [bold]${total:>10.6f}  total[/bold]")


@settings_app.command("set")
def settings_set(
    key: str = typer.Argument(..., help="cal_link | sender_name | sender_title | sender_city"),
    value: str = typer.Argument(...),
    tenant: str = typer.Option("demo", "--tenant"),
) -> None:
    """Set one key in tenant_settings.config (validates cal_link format)."""
    from disha.tenant import set_tenant_setting

    try:
        result = asyncio.run(set_tenant_setting(tenant, key, value))
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    prev = result["previous"]
    prev_str = f" (was: {prev!r})" if prev is not None else ""
    console.print(
        f"[green]Set {result['key']} = {result['new']!r}[/green] for {result['tenant']}{prev_str}"
    )


@settings_app.command("show")
def settings_show(tenant: str = typer.Option("demo", "--tenant")) -> None:
    """Print tenant_settings.config as JSON."""
    from disha.tenant import get_tenant_settings

    try:
        result = asyncio.run(get_tenant_settings(tenant))
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print_json(json.dumps(result, default=str))


async def _run_sequence_cli(tenant_slug: str, dry_run: bool) -> dict[str, Any]:
    from disha.db import admin_session
    from disha.llm import LLMRouter
    from disha.sequencer.engine import run_sequence
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        router = LLMRouter(tenant.id)
        run = await run_sequence(session, tenant.id, router, dry_run=dry_run)
    return run.model_dump(mode="json")


@sequence_app.command("run")
def sequence_run(
    tenant: str = typer.Option("demo"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Process due cadence steps for all eligible leads."""
    result = asyncio.run(_run_sequence_cli(tenant, dry_run))
    rows = result.get("results", [])
    if not rows:
        console.print("[dim]No eligible leads.[/dim]")
    else:
        for r in rows:
            console.print(
                f"  step={r['step_number']:<2}  {r['action']:<16}  "
                f"{r['reason']:<40}  lead={str(r['lead_id'])[:8]}"
            )
    footer = (
        f"Processed {result['processed']} | Sent {result['sent']} | "
        f"LinkedIn {result['queued_linkedin']} | Deferred {result['deferred']} | "
        f"Skipped {result['skipped']} | Stale {result['stale']} | "
        f"Errors {result['errors']}"
    )
    style = "yellow" if dry_run else "bold"
    console.print(f"[{style}]{footer}[/{style}]" + (" (dry-run)" if dry_run else ""))


async def _run_sequence_status(tenant_slug: str, lead_id: UUID) -> dict[str, Any]:
    from sqlalchemy import select

    from disha.db import admin_session
    from disha.db.models import Lead, LeadStateRow, Thread, Touch
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        lead = (await session.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
        if lead is None:
            raise RuntimeError(f"No lead with id={lead_id}")
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one_or_none()
        touches = list(
            (
                await session.execute(
                    select(Touch)
                    .join(Thread, Thread.id == Touch.thread_id)
                    .where(Thread.lead_id == lead_id)
                    .order_by(Touch.step_number.asc().nulls_last(), Touch.drafted_at.asc())
                )
            ).scalars()
        )
        return {
            "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
            "state": state.state.value if state else None,
            "sequence_started_at": (
                state.sequence_started_at.isoformat()
                if state and state.sequence_started_at
                else None
            ),
            "geo_assigned": state.geo_assigned if state else None,
            "touches": [
                {
                    "step": t.step_number,
                    "channel": t.channel.value,
                    "status": t.status,
                    "drafted_at": t.drafted_at.isoformat() if t.drafted_at else None,
                    "sent_at": t.sent_at.isoformat() if t.sent_at else None,
                    "subject": (t.subject or "")[:40],
                }
                for t in touches
            ],
        }


@sequence_app.command("status")
def sequence_status(
    lead_id: UUID = typer.Argument(...),
    tenant: str = typer.Option("demo"),
) -> None:
    """Show sequence position and touch history for a lead."""
    result = asyncio.run(_run_sequence_status(tenant, lead_id))
    console.print_json(json.dumps(result, default=str))


async def _list_manual_actions(tenant_slug: str, status: str) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from disha.db import admin_session
    from disha.db.models import Lead, ManualAction
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        actions = list(
            (
                await session.execute(
                    select(ManualAction)
                    .where(
                        ManualAction.tenant_id == tenant.id,
                        ManualAction.status == status,
                    )
                    .order_by(ManualAction.created_at.desc())
                )
            ).scalars()
        )
        out: list[dict[str, Any]] = []
        for a in actions:
            lead = (
                await session.execute(select(Lead).where(Lead.id == a.lead_id))
            ).scalar_one_or_none()
            lead_name = ""
            if lead is not None:
                lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip()
            out.append(
                {
                    "id": str(a.id),
                    "lead_name": lead_name,
                    "action_type": a.action_type,
                    "content": (a.content or "")[:80],
                    "created_at": a.created_at.isoformat(),
                }
            )
        return out


@linkedin_app.command("queue")
def linkedin_queue(
    tenant: str = typer.Option("demo"),
    status: str = typer.Option("pending", "--status"),
) -> None:
    """List manual LinkedIn actions awaiting execution."""
    rows = asyncio.run(_list_manual_actions(tenant, status))
    if not rows:
        console.print(f"[dim]No manual actions in status={status!r}.[/dim]")
        return
    for r in rows:
        console.print(
            f"  {r['id'][:8]}  {r['lead_name']:<22}  {r['action_type']:<18}  {r['content']}"
        )


async def _mark_linkedin(
    tenant_slug: str, action_id: UUID, *, done: bool, notes_or_reason: str | None
) -> dict[str, str]:
    from disha.db import admin_session
    from disha.sequencer.engine import mark_manual_action_done, mark_manual_action_skipped
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        if done:
            action = await mark_manual_action_done(
                session, tenant.id, action_id, notes=notes_or_reason
            )
        else:
            action = await mark_manual_action_skipped(
                session,
                tenant.id,
                action_id,
                reason=notes_or_reason or "not_relevant",
            )
        return {"id": str(action.id), "status": action.status}


@linkedin_app.command("done")
def linkedin_done(
    action_id: UUID = typer.Argument(...),
    notes: str | None = typer.Option(None, "--notes"),
    tenant: str = typer.Option("demo"),
) -> None:
    """Mark a LinkedIn manual action as completed; flip its touch to sent."""
    result = asyncio.run(_mark_linkedin(tenant, action_id, done=True, notes_or_reason=notes))
    console.print(f"[green]Marked {result['id'][:8]} as {result['status']}.[/green]")


@linkedin_app.command("skip")
def linkedin_skip(
    action_id: UUID = typer.Argument(...),
    reason: str = typer.Option("not_relevant", "--reason"),
    tenant: str = typer.Option("demo"),
) -> None:
    """Skip a LinkedIn manual action; flip its touch to skipped."""
    result = asyncio.run(_mark_linkedin(tenant, action_id, done=False, notes_or_reason=reason))
    console.print(f"[yellow]Skipped {result['id'][:8]} (reason: {reason}).[/yellow]")


@gmail_app.command("auth")
def gmail_auth(
    tenant: str = typer.Option("demo"),
    client_id: str = typer.Option(..., envvar="GMAIL_CLIENT_ID"),
    client_secret: str = typer.Option(..., envvar="GMAIL_CLIENT_SECRET"),
) -> None:
    """Initiate Gmail OAuth flow (opens browser)."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        err_console.print(f"[red]google-auth-oauthlib not installed: {exc}[/red]")
        raise typer.Exit(1) from exc

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=["https://www.googleapis.com/auth/gmail.send", "openid", "email"],
    )
    creds = flow.run_local_server(port=0)

    # Pull sender email from id_token if present, otherwise fall back to userinfo endpoint.
    sender_email = (
        creds.id_token.get("email") if hasattr(creds, "id_token") and creds.id_token else ""
    )

    async def _persist() -> None:
        from disha.adapters.gmail import GmailAdapter
        from disha.db import admin_session
        from disha.tenant.service import get_tenant_by_slug

        async with admin_session() as session:
            t = await get_tenant_by_slug(session, tenant)
            if t is None:
                raise RuntimeError(f"No tenant with slug={tenant!r}")
            await GmailAdapter.from_oauth_callback(
                session,
                t.id,
                token=creds.token,
                refresh_token=creds.refresh_token,
                token_uri=creds.token_uri,
                client_id=client_id,
                client_secret=client_secret,
                scopes=list(creds.scopes),
                sender_email=sender_email,
            )

    asyncio.run(_persist())
    console.print(f"[green]Gmail connected.[/green] Sender: {sender_email or '(unknown)'}")


async def _gmail_status(tenant_slug: str) -> dict[str, Any] | None:
    import json as _json

    from sqlalchemy import select

    from disha.crypto import decrypt
    from disha.db import admin_session
    from disha.db.models import Credential
    from disha.db.models.enums import CredentialStatus
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        cred = (
            await session.execute(
                select(Credential).where(
                    Credential.tenant_id == tenant.id,
                    Credential.provider == "gmail",
                    Credential.status == CredentialStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()
        if cred is None:
            return None
        payload = _json.loads(decrypt(cred.encrypted_payload).decode("utf-8"))
        return {
            "sender_email": payload.get("sender_email", "(unknown)"),
            "scopes": payload.get("scopes", []),
            "has_refresh_token": bool(payload.get("refresh_token")),
            "updated_at": cred.updated_at.isoformat(),
        }


@gmail_app.command("status")
def gmail_status(tenant: str = typer.Option("demo")) -> None:
    """Show Gmail credential status for a tenant. Never prints secrets."""
    result = asyncio.run(_gmail_status(tenant))
    if result is None:
        console.print("[dim]No Gmail credential configured.[/dim]")
        return
    console.print_json(json.dumps(result, default=str))


# ---------- Week 4 commands: lead/draft/notion/report ----------


async def _run_lead_review(tenant_slug: str, min_tier: str | None) -> list[dict[str, Any]]:
    from disha.db import admin_session
    from disha.lead_approval import list_leads_awaiting_approval
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        items = await list_leads_awaiting_approval(session, tenant.id, min_tier=min_tier)
    return [it.model_dump(mode="json") for it in items]


@lead_app.command("review")
def lead_review(
    min_tier: str = typer.Option("B", help="Show leads at this tier and above (A|B|C|skip)"),
    tenant: str = typer.Option("demo"),
) -> None:
    """List scored leads awaiting approval."""
    rows = asyncio.run(_run_lead_review(tenant, min_tier))
    if not rows:
        console.print("[dim]No leads awaiting approval.[/dim]")
        return
    for r in rows:
        score = f"{float(r['fit_score']):.3f}" if r.get("fit_score") is not None else "—"
        console.print(
            f"  {str(r['lead_id'])[:8]}  {r['tier']:<5}  {score:<6}  "
            f"{r['full_name']:<22}  {r['title']:<28}  {r['account_name']}"
        )
    console.print(f"[bold]{len(rows)}[/bold] leads awaiting approval.")


async def _do_lead_approve(tenant_slug: str, lead_id: UUID) -> None:
    from disha.db import admin_session
    from disha.lead_approval import approve_lead
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        await approve_lead(session, tenant.id, lead_id)


@lead_app.command("approve")
def lead_approve(
    lead_id: UUID = typer.Argument(...),
    tenant: str = typer.Option("demo"),
) -> None:
    """Approve one scored lead → state='approved' (will enter sequence on next run)."""
    asyncio.run(_do_lead_approve(tenant, lead_id))
    console.print(f"[green]Approved {str(lead_id)[:8]}.[/green]")


async def _do_lead_reject(tenant_slug: str, lead_id: UUID, reason: str) -> None:
    from disha.db import admin_session
    from disha.lead_approval import reject_lead
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        await reject_lead(session, tenant.id, lead_id, reason=reason)


@lead_app.command("reject")
def lead_reject(
    lead_id: UUID = typer.Argument(...),
    reason: str = typer.Option("manual_reject", "--reason"),
    tenant: str = typer.Option("demo"),
) -> None:
    """Reject a scored lead; reason goes to events.lead.rejected."""
    asyncio.run(_do_lead_reject(tenant, lead_id, reason))
    console.print(f"[yellow]Rejected {str(lead_id)[:8]} (reason: {reason}).[/yellow]")


async def _do_bulk_approve(tenant_slug: str, min_tier: str) -> int:
    from disha.db import admin_session
    from disha.lead_approval import bulk_approve
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        return await bulk_approve(session, tenant.id, min_tier=min_tier)


@lead_app.command("approve-all")
def lead_approve_all(
    min_tier: str = typer.Option("B", "--min-tier"),
    yes: bool = typer.Option(False, "--yes"),
    tenant: str = typer.Option("demo"),
) -> None:
    """Bulk approve scored leads at ≥min_tier."""
    if not yes:
        rows = asyncio.run(_run_lead_review(tenant, min_tier))
        count = len(rows)
        console.print(f"Would approve {count} leads at tier ≥ {min_tier}.")
        console.print("[yellow]Pass --yes to confirm.[/yellow]")
        return
    count = asyncio.run(_do_bulk_approve(tenant, min_tier))
    console.print(f"[green]Approved {count} leads.[/green]")


# --- draft ---


async def _run_draft_review(tenant_slug: str) -> list[dict[str, Any]]:
    from disha.approvals import list_pending_drafts
    from disha.db import admin_session
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        items = await list_pending_drafts(session, tenant.id)
    return [it.model_dump(mode="json") for it in items]


@draft_app.command("review")
def draft_review(tenant: str = typer.Option("demo")) -> None:
    """List touches in pending_approval state."""
    rows = asyncio.run(_run_draft_review(tenant))
    if not rows:
        console.print("[dim]No drafts pending review.[/dim]")
        return
    for r in rows:
        console.print(
            f"  {str(r['touch_id'])[:8]}  step={r['step_number']:<2}  "
            f"{r['channel']:<9}  {r['lead_name']:<22}  {r['account_name']:<20}"
        )
        console.print(f"    [dim]subject:[/dim] {r.get('subject') or '(none)'}")
        console.print(f"    [dim]preview:[/dim] {r['body_preview']}")
    console.print(f"[bold]{len(rows)}[/bold] drafts pending.")


async def _do_draft_approve(tenant_slug: str, touch_id: UUID) -> None:
    from disha.approvals import approve_draft
    from disha.db import admin_session
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        await approve_draft(session, tenant.id, touch_id)


@draft_app.command("approve")
def draft_approve(
    touch_id: UUID = typer.Argument(...),
    tenant: str = typer.Option("demo"),
) -> None:
    """Approve a pending draft (status → queued; sequencer sends on next run)."""
    asyncio.run(_do_draft_approve(tenant, touch_id))
    console.print(f"[green]Approved draft {str(touch_id)[:8]}.[/green]")


async def _do_draft_reject(tenant_slug: str, touch_id: UUID, reason: str) -> None:
    from disha.approvals import reject_draft
    from disha.db import admin_session
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        await reject_draft(session, tenant.id, touch_id, reason=reason)


@draft_app.command("reject")
def draft_reject(
    touch_id: UUID = typer.Argument(...),
    reason: str = typer.Option("rejected", "--reason"),
    tenant: str = typer.Option("demo"),
) -> None:
    """Reject a pending draft (status → skipped)."""
    asyncio.run(_do_draft_reject(tenant, touch_id, reason))
    console.print(f"[yellow]Rejected draft {str(touch_id)[:8]}.[/yellow]")


async def _do_draft_regenerate(tenant_slug: str, touch_id: UUID) -> UUID | None:
    from disha.approvals import regenerate_draft
    from disha.db import admin_session
    from disha.llm import LLMRouter
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        router = LLMRouter(tenant.id)
        return await regenerate_draft(session, tenant.id, touch_id, router)


@draft_app.command("regenerate")
def draft_regenerate(
    touch_id: UUID = typer.Argument(...),
    tenant: str = typer.Option("demo"),
) -> None:
    """Re-draft a pending touch (old → skipped, new → pending_approval)."""
    new_id = asyncio.run(_do_draft_regenerate(tenant, touch_id))
    if new_id is None:
        console.print("[yellow]Regenerated, but cadence prompt not found — no new draft.[/yellow]")
        return
    console.print(f"[green]New draft {str(new_id)[:8]} created.[/green]")


# --- notion ---


async def _do_notion_auth(tenant_slug: str, token: str) -> None:
    from disha.adapters.notion import NotionAdapter
    from disha.db import admin_session
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        await NotionAdapter.store_token(session, tenant.id, token)


@notion_app.command("auth")
def notion_auth(
    token: str = typer.Option(..., envvar="NOTION_TOKEN"),
    tenant: str = typer.Option("demo"),
) -> None:
    """Store Notion integration token (encrypted in credentials table)."""
    asyncio.run(_do_notion_auth(tenant, token))
    console.print("[green]Notion token stored.[/green]")


@notion_app.command("config")
def notion_config(
    database_id: str = typer.Argument(...),
    tenant: str = typer.Option("demo"),
) -> None:
    """Set the Notion database ID for lead sync (writes tenant_settings.notion_database_id)."""
    from disha.tenant import set_tenant_setting

    asyncio.run(set_tenant_setting(tenant, "notion_database_id", database_id))
    console.print(f"[green]Set notion_database_id = {database_id}[/green]")


async def _do_notion_sync(tenant_slug: str, full: bool) -> dict[str, Any]:
    from datetime import datetime

    from disha.adapters.notion import NotionAdapter
    from disha.db import admin_session
    from disha.db.models import TenantSettings
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        since: datetime | None = None
        if not full:
            from sqlalchemy import select as _sel

            row = (
                await session.execute(
                    _sel(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
                )
            ).scalar_one_or_none()
            cfg = (row.config or {}) if row else {}
            iso = cfg.get("notion_last_synced")
            if iso:
                since = datetime.fromisoformat(iso)
        result = await NotionAdapter().sync_all(session, tenant.id, since=since)
    return result.model_dump(mode="json")


@notion_app.command("sync")
def notion_sync(
    full: bool = typer.Option(False, "--full"),
    tenant: str = typer.Option("demo"),
) -> None:
    """Sync leads to the Notion database (incremental by default)."""
    result = asyncio.run(_do_notion_sync(tenant, full))
    console.print_json(json.dumps(result, default=str))


# --- report ---


async def _do_report_pipeline(tenant_slug: str) -> Any:
    from disha.db import admin_session
    from disha.reports import get_pipeline_snapshot, render_pipeline
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        snapshot = await get_pipeline_snapshot(session, tenant.id)
    render_pipeline(snapshot, console)


@report_app.command("pipeline")
def report_pipeline(tenant: str = typer.Option("demo")) -> None:
    """Current pipeline snapshot by state, tier."""
    asyncio.run(_do_report_pipeline(tenant))


async def _do_report_activity(tenant_slug: str, days: int) -> None:
    from datetime import UTC, datetime, timedelta

    from disha.db import admin_session
    from disha.reports import get_activity, render_activity
    from disha.tenant.service import get_tenant_by_slug

    now = datetime.now(UTC)
    start = now - timedelta(days=days)
    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        activity = await get_activity(session, tenant.id, start, now)
    render_activity(activity, console)


@report_app.command("daily")
def report_daily(tenant: str = typer.Option("demo")) -> None:
    """Last 24 hours of activity."""
    asyncio.run(_do_report_activity(tenant, 1))


@report_app.command("weekly")
def report_weekly(
    tenant: str = typer.Option("demo"),
    email: bool = typer.Option(False, "--email", help="Also send digest via Gmail"),
) -> None:
    """Last 7 days of activity + funnel; optionally email the digest."""
    asyncio.run(_do_report_activity(tenant, 7))
    asyncio.run(_do_report_funnel(tenant, 7))
    if email:
        asyncio.run(_do_email_digest(tenant))


async def _do_report_funnel(tenant_slug: str, days: int) -> None:
    from disha.db import admin_session
    from disha.reports import get_funnel, render_funnel
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        funnel = await get_funnel(session, tenant.id, days=days)
    render_funnel(funnel, console)


@report_app.command("funnel")
def report_funnel(
    days: int = typer.Option(30, "--days"),
    tenant: str = typer.Option("demo"),
) -> None:
    """Cumulative funnel: Sourced → Scored → Approved → InSeq → Replied → Booked."""
    asyncio.run(_do_report_funnel(tenant, days))


async def _do_email_digest(tenant_slug: str) -> None:
    from disha.adapters.gmail import GmailAdapter
    from disha.db import admin_session
    from disha.reports import send_weekly_digest
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        adapter = GmailAdapter()
        message_id = await send_weekly_digest(
            session, tenant.id, gmail_adapter=adapter, sender_slug=tenant_slug
        )
    console.print(f"[green]Digest sent (gmail message_id={message_id}).[/green]")


# --- cost forecast ---


async def _do_cost_forecast(tenant_slug: str, lookback: int) -> None:
    from disha.cost import get_cost_forecast, render_cost_forecast
    from disha.db import admin_session
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        forecast = await get_cost_forecast(session, tenant.id, lookback_days=lookback)
    render_cost_forecast(forecast, console)


@cost_app.command("forecast")
def cost_forecast(
    lookback: int = typer.Option(7, "--days"),
    tenant: str = typer.Option("demo"),
) -> None:
    """Project weekly/monthly cost at 10/25/50 leads/week based on recent activity."""
    asyncio.run(_do_cost_forecast(tenant, lookback))


# ---------- Week 5 commands: lead import/add, gmail poll, health ----------


async def _do_lead_import(
    tenant_slug: str, file_path: Path, *, no_score: bool, dry_run: bool
) -> dict[str, Any]:
    from disha.db import admin_session
    from disha.ingestion import import_from_csv
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        result = await import_from_csv(
            session, tenant.id, file_path, auto_score=not no_score, dry_run=dry_run
        )
    return result.model_dump(mode="json")


@lead_app.command("import")
def lead_import(
    file: Path = typer.Argument(..., exists=True, help="Path to a CSV with lead rows"),
    no_score: bool = typer.Option(False, "--no-score"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    tenant: str = typer.Option("demo"),
) -> None:
    """Import leads from a CSV file (header row required)."""
    result = asyncio.run(_do_lead_import(tenant, file, no_score=no_score, dry_run=dry_run))
    suffix = " (dry-run — nothing written)" if dry_run else ""
    console.print(
        f"[bold]Total {result['total_rows']}[/bold]  |  "
        f"[green]Created {result['created']}[/green]  |  "
        f"[yellow]Duplicates {result['skipped_duplicate']}[/yellow]  |  "
        f"[red]Invalid {result['skipped_invalid']}[/red]{suffix}"
    )
    for err in result.get("errors", []):
        console.print(f"  [red]{err}[/red]")


async def _do_lead_add(tenant_slug: str, row_kwargs: dict[str, Any], no_score: bool) -> Any:
    from disha.db import admin_session
    from disha.ingestion import ImportRow, add_single_lead
    from disha.tenant.service import get_tenant_by_slug

    row = ImportRow(**row_kwargs)
    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        lead_id = await add_single_lead(session, tenant.id, row, auto_score=not no_score)
    return lead_id


@lead_app.command("add")
def lead_add(
    email: str = typer.Option(..., "--email"),
    first_name: str = typer.Option(..., "--first-name"),
    last_name: str = typer.Option(..., "--last-name"),
    title: str = typer.Option(..., "--title"),
    company: str = typer.Option(..., "--company"),
    country: str = typer.Option("IN", "--country"),
    notes: str | None = typer.Option(None, "--notes"),
    linkedin_url: str | None = typer.Option(None, "--linkedin-url"),
    funding_stage: str | None = typer.Option(None, "--funding-stage"),
    employee_count: int | None = typer.Option(None, "--employee-count"),
    no_score: bool = typer.Option(False, "--no-score"),
    tenant: str = typer.Option("demo"),
) -> None:
    """Add a single lead manually (validates email; dedups by email hash)."""
    lead_id = asyncio.run(
        _do_lead_add(
            tenant,
            {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "title": title,
                "company": company,
                "country": country,
                "notes": notes,
                "linkedin_url": linkedin_url,
                "funding_stage": funding_stage,
                "employee_count": employee_count,
            },
            no_score,
        )
    )
    if lead_id is None:
        console.print("[yellow]Duplicate — lead already exists.[/yellow]")
        return
    console.print(
        f"[green]Created lead {str(lead_id)[:8]}[/green] | {first_name} {last_name} @ {company}"
    )


async def _do_gmail_poll(tenant_slug: str, max_threads: int) -> dict[str, Any]:
    from disha.adapters.gmail_poller import GmailPoller
    from disha.db import admin_session
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        result = await GmailPoller().poll(session, tenant.id, max_threads=max_threads)
    return result.model_dump(mode="json")


@gmail_app.command("poll")
def gmail_poll(
    max_threads: int = typer.Option(50, "--max-threads"),
    tenant: str = typer.Option("demo"),
) -> None:
    """Check Gmail threads for replies to sent touches; classify + update state."""
    result = asyncio.run(_do_gmail_poll(tenant, max_threads))
    console.print(
        f"Checked {result['threads_checked']} threads  |  "
        f"[green]{result['new_replies']} new[/green]  |  "
        f"[dim]{result['already_seen']} already seen[/dim]  |  "
        f"[red]{result['errors']} errors[/red]"
    )
    if result["new_replies"]:
        console.print("[dim]Run `disha report pipeline` to see updated lead states.[/dim]")


async def _do_health() -> dict[str, Any]:
    from disha.db import admin_session
    from disha.health import run_health_checks

    async with admin_session() as session:
        report = await run_health_checks(session)
    return report.model_dump(mode="json")


@app.command("health")
def health_cmd(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Check database connectivity, migration currency, prompts, env vars."""
    report = asyncio.run(_do_health())
    if json_output:
        console.print_json(json.dumps(report, default=str))
    else:
        icon = {
            "ok": "[green]ok  [/green]",
            "warn": "[yellow]warn[/yellow]",
            "fail": "[red]fail[/red]",
        }
        for check in report["checks"]:
            console.print(f"  {icon[check['status']]} {check['name']:<14} {check['detail']}")
        overall_style = {
            "ok": "bold green",
            "warn": "bold yellow",
            "fail": "bold red",
        }[report["overall"]]
        console.print(f"[{overall_style}]overall: {report['overall']}[/{overall_style}]")
    if report["overall"] == "fail":
        raise typer.Exit(1)


# ---------- Week 6: tenant create/list/upgrade/suspend/apikey, byok, server ----------


async def _do_tenant_create(
    *,
    slug: str,
    name: str,
    email: str,
    plan: str,
    template: str | None,
    sender_name: str | None,
    sender_title: str | None,
    sender_city: str | None,
) -> dict[str, Any]:
    from disha.tenant.provisioner import provision_tenant_via_admin

    result = await provision_tenant_via_admin(
        slug=slug,
        name=name,
        email=email,
        plan=plan,
        template=template,
        sender_name=sender_name,
        sender_title=sender_title,
        sender_city=sender_city,
    )
    return result.model_dump(mode="json")


@tenant_app.command("create")
def tenant_create(
    slug: str = typer.Argument(...),
    name: str = typer.Option(..., "--name"),
    email: str = typer.Option(..., "--email"),
    plan: str = typer.Option("starter", "--plan"),
    template: str | None = typer.Option(None, "--template", help="ai_consulting | ux_design"),
    sender_name: str | None = typer.Option(None, "--sender-name"),
    sender_title: str | None = typer.Option(None, "--sender-title"),
    sender_city: str | None = typer.Option(None, "--sender-city"),
) -> None:
    """Provision a new tenant (slug, settings, optional template-driven config, API key)."""
    try:
        r = asyncio.run(
            _do_tenant_create(
                slug=slug,
                name=name,
                email=email,
                plan=plan,
                template=template,
                sender_name=sender_name,
                sender_title=sender_title,
                sender_city=sender_city,
            )
        )
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Tenant created:[/green] {r['slug']} (plan={r['plan']})")
    console.print()
    console.print("[bold]API Key (save this — shown once):[/bold]")
    console.print(f"  [yellow]{r['api_key']}[/yellow]")
    console.print()
    console.print(f"  Key prefix: {r['api_key_prefix']}...")
    console.print()
    console.print("[dim]Next steps:[/dim]")
    console.print(f"  [dim]disha tenant byok set gemini --api-key <key> --tenant {r['slug']}[/dim]")
    console.print(f"  [dim]disha tenant settings set cal_link <url> --tenant {r['slug']}[/dim]")


async def _do_tenant_list() -> list[dict[str, Any]]:
    from disha.db import admin_session
    from disha.tenant.provisioner import list_tenants

    async with admin_session() as session:
        rows = await list_tenants(session)
    return [r.model_dump(mode="json") for r in rows]


@tenant_app.command("list")
def tenant_list_cmd() -> None:
    """List all tenants (admin view)."""
    rows = asyncio.run(_do_tenant_list())
    if not rows:
        console.print("[dim]No tenants.[/dim]")
        return
    for r in rows:
        active = "[green]y[/green]" if r["active"] else "[red]n[/red]"
        prefix = r.get("api_key_prefix") or "(no key)"
        console.print(
            f"  {r['slug']:<16} plan={r['plan']:<10} active={active}  "
            f"leads={r['lead_count']:<4} in_seq={r['in_sequence_count']:<3}  "
            f"key={prefix:<10}  {r['created_at'][:10]}"
        )


async def _do_tenant_upgrade(slug: str, plan: str) -> None:
    from disha.db import admin_session
    from disha.tenant.provisioner import upgrade_plan

    async with admin_session() as session:
        await upgrade_plan(session, slug, plan)


@tenant_app.command("upgrade")
def tenant_upgrade(
    slug: str = typer.Argument(...),
    plan: str = typer.Argument(..., help="starter | growth | pro | scale"),
) -> None:
    """Change a tenant's plan."""
    try:
        asyncio.run(_do_tenant_upgrade(slug, plan))
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Upgraded {slug} to {plan}.[/green]")


async def _do_tenant_suspend(slug: str) -> None:
    from disha.db import admin_session
    from disha.tenant.provisioner import suspend_tenant

    async with admin_session() as session:
        await suspend_tenant(session, slug)


@tenant_app.command("suspend")
def tenant_suspend(
    slug: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Suspend a tenant (deactivates all API keys, blocks API access)."""
    if not yes:
        err_console.print(
            f"This will suspend tenant {slug!r} and deactivate all API keys. "
            "Pass [bold]--yes[/bold] to confirm."
        )
        raise typer.Exit(1)
    asyncio.run(_do_tenant_suspend(slug))
    console.print(f"[yellow]Suspended {slug}.[/yellow]")


async def _do_tenant_reactivate(slug: str) -> None:
    from disha.db import admin_session
    from disha.tenant.provisioner import reactivate_tenant

    async with admin_session() as session:
        await reactivate_tenant(session, slug)


@tenant_app.command("reactivate")
def tenant_reactivate(slug: str = typer.Argument(...)) -> None:
    """Reactivate a suspended tenant."""
    asyncio.run(_do_tenant_reactivate(slug))
    console.print(f"[green]Reactivated {slug}.[/green]")


async def _do_tenant_apikey(slug: str, *, rotate: bool) -> dict[str, Any]:
    from sqlalchemy import select as _sel

    from disha.db import admin_session
    from disha.db.models import ApiKey, Tenant
    from disha.tenant.provisioner import rotate_api_key

    async with admin_session() as session:
        tenant = (await session.execute(_sel(Tenant).where(Tenant.slug == slug))).scalar_one()
        if rotate:
            new_key = await rotate_api_key(session, tenant.id)
            return {"action": "rotated", "api_key": new_key}
        rows = list(
            (
                await session.execute(
                    _sel(ApiKey)
                    .where(ApiKey.tenant_id == tenant.id)
                    .order_by(ApiKey.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return {
            "action": "list",
            "keys": [
                {
                    "name": k.name,
                    "key_prefix": k.key_prefix,
                    "plan": k.plan,
                    "active": k.active,
                    "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                    "created_at": k.created_at.isoformat(),
                }
                for k in rows
            ],
        }


@tenant_app.command("apikey")
def tenant_apikey(
    slug: str = typer.Argument(...),
    rotate: bool = typer.Option(False, "--rotate", help="Generate new key (deactivates old)"),
) -> None:
    """Show API key info or rotate. Plaintext only shown on --rotate."""
    r = asyncio.run(_do_tenant_apikey(slug, rotate=rotate))
    if r["action"] == "rotated":
        console.print(f"[bold]New API key for {slug} (shown once):[/bold]")
        console.print(f"  [yellow]{r['api_key']}[/yellow]")
        return
    if not r["keys"]:
        console.print("[dim]No API keys.[/dim]")
        return
    for k in r["keys"]:
        active = "[green]active[/green]" if k["active"] else "[red]revoked[/red]"
        last = k["last_used_at"] or "(never)"
        console.print(
            f"  {k['name']:<12} {k['key_prefix']:<10} plan={k['plan']:<8} "
            f"{active}  last_used={last}"
        )


# --- byok ---


async def _do_byok_set(tenant_slug: str, provider: str, api_key: str, validate: bool) -> None:
    from disha.db import admin_session
    from disha.tenant.byok import set_byok_key
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        await set_byok_key(
            session, tenant.id, provider=provider, api_key=api_key, validate=validate
        )


@byok_app.command("set")
def byok_set(
    provider: str = typer.Argument(..., help="gemini | anthropic | openai"),
    api_key: str = typer.Option(..., "--api-key"),
    tenant: str = typer.Option("demo"),
    no_validate: bool = typer.Option(False, "--no-validate"),
) -> None:
    """Set a BYOK LLM API key for a tenant (validates by default)."""
    try:
        asyncio.run(_do_byok_set(tenant, provider, api_key, validate=not no_validate))
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    suffix = "validated" if not no_validate else "validation skipped"
    console.print(f"[green]{provider} API key configured ({suffix}).[/green]")


async def _do_byok_status(tenant_slug: str) -> dict[str, Any]:
    from disha.db import admin_session
    from disha.tenant.byok import get_byok_status
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        return await get_byok_status(session, tenant.id)


@byok_app.command("status")
def byok_status_cmd(tenant: str = typer.Option("demo")) -> None:
    """Show BYOK provider configuration. Never prints plaintext keys."""
    status = asyncio.run(_do_byok_status(tenant))
    for provider, info in status.items():
        if info.get("configured"):
            at = info.get("configured_at", "?")[:19]
            console.print(f"  [green]ok  [/green] {provider:<10} configured_at={at}")
        else:
            console.print(f"  [dim]--  [/dim] {provider:<10} not configured")


async def _do_byok_revoke(tenant_slug: str, provider: str) -> None:
    from disha.db import admin_session
    from disha.tenant.byok import revoke_byok_key
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        await revoke_byok_key(session, tenant.id, provider)


@byok_app.command("revoke")
def byok_revoke(
    provider: str = typer.Argument(...),
    tenant: str = typer.Option("demo"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Revoke a BYOK API key (soft delete)."""
    if not yes:
        err_console.print(
            f"This will revoke {provider} for {tenant}. Pass [bold]--yes[/bold] to confirm."
        )
        raise typer.Exit(1)
    asyncio.run(_do_byok_revoke(tenant, provider))
    console.print(f"[yellow]Revoked {provider} for {tenant}.[/yellow]")


# --- server ---


@app.command("server")
def server_start(
    host: str = typer.Option("127.0.0.1", envvar="DISHA_HOST"),
    port: int = typer.Option(8000, envvar="DISHA_PORT"),
    workers: int = typer.Option(1, envvar="DISHA_WORKERS"),
    reload: bool = typer.Option(False, "--reload"),
    log_level: str = typer.Option("info"),
) -> None:
    """Start the Disha REST API server (uvicorn)."""
    import uvicorn

    uvicorn.run(
        "disha.api.app:app",
        host=host,
        port=port,
        workers=workers if not reload else 1,
        reload=reload,
        log_level=log_level,
    )


if __name__ == "__main__":
    app()
