"""`report-scrape add <name>` — single-shot end-to-end onboarding for a new company.

Chains the steps you'd otherwise run by hand:

  1. `discover`  — LLM looks up IR config + market ticker + logo URL,
                   appends a row to `data/companies.yaml`.
  2. logo cache  — if the LLM returned a logo URL, fetch the bytes and
                   save them as `apps/web/public/logos/<slug>.png` so the
                   viewer's existence check picks it up.
  3. `scrape`    — pull every PDF on the IR site (or the configured
                   direct-URL list).
  4. `classify`  — tag each PDF with kind + fiscal year.
  5. `parse`     — extract the big-three statements.
  6. `confirm`   — LLM-validate rows, attach hierarchy, drop noise.
  7. `normalize` — canonicalize labels across reports + flag restatements.
  8. `build`     — load every company's normalized data into DuckDB.
  9. `export`    — emit JSON consumed by the web viewer.
 10. `market`    — refresh live price + sparkline + market cap for ALL
                   companies (so the new one's snapshot lands in the same
                   `data/market.json` as the others).

Idempotent enough to re-run: discover updates the YAML row in place; later
stages re-overwrite their outputs.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import structlog

from pipeline.application import (
    build as _build,
)
from pipeline.application import (
    classify as _classify,
)
from pipeline.application import (
    confirm as _confirm,
)
from pipeline.application import (
    discover as _discover,
)
from pipeline.application import (
    export as _export,
)
from pipeline.application import (
    market as _market,
)
from pipeline.application import (
    normalize as _normalize,
)
from pipeline.application import (
    parse as _parse,
)
from pipeline.application import (
    research as _research,
)
from pipeline.application import (
    scrape as _scrape,
)
from pipeline.config import get_settings

log = structlog.get_logger()

_LOGO_DIR = Path(__file__).resolve().parents[5] / "apps" / "web" / "public" / "logos"
_LOGO_USER_AGENT = "Mozilla/5.0 report-scrape"
_MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB cap — most company logos are < 50 KB


def _download_logo(slug: str, url: str | None) -> Path | None:
    """Fetch a logo URL and write it to the web app's `public/logos/` dir.

    Returns the local path on success, None if the URL was missing,
    unreachable, oversized, or non-image. Failures are logged but never
    raised — a missing logo just falls back to ticker initials.
    """
    if not url:
        return None
    _LOGO_DIR.mkdir(parents=True, exist_ok=True)
    target = _LOGO_DIR / f"{slug}.png"
    try:
        with httpx.Client(follow_redirects=True, timeout=15.0) as client:
            r = client.get(url, headers={"User-Agent": _LOGO_USER_AGENT})
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").lower()
            if not ctype.startswith("image/"):
                log.warning("add.logo.not_image", url=url, content_type=ctype)
                return None
            data = r.content
            if len(data) > _MAX_LOGO_BYTES:
                log.warning("add.logo.too_large", url=url, bytes=len(data))
                return None
            target.write_bytes(data)
            log.info("add.logo.saved", slug=slug, path=str(target), bytes=len(data))
            return target
    except Exception as e:
        log.warning("add.logo.fetch_failed", url=url, err=str(e)[:120])
        return None


def run(company_name: str) -> None:
    """Onboard a new company end-to-end."""
    log.info("add.start", name=company_name)
    settings = get_settings()
    settings.ensure_dirs()

    # 1. Discover (LLM) + persist YAML row.
    result = _discover.run(company_name, write=True)
    if not result.found:
        log.error("add.aborted", reason="discovery returned found=False", notes=result.notes)
        raise RuntimeError(f"Discovery failed for {company_name!r}: {result.notes or 'see logs'}")
    slug = result.slug
    log.info("add.discovered", slug=slug, ir_url=result.ir_url)

    # 2. Cache the logo locally so the viewer's `/logos/<slug>.png` lookup wins.
    _download_logo(slug, result.logo_url)

    # 3-7. The data pipeline.
    log.info("add.scrape.start", slug=slug)
    _scrape.run(slug)
    log.info("add.classify.start", slug=slug)
    _classify.run(slug)
    log.info("add.parse.start", slug=slug)
    _parse.run(slug)

    # Closed-loop gap-fill: parse just emitted extracted.json with whatever
    # years we managed to land. Compute what's missing relative to the
    # 10-year target and run targeted re-discovery for those years. If new
    # URLs are found, scrape/classify/parse re-runs absorb them; their
    # output is merged into the same extracted.json.
    log.info("add.research.start", slug=slug)
    added = _research.fill_gaps(slug)
    if added:
        log.info("add.research.rescrape", slug=slug, new_urls=added)
        _scrape.run(slug)
        _classify.run(slug)
        _parse.run(slug)

    log.info("add.confirm.start", slug=slug)
    _confirm.run(slug)
    log.info("add.normalize.start", slug=slug)
    _normalize.run(slug)

    # 8-9. Aggregate + export (rebuild ALL companies so the new one slots in).
    log.info("add.build.start")
    _build.run()
    log.info("add.export.start")
    _export.run()

    # 10. Refresh market data for every company in the YAML.
    log.info("add.market.start")
    _market.run()

    log.info("add.done", slug=slug, name=result.name)
