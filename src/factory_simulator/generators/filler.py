"""Filler (gravimetric filler) equipment generator.

The filler deposits measured portions of product into trays.  It produces
8 signals and owns a 5-state machine (Off/Setup/Running/Starved/Fault).

Per-item fill weight generation (PRD 4.6, plan section 4):
- Generates ONE value per simulated item arrival, not on every tick.
- Item arrival rate = line_speed (packs/min), so item_interval = 60/line_speed s.
- Between items the last fill_weight is held.
- fill_deviation = fill_weight - fill_target, computed on each new item.
- packs_produced increments by 1 on each item arrival.
- reject_count increments by 1 when |fill_deviation| > fill_tolerance.

Hopper level follows a sawtooth depletion pattern:
- DepletionModel depletes proportional to packs per second (line_speed / 60).
- Auto-refills when level hits threshold (upstream batch delivery).

PRD Reference: Section 2b.4 (Filling Station), Section 4.6 (F&B signal models)
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
from factory_simulator.models.depletion import DepletionModel
from factory_simulator.models.noise import NoiseGenerator
from factory_simulator.models.state import StateMachineModel
from factory_simulator.models.steady_state import SteadyStateModel
from factory_simulator.store import SignalStore, SignalValue

# Filler states (PRD 2b.4)
STATE_OFF = 0
STATE_SETUP = 1
STATE_RUNNING = 2
STATE_STARVED = 3
STATE_FAULT = 4

_STATE_NAMES = ["Off", "Setup", "Running", "Starved", "Fault"]

# Default fill parameters
_DEFAULT_FILL_TARGET_G = 400.0
_DEFAULT_FILL_GIVEAWAY_G = 5.0
_DEFAULT_FILL_SIGMA_G = 3.0
_DEFAULT_FILL_TOLERANCE_G = 15.0
_DEFAULT_LINE_SPEED_PPM = 60.0    # packs per minute


def _float_param(params: dict[str, object], key: str, default: float) -> float:
    raw = params.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


class FillerGenerator(EquipmentGenerator):
    """Gravimetric filler generator -- 8 signals, 5-state machine.

    The filler produces per-item fill weights drawn from a Gaussian
    distribution.  Between item arrivals the last fill_weight is held.

    Parameters
    ----------
    equipment_id:
        Equipment prefix, typically ``"filler"``.
    config:
        Filler equipment config from YAML.
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

        # Equipment-level fill parameters from config extras
        extras = config.model_extra or {}
        self._fill_target: float = float(
            extras.get("fill_target_g", _DEFAULT_FILL_TARGET_G)
        )
        self._fill_giveaway: float = float(
            extras.get("fill_giveaway_g", _DEFAULT_FILL_GIVEAWAY_G)
        )
        self._fill_sigma: float = float(
            extras.get("fill_sigma_g", _DEFAULT_FILL_SIGMA_G)
        )
        self._fill_tolerance: float = float(
            extras.get("fill_tolerance_g", _DEFAULT_FILL_TOLERANCE_G)
        )

        # Per-item timing state
        self._time_since_last_item: float = 0.0
        self._last_fill_weight: float = self._fill_target + self._fill_giveaway

        # Per-item counters (incremented discretely on item arrivals)
        self._packs_produced: float = 0.0
        self._reject_count: float = 0.0

        # State tracking
        self._prev_state: int = STATE_OFF
        self._is_first_tick: bool = True

        # Fill weight clamp bounds (read from signal config if available)
        self._fw_min: float = 200.0
        self._fw_max: float = 800.0
        fw_cfg = config.signals.get("fill_weight")
        if fw_cfg is not None:
            if fw_cfg.min_clamp is not None:
                self._fw_min = fw_cfg.min_clamp
            if fw_cfg.max_clamp is not None:
                self._fw_max = fw_cfg.max_clamp

        # Build signal models
        self._build_models()

    # -- Model construction ---------------------------------------------------

    def _build_models(self) -> None:
        """Instantiate signal models from config."""
        sigs = self._signal_configs

        # 1. State machine
        self._state_machine = self._build_state_machine(sigs.get("state"))

        # 2. Line speed (steady state + noise)
        self._line_speed_model = self._build_line_speed(sigs.get("line_speed"))
        self._line_speed_noise = (
            self._make_noise(sigs["line_speed"])
            if "line_speed" in sigs
            else None
        )

        # 3. Fill target (steady state, no noise)
        self._fill_target_model = self._build_fill_target(sigs.get("fill_target"))

        # 4. Hopper level (depletion model)
        self._hopper_model = self._build_hopper(sigs.get("hopper_level"))
        self._hopper_noise = (
            self._make_noise(sigs["hopper_level"])
            if "hopper_level" in sigs
            else None
        )

    def _build_state_machine(
        self, sig_cfg: SignalConfig | None,
    ) -> StateMachineModel:
        """Build the filler state machine from config."""
        if sig_cfg is not None and sig_cfg.params:
            raw_states = sig_cfg.params.get("states", _STATE_NAMES)
            raw_initial = sig_cfg.params.get("initial_state", "Off")
        else:
            raw_states = _STATE_NAMES
            raw_initial = "Off"

        # Convert string-only state list to dicts
        if isinstance(raw_states, list) and raw_states and isinstance(raw_states[0], str):
            state_dicts = [
                {"name": name.capitalize(), "value": float(i)}
                for i, name in enumerate(raw_states)
            ]
        else:
            state_dicts = list(raw_states)

        initial_state = str(raw_initial).capitalize()

        # Filler transitions are primarily scenario-driven
        transitions: list[dict[str, object]] = [
            {"from": "Off", "to": "Setup", "trigger": "condition",
             "condition": "line_start"},
            {"from": "Setup", "to": "Running", "trigger": "condition",
             "condition": "setup_complete"},
            {"from": "Running", "to": "Starved", "trigger": "condition",
             "condition": "hopper_empty"},
            {"from": "Starved", "to": "Running", "trigger": "condition",
             "condition": "hopper_refilled"},
            {"from": "Running", "to": "Fault", "trigger": "condition",
             "condition": "fault_detected"},
            {"from": "Fault", "to": "Off", "trigger": "condition",
             "condition": "fault_cleared"},
            {"from": "Running", "to": "Off", "trigger": "condition",
             "condition": "line_stop"},
            {"from": "Starved", "to": "Off", "trigger": "condition",
             "condition": "line_stop"},
        ]

        params: dict[str, object] = {
            "states": state_dicts,
            "transitions": transitions,
            "initial_state": initial_state,
        }

        return StateMachineModel(params, self._spawn_rng())

    def _build_line_speed(
        self, sig_cfg: SignalConfig | None,
    ) -> SteadyStateModel:
        """Build the line speed steady state model."""
        params: dict[str, object] = {"target": _DEFAULT_LINE_SPEED_PPM}
        if sig_cfg is not None and sig_cfg.params:
            params.update(sig_cfg.params)
        return SteadyStateModel(params, self._spawn_rng())

    def _build_fill_target(
        self, sig_cfg: SignalConfig | None,
    ) -> SteadyStateModel:
        """Build the fill target steady state model."""
        params: dict[str, object] = {"target": self._fill_target}
        if sig_cfg is not None and sig_cfg.params:
            params.update(sig_cfg.params)
        return SteadyStateModel(params, self._spawn_rng())

    def _build_hopper(
        self, sig_cfg: SignalConfig | None,
    ) -> DepletionModel:
        """Build the hopper level depletion model."""
        params: dict[str, object] = {
            "initial_value": 80.0,
            "consumption_rate": 0.1,
            "refill_threshold": 10.0,
            "refill_value": 90.0,
        }
        if sig_cfg is not None and sig_cfg.params:
            params.update(sig_cfg.params)
        return DepletionModel(params, self._spawn_rng())

    # -- Public interface -----------------------------------------------------

    @property
    def state_machine(self) -> StateMachineModel:
        """Access the filler state machine (for scenarios and tests)."""
        return self._state_machine

    @property
    def fill_target(self) -> float:
        """Configured fill target weight (g)."""
        return self._fill_target

    @property
    def fill_tolerance(self) -> float:
        """Configured fill tolerance (g)."""
        return self._fill_tolerance

    @property
    def hopper_model(self) -> DepletionModel:
        """Access the hopper depletion model (for scenarios)."""
        return self._hopper_model

    @property
    def packs_produced(self) -> float:
        """Current packs produced count."""
        return self._packs_produced

    @property
    def reject_count(self) -> float:
        """Current reject count."""
        return self._reject_count

    @property
    def last_fill_weight(self) -> float:
        """Last generated fill weight (g)."""
        return self._last_fill_weight

    @property
    def line_speed_model(self) -> SteadyStateModel:
        """Access the line speed model (for scenarios)."""
        return self._line_speed_model

    def get_signal_ids(self) -> list[str]:
        """Return all 8 filler signal IDs."""
        return [self._signal_id(name) for name in self._signal_configs]

    def generate(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> list[SignalValue]:
        """Generate all filler signals for one tick.

        Generation order:
        1. Machine state
        2. Line speed
        3. Per-item logic (fill_weight, fill_deviation, packs_produced, reject_count)
        4. Fill target
        5. Hopper level

        Per-item signals update only on item arrivals; between arrivals the
        last fill_weight is held unchanged.
        """
        results: list[SignalValue] = []

        # --- 1. Machine state ---
        state_value = self._state_machine.generate(sim_time, dt)
        current_state = int(state_value)

        if self._is_first_tick or current_state != self._prev_state:
            self._handle_state_transition(current_state)
            self._is_first_tick = False
        self._prev_state = current_state

        results.append(self._make_sv(
            "state", state_value, sim_time,
            self._signal_configs.get("state"),
        ))

        # --- 2. Line speed ---
        is_running = current_state == STATE_RUNNING
        if is_running:
            raw_speed = self._line_speed_model.generate(sim_time, dt)
            line_speed = self._post_process("line_speed", raw_speed,
                                            self._line_speed_noise)
        else:
            # Update model without noise so drift state stays current
            self._line_speed_model.generate(sim_time, dt)
            line_speed = 0.0

        results.append(self._make_sv(
            "line_speed", line_speed, sim_time,
            self._signal_configs.get("line_speed"),
        ))

        # --- 3. Per-item logic ---
        if is_running and line_speed > 0.0:
            item_interval = 60.0 / line_speed  # seconds between items
            self._time_since_last_item += dt

            if self._time_since_last_item >= item_interval:
                # Item arrived: draw new fill weight from Gaussian
                mean = self._fill_target + self._fill_giveaway
                fill_weight = float(self._rng.normal(mean, self._fill_sigma))
                fill_weight = clamp(fill_weight, self._fw_min, self._fw_max)
                self._last_fill_weight = fill_weight
                self._time_since_last_item -= item_interval  # carry remainder

                # Increment packs counter
                self._packs_produced = min(
                    self._packs_produced + 1.0, 999999.0
                )

                # Reject check: |deviation| > tolerance
                deviation = fill_weight - self._fill_target
                if abs(deviation) > self._fill_tolerance:
                    self._reject_count = min(self._reject_count + 1.0, 9999.0)
        else:
            # Not running or speed=0: reset item timer
            self._time_since_last_item = 0.0

        fill_deviation = self._last_fill_weight - self._fill_target

        results.append(self._make_sv(
            "fill_weight", self._last_fill_weight, sim_time,
            self._signal_configs.get("fill_weight"),
        ))
        results.append(self._make_sv(
            "fill_deviation",
            clamp(fill_deviation, -20.0, 20.0),
            sim_time,
            self._signal_configs.get("fill_deviation"),
        ))
        results.append(self._make_sv(
            "packs_produced", self._packs_produced, sim_time,
            self._signal_configs.get("packs_produced"),
        ))
        results.append(self._make_sv(
            "reject_count", self._reject_count, sim_time,
            self._signal_configs.get("reject_count"),
        ))

        # --- 4. Fill target ---
        raw_target = self._fill_target_model.generate(sim_time, dt)
        fill_target_value = self._post_process("fill_target", raw_target)
        results.append(self._make_sv(
            "fill_target", fill_target_value, sim_time,
            self._signal_configs.get("fill_target"),
        ))

        # --- 5. Hopper level ---
        if is_running and line_speed > 0.0:
            # Depletion proportional to packs per second
            self._hopper_model.set_speed(line_speed / 60.0)
        else:
            self._hopper_model.set_speed(0.0)
        raw_hopper = self._hopper_model.generate(sim_time, dt)
        hopper_level = self._post_process("hopper_level", raw_hopper,
                                          self._hopper_noise)
        results.append(self._make_sv(
            "hopper_level", hopper_level, sim_time,
            self._signal_configs.get("hopper_level"),
        ))

        return results

    # -- State cascade --------------------------------------------------------

    def _handle_state_transition(self, new_state: int) -> None:
        """Handle state cascade on transition.

        Resets per-item timer when leaving Running state.
        """
        if new_state in (STATE_OFF, STATE_SETUP, STATE_FAULT, STATE_STARVED):
            self._time_since_last_item = 0.0

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
