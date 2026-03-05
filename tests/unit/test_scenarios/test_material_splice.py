"""Tests for the material splice scenario.

Verifies (PRD 5.13a):
- Trigger: press.unwind_diameter drops below 150 mm while Running.
- press.web_tension spikes 50-100 N above normal for 1-3 seconds.
- press.registration_error_x and _y increase by 0.1-0.3 mm for 10-20 s.
- press.waste_count rate increases during the splice window.
- press.unwind_diameter resets to 1500 mm (full reel).
- press.line_speed dips 5-10% then recovers within 5-10 seconds.
- press.machine_state stays Running (2) throughout.
- Multi-phase lifecycle: MONITORING -> SPLICE -> COMPLETED.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.generators.press import STATE_RUNNING, PressGenerator
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.material_splice import MaterialSplice, _Phase
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
    if config.scenarios.intermittent_fault is not None:
        config.scenarios.intermittent_fault.enabled = False
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


def _setup_running_press(
    engine: DataEngine,
    press: PressGenerator,
) -> None:
    """Get the press to Running with stable speed.

    Forces Running state and ticks enough for the speed ramp to
    complete and generators to fire.
    """
    press.state_machine.force_state("Running")
    # Run enough ticks for speed to ramp up and stabilise
    _run_ticks(engine, 50)


def _set_low_unwind(press: PressGenerator, diameter: float = 100.0) -> None:
    """Directly set unwind diameter low enough to trigger splice."""
    press._unwind_diameter._value = diameter


# ---------------------------------------------------------------------------
# Lifecycle and phase tests
# ---------------------------------------------------------------------------


class TestMaterialSpliceLifecycle:
    """Scenario lifecycle: pending -> active -> monitoring -> splice -> completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = MaterialSplice(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_to_monitoring(self) -> None:
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        rng = _make_rng()
        sc = MaterialSplice(start_time=0.0, rng=rng)
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active
        assert sc.internal_phase == _Phase.MONITORING

    def test_does_not_trigger_when_unwind_high(self) -> None:
        """No splice when unwind diameter > trigger threshold."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        # Ensure unwind is well above trigger threshold
        press._unwind_diameter._value = 500.0

        rng = _make_rng()
        sc = MaterialSplice(start_time=0.0, rng=rng)
        t = engine.clock.sim_time

        # Evaluate several ticks -- should stay in MONITORING
        for _ in range(20):
            t += engine.clock.dt
            sc.evaluate(t, engine.clock.dt, engine)

        assert sc.internal_phase == _Phase.MONITORING

    def test_does_not_trigger_when_not_running(self) -> None:
        """No splice when press is not Running (e.g. Idle)."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _run_ticks(engine, 5)  # Don't set to Running

        # Set unwind low, but press is not Running
        _set_low_unwind(press, 100.0)

        rng = _make_rng()
        sc = MaterialSplice(start_time=0.0, rng=rng)
        t = engine.clock.sim_time

        for _ in range(20):
            t += engine.clock.dt
            sc.evaluate(t, engine.clock.dt, engine)

        assert sc.internal_phase == _Phase.MONITORING

    def test_triggers_splice_when_unwind_low_and_running(self) -> None:
        """Splice triggers when unwind <= 150 mm during Running."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)
        _set_low_unwind(press, 100.0)

        rng = _make_rng()
        sc = MaterialSplice(start_time=0.0, rng=rng)
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)  # activate -> MONITORING

        # One more tick should trigger the splice
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.internal_phase == _Phase.SPLICE

    def test_completes_after_splice_duration(self) -> None:
        """Scenario completes after splice_duration elapsed."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)
        _set_low_unwind(press, 100.0)

        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={"splice_duration_range": 5.0},  # Fixed 5s for test
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)  # activate -> MONITORING

        # Trigger splice
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.internal_phase == _Phase.SPLICE

        # Run past splice duration (5s = 50 ticks at 100ms)
        for _ in range(55):
            t += engine.clock.dt
            sc.evaluate(t, engine.clock.dt, engine)

        assert sc.is_completed


# ---------------------------------------------------------------------------
# Parameter tests
# ---------------------------------------------------------------------------


