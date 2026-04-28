"""Tests for `inject_section_headers` — the Tesla-style hierarchy synthesis."""

from __future__ import annotations

from typing import Any

from pipeline.application.hierarchy import inject_section_headers


def _row(name: str, *, depth: int = 1, is_total: bool = False, order: int = 0) -> dict[str, Any]:
    return {
        "canonical_name": name,
        "depth": depth,
        "is_header": False,
        "is_total": is_total,
        "display_order": order,
        "parent_name": None,
        "values": {},
    }


def test_injects_header_above_total_section() -> None:
    rows = [
        _row("Cloud", order=0),
        _row("Software licenses", order=1),
        _row("Total revenue", is_total=True, order=2),
    ]
    out = inject_section_headers(rows)
    expected = ["Revenue", "Cloud", "Software licenses", "Total revenue"]
    assert [r["canonical_name"] for r in out] == expected
    assert out[0]["is_header"] is True
    assert out[0]["depth"] == 0
    assert out[1]["depth"] == 1
    assert out[3]["is_total"] is True


def test_grand_total_stands_alone() -> None:
    """`Gross profit` / `Net income` / etc. detected by name → no synthetic header."""
    rows = [
        _row("Total revenue", is_total=True, order=0),
        _row("Total cost of revenue", is_total=True, order=1),
        _row("Gross profit", is_total=True, depth=0, order=2),
    ]
    out = inject_section_headers(rows)
    names = [r["canonical_name"] for r in out]
    assert "Gross profit" in names
    # Gross profit is NOT preceded by a synthetic header
    gp_idx = names.index("Gross profit")
    prev = out[gp_idx - 1]
    assert not prev.get("is_header"), f"unexpected header before grand total: {prev}"


def test_multiple_sections() -> None:
    rows = [
        _row("Cloud", order=0),
        _row("Total revenue", is_total=True, order=1),
        _row("Cost of cloud", order=2),
        _row("Total cost of revenue", is_total=True, order=3),
    ]
    out = inject_section_headers(rows)
    names = [r["canonical_name"] for r in out]
    expected = [
        "Revenue",
        "Cloud",
        "Total revenue",
        "Cost of revenue",
        "Cost of cloud",
        "Total cost of revenue",
    ]
    assert names == expected
    assert out[0]["is_header"] is True
    assert out[3]["is_header"] is True


def test_existing_header_not_double_injected() -> None:
    """If the data already has an explicit header, don't add a synthetic one."""
    explicit_header = _row("Revenues", order=0)
    explicit_header["is_header"] = True
    rows = [
        explicit_header,
        _row("Cloud", order=1),
        _row("Total revenue", is_total=True, order=2),
    ]
    out = inject_section_headers(rows)
    headers = [r for r in out if r.get("is_header")]
    assert len(headers) == 1
    assert headers[0]["canonical_name"] == "Revenues"


def test_trailing_rows_without_total_emitted_as_is() -> None:
    """Tail rows that don't end in `Total X` get no synthetic header."""
    rows = [
        _row("Total revenue", is_total=True, order=0),
        _row("Earnings per share basic", order=1),
        _row("Earnings per share diluted", order=2),
    ]
    out = inject_section_headers(rows)
    names = [r["canonical_name"] for r in out]
    # No header is injected for the trailing block — that's expected.
    assert names == ["Total revenue", "Earnings per share basic", "Earnings per share diluted"]


def test_display_order_is_restamped_sequentially() -> None:
    rows = [
        _row("Cloud", order=10),
        _row("Total revenue", is_total=True, order=20),
    ]
    out = inject_section_headers(rows)
    assert [r["display_order"] for r in out] == list(range(len(out)))


def test_reindent_keeps_relative_depth() -> None:
    """Children at depths {0, 2} should become {1, 3} — relative differences preserved."""
    rows = [
        _row("Outer", depth=0, order=0),
        _row("Inner", depth=2, order=1),
        _row("Total foo", depth=0, is_total=True, order=2),
    ]
    out = inject_section_headers(rows)
    children = [r for r in out if r["canonical_name"] in ("Outer", "Inner")]
    assert children[0]["depth"] == 1
    assert children[1]["depth"] == 3


def test_total_subtotal_pinned_at_depth_one() -> None:
    rows = [
        _row("Cloud", order=0),
        _row("Total revenue", is_total=True, depth=5, order=1),
    ]
    out = inject_section_headers(rows)
    total = next(r for r in out if r["canonical_name"] == "Total revenue")
    assert total["depth"] == 1
