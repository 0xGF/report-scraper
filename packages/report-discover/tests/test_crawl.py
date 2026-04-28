"""Tests for the HTML extraction primitives in `_crawl.py`.

These primitives are pure regex/PSL helpers used by the agent's
distillation step (see `_agent._distil_page_for_agent`). They have no
network side-effects — feed them HTML and a seed URL and they return
extracted anchors.
"""

from __future__ import annotations

import pytest

from report_discover._crawl import (
    _extract_drilldown_links,
    _extract_pdf_links,
    _looks_like_pdf,
    _registrable_base,
    _same_org,
)

# --------------------------------------------------------------------------- #
# `_same_org` — eTLD+1 (registrable-domain) match
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        # Sibling subdomains under the same registrable domain — the
        # canonical case a naive host-equality filter gets wrong.
        ("https://investors.adyen.com/x", "https://brand.adyen.com/y.pdf", True),
        ("https://ir.spotify.com/x", "https://investor.spotify.com/y.pdf", True),
        # `www.` prefix variants must match bare and other-subdomain hosts.
        ("https://www.heineken.com/x", "https://heineken.com/y.pdf", True),
        ("https://www.bmw.com/x", "https://media.bmw.com/y.pdf", True),
        # Cross-TLD must NOT match — different registrable suffix even
        # if the second-level label matches.
        ("https://www.bmw.com/x", "https://annual-report.bmw.group/y.pdf", False),
        ("https://www.adyen.com/x", "https://adyen.co.uk/y.pdf", False),
        # Different companies entirely — clear rejection.
        ("https://www.adyen.com/x", "https://www.heineken.com/y.pdf", False),
        # Empty or schemeless inputs degrade gracefully.
        ("", "https://www.adyen.com/x", False),
        ("https://www.adyen.com/x", "", False),
    ],
)
def test_same_org(a: str, b: str, expected: bool) -> None:
    assert _same_org(a, b) is expected


def test_registrable_base_handles_psl_specials() -> None:
    # Public Suffix List handles two-level TLDs correctly.
    assert _registrable_base("www.example.co.uk") == "example.co.uk"
    assert _registrable_base("ir.bmw.group") == "bmw.group"
    assert _registrable_base("") == ""


# --------------------------------------------------------------------------- #
# `_looks_like_pdf` — first-pass triage including CDN markers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Bog-standard `.pdf` extension.
        ("https://www.heineken.com/files/report.pdf", True),
        ("https://www.heineken.com/files/report.pdf?v=1", True),
        # Each CDN marker — pulled from a real-world issuer's CMS.
        ("https://www.inditex.com/api/media/abc-123", True),  # Inditex
        ("https://brand.adyen.com/api/asset/abc-123", True),  # Adyen
        ("https://www.dassault.com/static-files/abc-123", True),  # Dassault
        ("https://example.com/sites/default/files/r.pdf", True),  # Drupal
        ("https://www.acme.com/dam/jcr:abc-def/report", True),  # AEM
        ("https://www.loreal.com/-/media/foo/report", True),  # Sitecore
        ("https://www.acme.com/getmedia/abc.ashx", True),  # Sitefinity
        ("https://www.acme.com/documents/2024/report", True),  # generic
        ("https://www.acme.com/files/2024/report", True),  # generic
        # Negative cases — must NOT trigger.
        ("https://www.acme.com/about", False),
        ("https://www.acme.com/news/2024/foo.html", False),
        ("https://www.acme.com/", False),
    ],
)
def test_looks_like_pdf(url: str, expected: bool) -> None:
    assert _looks_like_pdf(url) is expected


# --------------------------------------------------------------------------- #
# `_extract_drilldown_links` — year/period nav anchor extraction
# --------------------------------------------------------------------------- #


def test_extract_drilldown_links_picks_year_nav() -> None:
    """Anchors like `/financials/2024` are followed; flat nav is not."""
    html = """
    <html><body>
      <nav>
        <a href="/financials/2024">2024 financials</a>
        <a href="/financials/h1-2024">H1 2024</a>
        <a href="/reports/q1-2024">Q1 2024</a>
        <a href="/results/2023">2023 results</a>
        <a href="/financials">All financials</a>  <!-- not a drill-down -->
        <a href="/about">About</a>                 <!-- unrelated -->
        <a href="https://other.com/financials/2024">third party</a>  <!-- different org -->
      </nav>
    </body></html>
    """
    seed = "https://investors.adyen.com/financials"
    links = _extract_drilldown_links(html, seed, seed)
    assert "https://investors.adyen.com/financials/2024" in links
    assert "https://investors.adyen.com/financials/h1-2024" in links
    assert "https://investors.adyen.com/reports/q1-2024" in links
    assert "https://investors.adyen.com/results/2023" in links
    assert "https://investors.adyen.com/financials" not in links
    assert "https://investors.adyen.com/about" not in links
    assert "https://other.com/financials/2024" not in links


