"""Free email plausibility verification (no external API).

Hunter's free tier exhausts fast; this module rescues pattern-guessed emails
with two free signals:

  1. Domain correction — sourcing guesses `first.last@<companyslug>.com`, but
     enrichment resolves the company's REAL website (often .in/.co/.ai or a
     different name). If the account has a real domain, rebuild the address
     on it.
  2. MX check — a domain with MX (or A) records can receive mail. Guessed
     addresses on MX-valid domains get upgraded to "risky" (sendable under
     the ramp); domains with no mail setup stay "guessed" (blocked).

"risky" is honest: the mailbox itself is unverified, but the domain is real
and the pattern is the most common corporate format. With the 5/day ramp the
bounce exposure is small and visible.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.crypto import decrypt, encrypt, hash_email
from disha.db.models import Account, Lead, Tenant

logger = logging.getLogger(__name__)


def _mx_exists_sync(domain: str) -> bool:
    import dns.resolver

    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = 4.0
        resolver.resolve(domain, "MX")
        return True
    except Exception:  # noqa: BLE001 — NoAnswer/NXDOMAIN/timeout all mean "no MX"
        try:
            resolver.resolve(domain, "A")
            return True  # no MX but the domain resolves — many small cos do this
        except Exception:  # noqa: BLE001
            return False


async def mx_exists(domain: str) -> bool:
    if not domain or "." not in domain:
        return False
    return await asyncio.to_thread(_mx_exists_sync, domain)


_HUNTER_VERIFY_URL = "https://api.hunter.io/v2/email-verifier"


async def hunter_verify(email: str) -> str | None:
    """Verify one address via Hunter. Returns 'verified' | 'risky' | None.
    None means we couldn't verify (no key, quota exhausted, error) — caller
    should leave the existing confidence untouched."""
    import httpx

    from disha.config import get_settings

    key = get_settings().hunter_api_key.get_secret_value()
    if not key:
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                _HUNTER_VERIFY_URL,
                params={"email": email, "api_key": key},
                timeout=12,
            )
        if resp.status_code != 200:
            return None
        status_ = resp.json().get("data", {}).get("status", "")
        # valid → verified; accept_all/webmail/unknown/invalid → risky (don't
        # promote, but don't claim verified). Caller decides.
        return "verified" if status_ == "valid" else "risky"
    except Exception as exc:  # noqa: BLE001
        logger.debug("hunter verify error for %s: %s", email, exc)
        return None


async def verify_one_lead(
    session: AsyncSession, tenant_id: UUID, lead_id: UUID
) -> dict[str, Any]:
    """Verify a single lead's current email via Hunter. Updates confidence."""
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
        )
    ).scalar_one()
    try:
        email = decrypt(lead.email_encrypted).decode("utf-8")
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": "no_email"}

    result = await hunter_verify(email)
    raw = dict(lead.raw or {})
    if result == "verified":
        raw["email_confidence"] = "verified"
        raw["email_verify"] = "hunter_valid"
        lead.raw = raw
        return {"ok": True, "confidence": "verified", "email": email}
    if result == "risky":
        raw["email_confidence"] = "risky"
        raw["email_verify"] = "hunter_risky"
        lead.raw = raw
        return {"ok": True, "confidence": "risky", "email": email}
    # None — couldn't verify (no key / quota). Fall back to MX as a weak signal.
    domain = email.rsplit("@", 1)[-1]
    if await mx_exists(domain):
        return {"ok": False, "confidence": raw.get("email_confidence"), "reason": "unverifiable_no_quota", "email": email}
    return {"ok": False, "confidence": raw.get("email_confidence"), "reason": "no_mx", "email": email}


async def set_lead_email(
    session: AsyncSession, tenant_id: UUID, lead_id: UUID, new_email: str
) -> dict[str, Any]:
    """Manually set a confirmed email — marks it 'verified' (the user vouches
    for it). Re-hashes for dedup; rejects if it collides with another lead."""
    from disha.db.models import Tenant

    new_email = new_email.strip().lower()
    if "@" not in new_email or "." not in new_email.split("@")[-1]:
        return {"ok": False, "reason": "invalid_email"}

    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one()
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
        )
    ).scalar_one()

    new_hash = hash_email(new_email, tenant.email_hash_salt)
    clash = (
        await session.execute(
            select(Lead.id).where(
                Lead.tenant_id == tenant_id,
                Lead.email_hash == new_hash,
                Lead.id != lead_id,
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        return {"ok": False, "reason": "duplicate_email"}

    lead.email_encrypted = encrypt(new_email)
    lead.email_hash = new_hash
    raw = dict(lead.raw or {})
    raw["email_confidence"] = "verified"
    raw["email_verify"] = "manual"
    lead.raw = raw
    return {"ok": True, "confidence": "verified", "email": new_email}


def _local_part(first: str | None, last: str | None) -> str | None:
    fc = re.sub(r"[^a-z]", "", (first or "").lower())
    lc = re.sub(r"[^a-z]", "", (last or "").lower())
    if fc and lc:
        return f"{fc}.{lc}"
    return fc or None


async def reverify_guessed_leads(
    session: AsyncSession, tenant_id: UUID
) -> dict[str, Any]:
    """Upgrade 'guessed' leads where possible. Returns a summary.

    For each guessed lead:
      • swap in the enrichment-resolved account domain if it differs
      • MX-check → upgrade to 'risky' or leave blocked
    """
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one()
    salt = tenant.email_hash_salt

    rows = (
        await session.execute(
            select(Lead, Account)
            .outerjoin(Account, Account.id == Lead.account_id)
            .where(
                Lead.tenant_id == tenant_id,
                Lead.raw["email_confidence"].astext == "guessed",
            )
        )
    ).all()

    summary = {"checked": 0, "upgraded": 0, "domain_corrected": 0, "still_blocked": 0}

    for lead, account in rows:
        summary["checked"] += 1
        try:
            current_email = decrypt(lead.email_encrypted).decode("utf-8")
        except Exception:  # noqa: BLE001
            summary["still_blocked"] += 1
            continue
        current_domain = current_email.rsplit("@", 1)[-1].lower()

        # Prefer the enrichment-resolved real domain.
        real_domain = (account.domain or "").lower().strip() if account else ""
        candidate_domain = real_domain or current_domain

        email = current_email
        if real_domain and real_domain != current_domain:
            local = _local_part(lead.first_name, lead.last_name)
            if local:
                email = f"{local}@{real_domain}"

        if not await mx_exists(candidate_domain):
            summary["still_blocked"] += 1
            continue

        # Domain receives mail → persist (possibly corrected) address + upgrade.
        if email != current_email:
            new_hash = hash_email(email, salt)
            clash = (
                await session.execute(
                    select(Lead.id).where(
                        Lead.tenant_id == tenant_id,
                        Lead.email_hash == new_hash,
                        Lead.id != lead.id,
                    )
                )
            ).scalar_one_or_none()
            if clash is not None:
                summary["still_blocked"] += 1  # corrected address already exists
                continue
            lead.email_encrypted = encrypt(email)
            lead.email_hash = new_hash
            summary["domain_corrected"] += 1

        raw = dict(lead.raw or {})
        raw["email_confidence"] = "risky"
        raw["email_verify"] = "mx_ok"
        lead.raw = raw
        summary["upgraded"] += 1

    logger.info("email reverify complete", extra=summary)
    return summary
