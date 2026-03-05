"""Tests for the oven thermal excursion scenario (F&B).

Verifies (PRD 5.14.2):
- One oven zone drifts above its setpoint.
- Drift rate: 0.1-0.3 C per minute.
- Max drift: 3-10 C above setpoint.
- Adjacent zones respond via thermal coupling (handled by oven generator).
- After drift duration, temperature recovers via natural lag.
- Drift does not trigger a fault state.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.generators.oven import OvenGenerator
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.oven_thermal_excursion import OvenThermalExcursion
from factory_simulator.store import SignalStore

_FNB_CONFIG = Path(__file__).resolve().parents[3] / "config" / "factory-foodbev.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(seed: int = 42) -> tuple[DataEngine, SignalStore]:
    """Create a DataEngine from the F&B config with all auto-scenarios disabled."""
    config = load_config(_FNB_CONFIG, apply_env=False)
    config.simulation.random_seed = seed
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = 1.0

    # Disable packaging scenarios
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

    # Disable F&B auto-scenarios
    if config.scenarios.batch_cycle is not None:
        config.scenarios.batch_cycle.enabled = False
    if config.scenarios.oven_thermal_excursion is not None:
        config.scenarios.oven_thermal_excursion.enabled = False
    if config.scenarios.fill_weight_drift is not None:
        config.scenarios.fill_weight_drift.enabled = False
    if config.scenarios.seal_integrity_failure is not None:
        config.scenarios.seal_integrity_failure.enabled = False
    if config.scenarios.chiller_door_alarm is not None:
        config.scenarios.chiller_door_alarm.enabled = False
    if config.scenarios.cip_cycle is not None:
        config.scenarios.cip_cycle.enabled = False
    if config.scenarios.cold_chain_break is not None:
        config.scenarios.cold_chain_break.enabled = False

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    return engine, store


def _get_oven(engine: DataEngine) -> OvenGenerator:
    """Find the oven generator (raises if not found)."""
    for gen in engine.generators:
        if isinstance(gen, OvenGenerator):
            return gen
    raise RuntimeError("OvenGenerator not found — is F&B config loaded?")


def _run_ticks(engine: DataEngine, n: int) -> float:
    """Run n ticks and return the final sim_time."""
    t = 0.0
    for _ in range(n):
        t = engine.tick()
    return t


def _make_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def _stabilise_zone(oven: OvenGenerator, zone: int) -> None:
    """Force the zone temperature model's internal value to its current setpoint.

    This avoids waiting hundreds of ticks for the lag model to converge
    from ambient (20 C) to the oven setpoint (160-200 C).
    """
    model = oven.zone_temp_models[zone - 1]
    model._value = model.setpoint
    oven._prev_zone_temps[zone - 1] = model.setpoint


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestOvenThermalExcursionLifecycle:
    """Scenario lifecycle: pending -> active -> completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = OvenThermalExcursion(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_at_start_time(self) -> None:
        engine, _store = _make_engine()
        oven = _get_oven(engine)
        oven.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = OvenThermalExcursion(start_time=0.0, rng=rng)
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active

    def test_completes_after_drift_duration(self) -> None:
        """Scenario completes once drift_duration has elapsed."""
        engine, _store = _make_engine()
        oven = _get_oven(engine)
        oven.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = OvenThermalExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "drift_duration_range": [2.0, 2.0],  # 2 seconds for fast test
                "zone": 1,
            },
        )
        _stabilise_zone(oven, 1)

        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):  # 5s of sim time
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

    def test_duration_method(self) -> None:
        rng = _make_rng()
        sc = OvenThermalExcursion(
            start_time=0.0,
            rng=rng,
            params={"drift_duration_range": [3600, 3600]},
        )
        assert sc.duration() == pytest.approx(3600.0)


# ---------------------------------------------------------------------------
# Temperature drift tests
# ---------------------------------------------------------------------------


