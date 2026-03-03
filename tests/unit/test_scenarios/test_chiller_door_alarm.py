"""Tests for the chiller door alarm scenario (F&B cold room).

Verifies (PRD 5.14.5):
- chiller.door_open is set to True on activation.
- chiller.room_temp rises faster while door is open.
- chiller.door_open returns to False after scenario completes.
- Room temperature recovery handled naturally by generator bang-bang.
- Default parameter ranges match PRD (5-20 min).
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
from factory_simulator.scenarios.chiller_door_alarm import ChillerDoorAlarm
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


class TestChillerDoorAlarmLifecycle:
    """Scenario lifecycle: pending -> active -> completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = ChillerDoorAlarm(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_at_start_time(self) -> None:
        engine, store = _make_engine()
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = ChillerDoorAlarm(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active

    def test_completes_after_duration(self) -> None:
        """Scenario completes once the duration has elapsed."""
        engine, store = _make_engine()
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = ChillerDoorAlarm(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):  # 5s — well past 2s duration
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

    def test_duration_method_returns_scenario_duration(self) -> None:
        rng = _make_rng()
        sc = ChillerDoorAlarm(
            start_time=0.0,
            rng=rng,
            params={"duration_range": [900.0, 900.0]},
        )
        assert sc.duration() == pytest.approx(900.0)
        assert sc.scenario_duration == pytest.approx(900.0)


# ---------------------------------------------------------------------------
# Door open effect tests
# ---------------------------------------------------------------------------


class TestDoorOpenEffect:
    """PRD 5.14.5 steps 1-2: door_open sets to True, room_temp rises."""

    def test_door_open_is_true_on_activation(self) -> None:
        """chiller.door_open must be True while scenario is active."""
        engine, store = _make_engine()
        chiller = _get_chiller(engine)
        _run_ticks(engine, 3)

        # Verify door starts closed
        assert not chiller.door_open

        rng = _make_rng()
        sc = ChillerDoorAlarm(
            start_time=0.0, rng=rng,
            params={"duration_range": [60.0, 60.0]},
        )
        engine.scenario_engine.add_scenario(sc)
        engine.tick()  # activates scenario

        assert sc.is_active
        assert chiller.door_open

    def test_room_temp_rises_faster_with_door_open(self) -> None:
        """room_temp should rise more with door open than without.

        The chiller generator fires every 1 s (min sample_rate_ms=1000) and
        receives dt=0.1 s per fire.  Door-open heat rate is 1.5 C/min;
        background heat rate is 0.2 C/min.  In 30 s (30 generator fires):
          - baseline rise ~0.2/60 * 0.1 * 30 ~0.010 C
          - door rise    ~(0.2+1.5)/60 * 0.1 * 30 ~0.085 C
        The door case must be significantly larger than baseline.
        """
        # Baseline: no scenario, measure temp change over 30s
        engine_base, _ = _make_engine(seed=10)
        chiller_base = _get_chiller(engine_base)
        # Force compressor off to isolate heat gain
        chiller_base.compressor_forced_off = True
        _run_ticks(engine_base, 3)
        temp_start_base = chiller_base.room_temp
        _run_ticks(engine_base, 300)  # 30s
        temp_rise_base = chiller_base.room_temp - temp_start_base

        # With door open scenario
        engine_door, _ = _make_engine(seed=10)
        chiller_door = _get_chiller(engine_door)
        chiller_door.compressor_forced_off = True
        _run_ticks(engine_door, 3)

        rng = _make_rng()
        sc = ChillerDoorAlarm(
            start_time=0.0, rng=rng,
            params={"duration_range": [60.0, 60.0]},
        )
        engine_door.scenario_engine.add_scenario(sc)
        temp_start_door = chiller_door.room_temp
        _run_ticks(engine_door, 300)  # 30s
        temp_rise_door = chiller_door.room_temp - temp_start_door

        # Door adds ~7.5x background heat: door case rises significantly faster.
        # With 30 fires x 0.1 s dt, expected extra rise ~0.075 C.
        assert temp_rise_door > temp_rise_base + 0.04
        assert temp_rise_door > temp_rise_base * 4.0

    def test_door_open_state_visible_in_store(self) -> None:
        """chiller.door_open in store should reflect 1.0 while active.

        The chiller generator fires every 1 s (sample_rate_ms=1000).
        Run 12 ticks (1.2 s) after adding the scenario to ensure the
        generator fires at least once after door_open is set to True.
        """
        engine, store = _make_engine()
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = ChillerDoorAlarm(
            start_time=0.0, rng=rng,
            params={"duration_range": [60.0, 60.0]},
        )
        engine.scenario_engine.add_scenario(sc)
        _run_ticks(engine, 12)  # 1.2 s — generator fires at least once after activation

        # Generator should write door_open = 1.0 to store
        sv = store.get("chiller.door_open")
        assert sv is not None
        assert sv.value == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Recovery tests
# ---------------------------------------------------------------------------


class TestDoorAlarmRecovery:
    """PRD 5.14.5 step 4: door closes, temperature recovers."""

    def test_door_closed_after_completion(self) -> None:
        """chiller.door_open must be False after scenario completes."""
        engine, store = _make_engine()
        chiller = _get_chiller(engine)
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = ChillerDoorAlarm(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert not chiller.door_open

    def test_door_open_false_in_store_after_completion(self) -> None:
        """chiller.door_open in store should be 0.0 after scenario ends.

        The chiller generator fires every 1 s.  After scenario completes
        (sets door_open=False), run 12 more ticks (1.2 s) to guarantee the
        generator fires and writes 0.0 to the store.
        """
        engine, store = _make_engine()
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = ChillerDoorAlarm(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        # Run 12 more ticks so generator fires after door is closed
        _run_ticks(engine, 12)
        sv = store.get("chiller.door_open")
        assert sv is not None
        assert sv.value == pytest.approx(0.0)

    def test_scenario_does_not_permanently_open_door(self) -> None:
        """Door must be closed when scenario completes — generator recovery begins."""
        engine, store = _make_engine()
        chiller = _get_chiller(engine)
        _run_ticks(engine, 5)

        initial_temp = chiller.room_temp

        rng = _make_rng()
        sc = ChillerDoorAlarm(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        # Run through scenario
        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert not chiller.door_open

        # After door closes, compressor can now cool; run more ticks
        _run_ticks(engine, 3000)  # 5 min — enough for bang-bang to respond
        # Temperature should be back in the normal operating band
        assert chiller.room_temp < initial_temp + 3.0


# ---------------------------------------------------------------------------
# Parameter default tests
# ---------------------------------------------------------------------------


class TestChillerDoorAlarmDefaults:
    """Verify default parameter ranges match PRD 5.14.5."""

    def test_default_duration_range(self) -> None:
        """Default duration: 5-20 min (300-1200 s)."""
        durations: list[float] = []
        for seed in range(20):
            rng = np.random.default_rng(seed)
            sc = ChillerDoorAlarm(start_time=0.0, rng=rng)
            durations.append(sc.scenario_duration)

        for d in durations:
            assert 300.0 <= d <= 1200.0, f"Duration {d} out of [300, 1200] s range"

    def test_fixed_duration_is_deterministic(self) -> None:
        """Fixed duration_range produces consistent duration."""
        rng = _make_rng()
        sc = ChillerDoorAlarm(
            start_time=0.0,
            rng=rng,
            params={"duration_range": [600.0, 600.0]},
        )
        assert sc.duration() == pytest.approx(600.0)

    def test_different_seeds_produce_different_durations(self) -> None:
        """Different seeds should produce different durations."""
        durations = set()
        for seed in range(10):
            rng = np.random.default_rng(seed)
            sc = ChillerDoorAlarm(start_time=0.0, rng=rng)
            # Round to nearest second for uniqueness check
            durations.add(round(sc.scenario_duration))
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
        sc = ChillerDoorAlarm(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        # Without chiller generator, scenario should complete immediately
        for _ in range(5):
            engine.tick()

        assert sc.is_completed
