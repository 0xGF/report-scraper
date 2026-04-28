"""Shared helpers for extractors — heading detection, number parsing."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pipeline.domain.types import StatementKind, Unit

# Heading patterns per statement. Case-insensitive substring match, whitespace-insensitive.
# The matcher collapses runs of whitespace before testing — handles older PDF
# text extraction quirks where `Consolidated Statement` comes out as `ConsolidatedStatement`.
_STATEMENT_PATTERNS: dict[StatementKind, list[str]] = {
    StatementKind.INCOME: [
        "consolidated income statement",
        "consolidated statement of income",
        "consolidated statements of income",
        "consolidated statements of operations",
        "consolidated statement of operations",
        "consolidated statement of profit or loss",
        "consolidated statement of profit and loss",
        "consolidated profit and loss account",
        "consolidated statement of comprehensive income",
    ],
    StatementKind.BALANCE: [
        "consolidated statement of financial position",
        "consolidated statements of financial position",
        "consolidated balance sheet",
        "consolidated balance sheets",
    ],
    StatementKind.CASHFLOW: [
        "consolidated statement of cash flows",
        "consolidated statements of cash flows",
        "consolidated cash flow statement",
        "consolidated cash flows statement",
    ],
}

_WS_RE = re.compile(r"\s+")


def _normalize_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip().lower()


def match_statement(text: str) -> StatementKind | None:
    """Return the StatementKind whose heading patterns match this text, if any.

    Tries whitespace-collapsed match first, then a whitespace-stripped fallback
    for older PDFs where text extraction drops spaces (e.g.
    `ConsolidatedStatementsofOperations`).
    """
    normalized = _normalize_ws(text)
    stripped = normalized.replace(" ", "")
    for kind, patterns in _STATEMENT_PATTERNS.items():
        for pat in patterns:
            if pat in normalized or pat.replace(" ", "") in stripped:
                return kind
    return None


# -------- number parsing ---------------------------------------------------

_PAREN = re.compile(r"^\((.+)\)$")
_NEG_DASH = re.compile(r"^[–—]+$")  # noqa: RUF001 — EN DASH is intentional, matches PDF source
_EMPTY = {"", "-", "—", "–", "n/a", "na", "nm", "*"}  # noqa: RUF001 — same reason


def parse_number(raw: str | None) -> Decimal | None:
    """Parse a cell from a financial statement.

    Handles:
    - Parenthesized negatives: `(1,234)` → `-1234`
    - Thin-space / non-breaking-space thousand separators
    - Em-dash / en-dash as "missing"
    - Trailing notes like `1,234 (a)` — we strip the note
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s.lower() in _EMPTY or _NEG_DASH.match(s):
        return None
    # strip trailing footnote markers like "1,234 1" or "1,234 *"
    s = re.sub(r"\s*\(?[a-z*]\)?$", "", s, flags=re.IGNORECASE)
    s = s.strip()
    negative = False
    m = _PAREN.match(s)
    if m:
        negative = True
        s = m.group(1).strip()
    # remove currency symbols, thin spaces, commas, nbsp
    s = s.replace(",", "").replace(" ", "").replace("\u00a0", "").replace("\u2009", "")
    s = s.lstrip("€$£¥")
    if s in {"", "-"}:
        return None
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return -value if negative else value


# -------- unit detection ---------------------------------------------------

_UNIT_PATTERNS: list[tuple[Unit, re.Pattern[str]]] = [
    (Unit.MILLIONS, re.compile(r"in\s+million|\(\s*million|€\s*million|\bmillions of\b", re.I)),
    (
        Unit.THOUSANDS,
        re.compile(r"in\s+thousand|\(\s*thousand|€\s*thousand|\bthousands of\b", re.I),
    ),
    (Unit.BILLIONS, re.compile(r"in\s+billion|\(\s*billion", re.I)),
]


def detect_unit(text: str) -> Unit:
    for unit, pat in _UNIT_PATTERNS:
        if pat.search(text):
            return unit
    return Unit.UNITS


# -------- period header parsing --------------------------------------------

_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def parse_year_headers(headers: list[str]) -> list[int | None]:
    """Extract a year from each column header. None for non-period columns."""
    out: list[int | None] = []
    for h in headers:
        if not h:
            out.append(None)
            continue
        m = _YEAR_RE.search(h)
        out.append(int(m.group(1)) if m else None)
    return out


def row_has_period_headers(cells: list[str], *, min_periods: int = 1) -> bool:
    """True if at least `min_periods` cells in the row parse as fiscal years.

    Used by both PDF and HTML extractors to find the header row of a financial
    statement table — typically the row that contains "2024", "2023", "2022", etc.
    """
    return sum(1 for y in parse_year_headers(cells) if y) >= min_periods
