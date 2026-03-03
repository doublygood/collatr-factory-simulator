"""Cold start energy spike scenario.

Simulates the energy and current inrush that occurs when the packaging
line starts from a cold state (after being idle or off for >30 minutes).

Sequence (PRD 5.10):
1. When press.machine_state transitions from Off (0) or Idle (3) to
   Setup (1) or Running (2) after being idle for more than 30 minutes:
   - energy.line_power spikes to 150-200% of normal running power
     for 2-5 seconds.
   - press.main_drive_current spikes to 150-300% of running current
     (motor inrush).
2. After the spike, power settles to normal running level.

Frequency: 1-2 per day (each time the line starts from cold).
Duration: 2-5 seconds.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.energy import EnergyGenerator
    from factory_simulator.generators.press import PressGenerator

# Off and Idle states (trigger sources)
_IDLE_STATES = {0, 3}  # Off, Idle
# Setup and Running states (trigger targets)
_ACTIVE_STATES = {1, 2}  # Setup, Running


class _Phase(Enum):
    """Internal phase of the cold start scenario."""

    MONITORING = auto()
    SPIKE = auto()


class ColdStart(Scenario):
    """Cold start energy spike: inrush on startup from cold.

    The scenario monitors the press state machine.  When a transition
    from Off/Idle to Setup/Running is detected after the idle threshold
    (default 30 minutes), it temporarily overrides the energy line_power
    and press main_drive_current models to produce an inrush spike.

    The spike modifies ``CorrelatedFollowerModel._base`` on both signals
    to produce a high output regardless of current line speed (which is
    near zero at startup).  Max clamp values are temporarily raised to
    allow the spike to exceed normal operating range.

    Parameters (via ``params`` dict)
    ---------------------------------
    spike_duration_range : list[float]
        [min, max] spike duration in seconds (default [2.0, 5.0]).
    power_multiplier_range : list[float]
        [min, max] multiplier on normal running power
        (default [1.5, 2.0] = 150-200%).
    current_multiplier_range : list[float]
        [min, max] multiplier on normal running current
        (default [1.5, 3.0] = 150-300%).
    idle_threshold_s : float
        Minimum idle duration in seconds to trigger
        (default 1800.0 = 30 min).
    """

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Spike duration (PRD 5.10: 2-5 seconds)
        dur_range = p.get("spike_duration_range", [2.0, 5.0])
        if isinstance(dur_range, list) and len(dur_range) == 2:
            self._spike_duration = float(
                rng.uniform(float(dur_range[0]), float(dur_range[1]))
            )
        else:
            self._spike_duration = float(dur_range)  # type: ignore[arg-type]

        # Power multiplier (PRD 5.10: 150-200%)
        pow_range = p.get("power_multiplier_range", [1.5, 2.0])
        if isinstance(pow_range, list) and len(pow_range) == 2:
            self._power_multiplier = float(
                rng.uniform(float(pow_range[0]), float(pow_range[1]))
            )
        else:
            self._power_multiplier = float(pow_range)  # type: ignore[arg-type]

        # Current multiplier (PRD 5.10: 150-300%)
        cur_range = p.get("current_multiplier_range", [1.5, 3.0])
        if isinstance(cur_range, list) and len(cur_range) == 2:
            self._current_multiplier = float(
                rng.uniform(float(cur_range[0]), float(cur_range[1]))
            )
        else:
            self._current_multiplier = float(cur_range)  # type: ignore[arg-type]

        # Idle threshold (PRD 5.10: 30 minutes)
        raw_threshold = p.get("idle_threshold_s", 1800.0)
        self._idle_threshold = float(raw_threshold)  # type: ignore[arg-type]

        # Internal state
        self._internal_phase = _Phase.MONITORING
        self._spike_elapsed: float = 0.0
        self._idle_since: float | None = None
        self._prev_press_state: int | None = None

        # Saved model state for restore
        self._press: PressGenerator | None = None
        self._energy: EnergyGenerator | None = None
        self._saved_power_base: float = 0.0
        self._saved_current_base: float = 0.0
        self._saved_power_max_clamp: float | None = None
        self._saved_current_max_clamp: float | None = None

    # -- Public properties for testing -----------------------------------------

    @property
    def spike_duration(self) -> float:
        """Duration of the energy/current spike in seconds."""
        return self._spike_duration

    @property
    def power_multiplier(self) -> float:
        """Multiplier applied to normal running power."""
        return self._power_multiplier

    @property
    def current_multiplier(self) -> float:
        """Multiplier applied to normal running current."""
        return self._current_multiplier

    @property
    def idle_threshold(self) -> float:
        """Minimum idle duration (seconds) to trigger the spike."""
        return self._idle_threshold

    @property
    def internal_phase(self) -> _Phase:
        """Current internal phase (MONITORING or SPIKE)."""
        return self._internal_phase

    def duration(self) -> float:
        """Total planned duration of the spike effect."""
        return self._spike_duration

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Enter monitoring mode.  Track initial press state."""
        self._internal_phase = _Phase.MONITORING
        press = self._find_press(engine)
        if press is None:
            self.complete(sim_time, engine)
            return

        state = int(press.state_machine.current_value)
        self._prev_press_state = state
        if state in _IDLE_STATES:
            self._idle_since = sim_time

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Dispatch to monitoring or spike handler."""
        if self._internal_phase == _Phase.MONITORING:
            self._monitor_tick(sim_time, dt, engine)
        elif self._internal_phase == _Phase.SPIKE:
            self._spike_tick(sim_time, dt, engine)

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Restore model parameters after spike."""
        if self._energy is not None:
            self._energy._line_power._base = self._saved_power_base
            power_cfg = self._energy._signal_configs.get("line_power")
            if power_cfg is not None and self._saved_power_max_clamp is not None:
                power_cfg.max_clamp = self._saved_power_max_clamp

        if self._press is not None:
            self._press._main_drive_current._base = self._saved_current_base
            current_cfg = self._press._signal_configs.get("main_drive_current")
            if current_cfg is not None and self._saved_current_max_clamp is not None:
                current_cfg.max_clamp = self._saved_current_max_clamp

    # -- Internal phase handlers -----------------------------------------------

    def _monitor_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Watch the press state machine for a cold start trigger."""
        press = self._find_press(engine)
        if press is None:
            self.complete(sim_time, engine)
            return

        current_state = int(press.state_machine.current_value)

        if current_state in _IDLE_STATES:
            # Press is idle/off -- track the start of idle period
            if self._idle_since is None:
                self._idle_since = sim_time
        elif current_state in _ACTIVE_STATES:
            # Press is entering an active state -- check trigger
            if (
                self._prev_press_state is not None
                and self._prev_press_state in _IDLE_STATES
                and self._idle_since is not None
            ):
                idle_duration = sim_time - self._idle_since
                if idle_duration >= self._idle_threshold:
                    self._start_spike(sim_time, engine, press)
                    self._prev_press_state = current_state
                    return
            # Not a valid trigger; reset idle tracking
            self._idle_since = None
        else:
            # Fault, Maintenance, etc. -- reset idle tracking
            self._idle_since = None

        self._prev_press_state = current_state

    def _spike_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Track spike duration and complete when done."""
        self._spike_elapsed += dt
        if self._spike_elapsed > self._spike_duration:
            self.complete(sim_time, engine)

    def _start_spike(
        self,
        sim_time: float,
        engine: DataEngine,
        press: PressGenerator,
    ) -> None:
        """Transition from MONITORING to SPIKE.

        Calculates normal running power/current from model params and
        target speed, then sets _base to the spiked value.  Temporarily
        raises max_clamp where the spike would exceed the normal clamp.
        """
        self._internal_phase = _Phase.SPIKE
        self._spike_elapsed = 0.0
        self._press = press

        energy = self._find_energy(engine)
        self._energy = energy

        target_speed = press.target_speed

        # --- Spike energy.line_power ---
        if energy is not None:
            self._saved_power_base = energy._line_power._base
            normal_power = (
                energy._line_power._base
                + energy._line_power._gain * target_speed
            )
            spike_power = normal_power * self._power_multiplier
            energy._line_power._base = spike_power

            power_cfg = energy._signal_configs.get("line_power")
            if power_cfg is not None:
                self._saved_power_max_clamp = power_cfg.max_clamp
                if (
                    power_cfg.max_clamp is not None
                    and spike_power > power_cfg.max_clamp
                ):
                    power_cfg.max_clamp = spike_power * 1.2

        # --- Spike press.main_drive_current ---
        self._saved_current_base = press._main_drive_current._base
        normal_current = (
            press._main_drive_current._base
            + press._main_drive_current._gain * target_speed
        )
        spike_current = normal_current * self._current_multiplier
        press._main_drive_current._base = spike_current

        current_cfg = press._signal_configs.get("main_drive_current")
        if current_cfg is not None:
            self._saved_current_max_clamp = current_cfg.max_clamp
            if (
                current_cfg.max_clamp is not None
                and spike_current > current_cfg.max_clamp
            ):
                current_cfg.max_clamp = spike_current * 1.2

        # Ground truth: energy and current spike anomalies (PRD 4.7)
        gt = engine.ground_truth
        if gt is not None:
            gt.log_signal_anomaly(
                sim_time, "energy.line_power", "spike",
                spike_power, [0.0, normal_power],
            )
            gt.log_signal_anomaly(
                sim_time, "press.main_drive_current", "spike",
                spike_current, [0.0, normal_current],
            )

    # -- Helpers ---------------------------------------------------------------

    def _find_press(self, engine: DataEngine) -> PressGenerator | None:
        """Find the press generator in the engine."""
        from factory_simulator.generators.press import PressGenerator as _PG

        for gen in engine.generators:
            if isinstance(gen, _PG):
                return gen
        return None

    def _find_energy(self, engine: DataEngine) -> EnergyGenerator | None:
        """Find the energy generator in the engine."""
        from factory_simulator.generators.energy import EnergyGenerator as _EG

        for gen in engine.generators:
            if isinstance(gen, _EG):
                return gen
        return None
