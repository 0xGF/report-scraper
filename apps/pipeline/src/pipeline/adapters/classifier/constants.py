"""Tunable thresholds for the classifier pipeline.

All confidence values live in one place so tweaks don't require hunting
through the rule list and orchestrator. Higher = more sure.
"""

from __future__ import annotations

# Confidence assigned by `RuleClassifier` when a kind pattern matches.
CONFIDENCE_KIND_WITH_YEAR: float = 0.95  # rule matched + we found a fiscal year
CONFIDENCE_KIND_NO_YEAR: float = 0.75  # rule matched but no fiscal year
CONFIDENCE_YEAR_ONLY: float = 0.3  # no kind matched, only a year hint
CONFIDENCE_NONE: float = 0.0  # nothing matched

# Below this score, the orchestrator escalates to content sniffing / LLM.
LLM_ESCALATION_THRESHOLD: float = 0.5
