"""Tests for `RuleClassifier` — covers the main IR filename / link-text
patterns we hit across SAP, ASML, and Dassault.
"""

from __future__ import annotations

from pipeline.adapters.classifier.rules import RuleClassifier
from pipeline.domain.types import ReportKind
from report_scrape import ScrapedDocument


def _doc(
    *,
    url: str = "https://example.com/doc.pdf",
    local: str = "doc.pdf",
    link_text: str | None = None,
) -> ScrapedDocument:
    return ScrapedDocument(
        sha256="0" * 64,
        source_url=url,
        page_url="https://example.com",
        link_text=link_text,
        local_path=local,
        size_bytes=1,
        content_type="application/pdf",
        discovered_at="2026-01-01T00:00:00Z",
    )


def test_sap_20f_filename() -> None:
    r = RuleClassifier()
    hint = r.classify(
        _doc(
            url="https://www.sec.gov/.../sap-20231231x20f.htm",
            local="sap-20231231x20f.htm",
        )
    )
    assert hint.kind == ReportKind.ANNUAL
    assert hint.fiscal_year == 2023


def test_asml_uk_annual_report_filename() -> None:
    r = RuleClassifier()
    hint = r.classify(_doc(local="annual_report2017_uk.pdf"))
    assert hint.kind == ReportKind.ANNUAL
    assert hint.fiscal_year == 2017


def test_dassault_registration_document_prefers_urd() -> None:
    r = RuleClassifier()
    hint = r.classify(
        _doc(
            link_text="Registration Document",
            local="3DS_2021_URD_31032022.pdf",
        )
    )
    assert hint.kind == ReportKind.ANNUAL
    assert hint.fiscal_year == 2021


def test_dassault_corporate_report() -> None:
    r = RuleClassifier()
    hint = r.classify(_doc(link_text="Corporate Report 2020", local="corporate-report-2020.pdf"))
    assert hint.kind == ReportKind.ANNUAL
    assert hint.fiscal_year == 2020


def test_sustainability_filename() -> None:
    r = RuleClassifier()
    hint = r.classify(_doc(local="sustainability-report-2023.pdf"))
    assert hint.kind == ReportKind.SUSTAINABILITY
    assert hint.fiscal_year == 2023


def test_unrelated_doc_low_confidence() -> None:
    r = RuleClassifier()
    hint = r.classify(_doc(local="press-release-june-2024.pdf"))
    assert hint.kind in (ReportKind.OTHER, ReportKind.ANNUAL)
    assert hint.confidence <= 0.75
