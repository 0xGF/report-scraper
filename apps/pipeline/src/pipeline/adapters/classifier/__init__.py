"""Document classifiers — tag each scraped doc with `(kind, fiscal_year)`.

Two-tier pipeline:
1. `RuleClassifier` — filename + link-text regex. Deterministic, free, fast.
2. `LlmClassifier` — fallback for ambiguous docs. Cached on disk.

Consumers normally call `classify_all(docs, llm_client)` which applies both.
"""

from pipeline.adapters.classifier.orchestrator import classify_all
from pipeline.adapters.classifier.rules import RuleClassifier

__all__ = ["RuleClassifier", "classify_all"]
