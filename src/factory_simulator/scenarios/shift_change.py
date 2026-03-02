"""Shift change scenario.

Simulates a shift handover: the press goes idle briefly while the new
shift takes over, then resumes at a speed determined by the new shift's
operator biases.

Sequence (PRD 5.9):
1. press.machine_state -> Idle (3) for 5-15 minutes.
2. press.line_speed drops to 0.
3. energy.line_power drops to base load.
4. After changeover:
   - press.machine_state -> Running (2)
   - New shift may run at slightly different speed
   - Night shift (22:00-06:00) runs 5-10% slower
   - Weekend shifts may not run at all (configurable)

Frequency: 3 per day at configurable times with ±10 min jitter.
Duration: 5-15 minutes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.press import PressGenerator


def _float_param(p: dict[str, object], key: str, default: float) -> float:
    """Safely extract a float from a params dict."""
    raw = p.get(key, default)
    return float(raw)  # type: ignore[arg-type]


class ShiftChange(Scenario):
    """Shift change: brief idle, then resume at new shift's speed.

    Parameters (via ``params`` dict)
    ---------------------------------
    changeover_seconds : list[int]
        [min, max] idle duration range (default [300, 900]).
    speed_bias : float
        Speed multiplier for the new shift (default 1.0).
        Night shift typically uses 0.9-0.95.
    waste_rate_bias : float
        Waste rate multiplier for the new shift (default 1.0).
    shift_name : str
        Name of the shift being entered (e.g. "morning", "afternoon",
        "night"). For logging and identification only.
    """

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Changeover duration
        dur_range = p.get("changeover_seconds", [300, 900])
        if isinstance(dur_range, list) and len(dur_range) == 2:
            self._changeover_duration = float(
                rng.uniform(float(dur_range[0]), float(dur_range[1]))
            )
        else:
            self._changeover_duration = float(dur_range)  # type: ignore[arg-type]

        # Shift biases
        self._speed_bias = _float_param(p, "speed_bias", 1.0)
        self._waste_rate_bias = _float_param(p, "waste_rate_bias", 1.0)
        self._shift_name = str(p.get("shift_name", "unknown"))

        # Track original values for restoration if needed
        self._original_target_speed: float = 0.0
        self._original_waste_rate: float | None = None

    @property
    def shift_name(self) -> str:
        """Name of the shift being entered."""
        return self._shift_name

    def duration(self) -> float:
        return self._changeover_duration

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Force press to Idle for shift handover."""
        press = self._find_press(engine)
        if press is None:
            self.complete(sim_time, engine)
            return

        self._original_target_speed = press.target_speed

        # Force press to Idle -- cascade handles speed ramp to 0
        press.state_machine.force_state("Idle")

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Wait for changeover duration, then resume with new shift biases."""
        if self._elapsed >= self._changeover_duration:
            press = self._find_press(engine)
            if press is not None:
                # Apply new shift's speed bias
                new_speed = self._original_target_speed * self._speed_bias
                press._target_speed = new_speed

                # Apply waste rate bias
                self._apply_waste_bias(press)

                # Resume production
                press.state_machine.force_state("Running")

            self.complete(sim_time, engine)

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Nothing to restore -- shift biases are intentionally persistent."""

    # -- Helpers ---------------------------------------------------------------

    def _find_press(self, engine: DataEngine) -> PressGenerator | None:
        """Find the press generator."""
        from factory_simulator.generators.press import PressGenerator as _PG

        for gen in engine.generators:
            if isinstance(gen, _PG):
                return gen
        return None

    def _apply_waste_bias(self, press: PressGenerator) -> None:
        """Apply the shift's waste rate bias to the waste counter."""
        waste: Any = getattr(press, "_waste_count", None)
        if waste is not None and hasattr(waste, "_rate"):
            # Store base rate for reference, then apply bias
            base_rate = getattr(waste, "_base_rate", waste._rate)
            waste._rate = base_rate * self._waste_rate_bias
