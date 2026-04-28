"""Company loader — reads `data/companies.yaml`.

The pipeline is company-agnostic; all company-specific config lives in a
YAML file so adding a new company is a config change, not a code change.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from pipeline.domain.models import Company
from pipeline.domain.types import Currency
from report_scrape import IrSource

_CONFIG_PATH = Path(__file__).resolve().parents[5] / "data" / "companies.yaml"


def _load_companies() -> dict[str, Company]:
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Company config not found at {_CONFIG_PATH}. "
            "Create it with `companies: {slug: {name, ticker, exchange, "
            "reporting_currency, ir_url, sources}}`."
        )
    raw = yaml.safe_load(_CONFIG_PATH.read_text())
    if not isinstance(raw, dict) or "companies" not in raw:
        raise ValueError(f"{_CONFIG_PATH} must have a top-level `companies:` mapping")

    out: dict[str, Company] = {}
    for slug, cfg in raw["companies"].items():
        sources = [IrSource(**s) for s in cfg.get("sources", [])]
        out[slug] = Company(
            slug=slug,
            name=cfg["name"],
            ticker=cfg["ticker"],
            exchange=cfg["exchange"],
            reporting_currency=Currency(cfg["reporting_currency"]),
            ir_url=cfg["ir_url"],
            sources=sources,
            logo_url=cfg.get("logo_url"),
            description=cfg.get("description"),
            sector=cfg.get("sector"),
            headquarters=cfg.get("headquarters"),
            website=cfg.get("website"),
            founded=cfg.get("founded"),
            source_note=cfg.get("source_note"),
            market_ticker=cfg.get("market_ticker"),
            shares_outstanding_m=cfg.get("shares_outstanding_m"),
        )
    return out


@lru_cache
def _cached_companies() -> dict[str, Company]:
    return _load_companies()


def get_all_companies() -> dict[str, Company]:
    """Always go through the cache so post-`report-scrape discover` runs see the
    new YAML row after `_cached_companies.cache_clear()`. Prefer this over
    importing `COMPANIES` directly — that's a snapshot at import time and
    won't reflect mid-run YAML edits.
    """
    return _cached_companies()


# Snapshot at import time. Stable for read-only / static contexts (CLI help
# strings, etc.). Runtime iterations should call `get_all_companies()`.
COMPANIES: dict[str, Company] = _cached_companies()


def get_company(slug: str) -> Company:
    companies = get_all_companies()
    try:
        return companies[slug]
    except KeyError as e:
        known = ", ".join(sorted(companies))
        raise ValueError(f"Unknown company '{slug}'. Known: {known}") from e


def company_slug_from_label(label: str | None) -> str:
    """`sap:sec-edgar-20f` → `sap`. Fallback to the whole label, or empty.

    `IrSource.label` is "<slug>:<source-name>" by convention; downstream code
    only cares about the slug.
    """
    if not label:
        return ""
    return label.split(":", 1)[0]
