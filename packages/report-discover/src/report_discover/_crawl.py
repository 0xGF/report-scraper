"""HTML extraction primitives used by the agent's `fetch_url` tool.

The agent needs three things to pick the right annual-report PDF on
any IR site: the page title, the PDF anchors visible on the page, and
the drill-down nav links to year/period sub-pages. Everything else
(scripts, styles, hydration markers) is noise that drowns the signal
and burns tokens.

This module exposes the regex-and-Public-Suffix-List primitives that
turn raw HTML into those three signal lists. `_agent.py` calls
`_extract_pdf_links` + `_extract_drilldown_links` from inside its
distillation step; nothing here issues network requests.

The only non-trivial library use is `tldextract` for the eTLD+1
("registrable domain") same-organization check — needed because IR
sites routinely link to a sibling brand-asset CDN under the same
registrable domain (`brand.adyen.com` for `adyen.com`), and a naive
host-equality filter drops those.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import structlog
import tldextract

from report_discover.search import SearchResult

log = structlog.get_logger()


# CDN-style URL markers that signal "this is a PDF the host is serving
# without the `.pdf` extension." Each entry is a real pattern from a
# real issuer's CMS:
#   /api/media/             — Inditex
#   /api/asset/             — Adyen brand CDN
#   /static-files/          — Dassault
#   /sites/default/files/   — Drupal-based IR sites
#   /dam/jcr:               — Adobe Experience Manager (corporate sites)
#   /-/media/               — Sitecore (e.g., L'Oréal pre-2020)
#   /getmedia/              — Telerik Sitefinity
#   /documents/, /files/    — generic CMS slots
_PDF_PATH_MARKERS = (
    "/api/media/",
    "/api/asset/",
    "/static-files/",
    "/sites/default/files/",
    "/dam/jcr:",
    "/-/media/",
    "/getmedia/",
    "/documents/",
    "/files/",
)

# Path shapes that look like drill-down nav into year/period pages
# (`/financials/2024`, `/reports/q1-2024`, `/results/h1-2024`). The
# match is intentionally narrow — the path must end in a single
# slug-style segment under one of the known section names.
_DRILLDOWN_PATH_RE = re.compile(
    r"^/(?:[a-z]{2}/)?(?:financials|reports|results|annual|annual-reports|financial-reports)/[^/]+/?$",
    re.IGNORECASE,
)

# `<a ... href="..." ...>` open-tag capture. We deliberately do NOT
# match through to `</a>`: modern SSR frameworks (Nuxt, Vue, React with
# Server Components) inject hydration markers like
# `<a href="…"><!--[-->Label<!--]--></a>`, and any regex that demands
# `[^<]*` between `>` and `</a>` silently drops every such anchor —
# that was a real Adyen failure: 117 of 118 PDF links missed because
# the anchor text contained `<!-- -->` comments. Capturing the open tag
# alone is robust to nested children, comments, and arbitrary inner
# markup. Snippet text is recovered separately in `_anchor_text`.
_HREF_RE = re.compile(
    r"""<a\b([^>]*?)\bhref\s*=\s*['"]([^'"]+)['"]([^>]*)>""",
    re.IGNORECASE,
)

# `aria-label`, `title`, or `download` attribute → human-readable label
# for the SearchResult. None of these are required; the label is
# best-effort so the agent has *something* readable.
_LABEL_RE = re.compile(
    r"""\b(?:aria-label|title|download)\s*=\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# `<form action="...">` and similar carry hrefs too; some IR pages
# wrap their PDF anchors in those. Captured but only as a fallback.
_GENERIC_URL_RE = re.compile(r"""(?:href|src|action)\s*=\s*['"]([^'"]+\.pdf[^'"]*)['"]""", re.I)

# Cached extractor — uses the bundled Public Suffix List snapshot. We
# disable both the network-fetch and the on-disk cache so the library
# stays offline-deterministic; the bundled snapshot is updated when
# `tldextract` is bumped.
_TLDX = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=())


