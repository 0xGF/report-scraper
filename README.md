# report-scrape

European annual-report viewer. Type a company name, get back ten years of clean,
restatement-aware financial statements rendered in a Terminal-style grid.

```bash
uv run report-scrape add "Heineken"   # discover → scrape → parse → … → export → market
just web                       # localhost:3000 — Heineken now appears in the grid
```

That's the headline. The rest of this README explains how it works.

Currently shipped with **SAP**, **ASML**, **Dassault Systèmes**, **Heineken**,
**Inditex**, and **L'Oréal**. Adding more is one command — see
[`report-scrape add`](#report-scrape-add--one-shot-onboarding).

---

## Stack

- **Python 3.12** (uv workspace) — pipeline (scrape → classify → parse → confirm → normalize → DuckDB → export).
- **TypeScript / Next.js 16** (pnpm workspace) — viewer with AG Grid + shadcn/ui + Tailwind v4.
- **DuckDB** for consolidation and the restatement-aware view.
- **Docling** (IBM Research, MIT) for PDF table extraction; pdfplumber as a fallback.
- **OpenAI** structured outputs (`gpt-5` for discovery, `gpt-4o-mini` for everything else) — disk-cached, never used to read numbers.
- **Tavily** web search (free tier) — grounds discovery in real PDF URLs instead of LLM-guessed ones.

---

## Quick start

```bash
cp .env.example .env       # add OPENAI_API_KEY (required) and TAVILY_API_KEY (recommended)
just install               # uv sync + pnpm install
just web                   # Next.js dev server at localhost:3000
```

The `data/exports/` JSON is committed, so the viewer renders immediately
without re-running the pipeline. To onboard a new company end-to-end:

```bash
uv run report-scrape add "Adyen"  # one-shot
```

---

## `report-scrape add` — one-shot onboarding

A single command chains every stage so adding a company is genuinely zero
friction:

| # | Stage | What it does |
|---|---|---|
| 1 | **discover** | LLM produces canonical name / ticker / exchange / IR URL / scrape config; Tavily grounds the result in real PDF URLs (see [closed-loop discovery](#closed-loop-discovery)). |
| 2 | **logo cache** | Downloads `logo_url` to `apps/web/public/logos/<slug>.png`. |
| 3 | **scrape** | `report-scrape` walks the IR site (`curl_cffi` → `playwright` cascade) **or** downloads `direct_urls` directly. SHA-keyed cache. |
| 4 | **classify** | Rule-based filename / link-text → annual / quarterly / sustainability + fiscal year; LLM tiebreaker only on ambiguity. |
| 5 | **parse** | Docling first, pdfplumber fallback. **Multi-candidate per year** — alternates tried when the top-ranked PDF yields zero statements. |
| 6 | **confirm** | LLM cleans rows, drops noise, attaches preliminary hierarchy. Records the parser's claimed kind for diagnostics; never re-routes tables (we saw it relabel real balance sheets as income on Heineken — disabled). |
| 7 | **normalize** | Canonicalize labels across reports; LLM "drift mapper" resolves variant labels (`"Raw materials, consumables and services"` → `"Cost of revenue"`); pick the latest healthy report's row order as the canonical structure. |
| 8 | **build** | Load every company's normalized facts into DuckDB. |
| 9 | **export** | Static JSON for the viewer (`data/exports/<slug>/{income,balance,cashflow}.json` + `index.json`). |
| 10 | **market** | Refresh live price / sparkline / market cap from Yahoo Finance for every company. |

The command is idempotent — re-running `report-scrape add "Heineken"` re-discovers,
overwrites the YAML row, re-scrapes, and re-exports.

---

## Architecture

```
                  ┌───────────────────────────────────────┐
                  │   report-scrape add "Adyen"  (one-shot CLI)  │
                  └──────────┬────────────────────────────┘
                             │
                  ┌──────────▼──────────┐
                  │     discover        │ ← LLM + Tavily web search
                  │  (closed loop)      │   + Wayback fallback
                  └──────────┬──────────┘
                             │  populates `direct_urls` in
                             ▼  data/companies.yaml
       ┌────────┐    ┌──────────┐    ┌────────┐    ┌─────────┐    ┌──────────┐
       │ scrape │ →  │ classify │ →  │ parse  │ →  │ confirm │ →  │ normalize │
       └────────┘    └──────────┘    └────────┘    └─────────┘    └──────────┘
                                                                       │
                              ┌────────────────────────────────────────┘
                              ▼
                       ┌──────────────┐         ┌──────────────────────────┐
                       │   DuckDB     │   →     │   data/exports/*.json    │
                       │ consolidated │         │   (committed; web reads) │
                       │     view     │         └──────────────────────────┘
                       └──────────────┘                      │
                                                             ▼
                                                       ┌──────────┐
                                                       │ Next.js  │  → localhost:3000
                                                       │  viewer  │
                                                       └──────────┘
```

Each stage is independently re-runnable and writes its output to disk —
debug a single company by re-running just one stage.

---

## Workspace layout

```
report-scrape/
├── packages/
│   ├── report-scrape/         # Python — reusable IR-site scraper
│   └── report-discover/    # Python — closed-loop AI discovery (BYO LLM, BYO search)
├── apps/
│   └── pipeline/           # Python — the pipeline (scrape → … → export)
│   └── web/                # Next.js 16 + Tailwind v4 + AG Grid viewer
├── data/
│   ├── companies.yaml      # company config — committed
│   ├── raw/                # downloaded PDFs — gitignored
│   ├── normalized/         # per-company JSON snapshots
│   ├── report-scrape.duckdb       # consolidated store
│   ├── market.json         # live price / sparkline cache
│   └── exports/            # JSON consumed by the web app — committed
├── justfile                # unified task runner (Python + JS)
├── pyproject.toml          # uv workspace root
└── pnpm-workspace.yaml     # pnpm workspace root
```

---

## Closed-loop discovery

[`packages/report-discover/`](packages/report-discover) is a standalone library —
the most novel part of this submission. It turns a free-text company name
into a working scrape config, with **three layers of resilience** so it
works on the messy real-world IR sites the LLM doesn't know cold.

### The loop

```python
from report_discover import discover
from report_discover.adapters.openai import OpenAILlmClient
from report_discover.adapters.tavily import TavilyClient

llm = OpenAILlmClient(api_key="sk-...")
search = TavilyClient(api_key="tvly-...")

company = discover("Heineken", llm=llm, web_search=search)
# → DiscoveredCompany with .sources[0].direct_urls populated with verified PDF URLs
```

What runs inside `discover()`:

1. **LLM lookup** — structured-output call returns canonical name, ticker,
   exchange, currency, IR URL, plus an initial scrape config.
2. **IR-URL validation** — HEAD/GET; informational, no longer a hard gate.
3. **PDF grounding** ([`_pdfs.py`](packages/report-discover/src/report_discover/_pdfs.py)):
   1. LLM generates 4–6 search queries (mix of `site:` + bare, English variants).
   2. Tavily runs each query; results pooled and deduped to PDF URLs.
   3. LLM picks **at most one URL per fiscal year** from the pool — told to skip rather than guess.
   4. Each pick is HEAD-validated; content-type sniff rejects HTML masquerading as PDF.
4. **AI verify** — second LLM pass audits the result against the original query and reports per-field issues (wrong ticker, mismatched exchange, encoding artifacts).
5. **Persist** — the host app writes to `data/companies.yaml` and busts the in-process cache.

### Resilience layers

| Layer | When it triggers | What it does |
|---|---|---|
| **Broader-query retry** | First-round picks < 5 verified URLs | LLM generates a second batch of looser queries (drop `site:`, alternative report names like "consolidated annual accounts" or "universal registration document"); pool grows; picker re-runs. |
| **Wayback Machine fallback** | A picked URL fails HEAD/GET | Queries `archive.org/wayback/available`, rewrites the snapshot URL with `id_/` to preserve binary bytes, validates again. Recovers PDFs from defunct CDN paths. |
| **Multi-candidate per year** | First-ranked PDF for a year yields zero statements at parse time | `_rank_candidates_per_year` returns an ordered list per `(slug, year)`; the parse loop falls through to the next candidate. Prevents one malformed/scanned PDF from leaving a year empty. |

### BYO design

The library depends on two thin Protocols:

```python
class LlmClient(Protocol):
    def parse(self, *, model, system, user, schema): ...   # structured output
    def ping(self) -> bool: ...                            # cheap reachability

class WebSearchClient(Protocol):
    def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]: ...
```

Bundled adapters: `OpenAILlmClient` (with disk-cached responses) and
`TavilyClient`. Bring Anthropic, Brave, SerpAPI, a local model — anything
matching the contract works. See
[`packages/report-discover/README.md`](packages/report-discover/README.md)
for full library docs.

---

## Pipeline stages in detail

### Scrape (`report-scrape scrape <slug>`)

[`packages/report-scrape`](packages/report-scrape) walks each `IrSource`'s
`entry_urls`, follows `link_filter` to PDFs, downloads with SHA-keyed
caching, and emits a `ScrapedManifest` to `data/raw/<slug>.manifest.json`.

Strategies cascade: `curl_cffi` (Chrome TLS fingerprint, tenacity retry —
3 attempts, exponential backoff with jitter) → `playwright` (real browser,
JS execution). Per-source `strategies_override` forces a specific strategy
when needed.

`direct_urls` is a first-class field for cases where crawling is
impractical — Akamai-protected sites, age-gates, JS microsites with no
direct links. That's how `report-discover` plugs verified PDF URLs in.

### Classify

Rule-based first ([`adapters/classifier/rules.py`](apps/pipeline/src/report_scrape/adapters/classifier/rules.py)):
filename + link-text patterns map to (annual / quarterly / sustainability /
other) and infer fiscal year. Ambiguous docs fall through to a `gpt-4o-mini`
tiebreaker. Output: `data/raw/<slug>.classified.json`.

### Parse — three-tier verifier-gated extraction

The parse stage is the heaviest piece of the pipeline and the one where
the multi-agent literature pays off most. It implements the
**hierarchical-supervisor pattern** from the production multi-agent
financial-extraction systems:

```
┌──────────────────────────────────────────────────────────────────────┐
│  extract_statements(pdf)  — supervisor                                │
└────┬─────────────────────────────────────────────────────────────────┘
     │
     ▼  Tier 1 (cheap, deterministic)
┌─────────────────┐    ┌──────────┐
│ Docling         │ ─► │ Verifier │ ─► publish if clean
│ + pdfplumber    │    │  (LLM)   │ ─► reject ──┐
└─────────────────┘    └──────────┘              │
                                                 ▼
                                         Tier 2 (vision-LLM)
                                         ┌──────────────────┐    ┌──────────┐
                                         │  VlmExtractor    │ ─► │ Verifier │ ─► publish
                                         │  page → image →  │    │          │ ─► reject ──┐
                                         │  gpt-5 vision    │    └──────────┘              │
                                         └──────────────────┘                              │
                                                                                            ▼
                                                                                 Tier 3 (text-LLM)
                                                                                 ┌─────────────────┐
                                                                                 │  LlmExtractor   │
                                                                                 │  (final fallback)│
                                                                                 └─────────────────┘
```

Each tier's output is judged by the same `verify_extraction` agent,
which decides whether a statement passes the publish bar. Critical
issues (rows clearly don't match the claimed kind, all values zero,
truncated table) trigger the next tier; warnings publish with a flag.

**Tier 1 — heuristic ([pdf_extractor.py](apps/pipeline/src/report_scrape/adapters/extractor/pdf_extractor.py))**.
Docling first (IBM Research, MIT, MLP-based table structure recognition);
pdfplumber as a fallback when Docling returns zero rows. Cheap, fast,
handles ~80% of modern PDF layouts. HTML extractor handles SEC EDGAR
20-F filings.

**Tier 2 — vision-LLM ([vlm_extractor.py](apps/pipeline/src/report_scrape/adapters/extractor/vlm_extractor.py))**.
When the verifier rejects a heuristic extraction, we render the
candidate page via `pypdfium2` (no system deps) and ask gpt-5 vision to
read the statement directly. The schema includes `is_correct_kind` —
if the page actually shows a comprehensive-income table instead of an
income statement, the model says so and we move on without polluting
the data.

**Tier 3 — text-LLM ([llm_extractor.py](apps/pipeline/src/report_scrape/adapters/extractor/llm_extractor.py))**.
Last resort: a text-only LLM extractor for the rare case where the
vision pass also can't make sense of the page (heavily-scanned, rotated,
or otherwise visually degraded). Used as a backstop, not a primary path.

**Verifier agent ([verifier.py](apps/pipeline/src/report_scrape/adapters/extractor/verifier.py))**.
After every extraction, the verifier asks an LLM: *"are these rows
consistent with an income statement / balance sheet / cash flow? Are
the values plausible? Anything obviously truncated?"* with a severity
rubric (`critical` / `warning` / `info`). `is_publishable()` decides
whether to keep, drop, or escalate.

**Multi-candidate fall-through**: when multiple PDFs are downloaded for
the same fiscal year (an EN + NL pair, or a clean + scanned copy from
Wayback), `_rank_candidates_per_year` returns them ranked best-first.
The parse loop tries the top-ranked one; if extraction yields zero
statements, it drops to the next candidate. This composes with the
three-tier dispatcher — every candidate gets the full Docling → VLM →
text-LLM treatment.

**Cost shape**. Tier 1 is essentially free (one verifier call per
statement, ~$0.001). Tier 2 only fires on PDFs that fail Tier 1; about
$0.05 per page × 3 candidate pages × 3 statements = ~$0.45 per
problem PDF. Tier 3 is rare. A complete `report-scrape add` for a company
with mostly-good PDFs costs cents; one with old/messy PDFs costs a
few dollars.

### Confirm

LLM gate. Drops noise rows (zero-width chars, page numbers, footnote
artifacts), attaches preliminary hierarchy hints (depth, headers, totals).
Caches per-statement results on disk. **Note:** the auditor's `actual_kind`
output is recorded but no longer used to re-route tables — we observed
it wrongly flipping balance-sheet content to income on Heineken's 2023/2024
reports. The parser's heading-match is more reliable than a row-only vibe
check.

### Normalize

Maps free-form labels to canonical names per statement. Three-tier match:

1. **Exact** — case-insensitive equality against the latest healthy report's
   taxonomy.
2. **Fuzzy** — punctuation/whitespace-normalized key.
3. **LLM drift mapper** — for older reports whose labels diverge ("Raw
   materials, consumables and services" vs the latest's "Cost of
   revenue"), one batched LLM call resolves the unmatched set against
   the canonical taxonomy.

The latest healthy report's row order becomes the canonical hierarchy —
that's the spec's "order to match the latest report" bonus.

### Build (DuckDB consolidation)

[`adapters/store/duckdb_store.py`](apps/pipeline/src/report_scrape/adapters/store/duckdb_store.py)
inserts every report's facts into `facts`, keyed on
`(company, statement, line_item, period_end, source_fiscal_year)`. The
`consolidated` view picks the newest source per cell and flags
`is_restated=true` only when an earlier source reported a different value
(0.01 tolerance for trivial rounding):

```sql
CREATE OR REPLACE VIEW consolidated AS
WITH ranked AS (
    SELECT
        company_slug, statement, canonical_name, period_end, period_months,
        value, currency, unit, source_sha256, source_fiscal_year,
        row_number() OVER (
            PARTITION BY company_slug, statement, canonical_name, period_end
            ORDER BY source_fiscal_year DESC
        ) AS rn,
        COUNT(value) OVER (PARTITION BY ...) AS non_null_sources,
        MIN(value)   OVER (PARTITION BY ...) AS min_value,
        MAX(value)   OVER (PARTITION BY ...) AS max_value
    FROM facts
)
SELECT
    ...,
    (
        non_null_sources >= 2
        AND ABS(max_value - min_value) > 0.01
    ) AS is_restated
FROM ranked
WHERE rn = 1;
```

`rn = 1` **prefers the newer values** (the spec's bonus); the boolean
**surfaces that a restatement happened** (renders as a corner dot in the
viewer).

### Export

DuckDB → static JSON in `data/exports/<slug>/<statement>.json` plus a
top-level `index.json` carrying full per-company metadata (name, ticker,
exchange, IR URL, logo, sector, HQ, founded, source note, `has_data`
flag). The viewer reads these directly — no API, no runtime DB, deploys
as static files anywhere.

### Viewer

Next.js 16 + Tailwind v4 + AG Grid. Routes: `/c/<slug>`. Three tabs per
company (IS / BS / CF), 10+ year columns, bold section headers, indented
children, amber dot on restated cells. Companies in `companies.yaml` that
haven't been pipelined yet show a yellow "Pipeline pending" badge and
don't navigate on click. The grid is purely presentational — anything
wrong on screen is a Python-side bug.

---

## Coverage

Generated by `uv run report-scrape export`; the index.json drives this table.

| Company | Income | Balance | Cash Flow | Source |
|---|---|---|---|---|
| SAP | 12 yrs | 11 yrs | 10 yrs | SEC EDGAR (Form 20-F) |
| ASML | 29 yrs | 24 yrs | 27 yrs | asml.com investor archive |
| Dassault Systèmes | 10 yrs | 10 yrs | 10 yrs | investor.3ds.com (URD) |
| Heineken | 6 yrs | 9 yrs | 10 yrs | theheinekencompany.com (direct PDFs) |
| Inditex | 5 yrs | 5 yrs | 5 yrs | static.inditex.com (consolidated annual accounts) |
| L'Oréal | discovered via Tavily | | | loreal-finance.com / loreal.com |

---

## Reusable packages

Two libraries are factored out of the app and could be published to PyPI
as-is. Both are workspace members.

### `packages/report-scrape`

Strategy-cascading scraper for investor-relations sites:

```python
from report_scrape import IrScraper, IrSource
from report_scrape.strategies import CurlCffiStrategy, PlaywrightStrategy

source = IrSource(
    name="heineken:annual-reports",
    direct_urls=["https://www.theheinekencompany.com/.../annual-report-2024.pdf", ...],
)
with IrScraper(
    raw_dir=Path("data/raw"),
    strategies=[CurlCffiStrategy(), PlaywrightStrategy()],
) as s:
    manifest = s.scrape_source(source)
```

Tenacity retry, SHA-keyed cache, structured I/O contract (`ScrapedManifest`).
See [`packages/report-scrape/README.md`](packages/report-scrape/README.md).

### `packages/report-discover`

Closed-loop AI discovery — see [Closed-loop discovery](#closed-loop-discovery)
above. Public API:

```python
from report_discover import (
    discover,            # the loop
    verify,              # second-pass auditor
    find_pdf_urls,       # the search → pick → validate sub-routine
    DiscoveredCompany,
    LlmClient,           # Protocol — BYO LLM
    WebSearchClient,     # Protocol — BYO search
)
```

`DiscoveredCompany.sources` is shaped exactly like `report_scrape.IrSource`,
so `discover() → IrScraper.scrape_source()` composes directly.

---

## Company selection

> SAP's IR site (`sap.com/investors`) sits behind Akamai Premium bot
> protection. `curl_cffi` with a Chrome TLS fingerprint returns 403;
> Playwright (vanilla) and `patchright` (stealth-patched) also 403.
> Residential-proxy workarounds were judged out of scope. For SAP I
> sourced the canonical English annual report — Form 20-F, SAP's own
> SEC-filed and audited annual document — from SEC EDGAR's public JSON
> API. For ASML and Dassault Systèmes, the pipeline scrapes each
> company's IR site directly as spec'd. For Heineken / Inditex /
> L'Oréal, the closed-loop discovery in `report-discover` finds the
> annual-report PDFs via Tavily web search and populates `direct_urls`
> automatically. The scraper ([`packages/report-scrape`](packages/report-scrape))
> remains generic; `direct_urls` is a first-class field for exactly this
> kind of case.

---

## Restatement logic in plain English

> A cell is "restated" when the same line, for the same period, was
> reported with a *different number* by an earlier filing.

That's the financial meaning — a previously-published figure that the
company corrected, reclassified, or otherwise updated in a later report.
The displayed value is always the newest one (per the spec); the dot is
metadata that tells the viewer "FYI, an earlier report disagreed." See
[Build (DuckDB consolidation)](#build-duckdb-consolidation) for the SQL.

---

## Known limitations

- **SAP via EDGAR, not sap.com** — see [Company selection](#company-selection).
  Scraper unchanged; SAP's config uses `direct_urls`.
- **Tavily key required for novel companies** — without `TAVILY_API_KEY`,
  `discover()` falls back to LLM-only and warns. New companies the model
  doesn't know cold will mostly fail at this point. Get a free key at
  https://app.tavily.com/.
- **AI-verify is advisory.** The auditor flags suspicious discovery
  output (wrong ticker, encoding mojibake) but doesn't downgrade `found`
  — URL reachability and verified PDF count are the hard gates.
- **Confirm doesn't re-route tables.** We disabled the auditor's
  `actual_kind` reassignment after observing it relabel real balance-sheet
  content as income for some Heineken reports. The parser's heading-match
  is what determines a table's kind now.
- **Heineken income coverage is 6 years vs. 9–10 for balance/cashflow** —
  the pre-2019 Heineken income tables are subtle layouts that Docling
  picks up as different table kinds. Multi-candidate fall-through helps
  but doesn't fully recover them.
- **A handful of older ASML segment-specific PDFs don't extract** —
  legacy layouts where Docling can't recover the table structure.
  Reflected in the coverage table.

---

## Development

```bash
just install        # uv sync + pnpm install
just pipeline sap   # full pipeline for one company (already-onboarded)
just scrape-all     # scrape every configured company
just build          # consolidate into DuckDB + restatement view
just export         # DuckDB → data/exports/*.json
just web            # Next.js dev server
just ci             # ruff + mypy + pytest
just web-check      # next typecheck + eslint
just fmt            # ruff format + ruff check --fix
```

Pre-commit hooks (ruff + check-yaml + detect-private-key + large-file
guard) run on every commit; install with `uv run pre-commit install`.

`uv run pytest` covers 95 tests — number parsing, classifier rules,
statement matching, DuckDB restatement, hierarchy injection, pipeline
integration, and the discovery library's URL-failure / verify paths.
