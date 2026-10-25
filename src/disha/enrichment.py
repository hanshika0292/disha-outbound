"""Per-lead deep research: find the company website, then explain the fit.

enrich_lead() does three things:
  1. Resolves the company's real website (Serper search, junk domains filtered)
     and stores it on the account.
  2. Gathers research snippets: what the company does, recent news/funding,
     anything public about the person.
  3. Asks the LLM — primed with YOUR profile (sender title/expertise + active
     ICP) — to produce a fit brief: company summary, why they're a fit for
     you specifically, why reach out now, and a personalization hook.

Results are persisted where they compound:
  • lead.raw["enrichment"]      — full brief, shown in the lead drawer
  • lead.raw["pain_signal"]     — the why-now line → flows into email drafts
  • account.raw["summary"]      — company summary → flows into email drafts
  • account.domain / enriched_at
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.config import get_settings
from disha.db.models import Account, IcpRubric, Lead, TenantSettings
from disha.llm import LLMRequest, LLMRouter

logger = logging.getLogger(__name__)

_SERPER_URL = "https://google.serper.dev/search"

# Domains that are never a company's OWN website — directories, data
# providers, registries, aggregators, press. Sending mail to these is wrong:
# they showed up in search results ABOUT the company, not the company itself.
_JUNK_DOMAINS = (
    "linkedin.com", "crunchbase.com", "wikipedia.org", "facebook.com",
    "twitter.com", "x.com", "instagram.com", "youtube.com", "glassdoor",
    "indeed.com", "wellfound.com", "angel.co", "ycombinator.com",
    "techcrunch.com", "medium.com", "github.com", "pitchbook.com",
    "zoominfo.com", "apollo.io", "owler.com", "tracxn.com", "g2.com",
    # Registries / data providers / aggregators (caused real mis-sends)
    "zaubacorp.com", "preqin.com", "rocketreach.co", "signalhire.com",
    "lusha.com", "leadiq.com", "thecompanycheck.com", "instafinancials.com",
    "tofler.in", "yourstory.com", "inc42.com", "entrackr.com", "vccircle.com",
    "bloomberg.com", "reuters.com", "forbes.com", "businesswire.com",
    "prnewswire.com", "f6s.com", "startupranking.com", "cbinsights.com",
    "dnb.com", "zoomgov.com", "clutch.co", "goodfirms.co", "ambitionbox.com",
)

_BRIEF_SYSTEM = """\
You prepare a consultant for outreach. Given THEIR profile and research about
a prospect's company, produce a JSON brief:

{
  "company_summary": "<2 sentences: what the company actually does>",
  "why_fit": ["<2-3 bullets connecting THIS consultant's expertise to THIS company's likely needs — specific, no generic flattery>"],
  "why_now": "<1-2 sentences: a timing trigger (funding, hiring, launch, growth stage) that makes outreach timely — or honest 'no strong trigger found'>",
  "hook": "<one specific, natural opening line referencing something real about them — no 'I hope this finds you well'>",
  "funding_stage": "<one of: bootstrapped/pre-seed/seed/series_a/series_b/series_c — ONLY if the research clearly indicates it, else null>",
  "employee_count": <integer estimate ONLY if research indicates team size, else null>
}

