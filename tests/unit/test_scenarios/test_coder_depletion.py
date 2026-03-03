"""Tests for the coder consumable depletion scenario.

Verifies (PRD 5.12):
- Ink level depletes linearly (via existing DepletionModel).
- At 10% level: quality flag changes to "uncertain".
- At 2% level: coder enters Fault (3) state.
- After recovery: ink level resets to 100%, coder returns to Ready.
- Auto-refill disabled during scenario, restored after.
- G5 fix: gutter_fault probability = MTBF 500+ hours.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.generators.coder import CoderGenerator
from factory_simulator.generators.press import PressGenerator
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.coder_depletion import CoderDepletion, _Phase
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
    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    return engine, store


def _get_coder(engine: DataEngine) -> CoderGenerator:
    """Find the coder generator."""
    for gen in engine.generators:
        if isinstance(gen, CoderGenerator):
            return gen
    raise RuntimeError("Coder generator not found")


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


def _deplete_ink_to(coder: CoderGenerator, level: float) -> None:
    """Force the ink_level DepletionModel value to a specific level."""
    coder._ink_level._value = level


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestCoderDepletionLifecycle:
    """Scenario lifecycle: pending -> active (monitoring) -> depleted -> completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = CoderDepletion(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_into_monitoring(self) -> None:
        """Scenario activates at start_time and enters MONITORING phase."""
        engine, store = _make_engine()
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(start_time=0.0, rng=rng)
        engine.scenario_engine.add_scenario(sc)

        engine.tick()

        assert sc.is_active
        assert sc.internal_phase == _Phase.MONITORING

    def test_transitions_to_depleted_at_empty_threshold(self) -> None:
        """Scenario enters DEPLETED when ink_level <= 2%."""
        engine, store = _make_engine()
        press = _get_press(engine)
        coder = _get_coder(engine)

        # Press must be Running for coder to be Printing (which depletes ink)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={"recovery_duration_range": [1.0, 1.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        # Force ink level just above empty threshold
        _deplete_ink_to(coder, 3.0)
        engine.tick()
        assert sc.internal_phase == _Phase.MONITORING

        # Force ink level below empty threshold
        _deplete_ink_to(coder, 1.5)
        engine.tick()
        assert sc.internal_phase == _Phase.DEPLETED

    def test_completes_after_recovery_duration(self) -> None:
        """Scenario completes after recovery_duration in DEPLETED phase."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={"recovery_duration_range": [1.0, 1.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        # Force ink below empty threshold
        _deplete_ink_to(coder, 1.0)

        # Run until completed (recovery is 1.0s = 10 ticks + a few extra)
        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

    def test_duration_method(self) -> None:
        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={"recovery_duration_range": [5.5, 5.5]},
        )
        assert sc.duration() == pytest.approx(5.5)


# ---------------------------------------------------------------------------
# Quality flag tests (PRD 5.12: quality "uncertain" at 10%)
# ---------------------------------------------------------------------------


class TestCoderDepletionQualityFlag:
    """PRD 5.12: at 10% level, quality flag changes to 'uncertain'."""

    def test_quality_uncertain_at_low_ink(self) -> None:
        """Quality override set to 'uncertain' when ink <= 10%."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(start_time=0.0, rng=rng)
        engine.scenario_engine.add_scenario(sc)

        # Ink above threshold -- no quality override
        _deplete_ink_to(coder, 15.0)
        engine.tick()
        assert "ink_level" not in coder._quality_overrides
        assert not sc.low_ink_flagged

        # Ink at threshold -- quality should be "uncertain"
        _deplete_ink_to(coder, 9.0)
        engine.tick()
        assert coder._quality_overrides.get("ink_level") == "uncertain"
        assert sc.low_ink_flagged

    def test_quality_in_store_uncertain(self) -> None:
        """Signal store shows quality='uncertain' for ink_level when flagged."""
        engine, store = _make_engine()
        press = _get_press(engine)
        coder = _get_coder(engine)

        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(start_time=0.0, rng=rng)
        engine.scenario_engine.add_scenario(sc)

        # Force ink below 10%
        _deplete_ink_to(coder, 8.0)

        # Run enough ticks for scenario + generator to fire
        # Coder generator fires at its min sample rate (1000ms = 10 ticks)
        _run_ticks(engine, 15)

        sv = store.get("coder.ink_level")
        assert sv is not None
        quality = sv.quality
        assert quality == "uncertain"

    def test_quality_not_set_above_threshold(self) -> None:
        """Quality remains 'good' when ink is above 10%."""
        engine, store = _make_engine()
        press = _get_press(engine)
        coder = _get_coder(engine)

        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(start_time=0.0, rng=rng)
        engine.scenario_engine.add_scenario(sc)

        _deplete_ink_to(coder, 50.0)
        _run_ticks(engine, 15)

        sv = store.get("coder.ink_level")
        assert sv is not None
        quality = sv.quality
        assert quality == "good"

    def test_quality_flagged_even_if_level_skips_to_empty(self) -> None:
        """Quality flag set even when ink drops below both thresholds at once."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={"recovery_duration_range": [5.0, 5.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        # Skip past both thresholds in one step
        _deplete_ink_to(coder, 0.5)
        engine.tick()

        assert sc.low_ink_flagged
        assert coder._quality_overrides.get("ink_level") == "uncertain"
        assert sc.internal_phase == _Phase.DEPLETED

    def test_custom_low_ink_threshold(self) -> None:
        """Custom low_ink_threshold is respected."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={"low_ink_threshold": 20.0},
        )
        engine.scenario_engine.add_scenario(sc)

        _deplete_ink_to(coder, 18.0)
        engine.tick()

        assert sc.low_ink_flagged
        assert coder._quality_overrides.get("ink_level") == "uncertain"


