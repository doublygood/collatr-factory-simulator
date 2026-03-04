"""Tests for the evaluation framework: event matching, metrics, and Evaluator.

Covers:
- match_events: tolerance windows, overlapping windows, FP/TP/FN counting
- Evaluator.evaluate_from_data: precision, recall, F1, weighted variants
- Detection latency computation (median, p90)
- Per-scenario breakdown
- Random baseline structure
- Ground truth JSONL loading
- Detection CSV loading
- EvaluationConfig Pydantic model validation

PRD Reference: Section 12 (Evaluation Protocol)
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from factory_simulator.evaluation.evaluator import (
    Detection,
    Evaluator,
    EvaluatorSettings,
    GroundTruthEvent,
    match_events,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ev(scenario_type: str, start: float, end: float) -> GroundTruthEvent:
    return GroundTruthEvent(scenario_type=scenario_type, start_time=start, end_time=end)


def _det(timestamp: float, alert_type: str = "test") -> Detection:
    return Detection(timestamp=timestamp, alert_type=alert_type)


def _settings(pre: float = 30.0, post: float = 60.0) -> EvaluatorSettings:
    return EvaluatorSettings(pre_margin_seconds=pre, post_margin_seconds=post)


def _evaluator(pre: float = 30.0, post: float = 60.0) -> Evaluator:
    return Evaluator(settings=_settings(pre, post))


# ---------------------------------------------------------------------------
# match_events: core matching logic
# ---------------------------------------------------------------------------


class TestMatchEvents:
    def test_detection_exactly_at_start_is_tp(self) -> None:
        """Detection at event start → TP, latency=0."""
        event = _ev("web_break", 1000.0, 1060.0)
        matches, fp = match_events([event], [_det(1000.0)], 30.0, 60.0)
        assert len(matches) == 1
        assert matches[0].detected is True
        assert matches[0].latency == pytest.approx(0.0)
        assert fp == 0

    def test_detection_inside_window_is_tp(self) -> None:
        """Detection in the middle of the event window → TP."""
        event = _ev("web_break", 1000.0, 1060.0)
        matches, fp = match_events([event], [_det(1030.0)], 30.0, 60.0)
        assert matches[0].detected is True
        assert fp == 0

    def test_early_detection_within_pre_margin_is_tp(self) -> None:
        """Detection 20 s before event start (pre_margin=30) → TP, negative latency."""
        event = _ev("web_break", 1000.0, 1060.0)
        matches, fp = match_events([event], [_det(980.0)], 30.0, 60.0)
        assert matches[0].detected is True
        assert matches[0].latency == pytest.approx(-20.0)
        assert fp == 0

    def test_late_detection_within_post_margin_is_tp(self) -> None:
        """Detection 30 s after event end (post_margin=60) → TP."""
        event = _ev("web_break", 1000.0, 1060.0)
        matches, fp = match_events([event], [_det(1090.0)], 30.0, 60.0)
        assert matches[0].detected is True
        assert fp == 0

    def test_detection_at_pre_margin_boundary_is_tp(self) -> None:
        """Detection exactly at [start - pre_margin] is within the window → TP."""
        event = _ev("web_break", 1000.0, 1060.0)
        matches, fp = match_events([event], [_det(970.0)], 30.0, 60.0)
        assert matches[0].detected is True
        assert fp == 0

    def test_detection_at_post_margin_boundary_is_tp(self) -> None:
        """Detection exactly at [end + post_margin] is within the window → TP."""
        event = _ev("web_break", 1000.0, 1060.0)
        matches, fp = match_events([event], [_det(1120.0)], 30.0, 60.0)
        assert matches[0].detected is True
        assert fp == 0

    def test_detection_just_outside_pre_margin_is_fp(self) -> None:
        """Detection 31 s before start (pre_margin=30) → FP."""
        event = _ev("web_break", 1000.0, 1060.0)
        matches, fp = match_events([event], [_det(969.0)], 30.0, 60.0)
        assert matches[0].detected is False
        assert fp == 1

    def test_detection_just_outside_post_margin_is_fp(self) -> None:
        """Detection 61 s after event end (post_margin=60) → FP."""
        event = _ev("web_break", 1000.0, 1060.0)
        matches, fp = match_events([event], [_det(1121.0)], 30.0, 60.0)
        assert matches[0].detected is False
        assert fp == 1

    def test_no_detections_all_fn(self) -> None:
        """No detections → event is FN, fp_count=0."""
        events = [_ev("web_break", 1000.0, 1060.0)]
        matches, fp = match_events(events, [], 30.0, 60.0)
        assert matches[0].detected is False
        assert fp == 0

    def test_multiple_detections_in_window_count_as_one_tp(self) -> None:
        """Three detections inside one window → event detected once, fp=0."""
        event = _ev("web_break", 1000.0, 1060.0)
        dets = [_det(1010.0), _det(1020.0), _det(1030.0)]
        matches, fp = match_events([event], dets, 30.0, 60.0)
        assert matches[0].detected is True
        # First detection recorded
        assert matches[0].detection_time == pytest.approx(1010.0)
        # Other two are not FPs — they were in the same window
        assert fp == 0

    def test_detection_in_gap_is_fp(self) -> None:
        """Detection in gap between events → FP."""
        events = [
            _ev("web_break", 1000.0, 1060.0),
            _ev("micro_stop", 2000.0, 2030.0),
        ]
        matches, fp = match_events(events, [_det(1600.0)], 30.0, 60.0)
        assert fp == 1
        assert matches[0].detected is False
        assert matches[1].detected is False

    def test_overlapping_windows_assigned_to_nearest_event(self) -> None:
        """Detection in two overlapping windows → assigned to nearest by start time."""
        # event_a: start=1000, end=1030 → window [970, 1090]
        # event_b: start=1060, end=1090 → window [1030, 1150]
        # detection at 1045: |1045-1000|=45 vs |1045-1060|=15 → nearer to b
        event_a = _ev("web_break", 1000.0, 1030.0)
        event_b = _ev("micro_stop", 1060.0, 1090.0)
        matches, fp = match_events([event_a, event_b], [_det(1045.0)], 30.0, 60.0)
        assert matches[0].detected is False  # a not detected
        assert matches[1].detected is True  # b detected
        assert fp == 0

    def test_overlapping_windows_assigned_to_closer_start(self) -> None:
        """Detection closer to event_a's start → assigned to a."""
        # detection at 1005: |1005-1000|=5 vs |1005-1060|=55 → nearer to a
        event_a = _ev("web_break", 1000.0, 1030.0)
        event_b = _ev("micro_stop", 1060.0, 1090.0)
        matches, fp = match_events([event_a, event_b], [_det(1005.0)], 30.0, 60.0)
        assert matches[0].detected is True  # a detected
        assert matches[1].detected is False  # b not detected
        assert fp == 0

    def test_empty_events_empty_detections(self) -> None:
        """No events, no detections → empty matches, fp=0."""
        matches, fp = match_events([], [], 30.0, 60.0)
        assert matches == []
        assert fp == 0

    def test_empty_events_with_detections_all_fp(self) -> None:
        """Detections with no events → all FP."""
        dets = [_det(1000.0), _det(2000.0)]
        matches, fp = match_events([], dets, 30.0, 60.0)
        assert fp == 2
        assert matches == []

    def test_first_detection_time_recorded(self) -> None:
        """The earliest detection in the window is recorded as detection_time."""
        event = _ev("web_break", 1000.0, 1060.0)
        # Detections not in timestamp order in input
        dets = [_det(1050.0), _det(1010.0), _det(1030.0)]
        matches, _ = match_events([event], dets, 30.0, 60.0)
        assert matches[0].detection_time == pytest.approx(1010.0)
        assert matches[0].latency == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Evaluator: overall metrics