class TestOvenThermalExcursionTemperature:
    """PRD 5.14.2 steps 1-3: temperature drifts above setpoint."""

    def test_temperature_increases_above_setpoint(self) -> None:
        """Oven zone temperature must exceed setpoint during drift."""
        engine, store = _make_engine()
        oven = _get_oven(engine)
        oven.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        zone = 2
        _stabilise_zone(oven, zone)
        setpoint = oven.zone_temp_models[zone - 1].setpoint

        rng = _make_rng()
        sc = OvenThermalExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "drift_rate_range": [60.0, 60.0],  # 60 C/min — fast for test
                "drift_duration_range": [60.0, 60.0],
                "drift_range": [30.0, 30.0],
                "zone": zone,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run 100 ticks (10s sim time). At 60 C/min, drift is ~10 C.
        for _ in range(100):
            engine.tick()

        assert sc.is_active

        # Check model internal value (not store, which includes noise)
        zone_model_value = oven.zone_temp_models[zone - 1]._value
        assert zone_model_value > setpoint + 2.0

        # Also verify the store value shows the drift
        zone_temp = store.get_value(f"oven.zone_{zone}_temp")
        assert isinstance(zone_temp, float)
        assert zone_temp > setpoint + 1.0

    def test_drift_rate_produces_expected_offset(self) -> None:
        """Drift offset should match drift_rate * elapsed / 60."""
        engine, store = _make_engine()
        oven = _get_oven(engine)
        oven.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        zone = 1
        _stabilise_zone(oven, zone)
        setpoint = oven.zone_temp_models[zone - 1].setpoint

        rng = _make_rng()
        drift_rate = 12.0  # 12 C/min for clear signal
        sc = OvenThermalExcursion(
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

        # Run 300 ticks (30s sim time). At 12 C/min, expected drift = 6 C.
        for _ in range(300):
            engine.tick()

        assert sc.is_active
        zone_temp = store.get_value(f"oven.zone_{zone}_temp")
        assert isinstance(zone_temp, float)

        expected_drift = drift_rate * 30.0 / 60.0  # 6.0 C
        actual_drift = zone_temp - setpoint

        # Allow +-3 C tolerance for noise and lag correction at oven scale
        assert abs(actual_drift - expected_drift) < 3.0

    def test_drift_capped_at_max_drift(self) -> None:
        """Drift should not exceed max_drift."""
        engine, _store = _make_engine()
        oven = _get_oven(engine)
        oven.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        zone = 3
        _stabilise_zone(oven, zone)
        setpoint = oven.zone_temp_models[zone - 1].setpoint

        max_drift = 5.0
        rng = _make_rng()
        sc = OvenThermalExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "drift_rate_range": [60.0, 60.0],  # Very fast: 60 C/min
                "drift_duration_range": [120.0, 120.0],
                "drift_range": [max_drift, max_drift],
                "zone": zone,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run 300 ticks (30s). At 60 C/min, drift would be 30 C without cap.
        for _ in range(300):
            engine.tick()

        zone_model = oven.zone_temp_models[zone - 1]
        # The model value should be near setpoint + max_drift
        # Allow for lag correction reducing it slightly
        assert zone_model._value <= setpoint + max_drift + 1.0

    def test_no_fault_state_during_drift(self) -> None:
        """PRD 5.14.2: drift does not trigger a fault state."""
        engine, _store = _make_engine()
        oven = _get_oven(engine)
        oven.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        _stabilise_zone(oven, 2)

        rng = _make_rng()
        sc = OvenThermalExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "drift_rate_range": [6.0, 6.0],
                "drift_duration_range": [30.0, 30.0],
                "zone": 2,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(300):
            engine.tick()

        # Oven should remain in Running state (no fault)
        assert oven.state_machine.current_state == "Running"

    def test_setpoint_signal_unchanged_during_drift(self) -> None:
        """The setpoint signal must stay constant; only actual temp drifts."""
        engine, store = _make_engine()
        oven = _get_oven(engine)
        oven.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        zone = 1
        _stabilise_zone(oven, zone)

        # Record initial setpoint from the store
        initial_sp = store.get_value(f"oven.zone_{zone}_setpoint")

        rng = _make_rng()
        sc = OvenThermalExcursion(
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

        # Setpoint signal must be unchanged (scenario only moves PV, not SP)
        final_sp = store.get_value(f"oven.zone_{zone}_setpoint")
        assert final_sp == pytest.approx(initial_sp, abs=0.1)

    def test_zone_2_affects_only_target_zone_model(self) -> None:
        """Zone 2 drift should elevate zone_2_temp model; zones 1 & 3 minimal."""
        engine, _store = _make_engine()
        oven = _get_oven(engine)
        oven.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        # Stabilise all zones
        for z in (1, 2, 3):
            _stabilise_zone(oven, z)

        sp1 = oven.zone_temp_models[0].setpoint
        sp3 = oven.zone_temp_models[2].setpoint

        rng = _make_rng()
        sc = OvenThermalExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "drift_rate_range": [60.0, 60.0],
                "drift_duration_range": [60.0, 60.0],
                "drift_range": [20.0, 20.0],
                "zone": 2,
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run 100 ticks (10s). Zone 2 should drift; zones 1 and 3 may show
        # slight coupling effect but should not be forcibly elevated.
        for _ in range(100):
            engine.tick()

        # Zone 2 model value should be elevated well above its setpoint
        assert oven.zone_temp_models[1]._value > oven.zone_temp_models[1].setpoint + 5.0

        # Zone 1 and 3 model values should be near their setpoints
        # (allow a small coupling margin of +-3 C)
        assert abs(oven.zone_temp_models[0]._value - sp1) < 3.0
        assert abs(oven.zone_temp_models[2]._value - sp3) < 3.0


# ---------------------------------------------------------------------------
# Recovery tests
# ---------------------------------------------------------------------------


class TestOvenThermalExcursionRecovery:
    """PRD 5.14.2 step 4: temperature returns to setpoint after drift."""

    def test_temperature_recovers_toward_setpoint(self) -> None:
        """After completion, the lag model should pull temp back toward sp."""
        engine, _store = _make_engine()
        oven = _get_oven(engine)
        oven.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        zone = 1
        _stabilise_zone(oven, zone)
        setpoint = oven.zone_temp_models[zone - 1].setpoint

        rng = _make_rng()
        sc = OvenThermalExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "drift_rate_range": [60.0, 60.0],  # Fast drift
                "drift_duration_range": [3.0, 3.0],  # 3s
                "drift_range": [8.0, 8.0],
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
        temp_at_complete = oven.zone_temp_models[zone - 1]._value

        # Run many more ticks to let the lag model recover
        for _ in range(600):
            engine.tick()

        temp_after_recovery = oven.zone_temp_models[zone - 1]._value

        # Temperature should be closer to setpoint than at completion
        drift_at_complete = abs(temp_at_complete - setpoint)
        drift_after_recovery = abs(temp_after_recovery - setpoint)
        assert drift_after_recovery < drift_at_complete


# ---------------------------------------------------------------------------
# Zone selection tests
# ---------------------------------------------------------------------------


class TestOvenThermalExcursionZoneSelection:
    """Verify zone selection logic."""

    def test_explicit_zone_1(self) -> None:
        rng = _make_rng()
        sc = OvenThermalExcursion(start_time=0.0, rng=rng, params={"zone": 1})
        assert sc.zone == 1

    def test_explicit_zone_2(self) -> None:
        rng = _make_rng()
        sc = OvenThermalExcursion(start_time=0.0, rng=rng, params={"zone": 2})
        assert sc.zone == 2

    def test_explicit_zone_3(self) -> None:
        rng = _make_rng()
        sc = OvenThermalExcursion(start_time=0.0, rng=rng, params={"zone": 3})
        assert sc.zone == 3

    def test_random_zone_within_range(self) -> None:
        """When zone is not specified, it should be 1, 2, or 3."""
        rng = _make_rng()
        sc = OvenThermalExcursion(start_time=0.0, rng=rng)
        assert sc.zone in (1, 2, 3)


# ---------------------------------------------------------------------------
# Parameter defaults
# ---------------------------------------------------------------------------


class TestOvenThermalExcursionDefaults:
    """Verify default parameter ranges match PRD 5.14.2."""

    def test_default_drift_duration_range(self) -> None:
        """Default drift duration: 30-90 min (1800-5400 s)."""
        rng = _make_rng()
        sc = OvenThermalExcursion(start_time=0.0, rng=rng)
        assert 1800 <= sc.drift_duration <= 5400

    def test_default_max_drift_range(self) -> None:
        """Default max drift: 3-10 C."""
        rng = _make_rng()
        sc = OvenThermalExcursion(start_time=0.0, rng=rng)
        assert 3.0 <= sc.max_drift <= 10.0

    def test_default_drift_rate_range(self) -> None:
        """Default drift rate: 0.1-0.3 C per minute."""
        rng = _make_rng()
        sc = OvenThermalExcursion(start_time=0.0, rng=rng)
        assert 0.1 <= sc.drift_rate <= 0.3

    def test_fixed_params_are_deterministic(self) -> None:
        """Fixed parameter ranges should produce exact values."""
        rng = _make_rng()
        sc = OvenThermalExcursion(
            start_time=0.0,
            rng=rng,
            params={
                "drift_duration_range": [3000, 3000],
                "drift_range": [7.0, 7.0],
                "drift_rate_range": [0.2, 0.2],
            },
        )
        assert sc.drift_duration == pytest.approx(3000.0)
        assert sc.max_drift == pytest.approx(7.0)
        assert sc.drift_rate == pytest.approx(0.2)
