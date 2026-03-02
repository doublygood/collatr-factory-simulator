"""Tests for the GroundTruthLogger -- JSONL event log.

Verifies:
- JSONL format: each line is valid JSON.
- Header record structure: event_type "config", signals, scenarios.
- Event record structure for each event type.
- Scenario start/end events logged automatically by ScenarioEngine.
- All event types produce valid JSON with required fields.
- Logger handles open/close lifecycle correctly.
- No-op when logger is not opened (graceful degradation).

PRD Reference: Section 4.7 (Ground Truth Event Log)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from factory_simulator.config import (
    EquipmentConfig,
    FactoryConfig,
    FactoryInfo,
    ScenariosConfig,
    ShiftsConfig,
    SignalConfig,
    SimulationConfig,
)
from factory_simulator.engine.ground_truth import GroundTruthLogger

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_log(tmp_path: Path) -> Path:
    """Return a temporary path for the JSONL log file."""
    return tmp_path / "ground_truth.jsonl"


@pytest.fixture
def logger_open(tmp_log: Path) -> GroundTruthLogger:
    """Return an opened GroundTruthLogger writing to tmp_log."""
    gt = GroundTruthLogger(tmp_log)
    gt.open()
    yield gt  # type: ignore[misc]
    gt.close()


def _minimal_config(seed: int = 42) -> FactoryConfig:
    """Build a minimal config for header tests."""
    return FactoryConfig(
        factory=FactoryInfo(name="Test Factory", site_id="test"),
        simulation=SimulationConfig(random_seed=seed, tick_interval_ms=100),
        equipment={
            "press": EquipmentConfig(
                type="flexographic_press",
                signals={
                    "line_speed": SignalConfig(
                        model="ramp",
                        noise_sigma=0.5,
                        noise_type="gaussian",
                    ),
                    "web_tension": SignalConfig(
                        model="linear_gain",
                        noise_sigma=2.0,
                        noise_type="student_t",
                        noise_df=5.0,
                    ),
                },
            ),
        },
        scenarios=ScenariosConfig(),
        shifts=ShiftsConfig(),
    )


def _read_lines(path: Path) -> list[dict]:
    """Read all lines from JSONL file, parse as JSON."""
    lines = path.read_text().strip().split("\n")
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# Header record tests
# ---------------------------------------------------------------------------


class TestHeader:
    """Test the config header record (first line)."""

    def test_header_has_event_type_config(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        config = _minimal_config()
        logger_open.write_header(config)
        logger_open.close()

        records = _read_lines(tmp_log)
        assert len(records) == 1
        assert records[0]["event_type"] == "config"

    def test_header_contains_version_and_seed(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        config = _minimal_config(seed=99)
        logger_open.write_header(config)
        logger_open.close()

        header = _read_lines(tmp_log)[0]
        assert header["sim_version"] == "1.0.0"
        assert header["seed"] == 99

    def test_header_contains_profile_name(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        config = _minimal_config()
        logger_open.write_header(config)
        logger_open.close()

        header = _read_lines(tmp_log)[0]
        assert header["profile"] == "Test Factory"

    def test_header_signals_include_noise_params(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        config = _minimal_config()
        logger_open.write_header(config)
        logger_open.close()

        header = _read_lines(tmp_log)[0]
        signals = header["signals"]

        # Check line_speed noise
        ls = signals["press.line_speed"]
        assert ls["noise"] == "gaussian"
        assert ls["sigma"] == 0.5
        assert "df" not in ls  # Not student_t

        # Check web_tension noise (student_t with df)
        wt = signals["press.web_tension"]
        assert wt["noise"] == "student_t"
        assert wt["sigma"] == 2.0
        assert wt["df"] == 5.0

    def test_header_scenarios_list(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        config = _minimal_config()
        logger_open.write_header(config)
        logger_open.close()

        header = _read_lines(tmp_log)[0]
        scenarios = header["scenarios"]
        assert isinstance(scenarios, list)
        # Default ScenariosConfig has all enabled
        assert "job_changeover" in scenarios
        assert "web_break" in scenarios
        assert "unplanned_stop" in scenarios
        assert "shift_change" in scenarios

    def test_header_is_valid_json_line(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        config = _minimal_config()
        logger_open.write_header(config)
        logger_open.close()

        raw = tmp_log.read_text().strip()
        # Should be exactly one line
        lines = raw.split("\n")
        assert len(lines) == 1
        # Should parse as valid JSON
        parsed = json.loads(lines[0])
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Event record tests
# ---------------------------------------------------------------------------


class TestEventRecords:
    """Test individual event type logging."""

    def test_scenario_start_event(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        logger_open.log_scenario_start(
            sim_time=100.0,
            scenario_name="WebBreak",
            affected_signals=["press.web_tension", "press.line_speed"],
            parameters={"tension_spike_n": 720, "recovery_seconds": 1200},
        )
        logger_open.close()

        record = _read_lines(tmp_log)[0]
        assert record["event"] == "scenario_start"
        assert record["scenario"] == "WebBreak"
        assert "press.web_tension" in record["affected_signals"]
        assert record["parameters"]["tension_spike_n"] == 720

    def test_scenario_end_event(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        logger_open.log_scenario_end(sim_time=200.0, scenario_name="WebBreak")
        logger_open.close()

        record = _read_lines(tmp_log)[0]
        assert record["event"] == "scenario_end"
        assert record["scenario"] == "WebBreak"

    def test_state_change_event(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        logger_open.log_state_change(
            sim_time=50.0,
            signal="press.machine_state",
            from_state=2,
            to_state=4,
        )
        logger_open.close()

        record = _read_lines(tmp_log)[0]
        assert record["event"] == "state_change"
        assert record["signal"] == "press.machine_state"
        assert record["from"] == 2
        assert record["to"] == 4

    def test_signal_anomaly_event(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        logger_open.log_signal_anomaly(
            sim_time=100.5,
            signal="press.web_tension",
            anomaly_type="spike",
            value=720.3,
            normal_range=[60.0, 400.0],
        )
        logger_open.close()

        record = _read_lines(tmp_log)[0]
        assert record["event"] == "signal_anomaly"
        assert record["signal"] == "press.web_tension"
        assert record["anomaly_type"] == "spike"
        assert record["value"] == 720.3
        assert record["normal_range"] == [60.0, 400.0]

    def test_data_quality_event(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        logger_open.log_data_quality(
            sim_time=300.0,
            protocol="modbus",
            duration=5.0,
            description="connection drop",
        )
        logger_open.close()

        record = _read_lines(tmp_log)[0]
        assert record["event"] == "data_quality"
        assert record["protocol"] == "modbus"
        assert record["duration"] == 5.0
        assert record["description"] == "connection drop"

    def test_micro_stop_event(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        logger_open.log_micro_stop(
            sim_time=400.0, duration=3.0, speed_reduction_pct=15.0,
        )
        logger_open.close()

        record = _read_lines(tmp_log)[0]
        assert record["event"] == "micro_stop"
        assert record["duration"] == 3.0
        assert record["speed_reduction_pct"] == 15.0

    def test_shift_change_event(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        logger_open.log_shift_change(
            sim_time=28800.0, old_shift="morning", new_shift="afternoon",
        )
        logger_open.close()

        record = _read_lines(tmp_log)[0]
        assert record["event"] == "shift_change"
        assert record["old_shift"] == "morning"
        assert record["new_shift"] == "afternoon"

    def test_consumable_event(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        logger_open.log_consumable(
            sim_time=500.0,
            signal="coder.ink_level",
            new_value=100.0,
            description="ink refill",
        )
        logger_open.close()

        record = _read_lines(tmp_log)[0]
        assert record["event"] == "consumable"
        assert record["signal"] == "coder.ink_level"
        assert record["new_value"] == 100.0
        assert record["description"] == "ink refill"

    def test_sensor_disconnect_event(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        logger_open.log_sensor_disconnect(
            sim_time=600.0, signal="press.line_speed", sentinel_value=-1.0,
        )
        logger_open.close()

        record = _read_lines(tmp_log)[0]
        assert record["event"] == "sensor_disconnect"
        assert record["signal"] == "press.line_speed"
        assert record["sentinel_value"] == -1.0

    def test_stuck_sensor_event(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        logger_open.log_stuck_sensor(
            sim_time=700.0,
            signal="press.web_tension",
            frozen_value=200.0,
            duration=30.0,
        )
        logger_open.close()

        record = _read_lines(tmp_log)[0]
        assert record["event"] == "stuck_sensor"
        assert record["frozen_value"] == 200.0
        assert record["duration"] == 30.0

    def test_connection_drop_event(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        logger_open.log_connection_drop(
            sim_time=800.0,
            controller_id="plc1",
            protocol="modbus",
            duration=5.0,
            affected_signals=["press.line_speed", "press.web_tension"],
        )
        logger_open.close()

        record = _read_lines(tmp_log)[0]
        assert record["event"] == "connection_drop"
        assert record["controller_id"] == "plc1"
        assert record["protocol"] == "modbus"
        assert len(record["affected_signals"]) == 2


# ---------------------------------------------------------------------------
# JSONL format tests
# ---------------------------------------------------------------------------


class TestJsonlFormat:
    """Test that the output file is valid JSONL."""

    def test_multiple_events_produce_valid_jsonl(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        config = _minimal_config()
        logger_open.write_header(config)
        logger_open.log_scenario_start(
            sim_time=10.0,
            scenario_name="WebBreak",
            affected_signals=["press.web_tension"],
        )
        logger_open.log_state_change(
            sim_time=10.1, signal="press.machine_state",
            from_state=2, to_state=4,
        )
        logger_open.log_scenario_end(sim_time=100.0, scenario_name="WebBreak")
        logger_open.close()

        records = _read_lines(tmp_log)
        assert len(records) == 4
        assert records[0]["event_type"] == "config"
        assert records[1]["event"] == "scenario_start"
        assert records[2]["event"] == "state_change"
        assert records[3]["event"] == "scenario_end"

    def test_each_line_ends_with_newline(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        logger_open.log_scenario_start(
            sim_time=1.0, scenario_name="Test",
            affected_signals=[],
        )
        logger_open.log_scenario_end(sim_time=2.0, scenario_name="Test")
        logger_open.close()

        raw = tmp_log.read_text()
        lines = raw.split("\n")
        # Last element after split should be empty (trailing newline)
        assert lines[-1] == ""
        # Each non-empty line is valid JSON
        for line in lines[:-1]:
            json.loads(line)

    def test_sim_time_is_iso8601_string(
        self, logger_open: GroundTruthLogger, tmp_log: Path,
    ) -> None:
        logger_open.log_scenario_start(
            sim_time=3600.0,
            scenario_name="Test",
            affected_signals=[],
        )
        logger_open.close()

        record = _read_lines(tmp_log)[0]
        sim_time = record["sim_time"]
        assert isinstance(sim_time, str)
        assert sim_time.endswith("Z")
        assert "T" in sim_time


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Test open/close lifecycle and graceful degradation."""

    def test_write_before_open_is_noop(self, tmp_log: Path) -> None:
        gt = GroundTruthLogger(tmp_log)
        # Should not raise, should not create file
        gt.log_scenario_start(
            sim_time=0.0, scenario_name="Test", affected_signals=[],
        )
        assert not tmp_log.exists()

    def test_close_without_open_is_noop(self, tmp_log: Path) -> None:
        gt = GroundTruthLogger(tmp_log)
        gt.close()  # Should not raise

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "ground_truth.jsonl"
        gt = GroundTruthLogger(nested)
        gt.open()
        gt.log_scenario_end(sim_time=0.0, scenario_name="Test")
        gt.close()
        assert nested.exists()
        records = _read_lines(nested)
        assert len(records) == 1


