"""report-discover — AI-powered discovery of investor-relations sources.

Given a company name, find its annual-report PDFs and the metadata
needed to scrape them. The package is BYO-LLM and BYO-search: depend
on tiny `Protocol`s, plug in any provider that satisfies them.

Discovery composes four sources into a single candidate pool:

    1. **LLM lookup**       (`discover()` — generates name / ticker / IR URL)
    2. **Web search**       (Tavily / Brave / SerpAPI / your own)
    3. **Wayback CDX**      (historical PDFs the company has unlinked)
    4. **IR-page crawl**    (HTTP \u2192 Playwright fallback for JS-rendered SPAs)

The pool is then ranked by the LLM picker (one URL per fiscal year, or
year-unknown when the URL is opaque) and HEAD-validated. A second
`verify()` pass audits the generation against the original query.

Composition model:

    discover(name, llm=..., web_search=...) -> DiscoveredCompany
        # → .sources[0].direct_urls populated with verified PDF URLs

    research_gaps(company, missing_years=[...], llm=..., web_search=...) -> list[str]
        # → targeted second pass for fiscal years missing from a first pass

    verify(company, llm=...) -> Verdict
        # → audit the generation, flag suspicious fields

Public API:

    from report_discover import (
        DiscoveredCompany,
        DiscoveredSource,
        FieldIssue,
        LlmClient,           # Protocol — BYO LLM
        SearchResult,
        Verdict,
        WebSearchClient,     # Protocol — BYO search
        discover,
        find_pdf_urls,
        research_gaps,
        verify,
    )

    # Optional adapters
    from report_discover.adapters.openai import OpenAILlmClient
    from report_discover.adapters.tavily import TavilyClient
"""

from report_discover._discover import discover
from report_discover._pdfs import find_pdf_urls
from report_discover._research import research_gaps
from report_discover._verify import FieldIssue, Verdict, verify
from report_discover.llm import AgentLlmClient, LlmClient
from report_discover.models import DiscoveredCompany, DiscoveredSource
from report_discover.search import SearchResult, WebSearchClient

__version__ = "0.4.0"
__all__ = [
    "AgentLlmClient",
    "DiscoveredCompany",
    "DiscoveredSource",
    "FieldIssue",
    "LlmClient",
    "SearchResult",
    "Verdict",
    "WebSearchClient",
    "discover",
    "find_pdf_urls",
    "research_gaps",
    "verify",
]
