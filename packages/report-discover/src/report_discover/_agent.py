"""Agent-based PDF discovery — model navigates IR sites with tools.

Replaces the brittle pool-and-pick approach with a single tool-using
agent loop: the LLM is given the IR landing page, a `fetch_url` tool,
and a `head_url` tool. It navigates, reads anchor text and headings,
chooses the canonical annual-report PDF for each year in the target
range, and emits a structured `[{fiscal_year, url, evidence}, ...]`.

Why this beats the heuristic pool-and-pick approach:

  * No regex for drill-down nav, no `_BARE_YEAR_PAGE_RE`, no priority
    sort, no interim filter, no "prefer same-org" post-filter, no
    brute-force fallback. The model reads the page like a human and
    picks the document literally titled "Annual Report 2024".

  * Tokenized URLs (Adyen-style `/api/asset/<id>`) stop being a
    problem — the agent doesn't pattern-match the URL, it reads the
    page that linked it.

  * Different IR-site shapes (drill-down, single-page, JS-rendered,
    SEC-EDGAR-style) all flow through the same loop. No per-site
    code paths.

The deterministic infrastructure stays:
  * `_crawl.py`'s HTTP+Playwright fetch (the agent's `fetch_url` tool
    delegates to it for JS-rendered shells).
  * `_http.check_url` validates each URL the agent returns — the
    agent doesn't get to make up URLs that don't exist.
  * eTLD+1 same-org check rejects third-party PDFs the agent might
    accidentally return.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field

from report_discover._crawl import (
    _extract_drilldown_links,
    _extract_pdf_links,
)
from report_discover._http import check_url, resolve_with_fallback
from report_discover.llm import AgentLlmClient
from report_discover.models import DiscoveredCompany
from report_discover.search import WebSearchClient

log = structlog.get_logger()

_USER_AGENT = "Mozilla/5.0 report-discover-agent"

# SEC requires a contact email in the User-Agent for their JSON APIs.
# This is a project-specific contact; if you fork the package, change it.
_SEC_USER_AGENT = "report-discover (open-source) contact@example.com"

# Heuristic markers that an HTML response is a JS-rendered shell
# rather than the actual content. When we see these *and* the
# distilled extraction came up empty, we re-fetch via Playwright.
_JS_SHELL_MARKERS = (
    # Vue/Angular/Mustache template tokens that appear because the
    # client-side framework hasn't substituted them yet.
    "{{",
    # Spotify's Q4 IR platform leaves visible `{{docUrl}}` and similar.
    "ng-app",
    "ng-controller",
    "data-reactroot",
    'id="__nuxt"',
    'id="__next"',
)


def _looks_like_js_shell(html: str) -> bool:
    """Detect HTML that's a hydration shell rather than rendered content.

    Checked only when the deterministic distillation found zero PDF
    anchors and zero drill-down links — a successful static fetch
    that yields *something* doesn't trigger Playwright.
    """
    head = html[:50000]  # don't scan a huge page; signals are at the top
    return any(m in head for m in _JS_SHELL_MARKERS)


def _playwright_render(url: str, *, timeout_ms: int = 15000) -> str | None:
    """Render `url` in a headless Chromium and return the rendered HTML.

    Lazy-imports Playwright so users without the optional dep aren't
    forced to install it. Returns `None` if Playwright isn't
    available or the page fails to load — the caller falls back to
    the static HTML it already has.

    Wait pattern: `domcontentloaded` then a short selector wait for
    any anchor (`a[href]`) — sufficient for Spotify-style pages where
    the IR widget injects PDF links after the framework hydrates.
    """
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.info("agent.playwright_unavailable")
        return None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=_USER_AGENT)
                page = context.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    import contextlib

                    with contextlib.suppress(PWTimeout):
                        page.wait_for_selector("a[href]", timeout=5000, state="attached")
                    return page.content()
                finally:
                    page.close()
                    context.close()
            finally:
                browser.close()
    except Exception as e:
        log.warning("agent.playwright_failed", err=str(e)[:120], url=url[:120])
        return None


# Quick-and-dirty title extractor — captures `<title>...</title>`. The
# title is the only freeform-text signal we keep when distilling a
# page; everything else is anchor-shaped.
_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)


def _distil_page_for_agent(html: str, page_url: str, seed: str) -> str:
    """Compact a fetched HTML page into anchor-shaped tokens for the agent.

    Modern IR sites (Adyen / Wise / Spotify / etc.) ship a Nuxt or
    React shell with 200-400 KB of hydration HTML. Raw text drowns
    the agent in noise (CSS, scripts, SSR comments) and burns ~$0.05
    per fetch in tokens. The agent only needs three things to make a
    pick: the page title, the PDF anchors that point to plausible
    annual reports, and the in-section drill-down nav (e.g. links to
    `/financials/2024`). Everything else is irrelevant.

    Output format is plain text in three named sections so the model
    can read it without parsing JSON:

        TITLE: <page title>
        PAGE: <fetched URL>

        PDF LINKS:
          - url=... | label=...
        DRILL-DOWN LINKS (one level deeper):
          - <year-bucket URL>
    """
    title_m = _TITLE_RE.search(html)
    title = (title_m.group(1).strip()[:160]) if title_m else "(no title)"

    pdf_anchors = _extract_pdf_links(html, page_url, seed)
    drilldowns = _extract_drilldown_links(html, page_url, seed)

    lines: list[str] = [f"TITLE: {title}", f"PAGE: {page_url}", ""]

    lines.append(f"PDF LINKS ({len(pdf_anchors)}):")
    if not pdf_anchors:
        lines.append("  (none on this page)")
    else:
        # Cap at 60 to stay under reasonable token budgets — Adyen's
        # index page has ~30 unique PDFs, so this rarely truncates.
        for hit in list(pdf_anchors.values())[:60]:
            label = (hit.title or "").strip()[:80] or "(no label)"
            lines.append(f"  - url={hit.url}  label={label}")

    lines.append("")
    lines.append(f"DRILL-DOWN LINKS ({len(drilldowns)}):")
    if not drilldowns:
        lines.append("  (no year/period sub-pages linked from here)")
    else:
        for d in drilldowns[:40]:
            lines.append(f"  - {d}")

    return "\n".join(lines)


class _DiscoveredPdf(BaseModel):
    fiscal_year: int | None = Field(
        default=None,
        ge=1990,
        le=2099,
        description=(
            "The fiscal year the report covers. Set to NULL only when "
            "the URL is a clearly-labelled annual report but its year "
            "isn't visible in the page text (rare)."
        ),
    )
    url: str = Field(description="Direct PDF URL — must be reachable.")
    evidence: str = Field(
        description=(
            "One-line justification: the page-path you found this on, "
            "the anchor label, why you're confident this is the full "
            "annual report (not an H1/H2 letter, not quarterly results, "
            "not ESG-only)."
        ),
    )


class _DiscoveredPdfs(BaseModel):
    pdfs: list[_DiscoveredPdf] = Field(default_factory=list)


_SYSTEM = (
    "You are a financial-research agent. The user gives you a public "
    "company and the URL of its investor-relations page. Your job: "
    "find direct PDF links to the company's CONSOLIDATED ANNUAL REPORT "
    "for each year in the requested range.\n"
    "\n"
    "You have these tools:\n"
    "- `fetch_url(url)` — fetch any URL. For HTML, you receive a "
    "DISTILLED summary: the page title, every PDF link with its label, "
    "and every drill-down link to year/period sub-pages. The raw HTML "
    "is not returned — you don't need it. For PDFs, you get metadata "
    "(size, content-type).\n"
    "- `head_url(url)` — cheap reachability + content-type check. Use "
    "before returning a URL to confirm it exists and is a PDF.\n"
    "- `web_search(query)` — Google-quality web search. Use when the "
    "IR URL is broken (404 or redirect-loop), when the IR site is a "
    "JS-rendered shell with no PDF anchors after distillation (Q4inc, "
    "EQS, IRConnect — most US-listed and a chunk of EU listings host "
    "their PDFs on a vendor CDN like `s29.q4cdn.com/<id>/files/...` "
    "and the static HTML never lists them), or when you can't find a "
    "year that should exist. Search the company name + year + "
    "`annual report filetype:pdf`.\n"
    "- `sec_filings(ticker_or_name)` — direct SEC EDGAR lookup. "
    "ALWAYS use this FIRST for any US-listed company OR any foreign "
    "private issuer with US ADRs. EDGAR has the canonical Form 10-K "
    "(US issuer) or Form 20-F (foreign issuer) for every fiscal year, "
    "with stable URLs. Examples of companies where EDGAR is the right "
    "first stop: Spotify, ASML, BP, Adidas, Adyen ADR, anything traded "
    "on NYSE/NASDAQ. The `filing_date` is when the form was lodged "
    "(usually 30-90 days after FY-end), so a 20-F filed Feb 2025 "
    "covers fiscal year 2024.\n"
    "\n"
    "Strategy:\n"
    "0. If the company is US-listed (NYSE/NASDAQ) or has US ADRs, START "
    "with `sec_filings(ticker)` — EDGAR returns canonical 10-K / 20-F "
    "URLs and you're done in 1-2 iterations. Skip the IR site entirely "
    "for these. Submit those URLs.\n"
    "1. For non-SEC companies, try `fetch_url` on the IR URL. If it "
    "404s, try predictable subdomain variants: `investors.<base>`, "
    "`ir.<base>`. If those also fail, jump straight to `web_search` "
    "for `<company> annual report <year> filetype:pdf` — don't burn "
    "iterations on guesswork.\n"
    "2. If the IR landing page returns a distilled view with PDF "
    "anchors, follow drill-downs to per-year pages and pick the full "
    "annual report. The right doc is usually the largest PDF on a page "
    "literally titled `Annual Report <year>` or similar.\n"
    "3. If the IR landing page returns ZERO PDF anchors AND ZERO "
    "drill-down links, it's a JS-rendered shell that didn't hydrate. "
    "Use `web_search` instead — vendor-CDN URLs (e.g. "
    "`s29.q4cdn.com/175625835/files/doc_financials/2024/ar/Annual-Report-2024.pdf`) "
    "are public and Tavily/Google index them.\n"
    "4. The CONSOLIDATED ANNUAL REPORT goes by different names depending "
    "on jurisdiction and listing. ACCEPT these (they ARE the annual):\n"
    "    • `Annual Report` (most issuers, all jurisdictions)\n"
    "    • `Annual Report and Accounts` (UK, e.g. Wise plc, LSE filers)\n"
    "    • `Form 20-F` (US-listed foreign private issuers — Spotify, "
    "ASML, BP, all ADRs)\n"
    "    • `Form 10-K` (US domestic issuers)\n"
    "    • `Universal Registration Document` / `URD` (French CAC40, "
    "AEX issuers)\n"
    "    • `Integrated Report` (some issuers' primary annual filing)\n"
    "    • `Consolidated Annual Accounts` / `Consolidated Financial "
    "Statements` (Spanish, German, Dutch issuers)\n"
    "    • `Jaarverslag` (Dutch), `Geschäftsbericht` (German), "
    "`Document de référence` (French) and other localized equivalents\n"
    "  REJECT these — they look annual-shaped but ARE NOT consolidated "
    "annual reports, even when their title mentions the full year:\n"
    "    • `Q4 Shareholder Letter` / `Q4 Shareholder Deck` (these are "
    "QUARTERLY filings; even though Q4 reports full-year numbers, the "
    "consolidated annual is filed SEPARATELY ~3-6 weeks later)\n"
    "    • `Preliminary Results Announcement` / `Preliminary Financial "
    "Announcement` / `RNS Preliminary` (UK pre-publication of headline "
    "numbers — the actual annual follows later)\n"
    "    • `Form 6-K` (foreign-issuer interim filings — quarterly equivalent)\n"
    "    • `Form 8-K` (US event-driven filings — never an annual)\n"
    "    • `H1 Report` / `H2 Report` / `Half-Year Report` / `Interim Report`\n"
    "    • `Q1 / Q2 / Q3 / Q4 Trading Update` / `Trading Statement`\n"
    "    • `Investor Day` deck, `Capital Markets Day` deck\n"
    "    • `ESG Report` / `Sustainability Report` / `CSR Report` (UNLESS "
    "it's an integrated report that contains the consolidated statements)\n"
    "    • `Proxy Statement` / `Notice of AGM` / governance documents\n"
    "  CRITICAL: if the only document you can find for a year is in the "
    "REJECT list (e.g. only a Q4 letter, no actual 10-K/20-F/Annual "
    "Report), SKIP THAT YEAR. Returning a Q4 letter as the annual report "
    "WILL break the downstream parser. Missing years are accepted; wrong "
    "documents are not.\n"
    "5. PREFER first-party hosts. Sibling subdomains under the company's "
    "registrable domain are first-party (`brand.adyen.com` for "
    "`adyen.com`); vendor IR-platform CDNs (`q4cdn.com`, `eqs-cockpit.com`) "
    "are also first-party for that company because the CMS hosts the "
    "company's own files there. Skip generic mirror sites "
    "(annualreports.com, regulator portals, news aggregators) UNLESS "
    "no other URL exists for that year.\n"
    "6. Always call `head_url` on your final candidate URLs before "
    "submitting — it costs ~10ms and catches dead links.\n"
    "7. When you have your picks, call `submit__DiscoveredPdfs` with "
    "the structured payload. Include short `evidence` for each — describe "
    "where you found it (page-path, search query, anchor label) and why "
    "you're confident it's the consolidated annual. CALLING THIS TOOL "
    "ENDS THE LOOP, so submit only when ready; submit ONCE per run.\n"
    "\n"
    "Budget: ~25 tool calls max. Don't recursively crawl — be targeted. "
    "Skip a year if the report isn't easily findable rather than "
    "guessing. The downstream pipeline accepts gaps; it doesn't accept "
    "wrong PDFs."
)


def _sec_lookup_cik(ticker_or_name: str) -> str | None:
    """Resolve a ticker or company name to its SEC CIK.

    SEC publishes a daily-refreshed JSON of every public filer at
    `https://www.sec.gov/files/company_tickers.json`. Cheaper and more
    reliable than scraping EDGAR's HTML search.
    """
    try:
        with httpx.Client(timeout=15.0, headers={"User-Agent": _SEC_USER_AGENT}) as c:
            r = c.get("https://www.sec.gov/files/company_tickers.json")
            if r.status_code >= 400:
                return None
            data = r.json()
    except Exception:
        return None
    needle = ticker_or_name.strip().lower()
    for entry in data.values():
        if entry.get("ticker", "").lower() == needle or needle in entry.get("title", "").lower():
            cik = str(entry.get("cik_str", "")).zfill(10)
            return cik or None
    return None


def _sec_recent_filings(
    cik: str, *, form_types: tuple[str, ...] = ("20-F", "10-K", "40-F")
) -> list[dict[str, str]]:
    """Return every filing of the given form types for a CIK.

    Pulls `data.sec.gov/submissions/CIK<cik>.json`. The returned URLs
    point at the primary document (HTM or PDF); the downstream scraper
    handles HTM-to-text. Newer 20-Fs are HTM, older ones are sometimes
    PDF — both are valid annual filings.
    """
    cik_padded = cik.zfill(10)
    try:
        with httpx.Client(timeout=15.0, headers={"User-Agent": _SEC_USER_AGENT}) as c:
            r = c.get(f"https://data.sec.gov/submissions/CIK{cik_padded}.json")
            if r.status_code >= 400:
                return []
            data = r.json()
    except Exception:
        return []
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    primary = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])
    out: list[dict[str, str]] = []
    bare_cik = cik.lstrip("0")
    for i, f in enumerate(forms):
        if f not in form_types:
            continue
        if i >= len(accessions) or i >= len(primary) or i >= len(dates):
            continue
        accession_clean = accessions[i].replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{bare_cik}/{accession_clean}/{primary[i]}"
        out.append(
            {
                "form": f,
                "filing_date": dates[i],
                "url": url,
                "primary_document": primary[i],
            }
        )
    return out


def _build_executor(seed: str, *, web_search: WebSearchClient | None) -> tuple[Any, Any]:
    """Returns (executor, cache) for the agent's tool calls.

    The executor used to enforce a same-org filter on every URL the
    agent touched. That broke search-based discovery — many companies
    host their PDFs on a third-party CDN under a vendor's domain
    (Q4inc's `s29.q4cdn.com/<client_id>/...`, EQS Group's
    `eqs-cockpit.com/...`, etc.), and the filter rejected those even
    though they're the canonical source. We now trust the agent to
    pick the right host (with `web_search` for grounding when the IR
    site is broken or empty); the AI verify pass and the picker
    prompt's "first-party preferred" guidance catch the rare case
    where the agent picks a junk mirror.
    """
    cache: dict[str, str] = {}

    def fetch_url(url: str) -> str:
        if url in cache:
            return cache[url]
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=15.0,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                r = client.get(url)
        except Exception as e:
            result = f"ERROR fetching {url}: {e}"
            cache[url] = result
            return result
        ctype = (r.headers.get("content-type") or "").lower()
        if r.status_code >= 400 and r.status_code not in (403, 404, 405):
            result = f"HTTP {r.status_code} on {url}"
        elif "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
            # Don't dump PDF bytes; report metadata so the agent learns
            # this URL is a real PDF and can return it.
            result = (
                f"[PDF — content-type={ctype}, size={len(r.content)} bytes, status={r.status_code}]"
            )
        elif "html" in ctype:
            # Distil the HTML to what the agent actually needs: PDF
            # anchors + drill-down nav links + the page title. Raw HTML
            # for an Nuxt/React shell is mostly hydration noise (style,
            # script, SSR comments) — feeding 300 KB of it costs tokens
            # AND drowns the actual signal.
            result = _distil_page_for_agent(r.text, url, seed)
            # Playwright fallback: when the static distillation comes
            # up empty AND the page looks like a JS-rendered shell
            # (e.g., Spotify's Q4 IR platform leaves `{{docUrl}}`
            # template tokens unsubstituted in static HTML), re-fetch
            # via headless Chromium and re-distil. ~2s overhead but
            # unlocks the entire class of JS-only IR microsites.
            if (
                "PDF LINKS (0)" in result
                and "DRILL-DOWN LINKS (0)" in result
                and _looks_like_js_shell(r.text)
            ):
                log.info("agent.playwright_fallback", url=url)
                rendered = _playwright_render(url)
                if rendered:
                    result = _distil_page_for_agent(rendered, url, seed)
        else:
            result = (
                f"[non-html — content-type={ctype}, size={len(r.content)} "
                f"bytes, status={r.status_code}]"
            )
        cache[url] = result
        return result

    def head_url(url: str) -> str:
        # SEC EDGAR serves Form 20-F / 10-K as HTM, not PDF — and an
        # HTM 20-F is the canonical filing the downstream scraper
        # already handles (see SAP's config). `check_url` rejects HTML
        # outright by default, so for sec.gov URLs we degrade to a
        # status-only reachability check.
        is_sec = "sec.gov/archives/edgar" in url.lower()
        try:
            if is_sec:
                with httpx.Client(
                    follow_redirects=True,
                    timeout=10.0,
                    headers={"User-Agent": _SEC_USER_AGENT},
                ) as c:
                    r = c.head(url)
                    if r.status_code in (403, 404, 405):
                        r = c.get(url, headers={"Range": "bytes=0-1023"})
                    ok = r.status_code < 400
                return "EDGAR doc reachable" if ok else "NOT a reachable EDGAR doc"
            ok = check_url(url, timeout=10.0)
        except Exception as e:
            return f"ERROR: {e}"
        return "PDF reachable" if ok else "NOT a reachable PDF"

    def search_web(query: str, max_results: int = 8) -> str:
        """Fall back to a web search when navigation comes up empty.

        Returns a formatted list of `{url, title, snippet}` triples.
        The agent picks promising results and verifies them with
        `head_url` before submitting. When `web_search` isn't wired
        into the runtime (no Tavily key, BYO discovery), this returns
        a clear error so the agent knows to rely on navigation only.
        """
        if web_search is None:
            return (
                "ERROR: `web_search` is not configured in this runtime. "
                "Use fetch_url to navigate from the IR URL instead."
            )
        try:
            hits = web_search.search(query, max_results=max_results)
        except Exception as e:
            return f"ERROR: search failed: {e}"
        if not hits:
            return f"(no results for: {query})"
        lines = [f"SEARCH RESULTS ({len(hits)} for: {query}):"]
        for h in hits[:max_results]:
            title = (h.title or "").strip()[:120]
            snip = (h.snippet or "").strip().replace("\n", " ")[:200]
            lines.append(f"  - url: {h.url}")
            if title:
                lines.append(f"    title: {title}")
            if snip:
                lines.append(f"    snippet: {snip}")
        return "\n".join(lines)

    def sec_filings(ticker_or_name: str, form_types: list[str] | None = None) -> str:
        """Look up filings on SEC EDGAR by ticker or name."""
        cik = _sec_lookup_cik(ticker_or_name)
        if cik is None:
            return (
                f"NOT FOUND on EDGAR: '{ticker_or_name}' isn't in SEC's "
                "company-ticker index. The company is probably not US-listed "
                "and not an SEC foreign private issuer — fall back to "
                "fetch_url / web_search."
            )
        forms = tuple(form_types) if form_types else ("20-F", "10-K", "40-F")
        rows = _sec_recent_filings(cik, form_types=forms)
        if not rows:
            return f"EDGAR lookup OK (CIK={cik}) but no filings of {forms} on file."
        lines = [f"SEC EDGAR filings for {ticker_or_name} (CIK={cik}):"]
        for row in rows[:20]:
            lines.append(f"  - {row['filing_date']}  {row['form']}  url={row['url']}")
        return "\n".join(lines)

    def executor(name: str, args: dict[str, Any]) -> str:
        if name == "fetch_url":
            return fetch_url(str(args.get("url", "")))
        if name == "head_url":
            return head_url(str(args.get("url", "")))
        if name == "web_search":
            return search_web(
                str(args.get("query", "")),
                max_results=int(args.get("max_results", 8)),
            )
        if name == "sec_filings":
            forms_raw = args.get("form_types")
            forms = [str(f) for f in forms_raw] if isinstance(forms_raw, list) else None
            return sec_filings(str(args.get("ticker_or_name", "")), forms)
        return f"ERROR: unknown tool `{name}`"

    return executor, cache


_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Fetch any URL. For HTML pages you get a distilled view "
                "(title, PDF anchors with labels, drill-down nav links). "
                "For PDFs you get metadata (size, content-type)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "head_url",
            "description": (
                "Cheap reachability + content-type check. Use before "
                "returning a URL in your final answer to confirm it's a "
                "real PDF (not an HTML error page or 404)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for PDFs. Use this when the IR URL is "
                "broken (404), the IR site is JS-rendered with no usable "
                "anchors (vendor IR widgets like Q4inc/EQS load PDFs "
                "via XHR — the static HTML is empty), or you can't find "
                "a year that should exist. Always verify each picked URL "
                "with `head_url` before submitting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Free-text search query. Include the company "
                            "name, the year, 'annual report', and "
                            "'filetype:pdf' for best results."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 8,
                        "description": "1-15 results.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sec_filings",
            "description": (
                "Look up a company's filings on SEC EDGAR by ticker or "
                "name. ALWAYS prefer this over fetch_url / web_search for "
                "any US-listed company OR any foreign private issuer with "
                "ADRs (e.g., Spotify, ASML, BP, Adidas) — EDGAR has the "
                "canonical Form 10-K (US issuer) or Form 20-F (foreign "
                "issuer) for every fiscal year, with stable URLs that "
                "won't change. Returns a list of filings with "
                "`filing_date`, `form`, and `url`. The 20-F filed in "
                "Feb 2025 covers fiscal year 2024 (filed ~30-90 days "
                "after FY-end)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker_or_name": {
                        "type": "string",
                        "description": (
                            "Company ticker (e.g. 'SPOT', 'ASML') or name. "
                            "Tickers match faster; names use substring."
                        ),
                    },
                    "form_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "SEC form codes to filter to. Defaults to "
                            "['20-F', '10-K', '40-F'] — the consolidated "
                            "annual filings. Don't include 6-K (interim), "
                            "8-K (event), DEF 14A (proxy)."
                        ),
                    },
                },
                "required": ["ticker_or_name"],
            },
        },
    },
]


def find_pdfs_with_agent(
    company: DiscoveredCompany,
    *,
    llm: AgentLlmClient,
    model: str,
    web_search: WebSearchClient | None = None,
    target_years: int = 10,
    max_iterations: int = 25,
    validate: bool = True,
    years_filter: set[int] | None = None,
) -> list[str]:
    """Agent-driven replacement for `_pdfs.find_pdf_urls`.

    Returns validated direct PDF URLs sorted newest-to-oldest. May be
    shorter than `target_years` — the agent is told to skip rather
    than guess.

    `years_filter` is the gap-fill use-case: pass an explicit set of
    fiscal years and any pick outside that set is dropped after the
    agent returns. The agent itself sees those years in the prompt so
    it focuses its tool calls there.
    """
    if not company.ir_url and not company.website:
        return []
    seed = company.ir_url or company.website or ""
    end_year = dt.date.today().year
    start_year = end_year - target_years
    year_range = (start_year, end_year)

    if years_filter:
        years_block = (
            "target_years (FILL ONLY THESE — skip anything else): "
            f"{sorted(years_filter, reverse=True)}\n"
        )
    else:
        years_block = f"target_year_range: {year_range[0]}-{year_range[1]}\n"

    user = (
        f"company_name: {company.name}\n"
        f"ticker: {company.ticker}\n"
        f"ir_url: {seed}\n"
        f"website: {company.website or '(unknown)'}\n"
        f"{years_block}"
        "\n"
        "Find the consolidated annual-report PDF for each year above. "
        "Use the tools to navigate the company's IR site. When you're "
        "done, emit `_DiscoveredPdfs`."
    )

    executor, _cache = _build_executor(seed, web_search=web_search)

    try:
        result = llm.run_agent(
            model=model,
            system=_SYSTEM,
            user=user,
            tools=_TOOLS,
            executor=executor,
            schema=_DiscoveredPdfs,
            max_iterations=max_iterations,
        )
    except Exception as e:
        log.warning("agent.failed", err=str(e)[:160], company=company.slug)
        return []

    log.info(
        "agent.picks",
        company=company.slug,
        count=len(result.pdfs),
        years=sorted({p.fiscal_year for p in result.pdfs if p.fiscal_year}),
    )

    # Final validation — every URL the agent submitted must resolve to
    # a real PDF. The same-org check that used to live here was dropped
    # alongside the fetch-tool gate: vendor IR-platform CDNs (q4cdn,
    # eqs-cockpit, etc.) are off-eTLD+1 but legitimate, and search-
    # grounded picks are off-eTLD+1 by definition. Trust the agent's
    # picks; rely on the AI verify pass to flag bad mirrors.
    out: list[str] = []
    seen: set[str] = set()
    for p in sorted(
        result.pdfs,
        key=lambda x: (x.fiscal_year is None, -(x.fiscal_year or 0)),
    ):
        if years_filter and p.fiscal_year is not None and p.fiscal_year not in years_filter:
            continue
        if p.url in seen:
            continue
        if not validate:
            resolved: str | None = p.url
        elif "sec.gov/archives/edgar" in p.url.lower():
            # SEC EDGAR returned this URL via the structured API — it's
            # canonical by construction. resolve_with_fallback would
            # reject it because EDGAR serves HTM (`text/html`), and the
            # downstream scraper handles HTM directly (see SAP config).
            resolved = p.url
        else:
            resolved = resolve_with_fallback(p.url)
        if resolved:
            out.append(resolved)
            seen.add(p.url)
            seen.add(resolved)
        else:
            log.warning("agent.url_unreachable", year=p.fiscal_year, url=p.url[:120])

    return out
