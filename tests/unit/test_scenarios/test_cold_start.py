"""Tests for the cold start energy spike scenario.

Verifies (PRD 5.10):
- Trigger: state transition from Off/Idle to Setup/Running after >30 min idle.
- energy.line_power spikes to 150-200% of normal running power for 2-5 s.
- press.main_drive_current spikes to 150-300% of running current.
- After the spike, values settle to normal.
- No trigger if idle duration < threshold.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.generators.energy import EnergyGenerator
from factory_simulator.generators.press import PressGenerator
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.cold_start import ColdStart, _Phase
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


def _get_press(engine: DataEngine) -> PressGenerator:
    """Find the press generator."""
    for gen in engine.generators:
        if isinstance(gen, PressGenerator):
            return gen
    raise RuntimeError("Press generator not found")


def _get_energy(engine: DataEngine) -> EnergyGenerator:
    """Find the energy generator."""
    for gen in engine.generators:
        if isinstance(gen, EnergyGenerator):
            return gen
    raise RuntimeError("Energy generator not found")


def _run_ticks(engine: DataEngine, n: int) -> float:
    """Run n ticks and return final sim_time."""
    t = 0.0
    for _ in range(n):
        t = engine.tick()
    return t


def _make_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def _normal_running_power(energy: EnergyGenerator, target_speed: float) -> float:
    """Calculate the expected normal running power."""
    return energy._line_power._base + energy._line_power._gain * target_speed


def _normal_running_current(press: PressGenerator) -> float:
    """Calculate the expected normal running current."""
    return (
        press._main_drive_current._base
        + press._main_drive_current._gain * press.target_speed
    )


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestColdStartLifecycle:
    """Scenario lifecycle: pending -> active (monitoring) -> spike -> completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = ColdStart(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_into_monitoring(self) -> None:
        """Scenario activates at start_time and enters MONITORING phase."""
        engine, store = _make_engine()
        # Press starts in Idle by default
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = ColdStart(start_time=0.0, rng=rng, params={"idle_threshold_s": 1.0})

        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)

        assert sc.is_active
        assert sc.internal_phase == _Phase.MONITORING

    def test_transitions_to_spike_on_trigger(self) -> None:
        """Cold start enters SPIKE when idle threshold exceeded and state changes."""
        engine, store = _make_engine()
        press = _get_press(engine)

        # Start in Idle
        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 1.0,  # 1s threshold for fast test
                "spike_duration_range": [3.0, 3.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Run 20 ticks (2.0s > 1.0s threshold) while idle
        _run_ticks(engine, 20)
        assert sc.internal_phase == _Phase.MONITORING

        # Transition to Running
        press.state_machine.force_state("Running")
        engine.tick()

        assert sc.internal_phase == _Phase.SPIKE

    def test_completes_after_spike_duration(self) -> None:
        """Scenario completes when spike_elapsed > spike_duration."""
        engine, store = _make_engine()
        press = _get_press(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 0.5,
                "spike_duration_range": [1.0, 1.0],  # 1s spike
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Idle for 10 ticks (1.0s > 0.5s threshold)
        _run_ticks(engine, 10)

        # Trigger
        press.state_machine.force_state("Running")

        # Run until completed (spike is 1.0s = 10 ticks + a few extra)
        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

    def test_duration_method(self) -> None:
        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={"spike_duration_range": [3.5, 3.5]},
        )
        assert sc.duration() == pytest.approx(3.5)


# ---------------------------------------------------------------------------
# Trigger condition tests
# ---------------------------------------------------------------------------


class TestColdStartTrigger:
    """PRD 5.10: trigger on Off/Idle -> Setup/Running after >30 min idle."""

    def test_no_trigger_if_idle_too_short(self) -> None:
        """Spike should NOT fire if idle duration < threshold."""
        engine, store = _make_engine()
        press = _get_press(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 5.0,  # 5s threshold
                "spike_duration_range": [2.0, 2.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Only idle for 2.0s (20 ticks) < 5.0s threshold
        _run_ticks(engine, 20)

        # Transition to Running
        press.state_machine.force_state("Running")
        engine.tick()

        # Should still be monitoring, not spiking
        assert sc.internal_phase == _Phase.MONITORING

    def test_trigger_from_off_state(self) -> None:
        """Trigger should fire from Off state, not just Idle."""
        engine, store = _make_engine()
        press = _get_press(engine)

        press.state_machine.force_state("Off")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 1.0,
                "spike_duration_range": [2.0, 2.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Idle for 20 ticks (2.0s > 1.0s threshold) in Off state
        _run_ticks(engine, 20)

        # Transition to Setup
        press.state_machine.force_state("Setup")
        engine.tick()

        assert sc.internal_phase == _Phase.SPIKE

    def test_trigger_to_setup_state(self) -> None:
        """Trigger should fire when transitioning to Setup, not just Running."""
        engine, store = _make_engine()
        press = _get_press(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 1.0,
                "spike_duration_range": [2.0, 2.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        _run_ticks(engine, 20)

        press.state_machine.force_state("Setup")
        engine.tick()

        assert sc.internal_phase == _Phase.SPIKE

    def test_no_trigger_from_fault_state(self) -> None:
        """Transition from Fault to Running should NOT trigger spike."""
        engine, store = _make_engine()
        press = _get_press(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 1.0,
                "spike_duration_range": [2.0, 2.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Idle for 20 ticks (2.0s > threshold)
        _run_ticks(engine, 20)

        # Go through Fault first (resets idle tracking)
        press.state_machine.force_state("Fault")
        engine.tick()

        # Then to Running -- should NOT trigger because prev was Fault
        press.state_machine.force_state("Running")
        engine.tick()

        assert sc.internal_phase == _Phase.MONITORING

    def test_idle_tracking_resets_after_non_trigger(self) -> None:
        """If transition happens but idle was too short, idle tracking resets."""
        engine, store = _make_engine()
        press = _get_press(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 5.0,
                "spike_duration_range": [2.0, 2.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        # Short idle (2.0s < 5.0s threshold)
        _run_ticks(engine, 20)

        # Transition to Running (no trigger -- too short)
        press.state_machine.force_state("Running")
        engine.tick()
        assert sc.internal_phase == _Phase.MONITORING

        # Go back to idle for a long time
        press.state_machine.force_state("Idle")
        _run_ticks(engine, 60)  # 6.0s > 5.0s threshold

        # Now transition to Running -- should trigger
        press.state_machine.force_state("Running")
        engine.tick()
        assert sc.internal_phase == _Phase.SPIKE


# ---------------------------------------------------------------------------
# Spike magnitude tests
# ---------------------------------------------------------------------------


class TestColdStartSpikeMagnitude:
    """PRD 5.10: energy 150-200% and current 150-300% of normal running."""

    def test_power_base_set_to_spike_level(self) -> None:
        """Energy model _base should be set to spike_power during spike."""
        engine, store = _make_engine()
        press = _get_press(engine)
        energy = _get_energy(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        normal_power = _normal_running_power(energy, press.target_speed)
        power_mult = 1.75

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 0.5,
                "spike_duration_range": [5.0, 5.0],
                "power_multiplier_range": [power_mult, power_mult],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        _run_ticks(engine, 10)  # 1.0s > 0.5s threshold

        # Save original base
        original_base = energy._line_power._base

        # Trigger
        press.state_machine.force_state("Running")
        engine.tick()

        assert sc.internal_phase == _Phase.SPIKE
        expected_spike_base = normal_power * power_mult
        assert energy._line_power._base == pytest.approx(
            expected_spike_base, rel=1e-6
        )

        # Original base was different
        assert original_base != pytest.approx(expected_spike_base, rel=1e-6)

    def test_current_base_set_to_spike_level(self) -> None:
        """Press main_drive_current _base should spike during cold start."""
        engine, store = _make_engine()
        press = _get_press(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        normal_current = _normal_running_current(press)
        current_mult = 2.5

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 0.5,
                "spike_duration_range": [5.0, 5.0],
                "current_multiplier_range": [current_mult, current_mult],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        _run_ticks(engine, 10)

        original_base = press._main_drive_current._base

        press.state_machine.force_state("Running")
        engine.tick()

        assert sc.internal_phase == _Phase.SPIKE
        expected_spike_base = normal_current * current_mult
        assert press._main_drive_current._base == pytest.approx(
            expected_spike_base, rel=1e-6
        )
        assert original_base != pytest.approx(expected_spike_base, rel=1e-6)

    def test_power_visible_in_store(self) -> None:
        """Energy spike should be visible in the signal store value."""
        engine, store = _make_engine()
        press = _get_press(engine)
        energy = _get_energy(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        normal_power = _normal_running_power(energy, press.target_speed)

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 0.5,
                "spike_duration_range": [5.0, 5.0],
                "power_multiplier_range": [1.8, 1.8],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        _run_ticks(engine, 10)

        # Record pre-spike power
        pre_spike_power = float(store.get_value("energy.line_power", 0.0))

        press.state_machine.force_state("Running")

        # Run enough ticks for energy generator to fire (every 1000ms = 10 ticks)
        _run_ticks(engine, 15)

        spiked_power = float(store.get_value("energy.line_power", 0.0))

        # Spiked power should be well above pre-spike (idle base load)
        assert spiked_power > pre_spike_power * 2.0
        # And close to the expected spike level (tolerant of noise)
        expected_spike = normal_power * 1.8
        assert abs(spiked_power - expected_spike) < expected_spike * 0.15

    def test_current_visible_in_store(self) -> None:
        """Current spike should be visible in the signal store value."""
        engine, store = _make_engine()
        press = _get_press(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        normal_current = _normal_running_current(press)

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 0.5,
                "spike_duration_range": [5.0, 5.0],
                "current_multiplier_range": [2.0, 2.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        _run_ticks(engine, 10)

        pre_spike_current = float(
            store.get_value("press.main_drive_current", 0.0)
        )

        press.state_machine.force_state("Running")

        # Press generator fires every 500ms = 5 ticks
        _run_ticks(engine, 10)

        spiked_current = float(
            store.get_value("press.main_drive_current", 0.0)
        )

        assert spiked_current > pre_spike_current * 2.0
        expected_spike = normal_current * 2.0
        assert abs(spiked_current - expected_spike) < expected_spike * 0.15

    def test_max_clamp_raised_for_spike(self) -> None:
        """Max clamp should be raised to accommodate the spike."""
        engine, store = _make_engine()
        press = _get_press(engine)
        energy = _get_energy(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        power_cfg = energy._signal_configs.get("line_power")
        assert power_cfg is not None
        original_max = power_cfg.max_clamp

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 0.5,
                "spike_duration_range": [5.0, 5.0],
                # 200% of ~110 kW = ~220 kW > 200 kW max_clamp
                "power_multiplier_range": [2.0, 2.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        _run_ticks(engine, 10)

        press.state_machine.force_state("Running")
        engine.tick()

        # Max clamp should be raised during spike
        assert power_cfg.max_clamp is not None
        assert original_max is not None
        assert power_cfg.max_clamp > original_max


# ---------------------------------------------------------------------------
# Recovery / settle tests
# ---------------------------------------------------------------------------


class TestColdStartRecovery:
    """PRD 5.10 step 2: after the spike, values settle to normal."""

    def test_power_base_restored_after_spike(self) -> None:
        """Energy model _base should return to original after spike ends."""
        engine, store = _make_engine()
        press = _get_press(engine)
        energy = _get_energy(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        original_base = energy._line_power._base

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 0.5,
                "spike_duration_range": [1.0, 1.0],
                "power_multiplier_range": [1.8, 1.8],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        _run_ticks(engine, 10)

        press.state_machine.force_state("Running")

        # Run past spike duration
        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert energy._line_power._base == pytest.approx(original_base, rel=1e-6)

    def test_current_base_restored_after_spike(self) -> None:
        """Press current _base should return to original after spike ends."""
        engine, store = _make_engine()
        press = _get_press(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        original_base = press._main_drive_current._base

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 0.5,
                "spike_duration_range": [1.0, 1.0],
                "current_multiplier_range": [2.5, 2.5],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        _run_ticks(engine, 10)

        press.state_machine.force_state("Running")

        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert press._main_drive_current._base == pytest.approx(
            original_base, rel=1e-6
        )

    def test_max_clamp_restored_after_spike(self) -> None:
        """Max clamp should be restored to original after spike ends."""
        engine, store = _make_engine()
        press = _get_press(engine)
        energy = _get_energy(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        power_cfg = energy._signal_configs.get("line_power")
        assert power_cfg is not None
        original_max = power_cfg.max_clamp

        current_cfg = press._signal_configs.get("main_drive_current")
        assert current_cfg is not None
        original_current_max = current_cfg.max_clamp

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 0.5,
                "spike_duration_range": [1.0, 1.0],
                "power_multiplier_range": [2.0, 2.0],
                "current_multiplier_range": [3.0, 3.0],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        _run_ticks(engine, 10)

        press.state_machine.force_state("Running")

        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert power_cfg.max_clamp == original_max
        assert current_cfg.max_clamp == original_current_max

    def test_store_value_settles_after_spike(self) -> None:
        """After spike completes, store power should be near idle base load."""
        engine, store = _make_engine()
        press = _get_press(engine)
        energy = _get_energy(engine)

        press.state_machine.force_state("Idle")
        _run_ticks(engine, 5)

        normal_power = _normal_running_power(energy, press.target_speed)

        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "idle_threshold_s": 0.5,
                "spike_duration_range": [1.0, 1.0],
                "power_multiplier_range": [1.8, 1.8],
            },
        )
        engine.scenario_engine.add_scenario(sc)

        _run_ticks(engine, 10)

        press.state_machine.force_state("Running")

        # Run past spike
        for _ in range(20):
            engine.tick()
            if sc.is_completed:
                break
        assert sc.is_completed

        # Run a few more ticks for energy generator to fire with restored base
        _run_ticks(engine, 15)

        power_after = float(store.get_value("energy.line_power", 0.0))

        # After spike, the press is just starting to ramp up, so power
        # should be much lower than the spike value.  Speed is still near
        # zero, so power should be near base load (10 kW) + small speed
        # contribution + noise.
        spike_power = normal_power * 1.8
        assert power_after < spike_power * 0.5


# ---------------------------------------------------------------------------
# Parameter defaults
# ---------------------------------------------------------------------------


class TestColdStartDefaults:
    """Verify default parameter ranges match PRD 5.10."""

    def test_default_spike_duration_range(self) -> None:
        """Default spike duration: 2-5 seconds."""
        rng = _make_rng()
        sc = ColdStart(start_time=0.0, rng=rng)
        assert 2.0 <= sc.spike_duration <= 5.0

    def test_default_power_multiplier_range(self) -> None:
        """Default power multiplier: 1.5-2.0 (150-200%)."""
        rng = _make_rng()
        sc = ColdStart(start_time=0.0, rng=rng)
        assert 1.5 <= sc.power_multiplier <= 2.0

    def test_default_current_multiplier_range(self) -> None:
        """Default current multiplier: 1.5-3.0 (150-300%)."""
        rng = _make_rng()
        sc = ColdStart(start_time=0.0, rng=rng)
        assert 1.5 <= sc.current_multiplier <= 3.0

    def test_default_idle_threshold(self) -> None:
        """Default idle threshold: 1800.0 seconds (30 min)."""
        rng = _make_rng()
        sc = ColdStart(start_time=0.0, rng=rng)
        assert sc.idle_threshold == pytest.approx(1800.0)

    def test_fixed_params_are_deterministic(self) -> None:
        """Fixed parameter ranges should produce exact values."""
        rng = _make_rng()
        sc = ColdStart(
            start_time=0.0,
            rng=rng,
            params={
                "spike_duration_range": [3.0, 3.0],
                "power_multiplier_range": [1.7, 1.7],
                "current_multiplier_range": [2.2, 2.2],
                "idle_threshold_s": 900.0,
            },
        )
        assert sc.spike_duration == pytest.approx(3.0)
        assert sc.power_multiplier == pytest.approx(1.7)
        assert sc.current_multiplier == pytest.approx(2.2)
        assert sc.idle_threshold == pytest.approx(900.0)
