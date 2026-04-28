"""Synthesize Tesla-style section headers from a flat list of normalized rows.

The normalizer flags subtotal rows (`is_total=True`) but generally doesn't
emit explicit `is_header=True` rows for sections like "Revenues" / "Operating
Expenses". We back-derive those: every section ends in a "Total <X>" subtotal,
so we walk the rows in display order, group everything before each `Total <X>`
under a synthetic header named `<X>` (capitalized), and stamp the children at
depth 1 underneath.

Grand totals (Gross Profit, Net Income, etc.) are detected by name and stand
alone at depth 0 — no section header.

This is a pure function: input rows in, transformed rows out. Tested directly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_GRAND_TOTAL_HINTS = (
    "gross profit",
    "operating profit",
    "operating income",
    "income from operations",
    "income before",
    "profit before",
    "net income",
    "net profit",
    "net loss",
    "profit for",
    "loss for",
    "profit attributable",
    "comprehensive income",
)

_TOTAL_PREFIX = re.compile(r"^total\s+(.+)$", re.IGNORECASE)


def _looks_like_grand_total(name: str) -> bool:
    lower = name.lower()
    return any(lower.startswith(hint) for hint in _GRAND_TOTAL_HINTS)


def _derive_section_label(total_name: str) -> str | None:
    m = _TOTAL_PREFIX.match(total_name.strip())
    if not m:
        return None
    rest = m.group(1).strip()
    if not rest:
        return None
    return rest[0].upper() + rest[1:]


def _synthetic_header(label: str, sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "canonical_name": label,
        "depth": 0,
        "is_header": True,
        "is_total": False,
        "display_order": sample["display_order"],
        "parent_name": None,
        "values": {},
    }


def _reindent_children(children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Force a section's children to depth >= 1 so the synthetic header at
    depth 0 visually parents them. Preserves relative indentation."""
    if not children:
        return children
    min_depth = min(c.get("depth", 0) for c in children)
    offset = 1 - min_depth
    if offset == 0:
        return children
    return [{**c, "depth": (c.get("depth", 0) or 0) + offset} for c in children]


def inject_section_headers(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inject synthetic section header rows derived from `Total <X>` subtotals.

    The input is a list of row dicts (as produced by the exporter). The output
    is a new list with synthetic headers prepended to each section. `display_order`
    is re-stamped sequentially so downstream sorts are stable.
    """
    sorted_rows = sorted(rows, key=lambda r: r.get("display_order", 0))
    out: list[dict[str, Any]] = []
    buffer: list[dict[str, Any]] = []

    def flush(section_total: dict[str, Any] | None) -> None:
        nonlocal buffer
        first = buffer[0] if buffer else None
        if section_total and first and not first.get("is_header"):
            label = _derive_section_label(section_total["canonical_name"])
            if label:
                out.append(_synthetic_header(label, first))
                out.extend(_reindent_children(buffer))
            else:
                out.extend(buffer)
        else:
            out.extend(buffer)
        buffer = []

    for row in sorted_rows:
        name = row.get("canonical_name", "")
        is_total = bool(row.get("is_total"))

        # Grand-total rows stand alone at depth 0.
        if is_total and _looks_like_grand_total(name):
            flush(None)
            out.append({**row, "depth": 0})
            continue

        # "Total <X>" subtotal — flush buffered section under header "<X>".
        if is_total and _TOTAL_PREFIX.match(name):
            flush(row)
            out.append({**row, "depth": 1})
            continue

        buffer.append(row)

    flush(None)

    # Re-stamp display_order for stable downstream sorting.
    for i, row in enumerate(out):
        row["display_order"] = i
    return out
