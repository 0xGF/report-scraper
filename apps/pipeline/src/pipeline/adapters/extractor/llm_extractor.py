"""LLM-assisted extractor — the fallback when rules can't find a statement.

Strategy:
1. Scan every page of the PDF, collecting pages whose text mentions any
   statement-like phrase (permissive, fuzzy match).
2. For each candidate page, pull the raw text + any tables pdfplumber
   found on that page.
3. Send the page text (plus raw table cells) to the LLM with a structured
   output schema; it returns the statement kind + rows as a typed object.
4. Cache every call on disk (SHA of page text) so re-runs are free.

This is the robust path for old/unusual PDFs where pdfplumber's ruling
detection fails but the data is clearly present in the text stream.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pdfplumber
import structlog
from pydantic import BaseModel, Field

from pipeline.adapters.extractor.common import detect_unit
from pipeline.adapters.llm import LlmClient
from pipeline.domain.models import ClassifiedPdf, ExtractedStatement, RawLineItem
from pipeline.domain.types import Currency, StatementKind, Unit

log = structlog.get_logger()

# Heading-adjacent phrases we use to pre-filter pages before paying for LLM calls.
_CANDIDATE_PHRASES = [
    "income statement",
    "statement of operations",
    "statement of profit",
    "profit and loss",
    "statement of comprehensive income",
    "balance sheet",
    "statement of financial position",
    "statement of cash flows",
    "cash flow statement",
]

# Per-document caps — keep LLM spend reasonable.
_MAX_LLM_CALLS_PER_DOC = 6
_MAX_PAGES_TO_TRY = 12


class _LlmRowValue(BaseModel):
    period_end: str = Field(
        description="ISO date YYYY-12-31 for the period this value reports on.",
    )
    value: str | None = Field(
        default=None,
        description=(
            "Raw numeric value as it appears on the page (e.g. '1,234.5' or '(567)'). "
            "Null when the cell is explicitly empty (dash/em-dash)."
        ),
    )


class _LlmRow(BaseModel):
    raw_label: str = Field(description="The line-item label exactly as printed.")
    values: list[_LlmRowValue]


class _LlmExtractResult(BaseModel):
    is_statement: bool = Field(
        description=(
            "True if this page contains a primary consolidated statement "
            "matching `requested_kind`. False for table-of-contents pages, "
            "notes, segment reports, or pages that don't contain a full "
            "statement."
        ),
    )
    statement_kind: StatementKind | None = None
    unit: Unit = Field(
        default=Unit.UNITS,
        description="Reporting unit detected from page context (millions/thousands/etc.)",
    )
    rows: list[_LlmRow] = Field(default_factory=list)
    notes: str = Field(default="", description="Any extraction caveats.")


_SYSTEM = (
    "You extract the big-three financial statements (Income, Balance, Cash Flow) from "
    "corporate annual reports. Given a single page's raw text, you decide whether it "
    "contains the requested PRIMARY consolidated statement, and if so, extract every "
    "row with its label and every period's value. Rules:\n"
    "- Return `is_statement=false` for tables of contents, segment breakdowns, notes, "
    "or any partial statement. Only a primary consolidated statement counts.\n"
    "- Preserve labels exactly — do not translate or paraphrase.\n"
    "- For each row, emit one `values` entry per period column with an ISO date "
    "(YYYY-12-31) for the `period_end`.\n"
    "- Use `value=null` for empty/dash cells. Keep the raw string form for values "
    "('1,234.5', '(567)') — do not convert.\n"
    "- Detect the reporting unit from page text (millions/thousands/etc.).\n"
)


class LlmExtractor:
    def __init__(self, client: LlmClient, model: str) -> None:
        self._client = client
        self._model = model

    def extract(
        self,
        doc: ClassifiedPdf,
        *,
        currency: Currency = Currency.EUR,
        wanted: set[StatementKind] | None = None,
    ) -> list[ExtractedStatement]:
        if not doc.fiscal_year:
            return []
        path = Path(doc.local_path)
        if not path.exists():
            return []
        suffix = path.suffix.lower()
        if suffix not in {".pdf", ".htm", ".html"}:
            return []

        wanted = wanted or {StatementKind.INCOME, StatementKind.BALANCE, StatementKind.CASHFLOW}
        found: dict[StatementKind, ExtractedStatement] = {}
        llm_calls = 0

        pages = _stream_pages(path)
        candidates = _score_candidate_pages(pages, wanted)

        for kind in list(wanted):
            page_candidates = candidates.get(kind, [])[:_MAX_PAGES_TO_TRY]
            for page_idx, page_text in page_candidates:
                if llm_calls >= _MAX_LLM_CALLS_PER_DOC:
                    log.info("extract.llm.budget_exhausted", sha=doc.sha256[:12])
                    break
                llm_calls += 1
                try:
                    resp = self._ask(kind, page_text)
                except Exception:
                    log.exception("extract.llm.call_failed", sha=doc.sha256[:12], page=page_idx)
                    continue
                if not resp.is_statement or resp.statement_kind != kind:
                    continue
                rows = _rows_from_llm(resp)
                if not rows:
                    continue
                unit_from_text = detect_unit(page_text[:2000])
                unit = resp.unit if resp.unit != Unit.UNITS else unit_from_text
                found[kind] = ExtractedStatement(
                    company_slug=doc.company_slug,
                    source_sha256=doc.sha256,
                    source_fiscal_year=doc.fiscal_year or 0,
                    statement=kind,
                    currency=currency,
                    unit=unit,
                    rows=rows,
                )
                log.info(
                    "extract.llm.found",
                    sha=doc.sha256[:12],
                    statement=kind.value,
                    page=page_idx,
                    rows=len(rows),
                )
                break

        return list(found.values())

    def _ask(self, kind: StatementKind, page_text: str) -> _LlmExtractResult:
        user = (
            f"requested_kind: {kind.value}\n"
            f"page_text (truncated):\n-----\n{page_text[:12000]}\n-----"
        )
        return self._client.parse(
            model=self._model,
            system=_SYSTEM,
            user=user,
            schema=_LlmExtractResult,
        )


def _stream_pages(path: Path) -> list[str]:
    if path.suffix.lower() == ".pdf":
        with pdfplumber.open(path) as pdf:
            return [p.extract_text() or "" for p in pdf.pages]
    # HTML path — split by section markers roughly; good enough for LLM pre-filtering
    raw = path.read_text(encoding="utf-8", errors="replace")
    # strip tags crudely for candidate scoring
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text)
    # chunk into ~8KB "pages"
    chunk = 8000
    return [text[i : i + chunk] for i in range(0, len(text), chunk)]


def _score_candidate_pages(
    pages: list[str],
    wanted: set[StatementKind],
) -> dict[StatementKind, list[tuple[int, str]]]:
    out: dict[StatementKind, list[tuple[int, str]]] = {k: [] for k in wanted}
    for i, text in enumerate(pages):
        low = text.lower()
        stripped = low.replace(" ", "")
        for phrase in _CANDIDATE_PHRASES:
            if phrase in low or phrase.replace(" ", "") in stripped:
                # bucket by kind
                is_income = any(
                    k in phrase for k in ("income", "operations", "profit", "comprehensive")
                )
                is_balance = "balance" in phrase or "financial position" in phrase
                is_cashflow = "cash flow" in phrase
                if is_income and StatementKind.INCOME in wanted:
                    out[StatementKind.INCOME].append((i, text))
                elif is_balance and StatementKind.BALANCE in wanted:
                    out[StatementKind.BALANCE].append((i, text))
                elif is_cashflow and StatementKind.CASHFLOW in wanted:
                    out[StatementKind.CASHFLOW].append((i, text))
                break
    return out


def _rows_from_llm(resp: _LlmExtractResult) -> list[RawLineItem]:
    rows: list[RawLineItem] = []
    for i, r in enumerate(resp.rows):
        vals: dict[str, Decimal | None] = {}
        for v in r.values:
            parsed = _parse_value(v.value)
            vals[v.period_end] = parsed
        rows.append(
            RawLineItem(
                raw_label=r.raw_label,
                values_by_period_end=vals,
                source_page=0,
                source_table_index=i,
            )
        )
    return rows


def _parse_value(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    s = raw.strip()
    if not s or s in {"-", "—", "–", "n/a", "NA"}:  # noqa: RUF001 — EN DASH is intentional
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace(",", "").replace(" ", "").replace("\u00a0", "")
    try:
        d = Decimal(s)
    except Exception:
        return None
    return -d if neg else d
