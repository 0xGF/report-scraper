"""`report-scrape export` — emit the viewer-friendly JSON.

For each (company, statement) pair, writes
`data/exports/{company}/{statement}.json` containing:

- `company`, `statement`, `currency`, `unit`
- `periods`: sorted-descending list of period_end strings
- `rows`: line items in display order, each with `values` keyed by period
  and `{value, source_fiscal_year, is_restated}` triplets

That schema is exactly what AG Grid consumes on the Next.js side.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from pipeline.adapters.store import DuckdbStore
from pipeline.application.hierarchy import inject_section_headers
from pipeline.config import get_settings
from pipeline.domain.companies import get_all_companies
from pipeline.domain.types import StatementKind

log = structlog.get_logger()

_STATEMENTS = [StatementKind.INCOME, StatementKind.BALANCE, StatementKind.CASHFLOW]


def _export_path(base: Path, slug: str, statement: str) -> Path:
    (base / slug).mkdir(parents=True, exist_ok=True)
    return base / slug / f"{statement}.json"


def _index_path(base: Path) -> Path:
    return base / "index.json"


def run() -> None:
    settings = get_settings()
    exports_dir = settings.report_scrape_exports_dir
    exports_dir.mkdir(parents=True, exist_ok=True)

    statements_index: list[dict[str, Any]] = []
    companies_index: list[dict[str, Any]] = []

    with DuckdbStore(settings.report_scrape_duckdb_path) as store:
        for slug, company in get_all_companies().items():
            had_export = False
            for statement in _STATEMENTS:
                raw_rows = store.consolidated_rows(slug, statement.value)
                if not raw_rows:
                    log.info("export.skip", company=slug, statement=statement.value)
                    continue

                # Group cells by canonical_name; collect periods
                by_line: dict[str, dict[str, Any]] = {}
                periods: set[str] = set()
                currency: str | None = None
                unit: str | None = None

                for r in raw_rows:
                    name = r["canonical_name"]
                    if name not in by_line:
                        by_line[name] = {
                            "canonical_name": name,
                            "depth": r["depth"],
                            "is_header": r["is_header"],
                            "is_total": r["is_total"],
                            "display_order": r["display_order"],
                            "parent_name": r["parent_name"],
                            "values": {},
                        }
                    if r["period_end"] is None:
                        continue
                    period = r["period_end"].isoformat()
                    periods.add(period)
                    by_line[name]["values"][period] = {
                        "value": r["value"],
                        "source_fiscal_year": r["source_fiscal_year"],
                        "is_restated": r["is_restated"],
                    }
                    if currency is None:
                        currency = r["currency"]
                    if unit is None:
                        unit = r["unit"]

                flat_rows = sorted(by_line.values(), key=lambda x: x["display_order"])
                rows = inject_section_headers(flat_rows)
                sorted_periods = sorted(periods, reverse=True)

                payload = {
                    "company": company.name,
                    "company_slug": slug,
                    "ticker": company.ticker,
                    "exchange": company.exchange,
                    "statement": statement.value,
                    "currency": currency,
                    "unit": unit,
                    "periods": sorted_periods,
                    "rows": rows,
                }
                _export_path(exports_dir, slug, statement.value).write_text(
                    json.dumps(payload, indent=2, default=str)
                )
                statements_index.append(
                    {
                        "company_slug": slug,
                        "company_name": company.name,
                        "ticker": company.ticker,
                        "statement": statement.value,
                        "periods": sorted_periods,
                        "rows": len(rows),
                    }
                )
                had_export = True
                log.info(
                    "export.written",
                    company=slug,
                    statement=statement.value,
                    rows=len(rows),
                    periods=len(sorted_periods),
                )

            # Always list every configured company. `has_data` lets the viewer
            # render newly-discovered companies (in companies.yaml but not yet
            # pipelined) with a "pipeline pending" affordance instead of
            # linking to empty pages.
            companies_index.append(
                {
                    "slug": slug,
                    "name": company.name,
                    "ticker": company.ticker,
                    "exchange": company.exchange,
                    "reporting_currency": company.reporting_currency.value,
                    "ir_url": company.ir_url,
                    "website": company.website,
                    "logo_url": company.logo_url,
                    "description": company.description,
                    "sector": company.sector,
                    "headquarters": company.headquarters,
                    "founded": company.founded,
                    "source_note": company.source_note,
                    "has_data": had_export,
                }
            )

    _index_path(exports_dir).write_text(
        json.dumps(
            {"companies": companies_index, "statements": statements_index},
            indent=2,
        )
    )
    log.info(
        "export.done",
        companies=len(companies_index),
        statements=len(statements_index),
    )
