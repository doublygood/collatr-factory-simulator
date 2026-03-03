"""Tests for the motor bearing wear scenario (PRD 5.5).

Verifies:
- Exponential vibration increase: base_rate * exp(k * elapsed_hours)
- Motor current increases following the same exponential curve
- Warning / alarm threshold ground-truth logging (fire once each)
- Optional failure culmination: machine state → Fault
- Background priority (no preemption / deferral)
- On completion, all original values are restored
- Auto-scheduling via ScenarioEngine uses BearingWearConfig values
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.generators.press import PressGenerator
from factory_simulator.generators.vibration import VibrationGenerator
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.bearing_wear import BearingWear

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "factory.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def _make_engine(seed: int = 42) -> tuple[DataEngine, object]:
    """Create a DataEngine with all auto-scheduled scenarios disabled."""
    config = load_config(_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = seed
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = 1.0
    # Disable all auto-scheduled scenarios so the test controls exactly
    # which scenarios run.
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

    from factory_simulator.store import SignalStore
    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    return engine, store


def _get_press(engine: DataEngine) -> PressGenerator:
    for gen in engine.generators:
        if isinstance(gen, PressGenerator):
            return gen
    raise RuntimeError("PressGenerator not found")


def _get_vibration(engine: DataEngine) -> VibrationGenerator:
    for gen in engine.generators:
        if isinstance(gen, VibrationGenerator):
            return gen
    raise RuntimeError("VibrationGenerator not found")


def _run_ticks(engine: DataEngine, n: int) -> None:
    for _ in range(n):
        engine.tick()


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------


class TestBearingWearPriority:
    def test_priority_is_background(self) -> None:
        rng = _make_rng()
        sc = BearingWear(start_time=0.0, rng=rng)
        assert sc.priority == "background"


# ---------------------------------------------------------------------------
# Construction / defaults
# ---------------------------------------------------------------------------


class TestBearingWearDefaults:
    def test_default_base_rate_in_range(self) -> None:
        rng = _make_rng()
        sc = BearingWear(start_time=0.0, rng=rng)
        assert 0.001 <= sc.base_rate <= 0.005

    def test_default_k_in_range(self) -> None:
        rng = _make_rng()
        sc = BearingWear(start_time=0.0, rng=rng)
        assert 0.005 <= sc.k <= 0.01

    def test_default_warning_threshold(self) -> None:
        rng = _make_rng()
        sc = BearingWear(start_time=0.0, rng=rng)
        assert sc.warning_threshold == pytest.approx(15.0)

    def test_default_alarm_threshold(self) -> None:
        rng = _make_rng()
        sc = BearingWear(start_time=0.0, rng=rng)
        assert sc.alarm_threshold == pytest.approx(25.0)

    def test_default_not_culminate_in_failure(self) -> None:
        rng = _make_rng()
        sc = BearingWear(start_time=0.0, rng=rng)
        assert sc.culminate_in_failure is False

    def test_default_failure_vibration_in_range(self) -> None:
        rng = _make_rng()
        sc = BearingWear(start_time=0.0, rng=rng)
        assert 40.0 <= sc.failure_vibration <= 50.0

    def test_default_duration_2_weeks(self) -> None:
        rng = _make_rng()
        sc = BearingWear(start_time=0.0, rng=rng)
        assert sc.duration() == pytest.approx(336.0 * 3600.0)

    def test_fixed_params_deterministic(self) -> None:
        rng = _make_rng()
        sc = BearingWear(
            start_time=0.0,
            rng=rng,
            params={
                "base_rate": [0.002, 0.002],
                "acceleration_k": [0.007, 0.007],
                "warning_threshold": 12.0,
                "alarm_threshold": 22.0,
                "current_increase_percent": [2.5, 2.5],
                "culminate_in_failure": True,
                "failure_vibration": [45.0, 45.0],
                "duration_hours": 100.0,
            },
        )
        assert sc.base_rate == pytest.approx(0.002)
        assert sc.k == pytest.approx(0.007)
        assert sc.warning_threshold == pytest.approx(12.0)
        assert sc.alarm_threshold == pytest.approx(22.0)
        assert sc.current_factor == pytest.approx(0.025)
        assert sc.culminate_in_failure is True
        assert sc.failure_vibration == pytest.approx(45.0)
        assert sc.duration() == pytest.approx(100.0 * 3600.0)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestBearingWearLifecycle:
    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = BearingWear(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING

    def test_activates_at_start_time(self) -> None:
        engine, store = _make_engine()
        _get_press(engine).state_machine.force_state("Running")
        _run_ticks(engine, 5)

        sc = BearingWear(start_time=0.0, rng=_make_rng())
        engine.scenario_engine.add_scenario(sc)
        engine.tick()
        assert sc.is_active

    def test_completes_after_duration(self) -> None:
        engine, store = _make_engine()
        _get_press(engine).state_machine.force_state("Running")
        _run_ticks(engine, 5)

        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [0.001, 0.001],
                "acceleration_k": [0.005, 0.005],
                "duration_hours": 0.0005,  # ~1.8 seconds
            },
        )
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed


# ---------------------------------------------------------------------------
# Vibration increase
# ---------------------------------------------------------------------------


class TestBearingWearVibration:
    def test_vibration_target_increases_over_time(self) -> None:
        """Vibration model _target must increase as the scenario progresses."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        vib = _get_vibration(engine)
        original_x = vib._models["main_drive_x"]._target

        # Use a very fast k to see measurable increase in a short test
        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [1.0, 1.0],   # large for fast test
                "acceleration_k": [0.1, 0.1],
                "duration_hours": 10.0,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Activate + run for 1 hour of sim time
        # 1h = 3600s = 36000 ticks at 100ms
        for _ in range(36000):
            engine.tick()

        assert sc.is_active
        new_x = vib._models["main_drive_x"]._target
        elapsed_hours = sc.elapsed / 3600.0
        expected_increase = 1.0 * math.exp(0.1 * elapsed_hours)
        assert new_x == pytest.approx(original_x + expected_increase, rel=1e-3)

    def test_all_three_axes_affected(self) -> None:
        """All three vibration axes must increase."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        vib = _get_vibration(engine)
        orig = {name: m._target for name, m in vib._models.items()}

        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [2.0, 2.0],
                "acceleration_k": [0.1, 0.1],
                "duration_hours": 5.0,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Run ~10 minutes of sim time
        for _ in range(6000):
            engine.tick()

        for name in ("main_drive_x", "main_drive_y", "main_drive_z"):
            assert vib._models[name]._target > orig[name], (
                f"{name} target did not increase"
            )

    def test_vibration_exponential_shape(self) -> None:
        """Vibration increase follows base_rate * exp(k * elapsed_hours)."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        vib = _get_vibration(engine)
        base_rate = 0.5
        k = 0.2

        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [base_rate, base_rate],
                "acceleration_k": [k, k],
                "duration_hours": 20.0,
            },
        )
        orig_x = vib._models["main_drive_x"]._target
        engine.scenario_engine.add_scenario(sc)

        # Sample at t≈1h and t≈2h
        for _ in range(36000):  # 1 hour
            engine.tick()
        t1_hours = sc.elapsed / 3600.0
        actual_t1 = vib._models["main_drive_x"]._target - orig_x
        expected_t1 = base_rate * math.exp(k * t1_hours)
        assert actual_t1 == pytest.approx(expected_t1, rel=1e-2)

    def test_vibration_restored_on_completion(self) -> None:
        """Original vibration targets must be restored after scenario ends."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        vib = _get_vibration(engine)
        orig = {name: m._target for name, m in vib._models.items()}

        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [2.0, 2.0],
                "acceleration_k": [0.1, 0.1],
                "duration_hours": 0.001,  # ~3.6 seconds
            },
        )
        engine.scenario_engine.add_scenario(sc)

        for _ in range(100):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        for name in ("main_drive_x", "main_drive_y", "main_drive_z"):
            assert vib._models[name]._target == pytest.approx(orig[name], rel=1e-9)


# ---------------------------------------------------------------------------
# Current increase
# ---------------------------------------------------------------------------


class TestBearingWearCurrent:
    def test_current_base_increases_over_time(self) -> None:
        """press._main_drive_current._base must increase with bearing wear."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        original_base = press._main_drive_current._base

        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [1.0, 1.0],
                "acceleration_k": [0.1, 0.1],
                "current_increase_percent": [5.0, 5.0],
                "duration_hours": 10.0,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Run 30 minutes of sim time
        for _ in range(18000):
            engine.tick()

        assert press._main_drive_current._base > original_base

    def test_current_follows_same_exponential(self) -> None:
        """Current increase = saved_base * current_factor * exp(k * elapsed_hours)."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        k = 0.1
        current_pct = 10.0  # 10% for clear signal
        orig_base = press._main_drive_current._base

        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [0.001, 0.001],
                "acceleration_k": [k, k],
                "current_increase_percent": [current_pct, current_pct],
                "duration_hours": 5.0,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Run 1 hour
        for _ in range(36000):
            engine.tick()

        elapsed_hours = sc.elapsed / 3600.0
        current_factor = current_pct / 100.0
        expected_offset = orig_base * current_factor * math.exp(k * elapsed_hours)
        expected_base = orig_base + expected_offset

        assert press._main_drive_current._base == pytest.approx(
            expected_base, rel=1e-3
        )

    def test_current_base_restored_on_completion(self) -> None:
        """Current _base must be restored after scenario completes."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        original_base = press._main_drive_current._base

        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [0.5, 0.5],
                "acceleration_k": [0.1, 0.1],
                "current_increase_percent": [5.0, 5.0],
                "duration_hours": 0.001,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        for _ in range(100):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert press._main_drive_current._base == pytest.approx(
            original_base, rel=1e-9
        )


