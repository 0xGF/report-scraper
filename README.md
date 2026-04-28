# report-scrape

Type a company name, get back ten years of clean, restatement-aware
financial statements in a viewer.

```
packages/
  report-discover/   AI-powered IR discovery (BYO LLM + search)
  report-scrape/     Strategy-cascading PDF scraper

apps/
  pipeline/          classify → parse → confirm → normalize → build → export
  web/               Next.js viewer

data/
  companies.yaml     per-company config (generated, committed)
  exports/           viewer-ready JSON (committed)
  raw/               SHA-keyed PDF cache (gitignored)
```

## How it works

```
pipeline add <name>
   ↓
report-discover    agent loop with fetch_url / head_url / web_search /
                   sec_filings → DiscoveredCompany.direct_urls
   ↓
report-scrape      curl_cffi → Playwright cascade, SHA-keyed cache
   ↓
apps/pipeline      classify → parse (Docling + VLM fallback) →
                   confirm → normalize → DuckDB → JSON export
   ↓
apps/web           reads data/exports/{slug}/{income,balance,cashflow}
```

## Packages

**`report-discover`** — single tool-using agent loop. Given a company
name, the LLM picks the canonical annual-report PDF for each fiscal
year using four tools: `fetch_url` (distilled HTML view of any page),
`head_url` (reachability), `web_search` (Tavily fallback for broken /
JS-only IR sites), `sec_filings` (direct EDGAR lookup for SEC filers).
BYO `LlmClient` and `WebSearchClient` Protocols.

**`report-scrape`** — downloads PDFs the discoverer found. Cascades
`curl_cffi` (browser-fingerprinted) → Playwright (JS-rendered).
SHA-256-keyed disk cache; Wayback fallback on dead URLs. BYO
`Strategy` Protocol if you want a different fetcher.

**`apps/pipeline`** — orchestrates the eight stages between scrape and
export. Verifier-gated parsing (Docling first, pdfplumber fallback,
gpt-5 vision pass on failure). Restatement-aware DuckDB build keeps
the most-recently-filed value per `(slug, year, line_item)`.
