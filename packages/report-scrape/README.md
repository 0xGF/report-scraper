# report-scrape

**Strategy-cascading scraper for investor-relations sites.** Walk an IR
page, follow a regex link filter to PDFs, download with SHA-keyed
caching, return a structured manifest. Plug in `direct_urls` when
crawling is impossible (Akamai-protected sites, age-gates).

```python
from pathlib import Path
from report_scrape import IrScraper, IrSource
from report_scrape.strategies import CurlCffiStrategy, PlaywrightStrategy

source = IrSource(
    name="heineken:annual-reports",
    entry_urls=["https://www.theheinekencompany.com/investors"],
    link_filter=r".*annual[-_]report.*\.pdf(\?|$)",
)
with IrScraper(
    raw_dir=Path("data/raw"),
    strategies=[CurlCffiStrategy(), PlaywrightStrategy()],
) as s:
    manifest = s.scrape_source(source)
```

## What it does

- **Strategy cascade** — every URL falls through `curl_cffi` (Chrome
  TLS fingerprint) → `playwright` (real browser, JS execution) on
  `FetchStrategyError`. Per-source `strategies_override` forces a
  specific strategy when needed.
- **Tenacity retry** — 3 attempts with exponential-backoff jitter
  (1s → 8s) per HTTP request. Handles transient 5xx and rate-limits.
- **SHA-keyed download cache** — every PDF is written under
  `<sha256>.pdf`. Re-runs read the same bytes, no re-download.
- **`direct_urls` field** — skip discovery entirely when you have the
  URLs already (SEC EDGAR filings, manually-curated archives).

## Install

```bash
pip install report-scrape                # core (curl_cffi only)
pip install report-scrape[playwright]    # add JS-rendered support
```

After installing the `[playwright]` extra, run `playwright install
chromium` once (~150 MB).

## Public API

```python
from report_scrape import (
    IrScraper,           # the scraper itself
    IrSource,            # declarative scrape config
    PdfLink,             # discovered URL, not yet downloaded
    ScrapedDocument,     # downloaded file with sha256, size, source URL
    ScrapedManifest,     # structured result of one scrape_source() call
    ScrapeError,         # per-URL failure record
    ScrapeStatus,        # "ok" | "partial" | "failed"
)

from report_scrape.strategies import (
    CurlCffiStrategy,    # Chrome TLS fingerprint — primary
    PlaywrightStrategy,  # real Chromium — JS-rendered fallback
    FirecrawlStrategy,   # paid API — alternative
    FetchStrategy,       # Protocol — BYO strategy
    FetchStrategyError,
)
```

## `IrSource` schema

```python
class IrSource(BaseModel):
    name: str                          # stable identifier
    entry_urls: list[str]              # pages to crawl (may be empty)
    direct_urls: list[str]             # PDFs to download directly
    link_filter: str                   # regex applied to resolved URLs
    follow_links: str | None           # regex — non-PDF links crawled one level
    max_follow_depth: int = 1
    label: str | None                  # propagated onto each ScrapedDocument
    strategies_override: list[str] | None
```

`direct_urls` is a first-class field. Discovery tools like
[`report-discover`](../report-discover/) populate it automatically;
SEC EDGAR or manually-curated archives populate it directly.

## Strategy cascade

Each fetch tries strategies in order. First success wins; failures
fall through. The default cascade `[CurlCffiStrategy(),
PlaywrightStrategy()]` handles ~95% of European IR sites.

```python
class FetchStrategy(Protocol):
    name: str
    def fetch_html(self, url: str) -> FetchResult: ...
    def fetch_bytes(self, url: str) -> FetchResult: ...
    def close(self) -> None: ...
```

Implement the Protocol to add your own (ScrapingBee, ScraperAPI,
residential-proxy gateway, etc.).

## Module layout

```
report_scrape/
├── scraper.py                  # IrScraper — the orchestrator
├── models.py                   # IrSource, ScrapedDocument, ScrapedManifest
└── strategies/
    ├── base.py                 # FetchStrategy Protocol
    ├── curl_cffi_strategy.py   # Chrome TLS fingerprint — primary
    ├── playwright_strategy.py  # Real Chromium — JS-rendered fallback
    └── firecrawl_strategy.py   # Paid API — optional alternative
```
