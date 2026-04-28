"""Strategy protocol + shared result/error types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class FetchStrategyError(Exception):
    """Raised when a strategy fails. Scraper escalates to the next strategy."""

    def __init__(self, strategy: str, url: str, reason: str) -> None:
        self.strategy = strategy
        self.url = url
        self.reason = reason
        super().__init__(f"{strategy} failed for {url}: {reason}")


@dataclass(slots=True, frozen=True)
class FetchResult:
    """Result of a successful fetch. Content is HTML (text) or PDF bytes."""

    url: str
    status: int
    content_type: str
    text: str | None = None
    data: bytes | None = None


@runtime_checkable
class FetchStrategy(Protocol):
    """Pluggable fetcher. Implementations must be thread-safe OR not shared."""

    name: str

    def fetch_html(self, url: str) -> FetchResult:
        """Return rendered HTML for `url`. Raise FetchStrategyError on failure."""
        ...

    def fetch_bytes(self, url: str) -> FetchResult:
        """Download binary content from `url`. Raise FetchStrategyError on failure."""
        ...

    def close(self) -> None:
        """Release resources (browser contexts, HTTP pools, etc.)."""
        ...