# ---------------------------------------------------------------------------


class TestEvaluatorOverallMetrics:
    def test_perfect_detector(self) -> None:
        """All events detected exactly → precision=1, recall=1, F1=1."""
        events = [
            _ev("web_break", 1000.0, 1060.0),
            _ev("micro_stop", 2000.0, 2030.0),
        ]
        dets = [_det(1010.0), _det(2010.0)]
        result = _evaluator().evaluate_from_data(events, dets)
        assert result.precision == pytest.approx(1.0)
        assert result.recall == pytest.approx(1.0)
        assert result.f1 == pytest.approx(1.0)
        assert result.true_positives == 2
        assert result.false_positives == 0
        assert result.false_negatives == 0
        assert result.total_events == 2

    def test_no_detections(self) -> None:
        """No detections → recall=0, precision=0, all FN."""
        events = [_ev("web_break", 1000.0, 1060.0)]
        result = _evaluator().evaluate_from_data(events, [])
        assert result.recall == pytest.approx(0.0)
        assert result.precision == pytest.approx(0.0)
        assert result.f1 == pytest.approx(0.0)
        assert result.false_negatives == 1
        assert result.false_positives == 0

    def test_false_positives_only(self) -> None:
        """All detections outside windows → precision=0, recall=0."""
        events = [_ev("web_break", 1000.0, 1060.0)]
        dets = [_det(5000.0), _det(6000.0)]
        result = _evaluator().evaluate_from_data(events, dets)
        assert result.recall == pytest.approx(0.0)
        assert result.precision == pytest.approx(0.0)
        assert result.false_positives == 2

    def test_partial_detection_half_recall(self) -> None:
        """Half detected → recall=0.5, precision=1.0."""
        events = [
            _ev("web_break", 1000.0, 1060.0),
            _ev("micro_stop", 2000.0, 2030.0),
        ]
        dets = [_det(1010.0)]  # Only first event detected
        result = _evaluator().evaluate_from_data(events, dets)
        assert result.recall == pytest.approx(0.5)
        assert result.precision == pytest.approx(1.0)
        assert result.f1 == pytest.approx(2 / 3)
        assert result.true_positives == 1
        assert result.false_negatives == 1

    def test_mixed_tp_and_fp(self) -> None:
        """One TP and one FP → precision=0.5, recall=1.0."""
        events = [_ev("web_break", 1000.0, 1060.0)]
        dets = [_det(1010.0), _det(5000.0)]  # One matches, one is FP
        result = _evaluator().evaluate_from_data(events, dets)
        assert result.true_positives == 1
        assert result.false_positives == 1
        assert result.recall == pytest.approx(1.0)
        assert result.precision == pytest.approx(0.5)

    def test_f1_harmonic_mean(self) -> None:
        """F1 = 2*P*R/(P+R)."""
        # Two events, one detected (recall=0.5), one FP (precision=0.5)
        events = [
            _ev("web_break", 1000.0, 1060.0),
            _ev("micro_stop", 2000.0, 2030.0),
        ]
        dets = [_det(1010.0), _det(5000.0)]
        result = _evaluator().evaluate_from_data(events, dets)
        assert result.precision == pytest.approx(0.5)
        assert result.recall == pytest.approx(0.5)
        assert result.f1 == pytest.approx(0.5)  # 2*0.5*0.5/(0.5+0.5)


