"""Hierarchy inference over a representative Income Statement row list.

Covers section-header detection (value-less rows push a new depth level),
uppercase-total detection, and parent-name propagation.
"""

from __future__ import annotations

from decimal import Decimal

from pipeline.application.normalize import _infer_hierarchy
from pipeline.domain.models import RawLineItem


def _row(label: str, *, values: dict[str, Decimal | None] | None = None) -> RawLineItem:
    return RawLineItem(
        raw_label=label,
        values_by_period_end=values or {"2024-12-31": Decimal("1.0")},
        source_page=0,
        source_table_index=0,
    )


def test_header_pushes_depth() -> None:
    rows = [
        _row("Revenue"),
        _row("Attributable to:", values={"2024-12-31": None}),
        _row("Equity holders"),
        _row("Non-controlling interests"),
    ]
    hierarchy = _infer_hierarchy(rows)
    assert hierarchy[0].depth == 0
    assert hierarchy[1].is_header is True
    assert hierarchy[1].depth == 0
    assert hierarchy[2].depth == 1
    assert hierarchy[2].parent_name == "Attributable to:"
    assert hierarchy[3].depth == 1


def test_uppercase_is_total() -> None:
    rows = [
        _row("Revenue"),
        _row("TOTAL REVENUE"),
        _row("Cost of sales"),
        _row("NET INCOME"),
    ]
    hierarchy = _infer_hierarchy(rows)
    assert hierarchy[1].is_total is True
    assert hierarchy[3].is_total is True
    assert hierarchy[2].is_total is False


def test_prefix_totals() -> None:
    rows = [
        _row("Revenue"),
        _row("Total revenue"),
        _row("Operating income"),
        _row("Net cash provided by operating activities"),
    ]
    hierarchy = _infer_hierarchy(rows)
    assert hierarchy[1].is_total is True
    assert hierarchy[2].is_total is True
    assert hierarchy[3].is_total is True


def test_plain_rows_default_depth_zero() -> None:
    rows = [
        _row("Cash and cash equivalents"),
        _row("Trade receivables"),
    ]
    hierarchy = _infer_hierarchy(rows)
    assert all(h.depth == 0 and not h.is_header for h in hierarchy)
