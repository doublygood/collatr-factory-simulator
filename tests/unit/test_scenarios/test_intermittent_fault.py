"""Tests for the intermittent fault scenario (PRD 5.17).

Verifies:
- Priority is "background"
- Phase 1/2/3 transitions occur at correct elapsed times
- Spikes fire during each phase (bearing, electrical, sensor, pneumatic)
- Spike effects are correctly applied to and removed from generator models
- Sensor subtype writes sentinel values via post_gen_inject
- phase3_transition=False subtypes complete after Phase 2 (no Phase 3)
- phase3_transition=True subtypes remain active in permanent Phase 3
- spike_count increments for each spike
- duration() returns phase1 + phase2 duration
- Ground truth logs spike and phase-transition events
- Auto-scheduling via ScenarioEngine (one instance per enabled subtype)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.generators.coder import CoderGenerator
from factory_simulator.generators.press import PressGenerator
from factory_simulator.generators.vibration import VibrationGenerator
from factory_simulator.scenarios.base import ScenarioPhase
from factory_simulator.scenarios.intermittent_fault import IntermittentFault
from factory_simulator.store import SignalStore

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
    if config.scenarios.contextual_anomaly is not None:
        config.scenarios.contextual_anomaly.enabled = False
    if config.scenarios.intermittent_fault is not None:
        config.scenarios.intermittent_fault.enabled = False
    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    return DataEngine(config, store, clock)


def _get_press(engine: DataEngine) -> PressGenerator:
    for gen in engine.generators:
        if isinstance(gen, PressGenerator):
            return gen
    raise RuntimeError("PressGenerator not found")


def _get_vibration(engine: DataEngine) -> VibrationGenerator:
    for gen in engine.generators:
        if isinstance(gen, VibrationGenerator):
            return gen
    raise RuntimeError("VibrationGenerator not found")


def _get_coder(engine: DataEngine) -> CoderGenerator:
    for gen in engine.generators:
        if isinstance(gen, CoderGenerator):
            return gen
    raise RuntimeError("CoderGenerator not found")


def _make_bearing_fault(
    start_time: float = 0.0,
    rng: np.random.Generator | None = None,
    *,
    phase1_hours: float = 1.0,
    phase2_hours: float = 0.5,
    phase1_freq: float = 2.0,   # spikes per day
    phase2_freq: float = 10.0,
    spike_dur: float = 5.0,      # seconds
    phase3: bool = True,
) -> IntermittentFault:
    if rng is None:
        rng = _make_rng()
    return IntermittentFault(
        start_time=start_time,
        rng=rng,
        params={
            "subtype": "bearing",
            "phase3_transition": phase3,
            "affected_signals": [
                "vibration.main_drive_x",
                "vibration.main_drive_y",
                "vibration.main_drive_z",
            ],
            "phase1_duration_hours": [phase1_hours, phase1_hours],
            "phase1_frequency_per_day": [phase1_freq, phase1_freq],
            "phase1_spike_duration_s": [spike_dur, spike_dur],
            "phase2_duration_hours": [phase2_hours, phase2_hours],
            "phase2_frequency_per_day": [phase2_freq, phase2_freq],
            "phase2_spike_duration_s": [spike_dur, spike_dur],
            "spike_magnitude": [20.0, 20.0],
        },
    )


def _make_electrical_fault(
    start_time: float = 0.0,
    rng: np.random.Generator | None = None,
    *,
    phase1_hours: float = 1.0,
    phase2_hours: float = 0.5,
    phase3: bool = True,
) -> IntermittentFault:
    if rng is None:
        rng = _make_rng()
    return IntermittentFault(
        start_time=start_time,
        rng=rng,
        params={
            "subtype": "electrical",
            "phase3_transition": phase3,
            "affected_signals": ["press.main_drive_current"],
            "phase1_duration_hours": [phase1_hours, phase1_hours],
            "phase1_frequency_per_day": [2.0, 2.0],
            "phase1_spike_duration_s": [5.0, 5.0],
            "phase2_duration_hours": [phase2_hours, phase2_hours],
            "phase2_frequency_per_day": [10.0, 10.0],
            "phase2_spike_duration_s": [5.0, 5.0],
            "spike_magnitude_pct": [30.0, 30.0],
        },
    )


def _make_sensor_fault(
    start_time: float = 0.0,
    rng: np.random.Generator | None = None,
) -> IntermittentFault:
    if rng is None:
        rng = _make_rng()
    return IntermittentFault(
        start_time=start_time,
        rng=rng,
        params={
            "subtype": "sensor",
            "phase3_transition": True,
            "affected_signals": ["press.dryer_temp_zone_1"],
            "phase1_duration_hours": [1.0, 1.0],
            "phase1_frequency_per_day": [2.0, 2.0],
            "phase1_spike_duration_s": [5.0, 5.0],
            "phase2_duration_hours": [0.5, 0.5],
            "phase2_frequency_per_day": [10.0, 10.0],
            "phase2_spike_duration_s": [5.0, 5.0],
            "spike_magnitude": [6553.5, 6553.5],
        },
    )


def _make_pneumatic_fault(
    start_time: float = 0.0,
    rng: np.random.Generator | None = None,
) -> IntermittentFault:
    if rng is None:
        rng = _make_rng()
    return IntermittentFault(
        start_time=start_time,
        rng=rng,
        params={
            "subtype": "pneumatic",
            "phase3_transition": False,
            "affected_signals": ["coder.ink_pressure"],
            "phase1_duration_hours": [1.0, 1.0],
            "phase1_frequency_per_day": [2.0, 2.0],
            "phase1_spike_duration_s": [5.0, 5.0],
            "phase2_duration_hours": [0.5, 0.5],
            "phase2_frequency_per_day": [10.0, 10.0],
            "phase2_spike_duration_s": [5.0, 5.0],
            "spike_magnitude": [0.0, 0.0],
        },
    )


def _run_ticks(engine: DataEngine, n: int, dt: float = 0.1) -> None:
    """Advance the engine by n ticks of size dt."""
    for _ in range(n):
        engine.tick()


def _run_until_elapsed(
    engine: DataEngine,
    scenario: IntermittentFault,
    target_elapsed: float,
    dt: float = 0.1,
) -> None:
    """Run engine ticks until scenario.elapsed >= target_elapsed."""
    max_ticks = int(target_elapsed / dt) + 200
    ticks = 0
    while scenario.elapsed < target_elapsed and ticks < max_ticks:
        engine.tick()
        ticks += 1


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIntermittentFaultBasics:
    def test_priority_is_background(self) -> None:
        fault = _make_bearing_fault()
        assert fault.priority == "background"

    def test_subtype_stored(self) -> None:
        fault = _make_bearing_fault()
        assert fault.subtype == "bearing"

        fault2 = _make_electrical_fault()
        assert fault2.subtype == "electrical"

    def test_phase1_duration_exact_when_range_is_single_value(self) -> None:
        fault = _make_bearing_fault(phase1_hours=2.0)
        assert fault.phase1_duration_s == pytest.approx(2.0 * 3600.0)

    def test_phase2_duration_exact_when_range_is_single_value(self) -> None:
        fault = _make_bearing_fault(phase2_hours=1.0)
        assert fault.phase2_duration_s == pytest.approx(1.0 * 3600.0)

    def test_duration_returns_sum_of_phases(self) -> None:
        fault = _make_bearing_fault(phase1_hours=2.0, phase2_hours=1.0)
        assert fault.duration() == pytest.approx(3.0 * 3600.0)

    def test_spike_queue_not_empty(self) -> None:
        # 2 spikes/day x 1 hour phase: ~0.08 spikes expected; Poisson is random,
        # but with 2/day frequency over 24h, mean = 2 events.  Over 1h, P(0) ≈ 92%.
        # Use a higher frequency to guarantee spikes.
        fault = _make_bearing_fault(
            phase1_hours=24.0, phase1_freq=20.0, phase2_hours=24.0
        )
        assert len(fault._spike_queue) > 0

    def test_spike_queue_sorted_by_start(self) -> None:
        fault = _make_bearing_fault(phase1_hours=24.0, phase1_freq=10.0)
        starts = [s for s, _ in fault._spike_queue]
        assert starts == sorted(starts)

    def test_initial_phase_is_1(self) -> None:
        fault = _make_bearing_fault()
        assert fault.current_phase == 1

    def test_initial_spike_count_is_0(self) -> None:
        fault = _make_bearing_fault()
        assert fault.spike_count == 0

    def test_initial_in_spike_is_false(self) -> None:
        fault = _make_bearing_fault()
        assert fault.in_spike is False


class TestIntermittentFaultBearing:
    """Bearing subtype: vibration model _target is modified during spike."""

    def test_vibration_spikes_during_spike(self) -> None:
        """Vibration target set to spike_magnitude while in spike."""
        engine = _make_engine(seed=10)
        vib = _get_vibration(engine)

        # Warm up generators before injecting the scenario
        engine.tick()

        # Create scenario starting now, with guaranteed spike at dt=0
        # We force a spike by injecting a spike directly into the queue.
        fault = _make_bearing_fault(start_time=0.0)
        fault._spike_queue = [(0.05, 10.0)]  # spike starts at 0.05s elapsed
        engine.scenario_engine.add_scenario(fault)

        # Activate the scenario
        engine.tick()  # sim_time = 0.1; elapsed = 0.1 > 0.05 → spike starts

        assert fault.in_spike is True
        # Check vibration target changed to spike magnitude
        assert vib._models["main_drive_x"]._target == pytest.approx(20.0)

    def test_vibration_restores_after_spike(self) -> None:
        """Vibration target restored to baseline when spike ends."""
        engine = _make_engine(seed=11)
        vib = _get_vibration(engine)
        engine.tick()
        baseline = vib._models["main_drive_x"]._target

        fault = _make_bearing_fault(start_time=0.0)
        fault._spike_queue = [(0.05, 0.15)]  # spike lasts 0.1s
        engine.scenario_engine.add_scenario(fault)

        engine.tick()  # elapsed = 0.1 → spike starts
        assert fault.in_spike is True

        engine.tick()  # elapsed = 0.2 > 0.15 → spike ends
        assert fault.in_spike is False
        assert vib._models["main_drive_x"]._target == pytest.approx(baseline)

    def test_phase_transitions_at_correct_times(self) -> None:
        """current_phase changes from 1→2→3 at the configured durations."""
        # Use very short durations for test speed
        fault = _make_bearing_fault(
            start_time=0.0,
            phase1_hours=0.0,   # 0 hours = 0 seconds → immediately phase 2
            phase2_hours=0.0,   # immediately phase 3
            phase3=True,
        )
        engine = _make_engine(seed=12)
        engine.scenario_engine.add_scenario(fault)

        # Tick once to activate; with 0-second phases it goes straight to phase 3
        engine.tick()
        engine.tick()
        assert fault.current_phase == 3
        assert fault.phase3_active is True

    def test_phase2_transition_logged(self) -> None:
        """Phase 2 transition increments _phase2_transition_logged flag."""
        fault = _make_bearing_fault(
            start_time=0.0, phase1_hours=0.0, phase2_hours=1.0
        )
        engine = _make_engine(seed=13)
        engine.scenario_engine.add_scenario(fault)

        engine.tick()
        engine.tick()
        assert fault._phase2_transition_logged is True
        assert fault.current_phase == 2


class TestIntermittentFaultElectrical:
    """Electrical subtype: press.main_drive_current._base spiked by %."""

    def test_current_increases_during_spike(self) -> None:
        engine = _make_engine(seed=20)
        press = _get_press(engine)
        engine.tick()
        baseline = press._main_drive_current._base

        fault = _make_electrical_fault(start_time=0.0)
        fault._spike_queue = [(0.05, 10.0)]
        engine.scenario_engine.add_scenario(fault)
        engine.tick()

        assert fault.in_spike is True
        expected = baseline * (1.0 + 30.0 / 100.0)
        assert press._main_drive_current._base == pytest.approx(expected)

    def test_current_restores_after_spike(self) -> None:
        engine = _make_engine(seed=21)
        press = _get_press(engine)
        engine.tick()
        baseline = press._main_drive_current._base

        fault = _make_electrical_fault(start_time=0.0)
        fault._spike_queue = [(0.05, 0.15)]
        engine.scenario_engine.add_scenario(fault)
        engine.tick()  # start spike
        assert fault.in_spike is True
        engine.tick()  # end spike
        assert fault.in_spike is False
        assert press._main_drive_current._base == pytest.approx(baseline)


class TestIntermittentFaultSensor:
    """Sensor subtype: sentinel value written via post_gen_inject."""

    def test_sentinel_written_during_spike(self) -> None:
        engine = _make_engine(seed=30)
        store = engine.store
        engine.tick()

        fault = _make_sensor_fault(start_time=0.0)
        fault._spike_queue = [(0.05, 10.0)]
        engine.scenario_engine.add_scenario(fault)
        engine.tick()

        assert fault.in_spike is True
        # Temperature signal → sentinel = 6553.5
        assert store.get_value("press.dryer_temp_zone_1", 0.0) == pytest.approx(6553.5)

    def test_no_model_modification_for_sensor(self) -> None:
        """Sensor subtype does NOT modify generator model targets."""
        engine = _make_engine(seed=31)
        vib = _get_vibration(engine)
        engine.tick()
        baseline_vib = vib._models["main_drive_x"]._target

        fault = _make_sensor_fault(start_time=0.0)
        fault._spike_queue = [(0.05, 10.0)]
        engine.scenario_engine.add_scenario(fault)
        engine.tick()

        # Vibration model should be unchanged (sensor only affects its own signal)
        assert vib._models["main_drive_x"]._target == pytest.approx(baseline_vib)


class TestIntermittentFaultPneumatic:
    """Pneumatic subtype: coder ink_pressure._target drops to 0 during spike."""

    def test_pressure_target_zero_during_spike(self) -> None:
        engine = _make_engine(seed=40)
        coder = _get_coder(engine)
        engine.tick()

        fault = _make_pneumatic_fault(start_time=0.0)
        fault._spike_queue = [(0.05, 10.0)]
        engine.scenario_engine.add_scenario(fault)
        engine.tick()

        assert fault.in_spike is True
        assert coder._ink_pressure._target == pytest.approx(0.0)

    def test_pressure_restores_after_spike(self) -> None:
        engine = _make_engine(seed=41)
        coder = _get_coder(engine)
        engine.tick()
        baseline = coder._ink_pressure._target

        fault = _make_pneumatic_fault(start_time=0.0)
        fault._spike_queue = [(0.05, 0.15)]
        engine.scenario_engine.add_scenario(fault)
        engine.tick()
        engine.tick()

        assert fault.in_spike is False
        assert coder._ink_pressure._target == pytest.approx(baseline)

    def test_pneumatic_completes_after_phase2(self) -> None:
        """Pneumatic subtype (phase3_transition=False) completes at end of phase 2."""
        fault = _make_pneumatic_fault(start_time=0.0)
        engine = _make_engine(seed=42)
        engine.scenario_engine.add_scenario(fault)

        # Run past total_duration_s
        total_s = fault.duration()
        ticks_needed = int(total_s / 0.1) + 20
        for _ in range(ticks_needed):
            engine.tick()
            if fault.phase == ScenarioPhase.COMPLETED:
                break

        assert fault.phase == ScenarioPhase.COMPLETED
        assert fault.phase3_active is False


class TestIntermittentFaultPhase3:
    """Phase 3 permanent fault behaviour."""

    def test_bearing_enters_phase3(self) -> None:
        fault = _make_bearing_fault(
            start_time=0.0, phase1_hours=0.0, phase2_hours=0.0, phase3=True
        )
        engine = _make_engine(seed=50)
        engine.scenario_engine.add_scenario(fault)

        engine.tick()
        engine.tick()

        assert fault.phase3_active is True
        assert fault.current_phase == 3
        assert fault.in_spike is True

    def test_phase3_scenario_stays_active(self) -> None:
        """Once in phase 3, scenario remains ACTIVE (not COMPLETED)."""
        fault = _make_bearing_fault(
            start_time=0.0, phase1_hours=0.0, phase2_hours=0.0, phase3=True
        )
        engine = _make_engine(seed=51)
        engine.scenario_engine.add_scenario(fault)

        for _ in range(10):
            engine.tick()

        assert fault.phase == ScenarioPhase.ACTIVE

    def test_vibration_stays_spiked_in_phase3(self) -> None:
        """Vibration target remains at spike level throughout phase 3."""
        engine = _make_engine(seed=52)
        vib = _get_vibration(engine)

        fault = _make_bearing_fault(
            start_time=0.0, phase1_hours=0.0, phase2_hours=0.0, phase3=True
        )
        engine.scenario_engine.add_scenario(fault)

        for _ in range(10):
            engine.tick()

        assert vib._models["main_drive_x"]._target == pytest.approx(20.0)


class TestIntermittentFaultSpikeCount:
    """spike_count tracks the total number of spikes that have fired."""

    def test_spike_count_increments(self) -> None:
        engine = _make_engine(seed=60)
        fault = _make_bearing_fault(start_time=0.0)
        # Inject two distinct spikes into the queue
        fault._spike_queue = [(0.05, 0.15), (0.25, 0.35)]
        engine.scenario_engine.add_scenario(fault)

        # Tick past both spike windows
        for _ in range(6):
            engine.tick()

        assert fault.spike_count == 2

    def test_spike_count_zero_before_activation(self) -> None:
        fault = _make_bearing_fault(start_time=1000.0)
        assert fault.spike_count == 0


class TestIntermittentFaultGroundTruth:
    """Ground truth logging."""

    def test_gt_logs_spike_event(self, tmp_path: Any) -> None:
        """log_intermittent_fault is called when a spike starts."""
        from factory_simulator.engine.ground_truth import GroundTruthLogger

        gt_path = tmp_path / "gt.jsonl"
        gt = GroundTruthLogger(str(gt_path))
        gt.open()

        engine = _make_engine(seed=70)
        engine._ground_truth = gt
        engine._scenario_engine._ground_truth = gt

        fault = _make_bearing_fault(start_time=0.0)
        fault._spike_queue = [(0.05, 0.25)]
        engine.scenario_engine.add_scenario(fault)

        engine.tick()  # spike starts

        gt.close()
        events = [json.loads(line) for line in gt_path.read_text().splitlines() if line]
        if_events = [e for e in events if e.get("event") == "intermittent_fault"]
        assert len(if_events) >= 1
        spike_events = [e for e in if_events if not e.get("permanent", True)]
        assert len(spike_events) >= 1
        assert spike_events[0]["subtype"] == "bearing"
        assert spike_events[0]["phase"] == 1
        assert spike_events[0]["permanent"] is False

    def test_gt_logs_phase2_transition(self, tmp_path: Any) -> None:
        """Phase 1->2 transition creates a phase_transition ground truth event."""
        from factory_simulator.engine.ground_truth import GroundTruthLogger

        gt_path = tmp_path / "gt.jsonl"
        gt = GroundTruthLogger(str(gt_path))
        gt.open()

        engine = _make_engine(seed=71)
        engine._ground_truth = gt
        engine._scenario_engine._ground_truth = gt

        fault = _make_bearing_fault(start_time=0.0, phase1_hours=0.0, phase2_hours=1.0)
        fault._spike_queue = []  # no spikes; just test phase transition logging
        engine.scenario_engine.add_scenario(fault)

        engine.tick()
        engine.tick()

        gt.close()
        events = [json.loads(line) for line in gt_path.read_text().splitlines() if line]
        phase_events = [
            e for e in events
            if e.get("event") == "intermittent_fault"
            and "phase_transition" in e.get("note", "")
        ]
        assert any(
            "1_to_2" in e.get("note", "") for e in phase_events
        ), f"Expected phase_transition_1_to_2 in {phase_events}"

    def test_gt_logs_phase3_permanent_entry(self, tmp_path: Any) -> None:
        """Phase 3 entry creates a permanent=True ground truth event."""
        from factory_simulator.engine.ground_truth import GroundTruthLogger

        gt_path = tmp_path / "gt.jsonl"
        gt = GroundTruthLogger(str(gt_path))
        gt.open()

        engine = _make_engine(seed=72)
        engine._ground_truth = gt
        engine._scenario_engine._ground_truth = gt

        fault = _make_bearing_fault(
            start_time=0.0, phase1_hours=0.0, phase2_hours=0.0, phase3=True
        )
        fault._spike_queue = []
        engine.scenario_engine.add_scenario(fault)

        engine.tick()
        engine.tick()

        gt.close()
        events = [json.loads(line) for line in gt_path.read_text().splitlines() if line]
        permanent_events = [
            e for e in events
            if e.get("event") == "intermittent_fault" and e.get("permanent") is True
        ]
        assert len(permanent_events) >= 1


class TestIntermittentFaultScheduling:
    """Auto-scheduling via ScenarioEngine."""

    def test_intermittent_faults_scheduled_when_enabled(self) -> None:
        """Enabled subtypes appear in the scenario timeline for long sims."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 100
        config.simulation.tick_interval_ms = 100
        # Use 1 week so start_after_hours (24-72h) are within window
        config.simulation.sim_duration_s = 7 * 86400

        # Disable everything except intermittent_fault
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
        if config.scenarios.contextual_anomaly is not None:
            config.scenarios.contextual_anomaly.enabled = False
        if config.scenarios.intermittent_fault is not None:
            config.scenarios.intermittent_fault.enabled = True

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        if_scenarios = [
            s for s in engine.scenario_engine.scenarios
            if isinstance(s, IntermittentFault)
        ]
        # bearing (enabled), electrical (enabled), pneumatic (enabled); sensor disabled
        assert len(if_scenarios) == 3

    def test_intermittent_faults_not_scheduled_outside_sim_window(self) -> None:
        """No IntermittentFault if start_after_hours >= sim_duration."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 101
        # Default 8h sim is too short for any subtype (bearing=24h, etc.)
        config.simulation.sim_duration_s = 8 * 3600

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        if_scenarios = [
            s for s in engine.scenario_engine.scenarios
            if isinstance(s, IntermittentFault)
        ]
        assert len(if_scenarios) == 0

    def test_intermittent_faults_disabled_globally(self) -> None:
        """Setting enabled=False on the top-level config prevents scheduling."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 102
        config.simulation.sim_duration_s = 7 * 86400
        if config.scenarios.intermittent_fault is not None:
            config.scenarios.intermittent_fault.enabled = False

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        if_scenarios = [
            s for s in engine.scenario_engine.scenarios
            if isinstance(s, IntermittentFault)
        ]
        assert len(if_scenarios) == 0

    def test_start_time_matches_start_after_hours(self) -> None:
        """Bearing subtype starts at bearing_intermittent.start_after_hours * 3600."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 103
        config.simulation.sim_duration_s = 7 * 86400

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
        if config.scenarios.contextual_anomaly is not None:
            config.scenarios.contextual_anomaly.enabled = False
        if config.scenarios.intermittent_fault is not None:
            config.scenarios.intermittent_fault.enabled = True
            # Set known start time
            config.scenarios.intermittent_fault.faults.bearing_intermittent.start_after_hours = 48.0

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        bearing_faults = [
            s for s in engine.scenario_engine.scenarios
            if isinstance(s, IntermittentFault) and s.subtype == "bearing"
        ]
        assert len(bearing_faults) == 1
        assert bearing_faults[0].start_time == pytest.approx(48.0 * 3600.0)
