"""Mixer (high-shear) equipment generator.

The mixer prepares sauce/filling in batches.  It produces 8 signals and owns
a 6-state batch-cycle state machine (Off/Loading/Mixing/Holding/Discharging/CIP).

State cascade:
- **Off (0)**: All signals idle, speed 0, weight 0.
- **Loading (1)**: batch_weight ramps up, speed low (50-100 RPM), lid closed.
- **Mixing (2)**: Speed ramps to target, torque follows speed, batch_temp ramps.
- **Holding (3)**: Speed drops to 100-200 RPM, batch_temp holds at setpoint.
- **Discharging (4)**: batch_weight ramps down, speed low.
- **CIP (5)**: All production signals idle, CIP scenario manages signals.

PRD Reference: Section 2b.2 (Mixer equipment), Section 4.6 (F&B signal models)
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
from factory_simulator.models.counter import CounterModel
from factory_simulator.models.first_order_lag import FirstOrderLagModel
from factory_simulator.models.noise import NoiseGenerator
from factory_simulator.models.ramp import RampModel
from factory_simulator.models.state import StateMachineModel
from factory_simulator.models.string_generator import StringGeneratorModel
from factory_simulator.store import SignalStore, SignalValue

# Mixer states (PRD 2b.2)
STATE_OFF = 0
STATE_LOADING = 1
STATE_MIXING = 2
STATE_HOLDING = 3
STATE_DISCHARGING = 4
STATE_CIP = 5

_STATE_NAMES = ["Off", "Loading", "Mixing", "Holding", "Discharging", "Cip"]

# Loading parameters
_LOADING_LOW_SPEED = 75.0   # RPM during ingredient incorporation
_HOLDING_SPEED = 150.0      # RPM maintenance speed during hold
_DISCHARGE_SPEED = 50.0     # RPM low speed during discharge

# Default batch weight targets
_DEFAULT_BATCH_WEIGHT = 500.0  # kg

# Batch temperature defaults
_DEFAULT_BATCH_TEMP_SETPOINT = 65.0   # °C cooking target
_DEFAULT_BATCH_TEMP_AMBIENT = 4.0     # °C start (chilled ingredients)


def _float_param(params: dict[str, object], key: str, default: float) -> float:
    raw = params.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


class MixerGenerator(EquipmentGenerator):
    """High-shear mixer generator -- 8 signals, batch-cycle state machine.

    The mixer state machine drives all signal behaviour.  The batch cycle
    scenario (Task 3.15) triggers state transitions externally.

    Parameters
    ----------
    equipment_id:
        Equipment prefix, typically ``"mixer"``.
    config:
        Mixer equipment config from YAML.
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
        self._target_speed: float = float(
            extras.get("target_speed", 2000.0)
        )
        speed_range = extras.get("speed_range", [0, 3000])
        self._min_speed: float = float(speed_range[0])
        self._max_speed: float = float(speed_range[1])

        # Track state for cascade detection
        self._prev_state: int = STATE_OFF
        self._is_first_tick: bool = True

        # Build signal models
        self._build_models()

    # -- Model construction ---------------------------------------------------

    def _build_models(self) -> None:
        """Instantiate signal models from config."""
        sigs = self._signal_configs

        # 1. State machine
        self._state_machine = self._build_state_machine(sigs.get("state"))

        # 2. Speed (ramp model)
        self._speed_model = self._build_ramp(sigs.get("speed"))
        self._speed_noise = (
            self._make_noise(sigs["speed"]) if "speed" in sigs else None
        )

        # 3. Torque (correlated follower of speed)
        self._torque_model = self._build_correlated(sigs.get("torque"))

        # 4. Batch temperature (first-order lag tracking setpoint)
        self._batch_temp_model = self._build_first_order_lag(sigs.get("batch_temp"))

        # 5. Batch weight (ramp for loading/discharging)
        self._batch_weight_model = self._build_ramp(sigs.get("batch_weight"))
        self._batch_weight_noise = (
            self._make_noise(sigs["batch_weight"]) if "batch_weight" in sigs else None
        )

        # 6. Batch ID (string generator)
        self._batch_id_model = StringGeneratorModel(
            line_id="L1",
        )

        # 7. Mix time elapsed (counter, increments during mixing states)
        self._mix_time_model = self._build_counter(sigs.get("mix_time_elapsed"))

        # 8. Lid closed (binary state)
        self._lid_state_machine = self._build_lid_state(sigs.get("lid_closed"))

    def _build_state_machine(
        self, sig_cfg: SignalConfig | None,
    ) -> StateMachineModel:
        """Build the mixer state machine from config."""
        if sig_cfg is not None and sig_cfg.params:
            raw_states = sig_cfg.params.get("states", _STATE_NAMES)
            raw_initial = sig_cfg.params.get("initial_state", "Off")
        else:
            raw_states = _STATE_NAMES
            raw_initial = "Off"

        # Convert string-only state list to dicts
        if isinstance(raw_states, list) and raw_states and isinstance(raw_states[0], str):
            state_dicts = []
            for i, name in enumerate(raw_states):
                canonical = name.capitalize()
                state_dicts.append({"name": canonical, "value": float(i)})
        else:
            state_dicts = list(raw_states)

        initial_state = str(raw_initial).capitalize()

        # Mixer state transitions are primarily scenario-driven (batch cycle).
        # Provide minimal transitions; the batch cycle scenario uses force_state().
        transitions: list[dict[str, object]] = [
            {"from": "Off", "to": "Loading", "trigger": "condition",
             "condition": "batch_start"},
            {"from": "Loading", "to": "Mixing", "trigger": "condition",
             "condition": "loading_complete"},
            {"from": "Mixing", "to": "Holding", "trigger": "condition",
             "condition": "mixing_complete"},
            {"from": "Holding", "to": "Discharging", "trigger": "condition",
             "condition": "holding_complete"},
            {"from": "Discharging", "to": "Off", "trigger": "condition",
             "condition": "discharge_complete"},
            {"from": "Off", "to": "Cip", "trigger": "condition",
             "condition": "cip_start"},
            {"from": "Cip", "to": "Off", "trigger": "condition",
             "condition": "cip_complete"},
        ]

        params: dict[str, object] = {
            "states": state_dicts,
            "transitions": transitions,
            "initial_state": initial_state,
        }

        return StateMachineModel(params, self._spawn_rng())

    def _build_ramp(self, sig_cfg: SignalConfig | None) -> RampModel:
        """Build a ramp model.  Noise applied externally."""
        ramp_params: dict[str, object] = {
            "start": 0.0, "end": 0.0, "duration": 60.0, "steps": 1,
        }
        if sig_cfg is not None:
            dur = sig_cfg.params.get("ramp_duration_s", 60.0)
            ramp_params["duration"] = float(dur)
        return RampModel(ramp_params, self._spawn_rng())

    def _build_correlated(
        self, sig_cfg: SignalConfig | None,
    ) -> CorrelatedFollowerModel:
        """Build a correlated follower model from config."""
        params: dict[str, object] = {"base": 5.0, "gain": 0.03}
        noise = None
        if sig_cfg is not None:
            p = sig_cfg.params
            params["base"] = p.get("base", 5.0)
            params["gain"] = p.get("factor", p.get("gain", 0.03))
            noise = self._make_noise(sig_cfg)
        return CorrelatedFollowerModel(params, self._spawn_rng(), noise=noise)

    def _build_first_order_lag(
        self, sig_cfg: SignalConfig | None,
    ) -> FirstOrderLagModel:
        """Build a first-order lag model for batch temperature."""
        params: dict[str, object] = {
            "setpoint": _DEFAULT_BATCH_TEMP_AMBIENT,
            "tau": 300.0,
            "initial_value": _DEFAULT_BATCH_TEMP_AMBIENT,
        }
        noise = None
        if sig_cfg is not None:
            params.update(sig_cfg.params)
            # Use initial_value as the starting ambient temp
            if "initial_value" not in sig_cfg.params:
                params["initial_value"] = _DEFAULT_BATCH_TEMP_AMBIENT
            params["setpoint"] = params.get("initial_value", _DEFAULT_BATCH_TEMP_AMBIENT)
            noise = self._make_noise(sig_cfg)
        return FirstOrderLagModel(params, self._spawn_rng(), noise=noise)

    def _build_counter(self, sig_cfg: SignalConfig | None) -> CounterModel:
        """Build a counter model from config."""
        params: dict[str, object] = {"rate": 1.0}
        if sig_cfg is not None:
            params.update(sig_cfg.params)
        return CounterModel(params, self._spawn_rng())

    def _build_lid_state(
        self, sig_cfg: SignalConfig | None,
    ) -> StateMachineModel:
        """Build the lid binary state machine."""
        state_dicts = [
            {"name": "Open", "value": 0.0},
            {"name": "Closed", "value": 1.0},
        ]
        initial = "Closed"
        if sig_cfg is not None and sig_cfg.params:
            raw_initial = sig_cfg.params.get("initial_state", "closed")
            initial = str(raw_initial).capitalize()

        transitions: list[dict[str, object]] = [
            {"from": "Open", "to": "Closed", "trigger": "condition",
             "condition": "lid_close"},
            {"from": "Closed", "to": "Open", "trigger": "condition",
             "condition": "lid_open"},
        ]

        params: dict[str, object] = {
            "states": state_dicts,
            "transitions": transitions,
            "initial_state": initial,
        }

        return StateMachineModel(params, self._spawn_rng())

    # -- Public interface -----------------------------------------------------

    @property
    def state_machine(self) -> StateMachineModel:
        """Access the mixer state machine (for scenarios and tests)."""
        return self._state_machine

    @property
    def target_speed(self) -> float:
        """Configured target mixing speed (RPM)."""
        return self._target_speed

    @property
    def batch_id_model(self) -> StringGeneratorModel:
        """Access the batch ID string generator."""
        return self._batch_id_model

    @property
    def lid_state_machine(self) -> StateMachineModel:
        """Access the lid state machine."""
        return self._lid_state_machine

    @property
    def speed_model(self) -> RampModel:
        """Access the speed ramp model (for scenarios)."""
        return self._speed_model

    @property
    def batch_weight_model(self) -> RampModel:
        """Access the batch weight ramp model (for scenarios)."""
        return self._batch_weight_model

    @property
    def batch_temp_model(self) -> FirstOrderLagModel:
        """Access the batch temperature lag model (for scenarios)."""
        return self._batch_temp_model

    @property
    def mix_time_model(self) -> CounterModel:
        """Access the mix time counter (for scenarios)."""
        return self._mix_time_model

    def get_signal_ids(self) -> list[str]:
        """Return all 8 mixer signal IDs."""
        return [self._signal_id(name) for name in self._signal_configs]

    def get_counter_models(self) -> dict[str, CounterModel]:
        """Return counter models keyed by fully-qualified signal ID."""
        return {self._signal_id("mix_time_elapsed"): self._mix_time_model}

    def generate(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> list[SignalValue]:
        """Generate all mixer signals for one tick.

        Generation order:
        1. Machine state
        2. State cascade (ramp management)
        3. Speed
        4. Torque (correlated follower of speed)
        5. Batch temperature
        6. Batch weight
        7. Batch ID (string)
        8. Mix time elapsed
        9. Lid closed
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

        # --- 3. Speed ---
        is_active = current_state in (STATE_LOADING, STATE_MIXING,
                                       STATE_HOLDING, STATE_DISCHARGING)
        raw_speed = self._speed_model.generate(sim_time, dt)
        noise_for_speed = self._speed_noise if is_active else None
        speed = self._post_process("speed", raw_speed, noise_for_speed)
        results.append(self._make_sv(
            "speed", speed, sim_time,
            self._signal_configs.get("speed"),
        ))

        # --- 4. Torque (correlated follower of speed) ---
        self._torque_model.set_parent_value(speed)
        raw_torque = self._torque_model.generate(sim_time, dt)
        torque = self._post_process("torque", raw_torque)
        results.append(self._make_sv(
            "torque", torque, sim_time,
            self._signal_configs.get("torque"),
        ))

        # --- 5. Batch temperature ---
        raw_batch_temp = self._batch_temp_model.generate(sim_time, dt)
        batch_temp = self._post_process("batch_temp", raw_batch_temp)
        results.append(self._make_sv(
            "batch_temp", batch_temp, sim_time,
            self._signal_configs.get("batch_temp"),
        ))

        # --- 6. Batch weight ---
        raw_weight = self._batch_weight_model.generate(sim_time, dt)
        noise_for_weight = self._batch_weight_noise if is_active else None
        weight = self._post_process("batch_weight", raw_weight, noise_for_weight)
        results.append(self._make_sv(
            "batch_weight", weight, sim_time,
            self._signal_configs.get("batch_weight"),
        ))

        # --- 7. Batch ID ---
        batch_id_str = self._batch_id_model.generate(sim_time, dt)
        results.append(SignalValue(
            signal_id=self._signal_id("batch_id"),
            value=batch_id_str,
            timestamp=sim_time,
            quality="good",
        ))

        # --- 8. Mix time elapsed ---
        # Counter increments only when mixing is active (Mixing or Holding)
        if current_state in (STATE_MIXING, STATE_HOLDING):
            self._mix_time_model.set_speed(1.0)
        else:
            self._mix_time_model.set_speed(0.0)
        mix_time = self._mix_time_model.generate(sim_time, dt)
        results.append(self._make_sv(
            "mix_time_elapsed", mix_time, sim_time,
            self._signal_configs.get("mix_time_elapsed"),
        ))

        # --- 9. Lid closed ---
        lid_value = self._lid_state_machine.generate(sim_time, dt)
        results.append(self._make_sv(
            "lid_closed", lid_value, sim_time,
            self._signal_configs.get("lid_closed"),
        ))

        return results

    # -- State cascade --------------------------------------------------------

    def _handle_state_transition(
        self, new_state: int, sim_time: float,
    ) -> None:
        """Handle state cascade when mixer state changes.

        - Loading: start batch weight ramp up, low speed, close lid.
        - Mixing: start speed ramp to target, set batch temp setpoint.
        - Holding: drop speed to holding RPM, temp holds.
        - Discharging: start weight ramp down, low speed.
        - Off/CIP: speed 0, weight 0, reset mix time.
        """
        if new_state == STATE_LOADING:
            # Ramp weight up
            current_weight = self._batch_weight_model.value
            self._batch_weight_model.start_ramp(
                start=current_weight,
                end=_DEFAULT_BATCH_WEIGHT,
                duration=120.0,  # 2 min loading
            )
            # Low speed during ingredient addition
            current_speed = self._speed_model.value
            self._speed_model.start_ramp(
                start=current_speed,
                end=_LOADING_LOW_SPEED,
                duration=15.0,
            )
            # Close lid
            self._lid_state_machine.force_state("Closed")
            # New batch ID
            self._batch_id_model.new_batch()
            # Reset mix time
            self._mix_time_model.reset_counter()
            # Batch temp starts at ambient (chilled ingredients)
            self._batch_temp_model.set_setpoint(_DEFAULT_BATCH_TEMP_AMBIENT)

        elif new_state == STATE_MIXING:
            # Ramp speed to target
            current_speed = self._speed_model.value
            speed_cfg = self._signal_configs.get("speed")
            duration = 30.0
            if speed_cfg is not None:
                duration = float(speed_cfg.params.get("ramp_duration_s", 30.0))
            self._speed_model.start_ramp(
                start=current_speed,
                end=self._target_speed,
                duration=duration,
            )
            # Start heating batch (friction + jacket)
            self._batch_temp_model.set_setpoint(_DEFAULT_BATCH_TEMP_SETPOINT)
            # Weight holds at loaded value (ramp complete)
            current_weight = self._batch_weight_model.value
            self._batch_weight_model.start_ramp(
                start=current_weight,
                end=current_weight,
                duration=1.0,
            )

        elif new_state == STATE_HOLDING:
            # Drop speed to holding RPM
            current_speed = self._speed_model.value
            self._speed_model.start_ramp(
                start=current_speed,
                end=_HOLDING_SPEED,
                duration=15.0,
            )
            # Temp holds at setpoint (no change needed)

        elif new_state == STATE_DISCHARGING:
            # Ramp weight down
            current_weight = self._batch_weight_model.value
            self._batch_weight_model.start_ramp(
                start=current_weight,
                end=0.0,
                duration=90.0,  # 1.5 min discharge
            )
            # Low speed during discharge
            current_speed = self._speed_model.value
            self._speed_model.start_ramp(
                start=current_speed,
                end=_DISCHARGE_SPEED,
                duration=10.0,
            )

        elif new_state in (STATE_OFF, STATE_CIP):
            # Everything to zero / idle
            current_speed = self._speed_model.value
            if current_speed > 0.0:
                self._speed_model.start_ramp(
                    start=current_speed, end=0.0, duration=15.0,
                )
            current_weight = self._batch_weight_model.value
            if current_weight > 0.0:
                self._batch_weight_model.start_ramp(
                    start=current_weight, end=0.0, duration=30.0,
                )
            # Cool toward ambient
            self._batch_temp_model.set_setpoint(_DEFAULT_BATCH_TEMP_AMBIENT)

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
