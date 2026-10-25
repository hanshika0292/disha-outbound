"""Sequencer engine: advances eligible leads through their cadence.

run_sequence is the entry point — meant to be invoked by `disha sequence run`
on a schedule. For each eligible lead it figures out the next due step,
checks the send window, and either executes (email send via Gmail / LinkedIn
manual-queue) or defers.

Idempotency: a lead at step N already in `sent` won't be re-sent — the engine
finds the next unfilled step. If all steps are done with no reply, the lead
moves to `stale`.

dry_run uses a SAVEPOINT (session.begin_nested) that always rolls back, so
nothing leaks regardless of where execution stops.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, computed_field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.db.models import (
    Account,
    Cadence,
    GeoProfile,
    Lead,
    LeadStateRow,
    ManualAction,
    TenantSettings,
    Thread,
    Touch,
)
from disha.db.models.enums import Channel, Direction
from disha.db.models.enums import LeadState as LeadStateEnum
from disha.intelligence import draft_touch
from disha.llm import LLMRouter
from disha.sequencer.timing import (
    SendWindowResult,
    check_send_window,
    send_window_from_cadence_config,
)

_LINKEDIN_NOTE_MAX_CHARS = 300


def humanize_reason(reason: str, defer_until: datetime | None = None) -> str:
    """Turn an internal sequencer reason code into a plain-English explanation."""
    r = reason or ""
    if r.startswith("not_due_until"):
        return "Waiting for this step's scheduled day"
    if r == "outside_hours":
        when = f" (resumes {defer_until.strftime('%a %-I%p')})" if defer_until else ""
        return f"Outside your send window{when}"
    if r == "wrong_day":
        return "Your send window doesn't include today"
    if r == "festival_block":
        return "Paused for a holiday block"
    if r == "daily_send_budget_reached":
        return "Daily send limit reached — resumes tomorrow"
    if r.startswith("email_unverified_blocked"):
        conf = r.split(":", 1)[1] if ":" in r else "unverified"
        return f"Email not verified ({conf}) — verify it to send"
    if r == "email_guessed_blocked":
        return "Email is a guess — verify it to send"
    if r.startswith("already_processed"):
        return "Already handled this step"
    if r.startswith("geo_out_of_scope"):
        return "Lead's country is outside your target regions"
    if r == "draft_approval_required":
        return "Draft created — waiting for your approval"
    if r == "awaiting_manual_send":
        return "LinkedIn note queued for you to send"
    if r.startswith("gmail:"):
        return "Gmail send error — check your connection"
    return r.replace("_", " ")


class StepResult(BaseModel):
    lead_id: UUID
    step_number: int
    action: str  # "sent" | "queued_linkedin" | "deferred" | "skipped" | "error" | "stale"
    reason: str
    touch_id: UUID | None = None
    defer_until: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reason_human(self) -> str:
        return humanize_reason(self.reason, self.defer_until)


class SequenceRun(BaseModel):
    tenant_id: UUID
    processed: int
    sent: int
    queued_linkedin: int
    deferred: int
    skipped: int
    errors: int
    stale: int
    pending_approval: int = 0
    results: list[StepResult]


async def _load_tenant_settings(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(select(TenantSettings).where(TenantSettings.tenant_id == tenant_id))
    ).scalar_one_or_none()
    return dict(row.config) if row else {}


async def _load_geo_profile(session: AsyncSession, tenant_id: UUID) -> GeoProfile | None:
    result = await session.execute(
        select(GeoProfile).where(GeoProfile.tenant_id == tenant_id, GeoProfile.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def _load_cadences(session: AsyncSession, tenant_id: UUID) -> dict[str, Cadence]:
    rows = (
        (
            await session.execute(
                select(Cadence).where(Cadence.tenant_id == tenant_id, Cadence.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    return {str(c.config.get("region") or c.config.get("name", c.id)): c for c in rows}


async def _eligible_leads(
    session: AsyncSession, tenant_id: UUID
) -> list[tuple[LeadStateRow, Lead, Account | None]]:
    rows = (
        await session.execute(
            select(LeadStateRow, Lead)
            .join(Lead, Lead.id == LeadStateRow.lead_id)
            .where(
                LeadStateRow.tenant_id == tenant_id,
                LeadStateRow.state.in_([LeadStateEnum.APPROVED, LeadStateEnum.IN_SEQUENCE]),
            )
            .order_by(LeadStateRow.sequence_started_at.asc().nulls_first())
        )
    ).all()
    result: list[tuple[LeadStateRow, Lead, Account | None]] = []
    for state_row, lead in rows:
        account = None
        if lead.account_id is not None:
            account = (
                await session.execute(select(Account).where(Account.id == lead.account_id))
            ).scalar_one_or_none()
        result.append((state_row, lead, account))
    return result


def _resolve_geo_for_lead(
    state: LeadStateRow,
    account: Account | None,
    geo_profile: GeoProfile | None,
) -> str | None:
    """Pick the region key used to look up the cadence."""
    if state.geo_assigned:
        return state.geo_assigned
    if account and account.hq_country:
        return account.hq_country
    if geo_profile is not None:
        primary = geo_profile.config.get("primary", [])
        return primary[0] if primary else None
    return None


async def _existing_touches_for_lead(
    session: AsyncSession, tenant_id: UUID, lead_id: UUID
) -> list[Touch]:
    rows = (
        (
            await session.execute(
                select(Touch)
                .join(Thread, Thread.id == Touch.thread_id)
                .where(Touch.tenant_id == tenant_id, Thread.lead_id == lead_id)
                .order_by(Touch.step_number.asc().nulls_last(), Touch.drafted_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def _next_step_index(touches: list[Touch], total_steps: int) -> int | None:
    """0-based index of the next step to execute, or None if all done."""
    completed = {t.step_number for t in touches if t.status == "sent" and t.step_number is not None}
    for i in range(total_steps):
        if i not in completed:
            return i
    return None


# Warm-up ramp parameters: start at 5 email sends/day, grow 1.5× per week
# since the very first send, cap at 30/day (~150/week).
_RAMP_START_PER_DAY = 5
_RAMP_WEEKLY_FACTOR = 1.5
_RAMP_MAX_PER_DAY = 30


async def _daily_send_budget_remaining(
    session: AsyncSession,
    tenant_id: UUID,
    now: datetime,
    *,
    daily_cap_override: int | None = None,
) -> int:
    """How many more emails we may send today.

    If the tenant set `daily_send_cap` (established accounts that don't need a
    warm-up), that flat cap is used. Otherwise the warm-up ramp applies:
    5/day growing 1.5×/week, capped at 30.
    """
    from sqlalchemy import func as sa_func

    if daily_cap_override and daily_cap_override > 0:
        cap = daily_cap_override
    else:
        first_sent = (
            await session.execute(
                select(sa_func.min(Touch.sent_at)).where(
                    Touch.tenant_id == tenant_id,
                    Touch.channel == Channel.EMAIL,
                    Touch.sent_at.is_not(None),
                )
            )
        ).scalar_one_or_none()

        if first_sent is None:
            cap = _RAMP_START_PER_DAY
        else:
            weeks = max(0, (now - first_sent).days // 7)
            cap = min(
                int(_RAMP_START_PER_DAY * (_RAMP_WEEKLY_FACTOR**weeks)),
                _RAMP_MAX_PER_DAY,
            )

    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = (
        await session.execute(
            select(sa_func.count()).where(
                Touch.tenant_id == tenant_id,
                Touch.channel == Channel.EMAIL,
                Touch.sent_at >= day_start,
            )
        )
    ).scalar_one()

    return max(0, cap - int(sent_today))


async def run_sequence(
    session: AsyncSession,
    tenant_id: UUID,
    router: LLMRouter,
    *,
    now_utc: datetime | None = None,
    dry_run: bool = False,
) -> SequenceRun:
    """Process every approved/in_sequence lead for this tenant.

    `now_utc` is injectable for tests (default: real now). `dry_run=True` wraps
    work in a SAVEPOINT that rolls back at the end — nothing persists.
    """
    now = now_utc or datetime.now(UTC)
    results: list[StepResult] = []

    geo_profile = await _load_geo_profile(session, tenant_id)
    cadences = await _load_cadences(session, tenant_id)
    tenant_settings = await _load_tenant_settings(session, tenant_id)

    # Daily send budget — flat cap if the tenant set one (established account),
    # otherwise the warm-up ramp. Protects sender reputation; computed per run.
    try:
        cap_override = int(tenant_settings.get("daily_send_cap") or 0) or None
    except (TypeError, ValueError):
        cap_override = None
    send_budget = await _daily_send_budget_remaining(
        session, tenant_id, now, daily_cap_override=cap_override
    )

    savepoint = await session.begin_nested() if dry_run else None
    try:
        eligibles = await _eligible_leads(session, tenant_id)
        for state, lead, account in eligibles:
            result = await _process_one_lead(
                session=session,
                tenant_id=tenant_id,
                state=state,
                lead=lead,
                account=account,
                geo_profile=geo_profile,
                cadences=cadences,
                tenant_settings=tenant_settings,
                now=now,
                router=router,
                dry_run=dry_run,
                send_budget=send_budget,
            )
            if result.action in ("sent", "pending_approval"):
                send_budget -= 1
            results.append(result)
    finally:
        if savepoint is not None:
            await savepoint.rollback()

    return SequenceRun(
        tenant_id=tenant_id,
        processed=len(results),
        sent=sum(1 for r in results if r.action == "sent"),
        queued_linkedin=sum(1 for r in results if r.action == "queued_linkedin"),
        deferred=sum(1 for r in results if r.action == "deferred"),
        skipped=sum(1 for r in results if r.action == "skipped"),
        errors=sum(1 for r in results if r.action == "error"),
        stale=sum(1 for r in results if r.action == "stale"),
        pending_approval=sum(1 for r in results if r.action == "pending_approval"),
        results=results,
    )


async def _process_one_lead(
    *,
    session: AsyncSession,
    tenant_id: UUID,
    state: LeadStateRow,
    lead: Lead,
    account: Account | None,
    geo_profile: GeoProfile | None,
    cadences: dict[str, Cadence],
    tenant_settings: dict[str, Any],
    now: datetime,
    router: LLMRouter,
    dry_run: bool,
    send_budget: int = 10_000,
) -> StepResult:
    region = _resolve_geo_for_lead(state, account, geo_profile)
    if region is None or region not in cadences:
        return StepResult(
            lead_id=lead.id,
            step_number=-1,
            action="skipped",
            reason=f"geo_out_of_scope:{region!r}",
        )

    cadence = cadences[region]
    steps: list[dict[str, Any]] = list(cadence.config.get("steps", []))
    if not steps:
        return StepResult(
            lead_id=lead.id, step_number=-1, action="skipped", reason="cadence_has_no_steps"
        )

    # Pause guard: a reply with intent='not_now'/'out_of_office' sets
    # sequence_paused_until in the future. Strictly-less so a pause expiring
    # exactly at `now` proceeds.
    if state.sequence_paused_until is not None and state.sequence_paused_until > now:
        return StepResult(
            lead_id=lead.id,
            step_number=-1,
            action="deferred",
            reason="sequence_paused",
            defer_until=state.sequence_paused_until,
        )

    if state.state == LeadStateEnum.APPROVED and state.sequence_started_at is None:
        if not dry_run:
            state.state = LeadStateEnum.IN_SEQUENCE
            state.sequence_started_at = now
            state.geo_assigned = region

    sequence_started_at = state.sequence_started_at or now

    touches = await _existing_touches_for_lead(session, tenant_id, lead.id)
    next_idx = _next_step_index(touches, len(steps))
    if next_idx is None:
        if not dry_run:
            state.state = LeadStateEnum.STALE
        return StepResult(
            lead_id=lead.id,
            step_number=len(steps),
            action="stale",
            reason="all_steps_sent_no_reply",
        )

    step = steps[next_idx]
    day_offset = int(step.get("day_offset", step.get("day", 0)))
    due_at = sequence_started_at + timedelta(days=day_offset)
    if now < due_at:
        return StepResult(
            lead_id=lead.id,
            step_number=next_idx,
            action="deferred",
            reason=f"not_due_until={due_at.isoformat()}",
            defer_until=due_at,
        )

    sw = send_window_from_cadence_config(cadence.config)
    block_names = list(cadence.config.get("festival_blocks", []))
    window: SendWindowResult = check_send_window(now, sw, block_names)
    if not window.allowed:
        return StepResult(
            lead_id=lead.id,
            step_number=next_idx,
            action="deferred",
            reason=window.reason,
            defer_until=window.defer_until,
        )

    existing_for_step = next((t for t in touches if t.step_number == next_idx), None)
    # Skip rules by status:
    #   sent / pending_approval        → nothing to do this tick.
    #   queued EMAIL                   → an APPROVED draft; falls through to the
    #     email step (still behind window + ramp gates) and gets sent. Skipping
    #     these was the bug that stranded every approved draft forever.
    #   queued LINKEDIN                → already in the manual queue; falling
    #     through would duplicate its ManualAction every tick, so skip.
    if existing_for_step is not None and (
        existing_for_step.status in ("sent", "pending_approval")
        or (
            existing_for_step.status == "queued"
            and existing_for_step.channel == Channel.LINKEDIN
        )
    ):
        return StepResult(
            lead_id=lead.id,
            step_number=next_idx,
            action="deferred",
            reason=f"already_processed:{existing_for_step.status}",
            touch_id=existing_for_step.id,
        )

    # Step is due, window is open, no completed touch yet — execute.
    channel = step.get("channel", "email")
    # Auto-approve outreach overrides the manual-approval requirement: drafts
    # send directly instead of parking in pending_approval. (Send guardrails —
    # verified email, daily ramp, send window — still apply.)
    require_approval = bool(
        tenant_settings.get("require_draft_approval", False)
    ) and not bool(tenant_settings.get("auto_approve_outreach", False))
    if channel == "email":
        # Guardrail 1 — warm-up ramp: defer when today's send budget is spent.
        if send_budget <= 0:
            return StepResult(
                lead_id=lead.id,
                step_number=next_idx,
                action="deferred",
                reason="daily_send_budget_reached",
                defer_until=now.replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1),
            )
        # Guardrail 2 — only send to VERIFIED emails. Anything else ("guessed"
        # pattern, "risky" unconfirmed mailbox, or a BLANK/unknown confidence)
        # bounces too often; bounces poison Gmail sender reputation for ALL
        # future sends. We default-deny: a lead must be explicitly "verified"
        # (Hunter-valid or a manually confirmed address) to send. A blank
        # confidence used to be treated as trusted — that let unverified
        # sourced leads slip through and bounce, so it is now blocked too.
        # The tenant can opt into looser sending with allow_unverified_send=true
        # if they accept the bounce risk (e.g. for a hand-checked CSV import).
        confidence = (lead.raw or {}).get("email_confidence")
        allow_unverified = bool(tenant_settings.get("allow_unverified_send", False))
        if confidence != "verified" and not allow_unverified:
            return StepResult(
                lead_id=lead.id,
                step_number=next_idx,
                action="skipped",
                reason=f"email_unverified_blocked:{confidence or 'unknown'}",
            )
        return await _execute_email_step(
            session=session,
            tenant_id=tenant_id,
            lead=lead,
            step_idx=next_idx,
            existing_touch=existing_for_step,
            router=router,
            now=now,
            dry_run=dry_run,
            require_approval=require_approval,
        )
    elif channel == "linkedin":
        return await _execute_linkedin_step(
            session=session,
            tenant_id=tenant_id,
            lead=lead,
            step_idx=next_idx,
            existing_touch=existing_for_step,
            router=router,
            now=now,
            dry_run=dry_run,
        )
    else:
        return StepResult(
            lead_id=lead.id,
            step_number=next_idx,
            action="skipped",
            reason=f"unsupported_channel:{channel!r}",
        )


async def _execute_email_step(
    *,
    session: AsyncSession,
    tenant_id: UUID,
    lead: Lead,
    step_idx: int,
    existing_touch: Touch | None,
    router: LLMRouter,
    now: datetime,
    dry_run: bool,
    require_approval: bool = False,
) -> StepResult:
    from disha.adapters.gmail import GmailAdapter, GmailError

    cadence_step = step_idx + 1  # cadence step numbers are 1-indexed in the JSON
    if existing_touch is None:
        draft = await draft_touch(session, tenant_id, lead.id, cadence_step, router)
        if draft is None:
            # Drafter emitted touch.skipped event already. Persist a skipped touch row
            # so the sequencer doesn't re-attempt this step indefinitely.
            thread = await _get_or_create_thread(session, tenant_id, lead.id, Channel.EMAIL)
            touch = Touch(
                tenant_id=tenant_id,
                thread_id=thread.id,
                step_number=step_idx,
                direction=Direction.OUTBOUND,
                channel=Channel.EMAIL,
                status="skipped",
                body="(skipped: prompt_not_found)",
                error_message="prompt_not_found",
            )
            session.add(touch)
            await session.flush()
            return StepResult(
                lead_id=lead.id,
                step_number=step_idx,
                action="skipped",
                reason="prompt_not_found",
                touch_id=touch.id,
            )
        touch_id = draft.touch_id
    else:
        touch_id = existing_touch.id

    if require_approval and (existing_touch is None or existing_touch.status == "draft"):
        # Park the touch awaiting human review. Sequencer will skip it on the next
        # run (the "already_processed" guard) until disha draft approve runs.
        if not dry_run:
            touch_row = (
                await session.execute(select(Touch).where(Touch.id == touch_id))
            ).scalar_one()
            touch_row.status = "pending_approval"
            if touch_row.drafted_at is None:
                touch_row.drafted_at = now
        return StepResult(
            lead_id=lead.id,
            step_number=step_idx,
            action="pending_approval",
            reason="draft_approval_required",
            touch_id=touch_id,
        )

    if dry_run:
        return StepResult(
            lead_id=lead.id,
            step_number=step_idx,
            action="sent",
            reason="dry_run",
            touch_id=touch_id,
        )

    try:
        adapter = GmailAdapter()
        await adapter.send_touch(session, touch_id)
    except GmailError as exc:
        touch_row = (await session.execute(select(Touch).where(Touch.id == touch_id))).scalar_one()
        touch_row.status = "failed"
        touch_row.error_message = str(exc)[:1000]
        return StepResult(
            lead_id=lead.id,
            step_number=step_idx,
            action="error",
            reason=f"gmail:{exc}",
            touch_id=touch_id,
        )

    return StepResult(
        lead_id=lead.id,
        step_number=step_idx,
        action="sent",
        reason="gmail_send",
        touch_id=touch_id,
    )


async def _execute_linkedin_step(
    *,
    session: AsyncSession,
    tenant_id: UUID,
    lead: Lead,
    step_idx: int,
    existing_touch: Touch | None,
    router: LLMRouter,
    now: datetime,
    dry_run: bool,
) -> StepResult:
    cadence_step = step_idx + 1
    if existing_touch is None:
        draft = await draft_touch(session, tenant_id, lead.id, cadence_step, router)
        if draft is None:
            thread = await _get_or_create_thread(session, tenant_id, lead.id, Channel.LINKEDIN)
            touch = Touch(
                tenant_id=tenant_id,
                thread_id=thread.id,
                step_number=step_idx,
                direction=Direction.OUTBOUND,
                channel=Channel.LINKEDIN,
                status="skipped",
                body="(skipped: prompt_not_found)",
                error_message="prompt_not_found",
            )
            session.add(touch)
            await session.flush()
            return StepResult(
                lead_id=lead.id,
                step_number=step_idx,
                action="skipped",
                reason="prompt_not_found",
                touch_id=touch.id,
            )
        touch_id = draft.touch_id
        body = draft.body
    else:
        touch_id = existing_touch.id
        body = existing_touch.body

    # Hard-truncate at 297 + "..." if LLM exceeded the 300-char LinkedIn limit.
    truncated_body = body
    if len(truncated_body) > _LINKEDIN_NOTE_MAX_CHARS:
        truncated_body = truncated_body[: _LINKEDIN_NOTE_MAX_CHARS - 3] + "..."

    if dry_run:
        return StepResult(
            lead_id=lead.id,
            step_number=step_idx,
            action="queued_linkedin",
            reason="dry_run",
            touch_id=touch_id,
        )

    touch_row = (await session.execute(select(Touch).where(Touch.id == touch_id))).scalar_one()
    touch_row.status = "queued"
    if truncated_body != body:
        touch_row.body = truncated_body

    session.add(
        ManualAction(
            tenant_id=tenant_id,
            touch_id=touch_id,
            lead_id=lead.id,
            action_type="linkedin_connect",
            content=truncated_body,
            status="pending",
        )
    )
    return StepResult(
        lead_id=lead.id,
        step_number=step_idx,
        action="queued_linkedin",
        reason="awaiting_manual_send",
        touch_id=touch_id,
    )


async def _get_or_create_thread(
    session: AsyncSession, tenant_id: UUID, lead_id: UUID, channel: Channel
) -> Thread:
    existing = (
        await session.execute(
            select(Thread).where(
                Thread.tenant_id == tenant_id,
                Thread.lead_id == lead_id,
                Thread.channel == channel,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    thread = Thread(tenant_id=tenant_id, lead_id=lead_id, channel=channel, status="open")
    session.add(thread)
    await session.flush()
    return thread


async def mark_manual_action_done(
    session: AsyncSession,
    tenant_id: UUID,
    action_id: UUID,
    *,
    notes: str | None = None,
) -> ManualAction:
    """Mark a LinkedIn manual action as completed and flip its touch to sent."""
    action = (
        await session.execute(
            select(ManualAction).where(
                ManualAction.id == action_id, ManualAction.tenant_id == tenant_id
            )
        )
    ).scalar_one()
    action.status = "done"
    action.completed_at = datetime.now(UTC)
    if notes:
        action.notes = notes
    touch = (await session.execute(select(Touch).where(Touch.id == action.touch_id))).scalar_one()
    touch.status = "sent"
    touch.sent_at = datetime.now(UTC)
    return action


async def mark_manual_action_skipped(
    session: AsyncSession,
    tenant_id: UUID,
    action_id: UUID,
    *,
    reason: str = "not_relevant",
) -> ManualAction:
    action = (
        await session.execute(
            select(ManualAction).where(
                ManualAction.id == action_id, ManualAction.tenant_id == tenant_id
            )
        )
    ).scalar_one()
    action.status = "skipped"
    action.completed_at = datetime.now(UTC)
    action.notes = reason
    touch = (await session.execute(select(Touch).where(Touch.id == action.touch_id))).scalar_one()
    touch.status = "skipped"
    return action
