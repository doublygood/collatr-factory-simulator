"""Tests for the seal integrity failure scenario (F&B sealer).

Verifies (PRD 5.14.4):
- sealer.seal_temp drops below minimum threshold (~170 C).
- sealer.seal_pressure decreases due to weakened seal bar.
- sealer.vacuum_level degrades (less vacuum as geometry allows gas leakage).
- qc.reject_total spikes as QC station detects failed seals.
- After scenario completes, all values are restored to original state.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.generators.checkweigher import CheckweigherGenerator
from factory_simulator.generators.sealer import SealerGenerator
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.seal_integrity import SealIntegrityFailure
from factory_simulator.store import SignalStore

_FNB_CONFIG = Path(__file__).resolve().parents[3] / "config" / "factory-foodbev.yaml"
_PKG_CONFIG = Path(__file__).resolve().parents[3] / "config" / "factory.yaml"


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


def _get_sealer(engine: DataEngine) -> SealerGenerator:
    """Find the sealer generator (raises if not found)."""
    for gen in engine.generators:
        if isinstance(gen, SealerGenerator):
            return gen
    raise RuntimeError("SealerGenerator not found — is F&B config loaded?")


def _get_qc(engine: DataEngine) -> CheckweigherGenerator:
    """Find the QC/checkweigher generator (raises if not found)."""
    for gen in engine.generators:
        if isinstance(gen, CheckweigherGenerator):
            return gen
    raise RuntimeError("CheckweigherGenerator not found — is F&B config loaded?")


def _run_ticks(engine: DataEngine, n: int) -> float:
    """Run n ticks and return the final sim_time."""
    t = 0.0
    for _ in range(n):
        t = engine.tick()
    return t


def _make_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


# Fast params: 2-second scenario with aggressive degradation
_FAST_PARAMS: dict[str, object] = {
    "duration_range": [2.0, 2.0],
    "temp_drop_range": [20.0, 20.0],
    "pressure_drop_fraction": [0.4, 0.4],
    "vacuum_fraction_lost": [0.5, 0.5],
    "extra_reject_rate": [120.0, 120.0],  # 2 rejects/s = 120/min
}


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestSealIntegrityLifecycle:
    """Scenario lifecycle: pending -> active -> completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = SealIntegrityFailure(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_at_start_time(self) -> None:
        engine, _store = _make_engine()
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = SealIntegrityFailure(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active

    def test_completes_after_duration(self) -> None:
        """Scenario completes once the duration has elapsed."""
        engine, _store = _make_engine()
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = SealIntegrityFailure(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):  # 5s — well past 2s duration
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

    def test_duration_method_returns_scenario_duration(self) -> None:
        rng = _make_rng()
        sc = SealIntegrityFailure(
            start_time=0.0,
            rng=rng,
            params={"duration_range": [900.0, 900.0]},
        )
        assert sc.duration() == pytest.approx(900.0)


# ---------------------------------------------------------------------------
# Seal temperature degradation tests
# ---------------------------------------------------------------------------


class TestSealTempDegradation:
    """PRD 5.14.4 step 1: seal_temp drops below minimum threshold."""

    def test_seal_temp_drops_below_original_during_scenario(self) -> None:
        """seal_temp_current must be below original value while active."""
        engine, _store = _make_engine()
        sealer = _get_sealer(engine)
        _run_ticks(engine, 50)  # warm up (5s, sealer fires at least once)

        original_temp = sealer._seal_temp_current

        rng = _make_rng()
        sc = SealIntegrityFailure(
            start_time=0.0,
            rng=rng,
            params={
                "duration_range": [60.0, 60.0],
                "temp_drop_range": [20.0, 20.0],
                "pressure_drop_fraction": [0.0, 0.0],
                "vacuum_fraction_lost": [0.0, 0.0],
                "extra_reject_rate": [0.0, 0.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Run past ramp duration (20% of 60s = 12s = 120 ticks)
        _run_ticks(engine, 130)

        assert sc.is_active
        # seal_temp_current should be ~original - 20 C
        assert sealer._seal_temp_current < original_temp - 15.0

    def test_seal_temp_drop_proportional_to_temp_drop_param(self) -> None:
        """Drop amount should match temp_drop_range after full ramp."""
        engine, _store = _make_engine()
        sealer = _get_sealer(engine)
        _run_ticks(engine, 50)

        original_temp = sealer._seal_temp_current
        target_drop = 25.0

        rng = _make_rng()
        sc = SealIntegrityFailure(
            start_time=0.0,
            rng=rng,
            params={
                "duration_range": [100.0, 100.0],
                "temp_drop_range": [target_drop, target_drop],
                "pressure_drop_fraction": [0.0, 0.0],
                "vacuum_fraction_lost": [0.0, 0.0],
                "extra_reject_rate": [0.0, 0.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Run past full ramp (20% of 100s = 20s = 200 ticks)
        _run_ticks(engine, 210)

        assert sc.is_active
        # At full ramp: seal_temp_current = original - target_drop
        assert abs(sealer._seal_temp_current - (original_temp - target_drop)) < 2.0


# ---------------------------------------------------------------------------
# Seal pressure degradation tests
# ---------------------------------------------------------------------------


class TestSealPressureDegradation:
    """PRD 5.14.4 step 2: seal_pressure decreases during scenario."""

    def test_pressure_target_drops_during_scenario(self) -> None:
        """seal_pressure model target must be below original during scenario."""
        engine, _store = _make_engine()
        sealer = _get_sealer(engine)
        _run_ticks(engine, 50)

        original_target = sealer._seal_pressure_model._target

        rng = _make_rng()
        sc = SealIntegrityFailure(
            start_time=0.0,
            rng=rng,
            params={
                "duration_range": [60.0, 60.0],
                "temp_drop_range": [0.0, 0.0],
                "pressure_drop_fraction": [0.3, 0.3],
                "vacuum_fraction_lost": [0.0, 0.0],
                "extra_reject_rate": [0.0, 0.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Run past full ramp
        _run_ticks(engine, 130)

        assert sc.is_active
        assert sealer._seal_pressure_model._target < original_target - 0.5


# ---------------------------------------------------------------------------
# Vacuum degradation tests
# ---------------------------------------------------------------------------


class TestVacuumDegradation:
    """PRD 5.14.4 step 3: vacuum_level degrades during scenario."""

    def test_vacuum_target_moves_toward_zero_during_scenario(self) -> None:
        """vacuum_level target must move toward 0 (less negative) during scenario."""
        engine, _store = _make_engine()
        sealer = _get_sealer(engine)
        _run_ticks(engine, 50)

        original_vacuum = sealer._vacuum_model._target
        assert original_vacuum < 0.0, "vacuum target should be negative"

        rng = _make_rng()
        sc = SealIntegrityFailure(
            start_time=0.0,
            rng=rng,
            params={
                "duration_range": [60.0, 60.0],
                "temp_drop_range": [0.0, 0.0],
                "pressure_drop_fraction": [0.0, 0.0],
                "vacuum_fraction_lost": [0.5, 0.5],
                "extra_reject_rate": [0.0, 0.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Run past full ramp
        _run_ticks(engine, 130)

        assert sc.is_active
        # Target should be closer to 0 than original (e.g., -0.35 vs -0.7)
        assert sealer._vacuum_model._target > original_vacuum + 0.1


# ---------------------------------------------------------------------------
# QC reject spike tests
# ---------------------------------------------------------------------------


class TestQcRejectSpike:
    """PRD 5.14.4 step 4: qc.reject_total spikes during scenario."""

    def test_reject_total_increases_during_scenario(self) -> None:
        """qc._reject_total must increase while scenario is active."""
        engine, _store = _make_engine()
        qc = _get_qc(engine)
        _run_ticks(engine, 50)

        initial_rejects = qc._reject_total

        rng = _make_rng()
        sc = SealIntegrityFailure(
            start_time=0.0,
            rng=rng,
            params={
                "duration_range": [10.0, 10.0],
                "temp_drop_range": [0.0, 0.0],
                "pressure_drop_fraction": [0.0, 0.0],
                "vacuum_fraction_lost": [0.0, 0.0],
                "extra_reject_rate": [60.0, 60.0],  # 1 reject/s = 60/min
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Run 5 seconds = 50 ticks; at 1 reject/s → ~5 extra rejects
        _run_ticks(engine, 50)

        assert sc.is_active
        assert qc._reject_total > initial_rejects + 3.0

    def test_no_extra_rejects_without_qc_generator(self) -> None:
        """Scenario handles missing QC generator gracefully (no crash)."""
        # Use packaging config (no checkweigher generator)
        config = load_config(_PKG_CONFIG, apply_env=False)
        config.simulation.tick_interval_ms = 100

        for cfg_name in [
            "job_changeover", "unplanned_stop", "shift_change", "web_break",
            "dryer_drift", "ink_viscosity_excursion", "registration_drift",
            "cold_start_spike", "coder_depletion", "material_splice",
        ]:
            obj = getattr(config.scenarios, cfg_name, None)
            if obj is not None:
                obj.enabled = False

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        rng = _make_rng()
        sc = SealIntegrityFailure(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        # Without sealer generator, scenario should complete immediately
        for _ in range(5):
            engine.tick()

        assert sc.is_completed


# ---------------------------------------------------------------------------
# Recovery tests
# ---------------------------------------------------------------------------


class TestSealIntegrityRecovery:
    """PRD 5.14.4 step 5: original state restored on scenario completion."""

    def test_seal_temp_restored_after_completion(self) -> None:
        """sealer._seal_temp_current must be restored to original on completion."""
        engine, _store = _make_engine()
        sealer = _get_sealer(engine)
        _run_ticks(engine, 50)

        original_temp = sealer._seal_temp_current

        rng = _make_rng()
        sc = SealIntegrityFailure(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert sealer._seal_temp_current == pytest.approx(original_temp, abs=1e-9)

    def test_pressure_target_restored_after_completion(self) -> None:
        """seal_pressure model target must be restored on completion."""
        engine, _store = _make_engine()
        sealer = _get_sealer(engine)
        _run_ticks(engine, 50)

        original_pressure_target = sealer._seal_pressure_model._target

        rng = _make_rng()
        sc = SealIntegrityFailure(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert sealer._seal_pressure_model._target == pytest.approx(
            original_pressure_target, abs=1e-9
        )

    def test_vacuum_target_restored_after_completion(self) -> None:
        """vacuum_level model target must be restored on completion."""
        engine, _store = _make_engine()
        sealer = _get_sealer(engine)
        _run_ticks(engine, 50)

        original_vacuum_target = sealer._vacuum_model._target

        rng = _make_rng()
        sc = SealIntegrityFailure(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert sealer._vacuum_model._target == pytest.approx(
            original_vacuum_target, abs=1e-9
        )

    def test_all_degradation_visible_then_recovered(self) -> None:
        """Degradation is measurable mid-scenario and restored at completion."""
        engine, _store = _make_engine()
        sealer = _get_sealer(engine)
        _run_ticks(engine, 50)

        original_temp = sealer._seal_temp_current
        original_pressure = sealer._seal_pressure_model._target
        original_vacuum = sealer._vacuum_model._target

        degradation_seen = False

        rng = _make_rng()
        sc = SealIntegrityFailure(
            start_time=0.0,
            rng=rng,
            params={
                "duration_range": [1.0, 1.0],
                "temp_drop_range": [20.0, 20.0],
                "pressure_drop_fraction": [0.4, 0.4],
                "vacuum_fraction_lost": [0.5, 0.5],
                "extra_reject_rate": [0.0, 0.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_active and (
                sealer._seal_temp_current < original_temp - 5.0
                and sealer._seal_pressure_model._target < original_pressure - 0.3
                and sealer._vacuum_model._target > original_vacuum + 0.1
            ):
                degradation_seen = True
            if sc.is_completed:
                break

        assert degradation_seen, "Degradation was never measurable during scenario"
        assert sealer._seal_temp_current == pytest.approx(original_temp, abs=1e-9)
        assert sealer._seal_pressure_model._target == pytest.approx(
            original_pressure, abs=1e-9
        )
        assert sealer._vacuum_model._target == pytest.approx(
            original_vacuum, abs=1e-9
        )


# ---------------------------------------------------------------------------
# Parameter default tests
# ---------------------------------------------------------------------------


class TestSealIntegrityDefaults:
    """Verify default parameter ranges match PRD 5.14.4."""

    def test_default_duration_range(self) -> None:
        """Default duration: 5-30 min (300-1800 s)."""
        rng = _make_rng()
        sc = SealIntegrityFailure(start_time=0.0, rng=rng)
        assert 300.0 <= sc.scenario_duration <= 1800.0

    def test_default_temp_drop_range(self) -> None:
        """Default temp drop: 15-30 C."""
        rng = _make_rng()
        sc = SealIntegrityFailure(start_time=0.0, rng=rng)
        assert 15.0 <= sc.temp_drop <= 30.0

    def test_default_pressure_drop_fraction_range(self) -> None:
        """Default pressure drop fraction: 0.2-0.5."""
        rng = _make_rng()
        sc = SealIntegrityFailure(start_time=0.0, rng=rng)
        assert 0.2 <= sc.pressure_drop_fraction <= 0.5

    def test_default_vacuum_fraction_lost_range(self) -> None:
        """Default vacuum fraction lost: 0.3-0.6."""
        rng = _make_rng()
        sc = SealIntegrityFailure(start_time=0.0, rng=rng)
        assert 0.3 <= sc.vacuum_fraction_lost <= 0.6

    def test_default_extra_reject_rate_range(self) -> None:
        """Default extra reject rate: 5-20 per minute."""
        rng = _make_rng()
        sc = SealIntegrityFailure(start_time=0.0, rng=rng)
        assert 5.0 <= sc.extra_reject_rate <= 20.0

    def test_fixed_params_deterministic(self) -> None:
        """Fixed params produce deterministic scenario state."""
        rng = _make_rng()
        sc = SealIntegrityFailure(
            start_time=0.0,
            rng=rng,
            params={
                "duration_range": [600.0, 600.0],
                "temp_drop_range": [20.0, 20.0],
                "pressure_drop_fraction": [0.3, 0.3],
                "vacuum_fraction_lost": [0.4, 0.4],
                "extra_reject_rate": [10.0, 10.0],
            },
        )
        assert sc.duration() == pytest.approx(600.0)
        assert sc.temp_drop == pytest.approx(20.0)
        assert sc.pressure_drop_fraction == pytest.approx(0.3)
        assert sc.vacuum_fraction_lost == pytest.approx(0.4)
        assert sc.extra_reject_rate == pytest.approx(10.0)
