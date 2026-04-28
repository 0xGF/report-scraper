"""Extractors — turn a classified document into `ExtractedStatement`s.

Dispatches on `content_type` / file extension:
- HTML (e.g. SEC 20-F) → `HtmlExtractor`
- PDF (most IR reports) → `PdfExtractor`

Each extractor is responsible for locating the big three statements inside
the document and producing raw rows (pre-normalization). Concrete extractors
satisfy the `Extractor` Protocol so the dispatcher can swap them freely.
"""

from typing import Protocol

from pipeline.adapters.extractor.dispatcher import extract_statements
from pipeline.adapters.extractor.html_extractor import HtmlExtractor
from pipeline.adapters.extractor.pdf_extractor import PdfExtractor
from pipeline.domain.models import ClassifiedPdf, ExtractedStatement
from pipeline.domain.types import Currency


class Extractor(Protocol):
    """Structural type every concrete extractor must satisfy."""

    def extract(
        self,
        doc: ClassifiedPdf,
        *,
        currency: Currency = ...,
    ) -> list[ExtractedStatement]: ...


__all__ = ["Extractor", "HtmlExtractor", "PdfExtractor", "extract_statements"]
