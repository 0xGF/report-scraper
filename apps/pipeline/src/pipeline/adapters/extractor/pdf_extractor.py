"""PDF extractor — locates the big three statements in a PDF annual report.

Strategy:
1. Stream pages with pdfplumber.
2. For each page, check its text for statement-heading patterns.
3. When a page matches a statement, try the current page's tables first;
   if the page has no tables (heading on its own page), try the next.
4. Pick the largest table that looks financial (has numeric cells + a
   year in the header row).
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import structlog

from pipeline.adapters.extractor.common import (
    detect_unit,
    match_statement,
    parse_number,
    parse_year_headers,
    row_has_period_headers,
)
from pipeline.domain.models import ClassifiedPdf, ExtractedStatement, RawLineItem
from pipeline.domain.types import Currency, StatementKind

log = structlog.get_logger()

# Tables shorter than this are almost always TOC fragments or page headers,
# never an actual financial statement.
MIN_TABLE_ROWS: int = 3
# Window of rows we scan from the top of a candidate table looking for the
# year-header row. Real statement tables put it within the first few rows.
HEADER_SCAN_ROWS: int = 6


class PdfplumberExtractor:
    """Legacy pdfplumber-based extractor. Kept as a fallback for PDFs where
    Docling fails (e.g. pages with unusually fragmented tables).
    """

    def extract(
        self,
        doc: ClassifiedPdf,
        *,
        currency: Currency = Currency.EUR,
    ) -> list[ExtractedStatement]:
        if not doc.fiscal_year:
            log.warning("extract.pdf.no_fiscal_year", sha=doc.sha256[:12])
            return []
        path = Path(doc.local_path)
        if not path.exists():
            log.warning("extract.pdf.missing", path=str(path))
            return []

        found: dict[StatementKind, ExtractedStatement] = {}
        try:
            with pdfplumber.open(path) as pdf:
                for i, page in enumerate(pdf.pages):
                    if len(found) == 3:
                        break
                    text = page.extract_text() or ""
                    lines = text.splitlines()
                    # Look in the first 6 lines — enough to skip headers/page numbers
                    # without picking up table-of-contents noise deeper down.
                    header_block = "\n".join(lines[:6])
                    kind = match_statement(header_block)
                    if kind is None or kind in found:
                        continue
                    # TOC rejection: if the first ~20 lines mention 2+ different
                    # statement kinds, this is a contents listing, not a statement.
                    toc_block = "\n".join(lines[:20])
                    distinct_kinds = _count_distinct_kinds(toc_block)
                    if distinct_kinds >= 2:
                        continue

                    unit = detect_unit(text[:2000])
                    tables = _collect_tables(pdf, i, lookahead=1)
                    best = _pick_best_table(tables)
                    if best is None:
                        log.debug(
                            "extract.pdf.no_table", sha=doc.sha256[:12], page=i, kind=kind.value
                        )
                        continue

                    rows = _rows_from_table(best)
                    if not rows:
                        continue

                    statement = ExtractedStatement(
                        company_slug=doc.company_slug,
                        source_sha256=doc.sha256,
                        source_fiscal_year=doc.fiscal_year or 0,
                        statement=kind,
                        currency=currency,
                        unit=unit,
                        rows=rows,
                    )
                    found[kind] = statement
                    log.info(
                        "extract.pdf.found",
                        sha=doc.sha256[:12],
                        statement=kind.value,
                        page=i,
                        rows=len(rows),
                        unit=unit.value,
                    )
        except Exception:
            log.exception("extract.pdf.failed", sha=doc.sha256[:12])
            return list(found.values())

        return list(found.values())


def _count_distinct_kinds(text: str) -> int:
    """How many distinct statement kinds are mentioned in this block.

    Used to reject table-of-contents pages, which list 2+ statement names
    together.
    """
    from pipeline.adapters.extractor.common import _STATEMENT_PATTERNS, _normalize_ws

    t = _normalize_ws(text)
    seen: set[StatementKind] = set()
    for kind, patterns in _STATEMENT_PATTERNS.items():
        if any(p in t or p.replace(" ", "") in t.replace(" ", "") for p in patterns):
            seen.add(kind)
    return len(seen)


def _collect_tables(
    pdf: pdfplumber.PDF, page_idx: int, *, lookahead: int
) -> list[list[list[str | None]]]:
    """Gather tables from this page and up to `lookahead` following pages."""
    out: list[list[list[str | None]]] = []
    end = min(len(pdf.pages), page_idx + 1 + lookahead)
    for i in range(page_idx, end):
        try:
            page_tables = pdf.pages[i].extract_tables() or []
        except Exception:
            page_tables = []
        for t in page_tables:
            if t and len(t) >= MIN_TABLE_ROWS:
                out.append(t)
    return out


def _pick_best_table(
    tables: list[list[list[str | None]]],
) -> list[list[str | None]] | None:
    """Score each table by (has-year-header) * (rows) and return the best."""
    best: list[list[str | None]] | None = None
    best_score = -1
    for t in tables:
        years_found = any(
            row_has_period_headers([(c or "").strip() for c in row]) for row in t[:HEADER_SCAN_ROWS]
        )
        if not years_found:
            continue
        score = len(t) + sum(1 for row in t for c in row if c and any(d.isdigit() for d in c))
        if score > best_score:
            best_score = score
            best = t
    return best


def _rows_from_table(table: list[list[str | None]]) -> list[RawLineItem]:
    header_row_idx = _find_header_row(table)
    if header_row_idx is None:
        return []
    header_cells = [(c or "").strip() for c in table[header_row_idx]]
    years = parse_year_headers(header_cells)

    rows: list[RawLineItem] = []
    for r_idx, row in enumerate(table[header_row_idx + 1 :], start=header_row_idx + 1):
        cells = [(c or "").strip() for c in row]
        if not cells:
            continue
        # The label column is the first non-empty cell; if that's also a year header row, skip
        label = cells[0]
        if not label:
            continue
        values_by_period: dict[str, _DecimalOrNone] = {}
        for c_idx, cell in enumerate(cells):
            year = years[c_idx] if c_idx < len(years) else None
            if year is None:
                continue
            values_by_period[f"{year}-12-31"] = parse_number(cell)

        if not values_by_period:
            continue

        rows.append(
            RawLineItem(
                raw_label=label,
                values_by_period_end=values_by_period,
                source_page=header_row_idx,  # not precise; good-enough provenance
                source_table_index=r_idx,
            )
        )
    return rows


def _find_header_row(table: list[list[str | None]]) -> int | None:
    for i, row in enumerate(table[:HEADER_SCAN_ROWS]):
        if row_has_period_headers([(c or "").strip() for c in row]):
            return i
    return None


# Pydantic Decimal/None type alias used only for annotation clarity
from decimal import Decimal as _DecimalOrNone  # noqa: E402


class PdfExtractor:
    """Primary PDF extractor.

    Runs Docling first (robust table-structure recognition), then fills any
    missing statements with the pdfplumber fallback. This keeps us resilient
    when either tool chokes on a particular report's layout.
    """

    def extract(
        self,
        doc: ClassifiedPdf,
        *,
        currency: Currency = Currency.EUR,
    ) -> list[ExtractedStatement]:
        from pipeline.adapters.extractor.docling_extractor import DoclingPdfExtractor

        docling_results = DoclingPdfExtractor().extract(doc, currency=currency)
        found = {s.statement for s in docling_results}
        if len(found) == 3:
            return docling_results

        plumber_results = PdfplumberExtractor().extract(doc, currency=currency)
        merged = list(docling_results)
        for s in plumber_results:
            if s.statement not in found:
                merged.append(s)
        return merged
