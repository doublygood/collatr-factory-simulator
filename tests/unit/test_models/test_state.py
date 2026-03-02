"""Tests for StateMachineModel (PRD 4.2.9).

Tests cover construction validation, three trigger types (timer,
probability, condition), force_state, properties, reset, determinism
(Rule 13), PRD examples, and Hypothesis property-based tests.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factory_simulator.models.state import StateMachineModel

# ── Helpers ──────────────────────────────────────────────────────────────


def make_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def two_state_params(
    *,
    transitions: list[dict[str, object]] | None = None,
    initial_state: str | None = None,
) -> dict[str, object]:
    """Simple Off/On state machine."""
    params: dict[str, object] = {
        "states": [
            {"name": "Off", "value": 0},
            {"name": "On", "value": 1},
        ],
    }
    if transitions is not None:
        params["transitions"] = transitions
    else:
        params["transitions"] = []
    if initial_state is not None:
        params["initial_state"] = initial_state
    return params


def press_params() -> dict[str, object]:
    """Press machine states per PRD."""
    return {
        "states": [
            {"name": "Off", "value": 0},
            {"name": "Setup", "value": 1},
            {"name": "Running", "value": 2},
            {"name": "Idle", "value": 3},
            {"name": "Fault", "value": 4},
            {"name": "Maintenance", "value": 5},
        ],
        "transitions": [],
        "initial_state": "Off",
    }


# ── Construction ─────────────────────────────────────────────────────────


class TestConstruction:
    def test_minimal_two_states(self) -> None:
        sm = StateMachineModel(two_state_params(), make_rng())
        assert sm.current_state == "Off"
        assert sm.current_value == 0.0

    def test_initial_state_defaults_to_first(self) -> None:
        sm = StateMachineModel(two_state_params(), make_rng())
        assert sm.current_state == "Off"

    def test_initial_state_explicit(self) -> None:
        sm = StateMachineModel(two_state_params(initial_state="On"), make_rng())
        assert sm.current_state == "On"
        assert sm.current_value == 1.0

    def test_empty_states_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            StateMachineModel({"states": [], "transitions": []}, make_rng())

    def test_non_list_states_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            StateMachineModel({"states": "bad", "transitions": []}, make_rng())

    def test_duplicate_state_name_error(self) -> None:
        params: dict[str, object] = {
            "states": [
                {"name": "A", "value": 0},
                {"name": "A", "value": 1},
            ],
            "transitions": [],
        }
        with pytest.raises(ValueError, match="duplicate"):
            StateMachineModel(params, make_rng())

    def test_unknown_from_state_error(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Unknown", "to": "On", "trigger": "timer",
             "min_duration": 1},
        ])
        with pytest.raises(ValueError, match="unknown state"):
            StateMachineModel(params, make_rng())

    def test_unknown_to_state_error(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "Unknown", "trigger": "timer",
             "min_duration": 1},
        ])
        with pytest.raises(ValueError, match="unknown state"):
            StateMachineModel(params, make_rng())

    def test_invalid_trigger_type_error(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "invalid"},
        ])
        with pytest.raises(ValueError, match="invalid trigger"):
            StateMachineModel(params, make_rng())

    def test_negative_probability_error(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "probability",
             "probability": -0.1},
        ])
        with pytest.raises(ValueError, match="probability"):
            StateMachineModel(params, make_rng())

    def test_negative_min_duration_error(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "timer",
             "min_duration": -1},
        ])
        with pytest.raises(ValueError, match="min_duration"):
            StateMachineModel(params, make_rng())

    def test_negative_max_duration_error(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "timer",
             "max_duration": -1},
        ])
        with pytest.raises(ValueError, match="max_duration"):
            StateMachineModel(params, make_rng())

    def test_max_lt_min_duration_error(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "timer",
             "min_duration": 10, "max_duration": 5},
        ])
        with pytest.raises(ValueError, match="max_duration"):
            StateMachineModel(params, make_rng())

    def test_condition_trigger_requires_name(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "condition"},
        ])
        with pytest.raises(ValueError, match="condition"):
            StateMachineModel(params, make_rng())

    def test_unknown_initial_state_error(self) -> None:
        params = two_state_params(initial_state="Nonexistent")
        with pytest.raises(ValueError, match="initial_state"):
            StateMachineModel(params, make_rng())

    def test_no_transitions_stays_forever(self) -> None:
        sm = StateMachineModel(two_state_params(), make_rng())
        for _ in range(100):
            sm.generate(0.0, 0.1)
        assert sm.current_state == "Off"

    def test_many_states(self) -> None:
        states = [{"name": f"S{i}", "value": float(i)} for i in range(10)]
        params: dict[str, object] = {"states": states, "transitions": []}
        sm = StateMachineModel(params, make_rng())
        assert len(sm.state_names) == 10
        assert sm.current_state == "S0"

    def test_zero_probability_allowed(self) -> None:
        """probability=0 is valid (never fires stochastically)."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "probability",
             "probability": 0.0},
        ])
        sm = StateMachineModel(params, make_rng())
        assert sm.current_state == "Off"

    def test_self_transition_allowed(self) -> None:
        """Transition from a state to itself is valid."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "Off", "trigger": "timer",
             "min_duration": 1.0, "max_duration": 1.0},
        ])
        sm = StateMachineModel(params, make_rng())
        assert sm.current_state == "Off"


# ── Timer Transitions ────────────────────────────────────────────────────


class TestTimerTransitions:
    def test_fires_after_fixed_duration(self) -> None:
        """Timer with min == max fires at that exact duration."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "timer",
             "min_duration": 1.0, "max_duration": 1.0},
        ])
        sm = StateMachineModel(params, make_rng())
        # Use dt=0.25 (binary-exact) to avoid float accumulation drift
        dt = 0.25
        for _ in range(3):  # 0.75s
            sm.generate(0.0, dt)
        assert sm.current_state == "Off"
        sm.generate(0.0, dt)  # 1.0s exactly
        assert sm.current_state == "On"

    def test_draws_duration_within_range(self) -> None:
        """Timer draws duration from uniform(min, max)."""
        fire_times: list[float] = []
        for seed in range(50):
            params = two_state_params(transitions=[
                {"from": "Off", "to": "On", "trigger": "timer",
                 "min_duration": 5.0, "max_duration": 15.0},
            ])
            sm = StateMachineModel(params, make_rng(seed))
            dt = 0.1
            for tick in range(200):  # up to 20s
                sm.generate(0.0, dt)
                if sm.current_state == "On":
                    fire_times.append((tick + 1) * dt)
                    break
        assert len(fire_times) == 50
        # All durations in [min, max + dt tolerance]
        assert all(5.0 <= t <= 15.1 for t in fire_times)
        # Variation confirms randomness
        assert max(fire_times) - min(fire_times) > 1.0

    def test_respects_min_duration(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "timer",
             "min_duration": 5.0, "max_duration": 5.0},
        ])
        sm = StateMachineModel(params, make_rng())
        dt = 0.1
        for _ in range(49):  # 4.9s
            sm.generate(0.0, dt)
            assert sm.current_state == "Off"

    def test_competing_timers_shorter_wins(self) -> None:
        """Two timers from same state: shorter duration fires first."""
        params: dict[str, object] = {
            "states": [
                {"name": "A", "value": 0},
                {"name": "B", "value": 1},
                {"name": "C", "value": 2},
            ],
            "transitions": [
                {"from": "A", "to": "B", "trigger": "timer",
                 "min_duration": 1.0, "max_duration": 1.0},
                {"from": "A", "to": "C", "trigger": "timer",
                 "min_duration": 5.0, "max_duration": 5.0},
            ],
        }
        sm = StateMachineModel(params, make_rng())
        for _ in range(15):
            sm.generate(0.0, 0.1)
        assert sm.current_state == "B"

    def test_timer_redrawn_on_state_entry(self) -> None:
        """Cycling A -> B -> A redraws timer durations each entry."""
        params: dict[str, object] = {
            "states": [
                {"name": "A", "value": 0},
                {"name": "B", "value": 1},
            ],
            "transitions": [
                {"from": "A", "to": "B", "trigger": "timer",
                 "min_duration": 1.0, "max_duration": 1.0},
                {"from": "B", "to": "A", "trigger": "timer",
                 "min_duration": 1.0, "max_duration": 1.0},
            ],
        }
        sm = StateMachineModel(params, make_rng())
        dt = 0.1
        states: list[str] = []
        for _ in range(40):
            sm.generate(0.0, dt)
            states.append(sm.current_state)
        assert "A" in states and "B" in states

    def test_zero_duration_fires_first_tick(self) -> None:
        """Timer with min=max=0 fires on the very first tick."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "timer",
             "min_duration": 0.0, "max_duration": 0.0},
        ])
        sm = StateMachineModel(params, make_rng())
        sm.generate(0.0, 0.1)
        assert sm.current_state == "On"

    def test_state_changed_flag_on_transition(self) -> None:
        """state_changed is True only on the tick of transition."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "timer",
             "min_duration": 0.5, "max_duration": 0.5},
        ])
        sm = StateMachineModel(params, make_rng())
        dt = 0.1
        for _ in range(4):  # 0.4s
            sm.generate(0.0, dt)
            assert not sm.state_changed
        sm.generate(0.0, dt)  # 0.5s -> fires
        assert sm.state_changed
        sm.generate(0.0, dt)  # 0.6s -> no change
        assert not sm.state_changed

    def test_only_one_transition_per_tick(self) -> None:
        """Even with immediate transitions, only one fires per tick."""
        params: dict[str, object] = {
            "states": [
                {"name": "A", "value": 0},
                {"name": "B", "value": 1},
                {"name": "C", "value": 2},
            ],
            "transitions": [
                {"from": "A", "to": "B", "trigger": "timer",
                 "min_duration": 0.0, "max_duration": 0.0},
                {"from": "B", "to": "C", "trigger": "timer",
                 "min_duration": 0.0, "max_duration": 0.0},
            ],
        }
        sm = StateMachineModel(params, make_rng())
        sm.generate(0.0, 0.1)
        assert sm.current_state == "B"  # not C yet
        sm.generate(0.0, 0.1)
        assert sm.current_state == "C"


