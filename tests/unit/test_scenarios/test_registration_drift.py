"""Tests for the registration drift scenario.

Verifies (PRD 5.7):
- Registration error x or y drifts beyond +/-0.3 mm.
- Drift is gradual: 0.01-0.05 mm per second.
- Waste count increment rate increases while error exceeds 0.2 mm.
- Returns to center after auto-correction (reversion rate restored).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.generators.press import PressGenerator
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.registration_drift import RegistrationDrift
from factory_simulator.store import SignalStore

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "factory.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(seed: int = 42) -> tuple[DataEngine, SignalStore]:
    """Create a DataEngine with all auto-scheduled scenarios disabled."""
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
    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    return engine, store


def _get_press(engine: DataEngine) -> PressGenerator:
    """Find the press generator."""
    for gen in engine.generators:
        if isinstance(gen, PressGenerator):
            return gen
    raise RuntimeError("Press generator not found")


def _run_ticks(engine: DataEngine, n: int) -> float:
    """Run n ticks and return final sim_time."""
    t = 0.0
    for _ in range(n):
        t = engine.tick()
    return t


def _make_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestRegistrationDriftLifecycle:
    """Scenario lifecycle: pending -> active -> completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = RegistrationDrift(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_at_start_time(self) -> None:
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = RegistrationDrift(start_time=0.0, rng=rng)
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active

    def test_completes_after_drift_duration(self) -> None:
        """Scenario completes once drift_duration has elapsed."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={"duration_range": [2.0, 2.0]},
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):  # 5s of sim time
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

    def test_duration_method(self) -> None:
        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={"duration_range": [300, 300]},
        )
        assert sc.duration() == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# Drift behavior tests
# ---------------------------------------------------------------------------


class TestRegistrationDriftBehavior:
    """PRD 5.7 steps 1-2: gradual drift of registration error."""

    def test_drift_overrides_model_value(self) -> None:
        """The scenario must override the RandomWalkModel._value each tick."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={
                "axis": "x",
                "direction": 1,
                "drift_rate_range": [0.05, 0.05],
                "duration_range": [60.0, 60.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run 50 ticks (5s). Expected drift = 0.05 * 5.0 = 0.25 mm
        for _ in range(50):
            engine.tick()

        assert sc.is_active
        # Model value should be near the expected drift position
        # Center is 0.0, drift = +0.05 * elapsed
        expected = 0.0 + 0.05 * sc.elapsed
        assert press._reg_error_x._value == pytest.approx(expected, abs=0.01)

    def test_drift_positive_direction(self) -> None:
        """Positive direction drift should increase error value."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        center = press._reg_error_x._center

        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={
                "axis": "x",
                "direction": 1,
                "drift_rate_range": [0.03, 0.03],
                "duration_range": [60.0, 60.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(100):  # 10s
            engine.tick()

        assert press._reg_error_x._value > center

    def test_drift_negative_direction(self) -> None:
        """Negative direction drift should decrease error value."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        center = press._reg_error_y._center

        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={
                "axis": "y",
                "direction": -1,
                "drift_rate_range": [0.03, 0.03],
                "duration_range": [60.0, 60.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(100):  # 10s
            engine.tick()

        assert press._reg_error_y._value < center

    def test_reversion_suppressed_during_drift(self) -> None:
        """Mean-reversion rate must be 0 during drift to prevent pull-back."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        original_reversion = press._reg_error_x._reversion_rate
        assert original_reversion > 0.0  # Sanity check

        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={
                "axis": "x",
                "duration_range": [60.0, 60.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)
        engine.tick()  # Activate

        assert sc.is_active
        assert press._reg_error_x._reversion_rate == 0.0

    def test_drift_rate_within_prd_range(self) -> None:
        """Default drift rate must be in 0.01-0.05 mm/s (PRD 5.7)."""
        rng = _make_rng()
        sc = RegistrationDrift(start_time=0.0, rng=rng)
        assert 0.01 <= sc.drift_rate <= 0.05

    def test_drift_exceeds_0_3mm(self) -> None:
        """Drift should reach beyond 0.3 mm (PRD 5.7 step 1)."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={
                "axis": "x",
                "direction": 1,
                "drift_rate_range": [0.05, 0.05],
                "duration_range": [30.0, 30.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run 100 ticks (10s). Drift = 0.05 * 10 = 0.5 mm > 0.3
        for _ in range(100):
            engine.tick()

        assert abs(press._reg_error_x._value) > 0.3


# ---------------------------------------------------------------------------
# Waste rate tests
# ---------------------------------------------------------------------------


class TestRegistrationDriftWasteRate:
    """PRD 5.7 step 4: waste increases while error exceeds 0.2 mm."""

    def test_waste_not_increased_below_threshold(self) -> None:
        """Waste rate should remain normal when drift < 0.2 mm."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        original_rate = press._waste_count._rate

        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={
                "axis": "x",
                "direction": 1,
                "drift_rate_range": [0.01, 0.01],  # Slow drift
                "duration_range": [60.0, 60.0],
                "waste_increase_range": [1.5, 1.5],
                "waste_threshold": 0.2,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run 10 ticks (1s). Drift = 0.01 * 1.0 = 0.01 mm < 0.2
        for _ in range(10):
            engine.tick()

        assert sc.is_active
        assert press._waste_count._rate == pytest.approx(original_rate, rel=1e-9)

    def test_waste_increased_above_threshold(self) -> None:
        """Waste rate must increase when drift exceeds 0.2 mm."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        original_rate = press._waste_count._rate

        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={
                "axis": "x",
                "direction": 1,
                "drift_rate_range": [0.05, 0.05],
                "duration_range": [60.0, 60.0],
                "waste_increase_range": [1.5, 1.5],
                "waste_threshold": 0.2,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run 50 ticks (5s). Drift = 0.05 * 5.0 = 0.25 mm > 0.2
        for _ in range(50):
            engine.tick()

        assert sc.is_active
        assert press._waste_count._rate == pytest.approx(
            original_rate * 1.5, rel=1e-9
        )

    def test_waste_restored_on_completion(self) -> None:
        """Waste rate must return to original after scenario ends."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        original_rate = press._waste_count._rate

        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={
                "waste_increase_range": [1.3, 1.3],
                "duration_range": [2.0, 2.0],  # Short for fast test
                "drift_rate_range": [0.05, 0.05],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):  # 5s, enough to exceed 2s duration
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert press._waste_count._rate == pytest.approx(
            original_rate, rel=1e-9
        )


# ---------------------------------------------------------------------------
# Recovery tests
# ---------------------------------------------------------------------------


class TestRegistrationDriftRecovery:
    """PRD 5.7 step 5: returns to center after auto-correction."""

    def test_reversion_rate_restored_on_completion(self) -> None:
        """Mean-reversion rate must be restored after scenario ends."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        original_reversion = press._reg_error_x._reversion_rate

        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={
                "axis": "x",
                "duration_range": [2.0, 2.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert press._reg_error_x._reversion_rate == pytest.approx(
            original_reversion, rel=1e-9
        )

    def test_unaffected_axis_unchanged(self) -> None:
        """The axis not selected for drift must remain unmodified."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        original_y_reversion = press._reg_error_y._reversion_rate

        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={
                "axis": "x",  # Only X axis
                "duration_range": [60.0, 60.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)
        engine.tick()

        assert sc.is_active
        # Y axis reversion should be untouched
        assert press._reg_error_y._reversion_rate == pytest.approx(
            original_y_reversion, rel=1e-9
        )

    def test_all_params_restored_on_completion(self) -> None:
        """All modified parameters must be restored after completion."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        original_x_reversion = press._reg_error_x._reversion_rate
        original_y_reversion = press._reg_error_y._reversion_rate
        original_waste = press._waste_count._rate

        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={
                "axis": "x",
                "drift_rate_range": [0.05, 0.05],
                "waste_increase_range": [1.4, 1.4],
                "duration_range": [2.0, 2.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert press._reg_error_x._reversion_rate == pytest.approx(
            original_x_reversion, rel=1e-9
        )
        assert press._reg_error_y._reversion_rate == pytest.approx(
            original_y_reversion, rel=1e-9
        )
        assert press._waste_count._rate == pytest.approx(
            original_waste, rel=1e-9
        )


# ---------------------------------------------------------------------------
# Axis and direction selection tests
# ---------------------------------------------------------------------------


class TestRegistrationDriftAxisDirection:
    """Verify axis and direction selection logic."""

    def test_explicit_x(self) -> None:
        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0, rng=rng, params={"axis": "x"}
        )
        assert sc.axis == "x"

    def test_explicit_y(self) -> None:
        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0, rng=rng, params={"axis": "y"}
        )
        assert sc.axis == "y"

    def test_random_axis_is_valid(self) -> None:
        """When axis is not specified, it must be 'x' or 'y'."""
        rng = _make_rng()
        sc = RegistrationDrift(start_time=0.0, rng=rng)
        assert sc.axis in ("x", "y")

    def test_explicit_positive_direction(self) -> None:
        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0, rng=rng, params={"direction": 1}
        )
        assert sc.direction == 1

    def test_explicit_negative_direction(self) -> None:
        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0, rng=rng, params={"direction": -1}
        )
        assert sc.direction == -1

    def test_random_direction_is_valid(self) -> None:
        """When direction is not specified, it must be +1 or -1."""
        rng = _make_rng()
        sc = RegistrationDrift(start_time=0.0, rng=rng)
        assert sc.direction in (1, -1)


# ---------------------------------------------------------------------------
# Parameter defaults
# ---------------------------------------------------------------------------


class TestRegistrationDriftDefaults:
    """Verify default parameter ranges match PRD."""

    def test_default_duration_range(self) -> None:
        """Default duration: 2-10 min (120-600 s)."""
        rng = _make_rng()
        sc = RegistrationDrift(start_time=0.0, rng=rng)
        assert 120 <= sc.drift_duration <= 600

    def test_default_drift_rate_range(self) -> None:
        """Default drift rate: 0.01-0.05 mm/s (PRD 5.7)."""
        rng = _make_rng()
        sc = RegistrationDrift(start_time=0.0, rng=rng)
        assert 0.01 <= sc.drift_rate <= 0.05

    def test_default_waste_threshold(self) -> None:
        """Default waste threshold: 0.2 mm (PRD 5.7 step 4)."""
        rng = _make_rng()
        sc = RegistrationDrift(start_time=0.0, rng=rng)
        assert sc.waste_threshold == pytest.approx(0.2)

    def test_default_waste_multiplier_range(self) -> None:
        """Default waste multiplier: 1.2-1.5 (20-50% increase)."""
        rng = _make_rng()
        sc = RegistrationDrift(start_time=0.0, rng=rng)
        assert 1.2 <= sc.waste_multiplier <= 1.5

    def test_fixed_params_are_deterministic(self) -> None:
        """Fixed parameter ranges should produce exact values."""
        rng = _make_rng()
        sc = RegistrationDrift(
            start_time=0.0,
            rng=rng,
            params={
                "duration_range": [300, 300],
                "drift_rate_range": [0.03, 0.03],
                "waste_increase_range": [1.25, 1.25],
                "waste_threshold": 0.15,
                "axis": "y",
                "direction": -1,
            },
        )
        assert sc.drift_duration == pytest.approx(300.0)
        assert sc.drift_rate == pytest.approx(0.03)
        assert sc.waste_multiplier == pytest.approx(1.25)
        assert sc.waste_threshold == pytest.approx(0.15)
        assert sc.axis == "y"
        assert sc.direction == -1
