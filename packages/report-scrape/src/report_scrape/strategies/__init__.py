"""Pluggable fetch strategies for IR scraping.

Users compose a list of strategies; `IrScraper` tries each in order per URL
and the first one to succeed wins. This lets a single scraper instance
handle everything from static HTML to bot-protected JS-rendered SPAs
without the caller needing to know which URL needs what.

Default cascade = `[CurlCffiStrategy()]` (fast, free, handles ~80% of sites).
Add `PlaywrightStrategy()` for JS-rendered pages, `FirecrawlStrategy(key=...)`
for the hardest cases.
"""

from report_scrape.strategies.base import FetchResult, FetchStrategy, FetchStrategyError
from report_scrape.strategies.curl_cffi_strategy import CurlCffiStrategy
from report_scrape.strategies.firecrawl_strategy import FirecrawlStrategy
from report_scrape.strategies.playwright_strategy import PlaywrightStrategy

__all__ = [
    "CurlCffiStrategy",
    "FetchResult",
    "FetchStrategy",
    "FetchStrategyError",
    "FirecrawlStrategy",
    "PlaywrightStrategy",
]