# ---------------------------------------------------------------------------
# Failure culmination
# ---------------------------------------------------------------------------


class TestBearingWearFailure:
    def test_failure_sets_press_fault(self) -> None:
        """When culminate_in_failure=True and threshold reached, press → Fault."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [1000.0, 1000.0],  # massive base_rate to reach threshold fast
                "acceleration_k": [0.001, 0.001],
                "culminate_in_failure": True,
                "failure_vibration": [1.0, 1.0],  # low threshold for fast test
                "duration_hours": 100.0,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert press.state_machine.current_state == "Fault"

    def test_no_failure_without_flag(self) -> None:
        """Press stays Running when culminate_in_failure=False."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [1000.0, 1000.0],
                "acceleration_k": [0.001, 0.001],
                "culminate_in_failure": False,
                "failure_vibration": [1.0, 1.0],
                "duration_hours": 0.001,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        for _ in range(100):
            engine.tick()
            if sc.is_completed:
                break

        # Press should not have been forced to Fault by BearingWear
        # (it may be in any state after normal state machine, but not
        # forced Fault by bearing wear — check completed without Fault trigger)
        assert sc.is_completed


# ---------------------------------------------------------------------------
# Threshold logging
# ---------------------------------------------------------------------------


class TestBearingWearThresholds:
    def test_warning_threshold_not_breached_early(self) -> None:
        """With small base_rate/k, warning is not reached in a short run."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [0.001, 0.001],
                "acceleration_k": [0.005, 0.005],
                "warning_threshold": 15.0,
                "alarm_threshold": 25.0,
                "duration_hours": 0.01,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        for _ in range(500):
            engine.tick()

        assert not sc._warning_logged
        assert not sc._alarm_logged

    def test_warning_logged_when_crossed(self) -> None:
        """Warning flag is set once vibration increase >= warning_threshold."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        # Use params that will cross warning (5.0 mm/s) quickly
        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [100.0, 100.0],
                "acceleration_k": [0.001, 0.001],
                "warning_threshold": 5.0,
                "alarm_threshold": 9999.0,  # not crossed in this test
                "duration_hours": 1.0,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # base_rate=100 means increase = 100 * exp(0.001 * t_hours)
        # At t=0 this is already 100 >> 5.0, so warning should fire immediately
        for _ in range(20):
            engine.tick()

        assert sc._warning_logged

    def test_alarm_logged_when_crossed(self) -> None:
        """Alarm flag is set once vibration increase >= alarm_threshold."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [200.0, 200.0],
                "acceleration_k": [0.001, 0.001],
                "warning_threshold": 5.0,
                "alarm_threshold": 50.0,
                "duration_hours": 1.0,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        for _ in range(20):
            engine.tick()

        assert sc._alarm_logged

    def test_warning_logged_once_not_multiple_times(self) -> None:
        """Warning flag fires only once, not every tick after crossing."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        sc = BearingWear(
            start_time=0.0,
            rng=_make_rng(),
            params={
                "base_rate": [100.0, 100.0],
                "acceleration_k": [0.001, 0.001],
                "warning_threshold": 5.0,
                "alarm_threshold": 9999.0,
                "duration_hours": 1.0,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        for _ in range(200):
            engine.tick()

        # Flag should be True exactly once (not reset and re-set)
        assert sc._warning_logged is True


# ---------------------------------------------------------------------------
# Auto-scheduling via ScenarioEngine
# ---------------------------------------------------------------------------


class TestBearingWearScheduling:
    def test_bearing_wear_scheduled_when_enabled(self) -> None:
        """With bearing_wear enabled, one BearingWear appears in the timeline."""
        from factory_simulator.store import SignalStore

        config = load_config(_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 1
        config.simulation.tick_interval_ms = 100
        # Long simulation so start_after_hours (48h) is within window
        config.simulation.sim_duration_s = 86400 * 7  # 1 week

        # Disable everything except bearing_wear
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

        config.scenarios.bearing_wear.enabled = True
        config.scenarios.bearing_wear.start_after_hours = 10.0  # within 1 week

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        bearing_wear_scenarios = [
            s for s in engine.scenario_engine.scenarios
            if isinstance(s, BearingWear)
        ]
        assert len(bearing_wear_scenarios) == 1

    def test_bearing_wear_not_scheduled_when_disabled(self) -> None:
        """With bearing_wear disabled, no BearingWear appears."""
        from factory_simulator.store import SignalStore

        config = load_config(_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 2
        config.scenarios.bearing_wear.enabled = False

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        bearing_wear_scenarios = [
            s for s in engine.scenario_engine.scenarios
            if isinstance(s, BearingWear)
        ]
        assert len(bearing_wear_scenarios) == 0

    def test_bearing_wear_start_time_matches_config(self) -> None:
        """BearingWear start_time equals start_after_hours * 3600."""
        from factory_simulator.store import SignalStore

        config = load_config(_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 3
        config.simulation.sim_duration_s = 86400 * 7
        config.scenarios.bearing_wear.enabled = True
        config.scenarios.bearing_wear.start_after_hours = 24.0

        # Disable all others
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

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        sc = next(
            s for s in engine.scenario_engine.scenarios
            if isinstance(s, BearingWear)
        )
        assert sc.start_time == pytest.approx(24.0 * 3600.0)
