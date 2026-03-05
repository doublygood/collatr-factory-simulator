"""Ramp Up / Ramp Down signal model.

The base ramp produces a smooth linear trajectory:

    value = start + (end - start) * (elapsed / duration) + noise(0, sigma)

An optional step quantisation layer simulates operator behaviour during
manual speed-up (PRD 4.2.4).  Real press startups are not smooth -- the
operator adjusts speed in discrete steps with dwell times and small
overshoots at each step boundary.

Set ``steps=1`` to disable quantisation and produce a smooth ramp.

PRD Reference: Section 4.2.4
CLAUDE.md Rule 6: uses sim_time and dt, never wall clock.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

import math

import numpy as np

from factory_simulator.models.base import SignalModel
from factory_simulator.models.noise import NoiseGenerator

# Tolerance for floating-point accumulation when comparing elapsed to duration.
# Tick-based accumulation of small dt values (e.g. 110 x 0.1) can produce a
# result slightly less than the true sum due to IEEE 754 rounding.  A guard
# of 1 ns (1e-9 s) is far smaller than any realistic tick interval but large
# enough to absorb accumulated fp error.
_COMPLETION_EPSILON: float = 1e-9


def _float_param(params: dict[str, object], key: str, default: float) -> float:
    """Extract a float parameter from the params dict."""
    raw = params.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


class RampModel(SignalModel):
    """Ramp signal from start to end over a specified duration.

    When ``steps`` is 1, produces a smooth linear ramp.  When ``steps``
    > 1, the ramp is divided into discrete operator-style steps with
    random dwell times and overshoot at each step boundary.

    The total ramp duration is a hard cap.  If the sum of random dwell
    times exceeds the duration, all dwells are compressed proportionally.

    Parameters (via ``params`` dict)
    ---------------------------------
    start : float
        Starting value (default 0.0).
    end : float
        Ending value (default 100.0).
    duration : float
        Ramp duration in seconds (default 120.0).  Must be > 0.
    steps : int
        Number of steps (default 4, per PRD 4.2.4).
        Set to 1 for smooth linear ramp.
    step_overshoot_pct : float
        Overshoot as fraction of step size (default 0.03 = 3%).
    step_overshoot_decay_s : float
        Overshoot decay time constant in seconds (default 7.0).
    step_dwell_range : list[float, float]
        [min, max] dwell time at each step in seconds (default [15, 45]).
    """

    def __init__(
        self,
        params: dict[str, object],
        rng: np.random.Generator,
        *,
        noise: NoiseGenerator | None = None,
    ) -> None:
        super().__init__(params, rng)

        self._start_value = _float_param(params, "start", 0.0)
        self._end_value = _float_param(params, "end", 100.0)
        self._duration = _float_param(params, "duration", 120.0)
        _steps_raw = params.get("steps", 4)
        self._num_steps: int = int(_steps_raw)  # type: ignore[call-overload]
        self._overshoot_pct = _float_param(params, "step_overshoot_pct", 0.03)
        self._overshoot_decay_s = _float_param(
            params, "step_overshoot_decay_s", 7.0
        )
        self._noise = noise

        dwell_range_raw = params.get("step_dwell_range", [15.0, 45.0])
        if isinstance(dwell_range_raw, list | tuple):
            self._dwell_min = float(dwell_range_raw[0])
            self._dwell_max = float(dwell_range_raw[1])
        else:
            raise ValueError("step_dwell_range must be a list of [min, max]")

        if self._duration <= 0.0:
            raise ValueError("duration must be > 0")
        if self._num_steps < 1:
            raise ValueError("steps must be >= 1")
        if self._overshoot_decay_s <= 0.0:
            raise ValueError("step_overshoot_decay_s must be > 0")

        # Internal state
        self._elapsed: float = 0.0
        self._value: float = self._start_value
        self._current_step: int = -1
        self._step_overshoot: float = 0.0

        # Step plan (populated for stepped ramps)
        self._step_targets: list[float] = []
        self._transition_times: list[float] = []
        self._step_size: float = 0.0
        self._dwells: list[float] = []

        if self._num_steps > 1:
            self._build_step_plan()

    def _build_step_plan(self) -> None:
        """Pre-compute step targets, dwell times, and transition times.

        Step targets are evenly spaced from start to end.  Dwell times
        are drawn from a uniform distribution and compressed proportionally
        if their sum exceeds the configured duration.
        """
        ramp_range = self._end_value - self._start_value
        self._step_size = ramp_range / self._num_steps

        # Evenly-spaced targets: step 0 is start + step_size, last is end
        self._step_targets = [
            self._start_value + self._step_size * (i + 1)
            for i in range(self._num_steps)
        ]

        # Draw random dwell times
        dwells = [
            float(self._rng.uniform(self._dwell_min, self._dwell_max))
            for _ in range(self._num_steps)
        ]

        # Compress proportionally if total exceeds duration
        total = sum(dwells)
        if total > self._duration:
            scale = self._duration / total
            dwells = [d * scale for d in dwells]

        self._dwells = dwells

        # Transition times: step i begins at cumulative sum of prior dwells
        # Step 0 begins immediately (t=0)
        self._transition_times = [0.0]
        cumulative = 0.0
        for i in range(self._num_steps - 1):
            cumulative += dwells[i]
            self._transition_times.append(cumulative)

    @property
    def start_value(self) -> float:
        """Ramp start value."""
        return self._start_value

    @property
    def end_value(self) -> float:
        """Ramp end value."""
        return self._end_value

    @property
    def duration(self) -> float:
        """Ramp duration in seconds."""
        return self._duration

    @property
    def num_steps(self) -> int:
        """Number of ramp steps."""
        return self._num_steps

    @property
    def elapsed(self) -> float:
        """Elapsed time in seconds since ramp start."""
        return self._elapsed

    @property
    def complete(self) -> bool:
        """Whether the ramp has reached its end value."""
        return self._elapsed >= self._duration - _COMPLETION_EPSILON

    @property
    def value(self) -> float:
        """Current ramp value (before noise)."""
        return self._value

    def start_ramp(
        self,
        start: float | None = None,
        end: float | None = None,
        duration: float | None = None,
    ) -> None:
        """Start a new ramp, optionally with new parameters.

        Resets elapsed time and re-computes the step plan with fresh
        random dwell times.
        """
        if start is not None:
            self._start_value = start
        if end is not None:
            self._end_value = end
        if duration is not None:
            if duration <= 0.0:
                raise ValueError("duration must be > 0")
            self._duration = duration

        self._elapsed = 0.0
        self._value = self._start_value
        self._current_step = -1
        self._step_overshoot = 0.0

        if self._num_steps > 1:
            self._build_step_plan()

    def generate(self, sim_time: float, dt: float) -> float:
        """Produce ramp value for this tick.

        Parameters
        ----------
        sim_time:
            Current simulated time in seconds since start.
        dt:
            Simulated time delta for this tick in seconds.

        Returns
        -------
        float
            Signal value = ramp value + noise.
        """
        self._elapsed += dt

        if self._elapsed >= self._duration - _COMPLETION_EPSILON:
            # Ramp complete -- hold at end value (no overshoot).
            # The epsilon guards against floating-point accumulation where
            # tick-based elapsed can fall just short of the exact duration.
            self._value = self._end_value
        elif self._num_steps <= 1:
            # Smooth linear ramp
            progress = self._elapsed / self._duration
            self._value = (
                self._start_value
                + (self._end_value - self._start_value) * progress
            )
        else:
            # Stepped ramp with dwell times and overshoot
            self._update_stepped()

        result = self._value
        if self._noise is not None:
            result += self._noise.sample()

        return result

    def _update_stepped(self) -> None:
        """Update value for stepped ramp mode."""
        # Find which step we should be on (scan from last to first)
        target_step = 0
        for i in range(len(self._transition_times) - 1, -1, -1):
            if self._elapsed >= self._transition_times[i]:
                target_step = i
                break

        # Detect step transition -- replace overshoot
        if target_step > self._current_step:
            self._current_step = target_step
            self._step_overshoot = self._overshoot_pct * self._step_size

        # Base value at current step target
        base = self._step_targets[self._current_step]

        # Decaying overshoot since step began
        time_in_step = self._elapsed - self._transition_times[self._current_step]
        overshoot = 0.0
        if self._step_overshoot != 0.0 and time_in_step >= 0.0:
            overshoot = self._step_overshoot * math.exp(
                -time_in_step / self._overshoot_decay_s
            )

        self._value = base + overshoot

    def reset(self) -> None:
        """Reset ramp to start.

        Resets elapsed time and step tracking.  The step plan (dwell
        times, transition times) is preserved -- call :meth:`start_ramp`
        to re-draw dwell times.
        """
        self._elapsed = 0.0
        self._value = self._start_value
        self._current_step = -1
        self._step_overshoot = 0.0
        if self._noise is not None:
            self._noise.reset()
