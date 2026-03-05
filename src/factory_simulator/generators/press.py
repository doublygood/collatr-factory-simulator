"""Flexographic Press equipment generator.

The press is the primary machine on the packaging line, producing 21 of
47 signals.  It owns a 6-state state machine (Off/Setup/Running/Idle/
Fault/Maintenance) that drives all other press signals via state cascade.

PRD Reference: Section 2.2 (Press equipment), Section 8.4 (Generator
interface), Section 4.3 (Correlation model)
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
CLAUDE.md Rule 9: No locks (single writer, asyncio single-threaded).
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
from factory_simulator.models.depletion import DepletionModel
from factory_simulator.models.first_order_lag import FirstOrderLagModel
from factory_simulator.models.noise import CholeskyCorrelator, NoiseGenerator
from factory_simulator.models.ramp import RampModel
from factory_simulator.models.random_walk import RandomWalkModel
from factory_simulator.models.state import StateMachineModel
from factory_simulator.models.steady_state import SteadyStateModel
from factory_simulator.store import SignalStore, SignalValue

# Press machine states (PRD 2.2)
STATE_OFF = 0
STATE_SETUP = 1
STATE_RUNNING = 2
STATE_IDLE = 3
STATE_FAULT = 4
STATE_MAINTENANCE = 5

_STATE_NAMES = ["Off", "Setup", "Running", "Idle", "Fault", "Maintenance"]

# Ambient temperature for dryer cool-down (PRD Section 2.7)
_AMBIENT_TEMP_C = 20.0

# Default press state machine transitions
_DEFAULT_TRANSITIONS = [
    # Idle -> Setup (condition: scenario engine or test triggers job_start)
    {"from": "Idle", "to": "Setup", "trigger": "condition", "condition": "job_start"},
    # Setup -> Running (timer: 3-10 min setup)
    {"from": "Setup", "to": "Running", "trigger": "timer",
     "min_duration": 180.0, "max_duration": 600.0},
    # Running -> Idle (condition: scenario triggers job_complete)
    {"from": "Running", "to": "Idle", "trigger": "condition", "condition": "job_complete"},
    # Running -> Fault (probability: rare, ~0.0001/s = ~6/shift)
    {"from": "Running", "to": "Fault", "trigger": "probability",
     "probability": 0.0001, "min_duration": 60.0},
    # Fault -> Idle (timer: 1-10 min recovery)
    {"from": "Fault", "to": "Idle", "trigger": "timer",
     "min_duration": 60.0, "max_duration": 600.0},
    # Idle -> Off (condition: shutdown)
    {"from": "Idle", "to": "Off", "trigger": "condition", "condition": "shutdown"},
    # Off -> Setup (condition: startup)
    {"from": "Off", "to": "Setup", "trigger": "condition", "condition": "startup"},
    # Any -> Maintenance (conditions, one per source state)
    {"from": "Idle", "to": "Maintenance", "trigger": "condition",
     "condition": "maintenance_start"},
    {"from": "Off", "to": "Maintenance", "trigger": "condition",
     "condition": "maintenance_start"},
    # Maintenance -> Idle (timer)
    {"from": "Maintenance", "to": "Idle", "trigger": "timer",
     "min_duration": 1800.0, "max_duration": 7200.0},
]


def _float_param(params: dict[str, object], key: str, default: float) -> float:
    raw = params.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


class PressGenerator(EquipmentGenerator):
    """Flexographic press generator -- 21 signals, state-driven cascade.

    The press state machine drives all other signals:

    - **Off**: Speed 0, counters frozen, dryers cool toward ambient.
    - **Setup**: Speed 0, dryers heating to setpoint, preparing job.
    - **Running**: Speed ramping to target, counters active, full production.
    - **Idle**: Speed 0, counters frozen, dryers may stay warm.
    - **Fault**: Immediate speed 0, counters frozen, dryers hold.
    - **Maintenance**: Like Off, all systems down.

    Parameters
    ----------
    equipment_id:
        Equipment prefix, typically ``"press"``.
    config:
        Press equipment config from YAML.
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
            getattr(config, "target_speed", None) or extras.get("target_speed", 200.0)
        )
        speed_range = getattr(config, "speed_range", None) or extras.get(
            "speed_range", [50, 400]
        )
        self._min_speed: float = float(speed_range[0])
        self._max_speed: float = float(speed_range[1])

        # Track previous state for cascade detection
        self._prev_state: int = STATE_IDLE
        self._is_first_tick: bool = True

        # Build all signal models
        self._build_models()

    # -- Model construction ---------------------------------------------------

    def _build_models(self) -> None:
        """Instantiate signal models from config."""
        sigs = self._signal_configs

        # 1. State machine
        self._state_machine = self._build_state_machine(sigs.get("machine_state"))

        # 2. Line speed (ramp)
        self._line_speed_model = self._build_ramp(sigs.get("line_speed"))
        self._line_speed_noise = (
            self._make_noise(sigs["line_speed"]) if "line_speed" in sigs else None
        )

        # 3. Correlated followers
        self._web_tension = self._build_correlated(sigs.get("web_tension"))
        self._main_drive_current = self._build_correlated(sigs.get("main_drive_current"))
        self._main_drive_speed = self._build_correlated(sigs.get("main_drive_speed"))

        # 4. Random walks (registration error)
        self._reg_error_x = self._build_random_walk(sigs.get("registration_error_x"))
        self._reg_error_y = self._build_random_walk(sigs.get("registration_error_y"))

        # 5. Steady state signals
        self._ink_viscosity = self._build_steady_state(sigs.get("ink_viscosity"))
        self._ink_temperature = self._build_steady_state(sigs.get("ink_temperature"))
        self._nip_pressure = self._build_steady_state(sigs.get("nip_pressure"))

        # 6. Dryer setpoints (constant steady state, no noise)
        self._dryer_sp_1 = self._build_steady_state(sigs.get("dryer_setpoint_zone_1"))
        self._dryer_sp_2 = self._build_steady_state(sigs.get("dryer_setpoint_zone_2"))
        self._dryer_sp_3 = self._build_steady_state(sigs.get("dryer_setpoint_zone_3"))

        # 7. Dryer temperatures (first-order lag tracking setpoints)
        #    Noise is extracted and applied externally via Cholesky pipeline
        #    (PRD 4.3.1) to produce correlated noise across zones.
        self._dryer_temp_noises: list[NoiseGenerator | None] = []
        self._dryer_temp_names = [
            "dryer_temp_zone_1", "dryer_temp_zone_2", "dryer_temp_zone_3",
        ]
        for name in self._dryer_temp_names:
            sig_cfg = sigs.get(name)
            self._dryer_temp_noises.append(
                self._make_noise(sig_cfg) if sig_cfg is not None else None,
            )
        # Pass noise=None to lag models — all noise applied via Cholesky
        self._dryer_temp_1 = self._build_first_order_lag(
            sigs.get("dryer_temp_zone_1"), apply_noise=False,
        )
        self._dryer_temp_2 = self._build_first_order_lag(
            sigs.get("dryer_temp_zone_2"), apply_noise=False,
        )
        self._dryer_temp_3 = self._build_first_order_lag(
            sigs.get("dryer_temp_zone_3"), apply_noise=False,
        )
        self._dryer_temp_models = [
            self._dryer_temp_1, self._dryer_temp_2, self._dryer_temp_3,
        ]

        # PRD 4.3.1: Cholesky correlator for dryer zone noise
        dryer_extras = self._config.model_extra or {}
        custom_matrix = dryer_extras.get("dryer_zone_correlation_matrix")
        if custom_matrix is not None:
            dryer_corr = np.array(custom_matrix, dtype=np.float64)
        else:
            # PRD Section 4.3.1 dryer zone correlation matrix
            dryer_corr = np.array([
                [1.0,  0.1,  0.02],
                [0.1,  1.0,  0.1],
                [0.02, 0.1,  1.0],
            ])
        self._dryer_cholesky = CholeskyCorrelator(dryer_corr)

        # 8. Counters
        self._impression_count = self._build_counter(sigs.get("impression_count"))
        self._good_count = self._build_counter(sigs.get("good_count"))
        self._waste_count = self._build_counter(sigs.get("waste_count"))

        # 9. Depletion
        self._unwind_diameter = self._build_depletion(sigs.get("unwind_diameter"))

        # 10. Rewind diameter (counter that grows with usage)
        self._rewind_diameter = self._build_counter(sigs.get("rewind_diameter"))

    def _build_state_machine(
        self, sig_cfg: SignalConfig | None,
    ) -> StateMachineModel:
        """Build the press state machine from config."""
        params: dict[str, object] = {}

        if sig_cfg is not None and sig_cfg.params:
            raw_states = sig_cfg.params.get("states", _STATE_NAMES)
            raw_initial = sig_cfg.params.get("initial_state", "Idle")
            raw_transitions = sig_cfg.params.get("transitions")
        else:
            raw_states = _STATE_NAMES
            raw_initial = "Idle"
            raw_transitions = None

        # Convert string-only state list to dicts with name+value
        if isinstance(raw_states, list) and raw_states and isinstance(raw_states[0], str):
            state_dicts = []
            for i, name in enumerate(raw_states):
                # Capitalise to match our canonical names
                canonical = name.capitalize()
                state_dicts.append({"name": canonical, "value": float(i)})
        else:
            state_dicts = list(raw_states)

        # Normalise initial_state to capitalised form
        initial_state = str(raw_initial).capitalize()

        # Use config transitions or defaults
        transitions = list(raw_transitions) if raw_transitions else list(_DEFAULT_TRANSITIONS)

        params = {
            "states": state_dicts,
            "transitions": transitions,
            "initial_state": initial_state,
        }

        return StateMachineModel(params, self._spawn_rng())

    def _build_ramp(self, sig_cfg: SignalConfig | None) -> RampModel:
        """Build the line speed ramp model.

        Noise is NOT passed to the RampModel because it is applied
        externally via _line_speed_noise in _post_process, where we
        can suppress it when the machine is not running.
        """
        ramp_params: dict[str, object] = {
            "start": 0.0, "end": 0.0, "duration": 180.0, "steps": 1,
        }
        if sig_cfg is not None:
            dur = sig_cfg.params.get("ramp_duration_s", 180.0)
            ramp_params["duration"] = float(dur)
        return RampModel(ramp_params, self._spawn_rng())

    def _build_correlated(
        self, sig_cfg: SignalConfig | None,
    ) -> CorrelatedFollowerModel:
        """Build a correlated follower model from config."""
        params: dict[str, object] = {"base": 0.0, "gain": 1.0}
        noise = None
        if sig_cfg is not None:
            p = sig_cfg.params
            params["base"] = p.get("base", 0.0)
            params["gain"] = p.get("factor", p.get("gain", 1.0))
            noise = self._make_noise(sig_cfg)
        return CorrelatedFollowerModel(params, self._spawn_rng(), noise=noise)

    def _build_random_walk(
        self, sig_cfg: SignalConfig | None,
    ) -> RandomWalkModel:
        """Build a random walk model from config."""
        params: dict[str, object] = {
            "center": 0.0, "drift_rate": 0.01, "reversion_rate": 0.1,
        }
        noise = None
        if sig_cfg is not None:
            params.update(sig_cfg.params)
            if sig_cfg.min_clamp is not None:
                params["min_clamp"] = sig_cfg.min_clamp
            if sig_cfg.max_clamp is not None:
                params["max_clamp"] = sig_cfg.max_clamp
            noise = self._make_noise(sig_cfg)
        return RandomWalkModel(params, self._spawn_rng(), noise=noise)

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

    def _build_first_order_lag(
        self,
        sig_cfg: SignalConfig | None,
        *,
        apply_noise: bool = True,
    ) -> FirstOrderLagModel:
        """Build a first-order lag model from config.

        Parameters
        ----------
        apply_noise:
            When *False*, the model is created without internal noise.
            Used for signals whose noise is applied externally via the
            Cholesky correlation pipeline (PRD 4.3.1).
        """
        params: dict[str, object] = {
            "setpoint": _AMBIENT_TEMP_C, "tau": 120.0,
            "initial_value": _AMBIENT_TEMP_C,
        }
        noise = None
        if sig_cfg is not None:
            params.update(sig_cfg.params)
            if apply_noise:
                noise = self._make_noise(sig_cfg)
        return FirstOrderLagModel(params, self._spawn_rng(), noise=noise)

    def _build_counter(self, sig_cfg: SignalConfig | None) -> CounterModel:
        """Build a counter model from config."""
        params: dict[str, object] = {"rate": 1.0}
        if sig_cfg is not None:
            params.update(sig_cfg.params)
        return CounterModel(params, self._spawn_rng())

    def _build_depletion(
        self, sig_cfg: SignalConfig | None,
    ) -> DepletionModel:
        """Build a depletion model from config."""
        params: dict[str, object] = {
            "initial_value": 1200.0,
            "consumption_rate": 0.1,
        }
        noise = None
        if sig_cfg is not None:
            params.update(sig_cfg.params)
            noise = self._make_noise(sig_cfg)
        return DepletionModel(params, self._spawn_rng(), noise=noise)

    # -- Public interface -----------------------------------------------------

    @property
    def state_machine(self) -> StateMachineModel:
        """Access the press state machine (for scenarios and tests)."""
        return self._state_machine

    @property
    def target_speed(self) -> float:
        """Configured target operating speed (m/min)."""
        return self._target_speed

    def get_signal_ids(self) -> list[str]:
        """Return all 22 press signal IDs."""
        return [self._signal_id(name) for name in self._signal_configs]

    def get_counter_models(self) -> dict[str, CounterModel]:
        """Return counter models keyed by fully-qualified signal ID."""
        return {
            self._signal_id("impression_count"): self._impression_count,
            self._signal_id("good_count"): self._good_count,
            self._signal_id("waste_count"): self._waste_count,
            self._signal_id("rewind_diameter"): self._rewind_diameter,
        }

    def generate(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> list[SignalValue]:
        """Generate all press signals for one tick.

        Generation order respects dependencies:
        1. Machine state (drives everything)
        2. State cascade (ramp management, setpoint changes)
        3. Line speed (parent for correlated followers)
        4. Correlated followers
        5. Independent signals (registration, ink, dryer, etc.)
        6. Counters and depletion (depend on line speed)

        Parameters
        ----------
        sim_time:
            Current simulated time in seconds.
        dt:
            Simulated time delta for this tick in seconds.
        store:
            Signal store for reading cross-equipment state.
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
            "machine_state", state_value, sim_time,
            self._signal_configs.get("machine_state"),
        ))

        # --- 3. Line speed ---
        is_running = current_state == STATE_RUNNING
        raw_speed = self._line_speed_model.generate(sim_time, dt)
        # Only apply noise when the ramp is actually moving; at zero the
        # noise would leak positive values through the clamp floor.
        noise_for_speed = self._line_speed_noise if is_running else None
        speed = self._post_process("line_speed", raw_speed, noise_for_speed)
        results.append(self._make_sv(
            "line_speed", speed, sim_time,
            self._signal_configs.get("line_speed"),
        ))

        # --- 4. Correlated followers (depend on line speed) ---
        # Web tension
        self._web_tension.set_parent_value(speed)
        raw_tension = self._web_tension.generate(sim_time, dt)
        tension = self._post_process("web_tension", raw_tension)
        results.append(self._make_sv(
            "web_tension", tension, sim_time,
            self._signal_configs.get("web_tension"),
        ))

        # Main drive current
        self._main_drive_current.set_parent_value(speed)
        raw_current = self._main_drive_current.generate(sim_time, dt)
        drive_current = self._post_process("main_drive_current", raw_current)
        results.append(self._make_sv(
            "main_drive_current", drive_current, sim_time,
            self._signal_configs.get("main_drive_current"),
        ))

        # Main drive speed (RPM)
        self._main_drive_speed.set_parent_value(speed)
        raw_rpm = self._main_drive_speed.generate(sim_time, dt)
        drive_rpm = self._post_process("main_drive_speed", raw_rpm)
        results.append(self._make_sv(
            "main_drive_speed", drive_rpm, sim_time,
            self._signal_configs.get("main_drive_speed"),
        ))

        # --- 5. Registration errors (only drift when running) ---
        if is_running:
            raw_reg_x = self._reg_error_x.generate(sim_time, dt)
            raw_reg_y = self._reg_error_y.generate(sim_time, dt)
        else:
            # Frozen when not running (no drift)
            raw_reg_x = self._reg_error_x.value
            raw_reg_y = self._reg_error_y.value
        reg_x = self._post_process("registration_error_x", raw_reg_x)
        reg_y = self._post_process("registration_error_y", raw_reg_y)
        results.append(self._make_sv(
            "registration_error_x", reg_x, sim_time,
            self._signal_configs.get("registration_error_x"),
        ))
        results.append(self._make_sv(
            "registration_error_y", reg_y, sim_time,
            self._signal_configs.get("registration_error_y"),
        ))

        # --- 6. Ink signals ---
        raw_visc = self._ink_viscosity.generate(sim_time, dt)
        ink_visc = self._post_process("ink_viscosity", raw_visc)
        results.append(self._make_sv(
            "ink_viscosity", ink_visc, sim_time,
            self._signal_configs.get("ink_viscosity"),
        ))

        raw_ink_temp = self._ink_temperature.generate(sim_time, dt)
        ink_temp = self._post_process("ink_temperature", raw_ink_temp)
        results.append(self._make_sv(
            "ink_temperature", ink_temp, sim_time,
            self._signal_configs.get("ink_temperature"),
        ))

        # --- 7. Dryer setpoints (constant, no generation needed) ---
        sp1 = self._dryer_sp_1.generate(sim_time, dt)
        sp2 = self._dryer_sp_2.generate(sim_time, dt)
        sp3 = self._dryer_sp_3.generate(sim_time, dt)

        sp1 = self._post_process("dryer_setpoint_zone_1", sp1)
        sp2 = self._post_process("dryer_setpoint_zone_2", sp2)
        sp3 = self._post_process("dryer_setpoint_zone_3", sp3)

        results.append(self._make_sv(
            "dryer_setpoint_zone_1", sp1, sim_time,
            self._signal_configs.get("dryer_setpoint_zone_1"),
        ))
        results.append(self._make_sv(
            "dryer_setpoint_zone_2", sp2, sim_time,
            self._signal_configs.get("dryer_setpoint_zone_2"),
        ))
        results.append(self._make_sv(
            "dryer_setpoint_zone_3", sp3, sim_time,
            self._signal_configs.get("dryer_setpoint_zone_3"),
        ))

        # --- 8. Dryer temperatures (track setpoints) ---
        # Update setpoints on dryer lag models based on state
        self._update_dryer_setpoints(current_state, sp1, sp2, sp3)

        # Generate raw (noise-free) dryer temps from lag models
        raw_temps = [m.generate(sim_time, dt) for m in self._dryer_temp_models]

        # PRD 4.3.1: Apply Cholesky-correlated noise across dryer zones
        # Pipeline: N(0,1) draws → Cholesky L → scale by effective_sigma
        sigmas = np.array([
            ng.effective_sigma() if ng is not None else 0.0
            for ng in self._dryer_temp_noises
        ])
        correlated_noise = self._dryer_cholesky.generate_correlated(
            self._rng, sigmas,
        )

        for i, name in enumerate(self._dryer_temp_names):
            value = raw_temps[i] + float(correlated_noise[i])
            value = self._post_process(name, value)
            results.append(self._make_sv(
                name, value, sim_time,
                self._signal_configs.get(name),
            ))

        # --- 9. Nip pressure ---
        if current_state in (STATE_RUNNING, STATE_SETUP, STATE_IDLE):
            raw_nip = self._nip_pressure.generate(sim_time, dt)
        else:
            raw_nip = 0.0
        nip = self._post_process("nip_pressure", raw_nip)
        results.append(self._make_sv(
            "nip_pressure", nip, sim_time,
            self._signal_configs.get("nip_pressure"),
        ))

        # --- 10. Counters (proportional to line speed) ---
        self._impression_count.set_speed(speed)
        self._good_count.set_speed(speed)
        self._waste_count.set_speed(speed)

        imp = self._impression_count.generate(sim_time, dt)
        good = self._good_count.generate(sim_time, dt)
        waste = self._waste_count.generate(sim_time, dt)

        results.append(self._make_sv(
            "impression_count", imp, sim_time,
            self._signal_configs.get("impression_count"),
        ))
        results.append(self._make_sv(
            "good_count", good, sim_time,
            self._signal_configs.get("good_count"),
        ))
        results.append(self._make_sv(
            "waste_count", waste, sim_time,
            self._signal_configs.get("waste_count"),
        ))

        # --- 11. Unwind diameter (depletes with line speed) ---
        self._unwind_diameter.set_speed(speed)
        raw_unwind = self._unwind_diameter.generate(sim_time, dt)
        unwind = self._post_process("unwind_diameter", raw_unwind)
        results.append(self._make_sv(
            "unwind_diameter", unwind, sim_time,
            self._signal_configs.get("unwind_diameter"),
        ))

        # --- 12. Rewind diameter (grows with line speed) ---
        self._rewind_diameter.set_speed(speed)
        raw_rewind = self._rewind_diameter.generate(sim_time, dt)
        rewind = self._post_process("rewind_diameter", raw_rewind)
        results.append(self._make_sv(
            "rewind_diameter", rewind, sim_time,
            self._signal_configs.get("rewind_diameter"),
        ))

        # --- 11. Fault code (scenario-managed, preserve store value) ---
        # The unplanned_stop scenario writes fault_code to the store before
        # generators tick.  We read and re-emit the current value so the
        # signal is always present in the store for the Modbus register map.
        fault_sv = store.get(f"{self._equipment_id}.fault_code")
        fault_val = float(fault_sv.value) if fault_sv is not None else 0.0
        results.append(self._make_sv(
            "fault_code", fault_val, sim_time,
            self._signal_configs.get("fault_code"),
        ))

        return results

    # -- State cascade --------------------------------------------------------

    def _handle_state_transition(
        self, new_state: int, sim_time: float,
    ) -> None:
        """Handle state cascade when machine state changes.

        - Entering Running: start speed ramp up to target.
        - Leaving Running: start speed ramp down to 0.
        - Entering Fault: immediate ramp down (fast).
        - Entering Off/Maintenance: dryers cool to ambient.
        """
        if new_state == STATE_RUNNING:
            # Ramp up to target speed
            current_speed = self._line_speed_model.value
            ramp_cfg = self._signal_configs.get("line_speed")
            duration = 180.0  # default 3 min ramp
            if ramp_cfg is not None:
                duration = float(ramp_cfg.params.get("ramp_duration_s", 180.0))
            self._line_speed_model.start_ramp(
                start=current_speed,
                end=self._target_speed,
                duration=duration,
            )

        elif new_state == STATE_FAULT:
            # Fast ramp down on fault (30s emergency stop)
            current_speed = self._line_speed_model.value
            if current_speed > 0.0:
                self._line_speed_model.start_ramp(
                    start=current_speed, end=0.0, duration=30.0,
                )

        elif new_state in (STATE_OFF, STATE_IDLE, STATE_SETUP, STATE_MAINTENANCE):
            # Controlled ramp down if speed > 0
            current_speed = self._line_speed_model.value
            if current_speed > 0.0:
                self._line_speed_model.start_ramp(
                    start=current_speed, end=0.0, duration=60.0,
                )

        # Dryer setpoint changes on Off/Maintenance
        if new_state in (STATE_OFF, STATE_MAINTENANCE):
            # Dryers cool toward ambient (set setpoint to ambient temp)
            self._dryer_temp_1.set_setpoint(_AMBIENT_TEMP_C)
            self._dryer_temp_2.set_setpoint(_AMBIENT_TEMP_C)
            self._dryer_temp_3.set_setpoint(_AMBIENT_TEMP_C)

    def _update_dryer_setpoints(
        self,
        state: int,
        sp1: float,
        sp2: float,
        sp3: float,
    ) -> None:
        """Update dryer temperature setpoints based on machine state.

        When Running or Setup, dryers track their configured setpoints.
        When Off or Maintenance, they cool to ambient (handled in transition).
        """
        if state in (STATE_RUNNING, STATE_SETUP, STATE_IDLE):
            # Track the configured setpoints
            self._dryer_temp_1.set_setpoint(sp1)
            self._dryer_temp_2.set_setpoint(sp2)
            self._dryer_temp_3.set_setpoint(sp3)
        # Off/Maintenance/Fault: setpoints already set in transition handler

    # -- Signal value helpers -------------------------------------------------

    def _post_process(
        self,
        signal_name: str,
        raw_value: float,
        noise: NoiseGenerator | None = None,
    ) -> float:
        """Apply noise, quantisation, and clamping to a raw signal value."""
        value = raw_value

        # Add noise if provided externally (for signals where noise is
        # separate from the model, like line_speed)
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
