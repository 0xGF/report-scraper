"""Integration test for the `consolidated` view — verifies the bonus
restatement logic picks the *newest* report's value when a cell was
later restated.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from pipeline.adapters.store.duckdb_store import DuckdbStore
from pipeline.domain.models import FactValue, LineItem
from pipeline.domain.types import Currency, StatementKind, Unit


def _li(name: str, *, order: int, is_total: bool = False) -> LineItem:
    return LineItem(
        company_slug="testco",
        statement=StatementKind.INCOME,
        canonical_name=name,
        depth=1,
        parent_name=None,
        is_header=False,
        is_total=is_total,
        display_order=order,
    )


def _fact(*, value: Decimal, fy: int, is_restated: bool = False) -> FactValue:
    return FactValue(
        company_slug="testco",
        statement=StatementKind.INCOME,
        canonical_name="Revenue",
        period_end=date(2023, 12, 31),
        period_months=12,
        value=value,
        currency=Currency.EUR,
        unit=Unit.MILLIONS,
        source_sha256="a" * 64,
        source_fiscal_year=fy,
        is_restated=is_restated,
    )


def test_consolidated_picks_newest_source(tmp_path: Path) -> None:
    db = DuckdbStore(tmp_path / "test.duckdb")
    db.reset_company("testco")
    db.upsert_line_items([_li("Revenue", order=0)])
    # Two reports cover the same period: 2023 annual says 100, 2024 annual
    # restates 2023's value to 105. Consolidated should return 105.
    db.upsert_facts(
        [
            _fact(value=Decimal("100"), fy=2023, is_restated=False),
            _fact(value=Decimal("105"), fy=2024, is_restated=True),
        ]
    )
    rows = db.consolidated_rows("testco", StatementKind.INCOME.value)
    assert len(rows) == 1
    assert rows[0]["value"] == 105.0
    assert rows[0]["source_fiscal_year"] == 2024
    assert rows[0]["is_restated"] is True


def test_consolidated_multiple_periods(tmp_path: Path) -> None:
    db = DuckdbStore(tmp_path / "test.duckdb")
    db.reset_company("testco")
    db.upsert_line_items([_li("Revenue", order=0)])
    # 2023 report: one fact for 2023 period.
    # 2024 report: restates 2023 AND adds 2024.
    rev_2023_orig = _fact(value=Decimal("100"), fy=2023)
    rev_2023_restated = _fact(value=Decimal("102"), fy=2024, is_restated=True)
    rev_2024 = FactValue(
        company_slug="testco",
        statement=StatementKind.INCOME,
        canonical_name="Revenue",
        period_end=date(2024, 12, 31),
        period_months=12,
        value=Decimal("120"),
        currency=Currency.EUR,
        unit=Unit.MILLIONS,
        source_sha256="a" * 64,
        source_fiscal_year=2024,
        is_restated=False,
    )
    db.upsert_facts([rev_2023_orig, rev_2023_restated, rev_2024])
    rows = db.consolidated_rows("testco", StatementKind.INCOME.value)
    by_period = {r["period_end"]: r for r in rows}
    assert by_period[date(2023, 12, 31)]["value"] == 102.0
    assert by_period[date(2024, 12, 31)]["value"] == 120.0
