"""Motor bearing wear scenario.

Simulates gradual exponential degradation of the press main drive motor
bearing.  Increases vibration on all three axes and motor current following
the hockey-stick curve observed in the IMS/NASA bearing run-to-failure dataset.

Sequence (PRD 5.5):
1. vibration.main_drive_x/y/z baseline increases: base_rate * exp(k * t_hours).
2. Warning threshold (15-20 mm/s) reached after 1-2 weeks (simulated).
3. Alarm threshold (25-40 mm/s) reached after 3-5 weeks.
4. press.main_drive_current increases by 1-5% following the same curve.
5. Optional failure culmination: machine_state -> Fault with vibration spike.

This scenario runs at a much longer timescale than other scenarios.
Priority: background (never preempted, never deferred).

Frequency: One event over the full sim duration; controlled by start_after_hours.
Duration: Configured by duration_hours (default 336 = 2 weeks).

PRD Reference: Section 5.5
CLAUDE.md Rule 6: uses sim_time/elapsed (simulation clock), never wall clock.
CLAUDE.md Rule 12: no global state, all state via instance variables.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, ClassVar

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.press import PressGenerator
    from factory_simulator.generators.vibration import VibrationGenerator


class BearingWear(Scenario):
    """Motor bearing wear: exponential vibration and current degradation.

    Parameters (via ``params`` dict)
    ---------------------------------
    base_rate : list[float]
        [min, max] initial increase magnitude in mm/s (default [0.001, 0.005]).
        The vibration increase at elapsed_hours T is: base_rate * exp(k * T).
    acceleration_k : list[float]
        [min, max] exponential acceleration constant (default [0.005, 0.01]).
    warning_threshold : float
        Vibration level (mm/s) for warning ground truth log (default 15.0).
    alarm_threshold : float
        Vibration level (mm/s) for alarm ground truth log (default 25.0).
    current_increase_percent : list[float]
        [min, max] maximum current increase as % of base current
        (default [1.0, 5.0]).  Follows the same exponential curve as vibration.
    culminate_in_failure : bool
        If True, force press to Fault when vibration reaches failure_vibration
        (default False).
    failure_vibration : list[float]
        [min, max] vibration level (mm/s) at which failure occurs
        (default [40.0, 50.0]).
    duration_hours : float
        Scenario duration in hours (default 336.0 = 2 weeks).
    """

    priority: ClassVar[str] = "background"

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Exponential model parameters (PRD 5.5)
        base_rate_range = p.get("base_rate", [0.001, 0.005])
        if isinstance(base_rate_range, list) and len(base_rate_range) == 2:
            self._base_rate = float(
                rng.uniform(float(base_rate_range[0]), float(base_rate_range[1]))
            )
        else:
            self._base_rate = float(base_rate_range)  # type: ignore[arg-type]

        k_range = p.get("acceleration_k", [0.005, 0.01])
        if isinstance(k_range, list) and len(k_range) == 2:
            self._k = float(rng.uniform(float(k_range[0]), float(k_range[1])))
        else:
            self._k = float(k_range)  # type: ignore[arg-type]

        # Thresholds (PRD 5.5)
        self._warning_threshold = float(p.get("warning_threshold", 15.0))  # type: ignore[arg-type]
        self._alarm_threshold = float(p.get("alarm_threshold", 25.0))  # type: ignore[arg-type]

        # Current increase (same exponential curve, smaller magnitude)
        current_range = p.get("current_increase_percent", [1.0, 5.0])
        if isinstance(current_range, list) and len(current_range) == 2:
            max_pct = float(
                rng.uniform(float(current_range[0]), float(current_range[1]))
            )
        else:
            max_pct = float(current_range)  # type: ignore[arg-type]
        # Store as fraction of base current per exp unit
        self._current_factor = max_pct / 100.0

        # Failure settings
        self._culminate_in_failure = bool(p.get("culminate_in_failure", False))
        failure_range = p.get("failure_vibration", [40.0, 50.0])
        if isinstance(failure_range, list) and len(failure_range) == 2:
            self._failure_vibration = float(
                rng.uniform(float(failure_range[0]), float(failure_range[1]))
            )
        else:
            self._failure_vibration = float(failure_range)  # type: ignore[arg-type]

        # Duration
        duration_hours = float(p.get("duration_hours", 336.0))  # type: ignore[arg-type]
        self._duration_s = duration_hours * 3600.0

        # Saved generator state (populated on activate)
        self._vibration_gen: VibrationGenerator | None = None
        self._press: PressGenerator | None = None
        self._saved_vib_targets: dict[str, float] = {}
        self._saved_current_base: float = 0.0

        # Threshold logging flags (fire once each)
        self._warning_logged: bool = False
        self._alarm_logged: bool = False

    # -- Public properties for testing ----------------------------------------

    @property
    def base_rate(self) -> float:
        """Initial vibration increase constant (mm/s)."""
        return self._base_rate

    @property
    def k(self) -> float:
        """Exponential acceleration constant."""
        return self._k

    @property
    def warning_threshold(self) -> float:
        """Vibration warning threshold (mm/s)."""
        return self._warning_threshold

    @property
    def alarm_threshold(self) -> float:
        """Vibration alarm threshold (mm/s)."""
        return self._alarm_threshold

    @property
    def current_factor(self) -> float:
        """Current increase factor (fraction of base current per exp unit)."""
        return self._current_factor

    @property
    def culminate_in_failure(self) -> bool:
        """Whether the scenario culminates in a machine failure."""
        return self._culminate_in_failure

    @property
    def failure_vibration(self) -> float:
        """Vibration level that triggers failure (mm/s)."""
        return self._failure_vibration

    def duration(self) -> float:
        """Total planned scenario duration in seconds."""
        return self._duration_s

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Save baseline generator state and log scenario start."""
        self._vibration_gen = self._find_vibration(engine)
        self._press = self._find_press(engine)

        # Save original vibration targets (baseline mm/s for each axis)
        if self._vibration_gen is not None:
            for name, model in self._vibration_gen._models.items():
                self._saved_vib_targets[name] = model._target

        # Save original current base (intercept of correlated follower)
        if self._press is not None:
            self._saved_current_base = self._press._main_drive_current._base

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Apply exponential degradation each tick.

        PRD 5.5 formula:
            vibration_increase = base_rate * exp(k * elapsed_hours)
            current_pct_increase = current_factor * exp(k * elapsed_hours)
        """
        elapsed_hours = self._elapsed / 3600.0

        # Vibration increase (PRD 5.5): hockey-stick exponential curve
        vib_increase = self._base_rate * math.exp(self._k * elapsed_hours)

        # Apply to all three vibration axis models
        vib = self._vibration_gen
        if vib is not None:
            for name, model in vib._models.items():
                saved = self._saved_vib_targets.get(name, model._target)
                model._target = saved + vib_increase

        # Current increase: same exponential at smaller magnitude
        # Offset added to _base (intercept) of the correlated follower
        current_offset = self._saved_current_base * self._current_factor * math.exp(
            self._k * elapsed_hours
        )
        press = self._press
        if press is not None:
            press._main_drive_current._base = (
                self._saved_current_base + current_offset
            )

        # Threshold logging (fire once per threshold crossing)
        self._check_thresholds(sim_time, engine, vib_increase)

        # Check failure culmination
        if self._culminate_in_failure and vib_increase >= self._failure_vibration:
            self._trigger_failure(sim_time, engine, vib_increase)
            self.complete(sim_time, engine)
            return

        # Check duration expiry
        if self._elapsed >= self._duration_s:
            self.complete(sim_time, engine)

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Restore original generator state."""
        # Restore vibration targets
        vib = self._vibration_gen
        if vib is not None:
            for name, target in self._saved_vib_targets.items():
                if name in vib._models:
                    vib._models[name]._target = target

        # Restore current base
        press = self._press
        if press is not None:
            press._main_drive_current._base = self._saved_current_base

    # -- Helpers ---------------------------------------------------------------

    def _check_thresholds(
        self,
        sim_time: float,
        engine: DataEngine,
        vib_increase: float,
    ) -> None:
        """Log ground truth events when thresholds are crossed (once each)."""
        gt = engine.ground_truth

        if not self._warning_logged and vib_increase >= self._warning_threshold:
            self._warning_logged = True
            if gt is not None:
                for axis in ("vibration.main_drive_x", "vibration.main_drive_y",
                             "vibration.main_drive_z"):
                    gt.log_signal_anomaly(
                        sim_time, axis, "threshold_warning",
                        vib_increase, [0.0, self._warning_threshold],
                    )

        if not self._alarm_logged and vib_increase >= self._alarm_threshold:
            self._alarm_logged = True
            if gt is not None:
                for axis in ("vibration.main_drive_x", "vibration.main_drive_y",
                             "vibration.main_drive_z"):
                    gt.log_signal_anomaly(
                        sim_time, axis, "threshold_alarm",
                        vib_increase, [0.0, self._alarm_threshold],
                    )

    def _trigger_failure(
        self,
        sim_time: float,
        engine: DataEngine,
        vib_increase: float,
    ) -> None:
        """Force vibration spike and press fault on bearing failure (PRD 5.5 step 5)."""
        from factory_simulator.generators.press import STATE_FAULT

        # Spike vibration models to failure level
        vib = self._vibration_gen
        if vib is not None:
            for name, model in vib._models.items():
                saved = self._saved_vib_targets.get(name, model._target)
                model._target = saved + self._failure_vibration

        # Force press to Fault state
        press = self._press
        if press is not None:
            press.state_machine.force_state("Fault")
            press._prev_state = STATE_FAULT

        # Ground truth: bearing failure state change
        gt = engine.ground_truth
        if gt is not None:
            gt.log_state_change(
                sim_time, "press.machine_state", 2, 4,
            )
            gt.log_signal_anomaly(
                sim_time, "vibration.main_drive_x", "bearing_failure",
                self._failure_vibration, [0.0, self._alarm_threshold],
            )

    def _find_vibration(self, engine: DataEngine) -> VibrationGenerator | None:
        """Locate the VibrationGenerator in the engine."""
        from factory_simulator.generators.vibration import VibrationGenerator as _VG

        for gen in engine.generators:
            if isinstance(gen, _VG):
                return gen
        return None

    def _find_press(self, engine: DataEngine) -> PressGenerator | None:
        """Locate the PressGenerator in the engine."""
        from factory_simulator.generators.press import PressGenerator as _PG

        for gen in engine.generators:
            if isinstance(gen, _PG):
                return gen
        return None
