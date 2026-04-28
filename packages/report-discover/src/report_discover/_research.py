"""Targeted gap-fill discovery — find PDFs for *specific* missing years.

`discover()` runs once with a broad target window. The host pipeline
parses what it has, computes a gap set (years that came back empty),
and asks this module for a targeted second pass.

Implementation is a re-targeted agent run: the same agent that drives
`find_pdf_urls` is told to look for *only* the missing years. This
keeps everything in one place — no separate heuristic plumbing for
gap-fill versus initial discovery.

The function is idempotent and side-effect free; the caller owns
persistence and re-running pipeline stages on the new docs.
"""

from __future__ import annotations

import datetime as dt

import structlog

from report_discover._agent import find_pdfs_with_agent
from report_discover.llm import AgentLlmClient
from report_discover.models import DiscoveredCompany
from report_discover.search import WebSearchClient

log = structlog.get_logger()


def research_gaps(
    company: DiscoveredCompany,
    *,
    missing_years: list[int],
    llm: AgentLlmClient,
    web_search: WebSearchClient | None = None,
    model: str,
    max_iterations: int = 15,
    **_legacy: object,
) -> list[str]:
    """Return validated PDF URLs for the given missing fiscal years.

    The agent is briefed with the explicit target years and told to
    skip what it can't find rather than guess. Returns a subset of
    those years (or empty) — never invents URLs for years outside
    `missing_years`.
    """
    if not missing_years:
        return []

    log.info(
        "research_gaps.start",
        company=company.name,
        missing=sorted(missing_years, reverse=True),
    )

    # Frame the request as a tight year-range so the agent doesn't
    # waste tool calls on years already covered. We also pass the
    # explicit list in the user prompt via `target_years_override`
    # so the agent knows to filter against it.
    lo, hi = min(missing_years), max(missing_years)
    span = hi - lo + 1
    today = dt.date.today().year

    # The agent's `target_years` is a ROLLING window ending today; we
    # need to widen it so `lo` is in scope. The agent's prompt then
    # filters down to the explicit missing-years list.
    target_years = max(span, today - lo)

    urls = find_pdfs_with_agent(
        company,
        llm=llm,
        model=model,
        web_search=web_search,
        target_years=target_years,
        max_iterations=max_iterations,
        validate=True,
        years_filter=set(missing_years),
    )

    log.info("research_gaps.done", validated=len(urls))
    return urls
