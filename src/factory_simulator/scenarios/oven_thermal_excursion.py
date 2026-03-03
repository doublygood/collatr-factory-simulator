"""Oven thermal excursion scenario (F&B).

Simulates gradual temperature drift in one oven zone above its setpoint.
This models PID loop degradation or heating element issues that cause
subtle temperature excursions at oven scale (setpoints 160-220 C).

Sequence (PRD 5.14.2):
1. One oven zone drifts from its setpoint. Drift rate: 0.1-0.3 C per minute.
2. Adjacent zones respond via thermal coupling (0.05 factor) which is handled
   naturally by the oven generator each tick.
3. Product temperature at the exit deviates from target.
4. After drift duration, the zone returns to setpoint via natural lag recovery.

Analogous to the packaging DryerDrift but at oven scale.

Frequency: 1-2 per shift.
Duration: 30-90 minutes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.oven import OvenGenerator


class OvenThermalExcursion(Scenario):
    """Oven zone temperature drift: gradual setpoint deviation.

    The scenario overrides the affected oven zone's ``FirstOrderLagModel``
    internal ``_value`` each tick to produce a growing offset above the
    configured setpoint.  The lag model's ``generate()`` slightly corrects
    toward setpoint when it fires, but the scenario re-applies the offset
    on the next tick, maintaining the drift.

    Adjacent zones experience the coupling effect naturally via the oven
    generator's ``_update_zone_setpoints()`` which uses ``_prev_zone_temps``
    (PRD 5.14.2: thermal coupling factor 0.05).

    On completion, the zone lag model is left to recover naturally to its
    setpoint (no explicit reset).

    Parameters (via ``params`` dict)
    ---------------------------------
    drift_duration_range : list[float]
        [min, max] drift duration in seconds (default [1800, 5400]
        = 30-90 minutes per PRD 5.14.2).
    drift_range : list[float]
        [min, max] total maximum drift in degrees C (default [3.0, 10.0]).
    drift_rate_range : list[float]
        [min, max] drift rate in degrees C per minute
        (default [0.1, 0.3]).
    zone : int | None
        Which oven zone to affect (1, 2, or 3).  None = random (default).
    """

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Drift duration (PRD: 30-90 min)
        dur_range = p.get("drift_duration_range", [1800.0, 5400.0])
        if isinstance(dur_range, list) and len(dur_range) == 2:
            self._drift_duration = float(
                rng.uniform(float(dur_range[0]), float(dur_range[1]))
            )
        else:
            self._drift_duration = float(dur_range)  # type: ignore[arg-type]

        # Max drift (PRD: 3-10 C at oven scale)
        drift_range = p.get("drift_range", [3.0, 10.0])
        if isinstance(drift_range, list) and len(drift_range) == 2:
            self._max_drift = float(
                rng.uniform(float(drift_range[0]), float(drift_range[1]))
            )
        else:
            self._max_drift = float(drift_range)  # type: ignore[arg-type]

        # Drift rate (PRD: 0.1-0.3 C per minute)
        rate_range = p.get("drift_rate_range", [0.1, 0.3])
        if isinstance(rate_range, list) and len(rate_range) == 2:
            self._drift_rate = float(
                rng.uniform(float(rate_range[0]), float(rate_range[1]))
            )
        else:
            self._drift_rate = float(rate_range)  # type: ignore[arg-type]

        # Zone selection (1, 2, or 3)
        zone_param = p.get("zone")
        if zone_param is not None:
            self._zone: int = int(zone_param)  # type: ignore[call-overload]
        else:
            self._zone = int(rng.integers(1, 4))  # 1, 2, or 3

        # Saved state
        self._oven: OvenGenerator | None = None
        self._original_setpoint: float = 0.0

    # -- Public properties for testing -----------------------------------------

    @property
    def drift_duration(self) -> float:
        """Duration of the drift period in seconds."""
        return self._drift_duration

    @property
    def max_drift(self) -> float:
        """Maximum temperature drift in degrees C."""
        return self._max_drift

    @property
    def drift_rate(self) -> float:
        """Drift rate in degrees C per minute."""
        return self._drift_rate

    @property
    def zone(self) -> int:
        """Which oven zone is affected (1, 2, or 3)."""
        return self._zone

    def duration(self) -> float:
        """Total planned duration of the drift scenario."""
        return self._drift_duration

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Begin oven thermal excursion: save original setpoint."""
        oven = self._find_oven(engine)
        if oven is None:
            self.complete(sim_time, engine)
            return

        self._oven = oven

        # Record original setpoint for the affected zone
        zone_model = oven.zone_temp_models[self._zone - 1]
        self._original_setpoint = zone_model.setpoint

        # Ground truth: temperature drift anomaly (PRD 4.7)
        gt = engine.ground_truth
        if gt is not None:
            signal = f"oven.zone_{self._zone}_temp"
            gt.log_signal_anomaly(
                sim_time, signal, "drift",
                self._original_setpoint,
                [self._original_setpoint - 5.0, self._original_setpoint + 15.0],
            )

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Apply growing temperature drift offset each tick."""
        if self._oven is None:
            self.complete(sim_time, engine)
            return

        # Check if drift duration is complete
        if self._elapsed > self._drift_duration:
            self.complete(sim_time, engine)
            return

        # Calculate current drift offset (linear ramp, capped at max_drift)
        # drift_rate is in C/minute, elapsed is in seconds
        drift_offset = min(
            self._drift_rate * self._elapsed / 60.0,
            self._max_drift,
        )

        # Override the zone temperature model's internal value.
        # The scenario runs before generators (PRD 8.2 step 3), so this
        # value will be used as the starting point when the generator fires.
        # The lag model's correction is negligible; the scenario re-applies
        # on the next tick.
        zone_model = self._oven.zone_temp_models[self._zone - 1]
        zone_model._value = self._original_setpoint + drift_offset

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """No explicit restore: the lag model recovers naturally to setpoint."""
        # The zone temp lag model will track back to the configured setpoint
        # via the oven generator's normal generate() calls.
        pass

    # -- Helpers ---------------------------------------------------------------

    def _find_oven(self, engine: DataEngine) -> OvenGenerator | None:
        """Find the oven generator in the engine."""
        from factory_simulator.generators.oven import OvenGenerator as _OG

        for gen in engine.generators:
            if isinstance(gen, _OG):
                return gen
        return None
