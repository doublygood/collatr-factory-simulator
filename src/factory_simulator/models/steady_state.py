"""Steady State with Noise signal model.

The simplest model: signal stays near a target with noise.

    value = target + noise(0, sigma)

Optional within-regime drift: slow random walk layered onto the target
during long production runs.  Drift mean-reverts over hours.

    effective_target = target + drift_offset
    drift_offset += drift_rate * N(0,1) * sqrt(dt) - reversion_rate * drift_offset * dt

Optional calibration drift: persistent linear bias representing sensor
degradation over simulated days/weeks.  Does not revert.

    calibration_bias += calibration_drift_rate * dt
    value = value + calibration_bias

PRD Reference: Section 4.2.1
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


class SteadyStateModel(SignalModel):
    """Steady state signal with noise and optional drift.

    Parameters (via ``params`` dict)
    ---------------------------------
    target : float
        Nominal signal value.
    drift_rate : float, optional
        Magnitude of within-regime random walk (default 0.0 = disabled).
    reversion_rate : float, optional
        Pull-back rate toward zero for drift (default 0.0001).
    max_drift : float | None, optional
        Maximum absolute drift offset.  Defaults to 3% of ``|target|``
        (or 0.03 if target is zero).
    calibration_drift_rate : float, optional
        Persistent drift in signal units **per simulated second** (default 0.0).
        The PRD (Section 4.2.1, Appendix D) specifies drift rates in units per
        simulated **hour**.  Callers must divide by 3600 before passing to this
        model.  Internally the bias accumulates as ``rate * dt`` where *dt* is
        in seconds, so per-second units are required here.
    """

    def __init__(
        self,
        params: dict[str, object],
        rng: np.random.Generator,
        *,
        noise: NoiseGenerator | None = None,
    ) -> None:
        super().__init__(params, rng)

        self._target = _float_param(params, "target", 0.0)
        self._drift_rate = _float_param(params, "drift_rate", 0.0)
        self._reversion_rate = _float_param(params, "reversion_rate", 0.0001)

        max_drift_raw = params.get("max_drift")
        if max_drift_raw is not None:
            self._max_drift: float = float(max_drift_raw)  # type: ignore[arg-type]
        else:
            # Default: 3% of |target|, minimum 0.03 so zero-target signals
            # still allow some drift range.
            self._max_drift = max(abs(self._target) * 0.03, 0.03)

        self._calibration_drift_rate = _float_param(
            params, "calibration_drift_rate", 0.0
        )

        self._noise = noise

        # Internal state
        self._drift_offset: float = 0.0
        self._calibration_bias: float = 0.0

    @property
    def target(self) -> float:
        """Nominal target value."""
        return self._target

    @property
    def drift_offset(self) -> float:
        """Current within-regime drift offset."""
        return self._drift_offset

    @property
    def calibration_bias(self) -> float:
        """Current calibration drift bias."""
        return self._calibration_bias

    def generate(self, sim_time: float, dt: float) -> float:
        """Produce steady-state value with optional drift and noise.

        Parameters
        ----------
        sim_time:
            Current simulated time in seconds since start.
        dt:
            Simulated time delta for this tick in seconds.

        Returns
        -------
        float
            Signal value = effective_target + noise + calibration_bias.
        """
        # Within-regime drift (Ornstein-Uhlenbeck-like)
        if self._drift_rate > 0.0:
            sqrt_dt = np.sqrt(dt)
            innovation = self._drift_rate * self._rng.standard_normal() * sqrt_dt
            reversion = self._reversion_rate * self._drift_offset * dt
            self._drift_offset += innovation - reversion

            # Clamp drift to max_drift
            self._drift_offset = float(
                np.clip(self._drift_offset, -self._max_drift, self._max_drift)
            )

        effective_target = self._target + self._drift_offset

        # Core signal: target + noise
        value = effective_target
        if self._noise is not None:
            value += self._noise.sample()

        # Calibration drift (persistent, non-reverting).
        # Rate is in units/second (caller converts from PRD's units/hour ÷ 3600).
        if self._calibration_drift_rate != 0.0:
            self._calibration_bias += self._calibration_drift_rate * dt
            value += self._calibration_bias

        return value

    def reset(self) -> None:
        """Reset drift state to zero."""
        self._drift_offset = 0.0
        self._calibration_bias = 0.0
        if self._noise is not None:
            self._noise.reset()
