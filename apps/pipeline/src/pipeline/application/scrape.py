"""`report-scrape scrape <company>` — discover + download IR PDFs.

Thin wrapper: iterate over the company's `IrSource` configs, run the
scraper on each, persist the union of manifests as
`data/raw/{company}.manifest.json` so subsequent stages can pick it up.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from pipeline.config import get_settings
from pipeline.domain.companies import get_company
from report_scrape import IrScraper, ScrapedManifest, ScrapedPdf
from report_scrape.strategies import CurlCffiStrategy, PlaywrightStrategy

log = structlog.get_logger()


def _manifest_path(raw_dir: Path, company_slug: str) -> Path:
    return raw_dir / f"{company_slug}.manifest.json"


def _load(path: Path) -> dict[str, ScrapedPdf]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    return {row["sha256"]: ScrapedPdf.model_validate(row) for row in payload.get("pdfs", [])}


def _save(path: Path, pdfs: dict[str, ScrapedPdf], manifests: list[ScrapedManifest]) -> None:
    payload = {
        "pdfs": [v.model_dump() for v in pdfs.values()],
        "runs": [m.model_dump() for m in manifests],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def run(company_slug: str, *, discover_only: bool = False, limit: int | None = None) -> None:
    settings = get_settings()
    company = get_company(company_slug)
    raw_dir = settings.report_scrape_raw_dir
    manifest_path = _manifest_path(raw_dir, company_slug)

    existing = _load(manifest_path)
    log.info(
        "scrape.start",
        company=company.name,
        existing=len(existing),
        sources=len(company.sources),
    )

    strategies = [
        CurlCffiStrategy(impersonate="chrome", timeout=settings.scraper_timeout),
        # Playwright boots chromium lazily — no cost unless a source asks for it
        PlaywrightStrategy(timeout_ms=int(settings.scraper_timeout * 1000)),
    ]

    all_manifests: list[ScrapedManifest] = []
    with IrScraper(raw_dir=raw_dir, strategies=strategies) as scraper:
        for source in company.sources:
            log.info("scrape.source", name=source.name, entry_urls=source.entry_urls)
            manifest = scraper.scrape_source(source, discover_only=discover_only, limit=limit)
            all_manifests.append(manifest)
            for pdf in manifest.pdfs:
                existing[pdf.sha256] = pdf
            log.info(
                "scrape.source_done",
                name=source.name,
                status=manifest.status,
                found=manifest.pdfs_found,
                downloaded=manifest.pdfs_downloaded,
                failed=manifest.pdfs_failed,
            )

    _save(manifest_path, existing, all_manifests)
    log.info("scrape.done", company=company.name, total_unique=len(existing))
