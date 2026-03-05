"""Cold chain break scenario (F&B refrigeration failure).

Simulates a refrigeration failure where the compressor locks off, causing
the cold room temperature to rise toward ambient.  The room temperature
eventually crosses the 8 °C alarm threshold, putting product at risk.
After repair, the compressor restarts and temperature recovers via the
bang-bang controller naturally.

Sequence (PRD 5.14.7):
1. ``chiller.compressor_state`` locks to 0 (refrigeration failure).
2. ``chiller.room_temp`` rises from setpoint toward ambient at the
   background heat gain rate (~0.2 °C/min, plus any environmental load).
3. ``chiller.room_temp`` crosses the 8 °C alarm threshold.
4. After repair (scenario end), compressor is released.
5. Room temperature recovers via the bang-bang controller.

Frequency: 1-2 per month.
Duration: 30-120 minutes.

PRD Reference: Section 5.14.7
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
CLAUDE.md Rule 12: No global state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.chiller import ChillerGenerator

# Alarm threshold (°C) per PRD 5.14.7
_ALARM_THRESHOLD_C = 8.0


class ColdChainBreak(Scenario):
    """Refrigeration failure: compressor locks off, room_temp rises to alarm.

    The scenario locks the compressor off via ``ChillerGenerator.compressor_forced_off``.
    The generator's background heat gain then raises the room temperature
    naturally.  The alarm threshold (8 °C) is crossed during a 30-120 minute
    failure window.  On repair, the compressor lock is released and the
    bang-bang controller brings the room back to setpoint.

    Parameters (via ``params`` dict)
    ---------------------------------
    duration_range : list[float]
        [min, max] scenario duration in seconds
        (default [1800.0, 7200.0] = 30-120 minutes per PRD 5.14.7).
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

        # Duration (PRD: 30-120 min)
        dur_range = p.get("duration_range", [1800.0, 7200.0])
        if isinstance(dur_range, list) and len(dur_range) == 2:
            self._duration = float(
                rng.uniform(float(dur_range[0]), float(dur_range[1]))
            )
        else:
            self._duration = float(dur_range)  # type: ignore[arg-type]

        # Saved generator reference and original compressor forced-off state
        self._chiller: ChillerGenerator | None = None
        self._saved_forced_off: bool = False

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
        """Begin refrigeration failure: lock compressor off, log ground truth."""
        chiller = self._find_chiller(engine)

        if chiller is None:
            # No chiller in this profile (packaging) — complete immediately
            self.complete(sim_time, engine)
            return

        self._chiller = chiller

        # Save original forced-off state (normally False)
        self._saved_forced_off = chiller.compressor_forced_off

        # Lock compressor off — overrides bang-bang control
        chiller.compressor_forced_off = True

        # Ground truth: state changes on activation
        gt = engine.ground_truth
        if gt is not None:
            gt.log_state_change(
                sim_time,
                "chiller.compressor_state",
                "1",
                "0",
            )
            gt.log_signal_anomaly(
                sim_time,
                "chiller.room_temp",
                "drift",
                chiller.room_temp,
                [chiller.room_temp, _ALARM_THRESHOLD_C],
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
        """Repair: release compressor lock, bang-bang recovers temperature."""
        chiller = self._chiller or self._find_chiller(engine)
        if chiller is not None:
            chiller.compressor_forced_off = self._saved_forced_off

        # Ground truth: state change on completion
        gt = engine.ground_truth
        if gt is not None:
            gt.log_state_change(
                sim_time,
                "chiller.compressor_state",
                "0",
                "1",
            )

    # -- Helpers ---------------------------------------------------------------

    def _find_chiller(self, engine: DataEngine) -> ChillerGenerator | None:
        """Find the ChillerGenerator in the engine."""
        from factory_simulator.generators.chiller import ChillerGenerator as _CG

        for gen in engine.generators:
            if isinstance(gen, _CG):
                return gen
        return None
