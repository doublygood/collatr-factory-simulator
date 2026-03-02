"""Depletion Curve signal model.

The signal decreases over time proportional to usage:

    value -= consumption_rate * speed * dt

Models consumable levels such as ink cartridge level, unwind reel
diameter, and nozzle health.  Supports automatic refill when the
level drops below a threshold.

PRD Reference: Section 4.2.7
CLAUDE.md Rule 6: uses sim_time and dt, never wall clock.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

import numpy as np

from factory_simulator.models.base import SignalModel
from factory_simulator.models.noise import NoiseGenerator


def _float_param(params: dict[str, object], key: str, default: float) -> float:
    """Extract a float parameter from the params dict."""
    raw = params.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


class DepletionModel(SignalModel):
    """Consumable level that depletes proportional to usage.

    At each tick the value decreases by:

        value -= consumption_rate * speed * dt

    Where *speed* is the current usage driver set via :meth:`set_speed`
    (e.g. ``press.line_speed`` for unwind diameter, or print rate for
    ink level).  *consumption_rate* has units of "level per speed-unit
    per second", so:

        consumption_rate * speed * dt = level depleted per tick

    Features:

    - **Auto-refill**: When the value drops to or below
      ``refill_threshold``, the level jumps to ``refill_value``.
      Both must be configured for refill to activate.
    - **Noise**: Optional :class:`NoiseGenerator` adds measurement
      noise on top of the depletion value.

    Parameters (via ``params`` dict)
    ---------------------------------
    initial_value : float
        Starting level (default 100.0).
    consumption_rate : float
        Depletion rate per speed-unit per second (default 0.01).
        Must be >= 0.
    refill_threshold : float | None
        Level at which auto-refill triggers.  *None* disables refill
        (default).
    refill_value : float | None
        Level after refill.  *None* disables refill (default).
    """

    def __init__(
        self,
        params: dict[str, object],
        rng: np.random.Generator,
        *,
        noise: NoiseGenerator | None = None,
    ) -> None:
        super().__init__(params, rng)

        self._initial_value = _float_param(params, "initial_value", 100.0)
        self._consumption_rate = _float_param(params, "consumption_rate", 0.01)

        if self._consumption_rate < 0.0:
            raise ValueError("consumption_rate must be >= 0")

        # Refill config -- both must be set to enable refill
        threshold_raw = params.get("refill_threshold")
        value_raw = params.get("refill_value")
        self._refill_threshold: float | None = (
            float(threshold_raw) if threshold_raw is not None else None  # type: ignore[arg-type]
        )
        self._refill_value: float | None = (
            float(value_raw) if value_raw is not None else None  # type: ignore[arg-type]
        )

        if self._refill_threshold is not None and self._refill_threshold < 0.0:
            raise ValueError("refill_threshold must be >= 0")
        if self._refill_value is not None and self._refill_value <= 0.0:
            raise ValueError("refill_value must be > 0")
        if (
            self._refill_threshold is not None
            and self._refill_value is not None
            and self._refill_threshold >= self._refill_value
        ):
            raise ValueError("refill_threshold must be < refill_value")

        self._noise = noise

        # Internal state
        self._value: float = self._initial_value
        self._speed: float = 0.0

    @property
    def initial_value(self) -> float:
        """Starting level."""
        return self._initial_value

    @property
    def consumption_rate(self) -> float:
        """Depletion rate per speed-unit per second."""
        return self._consumption_rate

    @property
    def refill_threshold(self) -> float | None:
        """Level at which refill triggers (None = disabled)."""
        return self._refill_threshold

    @property
    def refill_value(self) -> float | None:
        """Level after refill (None = disabled)."""
        return self._refill_value

    @property
    def value(self) -> float:
        """Current level (before observation noise)."""
        return self._value

    @property
    def speed(self) -> float:
        """Current speed input."""
        return self._speed

    def set_speed(self, speed: float) -> None:
        """Set the current usage driver.

        Called by the equipment generator before each ``generate()``
        call with the current usage rate (e.g. line speed, print rate).

        Parameters
        ----------
        speed:
            Current usage rate driving depletion.
        """
        self._speed = speed

    def generate(self, sim_time: float, dt: float) -> float:
        """Produce the next depletion value.

        Applies the depletion formula per PRD 4.2.7:

            value -= consumption_rate * speed * dt

        Then checks for refill threshold crossing.

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
            The current level plus any observation noise.
        """
        # Deplete: consumption_rate * speed * dt
        decrement = self._consumption_rate * self._speed * dt
        self._value -= decrement

        # Auto-refill check
        if (
            self._refill_threshold is not None
            and self._refill_value is not None
            and self._value <= self._refill_threshold
        ):
            self._value = self._refill_value

        # Observation noise (measurement noise on the level)
        result = self._value
        if self._noise is not None:
            result += self._noise.sample()

        return result

    def refill(self, level: float | None = None) -> None:
        """Manually refill to a specified level.

        Used by scenarios (e.g. reel changeover sets unwind_diameter
        back to full).

        Parameters
        ----------
        level:
            New level value.  Defaults to ``refill_value`` if set,
            otherwise ``initial_value``.
        """
        if level is not None:
            self._value = level
        elif self._refill_value is not None:
            self._value = self._refill_value
        else:
            self._value = self._initial_value

    def reset(self) -> None:
        """Reset to initial value and clear speed."""
        self._value = self._initial_value
        self._speed = 0.0
        if self._noise is not None:
            self._noise.reset()
