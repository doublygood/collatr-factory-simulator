"""Oven (tunnel oven) equipment generator.

The oven cooks product as it travels on a conveyor belt through multiple
temperature zones.  It produces 13 signals and owns a 5-state machine
(Off/Preheat/Running/Idle/Cooldown).

Zone temperatures:
- 3 zones with independent Eurotherm PID controllers (FirstOrderLag)
- Zone 1: preheat zone, target ~160°C
- Zone 2: main cooking zone, target ~200°C
- Zone 3: finishing/holding zone, target ~180°C
- Thermal coupling: adjacent zones influence each other (coupling factor 0.05)

Zone output power:
- Correlated follower of zone temperature (inverse relationship)
- High output when zone is cold, low output at setpoint
- Served via multi-slave Modbus (UIDs 11, 12, 13) -- see Task 3.13

Product core temperature:
- ThermalDiffusionModel (Fourier series, PRD 4.2.10)
- Active during Running state; product enters at ~4°C, must reach 72°C

Belt speed:
- Steady-state with noise during Running/Preheat/Idle
- 0 when Off or Cooldown

PRD Reference: Section 2b.3 (Oven equipment), Section 4.2.10 (Thermal
    diffusion), Section 4.2.3 (First-order lag), Section 4.3.1 (correlation)
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
from factory_simulator.models.correlated import CorrelatedFollowerModel
from factory_simulator.models.first_order_lag import FirstOrderLagModel
from factory_simulator.models.noise import CholeskyCorrelator, NoiseGenerator
from factory_simulator.models.state import StateMachineModel
from factory_simulator.models.steady_state import SteadyStateModel
from factory_simulator.models.thermal_diffusion import ThermalDiffusionModel
from factory_simulator.store import SignalStore, SignalValue

# Oven states (PRD 2b.3)
STATE_OFF = 0
STATE_PREHEAT = 1
STATE_RUNNING = 2
STATE_IDLE = 3
STATE_COOLDOWN = 4

_STATE_NAMES = ["Off", "Preheat", "Running", "Idle", "Cooldown"]

# Ambient temperature for oven cool-down
_AMBIENT_TEMP_C = 20.0

# Product entry temperature (chilled ready meal)
_PRODUCT_ENTRY_TEMP_C = 4.0

# Default zone setpoints (PRD 2b.3: typical ready meal profile)
_DEFAULT_SP = [160.0, 200.0, 180.0]  # Zone 1, 2, 3

# Thermal coupling factor between adjacent zones (PRD 5.14.2)
_DEFAULT_COUPLING = 0.05

# State machine transition conditions (scenario/externally driven)
_DEFAULT_TRANSITIONS: list[dict[str, object]] = [
    {"from": "Off", "to": "Preheat", "trigger": "condition", "condition": "oven_start"},
    {"from": "Preheat", "to": "Running", "trigger": "condition", "condition": "production_start"},
    {"from": "Running", "to": "Idle", "trigger": "condition", "condition": "production_pause"},
    {"from": "Idle", "to": "Running", "trigger": "condition", "condition": "production_resume"},
    {"from": "Running", "to": "Cooldown", "trigger": "condition", "condition": "oven_stop"},
    {"from": "Idle", "to": "Cooldown", "trigger": "condition", "condition": "oven_stop"},
    {"from": "Preheat", "to": "Cooldown", "trigger": "condition", "condition": "oven_stop"},
    {"from": "Cooldown", "to": "Off", "trigger": "timer",
     "min_duration": 1800.0, "max_duration": 3600.0},
]


def _float_param(params: dict[str, object], key: str, default: float) -> float:
    raw = params.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


class OvenGenerator(EquipmentGenerator):
    """Tunnel oven generator -- 13 signals, 5-state machine.

    The oven state machine drives all signal behaviour:

    - **Off (0)**: Zone temps cool toward ambient.  Belt stopped.
    - **Preheat (1)**: Zone temps ramp to setpoints.  Belt slow or at target.
    - **Running (2)**: Production.  Product core temp active via ThermalDiffusion.
    - **Idle (3)**: At temperature but no product.  Belt at target.
    - **Cooldown (4)**: Zone temps cool toward ambient.  Belt stopped.

    Parameters
    ----------
    equipment_id:
        Equipment prefix, typically ``"oven"``.
    config:
        Oven equipment config from YAML.
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

        # Equipment-level config
        extras = config.model_extra or {}
        self._thermal_coupling: float = float(
            extras.get("thermal_coupling", _DEFAULT_COUPLING)
        )

        # State tracking
        self._prev_state: int = STATE_OFF
        self._is_first_tick: bool = True

        # Previous zone temps for thermal coupling computation
        self._prev_zone_temps: list[float] = list(_DEFAULT_SP)

        # Last product core temp value (held when not Running)
        self._product_core_value: float = _PRODUCT_ENTRY_TEMP_C

        self._build_models()

    # -- Model construction ---------------------------------------------------

    def _build_models(self) -> None:
        """Instantiate all signal models from config."""
        sigs = self._signal_configs

        # 1. State machine
        self._state_machine = self._build_state_machine(sigs.get("state"))

        # 2. Zone setpoints (SteadyState -- output to store each tick)
        self._zone_sp_models: list[SteadyStateModel] = [
            self._build_steady_state(sigs.get(f"zone_{i+1}_setpoint"))
            for i in range(3)
        ]

        # 3. Zone temperatures (FirstOrderLag tracking setpoints)
        # Extract noise generators separately for Cholesky correlation pipeline
        self._zone_temp_names = [
            "zone_1_temp", "zone_2_temp", "zone_3_temp",
        ]
        self._zone_temp_noises: list[NoiseGenerator | None] = []
        for name in self._zone_temp_names:
            sig_cfg = sigs.get(name)
            self._zone_temp_noises.append(
                self._make_noise(sig_cfg) if sig_cfg is not None else None,
            )
        # Pass noise=None to lag models — all noise applied via Cholesky
        self._zone_temp_models: list[FirstOrderLagModel] = [
            self._build_zone_temp(sigs.get(f"zone_{i+1}_temp"), apply_noise=False)
            for i in range(3)
        ]

        # PRD 4.3.1: Cholesky correlator for oven zone noise
        oven_extras = self._config.model_extra or {}
        custom_matrix = oven_extras.get("oven_zone_correlation_matrix")
        if custom_matrix is not None:
            oven_corr = np.array(custom_matrix, dtype=np.float64)
        else:
            # PRD Section 4.3.1 oven zone correlation matrix
            oven_corr = np.array([
                [1.0,  0.15, 0.05],
                [0.15, 1.0,  0.15],
                [0.05, 0.15, 1.0],
            ])
        self._oven_cholesky = CholeskyCorrelator(oven_corr)

        # 4. Belt speed (SteadyState)
        self._belt_speed_model = self._build_steady_state(sigs.get("belt_speed"))
        self._belt_speed_noise = (
            self._make_noise(sigs["belt_speed"]) if "belt_speed" in sigs else None
        )

        # 5. Product core temperature (ThermalDiffusionModel)
        self._thermal_diffusion = self._build_thermal_diffusion(
            sigs.get("product_core_temp")
        )

        # 6. Humidity zone 2 (SteadyState)
        self._humidity_model = self._build_steady_state(sigs.get("humidity_zone_2"))

        # 7. Output powers (CorrelatedFollower of zone temps, base 50, gain -0.3)
        self._output_power_models: list[CorrelatedFollowerModel] = [
            self._build_output_power(sigs.get(f"zone_{i+1}_output_power"))
            for i in range(3)
        ]

    def _build_state_machine(
        self, sig_cfg: SignalConfig | None,
    ) -> StateMachineModel:
        """Build oven state machine from config."""
        if sig_cfg is not None and sig_cfg.params:
            raw_states = sig_cfg.params.get("states", _STATE_NAMES)
            raw_initial = sig_cfg.params.get("initial_state", "Off")
        else:
            raw_states = _STATE_NAMES
            raw_initial = "Off"

        if isinstance(raw_states, list) and raw_states and isinstance(raw_states[0], str):
            state_dicts = [
                {"name": s.capitalize(), "value": float(i)}
                for i, s in enumerate(raw_states)
            ]
        else:
            state_dicts = list(raw_states)

        initial_state = str(raw_initial).capitalize()

        params: dict[str, object] = {
            "states": state_dicts,
            "transitions": list(_DEFAULT_TRANSITIONS),
            "initial_state": initial_state,
        }
        return StateMachineModel(params, self._spawn_rng())

    def _build_zone_temp(
        self,
        sig_cfg: SignalConfig | None,
        *,
        apply_noise: bool = True,
    ) -> FirstOrderLagModel:
        """Build a zone temperature first-order lag model.

        Zone always starts at ambient temperature (oven is off initially).
        The setpoint is updated to the configured target when the oven enters
        Preheat/Running/Idle via _handle_state_transition.

        Parameters
        ----------
        apply_noise:
            When *False*, the model is created without internal noise.
            Used for signals whose noise is applied externally via the
            Cholesky correlation pipeline (PRD 4.3.1).
        """
        params: dict[str, object] = {
            "setpoint": _AMBIENT_TEMP_C,
            "tau": 180.0,
            "initial_value": _AMBIENT_TEMP_C,
        }
        noise = None
        if sig_cfg is not None:
            params.update(sig_cfg.params)
            # Zone always starts at ambient regardless of configured initial_value
            params["setpoint"] = _AMBIENT_TEMP_C
            params["initial_value"] = _AMBIENT_TEMP_C
            if apply_noise:
                noise = self._make_noise(sig_cfg)
        return FirstOrderLagModel(params, self._spawn_rng(), noise=noise)

    def _build_steady_state(
        self, sig_cfg: SignalConfig | None,
    ) -> SteadyStateModel:
        """Build a steady state model from config."""
        params: dict[str, object] = {"target": 0.0}
        noise = None
        if sig_cfg is not None:
            params.update(sig_cfg.params)
            noise = self._make_noise(sig_cfg)
        return SteadyStateModel(params, self._spawn_rng(), noise=noise)

    def _build_thermal_diffusion(
        self, sig_cfg: SignalConfig | None,
    ) -> ThermalDiffusionModel:
        """Build ThermalDiffusionModel for product core temperature."""
        params: dict[str, object] = {
            "T_initial": _PRODUCT_ENTRY_TEMP_C,
            "T_oven": _DEFAULT_SP[1],  # zone 2 setpoint
            "alpha": 1.4e-7,
            "L": 0.025,
        }
        noise = None
        if sig_cfg is not None:
            # Only pick up alpha/L/T_initial from config if present
            for key in ("T_initial", "T_oven", "alpha", "L"):
                if key in sig_cfg.params:
                    params[key] = sig_cfg.params[key]
            noise = self._make_noise(sig_cfg)
        return ThermalDiffusionModel(params, self._spawn_rng(), noise=noise)

    def _build_output_power(
        self, sig_cfg: SignalConfig | None,
    ) -> CorrelatedFollowerModel:
        """Build correlated follower model for zone output power."""
        params: dict[str, object] = {"base": 50.0, "gain": -0.3}
        noise = None
        if sig_cfg is not None:
            p = sig_cfg.params
            params["base"] = float(p.get("base", 50.0))
            # Config uses "factor" key; CorrelatedFollowerModel uses "gain"
            params["gain"] = float(p.get("factor", p.get("gain", -0.3)))
            noise = self._make_noise(sig_cfg)
        return CorrelatedFollowerModel(params, self._spawn_rng(), noise=noise)

    # -- Public interface -----------------------------------------------------

    @property
    def state_machine(self) -> StateMachineModel:
        """Oven state machine (for scenarios and tests)."""
        return self._state_machine

    @property
    def zone_temp_models(self) -> list[FirstOrderLagModel]:
        """Zone temperature lag models [zone1, zone2, zone3] (for scenarios)."""
        return self._zone_temp_models

    @property
    def zone_setpoint_models(self) -> list[SteadyStateModel]:
        """Zone setpoint models [zone1, zone2, zone3] (for scenarios)."""
        return self._zone_sp_models

    @property
    def thermal_diffusion_model(self) -> ThermalDiffusionModel:
        """Product core temperature thermal diffusion model (for scenarios)."""
        return self._thermal_diffusion

    @property
    def thermal_coupling(self) -> float:
        """Thermal coupling factor between adjacent zones."""
        return self._thermal_coupling

    def get_signal_ids(self) -> list[str]:
        """Return all 13 oven signal IDs."""
        return [self._signal_id(name) for name in self._signal_configs]

    def generate(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> list[SignalValue]:
        """Generate all oven signals for one tick.

        Generation order:
        1. Machine state
        2. State cascade (setpoint management)
        3. Zone setpoints (SteadyState output)
        4. Zone temperatures (FirstOrderLag with thermal coupling)
        5. Belt speed
        6. Product core temperature (ThermalDiffusion during Running)
        7. Humidity zone 2
        8. Output powers (CorrelatedFollower of zone temps)
        """
        results: list[SignalValue] = []

        # --- 1. Machine state ---
        state_value = self._state_machine.generate(sim_time, dt)
        current_state = int(state_value)

        # --- 2. State cascade on transition ---
        if self._is_first_tick or current_state != self._prev_state:
            self._handle_state_transition(current_state, sim_time)
            self._is_first_tick = False

        self._prev_state = current_state

        results.append(self._make_sv(
            "state", state_value, sim_time,
            self._signal_configs.get("state"),
        ))

        # --- 3. Zone setpoints ---
        sp: list[float] = []
        for i in range(3):
            raw_sp = self._zone_sp_models[i].generate(sim_time, dt)
            sp_val = self._post_process(f"zone_{i+1}_setpoint", raw_sp)
            sp.append(sp_val)
            results.append(self._make_sv(
                f"zone_{i+1}_setpoint", sp_val, sim_time,
                self._signal_configs.get(f"zone_{i+1}_setpoint"),
            ))

        # --- 4. Zone temperatures ---
        # Update lag model setpoints (with thermal coupling from previous tick)
        if current_state in (STATE_PREHEAT, STATE_RUNNING, STATE_IDLE):
            self._update_zone_setpoints(sp)
        # Off/Cooldown setpoints updated in state transition

        # Generate raw (noise-free) zone temps from lag models
        raw_temps = [m.generate(sim_time, dt) for m in self._zone_temp_models]

        # PRD 4.3.1: Apply Cholesky-correlated noise across oven zones
        # Pipeline: N(0,1) draws → Cholesky L → scale by effective_sigma
        sigmas = np.array([
            ng.effective_sigma() if ng is not None else 0.0
            for ng in self._zone_temp_noises
        ])
        correlated_noise = self._oven_cholesky.generate_correlated(
            self._rng, sigmas,
        )

        zone_temps: list[float] = []
        for i, name in enumerate(self._zone_temp_names):
            z_temp = raw_temps[i] + float(correlated_noise[i])
            z_temp = self._post_process(name, z_temp)
            zone_temps.append(z_temp)
            results.append(self._make_sv(
                name, z_temp, sim_time,
                self._signal_configs.get(name),
            ))

        # Save zone temps for next tick's thermal coupling computation
        self._prev_zone_temps = list(zone_temps)

        # --- 5. Belt speed ---
        if current_state in (STATE_RUNNING, STATE_PREHEAT, STATE_IDLE):
            raw_belt = self._belt_speed_model.generate(sim_time, dt)
            belt_speed = raw_belt
            if self._belt_speed_noise is not None and current_state == STATE_RUNNING:
                belt_speed += self._belt_speed_noise.sample()
        else:
            belt_speed = 0.0

        belt_speed = self._post_process("belt_speed", belt_speed)
        # Override clamp: belt is 0 when Off or Cooldown
        if current_state in (STATE_OFF, STATE_COOLDOWN):
            belt_speed = 0.0
        results.append(self._make_sv(
            "belt_speed", belt_speed, sim_time,
            self._signal_configs.get("belt_speed"),
        ))

        # --- 6. Product core temperature ---
        if current_state == STATE_RUNNING:
            # Update thermal diffusion with current zone 2 temp (main cooking zone)
            self._thermal_diffusion.set_oven_temp(zone_temps[1])
            raw_core = self._thermal_diffusion.generate(sim_time, dt)
            self._product_core_value = raw_core
        else:
            raw_core = self._product_core_value

        core_temp = self._post_process("product_core_temp", raw_core)
        results.append(self._make_sv(
            "product_core_temp", core_temp, sim_time,
            self._signal_configs.get("product_core_temp"),
        ))

        # --- 7. Humidity zone 2 ---
        raw_hum = self._humidity_model.generate(sim_time, dt)
        humidity = self._post_process("humidity_zone_2", raw_hum)
        results.append(self._make_sv(
            "humidity_zone_2", humidity, sim_time,
            self._signal_configs.get("humidity_zone_2"),
        ))

        # --- 8. Output powers (correlated followers of zone temps) ---
        for i in range(3):
            self._output_power_models[i].set_parent_value(zone_temps[i])
            raw_power = self._output_power_models[i].generate(sim_time, dt)
            power = self._post_process(f"zone_{i+1}_output_power", raw_power)
            # Clamp to 0-100% (can't output negative power or > 100%)
            power = max(0.0, min(100.0, power))
            results.append(self._make_sv(
                f"zone_{i+1}_output_power", power, sim_time,
                self._signal_configs.get(f"zone_{i+1}_output_power"),
            ))

        return results

    # -- State cascade --------------------------------------------------------

    def _handle_state_transition(
        self, new_state: int, sim_time: float,
    ) -> None:
        """Handle oven signal cascade on state change.

        - Preheat/Running/Idle: zone temps track configured setpoints.
        - Off/Cooldown: zone temps cool toward ambient.
        - Running (entry): restart thermal diffusion model.
        - Running (exit): hold product_core_value at last computed value.
        """
        if new_state in (STATE_PREHEAT, STATE_RUNNING, STATE_IDLE):
            # Zone temps track configured setpoints
            for i in range(3):
                sp_val = self._zone_sp_models[i].target
                self._zone_temp_models[i].set_setpoint(sp_val)

        elif new_state in (STATE_OFF, STATE_COOLDOWN):
            # Zone temps cool toward ambient
            for model in self._zone_temp_models:
                model.set_setpoint(_AMBIENT_TEMP_C)

        if new_state == STATE_RUNNING:
            # New product enters oven: restart thermal diffusion
            # Use zone 2 setpoint as initial T_oven estimate
            zone2_sp = self._zone_sp_models[1].target
            self._thermal_diffusion.restart(
                T_initial=_PRODUCT_ENTRY_TEMP_C,
                T_oven=zone2_sp,
            )
            self._product_core_value = _PRODUCT_ENTRY_TEMP_C

    def _update_zone_setpoints(self, sp: list[float]) -> None:
        """Update zone temp lag model setpoints with thermal coupling.

        Adjacent zones influence each other with a small coupling factor.
        Uses previous tick's zone temps to avoid current-tick circular deps.

        Zone 1: influenced by zone 2.
        Zone 2: influenced by zones 1 and 3.
        Zone 3: influenced by zone 2.
        """
        c = self._thermal_coupling
        z1, z2, z3 = self._prev_zone_temps

        effective_sp = [
            sp[0] + c * (z2 - sp[0]),
            sp[1] + c * (z1 - sp[1]) + c * (z3 - sp[1]),
            sp[2] + c * (z2 - sp[2]),
        ]

        for i in range(3):
            self._zone_temp_models[i].set_setpoint(effective_sp[i])

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
        value: float | str,
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
