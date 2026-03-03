"""Sealer (tray sealer / MAP sealer) equipment generator.

The sealer applies lids to filled trays using heat sealing or modified
atmosphere packaging (MAP).  It produces 6 signals, all Modbus HR.

Behaviour:
- Follows filler state: active when filler is Running (state == 2),
  passive (seal_temp cools, pressure/vacuum released) otherwise.
- seal_temp: steady state at target when active, drops toward ambient
  when inactive (first-order lag decay).
- seal_pressure: steady state at target when active, 0.0 when inactive.
- seal_dwell: steady state at target always (process parameter).
- gas_co2_pct / gas_n2_pct: steady state; hold when inactive.
- vacuum_level: steady state at target when active, 0.0 when inactive.

PRD Reference: Section 2b.5 (Sealing and Lidding), Section 4.6
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
CLAUDE.md Rule 9: No locks (single-threaded asyncio).
CLAUDE.md Rule 12: No global state.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

import numpy as np

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.base import EquipmentGenerator
from factory_simulator.models.base import clamp, quantise
from factory_simulator.models.noise import NoiseGenerator
from factory_simulator.models.steady_state import SteadyStateModel
from factory_simulator.store import SignalStore, SignalValue

# Filler state that means "Running" (PRD 2b.4)
_FILLER_RUNNING_STATE = 2

# Thermal decay constant for seal bar cooling when inactive (τ = 180 s)
_SEAL_TEMP_TAU_S = 180.0
_AMBIENT_TEMP_C = 20.0

# Default signal targets (PRD 2b.5)
_DEFAULT_SEAL_TEMP_C = 180.0
_DEFAULT_SEAL_PRESSURE_BAR = 3.5
_DEFAULT_SEAL_DWELL_S = 2.0
_DEFAULT_GAS_CO2_PCT = 30.0
_DEFAULT_GAS_N2_PCT = 70.0
_DEFAULT_VACUUM_BAR = -0.7


class SealerGenerator(EquipmentGenerator):
    """Tray sealer generator -- 6 signals, follows filler state.

    Signals
    -------
    seal_temp       Seal bar temperature (Modbus HR)
    seal_pressure   Seal bar pressure (Modbus HR)
    seal_dwell      Seal dwell time (Modbus HR)
    gas_co2_pct     MAP gas CO2 fraction (Modbus HR)
    gas_n2_pct      MAP gas N2 fraction (Modbus HR)
    vacuum_level    Thermoform vacuum level (Modbus HR)

    Parameters
    ----------
    equipment_id:
        Equipment prefix, typically ``"sealer"``.
    config:
        Sealer equipment config from YAML.
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

        # Extract target values from signal config params (or defaults)
        self._seal_temp_target = self._get_signal_target(
            "seal_temp", _DEFAULT_SEAL_TEMP_C
        )

        # Track seal_temp as a continuous state (decays when inactive)
        self._seal_temp_current: float = _AMBIENT_TEMP_C

        # Build signal models
        self._build_models()

    # -- Model construction ---------------------------------------------------

    def _get_signal_target(self, signal_name: str, default: float) -> float:
        """Extract the 'target' param from a signal config, or use default."""
        sig_cfg = self._signal_configs.get(signal_name)
        if sig_cfg is not None and sig_cfg.params:
            raw = sig_cfg.params.get("target", default)
            if raw is not None:
                return float(raw)
        return default

    def _build_models(self) -> None:
        """Instantiate signal models from config."""
        sigs = self._signal_configs

        self._seal_temp_model = self._build_steady_state(
            sigs.get("seal_temp"), _DEFAULT_SEAL_TEMP_C
        )
        self._seal_temp_noise = (
            self._make_noise(sigs["seal_temp"]) if "seal_temp" in sigs else None
        )

        self._seal_pressure_model = self._build_steady_state(
            sigs.get("seal_pressure"), _DEFAULT_SEAL_PRESSURE_BAR
        )
        self._seal_pressure_noise = (
            self._make_noise(sigs["seal_pressure"])
            if "seal_pressure" in sigs
            else None
        )

        self._seal_dwell_model = self._build_steady_state(
            sigs.get("seal_dwell"), _DEFAULT_SEAL_DWELL_S
        )
        self._seal_dwell_noise = (
            self._make_noise(sigs["seal_dwell"]) if "seal_dwell" in sigs else None
        )

        self._gas_co2_model = self._build_steady_state(
            sigs.get("gas_co2_pct"), _DEFAULT_GAS_CO2_PCT
        )
        self._gas_co2_noise = (
            self._make_noise(sigs["gas_co2_pct"]) if "gas_co2_pct" in sigs else None
        )

        self._gas_n2_model = self._build_steady_state(
            sigs.get("gas_n2_pct"), _DEFAULT_GAS_N2_PCT
        )
        self._gas_n2_noise = (
            self._make_noise(sigs["gas_n2_pct"]) if "gas_n2_pct" in sigs else None
        )

        self._vacuum_model = self._build_steady_state(
            sigs.get("vacuum_level"), _DEFAULT_VACUUM_BAR
        )
        self._vacuum_noise = (
            self._make_noise(sigs["vacuum_level"])
            if "vacuum_level" in sigs
            else None
        )

    def _build_steady_state(
        self,
        sig_cfg: SignalConfig | None,
        default_target: float,
    ) -> SteadyStateModel:
        """Build a SteadyStateModel from signal config."""
        params: dict[str, object] = {"target": default_target}
        if sig_cfg is not None and sig_cfg.params:
            params.update(sig_cfg.params)
        return SteadyStateModel(params, self._spawn_rng())

    # -- Public interface -----------------------------------------------------

    def get_signal_ids(self) -> list[str]:
        """Return all 6 sealer signal IDs."""
        return [self._signal_id(name) for name in self._signal_configs]

    def generate(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> list[SignalValue]:
        """Generate all sealer signals for one tick.

        Reads filler state from the store to determine whether to run
        at nominal values or drop to passive values.

        Generation order:
        1. Determine active state from filler
        2. seal_temp (continuous: ramps/decays based on activity)
        3. seal_pressure (nominal or 0)
        4. seal_dwell (steady state always)
        5. gas_co2_pct, gas_n2_pct (hold when inactive)
        6. vacuum_level (nominal or 0)
        """
        results: list[SignalValue] = []

        # --- 1. Read filler state ---
        filler_state_val = store.get("filler.state")
        is_active = (
            filler_state_val is not None
            and int(filler_state_val.value) == _FILLER_RUNNING_STATE
        )

        # --- 2. seal_temp ---
        raw_target = self._seal_temp_model.generate(sim_time, dt)
        if is_active:
            # Ramp toward target via first-order lag
            alpha = dt / (_SEAL_TEMP_TAU_S + dt)
            self._seal_temp_current += alpha * (raw_target - self._seal_temp_current)
            seal_temp = self._post_process(
                "seal_temp", self._seal_temp_current, self._seal_temp_noise
            )
        else:
            # Decay toward ambient
            alpha = dt / (_SEAL_TEMP_TAU_S + dt)
            self._seal_temp_current += alpha * (_AMBIENT_TEMP_C - self._seal_temp_current)
            seal_temp = self._post_process(
                "seal_temp", self._seal_temp_current, noise=None
            )
        results.append(
            self._make_sv("seal_temp", seal_temp, sim_time,
                          self._signal_configs.get("seal_temp"))
        )

        # --- 3. seal_pressure ---
        raw_pressure = self._seal_pressure_model.generate(sim_time, dt)
        if is_active:
            pressure = self._post_process(
                "seal_pressure", raw_pressure, self._seal_pressure_noise
            )
        else:
            pressure = 0.0
        results.append(
            self._make_sv("seal_pressure", pressure, sim_time,
                          self._signal_configs.get("seal_pressure"))
        )

        # --- 4. seal_dwell ---
        raw_dwell = self._seal_dwell_model.generate(sim_time, dt)
        dwell = self._post_process("seal_dwell", raw_dwell, self._seal_dwell_noise)
        results.append(
            self._make_sv("seal_dwell", dwell, sim_time,
                          self._signal_configs.get("seal_dwell"))
        )

        # --- 5. gas_co2_pct, gas_n2_pct ---
        # Gas mix is always generated (holds at target even when inactive,
        # representing standby gas supply)
        raw_co2 = self._gas_co2_model.generate(sim_time, dt)
        co2 = self._post_process("gas_co2_pct", raw_co2, self._gas_co2_noise)
        results.append(
            self._make_sv("gas_co2_pct", co2, sim_time,
                          self._signal_configs.get("gas_co2_pct"))
        )

        raw_n2 = self._gas_n2_model.generate(sim_time, dt)
        n2 = self._post_process("gas_n2_pct", raw_n2, self._gas_n2_noise)
        results.append(
            self._make_sv("gas_n2_pct", n2, sim_time,
                          self._signal_configs.get("gas_n2_pct"))
        )

        # --- 6. vacuum_level ---
        raw_vacuum = self._vacuum_model.generate(sim_time, dt)
        if is_active:
            vacuum = self._post_process(
                "vacuum_level", raw_vacuum, self._vacuum_noise
            )
        else:
            vacuum = 0.0
        results.append(
            self._make_sv("vacuum_level", vacuum, sim_time,
                          self._signal_configs.get("vacuum_level"))
        )

        return results

    # -- Signal value helpers -------------------------------------------------

    def _post_process(
        self,
        signal_name: str,
        raw_value: float,
        noise: NoiseGenerator | None = None,
    ) -> float:
        """Apply noise, quantisation, and clamping to a raw signal value."""
        value = raw_value

        if noise is not None:
            value += noise.sample()

        sig_cfg = self._signal_configs.get(signal_name)
        if sig_cfg is not None:
            value = quantise(value, sig_cfg.resolution)
            value = clamp(value, sig_cfg.min_clamp, sig_cfg.max_clamp)

        return value

    def _make_sv(
        self,
        signal_name: str,
        value: float,
        sim_time: float,
        sig_cfg: SignalConfig | None = None,
    ) -> SignalValue:
        """Create a SignalValue with fully qualified signal ID."""
        return SignalValue(
            signal_id=self._signal_id(signal_name),
            value=value,
            timestamp=sim_time,
            quality="good",
        )