# ---------------------------------------------------------------------------
# ScenarioEngine integration with ground truth
# ---------------------------------------------------------------------------


class TestScenarioEngineIntegration:
    """Test that ScenarioEngine logs events to GroundTruthLogger."""

    def test_scenario_start_and_end_logged(
        self, tmp_log: Path,
    ) -> None:
        """Verify ScenarioEngine logs scenario_start on activation
        and scenario_end on completion."""
        from factory_simulator.engine.scenario_engine import ScenarioEngine
        from factory_simulator.scenarios.unplanned_stop import UnplannedStop

        gt = GroundTruthLogger(tmp_log)
        gt.open()

        rng = np.random.default_rng(42)
        scenarios_cfg = ScenariosConfig(
            # Disable auto-scheduling so we control the timeline
            unplanned_stop=ScenariosConfig().unplanned_stop.model_copy(
                update={"enabled": False},
            ),
            job_changeover=ScenariosConfig().job_changeover.model_copy(
                update={"enabled": False},
            ),
            shift_change=ScenariosConfig().shift_change.model_copy(
                update={"enabled": False},
            ),
        )

        engine_se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=ShiftsConfig(),
            rng=rng,
            ground_truth=gt,
        )

        # Manually add a short-duration scenario
        scenario = UnplannedStop(
            start_time=1.0,
            rng=np.random.default_rng(99),
            params={"duration_seconds": [0.2, 0.2]},
        )
        engine_se.add_scenario(scenario)

        # Build a minimal DataEngine mock for evaluate() calls.
        # UnplannedStop needs a press generator.  We use a real
        # DataEngine with minimal config for correctness.
        from factory_simulator.config import load_config
        from factory_simulator.engine.data_engine import DataEngine
        from factory_simulator.store import SignalStore

        config_path = (
            Path(__file__).resolve().parents[2] / "config" / "factory.yaml"
        )
        config = load_config(config_path, apply_env=False)
        config.simulation.random_seed = 42
        config.simulation.tick_interval_ms = 100
        store = SignalStore()
        data_engine = DataEngine(config, store)

        # Tick past scenario start_time
        sim_time = 0.0
        dt = 0.1
        for _ in range(20):
            sim_time += dt
            engine_se.tick(sim_time, dt, data_engine)

        # Run until scenario completes
        for _ in range(200):
            sim_time += dt
            engine_se.tick(sim_time, dt, data_engine)
            if scenario.is_completed:
                break

        gt.close()

        records = _read_lines(tmp_log)
        events = [r["event"] for r in records]

        assert "scenario_start" in events
        assert "scenario_end" in events

        # Check scenario_start has correct fields
        start_rec = next(r for r in records if r["event"] == "scenario_start")
        assert start_rec["scenario"] == "UnplannedStop"
        assert isinstance(start_rec["affected_signals"], list)

        # Check scenario_end has correct fields
        end_rec = next(r for r in records if r["event"] == "scenario_end")
        assert end_rec["scenario"] == "UnplannedStop"

    def test_no_ground_truth_is_noop(self) -> None:
        """ScenarioEngine works fine without a ground truth logger."""
        from factory_simulator.engine.scenario_engine import ScenarioEngine

        rng = np.random.default_rng(42)
        scenarios_cfg = ScenariosConfig(
            unplanned_stop=ScenariosConfig().unplanned_stop.model_copy(
                update={"enabled": False},
            ),
            job_changeover=ScenariosConfig().job_changeover.model_copy(
                update={"enabled": False},
            ),
            shift_change=ScenariosConfig().shift_change.model_copy(
                update={"enabled": False},
            ),
        )

        # Should not raise when ground_truth is None
        engine_se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=ShiftsConfig(),
            rng=rng,
            ground_truth=None,
        )
        assert engine_se is not None
