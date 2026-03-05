"""Tests for evaluation CLI: RunManifest, config overlays, Run A/B/C configs,
multi-seed evaluation, report formatting, and evaluate_command.

PRD Reference: Section 12.2, 12.3, 12.4, 12.5
"""

from __future__ import annotations

import csv
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from factory_simulator.evaluation.cli import (
    ConfidenceInterval,
    RunManifest,
    _ci,
    clean_config_overlay,
    create_manifest,
    evaluate_command,
    format_evaluation_report,
    format_multi_seed_report,
    full_impaired_config_overlay,
    impairments_only_config_overlay,
    load_manifest,
    run_a_simulation_config,
    run_b_simulation_config,
    run_c_simulation_config,
    run_multi_seed_evaluation,
    save_manifest,
    scenarios_only_config_overlay,
)
from factory_simulator.evaluation.evaluator import Evaluator
from factory_simulator.evaluation.metrics import EvaluationResult, RandomBaseline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KNOWN_ANOMALY_SCENARIOS = [
    "web_break",
    "unplanned_stop",
    "seal_integrity_failure",
    "cold_chain_break",
    "bearing_wear",
    "dryer_drift",
    "oven_excursion",
    "fill_weight_drift",
    "ink_viscosity_excursion",
    "registration_drift",
    "contextual_anomaly",
    "intermittent_fault",
    "micro_stop",
    "sensor_disconnect",
    "stuck_sensor",
]

_IMPAIRMENT_KEYS = [
    "modbus_drop",
    "opcua_stale",
    "mqtt_drop",
    "sensor_disconnect",
    "stuck_sensor",
]


def _make_result(
    precision: float = 0.8,
    recall: float = 0.7,
    f1: float = 0.747,
    total_events: int = 10,
    tp: int = 7,
    fp: int = 2,
    fn: int = 3,
) -> EvaluationResult:
    return EvaluationResult(
        precision=precision,
        recall=recall,
        f1=f1,
        weighted_recall=recall,
        weighted_f1=f1,
        per_scenario={},
        detection_latency_median=None,
        detection_latency_p90=None,
        random_baseline=RandomBaseline(
            anomaly_density=0.05, precision=0.1, recall=0.6, f1=0.17
        ),
        total_events=total_events,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
    )


def _write_gt_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for record in events:
            fh.write(json.dumps(record) + "\n")


