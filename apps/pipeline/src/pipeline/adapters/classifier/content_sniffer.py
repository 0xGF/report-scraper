"""First-N-chars text extractor for classification.

Opens a downloaded document and returns up to ~2KB of visible text from the
front matter. That's enough to see "ANNUAL REPORT 2022" / "Exercise 2022"
style headings that resolve classification ambiguity when filenames and
link text are opaque (Dassault's /static-files/{uuid} pattern, SAP's
older 20-F accession-style names).

Kept small and lazy — only invoked when rule confidence is low, and never
reads the full document.
"""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger()

_MAX_CHARS = 2000
_HTML_READ_BYTES = 200_000  # read up to 200KB of HTML before parsing


def sniff(local_path: str, content_type: str) -> str:
    path = Path(local_path)
    if not path.exists():
        return ""
    ext = path.suffix.lower()
    ct = content_type.split(";")[0].strip().lower()
    try:
        if ext == ".pdf" or ct == "application/pdf":
            return _sniff_pdf(path)
        if ext in {".htm", ".html"} or ct in {"text/html", "application/xhtml+xml"}:
            return _sniff_html(path)
    except Exception:
        log.exception("classify.sniff_failed", path=str(path))
    return ""


def _sniff_pdf(path: Path) -> str:
    import pdfplumber

    with pdfplumber.open(path) as doc:
        # Metadata first — designed covers often have empty extractable text,
        # but the Title/Subject/Keywords fields usually contain the year.
        meta = doc.metadata or {}
        meta_text = " ".join(
            str(v) for v in (meta.get("Title"), meta.get("Subject"), meta.get("Keywords")) if v
        )

        chunks: list[str] = [meta_text]
        for page in doc.pages[:3]:
            text = page.extract_text() or ""
            chunks.append(text)
            if sum(len(c) for c in chunks) >= _MAX_CHARS:
                break
    joined = " ".join(" ".join(chunks).split())
    return joined[:_MAX_CHARS]


def _sniff_html(path: Path) -> str:
    from selectolax.parser import HTMLParser

    with path.open("rb") as fh:
        raw = fh.read(_HTML_READ_BYTES)
    tree = HTMLParser(raw.decode("utf-8", errors="replace"))
    body = tree.body
    if body is None:
        return ""
    text = body.text(separator=" ")
    return " ".join(text.split())[:_MAX_CHARS]
