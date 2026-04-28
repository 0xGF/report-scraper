"""Playwright fetch strategy — real Chromium, runs JS.

For JS-rendered SPAs where curl_cffi gets an empty shell. Launches a
persistent browser for the strategy's lifetime so multiple URLs amortize
the startup cost. ~300MB chromium install (one-time: `playwright install chromium`).

Lazily imports `playwright` so report-scrape doesn't force the dependency on
consumers who only need `CurlCffiStrategy`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from report_scrape.strategies.base import FetchResult, FetchStrategyError

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Playwright


class PlaywrightStrategy:
    name = "playwright"

    def __init__(
        self,
        *,
        wait_until: str = "networkidle",
        timeout_ms: int = 30_000,
        user_agent: str | None = None,
    ) -> None:
        self._wait_until = wait_until
        self._timeout_ms = timeout_ms
        self._user_agent = user_agent
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def _ensure_browser(self) -> Browser:
        if self._browser is not None:
            return self._browser
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FetchStrategyError(
                self.name,
                "",
                "playwright not installed — `uv pip install playwright && "
                "uv run playwright install chromium`",
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        return self._browser

    def fetch_html(self, url: str) -> FetchResult:
        browser = self._ensure_browser()
        ctx = (
            browser.new_context(user_agent=self._user_agent)
            if self._user_agent
            else browser.new_context()
        )
        try:
            page = ctx.new_page()
            try:
                response = page.goto(url, wait_until=self._wait_until, timeout=self._timeout_ms)  # type: ignore[arg-type]
            except Exception as exc:
                raise FetchStrategyError(self.name, url, str(exc)) from exc
            status = response.status if response else 0
            if status >= 400:
                raise FetchStrategyError(self.name, url, f"HTTP {status}")
            html = page.content()
            return FetchResult(url=url, status=status or 200, content_type="text/html", text=html)
        finally:
            ctx.close()

    def fetch_bytes(self, url: str) -> FetchResult:
        # For binary downloads we bypass the browser and use the APIRequestContext,
        # which reuses the browser's fingerprint + cookies.
        browser = self._ensure_browser()
        ctx = browser.new_context()
        try:
            api = ctx.request
            try:
                r = api.get(url, timeout=self._timeout_ms)
            except Exception as exc:
                raise FetchStrategyError(self.name, url, str(exc)) from exc
            if r.status >= 400:
                raise FetchStrategyError(self.name, url, f"HTTP {r.status}")
            headers = r.headers
            ct = headers.get("content-type", "application/octet-stream").split(";")[0].strip()
            return FetchResult(url=url, status=r.status, content_type=ct, data=r.body())
        finally:
            ctx.close()

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
