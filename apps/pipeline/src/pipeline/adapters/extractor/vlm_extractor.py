"""Vision-LLM extractor — the AI-first path.

When Docling and pdfplumber both struggle on a layout (Heineken's pre-2019
income statement sitting next to a comprehensive-income table; L'Oréal's
pages with mixed-format figures), we render the candidate page as an image
and ask gpt-5 (vision) to read the statement directly.

This mirrors the production pattern documented in the multi-agent financial
PDF extraction literature: VLMs handle complex tabular layouts that defeat
heuristic table parsers (Docling, pdfplumber, Tabula). gpt-5 vision sees the
visual structure — a single income-statement column block, regardless of
adjacent tables — that text-based parsers miss.

Cost: ~$0.05 per page at gpt-5 list price. Used as a fallback gate, not a
default, so most companies still go through the cheaper Docling path.
"""

from __future__ import annotations

import base64
import io
from decimal import Decimal
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from pipeline.adapters.extractor.common import match_statement
from pipeline.adapters.llm import LlmClient
from pipeline.config import get_settings
from pipeline.domain.models import ClassifiedPdf, ExtractedStatement, RawLineItem
from pipeline.domain.types import Currency, StatementKind, Unit

log = structlog.get_logger()

# Pages > this index are unlikely to contain the primary financial statements
# and rastering more than necessary blows up token cost. Most issuers put the
# big three within the first 200 pages of an annual report; URDs stretch to
# 400+, so we go a bit higher to be safe.
_MAX_PAGES = 250
# Render quality. 144 DPI is the sweet spot — high enough that gpt-5 reads
# small footnote markers reliably, low enough to keep payloads under the
# vision-input token cap.
_RENDER_DPI = 144


class _VlmCell(BaseModel):
    """One numeric cell of a statement row."""

    period_end: str = Field(description="ISO date like '2024-12-31'.")
    value: str | None = Field(
        description=(
            "The number AS PRINTED. Keep parens for negatives, commas, "
            "decimal points. Null for dashes, n/a, blank cells."
        )
    )


class _VlmRow(BaseModel):
    label: str = Field(description="Line-item label exactly as printed.")
    cells: list[_VlmCell] = Field(default_factory=list)
    is_header: bool = Field(
        default=False,
        description=(
            "True if this row is a section header with no values (e.g. 'Operating expenses')."
        ),
    )
    is_total: bool = Field(
        default=False,
        description=(
            "True if this row is a subtotal or grand total (e.g. 'Total revenue', 'Net income')."
        ),
    )


class _VlmStatement(BaseModel):
    """Structured output schema for a single statement extraction."""

    is_correct_kind: bool = Field(
        description=(
            "True if the page actually contains the requested statement. "
            "If the page has, say, segment revenue or comprehensive income "
            "instead, set this to False and leave rows empty."
        )
    )
    actual_kind: StatementKind | None = Field(
        default=None,
        description="If is_correct_kind=False, what kind of statement is on the page (or null).",
    )
    unit: Unit = Field(
        description="Reporting unit detected from the table header / column subtitle.",
    )
    rows: list[_VlmRow] = Field(default_factory=list)
    notes: str = Field(default="", description="Quality flags or caveats.")


_SYSTEM_TEMPLATE = (
    "You are a financial-statement transcriber. The user shows you ONE PAGE "
    "of an annual report and asks for a specific statement: {kind_label}. "
    "Read the table off the image and return rows in display order.\n\n"
    "Critical rules:\n"
    "1. **Verify the page actually contains the requested statement.** "
    "Set `is_correct_kind=false` if the page shows comprehensive income, "
    "segment revenue, condensed summary, prior-year-only data, or any "
    "non-{kind_label} table. Don't try to extract from the wrong table.\n"
    "2. **Preserve labels VERBATIM** — same casing, punctuation, footnote "
    "markers. Don't paraphrase or translate.\n"
    "3. **Preserve numeric values AS PRINTED** — keep `(1,234)` as `(1,234)`, "
    "keep `—` as null, keep thousand-separators, keep decimal points.\n"
    "4. **One row per visual line.** A header row with sub-items below has "
    "`is_header=true` and empty cells.\n"
    "5. **Period ends are inferred from column headers.** A column titled "
    "'2024' or 'Year ended December 31, 2024' has period_end '2024-12-31' "
    "(use the company's reporting cycle — most are calendar-year).\n"
    "6. Do NOT invent rows or values not visible on the page."
)

_KIND_LABELS = {
    StatementKind.INCOME: "consolidated income statement (revenue down to net income)",
    StatementKind.BALANCE: "consolidated balance sheet (assets / liabilities / equity)",
    StatementKind.CASHFLOW: (
        "consolidated statement of cash flows (operating / investing / financing)"
    ),
}


