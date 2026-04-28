"""Closed-loop PDF discovery — agent-driven.

`find_pdf_urls` returns up to N direct PDF URLs for a company's annual
reports. The implementation is a single tool-using agent loop: the
model is given the IR URL and tools to navigate the site
(`fetch_url`, `head_url`), then emits a structured list of
`{fiscal_year, url, evidence}` records. Validated URLs flow into
`DiscoveredSource.direct_urls`.

This module replaced an earlier heuristic-pool approach (LLM picker
over a search/wayback/crawl pool, with same-org filters, drill-down
sorts, year-page harvests, brute-force fallbacks). The agent does the
same job in one call without per-site special cases — see `_agent.py`.
"""

from __future__ import annotations

import structlog

from report_discover._agent import find_pdfs_with_agent
from report_discover.llm import AgentLlmClient
from report_discover.models import DiscoveredCompany
from report_discover.search import WebSearchClient  # re-exported

log = structlog.get_logger()


def _company_domain(company: DiscoveredCompany) -> str:
    """eTLD-stripped hostname for the company. Best-effort, "" on failure."""
    if not company.website:
        return ""
    try:
        from urllib.parse import urlparse

        host = urlparse(company.website).hostname or ""
        return host.removeprefix("www.")
    except Exception:
        return ""


def find_pdf_urls(
    company: DiscoveredCompany,
    *,
    llm: AgentLlmClient,
    web_search: WebSearchClient | None = None,
    model: str,
    target_years: int = 10,
    validate: bool = True,
    max_iterations: int = 25,
    **_legacy: object,  # absorb retired kwargs (max_per_query, min_acceptable_years, …)
) -> list[str]:
    """Return validated direct PDF URLs for the company's annual reports.

    Single tool-using agent loop. The model navigates the IR site
    itself with `fetch_url` and `head_url` tools, picks the canonical
    annual report for each year in the target range, and emits the
    structured list. The library validates each URL and filters
    off-domain leaks before returning.

    Args:
        company: Result of an earlier `discover()` call.
        llm: BYO LLM, must satisfy `AgentLlmClient` (i.e., support
            `run_agent`). The bundled `OpenAILlmClient` does.
        web_search: BYO search backend for the agent's `web_search`
            tool. Pass `None` to disable search; the agent falls back
            to navigation-only discovery (works for IR sites with
            server-rendered PDF anchors). With `web_search` available,
            the agent can recover from broken IR URLs and JS-only
            sites by querying the open web.
        model: Model name passed through to the LLM.
        target_years: Year-range size the agent targets, ending today.
        validate: HEAD/GET each URL the agent returns before keeping it.
        max_iterations: Tool-call budget for the agent loop.

    Returns: URLs newest-to-oldest, may be shorter than `target_years`.
    """
    log.info("find_pdfs.start", company=company.name, target_years=target_years)
    urls = find_pdfs_with_agent(
        company,
        llm=llm,
        model=model,
        web_search=web_search,
        target_years=target_years,
        max_iterations=max_iterations,
        validate=validate,
    )
    log.info("find_pdfs.done", validated=len(urls))
    return urls
