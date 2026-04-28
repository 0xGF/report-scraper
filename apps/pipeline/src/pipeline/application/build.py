"""`report-scrape build` — load every company's normalized line items + facts
into DuckDB. The `consolidated` view in DuckDB implements the restatement
logic: for each (company, statement, line_item, period), pick the row
with the greatest source_fiscal_year.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from pipeline.adapters.store import DuckdbStore
from pipeline.config import get_settings
from pipeline.domain.companies import get_all_companies
from pipeline.domain.models import FactValue, LineItem

log = structlog.get_logger()


def _normalized_path(raw_dir: Path, slug: str) -> Path:
    return raw_dir / f"{slug}.normalized.json"


def run() -> None:
    settings = get_settings()
    with DuckdbStore(settings.report_scrape_duckdb_path) as store:
        for slug in get_all_companies():
            path = _normalized_path(settings.report_scrape_raw_dir, slug)
            if not path.exists():
                log.info("build.skip", company=slug, reason="no normalized file")
                continue
            payload = json.loads(path.read_text())
            line_items = [LineItem.model_validate(x) for x in payload.get("line_items", [])]
            facts = [FactValue.model_validate(x) for x in payload.get("facts", [])]

            store.reset_company(slug)
            store.upsert_line_items(line_items)
            store.upsert_facts(facts)

            log.info(
                "build.loaded",
                company=slug,
                line_items=len(line_items),
                facts=len(facts),
                summary=store.summary(slug),
            )