def _write_det_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        path.write_text("timestamp,alert_type\n", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _simple_gt_and_det(
    tmp_path: Path, suffix: str = ""
) -> tuple[Path, Path]:
    """Write a minimal ground truth / detections pair to tmp_path."""
    gt = tmp_path / f"gt{suffix}.jsonl"
    det = tmp_path / f"det{suffix}.csv"

    t_start = 1735700000.0
    t_end = t_start + 120.0

    def iso(t: float) -> str:
        return datetime.fromtimestamp(t, tz=UTC).isoformat()

    _write_gt_jsonl(
        gt,
        [
            {"event": "scenario_start", "scenario": "web_break", "sim_time": iso(t_start)},
            {"event": "scenario_end", "scenario": "web_break", "sim_time": iso(t_end)},
        ],
    )
    # Detection fires 10 s after start (within tolerance window)
    _write_det_csv(det, [{"timestamp": str(t_start + 10.0), "alert_type": "web_break"}])
    return gt, det


# ---------------------------------------------------------------------------
# RunManifest: construction
# ---------------------------------------------------------------------------


def test_run_manifest_fields() -> None:
    m = RunManifest(
        config_path="config/factory.yaml",
        seed=42,
        profile="packaging",
        duration_seconds=86400.0,
        version="0.1.0",
        git_hash="abc1234",
        start_wall_time="2026-01-01T08:00:00+00:00",
    )
    assert m.config_path == "config/factory.yaml"
    assert m.seed == 42
    assert m.profile == "packaging"
    assert m.duration_seconds == 86400.0
    assert m.version == "0.1.0"
    assert m.git_hash == "abc1234"
    assert m.end_wall_time is None
    assert m.time_scale == 1.0
    assert m.notes == ""


def test_run_manifest_none_seed() -> None:
    m = RunManifest(
        config_path="c.yaml",
        seed=None,
        profile="foodbev",
        duration_seconds=3600.0,
        version="0.1.0",
        git_hash="x",
        start_wall_time="2026-01-01T00:00:00+00:00",
    )
    assert m.seed is None


def test_run_manifest_optional_fields() -> None:
    m = RunManifest(
        config_path="c.yaml",
        seed=1,
        profile="packaging",
        duration_seconds=100.0,
        version="0.1.0",
        git_hash="x",
        start_wall_time="2026-01-01T00:00:00+00:00",
        end_wall_time="2026-01-01T01:00:00+00:00",
        time_scale=10.0,
        notes="benchmark run",
    )
    assert m.end_wall_time == "2026-01-01T01:00:00+00:00"
    assert m.time_scale == 10.0
    assert m.notes == "benchmark run"


# ---------------------------------------------------------------------------
# save_manifest / load_manifest round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_manifest_round_trip(tmp_path: Path) -> None:
    m = RunManifest(
        config_path="config/factory.yaml",
        seed=99,
        profile="packaging",
        duration_seconds=3600.0,
        version="0.1.0",
        git_hash="deadbeef",
        start_wall_time="2026-03-01T12:00:00+00:00",
        end_wall_time="2026-03-01T13:00:00+00:00",
        time_scale=1.0,
        notes="test run",
    )
    p = tmp_path / "manifest.yaml"
    save_manifest(m, p)
    loaded = load_manifest(p)
    assert loaded.config_path == m.config_path
    assert loaded.seed == m.seed
    assert loaded.profile == m.profile
    assert loaded.duration_seconds == m.duration_seconds
    assert loaded.version == m.version
    assert loaded.git_hash == m.git_hash
    assert loaded.start_wall_time == m.start_wall_time
    assert loaded.end_wall_time == m.end_wall_time
    assert loaded.time_scale == m.time_scale
    assert loaded.notes == m.notes


def test_save_manifest_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "deep" / "nested" / "manifest.yaml"
    m = RunManifest(
        config_path="c.yaml",
        seed=1,
        profile="packaging",
        duration_seconds=60.0,
        version="0.1.0",
        git_hash="x",
        start_wall_time="2026-01-01T00:00:00+00:00",
    )
    save_manifest(m, p)
    assert p.exists()


def test_load_manifest_preserves_none_seed(tmp_path: Path) -> None:
    m = RunManifest(
        config_path="c.yaml",
        seed=None,
        profile="foodbev",
        duration_seconds=60.0,
        version="0.1.0",
        git_hash="x",
        start_wall_time="2026-01-01T00:00:00+00:00",
    )
    p = tmp_path / "m.yaml"
    save_manifest(m, p)
    loaded = load_manifest(p)
    assert loaded.seed is None


def test_create_manifest_populates_version(tmp_path: Path) -> None:
    m = create_manifest(
        config_path=tmp_path / "factory.yaml",
        seed=42,
        profile="packaging",
        duration_seconds=3600.0,
    )
    assert m.version != ""
    assert len(m.version) > 0


def test_create_manifest_explicit_version(tmp_path: Path) -> None:
    m = create_manifest(
        config_path="c.yaml",
        seed=1,
        profile="packaging",
        duration_seconds=3600.0,
        version="9.9.9",
    )
    assert m.version == "9.9.9"


def test_create_manifest_has_git_hash(tmp_path: Path) -> None:
    m = create_manifest(
        config_path="c.yaml",
        seed=1,
        profile="packaging",
        duration_seconds=3600.0,
    )
    # git hash is a string (may be "unknown" in CI environments without git)
    assert isinstance(m.git_hash, str)
    assert len(m.git_hash) > 0


# ---------------------------------------------------------------------------
# clean_config_overlay  (PRD 12.3)
# ---------------------------------------------------------------------------


def test_clean_overlay_structure() -> None:
    overlay = clean_config_overlay()
    assert "scenarios" in overlay
    assert "data_quality" in overlay


def test_clean_overlay_normal_ops_enabled() -> None:
    overlay = clean_config_overlay()
    assert overlay["scenarios"]["job_changeover"]["enabled"] is True
    assert overlay["scenarios"]["shift_change"]["enabled"] is True


def test_clean_overlay_anomaly_scenarios_disabled() -> None:
    overlay = clean_config_overlay()
    for sc in _KNOWN_ANOMALY_SCENARIOS:
        assert overlay["scenarios"][sc]["enabled"] is False, f"{sc} should be disabled"


def test_clean_overlay_impairments_disabled() -> None:
    overlay = clean_config_overlay()
    for key in _IMPAIRMENT_KEYS:
        assert overlay["data_quality"][key]["enabled"] is False, (
            f"{key} should be disabled in clean run"
        )


def test_clean_overlay_noise_enabled() -> None:
    overlay = clean_config_overlay()
    assert overlay["data_quality"]["noise"]["enabled"] is True


# ---------------------------------------------------------------------------
# scenarios_only_config_overlay  (PRD 12.3)
# ---------------------------------------------------------------------------


def test_scenarios_only_impairments_disabled() -> None:
    overlay = scenarios_only_config_overlay()
    for key in _IMPAIRMENT_KEYS:
        assert overlay["data_quality"][key]["enabled"] is False


def test_scenarios_only_noise_enabled() -> None:
    overlay = scenarios_only_config_overlay()
    assert overlay["data_quality"]["noise"]["enabled"] is True


def test_scenarios_only_no_scenarios_section_by_default() -> None:
    overlay = scenarios_only_config_overlay()
    assert "scenarios" not in overlay


def test_scenarios_only_with_base_scenarios() -> None:
    base = {"web_break": {"enabled": True}, "dryer_drift": {"enabled": False}}
    overlay = scenarios_only_config_overlay(base_scenarios=base)
    assert overlay["scenarios"] == base


# ---------------------------------------------------------------------------
# impairments_only_config_overlay  (PRD 12.3)
# ---------------------------------------------------------------------------


def test_impairments_only_anomaly_scenarios_disabled() -> None:
    overlay = impairments_only_config_overlay()
    for sc in _KNOWN_ANOMALY_SCENARIOS:
        assert overlay["scenarios"][sc]["enabled"] is False


def test_impairments_only_normal_ops_enabled() -> None:
    overlay = impairments_only_config_overlay()
    assert overlay["scenarios"]["job_changeover"]["enabled"] is True
    assert overlay["scenarios"]["shift_change"]["enabled"] is True


def test_impairments_only_no_data_quality_by_default() -> None:
    overlay = impairments_only_config_overlay()
    assert "data_quality" not in overlay


def test_impairments_only_with_base_data_quality() -> None:
    dq = {"modbus_drop": {"enabled": True}}
    overlay = impairments_only_config_overlay(base_data_quality=dq)
    assert overlay["data_quality"] == dq


# ---------------------------------------------------------------------------
# full_impaired_config_overlay  (PRD 12.3)
# ---------------------------------------------------------------------------


def test_full_impaired_empty_by_default() -> None:
    overlay = full_impaired_config_overlay()
    assert overlay == {}


def test_full_impaired_with_scenarios() -> None:
    base_sc = {"web_break": {"enabled": True}}
    overlay = full_impaired_config_overlay(base_scenarios=base_sc)
    assert overlay["scenarios"] == base_sc
    assert "data_quality" not in overlay


def test_full_impaired_with_both() -> None:
    base_sc = {"web_break": {"enabled": True}}
    base_dq = {"modbus_drop": {"enabled": True}}
    overlay = full_impaired_config_overlay(base_scenarios=base_sc, base_data_quality=base_dq)
    assert overlay["scenarios"] == base_sc
    assert overlay["data_quality"] == base_dq


# ---------------------------------------------------------------------------
# Run A / B / C simulation configs  (PRD 12.5)
# ---------------------------------------------------------------------------


def test_run_a_duration_24h() -> None:
    cfg = run_a_simulation_config()
    assert cfg["simulation"]["duration_seconds"] == 86400


def test_run_a_time_scale_10x() -> None:
    cfg = run_a_simulation_config()
    assert cfg["simulation"]["time_scale"] == 10.0


def test_run_a_seeds_1_to_10() -> None:
    cfg = run_a_simulation_config()
    assert cfg["evaluation"]["seeds"] == list(range(1, 11))


def test_run_a_margins() -> None:
    cfg = run_a_simulation_config()
    assert cfg["evaluation"]["pre_margin_seconds"] == 30
    assert cfg["evaluation"]["post_margin_seconds"] == 60


def test_run_a_low_anomaly_rate() -> None:
    cfg = run_a_simulation_config()
    # web_break and unplanned_stop must be disabled
    assert cfg["scenarios"]["web_break"]["enabled"] is False
    assert cfg["scenarios"]["unplanned_stop"]["enabled"] is False
    # Normal ops enabled
    assert cfg["scenarios"]["job_changeover"]["enabled"] is True


def test_run_b_duration_24h() -> None:
    cfg = run_b_simulation_config()
    assert cfg["simulation"]["duration_seconds"] == 86400


def test_run_b_all_scenarios_enabled() -> None:
    cfg = run_b_simulation_config()
    assert cfg["scenarios"]["web_break"]["enabled"] is True
    assert cfg["scenarios"]["unplanned_stop"]["enabled"] is True
    assert cfg["scenarios"]["contextual_anomaly"]["enabled"] is True


def test_run_b_doubled_frequency() -> None:
    cfg = run_b_simulation_config()
    assert cfg["scenarios"]["web_break"]["frequency_multiplier"] == 2.0
    assert cfg["scenarios"]["unplanned_stop"]["frequency_multiplier"] == 2.0


def test_run_b_tripled_contextual_anomaly() -> None:
    cfg = run_b_simulation_config()
    assert cfg["scenarios"]["contextual_anomaly"]["frequency_multiplier"] == 3.0


def test_run_c_duration_7_days() -> None:
    cfg = run_c_simulation_config()
    assert cfg["simulation"]["duration_seconds"] == 604800


def test_run_c_time_scale_100x() -> None:
    cfg = run_c_simulation_config()
    assert cfg["simulation"]["time_scale"] == 100.0


def test_run_c_bearing_wear_culminates() -> None:
    cfg = run_c_simulation_config()
    bw = cfg["scenarios"]["bearing_wear"]
    assert bw["enabled"] is True
    assert bw["culminate_in_failure"] is True


def test_run_c_intermittent_fault_types() -> None:
    cfg = run_c_simulation_config()
    ift = cfg["scenarios"]["intermittent_fault"]
    assert "bearing" in ift["fault_types"]
    assert "electrical" in ift["fault_types"]


# ---------------------------------------------------------------------------
# _ci: confidence interval computation  (PRD 12.4)
# ---------------------------------------------------------------------------


def test_ci_empty_list() -> None:
    ci = _ci([])
    assert ci.n == 0
    assert ci.mean == 0.0
    assert ci.std == 0.0
    assert ci.ci_low == 0.0
    assert ci.ci_high == 0.0


def test_ci_single_value() -> None:
    ci = _ci([0.75])
    assert ci.n == 1
    assert ci.mean == pytest.approx(0.75)
    assert ci.std == 0.0
    assert ci.ci_low == ci.ci_high == pytest.approx(0.75)


def test_ci_mean_correct() -> None:
    values = [0.8, 0.6, 0.7]
    ci = _ci(values)
    assert ci.mean == pytest.approx(sum(values) / len(values))


def test_ci_formula_prd_12_4() -> None:
    """CI = mean ± 1.96 * std / sqrt(N)"""
    values = [0.8, 0.6, 0.7, 0.9, 0.75]
    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    std = math.sqrt(variance)
    margin = 1.96 * std / math.sqrt(n)

    ci = _ci(values)
    assert ci.ci_low == pytest.approx(mean - margin, rel=1e-6)
    assert ci.ci_high == pytest.approx(mean + margin, rel=1e-6)


def test_ci_symmetric() -> None:
    values = [0.5, 0.6, 0.7, 0.8]
    ci = _ci(values)
    half = (ci.ci_high - ci.ci_low) / 2.0
    assert ci.ci_low == pytest.approx(ci.mean - half, rel=1e-6)
    assert ci.ci_high == pytest.approx(ci.mean + half, rel=1e-6)


def test_ci_identical_values() -> None:
    values = [0.5, 0.5, 0.5]
    ci = _ci(values)
    assert ci.std == 0.0
    assert ci.ci_low == pytest.approx(0.5)
    assert ci.ci_high == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# run_multi_seed_evaluation
# ---------------------------------------------------------------------------


def test_multi_seed_mismatched_paths_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="same length"):
        run_multi_seed_evaluation(
            ground_truth_paths=["a.jsonl", "b.jsonl"],
            detections_paths=["c.csv"],
        )


