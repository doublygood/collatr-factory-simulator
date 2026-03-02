"""Sinusoidal with Noise signal model.

The signal follows a sine wave with noise.  Models signals with
periodic behaviour such as daily ambient temperature/humidity cycles.

    value = center + amplitude * sin(2 * pi * t / period + phase) + noise(0, sigma)

PRD Reference: Section 4.2.2
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


class SinusoidalModel(SignalModel):
    """Sinusoidal signal with noise.

    Produces a periodic signal following a sine wave.  Noise is injected
    via an optional :class:`NoiseGenerator`, keeping distribution selection
    at the config level.

    Parameters (via ``params`` dict)
    ---------------------------------
    center : float
        Mid-line value of the sine wave (default 0.0).
    amplitude : float
        Peak deviation from center (default 1.0).
    period : float
        Period of the sine wave in seconds (default 86400.0 = 24 hours).
        Must be > 0.
    phase : float
        Phase offset in radians (default 0.0).
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
        self._amplitude = _float_param(params, "amplitude", 1.0)
        self._period = _float_param(params, "period", 86400.0)
        self._phase = _float_param(params, "phase", 0.0)
        self._noise = noise

        if self._period <= 0.0:
            raise ValueError("period must be > 0")

    @property
    def center(self) -> float:
        """Mid-line value."""
        return self._center

    @property
    def amplitude(self) -> float:
        """Peak deviation from center."""
        return self._amplitude

    @property
    def period(self) -> float:
        """Period in seconds."""
        return self._period

    @property
    def phase(self) -> float:
        """Phase offset in radians."""
        return self._phase

    def generate(self, sim_time: float, dt: float) -> float:
        """Produce sinusoidal value with optional noise.

        Parameters
        ----------
        sim_time:
            Current simulated time in seconds since start.
        dt:
            Simulated time delta for this tick in seconds.

        Returns
        -------
        float
            Signal value = center + amplitude * sin(2*pi*t/period + phase) + noise.
        """
        angle = 2.0 * np.pi * sim_time / self._period + self._phase
        value = self._center + self._amplitude * float(np.sin(angle))

        if self._noise is not None:
            value += self._noise.sample()

        return value

    def reset(self) -> None:
        """Reset noise state."""
        if self._noise is not None:
            self._noise.reset()