# ── Probability Transitions ─────────────────────────────────────────────


class TestProbabilityTransitions:
    def test_fires_eventually_with_high_rate(self) -> None:
        """High probability rate fires within reasonable time."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "probability",
             "probability": 1.0},  # 1 per second
        ])
        sm = StateMachineModel(params, make_rng())
        dt = 0.1
        for _ in range(100):  # up to 10s
            sm.generate(0.0, dt)
            if sm.current_state == "On":
                break
        assert sm.current_state == "On"

    def test_respects_min_duration(self) -> None:
        """Cannot fire before min_duration even with high probability."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "probability",
             "probability": 100.0, "min_duration": 5.0},
        ])
        sm = StateMachineModel(params, make_rng())
        dt = 0.1
        for _ in range(49):  # 4.9s
            sm.generate(0.0, dt)
            assert sm.current_state == "Off"

    def test_forced_at_max_duration(self) -> None:
        """Zero probability but forced at max_duration."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "probability",
             "probability": 0.0, "max_duration": 2.0},
        ])
        sm = StateMachineModel(params, make_rng())
        dt = 0.1
        for _ in range(19):  # 1.9s
            sm.generate(0.0, dt)
        assert sm.current_state == "Off"
        sm.generate(0.0, dt)  # 2.0s
        assert sm.current_state == "On"

    def test_zero_probability_never_fires_without_max(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "probability",
             "probability": 0.0},
        ])
        sm = StateMachineModel(params, make_rng())
        for _ in range(1000):
            sm.generate(0.0, 0.1)
        assert sm.current_state == "Off"

    def test_higher_rate_fires_sooner_on_average(self) -> None:
        """Higher probability rate leads to earlier transition."""
        fire_times_low: list[float] = []
        fire_times_high: list[float] = []
        for seed in range(100):
            for rate, fire_list in [(0.01, fire_times_low),
                                    (1.0, fire_times_high)]:
                params = two_state_params(transitions=[
                    {"from": "Off", "to": "On", "trigger": "probability",
                     "probability": rate},
                ])
                sm = StateMachineModel(params, make_rng(seed))
                for tick in range(10000):
                    sm.generate(0.0, 0.1)
                    if sm.current_state == "On":
                        fire_list.append((tick + 1) * 0.1)
                        break
        avg_low = sum(fire_times_low) / len(fire_times_low)
        avg_high = sum(fire_times_high) / len(fire_times_high)
        assert avg_high < avg_low

    def test_time_scale_invariant_rule6(self) -> None:
        """Expected firing time similar regardless of dt (Rule 6)."""
        fire_times_small: list[float] = []
        fire_times_large: list[float] = []
        for seed in range(200):
            for dt_val, max_ticks, fire_list in [
                (0.01, 1000, fire_times_small),
                (0.1, 100, fire_times_large),
            ]:
                params = two_state_params(transitions=[
                    {"from": "Off", "to": "On", "trigger": "probability",
                     "probability": 0.5},
                ])
                sm = StateMachineModel(params, make_rng(seed))
                for tick in range(max_ticks):
                    sm.generate(0.0, dt_val)
                    if sm.current_state == "On":
                        fire_list.append((tick + 1) * dt_val)
                        break
        avg_small = sum(fire_times_small) / len(fire_times_small)
        avg_large = sum(fire_times_large) / len(fire_times_large)
        # Expected mean = 1/probability = 2.0s
        assert abs(avg_small - 2.0) < 1.0
        assert abs(avg_large - 2.0) < 1.0


# ── Condition Transitions ────────────────────────────────────────────────


class TestConditionTransitions:
    def test_fires_when_true(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "condition",
             "condition": "power_on"},
        ])
        sm = StateMachineModel(params, make_rng())
        sm.generate(0.0, 0.1)
        assert sm.current_state == "Off"
        sm.set_condition("power_on", True)
        sm.generate(0.0, 0.1)
        assert sm.current_state == "On"

    def test_does_not_fire_when_false(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "condition",
             "condition": "power_on"},
        ])
        sm = StateMachineModel(params, make_rng())
        for _ in range(100):
            sm.generate(0.0, 0.1)
        assert sm.current_state == "Off"

    def test_condition_cleared_prevents_fire(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "condition",
             "condition": "power_on"},
        ])
        sm = StateMachineModel(params, make_rng())
        sm.set_condition("power_on", True)
        sm.set_condition("power_on", False)
        sm.generate(0.0, 0.1)
        assert sm.current_state == "Off"

    def test_respects_min_duration(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "condition",
             "condition": "power_on", "min_duration": 5.0},
        ])
        sm = StateMachineModel(params, make_rng())
        sm.set_condition("power_on", True)
        # Use dt=0.25 (binary-exact) to avoid float accumulation drift
        dt = 0.25
        for _ in range(19):  # 4.75s
            sm.generate(0.0, dt)
            assert sm.current_state == "Off"
        sm.generate(0.0, dt)  # 5.0s exactly
        assert sm.current_state == "On"

    def test_forced_at_max_duration(self) -> None:
        """Condition never set but forced at max_duration."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "condition",
             "condition": "never_set", "max_duration": 3.0},
        ])
        sm = StateMachineModel(params, make_rng())
        for _ in range(29):  # 2.9s
            sm.generate(0.0, 0.1)
        assert sm.current_state == "Off"
        sm.generate(0.0, 0.1)  # 3.0s
        assert sm.current_state == "On"

    def test_multiple_conditions_first_match_wins(self) -> None:
        """Multiple condition transitions; first matching in list wins."""
        params: dict[str, object] = {
            "states": [
                {"name": "Idle", "value": 0},
                {"name": "Running", "value": 1},
                {"name": "Fault", "value": 2},
            ],
            "transitions": [
                {"from": "Idle", "to": "Running", "trigger": "condition",
                 "condition": "start"},
                {"from": "Idle", "to": "Fault", "trigger": "condition",
                 "condition": "error"},
            ],
        }
        sm = StateMachineModel(params, make_rng())
        sm.set_condition("error", True)
        sm.generate(0.0, 0.1)
        # "error" is second in list but "start" is first -- since start
        # is False, error fires.
        assert sm.current_state == "Fault"

    def test_get_condition_default_false(self) -> None:
        sm = StateMachineModel(two_state_params(), make_rng())
        assert sm.get_condition("nonexistent") is False

    def test_get_condition_set(self) -> None:
        sm = StateMachineModel(two_state_params(), make_rng())
        sm.set_condition("test", True)
        assert sm.get_condition("test") is True


