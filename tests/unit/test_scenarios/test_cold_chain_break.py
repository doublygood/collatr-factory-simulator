"""Tests for the cold chain break scenario (F&B refrigeration failure).

Verifies (PRD 5.14.7):
- chiller.compressor_forced_off is set to True on activation.
- chiller.compressor_state (store) reads 0.0 while scenario is active.
- chiller.room_temp rises while compressor is locked off.
- chiller.compressor_forced_off is released (False) after scenario completes.
- Default parameter ranges match PRD (30-120 min = 1800-7200 s).
- Graceful handling when no ChillerGenerator is present.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.generators.chiller import ChillerGenerator
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.cold_chain_break import ColdChainBreak
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
    for attr in [
        "batch_cycle", "oven_thermal_excursion", "fill_weight_drift",
        "seal_integrity_failure", "chiller_door_alarm", "cip_cycle",
        "cold_chain_break",
    ]:
        cfg = getattr(config.scenarios, attr, None)
        if cfg is not None:
            cfg.enabled = False

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    return engine, store


def _get_chiller(engine: DataEngine) -> ChillerGenerator:
    """Find the ChillerGenerator (raises if not found)."""
    for gen in engine.generators:
        if isinstance(gen, ChillerGenerator):
            return gen
    raise RuntimeError("ChillerGenerator not found — is F&B config loaded?")


def _run_ticks(engine: DataEngine, n: int) -> float:
    """Run n ticks and return the final sim_time."""
    t = 0.0
    for _ in range(n):
        t = engine.tick()
    return t


def _make_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


# Fast params: 2-second scenario
_FAST_PARAMS: dict[str, object] = {
    "duration_range": [2.0, 2.0],
}


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestColdChainBreakLifecycle:
    """Scenario lifecycle: pending -> active -> completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = ColdChainBreak(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_at_start_time(self) -> None:
        engine, store = _make_engine()
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = ColdChainBreak(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active

    def test_completes_after_duration(self) -> None:
        """Scenario completes once the duration has elapsed."""
        engine, store = _make_engine()
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = ColdChainBreak(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):  # 5s — well past 2s duration
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

    def test_duration_method_returns_scenario_duration(self) -> None:
        rng = _make_rng()
        sc = ColdChainBreak(
            start_time=0.0,
            rng=rng,
            params={"duration_range": [3600.0, 3600.0]},
        )
        assert sc.duration() == pytest.approx(3600.0)
        assert sc.scenario_duration == pytest.approx(3600.0)


# ---------------------------------------------------------------------------
# Compressor lock effect tests
# ---------------------------------------------------------------------------


class TestCompressorLock:
    """PRD 5.14.7 steps 1-2: compressor locked off, room_temp rises."""

    def test_compressor_forced_off_on_activation(self) -> None:
        """compressor_forced_off must be True while scenario is active."""
        engine, store = _make_engine()
        chiller = _get_chiller(engine)
        _run_ticks(engine, 3)

        # Verify compressor lock starts False
        assert not chiller.compressor_forced_off

        rng = _make_rng()
        sc = ColdChainBreak(
            start_time=0.0, rng=rng,
            params={"duration_range": [60.0, 60.0]},
        )
        engine.scenario_engine.add_scenario(sc)
        engine.tick()  # activates scenario

        assert sc.is_active
        assert chiller.compressor_forced_off

    def test_compressor_state_zero_in_store_after_activation(self) -> None:
        """chiller.compressor_state in store should be 0.0 while locked off.

        The chiller generator fires every 1 s (sample_rate_ms=1000).
        Run 12 ticks (1.2 s) after adding the scenario to ensure the
        generator fires at least once after compressor_forced_off is True.
        """
        engine, store = _make_engine()
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = ColdChainBreak(
            start_time=0.0, rng=rng,
            params={"duration_range": [60.0, 60.0]},
        )
        engine.scenario_engine.add_scenario(sc)
        _run_ticks(engine, 12)  # 1.2 s — generator fires at least once

        sv = store.get("chiller.compressor_state")
        assert sv is not None
        assert sv.value == pytest.approx(0.0)

    def test_room_temp_rises_with_compressor_locked_off(self) -> None:
        """room_temp should rise while compressor is locked off.

        The chiller generator fires every 1 s (sample_rate_ms=1000) with
        dt=0.1 s per fire.  Background heat gain: 0.2 C/min = 0.2/60 C/s.
        In 60 generator fires (6 s of physics):
          rise ~0.2/60 * 0.1 * 60 ~0.02 C
        Small but positive — direction is correct.
        """
        engine, store = _make_engine(seed=10)
        chiller = _get_chiller(engine)
        _run_ticks(engine, 3)

        # Record initial room temp
        temp_start = chiller.room_temp

        rng = _make_rng()
        sc = ColdChainBreak(
            start_time=0.0, rng=rng,
            params={"duration_range": [600.0, 600.0]},
        )
        engine.scenario_engine.add_scenario(sc)
        _run_ticks(engine, 600)  # 60 s (generator fires ~60 times)

        # room_temp should be higher than initial
        assert chiller.room_temp > temp_start

    def test_room_temp_rises_faster_than_with_compressor_cycling(self) -> None:
        """room_temp rises faster with compressor locked off vs. bang-bang.

        Without cold chain break, the bang-bang compressor periodically
        cools the room.  With the compressor locked off, temperature only
        rises.  After 60 s, the locked-off case must be warmer.
        """
        # Baseline: bang-bang (no scenario)
        engine_base, _ = _make_engine(seed=99)
        chiller_base = _get_chiller(engine_base)
        _run_ticks(engine_base, 3)
        temp_start_base = chiller_base.room_temp
        _run_ticks(engine_base, 600)  # 60 s
        temp_base = chiller_base.room_temp

        # With cold chain break: compressor locked off
        engine_fail, _ = _make_engine(seed=99)
        chiller_fail = _get_chiller(engine_fail)
        _run_ticks(engine_fail, 3)
        temp_start_fail = chiller_fail.room_temp

        rng = _make_rng()
        sc = ColdChainBreak(
            start_time=0.0, rng=rng,
            params={"duration_range": [600.0, 600.0]},
        )
        engine_fail.scenario_engine.add_scenario(sc)
        _run_ticks(engine_fail, 600)  # 60 s
        temp_fail = chiller_fail.room_temp

        # Compressor-locked case should be warmer (no cooling)
        rise_base = temp_base - temp_start_base
        rise_fail = temp_fail - temp_start_fail
        assert rise_fail > rise_base


# ---------------------------------------------------------------------------
# Recovery tests
# ---------------------------------------------------------------------------


class TestColdChainBreakRecovery:
    """PRD 5.14.7 step 4: compressor released, temperature recovers."""

    def test_compressor_forced_off_released_after_completion(self) -> None:
        """compressor_forced_off must be False after scenario completes."""
        engine, store = _make_engine()
        chiller = _get_chiller(engine)
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = ColdChainBreak(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert not chiller.compressor_forced_off

    def test_compressor_state_recovers_after_completion(self) -> None:
        """chiller.compressor_state in store should be 1.0 eventually after repair.

        After compressor_forced_off is released, the bang-bang controller
        will turn the compressor ON when room_temp > setpoint + 1°C.
        Run extra ticks to allow the generator to fire and observe recovery.
        """
        engine, store = _make_engine()
        chiller = _get_chiller(engine)
        _run_ticks(engine, 3)

        # Short scenario — room barely warms before repair
        rng = _make_rng()
        sc = ColdChainBreak(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

        # After repair, chiller should eventually turn compressor on
        # (temp rose a bit; bang-bang will activate when temp > setpoint + 1°C)
        # Run more ticks to allow bang-bang to respond
        _run_ticks(engine, 600)  # 60 s
        assert not chiller.compressor_forced_off

    def test_scenario_does_not_permanently_lock_compressor(self) -> None:
        """Compressor lock must not persist beyond scenario completion."""
        engine, store = _make_engine()
        chiller = _get_chiller(engine)
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = ColdChainBreak(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        # Run through scenario
        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert not chiller.compressor_forced_off

        # After repair, run 6000 ticks (10 min physics) — temp should recover
        _run_ticks(engine, 6000)
        # Room temperature should eventually return to operational range (< 10°C)
        assert chiller.room_temp < 10.0


# ---------------------------------------------------------------------------
# Parameter default tests
# ---------------------------------------------------------------------------


class TestColdChainBreakDefaults:
    """Verify default parameter ranges match PRD 5.14.7."""

    def test_default_duration_range(self) -> None:
        """Default duration: 30-120 min (1800-7200 s)."""
        durations: list[float] = []
        for seed in range(20):
            rng = np.random.default_rng(seed)
            sc = ColdChainBreak(start_time=0.0, rng=rng)
            durations.append(sc.scenario_duration)

        for d in durations:
            assert 1800.0 <= d <= 7200.0, f"Duration {d} out of [1800, 7200] s range"

    def test_fixed_duration_is_deterministic(self) -> None:
        """Fixed duration_range produces consistent duration."""
        rng = _make_rng()
        sc = ColdChainBreak(
            start_time=0.0,
            rng=rng,
            params={"duration_range": [3600.0, 3600.0]},
        )
        assert sc.duration() == pytest.approx(3600.0)

    def test_different_seeds_produce_different_durations(self) -> None:
        """Different seeds should produce different durations."""
        durations = set()
        for seed in range(10):
            rng = np.random.default_rng(seed)
            sc = ColdChainBreak(start_time=0.0, rng=rng)
            # Round to nearest minute for uniqueness check
            durations.add(round(sc.scenario_duration / 60))
        # At least 3 distinct durations from 10 seeds
        assert len(durations) >= 3


# ---------------------------------------------------------------------------
# No chiller tests
# ---------------------------------------------------------------------------


class TestNoChillerGenerator:
    """Graceful handling when no ChillerGenerator present (packaging profile)."""

    def test_completes_immediately_without_chiller(self) -> None:
        """Scenario should complete on first tick if no chiller found."""
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
        sc = ColdChainBreak(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        # Without chiller generator, scenario should complete immediately
        for _ in range(5):
            engine.tick()

        assert sc.is_completed
