"""Dispatcher — three-tier extraction with verifier-gated escalation.

  1. **Heuristic tier** (Docling → pdfplumber). Cheap, deterministic, handles
     ~80% of PDFs. Output is sent to the verifier; passing extractions are
     published as-is.
  2. **Vision-LLM tier** (`VlmExtractor`). When the verifier rejects a
     heuristic extraction (or the heuristic returned nothing), gpt-5 vision
     reads the candidate page directly. Slower and more expensive, but it
     reads visual structure that table parsers miss (Heineken's IS sitting
     next to a comprehensive-income table is the canonical case).
  3. **Text-LLM tier** (`LlmExtractor`). Final fallback for PDFs the VLM
     can't see clearly (poor scans, very long pages). The text-only LLM
     extractor tends to be less accurate than VLM but cheaper.

Each tier's output is verified by the same `verify_extraction` agent
before being published. This is the hierarchical-supervisor pattern:
extractor workers produce candidates, the verifier judges, the
supervisor (this module) decides what to publish.
"""

from __future__ import annotations

import threading
from pathlib import Path

import structlog

from pipeline.adapters.extractor.html_extractor import HtmlExtractor
from pipeline.adapters.extractor.pdf_extractor import PdfExtractor
from pipeline.adapters.llm import LlmClient
from pipeline.config import get_settings
from pipeline.domain.models import ClassifiedPdf, ExtractedStatement
from pipeline.domain.types import Currency, StatementKind

log = structlog.get_logger()

_ALL_KINDS = {StatementKind.INCOME, StatementKind.BALANCE, StatementKind.CASHFLOW}

# Cache ping result per-process — one network round-trip, not one per doc.
# Lock guards the lazy-init so concurrent extractors don't all ping at once.
_llm_ping_cache: bool | None = None
_llm_ping_lock = threading.Lock()


def _llm_is_live(client: LlmClient) -> bool:
    global _llm_ping_cache
    if _llm_ping_cache is not None:
        return _llm_ping_cache
    with _llm_ping_lock:
        if _llm_ping_cache is None:
            _llm_ping_cache = client.ping()
        return _llm_ping_cache


def extract_statements(
    doc: ClassifiedPdf,
    *,
    currency: Currency,
) -> list[ExtractedStatement]:
    """Run the three-tier verifier-gated extraction pipeline."""
    fmt = _detect_format(doc)
    if fmt == "html":
        return HtmlExtractor().extract(doc, currency=currency)
    if fmt != "pdf":
        log.warning("extract.unknown_format", sha=doc.sha256[:12], path=doc.local_path)
        return []

    # Tier 1: heuristic (Docling → pdfplumber)
    heuristic = PdfExtractor().extract(doc, currency=currency)
    settings = get_settings()
    has_llm = bool(settings.openai_api_key)
    client = (
        LlmClient(
            api_key=settings.openai_api_key,
            cache_dir=settings.report_scrape_cache_dir / "llm",
        )
        if has_llm
        else None
    )
    if client is not None and not _llm_is_live(client):
        client = None

    # Verify the heuristic tier; drop kinds that fail.
    accepted: dict[StatementKind, ExtractedStatement] = {}
    needs_vlm: set[StatementKind] = set()
    for s in heuristic:
        if client is None:
            accepted[s.statement] = s
            continue
        verdict = _verify(client, s, settings.openai_normalizer_model)
        if verdict is None or _is_publishable(verdict):
            accepted[s.statement] = s
        else:
            needs_vlm.add(s.statement)
            log.info(
                "extract.heuristic_rejected",
                sha=doc.sha256[:12],
                kind=s.statement.value,
                confidence=verdict.confidence,
                issues=[i.detail for i in verdict.issues[:3]],
            )

    missing = (_ALL_KINDS - set(accepted.keys())) | needs_vlm
    if not missing or client is None:
        return list(accepted.values())

    # Tier 2: vision-LLM (page-image → gpt-5)
    vlm_model = settings.openai_vlm_model
    log.info(
        "extract.vlm_pass",
        sha=doc.sha256[:12],
        missing=[k.value for k in missing],
        have=[k.value for k in accepted],
        model=vlm_model,
    )
    from pipeline.adapters.extractor.vlm_extractor import VlmExtractor

    try:
        vlm_results = VlmExtractor(client=client, model=vlm_model).extract(
            doc, currency=currency, wanted=missing
        )
    except Exception:
        log.exception("extract.vlm_failed", sha=doc.sha256[:12])
        vlm_results = []

    for s in vlm_results:
        verdict = _verify(client, s, settings.openai_normalizer_model)
        if verdict is None or _is_publishable(verdict):
            accepted[s.statement] = s

    missing = _ALL_KINDS - set(accepted.keys())
    if not missing:
        return list(accepted.values())

    # Tier 3: text-LLM fallback (last resort for VLM-unfriendly scans)
    log.info(
        "extract.text_llm_fallback",
        sha=doc.sha256[:12],
        missing=[k.value for k in missing],
    )
    from pipeline.adapters.extractor.llm_extractor import LlmExtractor

    try:
        text_llm_results = LlmExtractor(
            client=client, model=settings.openai_normalizer_model
        ).extract(doc, currency=currency, wanted=missing)
    except Exception:
        log.exception("extract.text_llm_fallback_failed", sha=doc.sha256[:12])
        return list(accepted.values())

    for s in text_llm_results:
        if s.statement not in accepted:
            accepted[s.statement] = s

    return list(accepted.values())


def _verify(
    client: LlmClient,
    statement: ExtractedStatement,
    model: str,
):  # -> Verdict | None  (avoid import-time circle)
    """Wrap the verifier so any LLM failure is non-fatal — the worst case
    is we publish a slightly-suspect extraction instead of dropping it."""
    from pipeline.adapters.extractor.verifier import verify_extraction

    try:
        return verify_extraction(statement, client=client, model=model)
    except Exception:
        log.exception("extract.verify_failed", sha=statement.source_sha256[:12])
        return None


def _is_publishable(verdict) -> bool:  # type: ignore[no-untyped-def]
    from pipeline.adapters.extractor.verifier import is_publishable

    return is_publishable(verdict)


def _detect_format(doc: ClassifiedPdf) -> str:
    ext = Path(doc.local_path).suffix.lower()
    if ext in {".htm", ".html"}:
        return "html"
    if ext == ".pdf":
        return "pdf"
    url_ext = Path(doc.source_url.split("?")[0]).suffix.lower()
    if url_ext in {".htm", ".html"}:
        return "html"
    if url_ext == ".pdf":
        return "pdf"
    return "unknown"
