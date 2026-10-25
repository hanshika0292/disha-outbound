"""MockSource — deterministic, realistic candidates with no external calls.

Honors the query (titles / countries / funding_stages / employee range / limit)
so the preview looks and behaves like a real provider. Default everywhere and
in tests, so the entire sourcing → score → approve flow is demoable today.
"""

from __future__ import annotations

from disha.sourcing.base import LeadSource
from disha.sourcing.schemas import SourceCandidate, SourceQuery

# Plausible AI/SaaS company pool: (company, industry, funding_stage, employees, country)
_COMPANIES = [
    ("Nyx Labs", "Artificial Intelligence", "series_a", 38, "IN"),
    ("Loomwork", "SaaS", "seed", 14, "IN"),
    ("Brightpath AI", "Artificial Intelligence", "series_b", 120, "US"),
    ("Cadence Data", "Data Infrastructure", "series_a", 45, "US"),
    ("Tindr Analytics", "Analytics", "bootstrapped", 22, "IN"),
    ("Vega Robotics", "Robotics", "series_a", 60, "SG"),
    ("Halcyon Health", "HealthTech", "series_b", 150, "US"),
    ("Meridian Cloud", "DevTools", "seed", 18, "UK"),
    ("Sable Security", "Security", "series_a", 52, "US"),
    ("Orchard ML", "Artificial Intelligence", "seed", 11, "IN"),
    ("Quill Finance", "FinTech", "series_b", 180, "SG"),
    ("Driftwood Studios", "SaaS", "bootstrapped", 9, "IN"),
    ("Pulsar Systems", "Data Infrastructure", "series_a", 70, "US"),
    ("Lantern AI", "Artificial Intelligence", "series_a", 33, "UK"),
    ("Coral Commerce", "E-commerce", "series_b", 140, "IN"),
]

# Person name pool — paired deterministically with companies by index.
_FIRST = [
    "Aarav", "Priya", "Maya", "Rohan", "Sofia", "Kabir", "Elena", "Dev",
    "Hannah", "Ishaan", "Nadia", "Leo", "Anika", "Marcus", "Tara",
]
_LAST = [
    "Sharma", "Mehta", "Chen", "Patel", "Rossi", "Khan", "Park", "Nair",
    "Walsh", "Reddy", "Haq", "Müller", "Iyer", "Cole", "Bose",
]

_DEFAULT_TITLES = [
    "Founder & CEO",
    "Co-founder & CTO",
    "VP Engineering",
    "Head of Product",
    "Head of Data",
]


def _slug(company: str) -> str:
    return "".join(ch for ch in company.lower() if ch.isalnum())


class MockSource(LeadSource):
    name = "mock"

    async def search(self, query: SourceQuery) -> list[SourceCandidate]:
        titles = query.titles or _DEFAULT_TITLES
        out: list[SourceCandidate] = []

        for i, (company, industry, stage, employees, country) in enumerate(_COMPANIES):
            if len(out) >= query.limit:
                break

            # Honor filters when provided.
            if query.countries and country not in query.countries:
                continue
            if query.funding_stages and stage not in query.funding_stages:
                continue
            if query.employee_min is not None and employees < query.employee_min:
                continue
            if query.employee_max is not None and employees > query.employee_max:
                continue
            if query.industries and industry not in query.industries:
                continue

            first = _FIRST[i % len(_FIRST)]
            last = _LAST[i % len(_LAST)]
            title = titles[i % len(titles)]
            # Reserved .example TLD + "example-" LinkedIn slugs: demo data must
            # never point at a real inbox or profile.
            domain = f"{_slug(company)}.example"

            out.append(
                SourceCandidate(
                    first_name=first,
                    last_name=last,
                    email=f"{first.lower()}.{last.lower()}@{domain}",
                    title=title,
                    company=company,
                    country=country,
                    linkedin_url=f"https://linkedin.com/in/example-{first.lower()}-{last.lower()}",
                    industry=industry,
                    funding_stage=stage,
                    employee_count=employees,
                    source_url=f"https://{domain}/about",
                    raw={
                        "provider": "mock",
                        "matched_keywords": query.keywords,
                        "headline": f"{title} at {company}",
                    },
                )
            )

        return out