# ---------------------------------------------------------------------------
# Evaluator: severity-weighted metrics
# ---------------------------------------------------------------------------


class TestWeightedMetrics:
    def test_weighted_recall_high_weight_detected(self) -> None:
        """Detecting high-weight event → high weighted recall."""
        events = [
            _ev("web_break", 1000.0, 1060.0),  # weight 10
            _ev("micro_stop", 2000.0, 2030.0),  # weight 1
        ]
        dets = [_det(1010.0)]  # Only web_break detected
        ev = Evaluator(
            settings=EvaluatorSettings(
                pre_margin_seconds=30.0,
                post_margin_seconds=60.0,
                severity_weights={"web_break": 10.0, "micro_stop": 1.0},
            )
        )
        result = ev.evaluate_from_data(events, dets)
        assert result.weighted_recall == pytest.approx(10.0 / 11.0, rel=1e-3)

    def test_weighted_recall_low_weight_detected(self) -> None:
        """Detecting only the low-weight event → low weighted recall."""
        events = [
            _ev("web_break", 1000.0, 1060.0),  # weight 10
            _ev("micro_stop", 2000.0, 2030.0),  # weight 1
        ]
        dets = [_det(2010.0)]  # Only micro_stop detected
        ev = Evaluator(
            settings=EvaluatorSettings(
                pre_margin_seconds=30.0,
                post_margin_seconds=60.0,
                severity_weights={"web_break": 10.0, "micro_stop": 1.0},
            )
        )
        result = ev.evaluate_from_data(events, dets)
        assert result.weighted_recall == pytest.approx(1.0 / 11.0, rel=1e-3)

    def test_weighted_recall_no_events(self) -> None:
        """No events → weighted_recall=0."""
        result = _evaluator().evaluate_from_data([], [])
        assert result.weighted_recall == pytest.approx(0.0)

    def test_default_severity_weights_applied(self) -> None:
        """Default weights from PRD 12.4 are present and correct."""
        ev = Evaluator()
        assert ev._s.severity_weights["web_break"] == pytest.approx(10.0)
        assert ev._s.severity_weights["micro_stop"] == pytest.approx(1.0)
        assert ev._s.severity_weights["bearing_wear"] == pytest.approx(8.0)

    def test_unknown_scenario_gets_default_weight_one(self) -> None:
        """Unknown scenario type uses weight 1.0."""
        events = [_ev("unknown_type", 1000.0, 1060.0)]
        dets = [_det(1010.0)]
        result = _evaluator().evaluate_from_data(events, dets)
        # weighted_recall = 1.0 / 1.0 = 1.0 (default weight 1)
        assert result.weighted_recall == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Evaluator: detection latency
