"""Web break scenario.

Simulates a web (substrate) break on the flexographic press.  The
break produces a distinctive tension spike followed by an emergency
stop and extended recovery period.

Sequence (PRD 5.3):
1. press.web_tension spikes above 600 N for 100-500 ms.
2. press.web_tension drops to 0 within 1 second.
3. press.machine_state transitions to Fault (4).
4. press.line_speed drops to 0 via emergency deceleration (5-10 s).
5. Coil 3 (web_break) sets to true.
6. Coil 1 (fault_active) sets to true.
7. After recovery duration (15-60 min):
   - Coils clear.
   - press.machine_state transitions to Setup (1), then Running (2).
   - Normal startup sequence follows.

Frequency: 1-2 per week (configurable).
Duration: 15-60 minutes recovery.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING, Any

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.press import PressGenerator


class _Phase(Enum):
    """Internal sub-phases of the web break event."""

    SPIKE = auto()         # Tension spike >600 N
    DECELERATION = auto()  # Tension → 0, emergency decel, fault state
    RECOVERY = auto()      # Waiting for operator recovery


class WebBreak(Scenario):
    """Web break: tension spike, emergency stop, timed recovery.

    Parameters (via ``params`` dict)
    ---------------------------------
    recovery_seconds : list[int | float]
        [min, max] recovery duration range.  Drawn from
        ``uniform(min, max)`` at init (default [900, 3600]).
    spike_tension_range : list[float]
        [min, max] spike tension in Newtons (default [650, 800]).
    spike_duration_range : list[float]
        [min, max] spike duration in seconds (default [0.1, 0.5]).
    decel_duration_range : list[float]
        [min, max] emergency deceleration time in seconds
        (default [5.0, 10.0]).
    """

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Recovery duration (PRD: 15-60 min)
        rec_range = p.get("recovery_seconds", [900, 3600])
        if isinstance(rec_range, list) and len(rec_range) == 2:
            self._recovery_duration = float(
                rng.uniform(float(rec_range[0]), float(rec_range[1]))
            )
        else:
            self._recovery_duration = float(rec_range)  # type: ignore[arg-type]

        # Spike tension (PRD: >600 N)
        spike_range = p.get("spike_tension_range", [650.0, 800.0])
        if isinstance(spike_range, list) and len(spike_range) == 2:
            self._spike_tension = float(
                rng.uniform(float(spike_range[0]), float(spike_range[1]))
            )
        else:
            self._spike_tension = float(spike_range)  # type: ignore[arg-type]

        # Spike duration (PRD: 100-500 ms)
        spike_dur = p.get("spike_duration_range", [0.1, 0.5])
        if isinstance(spike_dur, list) and len(spike_dur) == 2:
            self._spike_duration = float(
                rng.uniform(float(spike_dur[0]), float(spike_dur[1]))
            )
        else:
            self._spike_duration = float(spike_dur)  # type: ignore[arg-type]

        # Emergency deceleration (PRD: 5-10 s)
        decel_range = p.get("decel_duration_range", [5.0, 10.0])
        if isinstance(decel_range, list) and len(decel_range) == 2:
            self._decel_duration = float(
                rng.uniform(float(decel_range[0]), float(decel_range[1]))
            )
        else:
            self._decel_duration = float(decel_range)  # type: ignore[arg-type]

        # Internal state
        self._internal_phase = _Phase.SPIKE
        self._phase_elapsed: float = 0.0

        # Saved generator state for restore on completion
        self._saved_base: float = 0.0
        self._saved_gain: float = 1.0
        self._saved_max_clamp: float | None = None
        self._press: PressGenerator | None = None

    @property
    def spike_tension(self) -> float:
        """Peak tension during the web break spike (N)."""
        return self._spike_tension

    @property
    def spike_duration(self) -> float:
        """Duration of the tension spike (seconds)."""
        return self._spike_duration

    @property
    def decel_duration(self) -> float:
        """Emergency deceleration time (seconds)."""
        return self._decel_duration

    @property
    def recovery_duration(self) -> float:
        """Recovery wait time (seconds)."""
        return self._recovery_duration

    @property
    def internal_phase(self) -> _Phase:
        """Current internal sub-phase (for testing)."""
        return self._internal_phase

    def duration(self) -> float:
        """Total planned duration: spike + decel + recovery."""
        return self._spike_duration + self._decel_duration + self._recovery_duration

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Begin the web break: start the tension spike phase."""
        press = self._find_press(engine)
        if press is None:
            self.complete(sim_time, engine)
            return

        self._press = press
        self._internal_phase = _Phase.SPIKE
        self._phase_elapsed = 0.0

        # Save original tension model state
        tension_model: Any = press._web_tension
        self._saved_base = tension_model._base
        self._saved_gain = tension_model._gain

        # Save original max_clamp (tension config says 500, spike needs >600)
        sig_cfg = press._signal_configs.get("web_tension")
        if sig_cfg is not None:
            self._saved_max_clamp = sig_cfg.max_clamp

        # Override: produce a fixed spike tension regardless of speed
        tension_model._base = self._spike_tension
        tension_model._gain = 0.0

        # Raise max_clamp so the spike isn't clamped to 500 N
        if sig_cfg is not None:
            sig_cfg.max_clamp = 1000.0

        # Ground truth: tension spike anomaly (PRD 4.7)
        gt = engine.ground_truth
        if gt is not None:
            gt.log_signal_anomaly(
                sim_time, "press.web_tension", "spike",
                self._spike_tension, [60.0, 400.0],
            )

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Advance through web break sub-phases."""
        self._phase_elapsed += dt

        press = self._press
        if press is None:
            self.complete(sim_time, engine)
            return

        if self._internal_phase == _Phase.SPIKE:
            self._tick_spike(sim_time, engine, press)
        elif self._internal_phase == _Phase.DECELERATION:
            self._tick_deceleration()
        elif self._internal_phase == _Phase.RECOVERY:
            self._tick_recovery(sim_time, engine)

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Clear fault indicators and restore normal operation."""
        press = self._press or self._find_press(engine)
        if press is not None:
            # Restore tension model state
            self._restore_tension_model(press)

            # Transition to Setup (operator re-threads web, then normal startup)
            press.state_machine.force_state("Setup")

        # Clear coils
        store = engine.store
        store.set("press.web_break", 0.0, sim_time, "good")
        store.set("press.fault_active", 0.0, sim_time, "good")

    # -- Phase handlers --------------------------------------------------------

    def _tick_spike(
        self,
        sim_time: float,
        engine: DataEngine,
        press: PressGenerator,
    ) -> None:
        """SPIKE phase: hold tension spike, then transition to deceleration."""
        if self._phase_elapsed > self._spike_duration:
            self._enter_deceleration(sim_time, engine, press)

    def _enter_deceleration(
        self,
        sim_time: float,
        engine: DataEngine,
        press: PressGenerator,
    ) -> None:
        """Transition from SPIKE to DECELERATION.

        Drop tension to 0, force Fault state, start emergency decel,
        set coils.
        """
        from factory_simulator.generators.press import STATE_FAULT

        self._internal_phase = _Phase.DECELERATION
        self._phase_elapsed = 0.0

        # Drop tension: base=0 with gain=0 produces 0 regardless of speed
        tension_model: Any = press._web_tension
        tension_model._base = 0.0
        # gain stays 0 from SPIKE phase

        # Force Fault state -- prevent the press cascade from starting
        # its default 30s ramp (we use a custom 5-10s emergency decel)
        press.state_machine.force_state("Fault")
        press._prev_state = STATE_FAULT

        # Ground truth: state change Running(2) -> Fault(4) (PRD 4.7)
        gt = engine.ground_truth
        if gt is not None:
            gt.log_state_change(sim_time, "press.machine_state", 2, 4)

        # Start custom emergency deceleration (5-10 s, faster than default 30s)
        speed = press._line_speed_model.value
        if speed > 0.0:
            press._line_speed_model.start_ramp(
                start=speed, end=0.0, duration=self._decel_duration,
            )

        # Set coils in the store
        store = engine.store
        store.set("press.web_break", 1.0, sim_time, "bad")
        store.set("press.fault_active", 1.0, sim_time, "bad")

    def _tick_deceleration(self) -> None:
        """DECELERATION phase: wait for emergency decel to complete."""
        if self._phase_elapsed >= self._decel_duration:
            self._enter_recovery()

    def _enter_recovery(self) -> None:
        """Transition from DECELERATION to RECOVERY."""
        self._internal_phase = _Phase.RECOVERY
        self._phase_elapsed = 0.0

        # Restore gain now -- speed is ~0, so tension remains ~0 naturally
        press = self._press
        if press is not None:
            press._web_tension._gain = self._saved_gain

            # Restore max_clamp
            sig_cfg = press._signal_configs.get("web_tension")
            if sig_cfg is not None and self._saved_max_clamp is not None:
                sig_cfg.max_clamp = self._saved_max_clamp

    def _tick_recovery(
        self,
        sim_time: float,
        engine: DataEngine,
    ) -> None:
        """RECOVERY phase: wait for recovery duration, then complete."""
        if self._phase_elapsed >= self._recovery_duration:
            self.complete(sim_time, engine)

    # -- Helpers ---------------------------------------------------------------

    def _restore_tension_model(self, press: PressGenerator) -> None:
        """Restore all saved tension model and config state."""
        tension_model: Any = press._web_tension
        tension_model._base = self._saved_base
        tension_model._gain = self._saved_gain

        sig_cfg = press._signal_configs.get("web_tension")
        if sig_cfg is not None and self._saved_max_clamp is not None:
            sig_cfg.max_clamp = self._saved_max_clamp

    def _find_press(self, engine: DataEngine) -> PressGenerator | None:
        """Find the press generator in the engine."""
        from factory_simulator.generators.press import PressGenerator as _PG

        for gen in engine.generators:
            if isinstance(gen, _PG):
                return gen
        return None
