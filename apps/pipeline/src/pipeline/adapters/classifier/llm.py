"""LLM fallback classifier — only invoked for ambiguous docs.

Uses the shared `LlmClient` with a tight structured-output schema. Cached
on disk so re-runs don't re-spend tokens.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from pipeline.adapters.llm import LlmClient
from pipeline.domain.types import ReportKind
from report_scrape import ScrapedDocument


class _Response(BaseModel):
    kind: ReportKind
    fiscal_year: int | None = Field(
        default=None,
        description="Calendar year the report covers. Null if not determinable.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(description="One short sentence explaining the classification.")


_SYSTEM = (
    "You classify corporate investor-relations documents by report type and fiscal year. "
    "You never invent years — if the evidence is insufficient, return null for fiscal_year. "
    "Types: annual, interim, quarterly, sustainability, other. "
    "'annual' covers: annual report, integrated report, universal registration "
    "document, Form 20-F, Form 10-K. "
    "'interim' covers: half-year and semi-annual reports. "
    "Return the most recent fiscal year the document reports on, not the filing date."
)


class LlmClassifier:
    name = "llm"

    def __init__(self, client: LlmClient, model: str) -> None:
        self._client = client
        self._model = model

    def classify(self, doc: ScrapedDocument) -> _Response:
        user = (
            f"source_url: {doc.source_url}\n"
            f"local_filename: {doc.local_path.rsplit('/', 1)[-1]}\n"
            f"link_text: {doc.link_text or '(none)'}\n"
            f"content_type: {doc.content_type}\n"
            f"size_bytes: {doc.size_bytes}"
        )
        return self._client.parse(
            model=self._model,
            system=_SYSTEM,
            user=user,
            schema=_Response,
        )