# ---------------------------------------------------------------------------


class TestDetectionLatency:
    def test_latency_median_and_p90(self) -> None:
        """Median and p90 computed correctly for known latencies."""
        events = [_ev("web_break", t, t + 60.0) for t in [1000.0, 2000.0, 3000.0]]
        dets = [_det(1010.0), _det(2020.0), _det(3030.0)]
        result = _evaluator().evaluate_from_data(events, dets)
        # Latencies: 10, 20, 30 → median=20, p90≈28
        assert result.detection_latency_median == pytest.approx(20.0)
        assert result.detection_latency_p90 is not None
        assert 25.0 <= result.detection_latency_p90 <= 30.0

    def test_negative_latency_early_detection(self) -> None:
        """Early detection produces negative latency (not clamped)."""
        event = _ev("web_break", 1000.0, 1060.0)
        matches, _ = match_events([event], [_det(990.0)], 30.0, 60.0)
        assert matches[0].latency == pytest.approx(-10.0)

    def test_no_detections_latency_none(self) -> None:
        """No detections → latency stats are None."""
        events = [_ev("web_break", 1000.0, 1060.0)]
        result = _evaluator().evaluate_from_data(events, [])
        assert result.detection_latency_median is None
        assert result.detection_latency_p90 is None

    def test_single_detection_median_equals_p90(self) -> None:
        """Single latency value → median == p90."""
        events = [_ev("web_break", 1000.0, 1060.0)]
        result = _evaluator().evaluate_from_data(events, [_det(1015.0)])
        assert result.detection_latency_median == pytest.approx(15.0)
        assert result.detection_latency_p90 == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# Evaluator: per-scenario breakdown
# ---------------------------------------------------------------------------


