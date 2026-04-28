"""Verifier agent — post-extraction critique pass.

After Docling / pdfplumber / VLM produce an `ExtractedStatement`, the
verifier asks an LLM:

  - Are the row labels consistent with the claimed statement kind?
    (income statements have revenue/expense lines, NOT 'Total assets'.)
  - Is the year assignment correct? (Period_end columns shouldn't drift.)
  - Are values plausible at the unit scale?
    (A row labeled 'Revenue' shouldn't have value 0.01.)
  - Is anything obviously wrong (truncated rows, missing totals)?

Verdict drives a retry signal: when extraction is flagged bad, the
dispatcher escalates to a different extractor (Docling → VLM, or VLM
with a tighter prompt). This is the "Critique-Judge" pattern in the
hierarchical multi-agent literature.

Currently the verifier returns a verdict; the caller decides what to
do with it. We don't auto-retry yet — that's a follow-up — but the
verdict is logged and a `is_suspicious=True` extraction can be dropped
from the pipeline at the dispatcher level.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, Field

from pipeline.adapters.llm import LlmClient
from pipeline.domain.models import ExtractedStatement
from pipeline.domain.types import StatementKind

log = structlog.get_logger()


class _Issue(BaseModel):
    severity: str = Field(description="One of: 'critical', 'warning', 'info'.")
    field: str = Field(description="What's flagged: 'kind', 'rows', 'values', 'years', 'unit'.")
    detail: str = Field(description="One sentence describing the problem.")


class Verdict(BaseModel):
    """Result of a single verification pass over an extraction."""

    is_correct_kind: bool = Field(
        description="True iff the rows are consistent with the claimed statement kind."
    )
    is_complete: bool = Field(
        description="True iff the table looks complete (key totals present, no obvious truncation)."
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="0..1 — verifier's overall confidence in the extraction."
    )
    issues: list[_Issue] = Field(default_factory=list)
    summary: str = Field(default="", description="One-sentence judgement.")


_KIND_HINT = {
    StatementKind.INCOME: (
        "An INCOME statement has revenue at top, expense lines, "
        "operating profit, finance income/expense, tax, net income. "
        "Anchor labels: 'Revenue', 'Cost of revenue', 'Gross profit', "
        "'Operating profit', 'Net income', 'Earnings per share'."
    ),
    StatementKind.BALANCE: (
        "A BALANCE sheet has assets (current + non-current), liabilities, "
        "and equity. Anchor labels: 'Cash and equivalents', 'Trade "
        "receivables', 'Property, plant and equipment', 'Total assets', "
        "'Total liabilities', 'Total equity'."
    ),
    StatementKind.CASHFLOW: (
        "A CASH FLOW statement has three sections: operating, investing, "
        "financing activities. Anchor labels: 'Net cash from operating "
        "activities', 'Capital expenditure', 'Dividends paid', "
        "'Net change in cash'."
    ),
}


_SYSTEM = (
    "You are a financial-statement quality auditor. The user gives you an "
    "extracted statement (claimed kind + rows). Decide whether it's clean "
    "data the pipeline can publish, or whether the parser grabbed the wrong "
    "table / produced garbage.\n\n"
    "Severity rubric:\n"
    "- `critical`: the rows clearly don't match the claimed kind (e.g. "
    "balance-sheet rows tagged as income), or values are obviously wrong "
    "(all zeros, all the same number). Caller will drop the extraction.\n"
    "- `warning`: minor issues — one suspicious row, missing a sub-total, "
    "comparative period off by a year. Caller may keep but flag.\n"
    "- `info`: cosmetic — labels not perfectly verbatim, formatting noise.\n\n"
    "Only flag what's clearly wrong. Don't second-guess unusual but plausible "
    "data (a small-cap might genuinely have $5M of revenue)."
)


def verify_extraction(
    statement: ExtractedStatement,
    *,
    client: LlmClient,
    model: str,
) -> Verdict:
    """Run the verifier over a single extracted statement."""
    rows_block = "\n".join(
        f"{i:>3}. {r.raw_label!r}  values={dict(r.values_by_period_end)}"
        for i, r in enumerate(statement.rows[:60])
    )
    user = (
        f"company_slug: {statement.company_slug}\n"
        f"claimed_kind: {statement.statement.value}\n"
        f"unit: {statement.unit.value}\n"
        f"currency: {statement.currency.value}\n"
        f"source_fiscal_year: {statement.source_fiscal_year}\n\n"
        f"What an authentic {statement.statement.value} looks like:\n"
        f"{_KIND_HINT[statement.statement]}\n\n"
        f"Extracted rows ({len(statement.rows)}):\n{rows_block}"
    )
    return client.parse(
        model=model,
        system=_SYSTEM,
        user=user,
        schema=Verdict,
    )


def is_publishable(verdict: Verdict) -> bool:
    """Apply the severity rubric — drop extractions with critical issues
    or where the verifier can't confirm the kind."""
    if not verdict.is_correct_kind:
        return False
    if any(i.severity == "critical" for i in verdict.issues):
        return False
    return verdict.confidence >= 0.5
