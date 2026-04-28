"""Core discovery — `discover(name) -> DiscoveredCompany`.

Wraps a single LLM structured-output call and (optionally) sanity-checks
the IR URL is actually reachable. The library never touches disk —
persistence is the caller's concern.
"""

from __future__ import annotations

import re

import httpx
import structlog

from report_discover._pdfs import find_pdf_urls
from report_discover.llm import AgentLlmClient
from report_discover.models import DiscoveredCompany
from report_discover.search import WebSearchClient

log = structlog.get_logger()


_SYSTEM = (
    "You are a financial-research agent. The user will name a public company "
    "(typically European). Return everything needed to scrape its annual reports "
    "AND render a live market snapshot:\n"
    "- Canonical name, primary ticker, exchange code, reporting currency.\n"
    "- The exact URL of the IR page that lists annual reports / 20-F filings. "
    "Prefer the page that links directly to PDFs over a generic landing page.\n"
    "- A regex `link_filter` that selects annual-report PDFs from that page "
    "(default `.*\\.pdf(\\?|$)` is fine).\n"
    "- Logo URL (Wikipedia or the company's own assets), one-line description, "
    "sector, headquarters city, primary website, year founded.\n"
    "- `market_ticker`: the Yahoo Finance symbol for the company's PRIMARY home-"
    "exchange listing (e.g. 'SAP.DE' for SAP, 'ASML.AS' for ASML, 'DSY.PA' for "
    "Dassault Systèmes, 'HEIA.AS' for Heineken, 'ADYEN.AS' for Adyen). The "
    "currency the symbol trades in must match `reporting_currency`.\n"
    "- `shares_outstanding_m`: most-recently-reported diluted shares outstanding "
    "in MILLIONS (whole-number precision is fine; this is an annual figure).\n"
    "Set `found=false` if you're not confident in the IR URL — better to bail "
    "than to invent a wrong link. Otherwise produce a usable scrape config so "
    "downstream tooling can scrape immediately."
)


def discover(
    company_name: str,
    *,
    llm: AgentLlmClient,
    model: str = "gpt-4o",
    validate_url: bool = True,
    http_timeout: float = 10.0,
    web_search: WebSearchClient | None = None,
    target_years: int = 10,
) -> DiscoveredCompany:
    """Look up an IR source for `company_name`.

    Args:
        company_name: Free-text input (e.g. "Heineken", "SAP SE").
        llm: Any object satisfying the `AgentLlmClient` Protocol —
            i.e., supports both `parse` and `run_agent`. The bundled
            `OpenAILlmClient` does.
        model: Model name passed to `llm.parse`. Default `gpt-4o`.
        validate_url: If True, HEAD/GET the discovered IR URL and mark
            the result `found=False` if it 404s. LLMs hallucinate paths;
            this catches the common case cheaply.
        http_timeout: Seconds for the URL validation request.

    Returns:
        A `DiscoveredCompany`. Check `.found` before trusting the result.
    """
    log.info("discover.start", name=company_name)

    user = (
        f"company_name: {company_name}\n"
        "Return a `DiscoveredCompany`. Pick a slug like 'heineken' (no dots, "
        "no spaces). Pick a `link_filter` that catches annual-report PDFs."
    )
    result = llm.parse(
        model=model,
        system=_SYSTEM,
        user=user,
        schema=DiscoveredCompany,
    )
    result = _sanitize(result)

    if not result.found:
        log.warning(
            "discover.low_confidence",
            name=company_name,
            notes=result.notes,
            ir_url=result.ir_url,
        )
        # Don't bail yet — if a web-search backend is plugged in, the closed
        # loop below can still recover. Many companies the LLM is uncertain
        # about have perfectly-findable PDFs once Tavily searches the open web.
        if web_search is None:
            return result

    ir_url_ok = True
    ir_url_status = 200
    if validate_url:
        ir_url_ok, ir_url_status = _check_url(result.ir_url, timeout=http_timeout)
        if not ir_url_ok:
            log.warning(
                "discover.ir_url_unreachable",
                name=company_name,
                ir_url=result.ir_url,
                status=ir_url_status,
            )

    log.info(
        "discover.llm_ok",
        slug=result.slug,
        name=result.name,
        ir_url=result.ir_url,
        ir_url_reachable=ir_url_ok,
    )

    # Closed-loop step: ground the scrape config in real PDF URLs by
    # running the agent (it has fetch_url + head_url unconditionally,
    # plus web_search when a search backend is configured). Runs even
    # when the IR URL 404'd — with web_search the agent can recover.
    pdf_urls: list[str] = []
    if result.sources:
        pdf_urls = find_pdf_urls(
            result,
            llm=llm,
            web_search=web_search,
            model=model,
            target_years=target_years,
        )
        if pdf_urls:
            new_sources = [
                result.sources[0].model_copy(
                    update={
                        "direct_urls": list(pdf_urls),
                        # The scraper still needs a link_filter for the
                        # `direct_urls` fast-path; widen it so any PDF URL
                        # is accepted (we already verified them).
                        "link_filter": r".*\.pdf(\?|$)",
                    }
                ),
                *result.sources[1:],
            ]
            result = result.model_copy(update={"sources": new_sources})
            log.info(
                "discover.pdfs_grounded",
                slug=result.slug,
                pdf_urls=len(pdf_urls),
            )
        else:
            log.warning("discover.no_pdfs_found", slug=result.slug)

    # Final gate: discovery is "found" iff the scrape config is usable.
    # Web-search-grounded direct_urls is enough on its own — even when the
    # LLM bailed with low confidence or the IR URL 404'd, a populated
    # `direct_urls` means we can scrape. Otherwise we need a reachable IR
    # URL for the cascade scraper to crawl.
    if pdf_urls:
        # Successfully grounded — promote to `found=True` regardless of
        # the LLM's initial confidence or the IR URL's reachability.
        return result.model_copy(update={"found": True})
    if not ir_url_ok:
        return result.model_copy(
            update={
                "found": False,
                "notes": (
                    f"IR URL returned HTTP {ir_url_status} — likely hallucinated. "
                    "Set TAVILY_API_KEY (or pass `web_search=`) to ground discovery "
                    "in real search results."
                ),
            }
        )

    return result


