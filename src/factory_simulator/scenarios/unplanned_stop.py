"""Unplanned stop scenario.

Simulates an unexpected machine fault that halts production for a
configured duration before recovery.

Sequence (PRD 5.8):
1. press.machine_state -> Fault (4)
2. press.line_speed drops to 0
3. Coil 1 (fault_active) sets to true
4. A fault code is written to holding register 211
5. After stop duration, fault clears; normal startup follows.

Frequency: 1-2 per 8-hour shift.
Duration: 5-60 minutes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.press import PressGenerator

# Realistic fault codes (PRD 5.8 table)
_FAULT_CODES = [
    101,  # Motor overload
    102,  # Inverter fault
    201,  # Ink system pressure low
    202,  # Ink pump failure
    301,  # Registration sensor error
    302,  # Web guide sensor error
    401,  # Safety guard opened
    402,  # Emergency stop pressed
    501,  # Dryer overheat
    502,  # Dryer fan failure
]


class UnplannedStop(Scenario):
    """Unplanned stop: immediate fault, timed recovery.

    Parameters (via ``params`` dict)
    ---------------------------------
    duration_seconds : list[int]
        [min, max] stop duration range.  Duration drawn from
        ``uniform(min, max)`` at init (default [300, 3600]).
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

        # Stop duration
        dur_range = p.get("duration_seconds", [300, 3600])
        if isinstance(dur_range, list) and len(dur_range) == 2:
            self._stop_duration = float(rng.uniform(float(dur_range[0]), float(dur_range[1])))
        else:
            self._stop_duration = float(dur_range)  # type: ignore[arg-type]

        # Pick a random fault code
        self._fault_code: int = int(rng.choice(_FAULT_CODES))

    @property
    def fault_code(self) -> int:
        """The fault code for this stop event."""
        return self._fault_code

    def duration(self) -> float:
        return self._stop_duration

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Force press into Fault state and set fault indicators."""
        press = self._find_press(engine)
        if press is None:
            self.complete(sim_time, engine)
            return

        # Force fault state -- press cascade handles speed ramp to 0
        press.state_machine.force_state("Fault")

        # Set fault_active coil and fault_code in the store
        store = engine.store
        store.set("press.fault_active", 1.0, sim_time, "bad")
        store.set("press.fault_code", float(self._fault_code), sim_time, "good")

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Wait for stop duration to elapse, then recover."""
        if self._elapsed >= self._stop_duration:
            self.complete(sim_time, engine)

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Clear fault and return to Idle for normal startup."""
        press = self._find_press(engine)
        if press is not None:
            # Transition to Idle (normal restart path: Idle -> Setup -> Running)
            press.state_machine.force_state("Idle")

        # Clear fault indicators
        store = engine.store
        store.set("press.fault_active", 0.0, sim_time, "good")
        store.set("press.fault_code", 0.0, sim_time, "good")

    def _find_press(self, engine: DataEngine) -> PressGenerator | None:
        """Find the press generator."""
        from factory_simulator.generators.press import PressGenerator as _PG

        for gen in engine.generators:
            if isinstance(gen, _PG):
                return gen
        return None
