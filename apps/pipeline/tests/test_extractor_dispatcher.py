"""Resilience tests for the extractor dispatcher — covers unknown formats,
missing API keys, and concurrent ping caching. The audit flagged the global
ping cache as thread-unsafe; the test below pins down the new locked behavior.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.adapters.extractor import dispatcher
from pipeline.adapters.extractor.dispatcher import _detect_format, _llm_is_live, extract_statements
from pipeline.domain.models import ClassifiedPdf
from pipeline.domain.types import Currency, ReportKind


def _classified(*, local: str, url: str = "https://example.com/doc") -> ClassifiedPdf:
    return ClassifiedPdf(
        sha256="0" * 64,
        source_url=url,
        local_path=local,
        company_slug="acme",
        kind=ReportKind.ANNUAL,
        fiscal_year=2024,
        confidence=1.0,
        classified_by="rules",
    )


@pytest.fixture(autouse=True)
def _reset_ping_cache() -> Iterator[None]:
    """Each test starts with a clean ping cache — module-level globals are sticky."""
    dispatcher._llm_ping_cache = None
    yield
    dispatcher._llm_ping_cache = None


def test_detect_format_pdf() -> None:
    assert _detect_format(_classified(local="report.pdf")) == "pdf"


def test_detect_format_html() -> None:
    assert _detect_format(_classified(local="report.htm")) == "html"
    assert _detect_format(_classified(local="report.html")) == "html"


def test_detect_format_falls_back_to_url_ext() -> None:
    """Local file lacks extension → use URL extension."""
    doc = _classified(local="cached_blob", url="https://example.com/report.pdf?token=abc")
    assert _detect_format(doc) == "pdf"


def test_detect_format_unknown_returns_unknown() -> None:
    doc = _classified(local="report.xyz", url="https://example.com/report.xyz")
    assert _detect_format(doc) == "unknown"


def test_extract_statements_unknown_format_returns_empty() -> None:
    """Garbage extension → no extraction, no crash, no LLM call."""
    out = extract_statements(_classified(local="report.docx"), currency=Currency.EUR)
    assert out == []


def test_extract_statements_no_api_key_returns_rule_results(tmp_path: Path) -> None:
    """Missing API key → rule extractor runs, LLM fallback skipped silently."""
    settings = MagicMock()
    settings.openai_api_key = ""
    settings.report_scrape_cache_dir = tmp_path

    rule_results: list[object] = []  # rule extractor returns nothing for fake .pdf

    with (
        patch("pipeline.adapters.extractor.dispatcher.get_settings", return_value=settings),
        patch("pipeline.adapters.extractor.dispatcher.PdfExtractor") as pdf_cls,
    ):
        pdf_cls.return_value.extract.return_value = rule_results
        out = extract_statements(_classified(local="report.pdf"), currency=Currency.EUR)

    assert out == rule_results


def test_llm_is_live_caches_across_calls() -> None:
    """First call pings; subsequent calls reuse the cached result."""
    client = MagicMock()
    client.ping.return_value = True

    assert _llm_is_live(client) is True
    assert _llm_is_live(client) is True
    assert _llm_is_live(client) is True
    client.ping.assert_called_once()


def test_llm_is_live_thread_safe_pings_once() -> None:
    """20 threads racing on a cold cache → ping called exactly once."""
    client = MagicMock()
    ping_calls = 0
    ping_lock = threading.Lock()

    def slow_ping() -> bool:
        nonlocal ping_calls
        # Simulate latency so threads actually race on the cache miss.
        with ping_lock:
            ping_calls += 1
        threading.Event().wait(0.02)
        return True

    client.ping.side_effect = slow_ping

    results: list[bool] = []
    threads = [
        threading.Thread(target=lambda: results.append(_llm_is_live(client))) for _ in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(results)
    assert ping_calls == 1, f"expected one ping, got {ping_calls}"
