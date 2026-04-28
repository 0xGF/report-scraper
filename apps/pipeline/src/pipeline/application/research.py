"""Closed-loop gap-filling — measure coverage after the first parse pass,
then send `report-discover` back to find PDFs for years that came up empty.

The flow:

    initial discover  ──► scrape ──► classify ──► parse ──► extracted.json
                                                                │
                                                                ▼
                                                    coverage_gaps()
                                                                │
                                              ┌─────────────────┘
                                              │
                              if gaps  ──►  research_gaps()
                                              │  (per-year targeted search)
                                              ▼
                                  new direct_urls (a few PDFs)
                                              │
                                              ▼
                                  scrape ── classify ── parse  (only the new docs)
                                              │
                                              ▼
                                  merged extracted.json

The merge happens at `data/raw/<slug>.extracted.json` — `parse.run()`
overwrites with the union of all parsed statements, so the downstream
`confirm` / `normalize` stages see a single coherent dataset.

Idempotent: if there are no gaps, the loop is a single coverage call.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog
import yaml

from pipeline.config import get_settings
from pipeline.domain.companies import _cached_companies, get_company
from pipeline.domain.types import StatementKind
from report_discover import research_gaps as _research_gaps
from report_discover.adapters.openai import OpenAILlmClient
from report_discover.adapters.tavily import TavilyClient

log = structlog.get_logger()

_CONFIG_PATH = Path(__file__).resolve().parents[5] / "data" / "companies.yaml"
_TARGET_YEARS = 10
_ALL_KINDS = (StatementKind.INCOME, StatementKind.BALANCE, StatementKind.CASHFLOW)


def coverage_gaps(
    extracted_path: Path,
    *,
    target_years: int = _TARGET_YEARS,
) -> set[int]:
    """Return fiscal years that have no extracted statements at all.

    A year counts as "covered" when at least one statement was extracted
    for it. Years where parse succeeded but only one statement landed are
    *not* flagged here — we treat the year-level granularity because the
    targeted re-discovery operates on whole annual reports, not per-statement.
    """
    if not extracted_path.exists():
        return set()
    payload = json.loads(extracted_path.read_text())
    have_years = {int(s.get("source_fiscal_year", 0)) for s in payload}
    have_years.discard(0)

    import datetime as dt

    end_year = dt.date.today().year
    target = set(range(end_year - target_years, end_year))
    return target - have_years


def _build_llm() -> OpenAILlmClient:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY required for `research`.")
    return OpenAILlmClient(
        api_key=settings.openai_api_key,
        cache_dir=settings.report_scrape_cache_dir / "llm",
    )


def _build_search() -> TavilyClient | None:
    settings = get_settings()
    if not settings.tavily_api_key:
        return None
    return TavilyClient(api_key=settings.tavily_api_key)


def _append_direct_urls(slug: str, new_urls: list[str]) -> int:
    """Merge `new_urls` into `companies.yaml` — first source's `direct_urls`.

    Returns the number actually added (existing URLs are deduped).
    """
    raw = yaml.safe_load(_CONFIG_PATH.read_text())
    cfg = raw["companies"][slug]
    sources = cfg.setdefault("sources", [])
    if not sources:
        sources.append(
            {
                "name": f"{slug}:annual-reports",
                "direct_urls": [],
                "link_filter": r".*\.pdf(\?|$)",
            }
        )
    src = sources[0]
    existing = set(src.get("direct_urls") or [])
    additions = [u for u in new_urls if u not in existing]
    if not additions:
        return 0
    src["direct_urls"] = [*(src.get("direct_urls") or []), *additions]
    _CONFIG_PATH.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    _cached_companies.cache_clear()
    return len(additions)


def fill_gaps(slug: str) -> int:
    """One pass of gap-fill discovery for `slug`. Returns # URLs added.

    Reads extracted.json, computes missing years, runs `research_gaps`,
    and appends any verified URLs to companies.yaml. The caller re-runs
    scrape/classify/parse to pick the new docs up.
    """
    settings = get_settings()
    raw_dir = settings.report_scrape_raw_dir
    extracted_path = raw_dir / f"{slug}.extracted.json"

    gaps = coverage_gaps(extracted_path)
    if not gaps:
        log.info("research.no_gaps", slug=slug)
        return 0

    web_search = _build_search()
    if web_search is None:
        log.warning(
            "research.skipped",
            slug=slug,
            reason="TAVILY_API_KEY not set — gap-filling needs web search",
        )
        return 0

    company = get_company(slug)
    # Adapter shim — `report_discover.research_gaps` wants the library's
    # `DiscoveredCompany` shape; we have the app's `Company`. Build a
    # minimal-but-faithful shim so we don't have to round-trip through YAML.
    from report_discover import DiscoveredCompany as LibCompany
    from report_discover import DiscoveredSource as LibSource

    lib_company = LibCompany(
        found=True,
        slug=company.slug,
        name=company.name,
        ticker=company.ticker,
        exchange=company.exchange,
        reporting_currency=company.reporting_currency.value,
        ir_url=company.ir_url,
        website=company.website,
        logo_url=company.logo_url,
        description=company.description,
        sector=company.sector,
        headquarters=company.headquarters,
        sources=[LibSource(name=f"{slug}:annual-reports")],
    )

    new_urls = _research_gaps(
        lib_company,
        missing_years=sorted(gaps),
        llm=_build_llm(),
        web_search=web_search,
        model=settings.openai_discover_model,
    )
    if not new_urls:
        log.info("research.no_new_urls", slug=slug, gaps=sorted(gaps))
        return 0

    added = _append_direct_urls(slug, new_urls)
    log.info(
        "research.added_urls",
        slug=slug,
        gaps_filled_count=added,
        gap_years=sorted(gaps),
    )
    return added