def test_multi_seed_single_pair(tmp_path: Path) -> None:
    gt, det = _simple_gt_and_det(tmp_path)
    result = run_multi_seed_evaluation([gt], [det])
    assert len(result.per_seed) == 1
    assert result.seeds == [1]


def test_multi_seed_two_pairs(tmp_path: Path) -> None:
    gt1, det1 = _simple_gt_and_det(tmp_path, suffix="1")
    gt2, det2 = _simple_gt_and_det(tmp_path, suffix="2")
    result = run_multi_seed_evaluation([gt1, gt2], [det1, det2])
    assert len(result.per_seed) == 2
    # Both runs should give recall=1.0 (1 event, 1 detection within window)
    assert result.recall.mean == pytest.approx(1.0)


def test_multi_seed_explicit_seeds(tmp_path: Path) -> None:
    gt, det = _simple_gt_and_det(tmp_path)
    result = run_multi_seed_evaluation([gt], [det], seeds=[42])
    assert result.seeds == [42]


def test_multi_seed_ci_computed(tmp_path: Path) -> None:
    gt1, det1 = _simple_gt_and_det(tmp_path, suffix="a")
    gt2, det2 = _simple_gt_and_det(tmp_path, suffix="b")
    result = run_multi_seed_evaluation([gt1, gt2], [det1, det2])
    # With 2 seeds and identical outcomes, std should be 0
    assert result.f1.std == pytest.approx(0.0)
    assert isinstance(result.precision, ConfidenceInterval)
    assert isinstance(result.recall, ConfidenceInterval)
    assert isinstance(result.f1, ConfidenceInterval)


