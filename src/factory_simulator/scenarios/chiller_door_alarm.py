"""Chiller door alarm scenario (F&B cold room).

Simulates warm-air ingress from an open cold room door, causing the room
temperature to rise faster than normal and the compressor to cycle harder
trying to compensate.  On door close, the compressor brings the temperature
back to setpoint naturally via the bang-bang controller.

Sequence (PRD 5.14.5):
1. ``chiller.door_open`` discrete input sets to true.
2. ``chiller.room_temp`` rises at ~1.5 C/min (warm air ingress).
3. ``chiller.compressor_state`` cycles more frequently (shorter OFF periods)
   as the compressor works harder to counter the heat ingress.
4. After door close (scenario end), room temperature recovers via the
   bang-bang controller naturally tracking back to setpoint.

Frequency: 1-3 per week.
Duration: 5-20 minutes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.chiller import ChillerGenerator


class ChillerDoorAlarm(Scenario):
    """Cold room door open: room_temp rises, compressor works harder.

    The scenario sets ``chiller.door_open = True`` on the ChillerGenerator.
    The generator's built-in door-open heat rate (1.5 °C/min) raises the
    room temperature beyond what the bang-bang compressor can offset.

    On completion, ``door_open`` is set back to False.  The compressor then
    brings the temperature back to setpoint naturally.

    Parameters (via ``params`` dict)
    ---------------------------------
    duration_range : list[float]
        [min, max] scenario duration in seconds
        (default [300.0, 1200.0] = 5-20 minutes per PRD 5.14.5).
    """

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Duration (PRD: 5-20 min)
        dur_range = p.get("duration_range", [300.0, 1200.0])
        if isinstance(dur_range, list) and len(dur_range) == 2:
            self._duration = float(
                rng.uniform(float(dur_range[0]), float(dur_range[1]))
            )
        else:
            self._duration = float(dur_range)  # type: ignore[arg-type]

        # Saved chiller generator reference and original door state
        self._chiller: ChillerGenerator | None = None
        self._saved_door_open: bool = False

    # -- Public properties for testing -----------------------------------------

    @property
    def scenario_duration(self) -> float:
        """Total planned duration of the scenario in seconds."""
        return self._duration

    def duration(self) -> float:
        """Total planned duration of this scenario in seconds."""
        return self._duration

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Begin door alarm: set door_open = True, log ground truth."""
        chiller = self._find_chiller(engine)

        if chiller is None:
            # No chiller in this profile (packaging) — complete immediately
            self.complete(sim_time, engine)
            return

        self._chiller = chiller

        # Save original door state (normally False)
        self._saved_door_open = chiller.door_open

        # Open the door: generator will add extra heat each tick
        chiller.door_open = True

        # Ground truth: door open event (PRD 4.7)
        gt = engine.ground_truth
        if gt is not None:
            gt.log_signal_anomaly(
                sim_time,
                "chiller.door_open",
                "state_change",
                0.0,
                [0.0, 1.0],
            )
            gt.log_signal_anomaly(
                sim_time,
                "chiller.room_temp",
                "drift",
                chiller.room_temp,
                [chiller.room_temp, chiller.room_temp + 5.0],
            )

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Complete scenario once duration has elapsed."""
        if self._chiller is None:
            self.complete(sim_time, engine)
            return

        if self._elapsed > self._duration:
            self.complete(sim_time, engine)

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Close the door: generator bang-bang recovers temperature naturally."""
        chiller = self._chiller or self._find_chiller(engine)
        if chiller is not None:
            chiller.door_open = self._saved_door_open

    # -- Helpers ---------------------------------------------------------------

    def _find_chiller(self, engine: DataEngine) -> ChillerGenerator | None:
        """Find the ChillerGenerator in the engine."""
        from factory_simulator.generators.chiller import ChillerGenerator as _CG

        for gen in engine.generators:
            if isinstance(gen, _CG):
                return gen
        return None
