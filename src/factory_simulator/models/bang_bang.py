"""Bang-Bang with Hysteresis signal model.

Models an on/off controller with dead band.  The output oscillates
between two states based on a process variable crossing upper and
lower thresholds.

When ON (cooling active), the process variable decreases at a
configurable cooling rate.  When OFF (cooling inactive), the process
variable increases at a configurable heat gain rate from the
environment and door openings.  This produces the characteristic
sawtooth temperature pattern in cold rooms.

The model generates the **process variable** (temperature).  The
binary compressor state is exposed via the :attr:`compressor_on`
property for the equipment generator to write into a separate coil
signal.

PRD Reference: Section 4.2.12
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


class BangBangModel(SignalModel):
    """On/off controller with hysteresis dead band.

    The process variable (e.g. room temperature) oscillates between
    ``setpoint - dead_band_low`` and ``setpoint + dead_band_high``
    in a sawtooth pattern:

    - **Compressor ON**: ``pv -= cooling_rate * dt / 60``
      (cooling_rate in C/min, dt in seconds)
    - **Compressor OFF**: ``pv += heat_gain_rate * dt / 60``
      (heat_gain_rate in C/min, dt in seconds)

    Transition logic::

        if OFF and pv > setpoint + dead_band_high: turn ON
        if ON  and pv < setpoint - dead_band_low:  turn OFF

    Parameters (via ``params`` dict)
    ---------------------------------
    setpoint : float
        Target temperature in C (default 2.0).
    dead_band_high : float
        Offset above setpoint to turn compressor ON (default 1.0 C).
        Must be > 0.
    dead_band_low : float
        Offset below setpoint to turn compressor OFF (default 1.0 C).
        Must be > 0.
    cooling_rate : float
        Temperature decrease rate when ON, in C per minute (default 0.5).
        Must be > 0.
    heat_gain_rate : float
        Temperature increase rate when OFF, in C per minute (default 0.2).
        Must be > 0.
    initial_temp : float | None
        Starting process variable.  Defaults to ``setpoint`` if not set.
    initial_state : str
        Starting compressor state: ``"on"`` or ``"off"`` (default ``"off"``).
    """

    def __init__(
        self,
        params: dict[str, object],
        rng: np.random.Generator,
        *,
        noise: NoiseGenerator | None = None,
    ) -> None:
        super().__init__(params, rng)

        self._setpoint = _float_param(params, "setpoint", 2.0)

        self._dead_band_high = _float_param(params, "dead_band_high", 1.0)
        if self._dead_band_high <= 0.0:
            raise ValueError("dead_band_high must be > 0")

        self._dead_band_low = _float_param(params, "dead_band_low", 1.0)
        if self._dead_band_low <= 0.0:
            raise ValueError("dead_band_low must be > 0")

        self._cooling_rate = _float_param(params, "cooling_rate", 0.5)
        if self._cooling_rate <= 0.0:
            raise ValueError("cooling_rate must be > 0")

        self._heat_gain_rate = _float_param(params, "heat_gain_rate", 0.2)
        if self._heat_gain_rate <= 0.0:
            raise ValueError("heat_gain_rate must be > 0")

        # Initial process variable
        initial_temp_raw = params.get("initial_temp")
        if initial_temp_raw is not None:
            self._initial_temp: float = float(initial_temp_raw)  # type: ignore[arg-type]
        else:
            self._initial_temp = self._setpoint

        # Initial compressor state
        initial_state_str = str(params.get("initial_state", "off")).lower()
        if initial_state_str not in ("on", "off"):
            raise ValueError("initial_state must be 'on' or 'off'")
        self._initial_on: bool = initial_state_str == "on"

        self._noise = noise

        # Internal state
        self._pv: float = self._initial_temp
        self._on: bool = self._initial_on

    # -- Properties -----------------------------------------------------------

    @property
    def setpoint(self) -> float:
        """Target temperature."""
        return self._setpoint

    @property
    def dead_band_high(self) -> float:
        """Offset above setpoint to turn ON."""
        return self._dead_band_high

    @property
    def dead_band_low(self) -> float:
        """Offset below setpoint to turn OFF."""
        return self._dead_band_low

    @property
    def cooling_rate(self) -> float:
        """Cooling rate in C/min when compressor ON."""
        return self._cooling_rate

    @property
    def heat_gain_rate(self) -> float:
        """Heat gain rate in C/min when compressor OFF."""
        return self._heat_gain_rate

    @property
    def compressor_on(self) -> bool:
        """Whether the compressor is currently ON."""
        return self._on

    @property
    def pv(self) -> float:
        """Current process variable (temperature) without noise."""
        return self._pv

    def set_setpoint(self, setpoint: float) -> None:
        """Change the setpoint at runtime.

        Called by equipment generator or scenario engine when the
        chiller target temperature changes.
        """
        self._setpoint = setpoint

    def add_disturbance(self, delta: float) -> None:
        """Apply an instantaneous temperature disturbance.

        Used to model door-open events or other external heat loads
        that cause a step change in the process variable.

        Parameters
        ----------
        delta:
            Temperature change in C (positive = warming).
        """
        self._pv += delta

    # -- SignalModel interface ------------------------------------------------

    def generate(self, sim_time: float, dt: float) -> float:
        """Produce the next process variable value.

        The process variable evolves according to the bang-bang
        controller logic.  Noise (if configured) is added to the
        *returned* value but does not affect the internal state.

        Parameters
        ----------
        sim_time:
            Current simulated time in seconds since start (unused).
        dt:
            Simulated time delta for this tick in seconds.

        Returns
        -------
        float
            The process variable (temperature) with optional noise.
        """
        dt_min = dt / 60.0  # rates are in C/min

        # Evolve process variable
        if self._on:
            self._pv -= self._cooling_rate * dt_min
        else:
            self._pv += self._heat_gain_rate * dt_min

        # Hysteresis switching
        upper = self._setpoint + self._dead_band_high
        lower = self._setpoint - self._dead_band_low

        if not self._on and self._pv > upper:
            self._on = True
        elif self._on and self._pv < lower:
            self._on = False

        # Output: pv + optional noise
        value = self._pv
        if self._noise is not None:
            value += self._noise.sample()

        return value

    def reset(self) -> None:
        """Reset to initial state."""
        self._pv = self._initial_temp
        self._on = self._initial_on
        if self._noise is not None:
            self._noise.reset()