class TestPerScenarioBreakdown:
    def test_per_scenario_recall(self) -> None:
        """Per-scenario recall computed correctly."""
        events = [
            _ev("web_break", 1000.0, 1060.0),
            _ev("web_break", 2000.0, 2060.0),
            _ev("micro_stop", 3000.0, 3030.0),
        ]
        dets = [_det(1010.0)]  # Only first web_break detected
        result = _evaluator().evaluate_from_data(events, dets)

        wb = result.per_scenario["web_break"]
        assert wb.total_events == 2
        assert wb.detected_events == 1
        assert wb.recall == pytest.approx(0.5)

        ms = result.per_scenario["micro_stop"]
        assert ms.total_events == 1
        assert ms.detected_events == 0
        assert ms.recall == pytest.approx(0.0)

    def test_per_scenario_all_detected(self) -> None:
        """All events detected → per-scenario recall=1.0."""
        events = [
            _ev("web_break", 1000.0, 1060.0),
            _ev("micro_stop", 2000.0, 2030.0),
        ]
        dets = [_det(1010.0), _det(2010.0)]
        result = _evaluator().evaluate_from_data(events, dets)
        assert result.per_scenario["web_break"].recall == pytest.approx(1.0)
        assert result.per_scenario["micro_stop"].recall == pytest.approx(1.0)

    def test_per_scenario_latencies_recorded(self) -> None:
        """Per-scenario detection latencies are correctly collected."""
        events = [
            _ev("web_break", 1000.0, 1060.0),
            _ev("web_break", 2000.0, 2060.0),
        ]
        dets = [_det(1005.0), _det(2015.0)]
        result = _evaluator().evaluate_from_data(events, dets)
        wb = result.per_scenario["web_break"]
        assert len(wb.detection_latencies) == 2
        assert any(lat == pytest.approx(5.0) for lat in wb.detection_latencies)
        assert any(lat == pytest.approx(15.0) for lat in wb.detection_latencies)

    def test_empty_events_empty_per_scenario(self) -> None:
        """No events → per_scenario is empty dict."""
        result = _evaluator().evaluate_from_data([], [])
        assert result.per_scenario == {}


# ---------------------------------------------------------------------------
# Evaluator: random baseline
# ---------------------------------------------------------------------------


class TestRandomBaseline:
    def test_baseline_structure(self) -> None:
        """Random baseline has correct structure and bounded values."""
        events = [
            _ev("web_break", 1000.0, 1060.0),
            _ev("micro_stop", 3000.0, 3030.0),
        ]
        result = _evaluator().evaluate_from_data(events, [_det(1010.0)])
        bl = result.random_baseline
        assert 0.0 <= bl.anomaly_density <= 1.0
        assert 0.0 <= bl.precision <= 1.0
        assert 0.0 <= bl.recall <= 1.0
        assert 0.0 <= bl.f1 <= 1.0

    def test_baseline_high_density_high_recall(self) -> None:
        """Dense anomalies → random baseline achieves high recall."""
        # 10 events each 90s, tick_interval=1s, margins=5s
        events = [_ev("web_break", 100.0 * i, 100.0 * i + 90.0) for i in range(10)]
        ev = Evaluator(
            settings=EvaluatorSettings(
                pre_margin_seconds=5.0,
                post_margin_seconds=5.0,
                tick_interval_s=1.0,
                random_seed=42,
            )
        )
        result = ev.evaluate_from_data(events, [])
        # Dense anomaly density ≈ 0.8+ → baseline recall should be high
        assert result.random_baseline.anomaly_density > 0.5
        assert result.random_baseline.recall > 0.5

    def test_baseline_no_events_returns_zeros(self) -> None:
        """No events → baseline is all zeros."""
        result = _evaluator().evaluate_from_data([], [])
        bl = result.random_baseline
        assert bl.anomaly_density == pytest.approx(0.0)
        assert bl.recall == pytest.approx(0.0)

    def test_baseline_deterministic_with_seed(self) -> None:
        """Same seed → same baseline on repeated calls."""
        events = [_ev("web_break", 1000.0, 1060.0)]
        ev = Evaluator(settings=EvaluatorSettings(random_seed=42))
        r1 = ev.evaluate_from_data(events, [])
        r2 = ev.evaluate_from_data(events, [])
        assert r1.random_baseline.recall == pytest.approx(r2.random_baseline.recall)
        assert r1.random_baseline.precision == pytest.approx(
            r2.random_baseline.precision
        )

    def test_baseline_overlapping_events_not_double_counted(self) -> None:
        """Overlapping event intervals are merged before computing anomaly density."""
        # Two events that fully overlap — only 100s of anomaly time, not 200s
        ev_a = _ev("web_break", 1000.0, 1100.0)   # 100s
        ev_b = _ev("micro_stop", 1050.0, 1150.0)   # overlaps by 50s
        ev = Evaluator(
            settings=EvaluatorSettings(
                pre_margin_seconds=5.0,
                post_margin_seconds=5.0,
                tick_interval_s=1.0,
                random_seed=42,
            )
        )
        result = ev.evaluate_from_data([ev_a, ev_b], [])
        # Merged interval: [1000, 1150] = 150s (not 200s double-count)
        # Total duration: (1000 - 5) to (1150 + 5) = 160s
        expected_density = 150.0 / 160.0
        assert result.random_baseline.anomaly_density == pytest.approx(
            expected_density, abs=0.01
        )

    def test_baseline_non_overlapping_events_unchanged(self) -> None:
        """Non-overlapping events are not affected by the merging logic."""
        ev_a = _ev("web_break", 1000.0, 1060.0)   # 60s
        ev_b = _ev("micro_stop", 2000.0, 2030.0)  # 30s, no overlap
        ev = Evaluator(
            settings=EvaluatorSettings(
                pre_margin_seconds=0.0,
                post_margin_seconds=0.0,
                tick_interval_s=1.0,
                random_seed=42,
            )
        )
        result = ev.evaluate_from_data([ev_a, ev_b], [])
        # total_anomaly_time = 60 + 30 = 90s
        # total_duration = 2030 - 1000 = 1030s
        expected_density = 90.0 / 1030.0
        assert result.random_baseline.anomaly_density == pytest.approx(
            expected_density, abs=0.01
        )


