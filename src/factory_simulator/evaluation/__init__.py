"""Evaluation framework for anomaly detection benchmarking.

PRD Reference: Section 12 (Evaluation Protocol)
"""

from factory_simulator.evaluation.evaluator import (
    Detection,
    Evaluator,
    EvaluatorSettings,
    GroundTruthEvent,
    match_events,
)
from factory_simulator.evaluation.metrics import (
    DEFAULT_LATENCY_TARGETS,
    DEFAULT_SEVERITY_WEIGHTS,
    EvaluationResult,
    EventMatch,
    RandomBaseline,
    ScenarioMetrics,
)

__all__ = [
    "DEFAULT_LATENCY_TARGETS",
    "DEFAULT_SEVERITY_WEIGHTS",
    "Detection",
    "EvaluationResult",
    "Evaluator",
    "EvaluatorSettings",
    "EventMatch",
    "GroundTruthEvent",
    "RandomBaseline",
    "ScenarioMetrics",
    "match_events",
]
