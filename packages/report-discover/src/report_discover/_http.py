"""HTTP + Wayback utilities — content validation and snapshot recovery.

Two concerns live here:
  1. **PDF URL validation** — HEAD/GET with content-type sniffing to
     reject HTML masquerading as `.pdf` (a common pattern for
     defunct paths that 200-redirect to an error page).
  2. **Wayback Machine fallback** — single-URL snapshot lookup
     (`_wayback_snapshot`) and bulk historical-PDF discovery via the
     CDX API (`_wayback_cdx_pdfs`). Used to recover URLs the company
     has unlinked or moved.

Pure HTTP, no LLM. Splitting these out keeps the picker module focused
on prompts + LLM calls; this module is the deterministic plumbing.
"""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger()

_USER_AGENT = "Mozilla/5.0 report-discover"


def check_url(url: str, *, timeout: float = 10.0) -> bool:
    """Reachable + likely a real binary (not an HTML error page).

    Validation strategy: HEAD first, GET-with-Range as fallback. The
    fallback fires on **any** HEAD error, not just `(403, 405)` — many
    asset-CDN backends (Frontify, AEM Dispatcher, S3 with custom auth,
    a long tail of corporate CMSes) return inappropriate status codes
    for HEAD on URLs that GET serves perfectly. We've seen 403, 404,
    405, 500, and 503 on URLs that download fine via GET. Treating
    HEAD as advisory and confirming with a ranged GET costs at most
    1 KB extra per dead URL but rescues every CDN that has a quirky
    HEAD implementation — including ones we haven't met yet.
    """
    headers = {"User-Agent": _USER_AGENT}
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as c:
            r = c.head(url, headers=headers)
            if r.status_code >= 400:
                r = c.get(url, headers={**headers, "Range": "bytes=0-1023"})
            if r.status_code >= 400:
                return False
            ctype = (r.headers.get("content-type") or "").lower()
            # `application/pdf` is canonical; `application/octet-stream`
            # is what some CDNs return; both are acceptable. Reject HTML
            # outright (typical for "the URL is gone, here's a 200 with
            # an error page").
            return "html" not in ctype
    except Exception:
        return False


def wayback_snapshot(url: str, *, timeout: float = 10.0) -> str | None:
    """If `url` has an Internet Archive snapshot, return the archived URL.

    Uses the public Wayback availability API (no key required). Returns
    the `id_` (raw, non-rewritten) snapshot URL when one exists, else
    None. The `id_` flavor preserves the original PDF bytes — the
    default snapshot URL rewrites internal links and breaks binaries.
    """
    api = "https://archive.org/wayback/available"
    headers = {"User-Agent": _USER_AGENT}
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as c:
            r = c.get(api, params={"url": url}, headers=headers)
            if r.status_code >= 400:
                return None
            data = r.json()
            snap = (data.get("archived_snapshots") or {}).get("closest") or {}
            if not snap.get("available"):
                return None
            wb_url: str | None = snap.get("url")
            ts: str | None = snap.get("timestamp")
            if not wb_url or not ts:
                return None
            return wb_url.replace(f"/{ts}/", f"/{ts}id_/", 1)
    except Exception:
        return None


def resolve_with_fallback(url: str, *, timeout: float = 10.0) -> str | None:
    """Validate `url` directly; on failure, try its Wayback snapshot.

    Returns whichever URL passed validation, or None if both failed.
    """
    if check_url(url, timeout=timeout):
        return url
    snap = wayback_snapshot(url, timeout=timeout)
    if snap and check_url(snap, timeout=timeout):
        log.info("find_pdfs.wayback_recovered", original=url, snapshot=snap)
        return snap
    return None


def wayback_cdx_pdfs(
    domain: str,
    *,
    year_range: tuple[int, int],
    timeout: float = 60.0,
    limit: int = 200,
    retries: int = 2,
) -> list[str]:
    """List historical PDF URLs the Internet Archive indexed under `domain`.

    The CDX API returns every captured URL matching a path pattern. We
    filter to PDFs over the target year window — this catches old
    annual reports the company has since unlinked or moved.

    Wayback CDX is slow on large domains (sometimes 30s+); we use a
    generous timeout and one cheap retry on transient failures before
    giving up.

    Returns a list of unique snapshot-served PDF URLs (ready to
    download), newest-first. Empty list on any error.
    """
    if not domain:
        return []
    api = "https://web.archive.org/cdx/search/cdx"
    params = {
        "url": f"{domain}/*",
        "filter": "mimetype:application/pdf",
        "from": f"{year_range[0]}0101",
        "to": f"{year_range[1] + 1}1231",
        "output": "json",
        "fl": "timestamp,original,statuscode",
        "collapse": "urlkey",
        "limit": str(limit),
    }
    headers = {"User-Agent": _USER_AGENT}
    rows: list[list[str]] = []
    last_err: str | None = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout) as c:
                r = c.get(api, params=params, headers=headers)
                if r.status_code >= 400:
                    return []
                rows = r.json()
                break
        except Exception as e:
            last_err = str(e)[:120]
            if attempt < retries:
                log.info("wayback.cdx_retry", domain=domain, attempt=attempt + 1, err=last_err)
                continue
            log.warning("wayback.cdx_failed", domain=domain, err=last_err)
            return []

    if not rows or len(rows) < 2:
        return []
    # First row is the CSV header; skip it.
    urls: list[str] = []
    seen: set[str] = set()
    for ts, original, status in rows[1:]:
        if status and not status.startswith("2") and status != "-":
            continue
        # `id_` flavor serves the original PDF bytes (no internal-link rewriting).
        wb_url = f"https://web.archive.org/web/{ts}id_/{original}"
        if original in seen:
            continue
        seen.add(original)
        urls.append(wb_url)
    log.info("wayback.cdx_found", domain=domain, candidates=len(urls))
    return urls
