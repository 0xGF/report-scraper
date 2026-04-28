# report-discover

**AI-powered discovery of public-company annual-report PDFs.** Give it a
free-text company name, get back a fully validated scrape config —
canonical name, ticker, exchange, IR URL, *and* direct URLs to the last
N years of annual reports.

```python
from report_discover import discover
from report_discover.adapters.openai import OpenAILlmClient
from report_discover.adapters.tavily import TavilyClient

llm = OpenAILlmClient(api_key="sk-...")
search = TavilyClient(api_key="tvly-...")  # optional but strongly recommended

company = discover("Adyen", llm=llm, web_search=search)
# → DiscoveredCompany(slug="adyen", ticker="ADYEN", exchange="Euronext Amsterdam", ...)
# → .sources[0].direct_urls — verified PDF URLs ready to download
```

Built on two `Protocol`s — bring any LLM with a tool-calling loop, bring
any web-search backend. Ships adapters for OpenAI and Tavily.

## How it works

```
                        discover(name, llm)
                              │
                              ▼
                    LLM lookup (single call):
                    company → name, ticker, IR URL, sector, …
                              │
                              ▼
                    Agent loop (multi-call):
                    fetch_url, head_url tools
                    navigate IR site, pick PDFs
                              │
                              ▼
                    HEAD/GET validate
                    (Wayback fallback)
                              │
                              ▼
                    direct_urls in DiscoveredSource
                              │
                              ▼
                    AI verify (advisory)
                    flag wrong ticker / leaked third-party URLs
```

The agent loop is the load-bearing part. `discover` hands the LLM the
IR URL and three tools:

- **`fetch_url`** — fetches any URL and returns a distilled summary of
  HTML pages (title, PDF anchors with labels, drill-down nav links).
  For PDFs it returns metadata (size, content-type) — never raw bytes.
  Distillation collapses ~300 KB of Vue/Nuxt-shell HTML into ~1 KB of
  signal so the agent's context isn't drowned in hydration noise.
- **`head_url`** — cheap reachability + content-type check. The agent
  uses this to confirm a candidate URL before submitting it.
- **`web_search`** — Google-quality search via the BYO web-search
  backend. The agent falls back to this when the IR URL is broken,
  the IR site is a JS-only widget shell with no anchors after
  distillation (Q4inc, EQS, IRConnect), or a year is harder to find
  by navigation than by search.

For each fiscal year the agent returns one direct PDF URL plus
`evidence` (the page-path or search query it came from, and why it's
the consolidated annual report rather than an interim or press
release).

## Why an agent loop instead of a deterministic pool

The previous design was a heuristic pool: search Tavily + Wayback +
crawl, merge URLs into one bucket, ask the LLM to pick one per year.
That works for static IR sites with well-named PDFs. It chokes on
modern issuers where:

* **PDF URLs are tokenized** (`brand.adyen.com/api/asset/eyJjbGllbnRJZCI6...`).
  The picker has nothing in the URL to bucket by year.
* **The IR site lives on a separate subdomain** (`investors.adyen.com`)
  that the picker's same-host filter rejects, while sibling brand-
  asset CDNs host the PDFs.
* **Pages are JS-rendered** with hydration markers
  (`<a><!--[-->...<!--]--></a>`) that brittle anchor regexes silently drop.
* **Vendor IR widgets** (Q4inc, EQS, IRConnect — most US-listed and a
  chunk of EU listings) load PDF lists via XHR after page render. The
  static HTML never lists them; even Playwright with `networkidle`
  doesn't always trigger the vendor's API calls.
* **Year information lives on the linking page**, not the PDF URL —
  the page literally titled "Annual Report 2024" hosts one PDF, but
  to know that you have to *read* the page.

