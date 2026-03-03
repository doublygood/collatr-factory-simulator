"""Job changeover scenario.

Simulates a production job change: the press ramps down, pauses for
setup, optionally changes dryer setpoints, then ramps back up with a
startup waste spike.

Sequence (PRD 5.2):
1. press.machine_state -> Setup (1)
2. press.line_speed ramps to 0 over 30-60s
3. Counters stop incrementing
4. coder.state -> Standby (4)
5. After setup duration (10-30 min):
   - Dryer setpoints may change (speed_change_probability)
   - Dryer temps begin tracking new setpoint
6. press.machine_state -> Running (2)
7. press.line_speed ramps to new target over 2-5 min
8. Counters may reset (counter_reset_probability)
9. Waste count increments faster for 2-3 min (startup waste)

Frequency: 3-6 per 8-hour shift.
Duration: 10-30 minutes per changeover.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.press import PressGenerator


def _float_param(p: dict[str, object], key: str, default: float) -> float:
    """Safely extract a float from a params dict."""
    raw = p.get(key, default)
    return float(raw)  # type: ignore[arg-type]


class _Phase(Enum):
    RAMP_DOWN = auto()
    SETUP = auto()
    RAMP_UP = auto()
    WASTE_SPIKE = auto()


class JobChangeover(Scenario):
    """Job changeover: ramp down, setup pause, ramp up with waste spike.

    Parameters (via ``params`` dict)
    ---------------------------------
    setup_duration_s : float
        Setup pause duration in seconds.  Drawn from
        ``uniform(duration_seconds[0], duration_seconds[1])`` at init.
    ramp_down_s : float
        Time to ramp speed to 0 (default: uniform 30-60s).
    ramp_up_s : float
        Time to ramp speed back up (default: uniform 120-300s = 2-5 min).
    speed_change_probability : float
        Probability of changing target speed after setup (default 0.3).
    counter_reset_probability : float
        Probability of resetting counters (default 0.7).
    waste_spike_duration_s : float
        Duration of elevated waste rate after restart (default 150s = 2.5 min).
    waste_spike_factor : float
        Waste rate multiplier during spike (default 3.0).
    """

    priority: ClassVar[str] = "state_changing"

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Setup duration (seconds)
        dur_range = p.get("duration_seconds", [600, 1800])
        if isinstance(dur_range, list) and len(dur_range) == 2:
            self._setup_duration = float(rng.uniform(float(dur_range[0]), float(dur_range[1])))
        else:
            self._setup_duration = float(dur_range)  # type: ignore[arg-type]

        # Ramp timings
        self._ramp_down_s = float(rng.uniform(30.0, 60.0))
        self._ramp_up_s = float(rng.uniform(120.0, 300.0))

        # Probabilities
        self._speed_change_prob = _float_param(p, "speed_change_probability", 0.3)
        self._counter_reset_prob = _float_param(p, "counter_reset_probability", 0.7)

        # Waste spike
        self._waste_spike_duration = _float_param(p, "waste_spike_duration_s", 150.0)
        self._waste_spike_factor = _float_param(p, "waste_spike_factor", 3.0)

        # Internal state
        self._internal_phase = _Phase.RAMP_DOWN
        self._phase_elapsed: float = 0.0
        self._original_target_speed: float = 0.0
        self._new_target_speed: float = 0.0
        self._original_waste_rate: float | None = None

    def duration(self) -> float:
        """Total planned duration including ramp down, setup, ramp up, and waste spike."""
        return (
            self._ramp_down_s
            + self._setup_duration
            + self._ramp_up_s
            + self._waste_spike_duration
        )

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Start the changeover: force state to Setup, begin ramp down."""
        press = self._find_press(engine)
        if press is None:
            self.complete(sim_time, engine)
            return

        self._original_target_speed = press.target_speed

        # Decide new target speed (possibly changed for new job)
        if self._rng.random() < self._speed_change_prob:
            # Random new speed within ±20% of original
            factor = float(self._rng.uniform(0.8, 1.2))
            self._new_target_speed = self._original_target_speed * factor
        else:
            self._new_target_speed = self._original_target_speed

        # Force press to Setup state
        press.state_machine.force_state("Setup")

        # Begin ramp down phase
        self._internal_phase = _Phase.RAMP_DOWN
        self._phase_elapsed = 0.0

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Advance through changeover phases."""
        self._phase_elapsed += dt

        press = self._find_press(engine)
        if press is None:
            self.complete(sim_time, engine)
            return

        if self._internal_phase == _Phase.RAMP_DOWN:
            if self._phase_elapsed >= self._ramp_down_s:
                # Ramp down complete, enter setup pause
                self._internal_phase = _Phase.SETUP
                self._phase_elapsed = 0.0

        elif self._internal_phase == _Phase.SETUP:
            if self._phase_elapsed >= self._setup_duration:
                # Setup complete -- optionally reset counters
                if self._rng.random() < self._counter_reset_prob:
                    self._reset_counters(press)

                # Update press target speed for new job
                press._target_speed = self._new_target_speed

                # Transition to Running -- press cascade handles speed ramp
                press.state_machine.force_state("Running")

                self._internal_phase = _Phase.RAMP_UP
                self._phase_elapsed = 0.0

        elif self._internal_phase == _Phase.RAMP_UP:
            if self._phase_elapsed >= self._ramp_up_s:
                # Ramp up complete -- begin waste spike phase
                self._internal_phase = _Phase.WASTE_SPIKE
                self._phase_elapsed = 0.0

                # Elevate waste rate
                self._boost_waste_rate(press)

        elif (
            self._internal_phase == _Phase.WASTE_SPIKE
            and self._phase_elapsed >= self._waste_spike_duration
        ):
            # Waste spike over -- restore normal waste rate
            self._restore_waste_rate(press)
            self.complete(sim_time, engine)

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Ensure waste rate is restored on completion."""
        press = self._find_press(engine)
        if press is not None:
            self._restore_waste_rate(press)

    # -- Helpers ---------------------------------------------------------------

    def _find_press(self, engine: DataEngine) -> PressGenerator | None:
        """Find the press generator in the engine."""
        from factory_simulator.generators.press import PressGenerator as _PG

        for gen in engine.generators:
            if isinstance(gen, _PG):
                return gen
        return None

    def _reset_counters(self, press: PressGenerator) -> None:
        """Reset press counters that have reset_on_job_change=True."""
        for attr_name in ("_impression_count", "_good_count", "_waste_count"):
            counter: Any = getattr(press, attr_name, None)
            if (
                counter is not None
                and hasattr(counter, "reset_on_job_change")
                and counter.reset_on_job_change
            ):
                counter.reset_counter()

    def _boost_waste_rate(self, press: PressGenerator) -> None:
        """Temporarily boost waste counter rate for startup waste."""
        waste: Any = getattr(press, "_waste_count", None)
        if waste is not None and hasattr(waste, "_rate"):
            self._original_waste_rate = waste._rate
            waste._rate = waste._rate * self._waste_spike_factor

    def _restore_waste_rate(self, press: PressGenerator) -> None:
        """Restore original waste counter rate."""
        if self._original_waste_rate is not None:
            waste: Any = getattr(press, "_waste_count", None)
            if waste is not None and hasattr(waste, "_rate"):
                waste._rate = self._original_waste_rate
            self._original_waste_rate = None
