"""Pydantic models passed between pipeline stages."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from pipeline.domain.types import Currency, ReportKind, StatementKind, Unit
from report_scrape import IrSource


class Company(BaseModel):
    """A company we scrape — metadata + one or more declarative scrape sources."""

    model_config = ConfigDict(frozen=True)

    slug: str
    name: str
    ticker: str
    exchange: str
    reporting_currency: Currency
    ir_url: str
    sources: list[IrSource]
    # Optional rendering metadata. Populated by `report-scrape discover`; the
    # viewer falls back to defaults when these are missing.
    logo_url: str | None = None
    description: str | None = None
    sector: str | None = None
    headquarters: str | None = None
    website: str | None = None
    founded: int | None = None
    source_note: str | None = None
    # Market data inputs — Yahoo Finance ticker (may differ from primary listing
    # ticker) + most-recently-reported shares outstanding (millions). Used by
    # `report-scrape market` to compute current price * market cap.
    market_ticker: str | None = None
    shares_outstanding_m: float | None = None


class ClassifiedPdf(BaseModel):
    """A ScrapedPdf after classification — tagged with kind + fiscal year."""

    sha256: str
    source_url: str
    local_path: str
    company_slug: str
    kind: ReportKind
    fiscal_year: int | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    classified_by: str = Field(description="'rules' or 'llm'")
    link_text: str | None = None
    size_bytes: int = 0


class RawLineItem(BaseModel):
    """A row extracted from a PDF table, before normalization."""

    raw_label: str
    values_by_period_end: dict[str, Decimal | None]
    source_page: int
    source_table_index: int


class ExtractedStatement(BaseModel):
    """The raw, pre-normalization output of one statement from one report."""

    company_slug: str
    source_sha256: str
    source_fiscal_year: int
    statement: StatementKind
    currency: Currency
    unit: Unit
    period_months: int = 12
    rows: list[RawLineItem]


class LineItem(BaseModel):
    """A canonical line-item — a row in the consolidated taxonomy.

    Hierarchy and order are inherited from the *latest* report so the output
    table matches the company's most recent presentation.
    """

    model_config = ConfigDict(frozen=True)

    company_slug: str
    statement: StatementKind
    canonical_name: str
    depth: int = Field(ge=0)
    parent_name: str | None = None
    is_header: bool = False
    is_total: bool = False
    display_order: int


class FactValue(BaseModel):
    """One cell: (company, statement, line_item, period) → value.

    Every cell carries its provenance so the restatement query can pick the
    latest reported value per (line_item, period).
    """

    company_slug: str
    statement: StatementKind
    canonical_name: str
    period_end: date
    period_months: int
    value: Decimal | None
    currency: Currency
    unit: Unit
    source_sha256: str
    source_fiscal_year: int
    is_restated: bool = False
