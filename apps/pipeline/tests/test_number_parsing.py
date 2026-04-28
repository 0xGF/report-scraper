"""Unit tests for `common.parse_number`.

Covers edge cases we've actually seen in the wild across SAP / ASML /
Dassault: parenthesized negatives, em-dash for missing, thin-space /
nbsp thousand separators, trailing footnote markers, and currency
symbols.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from pipeline.adapters.extractor.common import parse_number


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,234", Decimal("1234")),
        ("1,234.5", Decimal("1234.5")),
        ("(1,234)", Decimal("-1234")),
        ("(1,234.5)", Decimal("-1234.5")),
        ("—", None),
        ("–", None),  # noqa: RUF001 — en dash is an intentional fixture
        ("-", None),
        ("", None),
        ("n/a", None),
        ("NM", None),
        # Thin-space (U+2009) thousand separator
        ("1\u20092\u2009345", Decimal("12345")),
        # Non-breaking-space separator
        ("1\u00a0234", Decimal("1234")),
        # Trailing footnote marker
        ("1,234 *", Decimal("1234")),
        ("1,234 (a)", Decimal("1234")),
        ("567b", Decimal("567")),
        # Currency symbols stripped
        ("€1,234", Decimal("1234")),
        ("$1,234.5", Decimal("1234.5")),
        ("£567", Decimal("567")),
    ],
)
def test_parse_number_cases(raw: str, expected: Decimal | None) -> None:
    assert parse_number(raw) == expected


def test_parse_number_none_passthrough() -> None:
    assert parse_number(None) is None


def test_parse_number_garbage() -> None:
    assert parse_number("nothing useful") is None
