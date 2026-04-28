"""HTML extractor — locates the big three statements in an HTML annual report.

Works on SEC 20-F/10-K filings and any other IR HTML where the financial
statements are presented as real `<table>` elements (not images).

Strategy:
1. Parse the doc, walk every element.
2. For each heading-ish element (h1-h5, p, span, b, strong), check if its
   text matches a statement heading (see `common.match_statement`).
3. The next `<table>` in document order is the matching statement.
4. Parse the table, resolve column years from the header row, read rows.

Tolerant of: XBRL inline tags, weird whitespace, nbsp, missing heads.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import structlog
from lxml import etree
from lxml import html as lxml_html

from pipeline.adapters.extractor.common import (
    detect_unit,
    match_statement,
    parse_number,
    parse_year_headers,
    row_has_period_headers,
)
from pipeline.domain.models import ClassifiedPdf, ExtractedStatement, RawLineItem
from pipeline.domain.types import Currency, StatementKind, Unit

log = structlog.get_logger()

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "p", "span", "b", "strong", "div"}

# Prefixes that mark a heading as an analysis / discussion / MD&A section
# that merely *references* a statement by name, not the actual statement.
# Filtering these stops SAP's 20-F from binding the wrong table to
# `balance` or `cashflow`.
_DISCUSSION_PREFIXES = (
    "analysis of",
    "classification in",
    "presentation in",
    "reconciliation of",
    "change in",
    "changes in",
    "summary of",
    "notes to",
    "note to",
)


class HtmlExtractor:
    def extract(
        self,
        doc: ClassifiedPdf,
        *,
        currency: Currency = Currency.EUR,
    ) -> list[ExtractedStatement]:
        if not doc.fiscal_year:
            log.warning("extract.html.no_fiscal_year", sha=doc.sha256[:12])
            return []
        path = Path(doc.local_path)
        if not path.exists():
            log.warning("extract.html.missing", path=str(path))
            return []

        # lxml's html parser is tolerant of malformed filings
        tree = lxml_html.fromstring(path.read_bytes())

        # Walk all elements in document order. For each candidate heading,
        # pair it with the nearest-following <table> and score the result
        # by how statement-shaped it looks. Pick the best (kind, heading,
        # table) per kind — robust against TOC/MD&A sections that *mention*
        # a statement name but aren't the real heading.
        candidates: dict[StatementKind, list[_Candidate]] = {
            StatementKind.INCOME: [],
            StatementKind.BALANCE: [],
            StatementKind.CASHFLOW: [],
        }
        for element in tree.iter():
            tag = element.tag if isinstance(element.tag, str) else ""
            if tag not in _HEADING_TAGS:
                continue
            text = _element_text(element)
            if not text or len(text) > 150:
                continue
            if _is_discussion_heading(text):
                continue
            kind = match_statement(text)
            if kind is None:
                continue

            table = _next_table(element)
            if table is None:
                continue

            statement = _build_statement(
                kind=kind,
                table=table,
                heading_text=text,
                doc=doc,
                currency=currency,
            )
            if statement is None or not statement.rows:
                continue

            score = _score_candidate(kind, statement.rows)
            candidates[kind].append(_Candidate(score=score, statement=statement))

        results: dict[StatementKind, ExtractedStatement] = {}
        for kind, lst in candidates.items():
            if not lst:
                continue
            lst.sort(key=lambda c: c.score, reverse=True)
            best = lst[0].statement
            results[kind] = best
            log.info(
                "extract.html.found",
                sha=doc.sha256[:12],
                statement=kind.value,
                rows=len(best.rows),
                unit=best.unit.value,
                score=lst[0].score,
                alternatives=len(lst) - 1,
            )
        return list(results.values())


@dataclass
class _Candidate:
    score: int
    statement: ExtractedStatement


# Row-label keywords we expect to see in each statement. More matches = more
# confident this is actually the right table for this kind.
_ROW_SIGNALS: dict[StatementKind, tuple[str, ...]] = {
    StatementKind.INCOME: (
        "revenue",
        "sales",
        "cost of",
        "gross profit",
        "operating",
        "net income",
        "profit for",
        "earnings per share",
    ),
    StatementKind.BALANCE: (
        "cash and cash equivalents",
        "trade receivables",
        "goodwill",
        "intangible assets",
        "total assets",
        "total liabilities",
        "total equity",
        "retained earnings",
    ),
    StatementKind.CASHFLOW: (
        "depreciation",
        "net cash",
        "cash flows from operating",
        "cash flows from investing",
        "cash flows from financing",
        "proceeds from",
        "purchases of",
    ),
}


def _score_candidate(kind: StatementKind, rows: list[RawLineItem]) -> int:
    """Count how many of the expected row-signal keywords appear in the
    first column of the extracted table. A real consolidated statement
    hits several; a TOC-next-table or MD&A reference hits zero or one.
    """
    signals = _ROW_SIGNALS[kind]
    corpus = " ".join(r.raw_label.lower() for r in rows)
    hits = sum(1 for s in signals if s in corpus)
    return hits * 10 + len(rows)


def _is_discussion_heading(text: str) -> bool:
    """True if the heading is an analysis / discussion / TOC entry that
    mentions a statement by name instead of being the statement's own
    heading (e.g. 'Analysis of Consolidated Statements of Cash Flows').
    """
    low = text.lower().lstrip()
    # strip leading punctuation/section markers
    low = low.lstrip("/()[]⚫• \t.0123456789")
    return low.startswith(_DISCUSSION_PREFIXES)


def _element_text(el: etree._Element) -> str:
    """Shallow text of an element — joined descendant text, trimmed."""
    return " ".join((el.text_content() or "").split())


def _next_table(heading: etree._Element) -> etree._Element | None:
    """Find the nearest following <table> in document order."""
    # iterate forward through the tree; skip tables nested inside the heading itself
    iterator = heading.iter()
    next(iterator, None)  # consume self
    for node in iterator:
        if isinstance(node.tag, str) and node.tag == "table":
            return node
    # walk siblings and subsequent cousins
    cur = heading
    while cur is not None:
        sib = cur.getnext()
        while sib is not None:
            if isinstance(sib.tag, str):
                if sib.tag == "table":
                    return sib
                found = sib.find(".//table")
                if found is not None:
                    return found
            sib = sib.getnext()
        cur = cur.getparent()
    return None


def _build_statement(
    *,
    kind: StatementKind,
    table: etree._Element,
    heading_text: str,
    doc: ClassifiedPdf,
    currency: Currency,
) -> ExtractedStatement | None:
    unit = _infer_unit(table, heading_text)
    header_cells = _header_row(table)
    if not header_cells:
        return None
    years = parse_year_headers(header_cells)

    rows: list[RawLineItem] = []
    for row_idx, tr in enumerate(table.iter("tr")):
        cells = [_cell_text(td) for td in tr.iter("th", "td")]
        if len(cells) < 2:
            continue
        label, *values = cells
        label = label.strip()
        if not label:
            continue
        # drop the header row itself
        if _looks_like_header(cells):
            continue
        values_by_period: dict[str, Decimal | None] = {}
        for idx, val in enumerate(values):
            col_idx = idx + 1  # offset for the label column
            year = years[col_idx] if col_idx < len(years) else None
            if year is None:
                continue
            values_by_period[f"{year}-12-31"] = parse_number(val)

        if not values_by_period:
            # pure header/separator row or no numeric cells
            continue

        rows.append(
            RawLineItem(
                raw_label=label,
                values_by_period_end=values_by_period,
                source_page=0,  # HTML has no pages
                source_table_index=row_idx,
            )
        )

    if not rows:
        return None

    return ExtractedStatement(
        company_slug=doc.company_slug,
        source_sha256=doc.sha256,
        source_fiscal_year=doc.fiscal_year or 0,
        statement=kind,
        currency=currency,
        unit=unit,
        rows=rows,
    )


def _header_row(table: etree._Element) -> list[str]:
    """Pick the header row — first row containing ≥1 year-like token."""
    rows = list(table.iter("tr"))
    for tr in rows[:5]:
        cells = [_cell_text(c) for c in tr.iter("th", "td")]
        if row_has_period_headers(cells):
            return cells
    # fallback: first row of the table
    if rows:
        return [_cell_text(c) for c in rows[0].iter("th", "td")]
    return []


def _cell_text(cell: etree._Element) -> str:
    txt = cell.text_content() or ""
    return " ".join(txt.split())


def _looks_like_header(cells: list[str]) -> bool:
    return row_has_period_headers(cells) and any(c.strip() for c in cells)


def _infer_unit(table: etree._Element, heading_text: str) -> Unit:
    """Look for 'in millions' / 'in thousands' language near the table."""
    near = heading_text + " "
    # also scan a few ancestors for caption/preceding-note text
    for rel in _preceding_text(table, limit=6):
        near += " " + rel
    return detect_unit(near)


def _preceding_text(table: etree._Element, *, limit: int) -> list[str]:
    out: list[str] = []
    cur = table
    while cur is not None and len(out) < limit:
        sib = cur.getprevious()
        while sib is not None and len(out) < limit:
            if isinstance(sib.tag, str):
                text = sib.text_content() or ""
                text = " ".join(text.split())
                if text:
                    out.append(text)
            sib = sib.getprevious()
        cur = cur.getparent()
    return out
