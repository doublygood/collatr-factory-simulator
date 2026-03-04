"""Counter Increment signal model.

The signal increments at a rate proportional to machine speed:

    value = value + rate * speed * dt

Models counters such as impression counts, good counts, waste counts,
cumulative energy, and ink consumption.  Supports rollover (wrap to
zero), reset on job change, and optional max-before-reset.

PRD Reference: Section 4.2.6
CLAUDE.md Rule 6: uses sim_time and dt, never wall clock.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

import numpy as np

from factory_simulator.models.base import SignalModel


def _float_param(params: dict[str, object], key: str, default: float) -> float:
    """Extract a float parameter from the params dict."""
    raw = params.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


def _bool_param(params: dict[str, object], key: str, default: bool) -> bool:
    """Extract a bool parameter from the params dict."""
    raw = params.get(key, default)
    if raw is None:
        return default
    return bool(raw)


class CounterModel(SignalModel):
    """Counter that increments proportional to speed.

    At each tick the value increases by:

        value += rate * speed * dt

    Where *speed* is the current machine speed set via :meth:`set_speed`
    (typically ``press.line_speed`` in m/min).  *rate* has units of
    "increments per speed-unit per second", so:

        rate * speed * dt = increments per tick

    Features:

    - **Rollover**: When the counter reaches ``rollover_value`` it wraps
      to zero.  *None* disables rollover.
    - **Reset on job change**: Call :meth:`reset_counter` to zero the
      counter (used by scenario engine during job changeover when
      ``reset_on_job_change`` is configured).
    - **Max before reset**: When set, the counter automatically resets
      to zero upon reaching this value (simulates operator resets).

    Parameters (via ``params`` dict)
    ---------------------------------
    rate : float
        Increment rate per speed-unit per second (default 1.0).
        Must be >= 0.
    rollover_value : float | None
        Counter wraps to zero at this value.  *None* disables.
        Also accepts ``rollover`` as an alias.
    reset_on_job_change : bool
        Whether the counter resets to zero on job changeover
        (default False).
    max_before_reset : float | None
        Auto-reset threshold.  *None* disables (default).
    initial_value : float
        Starting counter value (default 0.0).
    """

    def __init__(
        self,
        params: dict[str, object],
        rng: np.random.Generator,
    ) -> None:
        super().__init__(params, rng)

        self._rate = _float_param(params, "rate", 1.0)
        if self._rate < 0.0:
            raise ValueError("rate must be >= 0")

        # Accept both "rollover_value" and "rollover" (config uses "rollover")
        rollover_raw = params.get("rollover_value", params.get("rollover"))
        self._rollover_value: float | None = (
            float(rollover_raw) if rollover_raw is not None else None  # type: ignore[arg-type]
        )
        if self._rollover_value is not None and self._rollover_value <= 0.0:
            raise ValueError("rollover_value must be > 0")

        self._reset_on_job_change = _bool_param(params, "reset_on_job_change", False)

        max_reset_raw = params.get("max_before_reset")
        self._max_before_reset: float | None = (
            float(max_reset_raw) if max_reset_raw is not None else None  # type: ignore[arg-type]
        )
        if self._max_before_reset is not None and self._max_before_reset <= 0.0:
            raise ValueError("max_before_reset must be > 0")

        self._initial_value = _float_param(params, "initial_value", 0.0)
        if self._initial_value < 0.0:
            raise ValueError("initial_value must be >= 0")

        self._value: float = self._initial_value
        self._speed: float = 0.0
        self._rollover_occurred: bool = False

    @property
    def rate(self) -> float:
        """Increment rate per speed-unit per second."""
        return self._rate

    @property
    def rollover_value(self) -> float | None:
        """Counter rollover threshold (None = disabled)."""
        return self._rollover_value

    @property
    def reset_on_job_change(self) -> bool:
        """Whether the counter resets on job changeover."""
        return self._reset_on_job_change

    @property
    def max_before_reset(self) -> float | None:
        """Auto-reset threshold (None = disabled)."""
        return self._max_before_reset

    @property
    def rollover_occurred(self) -> bool:
        """True if rollover fired on the most recent ``generate()`` call."""
        return self._rollover_occurred

    @property
    def value(self) -> float:
        """Current counter value."""
        return self._value

    @property
    def speed(self) -> float:
        """Current speed input."""
        return self._speed

    def set_rollover_value(self, value: float | None) -> None:
        """Override the rollover threshold at runtime.

        Used by :class:`~factory_simulator.engine.data_engine.DataEngine`
        to apply ``DataQualityConfig.counter_rollover`` overrides (PRD 10.4).

        Parameters
        ----------
        value:
            New rollover threshold, or *None* to disable rollover.
        """
        if value is not None and value <= 0.0:
            raise ValueError("rollover_value must be > 0")
        self._rollover_value = value

    def set_speed(self, speed: float) -> None:
        """Set the current machine speed.

        Called by the equipment generator before each ``generate()``
        call with the current line speed from the store.

        Parameters
        ----------
        speed:
            Current machine speed (e.g. line_speed in m/min).
        """
        self._speed = speed

    def generate(self, sim_time: float, dt: float) -> float:
        """Produce the next counter value.

        Applies the increment formula per PRD 4.2.6:

            value += rate * speed * dt

        Then applies rollover and max-before-reset logic.

        Parameters
        ----------
        sim_time:
            Current simulated time in seconds since start (unused but
            required by the interface).
        dt:
            Simulated time delta for this tick in seconds.

        Returns
        -------
        float
            The current counter value.
        """
        # Reset per-tick rollover flag
        self._rollover_occurred = False

        # Accumulate: rate * speed * dt
        increment = self._rate * self._speed * dt
        self._value += increment

        # Rollover: wrap to zero
        if self._rollover_value is not None and self._value >= self._rollover_value:
            self._value = self._value % self._rollover_value
            self._rollover_occurred = True

        # Max-before-reset: auto-reset to zero
        if self._max_before_reset is not None and self._value >= self._max_before_reset:
            self._value = 0.0

        return self._value

    def reset_counter(self) -> None:
        """Reset counter to zero.

        Called by the scenario engine during job changeover for counters
        with ``reset_on_job_change=True``.
        """
        self._value = 0.0

    def reset(self) -> None:
        """Reset counter to initial value and speed to zero."""
        self._value = self._initial_value
        self._speed = 0.0
