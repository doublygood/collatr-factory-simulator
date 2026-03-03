"""Micro-stop scenario.

Brief speed dip (5-30s) without machine state change.  The machine stays
Running (2).  Only press.line_speed dips by 30-80%.  All correlated signals
(motor current, web tension, energy) respond through existing correlations.

Sequence (PRD 5.15):
1. press.line_speed drops by 30-80% over 2-5 seconds (ramp down).
2. press.web_tension fluctuates during deceleration (via correlations).
3. After 5-30 seconds at low speed, line_speed ramps back to target over 5-15s.
4. press.waste_count increment rate increases naturally (speed-proportional).

Key: machine_state stays Running (2) throughout.  No fault code is written.
     This is the behaviour OEE systems struggle to capture.

Priority: micro (activates without checks, never preempted).
Scheduling: Poisson, 10-50 events per 8-hour shift (configurable).

PRD Reference: Section 5.15
CLAUDE.md Rule 6: uses sim_time/elapsed (simulation clock), never wall clock.
CLAUDE.md Rule 12: no global state, all state via instance variables.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.press import PressGenerator


def _range_param(
    params: dict[str, object],
    key: str,
    default: list[float],
    rng: np.random.Generator,
) -> float:
    """Sample a float uniformly from a [min, max] param entry."""
    raw = params.get(key, default)
    if isinstance(raw, list | tuple) and len(raw) == 2:
        return float(rng.uniform(float(raw[0]), float(raw[1])))
    return float(raw)  # type: ignore[arg-type]


class MicroStop(Scenario):
    """Brief line speed dip without machine state change (PRD 5.15).

    Three internal sub-phases (tracked via ``_elapsed``):

    - **RAMP_DOWN** (0 → ramp_down_s): line speed drops from the current
      running speed to ``low_speed`` via a smooth ramp.
    - **HOLD** (ramp_down_s → ramp_down_s + hold_s): speed held at
      ``low_speed``; the ramp model holds at its end value.
    - **RAMP_UP** (ramp_down_s + hold_s → total_s): speed ramps back to the
      saved target speed.

    Parameters (via ``params`` dict)
    ---------------------------------
    duration_seconds : list[float]
        [min, max] hold duration at low speed in seconds (default [5.0, 30.0]).
    speed_drop_percent : list[float]
        [min, max] speed drop as percent of press target speed
        (default [30.0, 80.0]).
    ramp_down_seconds : list[float]
        [min, max] ramp-down duration in seconds (default [2.0, 5.0]).
    ramp_up_seconds : list[float]
        [min, max] ramp-up duration in seconds (default [5.0, 15.0]).
    """

    priority: ClassVar[str] = "micro"

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)
        p = self._params

        # Draw stochastic parameters at construction for reproducibility
        self._hold_s: float = _range_param(
            p, "duration_seconds", [5.0, 30.0], rng
        )
        self._drop_pct: float = _range_param(
            p, "speed_drop_percent", [30.0, 80.0], rng
        )
        self._ramp_down_s: float = _range_param(
            p, "ramp_down_seconds", [2.0, 5.0], rng
        )
        self._ramp_up_s: float = _range_param(
            p, "ramp_up_seconds", [5.0, 15.0], rng
        )

        # Total scenario duration
        self._total_s: float = self._ramp_down_s + self._hold_s + self._ramp_up_s

        # State saved on _on_activate
        self._press: PressGenerator | None = None
        self._saved_target: float = 0.0
        self._low_speed: float = 0.0

        # Sub-phase tracking
        self._ramp_up_started: bool = False

    # -- Public properties for testing -----------------------------------------

    @property
    def hold_s(self) -> float:
        """Hold duration at low speed (seconds)."""
        return self._hold_s

    @property
    def drop_pct(self) -> float:
        """Speed drop as percent of press target speed."""
        return self._drop_pct

    @property
    def ramp_down_s(self) -> float:
        """Ramp-down duration (seconds)."""
        return self._ramp_down_s

    @property
    def ramp_up_s(self) -> float:
        """Ramp-up duration (seconds)."""
        return self._ramp_up_s

    @property
    def saved_target(self) -> float:
        """Original press target speed at activation (m/min)."""
        return self._saved_target

    @property
    def low_speed(self) -> float:
        """Low speed target during the dip (m/min)."""
        return self._low_speed

    def duration(self) -> float:
        """Total planned scenario duration (seconds)."""
        return self._total_s

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(self, sim_time: float, engine: DataEngine) -> None:
        """Save baseline speed and start the ramp-down."""
        self._press = self._find_press(engine)
        if self._press is None:
            return

        # Save the press target speed (used as baseline and for recovery)
        self._saved_target = self._press._target_speed

        # Current actual speed from the ramp model (before noise)
        current_speed = self._press._line_speed_model.value

        # Drop is relative to the configured target speed so that a micro-stop
        # that fires during ramp-up still drops to a consistent level.
        baseline = self._saved_target if self._saved_target > 0.0 else current_speed
        self._low_speed = max(0.0, baseline * (1.0 - self._drop_pct / 100.0))

        # Start smooth ramp down: current_speed → low_speed over ramp_down_s
        self._press._line_speed_model.start_ramp(
            start=current_speed,
            end=self._low_speed,
            duration=max(self._ramp_down_s, 0.1),
        )
        self._ramp_up_started = False

        # Ground truth: micro-stop start
        gt = engine.ground_truth
        if gt is not None:
            gt.log_scenario_start(
                sim_time,
                "MicroStop",
                [
                    "press.line_speed",
                    "press.web_tension",
                    "press.waste_count",
                    "press.main_drive_current",
                ],
                {
                    "hold_s": self._hold_s,
                    "drop_pct": self._drop_pct,
                    "ramp_down_s": self._ramp_down_s,
                    "ramp_up_s": self._ramp_up_s,
                    "saved_target_speed": self._saved_target,
                    "low_speed": self._low_speed,
                },
            )

    def _on_tick(self, sim_time: float, dt: float, engine: DataEngine) -> None:
        """Manage HOLD→RAMP_UP transition and scenario completion."""
        press = self._press
        if press is None:
            self.complete(sim_time, engine)
            return

        # Transition from HOLD to RAMP_UP when elapsed crosses the boundary
        ramp_up_start = self._ramp_down_s + self._hold_s
        if self._elapsed >= ramp_up_start and not self._ramp_up_started:
            press._line_speed_model.start_ramp(
                start=self._low_speed,
                end=self._saved_target,
                duration=max(self._ramp_up_s, 0.1),
            )
            self._ramp_up_started = True

        # Complete when the full duration has elapsed
        if self._elapsed >= self._total_s:
            self.complete(sim_time, engine)

    def _on_complete(self, sim_time: float, engine: DataEngine) -> None:
        """Restore speed target if ramp has not fully recovered."""
        press = self._press
        if press is not None and self._saved_target > 0.0:
            current = press._line_speed_model.value
            # If speed hasn't recovered, initiate a quick ramp back
            if abs(current - self._saved_target) > 1.0:
                press._line_speed_model.start_ramp(
                    start=current,
                    end=self._saved_target,
                    duration=max(self._ramp_up_s, 0.1),
                )

        # Ground truth: micro-stop end
        gt = engine.ground_truth
        if gt is not None:
            gt.log_scenario_end(sim_time, "MicroStop")

    # -- Helpers ---------------------------------------------------------------

    def _find_press(self, engine: DataEngine) -> PressGenerator | None:
        """Locate the PressGenerator in the engine."""
        from factory_simulator.generators.press import PressGenerator as _PG

        for gen in engine.generators:
            if isinstance(gen, _PG):
                return gen
        return None