# ---------------------------------------------------------------------------
# Ground truth JSONL loading
# ---------------------------------------------------------------------------


class TestGroundTruthLoading:
    def _write_gt(self, tmp_path: Path, lines: list[dict]) -> Path:
        p = tmp_path / "gt.jsonl"
        with p.open("w") as fh:
            for line in lines:
                fh.write(json.dumps(line) + "\n")
        return p

    def test_loads_matched_pair(self, tmp_path: Path) -> None:
        """Matched start/end produces one event with correct duration."""
        gt = self._write_gt(
            tmp_path,
            [
                {"event_type": "config", "sim_version": "1.0.0"},
                {
                    "sim_time": "2026-01-01T00:16:40.000Z",
                    "event": "scenario_start",
                    "scenario": "web_break",
                    "affected_signals": [],
                },
                {
                    "sim_time": "2026-01-01T00:17:10.000Z",
                    "event": "scenario_end",
                    "scenario": "web_break",
                },
            ],
        )
        ev = Evaluator()
        events = ev.load_ground_truth(gt)
        assert len(events) == 1
        assert events[0].scenario_type == "web_break"
        assert abs((events[0].end_time - events[0].start_time) - 30.0) < 1.0

    def test_open_scenario_silently_dropped(self, tmp_path: Path) -> None:
        """scenario_start without matching end → silently dropped."""
        gt = self._write_gt(
            tmp_path,
            [
                {
                    "sim_time": "2026-01-01T00:16:40.000Z",
                    "event": "scenario_start",
                    "scenario": "web_break",
                    "affected_signals": [],
                },
                # No scenario_end
            ],
        )
        events = Evaluator().load_ground_truth(gt)
        assert len(events) == 0

    def test_multiple_scenario_types(self, tmp_path: Path) -> None:
        """Multiple scenario types loaded correctly."""
        gt = self._write_gt(
            tmp_path,
            [
                {
                    "sim_time": "2026-01-01T00:16:40.000Z",
                    "event": "scenario_start",
                    "scenario": "web_break",
                    "affected_signals": [],
                },
                {
                    "sim_time": "2026-01-01T00:17:10.000Z",
                    "event": "scenario_end",
                    "scenario": "web_break",
                },
                {
                    "sim_time": "2026-01-01T00:50:00.000Z",
                    "event": "scenario_start",
                    "scenario": "micro_stop",
                    "affected_signals": [],
                },
                {
                    "sim_time": "2026-01-01T00:50:15.000Z",
                    "event": "scenario_end",
                    "scenario": "micro_stop",
                },
            ],
        )
        events = Evaluator().load_ground_truth(gt)
        assert len(events) == 2
        types = {e.scenario_type for e in events}
        assert types == {"web_break", "micro_stop"}

    def test_non_scenario_events_ignored(self, tmp_path: Path) -> None:
        """state_change, data_quality etc. do not produce events."""
        gt = self._write_gt(
            tmp_path,
            [
                {
                    "sim_time": "2026-01-01T00:16:40.000Z",
                    "event": "state_change",
                    "signal": "press.state",
                    "from": 0,
                    "to": 1,
                },
                {
                    "sim_time": "2026-01-01T00:16:50.000Z",
                    "event": "data_quality",
                    "protocol": "modbus",
                    "duration": 5.0,
                },
                {
                    "sim_time": "2026-01-01T00:17:00.000Z",
                    "event": "sensor_disconnect",
                    "signal": "press.line_speed",
                    "sentinel_value": -999.0,
                },
            ],
        )
        events = Evaluator().load_ground_truth(gt)
        assert len(events) == 0

    def test_fifo_pairing_same_type(self, tmp_path: Path) -> None:
        """Two starts of same type before ends → FIFO pairing (first-in → first-out).

        FIFO: start@1h + end@3h = 2h; start@2h + end@4h = 2h.
        LIFO: start@2h + end@3h = 1h; start@1h + end@4h = 3h.
        Both events have duration 2h under FIFO.
        """
        gt = self._write_gt(
            tmp_path,
            [
                {
                    "sim_time": "2026-01-01T01:00:00.000Z",
                    "event": "scenario_start",
                    "scenario": "dryer_drift",
                    "affected_signals": [],
                },
                {
                    "sim_time": "2026-01-01T02:00:00.000Z",
                    "event": "scenario_start",
                    "scenario": "dryer_drift",
                    "affected_signals": [],
                },
                {
                    "sim_time": "2026-01-01T03:00:00.000Z",
                    "event": "scenario_end",
                    "scenario": "dryer_drift",
                },
                {
                    "sim_time": "2026-01-01T04:00:00.000Z",
                    "event": "scenario_end",
                    "scenario": "dryer_drift",
                },
            ],
        )
        events = Evaluator().load_ground_truth(gt)
        assert len(events) == 2
        # FIFO: 01:00→03:00 (2h) and 02:00→04:00 (2h) → both durations ≈ 7200s
        durations = sorted(ev.end_time - ev.start_time for ev in events)
        assert abs(durations[0] - 7200.0) < 2.0
        assert abs(durations[1] - 7200.0) < 2.0

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty JSONL file → no events."""
        p = tmp_path / "gt.jsonl"
        p.write_text("")
        events = Evaluator().load_ground_truth(p)
        assert events == []


# ---------------------------------------------------------------------------
# Detection CSV loading
# ---------------------------------------------------------------------------


class TestDetectionLoading:
    def _write_csv(
        self,
        tmp_path: Path,
        rows: list[dict],
        fieldnames: list[str] | None = None,
    ) -> Path:
        p = tmp_path / "detections.csv"
        names = fieldnames or ["timestamp", "alert_type", "signal_id", "confidence"]
        with p.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=names)
            writer.writeheader()
            writer.writerows(rows)
        return p

    def test_iso_timestamp(self, tmp_path: Path) -> None:
        """ISO timestamp strings are parsed correctly."""
        csv_path = self._write_csv(
            tmp_path,
            [
                {
                    "timestamp": "2026-01-01T01:00:00.000Z",
                    "alert_type": "web_break",
                    "signal_id": "",
                    "confidence": "0.9",
                }
            ],
        )
        dets = Evaluator().load_detections(csv_path)
        assert len(dets) == 1
        assert dets[0].alert_type == "web_break"
        assert dets[0].confidence == pytest.approx(0.9)

    def test_float_timestamp(self, tmp_path: Path) -> None:
        """Float UNIX timestamps are accepted."""
        t = 1.75e9
        csv_path = self._write_csv(
            tmp_path,
            [{"timestamp": str(t), "alert_type": "test", "signal_id": "", "confidence": "1.0"}],
        )
        dets = Evaluator().load_detections(csv_path)
        assert len(dets) == 1
        assert dets[0].timestamp == pytest.approx(t)

    def test_minimal_csv_timestamp_only(self, tmp_path: Path) -> None:
        """CSV with only a timestamp column works."""
        p = tmp_path / "det.csv"
        p.write_text("timestamp\n2026-01-01T01:00:00.000Z\n")
        dets = Evaluator().load_detections(p)
        assert len(dets) == 1

    def test_multiple_rows(self, tmp_path: Path) -> None:
        """Multiple rows all loaded."""
        rows = [
            {
                "timestamp": f"2026-01-01T0{i}:00:00.000Z",
                "alert_type": "test",
                "signal_id": "",
                "confidence": "1.0",
            }
            for i in range(3)
        ]
        csv_path = self._write_csv(tmp_path, rows)
        dets = Evaluator().load_detections(csv_path)
        assert len(dets) == 3

    def test_empty_csv_no_detections(self, tmp_path: Path) -> None:
        """CSV with header but no data rows → empty list."""
        p = tmp_path / "det.csv"
        p.write_text("timestamp,alert_type\n")
        dets = Evaluator().load_detections(p)
        assert dets == []


# ---------------------------------------------------------------------------
# EvaluationConfig Pydantic model
# ---------------------------------------------------------------------------


class TestEvaluationConfig:
    def test_defaults(self) -> None:
        """Default values match PRD 12.4 specification."""
        from factory_simulator.config import EvaluationConfig

        cfg = EvaluationConfig()
        assert cfg.pre_margin_seconds == pytest.approx(30.0)
        assert cfg.post_margin_seconds == pytest.approx(60.0)
        assert cfg.seeds == 1
        assert "web_break" in cfg.severity_weights
        assert cfg.severity_weights["web_break"] == pytest.approx(10.0)
        assert cfg.severity_weights["micro_stop"] == pytest.approx(1.0)
        assert cfg.severity_weights["bearing_wear"] == pytest.approx(8.0)
        assert "web_break" in cfg.latency_targets
        assert cfg.latency_targets["web_break"] == pytest.approx(2.0)

    def test_custom_margins_accepted(self) -> None:
        """Custom margins are accepted when non-negative."""
        from factory_simulator.config import EvaluationConfig

        cfg = EvaluationConfig(pre_margin_seconds=10.0, post_margin_seconds=120.0)
        assert cfg.pre_margin_seconds == pytest.approx(10.0)
        assert cfg.post_margin_seconds == pytest.approx(120.0)

    def test_negative_pre_margin_rejected(self) -> None:
        """Negative pre_margin_seconds is rejected."""
        from factory_simulator.config import EvaluationConfig

        with pytest.raises(ValueError):
            EvaluationConfig(pre_margin_seconds=-1.0)

    def test_negative_post_margin_rejected(self) -> None:
        """Negative post_margin_seconds is rejected."""
        from factory_simulator.config import EvaluationConfig

        with pytest.raises(ValueError):
            EvaluationConfig(post_margin_seconds=-0.1)

    def test_zero_seeds_rejected(self) -> None:
        """seeds=0 is rejected."""
        from factory_simulator.config import EvaluationConfig

        with pytest.raises(ValueError):
            EvaluationConfig(seeds=0)

    def test_negative_seeds_rejected(self) -> None:
        """Negative seeds is rejected."""
        from factory_simulator.config import EvaluationConfig

        with pytest.raises(ValueError):
            EvaluationConfig(seeds=-1)

    def test_custom_severity_weights(self) -> None:
        """Custom severity weights override defaults."""
        from factory_simulator.config import EvaluationConfig

        cfg = EvaluationConfig(severity_weights={"web_break": 5.0})
        assert cfg.severity_weights["web_break"] == pytest.approx(5.0)

    def test_zero_pre_margin_accepted(self) -> None:
        """Zero pre_margin (no early detection tolerance) is valid."""
        from factory_simulator.config import EvaluationConfig

        cfg = EvaluationConfig(pre_margin_seconds=0.0)
        assert cfg.pre_margin_seconds == pytest.approx(0.0)
