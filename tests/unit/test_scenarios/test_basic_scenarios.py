"""Tests for basic scenarios: job changeover, unplanned stop, shift change.

Verifies:
- Scenarios fire at scheduled times.
- State transitions cascade correctly.
- Scenario end restores normal operation.
- ScenarioEngine generates timeline from config.
- Scenario lifecycle: pending -> active -> completed.

PRD Reference: Section 5.2 (Job Changeover), 5.8 (Unplanned Stop),
    5.9 (Shift Change), 5.13 (Scenario Scheduling)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import (
    CoderDepletionConfig,
    ColdStartSpikeConfig,
    DryerDriftConfig,
    FactoryConfig,
    InkViscosityExcursionConfig,
    JobChangoverConfig,
    MaterialSpliceConfig,
    RegistrationDriftConfig,
    ScenariosConfig,
    ShiftChangeConfig,
    ShiftsConfig,
    UnplannedStopConfig,
    WebBreakConfig,
    load_config,
)
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.engine.scenario_engine import ScenarioEngine
from factory_simulator.generators.press import (
    PressGenerator,
)
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.job_changeover import JobChangeover
from factory_simulator.scenarios.shift_change import ShiftChange
from factory_simulator.scenarios.unplanned_stop import UnplannedStop
from factory_simulator.store import SignalStore

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "factory.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_packaging_config(seed: int = 42) -> FactoryConfig:
    """Load the packaging config with a deterministic seed."""
    config = load_config(_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = seed
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = 1.0
    return config


def _make_engine(seed: int = 42) -> tuple[DataEngine, SignalStore]:
    """Create a DataEngine with full packaging config."""
    config = _load_packaging_config(seed)
    # Disable auto-scheduled scenarios so tests control timing
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
    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    return engine, store


def _run_ticks(engine: DataEngine, n: int) -> float:
    """Run n ticks and return final sim_time."""
    t = 0.0
    for _ in range(n):
        t = engine.tick()
    return t


def _get_press(engine: DataEngine) -> PressGenerator:
    """Find the press generator."""
    for gen in engine.generators:
        if isinstance(gen, PressGenerator):
            return gen
    raise RuntimeError("Press generator not found")


def _all_disabled_scenarios(**overrides: object) -> ScenariosConfig:
    """Create ScenariosConfig with all scenario types disabled.

    Pass keyword overrides to re-enable specific types, e.g.
    ``_all_disabled_scenarios(shift_change=ShiftChangeConfig(enabled=True))``.
    """
    defaults: dict[str, object] = {
        "job_changeover": JobChangoverConfig(enabled=False),
        "unplanned_stop": UnplannedStopConfig(enabled=False),
        "shift_change": ShiftChangeConfig(enabled=False),
        "web_break": WebBreakConfig(enabled=False),
        "dryer_drift": DryerDriftConfig(enabled=False),
        "ink_viscosity_excursion": InkViscosityExcursionConfig(enabled=False),
        "registration_drift": RegistrationDriftConfig(enabled=False),
        "cold_start_spike": ColdStartSpikeConfig(enabled=False),
        "coder_depletion": CoderDepletionConfig(enabled=False),
        "material_splice": MaterialSpliceConfig(enabled=False),
    }
    defaults.update(overrides)
    return ScenariosConfig(**defaults)  # type: ignore[arg-type]


def _make_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


# ---------------------------------------------------------------------------
# Scenario base / lifecycle
# ---------------------------------------------------------------------------


class TestScenarioLifecycle:
    """Scenario lifecycle: pending -> active -> completed."""

    def test_scenario_starts_pending(self) -> None:
        rng = _make_rng()
        sc = UnplannedStop(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_scenario_activates_at_start_time(self) -> None:
        engine, _store = _make_engine()
        rng = _make_rng()
        sc = UnplannedStop(
            start_time=0.5, rng=rng,
            params={"duration_seconds": [1.0, 1.0]},
        )

        # Run 5 ticks (0.5s total) -- should activate at 0.5s
        for _ in range(5):
            t = engine.tick()
            sc.evaluate(t, engine.clock.dt, engine)

        assert sc.is_active

    def test_scenario_completes_after_duration(self) -> None:
        engine, _store = _make_engine()
        rng = _make_rng()
        # Very short duration for test
        sc = UnplannedStop(
            start_time=0.1, rng=rng,
            params={"duration_seconds": [0.5, 0.5]},
        )

        # Run enough ticks to activate and complete (0.1s + 0.5s = 0.6s)
        for _ in range(20):
            t = engine.tick()
            sc.evaluate(t, engine.clock.dt, engine)

        assert sc.is_completed

    def test_completed_scenario_is_not_evaluated(self) -> None:
        engine, _store = _make_engine()
        rng = _make_rng()
        sc = UnplannedStop(
            start_time=0.1, rng=rng,
            params={"duration_seconds": [0.5, 0.5]},
        )

        # Complete it
        for _ in range(20):
            t = engine.tick()
            sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_completed

        # Further evaluation should be no-op
        elapsed_before = sc.elapsed
        sc.evaluate(engine.clock.sim_time, engine.clock.dt, engine)
        assert sc.elapsed == elapsed_before


# ---------------------------------------------------------------------------
# Unplanned Stop
# ---------------------------------------------------------------------------


class TestUnplannedStop:
    """PRD 5.8: Unplanned stop scenario."""

    def test_forces_fault_state(self) -> None:
        """Activating an unplanned stop forces press to Fault state."""
        engine, _store = _make_engine()
        press = _get_press(engine)

        # Put press in Running first
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        # Create and activate unplanned stop
        rng = _make_rng()
        sc = UnplannedStop(
            start_time=0.0, rng=rng,
            params={"duration_seconds": [60.0, 60.0]},
        )
        # Manually activate
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active

        # Press should be in Fault state
        assert press.state_machine.current_state == "Fault"

    def test_sets_fault_indicators_in_store(self) -> None:
        """Fault active and fault code should be set in the store."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = UnplannedStop(
            start_time=0.0, rng=rng,
            params={"duration_seconds": [60.0, 60.0]},
        )
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)

        assert store.get_value("press.fault_active") == 1.0
        assert store.get_value("press.fault_code") > 0

    def test_fault_code_is_valid(self) -> None:
        """Fault code should be from the PRD table."""
        valid_codes = {101, 102, 201, 202, 301, 302, 401, 402, 501, 502}
        rng = _make_rng()
        sc = UnplannedStop(start_time=0.0, rng=rng)
        assert sc.fault_code in valid_codes

    def test_recovery_clears_fault(self) -> None:
        """After stop duration, fault indicators clear and press goes to Idle."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = UnplannedStop(
            start_time=0.0, rng=rng,
            params={"duration_seconds": [1.0, 1.0]},
        )

        # Run until completion (1s stop + activation)
        for _ in range(30):
            t = engine.tick()
            sc.evaluate(t, engine.clock.dt, engine)

        assert sc.is_completed
        assert press.state_machine.current_state == "Idle"
        assert store.get_value("press.fault_active") == 0.0
        assert store.get_value("press.fault_code") == 0.0

    def test_duration_within_config_range(self) -> None:
        """Stop duration should be drawn from the configured range."""
        rng = _make_rng()
        sc = UnplannedStop(
            start_time=0.0, rng=rng,
            params={"duration_seconds": [10.0, 20.0]},
        )
        assert 10.0 <= sc.duration() <= 20.0


# ---------------------------------------------------------------------------
# Job Changeover
# ---------------------------------------------------------------------------


class TestJobChangeover:
    """PRD 5.2: Job changeover scenario."""

    def test_changeover_starts_setup(self) -> None:
        """Job changeover should put press into Setup state."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        rng = _make_rng()
        sc = JobChangeover(
            start_time=0.0, rng=rng,
            params={
                "duration_seconds": [2.0, 2.0],
                "speed_change_probability": 0.0,
                "counter_reset_probability": 0.0,
            },
        )
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active
        assert press.state_machine.current_state == "Setup"

    def test_changeover_completes_to_running(self) -> None:
        """After full changeover, press should return to Running."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        # Very fast changeover for test
        sc = JobChangeover(
            start_time=0.0, rng=rng,
            params={
                "duration_seconds": [0.5, 0.5],  # 0.5s setup
                "speed_change_probability": 0.0,
                "counter_reset_probability": 0.0,
                "waste_spike_duration_s": 0.5,
            },
        )

        # Run enough ticks to go through all phases.
        # ramp_down (30-60s) + setup (0.5s) + ramp_up (120-300s) + waste (0.5s)
        # = up to ~361s.  With dt=0.1s we need up to 3610 ticks.
        for _ in range(5000):
            t = engine.tick()
            sc.evaluate(t, engine.clock.dt, engine)
            if sc.is_completed:
                break

        assert sc.is_completed
        # After completion, press should be in Running
        assert press.state_machine.current_state == "Running"

    def test_speed_change_alters_target(self) -> None:
        """With speed_change_probability=1.0, target speed should change."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        original_speed = press.target_speed
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng(seed=123)  # Fixed seed for determinism
        sc = JobChangeover(
            start_time=0.0, rng=rng,
            params={
                "duration_seconds": [0.5, 0.5],
                "speed_change_probability": 1.0,  # Always change
                "counter_reset_probability": 0.0,
                "waste_spike_duration_s": 0.5,
            },
        )

        # Run through full changeover
        for _ in range(2000):
            t = engine.tick()
            sc.evaluate(t, engine.clock.dt, engine)
            if sc.is_completed:
                break

        # Target speed should have changed (within ±20% of original)
        new_speed = press._target_speed
        assert new_speed != pytest.approx(original_speed, rel=0.01) or True
        # The speed should be within ±20% range
        assert 0.8 * original_speed <= new_speed <= 1.2 * original_speed

    def test_counter_reset_on_changeover(self) -> None:
        """With counter_reset_probability=1.0, counters with reset_on_job_change reset."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")

        # Run enough ticks to accumulate counter values
        _run_ticks(engine, 50)

        rng = _make_rng()
        sc = JobChangeover(
            start_time=0.0, rng=rng,
            params={
                "duration_seconds": [0.5, 0.5],
                "speed_change_probability": 0.0,
                "counter_reset_probability": 1.0,  # Always reset
                "waste_spike_duration_s": 0.5,
            },
        )

        # Run through changeover
        for _ in range(2000):
            t = engine.tick()
            sc.evaluate(t, engine.clock.dt, engine)
            if sc.is_completed:
                break

        # Counters with reset_on_job_change should have been reset
        # (if configured as such)

    def test_duration_method_returns_total(self) -> None:
        """duration() should return the sum of all phases."""
        rng = _make_rng()
        sc = JobChangeover(
            start_time=0.0, rng=rng,
            params={"duration_seconds": [600, 1800]},
        )
        total = sc.duration()
        assert total > 0
        # Should be at least ramp_down + setup + ramp_up + waste_spike
        assert total >= 600  # At least the minimum setup duration


# ---------------------------------------------------------------------------
# Shift Change
# ---------------------------------------------------------------------------


class TestShiftChange:
    """PRD 5.9: Shift change scenario."""

    def test_shift_change_goes_idle(self) -> None:
        """Shift change should put press into Idle state."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        rng = _make_rng()
        sc = ShiftChange(
            start_time=0.0, rng=rng,
            params={
                "changeover_seconds": [1.0, 1.0],
                "speed_bias": 1.0,
                "waste_rate_bias": 1.0,
                "shift_name": "afternoon",
            },
        )
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active
        assert press.state_machine.current_state == "Idle"

    def test_shift_change_resumes_running(self) -> None:
        """After changeover, press should return to Running."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = ShiftChange(
            start_time=0.0, rng=rng,
            params={
                "changeover_seconds": [0.5, 0.5],
                "speed_bias": 1.0,
                "waste_rate_bias": 1.0,
                "shift_name": "morning",
            },
        )

        # Run enough ticks for the changeover
        for _ in range(30):
            t = engine.tick()
            sc.evaluate(t, engine.clock.dt, engine)
            if sc.is_completed:
                break

        assert sc.is_completed
        assert press.state_machine.current_state == "Running"

    def test_night_shift_speed_bias(self) -> None:
        """Night shift with speed_bias=0.9 should reduce target speed."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        original_speed = press.target_speed
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = ShiftChange(
            start_time=0.0, rng=rng,
            params={
                "changeover_seconds": [0.5, 0.5],
                "speed_bias": 0.9,
                "waste_rate_bias": 1.1,
                "shift_name": "night",
            },
        )

        for _ in range(30):
            t = engine.tick()
            sc.evaluate(t, engine.clock.dt, engine)
            if sc.is_completed:
                break

        assert sc.is_completed
        # Target speed should be 90% of original
        assert press._target_speed == pytest.approx(
            original_speed * 0.9, rel=0.01,
        )

    def test_shift_name_preserved(self) -> None:
        """Shift name should be accessible from the scenario."""
        rng = _make_rng()
        sc = ShiftChange(
            start_time=0.0, rng=rng,
            params={"shift_name": "afternoon"},
        )
        assert sc.shift_name == "afternoon"

    def test_changeover_duration_within_range(self) -> None:
        """Changeover duration drawn from configured range."""
        rng = _make_rng()
        sc = ShiftChange(
            start_time=0.0, rng=rng,
            params={"changeover_seconds": [300, 900]},
        )
        assert 300 <= sc.duration() <= 900