class TestMaterialSpliceParameters:
    """Parameter parsing and randomisation."""

    def test_default_parameters(self) -> None:
        rng = _make_rng()
        sc = MaterialSplice(start_time=0.0, rng=rng)
        assert sc.trigger_diameter == 150.0
        assert sc.refill_diameter == 1500.0
        assert 10.0 <= sc.splice_duration <= 30.0
        assert 50.0 <= sc.tension_spike <= 100.0
        assert 1.0 <= sc.tension_spike_duration <= 3.0
        assert 0.1 <= sc.reg_error_increase <= 0.3
        assert 10.0 <= sc.reg_error_duration <= 20.0
        assert 1.5 <= sc.waste_multiplier <= 2.5
        assert 0.05 <= sc.speed_dip_pct <= 0.10
        assert 5.0 <= sc.speed_recovery_s <= 10.0

    def test_custom_parameters(self) -> None:
        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={
                "trigger_diameter": 200.0,
                "refill_diameter": 1200.0,
                "splice_duration_range": 15.0,
                "tension_spike_range": 75.0,
            },
        )
        assert sc.trigger_diameter == 200.0
        assert sc.refill_diameter == 1200.0
        assert sc.splice_duration == 15.0
        assert sc.tension_spike == 75.0

    def test_duration_property(self) -> None:
        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={"splice_duration_range": 20.0},
        )
        assert sc.duration() == 20.0

    def test_different_seeds_produce_different_values(self) -> None:
        sc1 = MaterialSplice(start_time=0.0, rng=_make_rng(1))
        sc2 = MaterialSplice(start_time=0.0, rng=_make_rng(2))
        # At least some values should differ with different seeds
        assert not (
            sc1.splice_duration == sc2.splice_duration
            and sc1.tension_spike == sc2.tension_spike
            and sc1.reg_error_increase == sc2.reg_error_increase
        )


# ---------------------------------------------------------------------------
# Tension spike tests
# ---------------------------------------------------------------------------


class TestTensionSpike:
    """Tension model modifications during splice."""

    def test_tension_base_increased_during_spike(self) -> None:
        """Tension model _base increases by spike amount on splice trigger."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        # Record original tension base
        original_base = press._web_tension._base

        _set_low_unwind(press, 100.0)
        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={"tension_spike_range": 75.0},  # Fixed spike magnitude
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)  # -> MONITORING
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)  # -> SPLICE

        # Tension base should be increased by the spike amount
        assert press._web_tension._base == pytest.approx(
            original_base + 75.0, abs=0.01
        )

    def test_tension_base_restored_after_spike_duration(self) -> None:
        """Tension model _base restored after tension_spike_duration."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        original_base = press._web_tension._base
        _set_low_unwind(press, 100.0)

        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={
                "tension_spike_range": 75.0,
                "tension_spike_duration_range": 1.0,  # 1 second
                "splice_duration_range": 20.0,
            },
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)  # -> MONITORING
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)  # -> SPLICE

        # Run past tension spike duration (1s = 10 ticks at 100ms) + 1 extra
        for _ in range(12):
            t += engine.clock.dt
            sc.evaluate(t, engine.clock.dt, engine)

        # Tension base should be restored
        assert press._web_tension._base == pytest.approx(original_base, abs=0.01)

    def test_max_clamp_raised_if_spike_exceeds(self) -> None:
        """Max clamp raised to allow spike to exceed normal range."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        sig_cfg = press._signal_configs.get("web_tension")
        assert sig_cfg is not None
        original_max = sig_cfg.max_clamp

        _set_low_unwind(press, 100.0)
        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={"tension_spike_range": 100.0},  # Large spike
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)

        # max_clamp should be raised if spike+base exceeds original
        expected_peak = (
            press._web_tension._base + press._web_tension._gain * press.target_speed
        )
        if original_max is not None and expected_peak > original_max:
            assert sig_cfg.max_clamp is not None
            assert sig_cfg.max_clamp > original_max

    def test_max_clamp_restored_on_completion(self) -> None:
        """Max clamp is restored when scenario completes."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        sig_cfg = press._signal_configs.get("web_tension")
        assert sig_cfg is not None
        original_max = sig_cfg.max_clamp

        _set_low_unwind(press, 100.0)
        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={
                "tension_spike_range": 100.0,
                "splice_duration_range": 3.0,
            },
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)

        # Run until completion
        for _ in range(40):
            t += engine.clock.dt
            sc.evaluate(t, engine.clock.dt, engine)

        assert sc.is_completed
        assert sig_cfg.max_clamp == original_max


# ---------------------------------------------------------------------------
# Registration error tests
# ---------------------------------------------------------------------------


