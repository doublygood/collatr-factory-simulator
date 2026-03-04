"""Evaluation framework: event-level anomaly detection evaluation.

Consumes two inputs:
  1. Ground truth JSONL sidecar (produced by the simulator)
  2. Detection alerts CSV (produced by the anomaly detection system under test)

Matches detections to ground truth events using tolerance windows (PRD 12.4)
and computes precision, recall, F1, severity-weighted variants, detection
latency, per-scenario breakdown, and a random baseline.

PRD Reference: Section 12 (Evaluation Protocol)
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from factory_simulator.evaluation.metrics import (
    DEFAULT_SEVERITY_WEIGHTS,
    EvaluationResult,
    EventMatch,
    RandomBaseline,
    ScenarioMetrics,
)

logger = logging.getLogger(__name__)

# Default tick interval for random baseline computation (seconds).
# Used when tick_interval_ms is not available from the ground truth header.
_DEFAULT_TICK_INTERVAL_S = 0.1


# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------


@dataclass
class GroundTruthEvent:
    """A matched scenario_start/scenario_end pair from the ground truth log."""

    scenario_type: str
    start_time: float  # UNIX seconds
    end_time: float  # UNIX seconds
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class Detection:
    """A single anomaly detection alert from the system under test."""

    timestamp: float  # UNIX seconds
    alert_type: str = ""
    signal_id: str = ""
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Evaluator settings (runtime, not the Pydantic config model)
# ---------------------------------------------------------------------------


@dataclass
class EvaluatorSettings:
    """Runtime settings for the evaluator."""

    pre_margin_seconds: float = 30.0
    post_margin_seconds: float = 60.0
    severity_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SEVERITY_WEIGHTS)
    )
    tick_interval_s: float = _DEFAULT_TICK_INTERVAL_S
    random_seed: int = 42


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso(ts: str) -> float:
    """Parse an ISO 8601 timestamp string to a UNIX float (seconds)."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _percentile(values: list[float], pct: float) -> float:
    """Compute a percentile of a list of values (pct in 0-100 range).

    Uses linear interpolation between adjacent values.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


# ---------------------------------------------------------------------------
# Core matching logic
# ---------------------------------------------------------------------------


def match_events(
    events: list[GroundTruthEvent],
    detections: list[Detection],
    pre_margin: float,
    post_margin: float,
) -> tuple[list[EventMatch], int]:
    """Match detections to ground truth events using tolerance windows.

    Rules (PRD 12.4):
    - Effective window per event: ``[start - pre_margin, end + post_margin]``
    - A detection in multiple overlapping windows is assigned to the nearest
      event by start time (using ``|detection_time - event.start_time|``).
    - Multiple detections assigned to the same event → one TP (first fires).
    - Detections not assigned to any event → false positives.

    Returns:
        matches:  one ``EventMatch`` per ground truth event (in input order).
        fp_count: number of detections that matched no event.
    """
    # Build effective windows: (window_start, window_end, event_index)
    windows: list[tuple[float, float, int]] = []
    for i, ev in enumerate(events):
        windows.append((ev.start_time - pre_margin, ev.end_time + post_margin, i))

    # For each detection, find which windows contain it and assign to nearest.
    # det_index -> event_index
    det_to_event: dict[int, int] = {}
    for det_idx, det in enumerate(detections):
        t = det.timestamp
        candidates: list[tuple[float, int]] = []  # (distance_to_start, event_idx)
        for ws, we, ev_idx in windows:
            if ws <= t <= we:
                dist = abs(t - events[ev_idx].start_time)
                candidates.append((dist, ev_idx))
        if candidates:
            candidates.sort()
            det_to_event[det_idx] = candidates[0][1]

    # For each event, collect detections assigned to it.
    event_to_dets: dict[int, list[float]] = {i: [] for i in range(len(events))}
    for det_idx, ev_idx in det_to_event.items():
        event_to_dets[ev_idx].append(detections[det_idx].timestamp)

    # Build one EventMatch per event.
    matches: list[EventMatch] = []
    for ev_idx, ev in enumerate(events):
        assigned_timestamps = event_to_dets[ev_idx]
        if assigned_timestamps:
            first_t = min(assigned_timestamps)
            latency = first_t - ev.start_time
            matches.append(
                EventMatch(
                    event_type=ev.scenario_type,
                    start_time=ev.start_time,
                    end_time=ev.end_time,
                    detected=True,
                    detection_time=first_t,
                    latency=latency,
                )
            )
        else:
            matches.append(
                EventMatch(
                    event_type=ev.scenario_type,
                    start_time=ev.start_time,
                    end_time=ev.end_time,
                    detected=False,
                )
            )

    fp_count = len(detections) - len(det_to_event)
    return matches, fp_count


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class Evaluator:
    """Event-level anomaly detection evaluator.

    Usage::

        ev = Evaluator()
        result = ev.evaluate(
            ground_truth_path="output/ground_truth.jsonl",
            detections_path="output/detections.csv",
        )
        print(f"F1={result.f1:.3f}  recall={result.recall:.3f}")
    """

    def __init__(self, settings: EvaluatorSettings | None = None) -> None:
        self._s = settings or EvaluatorSettings()

    # -- I/O -------------------------------------------------------------------

    def load_ground_truth(self, path: str | Path) -> list[GroundTruthEvent]:
        """Parse a JSONL ground truth file and return ground truth events.

        Only ``scenario_start`` / ``scenario_end`` pairs are extracted.
        Open scenarios (start without matching end) are silently dropped.
        Non-scenario events (state_change, data_quality, etc.) are ignored.

        FIFO pairing: when multiple starts of the same scenario type occur
        before an end, the first start is paired with the first end.
        """
        p = Path(path)
        events: list[GroundTruthEvent] = []
        # scenario_name -> FIFO list of (start_time, parameters)
        open_scenarios: dict[str, list[tuple[float, dict[str, Any]]]] = {}

        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record: dict[str, Any] = json.loads(line)

                # Header record uses "event_type": "config" — skip it.
                ev_type = record.get("event") or record.get("event_type")
                if ev_type == "scenario_start":
                    scenario = record["scenario"]
                    t = _parse_iso(record["sim_time"])
                    params: dict[str, Any] = record.get("parameters") or {}
                    open_scenarios.setdefault(scenario, []).append((t, params))
                elif ev_type == "scenario_end":
                    scenario = record["scenario"]
                    t = _parse_iso(record["sim_time"])
                    stack = open_scenarios.get(scenario)
                    if stack:
                        start_t, params = stack.pop(0)  # FIFO
                        events.append(
                            GroundTruthEvent(
                                scenario_type=scenario,
                                start_time=start_t,
                                end_time=t,
                                parameters=params,
                            )
                        )

        logger.info("Loaded %d events from %s", len(events), p)
        return events

    def load_detections(self, path: str | Path) -> list[Detection]:
        """Parse a detection alert CSV file.

        Expected columns: ``timestamp, alert_type, signal_id, confidence``
        (``alert_type``, ``signal_id``, and ``confidence`` are optional).

        ``timestamp`` accepts ISO 8601 strings or float UNIX seconds.
        """
        p = Path(path)
        detections: list[Detection] = []

        with p.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                ts_raw = (row.get("timestamp") or "").strip()
                if not ts_raw:
                    continue
                try:
                    t = float(ts_raw)
                except ValueError:
                    t = _parse_iso(ts_raw)

                detections.append(
                    Detection(
                        timestamp=t,
                        alert_type=(row.get("alert_type") or "").strip(),
                        signal_id=(row.get("signal_id") or "").strip(),
                        confidence=float((row.get("confidence") or 1.0) or 1.0),
                    )
                )

        logger.info("Loaded %d detections from %s", len(detections), p)
        return detections

    # -- Main API --------------------------------------------------------------

    def evaluate(
        self,
        ground_truth_path: str | Path,
        detections_path: str | Path,
    ) -> EvaluationResult:
        """Run evaluation from file paths and return a complete result."""
        events = self.load_ground_truth(ground_truth_path)
        detections = self.load_detections(detections_path)
        return self._compute(events, detections)

    def evaluate_from_data(
        self,
        events: list[GroundTruthEvent],
        detections: list[Detection],
    ) -> EvaluationResult:
        """Run evaluation from pre-loaded data (useful for testing)."""
        return self._compute(events, detections)

    # -- Internal computation --------------------------------------------------

    def _compute(
        self,
        events: list[GroundTruthEvent],
        detections: list[Detection],
    ) -> EvaluationResult:
        s = self._s
        matches, fp_count = match_events(
            events, detections, s.pre_margin_seconds, s.post_margin_seconds
        )

        tp = sum(1 for m in matches if m.detected)
        fn = sum(1 for m in matches if not m.detected)
        fp = fp_count

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        # Severity-weighted recall (PRD 12.4)
        total_weight = sum(s.severity_weights.get(m.event_type, 1.0) for m in matches)
        detected_weight = sum(
            s.severity_weights.get(m.event_type, 1.0)
            for m in matches
            if m.detected
        )
        weighted_recall = detected_weight / total_weight if total_weight > 0 else 0.0
        weighted_f1 = (
            2 * precision * weighted_recall / (precision + weighted_recall)
            if (precision + weighted_recall) > 0
            else 0.0
        )

        # Detection latency statistics
        latencies = [
            m.latency for m in matches if m.detected and m.latency is not None
        ]
        latency_median = _percentile(latencies, 50) if latencies else None
        latency_p90 = _percentile(latencies, 90) if latencies else None

        # Per-scenario breakdown
        per_scenario = self._per_scenario_metrics(matches)

        # Random baseline
        baseline = self._random_baseline(events)

        return EvaluationResult(
            precision=precision,
            recall=recall,
            f1=f1,
            weighted_recall=weighted_recall,
            weighted_f1=weighted_f1,
            per_scenario=per_scenario,
            detection_latency_median=latency_median,
            detection_latency_p90=latency_p90,
            random_baseline=baseline,
            total_events=len(events),
            true_positives=tp,
            false_positives=fp,
            false_negatives=fn,
        )

    def _per_scenario_metrics(
        self,
        matches: list[EventMatch],
    ) -> dict[str, ScenarioMetrics]:
        """Compute per-scenario recall and latency breakdown."""
        scenario_types: set[str] = {m.event_type for m in matches}
        result: dict[str, ScenarioMetrics] = {}
        for sc_type in sorted(scenario_types):
            sc_matches = [m for m in matches if m.event_type == sc_type]
            total = len(sc_matches)
            detected = sum(1 for m in sc_matches if m.detected)
            latencies = [
                m.latency
                for m in sc_matches
                if m.detected and m.latency is not None
            ]
            recall = detected / total if total > 0 else 0.0
            result[sc_type] = ScenarioMetrics(
                scenario_type=sc_type,
                total_events=total,
                detected_events=detected,
                recall=recall,
                detection_latencies=latencies,
            )
        return result

    def _random_baseline(self, events: list[GroundTruthEvent]) -> RandomBaseline:
        """Compute random detector baseline metrics (PRD 12.4).

        A random detector fires at each tick with probability p, where p
        equals the anomaly density (total anomaly ticks / total ticks).
        Uses a fixed seed for reproducibility.
        """
        if not events:
            return RandomBaseline(
                anomaly_density=0.0, precision=0.0, recall=0.0, f1=0.0
            )

        s = self._s
        # Time range: extend by margins to match the matching window space
        min_t = min(ev.start_time for ev in events) - s.pre_margin_seconds
        max_t = max(ev.end_time for ev in events) + s.post_margin_seconds
        total_duration = max_t - min_t
        if total_duration <= 0:
            return RandomBaseline(
                anomaly_density=0.0, precision=0.0, recall=0.0, f1=0.0
            )

        # Anomaly density: fraction of time covered by events
        total_anomaly_time = sum(ev.end_time - ev.start_time for ev in events)
        anomaly_density = min(total_anomaly_time / total_duration, 1.0)

        # Simulate random detector with seeded RNG
        tick_s = s.tick_interval_s
        rng = np.random.default_rng(s.random_seed)
        n_ticks = max(1, int(total_duration / tick_s))
        fire_mask = rng.random(n_ticks) < anomaly_density
        random_detections = [
            Detection(timestamp=min_t + i * tick_s, alert_type="random")
            for i in range(n_ticks)
            if fire_mask[i]
        ]

        if not random_detections:
            return RandomBaseline(
                anomaly_density=anomaly_density,
                precision=0.0,
                recall=0.0,
                f1=0.0,
            )

        matches, fp_count = match_events(
            events, random_detections, s.pre_margin_seconds, s.post_margin_seconds
        )
        b_tp = sum(1 for m in matches if m.detected)
        b_fn = sum(1 for m in matches if not m.detected)
        b_fp = fp_count

        b_precision = b_tp / (b_tp + b_fp) if (b_tp + b_fp) > 0 else 0.0
        b_recall = b_tp / (b_tp + b_fn) if (b_tp + b_fn) > 0 else 0.0
        b_f1 = (
            2 * b_precision * b_recall / (b_precision + b_recall)
            if (b_precision + b_recall) > 0
            else 0.0
        )

        return RandomBaseline(
            anomaly_density=anomaly_density,
            precision=b_precision,
            recall=b_recall,
            f1=b_f1,
        )
