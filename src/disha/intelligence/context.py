"""Build the {placeholders} dict used in prompt user_templates.

Pulls from lead + account + lead.raw signals (Week 2 doesn't query the signals
table yet — that's Week 3's ingestion work). As of Week 3, also picks up
tenant_settings.config (cal_link, sender_name/title/city) and optional
prior_subjects (used by draft.followup_2).
"""

from __future__ import annotations

import json
from typing import Any

from disha.db.models import Account, Lead


def _emp_bucket(n: int | None) -> str:
    if n is None:
        return "unknown"
    if n <= 10:
        return "1-10"
    if n <= 50:
        return "11-50"
    if n <= 200:
        return "51-200"
    if n <= 500:
        return "201-500"
    return "500+"


def build_lead_context(
    lead: Lead,
    account: Account | None,
    *,
    tenant_settings: dict[str, Any] | None = None,
    prior_subjects: list[str] | None = None,
) -> dict[str, Any]:
    raw = lead.raw or {}
    account_raw = (account.raw or {}) if account else {}
    settings = tenant_settings or {}

    first = lead.first_name or ""
    last = lead.last_name or ""
    company_name = account.name if account else (raw.get("company_name", ""))
    pain = raw.get("pain_signal", "none")
    hq_country = account.hq_country if account else raw.get("hq_country", "unknown")
    role_title = lead.role_title or ""

    return {
        "first_name": first,
        "last_name": last,
        "lead_name": (first + (" " + last if last else "")).strip(),
        "lead_title": role_title,
        "role_title": role_title,
        "seniority": lead.seniority or "unknown",
        "company_name": company_name,
        "account_name": company_name,
        "company_summary": account_raw.get("summary", raw.get("company_summary", "")),
        "industry": account.industry if account else raw.get("industry", "unknown"),
        "employee_count": (account.employee_count if account else raw.get("employee_count")),
        "employee_count_bucket": _emp_bucket(
            account.employee_count if account else raw.get("employee_count")
        ),
        "funding_stage": (
            account.funding_stage if account else raw.get("funding_stage", "unknown")
        ),
        "hq_country": hq_country,
        "account_country": hq_country,
        "hq_city": account.hq_city if account else raw.get("hq_city", ""),
        "pain_signal": pain,
        "pain_signal_summary": pain,
        "public_activity": raw.get("public_activity", "none observed"),
        "signals_json": json.dumps(raw.get("signals", []), indent=2),
        "linkedin_url": lead.linkedin_url or "",
        "cal_link": settings.get("cal_link", ""),
        "sender_name": settings.get("sender_name", "Meera"),
        "sender_title": settings.get("sender_title", "AI strategy consultant"),
        "sender_city": settings.get("sender_city", "Bangalore"),
        "prior_subjects": ", ".join(prior_subjects) if prior_subjects else "(none)",
    }
