"""Ink viscosity excursion scenario.

Simulates ink viscosity drifting outside the normal operating range
(below 18 seconds = too thin, or above 45 seconds = too thick).
This causes increased registration error and higher waste rates.

Sequence (PRD 5.6):
1. press.ink_viscosity drifts below 18 s (thin) or above 45 s (thick).
2. press.registration_error_x/y increases during the excursion.
3. press.waste_count increment rate increases by 10-30%.
4. After excursion duration, viscosity returns to normal range.

Frequency: 2-3 per shift.
Duration: 5-30 minutes.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.press import PressGenerator


class _Direction(Enum):
    """Excursion direction."""

    THIN = auto()  # viscosity drops below 18 s
    THICK = auto()  # viscosity rises above 45 s


class InkExcursion(Scenario):
    """Ink viscosity excursion: drift outside normal range with quality impact.

    The scenario gradually overrides the ``SteadyStateModel._target`` on the
    press ink viscosity model to push the value toward an extreme.  During
    the excursion, registration error drift rate is increased and waste rate
    is raised.

    On completion, all modified parameters are restored to their original
    values.  The viscosity model naturally returns to its configured target
    on the next ``generate()`` call.

    Parameters (via ``params`` dict)
    ---------------------------------
    duration_range : list[float]
        [min, max] excursion duration in seconds (default [300, 1800]
        = 5-30 minutes per PRD 5.6).
    direction : str | None
        "thin" or "thick".  None = random (default None).
    thin_target_range : list[float]
        [min, max] viscosity target during thin excursion (default [14.0, 17.0]).
    thick_target_range : list[float]
        [min, max] viscosity target during thick excursion (default [46.0, 50.0]).
    reg_error_multiplier_range : list[float]
        [min, max] multiplier for registration error drift_rate
        (default [3.0, 5.0]).
    waste_increase_range : list[float]
        [min, max] fractional waste rate multiplier
        (default [1.1, 1.3] = 10-30% increase per PRD 5.6).
    ramp_fraction : float
        Fraction of duration spent ramping to excursion target
        (default 0.3 = 30%).  Remainder holds at target.
    """

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Excursion duration (PRD: 5-30 min)
        dur_range = p.get("duration_range", [300.0, 1800.0])
        if isinstance(dur_range, list) and len(dur_range) == 2:
            self._excursion_duration = float(
                rng.uniform(float(dur_range[0]), float(dur_range[1]))
            )
        else:
            self._excursion_duration = float(dur_range)  # type: ignore[arg-type]

        # Direction: thin or thick
        dir_param = p.get("direction")
        if dir_param == "thin":
            self._direction = _Direction.THIN
        elif dir_param == "thick":
            self._direction = _Direction.THICK
        else:
            self._direction = (
                _Direction.THIN if rng.random() < 0.5 else _Direction.THICK
            )

        # Target viscosity during excursion
        if self._direction == _Direction.THIN:
            target_range = p.get("thin_target_range", [14.0, 17.0])
        else:
            target_range = p.get("thick_target_range", [46.0, 50.0])

        if isinstance(target_range, list) and len(target_range) == 2:
            self._excursion_target = float(
                rng.uniform(float(target_range[0]), float(target_range[1]))
            )
        else:
            self._excursion_target = float(target_range)  # type: ignore[arg-type]

        # Registration error drift multiplier
        reg_range = p.get("reg_error_multiplier_range", [3.0, 5.0])
        if isinstance(reg_range, list) and len(reg_range) == 2:
            self._reg_error_multiplier = float(
                rng.uniform(float(reg_range[0]), float(reg_range[1]))
            )
        else:
            self._reg_error_multiplier = float(reg_range)  # type: ignore[arg-type]

        # Waste rate multiplier (PRD: 10-30% increase)
        waste_range = p.get("waste_increase_range", [1.1, 1.3])
        if isinstance(waste_range, list) and len(waste_range) == 2:
            self._waste_multiplier = float(
                rng.uniform(float(waste_range[0]), float(waste_range[1]))
            )
        else:
            self._waste_multiplier = float(waste_range)  # type: ignore[arg-type]

        # Ramp fraction: portion of duration spent ramping to target
        raw_ramp = p.get("ramp_fraction", 0.3)
        self._ramp_fraction = float(raw_ramp)  # type: ignore[arg-type]

        # Saved state for restore on completion
        self._press: PressGenerator | None = None
        self._saved_visc_target: float = 0.0
        self._saved_reg_x_drift_rate: float = 0.0
        self._saved_reg_y_drift_rate: float = 0.0
        self._saved_waste_rate: float = 0.0

    # -- Public properties for testing -----------------------------------------

    @property
    def excursion_duration(self) -> float:
        """Duration of the excursion in seconds."""
        return self._excursion_duration

    @property
    def direction(self) -> _Direction:
        """Excursion direction (THIN or THICK)."""
        return self._direction

    @property
    def excursion_target(self) -> float:
        """Target viscosity during the excursion."""
        return self._excursion_target

    @property
    def reg_error_multiplier(self) -> float:
        """Registration error drift rate multiplier."""
        return self._reg_error_multiplier

    @property
    def waste_multiplier(self) -> float:
        """Waste rate multiplier during excursion."""
        return self._waste_multiplier

    def duration(self) -> float:
        """Total planned duration of this scenario."""
        return self._excursion_duration

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Begin ink excursion: save state, increase waste/registration rates."""
        press = self._find_press(engine)
        if press is None:
            self.complete(sim_time, engine)
            return

        self._press = press

        # Save original values for restore
        self._saved_visc_target = press._ink_viscosity._target
        self._saved_reg_x_drift_rate = press._reg_error_x._drift_rate
        self._saved_reg_y_drift_rate = press._reg_error_y._drift_rate
        self._saved_waste_rate = press._waste_count._rate

        # Increase registration error drift rate (PRD 5.6 step 2)
        press._reg_error_x._drift_rate = (
            self._saved_reg_x_drift_rate * self._reg_error_multiplier
        )
        press._reg_error_y._drift_rate = (
            self._saved_reg_y_drift_rate * self._reg_error_multiplier
        )

        # Increase waste rate (PRD 5.6 step 3: 10-30%)
        press._waste_count._rate = self._saved_waste_rate * self._waste_multiplier

        # Ground truth: viscosity excursion anomaly (PRD 4.7)
        gt = engine.ground_truth
        if gt is not None:
            gt.log_signal_anomaly(
                sim_time, "press.ink_viscosity", "excursion",
                self._excursion_target, [18.0, 45.0],
            )

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Gradually drift viscosity target toward excursion value."""
        if self._press is None:
            self.complete(sim_time, engine)
            return

        # Check if excursion duration is complete
        if self._elapsed > self._excursion_duration:
            self.complete(sim_time, engine)
            return

        # Calculate viscosity target progression:
        # Ramp phase: linearly interpolate from original to excursion target
        # Hold phase: stay at excursion target
        ramp_time = self._excursion_duration * self._ramp_fraction
        progress = (
            self._elapsed / ramp_time
            if ramp_time > 0 and self._elapsed < ramp_time
            else 1.0
        )

        current_target = (
            self._saved_visc_target
            + (self._excursion_target - self._saved_visc_target) * progress
        )

        # Override the viscosity model's target
        self._press._ink_viscosity._target = current_target

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Restore all modified parameters."""
        press = self._press or self._find_press(engine)
        if press is not None:
            # Restore viscosity target
            press._ink_viscosity._target = self._saved_visc_target
            # Restore registration error drift rates
            press._reg_error_x._drift_rate = self._saved_reg_x_drift_rate
            press._reg_error_y._drift_rate = self._saved_reg_y_drift_rate
            # Restore waste rate
            press._waste_count._rate = self._saved_waste_rate

    # -- Helpers ---------------------------------------------------------------

    def _find_press(self, engine: DataEngine) -> PressGenerator | None:
        """Find the press generator in the engine."""
        from factory_simulator.generators.press import PressGenerator as _PG

        for gen in engine.generators:
            if isinstance(gen, _PG):
                return gen
        return None
