"""Two-tier classification: rules first, LLM only where confidence is low."""

from __future__ import annotations

import structlog

from pipeline.adapters.classifier.constants import LLM_ESCALATION_THRESHOLD
from pipeline.adapters.classifier.content_sniffer import sniff
from pipeline.adapters.classifier.llm import LlmClassifier
from pipeline.adapters.classifier.rules import RuleClassifier
from pipeline.domain.companies import company_slug_from_label
from pipeline.domain.models import ClassifiedPdf
from report_scrape import ScrapedDocument

log = structlog.get_logger()


def classify_all(
    docs: list[ScrapedDocument],
    *,
    llm: LlmClassifier | None = None,
) -> list[ClassifiedPdf]:
    rules = RuleClassifier()
    out: list[ClassifiedPdf] = []
    llm_calls = 0

    sniff_hits = 0

    for doc in docs:
        hint = rules.classify(doc)
        classified_by = rules.name
        kind = hint.kind
        fiscal_year = hint.fiscal_year
        confidence = hint.confidence

        # Try content sniffing before paying for an LLM call.
        if (confidence < LLM_ESCALATION_THRESHOLD or fiscal_year is None) and doc.local_path:
            sniffed = sniff(doc.local_path, doc.content_type)
            if sniffed:
                alt = rules.classify_from_text(sniffed)
                # Accept the alt if it resolves kind OR fills in a missing year
                if (alt.confidence > confidence) or (fiscal_year is None and alt.fiscal_year):
                    kind = alt.kind if alt.confidence > confidence else kind
                    fiscal_year = alt.fiscal_year or fiscal_year
                    confidence = max(confidence, alt.confidence)
                    classified_by = f"{rules.name}+sniff"
                    sniff_hits += 1

        if confidence < LLM_ESCALATION_THRESHOLD and llm is not None:
            try:
                resp = llm.classify(doc)
                kind = resp.kind
                fiscal_year = resp.fiscal_year
                confidence = resp.confidence
                classified_by = llm.name
                llm_calls += 1
            except Exception as exc:
                # Resilience boundary — one bad doc shouldn't kill the batch.
                # Keep the rule-based hint, log full context for triage.
                log.exception(
                    "classify.llm_failed",
                    sha=doc.sha256[:12],
                    url=doc.source_url,
                    error_type=type(exc).__name__,
                    error=str(exc)[:200],
                )

        out.append(
            ClassifiedPdf(
                sha256=doc.sha256,
                source_url=doc.source_url,
                local_path=doc.local_path,
                company_slug=company_slug_from_label(doc.label),
                kind=kind,
                fiscal_year=fiscal_year,
                confidence=confidence,
                classified_by=classified_by,
                link_text=doc.link_text,
                size_bytes=doc.size_bytes,
            )
        )

    log.info("classify.done", docs=len(docs), sniff_hits=sniff_hits, llm_calls=llm_calls)
    return out
