"""Tests for the web break scenario.

Verifies (PRD 5.3):
- Tension spikes above 600 N for 100-500 ms.
- Tension drops to 0 after the spike.
- Machine state transitions to Fault (4).
- Line speed drops to 0 via emergency deceleration (5-10 s).
- Coil 3 (press.web_break) is set during the event.
- Coil 1 (press.fault_active) is set during the event.
- Recovery clears coils and restores operation (Setup -> Running).
- Multi-phase lifecycle: SPIKE -> DECELERATION -> RECOVERY -> COMPLETED.
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
from factory_simulator.scenarios.web_break import WebBreak, _Phase
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


# ---------------------------------------------------------------------------
# Lifecycle and phase tests
# ---------------------------------------------------------------------------


class TestWebBreakLifecycle:
    """Scenario lifecycle: pending -> active -> phases -> completed."""

    def test_starts_pending(self) -> None:
        rng = _make_rng()
        sc = WebBreak(start_time=10.0, rng=rng)
        assert sc.phase == ScenarioPhase.PENDING
        assert not sc.is_active
        assert not sc.is_completed

    def test_activates_at_start_time(self) -> None:
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 5)

        rng = _make_rng()
        sc = WebBreak(start_time=0.0, rng=rng)
        t = engine.clock.sim_time
        sc.evaluate(t, engine.clock.dt, engine)
        assert sc.is_active
        assert sc.internal_phase == _Phase.SPIKE

    def test_transitions_through_all_phases(self) -> None:
        """Run a fast web break and verify all phases are traversed."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={
                "spike_duration_range": [0.1, 0.1],
                "decel_duration_range": [0.5, 0.5],
                "recovery_seconds": [1.0, 1.0],
            },
        )

        seen_phases: set[_Phase] = set()
        for _ in range(200):
            t = engine.tick()
            sc.evaluate(t, engine.clock.dt, engine)
            if sc.is_active:
                seen_phases.add(sc.internal_phase)
            if sc.is_completed:
                break

        assert sc.is_completed
        assert _Phase.SPIKE in seen_phases
        assert _Phase.DECELERATION in seen_phases
        assert _Phase.RECOVERY in seen_phases

    def test_duration_method(self) -> None:
        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={
                "spike_duration_range": [0.2, 0.2],
                "decel_duration_range": [7.0, 7.0],
                "recovery_seconds": [1800, 1800],
            },
        )
        assert sc.duration() == pytest.approx(0.2 + 7.0 + 1800.0)


# ---------------------------------------------------------------------------
# Tension spike tests
# ---------------------------------------------------------------------------