_USER_AGENT = "Mozilla/5.0 report-discover"


_BROKEN_UNICODE_RE = re.compile(r"\x00([0-9a-fA-F]{2})")


def _strip_nulls(s: str) -> str:
    """Repair the OpenAI structured-output unicode-escape bug.

    The model emits `\\u00xx` inside strings; the JSON layer occasionally
    decodes only the first half, landing as `NUL + "xx"` (e.g. `\\u00f1`
    becomes `NUL + "f1"` instead of "ñ"). This wrecks downstream rendering
    — YAML chokes, the viewer prints "Disef1o" where "Diseño" was meant.

    We detect the `NUL + <two hex>` pattern and convert it back to the
    intended codepoint. Stray null bytes without two trailing hex digits
    (rare) are stripped as a final safety net.
    """
    if not s:
        return s
    s = _BROKEN_UNICODE_RE.sub(lambda m: chr(int(m.group(1), 16)), s)
    return s.replace("\x00", "")


def _sanitize(c: DiscoveredCompany) -> DiscoveredCompany:
    """Clean string fields on the returned company + its sources."""
    cleaned_sources = [
        s.model_copy(
            update={
                "name": _strip_nulls(s.name),
                "label": _strip_nulls(s.label) if s.label else None,
            }
        )
        for s in c.sources
    ]
    return c.model_copy(
        update={
            "slug": _strip_nulls(c.slug),
            "name": _strip_nulls(c.name),
            "ticker": _strip_nulls(c.ticker),
            "exchange": _strip_nulls(c.exchange),
            "ir_url": _strip_nulls(c.ir_url),
            "website": _strip_nulls(c.website) if c.website else None,
            "logo_url": _strip_nulls(c.logo_url) if c.logo_url else None,
            "description": _strip_nulls(c.description) if c.description else None,
            "sector": _strip_nulls(c.sector) if c.sector else None,
            "headquarters": _strip_nulls(c.headquarters) if c.headquarters else None,
            "notes": _strip_nulls(c.notes),
            "sources": cleaned_sources,
        }
    )


def _check_url(url: str, *, timeout: float) -> tuple[bool, int]:
    """HEAD/GET the URL to confirm it's not a 404. Returns (ok, status).

    Some sites reject HEAD outright (405) or with a bot 403; fall back to
    a real GET in those cases before declaring the URL dead.
    """
    headers = {"User-Agent": _USER_AGENT}
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as c:
            r = c.head(url, headers=headers)
            if r.status_code in (403, 405):
                r = c.get(url, headers=headers)
            return (r.status_code < 400, r.status_code)
    except Exception:
        return (False, 0)
