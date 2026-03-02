"""Coding and Marking equipment generator.

The coder is a continuous inkjet printer (CIJ) that prints date codes,
batch numbers, and barcodes.  It produces 11 signals.  Its state
follows the press: Printing when the press is Running, Standby when
the press is Idle, Off when the press is Off.

PRD Reference: Section 2.5 (Coding and Marking equipment)
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
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
from factory_simulator.models.state import StateMachineModel
from factory_simulator.models.steady_state import SteadyStateModel
from factory_simulator.store import SignalStore, SignalValue

# Coder state enum (PRD 2.5)
CODER_OFF = 0
CODER_READY = 1
CODER_PRINTING = 2
CODER_FAULT = 3
CODER_STANDBY = 4

_CODER_STATE_NAMES = ["Off", "Ready", "Printing", "Fault", "Standby"]

# Default coder state machine transitions
_DEFAULT_CODER_TRANSITIONS = [
    # Ready -> Printing (condition: press starts running)
    {"from": "Ready", "to": "Printing", "trigger": "condition", "condition": "press_running"},
    # Printing -> Ready (condition: press stops)
    {"from": "Printing", "to": "Ready", "trigger": "condition", "condition": "press_stopped"},
    # Ready -> Standby (condition: press idle for extended period)
    {"from": "Ready", "to": "Standby", "trigger": "condition", "condition": "press_idle"},
    # Standby -> Ready (condition: press activity)
    {"from": "Standby", "to": "Ready", "trigger": "condition", "condition": "press_active"},
    # Printing -> Fault (probability: rare)
    {"from": "Printing", "to": "Fault", "trigger": "probability",
     "probability": 0.00005, "min_duration": 120.0},
    # Fault -> Ready (timer: recovery)
    {"from": "Fault", "to": "Ready", "trigger": "timer",
     "min_duration": 60.0, "max_duration": 300.0},
    # Ready -> Off (condition: shutdown)
    {"from": "Ready", "to": "Off", "trigger": "condition", "condition": "shutdown"},
    # Off -> Ready (condition: startup)
    {"from": "Off", "to": "Ready", "trigger": "condition", "condition": "startup"},
    # Standby -> Off (condition: shutdown)
    {"from": "Standby", "to": "Off", "trigger": "condition", "condition": "shutdown"},
]


def _float_param(params: dict[str, object], key: str, default: float) -> float:
    raw = params.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


class CoderGenerator(EquipmentGenerator):
    """Coder (CIJ printer) generator -- 11 signals, follows press state.

    Signals:
    - state: state machine (Off/Ready/Printing/Fault/Standby)
    - prints_total: counter (increments when Printing)
    - ink_level: depletion (depletes when Printing)
    - printhead_temp: steady state
    - ink_pump_speed: correlated follower of press.line_speed
    - ink_pressure: steady state
    - ink_viscosity_actual: steady state
    - supply_voltage: steady state
    - ink_consumption_ml: counter (increments when Printing)
    - nozzle_health: depletion (slow degradation)
    - gutter_fault: state machine (clear/fault binary)

    The coder derives its state from the press machine state:
    - Press Running -> Coder Printing
    - Press Idle -> Coder Ready (then Standby after a while)
    - Press Off -> Coder Off
    """

    def __init__(
        self,
        equipment_id: str,
        config: EquipmentConfig,
        rng: np.random.Generator,
    ) -> None:
        super().__init__(equipment_id, config, rng)
        self._prev_press_state: int = 3  # Idle
        self._quality_overrides: dict[str, str] = {}
        self._build_models()

    def _build_models(self) -> None:
        sigs = self._signal_configs

        # 1. Coder state machine
        self._state_machine = self._build_state_machine(sigs.get("state"))

        # 2. Prints total (counter)
        self._prints_total = self._build_counter(sigs.get("prints_total"))

        # 3. Ink level (depletion)
        self._ink_level = self._build_depletion(sigs.get("ink_level"))

        # 4. Printhead temp (steady state)
        self._printhead_temp = self._build_steady_state(sigs.get("printhead_temp"))

        # 5. Ink pump speed (correlated follower)
        self._ink_pump_speed = self._build_correlated(sigs.get("ink_pump_speed"))

        # 6. Ink pressure (steady state)
        self._ink_pressure = self._build_steady_state(sigs.get("ink_pressure"))

        # 7. Ink viscosity (steady state)
        self._ink_viscosity = self._build_steady_state(sigs.get("ink_viscosity_actual"))

        # 8. Supply voltage (steady state)
        self._supply_voltage = self._build_steady_state(sigs.get("supply_voltage"))

        # 9. Ink consumption (counter)
        self._ink_consumption = self._build_counter(sigs.get("ink_consumption_ml"))

        # 10. Nozzle health (depletion)
        self._nozzle_health = self._build_depletion(sigs.get("nozzle_health"))

        # 11. Gutter fault (state machine - binary)
        self._gutter_fault = self._build_gutter_fault(sigs.get("gutter_fault"))

    def _build_state_machine(
        self, sig_cfg: SignalConfig | None,
    ) -> StateMachineModel:
        if sig_cfg is not None and sig_cfg.params:
            raw_states = sig_cfg.params.get("states", _CODER_STATE_NAMES)
            raw_initial = sig_cfg.params.get("initial_state", "Ready")
        else:
            raw_states = _CODER_STATE_NAMES
            raw_initial = "Ready"

        if isinstance(raw_states, list) and raw_states and isinstance(raw_states[0], str):
            state_dicts = []
            for i, name in enumerate(raw_states):
                canonical = name.capitalize()
                state_dicts.append({"name": canonical, "value": float(i)})
        else:
            state_dicts = list(raw_states)

        initial_state = str(raw_initial).capitalize()

        params: dict[str, object] = {
            "states": state_dicts,
            "transitions": list(_DEFAULT_CODER_TRANSITIONS),
            "initial_state": initial_state,
        }

        return StateMachineModel(params, self._spawn_rng())

    def _build_gutter_fault(
        self, sig_cfg: SignalConfig | None,
    ) -> StateMachineModel:
        if sig_cfg is not None and sig_cfg.params:
            raw_states = sig_cfg.params.get("states", ["Clear", "Fault"])
            raw_initial = sig_cfg.params.get("initial_state", "Clear")
        else:
            raw_states = ["Clear", "Fault"]
            raw_initial = "Clear"

        if isinstance(raw_states, list) and raw_states and isinstance(raw_states[0], str):
            state_dicts = [
                {"name": name.capitalize(), "value": float(i)}
                for i, name in enumerate(raw_states)
            ]
        else:
            state_dicts = list(raw_states)

        # Gutter faults: MTBF 500+ hours (PRD 5.12, fix G5)
        # rate = 1 / (500 * 3600) ≈ 0.000000556 per second
        transitions = [
            {"from": "Clear", "to": "Fault", "trigger": "probability",
             "probability": 0.000000556, "min_duration": 300.0},
            {"from": "Fault", "to": "Clear", "trigger": "timer",
             "min_duration": 5.0, "max_duration": 30.0},
        ]

        params: dict[str, object] = {
            "states": state_dicts,
            "transitions": transitions,
            "initial_state": str(raw_initial).capitalize(),
        }

        return StateMachineModel(params, self._spawn_rng())

    def _build_steady_state(
        self, sig_cfg: SignalConfig | None,
    ) -> SteadyStateModel:
        params: dict[str, object] = {"target": 0.0}
        noise = None
        if sig_cfg is not None:
            params.update(sig_cfg.params)
            noise = self._make_noise(sig_cfg)
        return SteadyStateModel(params, self._spawn_rng(), noise=noise)

    def _build_correlated(
        self, sig_cfg: SignalConfig | None,
    ) -> CorrelatedFollowerModel:
        params: dict[str, object] = {"base": 0.0, "gain": 1.0}
        noise = None
        if sig_cfg is not None:
            p = sig_cfg.params
            params["base"] = p.get("base", 0.0)
            params["gain"] = p.get("factor", p.get("gain", 1.0))
            noise = self._make_noise(sig_cfg)
        return CorrelatedFollowerModel(params, self._spawn_rng(), noise=noise)

    def _build_counter(self, sig_cfg: SignalConfig | None) -> CounterModel:
        params: dict[str, object] = {"rate": 1.0}
        if sig_cfg is not None:
            params.update(sig_cfg.params)
        return CounterModel(params, self._spawn_rng())

    def _build_depletion(
        self, sig_cfg: SignalConfig | None,
    ) -> DepletionModel:
        params: dict[str, object] = {
            "initial_value": 100.0,
            "consumption_rate": 0.01,
        }
        noise = None
        if sig_cfg is not None:
            params.update(sig_cfg.params)
            noise = self._make_noise(sig_cfg)
        return DepletionModel(params, self._spawn_rng(), noise=noise)

    # -- Public interface ---

    @property
    def state_machine(self) -> StateMachineModel:
        """Access the coder state machine (for scenarios and tests)."""
        return self._state_machine

    def get_signal_ids(self) -> list[str]:
        return [self._signal_id(name) for name in self._signal_configs]

    def generate(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> list[SignalValue]:
        results: list[SignalValue] = []

        # Read press state to drive coder state transitions
        press_state = int(store.get_value("press.machine_state", 3))
        press_speed = float(store.get_value("press.line_speed", 0.0))

        # Drive coder state from press state via conditions
        self._update_conditions_from_press(press_state)

        # 1. Coder state
        state_value = self._state_machine.generate(sim_time, dt)
        current_state = int(state_value)
        is_printing = current_state == CODER_PRINTING
        is_active = current_state in (CODER_READY, CODER_PRINTING, CODER_STANDBY)

        results.append(self._make_sv(
            "state", state_value, sim_time,
        ))

        # 2. Prints total (only when printing)
        self._prints_total.set_speed(press_speed if is_printing else 0.0)
        raw_prints = self._prints_total.generate(sim_time, dt)
        results.append(self._make_sv("prints_total", raw_prints, sim_time))

        # 3. Ink level (depletes when printing)
        self._ink_level.set_speed(press_speed if is_printing else 0.0)
        raw_ink = self._ink_level.generate(sim_time, dt)
        ink_level = self._post_process("ink_level", raw_ink)
        results.append(self._make_sv("ink_level", ink_level, sim_time))

        # 4. Printhead temp (active when coder is on)
        raw_pht = self._printhead_temp.generate(sim_time, dt) if is_active else 25.0
        pht = self._post_process("printhead_temp", raw_pht)
        results.append(self._make_sv("printhead_temp", pht, sim_time))

        # 5. Ink pump speed (follows press speed when printing)
        self._ink_pump_speed.set_parent_value(press_speed if is_printing else 0.0)
        raw_pump = self._ink_pump_speed.generate(sim_time, dt)
        pump = self._post_process("ink_pump_speed", raw_pump)
        results.append(self._make_sv("ink_pump_speed", pump, sim_time))

        # 6. Ink pressure (active when coder is on)
        raw_pressure = self._ink_pressure.generate(sim_time, dt) if is_active else 0.0
        pressure = self._post_process("ink_pressure", raw_pressure)
        results.append(self._make_sv("ink_pressure", pressure, sim_time))

        # 7. Ink viscosity
        raw_visc = self._ink_viscosity.generate(sim_time, dt) if is_active else 0.0
        visc = self._post_process("ink_viscosity_actual", raw_visc)
        results.append(self._make_sv("ink_viscosity_actual", visc, sim_time))

        # 8. Supply voltage (always present when coder active)
        raw_volt = self._supply_voltage.generate(sim_time, dt) if is_active else 0.0
        volt = self._post_process("supply_voltage", raw_volt)
        results.append(self._make_sv("supply_voltage", volt, sim_time))

        # 9. Ink consumption (increments when printing)
        self._ink_consumption.set_speed(press_speed if is_printing else 0.0)
        raw_cons = self._ink_consumption.generate(sim_time, dt)
        results.append(self._make_sv("ink_consumption_ml", raw_cons, sim_time))

        # 10. Nozzle health (slow degradation when printing)
        self._nozzle_health.set_speed(press_speed if is_printing else 0.0)
        raw_nozzle = self._nozzle_health.generate(sim_time, dt)
        nozzle = self._post_process("nozzle_health", raw_nozzle)
        results.append(self._make_sv("nozzle_health", nozzle, sim_time))

        # 11. Gutter fault (binary state machine)
        gutter_val = self._gutter_fault.generate(sim_time, dt)
        results.append(self._make_sv("gutter_fault", gutter_val, sim_time))

        self._prev_press_state = press_state
        return results

    def _update_conditions_from_press(self, press_state: int) -> None:
        """Set coder state machine conditions based on press state."""
        # Press machine states: 0=Off, 1=Setup, 2=Running, 3=Idle, 4=Fault, 5=Maint
        is_running = press_state == 2
        is_idle = press_state == 3
        is_off = press_state in (0, 5)

        self._state_machine.set_condition("press_running", is_running)
        self._state_machine.set_condition("press_stopped", not is_running)
        self._state_machine.set_condition("press_idle", is_idle)
        self._state_machine.set_condition("press_active", is_running or press_state == 1)
        self._state_machine.set_condition("shutdown", is_off)
        self._state_machine.set_condition("startup", press_state == 1)

    # -- Helpers ---

    def _post_process(self, signal_name: str, raw_value: float) -> float:
        value = raw_value
        sig_cfg = self._signal_configs.get(signal_name)
        if sig_cfg is not None:
            value = quantise(value, sig_cfg.resolution)
            value = clamp(value, sig_cfg.min_clamp, sig_cfg.max_clamp)
        return value

    def _make_sv(
        self, signal_name: str, value: float, sim_time: float,
    ) -> SignalValue:
        quality = self._quality_overrides.get(signal_name, "good")
        return SignalValue(
            signal_id=self._signal_id(signal_name),
            value=value,
            timestamp=sim_time,
            quality=quality,
        )
