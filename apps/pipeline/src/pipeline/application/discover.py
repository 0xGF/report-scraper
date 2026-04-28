"""`report-scrape discover <company_name>` — research agent for new companies.

Thin app-side adapter over the reusable `report-discover` library:
  1. Call `report_discover.discover(name)` to look up IR config + metadata.
  2. Run `report_discover.verify(...)` as a second-pass AI sanity check.
  3. Persist confident results to `data/companies.yaml`.
  4. Bust the in-process company cache so the rest of the pipeline sees it.

Everything generic lives in the library; everything app-specific
(YAML schema, cache busting, LLM client wiring) stays here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import structlog
import yaml

from pipeline.config import get_settings
from report_discover import DiscoveredCompany, Verdict, WebSearchClient
from report_discover import discover as _lib_discover
from report_discover import verify as _lib_verify
from report_discover.adapters.openai import OpenAILlmClient
from report_discover.adapters.tavily import TavilyClient

log = structlog.get_logger()

_CONFIG_PATH = Path(__file__).resolve().parents[5] / "data" / "companies.yaml"


def _build_llm() -> OpenAILlmClient:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY required for `report-scrape discover`.")
    client = OpenAILlmClient(
        api_key=settings.openai_api_key,
        cache_dir=settings.report_scrape_cache_dir / "llm",
    )
    if not client.ping():
        raise RuntimeError("OPENAI_API_KEY failed /v1/models ping.")
    return client


def _build_search() -> WebSearchClient | None:
    """Return a Tavily client iff TAVILY_API_KEY is set, else None.

    When None, discovery falls back to plain LLM-only (which hallucinates
    URLs for sites the model doesn't know cold) and the user is warned.
    """
    settings = get_settings()
    if not settings.tavily_api_key:
        log.warning(
            "discover.no_web_search",
            reason="TAVILY_API_KEY not set — falling back to LLM-only discovery",
        )
        return None
    return TavilyClient(api_key=settings.tavily_api_key)


def discover(company_name: str) -> DiscoveredCompany:
    """Pure discovery — no verify, no persistence. For programmatic use."""
    settings = get_settings()
    return _lib_discover(
        company_name,
        llm=_build_llm(),
        model=settings.openai_discover_model,
        web_search=_build_search(),
    )


def _to_yaml_entry(d: DiscoveredCompany) -> dict[str, Any]:
    """Render a DiscoveredCompany as a YAML entry matching the existing schema."""
    sources_yaml: list[dict[str, Any]] = []
    for s in d.sources:
        entry: dict[str, Any] = {"name": s.name}
        if s.label:
            entry["label"] = s.label
        if s.entry_urls:
            entry["entry_urls"] = list(s.entry_urls)
        if s.direct_urls:
            entry["direct_urls"] = list(s.direct_urls)
        if s.link_filter:
            entry["link_filter"] = s.link_filter
        if s.follow_links:
            entry["follow_links"] = s.follow_links
        sources_yaml.append(entry)
    return {
        "name": d.name,
        "ticker": d.ticker,
        "exchange": d.exchange,
        "reporting_currency": d.reporting_currency,
        "ir_url": d.ir_url,
        "website": d.website,
        "logo_url": d.logo_url,
        "description": d.description,
        "sector": d.sector,
        "headquarters": d.headquarters,
        "founded": d.founded,
        # Drives `report-scrape market` — without these the company has no live
        # price / market cap / sparkline.
        "market_ticker": d.market_ticker,
        "shares_outstanding_m": d.shares_outstanding_m,
        "sources": sources_yaml,
    }


def _slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "company"


def append_to_yaml(d: DiscoveredCompany) -> Path:
    """Append a discovered company to `companies.yaml`. Returns the path."""
    raw = yaml.safe_load(_CONFIG_PATH.read_text())
    if not isinstance(raw, dict):
        raw = {"companies": {}}
    raw.setdefault("companies", {})
    slug = d.slug or _slugify(d.name)
    if slug in raw["companies"]:
        log.info("discover.already_present", slug=slug)
    raw["companies"][slug] = _to_yaml_entry(d)
    _CONFIG_PATH.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return _CONFIG_PATH


def run(
    company_name: str,
    *,
    write: bool = True,
    ai_verify: bool = True,
) -> DiscoveredCompany:
    """Discover, verify, and (optionally) persist a company.

    The `ai_verify` flag runs a second-pass LLM auditor over the result and
    folds any flagged issues into `notes`. It does NOT downgrade `found` —
    the auditor is advisory; URL validation is the hard gate.
    """
    settings = get_settings()
    llm = _build_llm()
    model = settings.openai_discover_model
    web_search = _build_search()

    result = _lib_discover(
        company_name,
        llm=llm,
        model=model,
        web_search=web_search,
    )
    if not result.found:
        return result

    if ai_verify:
        verdict: Verdict = _lib_verify(
            result,
            llm=llm,
            original_query=company_name,
            model=model,
        )
        if verdict.issues:
            issue_lines = "; ".join(f"{i.field}: {i.issue}" for i in verdict.issues)
            log.warning(
                "discover.verify_flagged",
                slug=result.slug,
                confidence=verdict.confidence,
                issues=issue_lines,
            )
            existing = result.notes.strip()
            note = f"AI-verify flagged: {issue_lines}"
            result = result.model_copy(
                update={"notes": f"{existing}\n{note}".strip() if existing else note}
            )

    if write:
        path = append_to_yaml(result)
        log.info("discover.written", path=str(path), slug=result.slug)
        from pipeline.domain.companies import _cached_companies

        _cached_companies.cache_clear()
    return result
