"""Tests for the agent-based discoverer.

The agent itself isn't tested here — that requires a live LLM. What we
test is the deterministic plumbing around it:

  * `_build_executor` enforces the same-org filter on tool calls
  * `find_pdfs_with_agent` validates URLs the agent emits
  * Off-domain URLs the agent might return get dropped
  * The `years_filter` parameter restricts which picks survive
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar
from unittest.mock import patch

from pydantic import BaseModel

from report_discover._agent import (
    _build_executor,
    _DiscoveredPdf,
    _DiscoveredPdfs,
    _distil_page_for_agent,
    find_pdfs_with_agent,
)
from report_discover.models import DiscoveredCompany, DiscoveredSource

T = TypeVar("T", bound=BaseModel)

FIXTURES = Path(__file__).parent / "fixtures"


def _company(domain: str = "adyen.com") -> DiscoveredCompany:
    return DiscoveredCompany(
        found=True,
        slug="adyen",
        name="Adyen N.V.",
        ticker="ADYEN",
        exchange="AEX",
        reporting_currency="EUR",
        ir_url=f"https://www.{domain}/investor-relations",
        website=f"https://www.{domain}",
        sources=[DiscoveredSource(name="adyen:ir")],
    )


# --------------------------------------------------------------------------- #
# `_build_executor` — fetch_url, head_url, web_search dispatch
# --------------------------------------------------------------------------- #


def test_executor_fetches_pdf_metadata() -> None:
    """fetch_url returns size + content-type for a real PDF response."""
    seed = "https://www.adyen.com/investor-relations"
    executor, _cache = _build_executor(seed, web_search=None)
    with patch("report_discover._agent.httpx.Client") as mock_client:
        resp = mock_client.return_value.__enter__.return_value.get.return_value
        resp.headers = {"content-type": "application/pdf"}
        resp.content = b"%PDF-1.4..."
        resp.text = ""
        resp.status_code = 200
        out = executor("fetch_url", {"url": "https://brand.adyen.com/api/asset/abc/download"})
    assert "PDF" in out


def test_executor_does_not_gate_off_domain() -> None:
    """The agent can fetch any URL — vendor IR-platform CDNs (q4cdn,
    eqs-cockpit) host legitimate company PDFs but are off-eTLD+1, so a
    same-org gate would block them. The post-validation HEAD/GET +
    AI verify pass catch bad picks instead."""
    seed = "https://www.adyen.com/investor-relations"
    executor, _ = _build_executor(seed, web_search=None)
    with patch("report_discover._agent.httpx.Client") as mock_client:
        resp = mock_client.return_value.__enter__.return_value.get.return_value
        resp.headers = {"content-type": "application/pdf"}
        resp.content = b"%PDF-1.4..."
        resp.text = ""
        resp.status_code = 200
        out = executor("fetch_url", {"url": "https://s29.q4cdn.com/abc.pdf"})
    assert "REJECTED" not in out
    assert "PDF" in out


def test_executor_unknown_tool_returns_error() -> None:
    executor, _ = _build_executor("https://www.adyen.com/", web_search=None)
    assert executor("not_a_tool", {}).startswith("ERROR")


def test_executor_web_search_unavailable_when_unconfigured() -> None:
    """When no `web_search` backend is wired, the tool returns a clear
    error so the agent knows to fall back to navigation."""
    executor, _ = _build_executor("https://www.adyen.com/", web_search=None)
    out = executor("web_search", {"query": "Adyen annual report"})
    assert "ERROR" in out and "not configured" in out


def test_executor_sec_filings_returns_filings_when_cik_resolves() -> None:
    """When `_sec_lookup_cik` returns a CIK and `_sec_recent_filings`
    returns rows, the executor formats them as a plain-text block the
    agent can read."""
    executor, _ = _build_executor("https://investors.spotify.com/", web_search=None)
    with (
        patch("report_discover._agent._sec_lookup_cik", return_value="0001639920"),
        patch(
            "report_discover._agent._sec_recent_filings",
            return_value=[
                {
                    "form": "20-F",
                    "filing_date": "2025-02-05",
                    "url": "https://www.sec.gov/Archives/edgar/data/1639920/000163992025000003/ck0001639920-20241231.htm",
                    "primary_document": "ck0001639920-20241231.htm",
                },
                {
                    "form": "20-F",
                    "filing_date": "2024-02-08",
                    "url": "https://www.sec.gov/Archives/edgar/data/1639920/000163992024000004/ck0001639920-20231231.htm",
                    "primary_document": "ck0001639920-20231231.htm",
                },
            ],
        ),
    ):
        out = executor("sec_filings", {"ticker_or_name": "SPOT"})
    assert "CIK=0001639920" in out
    assert "20-F" in out
    assert "2025-02-05" in out
    assert "ck0001639920-20241231.htm" in out


def test_executor_sec_filings_when_company_not_on_edgar() -> None:
    """A company not on EDGAR (e.g. private European issuer) gets a
    clear 'NOT FOUND' message so the agent knows to fall back to
    web_search / fetch_url."""
    executor, _ = _build_executor("https://www.adyen.com/", web_search=None)
    with patch("report_discover._agent._sec_lookup_cik", return_value=None):
        out = executor("sec_filings", {"ticker_or_name": "PRIVATECO"})
    assert "NOT FOUND" in out
    assert "fetch_url" in out or "web_search" in out


def test_executor_web_search_dispatches_to_backend() -> None:
    """When `web_search` IS wired, query results are formatted as a
    plain-text block the agent can read."""
    from report_discover.search import SearchResult

    class _FakeSearch:
        def search(self, query: str, *, max_results: int = 8) -> list[SearchResult]:
            return [
                SearchResult(
                    url="https://s29.q4cdn.com/abc/Annual-Report-2024.pdf",
                    title="Spotify 2024 Annual Report",
                    snippet="Consolidated annual filing",
                ),
            ]

    executor, _ = _build_executor("https://investors.spotify.com/", web_search=_FakeSearch())
    out = executor("web_search", {"query": "Spotify annual report 2024 filetype:pdf"})
    assert "https://s29.q4cdn.com/abc/Annual-Report-2024.pdf" in out
    assert "Spotify 2024 Annual Report" in out


# --------------------------------------------------------------------------- #
# `find_pdfs_with_agent` — agent-loop wrapper + validation
# --------------------------------------------------------------------------- #


class _FakeAgentLlm:
    """Returns a canned `_DiscoveredPdfs` result without invoking any tool."""

    def __init__(self, picks: list[_DiscoveredPdf]) -> None:
        self.picks = picks
        self.calls: list[dict[str, Any]] = []

    def ping(self) -> bool:
        return True

    def parse(self, *, model: str, system: str, user: str, schema: type[T]) -> T:
        # Stubbed only to satisfy the LlmClient Protocol; agent path
        # exercises `run_agent`.
        raise AssertionError("parse should not be called in agent path")

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
        self.calls.append(
            {"model": model, "user": user, "tools": [t["function"]["name"] for t in tools]}
        )
        return _DiscoveredPdfs(pdfs=self.picks)  # type: ignore[return-value]


def test_agent_validates_each_returned_url() -> None:
    """The agent's URLs flow through `resolve_with_fallback`. Unreachable
    URLs get dropped without breaking the run."""
    picks = [
        _DiscoveredPdf(fiscal_year=2024, url="https://brand.adyen.com/ok.pdf", evidence="annual"),
        _DiscoveredPdf(fiscal_year=2023, url="https://brand.adyen.com/dead.pdf", evidence="annual"),
    ]
    llm = _FakeAgentLlm(picks)

    # Stub the validator: ok.pdf passes, dead.pdf fails (and so does its
    # Wayback fallback).
    def _resolve(url: str, **_: Any) -> str | None:
        return url if "ok" in url else None

    with patch("report_discover._agent.resolve_with_fallback", side_effect=_resolve):
        out = find_pdfs_with_agent(
            _company(), llm=llm, model="gpt-5", target_years=10, validate=True
        )
    assert out == ["https://brand.adyen.com/ok.pdf"]


def test_agent_keeps_off_etld1_picks() -> None:
    """The same-org filter was dropped — vendor IR-platform CDNs and
    search-grounded picks legitimately live off the company's eTLD+1
    (e.g. Spotify's PDFs at `s29.q4cdn.com/<id>/...`). Only reachability
    fails a pick now; the AI verify pass catches bad-domain mirrors."""
    picks = [
        _DiscoveredPdf(fiscal_year=2024, url="https://brand.adyen.com/ok.pdf", evidence="annual"),
        _DiscoveredPdf(
            fiscal_year=2023,
            url="https://s29.q4cdn.com/175625835/files/Annual-Report-2023.pdf",
            evidence="vendor CDN",
        ),
    ]
    llm = _FakeAgentLlm(picks)
    with patch("report_discover._agent.resolve_with_fallback", lambda url, **_: url):
        out = find_pdfs_with_agent(
            _company(), llm=llm, model="gpt-5", target_years=10, validate=True
        )
    assert sorted(out) == sorted(
        [
            "https://brand.adyen.com/ok.pdf",
            "https://s29.q4cdn.com/175625835/files/Annual-Report-2023.pdf",
        ]
    )


def test_agent_returns_newest_first() -> None:
    """Picks are ordered newest-to-oldest; `fiscal_year=None` falls last."""
    picks = [
        _DiscoveredPdf(fiscal_year=2018, url="https://brand.adyen.com/2018.pdf", evidence=""),
        _DiscoveredPdf(fiscal_year=None, url="https://brand.adyen.com/unknown.pdf", evidence=""),
        _DiscoveredPdf(fiscal_year=2024, url="https://brand.adyen.com/2024.pdf", evidence=""),
        _DiscoveredPdf(fiscal_year=2021, url="https://brand.adyen.com/2021.pdf", evidence=""),
    ]
    llm = _FakeAgentLlm(picks)
    with patch("report_discover._agent.resolve_with_fallback", lambda url, **_: url):
        out = find_pdfs_with_agent(
            _company(), llm=llm, model="gpt-5", target_years=10, validate=True
        )
    assert out == [
        "https://brand.adyen.com/2024.pdf",
        "https://brand.adyen.com/2021.pdf",
        "https://brand.adyen.com/2018.pdf",
        "https://brand.adyen.com/unknown.pdf",
    ]


def test_agent_years_filter_drops_off_target_years() -> None:
    """`years_filter` is the gap-fill use-case — anything outside is dropped."""
    picks = [
        _DiscoveredPdf(fiscal_year=2024, url="https://brand.adyen.com/2024.pdf", evidence=""),
        _DiscoveredPdf(fiscal_year=2018, url="https://brand.adyen.com/2018.pdf", evidence=""),
        _DiscoveredPdf(fiscal_year=2020, url="https://brand.adyen.com/2020.pdf", evidence=""),
    ]
    llm = _FakeAgentLlm(picks)
    with patch("report_discover._agent.resolve_with_fallback", lambda url, **_: url):
        out = find_pdfs_with_agent(
            _company(),
            llm=llm,
            model="gpt-5",
            target_years=10,
            validate=True,
            years_filter={2018, 2020},
        )
    assert sorted(out) == [
        "https://brand.adyen.com/2018.pdf",
        "https://brand.adyen.com/2020.pdf",
    ]


def test_agent_short_circuits_when_no_seed() -> None:
    """Without an IR URL or website, the agent isn't invoked."""
    co = _company().model_copy(update={"ir_url": "", "website": ""})
    out = find_pdfs_with_agent(co, llm=_FakeAgentLlm([]), model="gpt-5")
    assert out == []


# --------------------------------------------------------------------------- #
# `_distil_page_for_agent` — what the agent's `fetch_url` tool returns
# --------------------------------------------------------------------------- #


def test_distil_collapses_300kb_html_to_signal() -> None:
    """The Adyen index fixture is ~300 KB raw. After distillation the
    agent should see a few KB containing the title, all PDF anchors,
    and all year-bucket drill-down links — no script/style noise."""
    html = (FIXTURES / "adyen_financials_index.html").read_text()
    out = _distil_page_for_agent(
        html,
        page_url="https://investors.adyen.com/financials",
        seed="https://www.adyen.com/investors",
    )
    # Distilled output is dramatically smaller than raw HTML.
    assert len(out) < len(html) // 10, (
        f"distilled output should be ≪ raw HTML; got {len(out)} vs {len(html)}"
    )
    # Three named sections are always present.
    assert out.startswith("TITLE: ")
    assert "PDF LINKS (" in out
    assert "DRILL-DOWN LINKS (" in out
    # The eight bare-year drill-downs (2018..2025) all surface in the
    # drill-down section — that's the year signal the agent uses to
    # navigate.
    drill = out.split("DRILL-DOWN LINKS")[1]
    for year in (2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025):
        assert f"/financials/{year}" in drill, (
            f"distilled drill-down section missing /financials/{year}"
        )


def test_distil_year_page_yields_single_unambiguous_pdf() -> None:
    """A drill-down year page (`/financials/2024`) contains exactly one
    PDF — the canonical annual report. The distilled view must surface
    it with the year-bearing page title so the agent picks it without
    guesswork."""
    html = (FIXTURES / "adyen_financials_2024.html").read_text()
    out = _distil_page_for_agent(
        html,
        page_url="https://investors.adyen.com/financials/2024",
        seed="https://www.adyen.com/investors",
    )
    # Title carries the year — strongest signal the agent has for a
    # tokenized PDF URL.
    assert "Annual Report 2024" in out
    assert "PDF LINKS (1)" in out
    assert "brand.adyen.com/api/asset/" in out
