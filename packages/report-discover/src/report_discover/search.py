"""Web search adapter Protocol — BYO provider.

Discovery without web search relies on the LLM's training-data knowledge of
IR URLs, which hallucinates frequently. With a `WebSearchClient` plugged in,
`discover()` can ground its answer in real search results and populate
`DiscoveredSource.direct_urls` with verified PDF URLs.

Anything that returns `list[SearchResult]` for a query string works:
Tavily, Brave Search, SerpAPI, your own internal index. The bundled
`adapters/tavily.py` is one implementation; bring others.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """One web-search hit. Mirrors the fields all major search APIs surface."""

    title: str = Field(description="Page title or short headline.")
    url: str = Field(description="Absolute URL of the result.")
    snippet: str = Field(default="", description="Short text excerpt or summary.")


class WebSearchClient(Protocol):
    """Minimal contract for a web-search backend.

    Implementations should return up to `max_results` hits per call. The
    library batches multiple queries; rate-limiting / caching are the
    implementation's responsibility.
    """

    def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        """Run one search query and return up to `max_results` hits."""
        ...
