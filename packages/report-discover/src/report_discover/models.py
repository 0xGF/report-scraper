"""Pydantic schemas returned by `discover` and `verify`."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DiscoveredSource(BaseModel):
    """A single scrape config — typically the IR annual-report archive."""

    name: str = Field(description="Stable identifier, e.g. 'heineken:annual-reports'.")
    entry_urls: list[str] = Field(
        default_factory=list,
        description="Pages crawled for PDF links — typically the IR annual-report archive.",
    )
    direct_urls: list[str] = Field(
        default_factory=list,
        description=(
            "Direct PDF/HTML URLs when discovery is impractical (e.g. Akamai-protected sites)."
        ),
    )
    link_filter: str = Field(
        default=r".*\.pdf(\?|$)",
        description="Regex applied to resolved URLs.",
    )
    follow_links: str | None = Field(
        default=None,
        description="Optional regex — non-PDF links matching this are crawled one level.",
    )
    label: str | None = Field(
        default=None, description="Free-form tag propagated to scraped records."
    )


class DiscoveredCompany(BaseModel):
    """The full discovery result — everything needed to spin up a scrape pipeline."""

    found: bool = Field(
        description=(
            "True if the model is confident the answer is correct. False = caller should fall back."
        )
    )
    slug: str = Field(description="URL-safe identifier, lowercase, dashes only.")
    name: str = Field(description="Canonical legal name, e.g. 'Heineken N.V.'")
    ticker: str = Field(description="Primary stock ticker.")
    exchange: str = Field(description="Exchange code, e.g. 'AEX', 'XETRA', 'NYSE'.")
    reporting_currency: str = Field(description="ISO 4217, e.g. 'EUR', 'USD'.")
    ir_url: str = Field(description="The IR landing page or annual-report archive URL.")
    website: str | None = None
    logo_url: str | None = Field(
        default=None,
        description="A direct image URL for the company's logo (SVG/PNG preferred).",
    )
    description: str | None = Field(default=None, description="One-sentence business description.")
    sector: str | None = Field(
        default=None, description="GICS-style sector, e.g. 'Consumer Staples'."
    )
    headquarters: str | None = Field(default=None, description="City, country.")
    founded: int | None = Field(default=None, description="Year the company was founded.")
    # Market-data inputs — populated alongside core metadata so a fresh
    # discover → market run can render live price + market cap without any
    # manual edits to the YAML.
    market_ticker: str | None = Field(
        default=None,
        description=(
            "Yahoo Finance ticker symbol used by `report-scrape market` (e.g. 'SAP.DE', "
            "'ASML.AS', 'DSY.PA'). Use the primary listing's YF symbol; cross-listed "
            "stocks should pick the home-exchange ticker so the currency lines up."
        ),
    )
    shares_outstanding_m: float | None = Field(
        default=None,
        description=(
            "Most-recently-reported shares outstanding, in MILLIONS. Yahoo's free "
            "chart endpoint does not return market cap, so the pipeline computes "
            "market cap as price * shares. Pull from the latest annual report."
        ),
    )
    sources: list[DiscoveredSource] = Field(
        description="At least one scrape config to drive an IR scraper."
    )
    notes: str = Field(default="", description="Caveats — e.g. JS-rendered IR site.")
