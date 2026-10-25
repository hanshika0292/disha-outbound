"""WebResearchSource — Apollo-style lead discovery with no paid lead database.

Pipeline
────────
1. Serper.dev  — 6-8 diverse Google searches across LinkedIn, Crunchbase,
                 AngelList/Wellfound, YC, funding news, company sites.
2. Batch LLM   — snippets split into groups of 5 and processed in parallel;
                 each batch call is small so Gemini doesn't truncate.
3. Hunter.io   — verified emails; falls back to firstname.lastname@domain.com.

Required env vars
─────────────────
  SERPER_API_KEY   — serper.dev (free: 2 500 queries/month)
Optional
  HUNTER_API_KEY   — hunter.io  (free: 25 lookups/month)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from uuid import UUID

import httpx

from disha.sourcing.base import LeadSource
from disha.sourcing.schemas import SourceCandidate, SourceQuery

logger = logging.getLogger(__name__)

_SERPER_URL = "https://google.serper.dev/search"
_HUNTER_FIND_URL = "https://api.hunter.io/v2/email-finder"
_HUNTER_VERIFY_URL = "https://api.hunter.io/v2/email-verifier"

_COUNTRY_NAMES: dict[str, str] = {
    "IN": "India", "US": "United States", "UK": "United Kingdom",
    "SG": "Singapore", "AU": "Australia", "DE": "Germany",
    "FR": "France", "CA": "Canada", "AE": "UAE", "NL": "Netherlands",
}

_STAGE_MAP: dict[str, str] = {
    "pre_seed": "pre-seed", "pre-seed": "pre-seed",
    "seed": "seed round", "series_a": "Series A",
    "series_b": "Series B", "series_c": "Series C",
    "bootstrapped": "bootstrapped",
}

# Concise prompt — fewer tokens → less chance of truncation
_EXTRACT_SYSTEM = """\
Extract B2B leads from Google search snippets. Be GENEROUS — extract every real person you can see.

Return a JSON array. Each item MUST have: first_name, last_name, title, company, country (ISO-2).
Also include if visible: linkedin_url, email, industry, source_url,
funding_stage (pre-seed/seed/series_a/series_b/bootstrapped), employee_count (int).

