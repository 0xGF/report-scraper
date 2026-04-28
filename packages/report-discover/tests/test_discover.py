"""Tests for the discover/verify pipeline using a fake LLM client."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from report_discover import (
    DiscoveredCompany,
    DiscoveredSource,
    Verdict,
    discover,
    verify,
)

T = TypeVar("T", bound=BaseModel)


class _FakeLlm:
    """Returns a pre-baked instance for whatever schema is asked."""

    def __init__(self, responses: dict[type[BaseModel], BaseModel]) -> None:
        self._responses = responses

    def ping(self) -> bool:
        return True

    def parse(self, *, model: str, system: str, user: str, schema: type[T]) -> T:
        if schema not in self._responses:
            raise AssertionError(f"FakeLlm has no canned response for {schema.__name__}")
        return self._responses[schema]  # type: ignore[return-value]

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
        # The discover-flow tests don't exercise the agent path; this
        # stub just needs to satisfy the AgentLlmClient Protocol so
        # mypy's structural check passes.
        raise AssertionError("FakeLlm.run_agent should not be called in these tests")


def _heineken_company(found: bool = True) -> DiscoveredCompany:
    return DiscoveredCompany(
        found=found,
        slug="heineken",
        name="Heineken N.V.",
        ticker="HEIA",
        exchange="AEX",
        reporting_currency="EUR",
        ir_url="https://www.theheinekencompany.com/investors/results-and-reports",
        sources=[
            DiscoveredSource(
                name="heineken:annual-reports",
                entry_urls=["https://www.theheinekencompany.com/investors/results-and-reports"],
            )
        ],
    )


def test_discover_returns_low_confidence_without_url_check() -> None:
    """`found=False` short-circuits the URL validator."""
    canned = _heineken_company(found=False)
    llm = _FakeLlm({DiscoveredCompany: canned})

    result = discover("Heineken", llm=llm, validate_url=True)

    assert result.found is False
    assert result.slug == "heineken"


def test_discover_skips_url_check_when_disabled() -> None:
    """validate_url=False returns the raw LLM result untouched."""
    canned = _heineken_company(found=True)
    llm = _FakeLlm({DiscoveredCompany: canned})

    result = discover("Heineken", llm=llm, validate_url=False)

    assert result.found is True
    assert result.ir_url.endswith("/results-and-reports")


def test_discover_marks_unreachable_url_as_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 on the IR URL flips found→False and writes a notes hint."""
    canned = _heineken_company(found=True)
    llm = _FakeLlm({DiscoveredCompany: canned})

    monkeypatch.setattr(
        "report_discover._discover._check_url",
        lambda url, *, timeout: (False, 404),
    )

    result = discover("Heineken", llm=llm, validate_url=True)

    assert result.found is False
    assert "404" in result.notes
    # The original IR URL is preserved so the caller can debug.
    assert result.ir_url.endswith("/results-and-reports")


def test_verify_passes_through_canned_verdict() -> None:
    """`verify` is a single LLM call that returns a Verdict — exercise the wiring."""
    company = _heineken_company()
    canned_verdict = Verdict(ok=True, confidence=0.9, issues=[], summary="Looks right.")
    llm = _FakeLlm({Verdict: canned_verdict})

    verdict = verify(company, llm=llm, original_query="Heineken")

    assert verdict.ok is True
    assert verdict.confidence == pytest.approx(0.9)
    assert verdict.issues == []
