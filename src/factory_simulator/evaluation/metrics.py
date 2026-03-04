"""Metric data structures for the evaluation framework.

PRD Reference: Section 12.4 (Evaluation Metrics)
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Default severity weights from PRD 12.4
# ---------------------------------------------------------------------------

DEFAULT_SEVERITY_WEIGHTS: dict[str, float] = {
    "web_break": 10.0,
    "unplanned_stop": 5.0,
    "seal_integrity_failure": 8.0,
    "cold_chain_break": 10.0,
    "bearing_wear": 8.0,
    "dryer_drift": 3.0,
    "oven_excursion": 3.0,
    "fill_weight_drift": 3.0,
    "ink_viscosity_excursion": 2.0,
    "registration_drift": 2.0,
    "contextual_anomaly": 5.0,
    "intermittent_fault": 4.0,
    "micro_stop": 1.0,
    "sensor_disconnect": 2.0,
    "stuck_sensor": 3.0,
}

# Default detection latency targets (seconds) from PRD 12.4
DEFAULT_LATENCY_TARGETS: dict[str, float] = {
    "web_break": 2.0,
    "unplanned_stop": 10.0,
    "seal_integrity_failure": 60.0,
    "cold_chain_break": 300.0,
    "bearing_wear": 86400.0,
    "dryer_drift": 900.0,
    "fill_weight_drift": 600.0,
    "contextual_anomaly": 300.0,
    "intermittent_fault": 172800.0,
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class EventMatch:
    """Result of matching a single ground truth event to detections.

    ``latency`` is seconds from ``scenario_start`` to the first detection
    within the tolerance window.  Negative latency means the detector fired
    before the annotated start (early detection — desirable).
    """

    event_type: str
    start_time: float  # UNIX seconds
    end_time: float  # UNIX seconds
    detected: bool
    detection_time: float | None = None
    latency: float | None = None  # seconds; negative = early detection


@dataclass
class ScenarioMetrics:
    """Per-scenario metrics for a single scenario type."""

    scenario_type: str
    total_events: int
    detected_events: int
    recall: float
    detection_latencies: list[float] = field(default_factory=list)


@dataclass
class RandomBaseline:
    """Metrics produced by a random detector baseline.

    PRD 12.4: a random detector fires at each tick with probability p,
    where p = total anomaly ticks / total ticks (the anomaly density).
    """

    anomaly_density: float  # fraction of ticks that are anomalous
    precision: float
    recall: float
    f1: float


@dataclass
class EvaluationResult:
    """Complete evaluation result for one ground-truth / detections pair."""

    precision: float
    recall: float
    f1: float
    weighted_recall: float
    weighted_f1: float
    per_scenario: dict[str, ScenarioMetrics]
    detection_latency_median: float | None
    detection_latency_p90: float | None
    random_baseline: RandomBaseline
    total_events: int
    true_positives: int
    false_positives: int
    false_negatives: int
