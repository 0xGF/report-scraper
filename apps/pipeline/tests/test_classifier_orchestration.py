"""Resilience tests for `classify_all` — covers the LLM-unavailable and
LLM-explodes paths. The audit flagged these as untested edge cases.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from unittest.mock import MagicMock

import pytest

from pipeline.adapters.classifier.llm import LlmClassifier, _Response
from pipeline.adapters.classifier.orchestrator import classify_all
from pipeline.domain.types import ReportKind
from report_scrape import ScrapedDocument


def _doc(
    *,
    url: str = "https://example.com/mystery.pdf",
    local: str = "mystery.pdf",
) -> ScrapedDocument:
    return ScrapedDocument(
        sha256="0" * 64,
        source_url=url,
        page_url="https://example.com",
        link_text=None,
        local_path=local,
        size_bytes=1,
        content_type="application/pdf",
        discovered_at="2026-01-01T00:00:00Z",
    )


@contextmanager
def _mock_classifier(side_effect: object) -> Iterator[LlmClassifier]:
    """Build a `LlmClassifier` with a stubbed `.classify` so we don't touch OpenAI."""
    mock = MagicMock(spec=LlmClassifier)
    mock.name = "llm"
    if isinstance(side_effect, BaseException):
        mock.classify.side_effect = side_effect
    else:
        mock.classify.return_value = side_effect
    yield cast(LlmClassifier, mock)


def test_classify_all_without_llm_uses_rules_only() -> None:
    """No LLM passed in → orchestrator falls back to rules+sniff result."""
    docs = [_doc()]
    out = classify_all(docs, llm=None)
    assert len(out) == 1
    assert out[0].classified_by.startswith("rules")


def test_classify_all_swallows_llm_exceptions() -> None:
    """LLM throws → keep rule hint, don't crash the batch."""
    docs = [_doc(), _doc(local="annual-report-2023.pdf")]  # second is high-confidence rule
    with _mock_classifier(RuntimeError("OpenAI API exploded")) as llm:
        out = classify_all(docs, llm=llm)
    assert len(out) == 2
    # Mystery doc fell back to whatever rules said (low confidence is fine).
    assert out[0].classified_by != "llm"


def test_classify_all_uses_llm_when_rules_uncertain() -> None:
    """Rules say 'other' with low confidence → LLM gets called and wins."""
    response = _Response(kind=ReportKind.ANNUAL, fiscal_year=2024, confidence=0.92, reason="stub")
    with _mock_classifier(response) as llm:
        out = classify_all([_doc()], llm=llm)
    # With low-confidence rules + non-existent local file (sniffer no-op), LLM should fire.
    assert out[0].kind == ReportKind.ANNUAL
    assert out[0].fiscal_year == 2024
    assert out[0].classified_by == "llm"


def test_classify_all_skips_llm_when_rules_confident() -> None:
    """High-confidence rule hit → LLM stays untouched (cost matters)."""
    high_conf = _doc(local="sap-20231231x20f.htm")
    stub_resp = _Response(kind=ReportKind.OTHER, fiscal_year=None, confidence=1.0, reason="stub")
    with _mock_classifier(stub_resp) as llm:
        out = classify_all([high_conf], llm=llm)
    mock = cast(MagicMock, llm)
    mock.classify.assert_not_called()
    assert out[0].kind == ReportKind.ANNUAL


def test_classify_all_one_bad_doc_doesnt_kill_batch() -> None:
    """First doc raises in LLM, second doc still classified."""
    docs = [_doc(local="mystery1.pdf"), _doc(local="mystery2.pdf")]
    response = _Response(kind=ReportKind.ANNUAL, fiscal_year=2022, confidence=0.9, reason="stub")
    with _mock_classifier(response) as llm:
        # First call raises, second returns ok.
        cast(MagicMock, llm).classify.side_effect = [RuntimeError("transient"), response]
        out = classify_all(docs, llm=llm)
    assert len(out) == 2
    assert out[1].classified_by == "llm"


@pytest.mark.parametrize(
    "label,expected",
    [
        ("sap:sec-edgar-20f", "sap"),
        ("dassault", "dassault"),
        (None, ""),
        ("", ""),
    ],
)
def test_company_slug_extraction_from_label(label: str | None, expected: str) -> None:
    from pipeline.domain.companies import company_slug_from_label

    assert company_slug_from_label(label) == expected
