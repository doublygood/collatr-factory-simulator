"""Tests for the factory_simulator CLI entry point (Task 5.9).

Validates:
- Parser structure: all subcommands and flags are present
- Duration parsing: days/hours/minutes/seconds/bare-seconds
- Version command and --version flag
- Evaluate subcommand delegation
- Run subcommand config loading and overrides
- python -m factory_simulator (__main__.py) importable

PRD Reference: Appendix F (Phase 5 — CLI and Productisation)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from factory_simulator.cli import (
    _default_config_path,
    _load_config,
    build_parser,
    evaluate_command,
    main,
    parse_duration,
    run_command,
    version_command,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory.yaml"
_FOODBEV_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory-foodbev.yaml"


def _run_args(**kwargs: Any) -> SimpleNamespace:
    """Build a Namespace for the 'run' subcommand with defaults."""
    defaults: dict[str, Any] = {
        "command": "run",
        "config": None,
        "profile": "packaging",
        "seed": None,
        "time_scale": None,
        "batch_output": None,
        "batch_duration": None,
        "batch_format": "csv",
        "network_mode": "collapsed",
        "log_level": "info",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------


class TestParseDuration:
    def test_days(self) -> None:
        assert parse_duration("7d") == pytest.approx(7 * 86400.0)

    def test_hours(self) -> None:
        assert parse_duration("24h") == pytest.approx(86400.0)

    def test_minutes(self) -> None:
        assert parse_duration("30m") == pytest.approx(1800.0)

    def test_seconds_suffix(self) -> None:
        assert parse_duration("3600s") == pytest.approx(3600.0)

    def test_bare_number(self) -> None:
        assert parse_duration("3600") == pytest.approx(3600.0)

    def test_float_days(self) -> None:
        assert parse_duration("0.5d") == pytest.approx(43200.0)

    def test_float_hours(self) -> None:
        assert parse_duration("1.5h") == pytest.approx(5400.0)

    def test_whitespace_stripped(self) -> None:
        assert parse_duration("  7d  ") == pytest.approx(7 * 86400.0)

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_duration("abc")

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty duration"):
            parse_duration("")

    def test_one_day(self) -> None:
        assert parse_duration("1d") == pytest.approx(86400.0)

    def test_zero_hours(self) -> None:
        assert parse_duration("0h") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Default config path resolution
# ---------------------------------------------------------------------------


class TestDefaultConfigPath:
    def test_packaging_returns_factory_yaml(self) -> None:
        p = _default_config_path("packaging")
        assert p.name == "factory.yaml"
        assert p.exists()

    def test_foodbev_returns_foodbev_yaml(self) -> None:
        p = _default_config_path("foodbev")
        assert p.name == "factory-foodbev.yaml"
        assert p.exists()


# ---------------------------------------------------------------------------
# Parser structure
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_parser_created(self) -> None:
        parser = build_parser()
        assert parser is not None
        assert parser.prog == "factory-simulator"

    def test_version_flag_present(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "factory-simulator" in captured.out
        assert "0.1.0" in captured.out

    def test_run_subcommand_exists(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run"])
        assert args.command == "run"

    def test_evaluate_subcommand_exists(self) -> None:
        parser = build_parser()
        # evaluate requires --ground-truth and --detections; just check it parses
        args = parser.parse_args(
            ["evaluate", "--ground-truth", "gt.jsonl", "--detections", "det.csv"]
        )
        assert args.command == "evaluate"

    def test_version_subcommand_exists(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["version"])
        assert args.command == "version"

    def test_run_has_config_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "--config", "/tmp/cfg.yaml"])
        assert args.config == "/tmp/cfg.yaml"

    def test_run_has_profile_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "--profile", "foodbev"])
        assert args.profile == "foodbev"

    def test_run_has_seed_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "--seed", "42"])
        assert args.seed == 42

    def test_run_has_time_scale_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "--time-scale", "10.0"])
        assert args.time_scale == pytest.approx(10.0)

    def test_run_has_batch_output_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "--batch-output", "/tmp/out"])
        assert args.batch_output == "/tmp/out"

    def test_run_has_batch_duration_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "--batch-duration", "7d"])
        assert args.batch_duration == "7d"

    def test_run_has_batch_format_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "--batch-format", "parquet"])
        assert args.batch_format == "parquet"

    def test_run_has_network_mode_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "--network-mode", "realistic"])
        assert args.network_mode == "realistic"

    def test_run_has_log_level_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "--log-level", "debug"])
        assert args.log_level == "debug"

    def test_run_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run"])
        assert args.profile == "packaging"
        assert args.batch_format == "csv"
        assert args.network_mode == "collapsed"
        assert args.log_level == "info"
        assert args.seed is None
        assert args.time_scale is None
        assert args.batch_output is None
        assert args.batch_duration is None

    def test_evaluate_has_pre_margin(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["evaluate", "--ground-truth", "g.jsonl", "--detections", "d.csv",
             "--pre-margin", "60"]
        )
        assert args.pre_margin == pytest.approx(60.0)

    def test_evaluate_has_post_margin(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["evaluate", "--ground-truth", "g.jsonl", "--detections", "d.csv",
             "--post-margin", "120"]
        )
        assert args.post_margin == pytest.approx(120.0)

    def test_evaluate_has_output_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["evaluate", "--ground-truth", "g.jsonl", "--detections", "d.csv",
             "--output", "report.txt"]
        )
        assert args.output == "report.txt"

    def test_evaluate_default_margins(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["evaluate", "--ground-truth", "g.jsonl", "--detections", "d.csv"]
        )
        assert args.pre_margin == pytest.approx(30.0)
        assert args.post_margin == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# Version command
# ---------------------------------------------------------------------------


class TestVersionCommand:
    def test_version_command_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = version_command()
        assert result == 0

    def test_version_command_prints_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        version_command()
        out = capsys.readouterr().out
        assert "factory-simulator" in out
        assert "0.1.0" in out

    def test_main_version_subcommand(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = main(["version"])
        assert result == 0
        out = capsys.readouterr().out
        assert "factory-simulator" in out

    def test_main_no_command_returns_zero(self) -> None:
        # No subcommand → prints help, returns 0
        result = main([])
        assert result == 0

    def test_main_dispatches_to_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        main(["version"])
        out = capsys.readouterr().out
        assert "0.1.0" in out


# ---------------------------------------------------------------------------
# Evaluate command delegation
# ---------------------------------------------------------------------------


class TestEvaluateCommand:
    def test_evaluate_missing_ground_truth_returns_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = SimpleNamespace(
            ground_truth=None,
            detections=None,
            pre_margin=30.0,
            post_margin=60.0,
            output=None,
        )
        result = evaluate_command(args)
        assert result == 1

    def test_evaluate_missing_detections_returns_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = SimpleNamespace(
            ground_truth="gt.jsonl",
            detections=None,
            pre_margin=30.0,
            post_margin=60.0,
            output=None,
        )
        result = evaluate_command(args)
        assert result == 1

    def test_evaluate_mismatched_paths_returns_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = SimpleNamespace(
            ground_truth="gt1.jsonl,gt2.jsonl",
            detections="det1.csv",
            pre_margin=30.0,
            post_margin=60.0,
            output=None,
        )
        result = evaluate_command(args)
        assert result == 1

    def test_evaluate_with_real_files(self, tmp_path: Path) -> None:
        """Evaluate command returns 0 when given valid files."""
        # Create a minimal ground truth JSONL (no events)
        gt = tmp_path / "gt.jsonl"
        gt.write_text("", encoding="utf-8")
        # Create a minimal detections CSV (just header)
        det = tmp_path / "det.csv"
        det.write_text("timestamp,scenario_type\n", encoding="utf-8")

        args = SimpleNamespace(
            ground_truth=str(gt),
            detections=str(det),
            pre_margin=30.0,
            post_margin=60.0,
            output=None,
        )
        result = evaluate_command(args)
        assert result == 0

    def test_evaluate_writes_output_file(self, tmp_path: Path) -> None:
        gt = tmp_path / "gt.jsonl"
        gt.write_text("", encoding="utf-8")
        det = tmp_path / "det.csv"
        det.write_text("timestamp,scenario_type\n", encoding="utf-8")
        out_file = tmp_path / "report.txt"

        args = SimpleNamespace(
            ground_truth=str(gt),
            detections=str(det),
            pre_margin=30.0,
            post_margin=60.0,
            output=str(out_file),
        )
        evaluate_command(args)
        assert out_file.exists()
        assert out_file.stat().st_size > 0


# ---------------------------------------------------------------------------
# Config loading from CLI args
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_loads_default_packaging_config(self) -> None:
        args = _run_args()
        config = _load_config(args)
        assert config is not None
        assert len(config.equipment) > 0

    def test_loads_foodbev_config(self) -> None:
        args = _run_args(profile="foodbev")
        config = _load_config(args)
        assert config is not None
        assert len(config.equipment) > 0

    def test_applies_seed_override(self) -> None:
        args = _run_args(seed=99)
        config = _load_config(args)
        assert config.simulation.random_seed == 99

    def test_applies_time_scale_override(self) -> None:
        args = _run_args(time_scale=10.0)
        config = _load_config(args)
        assert config.simulation.time_scale == pytest.approx(10.0)

    def test_seed_none_not_overridden(self) -> None:
        args = _run_args(seed=None)
        config = _load_config(args)
        # Should use whatever the YAML has (may be None or a number)
        # Just verify it loads without error
        assert config is not None

    def test_network_mode_realistic(self) -> None:
        args = _run_args(network_mode="realistic")
        config = _load_config(args)
        assert config.network is not None
        assert config.network.mode == "realistic"

    def test_network_mode_collapsed_leaves_network_none_or_collapsed(self) -> None:
        args = _run_args(network_mode="collapsed")
        config = _load_config(args)
        # collapsed mode: network stays None or mode == "collapsed"
        if config.network is not None:
            assert config.network.mode == "collapsed"

    def test_batch_output_sets_format(self, tmp_path: Path) -> None:
        args = _run_args(batch_output=str(tmp_path), batch_format="csv")
        config = _load_config(args)
        assert config.batch_output.format == "csv"
        assert config.batch_output.path == str(tmp_path)

    def test_batch_duration_sets_sim_duration(self) -> None:
        args = _run_args(batch_duration="24h")
        config = _load_config(args)
        assert config.simulation.sim_duration_s == pytest.approx(86400.0)

    def test_batch_duration_days(self) -> None:
        args = _run_args(batch_duration="7d")
        config = _load_config(args)
        assert config.simulation.sim_duration_s == pytest.approx(604800.0)

    def test_custom_config_path(self) -> None:
        args = _run_args(config=str(_CONFIG_PATH))
        config = _load_config(args)
        assert len(config.equipment) > 0

    def test_log_level_applied(self) -> None:
        args = _run_args(log_level="debug")
        config = _load_config(args)
        assert config.simulation.log_level == "debug"


# ---------------------------------------------------------------------------
# Run command — batch mode (short simulation, no protocol servers)
# ---------------------------------------------------------------------------


class TestRunCommandBatch:
    def test_run_batch_mode_produces_csv(self, tmp_path: Path) -> None:
        """run with --batch-output and short duration produces a signals.csv."""
        args = _run_args(
            seed=42,
            time_scale=1000.0,
            batch_output=str(tmp_path),
            batch_duration="1s",   # 1 simulated second
            batch_format="csv",
        )
        result = run_command(args)
        assert result == 0
        out_file = tmp_path / "signals.csv"
        assert out_file.exists()
        assert out_file.stat().st_size > 0

    def test_run_batch_mode_csv_has_header(self, tmp_path: Path) -> None:
        """CSV output contains the expected column headers."""
        import csv as csv_mod

        args = _run_args(
            seed=42,
            time_scale=1000.0,
            batch_output=str(tmp_path),
            batch_duration="1s",
        )
        run_command(args)
        with open(tmp_path / "signals.csv", encoding="utf-8") as fh:
            reader = csv_mod.DictReader(fh)
            assert reader.fieldnames is not None
            assert list(reader.fieldnames) == ["timestamp", "signal_id", "value", "quality"]

    def test_run_batch_mode_foodbev(self, tmp_path: Path) -> None:
        args = _run_args(
            profile="foodbev",
            seed=42,
            time_scale=1000.0,
            batch_output=str(tmp_path),
            batch_duration="1s",
        )
        result = run_command(args)
        assert result == 0
        assert (tmp_path / "signals.csv").exists()


# ---------------------------------------------------------------------------
# main() dispatcher
# ---------------------------------------------------------------------------


class TestMainDispatcher:
    def test_main_help_exits_zero(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_main_run_help_exits_zero(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "--help"])
        assert exc_info.value.code == 0

    def test_main_evaluate_help_exits_zero(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["evaluate", "--help"])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# __main__.py — python -m factory_simulator
# ---------------------------------------------------------------------------


class TestMainModule:
    def test_main_module_importable(self) -> None:
        """__main__.py can be imported (checks for syntax errors)."""
        import importlib

        spec = importlib.util.find_spec("factory_simulator.__main__")  # type: ignore[attr-defined]
        assert spec is not None

    def test_python_m_version(self) -> None:
        """python -m factory_simulator version exits 0 and prints version."""
        result = subprocess.run(
            [sys.executable, "-m", "factory_simulator", "version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "factory-simulator" in result.stdout
        assert "0.1.0" in result.stdout

    def test_python_m_help(self) -> None:
        """python -m factory_simulator --help exits 0."""
        result = subprocess.run(
            [sys.executable, "-m", "factory_simulator", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "run" in result.stdout
        assert "evaluate" in result.stdout
        assert "version" in result.stdout
