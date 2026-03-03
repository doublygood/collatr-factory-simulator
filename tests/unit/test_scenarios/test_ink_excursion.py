"""Tests for the ink viscosity excursion scenario.

Verifies (PRD 5.6):
- Ink viscosity drifts below 18 s (thin) or above 45 s (thick).
- Registration error x/y increases during the excursion.
- Waste count increment rate increases by 10-30%.
- After excursion duration, viscosity returns to normal range.
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
from factory_simulator.scenarios.ink_excursion import InkExcursion, _Direction
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


class TestInkExcursionLifecycle:
    """Scenario lifecycle: pending -> active -> completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = InkExcursion(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_at_start_time(self) -> None:
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = InkExcursion(start_time=0.0, rng=rng)
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active

    def test_completes_after_excursion_duration(self) -> None:
        """Scenario completes once excursion_duration has elapsed."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = InkExcursion(
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
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={"duration_range": [600, 600]},
        )
        assert sc.duration() == pytest.approx(600.0)


# ---------------------------------------------------------------------------
# Viscosity drift tests
# ---------------------------------------------------------------------------


class TestInkExcursionViscosity:
    """PRD 5.6 step 1: viscosity drifts outside normal range."""

    def test_thin_excursion_lowers_target(self) -> None:
        """Thin excursion should lower the viscosity model target."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        original_target = press._ink_viscosity._target  # 28.0

        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "direction": "thin",
                "thin_target_range": [15.0, 15.0],
                "duration_range": [60.0, 60.0],
                "ramp_fraction": 0.3,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run enough ticks to be well into the ramp phase
        # 60s duration * 0.3 ramp = 18s ramp. Run 300 ticks (30s) to finish ramp.
        for _ in range(300):
            engine.tick()

        assert sc.is_active
        # Target should have moved toward 15.0
        assert press._ink_viscosity._target < original_target
        assert press._ink_viscosity._target == pytest.approx(15.0, abs=0.5)

    def test_thick_excursion_raises_target(self) -> None:
        """Thick excursion should raise the viscosity model target."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        original_target = press._ink_viscosity._target  # 28.0

        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "direction": "thick",
                "thick_target_range": [48.0, 48.0],
                "duration_range": [60.0, 60.0],
                "ramp_fraction": 0.3,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(300):
            engine.tick()

        assert sc.is_active
        assert press._ink_viscosity._target > original_target
        assert press._ink_viscosity._target == pytest.approx(48.0, abs=0.5)

    def test_viscosity_ramps_gradually(self) -> None:
        """Viscosity target should change gradually during ramp phase."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        original_target = press._ink_viscosity._target  # 28.0

        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "direction": "thin",
                "thin_target_range": [15.0, 15.0],
                "duration_range": [100.0, 100.0],
                "ramp_fraction": 0.5,  # 50s ramp
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run 100 ticks (10s) — 20% through the 50s ramp
        for _ in range(100):
            engine.tick()

        mid_target = press._ink_viscosity._target
        # Should be between original (28) and excursion (15)
        assert mid_target < original_target
        assert mid_target > 15.0

        # Run more ticks to reach past ramp end
        for _ in range(400):
            engine.tick()

        end_target = press._ink_viscosity._target
        # Should be at or very near the excursion target
        assert end_target == pytest.approx(15.0, abs=0.5)
        # End target should be closer to excursion than mid target
        assert abs(end_target - 15.0) < abs(mid_target - 15.0)

    def test_viscosity_store_value_drifts_during_excursion(self) -> None:
        """The store value should reflect the drifted viscosity."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        # Record baseline viscosity
        baseline_visc = store.get_value("press.ink_viscosity")
        assert isinstance(baseline_visc, float)

        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "direction": "thick",
                "thick_target_range": [48.0, 48.0],
                "duration_range": [60.0, 60.0],
                "ramp_fraction": 0.2,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Ink viscosity generator fires every 30s (sample_rate_ms=30000)
        # = 300 ticks. Run 600 ticks (60s) to get 2 generator fires.
        for _ in range(600):
            engine.tick()

        excursion_visc = store.get_value("press.ink_viscosity")
        assert isinstance(excursion_visc, float)
        # Should be significantly higher than baseline (noise sigma=1.5)
        assert excursion_visc > baseline_visc + 5.0


# ---------------------------------------------------------------------------
# Registration error tests
# ---------------------------------------------------------------------------


class TestInkExcursionRegistrationError:
    """PRD 5.6 step 2: registration error increases during excursion."""

    def test_reg_error_drift_rate_increased(self) -> None:
        """Registration error drift rates must be multiplied during excursion."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        original_x_rate = press._reg_error_x._drift_rate
        original_y_rate = press._reg_error_y._drift_rate

        rng = _make_rng()
        multiplier = 4.0
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "reg_error_multiplier_range": [multiplier, multiplier],
                "duration_range": [60.0, 60.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)
        engine.tick()  # Activate

        assert sc.is_active
        assert press._reg_error_x._drift_rate == pytest.approx(
            original_x_rate * multiplier, rel=1e-9
        )
        assert press._reg_error_y._drift_rate == pytest.approx(
            original_y_rate * multiplier, rel=1e-9
        )

    def test_reg_error_drift_rate_restored(self) -> None:
        """Registration error drift rates must be restored after completion."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        original_x_rate = press._reg_error_x._drift_rate
        original_y_rate = press._reg_error_y._drift_rate

        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "reg_error_multiplier_range": [5.0, 5.0],
                "duration_range": [2.0, 2.0],  # Short for fast test
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):  # 5s, enough to exceed 2s duration
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert press._reg_error_x._drift_rate == pytest.approx(
            original_x_rate, rel=1e-9
        )
        assert press._reg_error_y._drift_rate == pytest.approx(
            original_y_rate, rel=1e-9
        )


# ---------------------------------------------------------------------------
# Waste rate tests
# ---------------------------------------------------------------------------


class TestInkExcursionWasteRate:
    """PRD 5.6 step 3: waste_count increment rate increases 10-30%."""

    def test_waste_rate_increased_during_excursion(self) -> None:
        """Waste counter rate must increase by the configured multiplier."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        original_rate = press._waste_count._rate

        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "waste_increase_range": [1.2, 1.2],  # 20% increase
                "duration_range": [60.0, 60.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)
        engine.tick()  # Activate

        assert sc.is_active
        assert press._waste_count._rate == pytest.approx(
            original_rate * 1.2, rel=1e-9
        )

    def test_waste_rate_restored_after_completion(self) -> None:
        """Waste counter rate must return to original after scenario ends."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        original_rate = press._waste_count._rate

        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "waste_increase_range": [1.25, 1.25],
                "duration_range": [2.0, 2.0],  # Short for fast test
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):  # 5s
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


class TestInkExcursionRecovery:
    """PRD 5.6 step 4: viscosity returns to normal range."""

    def test_viscosity_target_restored_on_completion(self) -> None:
        """Viscosity model target must return to original after completion."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        original_target = press._ink_viscosity._target

        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "direction": "thin",
                "thin_target_range": [15.0, 15.0],
                "duration_range": [2.0, 2.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert press._ink_viscosity._target == pytest.approx(
            original_target, rel=1e-9
        )

    def test_all_params_restored_on_completion(self) -> None:
        """All modified parameters must be restored after completion."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        original_target = press._ink_viscosity._target
        original_x_rate = press._reg_error_x._drift_rate
        original_y_rate = press._reg_error_y._drift_rate
        original_waste = press._waste_count._rate

        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "direction": "thick",
                "thick_target_range": [48.0, 48.0],
                "reg_error_multiplier_range": [5.0, 5.0],
                "waste_increase_range": [1.3, 1.3],
                "duration_range": [2.0, 2.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert press._ink_viscosity._target == pytest.approx(original_target, rel=1e-9)
        assert press._reg_error_x._drift_rate == pytest.approx(original_x_rate, rel=1e-9)
        assert press._reg_error_y._drift_rate == pytest.approx(original_y_rate, rel=1e-9)
        assert press._waste_count._rate == pytest.approx(original_waste, rel=1e-9)


# ---------------------------------------------------------------------------
# Direction selection tests
# ---------------------------------------------------------------------------


class TestInkExcursionDirection:
    """Verify direction selection logic."""

    def test_explicit_thin(self) -> None:
        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0, rng=rng, params={"direction": "thin"}
        )
        assert sc.direction == _Direction.THIN

    def test_explicit_thick(self) -> None:
        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0, rng=rng, params={"direction": "thick"}
        )
        assert sc.direction == _Direction.THICK

    def test_random_direction_is_valid(self) -> None:
        """When direction is not specified, it must be THIN or THICK."""
        rng = _make_rng()
        sc = InkExcursion(start_time=0.0, rng=rng)
        assert sc.direction in (_Direction.THIN, _Direction.THICK)

    def test_thin_target_in_expected_range(self) -> None:
        """Thin excursion target should be below 18 (normal lower bound)."""
        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={"direction": "thin"},
        )
        assert sc.excursion_target < 18.0  # Below normal range

    def test_thick_target_in_expected_range(self) -> None:
        """Thick excursion target should be above 45 (normal upper bound)."""
        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={"direction": "thick"},
        )
        assert sc.excursion_target > 45.0  # Above normal range


# ---------------------------------------------------------------------------
# Parameter defaults
# ---------------------------------------------------------------------------


class TestInkExcursionDefaults:
    """Verify default parameter ranges match PRD."""

    def test_default_duration_range(self) -> None:
        """Default excursion duration: 5-30 min (300-1800 s)."""
        rng = _make_rng()
        sc = InkExcursion(start_time=0.0, rng=rng)
        assert 300 <= sc.excursion_duration <= 1800

    def test_default_waste_increase_range(self) -> None:
        """Default waste increase: 10-30% (multiplier 1.1-1.3)."""
        rng = _make_rng()
        sc = InkExcursion(start_time=0.0, rng=rng)
        assert 1.1 <= sc.waste_multiplier <= 1.3

    def test_default_reg_error_multiplier_range(self) -> None:
        """Default registration error multiplier: 3.0-5.0."""
        rng = _make_rng()
        sc = InkExcursion(start_time=0.0, rng=rng)
        assert 3.0 <= sc.reg_error_multiplier <= 5.0

    def test_fixed_params_are_deterministic(self) -> None:
        """Fixed parameter ranges should produce exact values."""
        rng = _make_rng()
        sc = InkExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "duration_range": [500, 500],
                "waste_increase_range": [1.15, 1.15],
                "reg_error_multiplier_range": [4.0, 4.0],
                "direction": "thin",
                "thin_target_range": [16.0, 16.0],
            },
        )
        assert sc.excursion_duration == pytest.approx(500.0)
        assert sc.waste_multiplier == pytest.approx(1.15)
        assert sc.reg_error_multiplier == pytest.approx(4.0)
        assert sc.excursion_target == pytest.approx(16.0)
