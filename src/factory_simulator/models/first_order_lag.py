"""First-Order Lag (Setpoint Tracking) signal model.

The signal tracks a setpoint with exponential lag, modelling temperature
controllers (Eurotherm PID loops).

    value = value + (setpoint - value) * (1 - exp(-dt / tau)) + noise(0, sigma)

Optional second-order underdamped response when damping_ratio < 1.0
produces characteristic overshoot and ringing of real PID loops:

    value = setpoint + A * exp(-zeta * omega_n * t) * sin(omega_d * t + phase)

Where omega_n = 1/tau, omega_d = omega_n * sqrt(1 - zeta^2),
A = step_size / sqrt(1 - zeta^2), phase = arccos(zeta).

Transients reset on each setpoint change.  No stacking of transients.

PRD Reference: Section 4.2.3
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


class FirstOrderLagModel(SignalModel):
    """First-order lag signal tracking a setpoint.

    Models temperature controllers (Eurotherm PID loops) where the
    process variable (PV) tracks the setpoint (SP) with first-order
    dynamics.  The time constant ``tau`` models thermal mass.

    When ``damping_ratio`` < 1.0, setpoint changes trigger an underdamped
    second-order transient with overshoot and ringing.  When >= 1.0,
    the model is a pure first-order exponential lag.

    Parameters (via ``params`` dict)
    ---------------------------------
    setpoint : float
        Target value to track (default 0.0).  Can be changed at runtime
        via :meth:`set_setpoint`.
    tau : float
        Time constant in seconds (default 60.0).  Must be > 0.
    initial_value : float | None
        Starting value (default: same as setpoint).
    damping_ratio : float
        Damping ratio for second-order response (default 1.0).
        Range [0.1, 2.0].  Values < 1.0 produce underdamped oscillation.
    """

    def __init__(
        self,
        params: dict[str, object],
        rng: np.random.Generator,
        *,
        noise: NoiseGenerator | None = None,
    ) -> None:
        super().__init__(params, rng)

        self._setpoint = _float_param(params, "setpoint", 0.0)
        self._tau = _float_param(params, "tau", 60.0)
        self._damping_ratio = _float_param(params, "damping_ratio", 1.0)
        self._noise = noise

        if self._tau <= 0.0:
            raise ValueError("tau must be > 0")
        if not 0.1 <= self._damping_ratio <= 2.0:
            raise ValueError("damping_ratio must be in [0.1, 2.0]")

        # Initial value defaults to setpoint
        initial_raw = params.get("initial_value")
        if initial_raw is not None:
            self._value: float = float(initial_raw)  # type: ignore[arg-type]
        else:
            self._value = self._setpoint

        # Second-order precomputed constants
        self._omega_n: float = 1.0 / self._tau
        self._omega_d: float = (
            self._omega_n * math.sqrt(1.0 - self._damping_ratio**2)
            if self._damping_ratio < 1.0
            else 0.0
        )

        # Transient state for underdamped response
        self._transient_t: float = 0.0
        self._transient_A: float = 0.0
        self._transient_phase: float = 0.0
        self._in_transient: bool = False

        # Start transient if underdamped and initial value differs from setpoint
        if self._damping_ratio < 1.0 and abs(self._value - self._setpoint) > 1e-12:
            self._start_transient(self._value, self._setpoint)

    @property
    def setpoint(self) -> float:
        """Current setpoint."""
        return self._setpoint

    @property
    def tau(self) -> float:
        """Time constant in seconds."""
        return self._tau

    @property
    def damping_ratio(self) -> float:
        """Damping ratio (< 1.0 underdamped, >= 1.0 critically/overdamped)."""
        return self._damping_ratio

    @property
    def value(self) -> float:
        """Current internal value (before noise)."""
        return self._value

    def set_setpoint(self, new_setpoint: float) -> None:
        """Change the setpoint at runtime.

        For underdamped models (damping_ratio < 1.0), this triggers a
        new transient from the current value to the new setpoint.
        Any existing transient is abandoned (PRD: transients do not stack).
        """
        if abs(new_setpoint - self._setpoint) < 1e-12:
            return  # No meaningful change

        old_value = self._value
        self._setpoint = new_setpoint

        if self._damping_ratio < 1.0:
            self._start_transient(old_value, new_setpoint)

    def _start_transient(self, from_value: float, to_setpoint: float) -> None:
        """Begin a new underdamped transient.

        Computes amplitude A and phase from the step size and damping
        ratio per PRD Section 4.2.3.
        """
        step_size = from_value - to_setpoint
        zeta = self._damping_ratio
        self._transient_phase = math.acos(zeta)
        self._transient_A = step_size / math.sqrt(1.0 - zeta**2)
        self._transient_t = 0.0
        self._in_transient = True

    def generate(self, sim_time: float, dt: float) -> float:
        """Produce value tracking the setpoint with lag.

        Parameters
        ----------
        sim_time:
            Current simulated time in seconds since start.
        dt:
            Simulated time delta for this tick in seconds.

        Returns
        -------
        float
            Signal value = lag-tracked value + noise.
        """
        if self._damping_ratio < 1.0 and self._in_transient:
            # Underdamped second-order response (PRD 4.2.3)
            self._transient_t += dt
            t = self._transient_t
            zeta = self._damping_ratio
            envelope = self._transient_A * math.exp(
                -zeta * self._omega_n * t
            )
            self._value = self._setpoint + envelope * math.sin(
                self._omega_d * t + self._transient_phase
            )

            # Check if transient has settled (envelope negligible)
            scale = max(abs(self._setpoint), 1.0)
            if abs(envelope) < 1e-9 * scale:
                self._in_transient = False
                self._value = self._setpoint
        else:
            # First-order exponential lag
            alpha = 1.0 - math.exp(-dt / self._tau)
            self._value += (self._setpoint - self._value) * alpha

        result = self._value
        if self._noise is not None:
            result += self._noise.sample()

        return result

    def reset(self) -> None:
        """Reset to initial state.

        Restores the internal value to the configured initial_value
        (or current setpoint if none was set).  Restarts the underdamped
        transient if applicable, so behaviour matches a fresh model.
        """
        initial_raw = self._params.get("initial_value")
        if initial_raw is not None:
            self._value = float(initial_raw)  # type: ignore[arg-type]
        else:
            self._value = self._setpoint

        self._transient_t = 0.0
        self._transient_A = 0.0
        self._transient_phase = 0.0
        self._in_transient = False

        # Restart transient if underdamped and value != setpoint
        if self._damping_ratio < 1.0 and abs(self._value - self._setpoint) > 1e-12:
            self._start_transient(self._value, self._setpoint)

        if self._noise is not None:
            self._noise.reset()
