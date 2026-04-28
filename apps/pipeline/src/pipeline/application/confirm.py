"""`report-scrape confirm <company>` — AI verifier between parse and normalize.

Takes raw `ExtractedStatement`s out of `parse` and runs each one through
the LLM to:
  1. Validate that the rows actually match the claimed statement kind
     (catches "balance" tables that are really revenue breakdowns, etc.).
  2. Drop noise rows (zero-width spaces, footnote markers, junk).
  3. Assign hierarchy — depth, parent_label, is_header, is_total —
     directly from the row content, leveraging the LLM's understanding of
     financial statement structure rather than indentation heuristics.

Output: `data/raw/{company}.confirmed.json` with the same shape as
extracted.json but with cleaned rows + hierarchy attached. If no LLM key
is available, the stage runs a heuristic confirmer that just trims the
junk rows and uses the existing indentation-based hierarchy.

The downstream `normalize` step reads `.confirmed.json` when present and
falls back to `.extracted.json` otherwise, so the pipeline degrades
gracefully when the LLM can't run.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

from pipeline.adapters.llm import LlmClient
from pipeline.config import get_settings
from pipeline.domain.companies import get_company
from pipeline.domain.models import ExtractedStatement
from pipeline.domain.types import StatementKind

log = structlog.get_logger()


def _extracted_path(raw_dir: Path, slug: str) -> Path:
    return raw_dir / f"{slug}.extracted.json"


def _confirmed_path(raw_dir: Path, slug: str) -> Path:
    return raw_dir / f"{slug}.confirmed.json"


# ---- LLM schema -----------------------------------------------------------


class _ConfirmedCell(BaseModel):
    """One cell of a confirmed row. List form (not dict) because OpenAI's
    strict structured-output mode forbids open-key objects.
    """

    period_end: str = Field(description="Period-end ISO date, e.g. '2024-12-31'.")
    value: str | None = Field(
        description=("Raw numeric value as printed: '1,234.5' or '(567)'. Null for empty cells."),
    )


class _ConfirmedRow(BaseModel):
    label: str = Field(description="Cleaned line-item label, exactly as it should appear.")
    cells: list[_ConfirmedCell] = Field(
        description="One entry per period column. Empty list for header rows."
    )
    depth: int = Field(ge=0, description="Indent depth — 0 for top-level rows.")
    parent_label: str | None = Field(
        default=None, description="Label of the section header this row sits under, or null."
    )
    is_header: bool = Field(description="True if this row is a section header (no values).")
    is_total: bool = Field(description="True if this row is a subtotal or grand total.")


class _ConfirmResult(BaseModel):
    correct_kind: bool = Field(
        description="True if the supplied rows match the claimed statement kind."
    )
    actual_kind: StatementKind | None = Field(
        default=None,
        description="If correct_kind is False, the kind these rows actually belong to.",
    )
    rows: list[_ConfirmedRow] = Field(
        default_factory=list,
        description=(
            "Cleaned rows in display order. DROP rows that aren't part of the statement "
            "(zero-width chars only, malformed labels, page numbers, MD&A fragments)."
        ),
    )
    notes: str = Field(default="", description="Any quality flags or caveats.")


_SYSTEM = (
    "You verify and organize raw financial-statement extractions. Given a "
    "list of rows extracted from a single annual report's IS / BS / CF "
    "statement, you:\n"
    "1. Confirm whether the rows actually match the claimed statement kind. "
    "Set `correct_kind=false` if they don't (e.g. revenue rows tagged as "
    "balance sheet) and set `actual_kind` to the right enum.\n"
    "2. Output a cleaned, ordered row list. DROP rows that aren't really "
    "part of the statement (zero-width chars, page numbers, dangling "
    "footnote artifacts).\n"
    "3. For each row, assign hierarchy — depth (integer, 0 = top level), "
    "parent_label (if it's a child of a section header), is_header (true "
    "for label-only rows that group children), is_total (true for "
    "subtotals / grand totals like 'Total revenue', 'NET INCOME', "
    "'Operating cash flow').\n"
    "Preserve label text and values exactly as printed — do not translate, "
    "round, or paraphrase."
)


def _ask_llm(
    client: LlmClient,
    *,
    model: str,
    statement: ExtractedStatement,
) -> _ConfirmResult:
    rows_summary = "\n".join(
        f"- {r.raw_label!r} | {dict(r.values_by_period_end)}" for r in statement.rows
    )
    user = (
        f"claimed_kind: {statement.statement.value}\n"
        f"unit: {statement.unit.value}\n"
        f"currency: {statement.currency.value}\n"
        f"source_fiscal_year: {statement.source_fiscal_year}\n"
        f"rows ({len(statement.rows)}):\n{rows_summary}"
    )
    return client.parse(model=model, system=_SYSTEM, user=user, schema=_ConfirmResult)


def _heuristic_confirm(statement: ExtractedStatement) -> dict[str, Any]:
    """No-LLM path — drop obvious junk, attach indent-based hierarchy."""
    from pipeline.application.normalize import _clean_label, _infer_hierarchy

    hierarchy = _infer_hierarchy(statement.rows)
    cleaned: list[dict[str, Any]] = []
    for r, h in zip(statement.rows, hierarchy, strict=True):
        label = _clean_label(r.raw_label)
        if not label:
            continue
        cleaned.append(
            {
                "label": label,
                "values_by_period_end": {
                    k: (str(v) if isinstance(v, Decimal) else v)
                    for k, v in r.values_by_period_end.items()
                },
                "depth": h.depth,
                "parent_label": h.parent_name,
                "is_header": h.is_header,
                "is_total": h.is_total,
            }
        )
    return {
        "correct_kind": True,
        "actual_kind": None,
        "rows": cleaned,
        "confirmed_by": "heuristic",
    }


def _llm_confirm(
    client: LlmClient,
    model: str,
    statement: ExtractedStatement,
) -> dict[str, Any] | None:
    try:
        result = _ask_llm(client, model=model, statement=statement)
    except Exception:
        log.exception(
            "confirm.llm_failed",
            sha=statement.source_sha256[:12],
            statement=statement.statement.value,
        )
        return None

    rows: list[dict[str, Any]] = []
    for r in result.rows:
        rows.append(
            {
                "label": r.label,
                "values_by_period_end": {c.period_end: c.value for c in r.cells},
                "depth": r.depth,
                "parent_label": r.parent_label,
                "is_header": r.is_header,
                "is_total": r.is_total,
            }
        )
    return {
        "correct_kind": result.correct_kind,
        "actual_kind": result.actual_kind.value if result.actual_kind else None,
        "rows": rows,
        "notes": result.notes,
        "confirmed_by": "llm",
    }


def run(company_slug: str) -> None:
    settings = get_settings()
    company = get_company(company_slug)
    raw_dir = settings.report_scrape_raw_dir

    epath = _extracted_path(raw_dir, company_slug)
    if not epath.exists():
        raise FileNotFoundError(
            f"No extracted file at {epath}. Run `report-scrape parse {company_slug}` first."
        )
    statements = [ExtractedStatement.model_validate(x) for x in json.loads(epath.read_text())]
    log.info("confirm.start", company=company.name, statements=len(statements))

    client: LlmClient | None = None
    if settings.openai_api_key:
        candidate = LlmClient(
            api_key=settings.openai_api_key,
            cache_dir=settings.report_scrape_cache_dir / "llm",
        )
        if candidate.ping():
            client = candidate
        else:
            log.warning(
                "confirm.no_llm",
                reason="OPENAI_API_KEY failed /v1/models ping; using heuristics",
            )

    confirmed: list[dict[str, Any]] = []
    llm_calls = 0
    drops = 0
    kind_corrections = 0

    for s in statements:
        result: dict[str, Any] | None = None
        if client is not None:
            result = _llm_confirm(client, settings.openai_normalizer_model, s)
            if result is not None:
                llm_calls += 1
        if result is None:
            result = _heuristic_confirm(s)

        # Statement-level metadata + the confirmed payload
        original_rows = len(s.rows)
        kept_rows = len(result["rows"])
        drops += max(0, original_rows - kept_rows)
        if not result.get("correct_kind", True):
            kind_corrections += 1

        confirmed.append(
            {
                "company_slug": s.company_slug,
                "source_sha256": s.source_sha256,
                "source_fiscal_year": s.source_fiscal_year,
                # Trust the parser's table classification — the LLM auditor's
                # `actual_kind` is recorded for diagnostics but no longer used
                # to re-route tables. We saw the auditor wrongly relabel real
                # balance-sheet content as income for some Heineken reports;
                # the parser's heading-match is more reliable than a row-only
                # vibe check.
                "statement": s.statement.value,
                "claimed_statement": s.statement.value,
                "currency": s.currency.value,
                "unit": s.unit.value,
                "period_months": s.period_months,
                "correct_kind": result.get("correct_kind", True),
                "confirmed_by": result.get("confirmed_by", "heuristic"),
                "notes": result.get("notes", ""),
                "rows": result["rows"],
            }
        )
        log.info(
            "confirm.statement_done",
            sha=s.source_sha256[:12],
            statement=s.statement.value,
            actual=result.get("actual_kind"),
            rows_in=original_rows,
            rows_out=kept_rows,
            confirmed_by=result.get("confirmed_by"),
        )

    _confirmed_path(raw_dir, company_slug).write_text(
        json.dumps(confirmed, indent=2, sort_keys=True, default=str)
    )
    log.info(
        "confirm.done",
        company=company.name,
        statements=len(confirmed),
        llm_calls=llm_calls,
        rows_dropped=drops,
        kind_corrections=kind_corrections,
    )