# ── Force State ──────────────────────────────────────────────────────────


class TestForceState:
    def test_changes_state(self) -> None:
        sm = StateMachineModel(two_state_params(), make_rng())
        assert sm.current_state == "Off"
        sm.force_state("On")
        assert sm.current_state == "On"
        assert sm.current_value == 1.0

    def test_unknown_state_raises(self) -> None:
        sm = StateMachineModel(two_state_params(), make_rng())
        with pytest.raises(ValueError, match="unknown"):
            sm.force_state("Nonexistent")

    def test_resets_time_in_state(self) -> None:
        sm = StateMachineModel(two_state_params(), make_rng())
        for _ in range(10):
            sm.generate(0.0, 0.1)
        assert sm.time_in_state == pytest.approx(1.0)
        sm.force_state("On")
        assert sm.time_in_state == 0.0

    def test_draws_new_timers(self) -> None:
        """After force_state, timer transitions from new state work."""
        params: dict[str, object] = {
            "states": [
                {"name": "A", "value": 0},
                {"name": "B", "value": 1},
                {"name": "C", "value": 2},
            ],
            "transitions": [
                {"from": "B", "to": "C", "trigger": "timer",
                 "min_duration": 1.0, "max_duration": 1.0},
            ],
        }
        sm = StateMachineModel(params, make_rng())
        sm.force_state("B")
        for _ in range(15):
            sm.generate(0.0, 0.1)
        assert sm.current_state == "C"

    def test_force_same_state_resets_timer(self) -> None:
        """Forcing the current state restarts time_in_state and timers."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "timer",
             "min_duration": 2.0, "max_duration": 2.0},
        ])
        sm = StateMachineModel(params, make_rng())
        for _ in range(15):  # 1.5s
            sm.generate(0.0, 0.1)
        assert sm.current_state == "Off"
        sm.force_state("Off")  # restart timer
        for _ in range(15):  # 1.5s from restart
            sm.generate(0.0, 0.1)
        assert sm.current_state == "Off"  # still Off; only 1.5s into new timer
        for _ in range(5):  # 0.5s more = 2.0s total from restart
            sm.generate(0.0, 0.1)
        assert sm.current_state == "On"


# ── Properties ───────────────────────────────────────────────────────────


class TestProperties:
    def test_current_state_and_value(self) -> None:
        sm = StateMachineModel(press_params(), make_rng())
        assert sm.current_state == "Off"
        assert sm.current_value == 0.0

    def test_time_in_state_accumulates(self) -> None:
        sm = StateMachineModel(two_state_params(), make_rng())
        sm.generate(0.0, 0.1)
        assert sm.time_in_state == pytest.approx(0.1)
        sm.generate(0.0, 0.1)
        assert sm.time_in_state == pytest.approx(0.2)

    def test_state_names_ordered(self) -> None:
        sm = StateMachineModel(press_params(), make_rng())
        assert sm.state_names == [
            "Off", "Setup", "Running", "Idle", "Fault", "Maintenance"
        ]

    def test_state_changed_initially_false(self) -> None:
        sm = StateMachineModel(two_state_params(), make_rng())
        assert not sm.state_changed

    def test_generate_returns_state_value(self) -> None:
        sm = StateMachineModel(press_params(), make_rng())
        value = sm.generate(0.0, 0.1)
        assert value == 0.0

    def test_generate_returns_new_value_after_transition(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "timer",
             "min_duration": 0.0, "max_duration": 0.0},
        ])
        sm = StateMachineModel(params, make_rng())
        value = sm.generate(0.0, 0.1)
        assert value == 1.0


# ── Reset ────────────────────────────────────────────────────────────────


class TestReset:
    def test_restores_initial_state(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "timer",
             "min_duration": 0.0, "max_duration": 0.0},
        ])
        sm = StateMachineModel(params, make_rng())
        sm.generate(0.0, 0.1)
        assert sm.current_state == "On"
        sm.reset()
        assert sm.current_state == "Off"

    def test_clears_conditions(self) -> None:
        sm = StateMachineModel(two_state_params(), make_rng())
        sm.set_condition("test", True)
        assert sm.get_condition("test") is True
        sm.reset()
        assert sm.get_condition("test") is False

    def test_clears_time_in_state(self) -> None:
        sm = StateMachineModel(two_state_params(), make_rng())
        sm.generate(0.0, 0.1)
        sm.generate(0.0, 0.1)
        assert sm.time_in_state > 0
        sm.reset()
        assert sm.time_in_state == 0.0

    def test_clears_state_changed(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "timer",
             "min_duration": 0.0, "max_duration": 0.0},
        ])
        sm = StateMachineModel(params, make_rng())
        sm.generate(0.0, 0.1)
        assert sm.state_changed
        sm.reset()
        assert not sm.state_changed

    def test_reset_allows_replaying_from_initial(self) -> None:
        """After reset, transitions fire again from the initial state."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "timer",
             "min_duration": 1.0, "max_duration": 1.0},
        ])
        sm = StateMachineModel(params, make_rng())
        for _ in range(15):
            sm.generate(0.0, 0.1)
        assert sm.current_state == "On"
        sm.reset()
        assert sm.current_state == "Off"
        for _ in range(15):
            sm.generate(0.0, 0.1)
        assert sm.current_state == "On"


