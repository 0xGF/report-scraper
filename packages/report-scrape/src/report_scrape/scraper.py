"""Generic IR-site scraper.

Composes a cascade of `FetchStrategy` implementations — each URL is fetched
via the first strategy that succeeds, with automatic escalation to the next
on failure. This is what makes the package theoretically work for any URL:
a user can always layer a heavier strategy (Playwright, Firecrawl) under
the default `CurlCffiStrategy`.

Content is discovered generically (regex-based) so the scraper never needs
to know about specific companies or domains.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

import structlog
from selectolax.parser import HTMLParser

from report_scrape.models import (
    IrSource,
    PdfLink,
    ScrapedDocument,
    ScrapedManifest,
    ScrapeError,
)
from report_scrape.strategies import CurlCffiStrategy, FetchStrategy, FetchStrategyError
from report_scrape.strategies.base import FetchResult

log = structlog.get_logger()


def _normalize(base: str, href: str) -> str:
    return urljoin(base, href.strip())


class IrScraper:
    def __init__(
        self,
        *,
        raw_dir: Path,
        strategies: list[FetchStrategy] | None = None,
    ) -> None:
        self._raw_dir = raw_dir
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._strategies: list[FetchStrategy] = strategies or [CurlCffiStrategy()]
        self._strategy_names = [s.name for s in self._strategies]

    def close(self) -> None:
        for s in self._strategies:
            try:
                s.close()
            except Exception:
                log.exception("scraper.strategy_close_failed", strategy=s.name)

    def __enter__(self) -> IrScraper:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------ API
    def scrape_source(
        self,
        source: IrSource,
        *,
        discover_only: bool = False,
        limit: int | None = None,
    ) -> ScrapedManifest:
        started = datetime.now(UTC)
        errors: list[ScrapeError] = []
        pdfs: list[ScrapedDocument] = []

        link_re = re.compile(source.link_filter)
        follow_re = re.compile(source.follow_links) if source.follow_links else None

        active_strategies = self._resolve_strategies(source.strategies_override)

        pages_visited: set[str] = set()
        queue: list[tuple[str, int]] = [(u, 0) for u in source.entry_urls]
        pdf_candidates: dict[str, PdfLink] = {
            u: PdfLink(url=u, page_url=u, link_text=None) for u in source.direct_urls
        }

        while queue:
            page_url, depth = queue.pop(0)
            if page_url in pages_visited:
                continue
            pages_visited.add(page_url)

            try:
                html = self._fetch_html(page_url, active_strategies)
            except FetchStrategyError as exc:
                errors.append(ScrapeError(url=page_url, stage="fetch_page", message=exc.reason))
                log.warning("scraper.page_fetch_failed", url=page_url, reason=exc.reason)
                continue

            for url, text in _extract_hrefs(html, page_url):
                if link_re.search(url):
                    pdf_candidates.setdefault(
                        url, PdfLink(url=url, page_url=page_url, link_text=text)
                    )
                elif (
                    follow_re
                    and follow_re.search(url)
                    and depth < source.max_follow_depth
                    and url not in pages_visited
                ):
                    queue.append((url, depth + 1))

        log.info(
            "scraper.discovered",
            source=source.name,
            pages=len(pages_visited),
            candidates=len(pdf_candidates),
            strategies=self._strategy_names,
        )

        if not discover_only:
            to_download = list(pdf_candidates.values())
            if limit is not None:
                to_download = to_download[:limit]
            for link in to_download:
                try:
                    pdf = self._download(link, source, active_strategies)
                    pdfs.append(pdf)
                except FetchStrategyError as exc:
                    errors.append(
                        ScrapeError(url=link.url, stage="download_pdf", message=exc.reason)
                    )
                    log.warning("scraper.download_failed", url=link.url, reason=exc.reason)

        completed = datetime.now(UTC)
        download_errors = [e for e in errors if e.stage == "download_pdf"]
        status = self._status(
            discover_only=discover_only,
            found=len(pdf_candidates),
            downloaded=len(pdfs),
            download_failures=len(download_errors),
            page_errors=any(e.stage == "fetch_page" for e in errors),
        )

        return ScrapedManifest(
            source_name=source.name,
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
            entry_urls=list(source.entry_urls),
            pages_fetched=len(pages_visited),
            pdfs_found=len(pdf_candidates),
            pdfs_downloaded=len(pdfs),
            pdfs_failed=len(download_errors),
            status=status,
            pdfs=pdfs,
            errors=errors,
        )

    # ------------------------------------------------------------------ internals
    def _resolve_strategies(self, override: list[str] | None) -> list[FetchStrategy]:
        if not override:
            return self._strategies
        by_name = {s.name: s for s in self._strategies}
        missing = [name for name in override if name not in by_name]
        if missing:
            raise ValueError(
                f"IrSource requested strategies {missing} not registered on the scraper "
                f"(available: {self._strategy_names})"
            )
        return [by_name[name] for name in override]

    def _fetch_html(self, url: str, strategies: list[FetchStrategy]) -> str:
        last_exc: FetchStrategyError | None = None
        for s in strategies:
            try:
                result = s.fetch_html(url)
                log.debug("scraper.fetch_ok", url=url, strategy=s.name, status=result.status)
                return result.text or ""
            except FetchStrategyError as exc:
                last_exc = exc
                log.debug("scraper.fetch_escalate", url=url, strategy=s.name, reason=exc.reason)
                continue
        assert last_exc is not None
        raise last_exc

    def _fetch_bytes(self, url: str, strategies: list[FetchStrategy]) -> FetchResult:
        last_exc: FetchStrategyError | None = None
        for s in strategies:
            try:
                result = s.fetch_bytes(url)
                log.debug("scraper.fetch_bytes_ok", url=url, strategy=s.name)
                return result
            except FetchStrategyError as exc:
                last_exc = exc
                log.debug(
                    "scraper.fetch_bytes_escalate", url=url, strategy=s.name, reason=exc.reason
                )
                continue
        assert last_exc is not None
        raise last_exc

    def _download(
        self, link: PdfLink, source: IrSource, strategies: list[FetchStrategy]
    ) -> ScrapedDocument:
        result = self._fetch_bytes(link.url, strategies)
        content = result.data or b""
        sha = hashlib.sha256(content).hexdigest()
        suffix = _suffix_for(link.url, result.content_type)
        local = self._raw_dir / f"{sha}{suffix}"
        if not local.exists():
            local.write_bytes(content)
            log.info(
                "scraper.downloaded",
                url=link.url,
                sha=sha[:12],
                bytes=len(content),
                content_type=result.content_type,
            )
        else:
            log.debug("scraper.cache_hit", sha=sha[:12], url=link.url)

        return ScrapedDocument(
            sha256=sha,
            source_url=link.url,
            page_url=link.page_url,
            link_text=link.link_text,
            local_path=str(local),
            size_bytes=len(content),
            content_type=result.content_type,
            discovered_at=datetime.now(UTC).isoformat(),
            source_name=source.name,
            label=source.label,
        )

    @staticmethod
    def _status(
        *,
        discover_only: bool,
        found: int,
        downloaded: int,
        download_failures: int,
        page_errors: bool,
    ) -> Literal["ok", "partial", "failed"]:
        if page_errors and found == 0:
            return "failed"
        if discover_only:
            return "partial" if page_errors else "ok"
        if download_failures == 0 and not page_errors:
            return "ok"
        if downloaded == 0:
            return "failed"
        return "partial"


def _suffix_for(url: str, content_type: str) -> str:
    """Pick a file suffix from the URL first, content-type second, default `.bin`."""
    url_suffix = Path(urlparse(url).path).suffix.lower()
    if url_suffix in {".pdf", ".htm", ".html", ".xml", ".txt", ".xlsx", ".xbrl", ".zip"}:
        return url_suffix
    ct = content_type.split(";")[0].strip().lower()
    return {
        "application/pdf": ".pdf",
        "text/html": ".html",
        "application/xhtml+xml": ".html",
        "text/plain": ".txt",
        "application/xml": ".xml",
        "application/json": ".json",
    }.get(ct, ".bin")


def _extract_hrefs(html: str, base_url: str) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    tree = HTMLParser(html)
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if not href:
            continue
        resolved = _normalize(base_url, href)
        text = (a.text() or "").strip() or None
        out.append((resolved, text))
    return out


__all__ = ["IrScraper"]
