"""Reusable scraping primitives for investor-relations sites.

Usage:
    from report_scrape import IrScraper, IrSource
    from report_scrape.strategies import CurlCffiStrategy, PlaywrightStrategy

    source = IrSource(
        name="sap:reports-archive",
        entry_urls=["https://www.sap.com/investors/en/financial-documents-and-events/reports-archive.html"],
    )
    with IrScraper(
        raw_dir=Path("data/raw"),
        strategies=[CurlCffiStrategy(), PlaywrightStrategy()],  # cascade
    ) as s:
        manifest = s.scrape_source(source)
"""

from report_scrape.models import (
    IrSource,
    PdfLink,
    ScrapedDocument,
    ScrapedManifest,
    ScrapedPdf,  # legacy alias
    ScrapeError,
    ScrapeStatus,
)
from report_scrape.scraper import IrScraper

__version__ = "0.1.0"
__all__ = [
    "IrScraper",
    "IrSource",
    "PdfLink",
    "ScrapeError",
    "ScrapeStatus",
    "ScrapedDocument",
    "ScrapedManifest",
    "ScrapedPdf",
]
