"""Thermal Diffusion (Sigmoid) signal model.

Simulates heat penetration into a solid food product using a truncated
Fourier series solution for 1D heat conduction in a slab:

    T(t) = T_oven - (T_oven - T_initial) * SUM C_n * exp(-decay_n * t)

Where C_n = 8 / ((2n+1)^2 * pi^2), alpha is thermal diffusivity (m^2/s),
L is the product half-thickness (m), and
decay_n = (2n+1)^2 * pi^2 * alpha / (4 * L^2).

Note: The standard Fourier solution for a slab with half-thickness L uses
4*L^2 in the decay denominator.  The PRD formula writes L^2 but defines
L as "half-thickness".  We use 4*L^2 to match the standard physics and
the PRD's expected timing (~15-20 min for a ready meal to reach 72C).

Terms are summed dynamically until T(0) is within 1C of T_initial
(PRD 4.2.10 convergence requirement).

PRD Reference: Section 4.2.10
CLAUDE.md Rule 6: uses sim_time and dt, never wall clock.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

import math

import numpy as np

from factory_simulator.models.base import SignalModel
from factory_simulator.models.noise import NoiseGenerator


def _float_param(params: dict[str, object], key: str, default: float) -> float:
    """Extract a float parameter from the params dict."""
    raw = params.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


class ThermalDiffusionModel(SignalModel):
    """Thermal diffusion for heat penetration in solid food products.

    Models heat conduction into a slab product using a truncated Fourier
    series.  The number of terms is chosen dynamically so that T(0) is
    within 1C of T_initial (PRD 4.2.10).

    Parameters (via ``params`` dict)
    ---------------------------------
    T_initial : float
        Product entry temperature in C (default 4.0).
    T_oven : float
        Oven zone temperature in C (default 180.0).
    alpha : float
        Thermal diffusivity in m^2/s (default 1.4e-7, meat-based product).
    L : float
        Product half-thickness in m (default 0.025).
    """

    def __init__(
        self,
        params: dict[str, object],
        rng: np.random.Generator,
        *,
        noise: NoiseGenerator | None = None,
    ) -> None:
        super().__init__(params, rng)

        self._T_initial = _float_param(params, "T_initial", 4.0)
        self._T_oven = _float_param(params, "T_oven", 180.0)
        self._alpha = _float_param(params, "alpha", 1.4e-7)
        self._L = _float_param(params, "L", 0.025)
        self._noise = noise

        if self._L <= 0.0:
            raise ValueError("L (half-thickness) must be > 0")
        if self._alpha <= 0.0:
            raise ValueError("alpha (thermal diffusivity) must be > 0")

        # Precompute Fourier coefficients and decay rates
        self._n_terms: int = 0
        self._coefficients: list[float] = []
        self._decay_rates: list[float] = []
        self._compute_terms()

        # Elapsed time since product entered oven
        self._elapsed: float = 0.0

    @property
    def T_initial(self) -> float:
        """Product entry temperature (C)."""
        return self._T_initial

    @property
    def T_oven(self) -> float:
        """Oven zone temperature (C)."""
        return self._T_oven

    @property
    def elapsed(self) -> float:
        """Time since product entered oven (seconds)."""
        return self._elapsed

    @property
    def n_terms(self) -> int:
        """Number of Fourier terms used for convergence."""
        return self._n_terms

    def _compute_terms(self) -> None:
        """Compute Fourier coefficients and decay rates.

        Adds terms until ``|T(0) - T_initial| <= 1.0 C``
        (PRD 4.2.10 convergence requirement).
        """
        delta_T = abs(self._T_oven - self._T_initial)
        pi_sq = math.pi**2
        # Standard Fourier solution for slab with half-thickness L:
        # decay_n = (2n+1)^2 * pi^2 * alpha / (4 * L^2)
        alpha_over_4L2 = self._alpha / (4.0 * self._L**2)

        coefficients: list[float] = []
        decay_rates: list[float] = []
        coeff_sum = 0.0

        # Safety limit -- the infinite series converges to 1.0
        max_terms = 500

        for n in range(max_terms):
            k = 2 * n + 1  # Odd harmonics: 1, 3, 5, ...
            c_n = 8.0 / (k * k * pi_sq)
            decay = k * k * pi_sq * alpha_over_4L2

            coefficients.append(c_n)
            decay_rates.append(decay)
            coeff_sum += c_n

            # Convergence: |T(0) - T_initial| = delta_T * (1 - coeff_sum)
            if delta_T <= 0.0 or delta_T * (1.0 - coeff_sum) <= 1.0:
                break

        self._n_terms = len(coefficients)
        self._coefficients = coefficients
        self._decay_rates = decay_rates

    def set_oven_temp(self, T_oven: float) -> None:
        """Change the oven temperature at runtime.

        Recomputes Fourier terms for the new temperature difference.
        Does not reset elapsed time (product continues heating).
        """
        self._T_oven = T_oven
        self._compute_terms()

    def restart(
        self,
        T_initial: float | None = None,
        T_oven: float | None = None,
    ) -> None:
        """Start a new product cycle.

        Resets elapsed time and optionally updates temperatures.
        Used by equipment generator when a new product enters the oven.
        """
        if T_initial is not None:
            self._T_initial = T_initial
        if T_oven is not None:
            self._T_oven = T_oven
        self._elapsed = 0.0
        self._compute_terms()

    def generate(self, sim_time: float, dt: float) -> float:
        """Compute product core temperature at current elapsed time.

        Parameters
        ----------
        sim_time:
            Current simulated time in seconds since start (unused, kept
            for interface compliance).
        dt:
            Simulated time delta for this tick in seconds.

        Returns
        -------
        float
            Product core temperature in C.
        """
        self._elapsed += dt
        t = self._elapsed

        # Fourier series:
        # T(t) = T_oven - (T_oven - T_initial) * SUM C_n * exp(-decay_n * t)
        delta_T = self._T_oven - self._T_initial
        series_sum = 0.0
        for i in range(self._n_terms):
            series_sum += self._coefficients[i] * math.exp(
                -self._decay_rates[i] * t
            )

        value = self._T_oven - delta_T * series_sum

        if self._noise is not None:
            value += self._noise.sample()

        return value

    def reset(self) -> None:
        """Reset to initial state (new product enters oven)."""
        self._elapsed = 0.0
        if self._noise is not None:
            self._noise.reset()