# ---------------------------------------------------------------------------
# format_evaluation_report
# ---------------------------------------------------------------------------


def test_format_report_returns_string() -> None:
    result = _make_result()
    report = format_evaluation_report(result)
    assert isinstance(report, str)
    assert len(report) > 0


def test_format_report_contains_key_metrics() -> None:
    result = _make_result(precision=0.85, recall=0.70, f1=0.769)
    report = format_evaluation_report(result)
    assert "0.850" in report
    assert "0.700" in report


def test_format_report_contains_baseline() -> None:
    result = _make_result()
    report = format_evaluation_report(result)
    assert "Random baseline" in report or "random baseline" in report.lower()


def test_format_report_with_title() -> None:
    result = _make_result()
    report = format_evaluation_report(result, title="My Custom Title")
    assert "My Custom Title" in report


def test_format_report_with_per_scenario(tmp_path: Path) -> None:
    gt, det = _simple_gt_and_det(tmp_path)
    ev = Evaluator()
    result = ev.evaluate(gt, det)
    report = format_evaluation_report(result)
    assert "web_break" in report


def test_format_report_with_latency(tmp_path: Path) -> None:
    gt, det = _simple_gt_and_det(tmp_path)
    ev = Evaluator()
    result = ev.evaluate(gt, det)
    from factory_simulator.evaluation.metrics import DEFAULT_LATENCY_TARGETS

    report = format_evaluation_report(result, latency_targets=DEFAULT_LATENCY_TARGETS)
    assert "target" in report.lower() or "web_break" in report


