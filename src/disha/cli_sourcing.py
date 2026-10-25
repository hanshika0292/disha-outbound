"""Sourcing CLI — preview / run lead discovery (cron-friendly).

  disha sourcing preview            # dry-run: ICP-derived query + candidates
  disha sourcing run                # add (+score) into awaiting-approval

The scheduled phase wires `disha sourcing run` into the prod compose cron.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import typer
from rich.console import Console

console = Console()
err_console = Console(stderr=True)

sourcing_app = typer.Typer(no_args_is_help=True, help="Lead sourcing / discovery.")


async def _do_preview(tenant_slug: str, provider: str, limit: int) -> dict[str, Any]:
    from disha.db import admin_session
    from disha.sourcing import preview_candidates
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        query, candidates = await preview_candidates(
            session, tenant.id, provider=provider, limit=limit
        )
        return {
            "provider": provider,
            "query": query.model_dump(mode="json"),
            "candidates": [c.model_dump(mode="json") for c in candidates],
        }


@sourcing_app.command("preview")
def sourcing_preview(
    tenant: str = typer.Option("demo"),
    provider: str = typer.Option("mock"),
    limit: int = typer.Option(25),
) -> None:
    """Dry-run discovery: show the ICP-derived query + candidates. No writes."""
    out = asyncio.run(_do_preview(tenant, provider, limit))
    console.print_json(json.dumps(out, default=str))


async def _do_run(
    tenant_slug: str, provider: str, limit: int, auto_score: bool, dry_run: bool
) -> dict[str, Any]:
    from disha.db import admin_session
    from disha.sourcing import source_and_score
    from disha.tenant.service import get_tenant_by_slug

    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, tenant_slug)
        if tenant is None:
            raise RuntimeError(f"No tenant with slug={tenant_slug!r}")
        tenant_id = tenant.id

    # source_and_score owns its own short transactions (add → commit → score
    # each lead separately), so a long batch never holds one connection open
    # across many LLM calls.
    result = await source_and_score(
        tenant_id,
        provider=provider,
        limit=limit,
        auto_score=auto_score,
        dry_run=dry_run,
    )
    return result.model_dump(mode="json")


@sourcing_app.command("run")
def sourcing_run(
    tenant: str = typer.Option("demo"),
    provider: str = typer.Option("mock"),
    limit: int = typer.Option(25),
    no_score: bool = typer.Option(False, "--no-score"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Source leads: add (+score) into awaiting-approval. Cron-friendly."""
    try:
        result = asyncio.run(
            _do_run(tenant, provider, limit, auto_score=not no_score, dry_run=dry_run)
        )
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print_json(json.dumps(result, default=str))