class TestRegistrationError:
    """Registration error modifications during splice."""

    def test_reg_error_offset_applied(self) -> None:
        """Both X and Y registration errors increase during splice."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        orig_x = press._reg_error_x._value
        orig_y = press._reg_error_y._value

        _set_low_unwind(press, 100.0)
        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={
                "reg_error_increase_range": 0.2,  # Fixed for testing
                "reg_error_duration_range": 15.0,
                "splice_duration_range": 20.0,
            },
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)

        # After splice activation, reg errors should be offset
        assert press._reg_error_x._value == pytest.approx(
            orig_x + 0.2, abs=0.01
        )
        assert press._reg_error_y._value == pytest.approx(
            orig_y + 0.2, abs=0.01
        )

    def test_reversion_rate_suppressed_during_splice(self) -> None:
        """Mean-reversion is suppressed during the registration error window."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        _set_low_unwind(press, 100.0)
        rng = _make_rng()
        sc = MaterialSplice(start_time=0.0, rng=rng, params={
            "splice_duration_range": 20.0,
        })

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)

        assert press._reg_error_x._reversion_rate == 0.0
        assert press._reg_error_y._reversion_rate == 0.0

    def test_reversion_rate_restored_after_reg_duration(self) -> None:
        """Mean-reversion rate is restored after registration error duration."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        orig_reversion_x = press._reg_error_x._reversion_rate
        orig_reversion_y = press._reg_error_y._reversion_rate

        _set_low_unwind(press, 100.0)
        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={
                "reg_error_duration_range": 2.0,  # Short for testing
                "splice_duration_range": 5.0,
            },
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)

        # Run past reg error duration (2s = 20 ticks) + margin
        for _ in range(25):
            t += engine.clock.dt
            sc.evaluate(t, engine.clock.dt, engine)

        assert press._reg_error_x._reversion_rate == pytest.approx(
            orig_reversion_x, abs=1e-6
        )
        assert press._reg_error_y._reversion_rate == pytest.approx(
            orig_reversion_y, abs=1e-6
        )

    def test_reg_error_both_axes_affected(self) -> None:
        """Both X and Y axes are affected, not just one."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        # Capture values BEFORE triggering splice
        pre_x = press._reg_error_x._value
        pre_y = press._reg_error_y._value

        _set_low_unwind(press, 100.0)
        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={"reg_error_increase_range": 0.25},
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)

        # Both axes should be offset from their pre-splice values
        assert press._reg_error_x._value == pytest.approx(
            pre_x + 0.25, abs=0.01
        )
        assert press._reg_error_y._value == pytest.approx(
            pre_y + 0.25, abs=0.01
        )


# ---------------------------------------------------------------------------
# Waste rate tests
# ---------------------------------------------------------------------------


class TestWasteRate:
    """Waste rate modifications during splice."""

    def test_waste_rate_increased(self) -> None:
        """Waste count rate increases during splice."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        original_rate = press._waste_count._rate

        _set_low_unwind(press, 100.0)
        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={"waste_multiplier_range": 2.0},  # Fixed for testing
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)

        assert press._waste_count._rate == pytest.approx(
            original_rate * 2.0, abs=0.001
        )

    def test_waste_rate_restored_on_complete(self) -> None:
        """Waste count rate returns to normal after scenario completes."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        original_rate = press._waste_count._rate

        _set_low_unwind(press, 100.0)
        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={
                "waste_multiplier_range": 2.0,
                "splice_duration_range": 3.0,
            },
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)

        # Run past completion
        for _ in range(40):
            t += engine.clock.dt
            sc.evaluate(t, engine.clock.dt, engine)

        assert sc.is_completed
        assert press._waste_count._rate == pytest.approx(original_rate, abs=0.001)


# ---------------------------------------------------------------------------
# Unwind diameter tests
# ---------------------------------------------------------------------------


class TestUnwindReset:
    """Unwind diameter refill on splice."""

    def test_unwind_resets_to_full_reel(self) -> None:
        """Unwind diameter resets to 1500 mm on splice trigger."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)
        _set_low_unwind(press, 100.0)

        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={"refill_diameter": 1500.0},
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)  # triggers splice

        assert press._unwind_diameter.value == pytest.approx(1500.0, abs=1.0)

    def test_custom_refill_diameter(self) -> None:
        """Unwind refills to custom diameter when configured."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)
        _set_low_unwind(press, 100.0)

        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={"refill_diameter": 1200.0},
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)

        assert press._unwind_diameter.value == pytest.approx(1200.0, abs=1.0)


# ---------------------------------------------------------------------------
# Speed dip tests
# ---------------------------------------------------------------------------


