"""Random Walk with Mean Reversion signal model.

The signal drifts randomly but tends to return to a center value:

    delta = drift_rate * N(0, 1) - reversion_rate * (value - center)
    value = value + delta * dt

Models signals with slow drift such as ink viscosity and registration
error.  The mean-reversion term ensures the signal stays near the
center over long periods while allowing realistic short-term wandering.

PRD Reference: Section 4.2.5
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


class RandomWalkModel(SignalModel):
    """Random walk with mean reversion around a center value.

    At each tick the value moves by:

        delta = drift_rate * N(0, 1) - reversion_rate * (value - center)
        value = value + delta * dt

    The ``drift_rate`` controls how fast the signal wanders (units per
    sqrt-second -- scaled by ``sqrt(dt)`` implicitly through the discrete
    Euler step).  The ``reversion_rate`` pulls the signal back toward
    ``center``.

    An optional :class:`NoiseGenerator` adds observation noise *on top*
    of the random walk (measurement noise distinct from the walk process).

    Parameters (via ``params`` dict)
    ---------------------------------
    center : float
        Mean-reversion target (default 0.0).
    drift_rate : float
        Magnitude of random walk increments (default 1.0).  Must be >= 0.
    reversion_rate : float
        Strength of mean reversion (default 0.1).  Must be >= 0.
    initial_value : float
        Starting value.  Defaults to ``center``.
    min_clamp : float | None
        Lower physical bound.  *None* means no bound.
    max_clamp : float | None
        Upper physical bound.  *None* means no bound.
    """

    def __init__(
        self,
        params: dict[str, object],
        rng: np.random.Generator,
        *,
        noise: NoiseGenerator | None = None,
    ) -> None:
        super().__init__(params, rng)

        self._center = _float_param(params, "center", 0.0)
        self._drift_rate = _float_param(params, "drift_rate", 1.0)
        self._reversion_rate = _float_param(params, "reversion_rate", 0.1)
        self._noise = noise

        # Clamp bounds
        min_raw = params.get("min_clamp")
        max_raw = params.get("max_clamp")
        self._min_clamp: float | None = float(min_raw) if min_raw is not None else None  # type: ignore[arg-type]
        self._max_clamp: float | None = float(max_raw) if max_raw is not None else None  # type: ignore[arg-type]

        if self._drift_rate < 0.0:
            raise ValueError("drift_rate must be >= 0")
        if self._reversion_rate < 0.0:
            raise ValueError("reversion_rate must be >= 0")

        # Initial value defaults to center
        self._initial_value = _float_param(params, "initial_value", self._center)
        self._value: float = self._initial_value

    @property
    def center(self) -> float:
        """Mean-reversion center."""
        return self._center

    @property
    def drift_rate(self) -> float:
        """Random walk drift magnitude."""
        return self._drift_rate

    @property
    def reversion_rate(self) -> float:
        """Mean reversion strength."""
        return self._reversion_rate

    @property
    def value(self) -> float:
        """Current walk value (before observation noise)."""
        return self._value

    def generate(self, sim_time: float, dt: float) -> float:
        """Produce the next random walk value.

        Applies the discrete Euler step per PRD 4.2.5:

            delta = drift_rate * N(0, 1) - reversion_rate * (value - center)
            value = value + delta * dt

        Then applies clamp bounds and observation noise.

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
            Signal value = clamped walk value + observation noise.
        """
        # Random walk step: PRD formula exactly
        innovation = self._drift_rate * self._rng.standard_normal()
        reversion = self._reversion_rate * (self._value - self._center)
        delta = innovation - reversion
        self._value += delta * dt

        # Apply physical bounds
        if self._min_clamp is not None and self._value < self._min_clamp:
            self._value = self._min_clamp
        if self._max_clamp is not None and self._value > self._max_clamp:
            self._value = self._max_clamp

        result = self._value
        if self._noise is not None:
            result += self._noise.sample()

        return result

    def set_center(self, new_center: float) -> None:
        """Change the mean-reversion center at runtime.

        Used by scenarios (e.g. ink viscosity target change during
        job changeover).
        """
        self._center = new_center

    def reset(self) -> None:
        """Reset walk to initial value and clear noise state."""
        self._value = self._initial_value
        if self._noise is not None:
            self._noise.reset()
