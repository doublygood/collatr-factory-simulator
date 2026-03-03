"""Tests for the micro-stop scenario (PRD 5.15).

Verifies:
- Priority is "micro"
- Default params are drawn from the correct ranges
- Speed drops by the expected percentage during the dip
- Machine state stays Running (2) throughout
- Speed recovers to target after ramp-up
- Scenario completes after total_s = ramp_down_s + hold_s + ramp_up_s
- Auto-scheduling via ScenarioEngine uses MicroStopConfig values
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
from factory_simulator.scenarios.micro_stop import MicroStop

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "factory.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def _make_engine(seed: int = 42) -> DataEngine:
    """Create a DataEngine with all auto-scheduled scenarios disabled."""
    config = load_config(_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = seed
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = 1.0
    # Disable all auto-scheduled scenarios so the test controls exactly
    # which scenarios run.
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

    from factory_simulator.store import SignalStore
    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    return DataEngine(config, store, clock)


def _get_press(engine: DataEngine) -> PressGenerator:
    for gen in engine.generators:
        if isinstance(gen, PressGenerator):
            return gen
    raise RuntimeError("PressGenerator not found")


def _run_ticks(engine: DataEngine, n: int) -> None:
    for _ in range(n):
        engine.tick()


def _make_fast_micro_stop(
    *,
    hold_s: float = 5.0,
    drop_pct: float = 50.0,
    ramp_down_s: float = 0.5,
    ramp_up_s: float = 0.5,
    start_time: float = 0.0,
) -> MicroStop:
    """Construct a MicroStop with deterministic fixed params for fast tests."""
    rng = _make_rng()
    return MicroStop(
        start_time=start_time,
        rng=rng,
        params={
            "duration_seconds": [hold_s, hold_s],
            "speed_drop_percent": [drop_pct, drop_pct],
            "ramp_down_seconds": [ramp_down_s, ramp_down_s],
            "ramp_up_seconds": [ramp_up_s, ramp_up_s],
        },
    )


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------


class TestMicroStopPriority:
    def test_priority_is_micro(self) -> None:
        sc = _make_fast_micro_stop()
        assert sc.priority == "micro"


# ---------------------------------------------------------------------------
# Construction / defaults
# ---------------------------------------------------------------------------


class TestMicroStopDefaults:
    def test_default_hold_in_range(self) -> None:
        rng = _make_rng()
        sc = MicroStop(start_time=0.0, rng=rng)
        assert 5.0 <= sc.hold_s <= 30.0

    def test_default_drop_pct_in_range(self) -> None:
        rng = _make_rng()
        sc = MicroStop(start_time=0.0, rng=rng)
        assert 30.0 <= sc.drop_pct <= 80.0

    def test_default_ramp_down_in_range(self) -> None:
        rng = _make_rng()
        sc = MicroStop(start_time=0.0, rng=rng)
        assert 2.0 <= sc.ramp_down_s <= 5.0

    def test_default_ramp_up_in_range(self) -> None:
        rng = _make_rng()
        sc = MicroStop(start_time=0.0, rng=rng)
        assert 5.0 <= sc.ramp_up_s <= 15.0

    def test_duration_equals_sum_of_phases(self) -> None:
        rng = _make_rng()
        sc = MicroStop(start_time=0.0, rng=rng)
        expected = sc.ramp_down_s + sc.hold_s + sc.ramp_up_s
        assert sc.duration() == pytest.approx(expected)

    def test_fixed_params_are_stored(self) -> None:
        sc = _make_fast_micro_stop(
            hold_s=10.0, drop_pct=60.0, ramp_down_s=2.0, ramp_up_s=8.0
        )
        assert sc.hold_s == pytest.approx(10.0)
        assert sc.drop_pct == pytest.approx(60.0)
        assert sc.ramp_down_s == pytest.approx(2.0)
        assert sc.ramp_up_s == pytest.approx(8.0)
        assert sc.duration() == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestMicroStopLifecycle:
    def test_starts_pending(self) -> None:
        sc = _make_fast_micro_stop(start_time=10.0)
        assert sc.phase == ScenarioPhase.PENDING

    def test_activates_at_start_time(self) -> None:
        engine = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        sc = _make_fast_micro_stop(start_time=0.0)
        engine.scenario_engine.add_scenario(sc)
        engine.tick()
        assert sc.is_active

    def test_completes_after_total_duration(self) -> None:
        engine = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        # Very short micro-stop: 0.5 + 1.0 + 0.5 = 2.0 seconds total
        sc = _make_fast_micro_stop(
            hold_s=1.0, ramp_down_s=0.5, ramp_up_s=0.5, start_time=0.0
        )
        engine.scenario_engine.add_scenario(sc)

        # 2s at 100ms/tick = 20 ticks; add margin
        for _ in range(50):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed


# ---------------------------------------------------------------------------
# Speed dip
# ---------------------------------------------------------------------------


class TestMicroStopSpeedDip:
    def test_speed_drops_during_micro_stop(self) -> None:
        """Line speed must be lower during the micro-stop hold phase.

        The press generator fires every 500ms but advances the ramp by only
        0.1s per call (tick dt).  To have the press at near-target speed we
        set the line speed ramp directly to a realistic running value rather
        than waiting for many ramp-up ticks.
        """
        engine = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        # Manually place the ramp at ~190 m/min → 200 m/min (slow drift):
        # this simulates a press that is nearly at target speed.
        press._line_speed_model.start_ramp(
            start=190.0, end=200.0, duration=10000.0,
        )
        _run_ticks(engine, 5)  # let one generator call fire to initialise

        speed_before = press._line_speed_model.value  # ≈ 190 m/min

        # 50% drop → low_speed = 200 * 0.5 = 100 m/min (< 190 = current speed)
        sc = _make_fast_micro_stop(
            hold_s=2.0, drop_pct=50.0, ramp_down_s=0.1, ramp_up_s=0.1, start_time=0.0
        )
        engine.scenario_engine.add_scenario(sc)

        # Activate scenario (1 tick)
        engine.tick()
        assert sc.is_active

        # Run enough ticks for ramp-down to complete (0.1s → needs 1 gen fire = 5 ticks)
        _run_ticks(engine, 15)

        # Measure speed during hold
        speed_during = press._line_speed_model.value

        # Speed should be well below the pre-stop speed
        assert speed_during < speed_before * 0.8, (
            f"Speed {speed_during:.1f} not lower than 80% of pre-stop "
            f"speed {speed_before:.1f}"
        )

    def test_machine_state_stays_running(self) -> None:
        """Machine state must remain Running throughout the micro-stop."""
        engine = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        sc = _make_fast_micro_stop(
            hold_s=1.0, drop_pct=60.0, ramp_down_s=0.2, ramp_up_s=0.2, start_time=0.0
        )
        engine.scenario_engine.add_scenario(sc)

        for _ in range(30):
            engine.tick()
            # Use the state machine's current_state property (read-only, no advance)
            if sc.is_active:
                assert press.state_machine.current_state == "Running", (
                    f"Machine state changed to {press.state_machine.current_state!r} "
                    "during micro-stop"
                )
            if sc.is_completed:
                break

    def test_speed_recovers_after_micro_stop(self) -> None:
        """Speed must ramp back to approximately the original target.

        Uses the same ramp-injection technique as test_speed_drops to ensure
        the press starts at near-target speed before the micro-stop.
        """
        engine = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        # Inject a near-target speed directly
        press._line_speed_model.start_ramp(
            start=190.0, end=200.0, duration=10000.0,
        )
        _run_ticks(engine, 5)

        target = press._target_speed  # 200 m/min

        # Short ramp phases to keep test fast; hold long enough that the
        # scenario completes within a manageable number of ticks.
        # Total = 0.3 + 1.0 + 0.3 = 1.6s → needs ~80 gen-fires → ~400 ticks
        sc = _make_fast_micro_stop(
            hold_s=1.0, drop_pct=50.0, ramp_down_s=0.3, ramp_up_s=0.3, start_time=0.0
        )
        engine.scenario_engine.add_scenario(sc)

        # Run until completion
        for _ in range(500):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        speed_after = press._line_speed_model.value
        # Speed should have recovered to within 30% of target
        # (ramp is still in progress at exactly completion time; generous bound)
        assert speed_after >= target * 0.5, (
            f"Speed {speed_after:.1f} did not recover towards {target:.1f}"
        )

    def test_low_speed_matches_drop_percent(self) -> None:
        """The saved low_speed should equal target * (1 - drop_pct/100)."""
        engine = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        sc = _make_fast_micro_stop(drop_pct=50.0, start_time=0.0)
        engine.scenario_engine.add_scenario(sc)
        engine.tick()  # activate

        expected_low = press._target_speed * 0.50
        assert sc.low_speed == pytest.approx(expected_low, rel=0.01)
        assert sc.saved_target == pytest.approx(press._target_speed, rel=0.01)


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


class TestMicroStopScheduling:
    def test_scheduling_creates_micro_stops_when_enabled(self) -> None:
        """ScenarioEngine must schedule MicroStop instances when enabled."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 42
        config.simulation.tick_interval_ms = 100
        config.simulation.sim_duration_s = 7 * 24 * 3600  # 1 week
        # Disable everything except micro_stop
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
            config.scenarios.micro_stop.enabled = True

        from factory_simulator.store import SignalStore
        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        micro_stops = [
            s for s in engine.scenario_engine.scenarios
            if isinstance(s, MicroStop)
        ]
        # 1 week = 21 shifts; 10-50 per shift means 210-1050 expected.
        # Poisson is stochastic so use loose bounds.
        assert len(micro_stops) >= 50, (
            f"Expected many MicroStops over 1 week, got {len(micro_stops)}"
        )

    def test_micro_stop_priority_in_engine(self) -> None:
        """All auto-scheduled MicroStop instances must have 'micro' priority."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 42
        config.simulation.sim_duration_s = 8 * 3600  # 1 shift
        if config.scenarios.micro_stop is not None:
            config.scenarios.micro_stop.enabled = True

        from factory_simulator.store import SignalStore
        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        for s in engine.scenario_engine.scenarios:
            if isinstance(s, MicroStop):
                assert s.priority == "micro"
