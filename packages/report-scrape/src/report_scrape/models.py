"""Input/output contracts for report-scrape.

The package exposes two templates so consumers never hand-roll their own
shape:
- `IrSource` — declarative config for *one* scrape target
- `ScrapedManifest` — the structured result of one run

`ScrapedPdf` and `PdfLink` are the element-level records used by both.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PdfLink(BaseModel):
    """A PDF link discovered on a page — not yet downloaded."""

    model_config = ConfigDict(frozen=True)

    url: str
    page_url: str
    link_text: str | None = None


class ScrapedDocument(BaseModel):
    """A downloaded document, identified by SHA-256 of its bytes.

    The scraper is format-agnostic — a `ScrapedDocument` can be a PDF, an
    SEC-filed HTML 20-F, a plain-text report, etc. Downstream pipelines
    dispatch on `content_type` or file extension.
    """

    sha256: str
    source_url: str
    page_url: str
    link_text: str | None
    local_path: str
    size_bytes: int
    content_type: str
    discovered_at: str
    source_name: str | None = Field(
        default=None,
        description=(
            "The `IrSource.name` that produced this record (propagated for multi-source runs)."
        ),
    )
    label: str | None = Field(
        default=None,
        description="Arbitrary tag from `IrSource.label` — e.g. 'sap:reports-archive'.",
    )


# Backwards-compat alias for anything still importing ScrapedPdf
ScrapedPdf = ScrapedDocument


class IrSource(BaseModel):
    """Declarative config for a single scrape target.

    One `IrSource` = one logical source. A company may correspond to multiple
    sources (e.g. separate pages for annual + quarterly archives).

    Notes on fields:
    - `entry_urls`: pages to fetch first. Every PDF linked from these pages
      is considered a candidate for download.
    - `link_filter`: regex applied to the *resolved* URL. Default matches any
      `.pdf` URL. Narrow it to (say) `r"annual-report.*\\.pdf$"` to be stricter.
    - `follow_links`: optional regex — when set, *non-PDF* links matching this
      pattern are also enqueued and crawled one level deep. Useful when the
      landing page links to year-specific sub-pages.
    - `max_follow_depth`: how many levels of `follow_links` hops to allow.
    - `label`: propagated onto every `ScrapedPdf.label` so downstream pipelines
      can group by source without having to re-derive it.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, description="Stable identifier for this source")
    entry_urls: list[str] = Field(
        default_factory=list,
        description="Pages crawled for PDF links; may be empty if using `direct_urls` only.",
    )
    direct_urls: list[str] = Field(
        default_factory=list,
        description=(
            "PDF URLs to download directly, no discovery. Useful when the IR page is "
            "JS-rendered or the URLs are known in advance."
        ),
    )
    link_filter: str = Field(default=r".*\.pdf(\?|$)", description="Regex applied to resolved URLs")
    follow_links: str | None = Field(
        default=None,
        description="Regex — non-PDF links matching this are crawled one more level",
    )
    max_follow_depth: int = Field(default=1, ge=0, le=3)
    label: str | None = None
    strategies_override: list[str] | None = Field(
        default=None,
        description=(
            "If set, only strategies with these `name` values are used for this source "
            "(in the order given). Example: ['playwright'] forces JS rendering."
        ),
    )

    @field_validator("link_filter", "follow_links")
    @classmethod
    def _validate_regex(cls, v: str | None) -> str | None:
        if v is not None:
            re.compile(v)
        return v

    def model_post_init(self, __context: object) -> None:
        if not self.entry_urls and not self.direct_urls:
            raise ValueError("IrSource requires at least one entry_url or direct_url")


ScrapeStatus = Literal["ok", "partial", "failed"]


class ScrapeError(BaseModel):
    url: str
    stage: Literal["fetch_page", "download_pdf"]
    message: str


class ScrapedManifest(BaseModel):
    """Structured result of one `IrScraper.scrape_source(...)` call.

    Always emitted, even if some downloads failed — consumers inspect
    `status` + `errors` to decide what to do.
    """

    source_name: str
    started_at: str
    completed_at: str
    entry_urls: list[str]
    pages_fetched: int
    pdfs_found: int
    pdfs_downloaded: int
    pdfs_failed: int
    status: ScrapeStatus
    pdfs: list[ScrapedDocument]
    errors: list[ScrapeError]
