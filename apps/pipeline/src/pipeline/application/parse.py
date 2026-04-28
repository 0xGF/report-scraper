"""`report-scrape parse <company>` — extract IS / BS / CF tables from annual reports.

Reads `data/raw/{company}.classified.json`, dispatches each annual doc to
the right extractor (PDF or HTML), writes
`data/raw/{company}.extracted.json`.

Deduplicates by (fiscal_year, statement) — when a company has multiple
annuals for the same year (e.g. ASML's NL + UK versions), the first one
that extracts successfully wins.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from pipeline.adapters.extractor import extract_statements
from pipeline.config import get_settings
from pipeline.domain.companies import get_company
from pipeline.domain.models import ClassifiedPdf, ExtractedStatement
from pipeline.domain.types import ReportKind

log = structlog.get_logger()


def _classified_path(raw_dir: Path, slug: str) -> Path:
    return raw_dir / f"{slug}.classified.json"


def _extracted_path(raw_dir: Path, slug: str) -> Path:
    return raw_dir / f"{slug}.extracted.json"


def _rank_candidates_per_year(
    docs: list[ClassifiedPdf],
) -> dict[tuple[str, int], list[ClassifiedPdf]]:
    """For each (slug, fiscal_year), return docs ranked best-first.

    The parser tries the top-ranked PDF first; if extraction yields zero
    statements, it falls through to the next candidate. This matters when
    a year has multiple candidate PDFs (e.g. an English + a localized
    version, or a clean copy + a scanned scrape).

    Preference order (high → low):
    1. `link_text` contains "registration document" / "urd" / "form 20-f" / "annual report"
       (discriminates Dassault's URD from its glossy "Corporate Report")
    2. English-language hints in link_text / filename / URL ("uk", "eng", "english")
    3. Higher classifier confidence
    4. Tiebreaker: larger file (>8 MB preferred — URDs are 500+ pages)
    """
    grouped: dict[tuple[str, int], list[ClassifiedPdf]] = {}
    for d in docs:
        if d.kind != ReportKind.ANNUAL or not d.fiscal_year:
            continue
        grouped.setdefault((d.company_slug, d.fiscal_year), []).append(d)

    urd_hints = ("registration document", "urd", "form 20-f", "annual report")
    english_hints = ("_uk.", "-uk.", "_uk_", "uk_", "_eng", "-eng", "english")

    def score(d: ClassifiedPdf) -> tuple[int, int, float, int]:
        link = (d.link_text or "").lower()
        name = d.local_path.lower()
        url = d.source_url.lower()

        urd_hint = int(any(k in link for k in urd_hints))
        english_hint = int(
            any(k in link for k in english_hints)
            or any(k in name or k in url for k in english_hints)
        )
        size_bonus = int(d.size_bytes > 8 * 1024 * 1024)
        return (urd_hint, english_hint, d.confidence, size_bonus)

    return {key: sorted(group, key=score, reverse=True) for key, group in grouped.items()}


def run(company_slug: str) -> None:
    settings = get_settings()
    company = get_company(company_slug)
    raw_dir = settings.report_scrape_raw_dir

    cpath = _classified_path(raw_dir, company_slug)
    if not cpath.exists():
        raise FileNotFoundError(
            f"No classified file at {cpath}. Run `report-scrape classify {company_slug}` first."
        )
    classified = [ClassifiedPdf.model_validate(x) for x in json.loads(cpath.read_text())]
    log.info("parse.start", company=company.name, classified=len(classified))

    ranked = _rank_candidates_per_year(classified)
    log.info(
        "parse.candidates",
        years=sorted({fy for _, fy in ranked}),
        total_candidates=sum(len(v) for v in ranked.values()),
    )

    all_statements: list[ExtractedStatement] = []
    success = 0
    # Iterate (year → ranked PDFs) newest-first so logs read top-to-bottom.
    for (_, fy), pdfs in sorted(ranked.items(), key=lambda kv: -kv[0][1]):
        # Fall-through: try the top-ranked PDF; if extraction yields zero
        # statements, drop down to the next candidate. Prevents a single
        # malformed/scanned PDF from leaving a year empty when alternates
        # were also downloaded.
        chosen_statements: list[ExtractedStatement] = []
        chosen_doc: ClassifiedPdf | None = None
        for attempt, doc in enumerate(pdfs):
            statements = extract_statements(doc, currency=company.reporting_currency)
            if statements:
                chosen_statements = statements
                chosen_doc = doc
                if attempt > 0:
                    log.info(
                        "parse.fallthrough_recovered",
                        fy=fy,
                        attempt=attempt + 1,
                        sha=doc.sha256[:12],
                    )
                break
            log.info(
                "parse.doc_empty",
                fy=fy,
                sha=doc.sha256[:12],
                attempt=attempt + 1,
                remaining=len(pdfs) - attempt - 1,
            )
        if chosen_statements:
            success += 1
        all_statements.extend(chosen_statements)
        log.info(
            "parse.doc_done",
            fy=fy,
            sha=(chosen_doc.sha256[:12] if chosen_doc else None),
            statements=[s.statement.value for s in chosen_statements],
        )

    _extracted_path(raw_dir, company_slug).write_text(
        json.dumps(
            [s.model_dump() for s in all_statements],
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    log.info(
        "parse.done",
        company=company.name,
        years=len(ranked),
        docs_with_extractions=success,
        total_statements=len(all_statements),
    )
