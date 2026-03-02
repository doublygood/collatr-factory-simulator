"""Registration drift scenario.

Simulates gradual drift of registration error (X or Y axis) beyond
normal operating range.  The drift causes increased waste when the
error exceeds 0.2 mm.  After the configured duration, auto-correction
returns the error to center.

Sequence (PRD 5.7):
1. press.registration_error_x or _y drifts beyond +/-0.3 mm.
2. Drift is gradual: 0.01-0.05 mm per second.
3. Often triggered by a speed change or temperature shift.
4. press.waste_count increment rate increases while error exceeds 0.2 mm.
5. Returns to center after auto-correction or operator intervention.

Frequency: 1-3 per shift.
Duration: 2-10 minutes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.press import PressGenerator
    from factory_simulator.models.random_walk import RandomWalkModel


class RegistrationDrift(Scenario):
    """Registration drift: gradual error increase with waste impact.

    The scenario suppresses mean-reversion on the affected axis's
    ``RandomWalkModel`` (setting ``_reversion_rate = 0``) and overrides
    ``_value`` each tick to produce a controlled linear drift from the
    walk's center.  The generator's ``generate()`` may add a random step
    when it fires, but the scenario re-applies the override on the next
    tick, keeping the drift on track.

    Waste rate is increased when the absolute error exceeds the
    ``waste_threshold`` (default 0.2 mm per PRD 5.7 step 4).

    On completion, ``_reversion_rate`` is restored and the model's
    mean-reversion naturally pulls the value back to center.

    Parameters (via ``params`` dict)
    ---------------------------------
    duration_range : list[float]
        [min, max] drift duration in seconds (default [120, 600]
        = 2-10 minutes per PRD 5.7).
    drift_rate_range : list[float]
        [min, max] drift rate in mm per second (default [0.01, 0.05]).
    axis : str | None
        "x" or "y".  None = random (default None).
    direction : int | None
        +1 or -1.  None = random (default None).
    waste_increase_range : list[float]
        [min, max] fractional waste rate multiplier while error > threshold
        (default [1.2, 1.5]).  PRD 5.7 does not give an exact range;
        these match dryer drift's 20-50% increase for consistency.
    waste_threshold : float
        Error magnitude above which waste rate increases (default 0.2 mm,
        per PRD 5.7 step 4).
    """

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Drift duration (PRD: 2-10 min)
        dur_range = p.get("duration_range", [120.0, 600.0])
        if isinstance(dur_range, list) and len(dur_range) == 2:
            self._drift_duration = float(
                rng.uniform(float(dur_range[0]), float(dur_range[1]))
            )
        else:
            self._drift_duration = float(dur_range)  # type: ignore[arg-type]

        # Drift rate (PRD: 0.01-0.05 mm/s)
        rate_range = p.get("drift_rate_range", [0.01, 0.05])
        if isinstance(rate_range, list) and len(rate_range) == 2:
            self._drift_rate = float(
                rng.uniform(float(rate_range[0]), float(rate_range[1]))
            )
        else:
            self._drift_rate = float(rate_range)  # type: ignore[arg-type]

        # Axis selection: x or y
        axis_param = p.get("axis")
        if axis_param == "x":
            self._axis = "x"
        elif axis_param == "y":
            self._axis = "y"
        else:
            self._axis = "x" if rng.random() < 0.5 else "y"

        # Drift direction: +1 or -1
        dir_param = p.get("direction")
        if dir_param is not None:
            self._direction: int = 1 if int(dir_param) > 0 else -1  # type: ignore[call-overload]
        else:
            self._direction = 1 if rng.random() < 0.5 else -1

        # Waste rate multiplier (PRD 5.7: increases while > 0.2 mm)
        waste_range = p.get("waste_increase_range", [1.2, 1.5])
        if isinstance(waste_range, list) and len(waste_range) == 2:
            self._waste_multiplier = float(
                rng.uniform(float(waste_range[0]), float(waste_range[1]))
            )
        else:
            self._waste_multiplier = float(waste_range)  # type: ignore[arg-type]

        # Waste threshold (PRD 5.7 step 4: 0.2 mm)
        raw_thresh = p.get("waste_threshold", 0.2)
        self._waste_threshold = float(raw_thresh)  # type: ignore[arg-type]

        # Saved state for restore on completion
        self._press: PressGenerator | None = None
        self._model: RandomWalkModel | None = None
        self._saved_reversion_rate: float = 0.0
        self._saved_waste_rate: float = 0.0
        self._start_value: float = 0.0
        self._center: float = 0.0
        self._waste_increased: bool = False

    # -- Public properties for testing -----------------------------------------

    @property
    def drift_duration(self) -> float:
        """Duration of the drift period in seconds."""
        return self._drift_duration

    @property
    def drift_rate(self) -> float:
        """Drift rate in mm per second."""
        return self._drift_rate

    @property
    def axis(self) -> str:
        """Which axis is affected ('x' or 'y')."""
        return self._axis

    @property
    def direction(self) -> int:
        """Drift direction (+1 or -1)."""
        return self._direction

    @property
    def waste_multiplier(self) -> float:
        """Waste rate multiplier when error exceeds threshold."""
        return self._waste_multiplier

    @property
    def waste_threshold(self) -> float:
        """Error magnitude above which waste increases (mm)."""
        return self._waste_threshold

    def duration(self) -> float:
        """Total planned duration of the drift scenario."""
        return self._drift_duration

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Begin registration drift: save state, suppress reversion."""
        press = self._find_press(engine)
        if press is None:
            self.complete(sim_time, engine)
            return

        self._press = press

        # Get the affected model
        model = self._get_model(press)
        self._model = model

        # Save original parameters for restore
        self._saved_reversion_rate = model._reversion_rate
        self._saved_waste_rate = press._waste_count._rate
        self._start_value = model._value
        self._center = model._center

        # Suppress mean-reversion so the drift is not pulled back
        model._reversion_rate = 0.0

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Apply linear drift offset each tick."""
        if self._model is None or self._press is None:
            self.complete(sim_time, engine)
            return

        # Check if drift duration is complete
        if self._elapsed > self._drift_duration:
            self.complete(sim_time, engine)
            return

        # Calculate current drift position: linear ramp from center
        drift_offset = self._direction * self._drift_rate * self._elapsed
        drift_value = self._center + drift_offset

        # Override the random walk's internal value
        self._model._value = drift_value

        # Check waste threshold (PRD 5.7 step 4: increase when > 0.2 mm)
        if abs(drift_value - self._center) > self._waste_threshold:
            if not self._waste_increased:
                self._press._waste_count._rate = (
                    self._saved_waste_rate * self._waste_multiplier
                )
                self._waste_increased = True
        elif self._waste_increased:
            # Below threshold again (shouldn't happen with linear drift,
            # but handle for robustness)
            self._press._waste_count._rate = self._saved_waste_rate
            self._waste_increased = False

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Restore reversion rate and waste rate."""
        press = self._press or self._find_press(engine)
        model = self._model

        if press is not None:
            # Restore waste rate
            press._waste_count._rate = self._saved_waste_rate

        if model is not None:
            # Restore reversion rate (model naturally reverts to center)
            model._reversion_rate = self._saved_reversion_rate

    # -- Helpers ---------------------------------------------------------------

    def _get_model(self, press: PressGenerator) -> RandomWalkModel:
        """Get the RandomWalkModel for the affected axis."""
        if self._axis == "x":
            return press._reg_error_x
        return press._reg_error_y

    def _find_press(self, engine: DataEngine) -> PressGenerator | None:
        """Find the press generator in the engine."""
        from factory_simulator.generators.press import PressGenerator as _PG

        for gen in engine.generators:
            if isinstance(gen, _PG):
                return gen
        return None