def _looks_like_pdf(url: str) -> bool:
    """Decide whether a URL points at a PDF based on the path alone.

    Catches the standard `.pdf` extension AND the opaque-CDN cases —
    `/api/asset/<uuid>`, `/dam/jcr:<id>`, `/sites/default/files/...` —
    where issuers strip the extension. The downstream HEAD validator
    rejects HTML; this filter is just for first-pass triage.
    """
    u = url.lower().split("#", 1)[0].split("?", 1)[0]
    if u.endswith(".pdf"):
        return True
    return any(marker in u for marker in _PDF_PATH_MARKERS)


def _registrable_base(host: str) -> str:
    """Return the eTLD+1 (`adyen.com`) for `host`, or '' if unparseable."""
    if not host:
        return ""
    ex = _TLDX(host)
    if not ex.domain or not ex.suffix:
        return ""
    return f"{ex.domain}.{ex.suffix}"


def _same_org(a: str, b: str) -> bool:
    """True iff `a` and `b` share a registrable domain (eTLD+1).

    Filters out third-party PDFs an IR page might link to (auditor
    reports, regulator filings) — we want only the company's own
    annual reports. The eTLD+1 match (rather than naive host-suffix)
    correctly accepts sibling subdomains like `investors.adyen.com`
    and `brand.adyen.com` while still rejecting `bmw.com` vs
    `annual-report.bmw.group` (different registrable suffix).
    """
    ha = urlparse(a).netloc.lower().removeprefix("www.")
    hb = urlparse(b).netloc.lower().removeprefix("www.")
    if not ha or not hb:
        return False
    ra = _registrable_base(ha)
    rb = _registrable_base(hb)
    if not ra or not rb:
        # Fall back to host equality when PSL can't classify (intranet
        # hostnames, raw IPs). Better to under-match than over-match.
        return ha == hb
    return ra == rb


def _anchor_text(pre_attrs: str, post_attrs: str) -> str:
    """Best-effort label for an anchor — `aria-label`, `title`, or `download`."""
    for chunk in (pre_attrs, post_attrs):
        m = _LABEL_RE.search(chunk)
        if m:
            return m.group(1).strip()[:160]
    return ""


def _extract_pdf_links(html: str, page_url: str, seed: str) -> dict[str, SearchResult]:
    """Pull PDF anchors out of one page's HTML.

    The page URL the anchor was extracted from is embedded in the
    SearchResult snippet so the agent can year-bucket tokenized URLs
    by the page that linked them (`/financials/2024` vs `/financials/h1-2024`).
    """
    page_path = urlparse(page_url).path or "/"
    snippet = f"(crawled from {page_path})"
    out: dict[str, SearchResult] = {}
    for pre_attrs, href, post_attrs in _HREF_RE.findall(html):
        abs_url = urljoin(page_url, href)
        if not _looks_like_pdf(abs_url) or not _same_org(abs_url, seed):
            continue
        label = _anchor_text(pre_attrs, post_attrs)
        out.setdefault(
            abs_url,
            SearchResult(
                title=label or "(IR page anchor)",
                url=abs_url,
                snippet=snippet,
            ),
        )
    # Fallback for hrefs hidden in `<form action>` etc.
    for url in _GENERIC_URL_RE.findall(html):
        abs_url = urljoin(page_url, url)
        if not _same_org(abs_url, seed):
            continue
        out.setdefault(
            abs_url,
            SearchResult(title="(IR form/embed)", url=abs_url, snippet=snippet),
        )
    return out


def _extract_drilldown_links(html: str, page_url: str, seed: str) -> list[str]:
    """Find anchors that look like drill-down nav into year/period pages.

    Only returns anchors on the same registrable domain whose path
    matches `_DRILLDOWN_PATH_RE`. Used to surface one level deeper
    into sites where the PDF list lives at e.g. `/financials/2024`
    rather than on the IR landing page itself.
    """
    out: list[str] = []
    seen: set[str] = set()
    for _pre, href, _post in _HREF_RE.findall(html):
        abs_url = urljoin(page_url, href)
        if not _same_org(abs_url, seed):
            continue
        path = urlparse(abs_url).path or "/"
        if not _DRILLDOWN_PATH_RE.match(path):
            continue
        # Strip fragments — same `/financials/2024#tab-…` shouldn't be
        # surfaced twice.
        clean = abs_url.split("#", 1)[0]
        if clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out
