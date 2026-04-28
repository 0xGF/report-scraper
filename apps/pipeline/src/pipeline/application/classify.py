"""`report-scrape classify <company>` — tag each scraped PDF with (kind, fiscal_year).

Reads the scrape manifest, classifies every entry, writes
`data/raw/{company}.classified.json`. LLM fallback is used only for docs
whose rule-based confidence is low.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from pipeline.adapters.classifier import classify_all
from pipeline.adapters.classifier.llm import LlmClassifier
from pipeline.adapters.llm import LlmClient
from pipeline.config import get_settings
from pipeline.domain.companies import get_company
from pipeline.domain.models import ClassifiedPdf
from report_scrape import ScrapedDocument

log = structlog.get_logger()


def _manifest_path(raw_dir: Path, slug: str) -> Path:
    return raw_dir / f"{slug}.manifest.json"


def _classified_path(raw_dir: Path, slug: str) -> Path:
    return raw_dir / f"{slug}.classified.json"


def _load_manifest_docs(path: Path) -> list[ScrapedDocument]:
    if not path.exists():
        raise FileNotFoundError(
            f"No scrape manifest at {path}. Run `report-scrape scrape <company>` first."
        )
    payload = json.loads(path.read_text())
    return [ScrapedDocument.model_validate(row) for row in payload.get("pdfs", [])]


def run(company_slug: str) -> None:
    settings = get_settings()
    company = get_company(company_slug)
    raw_dir = settings.report_scrape_raw_dir

    docs = _load_manifest_docs(_manifest_path(raw_dir, company_slug))
    log.info("classify.start", company=company.name, docs=len(docs))

    llm: LlmClassifier | None = None
    if settings.openai_api_key:
        client = LlmClient(
            api_key=settings.openai_api_key,
            cache_dir=settings.report_scrape_cache_dir / "llm",
        )
        if client.ping():
            llm = LlmClassifier(client=client, model=settings.openai_classifier_model)
        else:
            log.warning(
                "classify.no_llm",
                reason="OPENAI_API_KEY failed /v1/models ping; using rules only",
            )
    else:
        log.info("classify.no_llm", reason="OPENAI_API_KEY not set; using rules only")

    classified = classify_all(docs, llm=llm)

    # Force-assign company slug from the CLI arg in case labels were missing
    classified = [
        ClassifiedPdf(**{**c.model_dump(), "company_slug": company_slug}) for c in classified
    ]

    _classified_path(raw_dir, company_slug).write_text(
        json.dumps([c.model_dump() for c in classified], indent=2, sort_keys=True, default=str)
    )
    _summarize(classified)


def _summarize(classified: list[ClassifiedPdf]) -> None:
    from collections import Counter

    by_kind: Counter[str] = Counter(c.kind.value for c in classified)
    annuals = [c for c in classified if c.kind.value == "annual" and c.fiscal_year]
    years = sorted({c.fiscal_year for c in annuals if c.fiscal_year})

    log.info("classify.done", by_kind=dict(by_kind), annual_years=years, annual_count=len(annuals))
