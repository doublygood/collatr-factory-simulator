"""Intermittent fault scenario.

Simulates the three-phase sporadic-to-permanent fault progression described
in PRD Section 5.17.  Four subtypes are supported:

1. bearing   -- vibration.main_drive_x/y/z spike to 15-25 mm/s for seconds
2. electrical -- press.main_drive_current spikes by 20-50% for seconds
3. sensor    -- any signal briefly reports its sentinel value (1-5 s)
4. pneumatic -- coder.ink_pressure drops to 0 for seconds

Each enabled subtype is a separate ``IntermittentFault`` instance scheduled
by the ``ScenarioEngine``.

Phase progression (PRD 5.17):
    Phase 1 (Sporadic): rare spikes, long phase duration (days to weeks).
    Phase 2 (Frequent): common spikes, shorter duration (days).
    Phase 3 (Permanent): fault becomes continuous; signal does not return
        to normal.  Only subtypes with ``phase3_transition=True`` enter
        Phase 3 (bearing, electrical, sensor).  Pneumatic completes after
        Phase 2.

Priority: background (never preempted, never deferred).
Duration: phase1_duration_s + phase2_duration_s (drawn from config ranges).

PRD Reference: Section 5.17
CLAUDE.md Rule 6: uses sim_time/elapsed (simulation clock), never wall clock.
CLAUDE.md Rule 12: no global state, all state via instance variables.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.coder import CoderGenerator
    from factory_simulator.generators.press import PressGenerator
    from factory_simulator.generators.vibration import VibrationGenerator
    from factory_simulator.store import SignalStore

logger = logging.getLogger(__name__)

# Subtype string constants
_SUBTYPE_BEARING = "bearing"
_SUBTYPE_ELECTRICAL = "electrical"
_SUBTYPE_SENSOR = "sensor"
_SUBTYPE_PNEUMATIC = "pneumatic"

# Time constants for Poisson scheduling
_DAY_SECONDS: float = 86400.0

# Sentinel values per PRD Section 10.9
_SENTINEL_TEMPERATURE: float = 6553.5
_SENTINEL_PRESSURE: float = 0.0
_SENTINEL_VOLTAGE: float = -32768.0
_SENTINEL_DEFAULT: float = 0.0


def _sentinel_for_signal(signal_id: str) -> float:
    """Return the PRD-specified sentinel value for a signal (PRD 10.9)."""
    lower = signal_id.lower()
    if "temp" in lower or "temperature" in lower:
        return _SENTINEL_TEMPERATURE
    if "voltage" in lower:
        return _SENTINEL_VOLTAGE
    if "pressure" in lower:
        return _SENTINEL_PRESSURE
    return _SENTINEL_DEFAULT


class IntermittentFault(Scenario):
    """Three-phase intermittent fault with subtype dispatch (PRD 5.17).

    Parameters (via ``params`` dict)
    ---------------------------------
    subtype : str
        One of "bearing", "electrical", "sensor", "pneumatic".
    phase3_transition : bool
        If True, scenario enters permanent Phase 3 after Phase 2 ends.
        Default: True (pneumatic config defaults to False).
    phase1_duration_hours : list[float]
        ``[min, max]`` Phase 1 duration in hours.
    phase1_frequency_per_day : list[float]
        ``[min, max]`` spikes per day during Phase 1.
    phase1_spike_duration_s : list[float]
        ``[min, max]`` spike duration in seconds during Phase 1.
    phase2_duration_hours : list[float]
        ``[min, max]`` Phase 2 duration in hours.
    phase2_frequency_per_day : list[float]
        ``[min, max]`` spikes per day during Phase 2.
    phase2_spike_duration_s : list[float]
        ``[min, max]`` spike duration in seconds during Phase 2.
    affected_signals : list[str]
        Signal IDs affected by this fault instance.
    spike_magnitude : list[float]
        ``[min, max]`` absolute spike level in signal units (bearing: mm/s).
    spike_magnitude_pct : list[float]
        ``[min, max]`` spike as % increase over base value (electrical only).
    """

    priority: ClassVar[str] = "background"

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)
        p = self._params

        self._subtype: str = str(p.get("subtype", _SUBTYPE_BEARING))
        self._phase3_transition: bool = bool(p.get("phase3_transition", True))
        self._affected_signals: list[str] = list(
            p.get("affected_signals", [])  # type: ignore[call-overload]
        )

        # ---- Phase 1 parameters ----
        p1_dur_h = p.get("phase1_duration_hours", [168.0, 336.0])
        if isinstance(p1_dur_h, list | tuple) and len(p1_dur_h) == 2:
            self._phase1_duration_s: float = (
                float(rng.uniform(float(p1_dur_h[0]), float(p1_dur_h[1]))) * 3600.0
            )
        else:
            self._phase1_duration_s = float(p1_dur_h) * 3600.0  # type: ignore[arg-type]

        p1_freq = p.get("phase1_frequency_per_day", [1.0, 3.0])
        if isinstance(p1_freq, list | tuple) and len(p1_freq) == 2:
            p1_freq_per_day = float(rng.uniform(float(p1_freq[0]), float(p1_freq[1])))
        else:
            p1_freq_per_day = float(p1_freq)  # type: ignore[arg-type]

        p1_spike_dur = p.get("phase1_spike_duration_s", [10.0, 60.0])

        # ---- Phase 2 parameters ----
        p2_dur_h = p.get("phase2_duration_hours", [48.0, 168.0])
        if isinstance(p2_dur_h, list | tuple) and len(p2_dur_h) == 2:
            self._phase2_duration_s: float = (
                float(rng.uniform(float(p2_dur_h[0]), float(p2_dur_h[1]))) * 3600.0
            )
        else:
            self._phase2_duration_s = float(p2_dur_h) * 3600.0  # type: ignore[arg-type]

        p2_freq = p.get("phase2_frequency_per_day", [5.0, 20.0])
        if isinstance(p2_freq, list | tuple) and len(p2_freq) == 2:
            p2_freq_per_day = float(rng.uniform(float(p2_freq[0]), float(p2_freq[1])))
        else:
            p2_freq_per_day = float(p2_freq)  # type: ignore[arg-type]

        p2_spike_dur = p.get("phase2_spike_duration_s", [30.0, 300.0])

        # Total duration = phase 1 + phase 2
        self._total_duration_s: float = (
            self._phase1_duration_s + self._phase2_duration_s
        )

        # ---- Spike magnitude ----
        if self._subtype == _SUBTYPE_ELECTRICAL:
            pct_range = p.get("spike_magnitude_pct", [20.0, 50.0])
            if isinstance(pct_range, list | tuple) and len(pct_range) == 2:
                self._spike_magnitude_pct: float = float(
                    rng.uniform(float(pct_range[0]), float(pct_range[1]))
                )
            else:
                self._spike_magnitude_pct = float(pct_range)  # type: ignore[arg-type]
            self._spike_magnitude: float = 0.0  # not used for electrical
        else:
            mag_range = p.get("spike_magnitude", [15.0, 25.0])
            if isinstance(mag_range, list | tuple) and len(mag_range) == 2:
                self._spike_magnitude = float(
                    rng.uniform(float(mag_range[0]), float(mag_range[1]))
                )
            else:
                self._spike_magnitude = float(mag_range)  # type: ignore[arg-type]
            self._spike_magnitude_pct = 0.0  # not used for non-electrical

        # ---- Pre-generate spike schedule ----
        # Each entry: (start_elapsed_s, end_elapsed_s) relative to scenario start.
        self._spike_queue: list[tuple[float, float]] = []
        self._build_spike_schedule(
            rng,
            phase_start_elapsed=0.0,
            phase_duration_s=self._phase1_duration_s,
            freq_per_day=p1_freq_per_day,
            spike_dur_param=p1_spike_dur,
        )
        self._build_spike_schedule(
            rng,
            phase_start_elapsed=self._phase1_duration_s,
            phase_duration_s=self._phase2_duration_s,
            freq_per_day=p2_freq_per_day,
            spike_dur_param=p2_spike_dur,
        )
        self._spike_queue.sort(key=lambda t: t[0])

        # ---- Runtime spike state ----
        self._next_spike_idx: int = 0
        self._in_spike: bool = False
        self._current_spike_end_elapsed: float = -1.0
        self._spike_count: int = 0

        # ---- Phase tracking ----
        self._current_phase: int = 1
        self._phase3_active: bool = False
        self._phase2_transition_logged: bool = False
        self._phase3_transition_logged: bool = False

        # ---- Generator references (populated on activate) ----
        self._vibration_gen: VibrationGenerator | None = None
        self._press: PressGenerator | None = None
        self._coder: CoderGenerator | None = None

        # ---- Saved baseline generator state ----
        self._saved_vib_targets: dict[str, float] = {}
        self._saved_current_base: float = 0.0
        self._saved_ink_pressure_target: float = 0.0

    # -- Public properties for testing -----------------------------------------

    @property
    def subtype(self) -> str:
        """Fault subtype identifier."""
        return self._subtype

    @property
    def phase1_duration_s(self) -> float:
        """Drawn Phase 1 duration in seconds."""
        return self._phase1_duration_s

    @property
    def phase2_duration_s(self) -> float:
        """Drawn Phase 2 duration in seconds."""
        return self._phase2_duration_s

    @property
    def current_phase(self) -> int:
        """Current internal phase (1, 2, or 3)."""
        return self._current_phase

    @property
    def spike_count(self) -> int:
        """Total number of spikes that have started."""
        return self._spike_count

    @property
    def in_spike(self) -> bool:
        """True while a spike is actively being applied."""
        return self._in_spike

    @property
    def phase3_active(self) -> bool:
        """True when the permanent Phase 3 state is active."""
        return self._phase3_active

    @property
    def spike_magnitude(self) -> float:
        """Absolute spike magnitude (bearing/sensor/pneumatic)."""
        return self._spike_magnitude

    @property
    def spike_magnitude_pct(self) -> float:
        """Spike magnitude as percent of base (electrical)."""
        return self._spike_magnitude_pct

    def duration(self) -> float:
        """Total planned scenario duration: phase1 + phase2 (seconds)."""
        return self._total_duration_s

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(self, sim_time: float, engine: DataEngine) -> None:
        """Locate generators, save baseline state, log scenario start."""
        self._vibration_gen = self._find_vibration(engine)
        self._press = self._find_press(engine)
        self._coder = self._find_coder(engine)

        # Save baseline model state for spike application / restoration.
        if self._vibration_gen is not None:
            for name, model in self._vibration_gen._models.items():
                self._saved_vib_targets[name] = model._target

        if self._press is not None:
            self._saved_current_base = self._press._main_drive_current._base

        if self._coder is not None and self._coder._ink_pressure is not None:
            self._saved_ink_pressure_target = self._coder._ink_pressure._target

        gt = engine.ground_truth
        if gt is not None:
            gt.log_scenario_start(
                sim_time,
                "IntermittentFault",
                list(self._affected_signals),
                {
                    "subtype": self._subtype,
                    "phase1_duration_s": self._phase1_duration_s,
                    "phase2_duration_s": self._phase2_duration_s,
                    "phase3_transition": self._phase3_transition,
                },
            )

    def _on_tick(self, sim_time: float, dt: float, engine: DataEngine) -> None:
        """Manage phase transitions and spike lifecycle each tick."""
        # Once Phase 3 is active the scenario stays permanently spiking.
        if self._phase3_active:
            return

        # ---- Phase transition checks ----------------------------------------
        if self._current_phase == 1 and self._elapsed >= self._phase1_duration_s:
            self._current_phase = 2
            if not self._phase2_transition_logged:
                self._phase2_transition_logged = True
                self._log_phase_transition(sim_time, engine, from_phase=1, to_phase=2)

        if self._current_phase == 2 and self._elapsed >= self._total_duration_s:
            if self._phase3_transition:
                self._current_phase = 3
                self._enter_phase3(sim_time, engine)
            else:
                # Subtype without permanent phase: end current spike and complete.
                if self._in_spike:
                    self._end_spike(sim_time, engine)
                self.complete(sim_time, engine)
            return

        # ---- Active spike: check expiry -------------------------------------
        if self._in_spike and self._elapsed >= self._current_spike_end_elapsed:
            self._end_spike(sim_time, engine)

        # ---- Next spike: check if we should start ---------------------------
        if not self._in_spike:
            while self._next_spike_idx < len(self._spike_queue):
                start_e, end_e = self._spike_queue[self._next_spike_idx]
                if self._elapsed >= start_e:
                    self._next_spike_idx += 1
                    # Skip spikes whose window has already passed entirely.
                    if self._elapsed < end_e:
                        self._start_spike(sim_time, end_e, engine)
                        break
                else:
                    break  # Next spike hasn't arrived yet.

    def _on_complete(self, sim_time: float, engine: DataEngine) -> None:
        """End any active spike, restore state (if not phase 3), log end."""
        if self._in_spike and not self._phase3_active:
            self._end_spike(sim_time, engine)

        if not self._phase3_active:
            self._restore_generator_state()

        gt = engine.ground_truth
        if gt is not None:
            gt.log_scenario_end(sim_time, "IntermittentFault")

    def post_gen_inject(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> None:
        """Write sentinel values for sensor subtype during spikes (PRD 5.17).

        Called AFTER generators write to the store; overrides their output
        with sentinel values while the sensor fault is active.
        """
        if self._subtype != _SUBTYPE_SENSOR:
            return
        if not self._in_spike and not self._phase3_active:
            return
        for sig_id in self._affected_signals:
            sentinel = _sentinel_for_signal(sig_id)
            store.set(sig_id, sentinel, sim_time)

    # -- Spike management -------------------------------------------------------

    def _start_spike(
        self,
        sim_time: float,
        end_elapsed: float,
        engine: DataEngine,
    ) -> None:
        """Begin a spike: apply effect to generator models and log GT."""
        self._in_spike = True
        self._current_spike_end_elapsed = end_elapsed
        self._spike_count += 1

        self._apply_spike(spike_on=True)

        spike_duration = end_elapsed - self._elapsed

        gt = engine.ground_truth
        if gt is not None:
            magnitude = (
                self._spike_magnitude_pct
                if self._subtype == _SUBTYPE_ELECTRICAL
                else self._spike_magnitude
            )
            gt.log_intermittent_fault(
                sim_time=sim_time,
                subtype=self._subtype,
                phase=self._current_phase,
                affected_signals=self._affected_signals,
                magnitude=magnitude,
                duration=spike_duration,
                permanent=False,
            )

    def _end_spike(self, sim_time: float, engine: DataEngine) -> None:
        """End the current spike: restore generator models to baseline."""
        self._in_spike = False
        self._apply_spike(spike_on=False)

    def _apply_spike(self, spike_on: bool) -> None:
        """Apply or remove spike effect on the relevant generator model."""
        if self._subtype == _SUBTYPE_BEARING:
            vib = self._vibration_gen
            if vib is not None:
                for name, model in vib._models.items():
                    if spike_on:
                        model._target = self._spike_magnitude
                    else:
                        model._target = self._saved_vib_targets.get(name, model._target)

        elif self._subtype == _SUBTYPE_ELECTRICAL:
            press = self._press
            if press is not None:
                if spike_on:
                    press._main_drive_current._base = self._saved_current_base * (
                        1.0 + self._spike_magnitude_pct / 100.0
                    )
                else:
                    press._main_drive_current._base = self._saved_current_base

        elif self._subtype == _SUBTYPE_PNEUMATIC:
            coder = self._coder
            if coder is not None and coder._ink_pressure is not None:
                if spike_on:
                    coder._ink_pressure._target = 0.0
                else:
                    coder._ink_pressure._target = self._saved_ink_pressure_target

        # Sensor subtype: handled by post_gen_inject — no model modification.

    # -- Phase 3 ---------------------------------------------------------------

    def _enter_phase3(self, sim_time: float, engine: DataEngine) -> None:
        """Transition to permanent Phase 3 fault state."""
        self._phase3_active = True

        if not self._phase3_transition_logged:
            self._phase3_transition_logged = True
            self._log_phase_transition(sim_time, engine, from_phase=2, to_phase=3)

        # Apply the permanent spike effect.
        self._in_spike = True
        self._apply_spike(spike_on=True)

        magnitude = (
            self._spike_magnitude_pct
            if self._subtype == _SUBTYPE_ELECTRICAL
            else self._spike_magnitude
        )
        gt = engine.ground_truth
        if gt is not None:
            gt.log_intermittent_fault(
                sim_time=sim_time,
                subtype=self._subtype,
                phase=3,
                affected_signals=self._affected_signals,
                magnitude=magnitude,
                duration=0.0,
                permanent=True,
            )

    # -- Ground truth helpers --------------------------------------------------

    def _log_phase_transition(
        self,
        sim_time: float,
        engine: DataEngine,
        from_phase: int,
        to_phase: int,
    ) -> None:
        """Log a phase transition event to ground truth."""
        gt = engine.ground_truth
        if gt is not None:
            gt.log_intermittent_fault(
                sim_time=sim_time,
                subtype=self._subtype,
                phase=to_phase,
                affected_signals=self._affected_signals,
                magnitude=0.0,
                duration=0.0,
                permanent=to_phase == 3,
                note=f"phase_transition_{from_phase}_to_{to_phase}",
            )

    # -- Generator finders -----------------------------------------------------

    def _find_vibration(self, engine: DataEngine) -> VibrationGenerator | None:
        from factory_simulator.generators.vibration import VibrationGenerator as _VG
        for gen in engine.generators:
            if isinstance(gen, _VG):
                return gen
        return None

    def _find_press(self, engine: DataEngine) -> PressGenerator | None:
        from factory_simulator.generators.press import PressGenerator as _PG
        for gen in engine.generators:
            if isinstance(gen, _PG):
                return gen
        return None

    def _find_coder(self, engine: DataEngine) -> CoderGenerator | None:
        from factory_simulator.generators.coder import CoderGenerator as _CG
        for gen in engine.generators:
            if isinstance(gen, _CG):
                return gen
        return None

    # -- Spike schedule builder ------------------------------------------------

    def _build_spike_schedule(
        self,
        rng: np.random.Generator,
        phase_start_elapsed: float,
        phase_duration_s: float,
        freq_per_day: float,
        spike_dur_param: object,
    ) -> None:
        """Append Poisson-distributed spike events for one phase to the queue.

        Events are expressed as ``(start_elapsed_s, end_elapsed_s)`` relative
        to scenario activation (not phase start).
        """
        if freq_per_day <= 0.0 or phase_duration_s <= 0.0:
            return

        mean_interval = _DAY_SECONDS / freq_per_day

        t = 0.0  # relative to phase start
        while t < phase_duration_s:
            gap = float(rng.exponential(mean_interval))
            t += gap
            if t >= phase_duration_s:
                break

            if isinstance(spike_dur_param, list | tuple) and len(spike_dur_param) == 2:
                spike_dur = float(
                    rng.uniform(float(spike_dur_param[0]), float(spike_dur_param[1]))
                )
            else:
                spike_dur = float(spike_dur_param)  # type: ignore[arg-type]

            start_e = phase_start_elapsed + t
            end_e = start_e + spike_dur
            # Clamp to phase end.
            phase_end = phase_start_elapsed + phase_duration_s
            if end_e > phase_end:
                end_e = phase_end
            if end_e > start_e:
                self._spike_queue.append((start_e, end_e))

    # -- Restore helper --------------------------------------------------------

    def _restore_generator_state(self) -> None:
        """Restore all generator models to their saved baseline state."""
        vib = self._vibration_gen
        if vib is not None:
            for name, saved_target in self._saved_vib_targets.items():
                if name in vib._models:
                    vib._models[name]._target = saved_target

        press = self._press
        if press is not None:
            press._main_drive_current._base = self._saved_current_base

        coder = self._coder
        if coder is not None and coder._ink_pressure is not None:
            coder._ink_pressure._target = self._saved_ink_pressure_target
