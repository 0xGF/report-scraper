"""Pipeline-level smoke tests against the committed `data/exports/`.

These run *after* the pipeline has been executed at least once and assert
that the exported JSON files contain the rows + values we expect for each
company's latest report. They guard against the silent-regression class
of bugs (e.g. "balance sheet is now full of revenue rows") that unit
tests can't catch.

Skipped automatically when the exports aren't present, so CI on a fresh
checkout doesn't fail until the pipeline has run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_EXPORTS = Path(__file__).resolve().parents[3] / "data" / "exports"


def _load(slug: str, statement: str) -> dict | None:
    path = _EXPORTS / slug / f"{statement}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _row(payload: dict, name: str) -> dict | None:
    name_lower = name.lower()
    for r in payload["rows"]:
        if r["canonical_name"].lower() == name_lower:
            return r
    return None


def _value(row: dict, period_end: str) -> float | None:
    cell = row["values"].get(period_end)
    return cell["value"] if cell else None


# Each entry: (company, statement, expected row label, [periods that should have values])
_EXPECTATIONS: list[tuple[str, str, str, list[str]]] = [
    # SAP — should hit revenue, total assets, net cash from operating
    ("sap", "income", "Cloud", ["2024-12-31", "2023-12-31"]),
    ("sap", "balance", "Cash and cash equivalents", ["2024-12-31"]),
    ("sap", "cashflow", "Profit (loss) after tax", ["2024-12-31"]),
    # ASML — Net system sales is the headline IS line
    ("asml", "income", "Net system sales", ["2024-12-31", "2023-12-31"]),
    ("asml", "cashflow", "Net income", ["2024-12-31"]),
    # Dassault — TOTAL REVENUE is the IS marquee, Cash is the BS opener
    ("dassault", "income", "TOTAL REVENUE", ["2024-12-31", "2023-12-31"]),
    ("dassault", "balance", "Cash and cash equivalents", ["2024-12-31"]),
    ("dassault", "cashflow", "Net income", ["2024-12-31"]),
]


@pytest.mark.parametrize("company,statement,expected_label,periods", _EXPECTATIONS)
def test_export_contains_expected_row(
    company: str, statement: str, expected_label: str, periods: list[str]
) -> None:
    payload = _load(company, statement)
    if payload is None:
        pytest.skip(f"No export for {company}/{statement} — pipeline not run")
    row = _row(payload, expected_label)
    assert row is not None, (
        f"{company}/{statement}: expected row '{expected_label}' not found. "
        f"Top labels: {[r['canonical_name'] for r in payload['rows'][:8]]}"
    )
    for p in periods:
        v = _value(row, p)
        assert v is not None, f"{company}/{statement} '{expected_label}' has no value for {p}"


@pytest.mark.parametrize(
    "company,statement",
    [
        ("sap", "income"),
        ("sap", "balance"),
        ("sap", "cashflow"),
        ("asml", "income"),
        ("asml", "balance"),
        ("asml", "cashflow"),
        ("dassault", "income"),
        ("dassault", "balance"),
        ("dassault", "cashflow"),
    ],
)
def test_export_has_at_least_10_years(company: str, statement: str) -> None:
    payload = _load(company, statement)
    if payload is None:
        pytest.skip(f"No export for {company}/{statement} — pipeline not run")
    assert len(payload["periods"]) >= 10, (
        f"{company}/{statement}: only {len(payload['periods'])} periods "
        f"({payload['periods']}); need ≥10 for the assignment."
    )


@pytest.mark.parametrize(
    "company,statement,wrong_label",
    [
        # If 'balance' sneaks back into income statement as a wrong-table extraction
        ("sap", "balance", "Total revenue"),
        ("dassault", "balance", "TOTAL REVENUE"),
        ("asml", "balance", "Net system sales"),
    ],
)
def test_balance_sheet_is_not_income_statement(
    company: str, statement: str, wrong_label: str
) -> None:
    """Catches the SAP-2025 class of bug where balance sheet rows are
    actually revenue breakdowns from a misidentified table."""
    payload = _load(company, statement)
    if payload is None:
        pytest.skip(f"No export for {company}/{statement} — pipeline not run")
    row = _row(payload, wrong_label)
    assert row is None, (
        f"{company}/{statement} contains '{wrong_label}' — it's leaking "
        f"income statement data into the balance sheet."
    )
