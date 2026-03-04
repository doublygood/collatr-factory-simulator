"""Evaluation CLI subcommand, run manifests, clean/impaired pairing helpers,
and multi-seed evaluation with confidence intervals.

PRD Reference:
- Section 12.2 (Dataset Generation) — RunManifest
- Section 12.3 (Clean/Impaired Pairing) — config overlays
- Section 12.4 (Evaluation Metrics) — multi-seed CI formula
- Section 12.5 (Recommended Run Configurations) — Run A/B/C helpers
"""

from __future__ import annotations

import math
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from factory_simulator.evaluation.evaluator import Evaluator, EvaluatorSettings
from factory_simulator.evaluation.metrics import DEFAULT_LATENCY_TARGETS, EvaluationResult

# ---------------------------------------------------------------------------
# Run Manifest  (PRD 12.2)
# ---------------------------------------------------------------------------


@dataclass
class RunManifest:
    """YAML metadata file recorded alongside each simulator run.

    Enables exact reproduction: given this file, anyone can re-run the
    simulation and get byte-identical output (Rule 13, PRD 12.2).

    Fields
    ------
    config_path      : Path to the YAML config used for the run.
    seed             : Random seed (None = time-based, non-reproducible).
    profile          : ``"packaging"`` or ``"foodbev"``.
    duration_seconds : Simulated duration in seconds.
    version          : Simulator package version string.
    git_hash         : Short git commit hash at run time.
    start_wall_time  : ISO 8601 UTC timestamp when the run started.
    end_wall_time    : ISO 8601 UTC timestamp when the run finished.
    time_scale       : Simulation speed multiplier (1.0 = real-time).
    notes            : Free-form annotation for this run.
    """

    config_path: str
    seed: int | None
    profile: str
    duration_seconds: float
    version: str
    git_hash: str
    start_wall_time: str
    end_wall_time: str | None = None
    time_scale: float = 1.0
    notes: str = ""


def _get_git_hash() -> str:
    """Return current HEAD short git hash, or ``'unknown'`` if unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return "unknown"


def _now_iso() -> str:
    """Current UTC time as ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def create_manifest(
    config_path: str | Path,
    seed: int | None,
    profile: str,
    duration_seconds: float,
    time_scale: float = 1.0,
    notes: str = "",
    version: str | None = None,
) -> RunManifest:
    """Create a new RunManifest populated with version and git hash."""
    from factory_simulator import __version__

    return RunManifest(
        config_path=str(config_path),
        seed=seed,
        profile=profile,
        duration_seconds=duration_seconds,
        time_scale=time_scale,
        notes=notes,
        version=version if version is not None else __version__,
        git_hash=_get_git_hash(),
        start_wall_time=_now_iso(),
    )


def save_manifest(manifest: RunManifest, path: str | Path) -> None:
    """Serialise a RunManifest to a YAML file, creating parent dirs as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(manifest)
    with p.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=False)


def load_manifest(path: str | Path) -> RunManifest:
    """Deserialise a RunManifest from a YAML file."""
    with Path(path).open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)
    return RunManifest(**data)


# ---------------------------------------------------------------------------
# Clean / Impaired config overlays  (PRD 12.3)
# ---------------------------------------------------------------------------

# Scenario types that are normal operations (not anomalies).
_NORMAL_OPERATION_SCENARIOS: frozenset[str] = frozenset({"job_changeover", "shift_change"})

# All anomaly scenario types recognised by the simulator.
_ANOMALY_SCENARIOS: tuple[str, ...] = (
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
)

# Data-quality keys disabled in clean runs (PRD 12.3).
_IMPAIRMENT_KEYS: tuple[str, ...] = (
    "modbus_drop",
    "opcua_stale",
    "mqtt_drop",
    "sensor_disconnect",
    "stuck_sensor",
)


def clean_config_overlay() -> dict[str, Any]:
    """Config overlay for PRD 12.3 clean runs.

    Disables all anomaly scenarios.  Keeps ``job_changeover`` and
    ``shift_change`` (normal operations, not anomalies).  Disables all
    communication and sensor impairments.  Noise stays on (it is part of the
    base signal, not an injected impairment).

    Apply as a deep-merge overlay on top of a base simulation config.
    """
    scenarios: dict[str, Any] = {
        "job_changeover": {"enabled": True},
        "shift_change": {"enabled": True},
    }
    for sc in _ANOMALY_SCENARIOS:
        scenarios[sc] = {"enabled": False}

    data_quality: dict[str, Any] = {"noise": {"enabled": True}}
    for key in _IMPAIRMENT_KEYS:
        data_quality[key] = {"enabled": False}

    return {"scenarios": scenarios, "data_quality": data_quality}


def scenarios_only_config_overlay(
    base_scenarios: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Config overlay: scenarios enabled, communication/sensor impairments off.

    Produces the *scenarios-only* impaired run from PRD 12.3 paired design.
    ``base_scenarios`` holds the scenario sub-config for the impaired run;
    when ``None`` the scenarios section is omitted (caller's base config wins).
    """
    data_quality: dict[str, Any] = {"noise": {"enabled": True}}
    for key in _IMPAIRMENT_KEYS:
        data_quality[key] = {"enabled": False}

    overlay: dict[str, Any] = {"data_quality": data_quality}
    if base_scenarios is not None:
        overlay["scenarios"] = base_scenarios
    return overlay


