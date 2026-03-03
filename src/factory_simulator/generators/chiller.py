"""Refrigeration (chiller / cold room) equipment generator.

Models a cold room refrigeration plant with bang-bang hysteresis
temperature control, periodic defrost cycles, compressor pressure
monitoring, and door-open heat ingress.  Produces 7 signals.

Behaviour (PRD 4.2.12):
- ``room_temp`` oscillates around the setpoint in a sawtooth pattern.
  The compressor turns ON when room_temp > setpoint + dead_band_high,
  and turns OFF when room_temp < setpoint - dead_band_low.
- Defrost cycles run 4 times per day (every 6 hours), lasting 20 min.
  During defrost the compressor is forced OFF and defrost heaters add
  additional heat to the room.
- ``door_open`` is scenario-controlled.  When True, heat ingress rate
  is increased, producing the temperature spikes described in PRD 2b.7.
- ``suction_pressure`` and ``discharge_pressure`` track compressor state
  via first-order lag: suction drops when compressor is ON (refrigerant
  being drawn away), discharge rises.
- ``compressor_forced_off`` allows scenarios (cold chain break) to lock
  the compressor off independently of the bang-bang logic.

PRD Reference: Section 2b.7 (Refrigeration), Section 4.2.12 (Bang-Bang)
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
CLAUDE.md Rule 9: No locks (single-threaded asyncio).
CLAUDE.md Rule 12: No global state.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

import numpy as np

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.base import EquipmentGenerator
from factory_simulator.models.base import clamp
from factory_simulator.models.noise import NoiseGenerator
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Bang-bang hysteresis parameters (PRD 4.2.12)
# ---------------------------------------------------------------------------

# Default setpoint if not specified in config
_DEFAULT_SETPOINT_C = 2.0

# Dead band: compressor turns ON above setpoint + HIGH, OFF below setpoint - LOW
_DEAD_BAND_HIGH_C = 1.0   # °C above setpoint → turn compressor ON
_DEAD_BAND_LOW_C = 1.0    # °C below setpoint → turn compressor OFF

# Temperature change rates (per second, converted from per-minute PRD values)
_COOLING_RATE_C_PER_S = 0.5 / 60.0          # 0.5 °C/min when compressor ON
_HEAT_GAIN_RATE_C_PER_S = 0.2 / 60.0        # 0.2 °C/min heat gain when OFF
_DEFROST_HEAT_RATE_C_PER_S = 3.0 / 60.0     # 3 °C/min from defrost heaters
_DOOR_OPEN_HEAT_RATE_C_PER_S = 1.5 / 60.0   # 1.5 °C/min extra from door open

# ---------------------------------------------------------------------------
# Defrost timing (PRD 2b.7: 2-4 times per day, 15-30 min)
# ---------------------------------------------------------------------------
_DEFROST_PERIOD_S = 21600.0    # 6 h between defrost cycles (4 per day)
_DEFROST_DURATION_S = 1200.0   # 20 min per defrost cycle

# ---------------------------------------------------------------------------
# Pressure targets (PRD 2b.7: suction 0-10 bar, discharge 5-25 bar)
# ---------------------------------------------------------------------------
_SUCTION_TARGET_ON = 3.0     # bar; compressor ON draws refrigerant, suction falls
_SUCTION_TARGET_OFF = 4.5    # bar; pressures equalize when compressor OFF
_DISCHARGE_TARGET_ON = 16.0  # bar; compressor pushes high-side pressure up
_DISCHARGE_TARGET_OFF = 12.0 # bar; falls when compressor OFF

# First-order lag time constant for pressure transitions (s)
_PRESSURE_TAU_S = 60.0


class ChillerGenerator(EquipmentGenerator):
    """Cold room refrigeration generator -- 7 signals.

    Signals
    -------
    room_temp           Cold room temperature (Modbus HR + IR 110)
    setpoint            Target temperature (Modbus HR, writable)
    compressor_state    Compressor on/off (Modbus coil 101)
    suction_pressure    Compressor suction pressure (Modbus HR)
    discharge_pressure  Compressor discharge pressure (Modbus HR)
    defrost_active      Defrost cycle state (Modbus coil 102)
    door_open           Cold room door state (Modbus DI 100)

    Parameters
    ----------
    equipment_id:
        Equipment prefix, typically ``"chiller"``.
    config:
        Chiller equipment config from YAML.
    rng:
        numpy random Generator (from SeedSequence).
    """

    def __init__(
        self,
        equipment_id: str,
        config: EquipmentConfig,
        rng: np.random.Generator,
    ) -> None:
        super().__init__(equipment_id, config, rng)

        # Read setpoint from signal config (default 2 °C per PRD 4.2.12)
        setpoint_cfg = config.signals.get("setpoint")
        self._setpoint: float = _DEFAULT_SETPOINT_C
        if setpoint_cfg is not None and setpoint_cfg.params:
            target = setpoint_cfg.params.get("target")
            if target is not None:
                self._setpoint = float(target)

        # Room temperature starts at setpoint (stable steady-state entry)
        self._room_temp: float = self._setpoint

        # Bang-bang: start with compressor ON so cooling cycle begins
        self._compressor_on: bool = True

        # Compressor lock (used by cold chain break scenario, task 3.21)
        self._compressor_forced_off: bool = False

        # Defrost state
        self._defrost_active: bool = False
        self._time_since_last_defrost: float = 0.0
        self._defrost_elapsed: float = 0.0

        # Door open state (controlled by chiller door alarm scenario, task 3.19)
        self._door_open: bool = False

        # Pressure state: start at the ON-state targets
        self._suction_current: float = _SUCTION_TARGET_ON
        self._discharge_current: float = _DISCHARGE_TARGET_ON

        # Noise generators for analog signals
        room_temp_cfg = config.signals.get("room_temp")
        self._room_temp_cfg: SignalConfig | None = room_temp_cfg
        self._room_temp_noise: NoiseGenerator | None = (
            self._make_noise(room_temp_cfg)
            if room_temp_cfg is not None and room_temp_cfg.noise_sigma > 0.0
            else None
        )

        suction_cfg = config.signals.get("suction_pressure")
        self._suction_cfg: SignalConfig | None = suction_cfg
        self._suction_noise: NoiseGenerator | None = (
            self._make_noise(suction_cfg)
            if suction_cfg is not None and suction_cfg.noise_sigma > 0.0
            else None
        )

        discharge_cfg = config.signals.get("discharge_pressure")
        self._discharge_cfg: SignalConfig | None = discharge_cfg
        self._discharge_noise: NoiseGenerator | None = (
            self._make_noise(discharge_cfg)
            if discharge_cfg is not None and discharge_cfg.noise_sigma > 0.0
            else None
        )

    # -- Public properties (for scenarios and tests) --------------------------

    @property
    def room_temp(self) -> float:
        """Current simulated room temperature (°C)."""
        return self._room_temp

    @room_temp.setter
    def room_temp(self, value: float) -> None:
        """Override room temperature directly (for scenarios)."""
        self._room_temp = value

    @property
    def compressor_on(self) -> bool:
        """Whether the compressor is currently running."""
        return self._compressor_on

    @property
    def compressor_forced_off(self) -> bool:
        """Whether the compressor is locked off by a scenario."""
        return self._compressor_forced_off

    @compressor_forced_off.setter
    def compressor_forced_off(self, value: bool) -> None:
        """Lock the compressor off (cold chain break scenario, task 3.21)."""
        self._compressor_forced_off = value
        if value:
            self._compressor_on = False

    @property
    def door_open(self) -> bool:
        """Whether the cold room door is open."""
        return self._door_open

    @door_open.setter
    def door_open(self, value: bool) -> None:
        """Open or close the cold room door (chiller door alarm scenario)."""
        self._door_open = value

    @property
    def defrost_active(self) -> bool:
        """Whether a defrost cycle is currently active."""
        return self._defrost_active

    @property
    def setpoint(self) -> float:
        """Current temperature setpoint (°C)."""
        return self._setpoint

    # -- EquipmentGenerator interface -----------------------------------------

    def get_signal_ids(self) -> list[str]:
        """Return all 7 chiller signal IDs."""
        return [self._signal_id(name) for name in self._signal_configs]

    def generate(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> list[SignalValue]:
        """Generate all chiller signals for one tick.

        Generation order:
        1. Advance defrost timer; start/end defrost cycles.
        2. Bang-bang: update compressor_on based on room_temp vs setpoint.
        3. Advance room_temp (cooling or warming depending on compressor/defrost/door).
        4. Update suction/discharge pressures via first-order lag.
        5. Build and return SignalValue list.
        """
        # --- 1. Defrost timer ---
        self._time_since_last_defrost += dt

        if not self._defrost_active:
            if self._time_since_last_defrost >= _DEFROST_PERIOD_S:
                # Start defrost cycle
                self._defrost_active = True
                self._defrost_elapsed = 0.0
                self._time_since_last_defrost = 0.0
        else:
            self._defrost_elapsed += dt
            if self._defrost_elapsed >= _DEFROST_DURATION_S:
                # End defrost cycle; reset timer so next cycle starts from now
                self._defrost_active = False
                self._defrost_elapsed = 0.0

        # --- 2. Bang-bang compressor control ---
        # Scenarios can force the compressor off (cold chain break, defrost)
        if self._defrost_active or self._compressor_forced_off:
            self._compressor_on = False
        else:
            # Normal bang-bang hysteresis (PRD 4.2.12)
            if self._compressor_on:
                # Currently ON: switch OFF when temp drops below lower threshold
                if self._room_temp < self._setpoint - _DEAD_BAND_LOW_C:
                    self._compressor_on = False
            else:
                # Currently OFF: switch ON when temp rises above upper threshold
                if self._room_temp > self._setpoint + _DEAD_BAND_HIGH_C:
                    self._compressor_on = True

        # --- 3. Advance room temperature ---
        if self._compressor_on:
            # Compressor running: active cooling
            self._room_temp -= _COOLING_RATE_C_PER_S * dt
        else:
            # Compressor off: heat ingress from environment + optional sources
            heat_rate = _HEAT_GAIN_RATE_C_PER_S
            if self._defrost_active:
                heat_rate += _DEFROST_HEAT_RATE_C_PER_S
            if self._door_open:
                heat_rate += _DOOR_OPEN_HEAT_RATE_C_PER_S
            self._room_temp += heat_rate * dt

        # Apply noise and signal config clamp for the output value
        room_temp_out = self._room_temp
        if self._room_temp_noise is not None:
            room_temp_out += self._room_temp_noise.sample()
        if self._room_temp_cfg is not None:
            room_temp_out = clamp(
                room_temp_out,
                self._room_temp_cfg.min_clamp,
                self._room_temp_cfg.max_clamp,
            )

        # --- 4. Suction / discharge pressure (first-order lag) ---
        suction_target = (
            _SUCTION_TARGET_ON if self._compressor_on else _SUCTION_TARGET_OFF
        )
        discharge_target = (
            _DISCHARGE_TARGET_ON if self._compressor_on else _DISCHARGE_TARGET_OFF
        )

        alpha_p = dt / (_PRESSURE_TAU_S + dt)
        self._suction_current += alpha_p * (suction_target - self._suction_current)
        self._discharge_current += alpha_p * (
            discharge_target - self._discharge_current
        )

        suction_out = self._suction_current
        if self._suction_noise is not None:
            suction_out += self._suction_noise.sample()
        if self._suction_cfg is not None:
            suction_out = clamp(
                suction_out,
                self._suction_cfg.min_clamp,
                self._suction_cfg.max_clamp,
            )

        discharge_out = self._discharge_current
        if self._discharge_noise is not None:
            discharge_out += self._discharge_noise.sample()
        if self._discharge_cfg is not None:
            discharge_out = clamp(
                discharge_out,
                self._discharge_cfg.min_clamp,
                self._discharge_cfg.max_clamp,
            )

        # --- 5. Build results ---
        return [
            self._make_sv("room_temp", room_temp_out, sim_time),
            self._make_sv("setpoint", self._setpoint, sim_time),
            self._make_sv(
                "compressor_state", 1.0 if self._compressor_on else 0.0, sim_time
            ),
            self._make_sv("suction_pressure", suction_out, sim_time),
            self._make_sv("discharge_pressure", discharge_out, sim_time),
            self._make_sv(
                "defrost_active", 1.0 if self._defrost_active else 0.0, sim_time
            ),
            self._make_sv(
                "door_open", 1.0 if self._door_open else 0.0, sim_time
            ),
        ]

    # -- Helper ---------------------------------------------------------------

    def _make_sv(
        self,
        signal_name: str,
        value: float,
        sim_time: float,
    ) -> SignalValue:
        """Create a SignalValue with fully qualified signal ID."""
        return SignalValue(
            signal_id=self._signal_id(signal_name),
            value=value,
            timestamp=sim_time,
            quality="good",
        )
