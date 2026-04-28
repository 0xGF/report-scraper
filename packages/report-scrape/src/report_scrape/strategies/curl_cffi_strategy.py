"""`curl_cffi` fetch strategy — Chrome TLS fingerprint, no browser.

Passes JA3/JA4 filtering and most UA-based bot gates. Handles ~80% of IR
sites including those behind basic WAFs. Fails against fully JS-rendered
SPAs (the HTML shell comes back, but the interesting content is loaded
client-side).
"""

from __future__ import annotations

from curl_cffi import requests as cffi_requests
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from report_scrape.strategies.base import FetchResult, FetchStrategyError


class CurlCffiStrategy:
    name = "curl_cffi"

    def __init__(self, *, impersonate: str = "chrome", timeout: float = 30.0) -> None:
        self._session = cffi_requests.Session(impersonate=impersonate, timeout=timeout)

    def fetch_html(self, url: str) -> FetchResult:
        return self._fetch(url, want_bytes=False)

    def fetch_bytes(self, url: str) -> FetchResult:
        return self._fetch(url, want_bytes=True)

    def close(self) -> None:
        self._session.close()

    @retry(
        wait=wait_exponential_jitter(initial=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _fetch(self, url: str, *, want_bytes: bool) -> FetchResult:
        try:
            r = self._session.get(url)
        except Exception as exc:  # network / TLS errors
            raise FetchStrategyError(self.name, url, str(exc)) from exc
        if r.status_code >= 400:
            raise FetchStrategyError(self.name, url, f"HTTP {r.status_code}")
        ct = r.headers.get("content-type", "").split(";")[0].strip() or "application/octet-stream"
        return FetchResult(
            url=url,
            status=r.status_code,
            content_type=ct,
            text=None if want_bytes else r.text,
            data=r.content if want_bytes else None,
        )