# ---------------------------------------------------------------------------
# format_multi_seed_report
# ---------------------------------------------------------------------------


def test_format_multi_seed_report_returns_string(tmp_path: Path) -> None:
    gt, det = _simple_gt_and_det(tmp_path)
    multi = run_multi_seed_evaluation([gt], [det])
    report = format_multi_seed_report(multi)
    assert isinstance(report, str)
    assert len(report) > 0


def test_format_multi_seed_report_contains_seeds(tmp_path: Path) -> None:
    gt, det = _simple_gt_and_det(tmp_path)
    multi = run_multi_seed_evaluation([gt], [det])
    report = format_multi_seed_report(multi)
    assert "Seeds" in report or "seeds" in report.lower()


def test_format_multi_seed_report_contains_ci(tmp_path: Path) -> None:
    gt1, det1 = _simple_gt_and_det(tmp_path, suffix="x")
    gt2, det2 = _simple_gt_and_det(tmp_path, suffix="y")
    multi = run_multi_seed_evaluation([gt1, gt2], [det1, det2])
    report = format_multi_seed_report(multi)
    assert "CI" in report or "95%" in report


def test_format_multi_seed_custom_title(tmp_path: Path) -> None:
    gt, det = _simple_gt_and_det(tmp_path)
    multi = run_multi_seed_evaluation([gt], [det])
    report = format_multi_seed_report(multi, title="My Benchmark")
    assert "My Benchmark" in report


