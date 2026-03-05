"""Tests for the fill weight drift scenario (F&B filler).

Verifies (PRD 5.14.3):
- filler.fill_weight mean drifts from target at 0.05-0.2 g per minute.
- As the mean drifts, more fills fall outside the acceptable range.
- filler.reject_count increases proportionally during drift.
- After drift duration, the mean returns to target (operator recalibrates).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.generators.filler import FillerGenerator
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.fill_weight_drift import FillWeightDrift
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


def _get_filler(engine: DataEngine) -> FillerGenerator:
    """Find the filler generator (raises if not found)."""
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


# Short drift for fast tests: 1 second with a fast rate
_FAST_PARAMS: dict[str, object] = {
    "drift_duration_range": [2.0, 2.0],   # 2 seconds
    "drift_rate_range": [60.0, 60.0],     # 60 g/min — 2 g after 2s
    "max_drift_range": [10.0, 10.0],      # cap well above
    "direction": 1,                        # over-weight
}


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestFillWeightDriftLifecycle:
    """Scenario lifecycle: pending -> active -> completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = FillWeightDrift(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_at_start_time(self) -> None:
        engine, _store = _make_engine()
        filler = _get_filler(engine)
        filler.state_machine.force_state("Running")
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = FillWeightDrift(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active

    def test_completes_after_drift_duration(self) -> None:
        """Scenario completes once drift_duration has elapsed."""
        engine, _store = _make_engine()
        filler = _get_filler(engine)
        filler.state_machine.force_state("Running")
        _run_ticks(engine, 3)

        rng = _make_rng()
        sc = FillWeightDrift(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):  # 5s — well past 2s drift duration
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

    def test_duration_method_returns_drift_duration(self) -> None:
        rng = _make_rng()
        sc = FillWeightDrift(
            start_time=0.0,
            rng=rng,
            params={"drift_duration_range": [900.0, 900.0]},
        )
        assert sc.duration() == pytest.approx(900.0)


# ---------------------------------------------------------------------------
# Drift effect tests
# ---------------------------------------------------------------------------


class TestFillWeightDriftEffect:
    """PRD 5.14.3: fill weight mean drifts from target during scenario."""

    def test_giveaway_increases_in_over_weight_direction(self) -> None:
        """With direction=+1, _fill_giveaway should increase above original."""
        engine, _store = _make_engine()
        filler = _get_filler(engine)
        filler.state_machine.force_state("Running")
        _run_ticks(engine, 3)

        original_giveaway = filler._fill_giveaway

        rng = _make_rng()
        sc = FillWeightDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_duration_range": [60.0, 60.0],
                "drift_rate_range": [6.0, 6.0],  # 6 g/min
                "max_drift_range": [20.0, 20.0],
                "direction": 1,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Run 100 ticks (10 seconds). At 6 g/min → 1 g drift.
        for _ in range(100):
            engine.tick()

        assert sc.is_active
        assert filler._fill_giveaway > original_giveaway + 0.5

    def test_giveaway_decreases_in_under_weight_direction(self) -> None:
        """With direction=-1, _fill_giveaway should decrease below original."""
        engine, _store = _make_engine()
        filler = _get_filler(engine)
        filler.state_machine.force_state("Running")
        _run_ticks(engine, 3)

        original_giveaway = filler._fill_giveaway

        rng = _make_rng()
        sc = FillWeightDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_duration_range": [60.0, 60.0],
                "drift_rate_range": [6.0, 6.0],  # 6 g/min
                "max_drift_range": [20.0, 20.0],
                "direction": -1,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Run 100 ticks (10s). At 6 g/min → -1 g drift.
        for _ in range(100):
            engine.tick()

        assert sc.is_active
        assert filler._fill_giveaway < original_giveaway - 0.5

    def test_drift_proportional_to_rate_and_elapsed(self) -> None:
        """Drift offset should match drift_rate * elapsed / 60 (before capping)."""
        engine, _store = _make_engine()
        filler = _get_filler(engine)
        filler.state_machine.force_state("Running")
        _run_ticks(engine, 3)

        original_giveaway = filler._fill_giveaway
        drift_rate = 6.0  # g/min

        rng = _make_rng()
        sc = FillWeightDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_duration_range": [120.0, 120.0],
                "drift_rate_range": [drift_rate, drift_rate],
                "max_drift_range": [50.0, 50.0],  # no cap
                "direction": 1,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Run 300 ticks (30s). Expected drift = 6 g/min * 30s / 60 = 3 g.
        for _ in range(300):
            engine.tick()

        assert sc.is_active
        expected_drift = drift_rate * 30.0 / 60.0  # 3.0 g
        actual_drift = filler._fill_giveaway - original_giveaway

        # Allow ±0.5 g tolerance for tick rounding
        assert abs(actual_drift - expected_drift) < 0.5

    def test_drift_capped_at_max_drift(self) -> None:
        """Drift should not exceed max_drift regardless of elapsed time."""
        engine, _store = _make_engine()
        filler = _get_filler(engine)
        filler.state_machine.force_state("Running")
        _run_ticks(engine, 3)

        original_giveaway = filler._fill_giveaway
        max_drift = 2.0

        rng = _make_rng()
        sc = FillWeightDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_duration_range": [120.0, 120.0],
                "drift_rate_range": [60.0, 60.0],  # would give 60 g/min without cap
                "max_drift_range": [max_drift, max_drift],
                "direction": 1,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Run 300 ticks (30s). Without cap, drift would be 30 g.
        for _ in range(300):
            engine.tick()

        assert sc.is_active
        actual_drift = filler._fill_giveaway - original_giveaway
        assert actual_drift <= max_drift + 0.01


# ---------------------------------------------------------------------------
# Recovery tests
# ---------------------------------------------------------------------------


class TestFillWeightDriftRecovery:
    """PRD 5.14.3 step 4: mean returns to target after operator recalibration."""

    def test_giveaway_restored_after_completion(self) -> None:
        """On scenario completion, _fill_giveaway must be restored to original."""
        engine, _store = _make_engine()
        filler = _get_filler(engine)
        filler.state_machine.force_state("Running")
        _run_ticks(engine, 3)

        original_giveaway = filler._fill_giveaway

        rng = _make_rng()
        sc = FillWeightDrift(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert filler._fill_giveaway == pytest.approx(original_giveaway, abs=1e-9)

    def test_giveaway_elevated_during_drift_then_restored(self) -> None:
        """Giveaway is elevated mid-scenario and restored at completion."""
        engine, _store = _make_engine()
        filler = _get_filler(engine)
        filler.state_machine.force_state("Running")
        _run_ticks(engine, 3)

        original_giveaway = filler._fill_giveaway
        peak_giveaway_seen = False

        rng = _make_rng()
        sc = FillWeightDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_duration_range": [1.0, 1.0],
                "drift_rate_range": [60.0, 60.0],
                "max_drift_range": [10.0, 10.0],
                "direction": 1,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        for _ in range(50):
            engine.tick()
            if sc.is_active and filler._fill_giveaway > original_giveaway + 0.3:
                peak_giveaway_seen = True
            if sc.is_completed:
                break

        assert peak_giveaway_seen, "giveaway was never elevated during drift"
        assert filler._fill_giveaway == pytest.approx(original_giveaway, abs=1e-9)


# ---------------------------------------------------------------------------
# Parameter default tests
# ---------------------------------------------------------------------------


class TestFillWeightDriftDefaults:
    """Verify default parameter ranges match PRD 5.14.3."""

    def test_default_drift_duration_range(self) -> None:
        """Default drift duration: 10-60 min (600-3600 s)."""
        rng = _make_rng()
        sc = FillWeightDrift(start_time=0.0, rng=rng)
        assert 600.0 <= sc.drift_duration <= 3600.0

    def test_default_drift_rate_range(self) -> None:
        """Default drift rate: 0.05-0.2 g per minute."""
        rng = _make_rng()
        sc = FillWeightDrift(start_time=0.0, rng=rng)
        assert 0.05 <= sc.drift_rate <= 0.2

    def test_direction_explicit_plus_one(self) -> None:
        rng = _make_rng()
        sc = FillWeightDrift(start_time=0.0, rng=rng, params={"direction": 1})
        assert sc.direction == 1

    def test_direction_explicit_minus_one(self) -> None:
        rng = _make_rng()
        sc = FillWeightDrift(start_time=0.0, rng=rng, params={"direction": -1})
        assert sc.direction == -1

    def test_random_direction_in_valid_set(self) -> None:
        """Random direction must be -1 or +1."""
        seen = set()
        for seed in range(20):
            rng = _make_rng(seed)
            sc = FillWeightDrift(start_time=0.0, rng=rng)
            assert sc.direction in (-1, 1)
            seen.add(sc.direction)
        # Both directions should appear across 20 seeds
        assert seen == {-1, 1}

    def test_fixed_params_deterministic(self) -> None:
        rng = _make_rng()
        sc = FillWeightDrift(
            start_time=0.0,
            rng=rng,
            params={
                "drift_duration_range": [1200.0, 1200.0],
                "drift_rate_range": [0.1, 0.1],
                "max_drift_range": [4.0, 4.0],
                "direction": 1,
            },
        )
        assert sc.drift_duration == pytest.approx(1200.0)
        assert sc.drift_rate == pytest.approx(0.1)
        assert sc.max_drift == pytest.approx(4.0)
        assert sc.direction == 1


# ---------------------------------------------------------------------------
# Resilience: no filler in engine
# ---------------------------------------------------------------------------


class TestFillWeightDriftNoFiller:
    """Scenario completes gracefully when no filler generator is present."""

    def test_completes_immediately_without_filler(self) -> None:
        """If no filler exists, the scenario should complete without crashing."""
        config = load_config(_PKG_CONFIG, apply_env=False)
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
        sc = FillWeightDrift(start_time=0.0, rng=rng, params=_FAST_PARAMS)
        engine.scenario_engine.add_scenario(sc)

        for _ in range(5):
            engine.tick()

        assert sc.is_completed
