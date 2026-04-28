"""AI verification — second-pass sanity check on a `DiscoveredCompany`.

The first call (in `discover.py`) is a generative LLM lookup: it has every
incentive to make something up. `verify` runs a separate prompt that asks
the model to *evaluate* the result against the original company name, with
a structured output, and reports per-field issues.

This is cheap insurance against hallucinated tickers, mismatched currencies,
wrong-country IR URLs, and similar systematic errors. It does not replace
URL reachability checks (already in `discover`) — it complements them.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, Field

from report_discover.llm import LlmClient
from report_discover.models import DiscoveredCompany

log = structlog.get_logger()


class FieldIssue(BaseModel):
    field: str = Field(description="The DiscoveredCompany field name, e.g. 'ticker'.")
    issue: str = Field(description="One-sentence description of what's wrong.")


class Verdict(BaseModel):
    """Result of an AI verification pass.

    `ok=True` and an empty `issues` list means the verifier found nothing
    suspicious. Treat `confidence` as a soft signal, not a probability.
    """

    ok: bool = Field(description="True iff the verifier sees no issues worth surfacing.")
    confidence: float = Field(ge=0.0, le=1.0, description="0..1 — verifier's overall confidence.")
    issues: list[FieldIssue] = Field(
        default_factory=list, description="Per-field problems the verifier flagged."
    )
    summary: str = Field(default="", description="One-sentence overall judgement.")


_SYSTEM = (
    "You are an auditor reviewing automated company-discovery output. The "
    "user will give you (a) the original free-text company name they queried, "
    "and (b) a structured `DiscoveredCompany` result produced by another LLM. "
    "Your job is to spot mistakes — wrong ticker, mismatched exchange, "
    "incorrect reporting currency, an IR URL that points at a different "
    "company, hallucinated fields, slug that doesn't match the name, etc. "
    "Be skeptical but not paranoid: only flag things you have real reason to "
    "doubt. Return per-field `issues` you'd want a human to double-check, an "
    "overall `ok` boolean, and a `confidence` in 0..1. Empty `issues` is the "
    "right answer when the result looks plausible."
)


def verify(
    company: DiscoveredCompany,
    *,
    llm: LlmClient,
    original_query: str | None = None,
    model: str = "gpt-4o",
) -> Verdict:
    """Run a verifier LLM pass over a `DiscoveredCompany`.

    Args:
        company: The result of an earlier `discover()` call.
        llm: Any `LlmClient` — usually the same one used for discovery.
        original_query: The exact free-text the user typed. If omitted,
            `company.name` is used as a stand-in (less informative).
        model: Model name. Default `gpt-4o`.
    """
    query = original_query or company.name
    log.info("verify.start", query=query, slug=company.slug)

    user = f"original_query: {query!r}\ndiscovered:\n{company.model_dump_json(indent=2)}"
    verdict = llm.parse(
        model=model,
        system=_SYSTEM,
        user=user,
        schema=Verdict,
    )
    log.info(
        "verify.done",
        ok=verdict.ok,
        confidence=verdict.confidence,
        issues=len(verdict.issues),
    )
    return verdict