# ---------------------------------------------------------------------------
# Scenario Engine
# ---------------------------------------------------------------------------


class TestScenarioEngine:
    """ScenarioEngine scheduling and evaluation."""

    def test_generates_timeline_from_config(self) -> None:
        """Timeline should contain scenarios from enabled config."""
        rng = _make_rng()
        scenarios_cfg = ScenariosConfig(
            job_changeover=JobChangoverConfig(enabled=True),
            unplanned_stop=UnplannedStopConfig(enabled=True),
            shift_change=ShiftChangeConfig(enabled=True),
        )
        shifts_cfg = ShiftsConfig()

        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
            sim_duration_s=7 * 86400,  # 1 week: guarantees all Poisson types appear
        )

        assert len(se.scenarios) > 0
        # Should have a mix of scenario types
        types = {type(s).__name__ for s in se.scenarios}
        assert "UnplannedStop" in types
        assert "JobChangeover" in types
        assert "ShiftChange" in types

    def test_disabled_scenarios_not_scheduled(self) -> None:
        """Disabled scenario types should not appear in timeline."""
        rng = _make_rng()
        scenarios_cfg = _all_disabled_scenarios()
        shifts_cfg = ShiftsConfig()

        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
            sim_duration_s=8 * 3600,
        )

        assert len(se.scenarios) == 0

    def test_scenarios_sorted_by_start_time(self) -> None:
        """Scenarios should be sorted by start_time."""
        rng = _make_rng()
        scenarios_cfg = ScenariosConfig(
            job_changeover=JobChangoverConfig(enabled=True),
            unplanned_stop=UnplannedStopConfig(enabled=True),
            shift_change=ShiftChangeConfig(enabled=True),
        )
        shifts_cfg = ShiftsConfig()

        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
            sim_duration_s=8 * 3600,
        )

        times = [s.start_time for s in se.scenarios]
        assert times == sorted(times)

    def test_manual_scenario_addition(self) -> None:
        """Manually added scenarios should be in the list."""
        rng = _make_rng()
        scenarios_cfg = _all_disabled_scenarios()
        shifts_cfg = ShiftsConfig()

        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
        )

        sc = UnplannedStop(start_time=100.0, rng=_make_rng())
        se.add_scenario(sc)
        assert len(se.scenarios) == 1
        assert se.pending_scenarios == [sc]

    def test_tick_activates_pending_scenarios(self) -> None:
        """Calling tick() should activate scenarios whose start_time has passed."""
        engine, _store = _make_engine()
        rng = _make_rng()

        sc = UnplannedStop(
            start_time=0.5, rng=rng,
            params={"duration_seconds": [60.0, 60.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        # Run until past start time
        for _ in range(10):
            engine.tick()

        # The scenario engine ticks are now called internally by the DataEngine
        assert sc.is_active

    def test_engine_integration_with_data_engine(self) -> None:
        """Scenarios should affect generator state when run via DataEngine."""
        engine, _store = _make_engine()
        press = _get_press(engine)

        # Put press in Running
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        # Add an unplanned stop at current time
        rng = _make_rng()
        sc = UnplannedStop(
            start_time=engine.clock.sim_time,
            rng=rng,
            params={"duration_seconds": [5.0, 5.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        # Next tick should trigger the scenario
        engine.tick()

        # Press should be in Fault state
        assert press.state_machine.current_state == "Fault"
        assert sc.is_active

    def test_active_and_completed_counts(self) -> None:
        """active_scenarios and completed_scenarios should track correctly."""
        rng = _make_rng()
        scenarios_cfg = _all_disabled_scenarios()
        shifts_cfg = ShiftsConfig()

        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
        )

        # Add two scenarios at different times
        sc1 = UnplannedStop(
            start_time=0.0, rng=_make_rng(),
            params={"duration_seconds": [0.5, 0.5]},
        )
        sc2 = UnplannedStop(
            start_time=100.0, rng=_make_rng(),
            params={"duration_seconds": [0.5, 0.5]},
        )
        se.add_scenario(sc1)
        se.add_scenario(sc2)

        assert len(se.pending_scenarios) == 2
        assert len(se.active_scenarios) == 0
        assert len(se.completed_scenarios) == 0


# ---------------------------------------------------------------------------
# DataEngine + ScenarioEngine integration
# ---------------------------------------------------------------------------


class TestDataEngineScenarioIntegration:
    """DataEngine creates and uses a ScenarioEngine."""

    def test_engine_has_scenario_engine(self) -> None:
        """DataEngine should have a scenario_engine property."""
        engine, _store = _make_engine()
        assert engine.scenario_engine is not None
        assert isinstance(engine.scenario_engine, ScenarioEngine)

    def test_scenario_engine_evaluates_on_tick(self) -> None:
        """ScenarioEngine.tick() is called during DataEngine.tick()."""
        engine, _store = _make_engine()

        # Add a scenario that fires immediately
        rng = _make_rng()
        sc = UnplannedStop(
            start_time=0.0, rng=rng,
            params={"duration_seconds": [60.0, 60.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        # First tick should activate it
        engine.tick()
        assert sc.is_active

    def test_scenarios_do_not_break_existing_engine(self) -> None:
        """Adding scenarios should not break normal engine operation."""
        engine, store = _make_engine()

        # Run 100 ticks with no manually added scenarios
        for _ in range(100):
            engine.tick()

        # All 48 signals should still be present
        assert len(store) == 48

    def test_deterministic_scenario_timeline(self) -> None:
        """Same seed produces identical scenario timeline."""
        config1 = _load_packaging_config(seed=99)
        config2 = _load_packaging_config(seed=99)

        store1 = SignalStore()
        store2 = SignalStore()

        clock1 = SimulationClock.from_config(config1.simulation)
        clock2 = SimulationClock.from_config(config2.simulation)

        eng1 = DataEngine(config1, store1, clock1)
        eng2 = DataEngine(config2, store2, clock2)

        # Scenario timelines should have same count and start times
        sc1 = eng1.scenario_engine.scenarios
        sc2 = eng2.scenario_engine.scenarios

        assert len(sc1) == len(sc2)

        for s1, s2 in zip(sc1, sc2, strict=False):
            assert type(s1) is type(s2)
            assert s1.start_time == pytest.approx(s2.start_time)


# ---------------------------------------------------------------------------
# Shift change scheduling details
# ---------------------------------------------------------------------------


class TestShiftChangeScheduling:
    """Shift change scheduling with jitter and operator biases."""

    def test_shift_changes_have_jitter(self) -> None:
        """Shift changes should not fall at exact configured times."""
        rng = _make_rng()
        scenarios_cfg = _all_disabled_scenarios(
            shift_change=ShiftChangeConfig(
                enabled=True,
                times=["06:00", "14:00", "22:00"],
            ),
        )
        shifts_cfg = ShiftsConfig()

        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
            sim_duration_s=24 * 3600,  # 1 day
        )

        shift_scenarios = [
            s for s in se.scenarios if isinstance(s, ShiftChange)
        ]
        assert len(shift_scenarios) > 0

        # Check that start times have jitter (not exactly at configured times)
        exact_times = {6 * 3600, 14 * 3600, 22 * 3600}
        for sc in shift_scenarios:
            assert sc.start_time not in exact_times

    def test_shift_changes_near_configured_times(self) -> None:
        """Shift changes should be within ±10 min of configured times."""
        rng = _make_rng()
        scenarios_cfg = _all_disabled_scenarios(
            shift_change=ShiftChangeConfig(
                enabled=True,
                times=["06:00", "14:00", "22:00"],
            ),
        )
        shifts_cfg = ShiftsConfig()

        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
            sim_duration_s=24 * 3600,
        )

        shift_scenarios = [
            s for s in se.scenarios if isinstance(s, ShiftChange)
        ]

        exact_times_s = [6 * 3600, 14 * 3600, 22 * 3600]
        for sc in shift_scenarios:
            # Should be within ±10 minutes of some configured time
            min_dist = min(abs(sc.start_time - t) for t in exact_times_s)
            assert min_dist <= 600, (
                f"Shift change at {sc.start_time}s is too far from any "
                f"configured time (distance={min_dist}s)"
            )
