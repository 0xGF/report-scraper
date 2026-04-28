"""Rule-based classifier — filename + link-text regex.

Covers the vast majority of IR documents without an LLM call. Returns a
confidence score so the orchestrator can decide whether to escalate to
the LLM fallback for the leftover ambiguous cases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pipeline.adapters.classifier.constants import (
    CONFIDENCE_KIND_NO_YEAR,
    CONFIDENCE_KIND_WITH_YEAR,
    CONFIDENCE_NONE,
    CONFIDENCE_YEAR_ONLY,
)
from pipeline.domain.types import ReportKind, is_valid_year
from report_scrape import ScrapedDocument


@dataclass(slots=True, frozen=True)
class ClassificationHint:
    kind: ReportKind
    fiscal_year: int | None
    confidence: float
    matched: str  # human-readable debug: which rule hit


# Ordered by specificity — first match wins. Groups capture the year where the
# regex can; when they can't, the year-fallback scan picks up any 4-digit year.
#
# Cover-page patterns (URD / Annual Report / 20-F) come BEFORE the generic
# "financial statements" catch-all, so a sniffed Dassault URD whose cover
# says "2024 Universal Registration Document" doesn't get its kind nailed
# down by a deeper "4 Financial statements" TOC entry pulling a stray year.
_KIND_PATTERNS: list[tuple[ReportKind, str]] = [
    # SEC Form 20-F / 10-K
    (ReportKind.ANNUAL, r"(?:^|[-_./])(\d{4})\d{4}x?20-?f"),  # sap-20191231x20f[...]
    (ReportKind.ANNUAL, r"form[-_\s]*20-?f|form[-_\s]*10-?k"),
    (ReportKind.ANNUAL, r"20-?f\b|10-?k\b"),
    # Universal Registration Document — French cover format captures the
    # FY directly: "Document d'enregistrement universel 2022".
    (ReportKind.ANNUAL, r"document\s+d['\u2019\u2018]enregistrement\s+universel\s+(\d{4})"),
    (ReportKind.ANNUAL, r"document\s+d['\u2019\u2018]enregistrement\s+universel"),
    # `3DS_2021_URD_31032022.pdf` — fiscal year sits right before the URD
    # token; trailing digits are a filing-date stamp, not the FY.
    (ReportKind.ANNUAL, r"[-_/\s](\d{4})[-_/\s]urd[-_/\s]?"),
    (ReportKind.ANNUAL, r"universal[-_\s]+registration[-_\s]+document"),
    (ReportKind.ANNUAL, r"[-_/\s]urd[-_/\s]"),
    (ReportKind.ANNUAL, r"\burd\b"),
    (ReportKind.ANNUAL, r"registration[-_\s]+document"),
    # Explicit "annual report YYYY" / "YYYY annual report"
    (ReportKind.ANNUAL, r"annual[-_\s]+report[-_\s]+(\d{4})"),
    (ReportKind.ANNUAL, r"(\d{4})[-_\s]+annual[-_\s]+report"),
    (ReportKind.ANNUAL, r"annual[-_\s]+report"),
    (ReportKind.ANNUAL, r"annual[-_\s]+financial[-_\s]+report"),
    # Integrated reports (SAP's annual-report-equivalent)
    (ReportKind.ANNUAL, r"integrated[-_\s]+report"),
    # Generic "financial statements" — last resort because it's too broad
    # otherwise (matches TOCs, deep section headings, etc.).
    (ReportKind.ANNUAL, r"(annual[-_\s]+|consolidated[-_\s]+)?financial[-_\s]+statements"),
    # Dassault-style "Corporate Report" — a glossy annual summary
    (ReportKind.ANNUAL, r"corporate[-_\s]+report"),
    # Half-year / interim reports
    (ReportKind.INTERIM, r"half[-_\s]?year[-_\s]?(?:report|financial)(?:[-_\s]?(\d{4}))?"),
    (ReportKind.INTERIM, r"interim[-_\s]?(?:financial|report)(?:[-_\s]?(\d{4}))?"),
    (ReportKind.INTERIM, r"h[12][-_\s]?(\d{4})"),
    # Quarterly
    (ReportKind.QUARTERLY, r"q[1-4][-_\s]?(\d{4})"),
    (ReportKind.QUARTERLY, r"(\d{4})[-_\s]?q[1-4]"),
    (ReportKind.QUARTERLY, r"quarterly[-_\s]?(?:report|statement)?"),
    # Sustainability / ESG
    (ReportKind.SUSTAINABILITY, r"sustainab(?:le|ility)"),
    (ReportKind.SUSTAINABILITY, r"\besg\b"),
    (ReportKind.SUSTAINABILITY, r"environment(?:al)?[-_\s]+report"),
    (ReportKind.SUSTAINABILITY, r"social[-_\s]+report"),
    (ReportKind.SUSTAINABILITY, r"\bethical\b"),
    (ReportKind.SUSTAINABILITY, r"climate"),
    (ReportKind.SUSTAINABILITY, r"corporate[-_\s]+responsib(?:le|ility)"),
]

# Year fallback — reject digit adjacency only (so a glued filename like
# `annual_report2017_uk.pdf` still yields 2017), not letter adjacency.
_YEAR_FALLBACK = re.compile(r"(?<!\d)(19[9]\d|20[0-3]\d)(?!\d)")


class RuleClassifier:
    """Stateless — instantiate once, classify many."""

    name = "rules"

    def classify(self, doc: ScrapedDocument) -> ClassificationHint:
        return self._classify_text(self._haystack(doc).lower())

    def classify_from_text(self, text: str) -> ClassificationHint:
        """Run the same patterns over arbitrary text (e.g., content sniffer output)."""
        return self._classify_text(text.lower())

    def _classify_text(self, haystack: str) -> ClassificationHint:
        for kind, pattern in _KIND_PATTERNS:
            m = re.search(pattern, haystack, re.IGNORECASE)
            if not m:
                continue
            year = (
                self._year_from_match(m)
                or self._year_near(haystack, m.start(), m.end())
                or self._year_fallback(haystack)
            )
            confidence = CONFIDENCE_KIND_WITH_YEAR if year else CONFIDENCE_KIND_NO_YEAR
            return ClassificationHint(
                kind=kind,
                fiscal_year=year,
                confidence=confidence,
                matched=f"rule:{pattern}",
            )

        # No kind match — still try to get a year so downstream can decide
        year = self._year_fallback(haystack)
        if year:
            return ClassificationHint(
                kind=ReportKind.OTHER,
                fiscal_year=year,
                confidence=CONFIDENCE_YEAR_ONLY,
                matched="rule:year-only",
            )
        return ClassificationHint(
            kind=ReportKind.OTHER,
            fiscal_year=None,
            confidence=CONFIDENCE_NONE,
            matched="rule:none",
        )

    @staticmethod
    def _haystack(doc: ScrapedDocument) -> str:
        parts = [
            Path(doc.source_url).name,
            Path(doc.local_path).name,
            doc.link_text or "",
            doc.source_url,
        ]
        return " | ".join(parts)

    @staticmethod
    def _year_from_match(m: re.Match[str]) -> int | None:
        for group in m.groups():
            if group and group.isdigit():
                year = int(group)
                if is_valid_year(year):
                    return year
        return None

    @staticmethod
    def _year_fallback(haystack: str) -> int | None:
        years = [int(y) for y in _YEAR_FALLBACK.findall(haystack) if is_valid_year(int(y))]
        if not years:
            return None
        return max(years)

    @staticmethod
    def _year_near(haystack: str, start: int, end: int, *, window: int = 80) -> int | None:
        """Year nearest to a kind-match span. Cover-page text like
        '2024 Universal Registration Document' has the fiscal year right
        next to the kind heading; that's almost always the FY, even when
        a later (publication) year also appears in the document.
        """
        a = max(0, start - window)
        b = min(len(haystack), end + window)
        candidates: list[tuple[int, int]] = []
        for m in _YEAR_FALLBACK.finditer(haystack[a:b]):
            year = int(m.group(0))
            if not is_valid_year(year):
                continue
            dist = (start - m.end()) if m.end() <= start else (m.start() - end)
            candidates.append((abs(dist), year))
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][1]
