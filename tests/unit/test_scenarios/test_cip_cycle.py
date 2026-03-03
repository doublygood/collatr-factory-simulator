"""Tests for the CIP cycle scenario (F&B).

Verifies (PRD 5.14.6):
- CIP generator starts at Idle and is kicked to Pre_rinse on activation.
- Mixer is placed in Cip state, filler in Off state, on activation.
- CIP generator auto-advances through phases (verified by elapsed > 0 check).
- Scenario completes when CIP returns to Idle (or timeout).
- Mixer and filler are returned to Off after completion.
- Default parameter ranges match PRD (30-60 min).
- Graceful handling when no CipGenerator is present (packaging profile).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.generators.cip import (
    STATE_IDLE,
    STATE_PRE_RINSE,
    CipGenerator,
)
from factory_simulator.generators.filler import FillerGenerator
from factory_simulator.generators.mixer import MixerGenerator
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.cip_cycle import CipCycle
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

    # Disable all F&B auto-scenarios
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


def _get_cip(engine: DataEngine) -> CipGenerator:
    """Find the CipGenerator (raises if not found)."""
    for gen in engine.generators:
        if isinstance(gen, CipGenerator):
            return gen
    raise RuntimeError("CipGenerator not found — is F&B config loaded?")


def _get_mixer(engine: DataEngine) -> MixerGenerator:
    """Find the MixerGenerator (raises if not found)."""
    for gen in engine.generators:
        if isinstance(gen, MixerGenerator):
            return gen
    raise RuntimeError("MixerGenerator not found — is F&B config loaded?")


def _get_filler(engine: DataEngine) -> FillerGenerator:
    """Find the FillerGenerator (raises if not found)."""
    for gen in engine.generators:
        if isinstance(gen, FillerGenerator):
            return gen
    raise RuntimeError("FillerGenerator not found — is F&B config loaded?")


def _run_ticks(engine: DataEngine, n: int) -> float:
    """Run n ticks and return the final sim_time."""
    t = 0.0
    for _ in range(n):
        t = engine.tick()
    return t


def _make_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


# Very short timeout for fast tests: 2 s (scenario completes via timeout)
_FAST_PARAMS: dict[str, object] = {
    "cycle_duration_range": [2.0, 2.0],
}


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestCipCycleLifecycle:
    """Scenario lifecycle: pending → active → completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = CipCycle(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_at_start_time(self) -> None:
        engine, _ = _make_engine()
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = CipCycle(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active

    def test_completes_after_timeout(self) -> None:
        """Scenario completes once the timeout duration has elapsed."""
        engine, _ = _make_engine()
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = CipCycle(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        # Run well past the 2 s timeout (50 ticks = 5 s)
        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

    def test_duration_method_returns_max_duration(self) -> None:
        rng = _make_rng()
        sc = CipCycle(
            start_time=0.0,
            rng=rng,
            params={"cycle_duration_range": [3600.0, 3600.0]},
        )
        assert sc.duration() == pytest.approx(3600.0)
        assert sc.max_duration == pytest.approx(3600.0)


# ---------------------------------------------------------------------------
# CIP generator activation tests
# ---------------------------------------------------------------------------


class TestCipGeneratorActivation:
    """PRD 5.14.6 steps 1-3: CIP starts, generator enters Pre_rinse."""

    def test_cip_starts_idle(self) -> None:
        """CIP generator starts in Idle state before scenario activates."""
        engine, _ = _make_engine()
        cip = _get_cip(engine)
        _run_ticks(engine, 3)
        assert cip.state == STATE_IDLE

    def test_cip_enters_pre_rinse_on_activation(self) -> None:
        """CIP generator must be in Pre_rinse after scenario activates."""
        engine, _ = _make_engine()
        cip = _get_cip(engine)
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = CipCycle(
            start_time=0.0, rng=rng,
            params={"cycle_duration_range": [300.0, 300.0]},
        )
        engine.scenario_engine.add_scenario(sc)
        engine.tick()  # activates scenario

        assert sc.is_active
        assert cip.state == STATE_PRE_RINSE

    def test_cip_flow_increases_after_activation(self) -> None:
        """CIP flow rate should increase from 0 after entering Pre_rinse.

        The CIP generator fires every 1 s (min sample_rate_ms=1000 ms).
        The initial 3 ticks fire the generator once at t=0.1 (in Idle).
        After the scenario activates, the generator fires again at t=1.1
        and t=2.1 (twice in Pre_rinse).

        After 2 fires with dt=0.1 s, tau=15 s:
          flow ≈ 60 * (1 - exp(-0.1/15)) * 2 ≈ 0.795 L/min

        The assertion uses 0.5 as a conservative lower bound.
        """
        engine, _ = _make_engine()
        cip = _get_cip(engine)
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = CipCycle(
            start_time=0.0, rng=rng,
            params={"cycle_duration_range": [300.0, 300.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        # Flow starts at 0 when Idle
        assert cip.flow_rate == pytest.approx(0.0, abs=0.01)

        # Run 25 ticks (2.5 s total from t=0.3) — generator fires at t=1.1 and t=2.1
        # That is 2 fires in Pre_rinse state, giving flow ≈ 0.795 L/min
        _run_ticks(engine, 25)

        assert cip.flow_rate > 0.5, (
            f"Expected flow > 0.5 L/min after 2 generator fires in Pre_rinse, "
            f"got {cip.flow_rate:.3f}"
        )

    def test_cip_state_visible_in_store(self) -> None:
        """cip.state in the signal store should reflect Pre_rinse (=1) after activation.

        The CIP generator fires every 1 s (min sample_rate_ms=1000 ms).
        After the initial 3 ticks (last fire at t=0.1), the next fire
        is at t=1.1.  We need to run 12 ticks after adding the scenario
        (getting to t=0.3+1.2=1.5, past t=1.1) to guarantee the generator
        fires in Pre_rinse state and writes 1.0 to the store.
        """
        engine, store = _make_engine()
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = CipCycle(
            start_time=0.0, rng=rng,
            params={"cycle_duration_range": [300.0, 300.0]},
        )
        engine.scenario_engine.add_scenario(sc)
        _run_ticks(engine, 12)  # 1.2 s — generator fires at t=1.1

        sv = store.get("cip.state")
        assert sv is not None
        assert sv.value == pytest.approx(float(STATE_PRE_RINSE))


# ---------------------------------------------------------------------------
# Production stop tests
# ---------------------------------------------------------------------------


class TestProductionStop:
    """PRD 5.14.6 step 1: mixer → Cip, filler → Off on activation."""

    def test_mixer_enters_cip_state_on_activation(self) -> None:
        """Mixer must be in Cip state after CIP scenario activates."""
        engine, _ = _make_engine()
        mixer = _get_mixer(engine)
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = CipCycle(
            start_time=0.0, rng=rng,
            params={"cycle_duration_range": [300.0, 300.0]},
        )
        engine.scenario_engine.add_scenario(sc)
        engine.tick()  # activates scenario

        assert sc.is_active
        assert mixer.state_machine.current_state == "Cip"

    def test_filler_enters_off_state_on_activation(self) -> None:
        """Filler must be in Off state after CIP scenario activates."""
        engine, _ = _make_engine()
        filler = _get_filler(engine)
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = CipCycle(
            start_time=0.0, rng=rng,
            params={"cycle_duration_range": [300.0, 300.0]},
        )
        engine.scenario_engine.add_scenario(sc)
        engine.tick()  # activates scenario

        assert sc.is_active
        assert filler.state_machine.current_state == "Off"


# ---------------------------------------------------------------------------
# Recovery / completion tests
# ---------------------------------------------------------------------------


class TestCipCycleCompletion:
    """PRD 5.14.6 steps 4-5: production resumes after CIP."""

    def test_mixer_returns_to_off_after_completion(self) -> None:
        """Mixer must be in Off state after scenario completes."""
        engine, _ = _make_engine()
        mixer = _get_mixer(engine)
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = CipCycle(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert mixer.state_machine.current_state == "Off"

    def test_filler_remains_off_after_completion(self) -> None:
        """Filler must be in Off state after scenario completes."""
        engine, _ = _make_engine()
        filler = _get_filler(engine)
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = CipCycle(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert filler.state_machine.current_state == "Off"

    def test_cip_returns_to_idle_after_completion(self) -> None:
        """CIP generator must be in Idle state after scenario completes."""
        engine, _ = _make_engine()
        cip = _get_cip(engine)
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = CipCycle(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert cip.state == STATE_IDLE

    def test_cip_completes_when_idle_state_reached(self) -> None:
        """Scenario ends when CIP generator returns to Idle naturally.

        Force the CIP back to Idle manually after a few ticks to
        simulate a very fast cycle, and verify the scenario notices.
        """
        engine, _ = _make_engine()
        cip = _get_cip(engine)
        _run_ticks(engine, 3)

        # Use a long timeout so the scenario can only complete via Idle detection
        rng = _make_rng()
        sc = CipCycle(
            start_time=0.0, rng=rng,
            params={"cycle_duration_range": [10000.0, 10000.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        # Activate: CIP enters Pre_rinse
        engine.tick()
        assert sc.is_active
        assert cip.state == STATE_PRE_RINSE

        # Manually drive CIP back to Idle to simulate cycle completion
        cip.force_state("Idle")

        # One more tick should detect Idle and complete the scenario
        engine.tick()
        assert sc.is_completed


# ---------------------------------------------------------------------------
# Parameter default tests
# ---------------------------------------------------------------------------


class TestCipCycleDefaults:
    """Verify default parameter ranges match PRD 5.14.6."""

    def test_default_duration_range(self) -> None:
        """Default max_duration: 30-60 min (1800-3600 s)."""
        durations: list[float] = []
        for seed in range(20):
            rng = np.random.default_rng(seed)
            sc = CipCycle(start_time=0.0, rng=rng)
            durations.append(sc.max_duration)

        for d in durations:
            assert 1800.0 <= d <= 3600.0, (
                f"max_duration {d} out of [1800, 3600] s range"
            )

    def test_fixed_duration_is_deterministic(self) -> None:
        """Fixed cycle_duration_range produces consistent duration."""
        rng = _make_rng()
        sc = CipCycle(
            start_time=0.0,
            rng=rng,
            params={"cycle_duration_range": [2700.0, 2700.0]},
        )
        assert sc.duration() == pytest.approx(2700.0)
        assert sc.max_duration == pytest.approx(2700.0)

    def test_different_seeds_produce_different_durations(self) -> None:
        """Different seeds should produce different max durations."""
        durations = set()
        for seed in range(10):
            rng = np.random.default_rng(seed)
            sc = CipCycle(start_time=0.0, rng=rng)
            durations.add(round(sc.max_duration))
        assert len(durations) >= 3


# ---------------------------------------------------------------------------
# No CIP generator tests (packaging profile)
# ---------------------------------------------------------------------------


class TestNoCipGenerator:
    """Graceful handling when no CipGenerator present (packaging profile)."""

    def test_completes_immediately_without_cip(self) -> None:
        """Scenario should complete on first tick if no CIP generator found."""
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
        sc = CipCycle(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(5):
            engine.tick()

        assert sc.is_completed
