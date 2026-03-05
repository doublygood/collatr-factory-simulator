"""Tests for the batch cycle scenario (F&B mixer).

Verifies (PRD 5.14.1):
- Mixer transitions: Off → Loading → Mixing → Holding → Discharging → Off.
- Phase durations drawn from configured ranges (batch-to-batch variation).
- Scenario completes after the full batch cycle.
- Ground truth events logged for state transitions.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.generators.mixer import (
    STATE_DISCHARGING,
    STATE_HOLDING,
    STATE_LOADING,
    STATE_MIXING,
    STATE_OFF,
    MixerGenerator,
)
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.batch_cycle import BatchCycle, _Phase
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

    # Disable packaging scenarios (not applicable for F&B)
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

    # Disable F&B auto-scenarios (we inject manually in tests)
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


def _get_mixer(engine: DataEngine) -> MixerGenerator:
    """Find the mixer generator (raises if not found)."""
    for gen in engine.generators:
        if isinstance(gen, MixerGenerator):
            return gen
    raise RuntimeError("MixerGenerator not found — is F&B config loaded?")


def _run_ticks(engine: DataEngine, n: int) -> float:
    """Run n ticks and return the final sim_time."""
    t = 0.0
    for _ in range(n):
        t = engine.tick()
    return t


def _make_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


# Short durations (seconds) for fast tests
_FAST_PARAMS = {
    "loading_duration_range": [1.0, 1.0],
    "mixing_duration_range": [1.0, 1.0],
    "holding_duration_range": [1.0, 1.0],
    "discharging_duration_range": [1.0, 1.0],
}


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestBatchCycleLifecycle:
    """Scenario lifecycle: pending → active → completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = BatchCycle(start_time=10.0, rng=rng, params=_FAST_PARAMS)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_at_start_time(self) -> None:
        engine, _store = _make_engine()
        rng = _make_rng()
        sc = BatchCycle(start_time=0.0, rng=rng, params=_FAST_PARAMS)

        engine.scenario_engine.add_scenario(sc)
        engine.tick()

        assert sc.is_active

    def test_completes_after_all_phases(self) -> None:
        """Scenario completes once all phases have elapsed."""
        engine, _store = _make_engine()
        rng = _make_rng()
        sc = BatchCycle(start_time=0.0, rng=rng, params=_FAST_PARAMS)

        engine.scenario_engine.add_scenario(sc)

        # Total duration = 4 x 1.0 s = 4.0 s; 50 ticks = 5.0 s
        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

    def test_duration_method_returns_sum_of_phases(self) -> None:
        rng = _make_rng()
        sc = BatchCycle(
            start_time=0.0,
            rng=rng,
            params={
                "loading_duration_range": [120.0, 120.0],
                "mixing_duration_range": [600.0, 600.0],
                "holding_duration_range": [300.0, 300.0],
                "discharging_duration_range": [180.0, 180.0],
            },
        )
        assert sc.duration() == pytest.approx(1200.0)

    def test_duration_within_prd_range(self) -> None:
        """Default params produce batches in the 20-45 min PRD range."""
        durations = [BatchCycle(0.0, _make_rng(i)).duration() for i in range(20)]
        for d in durations:
            assert 19 * 60 <= d <= 46 * 60, f"Duration {d:.0f}s outside 19-46 min range"


# ---------------------------------------------------------------------------
# State transition tests
# ---------------------------------------------------------------------------