def _render_pages_to_pngs(
    path: Path, max_pages: int = _MAX_PAGES, dpi: int = _RENDER_DPI
) -> list[bytes]:
    """Rasterize PDF pages to PNG bytes using pypdfium2 (no system deps)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    n = min(len(pdf), max_pages)
    out: list[bytes] = []
    scale = dpi / 72.0  # PDF user-units are 72 DPI
    for i in range(n):
        page = pdf[i]
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG", optimize=True)
        out.append(buf.getvalue())
    return out


def _png_to_data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _find_candidate_pages(
    pdf_path: Path,
    *,
    wanted: set[StatementKind],
) -> dict[StatementKind, list[int]]:
    """Use pdfplumber's text extraction to find pages whose heading matches each
    desired statement. We use the same `match_statement` heuristic as the
    Docling and pdfplumber extractors — VLM only kicks in once we've narrowed
    to a handful of candidate pages, so we don't waste tokens on the whole PDF.
    """
    try:
        import pdfplumber
    except Exception:
        return {kind: list(range(min(_MAX_PAGES, 30))) for kind in wanted}

    out: dict[StatementKind, list[int]] = {kind: [] for kind in wanted}
    with pdfplumber.open(str(pdf_path)) as pdf:
        n = min(len(pdf.pages), _MAX_PAGES)
        for i in range(n):
            try:
                text = pdf.pages[i].extract_text() or ""
            except Exception:
                continue
            lines = text.splitlines()
            header_block = "\n".join(lines[:8])
            kind = match_statement(header_block)
            if kind in wanted:
                out[kind].append(i)
    return out


class VlmExtractor:
    """Vision-LLM extractor. Pass a `ClassifiedPdf` and the requested
    statement kinds; for each kind we find the candidate page, render it
    to an image, and ask gpt-5 (vision) to dictate the rows.
    """

    def __init__(self, client: LlmClient, model: str) -> None:
        self._client = client
        self._model = model

    def extract(
        self,
        doc: ClassifiedPdf,
        *,
        currency: Currency,
        wanted: set[StatementKind],
    ) -> list[ExtractedStatement]:
        path = Path(doc.local_path)
        if not path.exists() or not doc.fiscal_year:
            return []

        candidates_by_kind = _find_candidate_pages(path, wanted=wanted)

        # Render only the pages we'll actually send. Cap per kind to avoid
        # blowing up token cost on PDFs with many candidate pages.
        page_ix_set: set[int] = set()
        for ix_list in candidates_by_kind.values():
            page_ix_set.update(ix_list[:3])
        if not page_ix_set:
            return []

        try:
            pngs = _render_pages_to_pngs(path)
        except Exception:
            log.exception("vlm.render_failed", sha=doc.sha256[:12])
            return []

        out: list[ExtractedStatement] = []
        for kind in wanted:
            for page_ix in candidates_by_kind.get(kind, [])[:3]:
                if page_ix >= len(pngs):
                    continue
                statement = self._extract_one(
                    doc=doc,
                    currency=currency,
                    kind=kind,
                    page_ix=page_ix,
                    png=pngs[page_ix],
                )
                if statement is not None:
                    out.append(statement)
                    break  # found this kind — move on
        return out

    def _extract_one(
        self,
        *,
        doc: ClassifiedPdf,
        currency: Currency,
        kind: StatementKind,
        page_ix: int,
        png: bytes,
    ) -> ExtractedStatement | None:
        kind_label = _KIND_LABELS[kind]
        system = _SYSTEM_TEMPLATE.format(kind_label=kind_label)
        user_text = (
            f"Statement requested: {kind_label}\n"
            f"Source: page {page_ix + 1} of an annual report by "
            f"{doc.company_slug} (fiscal year {doc.fiscal_year}).\n"
            "Return a `_VlmStatement`."
        )

        try:
            result = self._client.parse_with_image(
                model=self._model,
                system=system,
                user=user_text,
                image_data_url=_png_to_data_url(png),
                schema=_VlmStatement,
            )
        except Exception:
            log.exception("vlm.call_failed", sha=doc.sha256[:12], kind=kind.value)
            return None

        if not result.is_correct_kind:
            log.info(
                "vlm.skipped_wrong_kind",
                sha=doc.sha256[:12],
                page=page_ix,
                claimed=kind.value,
                actual=result.actual_kind.value if result.actual_kind else None,
            )
            return None
        if not result.rows:
            return None

        rows = _rows_to_raw(result.rows, doc.fiscal_year or 0)
        if not rows:
            return None

        log.info(
            "vlm.extracted",
            sha=doc.sha256[:12],
            kind=kind.value,
            page=page_ix,
            rows=len(rows),
        )

        return ExtractedStatement(
            company_slug=doc.company_slug,
            source_sha256=doc.sha256,
            source_fiscal_year=doc.fiscal_year or 0,
            statement=kind,
            currency=currency,
            unit=result.unit,
            rows=rows,
        )


def _rows_to_raw(rows: list[_VlmRow], fiscal_year: int) -> list[RawLineItem]:
    out: list[RawLineItem] = []
    for i, r in enumerate(rows):
        values: dict[str, Decimal | None] = {}
        for c in r.cells:
            values[c.period_end] = _parse_number(c.value)
        out.append(
            RawLineItem(
                raw_label=r.label,
                values_by_period_end=values,
                source_page=0,
                source_table_index=i,
            )
        )
    return out


def _parse_number(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in {"-", "—", "–", "n/a", "NA", "nm", "*"}:  # noqa: RUF001
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = s.replace(",", "").replace("\u00a0", "").replace("\u2009", "")
    s = s.replace("$", "").replace("€", "").replace("£", "").replace("¥", "")
    s = s.strip()
    try:
        d = Decimal(s)
        return -d if neg else d
    except Exception:
        return None


def vlm_is_available() -> bool:
    """True iff a working LLM client is available for the VLM path."""
    settings = get_settings()
    return bool(settings.openai_api_key)
