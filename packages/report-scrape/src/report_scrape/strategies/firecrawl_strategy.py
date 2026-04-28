"""Firecrawl fetch strategy — hosted scraping service.

Real Chrome fingerprints + managed proxies. Costs money. Useful as a last
resort for sites where Playwright's default fingerprint still triggers
detection (Akamai Bot Manager hard mode, Cloudflare challenge pages).

Activates only when an API key is provided; otherwise the strategy raises
on first use so consumers explicitly opt in.
"""

from __future__ import annotations

import httpx

from report_scrape.strategies.base import FetchResult, FetchStrategyError


class FirecrawlStrategy:
    name = "firecrawl"
    _API = "https://api.firecrawl.dev/v1/scrape"

    def __init__(self, *, api_key: str | None, timeout: float = 60.0) -> None:
        self._api_key = api_key
        self._client = httpx.Client(timeout=timeout)

    def _require_key(self, url: str) -> str:
        if not self._api_key:
            raise FetchStrategyError(
                self.name, url, "FIRECRAWL_API_KEY is not set; strategy disabled"
            )
        return self._api_key

    def fetch_html(self, url: str) -> FetchResult:
        key = self._require_key(url)
        try:
            r = self._client.post(
                self._API,
                headers={"Authorization": f"Bearer {key}"},
                json={"url": url, "formats": ["html"]},
            )
        except Exception as exc:
            raise FetchStrategyError(self.name, url, str(exc)) from exc
        if r.status_code >= 400:
            raise FetchStrategyError(self.name, url, f"HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        if not body.get("success"):
            raise FetchStrategyError(self.name, url, str(body)[:200])
        html = body.get("data", {}).get("html") or ""
        return FetchResult(url=url, status=200, content_type="text/html", text=html)

    def fetch_bytes(self, url: str) -> FetchResult:
        # Firecrawl's /scrape is HTML-oriented. For binary downloads we fall through
        # with a plain httpx call that benefits from the caller having already
        # resolved redirects, etc. Consumers should chain a lighter strategy
        # after this one for binary fetches.
        raise FetchStrategyError(
            self.name,
            url,
            "firecrawl strategy does not support binary downloads; chain CurlCffi after it",
        )

    def close(self) -> None:
        self._client.close()
