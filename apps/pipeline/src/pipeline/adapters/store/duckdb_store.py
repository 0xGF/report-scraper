"""DuckDB store for line items + facts + restatement-aware consolidation.

Schema:
    line_items(company_slug, statement, canonical_name, depth, parent_name,
               is_header, is_total, display_order)
    facts(company_slug, statement, canonical_name, period_end, period_months,
          value, currency, unit, source_sha256, source_fiscal_year, is_restated)

Consolidated view:
    consolidated = SELECT ... FROM facts
                   QUALIFY row_number() OVER (
                     PARTITION BY company, statement, name, period_end
                     ORDER BY source_fiscal_year DESC
                   ) = 1

This gives the "latest restated value per cell" view — the bonus in the
assignment spec.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import structlog

from pipeline.domain.models import FactValue, LineItem

log = structlog.get_logger()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS line_items (
    company_slug VARCHAR NOT NULL,
    statement VARCHAR NOT NULL,
    canonical_name VARCHAR NOT NULL,
    depth INTEGER NOT NULL,
    parent_name VARCHAR,
    is_header BOOLEAN NOT NULL,
    is_total BOOLEAN NOT NULL,
    display_order INTEGER NOT NULL,
    PRIMARY KEY (company_slug, statement, canonical_name)
);

CREATE TABLE IF NOT EXISTS facts (
    company_slug VARCHAR NOT NULL,
    statement VARCHAR NOT NULL,
    canonical_name VARCHAR NOT NULL,
    period_end DATE NOT NULL,
    period_months INTEGER NOT NULL,
    value DECIMAL(30, 4),
    currency VARCHAR NOT NULL,
    unit VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL,
    source_fiscal_year INTEGER NOT NULL,
    is_restated BOOLEAN NOT NULL,
    PRIMARY KEY (company_slug, statement, canonical_name, period_end, source_fiscal_year)
);

-- Consolidated view: pick the latest source for every cell, AND mark a cell as
-- `is_restated` ONLY when a different value was reported by an earlier source
-- (i.e. the company actually changed the figure across reports). This matches
-- the financial meaning of "restated" — a previously-reported number that was
-- corrected, reclassified, or otherwise updated in a later filing.
CREATE OR REPLACE VIEW consolidated AS
WITH ranked AS (
    SELECT
        company_slug, statement, canonical_name, period_end, period_months,
        value, currency, unit, source_sha256, source_fiscal_year,
        row_number() OVER (
            PARTITION BY company_slug, statement, canonical_name, period_end
            ORDER BY source_fiscal_year DESC
        ) AS rn,
        COUNT(value) OVER (
            PARTITION BY company_slug, statement, canonical_name, period_end
        ) AS non_null_sources,
        MIN(value) OVER (
            PARTITION BY company_slug, statement, canonical_name, period_end
        ) AS min_value,
        MAX(value) OVER (
            PARTITION BY company_slug, statement, canonical_name, period_end
        ) AS max_value
    FROM facts
)
SELECT
    company_slug, statement, canonical_name, period_end, period_months,
    value, currency, unit, source_sha256, source_fiscal_year,
    -- Restated when ≥ 2 sources reported a value AND those values differ.
    -- Tolerance of 0.01 absorbs trivial DECIMAL(30,4) rounding.
    (
        non_null_sources >= 2
        AND min_value IS NOT NULL
        AND max_value IS NOT NULL
        AND ABS(max_value - min_value) > 0.01
    ) AS is_restated
FROM ranked
WHERE rn = 1;
"""


class DuckdbStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(db_path))
        self._conn.execute(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DuckdbStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def reset_company(self, company_slug: str) -> None:
        self._conn.execute("DELETE FROM line_items WHERE company_slug = ?", [company_slug])
        self._conn.execute("DELETE FROM facts WHERE company_slug = ?", [company_slug])

    def upsert_line_items(self, items: list[LineItem]) -> None:
        if not items:
            return
        rows = [
            (
                i.company_slug,
                i.statement.value,
                i.canonical_name,
                i.depth,
                i.parent_name,
                i.is_header,
                i.is_total,
                i.display_order,
            )
            for i in items
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO line_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    def upsert_facts(self, facts: list[FactValue]) -> None:
        if not facts:
            return
        rows = [
            (
                f.company_slug,
                f.statement.value,
                f.canonical_name,
                f.period_end,
                f.period_months,
                float(f.value) if f.value is not None else None,
                f.currency.value,
                f.unit.value,
                f.source_sha256,
                f.source_fiscal_year,
                f.is_restated,
            )
            for f in facts
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    def consolidated_rows(self, company_slug: str, statement: str) -> list[dict[str, Any]]:
        """Return consolidated (latest-restated) facts for one (company, statement)."""
        r = self._conn.execute(
            """
            SELECT c.canonical_name,
                   c.depth, c.is_header, c.is_total, c.display_order, c.parent_name,
                   f.period_end, f.value, f.currency, f.unit,
                   f.source_fiscal_year, f.is_restated
            FROM line_items c
            LEFT JOIN consolidated f
              ON f.company_slug = c.company_slug
             AND f.statement = c.statement
             AND f.canonical_name = c.canonical_name
            WHERE c.company_slug = ? AND c.statement = ?
            ORDER BY c.display_order, f.period_end DESC
            """,
            [company_slug, statement],
        ).fetchall()
        cols = [
            "canonical_name",
            "depth",
            "is_header",
            "is_total",
            "display_order",
            "parent_name",
            "period_end",
            "value",
            "currency",
            "unit",
            "source_fiscal_year",
            "is_restated",
        ]
        return [dict(zip(cols, row, strict=True)) for row in r]

    def summary(self, company_slug: str) -> dict[str, int]:
        r = self._conn.execute(
            """
            SELECT statement, COUNT(*)
            FROM facts WHERE company_slug = ?
            GROUP BY statement
            """,
            [company_slug],
        ).fetchall()
        return {stmt: n for stmt, n in r}