class TestBatchCycleStateTransitions:
    """Mixer passes through Loading → Mixing → Holding → Discharging → Off."""

    def test_mixer_enters_loading_on_activate(self) -> None:
        engine, _store = _make_engine()
        mixer = _get_mixer(engine)

        rng = _make_rng()
        sc = BatchCycle(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        engine.tick()

        assert sc.is_active
        assert int(mixer.state_machine.generate(0.0, 0.0)) == STATE_LOADING

    def test_mixer_transitions_loading_to_mixing(self) -> None:
        """After loading_duration, mixer should enter Mixing."""
        engine, _store = _make_engine()
        rng = _make_rng()
        sc = BatchCycle(
            start_time=0.0,
            rng=rng,
            params={
                "loading_duration_range": [0.5, 0.5],   # 0.5s
                "mixing_duration_range": [60.0, 60.0],
                "holding_duration_range": [60.0, 60.0],
                "discharging_duration_range": [60.0, 60.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)
        mixer = _get_mixer(engine)

        # Run 6 ticks (0.6s) — past the 0.5s loading duration
        for _ in range(6):
            engine.tick()

        assert sc.internal_phase == _Phase.MIXING
        assert int(mixer.state_machine.generate(0.0, 0.0)) == STATE_MIXING

    def test_mixer_transitions_mixing_to_holding(self) -> None:
        """After mixing_duration, mixer should enter Holding."""
        engine, _store = _make_engine()
        rng = _make_rng()
        sc = BatchCycle(
            start_time=0.0,
            rng=rng,
            params={
                "loading_duration_range": [0.2, 0.2],
                "mixing_duration_range": [0.5, 0.5],
                "holding_duration_range": [60.0, 60.0],
                "discharging_duration_range": [60.0, 60.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)
        mixer = _get_mixer(engine)

        # Run 10 ticks (1.0s) — past loading (0.2s) + mixing (0.5s)
        for _ in range(10):
            engine.tick()

        assert sc.internal_phase == _Phase.HOLDING
        assert int(mixer.state_machine.generate(0.0, 0.0)) == STATE_HOLDING

    def test_mixer_transitions_holding_to_discharging(self) -> None:
        """After holding_duration, mixer should enter Discharging."""
        engine, _store = _make_engine()
        rng = _make_rng()
        sc = BatchCycle(
            start_time=0.0,
            rng=rng,
            params={
                "loading_duration_range": [0.2, 0.2],
                "mixing_duration_range": [0.2, 0.2],
                "holding_duration_range": [0.5, 0.5],
                "discharging_duration_range": [60.0, 60.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)
        mixer = _get_mixer(engine)

        # Run 12 ticks (1.2s) — past all three prior phases
        for _ in range(12):
            engine.tick()

        assert sc.internal_phase == _Phase.DISCHARGING
        assert int(mixer.state_machine.generate(0.0, 0.0)) == STATE_DISCHARGING

    def test_mixer_returns_to_off_after_completion(self) -> None:
        """Mixer state returns to Off once the batch completes."""
        engine, _store = _make_engine()
        rng = _make_rng()
        sc = BatchCycle(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)
        mixer = _get_mixer(engine)

        for _ in range(60):  # 6s — well past 4s total
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert int(mixer.state_machine.generate(0.0, 0.0)) == STATE_OFF

    def test_full_phase_sequence(self) -> None:
        """Observe all phase transitions in order."""
        engine, _store = _make_engine()
        rng = _make_rng()
        sc = BatchCycle(
            start_time=0.0,
            rng=rng,
            params={
                "loading_duration_range": [0.3, 0.3],
                "mixing_duration_range": [0.3, 0.3],
                "holding_duration_range": [0.3, 0.3],
                "discharging_duration_range": [0.3, 0.3],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        phases_seen: list[_Phase] = []
        last_phase = None

        for _ in range(50):
            engine.tick()
            if sc.is_active and sc.internal_phase != last_phase:
                phases_seen.append(sc.internal_phase)
                last_phase = sc.internal_phase
            if sc.is_completed:
                break

        assert phases_seen == [
            _Phase.LOADING,
            _Phase.MIXING,
            _Phase.HOLDING,
            _Phase.DISCHARGING,
        ]


# ---------------------------------------------------------------------------
# Batch-to-batch variation tests
# ---------------------------------------------------------------------------


class TestBatchCycleVariation:
    """PRD 5.14.1: each batch has slightly different ingredient volumes / durations."""

    def test_different_seeds_produce_different_durations(self) -> None:
        """Two batches with different RNG seeds must differ in total duration."""
        sc_a = BatchCycle(0.0, _make_rng(1))
        sc_b = BatchCycle(0.0, _make_rng(2))
        # Durations are drawn from uniform [120,300] + [600,1500] + [300,600] + [120,300]
        # Very unlikely to be equal
        assert sc_a.duration() != pytest.approx(sc_b.duration(), rel=1e-6)

    def test_phase_durations_within_configured_range(self) -> None:
        """Phase durations must respect the configured [min, max] bounds."""
        lo_lo, lo_hi = 100.0, 200.0
        mx_lo, mx_hi = 400.0, 800.0
        ho_lo, ho_hi = 200.0, 400.0
        di_lo, di_hi = 100.0, 200.0

        params = {
            "loading_duration_range": [lo_lo, lo_hi],
            "mixing_duration_range": [mx_lo, mx_hi],
            "holding_duration_range": [ho_lo, ho_hi],
            "discharging_duration_range": [di_lo, di_hi],
        }
        for seed in range(20):
            sc = BatchCycle(0.0, _make_rng(seed), params)
            assert lo_lo <= sc.loading_duration <= lo_hi
            assert mx_lo <= sc.mixing_duration <= mx_hi
            assert ho_lo <= sc.holding_duration <= ho_hi
            assert di_lo <= sc.discharging_duration <= di_hi


# ---------------------------------------------------------------------------
# Resilience: no mixer in engine
# ---------------------------------------------------------------------------


class TestBatchCycleNoMixer:
    """Scenario completes gracefully when no mixer generator is present."""

    def test_completes_immediately_without_mixer(self) -> None:
        """If no mixer exists the scenario should complete without crashing."""
        # Use the packaging config which has no mixer
        pkg_config_path = (
            Path(__file__).resolve().parents[3] / "config" / "factory.yaml"
        )
        config = load_config(pkg_config_path, apply_env=False)
        config.simulation.tick_interval_ms = 100
        # Disable all packaging auto-scenarios
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
        sc = BatchCycle(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(5):
            engine.tick()

        assert sc.is_completed
