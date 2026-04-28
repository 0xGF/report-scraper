"""Docling-based PDF extractor.

Hybrid strategy — fast text scan + targeted table extraction:

1. Open the PDF with pdfplumber and scan each page's first ~15 lines of
   text for a statement heading (IS / BS / CF). The page must also look
   like a real statement page (has `Year ended` / `(in millions)` / etc.)
   so that footnote references and section intros don't match.
2. Call Docling once over the smallest page range that covers all
   candidates. Docling's pipeline is trimmed (no OCR, no images).
3. Tables in the result carry page provenance. Route each table to the
   candidate whose heading page it matches (±1 for wrap-around).
4. Pick the best table per statement and convert via
   `export_to_dataframe` → `RawLineItem`s. Leading whitespace on the
   label column is preserved (hierarchy signal for Step 3).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pdfplumber
import structlog

from pipeline.adapters.extractor.common import (
    detect_unit,
    match_statement,
    parse_number,
    parse_year_headers,
)
from pipeline.adapters.extractor.pdf_extractor import _count_distinct_kinds
from pipeline.domain.models import ClassifiedPdf, ExtractedStatement, RawLineItem
from pipeline.domain.types import Currency, StatementKind, Unit

log = structlog.get_logger()

# How far after the heading line to scan (covers boilerplate + section numbers).
_HEADING_LINES = 12

# Docling page range is 1-indexed & inclusive. Two-page window per heading
# is plenty — statements usually fit on one page; if they wrap, it's to the next.
_PAGE_WINDOW_BEFORE = 0
_PAGE_WINDOW_AFTER = 1

# Signals that a page actually contains a statement TABLE, not just a
# section intro / footnote that happens to mention the statement name.
_TABLE_PAGE_HINTS = (
    "year ended",
    "period ended",
    "as at",
    "as of",
    "(in million",
    "(in thousand",
    "(in \u20ac million",
    "in millions",
    "in thousands",
    "in euros",
)

_converter: Any | None = None


def _get_converter() -> Any:
    """Lazy-initialize a single DocumentConverter with a trimmed pipeline."""
    global _converter
    if _converter is not None:
        return _converter

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = True
    opts.generate_page_images = False
    opts.generate_picture_images = False

    _converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    return _converter


class DoclingPdfExtractor:
    def extract(
        self,
        doc: ClassifiedPdf,
        *,
        currency: Currency = Currency.EUR,
    ) -> list[ExtractedStatement]:
        if not doc.fiscal_year:
            log.warning("extract.docling.no_fiscal_year", sha=doc.sha256[:12])
            return []
        path = Path(doc.local_path)
        if not path.exists():
            log.warning("extract.docling.missing", path=str(path))
            return []

        try:
            candidates = _find_candidate_pages(path)
        except Exception:
            log.exception("extract.docling.scan_failed", sha=doc.sha256[:12])
            return []

        if not candidates:
            log.info("extract.docling.no_candidates", sha=doc.sha256[:12])
            return []

        # Call Docling once covering all candidate windows (1-indexed).
        start_1 = min(c[1] for c in candidates) + 1 - _PAGE_WINDOW_BEFORE
        end_1 = max(c[1] for c in candidates) + 1 + _PAGE_WINDOW_AFTER
        start_1 = max(1, start_1)

        try:
            converter = _get_converter()
            result = converter.convert(path, page_range=(start_1, end_1))
            docling_doc = result.document
        except Exception:
            log.exception("extract.docling.convert_failed", sha=doc.sha256[:12])
            return []

        # Group Docling tables by page.
        tables_by_page = _group_tables_by_page(docling_doc)

        found: dict[StatementKind, ExtractedStatement] = {}
        for kind, page_idx, unit_hint in candidates:
            if kind in found:
                continue
            # Docling uses 1-indexed page numbers.
            target_1 = page_idx + 1
            window_tables: list[Any] = []
            for offset in range(-_PAGE_WINDOW_BEFORE, _PAGE_WINDOW_AFTER + 1):
                window_tables.extend(tables_by_page.get(target_1 + offset, []))
            if not window_tables:
                continue

            best = _pick_best_table(window_tables, docling_doc, target_page=target_1)
            if best is None:
                continue
            rows = _rows_from_dataframe(best, page_idx=page_idx)
            if len(rows) < 5:
                # Too few rows to be a real statement — let other extractors
                # (pdfplumber fallback, LLM) have a shot instead of locking
                # this in as the extraction for this statement kind.
                log.info(
                    "extract.docling.row_count_too_low",
                    sha=doc.sha256[:12],
                    statement=kind.value,
                    rows=len(rows),
                )
                continue

            statement = ExtractedStatement(
                company_slug=doc.company_slug,
                source_sha256=doc.sha256,
                source_fiscal_year=doc.fiscal_year or 0,
                statement=kind,
                currency=currency,
                unit=unit_hint,
                rows=rows,
            )
            found[kind] = statement
            log.info(
                "extract.docling.found",
                sha=doc.sha256[:12],
                statement=kind.value,
                page=page_idx,
                rows=len(rows),
                unit=unit_hint.value,
            )
        return list(found.values())


def _find_candidate_pages(path: Path) -> list[tuple[StatementKind, int, Unit]]:
    """Text-scan the PDF and return (kind, page_idx, unit) per statement.

    A page is a candidate only when ALL of these hold:
    - One of the first `_HEADING_LINES` lines is *itself* a statement
      heading (short line where the heading text dominates — filters out
      mid-sentence mentions in ESG / EU Taxonomy prose).
    - It's not a table of contents (≥2 distinct statement kinds in the
      first 20 lines).
    - At least one `_TABLE_PAGE_HINTS` token appears near the top of the
      page (rules out footnote pages).
    - First qualifying page per kind wins.
    """
    hits: dict[StatementKind, tuple[int, Unit]] = {}
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            if len(hits) == 3:
                break
            try:
                text = page.extract_text() or ""
            except Exception:
                continue
            if not text:
                continue
            lines = text.splitlines()
            kind = _heading_line_match(lines[:_HEADING_LINES])
            if kind is None or kind in hits:
                continue
            toc_block = "\n".join(lines[:20])
            if _count_distinct_kinds(toc_block) >= 2:
                continue
            lower = text[:3000].lower()
            if not any(h in lower for h in _TABLE_PAGE_HINTS):
                continue
            unit = detect_unit(text[:2000])
            hits[kind] = (i, unit)
    return [(k, v[0], v[1]) for k, v in hits.items()]


def _heading_line_match(lines: list[str]) -> StatementKind | None:
    """Return the statement kind if one of these lines IS a statement
    heading — a short line whose content is (approximately) just the
    statement name, not a sentence that happens to mention it.
    """
    for raw in lines:
        line = " ".join(raw.split())
        if not line:
            continue
        # Heading lines are usually short. 120 chars is generous.
        if len(line) > 120:
            continue
        kind = match_statement(line)
        if kind is None:
            continue
        # The heading should dominate the line. Accept either:
        # (a) leftover is a short section-number-like token ("4.1.1", "4"), or
        # (b) leftover is empty or very short (≤ 10 chars).
        # Filters sentences like "...as reported in our Consolidated
        # statement of profit or loss associated..."
        from pipeline.adapters.extractor.common import _STATEMENT_PATTERNS, _normalize_ws

        norm = _normalize_ws(line)
        for pat in _STATEMENT_PATTERNS[kind]:
            if pat in norm:
                leftover = norm.replace(pat, "", 1).strip()
                if _leftover_is_heading_context(leftover):
                    return kind
                break
    return None


def _leftover_is_heading_context(leftover: str) -> bool:
    """True if `leftover` looks like section numbering / punctuation /
    a trivial prefix — not natural-language context around the heading.
    """
    if len(leftover) <= 10:
        return True
    # Strip digits, dots, and whitespace — what remains should be empty.
    scrubbed = "".join(c for c in leftover if c.isalpha())
    return len(scrubbed) == 0


def _group_tables_by_page(document: Any) -> dict[int, list[Any]]:
    """Map Docling page_no (1-indexed) → list of TableItem on that page."""
    out: dict[int, list[Any]] = {}
    for tbl in getattr(document, "tables", []) or []:
        prov = getattr(tbl, "prov", None) or []
        if not prov:
            continue
        page_no = getattr(prov[0], "page_no", None)
        if page_no is None:
            continue
        out.setdefault(page_no, []).append(tbl)
    return out


def _pick_best_table(
    tables: list[Any],
    document: Any,
    *,
    target_page: int,
) -> Any | None:
    """Pick the most statement-looking table — year headers + numeric body.

    Tables on `target_page` are strongly preferred over overflow tables on
    neighbouring pages. Non-period columns heavily penalise the score —
    that rules out Statements of Changes in Equity (many equity-component
    columns, few year columns) even when they're bigger than the target
    statement.
    """
    best = None
    best_score = -1.0
    for tbl in tables:
        try:
            df = tbl.export_to_dataframe(document)
        except Exception:
            continue
        if df.empty or df.shape[0] < 3 or df.shape[1] < 2:
            continue

        headers = [str(c) for c in df.columns.tolist()]
        header_years = sum(1 for y in parse_year_headers(headers) if y is not None)
        body_years = 0
        if header_years == 0 and len(df) > 0:
            first_row = [str(c) for c in df.iloc[0].tolist()]
            body_years = sum(1 for y in parse_year_headers(first_row) if y is not None)
        years_found = max(header_years, body_years)
        if years_found < 1:
            continue

        total_cols = df.shape[1]
        year_ratio = years_found / total_cols  # 1.0 = every non-label column is a year

        numeric_cells = 0
        for _, row in df.iterrows():
            for cell in row.tolist():
                s = str(cell) if cell is not None else ""
                if any(ch.isdigit() for ch in s):
                    numeric_cells += 1

        prov = getattr(tbl, "prov", None) or []
        page_no = getattr(prov[0], "page_no", None) if prov else None
        on_target_page = int(page_no == target_page) if page_no is not None else 0

        # Heavy bonus for being on the heading page; heavy bonus for a high
        # year-column ratio (keeps us on the target statement, not SCE).
        score = (
            on_target_page * 1000
            + year_ratio * 500
            + years_found * 10
            + numeric_cells
            + df.shape[0]
        )
        if score > best_score:
            best_score = score
            best = df
    return best


def _rows_from_dataframe(df: Any, *, page_idx: int) -> list[RawLineItem]:
    headers = [str(c) for c in df.columns.tolist()]
    years = parse_year_headers(headers)

    body_start = 0
    if sum(1 for y in years if y is not None) == 0 and len(df) > 0:
        first_row = [str(c) for c in df.iloc[0].tolist()]
        row_years = parse_year_headers(first_row)
        if sum(1 for y in row_years if y is not None) >= 1:
            years = row_years
            body_start = 1

    rows: list[RawLineItem] = []
    for idx in range(body_start, len(df)):
        raw_cells = df.iloc[idx].tolist()
        cells = ["" if c is None else str(c) for c in raw_cells]
        if not cells:
            continue
        label_idx = _find_label_idx(cells)
        if label_idx is None:
            continue
        # Preserve leading whitespace — hierarchy signal for Step 3.
        label_raw = cells[label_idx]
        label = label_raw.rstrip()
        if not label.strip():
            continue

        values_by_period: dict[str, Decimal | None] = {}
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
                source_page=page_idx,
                source_table_index=idx,
            )
        )
    return rows


def _find_label_idx(cells: list[str]) -> int | None:
    """First cell with letters and not parseable as a plain number."""
    for i, c in enumerate(cells):
        s = c.strip()
        if not s:
            continue
        has_letter = any(ch.isalpha() for ch in s)
        if has_letter and parse_number(s) is None:
            return i
    return None
