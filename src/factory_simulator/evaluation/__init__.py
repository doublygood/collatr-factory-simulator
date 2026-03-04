"""Evaluation framework for anomaly detection benchmarking.

PRD Reference: Section 12 (Evaluation Protocol)
"""

from factory_simulator.evaluation.cli import (
    ConfidenceInterval,
    MultiSeedResult,
    RunManifest,
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
    "ConfidenceInterval",
    "Detection",
    "EvaluationResult",
    "Evaluator",
    "EvaluatorSettings",
    "EventMatch",
    "GroundTruthEvent",
    "MultiSeedResult",
    "RandomBaseline",
    "RunManifest",
    "ScenarioMetrics",
    "clean_config_overlay",
    "create_manifest",
    "evaluate_command",
    "format_evaluation_report",
    "format_multi_seed_report",
    "full_impaired_config_overlay",
    "impairments_only_config_overlay",
    "load_manifest",
    "match_events",
    "run_a_simulation_config",
    "run_b_simulation_config",
    "run_c_simulation_config",
    "run_multi_seed_evaluation",
    "save_manifest",
    "scenarios_only_config_overlay",
]