class TestWebBreakTensionSpike:
    """PRD 5.3 step 1: tension spikes above 600 N for 100-500 ms."""

    def test_tension_exceeds_600n_during_spike(self) -> None:
        """During the SPIKE phase, web_tension must exceed 600 N."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={
                "spike_tension_range": [650.0, 650.0],
                "spike_duration_range": [1.0, 1.0],  # 10 ticks — long enough for gen to fire
                "decel_duration_range": [5.0, 5.0],
                "recovery_seconds": [60.0, 60.0],
            },
        )

        # Add to engine so scenario affects the generator
        engine.scenario_engine.add_scenario(sc)

        # Run 5 ticks so the press generator fires (500ms interval)
        # while still in SPIKE phase (elapsed 0.5s < spike_duration 1.0s)
        for _ in range(5):
            engine.tick()

        assert sc.is_active
        assert sc.internal_phase == _Phase.SPIKE

        # Tension in the store should exceed 600 N
        tension = store.get_value("press.web_tension")
        assert isinstance(tension, float)
        assert tension > 600.0

    def test_spike_tension_within_configured_range(self) -> None:
        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={"spike_tension_range": [700.0, 750.0]},
        )
        assert 700.0 <= sc.spike_tension <= 750.0

    def test_spike_duration_within_configured_range(self) -> None:
        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={"spike_duration_range": [0.1, 0.5]},
        )
        assert 0.1 <= sc.spike_duration <= 0.5

    def test_spike_above_normal_max_clamp(self) -> None:
        """The spike must exceed the normal max_clamp (500 N)."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={
                "spike_tension_range": [700.0, 700.0],
                "spike_duration_range": [1.0, 1.0],  # long enough for gen to fire
                "recovery_seconds": [1.0, 1.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Run 5 ticks so the press generator fires during SPIKE
        for _ in range(5):
            engine.tick()

        assert sc.internal_phase == _Phase.SPIKE

        tension = store.get_value("press.web_tension")
        assert isinstance(tension, float)
        # Must exceed the normal max_clamp of 500 N
        assert tension > 500.0


# ---------------------------------------------------------------------------
# Tension drop tests
# ---------------------------------------------------------------------------


class TestWebBreakTensionDrop:
    """PRD 5.3 step 2: tension drops to 0 after the spike."""

    def test_tension_drops_after_spike(self) -> None:
        """After SPIKE ends, tension should be near 0."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={
                "spike_duration_range": [0.1, 0.1],  # 1 tick
                "decel_duration_range": [1.0, 1.0],
                "recovery_seconds": [60.0, 60.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Tick through the spike phase
        engine.tick()  # activates SPIKE
        assert sc.internal_phase == _Phase.SPIKE

        # Run a few more ticks into DECELERATION
        for _ in range(5):
            engine.tick()

        assert sc.internal_phase == _Phase.DECELERATION
        tension = store.get_value("press.web_tension")
        assert isinstance(tension, float)
        # Tension should be near 0 (gain=0, base=0)
        assert tension < 10.0


# ---------------------------------------------------------------------------
# Fault state and deceleration tests
# ---------------------------------------------------------------------------


class TestWebBreakFaultAndDecel:
    """PRD 5.3 steps 3-6: fault state, emergency decel, coils."""

    def test_forces_fault_state(self) -> None:
        """Press enters Fault state after the tension spike."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={
                "spike_duration_range": [0.1, 0.1],
                "decel_duration_range": [5.0, 5.0],
                "recovery_seconds": [60.0, 60.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Tick through spike (1 tick) then enter deceleration
        for _ in range(3):
            engine.tick()

        assert sc.internal_phase == _Phase.DECELERATION
        assert press.state_machine.current_state == "Fault"

    def test_web_break_coil_set(self) -> None:
        """Coil 3 (press.web_break) must be set during the event."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={
                "spike_duration_range": [0.1, 0.1],
                "decel_duration_range": [5.0, 5.0],
                "recovery_seconds": [60.0, 60.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)
        for _ in range(3):
            engine.tick()

        assert store.get_value("press.web_break") == 1.0

    def test_fault_active_coil_set(self) -> None:
        """Coil 1 (press.fault_active) must be set during the event."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={
                "spike_duration_range": [0.1, 0.1],
                "decel_duration_range": [5.0, 5.0],
                "recovery_seconds": [60.0, 60.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)
        for _ in range(3):
            engine.tick()

        assert store.get_value("press.fault_active") == 1.0

    def test_emergency_deceleration(self) -> None:
        """Line speed drops to 0 during deceleration phase."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 200)  # Let speed ramp up

        initial_speed = store.get_value("press.line_speed")
        assert isinstance(initial_speed, float)
        assert initial_speed > 0

        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={
                "spike_duration_range": [0.1, 0.1],
                "decel_duration_range": [2.0, 2.0],  # 2s decel for test
                "recovery_seconds": [60.0, 60.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # The RampModel advances elapsed by dt (0.1s) per gen fire, and
        # the gen fires every 500ms (5 ticks). For a 2.0s decel ramp we
        # need >=20 gen fires = 100 ticks, plus margin.
        for _ in range(150):
            engine.tick()

        # After decel, speed should be near 0
        final_speed = store.get_value("press.line_speed")
        assert isinstance(final_speed, float)
        assert final_speed < initial_speed * 0.1  # At least 90% reduction

    def test_decel_duration_within_range(self) -> None:
        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={"decel_duration_range": [5.0, 10.0]},
        )
        assert 5.0 <= sc.decel_duration <= 10.0


# ---------------------------------------------------------------------------
# Recovery tests
# ---------------------------------------------------------------------------


class TestWebBreakRecovery:
    """PRD 5.3 step 7: recovery clears coils, restores operation."""

    def test_coils_clear_on_recovery(self) -> None:
        """After recovery, both web_break and fault_active coils must clear."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={
                "spike_duration_range": [0.1, 0.1],
                "decel_duration_range": [0.5, 0.5],
                "recovery_seconds": [1.0, 1.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(200):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert store.get_value("press.web_break") == 0.0
        assert store.get_value("press.fault_active") == 0.0

    def test_state_transitions_to_setup(self) -> None:
        """After recovery, press should be in Setup state."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={
                "spike_duration_range": [0.1, 0.1],
                "decel_duration_range": [0.5, 0.5],
                "recovery_seconds": [1.0, 1.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(200):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert press.state_machine.current_state == "Setup"

    def test_recovery_duration_within_range(self) -> None:
        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={"recovery_seconds": [900, 3600]},
        )
        assert 900 <= sc.recovery_duration <= 3600

    def test_tension_model_restored_after_complete(self) -> None:
        """Tension model parameters must be restored to originals."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        # Save original values
        original_base = press._web_tension._base
        original_gain = press._web_tension._gain
        sig_cfg = press._signal_configs.get("web_tension")
        original_max_clamp = sig_cfg.max_clamp if sig_cfg else None

        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={
                "spike_duration_range": [0.1, 0.1],
                "decel_duration_range": [0.5, 0.5],
                "recovery_seconds": [1.0, 1.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        for _ in range(200):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed

        # Verify model parameters restored
        assert press._web_tension._base == original_base
        assert press._web_tension._gain == original_gain
        if sig_cfg is not None:
            assert sig_cfg.max_clamp == original_max_clamp

    def test_max_clamp_restored_after_spike(self) -> None:
        """max_clamp should be raised during spike and restored by recovery."""
        engine, store = _make_engine()
        press = _get_press(engine)
        press.state_machine.force_state("Running")
        _run_ticks(engine, 10)

        sig_cfg = press._signal_configs.get("web_tension")
        assert sig_cfg is not None
        original_clamp = sig_cfg.max_clamp

        rng = _make_rng()
        sc = WebBreak(
            start_time=0.0,
            rng=rng,
            params={
                "spike_duration_range": [0.3, 0.3],
                "decel_duration_range": [0.5, 0.5],
                "recovery_seconds": [60.0, 60.0],
            },
        )

        engine.scenario_engine.add_scenario(sc)

        # Tick once to enter spike phase -- max_clamp should be raised
        engine.tick()
        assert sig_cfg.max_clamp > original_clamp  # type: ignore[operator]

        # Complete the scenario
        for _ in range(700):
            engine.tick()
            if sc.is_completed:
                break

        assert sc.is_completed
        assert sig_cfg.max_clamp == original_clamp


# ---------------------------------------------------------------------------
# Parameter defaults
# ---------------------------------------------------------------------------


class TestWebBreakDefaults:
    """Verify default parameter ranges match PRD."""

    def test_default_recovery_range(self) -> None:
        """Default recovery: 15-60 min (900-3600 s)."""
        rng = _make_rng()
        sc = WebBreak(start_time=0.0, rng=rng)
        assert 900 <= sc.recovery_duration <= 3600

    def test_default_spike_tension_range(self) -> None:
        """Default spike tension: 650-800 N (well above 600 threshold)."""
        rng = _make_rng()
        sc = WebBreak(start_time=0.0, rng=rng)
        assert sc.spike_tension >= 600.0

    def test_default_spike_duration_range(self) -> None:
        """Default spike duration: 100-500 ms."""
        rng = _make_rng()
        sc = WebBreak(start_time=0.0, rng=rng)
        assert 0.1 <= sc.spike_duration <= 0.5

    def test_default_decel_range(self) -> None:
        """Default decel: 5-10 s."""
        rng = _make_rng()
        sc = WebBreak(start_time=0.0, rng=rng)
        assert 5.0 <= sc.decel_duration <= 10.0
