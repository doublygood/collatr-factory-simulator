"""Tests for the dryer temperature drift scenario.

Verifies (PRD 5.4):
- One dryer zone drifts above its setpoint.
- Drift rate: 0.05-0.2 C per minute.
- Over 30-120 minutes, the zone drifts 5-15 C above setpoint.
- press.waste_count increment rate increases by 20-50% during drift.
- After drift duration, temperature returns to setpoint.
- Drift does not trigger a fault state.
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
from factory_simulator.scenarios.dryer_drift import DryerDrift
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


def _stabilise_dryer(press: PressGenerator, zone: int) -> None:
    """Force the dryer model's internal value to its setpoint.

    This avoids waiting hundreds of ticks for the lag model to converge
    from its initial_value (20 C) to the setpoint (75-85 C).
    """
    if zone == 1:
        model = press._dryer_temp_1
    elif zone == 2:
        model = press._dryer_temp_2
    else:
        model = press._dryer_temp_3
    model._value = model.setpoint


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestDryerDriftLifecycle:
    """Scenario lifecycle: pending -> active -> completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = DryerDrift(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_at_start_time(self) -> None:
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = DryerDrift(start_time=0.0, rng=rng)
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active

    def test_completes_after_drift_duration(self) -> None:
        """Scenario completes once drift_duration has elapsed."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = DryerDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_duration_range": [2.0, 2.0],  # 2 seconds for fast test
                "zone": 1,
            },
        )
        _stabilise_dryer(press, 1)

        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):  # 5s of sim time
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

    def test_duration_method(self) -> None:
        rng = _make_rng()
        sc = DryerDrift(
            start_time=0.0,
            rng=rng,
            params={"drift_duration_range": [3600, 3600]},
        )
        assert sc.duration() == pytest.approx(3600.0)


# ---------------------------------------------------------------------------
# Temperature drift tests
# ---------------------------------------------------------------------------


class TestDryerDriftTemperature:
    """PRD 5.4 steps 1-3: temperature drifts above setpoint."""

    def test_temperature_increases_above_setpoint(self) -> None:
        """Dryer temperature model value must exceed the setpoint during drift."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        zone = 1
        _stabilise_dryer(press, zone)
        setpoint = press._dryer_temp_1.setpoint

        rng = _make_rng()
        sc = DryerDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_rate_range": [30.0, 30.0],  # 30 C/min — fast for test
                "drift_duration_range": [60.0, 60.0],
                "drift_range": [20.0, 20.0],
                "zone": zone,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run 100 ticks (10s sim time).  At 30 C/min, drift is ~5 C.
        # The dryer temp generator fires every 5s (50 ticks), so we
        # get 2 generator fires, updating the store with the drifted value.
        for _ in range(100):
            engine.tick()

        assert sc.is_active

        # Check model internal value (not store, which includes noise)
        dryer_model_value = press._dryer_temp_1._value
        assert dryer_model_value > setpoint + 2.0

        # Also verify the store value shows the drift (tolerant of noise)
        dryer_temp = store.get_value("press.dryer_temp_zone_1")
        assert isinstance(dryer_temp, float)
        assert dryer_temp > setpoint + 1.0

    def test_drift_rate_produces_expected_offset(self) -> None:
        """Drift offset should match drift_rate * elapsed / 60."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        zone = 2
        _stabilise_dryer(press, zone)
        setpoint = press._dryer_temp_2.setpoint

        rng = _make_rng()
        drift_rate = 12.0  # 12 C/min for clear signal
        sc = DryerDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_rate_range": [drift_rate, drift_rate],
                "drift_duration_range": [120.0, 120.0],
                "drift_range": [50.0, 50.0],  # won't cap
                "zone": zone,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run 300 ticks (30s sim time).  At 12 C/min, expected drift = 6 C.
        for _ in range(300):
            engine.tick()

        assert sc.is_active
        dryer_temp = store.get_value("press.dryer_temp_zone_2")
        assert isinstance(dryer_temp, float)

        expected_drift = drift_rate * 30.0 / 60.0  # 6.0 C
        actual_drift = dryer_temp - setpoint

        # Allow +-2 C tolerance for noise (sigma=0.8) and lag correction
        assert abs(actual_drift - expected_drift) < 2.0

    def test_drift_capped_at_max_drift(self) -> None:
        """Drift should not exceed max_drift."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        zone = 1
        _stabilise_dryer(press, zone)
        setpoint = press._dryer_temp_1.setpoint

        max_drift = 3.0
        rng = _make_rng()
        sc = DryerDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_rate_range": [30.0, 30.0],  # Very fast: 30 C/min
                "drift_duration_range": [120.0, 120.0],
                "drift_range": [max_drift, max_drift],
                "zone": zone,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run 300 ticks (30s).  At 30 C/min, drift would be 15 C without cap.
        # With cap at 3 C, the dryer model value should not drift much beyond.
        for _ in range(300):
            engine.tick()

        dryer_model = press._dryer_temp_1
        # The model value (before noise) should be near setpoint + max_drift
        # Allow for lag correction reducing it slightly
        assert dryer_model._value <= setpoint + max_drift + 0.5

    def test_no_fault_state_during_drift(self) -> None:
        """PRD 5.4: drift does not trigger a fault state."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        _stabilise_dryer(press, 1)

        rng = _make_rng()
        sc = DryerDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_rate_range": [6.0, 6.0],
                "drift_duration_range": [30.0, 30.0],
                "zone": 1,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(300):
            engine.tick()

        assert press.state_machine.current_state == "Running"

    def test_setpoint_unchanged_during_drift(self) -> None:
        """The setpoint signal must stay constant; only actual temp drifts."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        zone = 1
        _stabilise_dryer(press, zone)

        # Record initial setpoint from the store
        initial_sp = store.get_value("press.dryer_setpoint_zone_1")

        rng = _make_rng()
        sc = DryerDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_rate_range": [6.0, 6.0],
                "drift_duration_range": [30.0, 30.0],
                "zone": zone,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(200):
            engine.tick()

        # Setpoint signal must be unchanged
        final_sp = store.get_value("press.dryer_setpoint_zone_1")
        assert final_sp == pytest.approx(initial_sp, abs=0.1)


# ---------------------------------------------------------------------------
# Waste rate tests
# ---------------------------------------------------------------------------


class TestDryerDriftWasteRate:
    """PRD 5.4 step 4: waste_count increment rate increases 20-50%."""

    def test_waste_rate_increased_during_drift(self) -> None:
        """Waste counter rate must increase by the configured multiplier."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        original_rate = press._waste_count._rate

        rng = _make_rng()
        sc = DryerDrift(
            start_time=0.0,
            rng=rng,
            params={
                "waste_increase_range": [1.3, 1.3],  # 30% increase
                "drift_duration_range": [60.0, 60.0],
                "zone": 1,
            },
        )

        engine.scenario_engine.add_scenario(sc)
        engine.tick()  # Activate

        assert sc.is_active
        assert press._waste_count._rate == pytest.approx(
            original_rate * 1.3, rel=1e-9
        )

    def test_waste_rate_restored_after_completion(self) -> None:
        """Waste counter rate must return to original after scenario ends."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        original_rate = press._waste_count._rate

        rng = _make_rng()
        sc = DryerDrift(
            start_time=0.0,
            rng=rng,
            params={
                "waste_increase_range": [1.4, 1.4],
                "drift_duration_range": [2.0, 2.0],  # Short for fast test
                "zone": 1,
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


class TestDryerDriftRecovery:
    """PRD 5.4 step 5: temperature returns to setpoint after drift."""

    def test_temperature_recovers_toward_setpoint(self) -> None:
        """After completion, the lag model should pull temp back toward sp."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        zone = 1
        _stabilise_dryer(press, zone)
        setpoint = press._dryer_temp_1.setpoint

        rng = _make_rng()
        sc = DryerDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_rate_range": [30.0, 30.0],  # Fast drift
                "drift_duration_range": [3.0, 3.0],  # 3s
                "drift_range": [5.0, 5.0],
                "zone": zone,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run until scenario completes
        for _ in range(100):
            engine.tick()
            if sc.is_completed:
                break
        assert sc.is_completed

        # Record temperature right after completion
        temp_at_complete = press._dryer_temp_1._value

        # Run many more ticks to let the lag model recover.
        # With tau=120s and dt_per_generate=5s, alpha ≈ 0.04 per fire.
        # After ~600 ticks (60s), we get 12 generator fires.
        # The offset should reduce significantly.
        for _ in range(600):
            engine.tick()

        temp_after_recovery = press._dryer_temp_1._value

        # Temperature should be closer to setpoint than at completion
        drift_at_complete = abs(temp_at_complete - setpoint)
        drift_after_recovery = abs(temp_after_recovery - setpoint)
        assert drift_after_recovery < drift_at_complete


# ---------------------------------------------------------------------------
# Zone selection tests
# ---------------------------------------------------------------------------


class TestDryerDriftZoneSelection:
    """Verify zone selection logic."""

    def test_explicit_zone_1(self) -> None:
        rng = _make_rng()
        sc = DryerDrift(start_time=0.0, rng=rng, params={"zone": 1})
        assert sc.zone == 1

    def test_explicit_zone_2(self) -> None:
        rng = _make_rng()
        sc = DryerDrift(start_time=0.0, rng=rng, params={"zone": 2})
        assert sc.zone == 2

    def test_explicit_zone_3(self) -> None:
        rng = _make_rng()
        sc = DryerDrift(start_time=0.0, rng=rng, params={"zone": 3})
        assert sc.zone == 3

    def test_random_zone_within_range(self) -> None:
        """When zone is not specified, it should be 1, 2, or 3."""
        rng = _make_rng()
        sc = DryerDrift(start_time=0.0, rng=rng)
        assert sc.zone in (1, 2, 3)

    def test_zone_2_affects_correct_model(self) -> None:
        """Zone 2 should modify dryer_temp_zone_2, not zone_1 or zone_3."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        for z in (1, 2, 3):
            _stabilise_dryer(press, z)

        sp1 = press._dryer_temp_1.setpoint
        sp3 = press._dryer_temp_3.setpoint

        rng = _make_rng()
        sc = DryerDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_rate_range": [30.0, 30.0],
                "drift_duration_range": [60.0, 60.0],
                "drift_range": [10.0, 10.0],
                "zone": 2,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run 100 ticks (10s).  Zone 2 should drift; zones 1 and 3 should not.
        for _ in range(100):
            engine.tick()

        # Zone 2 model value should be elevated
        assert press._dryer_temp_2._value > press._dryer_temp_2.setpoint + 1.0

        # Zone 1 and 3 model values should be near their setpoints
        # (allow noise margin of +-3 C for the lag model at initial_value=20)
        # Since we stabilised them, they should be very close
        assert abs(press._dryer_temp_1._value - sp1) < 3.0
        assert abs(press._dryer_temp_3._value - sp3) < 3.0


# ---------------------------------------------------------------------------
# Parameter defaults
# ---------------------------------------------------------------------------


class TestDryerDriftDefaults:
    """Verify default parameter ranges match PRD."""

    def test_default_drift_duration_range(self) -> None:
        """Default drift duration: 30-120 min (1800-7200 s)."""
        rng = _make_rng()
        sc = DryerDrift(start_time=0.0, rng=rng)
        assert 1800 <= sc.drift_duration <= 7200

    def test_default_max_drift_range(self) -> None:
        """Default max drift: 5-15 C."""
        rng = _make_rng()
        sc = DryerDrift(start_time=0.0, rng=rng)
        assert 5.0 <= sc.max_drift <= 15.0

    def test_default_drift_rate_range(self) -> None:
        """Default drift rate: 0.05-0.2 C per minute."""
        rng = _make_rng()
        sc = DryerDrift(start_time=0.0, rng=rng)
        assert 0.05 <= sc.drift_rate <= 0.2

    def test_default_waste_increase_range(self) -> None:
        """Default waste increase: 20-50% (multiplier 1.2-1.5)."""
        rng = _make_rng()
        sc = DryerDrift(start_time=0.0, rng=rng)
        assert 1.2 <= sc.waste_multiplier <= 1.5

    def test_fixed_params_are_deterministic(self) -> None:
        """Fixed parameter ranges should produce exact values."""
        rng = _make_rng()
        sc = DryerDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_duration_range": [3000, 3000],
                "drift_range": [8.0, 8.0],
                "drift_rate_range": [0.1, 0.1],
                "waste_increase_range": [1.35, 1.35],
            },
        )
        assert sc.drift_duration == pytest.approx(3000.0)
        assert sc.max_drift == pytest.approx(8.0)
        assert sc.drift_rate == pytest.approx(0.1)
        assert sc.waste_multiplier == pytest.approx(1.35)