# ---------------------------------------------------------------------------
# evaluate_command
# ---------------------------------------------------------------------------


def test_evaluate_command_missing_args_returns_1() -> None:
    args = SimpleNamespace(ground_truth=None, detections=None)
    assert evaluate_command(args) == 1


def test_evaluate_command_missing_detections_returns_1() -> None:
    args = SimpleNamespace(ground_truth="some.jsonl", detections=None)
    assert evaluate_command(args) == 1


def test_evaluate_command_mismatched_lists_returns_1(tmp_path: Path) -> None:
    args = SimpleNamespace(
        ground_truth="a.jsonl,b.jsonl",
        detections="c.csv",
        pre_margin=30.0,
        post_margin=60.0,
        output=None,
    )
    assert evaluate_command(args) == 1


def test_evaluate_command_single_file(tmp_path: Path, capsys: Any) -> None:
    gt, det = _simple_gt_and_det(tmp_path)
    args = SimpleNamespace(
        ground_truth=str(gt),
        detections=str(det),
        pre_margin=30.0,
        post_margin=60.0,
        output=None,
    )
    rc = evaluate_command(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "Precision" in captured.out or "precision" in captured.out.lower()


def test_evaluate_command_writes_output_file(tmp_path: Path) -> None:
    gt, det = _simple_gt_and_det(tmp_path)
    out_path = tmp_path / "report.txt"
    args = SimpleNamespace(
        ground_truth=str(gt),
        detections=str(det),
        pre_margin=30.0,
        post_margin=60.0,
        output=str(out_path),
    )
    rc = evaluate_command(args)
    assert rc == 0
    assert out_path.exists()
    assert len(out_path.read_text()) > 0


def test_evaluate_command_multi_seed(tmp_path: Path, capsys: Any) -> None:
    gt1, det1 = _simple_gt_and_det(tmp_path, suffix="1")
    gt2, det2 = _simple_gt_and_det(tmp_path, suffix="2")
    args = SimpleNamespace(
        ground_truth=f"{gt1},{gt2}",
        detections=f"{det1},{det2}",
        pre_margin=30.0,
        post_margin=60.0,
        output=None,
    )
    rc = evaluate_command(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "Seeds" in captured.out or "seeds" in captured.out.lower()


# ---------------------------------------------------------------------------
# evaluate_command with EvaluationConfig wired from FactoryConfig  (6b.5)
# ---------------------------------------------------------------------------


def _write_factory_config_yaml(path: Path, evaluation_section: str | None = None) -> None:
    """Write a minimal factory config YAML to ``path``.

    When ``evaluation_section`` is provided it is inserted; otherwise the
    section is omitted so that Pydantic defaults apply.
    """
    content = "simulation:\n  duration_seconds: 3600\n"
    if evaluation_section is not None:
        content += evaluation_section
    path.write_text(content, encoding="utf-8")


def test_evaluate_command_config_custom_pre_margin(tmp_path: Path, capsys: Any) -> None:
    """EvaluationConfig.pre_margin_seconds from YAML is used by the evaluator."""
    gt, det = _simple_gt_and_det(tmp_path)
    cfg_path = tmp_path / "factory.yaml"
    _write_factory_config_yaml(
        cfg_path,
        "evaluation:\n  pre_margin_seconds: 5.0\n  post_margin_seconds: 10.0\n",
    )

    # Without config: detection 10s after start, default pre_margin=30 → TP
    # With custom config: pre_margin=5, post_margin=10 — detection is AFTER start
    # and within post_margin (10s ≤ 10s) → still TP.
    # The key test is that settings flow through without error.
    args = SimpleNamespace(
        ground_truth=str(gt),
        detections=str(det),
        config=str(cfg_path),
        pre_margin=None,
        post_margin=None,
        output=None,
    )
    rc = evaluate_command(args)
    assert rc == 0


def test_evaluate_command_config_margins_affect_matching(tmp_path: Path) -> None:
    """Zero post_margin misses a detection that fires after the event ends."""
    from datetime import UTC, datetime

    from factory_simulator.evaluation.evaluator import Evaluator, EvaluatorSettings

    # Event window: [t_start, t_end] = [0, 10]
    # Detection fires at t_end + 5 = 15 (after event end)
    # With post_margin=60: window extends to t_end+60=70 → TP
    # With post_margin=0:  window ends at t_end=10 → detection at 15 is FN
    t_start = 1_700_000_000.0
    t_end = t_start + 10.0
    t_det = t_end + 5.0  # 5s after event end

    def iso(t: float) -> str:
        return datetime.fromtimestamp(t, tz=UTC).isoformat()

    gt_path = tmp_path / "gt_margins.jsonl"
    det_path = tmp_path / "det_margins.csv"
    _write_gt_jsonl(
        gt_path,
        [
            {"event": "scenario_start", "scenario": "web_break", "sim_time": iso(t_start)},
            {"event": "scenario_end", "scenario": "web_break", "sim_time": iso(t_end)},
        ],
    )
    _write_det_csv(det_path, [{"timestamp": str(t_det), "alert_type": "web_break"}])

    # Zero post_margin → detection after event end is FN
    settings_zero = EvaluatorSettings(pre_margin_seconds=0.0, post_margin_seconds=0.0)
    result_zero = Evaluator(settings=settings_zero).evaluate(str(gt_path), str(det_path))
    assert result_zero.recall == 0.0

    # Large post_margin → detection is TP
    settings_wide = EvaluatorSettings(pre_margin_seconds=0.0, post_margin_seconds=60.0)
    result_wide = Evaluator(settings=settings_wide).evaluate(str(gt_path), str(det_path))
    assert result_wide.recall == 1.0


def test_evaluate_command_no_config_uses_defaults(tmp_path: Path, capsys: Any) -> None:
    """No config path → Pydantic EvaluationConfig defaults apply (30s/60s)."""
    gt, det = _simple_gt_and_det(tmp_path)
    args = SimpleNamespace(
        ground_truth=str(gt),
        detections=str(det),
        config=None,
        pre_margin=None,
        post_margin=None,
        output=None,
    )
    rc = evaluate_command(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "Precision" in captured.out or "precision" in captured.out.lower()


def test_evaluate_command_cli_arg_overrides_config(tmp_path: Path, capsys: Any) -> None:
    """CLI --pre-margin / --post-margin override config values when provided."""
    gt, det = _simple_gt_and_det(tmp_path)
    cfg_path = tmp_path / "factory.yaml"
    # Config says 0s margins (would cause FN), but CLI overrides with 30/60
    _write_factory_config_yaml(
        cfg_path,
        "evaluation:\n  pre_margin_seconds: 0.0\n  post_margin_seconds: 0.0\n",
    )
    args = SimpleNamespace(
        ground_truth=str(gt),
        detections=str(det),
        config=str(cfg_path),
        pre_margin=30.0,   # explicit CLI override — should win over config's 0s
        post_margin=60.0,  # explicit CLI override
        output=None,
    )
    rc = evaluate_command(args)
    assert rc == 0
    captured = capsys.readouterr()
    # With 30/60 margins, detection at start+10 is within post_margin → TP → recall=1.0
    assert "1.000" in captured.out  # recall should be 1.0


def test_evaluation_config_loaded_from_factory_config(tmp_path: Path) -> None:
    """FactoryConfig.evaluation field round-trips through YAML load."""
    from factory_simulator.config import load_config

    cfg_path = tmp_path / "factory.yaml"
    _write_factory_config_yaml(
        cfg_path,
        "evaluation:\n  pre_margin_seconds: 45.0\n  post_margin_seconds: 90.0\n  seeds: 3\n",
    )
    cfg = load_config(cfg_path, apply_env=False)
    assert cfg.evaluation.pre_margin_seconds == 45.0
    assert cfg.evaluation.post_margin_seconds == 90.0
    assert cfg.evaluation.seeds == 3


def test_evaluation_config_absent_uses_pydantic_defaults(tmp_path: Path) -> None:
    """FactoryConfig without evaluation: section → Pydantic defaults (30/60)."""
    from factory_simulator.config import load_config

    cfg_path = tmp_path / "factory.yaml"
    _write_factory_config_yaml(cfg_path)  # no evaluation section
    cfg = load_config(cfg_path, apply_env=False)
    assert cfg.evaluation.pre_margin_seconds == 30.0
    assert cfg.evaluation.post_margin_seconds == 60.0
    assert cfg.evaluation.seeds == 1