# ---------------------------------------------------------------------------
# Fault state tests (PRD 5.12: Fault at 2%)
# ---------------------------------------------------------------------------


class TestCoderDepletionFault:
    """PRD 5.12: at 2% level, coder enters Fault (3)."""

    def test_coder_faulted_at_empty(self) -> None:
        """Coder state forced to Fault when ink <= 2%."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={"recovery_duration_range": [5.0, 5.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        _deplete_ink_to(coder, 1.0)
        engine.tick()

        assert coder._state_machine.current_state == "Fault"
        assert sc.internal_phase == _Phase.DEPLETED

    def test_coder_stays_in_fault_during_recovery(self) -> None:
        """Coder remains in Fault for the full recovery duration."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={"recovery_duration_range": [3.0, 3.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        _deplete_ink_to(coder, 1.0)

        # Run 20 ticks (2.0s < 3.0s recovery).  Check coder stays in Fault.
        for _ in range(20):
            engine.tick()

        assert not sc.is_completed
        assert coder._state_machine.current_state == "Fault"

    def test_custom_empty_threshold(self) -> None:
        """Custom empty_threshold is respected."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={
                "empty_threshold": 5.0,
                "recovery_duration_range": [1.0, 1.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Above custom threshold -- no fault
        _deplete_ink_to(coder, 6.0)
        engine.tick()
        assert sc.internal_phase == _Phase.MONITORING

        # Below custom threshold -- fault
        _deplete_ink_to(coder, 4.0)
        engine.tick()
        assert sc.internal_phase == _Phase.DEPLETED


# ---------------------------------------------------------------------------
# Recovery / refill tests (PRD 5.12: refill to 100%)
# ---------------------------------------------------------------------------


class TestCoderDepletionRecovery:
    """PRD 5.12: operator intervention resets ink to 100%."""

    def test_ink_refilled_after_recovery(self) -> None:
        """Ink level resets to 100% after recovery completes."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={"recovery_duration_range": [1.0, 1.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        _deplete_ink_to(coder, 1.0)

        # Run past recovery
        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert coder._ink_level.value == pytest.approx(100.0)

    def test_coder_returns_to_ready_after_recovery(self) -> None:
        """Coder state forced to Ready after recovery."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={"recovery_duration_range": [1.0, 1.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        _deplete_ink_to(coder, 1.0)

        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert coder._state_machine.current_state == "Ready"

    def test_quality_cleared_after_recovery(self) -> None:
        """Quality override removed after recovery."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={"recovery_duration_range": [1.0, 1.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        _deplete_ink_to(coder, 1.0)

        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert "ink_level" not in coder._quality_overrides

    def test_auto_refill_restored_after_recovery(self) -> None:
        """DepletionModel refill_threshold restored to original value."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        original_threshold = coder._ink_level._refill_threshold
        assert original_threshold is not None  # config has 5.0

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={"recovery_duration_range": [1.0, 1.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        # During scenario, auto-refill is disabled
        engine.tick()
        assert coder._ink_level._refill_threshold is None

        _deplete_ink_to(coder, 1.0)

        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert coder._ink_level._refill_threshold == original_threshold

    def test_fault_timer_restored_after_recovery(self) -> None:
        """Fault->Ready transition timer restored to original parameters."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        # Find original Fault->Ready transition params
        original_min = None
        original_max = None
        for t in coder._state_machine._transitions:
            if t.from_state == "Fault" and t.to_state == "Ready":
                original_min = t.min_duration
                original_max = t.max_duration
                break
        assert original_min is not None

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={"recovery_duration_range": [1.0, 1.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        _deplete_ink_to(coder, 1.0)

        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

        # Verify transition params restored
        for t in coder._state_machine._transitions:
            if t.from_state == "Fault" and t.to_state == "Ready":
                assert t.min_duration == original_min
                assert t.max_duration == original_max
                break

    def test_custom_refill_level(self) -> None:
        """Custom refill_level is respected."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={
                "recovery_duration_range": [1.0, 1.0],
                "refill_level": 95.0,
            },
        )
        engine.scenario_engine.add_scenario(sc)

        _deplete_ink_to(coder, 1.0)

        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert coder._ink_level.value == pytest.approx(95.0)


# ---------------------------------------------------------------------------
# Auto-refill suppression tests
# ---------------------------------------------------------------------------


class TestCoderDepletionAutoRefill:
    """The scenario must suppress auto-refill during its active period."""

    def test_auto_refill_disabled_during_scenario(self) -> None:
        """DepletionModel does NOT auto-refill while scenario is active."""
        engine, store = _make_engine()
        coder = _get_coder(engine)

        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={"recovery_duration_range": [5.0, 5.0]},
        )
        engine.scenario_engine.add_scenario(sc)

        # Force ink below the config's refill_threshold (5.0)
        # but above the empty_threshold (2.0).
        # Without the scenario, auto-refill would trigger at 5%.
        _deplete_ink_to(coder, 4.0)
        engine.tick()

        # Ink should still be ~4.0, not refilled to 100
        assert coder._ink_level.value < 10.0
        assert sc.internal_phase == _Phase.MONITORING


# ---------------------------------------------------------------------------
# Parameter defaults
# ---------------------------------------------------------------------------


class TestCoderDepletionDefaults:
    """Verify default parameter values match PRD 5.12."""

    def test_default_low_ink_threshold(self) -> None:
        rng = _make_rng()
        sc = CoderDepletion(start_time=0.0, rng=rng)
        assert sc.low_ink_threshold == pytest.approx(10.0)

    def test_default_empty_threshold(self) -> None:
        rng = _make_rng()
        sc = CoderDepletion(start_time=0.0, rng=rng)
        assert sc.empty_threshold == pytest.approx(2.0)

    def test_default_recovery_duration_range(self) -> None:
        """Recovery duration: 300-1800 seconds (5-30 min)."""
        rng = _make_rng()
        sc = CoderDepletion(start_time=0.0, rng=rng)
        assert 300.0 <= sc.recovery_duration <= 1800.0

    def test_default_refill_level(self) -> None:
        rng = _make_rng()
        sc = CoderDepletion(start_time=0.0, rng=rng)
        assert sc.refill_level == pytest.approx(100.0)

    def test_fixed_params_are_deterministic(self) -> None:
        rng = _make_rng()
        sc = CoderDepletion(
            start_time=0.0,
            rng=rng,
            params={
                "low_ink_threshold": 15.0,
                "empty_threshold": 3.0,
                "recovery_duration_range": [600.0, 600.0],
                "refill_level": 98.0,
            },
        )
        assert sc.low_ink_threshold == pytest.approx(15.0)
        assert sc.empty_threshold == pytest.approx(3.0)
        assert sc.recovery_duration == pytest.approx(600.0)
        assert sc.refill_level == pytest.approx(98.0)


# ---------------------------------------------------------------------------
# G5 fix: gutter_fault MTBF
# ---------------------------------------------------------------------------


class TestGutterFaultMTBF:
    """G5 fix: gutter_fault probability should give MTBF 500+ hours."""

    def test_gutter_fault_probability(self) -> None:
        """Probability ≈ 0.000000556 per second (MTBF 500h = 1,800,000s)."""
        engine, _ = _make_engine()
        coder = _get_coder(engine)

        # Find the Clear -> Fault transition
        prob = None
        for t in coder._gutter_fault._transitions:
            if t.from_state == "Clear" and t.to_state == "Fault":
                prob = t.probability
                break

        assert prob is not None
        # MTBF = 1/prob >= 500 hours = 1,800,000 seconds
        mtbf_s = 1.0 / prob
        mtbf_h = mtbf_s / 3600.0
        assert mtbf_h >= 490.0  # ~500 hours with tolerance
        # Also verify it's in the right ballpark (not accidentally 0)
        assert prob > 0.0
        assert prob < 0.000001  # much less than 1e-6