class TestSpeedDip:
    """Line speed dip during splice."""

    def test_speed_dip_started_on_splice(self) -> None:
        """Line speed ramp model starts a dip on splice trigger."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        # Get stable speed
        original_speed = press._line_speed_model.value
        assert original_speed > 0.0

        _set_low_unwind(press, 100.0)
        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={
                "speed_dip_pct_range": 0.10,  # 10% dip
                "splice_duration_range": 20.0,
            },
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)  # triggers splice

        # Ramp model should be heading toward dipped speed
        expected_end = original_speed * 0.90
        assert press._line_speed_model.end_value == pytest.approx(
            expected_end, rel=0.05
        )

    def test_speed_recovery_ramp_started(self) -> None:
        """Speed recovery ramp is started after the dip."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        _set_low_unwind(press, 100.0)
        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={
                "speed_dip_pct_range": 0.10,
                "splice_duration_range": 20.0,
                "speed_recovery_range": 5.0,
            },
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)

        # Run past the 2-second dip period (20 ticks) + margin
        for _ in range(25):
            t += engine.clock.dt
            sc.evaluate(t, engine.clock.dt, engine)

        # Recovery ramp should target the original target speed
        assert press._line_speed_model.end_value == pytest.approx(
            press.target_speed, rel=0.01
        )


# ---------------------------------------------------------------------------
# Machine state tests
# ---------------------------------------------------------------------------


class TestMachineStateDuringSplice:
    """Machine state stays Running throughout splice (flying splice)."""

    def test_machine_state_stays_running(self) -> None:
        """Press machine_state remains Running (2) during entire splice."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)
        _set_low_unwind(press, 100.0)

        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={"splice_duration_range": 5.0},
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)

        # Run through entire splice, checking state each tick
        for _ in range(60):
            t += engine.clock.dt
            sc.evaluate(t, engine.clock.dt, engine)
            state = int(press.state_machine.current_value)
            assert state == STATE_RUNNING, (
                f"Machine state changed to {state} during splice"
            )


# ---------------------------------------------------------------------------
# Full integration tests (scenario + engine)
# ---------------------------------------------------------------------------


class TestMaterialSpliceIntegration:
    """Full integration with the data engine."""

    def test_scenario_evaluates_in_engine_tick_loop(self) -> None:
        """Scenario evaluates correctly when called from the engine loop."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)
        _set_low_unwind(press, 100.0)

        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={"splice_duration_range": 5.0},
        )

        # Manually evaluate alongside engine ticks
        dt = engine.clock.dt
        for _ in range(70):
            t = engine.tick()
            sc.evaluate(t, dt, engine)

        assert sc.is_completed

    def test_all_effects_applied_and_restored(self) -> None:
        """All model modifications are applied during splice and restored after."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)

        # Capture all original values
        orig_tension_base = press._web_tension._base
        orig_waste_rate = press._waste_count._rate
        orig_rev_x = press._reg_error_x._reversion_rate
        orig_rev_y = press._reg_error_y._reversion_rate

        _set_low_unwind(press, 100.0)
        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={"splice_duration_range": 3.0},
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)

        # During splice: waste rate should be increased
        assert press._waste_count._rate > orig_waste_rate

        # Run to completion
        for _ in range(40):
            t += engine.clock.dt
            sc.evaluate(t, engine.clock.dt, engine)

        assert sc.is_completed

        # All values should be restored
        assert press._web_tension._base == pytest.approx(orig_tension_base, abs=0.01)
        assert press._waste_count._rate == pytest.approx(orig_waste_rate, abs=0.001)
        assert press._reg_error_x._reversion_rate == pytest.approx(
            orig_rev_x, abs=1e-6
        )
        assert press._reg_error_y._reversion_rate == pytest.approx(
            orig_rev_y, abs=1e-6
        )

    def test_unwind_depletes_after_refill(self) -> None:
        """After refill, unwind_diameter continues depleting normally."""
        engine, _store = _make_engine()
        press = _get_press(engine)
        _setup_running_press(engine, press)
        _set_low_unwind(press, 100.0)

        rng = _make_rng()
        sc = MaterialSplice(
            start_time=0.0, rng=rng,
            params={"splice_duration_range": 3.0},
        )

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        t += engine.clock.dt
        sc.evaluate(t, engine.clock.dt, engine)

        # After splice, unwind should be at refill level
        refill_val = press._unwind_diameter.value
        assert refill_val > 1000.0  # Should be ~1500

        # Run completion and several more engine ticks
        for _ in range(50):
            t += engine.clock.dt
            sc.evaluate(t, engine.clock.dt, engine)
        for _ in range(100):
            engine.tick()

        # Unwind should have depleted slightly (still well above trigger)
        # The depletion model consumes per speed*dt, so with Running speed
        # it should decrease
        final_val = press._unwind_diameter.value
        assert final_val < refill_val