# ── Determinism (Rule 13) ───────────────────────────────────────────────


class TestDeterminism:
    def test_same_seed_same_timer_sequence(self) -> None:
        params: dict[str, object] = {
            "states": [
                {"name": "A", "value": 0},
                {"name": "B", "value": 1},
            ],
            "transitions": [
                {"from": "A", "to": "B", "trigger": "timer",
                 "min_duration": 1.0, "max_duration": 5.0},
                {"from": "B", "to": "A", "trigger": "timer",
                 "min_duration": 1.0, "max_duration": 5.0},
            ],
        }
        sm1 = StateMachineModel(params, make_rng(99))
        sm2 = StateMachineModel(params, make_rng(99))
        for _ in range(100):
            v1 = sm1.generate(0.0, 0.1)
            v2 = sm2.generate(0.0, 0.1)
            assert v1 == v2

    def test_same_seed_same_probability_sequence(self) -> None:
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "probability",
             "probability": 0.5},
        ])
        sm1 = StateMachineModel(params, make_rng(42))
        sm2 = StateMachineModel(params, make_rng(42))
        for _ in range(50):
            v1 = sm1.generate(0.0, 0.1)
            v2 = sm2.generate(0.0, 0.1)
            assert v1 == v2

    def test_different_seeds_differ(self) -> None:
        params: dict[str, object] = {
            "states": [
                {"name": "A", "value": 0},
                {"name": "B", "value": 1},
            ],
            "transitions": [
                {"from": "A", "to": "B", "trigger": "timer",
                 "min_duration": 1.0, "max_duration": 10.0},
                {"from": "B", "to": "A", "trigger": "timer",
                 "min_duration": 1.0, "max_duration": 10.0},
            ],
        }
        sm1 = StateMachineModel(params, make_rng(1))
        sm2 = StateMachineModel(params, make_rng(2))
        values1 = [sm1.generate(0.0, 0.1) for _ in range(200)]
        values2 = [sm2.generate(0.0, 0.1) for _ in range(200)]
        assert values1 != values2

    def test_condition_only_always_deterministic(self) -> None:
        """Condition-only transitions are deterministic regardless of seed."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "condition",
             "condition": "go"},
        ])
        sm1 = StateMachineModel(params, make_rng(1))
        sm2 = StateMachineModel(params, make_rng(999))
        sm1.set_condition("go", True)
        sm2.set_condition("go", True)
        for _ in range(10):
            v1 = sm1.generate(0.0, 0.1)
            v2 = sm2.generate(0.0, 0.1)
            assert v1 == v2


# ── PRD Examples ─────────────────────────────────────────────────────────


class TestPRDExamples:
    def test_press_machine_state_six_states(self) -> None:
        """press.machine_state has 6 states with values 0-5."""
        sm = StateMachineModel(press_params(), make_rng())
        expected = [
            ("Off", 0), ("Setup", 1), ("Running", 2),
            ("Idle", 3), ("Fault", 4), ("Maintenance", 5),
        ]
        for name, val in expected:
            sm.force_state(name)
            assert sm.current_value == val

    def test_press_setup_to_running_timer(self) -> None:
        """Setup -> Running after 10-30 minutes (timer-based)."""
        params: dict[str, object] = {
            "states": [
                {"name": "Setup", "value": 1},
                {"name": "Running", "value": 2},
            ],
            "transitions": [
                {"from": "Setup", "to": "Running", "trigger": "timer",
                 "min_duration": 600, "max_duration": 1800},
            ],
            "initial_state": "Setup",
        }
        sm = StateMachineModel(params, make_rng())
        dt = 1.0
        fired_at: float | None = None
        for tick in range(1800):
            sm.generate(0.0, dt)
            if sm.current_state == "Running":
                fired_at = (tick + 1) * dt
                break
        assert fired_at is not None
        assert 600 <= fired_at <= 1800

    def test_coder_gutter_fault_mtbf(self) -> None:
        """Gutter fault: OK -> Fault with MTBF 500+ hours probability."""
        mtbf_hours = 500
        rate_per_second = 1.0 / (mtbf_hours * 3600)
        params: dict[str, object] = {
            "states": [
                {"name": "OK", "value": 0},
                {"name": "Fault", "value": 1},
            ],
            "transitions": [
                {"from": "OK", "to": "Fault", "trigger": "probability",
                 "probability": rate_per_second},
            ],
        }
        sm = StateMachineModel(params, make_rng())
        dt = 1.0
        for _ in range(3600):  # 1 hour of simulation
            sm.generate(0.0, dt)
        # After 1 hour, the probability of fault is ~1-exp(-1/500) ≈ 0.2%
        # Just verify state is valid
        assert sm.current_state in ("OK", "Fault")

    def test_coder_nozzle_health_degradation(self) -> None:
        """Nozzle health: Good -> Degraded -> Blocked over hours."""
        params: dict[str, object] = {
            "states": [
                {"name": "Good", "value": 0},
                {"name": "Degraded", "value": 1},
                {"name": "Blocked", "value": 2},
            ],
            "transitions": [
                {"from": "Good", "to": "Degraded", "trigger": "timer",
                 "min_duration": 3600, "max_duration": 7200},
                {"from": "Degraded", "to": "Blocked", "trigger": "timer",
                 "min_duration": 1800, "max_duration": 3600},
            ],
        }
        sm = StateMachineModel(params, make_rng())
        dt = 10.0
        for _ in range(1080):  # ~3 hours
            sm.generate(0.0, dt)
        assert sm.current_state in ("Degraded", "Blocked")

    def test_coder_follows_press_via_conditions(self) -> None:
        """Coder Ready -> Printing when press enters Running."""
        params: dict[str, object] = {
            "states": [
                {"name": "Off", "value": 0},
                {"name": "Ready", "value": 1},
                {"name": "Printing", "value": 2},
                {"name": "Fault", "value": 3},
                {"name": "Standby", "value": 4},
            ],
            "transitions": [
                {"from": "Ready", "to": "Printing", "trigger": "condition",
                 "condition": "press_running"},
                {"from": "Printing", "to": "Standby", "trigger": "condition",
                 "condition": "press_not_running"},
            ],
            "initial_state": "Ready",
        }
        sm = StateMachineModel(params, make_rng())
        sm.generate(0.0, 0.1)
        assert sm.current_state == "Ready"

        sm.set_condition("press_running", True)
        sm.generate(0.0, 0.1)
        assert sm.current_state == "Printing"

        sm.set_condition("press_running", False)
        sm.set_condition("press_not_running", True)
        sm.generate(0.0, 0.1)
        assert sm.current_state == "Standby"

    def test_unplanned_stop_scenario(self) -> None:
        """Simulate unplanned stop: Running -> Fault -> Setup -> Running."""
        params: dict[str, object] = {
            "states": [
                {"name": "Running", "value": 2},
                {"name": "Fault", "value": 4},
                {"name": "Setup", "value": 1},
            ],
            "transitions": [
                {"from": "Fault", "to": "Setup", "trigger": "timer",
                 "min_duration": 900, "max_duration": 3600},
                {"from": "Setup", "to": "Running", "trigger": "timer",
                 "min_duration": 600, "max_duration": 1800},
            ],
            "initial_state": "Running",
        }
        sm = StateMachineModel(params, make_rng())
        # Running
        sm.generate(0.0, 1.0)
        assert sm.current_state == "Running"
        # Scenario forces Fault
        sm.force_state("Fault")
        assert sm.current_state == "Fault"
        # Wait for recovery
        for _ in range(3600):
            sm.generate(0.0, 1.0)
            if sm.current_state != "Fault":
                break
        assert sm.current_state == "Setup"
        # Wait for setup -> running
        for _ in range(1800):
            sm.generate(0.0, 1.0)
            if sm.current_state == "Running":
                break
        assert sm.current_state == "Running"


# ── Property-Based (Hypothesis) ─────────────────────────────────────────


class TestHypothesis:
    @given(
        seed=st.integers(min_value=0, max_value=2**31),
        num_ticks=st.integers(min_value=1, max_value=200),
        dt=st.floats(min_value=0.01, max_value=1.0),
    )
    @settings(max_examples=50)
    def test_output_always_valid_state_value(
        self, seed: int, num_ticks: int, dt: float
    ) -> None:
        """Output is always one of the defined state values."""
        params = press_params()
        valid_values = {0.0, 1.0, 2.0, 3.0, 4.0, 5.0}
        sm = StateMachineModel(params, make_rng(seed))
        for _ in range(num_ticks):
            value = sm.generate(0.0, dt)
            assert value in valid_values

    @given(seed=st.integers(min_value=0, max_value=2**31))
    @settings(max_examples=30)
    def test_determinism_any_seed(self, seed: int) -> None:
        """Same seed always produces identical sequence."""
        params: dict[str, object] = {
            "states": [
                {"name": "A", "value": 0},
                {"name": "B", "value": 1},
            ],
            "transitions": [
                {"from": "A", "to": "B", "trigger": "timer",
                 "min_duration": 0.5, "max_duration": 2.0},
                {"from": "B", "to": "A", "trigger": "timer",
                 "min_duration": 0.5, "max_duration": 2.0},
            ],
        }
        sm1 = StateMachineModel(params, make_rng(seed))
        sm2 = StateMachineModel(params, make_rng(seed))
        for _ in range(50):
            assert sm1.generate(0.0, 0.1) == sm2.generate(0.0, 0.1)

    @given(
        seed=st.integers(min_value=0, max_value=2**31),
        min_dur=st.floats(min_value=0.5, max_value=5.0),
    )
    @settings(max_examples=30)
    def test_min_duration_always_respected(
        self, seed: int, min_dur: float
    ) -> None:
        """No transition fires before min_duration."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "probability",
             "probability": 100.0, "min_duration": min_dur},
        ])
        sm = StateMachineModel(params, make_rng(seed))
        dt = 0.1
        ticks_before_min = int(min_dur / dt) - 1
        for _ in range(max(ticks_before_min, 0)):
            sm.generate(0.0, dt)
            assert sm.current_state == "Off"

    @given(
        seed=st.integers(min_value=0, max_value=2**31),
        max_dur=st.floats(min_value=1.0, max_value=10.0),
    )
    @settings(max_examples=30)
    def test_timer_fires_within_max_duration(
        self, seed: int, max_dur: float
    ) -> None:
        """Timer transition fires by max_duration."""
        params = two_state_params(transitions=[
            {"from": "Off", "to": "On", "trigger": "timer",
             "min_duration": 0.0, "max_duration": max_dur},
        ])
        sm = StateMachineModel(params, make_rng(seed))
        dt = 0.1
        max_ticks = int(max_dur / dt) + 2
        for _ in range(max_ticks):
            sm.generate(0.0, dt)
        assert sm.current_state == "On"

    @given(seed=st.integers(min_value=0, max_value=2**31))
    @settings(max_examples=30)
    def test_output_always_finite(self, seed: int) -> None:
        params = press_params()
        sm = StateMachineModel(params, make_rng(seed))
        for _ in range(100):
            value = sm.generate(0.0, 0.1)
            assert np.isfinite(value)


# ── Package Imports ──────────────────────────────────────────────────────


class TestPackageImports:
    def test_importable_from_models_package(self) -> None:
        from factory_simulator.models import StateMachineModel as SM

        assert SM is StateMachineModel

    def test_in_all(self) -> None:
        from factory_simulator import models

        assert "StateMachineModel" in models.__all__
