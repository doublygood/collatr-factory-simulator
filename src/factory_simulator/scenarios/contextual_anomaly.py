"""Contextual anomaly scenario.

Contextual anomalies are signal values that are normal in one machine state
but anomalous in another.  Threshold-based detection algorithms cannot catch
them — the value is within range; only the context is wrong.

Five anomaly types (PRD 5.16):

1. heater_stuck      -- coder.printhead_temp at 40-42°C during coder Off/Standby
2. pressure_bleed    -- coder.ink_pressure at 800-850 mbar during coder Off
3. counter_false_trigger -- press.impression_count increments during press Idle
4. hot_during_maintenance -- press.dryer_temp_zone_1 at 100°C during Maintenance
5. vibration_during_off  -- vibration.main_drive_x at 3-5 mm/s during press Off

Scheduling: 2-5 events per simulated week (Poisson).  Each event picks one type
using probability weights from config, then waits for the required machine state.
If the target state never occurs within 2x the injection duration, the event
times out and is cancelled.

Injection mechanism: scenarios run BEFORE generators (PRD 8.2 step 3).  The
contextual anomaly uses the ``post_gen_inject`` hook that is called AFTER all
generators write, so the anomalous value overwrites whatever the generator
produced.

Priority: non_state_changing (deferred if a state_changing scenario is active).

PRD Reference: Section 5.16
CLAUDE.md Rule 6: uses sim_time/elapsed (simulation clock), never wall clock.
CLAUDE.md Rule 12: no global state, all state via instance variables.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.store import SignalStore


# ---------------------------------------------------------------------------
# Anomaly type metadata
# ---------------------------------------------------------------------------

# Machine state constants (mirrors press.py and coder.py)
_PRESS_RUNNING = 2
_PRESS_IDLE = 3
_PRESS_OFF = 0
_PRESS_MAINTENANCE = 5

_CODER_OFF = 0
_CODER_STANDBY = 4
_CODER_PRINTING = 2

#: Per-type: which signal to check for state, which values are the target
#: (anomalous) states, the affected signal, and the "expected" (normal) state
#: for ground truth logging.
_TYPE_META: dict[str, dict[str, Any]] = {
    "heater_stuck": {
        "state_signal": "coder.state",
        "target_states": frozenset({_CODER_OFF, _CODER_STANDBY}),
        "signal_id": "coder.printhead_temp",
        "value_range": [40.0, 42.0],
        "is_counter": False,
        "expected_state": _CODER_PRINTING,
    },
    "pressure_bleed": {
        "state_signal": "coder.state",
        "target_states": frozenset({_CODER_OFF}),
        "signal_id": "coder.ink_pressure",
        "value_range": [800.0, 850.0],
        "is_counter": False,
        "expected_state": _CODER_PRINTING,
    },
    "counter_false_trigger": {
        "state_signal": "press.machine_state",
        "target_states": frozenset({_PRESS_IDLE}),
        "signal_id": "press.impression_count",
        "value_range": None,   # special: increments each tick
        "is_counter": True,
        "expected_state": _PRESS_RUNNING,
    },
    "hot_during_maintenance": {
        "state_signal": "press.machine_state",
        "target_states": frozenset({_PRESS_MAINTENANCE}),
        "signal_id": "press.dryer_temp_zone_1",
        "value_range": [100.0, 100.0],
        "is_counter": False,
        "expected_state": _PRESS_RUNNING,
    },
    "vibration_during_off": {
        "state_signal": "press.machine_state",
        "target_states": frozenset({_PRESS_OFF}),
        "signal_id": "vibration.main_drive_x",
        "value_range": [3.0, 5.0],
        "is_counter": False,
        "expected_state": _PRESS_RUNNING,
    },
}

_ANOMALY_TYPE_NAMES: list[str] = list(_TYPE_META.keys())


# ---------------------------------------------------------------------------
# ContextualAnomaly
# ---------------------------------------------------------------------------


class ContextualAnomaly(Scenario):
    """Contextual anomaly: state-dependent signal injection (PRD 5.16).

    Parameters (via ``params`` dict)
    ---------------------------------
    types_config : dict
        Per-type configs keyed by anomaly type name.  Each entry contains:
        ``probability``, ``duration_seconds`` (range), and optionally
        ``increment_rate`` (for counter_false_trigger).
    """

    priority: ClassVar[str] = "non_state_changing"

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)
        p = self._params

        types_cfg: dict[str, Any] = dict(p.get("types_config", {}))  # type: ignore[call-overload]

        # -- Select anomaly type using probability weights --------------------
        weights = [
            float(types_cfg.get(t, {}).get("probability", 0.2))
            for t in _ANOMALY_TYPE_NAMES
        ]
        total = sum(weights)
        if total > 0.0:
            norm_weights = [w / total for w in weights]
        else:
            n = len(_ANOMALY_TYPE_NAMES)
            norm_weights = [1.0 / n] * n

        # Use categorical draw (cumulative sum with uniform draw)
        u = float(rng.uniform(0.0, 1.0))
        chosen_idx = len(_ANOMALY_TYPE_NAMES) - 1
        cumsum = 0.0
        for i, w in enumerate(norm_weights):
            cumsum += w
            if u <= cumsum:
                chosen_idx = i
                break
        self._anomaly_type: str = _ANOMALY_TYPE_NAMES[chosen_idx]

        # -- Draw injection duration from configured range --------------------
        type_entry: dict[str, Any] = types_cfg.get(self._anomaly_type, {})
        dur_range = type_entry.get("duration_seconds", [60.0, 600.0])
        if isinstance(dur_range, list | tuple) and len(dur_range) == 2:
            self._duration_s: float = float(
                rng.uniform(float(dur_range[0]), float(dur_range[1]))
            )
        else:
            self._duration_s = float(dur_range)

        # Timeout: give up waiting for target state after 2x injection duration
        self._timeout_s: float = 2.0 * self._duration_s

        # -- Draw injected value (for non-counter types) ----------------------
        meta = _TYPE_META[self._anomaly_type]
        value_range = meta["value_range"]
        if value_range is not None:
            lo, hi = float(value_range[0]), float(value_range[1])
            self._injected_value: float = (
                float(rng.uniform(lo, hi)) if lo != hi else lo
            )
        else:
            self._injected_value = 0.0  # unused for counter type

        # Counter increment rate (impressions per second)
        self._increment_rate: float = float(
            type_entry.get("increment_rate", 0.1)
        )

        # -- Runtime state (populated in _on_activate / _on_tick) -------------
        self._waiting: bool = True        # True = waiting for target state
        self._injecting: bool = False     # True = actively injecting
        self._inject_elapsed: float = 0.0
        self._injection_start_time: float = 0.0
        self._actual_state: int = -1      # state seen when injection started
        self._last_dt: float = 0.1        # updated each _on_tick for counter use
        self._gt_logged: bool = False     # guard: log ground truth once per inject

    # -- Public properties for testing ----------------------------------------

    @property
    def anomaly_type(self) -> str:
        """Name of the selected anomaly type."""
        return self._anomaly_type

    @property
    def duration_s(self) -> float:
        """Configured injection duration (seconds)."""
        return self._duration_s

    @property
    def timeout_s(self) -> float:
        """Timeout for waiting (seconds; 2x duration)."""
        return self._timeout_s

    @property
    def injected_value(self) -> float:
        """Injected signal value (0 for counter type)."""
        return self._injected_value

    @property
    def is_waiting(self) -> bool:
        """True while waiting for target machine state."""
        return self._waiting

    @property
    def is_injecting(self) -> bool:
        """True while actively writing the anomalous value."""
        return self._injecting

    def duration(self) -> float:
        """Total planned scenario duration (injection only, excludes wait)."""
        return self._duration_s

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(self, sim_time: float, engine: DataEngine) -> None:
        """Begin waiting for the target machine state."""
        self._waiting = True
        self._injecting = False
        self._inject_elapsed = 0.0
        self._gt_logged = False

    def _on_tick(self, sim_time: float, dt: float, engine: DataEngine) -> None:
        """Manage wait → inject → complete transitions."""
        self._last_dt = dt

        if self._waiting:
            # Check timeout: give up if target state never appeared
            if self._elapsed >= self._timeout_s:
                self.complete(sim_time, engine)
                return

            # Check if target state has been reached
            meta = _TYPE_META[self._anomaly_type]
            state_val = int(engine.store.get_value(meta["state_signal"], -1))
            if state_val in meta["target_states"]:
                # Target state found — switch to injecting
                self._waiting = False
                self._injecting = True
                self._inject_elapsed = 0.0
                self._injection_start_time = sim_time
                self._actual_state = state_val
                self._log_gt_start(sim_time, engine)

        else:
            # Currently injecting
            meta = _TYPE_META[self._anomaly_type]
            state_val = int(engine.store.get_value(meta["state_signal"], -1))

            # End early if machine state changed away from target
            if state_val not in meta["target_states"]:
                self.complete(sim_time, engine)
                return

            self._inject_elapsed += dt

            # End when full injection duration elapsed
            if self._inject_elapsed >= self._duration_s:
                self.complete(sim_time, engine)

    def post_gen_inject(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> None:
        """Write anomalous value to store after generators (PRD 5.16)."""
        if not self._injecting:
            return

        meta = _TYPE_META[self._anomaly_type]
        state_val = int(store.get_value(meta["state_signal"], -1))

        # Only inject while the machine is still in the target state
        if state_val not in meta["target_states"]:
            return

        signal_id: str = meta["signal_id"]

        if meta["is_counter"]:
            # Increment the counter by rate * dt
            current = float(store.get_value(signal_id, 0.0))
            new_val = current + self._increment_rate * dt
            store.set(signal_id, new_val, sim_time)
        else:
            store.set(signal_id, self._injected_value, sim_time)

    def _on_complete(self, sim_time: float, engine: DataEngine) -> None:
        """Log ground truth end event and reset injection state."""
        self._injecting = False
        self._waiting = False

        gt = engine.ground_truth
        if gt is not None:
            gt.log_scenario_end(sim_time, "ContextualAnomaly")

    # -- Helpers ---------------------------------------------------------------

    def _log_gt_start(self, sim_time: float, engine: DataEngine) -> None:
        """Log ground truth injection start (called when injection begins)."""
        if self._gt_logged:
            return
        self._gt_logged = True

        gt = engine.ground_truth
        if gt is None:
            return

        meta = _TYPE_META[self._anomaly_type]
        gt.log_contextual_anomaly(
            sim_time=sim_time,
            anomaly_type=self._anomaly_type,
            signal=meta["signal_id"],
            injected_value=self._injected_value,
            expected_state=meta["expected_state"],
            actual_state=self._actual_state,
        )
