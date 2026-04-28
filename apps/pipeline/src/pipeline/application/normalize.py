"""`report-scrape normalize <company>` — canonicalize line items across reports.

Input precedence:
  1. `data/raw/{company}.confirmed.json` — emitted by `report-scrape confirm`
     (LLM-validated rows, hierarchy attached, dropped junk). Preferred.
  2. `data/raw/{company}.extracted.json` — raw parser output. Fallback.

Approach:
1. Group all statements for this company by statement kind.
2. Pick the LATEST healthy report — its row order defines the canonical
   taxonomy (canonical_name, display_order) and the hierarchy.
3. Map every report's rows to the canonical taxonomy:
   - Exact lowercase match wins.
   - Fuzzy match (whitespace + punctuation + footnote-marker stripped)
     covers labels that drift slightly across years.
4. Every cell gets provenance — `source_fiscal_year` + `is_restated`.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

from pipeline.adapters.llm import LlmClient
from pipeline.config import get_settings
from pipeline.domain.companies import get_company
from pipeline.domain.models import ExtractedStatement, FactValue, LineItem, RawLineItem
from pipeline.domain.types import Currency, StatementKind, Unit

log = structlog.get_logger()

_ZWSP = "\u200b"

# Control / format characters and stray bullet markers that leak in from
# PDF/HTML extraction and corrupt label matching across years.
_LABEL_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202f]")

_TOTAL_RE = re.compile(
    r"^\s*(total|gross profit|gross margin|operating (income|profit|loss|margin)|"
    r"net (income|profit|loss|cash|revenue|revenues|sales)|"
    r"income before|earnings before|profit before|"
    r"cash .*? (?:at|end of) (?:year|period|the period)|"
    r"(?:net )?cash (?:provided by|used in))\b",
    re.IGNORECASE,
)

# Trailing footnote markers — `1`, `(2)`, `(a)`, `*`, `**`. We cap digit
# length at 2 so labels that legitimately end in a year (rare in row
# labels but possible in section headers like "Cash at year-end 2024")
# don't get truncated.
_TRAIL_NOTE = re.compile(r"\s*\(?\d{1,2}\)?$|\s*\([a-z]\)$|\s*\*+$", re.IGNORECASE)


def _confirmed_path(raw_dir: Path, slug: str) -> Path:
    return raw_dir / f"{slug}.confirmed.json"


def _extracted_path(raw_dir: Path, slug: str) -> Path:
    return raw_dir / f"{slug}.extracted.json"


def _normalized_path(raw_dir: Path, slug: str) -> Path:
    return raw_dir / f"{slug}.normalized.json"


def _clean_label(label: str) -> str:
    """Strip ZWSP, control chars, and trailing footnote markers (`1`, `*`)
    so the same conceptual label normalizes the same way across reports.
    """
    s = _LABEL_CONTROL.sub("", label)
    s = " ".join(s.split())
    s = _TRAIL_NOTE.sub("", s).strip()
    return s


def _fuzzy_key(label: str) -> str:
    """Aggressive normalization — for cross-report label matching."""
    s = _clean_label(label).lower()
    s = _TRAIL_NOTE.sub("", s).strip()
    # Strip punctuation that's commonly inconsistent
    s = re.sub(r"[,.;:'\u2019\u2018\u201c\u201d]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _leading_indent(label: str) -> int:
    count = 0
    for ch in label.replace(_ZWSP, ""):
        if ch == " ":
            count += 1
        elif ch == "\t":
            count += 4
        else:
            break
    return count


_HEALTHY_MIN_ROWS = 8


def _has_enough_rows(rows: list[RawLineItem]) -> bool:
    return len(rows) >= _HEALTHY_MIN_ROWS


def _is_total(label: str) -> bool:
    cleaned = _clean_label(label)
    if not cleaned:
        return False
    alpha = [c for c in cleaned if c.isalpha()]
    if alpha and all(c.isupper() for c in alpha) and len(alpha) >= 3:
        return True
    return bool(_TOTAL_RE.match(cleaned))


def _has_any_value(row: RawLineItem) -> bool:
    return any(v is not None for v in row.values_by_period_end.values())


@dataclass
class _Hierarchy:
    depth: int
    parent_name: str | None
    is_header: bool
    is_total: bool


def _infer_hierarchy(rows: list[RawLineItem]) -> list[_Hierarchy]:
    stack: list[str] = []
    out: list[_Hierarchy] = []

    for row in rows:
        label_clean = _clean_label(row.raw_label)
        indent = _leading_indent(row.raw_label)
        is_header = not _has_any_value(row)
        is_total = _is_total(label_clean)

        if is_header:
            depth = len(stack)
            parent = stack[-1] if stack else None
            stack.append(label_clean)
        else:
            depth = len(stack) + (1 if indent >= 2 else 0)
            parent = stack[-1] if stack else None

        out.append(
            _Hierarchy(
                depth=depth,
                parent_name=parent,
                is_header=is_header,
                is_total=is_total,
            )
        )

        if is_total and stack and not is_header:
            stack.pop()

    return out


# ---- Input adapter --------------------------------------------------------


@dataclass
class _ConfirmedStatement:
    """Common shape used internally — built either from the confirmed JSON
    (LLM-tagged hierarchy) or the extracted JSON (heuristic hierarchy).
    """

    company_slug: str
    source_sha256: str
    source_fiscal_year: int
    statement: StatementKind
    currency: Currency
    unit: Unit
    period_months: int
    rows: list[dict[str, Any]]  # each: {label, values, depth, parent_label, is_header, is_total}


def _load_inputs(raw_dir: Path, slug: str) -> tuple[list[_ConfirmedStatement], str]:
    """Prefer confirmed.json, fall back to extracted.json. Returns the
    statements and which source they came from (for log clarity).
    """
    cpath = _confirmed_path(raw_dir, slug)
    if cpath.exists():
        return _load_from_confirmed(cpath), "confirmed"
    epath = _extracted_path(raw_dir, slug)
    if not epath.exists():
        raise FileNotFoundError(
            f"Neither {cpath} nor {epath} exists. Run `report-scrape parse {slug}` first."
        )
    return _load_from_extracted(epath), "extracted"


def _load_from_confirmed(path: Path) -> list[_ConfirmedStatement]:
    payload = json.loads(path.read_text())
    out: list[_ConfirmedStatement] = []
    for s in payload:
        kind = StatementKind(s["statement"])
        out.append(
            _ConfirmedStatement(
                company_slug=s["company_slug"],
                source_sha256=s["source_sha256"],
                source_fiscal_year=int(s["source_fiscal_year"]),
                statement=kind,
                currency=Currency(s["currency"]),
                unit=Unit(s["unit"]),
                period_months=int(s.get("period_months", 12)),
                rows=list(s["rows"]),
            )
        )
    return out


def _load_from_extracted(path: Path) -> list[_ConfirmedStatement]:
    payload = [ExtractedStatement.model_validate(x) for x in json.loads(path.read_text())]
    out: list[_ConfirmedStatement] = []
    for s in payload:
        # Apply the heuristic hierarchy here so the rest of normalize is
        # input-source-agnostic.
        h = _infer_hierarchy(s.rows)
        rows = []
        for row, hi in zip(s.rows, h, strict=True):
            rows.append(
                {
                    "label": _clean_label(row.raw_label),
                    "values_by_period_end": {
                        k: (str(v) if isinstance(v, Decimal) else v)
                        for k, v in row.values_by_period_end.items()
                    },
                    "depth": hi.depth,
                    "parent_label": hi.parent_name,
                    "is_header": hi.is_header,
                    "is_total": hi.is_total,
                }
            )
        out.append(
            _ConfirmedStatement(
                company_slug=s.company_slug,
                source_sha256=s.source_sha256,
                source_fiscal_year=s.source_fiscal_year,
                statement=s.statement,
                currency=s.currency,
                unit=s.unit,
                period_months=s.period_months,
                rows=rows,
            )
        )
    return out


# ---- Pipeline -------------------------------------------------------------


def run(company_slug: str) -> None:
    settings = get_settings()
    company = get_company(company_slug)
    raw_dir = settings.report_scrape_raw_dir

    statements, source = _load_inputs(raw_dir, company_slug)
    log.info(
        "normalize.start",
        company=company.name,
        statements=len(statements),
        source=source,
    )

    by_kind: dict[StatementKind, list[_ConfirmedStatement]] = defaultdict(list)
    for s in statements:
        by_kind[s.statement].append(s)

    all_line_items: list[LineItem] = []
    all_facts: list[FactValue] = []

    for kind, group in by_kind.items():
        group.sort(key=lambda s: s.source_fiscal_year, reverse=True)
        latest = next(
            (s for s in group if len(s.rows) >= _HEALTHY_MIN_ROWS),
            group[0],
        )

        # Build canonical taxonomy from the latest healthy report.
        canonical: dict[str, LineItem] = {}  # exact-lowercase key → LineItem
        fuzzy_to_exact: dict[str, str] = {}  # fuzzy key → exact key
        ordered_exact: list[str] = []

        for idx, row in enumerate(latest.rows):
            label = _clean_label(row["label"])
            if not label:
                continue
            exact_key = label.lower()
            if exact_key in canonical:
                continue
            li = LineItem(
                company_slug=company_slug,
                statement=kind,
                canonical_name=label,
                depth=int(row.get("depth", 0)),
                parent_name=row.get("parent_label"),
                is_header=bool(row.get("is_header")),
                is_total=bool(row.get("is_total")),
                display_order=idx,
            )
            canonical[exact_key] = li
            ordered_exact.append(exact_key)
            fuzzy_to_exact.setdefault(_fuzzy_key(label), exact_key)

        all_line_items.extend(canonical[k] for k in ordered_exact)

        # Collect labels that DON'T match exact/fuzzy. These are the "drift"
        # candidates that an LLM canonicalizer can resolve in one batch.
        drift_unmatched: set[str] = set()
        for report in group:
            for row in report.rows:
                label = _clean_label(row["label"])
                if not label:
                    continue
                exact = label.lower()
                if exact in canonical:
                    continue
                if _fuzzy_key(label) in fuzzy_to_exact:
                    continue
                drift_unmatched.add(label)

        llm_map: dict[str, str] = {}
        if drift_unmatched:
            llm_map = _llm_canonicalize_labels(
                canonical_names=[canonical[k].canonical_name for k in ordered_exact],
                unmatched=sorted(drift_unmatched),
                statement_kind=kind.value,
                company_name=company.name,
            )
            log.info(
                "normalize.llm_drift_map",
                statement=kind.value,
                unmatched=len(drift_unmatched),
                resolved=len(llm_map),
            )

        match_stats = {"exact": 0, "fuzzy": 0, "llm": 0, "missed": 0}
        for report in group:
            for row in report.rows:
                label = _clean_label(row["label"])
                if not label:
                    continue
                exact = label.lower()
                target_exact: str | None = None
                if exact in canonical:
                    target_exact = exact
                    match_stats["exact"] += 1
                else:
                    fkey = _fuzzy_key(label)
                    candidate = fuzzy_to_exact.get(fkey)
                    if candidate is not None:
                        target_exact = candidate
                        match_stats["fuzzy"] += 1
                    elif label.lower() in llm_map:
                        target_exact = llm_map[label.lower()].lower()
                        match_stats["llm"] += 1
                    else:
                        match_stats["missed"] += 1

                if target_exact is None:
                    continue

                target_li = canonical[target_exact]
                for period_iso, raw_value in row["values_by_period_end"].items():
                    try:
                        period_end = date.fromisoformat(period_iso)
                    except ValueError:
                        continue
                    parsed_value = _coerce_value(raw_value)
                    is_restated = period_end.year < report.source_fiscal_year
                    all_facts.append(
                        FactValue(
                            company_slug=company_slug,
                            statement=kind,
                            canonical_name=target_li.canonical_name,
                            period_end=period_end,
                            period_months=report.period_months,
                            value=parsed_value,
                            currency=report.currency,
                            unit=report.unit,
                            source_sha256=report.source_sha256,
                            source_fiscal_year=report.source_fiscal_year,
                            is_restated=is_restated,
                        )
                    )

        log.info(
            "normalize.statement_done",
            company=company_slug,
            statement=kind.value,
            reports=len(group),
            line_items=len(canonical),
            headers=sum(1 for li in canonical.values() if li.is_header),
            totals=sum(1 for li in canonical.values() if li.is_total),
            **match_stats,
        )

    _normalized_path(raw_dir, company_slug).write_text(
        json.dumps(
            {
                "line_items": [li.model_dump() for li in all_line_items],
                "facts": [f.model_dump() for f in all_facts],
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    log.info(
        "normalize.done",
        company=company.name,
        line_items=len(all_line_items),
        facts=len(all_facts),
    )


def _llm_canonicalize_labels(
    *,
    canonical_names: list[str],
    unmatched: list[str],
    statement_kind: str,
    company_name: str,
) -> dict[str, str]:
    """Map drift-y old-report labels to the latest report's canonical taxonomy.

    Labels like 'Software & support' and 'Software licenses and support' refer
    to the same line item but won't match by exact-or-fuzzy. The LLM clusters
    them given the canonical list. Returns `{old_label: canonical_name}`. If
    the LLM is unavailable, returns `{}` (caller falls back to drop-on-miss).
    """
    if not unmatched or not canonical_names:
        return {}

    settings = get_settings()
    if not settings.openai_api_key:
        return {}

    from pydantic import BaseModel, Field

    class _Mapping(BaseModel):
        old_label: str = Field(description="Label as it appeared in an older report.")
        canonical_label: str | None = Field(
            description=(
                "Best match from the canonical list. Null if there is no good "
                "match — the line is unique to this older report."
            ),
        )

    class _Result(BaseModel):
        mappings: list[_Mapping]

    client = LlmClient(
        api_key=settings.openai_api_key,
        cache_dir=settings.report_scrape_cache_dir / "llm",
    )
    if not client.ping():
        return {}

    canonical_block = "\n".join(f"- {n}" for n in canonical_names)
    unmatched_block = "\n".join(f"- {label}" for label in unmatched)
    system = (
        "You map line-item labels from older annual reports onto a canonical "
        "taxonomy taken from the company's latest report. Two labels match if "
        "they describe the same financial line item (e.g. 'Software & support' "
        "and 'Software licenses and support'). When in doubt, leave it null — "
        "wrong matches pollute the table.\n"
        "Rules:\n"
        "- Only return a canonical_label if it appears VERBATIM in the supplied "
        "canonical list.\n"
        "- Return null when the old label has no clear counterpart, when the "
        "old label is a section header, or when it's a different concept."
    )
    user = (
        f"company: {company_name}\n"
        f"statement: {statement_kind}\n\n"
        f"Canonical taxonomy (latest report):\n{canonical_block}\n\n"
        f"Old labels needing mapping:\n{unmatched_block}"
    )
    try:
        result = client.parse(
            model=settings.openai_drift_model,
            system=system,
            user=user,
            schema=_Result,
        )
    except Exception:
        log.exception("normalize.llm_canonicalize_failed")
        return {}

    canonical_lower = {n.lower(): n for n in canonical_names}
    out: dict[str, str] = {}
    for m in result.mappings:
        if m.canonical_label is None:
            continue
        # Defend against the LLM ignoring our verbatim-only rule.
        canon = canonical_lower.get(m.canonical_label.lower())
        if canon is None:
            continue
        out[m.old_label.lower()] = canon
    return out


def _coerce_value(raw: Any) -> Decimal | None:
    """Parse the LLM-string-or-Decimal-or-None value into a Decimal."""
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    # Reuse the parse_number used at extraction time for symmetry.
    from pipeline.adapters.extractor.common import parse_number

    return parse_number(s)