Rules:
- Extract ALL real named people with job titles you can find.
- Prefer founders, CEOs, CTOs, VPs, Heads of X.
- Use the target country if country is not in the snippet.
- Return [] only if truly no named person exists.
- Return ONLY a valid JSON array. No markdown, no prose.
"""


# ── Query builder ────────────────────────────────────────────────────────────

def _build_queries(query: SourceQuery) -> list[str]:
    """Build 6-8 diverse search queries spanning multiple sources."""
    titles = query.titles[:4] or ["Founder", "CEO", "CTO"]
    # Use short OR list to avoid overly-long queries
    title_str = " OR ".join(f'"{t}"' for t in titles[:3])
    industries = query.industries[:3] or ["SaaS", "AI"]
    industry_str = " OR ".join(f'"{i}"' for i in industries[:2])
    countries = [_COUNTRY_NAMES.get(c, c) for c in (query.countries or [])]
    geo = f'"{countries[0]}"' if countries else ""
    geo2 = f'"{countries[1]}"' if len(countries) > 1 else geo

    stages = [_STAGE_MAP.get(s, s) for s in (query.funding_stages or [])[:2]]
    stage_str = " OR ".join(f'"{s}"' for s in stages) if stages else ""

    queries: list[str] = []

    # 1 & 2 — LinkedIn profiles (best signal, one per top country)
    for g in ([geo, geo2] if geo2 and geo2 != geo else [geo]):
        if g:
            queries.append(
                f'site:linkedin.com/in ({title_str}) ({industry_str}) {g}'
            )
        else:
            queries.append(f'site:linkedin.com/in ({title_str}) ({industry_str})')

    # 3 — Crunchbase founder profiles
    queries.append(
        f'site:crunchbase.com/person ({industry_str}) {geo} ({title_str})'
    )

    # 4 — AngelList / Wellfound startup founders
    queries.append(
        f'(site:wellfound.com OR site:angel.co) founder ({industry_str}) {geo}'
    )

    # 5 — Funding announcement news (mentions founder name + company)
    if stage_str:
        queries.append(
            f'({industry_str}) startup {stage_str} founder {geo} '
            f'(site:techcrunch.com OR site:yourstory.com OR site:inc42.com OR site:venturebeat.com)'
        )
    else:
        queries.append(
            f'({industry_str}) startup founder "raised" {geo} '
            f'(site:techcrunch.com OR site:yourstory.com OR site:inc42.com)'
        )

    # 6 — YC alumni (strong signal for seed/series_a)
    if not stages or any(s in ("pre-seed", "seed round", "Series A") for s in stages):
        queries.append(
            f'site:ycombinator.com/companies ({industry_str}) {geo}'
        )

    # 7 — Keyword + LinkedIn (ICP niche)
    for kw in query.keywords[:1]:
        if kw and len(kw) < 40:
            queries.append(f'"{kw}" ({title_str}) site:linkedin.com/in')

    # Return at most limit//4 + 3 queries; always at least 4
    cap = max(4, query.limit // 4 + 3)
    return queries[:cap]


# ── Serper ───────────────────────────────────────────────────────────────────

async def _serper_search(
    client: httpx.AsyncClient, query: str, api_key: str, n: int = 10
) -> list[dict[str, Any]]:
    try:
        resp = await client.post(
            _SERPER_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": n},
            timeout=12,
        )
        resp.raise_for_status()
        return resp.json().get("organic", [])
    except Exception as exc:
        logger.warning("serper failed %r: %s", query[:50], exc)
        return []


# ── LLM extraction (batched) ─────────────────────────────────────────────────

def _parse_llm_json(raw: str) -> list[dict]:
    """Parse LLM JSON output; handle truncation by extracting complete objects."""
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip()).rstrip("` \n")
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        pass
    # Truncated: extract complete {...} objects character-by-character
    start = raw.find("[")
    if start == -1:
        start = 0
    partial = raw[start:]
    objects: list[dict] = []
    depth, obj_start = 0, None
    for i, ch in enumerate(partial):
        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    obj = json.loads(partial[obj_start : i + 1])
                    if isinstance(obj, dict):
                        objects.append(obj)
                except json.JSONDecodeError:
                    pass
                obj_start = None
    return objects


async def _llm_extract_batch(
    snippets: list[dict[str, Any]],
    query: SourceQuery,
    tenant_id: UUID,
) -> list[dict[str, Any]]:
    """Extract candidates from one batch of snippets (≤6 per call)."""
    from disha.llm import LLMRequest, LLMRouter

    if not snippets:
        return []

    snippet_text = "\n\n".join(
        f"[{i+1}] URL:{r.get('link','')}\n"
        f"Title:{r.get('title','')}\n"
        f"Snippet:{r.get('snippet','')}"
        for i, r in enumerate(snippets)
    )
    context = (
        f"Titles: {', '.join(query.titles[:4])}\n"
        f"Industries: {', '.join(query.industries[:3])}\n"
        f"Countries: {', '.join(query.countries or ['Any'])}\n"
        f"Stages: {', '.join(query.funding_stages or ['Any'])}"
    )

    router = LLMRouter(tenant_id)
    try:
        resp = await router.call(
            LLMRequest(
                purpose="sourcing_extraction",
                prompt_version=1,
                system=_EXTRACT_SYSTEM,
                user=f"Context:\n{context}\n\nSnippets:\n{snippet_text}",
                max_tokens=2048,  # small per batch → no truncation
                temperature=0.0,
            )
        )
        return _parse_llm_json(resp.content)
    except Exception as exc:
        logger.warning("llm batch extraction failed: %s", exc)
        return []


async def _llm_extract_all(
    all_snippets: list[dict[str, Any]],
    query: SourceQuery,
    tenant_id: UUID,
    batch_size: int = 6,
) -> list[dict[str, Any]]:
    """Split snippets into batches and run LLM extraction in parallel."""
    batches = [
        all_snippets[i : i + batch_size]
        for i in range(0, len(all_snippets), batch_size)
    ]
    results = await asyncio.gather(
        *[_llm_extract_batch(batch, query, tenant_id) for batch in batches],
        return_exceptions=True,
    )
    out: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, list):
            out.extend(r)
    return out


# ── Email helpers ─────────────────────────────────────────────────────────────

def _domain_from_company(company: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "", company.lower())
    return f"{slug}.com" if slug else ""


def _guess_email(first: str, last: str, domain: str) -> str | None:
    if not domain:
        return None
    fc = re.sub(r"[^a-z]", "", first.lower())
    lc = re.sub(r"[^a-z]", "", last.lower())
    return f"{fc}.{lc}@{domain}" if fc and lc else None


async def _hunter_find(
    client: httpx.AsyncClient, first: str, last: str, domain: str, api_key: str
) -> str | None:
    if not domain or not api_key:
        return None
    try:
        resp = await client.get(
            _HUNTER_FIND_URL,
            params={"first_name": first, "last_name": last,
                    "domain": domain, "api_key": api_key},
            timeout=8,
        )
        data = resp.json().get("data", {})
        email = data.get("email")
        score = data.get("score", 0)
        return email if email and score >= 50 else None
    except Exception as exc:
        logger.debug("hunter failed %s %s@%s: %s", first, last, domain, exc)
        return None


async def _hunter_verify(
    client: httpx.AsyncClient, email: str, api_key: str
) -> str:
    """Verify a known email address. Returns confidence: verified | risky.

    Hunter statuses: valid → verified; accept_all/webmail/unknown → risky
    (deliverable-maybe); invalid/disposable/blocked → risky. We never return
    'guessed' here — that's reserved for pattern-constructed addresses.
    """
    if not api_key:
        return "risky"
    try:
        resp = await client.get(
            _HUNTER_VERIFY_URL,
            params={"email": email, "api_key": api_key},
            timeout=10,
        )
        status_ = resp.json().get("data", {}).get("status", "unknown")
        return "verified" if status_ == "valid" else "risky"
    except Exception as exc:
        logger.debug("hunter verify failed %s: %s", email, exc)
        return "risky"


# ── Candidate parser ──────────────────────────────────────────────────────────

# LLMs emit these literal strings when a field is missing — treat as empty.
_NULLISH = {"none", "n/a", "na", "null", "unknown", "-", "—", ""}


def _clean(value: Any) -> str:
    """Strip a field and blank out LLM null-placeholders ('None', 'N/A', …)."""
    s = str(value or "").strip()
    return "" if s.lower() in _NULLISH else s


def _parse_candidate(raw: dict[str, Any]) -> SourceCandidate | None:
    try:
        first = _clean(raw.get("first_name"))
        last  = _clean(raw.get("last_name"))
        title   = _clean(raw.get("title"))
        company = _clean(raw.get("company"))
        country = (_clean(raw.get("country")) or "US").upper()[:2]
        # Require a real first name, title, and company. Last name is optional
        # (many founders go by one name) — but if present it must be real.
        if not (first and title and company):
            return None
        return SourceCandidate(
            first_name=first, last_name=last,
            email=raw.get("email") or None,
            title=title, company=company, country=country,
            linkedin_url=raw.get("linkedin_url") or None,
            industry=raw.get("industry") or None,
            funding_stage=raw.get("funding_stage") or None,
            employee_count=raw.get("employee_count") or None,
            source_url=raw.get("source_url") or None,
            raw={"provider": "web", "extracted": raw},
        )
    except Exception:
        return None


# ── Main source ───────────────────────────────────────────────────────────────

class WebResearchSource(LeadSource):
    """Apollo-style: Serper (multi-source) → batched LLM → Hunter email."""

    name = "web"

    def __init__(self, tenant_id: UUID | None = None) -> None:
        self._tenant_id = tenant_id

    async def search(self, query: SourceQuery) -> list[SourceCandidate]:
        from disha.config import get_settings

        settings = get_settings()
        serper_key = settings.serper_api_key.get_secret_value()
        hunter_key = settings.hunter_api_key.get_secret_value()

        if not serper_key:
            raise ValueError("SERPER_API_KEY not set — sign up at serper.dev")
        if self._tenant_id is None:
            raise ValueError("WebResearchSource requires tenant_id")

        search_queries = _build_queries(query)
        logger.info("web sourcing: %d queries, limit=%d", len(search_queries), query.limit)

        async with httpx.AsyncClient() as client:
            # ── 1. Fetch search results (all queries in parallel) ──────────
            fetch_tasks = [
                _serper_search(client, q, serper_key, n=10) for q in search_queries
            ]
            all_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

            all_snippets: list[dict[str, Any]] = []
            seen_urls: set[str] = set()
            for res in all_results:
                if isinstance(res, list):
                    for r in res:
                        url = r.get("link", "")
                        if url not in seen_urls:
                            seen_urls.add(url)
                            all_snippets.append(r)

            logger.info("web sourcing: %d unique snippets", len(all_snippets))

            # ── 2. Batched parallel LLM extraction ─────────────────────────
            raw_candidates = await _llm_extract_all(
                all_snippets, query, self._tenant_id, batch_size=6
            )
            logger.info("web sourcing: %d raw candidates from LLM", len(raw_candidates))

            # ── 3. Parse + dedupe ──────────────────────────────────────────
            seen: set[tuple[str, str]] = set()
            candidates: list[SourceCandidate] = []
            for raw in raw_candidates:
                c = _parse_candidate(raw)
                if c is None:
                    continue
                key = (c.first_name.lower(), c.company.lower())
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(c)
                if len(candidates) >= query.limit * 2:
                    break

            # ── 4. Email resolution + verification ─────────────────────────
            # Only the final slice gets Hunter calls — its free tier is small
            # (25-50/mo) so we don't burn quota on candidates we'll trim anyway.
            final = candidates[: query.limit]

            async def _resolve(c: SourceCandidate) -> None:
                if c.email:
                    # Email came from extraction (a page/snippet) — verify it.
                    c.raw["email_confidence"] = (
                        await _hunter_verify(client, c.email, hunter_key)
                        if hunter_key
                        else "risky"
                    )
                    return
                domain = _domain_from_company(c.company)
                if hunter_key:
                    found = await _hunter_find(
                        client, c.first_name, c.last_name, domain, hunter_key
                    )
                    if found:
                        # Hunter finder result with score ≥ 50 — sourced from
                        # real observations, treat as verified.
                        c.email = found
                        c.raw["email_confidence"] = "verified"
                        return
                c.email = _guess_email(c.first_name, c.last_name, domain)
                # A pattern-guessed address is ALWAYS "guessed" — an MX record
                # only proves the domain receives mail, not that this mailbox
                # exists. Calling MX-valid guesses "risky" (and letting them
                # send) caused real bounces. They stay blocked until a real
                # verification provider confirms the mailbox.
                c.raw["email_confidence"] = "guessed" if c.email else None

            await asyncio.gather(
                *(_resolve(c) for c in final), return_exceptions=True
            )

        logger.info(
            "web sourcing: %d candidates (%d verified, %d risky, %d guessed)",
            len(final),
            sum(1 for c in final if c.raw.get("email_confidence") == "verified"),
            sum(1 for c in final if c.raw.get("email_confidence") == "risky"),
            sum(1 for c in final if c.raw.get("email_confidence") == "guessed"),
        )
        return final
