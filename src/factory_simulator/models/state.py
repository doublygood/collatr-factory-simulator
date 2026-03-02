"""State Machine signal model.

The signal transitions between discrete states based on rules and
probabilities.  Three trigger types are supported:

- **timer**: After entering the source state, draws a duration from
  ``uniform(min_duration, max_duration)``.  Fires when elapsed.
- **probability**: Per-second rate.  Each tick: ``p = rate * dt``.
  Forced at ``max_duration``.
- **condition**: Fires when named condition is ``True``.  Forced at
  ``max_duration``.

Used for: ``press.machine_state``, ``coder.state``,
``coder.nozzle_health``, ``coder.gutter_fault``.

PRD Reference: Section 4.2.9
CLAUDE.md Rule 6: uses sim_time and dt, never wall clock.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from factory_simulator.models.base import SignalModel

_VALID_TRIGGERS = frozenset({"timer", "probability", "condition"})


@dataclass(slots=True)
class _StateDefinition:
    """Internal representation of a single state."""

    name: str
    value: float


@dataclass(slots=True)
class _TransitionDefinition:
    """Internal representation of a transition between states."""

    from_state: str
    to_state: str
    trigger: str  # "timer", "probability", "condition"
    probability: float  # per-second rate for probability triggers
    min_duration: float  # min time in source state before this can fire
    max_duration: float  # max time; 0 = no maximum
    condition: str  # condition name for condition triggers


class StateMachineModel(SignalModel):
    """Discrete state machine model (PRD 4.2.9).

    Parameters (via ``params`` dict)
    ---------------------------------
    states : list[dict]
        Each dict has ``name`` (str) and ``value`` (float).
    transitions : list[dict]
        Each dict has ``from`` (str), ``to`` (str), ``trigger`` (str),
        and optionally ``probability`` (float), ``min_duration`` (float),
        ``max_duration`` (float), ``condition`` (str).
    initial_state : str
        Name of the starting state (default: first state in list).
    """

    def __init__(
        self,
        params: dict[str, object],
        rng: np.random.Generator,
    ) -> None:
        super().__init__(params, rng)

        # -- Parse states -------------------------------------------------
        raw_states = params.get("states", [])
        if not isinstance(raw_states, list) or len(raw_states) == 0:
            raise ValueError("states must be a non-empty list")

        self._states: dict[str, _StateDefinition] = {}
        self._state_order: list[str] = []
        for s in raw_states:
            if not isinstance(s, dict):
                raise ValueError(
                    "each state must be a dict with 'name' and 'value'"
                )
            name = str(s["name"])
            value = float(s["value"])
            if name in self._states:
                raise ValueError(f"duplicate state name: {name}")
            self._states[name] = _StateDefinition(name=name, value=value)
            self._state_order.append(name)

        # -- Parse transitions --------------------------------------------
        raw_trans = params.get("transitions", [])
        if not isinstance(raw_trans, list):
            raise ValueError("transitions must be a list")

        self._transitions: list[_TransitionDefinition] = []
        for t in raw_trans:
            if not isinstance(t, dict):
                raise ValueError("each transition must be a dict")
            self._parse_transition(t)

        # -- Initial state ------------------------------------------------
        initial_raw = params.get("initial_state", self._state_order[0])
        self._initial_state = str(initial_raw)
        if self._initial_state not in self._states:
            raise ValueError(
                f"initial_state '{self._initial_state}' not in states"
            )

        # -- Runtime state ------------------------------------------------
        self._current_state: str = self._initial_state
        self._time_in_state: float = 0.0
        self._state_changed: bool = False
        self._conditions: dict[str, bool] = {}
        self._timer_durations: dict[int, float] = {}

        self._draw_timer_durations()

    # -- Parsing ----------------------------------------------------------

    def _parse_transition(self, t: dict[str, object]) -> None:
        """Parse and validate a single transition dict."""
        from_state = str(t["from"])
        to_state = str(t["to"])
        trigger = str(t["trigger"])

        if from_state not in self._states:
            raise ValueError(
                f"transition from unknown state: {from_state}"
            )
        if to_state not in self._states:
            raise ValueError(
                f"transition to unknown state: {to_state}"
            )
        if trigger not in _VALID_TRIGGERS:
            raise ValueError(
                f"invalid trigger type: {trigger!r}, "
                f"must be one of {sorted(_VALID_TRIGGERS)}"
            )

        probability = float(t.get("probability", 0.0))  # type: ignore[arg-type]
        min_dur = float(t.get("min_duration", 0.0))  # type: ignore[arg-type]
        max_dur = float(t.get("max_duration", 0.0))  # type: ignore[arg-type]
        condition = str(t.get("condition", ""))

        if trigger == "probability" and probability < 0:
            raise ValueError("probability must be >= 0")
        if min_dur < 0:
            raise ValueError("min_duration must be >= 0")
        if max_dur < 0:
            raise ValueError("max_duration must be >= 0")
        if max_dur > 0 and max_dur < min_dur:
            raise ValueError(
                "max_duration must be >= min_duration when set"
            )
        if trigger == "condition" and not condition:
            raise ValueError(
                "condition trigger requires a 'condition' name"
            )

        self._transitions.append(
            _TransitionDefinition(
                from_state=from_state,
                to_state=to_state,
                trigger=trigger,
                probability=probability,
                min_duration=min_dur,
                max_duration=max_dur,
                condition=condition,
            )
        )

    # -- Timer management -------------------------------------------------

    def _draw_timer_durations(self) -> None:
        """Draw random durations for timer transitions from current state."""
        self._timer_durations.clear()
        for i, t in enumerate(self._transitions):
            if (
                t.from_state == self._current_state
                and t.trigger == "timer"
            ):
                if t.max_duration > t.min_duration:
                    duration = float(
                        self._rng.uniform(t.min_duration, t.max_duration)
                    )
                else:
                    duration = t.min_duration
                self._timer_durations[i] = duration

    # -- Properties -------------------------------------------------------

    @property
    def current_state(self) -> str:
        """Name of the current state."""
        return self._current_state

    @property
    def current_value(self) -> float:
        """Numeric value of the current state."""
        return self._states[self._current_state].value

    @property
    def time_in_state(self) -> float:
        """Time spent in the current state (seconds)."""
        return self._time_in_state

    @property
    def state_names(self) -> list[str]:
        """Ordered list of all state names."""
        return list(self._state_order)

    @property
    def state_changed(self) -> bool:
        """Whether the state changed during the last ``generate()`` call."""
        return self._state_changed

    # -- External inputs --------------------------------------------------

    def set_condition(self, name: str, value: bool) -> None:
        """Set an external condition for condition-based triggers."""
        self._conditions[name] = value

    def get_condition(self, name: str) -> bool:
        """Get value of a condition (``False`` if unset)."""
        return self._conditions.get(name, False)

    def force_state(self, state_name: str) -> None:
        """Force immediate transition (bypasses all checks).

        Used by the scenario engine for externally-driven state changes
        such as unplanned stops and shift changes.
        """
        if state_name not in self._states:
            raise ValueError(f"unknown state: {state_name}")
        self._enter_state(state_name)

    # -- Core -------------------------------------------------------------

    def _enter_state(self, state_name: str) -> None:
        """Enter a new state: reset timer, draw new durations."""
        self._current_state = state_name
        self._time_in_state = 0.0
        self._state_changed = True
        self._draw_timer_durations()

    def generate(self, sim_time: float, dt: float) -> float:
        """Evaluate transitions and return current state value.

        Parameters
        ----------
        sim_time:
            Current simulated time in seconds since start.
        dt:
            Simulated time delta for this tick in seconds.

        Returns
        -------
        float
            Numeric value of the current state.
        """
        self._state_changed = False
        self._time_in_state += dt
        self._evaluate_transitions(dt)
        return self._states[self._current_state].value

    def _evaluate_transitions(self, dt: float) -> None:
        """Check transitions from current state; fire first match."""
        for i, t in enumerate(self._transitions):
            if t.from_state != self._current_state:
                continue

            # Min-duration gate
            if self._time_in_state < t.min_duration:
                continue

            fired = False

            if t.trigger == "timer":
                drawn = self._timer_durations.get(i, t.min_duration)
                if self._time_in_state >= drawn:
                    fired = True

            elif t.trigger == "probability":
                if t.probability > 0 and dt > 0:
                    p_tick = min(t.probability * dt, 1.0)
                    if self._rng.random() < p_tick:
                        fired = True
                # Forced at max_duration
                if (
                    not fired
                    and t.max_duration > 0
                    and self._time_in_state >= t.max_duration
                ):
                    fired = True

            elif t.trigger == "condition":
                if self._conditions.get(t.condition, False):
                    fired = True
                # Forced at max_duration
                if (
                    not fired
                    and t.max_duration > 0
                    and self._time_in_state >= t.max_duration
                ):
                    fired = True

            if fired:
                self._enter_state(t.to_state)
                return

    def reset(self) -> None:
        """Reset to initial state."""
        self._current_state = self._initial_state
        self._time_in_state = 0.0
        self._state_changed = False
        self._conditions.clear()
        self._draw_timer_durations()
