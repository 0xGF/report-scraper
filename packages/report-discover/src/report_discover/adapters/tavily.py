"""Tavily web-search adapter.

Tavily (https://tavily.com) has a free tier and an AI-friendly response
format — its `/search` endpoint returns JSON with title/url/content per
hit, which maps cleanly to our `SearchResult`.

Install with `pip install report-discover[tavily]`. Set `TAVILY_API_KEY`
or pass `api_key=` directly.
"""

from __future__ import annotations

import httpx
import structlog

from report_discover.search import SearchResult

log = structlog.get_logger()

_TAVILY_ENDPOINT = "https://api.tavily.com/search"


class TavilyClient:
    """Tavily-backed `WebSearchClient`.

    A single instance reuses one `httpx.Client`. Call `close()` when done
    or use as a context manager.
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 20.0,
        search_depth: str = "basic",
    ) -> None:
        if not api_key:
            raise ValueError("Tavily api_key is required.")
        self._api_key = api_key
        self._search_depth = search_depth
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TavilyClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        body = {
            "api_key": self._api_key,
            "query": query,
            "search_depth": self._search_depth,
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        try:
            r = self._client.post(_TAVILY_ENDPOINT, json=body)
            r.raise_for_status()
        except Exception as e:
            log.warning("tavily.search_failed", query=query[:80], err=str(e)[:120])
            return []

        data = r.json()
        results: list[SearchResult] = []
        for item in data.get("results", [])[:max_results]:
            url = item.get("url")
            title = item.get("title") or ""
            if not url:
                continue
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=item.get("content") or "",
                )
            )
        log.debug("tavily.search_ok", query=query[:80], hits=len(results))
        return results
