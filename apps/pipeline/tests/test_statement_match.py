"""Tests for `match_statement` + `parse_year_headers` — the heading and
period detectors used by every extractor.
"""

from __future__ import annotations

from pipeline.adapters.extractor.common import (
    match_statement,
    parse_year_headers,
)
from pipeline.adapters.extractor.docling_extractor import _heading_line_match
from pipeline.adapters.extractor.pdf_extractor import _count_distinct_kinds
from pipeline.domain.types import StatementKind


def test_match_income_us_gaap() -> None:
    assert match_statement("Consolidated Statements of Operations") == StatementKind.INCOME


def test_match_income_ifrs() -> None:
    assert match_statement("Consolidated Statement of Profit or Loss") == StatementKind.INCOME


def test_match_balance_ifrs() -> None:
    assert match_statement("Consolidated Statement of Financial Position") == StatementKind.BALANCE


def test_match_balance_us_gaap() -> None:
    assert match_statement("Consolidated Balance Sheets") == StatementKind.BALANCE


def test_match_cashflow() -> None:
    assert match_statement("Consolidated Statements of Cash Flows") == StatementKind.CASHFLOW


def test_match_ignores_unrelated_text() -> None:
    assert match_statement("Management Discussion and Analysis") is None


def test_match_whitespace_collapsed() -> None:
    # Old PDFs can drop spaces during text extraction
    assert match_statement("ConsolidatedStatementsofOperations") == StatementKind.INCOME


def test_parse_year_headers_basic() -> None:
    years = parse_year_headers(["Line item", "2023", "2022", "2021"])
    assert years == [None, 2023, 2022, 2021]


def test_parse_year_headers_year_ended() -> None:
    years = parse_year_headers(["", "Year ended December 31, 2024", "Year ended December 31, 2023"])
    assert years == [None, 2024, 2023]


def test_parse_year_headers_ignores_non_period() -> None:
    years = parse_year_headers(["Line item", "Note", "2023", "2022"])
    assert years == [None, None, 2023, 2022]


def test_count_distinct_kinds_toc_page() -> None:
    # A TOC page lists all three
    toc = (
        "Consolidated Statements of Operations\n"
        "Consolidated Balance Sheets\n"
        "Consolidated Statements of Cash Flows\n"
    )
    assert _count_distinct_kinds(toc) == 3


def test_count_distinct_kinds_single_statement_page() -> None:
    assert _count_distinct_kinds("Consolidated Balance Sheets\nAs of December 31, 2024") == 1


def test_heading_line_match_rejects_embedded_mentions() -> None:
    # A prose page that merely mentions the statement should NOT match —
    # this was the ASML-2025 EU-Taxonomy bug.
    lines = [
        "Operational expenditure",
        "that are not capitalized but accounted for in",
        "our Consolidated statement of profit or loss associated",
        "with CE 1.2 Manufacture of electrical and electronic",
    ]
    assert _heading_line_match(lines) is None


def test_heading_line_match_accepts_real_heading() -> None:
    lines = [
        "4",
        "Financial statements",
        "Consolidated Statements of Income",
        "Year ended December 31,",
    ]
    assert _heading_line_match(lines) == StatementKind.INCOME
