"""Tests for `research_gaps` — targeted gap-fill discovery.

`research_gaps` runs the same agent loop as `find_pdf_urls`, but
narrows the prompt to an explicit list of missing fiscal years and
drops any pick outside that set. We verify the year-filter behavior
with a mocked agent client.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar
from unittest.mock import patch

from pydantic import BaseModel

from report_discover._agent import _DiscoveredPdf, _DiscoveredPdfs
from report_discover._research import research_gaps
from report_discover.models import DiscoveredCompany, DiscoveredSource

T = TypeVar("T", bound=BaseModel)


def _company() -> DiscoveredCompany:
    return DiscoveredCompany(
        found=True,
        slug="adyen",
        name="Adyen N.V.",
        ticker="ADYEN",
        exchange="AEX",
        reporting_currency="EUR",
        ir_url="https://investors.adyen.com/financials",
        website="https://www.adyen.com",
        sources=[DiscoveredSource(name="adyen:ir")],
    )


class _FakeAgentLlm:
    def __init__(self, picks: list[_DiscoveredPdf]) -> None:
        self._picks = picks

    def ping(self) -> bool:
        return True

    def parse(self, *, model: str, system: str, user: str, schema: type[T]) -> T:
        raise AssertionError("research_gaps doesn't use parse")

    def run_agent(
        self,
        *,
        model: str,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        executor: Callable[[str, dict[str, Any]], str],
        schema: type[T],
        max_iterations: int = 15,
    ) -> T:
        return _DiscoveredPdfs(pdfs=self._picks)  # type: ignore[return-value]


def test_research_gaps_returns_only_requested_years() -> None:
    """The agent might return PDFs for years outside the missing set
    (e.g. it picks 2018 because that's what the page lists). The
    filter drops those — caller's existing scrape pool stays
    untouched."""
    # Agent returns three picks: 2018 (already covered), 2017 (asked),
    # 2016 (asked). Filter should drop 2018 only.
    picks = [
        _DiscoveredPdf(fiscal_year=2018, url="https://brand.adyen.com/2018.pdf", evidence=""),
        _DiscoveredPdf(fiscal_year=2017, url="https://brand.adyen.com/2017.pdf", evidence=""),
        _DiscoveredPdf(fiscal_year=2016, url="https://brand.adyen.com/2016.pdf", evidence=""),
    ]
    with patch(
        "report_discover._agent.resolve_with_fallback",
        side_effect=lambda url, **_: url,
    ):
        out = research_gaps(
            _company(),
            missing_years=[2017, 2016],
            llm=_FakeAgentLlm(picks),
            model="gpt-4o",
        )
    assert sorted(out) == [
        "https://brand.adyen.com/2016.pdf",
        "https://brand.adyen.com/2017.pdf",
    ]


def test_research_gaps_returns_empty_for_empty_missing_list() -> None:
    """No missing years → nothing to fill, no agent call."""
    out = research_gaps(
        _company(),
        missing_years=[],
        llm=_FakeAgentLlm([]),
        model="gpt-4o",
    )
    assert out == []


def test_research_gaps_widens_year_range_to_include_oldest_missing() -> None:
    """Caller may ask for very old years (e.g. 2010 when today is 2026).
    The agent's rolling target_years window is widened so the year is
    in scope, but the explicit `years_filter` keeps the picks tight."""
    picks = [
        _DiscoveredPdf(fiscal_year=2010, url="https://brand.adyen.com/2010.pdf", evidence=""),
    ]
    with patch(
        "report_discover._agent.resolve_with_fallback",
        side_effect=lambda url, **_: url,
    ):
        out = research_gaps(
            _company(),
            missing_years=[2010],
            llm=_FakeAgentLlm(picks),
            model="gpt-4o",
        )
    assert out == ["https://brand.adyen.com/2010.pdf"]