def impairments_only_config_overlay(
    base_data_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Config overlay: impairments enabled, anomaly scenarios disabled.

    Produces the *impairments-only* impaired run from PRD 12.3.
    Normal operations (``job_changeover``, ``shift_change``) stay enabled.
    ``base_data_quality`` holds the data-quality sub-config to apply;
    when ``None`` the data_quality section is omitted.
    """
    scenarios: dict[str, Any] = {
        "job_changeover": {"enabled": True},
        "shift_change": {"enabled": True},
    }
    for sc in _ANOMALY_SCENARIOS:
        scenarios[sc] = {"enabled": False}

    overlay: dict[str, Any] = {"scenarios": scenarios}
    if base_data_quality is not None:
        overlay["data_quality"] = base_data_quality
    return overlay


def full_impaired_config_overlay(
    base_scenarios: dict[str, Any] | None = None,
    base_data_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Config overlay: all scenarios and impairments enabled.

    Produces the *full impaired* run from PRD 12.3.  Both ``base_scenarios``
    and ``base_data_quality`` are optional; when provided they replace the
    corresponding sections in the base config.
    """
    overlay: dict[str, Any] = {}
    if base_scenarios is not None:
        overlay["scenarios"] = base_scenarios
    if base_data_quality is not None:
        overlay["data_quality"] = base_data_quality
    return overlay


# ---------------------------------------------------------------------------
# PRD 12.5 Recommended run configurations
# ---------------------------------------------------------------------------


def run_a_simulation_config() -> dict[str, Any]:
    """PRD 12.5 Run A: Normal Operations (24 simulated hours).

    Low anomaly rate — three shifts, job changeovers, dryer drift, micro-stops.
    Tests false positive rate under near-normal conditions.
    Time compression: 10x (2.4 real hours).
    """
    return {
        "simulation": {
            "duration_seconds": 86400,
            "time_scale": 10.0,
        },
        "evaluation": {
            "pre_margin_seconds": 30,
            "post_margin_seconds": 60,
            "seeds": list(range(1, 11)),
        },
        "scenarios": {
            "job_changeover": {"enabled": True},
            "shift_change": {"enabled": True},
            "micro_stop": {"enabled": True},
            "dryer_drift": {"enabled": True, "frequency_per_hour": 0.333},
            "ink_viscosity_excursion": {"enabled": True},
            "web_break": {"enabled": False},
            "unplanned_stop": {"enabled": False},
            "seal_integrity_failure": {"enabled": False},
            "cold_chain_break": {"enabled": False},
            "bearing_wear": {"enabled": False},
            "fill_weight_drift": {"enabled": False},
            "oven_excursion": {"enabled": False},
            "registration_drift": {"enabled": False},
            "contextual_anomaly": {"enabled": False},
            "intermittent_fault": {"enabled": False},
            "sensor_disconnect": {"enabled": False},
            "stuck_sensor": {"enabled": False},
        },
        "data_quality": {
            "noise": {"enabled": True},
            "modbus_drop": {"enabled": True},
            "opcua_stale": {"enabled": True},
            "mqtt_drop": {"enabled": True},
            "sensor_disconnect": {"enabled": False},
            "stuck_sensor": {"enabled": False},
            "duplicate_timestamps": {"enabled": True},
        },
    }


def run_b_simulation_config() -> dict[str, Any]:
    """PRD 12.5 Run B: Heavy Anomaly (24 simulated hours).

    All scenarios enabled; web_break and unplanned_stop doubled, contextual
    anomaly tripled.  All impairments on, sensor_disconnect doubled.
    Tests detection rate under heavy fault load.
    Time compression: 10x (2.4 real hours).
    """
    return {
        "simulation": {
            "duration_seconds": 86400,
            "time_scale": 10.0,
        },
        "evaluation": {
            "pre_margin_seconds": 30,
            "post_margin_seconds": 60,
            "seeds": list(range(1, 11)),
        },
        "scenarios": {
            "job_changeover": {"enabled": True},
            "shift_change": {"enabled": True},
            "micro_stop": {"enabled": True},
            "web_break": {"enabled": True, "frequency_multiplier": 2.0},
            "unplanned_stop": {"enabled": True, "frequency_multiplier": 2.0},
            "seal_integrity_failure": {"enabled": True},
            "cold_chain_break": {"enabled": True},
            "bearing_wear": {"enabled": True},
            "dryer_drift": {"enabled": True},
            "oven_excursion": {"enabled": True},
            "fill_weight_drift": {"enabled": True},
            "ink_viscosity_excursion": {"enabled": True},
            "registration_drift": {"enabled": True},
            "contextual_anomaly": {"enabled": True, "frequency_multiplier": 3.0},
            "intermittent_fault": {"enabled": True},
            "sensor_disconnect": {"enabled": True},
            "stuck_sensor": {"enabled": True},
        },
        "data_quality": {
            "noise": {"enabled": True},
            "modbus_drop": {"enabled": True},
            "opcua_stale": {"enabled": True},
            "mqtt_drop": {"enabled": True},
            "sensor_disconnect": {"enabled": True, "frequency_multiplier": 2.0},
            "stuck_sensor": {"enabled": True},
            "duplicate_timestamps": {"enabled": True},
        },
    }


def run_c_simulation_config() -> dict[str, Any]:
    """PRD 12.5 Run C: Long-Term Degradation (7 simulated days).

    100x batch mode (under 2 real hours, no live protocol serving).
    Bearing wear progresses to failure.  Intermittent faults evolve toward
    permanent failure.  Tests trend detection over long horizons.
    """
    return {
        "simulation": {
            "duration_seconds": 604800,  # 7 days
            "time_scale": 100.0,
        },
        "evaluation": {
            "pre_margin_seconds": 30,
            "post_margin_seconds": 60,
            "seeds": list(range(1, 11)),
        },
        "scenarios": {
            "job_changeover": {"enabled": True},
            "shift_change": {"enabled": True},
            "micro_stop": {"enabled": True},
            "bearing_wear": {
                "enabled": True,
                "start_hour": 0,
                "culminate_in_failure": True,
            },
            "intermittent_fault": {
                "enabled": True,
                "fault_types": ["bearing", "electrical"],
            },
            "web_break": {"enabled": True},
            "unplanned_stop": {"enabled": True},
            "dryer_drift": {"enabled": True},
            "ink_viscosity_excursion": {"enabled": True},
            "contextual_anomaly": {"enabled": True},
            # F&B-only scenarios: disabled in packaging context
            "seal_integrity_failure": {"enabled": False},
            "cold_chain_break": {"enabled": False},
            "oven_excursion": {"enabled": False},
            "fill_weight_drift": {"enabled": False},
            "registration_drift": {"enabled": False},
            "sensor_disconnect": {"enabled": False},
            "stuck_sensor": {"enabled": False},
        },
        "data_quality": {
            "noise": {"enabled": True},
            "modbus_drop": {"enabled": True},
            "opcua_stale": {"enabled": True},
            "mqtt_drop": {"enabled": True},
            "duplicate_timestamps": {"enabled": True},
        },
    }


def save_run_config(config: dict[str, Any], path: str | Path) -> None:
    """Write a run config dict to a YAML file (creates parent dirs)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# Multi-seed evaluation with confidence intervals  (PRD 12.4)
# ---------------------------------------------------------------------------


@dataclass
class ConfidenceInterval:
    """95% confidence interval for a metric computed over N seeds.

    PRD 12.4: ``CI = mean ± 1.96 * std / sqrt(N)``
    """

    mean: float
    std: float
    n: int
    ci_low: float
    ci_high: float


@dataclass
class MultiSeedResult:
    """Evaluation results aggregated over N seeds with confidence intervals.

    PRD 12.4: Run N=10 independent seeds. Report mean and standard deviation
    of precision, recall, F1, and detection latency (median and p90).
    """

    seeds: list[int]
    per_seed: list[EvaluationResult]
    precision: ConfidenceInterval
    recall: ConfidenceInterval
    f1: ConfidenceInterval
    weighted_recall: ConfidenceInterval
    weighted_f1: ConfidenceInterval


def _ci(values: list[float]) -> ConfidenceInterval:
    """Compute a 95% CI from a list of scalar values (sample std dev, N-1).

    Uses the PRD 12.4 formula: ``CI = mean ± 1.96 * std / sqrt(N)``.
    """
    n = len(values)
    if n == 0:
        return ConfidenceInterval(mean=0.0, std=0.0, n=0, ci_low=0.0, ci_high=0.0)
    mean = sum(values) / n
    if n == 1:
        return ConfidenceInterval(mean=mean, std=0.0, n=1, ci_low=mean, ci_high=mean)
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    std = math.sqrt(variance)
    margin = 1.96 * std / math.sqrt(n)
    return ConfidenceInterval(
        mean=mean, std=std, n=n, ci_low=mean - margin, ci_high=mean + margin
    )


def run_multi_seed_evaluation(
    ground_truth_paths: Sequence[str | Path],
    detections_paths: Sequence[str | Path],
    settings: EvaluatorSettings | None = None,
    seeds: list[int] | None = None,
) -> MultiSeedResult:
    """Evaluate over multiple seeds and compute 95% confidence intervals.

    ``ground_truth_paths[i]`` is paired with ``detections_paths[i]`` for each
    seed.  Both sequences must have the same length.

    PRD 12.4: Use N=10 consecutive integers starting from a base for published
    benchmarking.  A result is significant if the 95% CI does not overlap.

    Parameters
    ----------
    ground_truth_paths : One JSONL ground truth file per seed.
    detections_paths   : One CSV detections file per seed (same order).
    settings           : Evaluator settings (margins, weights).  Defaults apply.
    seeds              : Explicit seed labels for reporting.  Defaults to
                         ``range(1, N+1)``.
    """
    if len(ground_truth_paths) != len(detections_paths):
        raise ValueError(
            f"ground_truth_paths and detections_paths must have the same length, "
            f"got {len(ground_truth_paths)} and {len(detections_paths)}"
        )

    ev = Evaluator(settings=settings)
    per_seed: list[EvaluationResult] = []
    actual_seeds = (
        seeds if seeds is not None else list(range(1, len(ground_truth_paths) + 1))
    )

    for gt_path, det_path in zip(ground_truth_paths, detections_paths, strict=False):
        per_seed.append(ev.evaluate(gt_path, det_path))

    return MultiSeedResult(
        seeds=actual_seeds,
        per_seed=per_seed,
        precision=_ci([r.precision for r in per_seed]),
        recall=_ci([r.recall for r in per_seed]),
        f1=_ci([r.f1 for r in per_seed]),
        weighted_recall=_ci([r.weighted_recall for r in per_seed]),
        weighted_f1=_ci([r.weighted_f1 for r in per_seed]),
    )


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def format_evaluation_report(
    result: EvaluationResult,
    latency_targets: dict[str, float] | None = None,
    title: str = "Evaluation Results",
) -> str:
    """Format an EvaluationResult as a human-readable text report."""
    lines: list[str] = []
    sep = "=" * 62
    lines.append(sep)
    lines.append(f"  {title}")
    lines.append(sep)
    lines.append(
        f"  Events:     {result.total_events}  "
        f"TP={result.true_positives}  "
        f"FP={result.false_positives}  "
        f"FN={result.false_negatives}"
    )
    lines.append(f"  Precision:  {result.precision:.3f}")
    lines.append(f"  Recall:     {result.recall:.3f}")
    lines.append(f"  F1:         {result.f1:.3f}")
    lines.append(f"  W-Recall:   {result.weighted_recall:.3f}")
    lines.append(f"  W-F1:       {result.weighted_f1:.3f}")

    if result.detection_latency_median is not None:
        p90 = result.detection_latency_p90
        p90_str = f"{p90:.1f}s" if p90 is not None else "N/A"
        lines.append(
            f"  Latency:    median={result.detection_latency_median:.1f}s  p90={p90_str}"
        )

    bl = result.random_baseline
    lines.append(f"\n  Random baseline (density={bl.anomaly_density:.3f}):")
    lines.append(
        f"    precision={bl.precision:.3f}  recall={bl.recall:.3f}  f1={bl.f1:.3f}"
    )

    if result.per_scenario:
        lines.append("\n  Per-scenario breakdown:")
        lines.append(
            f"    {'Scenario':<35} {'Events':>6} {'Detected':>8} {'Recall':>7}"
        )
        lines.append(f"    {'-' * 60}")
        for sc_type, sm in sorted(result.per_scenario.items()):
            row = (
                f"    {sc_type:<35} {sm.total_events:>6} "
                f"{sm.detected_events:>8} {sm.recall:>7.3f}"
            )
            if latency_targets and sc_type in latency_targets and sm.detection_latencies:
                med = sum(sm.detection_latencies) / len(sm.detection_latencies)
                target = latency_targets[sc_type]
                row += f"  ({med:.1f}s / {target:.0f}s target)"
            lines.append(row)

    lines.append(sep)
    return "\n".join(lines)


def format_multi_seed_report(
    result: MultiSeedResult,
    title: str = "Multi-Seed Evaluation Results",
) -> str:
    """Format a MultiSeedResult with confidence intervals."""
    lines: list[str] = []
    sep = "=" * 62
    lines.append(sep)
    lines.append(f"  {title}")
    lines.append(f"  Seeds: {result.seeds}  (N={len(result.seeds)})")
    lines.append(sep)

    def _fmt(ci: ConfidenceInterval, label: str) -> str:
        return (
            f"  {label:<16} mean={ci.mean:.3f}  std={ci.std:.3f}  "
            f"95% CI [{ci.ci_low:.3f}, {ci.ci_high:.3f}]"
        )

    lines.append(_fmt(result.precision, "Precision:"))
    lines.append(_fmt(result.recall, "Recall:"))
    lines.append(_fmt(result.f1, "F1:"))
    lines.append(_fmt(result.weighted_recall, "W-Recall:"))
    lines.append(_fmt(result.weighted_f1, "W-F1:"))
    lines.append(sep)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Evaluate subcommand handler  (called from Task 5.9 CLI)
# ---------------------------------------------------------------------------


def evaluate_command(args: Any) -> int:
    """Handle the ``evaluate`` CLI subcommand.  Returns exit code (0 / 1).

    Expected attrs on ``args`` (argparse.Namespace or any object):
    - ``ground_truth`` : path or comma-separated paths
    - ``detections``   : path or comma-separated paths
    - ``pre_margin``   : float seconds (default 30.0)
    - ``post_margin``  : float seconds (default 60.0)
    - ``output``       : optional path to write the text report
    """
    import sys

    pre_margin: float = float(getattr(args, "pre_margin", 30.0))
    post_margin: float = float(getattr(args, "post_margin", 60.0))
    settings = EvaluatorSettings(
        pre_margin_seconds=pre_margin,
        post_margin_seconds=post_margin,
    )

    ground_truth = getattr(args, "ground_truth", None)
    detections = getattr(args, "detections", None)

    if not ground_truth or not detections:
        print(
            "Error: --ground-truth and --detections are required.",
            file=sys.stderr,
        )
        return 1

    # Accept comma-separated lists of paths for multi-seed mode.
    gt_paths = [p.strip() for p in str(ground_truth).split(",") if p.strip()]
    det_paths = [p.strip() for p in str(detections).split(",") if p.strip()]

    if len(gt_paths) != len(det_paths):
        print(
            f"Error: number of ground-truth files ({len(gt_paths)}) must equal "
            f"number of detection files ({len(det_paths)}).",
            file=sys.stderr,
        )
        return 1

    output = getattr(args, "output", None)

    if len(gt_paths) == 1:
        ev = Evaluator(settings=settings)
        result = ev.evaluate(gt_paths[0], det_paths[0])
        report = format_evaluation_report(result, latency_targets=DEFAULT_LATENCY_TARGETS)
        print(report)
        if output:
            Path(str(output)).write_text(report, encoding="utf-8")
    else:
        multi = run_multi_seed_evaluation(gt_paths, det_paths, settings=settings)
        report = format_multi_seed_report(multi)
        print(report)
        if output:
            Path(str(output)).write_text(report, encoding="utf-8")

    return 0