def test_extract_drilldown_links_dedupes_fragments() -> None:
    html = """
    <a href="/financials/2024#tab-overview">A</a>
    <a href="/financials/2024#tab-pdfs">B</a>
    """
    seed = "https://investors.adyen.com/financials"
    links = _extract_drilldown_links(html, seed, seed)
    assert links == ["https://investors.adyen.com/financials/2024"]


def test_extract_drilldown_links_accepts_locale_prefix() -> None:
    """`/en/financials/2024` is the same drill-down with a locale prefix."""
    html = '<a href="/en/financials/2024">2024</a>'
    seed = "https://www.heineken.com/en/investors"
    links = _extract_drilldown_links(html, seed, seed)
    assert "https://www.heineken.com/en/financials/2024" in links


def test_extract_drilldown_links_handles_nested_children() -> None:
    """Drill-down extraction must survive anchors with nested children
    (the SSR-comment failure that dropped Adyen's PDF anchors)."""
    seed = "https://investors.adyen.com/financials"
    html = """
    <a href="/financials/h1-2024" class="nav"><!--[-->
      <span><b>H1</b> 2024</span>
    <!--]--></a>
    """
    links = _extract_drilldown_links(html, seed, seed)
    assert "https://investors.adyen.com/financials/h1-2024" in links


# --------------------------------------------------------------------------- #
# `_extract_pdf_links` — PDF anchor extraction with eTLD+1 + CDN markers
# --------------------------------------------------------------------------- #


def test_extract_pdf_links_accepts_sibling_brand_cdn() -> None:
    """The Adyen pattern: PDF anchors point at brand.adyen.com from
    investors.adyen.com — same registrable domain, different subdomain."""
    seed = "https://investors.adyen.com/financials"
    html = """
    <a href="https://brand.adyen.com/api/asset/abc-2024">Adyen Annual Report 2024</a>
    <a href="https://brand.adyen.com/api/asset/abc-2023">Adyen Annual Report 2023</a>
    <a href="https://www.regulator.example/filing.pdf">External filing</a>
    """
    found = _extract_pdf_links(html, seed, seed)
    assert "https://brand.adyen.com/api/asset/abc-2024" in found
    assert "https://brand.adyen.com/api/asset/abc-2023" in found
    assert "https://www.regulator.example/filing.pdf" not in found


def test_extract_pdf_links_handles_ssr_comments_in_anchor() -> None:
    """Real-world Adyen anchors wrap labels in Vue/Nuxt hydration
    comments (`<a href="..."><!--[-->H1<!--]--></a>`). An overly
    strict closing-tag regex drops every such anchor — the bug that
    left only 1 of 60 PDFs extracted on the live IR site."""
    seed = "https://investors.adyen.com/financials"
    html = """
    <a href="https://brand.adyen.com/api/asset/abc/download"
       target="_blank" rel="noopener" aria-label="Annual report 2024"
       download class="ds-button"><!--[--><!--[-->Annual report<!--]--><!--]--></a>
    <a href="https://brand.adyen.com/api/asset/def/download"
       aria-label="H1 2024"><span><i></i>H1</span></a>
    """
    found = _extract_pdf_links(html, seed, seed)
    assert "https://brand.adyen.com/api/asset/abc/download" in found
    assert "https://brand.adyen.com/api/asset/def/download" in found
    assert found["https://brand.adyen.com/api/asset/abc/download"].title == "Annual report 2024"


def test_extract_pdf_links_embeds_source_page_in_snippet() -> None:
    """The page path the anchor was extracted from must appear in the
    snippet — that's how the agent year-buckets tokenized URLs."""
    seed = "https://www.adyen.com/investor-relations"
    page_url = "https://investors.adyen.com/financials/2024"
    html = '<a href="https://brand.adyen.com/api/asset/abc/download">PDF</a>'
    found = _extract_pdf_links(html, page_url, seed)
    snippet = found["https://brand.adyen.com/api/asset/abc/download"].snippet
    assert "/financials/2024" in snippet


def test_extract_pdf_links_rejects_non_pdf_anchors() -> None:
    seed = "https://investors.adyen.com/financials"
    html = """
    <a href="/about.html">About</a>
    <a href="/news/2024/launch">News</a>
    <a href="/files/report.pdf">Report</a>
    """
    found = _extract_pdf_links(html, seed, seed)
    assert "https://investors.adyen.com/files/report.pdf" in found
    assert all("about.html" not in u for u in found)
