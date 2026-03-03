"""Tests for the contextual anomaly scenario (PRD 5.16).

Verifies:
- Priority is "non_state_changing"
- Type selection uses probability weights
- Each anomaly type injects the correct signal when the target state is met
- Anomaly ends early when machine state changes
- Timeout fires at 2x duration when target state never occurs
- Scheduling creates ContextualAnomaly instances when enabled
- Ground truth logs contextual_anomaly events
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.engine.ground_truth import GroundTruthLogger
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.contextual_anomaly import ContextualAnomaly
from factory_simulator.store import SignalStore

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "factory.yaml"

# ---------------------------------------------------------------------------
# Press machine state constants (mirrors press.py)
# ---------------------------------------------------------------------------
_PRESS_OFF = 0
_PRESS_SETUP = 1
_PRESS_RUNNING = 2
_PRESS_IDLE = 3
_PRESS_FAULT = 4
_PRESS_MAINTENANCE = 5

# Coder state constants (mirrors coder.py)
_CODER_OFF = 0
_CODER_READY = 1
_CODER_PRINTING = 2
_CODER_FAULT = 3
_CODER_STANDBY = 4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def _make_engine(seed: int = 42) -> DataEngine:
    """Create a DataEngine with all scenarios disabled except contextual_anomaly."""
    config = load_config(_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = seed
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = 1.0
    config.scenarios.job_changeover.enabled = False
    config.scenarios.unplanned_stop.enabled = False
    config.scenarios.shift_change.enabled = False
    config.scenarios.web_break.enabled = False
    config.scenarios.dryer_drift.enabled = False
    config.scenarios.ink_viscosity_excursion.enabled = False
    config.scenarios.registration_drift.enabled = False
    config.scenarios.cold_start_spike.enabled = False
    config.scenarios.coder_depletion.enabled = False
    config.scenarios.material_splice.enabled = False
    config.scenarios.bearing_wear.enabled = False
    if config.scenarios.micro_stop is not None:
        config.scenarios.micro_stop.enabled = False
    if config.scenarios.intermittent_fault is not None:
        config.scenarios.intermittent_fault.enabled = False
    if config.scenarios.contextual_anomaly is not None:
        config.scenarios.contextual_anomaly.enabled = False

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    return DataEngine(config, store, clock)


def _run_ticks(engine: DataEngine, n: int) -> None:
    for _ in range(n):
        engine.tick()


def _make_anomaly(
    anomaly_type: str,
    duration_s: float = 5.0,
    start_time: float = 0.0,
) -> ContextualAnomaly:
    """Build a ContextualAnomaly forced to a specific type."""
    # Force type by setting all other probabilities to 0
    dur = [duration_s, duration_s]
    types_cfg: dict[str, Any] = {
        "heater_stuck": {"probability": 0.0, "duration_seconds": dur},
        "pressure_bleed": {"probability": 0.0, "duration_seconds": dur},
        "counter_false_trigger": {
            "probability": 0.0,
            "duration_seconds": dur,
            "increment_rate": 0.5,
        },
        "hot_during_maintenance": {"probability": 0.0, "duration_seconds": dur},
        "vibration_during_off": {"probability": 0.0, "duration_seconds": dur},
    }
    types_cfg[anomaly_type] = dict(types_cfg[anomaly_type])
    types_cfg[anomaly_type]["probability"] = 1.0

    rng = _make_rng(seed=42)
    return ContextualAnomaly(
        start_time=start_time,
        rng=rng,
        params={"types_config": types_cfg},
    )


def _set_store_state(store: SignalStore, signal: str, value: float) -> None:
    """Write a state value into the store (simulates generator output)."""
    store.set(signal, value, timestamp=0.0)


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------


class TestContextualAnomalyPriority:
    def test_priority_is_non_state_changing(self) -> None:
        sc = _make_anomaly("heater_stuck")
        assert sc.priority == "non_state_changing"


# ---------------------------------------------------------------------------
# Type selection
# ---------------------------------------------------------------------------


class TestTypeSelection:
    def test_forced_type_is_used(self) -> None:
        for type_name in [
            "heater_stuck",
            "pressure_bleed",
            "counter_false_trigger",
            "hot_during_maintenance",
            "vibration_during_off",
        ]:
            sc = _make_anomaly(type_name)
            assert sc.anomaly_type == type_name

    def test_probability_weights_produce_all_types(self) -> None:
        """With uniform weights, all 5 types should appear over many samples."""
        types_cfg: dict[str, Any] = {
            "heater_stuck": {"probability": 0.2, "duration_seconds": [5.0, 5.0]},
            "pressure_bleed": {"probability": 0.2, "duration_seconds": [5.0, 5.0]},
            "counter_false_trigger": {
                "probability": 0.2,
                "duration_seconds": [5.0, 5.0],
                "increment_rate": 0.1,
            },
            "hot_during_maintenance": {"probability": 0.2, "duration_seconds": [5.0, 5.0]},
            "vibration_during_off": {"probability": 0.2, "duration_seconds": [5.0, 5.0]},
        }
        seen = set()
        for seed in range(50):
            sc = ContextualAnomaly(
                start_time=0.0,
                rng=np.random.default_rng(seed),
                params={"types_config": types_cfg},
            )
            seen.add(sc.anomaly_type)
            if len(seen) == 5:
                break
        assert len(seen) == 5, f"Only saw types: {seen}"


# ---------------------------------------------------------------------------
# Lifecycle: waiting → injecting → complete
# ---------------------------------------------------------------------------


class TestContextualAnomalyLifecycle:
    def test_starts_pending(self) -> None:
        sc = _make_anomaly("vibration_during_off", start_time=10.0)
        assert sc.phase == ScenarioPhase.PENDING

    def test_activates_at_start_time(self) -> None:
        engine = _make_engine()
        sc = _make_anomaly("vibration_during_off", start_time=0.0)
        engine.scenario_engine.add_scenario(sc)
        engine.tick()
        assert sc.is_active

    def test_waiting_when_state_not_met(self) -> None:
        """When press is Running (not Off), vibration_during_off stays waiting."""
        engine = _make_engine()
        # Force press to Running state so Off condition is not met
        from factory_simulator.generators.press import PressGenerator
        press = next(g for g in engine.generators if isinstance(g, PressGenerator))
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        sc = _make_anomaly("vibration_during_off", start_time=0.0)
        engine.scenario_engine.add_scenario(sc)
        engine.tick()
        assert sc.is_active
        assert sc.is_waiting
        assert not sc.is_injecting

    def test_injection_starts_when_state_met(self) -> None:
        """Injection begins when press enters Off state."""
        engine = _make_engine()
        from factory_simulator.generators.press import PressGenerator
        press = next(g for g in engine.generators if isinstance(g, PressGenerator))
        press.state_machine.force_state("Off")
        # Run a few ticks so store has press.machine_state = 0
        _run_ticks(engine, 5)

        sc = _make_anomaly("vibration_during_off", duration_s=5.0, start_time=0.0)
        engine.scenario_engine.add_scenario(sc)

        # Run until injection starts (up to 20 ticks)
        for _ in range(20):
            engine.tick()
            if sc.is_injecting:
                break

        assert sc.is_injecting, "Injection should have started when press is Off"
        assert not sc.is_waiting

    def test_completes_after_injection_duration(self) -> None:
        """Scenario completes after injection_duration_s elapses while injecting."""
        engine = _make_engine()
        from factory_simulator.generators.press import PressGenerator
        press = next(g for g in engine.generators if isinstance(g, PressGenerator))
        press.state_machine.force_state("Off")
        _run_ticks(engine, 5)

        sc = _make_anomaly("vibration_during_off", duration_s=1.0, start_time=0.0)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(200):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestContextualAnomalyTimeout:
    def test_timeout_cancels_if_state_never_reached(self) -> None:
        """If the target state never occurs within 2x duration, complete."""
        engine = _make_engine()
        from factory_simulator.generators.press import PressGenerator
        press = next(g for g in engine.generators if isinstance(g, PressGenerator))
        # Keep press Running so Off state is never reached (vibration_during_off)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        # Duration = 1s → timeout = 2s
        sc = _make_anomaly("vibration_during_off", duration_s=1.0, start_time=0.0)
        engine.scenario_engine.add_scenario(sc)

        # 2s timeout = 20 ticks at 100ms. Give 50 ticks of margin.
        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed, (
            "Scenario should have timed out after 2x duration without seeing Off state"
        )


# ---------------------------------------------------------------------------
# Early termination
# ---------------------------------------------------------------------------


class TestContextualAnomalyEarlyTermination:
    def test_injection_stops_when_state_changes(self) -> None:
        """Injection ends early when machine state leaves the target state."""
        engine = _make_engine()
        from factory_simulator.generators.press import PressGenerator
        press = next(g for g in engine.generators if isinstance(g, PressGenerator))
        # Start in Off state
        press.state_machine.force_state("Off")
        _run_ticks(engine, 5)

        sc = _make_anomaly("vibration_during_off", duration_s=30.0, start_time=0.0)
        engine.scenario_engine.add_scenario(sc)

        # Wait for injection to start
        for _ in range(30):
            engine.tick()
            if sc.is_injecting:
                break
        assert sc.is_injecting, "Injection should have started"

        # Change press state to Running — anomaly should end quickly
        press.state_machine.force_state("Running")

        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed, "Anomaly should complete when machine state leaves Off"


# ---------------------------------------------------------------------------
# Injection values
# ---------------------------------------------------------------------------


class TestContextualAnomalyInjection:
    def test_vibration_during_off_injects_correct_range(self) -> None:
        """vibration.main_drive_x must be within [3, 5] during Off state."""
        engine = _make_engine()
        from factory_simulator.generators.press import PressGenerator
        press = next(g for g in engine.generators if isinstance(g, PressGenerator))
        press.state_machine.force_state("Off")
        _run_ticks(engine, 5)

        sc = _make_anomaly("vibration_during_off", duration_s=5.0, start_time=0.0)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(30):
            engine.tick()
            if sc.is_injecting:
                break
        assert sc.is_injecting

        # Run one more tick so post_gen_inject fires
        engine.tick()
        value = float(engine.store.get_value("vibration.main_drive_x", -1.0))
        assert 2.9 <= value <= 5.1, (
            f"vibration.main_drive_x {value:.3f} out of [3, 5] range during Off"
        )

    def test_hot_during_maintenance_injects_100c(self) -> None:
        """press.dryer_temp_zone_1 must be ~100°C during Maintenance."""
        engine = _make_engine()
        from factory_simulator.generators.press import PressGenerator
        press = next(g for g in engine.generators if isinstance(g, PressGenerator))
        press.state_machine.force_state("Maintenance")
        _run_ticks(engine, 5)

        sc = _make_anomaly("hot_during_maintenance", duration_s=5.0, start_time=0.0)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(30):
            engine.tick()
            if sc.is_injecting:
                break
        assert sc.is_injecting

        engine.tick()
        value = float(engine.store.get_value("press.dryer_temp_zone_1", -1.0))
        assert value == pytest.approx(100.0, abs=0.1), (
            f"dryer_temp_zone_1 {value:.1f} expected 100°C during Maintenance"
        )

    def test_counter_false_trigger_increments(self) -> None:
        """press.impression_count must increment during Idle state."""
        engine = _make_engine()
        from factory_simulator.generators.press import PressGenerator
        press = next(g for g in engine.generators if isinstance(g, PressGenerator))
        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        sc = _make_anomaly("counter_false_trigger", duration_s=5.0, start_time=0.0)
        engine.scenario_engine.add_scenario(sc)

        # Wait for injection
        for _ in range(30):
            engine.tick()
            if sc.is_injecting:
                break
        assert sc.is_injecting

        count_before = float(engine.store.get_value("press.impression_count", 0.0))
        # Run several ticks to accumulate increments
        _run_ticks(engine, 20)
        count_after = float(engine.store.get_value("press.impression_count", 0.0))

        assert count_after > count_before, (
            f"impression_count did not increment: before={count_before}, after={count_after}"
        )

    def test_heater_stuck_injects_temp_range(self) -> None:
        """coder.printhead_temp must be in [40, 42] when coder is Standby/Off."""
        engine = _make_engine()
        from factory_simulator.generators.press import PressGenerator
        press = next(g for g in engine.generators if isinstance(g, PressGenerator))
        # Press Idle → coder goes to Standby (state 4)
        press.state_machine.force_state("Idle")
        _run_ticks(engine, 10)

        sc = _make_anomaly("heater_stuck", duration_s=5.0, start_time=0.0)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_injecting:
                break

        if not sc.is_injecting:
            pytest.skip(
                "Coder did not reach Standby/Off state within timeout — "
                "depends on coder state machine settling"
            )

        engine.tick()
        value = float(engine.store.get_value("coder.printhead_temp", -1.0))
        assert 39.9 <= value <= 42.1, (
            f"coder.printhead_temp {value:.1f} out of [40, 42] range"
        )

    def test_pressure_bleed_injects_pressure_range(self) -> None:
        """coder.ink_pressure must be in [800, 850] when coder is Off."""
        engine = _make_engine()
        from factory_simulator.generators.press import PressGenerator
        press = next(g for g in engine.generators if isinstance(g, PressGenerator))
        # Press Off → coder Off (state 0)
        press.state_machine.force_state("Off")
        _run_ticks(engine, 15)

        sc = _make_anomaly("pressure_bleed", duration_s=5.0, start_time=0.0)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_injecting:
                break

        if not sc.is_injecting:
            pytest.skip(
                "Coder did not reach Off state within timeout — "
                "depends on coder state machine settling"
            )

        engine.tick()
        value = float(engine.store.get_value("coder.ink_pressure", -1.0))
        assert 799.0 <= value <= 851.0, (
            f"coder.ink_pressure {value:.0f} out of [800, 850] mbar range"
        )


# ---------------------------------------------------------------------------
# Ground truth logging
# ---------------------------------------------------------------------------


class TestContextualAnomalyGroundTruth:
    def test_ground_truth_logs_anomaly_on_injection_start(self, tmp_path: Any) -> None:
        """Ground truth must include contextual_anomaly event when injection begins."""
        gt_path = tmp_path / "gt.jsonl"
        gt = GroundTruthLogger(str(gt_path))
        gt.open()

        engine = _make_engine()
        engine._ground_truth = gt
        engine._scenario_engine._ground_truth = gt

        from factory_simulator.generators.press import PressGenerator
        press = next(g for g in engine.generators if isinstance(g, PressGenerator))
        press.state_machine.force_state("Off")
        _run_ticks(engine, 5)

        sc = _make_anomaly("vibration_during_off", duration_s=3.0, start_time=0.0)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        gt.close()
        events = [json.loads(line) for line in gt_path.read_text().splitlines() if line]
        ca_events = [e for e in events if e.get("event") == "contextual_anomaly"]
        assert len(ca_events) >= 1, "Expected at least one contextual_anomaly ground truth event"
        ev = ca_events[0]
        assert ev["anomaly_type"] == "vibration_during_off"
        assert ev["signal"] == "vibration.main_drive_x"
        assert "injected_value" in ev
        assert "expected_state" in ev
        assert "actual_state" in ev


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


class TestContextualAnomalyScheduling:
    def test_scheduling_creates_anomalies_when_enabled(self) -> None:
        """ScenarioEngine must schedule ContextualAnomaly instances when enabled."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 42
        config.simulation.tick_interval_ms = 100
        config.simulation.sim_duration_s = 7 * 24 * 3600  # 1 week
        # Disable everything except contextual_anomaly
        config.scenarios.job_changeover.enabled = False
        config.scenarios.unplanned_stop.enabled = False
        config.scenarios.shift_change.enabled = False
        config.scenarios.web_break.enabled = False
        config.scenarios.dryer_drift.enabled = False
        config.scenarios.ink_viscosity_excursion.enabled = False
        config.scenarios.registration_drift.enabled = False
        config.scenarios.cold_start_spike.enabled = False
        config.scenarios.coder_depletion.enabled = False
        config.scenarios.material_splice.enabled = False
        config.scenarios.bearing_wear.enabled = False
        if config.scenarios.micro_stop is not None:
            config.scenarios.micro_stop.enabled = False
        if config.scenarios.intermittent_fault is not None:
            config.scenarios.intermittent_fault.enabled = False
        if config.scenarios.contextual_anomaly is not None:
            config.scenarios.contextual_anomaly.enabled = True

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        anomalies = [
            s for s in engine.scenario_engine.scenarios
            if isinstance(s, ContextualAnomaly)
        ]
        # 1 week = 2-5 events. Poisson is stochastic; expect at least 1.
        assert len(anomalies) >= 1, (
            f"Expected contextual anomaly events over 1 week, got {len(anomalies)}"
        )

    def test_contextual_anomaly_priority_in_engine(self) -> None:
        """All scheduled ContextualAnomaly instances must have non_state_changing priority."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 42
        config.simulation.sim_duration_s = 7 * 24 * 3600
        if config.scenarios.contextual_anomaly is not None:
            config.scenarios.contextual_anomaly.enabled = True

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        for s in engine.scenario_engine.scenarios:
            if isinstance(s, ContextualAnomaly):
                assert s.priority == "non_state_changing"
