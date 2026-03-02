"""Correlated Follower signal model.

The signal derives from another signal (the parent) with a linear
transformation and optional transport lag:

    value = base + gain * parent_value + noise(0, sigma)

Supports two lag modes:
- **Fixed**: constant delay in seconds.
- **Transport**: speed-dependent delay: ``lag = distance_m / (speed / 60)``.
  Uses a ring buffer to delay the parent signal.

Supports time-varying covariance (PRD 4.3.2): the gain parameter drifts
via a multiplicative random walk on the log scale to produce realistic
scatter that widens/shifts over hours and days.

PRD Reference: Section 4.2.8 (Correlated Follower), Section 4.3.2
    (Time-Varying Covariance)
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


class CorrelatedFollowerModel(SignalModel):
    """Signal that follows a parent via a linear transformation.

    The core formula is:

        value = base + gain_effective * lagged_parent + noise

    Where ``gain_effective`` may drift over time (PRD 4.3.2) and
    ``lagged_parent`` may be delayed via a ring buffer (PRD 4.2.8).

    The model does not read the parent value itself -- the equipment
    generator calls :meth:`set_parent_value` each tick before
    :meth:`generate`.

    Parameters (via ``params`` dict)
    ---------------------------------
    base : float
        Intercept of the linear transform (default 0.0).
    gain : float
        Slope (k) of the linear transform (default 1.0).
    lag_mode : str
        ``"none"`` (default), ``"fixed"``, or ``"transport"``.
    lag_seconds : float
        Fixed lag in seconds (for ``lag_mode="fixed"``).
    distance_m : float
        Transport distance in metres (for ``lag_mode="transport"``).
    min_speed : float
        Minimum nonzero line speed in m/min for buffer sizing
        (default 50.0).  Used only for ring buffer allocation.
    tick_interval : float
        Tick interval in seconds for ring buffer indexing
        (default 0.1).
    gain_drift_volatility : float
        Log-normal drift volatility per sqrt(second). 0 disables
        drift (default 0.0).
    gain_drift_reversion : float
        Mean-reversion rate for gain drift (default 0.02).
    """

    def __init__(
        self,
        params: dict[str, object],
        rng: np.random.Generator,
        *,
        noise: NoiseGenerator | None = None,
    ) -> None:
        super().__init__(params, rng)

        self._base = _float_param(params, "base", 0.0)
        self._gain = _float_param(params, "gain", 1.0)

        # --- Lag configuration ---
        lag_mode_raw = params.get("lag_mode", "none")
        self._lag_mode: str = str(lag_mode_raw) if lag_mode_raw is not None else "none"
        if self._lag_mode not in ("none", "fixed", "transport"):
            raise ValueError(
                f"lag_mode must be 'none', 'fixed', or 'transport', "
                f"got '{self._lag_mode}'"
            )

        self._lag_seconds = _float_param(params, "lag_seconds", 0.0)
        if self._lag_mode == "fixed" and self._lag_seconds < 0.0:
            raise ValueError("lag_seconds must be >= 0 for fixed lag")

        self._distance_m = _float_param(params, "distance_m", 0.0)
        if self._lag_mode == "transport" and self._distance_m <= 0.0:
            raise ValueError("distance_m must be > 0 for transport lag")

        self._min_speed = _float_param(params, "min_speed", 50.0)
        if self._lag_mode == "transport" and self._min_speed <= 0.0:
            raise ValueError("min_speed must be > 0 for transport lag")

        self._tick_interval = _float_param(params, "tick_interval", 0.1)
        if self._tick_interval <= 0.0:
            raise ValueError("tick_interval must be > 0")

        # --- Ring buffer for lag ---
        # The buffer uses a write-at-head, read-from-behind design:
        # write current parent at _buffer_pos, then read from
        # (_buffer_pos - lag_ticks) % _buffer_size.  Buffer must be
        # larger than the maximum lag in ticks.
        self._buffer: list[float] = []
        self._buffer_pos: int = 0
        self._buffer_size: int = 0
        self._fixed_lag_ticks: int = 0

        if self._lag_mode == "fixed":
            self._fixed_lag_ticks = max(1, math.ceil(self._lag_seconds / self._tick_interval))
            # Buffer must be > lag_ticks so write and read don't collide
            self._buffer_size = self._fixed_lag_ticks + 1
            self._buffer = [0.0] * self._buffer_size
        elif self._lag_mode == "transport":
            # Transport lag: buffer sized at 2x max lag at min speed (PRD 4.2.8)
            max_lag_s = self._distance_m / (self._min_speed / 60.0)
            self._buffer_size = max(2, math.ceil(2.0 * max_lag_s / self._tick_interval) + 1)
            self._buffer = [0.0] * self._buffer_size

        # --- Gain drift (PRD 4.3.2) ---
        self._gain_drift_volatility = _float_param(
            params, "gain_drift_volatility", 0.0
        )
        self._gain_drift_reversion = _float_param(
            params, "gain_drift_reversion", 0.02
        )
        if self._gain_drift_volatility < 0.0:
            raise ValueError("gain_drift_volatility must be >= 0")
        if self._gain_drift_reversion < 0.0:
            raise ValueError("gain_drift_reversion must be >= 0")

        # Log-space drift state
        self._log_drift: float = 0.0

        # Noise
        self._noise = noise

        # Parent value state
        self._parent_value: float = 0.0
        self._speed: float = 0.0  # for transport lag speed reference
        self._last_output: float = self._base  # frozen output at zero speed

    # --- Properties ---

    @property
    def base(self) -> float:
        """Intercept of the linear transform."""
        return self._base

    @property
    def gain(self) -> float:
        """Nominal gain (k) of the linear transform."""
        return self._gain

    @property
    def lag_mode(self) -> str:
        """Lag mode: 'none', 'fixed', or 'transport'."""
        return self._lag_mode

    @property
    def gain_drift_factor(self) -> float:
        """Current multiplicative gain drift factor (1.0 = no drift)."""
        return math.exp(self._log_drift)

    @property
    def effective_gain(self) -> float:
        """Current effective gain after drift."""
        return self._gain * self.gain_drift_factor

    @property
    def buffer_size(self) -> int:
        """Ring buffer size (0 if no lag)."""
        return self._buffer_size

    # --- External input methods ---

    def set_parent_value(self, value: float) -> None:
        """Set the current parent signal value.

        Called by the equipment generator before :meth:`generate`.
        """
        self._parent_value = value

    def set_speed(self, speed: float) -> None:
        """Set the current line speed for transport lag.

        Only used when ``lag_mode="transport"``.  Speed is in the same
        units as the config (m/min).
        """
        self._speed = speed

    # --- Core generation ---

    def generate(self, sim_time: float, dt: float) -> float:
        """Produce the next correlated follower value.

        Steps:
        1. Update gain drift (if enabled).
        2. Get parent value (with lag if configured).
        3. Apply linear transform: base + gain_eff * parent.
        4. Add noise.

        Parameters
        ----------
        sim_time:
            Current simulated time in seconds since start.
        dt:
            Simulated time delta for this tick in seconds.

        Returns
        -------
        float
            The correlated follower value.
        """
        # Step 1: Update gain drift (PRD 4.3.2)
        if self._gain_drift_volatility > 0.0:
            noise_draw = float(self._rng.standard_normal())
            self._log_drift += (
                self._gain_drift_volatility * noise_draw * math.sqrt(dt)
                - self._gain_drift_reversion * self._log_drift * dt
            )

        gain_eff = self._gain * math.exp(self._log_drift)

        # Step 2: Get (possibly lagged) parent value
        parent = self._get_lagged_parent()

        # Step 3: Linear transform
        result = self._base + gain_eff * parent

        # Step 4: Add noise (pass parent for speed-dependent sigma)
        if self._noise is not None:
            result += self._noise.sample(parent_value=parent)

        self._last_output = result
        return result

    def _get_lagged_parent(self) -> float:
        """Return the parent value, possibly delayed through the ring buffer."""
        if self._lag_mode == "none":
            return self._parent_value

        if self._lag_mode == "fixed":
            return self._fixed_lag()

        # Transport mode
        return self._transport_lag()

    def _fixed_lag(self) -> float:
        """Fixed lag: write current parent, read from lag_ticks behind."""
        # Write current value at current position
        self._buffer[self._buffer_pos] = self._parent_value

        # Read from lag_ticks behind the current write position
        read_pos = (self._buffer_pos - self._fixed_lag_ticks) % self._buffer_size
        delayed = self._buffer[read_pos]

        # Advance write position
        self._buffer_pos = (self._buffer_pos + 1) % self._buffer_size

        return delayed

    def _transport_lag(self) -> float:
        """Transport lag: speed-dependent delay via ring buffer.

        At zero speed, material transport stops: the downstream value
        freezes at its last value (PRD 4.2.8).
        """
        # Write current value at current position
        self._buffer[self._buffer_pos] = self._parent_value

        if self._speed <= 0.0:
            # Zero speed: no transport, freeze output
            # Advance write position but return last known output
            self._buffer_pos = (self._buffer_pos + 1) % self._buffer_size
            return self._last_output - self._base  # return un-transformed parent

        # Compute current lag in ticks
        lag_s = self._distance_m / (self._speed / 60.0)
        lag_ticks = round(lag_s / self._tick_interval)
        lag_ticks = min(lag_ticks, self._buffer_size - 1)
        lag_ticks = max(lag_ticks, 0)

        # Read from delayed position
        read_pos = (self._buffer_pos - lag_ticks) % self._buffer_size
        delayed = self._buffer[read_pos]

        # Advance write position
        self._buffer_pos = (self._buffer_pos + 1) % self._buffer_size

        return delayed

    def reset(self) -> None:
        """Reset all internal state."""
        self._log_drift = 0.0
        self._parent_value = 0.0
        self._speed = 0.0
        self._last_output = self._base
        self._buffer_pos = 0
        if self._buffer:
            self._buffer = [0.0] * self._buffer_size
        if self._noise is not None:
            self._noise.reset()
