"""Dryer temperature drift scenario.

Simulates gradual temperature drift in one dryer zone above its setpoint.
This models PID loop degradation or heating element issues that cause
subtle temperature excursions without triggering fault states.

Sequence (PRD 5.4):
1. One dryer zone's actual temperature begins drifting above its setpoint.
2. Drift rate: 0.05-0.2 C per minute.
3. Over 30-120 minutes, the zone drifts 5-15 C above setpoint.
4. press.waste_count increment rate increases by 20-50% during drift.
5. After drift duration, temperature returns to setpoint.

Frequency: 1-2 per shift (configurable).
Duration: 30-120 minutes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.press import PressGenerator


class DryerDrift(Scenario):
    """Dryer temperature drift: gradual setpoint deviation with waste impact.

    The scenario overrides the affected dryer zone's ``FirstOrderLagModel``
    internal ``_value`` each tick to produce a growing offset above the
    configured setpoint.  The lag model's ``generate()`` slightly corrects
    toward setpoint when it fires (~4% per call with tau=120 s), but the
    scenario re-applies the offset on the next tick, maintaining the drift.

    Waste rate is increased by modifying ``CounterModel._rate`` on the
    press waste counter.

    On completion, waste rate is restored and the lag model is left to
    naturally recover toward its setpoint (tau=120 s gives ~10 min recovery).

    Parameters (via ``params`` dict)
    ---------------------------------
    drift_duration_range : list[float]
        [min, max] drift duration in seconds (default [1800, 7200]
        = 30-120 minutes per PRD 5.4).
    drift_range : list[float]
        [min, max] total maximum drift in degrees C (default [5.0, 15.0]).
    drift_rate_range : list[float]
        [min, max] drift rate in degrees C per minute
        (default [0.05, 0.2]).
    waste_increase_range : list[float]
        [min, max] fractional waste rate multiplier
        (default [1.2, 1.5] = 20-50% increase).
    zone : int | None
        Which dryer zone to affect (1, 2, or 3).  None = random
        (default None).
    """

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Drift duration (PRD: 30-120 min)
        dur_range = p.get("drift_duration_range", [1800.0, 7200.0])
        if isinstance(dur_range, list) and len(dur_range) == 2:
            self._drift_duration = float(
                rng.uniform(float(dur_range[0]), float(dur_range[1]))
            )
        else:
            self._drift_duration = float(dur_range)  # type: ignore[arg-type]

        # Max drift (PRD: 5-15 C)
        drift_range = p.get("drift_range", [5.0, 15.0])
        if isinstance(drift_range, list) and len(drift_range) == 2:
            self._max_drift = float(
                rng.uniform(float(drift_range[0]), float(drift_range[1]))
            )
        else:
            self._max_drift = float(drift_range)  # type: ignore[arg-type]

        # Drift rate (PRD: 0.05-0.2 C per minute)
        rate_range = p.get("drift_rate_range", [0.05, 0.2])
        if isinstance(rate_range, list) and len(rate_range) == 2:
            self._drift_rate = float(
                rng.uniform(float(rate_range[0]), float(rate_range[1]))
            )
        else:
            self._drift_rate = float(rate_range)  # type: ignore[arg-type]

        # Waste rate multiplier (PRD: 20-50% increase)
        waste_range = p.get("waste_increase_range", [1.2, 1.5])
        if isinstance(waste_range, list) and len(waste_range) == 2:
            self._waste_multiplier = float(
                rng.uniform(float(waste_range[0]), float(waste_range[1]))
            )
        else:
            self._waste_multiplier = float(waste_range)  # type: ignore[arg-type]

        # Zone selection (1, 2, or 3)
        zone_param = p.get("zone")
        if zone_param is not None:
            self._zone: int = int(zone_param)  # type: ignore[call-overload]
        else:
            self._zone = int(rng.integers(1, 4))  # 1, 2, or 3

        # Saved state for restore on completion
        self._press: PressGenerator | None = None
        self._saved_waste_rate: float = 0.0
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
    def waste_multiplier(self) -> float:
        """Waste rate multiplier during drift."""
        return self._waste_multiplier

    @property
    def zone(self) -> int:
        """Which dryer zone is affected (1, 2, or 3)."""
        return self._zone

    def duration(self) -> float:
        """Total planned duration of the drift scenario."""
        return self._drift_duration

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Begin dryer drift: save state, increase waste rate."""
        press = self._find_press(engine)
        if press is None:
            self.complete(sim_time, engine)
            return

        self._press = press

        # Save original waste rate for restore
        self._saved_waste_rate = press._waste_count._rate

        # Increase waste rate (PRD 5.4: 20-50% increase)
        press._waste_count._rate = self._saved_waste_rate * self._waste_multiplier

        # Record the current setpoint for the affected zone
        dryer_model = self._get_dryer_model(press)
        self._original_setpoint = dryer_model.setpoint

        # Ground truth: temperature drift anomaly (PRD 4.7)
        gt = engine.ground_truth
        if gt is not None:
            signal = f"press.dryer_zone{self._zone}_temp"
            gt.log_signal_anomaly(
                sim_time, signal, "drift",
                self._original_setpoint,
                [self._original_setpoint - 5.0, self._original_setpoint + 5.0],
            )

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Apply growing temperature drift offset each tick."""
        if self._press is None:
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

        # Override the dryer temperature model's internal value.
        # The scenario runs before generators (PRD 8.2 step 3), so this
        # value will be used as the starting point when the generator's
        # generate() fires.  The lag model's correction (~4% per fire)
        # is negligible; the scenario re-applies on the next tick.
        dryer_model = self._get_dryer_model(self._press)
        dryer_model._value = self._original_setpoint + drift_offset

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Restore waste rate.  Lag model recovers naturally."""
        press = self._press or self._find_press(engine)
        if press is not None:
            # Restore original waste rate
            press._waste_count._rate = self._saved_waste_rate
            # No explicit temperature restore: the lag model's generate()
            # will track back to setpoint naturally (tau=120 s, ~10 min).

    # -- Helpers ---------------------------------------------------------------

    def _get_dryer_model(self, press: PressGenerator) -> Any:
        """Get the FirstOrderLagModel for the affected zone."""
        if self._zone == 1:
            return press._dryer_temp_1
        elif self._zone == 2:
            return press._dryer_temp_2
        else:
            return press._dryer_temp_3

    def _find_press(self, engine: DataEngine) -> PressGenerator | None:
        """Find the press generator in the engine."""
        from factory_simulator.generators.press import PressGenerator as _PG

        for gen in engine.generators:
            if isinstance(gen, _PG):
                return gen
        return None