The agent reads the page. It sees the title, the anchor labels, and
the year-bucket nav. When the page is empty or broken, it switches
to `web_search` — vendor-CDN URLs (Spotify's `s29.q4cdn.com/...`,
Wise's `wise.com/imaginary-v2/...`) are public and Tavily/Google
index them. No regex pile required.

## Install

```bash
pip install report-discover                       # core
pip install report-discover[openai]               # bundled OpenAI adapter
pip install report-discover[playwright]           # JS-rendered IR sites
pip install report-discover[openai,playwright]    # everything
```

After installing the `[playwright]` extra, run `playwright install
chromium` once to download the browser binary (~150 MB). Without it,
the JS-shell fallback in `fetch_url` no-ops and the agent falls back
to `web_search`.

## Public API

```python
from report_discover import (
    discover,           # full closed-loop discovery
    research_gaps,      # targeted re-discovery for missing years
    find_pdf_urls,      # the agent sub-routine on its own
    verify,             # second-pass auditor
    DiscoveredCompany,
    DiscoveredSource,
    AgentLlmClient,     # Protocol — BYO tool-using LLM
    LlmClient,          # Protocol — BYO structured-output LLM
    SearchResult,
    Verdict,
    FieldIssue,
)
```

### `discover(name, *, llm) -> DiscoveredCompany`

Single-shot discovery. Returns a `DiscoveredCompany` whose
`.sources[0].direct_urls` lists verified PDF URLs ready to scrape.
The LLM call generates the metadata (ticker, IR URL, sector, etc.);
the agent loop fills in `direct_urls` by navigating the IR site.

### `research_gaps(company, missing_years, *, llm) -> list[str]`

Targeted second pass when the first run leaves gaps. The host
pipeline parses what it has, computes which fiscal years are empty,
and asks this function to fill just those. The same agent loop runs,
but the prompt is narrowed to the explicit year list and any pick
outside that set is dropped. Idempotent and side-effect free — the
caller persists the results.

### `verify(company, *, llm) -> Verdict`

Audit pass. Asks the LLM to compare the discovery output against the
original query and flag per-field issues (wrong ticker, mismatched
exchange, encoding artifacts, leaked third-party URLs). Advisory:
returns a `Verdict`; the caller decides whether to keep, drop, or
annotate.

## BYO LLM

Two Protocols, one method each. Implement both and you're in:

```python
from collections.abc import Callable
from typing import Any, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class MyLlm:
    def parse(self, *, model: str, system: str, user: str, schema: type[T]) -> T:
        ...  # call your model with structured output, return parsed schema

    def ping(self) -> bool:
        ...  # cheap reachability check

    def run_agent(
        self,
        *,
        model: str,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        executor: Callable[[str, dict[str, Any]], str],
        schema: type[T],
        max_iterations: int = 25,
    ) -> T:
        # Drive a tool-call loop until the model emits a structured `schema`.
        # Tools follow the OpenAI tool-spec shape; `executor(name, args)`
        # runs a tool call and returns its string result. The bundled
        # `OpenAILlmClient.run_agent` is the reference implementation.
        ...
```

## Module layout

```
report_discover/
├── _discover.py    # discover() — LLM lookup + agent ground-loop + verify gate
├── _agent.py       # find_pdfs_with_agent() — agent loop + tool executor
├── _pdfs.py        # find_pdf_urls() — public entry, delegates to _agent
├── _research.py    # research_gaps() — gap-fill via the same agent
├── _crawl.py       # HTML extraction primitives (regex + PSL)
├── _http.py        # URL validation + Wayback Machine fallback
├── _verify.py      # second-pass auditor
├── llm.py          # LlmClient + AgentLlmClient Protocols
├── search.py       # SearchResult model (used inside _crawl)
├── models.py       # Pydantic schemas (DiscoveredCompany, etc.)
└── adapters/
    └── openai.py   # bundled OpenAI adapter (parse + run_agent + disk cache)
```

Underscore-prefixed modules are implementation; the public surface is
the `report_discover` namespace and the `adapters/` subpackage.

## What the agent skips

The agent prompt explicitly tells it to skip — and the verifier
catches the rare cases where it doesn't:

- H1/H2 shareholder letters and half-year reports
- Quarterly trading updates (Q1/Q2/Q3/Q4)
- Press releases and conference decks
- Investor-day presentations
- Sustainability/CSR-only reports without consolidated statements
- Proxy / governance documents
- Third-party mirrors (annualreports.com, regulator portals,
  business-intelligence aggregators)

…unless they're the company's primary annual filing (SAP's 20-F,
some issuers' "integrated report").

## Test approach

The agent path itself isn't unit-tested with a live LLM. What's
tested:

- **Distillation primitives** (`_extract_pdf_links`,
  `_extract_drilldown_links`, `_same_org`, `_looks_like_pdf`) —
  pure regex/PSL helpers, fed real Adyen IR HTML fixtures.
- **The agent's `fetch_url` executor** — same-org gate, error
  surfacing, content-type handling.
- **The post-validation pipeline** — URL reachability, eTLD+1 leak
  rejection, year-filter for gap-fill.
- **`_distil_page_for_agent`** — verified against captured Adyen IR
  HTML that the agent actually saw in production.

Live agent runs are validated end-to-end at the host-pipeline level
(see `apps/pipeline` for `pipeline add <company>`).

## Why a separate package

Discovery is a generic problem; pulling it out of the consuming app
makes it reusable across projects and easy to test in isolation. The
host app stitches `discover()` → `report-scrape` → its own parsing
pipeline.