Ground every claim in the research provided. If research is thin, say less —
never invent facts. Return ONLY JSON.
"""


async def _serper(client: httpx.AsyncClient, q: str, key: str, n: int = 6) -> list[dict[str, Any]]:
    try:
        r = await client.post(
            _SERPER_URL,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": q, "num": n},
            timeout=12,
        )
        r.raise_for_status()
        return r.json().get("organic", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrichment serper failed %r: %s", q[:50], exc)
        return []


def _is_junk(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(j in host for j in _JUNK_DOMAINS)


async def _find_website(
    client: httpx.AsyncClient, company: str, serper_key: str
) -> str | None:
    results = await _serper(client, f'"{company}" official website', serper_key)
    for r in results:
        url = r.get("link", "")
        if url and not _is_junk(url):
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}"
    return None


async def _fetch_homepage_text(client: httpx.AsyncClient, url: str) -> str:
    """Best-effort homepage text (crude tag strip, capped)."""
    try:
        r = await client.get(url, timeout=10, follow_redirects=True)
        html = r.text[:200_000]
        text = re.sub(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>", " ", html, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:2500]
    except Exception:  # noqa: BLE001
        return ""


def _parse_brief(content: str) -> dict[str, Any]:
    """Parse the LLM brief robustly: strict JSON first, then balanced-brace
    extraction of the first complete object (handles fences, trailing prose,
    and truncated output), then a plain-text fallback."""
    raw = re.sub(r"^```[a-z]*\n?", "", content.strip()).rstrip("` \n")
    try:
        out = json.loads(raw)
        if isinstance(out, dict):
            return out
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(raw)):
            ch = raw[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        out = json.loads(raw[start : i + 1])
                        if isinstance(out, dict):
                            return out
                    except json.JSONDecodeError:
                        break
    # Truncated mid-JSON: salvage whatever complete string fields exist.
    def field(name: str) -> str:
        m = re.search(rf'"{name}"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        return m.group(1).replace('\\"', '"').replace("\\n", "\n") if m else ""

    why_fit: list[str] = []
    if '"why_fit"' in raw:
        seg = raw[raw.find('"why_fit"') :]
        bracket = seg.find("[")
        if bracket != -1:
            end = seg.find("]", bracket)
            arr = seg[bracket : end if end != -1 else len(seg)]
            why_fit = re.findall(r'"((?:[^"\\]|\\.)*)"', arr)
    return {
        "company_summary": field("company_summary") or raw[:400],
        "why_fit": [w for w in why_fit if w and w != "why_fit"][:3],
        "why_now": field("why_now"),
        "hook": field("hook"),
    }


async def enrich_lead(
    session: AsyncSession,
    tenant_id: UUID,
    lead_id: UUID,
    router: LLMRouter | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    serper_key = settings.serper_api_key.get_secret_value()
    if not serper_key:
        raise ValueError("SERPER_API_KEY not configured")

    lead = (
        await session.execute(
            select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
        )
    ).scalar_one()
    account = (
        await session.execute(select(Account).where(Account.id == lead.account_id))
    ).scalar_one_or_none()
    company = (account.name if account else None) or "their company"
    person = f"{lead.first_name} {lead.last_name or ''}".strip()

    ts_row = (
        await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    config = dict(ts_row.config) if ts_row else {}

    icp = (
        await session.execute(
            select(IcpRubric).where(
                IcpRubric.tenant_id == tenant_id, IcpRubric.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    icp_name = (icp.config or {}).get("name", "") if icp else ""

    # ── Research ────────────────────────────────────────────────────────────
    async with httpx.AsyncClient() as client:
        website = await _find_website(client, company, serper_key)
        homepage_text = await _fetch_homepage_text(client, website) if website else ""

        news = await _serper(
            client, f'"{company}" funding OR launch OR hiring OR growth news', serper_key
        )
        person_results = await _serper(client, f'"{person}" "{company}"', serper_key, n=4)

    def fmt(results: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"- {r.get('title','')}: {r.get('snippet','')}" for r in results[:6]
        )

    research = (
        f"Website: {website or 'not found'}\n"
        f"Homepage text: {homepage_text[:1500] or '(unavailable)'}\n\n"
        f"News/search results:\n{fmt(news) or '(none)'}\n\n"
        f"About the person:\n{fmt(person_results) or '(none)'}"
    )

    # ── LLM brief ───────────────────────────────────────────────────────────
    used_router = router or LLMRouter(tenant_id)
    user_prompt = (
        f"CONSULTANT PROFILE:\n"
        f"  Name: {config.get('sender_name', '')} — {config.get('sender_title', '')}\n"
        f"  ICP: {icp_name}\n\n"
        f"PROSPECT:\n"
        f"  {person} — {lead.role_title or ''} at {company}"
        f" ({account.hq_country if account else ''},"
        f" {account.funding_stage if account else 'stage unknown'},"
        f" {account.employee_count if account else '?'} employees)\n\n"
        f"RESEARCH:\n{research}"
    )
    resp = await used_router.call(
        LLMRequest(
            purpose="lead_enrichment",
            prompt_version=1,
            system=_BRIEF_SYSTEM,
            user=user_prompt,
            # Generous: Gemini 2.5 spends output budget on internal reasoning
            # before the JSON, so visible output truncates far below the cap.
            max_tokens=8192,
            temperature=0.3,
        )
    )
    brief = _parse_brief(resp.content)

    result = {
        "website": website,
        "company_summary": brief.get("company_summary", ""),
        "why_fit": brief.get("why_fit", []),
        "why_now": brief.get("why_now", ""),
        "hook": brief.get("hook", ""),
        "enriched_at": datetime.now(UTC).isoformat(),
    }

    # ── Persist where it compounds ──────────────────────────────────────────
    lead_raw = dict(lead.raw or {})
    lead_raw["enrichment"] = result
    # These two keys feed the email drafter's context directly:
    if result["why_now"]:
        lead_raw["pain_signal"] = result["why_now"]
    if result["hook"]:
        lead_raw["public_activity"] = result["hook"]
    lead.raw = lead_raw

    if account is not None:
        acc_raw = dict(account.raw or {})
        if result["company_summary"]:
            acc_raw["summary"] = result["company_summary"]
        if website:
            acc_raw["website"] = website
            try:
                account.domain = urlparse(website).netloc.removeprefix("www.")
            except Exception:  # noqa: BLE001
                pass
        # Backfill scoring-critical fields the research uncovered. fit_funding
        # (25%) and fit_size (15%) read these — without them web-sourced leads
        # can never tier above C no matter how good the fit is.
        stage = brief.get("funding_stage")
        if stage and not account.funding_stage:
            stage_norm = str(stage).strip().lower().replace("-", "_").replace(" ", "_")
            if stage_norm in {"bootstrapped", "pre_seed", "seed", "series_a", "series_b", "series_c"}:
                account.funding_stage = stage_norm
        emp = brief.get("employee_count")
        if emp and not account.employee_count:
            try:
                account.employee_count = int(emp)
            except (TypeError, ValueError):
                pass
        account.raw = acc_raw
        account.enriched_at = datetime.now(UTC)

    return result
