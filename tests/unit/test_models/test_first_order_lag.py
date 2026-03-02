"""Tests for the FirstOrderLagModel.

PRD Reference: Section 4.2.3 (First-Order Lag / Setpoint Tracking)
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factory_simulator.models.first_order_lag import FirstOrderLagModel
from factory_simulator.models.noise import NoiseGenerator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEED = 42
DT = 0.1  # 100 ms tick


def _make_rng(seed: int = SEED) -> np.random.Generator:
    return np.random.default_rng(seed)


def _make_noise(
    sigma: float = 1.0,
    distribution: str = "gaussian",
    seed: int = SEED,
    **kwargs: object,
) -> NoiseGenerator:
    return NoiseGenerator(
        sigma=sigma, distribution=distribution, rng=_make_rng(seed), **kwargs  # type: ignore[arg-type]
    )


def _make_model(
    params: dict[str, object] | None = None,
    seed: int = SEED,
    noise: NoiseGenerator | None = None,
) -> FirstOrderLagModel:
    p = params if params is not None else {"setpoint": 100.0, "tau": 10.0}
    return FirstOrderLagModel(p, _make_rng(seed), noise=noise)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_defaults(self) -> None:
        model = FirstOrderLagModel({}, _make_rng())
        assert model.setpoint == 0.0
        assert model.tau == 60.0
        assert model.damping_ratio == 1.0
        assert model.value == 0.0  # initial_value defaults to setpoint

    def test_explicit_params(self) -> None:
        model = _make_model({
            "setpoint": 150.0, "tau": 30.0, "damping_ratio": 0.6,
        })
        assert model.setpoint == 150.0
        assert model.tau == 30.0
        assert model.damping_ratio == 0.6

    def test_initial_value_defaults_to_setpoint(self) -> None:
        model = _make_model({"setpoint": 200.0, "tau": 10.0})
        assert model.value == 200.0

    def test_explicit_initial_value(self) -> None:
        model = _make_model({
            "setpoint": 200.0, "tau": 10.0, "initial_value": 20.0,
        })
        assert model.value == 20.0

    def test_invalid_tau_zero(self) -> None:
        with pytest.raises(ValueError, match="tau must be > 0"):
            FirstOrderLagModel({"tau": 0.0}, _make_rng())

    def test_invalid_tau_negative(self) -> None:
        with pytest.raises(ValueError, match="tau must be > 0"):
            FirstOrderLagModel({"tau": -5.0}, _make_rng())

    def test_invalid_damping_ratio_too_low(self) -> None:
        with pytest.raises(ValueError, match="damping_ratio must be in"):
            FirstOrderLagModel({"damping_ratio": 0.05}, _make_rng())

    def test_invalid_damping_ratio_too_high(self) -> None:
        with pytest.raises(ValueError, match="damping_ratio must be in"):
            FirstOrderLagModel({"damping_ratio": 3.0}, _make_rng())

    def test_damping_ratio_at_boundaries(self) -> None:
        """0.1 and 2.0 are valid boundary values."""
        m1 = FirstOrderLagModel({"damping_ratio": 0.1}, _make_rng())
        assert m1.damping_ratio == 0.1
        m2 = FirstOrderLagModel({"damping_ratio": 2.0}, _make_rng())
        assert m2.damping_ratio == 2.0


# ---------------------------------------------------------------------------
# First-order lag (damping >= 1.0, critically/overdamped)
# ---------------------------------------------------------------------------


class TestFirstOrderLag:
    def test_at_setpoint_stays_at_setpoint(self) -> None:
        """When value == setpoint, no change (no noise)."""
        model = _make_model({"setpoint": 100.0, "tau": 10.0})
        for i in range(100):
            value = model.generate(i * DT, DT)
            assert value == pytest.approx(100.0)

    def test_converges_to_setpoint(self) -> None:
        """Value moves toward setpoint over time."""
        model = _make_model({
            "setpoint": 100.0, "tau": 10.0, "initial_value": 20.0,
        })
        values = [model.generate(i * DT, DT) for i in range(2000)]
        # Should approach 100.0
        assert values[-1] == pytest.approx(100.0, abs=0.01)
        # First value should be closer to 20 than 100
        assert values[0] < 50.0

    def test_convergence_monotonic_from_below(self) -> None:
        """First-order lag approaches setpoint monotonically (no overshoot)."""
        model = _make_model({
            "setpoint": 100.0, "tau": 10.0, "initial_value": 0.0,
        })
        values = [model.generate(i * DT, DT) for i in range(2000)]
        # All values <= setpoint (approaching from below)
        for v in values:
            assert v <= 100.0 + 1e-10
        # Non-decreasing (monotonic approach)
        for i in range(1, len(values)):
            assert values[i] >= values[i - 1] - 1e-10

    def test_convergence_monotonic_from_above(self) -> None:
        """Monotonic approach from above setpoint."""
        model = _make_model({
            "setpoint": 50.0, "tau": 10.0, "initial_value": 200.0,
        })
        values = [model.generate(i * DT, DT) for i in range(2000)]
        # All values >= setpoint
        for v in values:
            assert v >= 50.0 - 1e-10
        # Non-increasing
        for i in range(1, len(values)):
            assert values[i] <= values[i - 1] + 1e-10
        assert values[-1] == pytest.approx(50.0, abs=0.01)

    def test_time_constant_one_tau(self) -> None:
        """After 1 tau, ~63.2% of the step is covered."""
        tau = 10.0
        model = _make_model({
            "setpoint": 100.0, "tau": tau, "initial_value": 0.0,
        })
        ticks_per_tau = int(tau / DT)
        for i in range(ticks_per_tau):
            model.generate(i * DT, DT)
        # 100 * (1 - exp(-1)) ≈ 63.2
        assert model.value == pytest.approx(63.21, abs=1.0)

    def test_time_constant_five_tau(self) -> None:
        """After 5 tau, ~99.3% settled."""
        tau = 10.0
        model = _make_model({
            "setpoint": 100.0, "tau": tau, "initial_value": 0.0,
        })
        ticks = int(5 * tau / DT)
        for i in range(ticks):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(100.0, abs=1.0)

    def test_smaller_tau_faster_convergence(self) -> None:
        """Smaller tau converges faster."""
        fast = FirstOrderLagModel(
            {"setpoint": 100.0, "tau": 5.0, "initial_value": 0.0},
            _make_rng(),
        )
        slow = FirstOrderLagModel(
            {"setpoint": 100.0, "tau": 50.0, "initial_value": 0.0},
            _make_rng(),
        )
        for i in range(100):
            fast.generate(i * DT, DT)
            slow.generate(i * DT, DT)
        assert abs(100.0 - fast.value) < abs(100.0 - slow.value)

    def test_overdamped_no_overshoot(self) -> None:
        """damping_ratio > 1.0 still uses first-order lag, no overshoot."""
        model = _make_model({
            "setpoint": 100.0, "tau": 10.0, "initial_value": 0.0,
            "damping_ratio": 1.5,
        })
        values = [model.generate(i * DT, DT) for i in range(2000)]
        for v in values:
            assert v <= 100.0 + 1e-10

    def test_negative_setpoint(self) -> None:
        """Tracks negative setpoints correctly."""
        model = _make_model({
            "setpoint": -50.0, "tau": 10.0, "initial_value": 0.0,
        })
        for i in range(2000):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(-50.0, abs=0.01)


# ---------------------------------------------------------------------------
# Setpoint changes
# ---------------------------------------------------------------------------


class TestSetpointChanges:
    def test_setpoint_change_first_order(self) -> None:
        """After setpoint change, value tracks new setpoint."""
        model = _make_model({"setpoint": 100.0, "tau": 5.0})
        assert model.value == pytest.approx(100.0)

        model.set_setpoint(200.0)
        assert model.setpoint == 200.0

        for i in range(500):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(200.0, abs=0.5)

    def test_multiple_setpoint_changes(self) -> None:
        """Model tracks through multiple setpoint changes."""
        model = _make_model({"setpoint": 100.0, "tau": 5.0})

        model.set_setpoint(200.0)
        for i in range(500):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(200.0, abs=0.5)

        model.set_setpoint(50.0)
        for i in range(500, 1000):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(50.0, abs=0.5)

    def test_no_change_same_setpoint(self) -> None:
        """Setting same setpoint is a no-op."""
        model = _make_model({"setpoint": 100.0, "tau": 10.0})
        model.set_setpoint(100.0)
        assert model.setpoint == 100.0
        assert model.value == 100.0

    def test_setpoint_step_down(self) -> None:
        """Step from high to low setpoint."""
        model = _make_model({"setpoint": 200.0, "tau": 5.0})
        model.set_setpoint(50.0)
        for i in range(500):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(50.0, abs=0.5)


# ---------------------------------------------------------------------------
# Underdamped response (damping_ratio < 1.0)
# ---------------------------------------------------------------------------


class TestUnderdampedResponse:
    def test_overshoot_from_below(self) -> None:
        """Underdamped response overshoots the setpoint."""
        model = _make_model({
            "setpoint": 100.0, "tau": 10.0, "initial_value": 0.0,
            "damping_ratio": 0.5,
        })
        values = [model.generate(i * DT, DT) for i in range(5000)]
        assert max(values) > 100.0

    def test_undershoot_from_above(self) -> None:
        """Approaching from above: undershoots below setpoint."""
        model = _make_model({
            "setpoint": 50.0, "tau": 10.0, "initial_value": 150.0,
            "damping_ratio": 0.5,
        })
        values = [model.generate(i * DT, DT) for i in range(5000)]
        assert min(values) < 50.0

    def test_eventually_settles(self) -> None:
        """Underdamped response settles to setpoint."""
        model = _make_model({
            "setpoint": 100.0, "tau": 10.0, "initial_value": 0.0,
            "damping_ratio": 0.5,
        })
        for i in range(10000):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(100.0, abs=0.1)

    def test_higher_damping_less_overshoot(self) -> None:
        """Higher damping ratio produces less overshoot."""
        def max_overshoot(damping: float) -> float:
            model = FirstOrderLagModel(
                {"setpoint": 100.0, "tau": 10.0, "initial_value": 0.0,
                 "damping_ratio": damping},
                _make_rng(),
            )
            values = [model.generate(i * DT, DT) for i in range(5000)]
            return max(values) - 100.0

        overshoot_05 = max_overshoot(0.5)
        overshoot_07 = max_overshoot(0.7)
        overshoot_09 = max_overshoot(0.9)

        assert overshoot_05 > overshoot_07
        assert overshoot_07 > overshoot_09
        assert overshoot_09 > 0  # still overshoots

    def test_overshoot_magnitude_zeta_05(self) -> None:
        """For zeta=0.5, theoretical overshoot is ~16.3% of step size."""
        import math
        model = _make_model({
            "setpoint": 100.0, "tau": 10.0, "initial_value": 0.0,
            "damping_ratio": 0.5,
        })
        values = [model.generate(i * DT, DT) for i in range(5000)]
        overshoot_pct = (max(values) - 100.0) / 100.0 * 100
        # Theoretical: exp(-pi * 0.5 / sqrt(1-0.25)) * 100 ≈ 16.3%
        theoretical = math.exp(-math.pi * 0.5 / math.sqrt(0.75)) * 100
        assert overshoot_pct == pytest.approx(theoretical, abs=2.0)

    def test_setpoint_change_during_transient(self) -> None:
        """Setpoint change mid-transient: old transient abandoned, new starts."""
        model = _make_model({
            "setpoint": 100.0, "tau": 10.0, "initial_value": 0.0,
            "damping_ratio": 0.5,
        })
        # Run mid-transient
        for i in range(50):
            model.generate(i * DT, DT)
        mid_value = model.value
        assert mid_value > 0.0

        # Change setpoint mid-transient
        model.set_setpoint(200.0)

        # Should eventually settle at new setpoint
        for i in range(50, 10050):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(200.0, abs=0.1)

    def test_underdamped_then_stable_after_settle(self) -> None:
        """After transient settles, value stays at setpoint."""
        model = _make_model({
            "setpoint": 100.0, "tau": 10.0, "initial_value": 0.0,
            "damping_ratio": 0.7,
        })
        for i in range(10000):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(100.0, abs=0.01)

        # Stays at setpoint via first-order lag branch
        for i in range(10000, 10100):
            v = model.generate(i * DT, DT)
            assert v == pytest.approx(100.0, abs=0.01)

    def test_initial_value_at_setpoint_no_transient(self) -> None:
        """If initial_value == setpoint, no transient even for underdamped."""
        model = _make_model({
            "setpoint": 100.0, "tau": 10.0,
            "damping_ratio": 0.5,
        })
        # value defaults to setpoint = 100
        values = [model.generate(i * DT, DT) for i in range(100)]
        for v in values:
            assert v == pytest.approx(100.0, abs=1e-10)

    def test_new_setpoint_change_triggers_transient_after_settle(self) -> None:
        """After underdamped transient settles, new setpoint starts new transient."""
        model = _make_model({
            "setpoint": 100.0, "tau": 10.0, "initial_value": 0.0,
            "damping_ratio": 0.6,
        })
        # Settle first transient
        for i in range(10000):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(100.0, abs=0.01)

        # New setpoint should trigger overshoot
        model.set_setpoint(200.0)
        values = [model.generate(i * DT, DT) for i in range(10000, 15000)]
        assert max(values) > 200.0  # overshoot


# ---------------------------------------------------------------------------
# Noise
# ---------------------------------------------------------------------------


class TestNoiseGeneration:
    def test_mean_near_setpoint_after_settling(self) -> None:
        """With noise, mean converges to setpoint after settling."""
        noise = _make_noise(sigma=2.0)
        model = _make_model(
            {"setpoint": 100.0, "tau": 5.0, "initial_value": 0.0},
            noise=noise,
        )
        # Let it settle (10 tau)
        settle_ticks = int(10 * 5.0 / DT)
        for i in range(settle_ticks):
            model.generate(i * DT, DT)

        # Collect steady-state values
        values = []
        for i in range(settle_ticks, settle_ticks + 10000):
            values.append(model.generate(i * DT, DT))
        mean = np.mean(values)
        assert mean == pytest.approx(100.0, abs=1.0)

    def test_noise_adds_variation(self) -> None:
        """Noise causes variation around the setpoint."""
        noise = _make_noise(sigma=5.0)
        model = _make_model(
            {"setpoint": 100.0, "tau": 5.0},
            noise=noise,
        )
        values = [model.generate(i * DT, DT) for i in range(1000)]
        assert np.std(values) > 1.0

    def test_zero_sigma_clean_signal(self) -> None:
        """Zero sigma noise produces clean lag response."""
        noise = _make_noise(sigma=0.0)
        model_noisy = _make_model(
            {"setpoint": 100.0, "tau": 10.0, "initial_value": 0.0},
            noise=noise,
        )
        model_clean = _make_model(
            {"setpoint": 100.0, "tau": 10.0, "initial_value": 0.0},
        )
        for i in range(500):
            v_noisy = model_noisy.generate(i * DT, DT)
            v_clean = model_clean.generate(i * DT, DT)
            assert v_noisy == pytest.approx(v_clean, abs=1e-10)


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_restores_initial_value(self) -> None:
        model = _make_model({
            "setpoint": 100.0, "tau": 10.0, "initial_value": 20.0,
        })
        for i in range(500):
            model.generate(i * DT, DT)
        assert model.value != pytest.approx(20.0)

        model.reset()
        assert model.value == pytest.approx(20.0)

    def test_reset_defaults_to_current_setpoint(self) -> None:
        """Without initial_value, reset uses current setpoint."""
        model = _make_model({"setpoint": 100.0, "tau": 10.0})
        model.set_setpoint(200.0)
        for i in range(500):
            model.generate(i * DT, DT)
        model.reset()
        # No initial_value in params, so uses current setpoint
        assert model.value == pytest.approx(200.0)

    def test_reset_restarts_underdamped_transient(self) -> None:
        """Reset with initial_value != setpoint restarts transient."""
        model = _make_model({
            "setpoint": 100.0, "tau": 10.0, "initial_value": 0.0,
            "damping_ratio": 0.5,
        })
        # Run past transient
        for i in range(10000):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(100.0, abs=0.1)

        model.reset()
        assert model.value == 0.0
        # Should overshoot again after reset
        values = [model.generate(i * DT, DT) for i in range(5000)]
        assert max(values) > 100.0

    def test_reset_at_setpoint_no_transient(self) -> None:
        """Reset when initial_value matches setpoint: no transient."""
        model = _make_model({
            "setpoint": 100.0, "tau": 10.0, "damping_ratio": 0.5,
        })
        model.set_setpoint(200.0)
        for i in range(20):
            model.generate(i * DT, DT)
        model.reset()
        # No initial_value, so value=200 (current setpoint)=setpoint
        assert not model._in_transient

    def test_reset_clears_ar1_noise(self) -> None:
        noise = _make_noise(sigma=1.0, distribution="ar1", phi=0.9)
        model = _make_model(
            {"setpoint": 100.0, "tau": 10.0},
            noise=noise,
        )
        for i in range(100):
            model.generate(i * DT, DT)
        model.reset()
        assert noise._ar1_prev == 0.0


# ---------------------------------------------------------------------------
# Determinism (Rule 13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_same_output(self) -> None:
        """Two models with same seed produce identical sequences."""
        params: dict[str, object] = {
            "setpoint": 100.0, "tau": 10.0, "initial_value": 20.0,
        }
        noise1 = _make_noise(sigma=2.0, seed=99)
        model1 = FirstOrderLagModel(params, _make_rng(99), noise=noise1)

        noise2 = _make_noise(sigma=2.0, seed=99)
        model2 = FirstOrderLagModel(params, _make_rng(99), noise=noise2)

        for i in range(500):
            v1 = model1.generate(i * DT, DT)
            v2 = model2.generate(i * DT, DT)
            assert v1 == v2

    def test_same_seed_underdamped(self) -> None:
        """Deterministic for underdamped models too."""
        params: dict[str, object] = {
            "setpoint": 100.0, "tau": 10.0, "initial_value": 0.0,
            "damping_ratio": 0.6,
        }
        noise1 = _make_noise(sigma=1.0, seed=77)
        model1 = FirstOrderLagModel(params, _make_rng(77), noise=noise1)

        noise2 = _make_noise(sigma=1.0, seed=77)
        model2 = FirstOrderLagModel(params, _make_rng(77), noise=noise2)

        for i in range(500):
            v1 = model1.generate(i * DT, DT)
            v2 = model2.generate(i * DT, DT)
            assert v1 == v2

    def test_different_seeds_differ(self) -> None:
        """Different seeds produce different output sequences."""
        params: dict[str, object] = {
            "setpoint": 100.0, "tau": 10.0, "initial_value": 20.0,
        }
        noise1 = _make_noise(sigma=2.0, seed=1)
        model1 = FirstOrderLagModel(params, _make_rng(1), noise=noise1)

        noise2 = _make_noise(sigma=2.0, seed=2)
        model2 = FirstOrderLagModel(params, _make_rng(2), noise=noise2)

        values1 = [model1.generate(i * DT, DT) for i in range(100)]
        values2 = [model2.generate(i * DT, DT) for i in range(100)]
        assert values1 != values2

    def test_no_noise_deterministic_regardless_of_seed(self) -> None:
        """Without noise, output is deterministic for any seed."""
        params: dict[str, object] = {
            "setpoint": 100.0, "tau": 10.0, "initial_value": 0.0,
        }
        model1 = FirstOrderLagModel(params, _make_rng(1))
        model2 = FirstOrderLagModel(params, _make_rng(999))

        for i in range(200):
            assert model1.generate(i * DT, DT) == model2.generate(i * DT, DT)


# ---------------------------------------------------------------------------
# Hypothesis property-based tests
# ---------------------------------------------------------------------------


class TestPropertyBased:
    @given(
        setpoint=st.floats(min_value=-1000, max_value=1000, allow_nan=False),
        tau=st.floats(min_value=0.1, max_value=1000, allow_nan=False),
        initial_value=st.floats(min_value=-1000, max_value=1000, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_output_finite(
        self, setpoint: float, tau: float, initial_value: float
    ) -> None:
        """Output is always finite for valid inputs."""
        model = FirstOrderLagModel(
            {"setpoint": setpoint, "tau": tau, "initial_value": initial_value},
            _make_rng(),
        )
        for i in range(10):
            value = model.generate(i * DT, DT)
            assert np.isfinite(value)

    @given(
        setpoint=st.floats(min_value=-100, max_value=100, allow_nan=False),
        tau=st.floats(min_value=0.1, max_value=100, allow_nan=False),
    )
    @settings(max_examples=50)
    def test_converges_to_setpoint(
        self, setpoint: float, tau: float
    ) -> None:
        """First-order lag converges to setpoint after many time constants."""
        model = FirstOrderLagModel(
            {"setpoint": setpoint, "tau": tau, "initial_value": 0.0},
            _make_rng(),
        )
        n_ticks = max(int(20 * tau / DT), 100)
        for i in range(n_ticks):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(setpoint, abs=0.1)

    @given(seed=st.integers(min_value=0, max_value=2**31))
    @settings(max_examples=20)
    def test_determinism_any_seed(self, seed: int) -> None:
        """Any seed produces deterministic output."""
        params: dict[str, object] = {
            "setpoint": 100.0, "tau": 10.0, "initial_value": 20.0,
        }
        n1 = NoiseGenerator(sigma=1.0, distribution="gaussian", rng=_make_rng(seed))
        m1 = FirstOrderLagModel(params, _make_rng(seed), noise=n1)

        n2 = NoiseGenerator(sigma=1.0, distribution="gaussian", rng=_make_rng(seed))
        m2 = FirstOrderLagModel(params, _make_rng(seed), noise=n2)

        for i in range(20):
            assert m1.generate(i * DT, DT) == m2.generate(i * DT, DT)

    @given(
        damping=st.floats(min_value=0.1, max_value=0.95, allow_nan=False),
    )
    @settings(max_examples=30)
    def test_underdamped_overshoots(self, damping: float) -> None:
        """All underdamped models overshoot the setpoint."""
        model = FirstOrderLagModel(
            {"setpoint": 100.0, "tau": 10.0, "initial_value": 0.0,
             "damping_ratio": damping},
            _make_rng(),
        )
        values = [model.generate(i * DT, DT) for i in range(5000)]
        assert max(values) > 100.0

    @given(
        tau=st.floats(min_value=0.1, max_value=100, allow_nan=False),
        damping=st.floats(min_value=1.0, max_value=2.0, allow_nan=False),
    )
    @settings(max_examples=30)
    def test_critically_overdamped_no_overshoot(
        self, tau: float, damping: float
    ) -> None:
        """Critically/overdamped models never overshoot (from below)."""
        model = FirstOrderLagModel(
            {"setpoint": 100.0, "tau": tau, "initial_value": 0.0,
             "damping_ratio": damping},
            _make_rng(),
        )
        values = [model.generate(i * DT, DT) for i in range(2000)]
        assert max(values) <= 100.0 + 1e-10


# ---------------------------------------------------------------------------
# PRD examples
# ---------------------------------------------------------------------------


class TestPRDExamples:
    def test_dryer_temp_zone(self) -> None:
        """PRD 4.2.3: press.dryer_temp_zone with damping_ratio ~0.6.

        Eurotherm PID tracking 180C setpoint from ambient (~20C).
        tau = 60s for industrial dryer thermal mass.
        """
        noise = _make_noise(sigma=2.8)  # PRD: print head temp sigma 2.8C
        model = FirstOrderLagModel(
            {
                "setpoint": 180.0,
                "tau": 60.0,
                "initial_value": 20.0,
                "damping_ratio": 0.6,
            },
            _make_rng(),
            noise=noise,
        )
        # Run for 10 minutes (600s)
        dt = 0.5  # 500ms ticks
        values = []
        for i in range(1200):
            values.append(model.generate(i * dt, dt))

        arr = np.array(values)
        # Should overshoot setpoint (underdamped)
        assert np.max(arr) > 180.0
        # Should settle near setpoint
        last_100 = arr[-100:]
        assert np.mean(last_100) == pytest.approx(180.0, abs=5.0)

    def test_laminator_nip_temp(self) -> None:
        """PRD 4.2.3: laminator.nip_temp with damping_ratio ~0.7.

        Less oscillatory than dryer zones.
        """
        model = FirstOrderLagModel(
            {
                "setpoint": 120.0,
                "tau": 45.0,
                "initial_value": 25.0,
                "damping_ratio": 0.7,
            },
            _make_rng(),
        )
        dt = 0.5
        values = [model.generate(i * dt, dt) for i in range(1200)]

        # Should overshoot (damping 0.7 -> ~4.6% overshoot)
        overshoot = max(values) - 120.0
        step = 120.0 - 25.0
        overshoot_pct = overshoot / step * 100
        assert overshoot_pct > 0.0
        assert overshoot_pct < 15.0  # much less than zeta=0.5

        # Should settle (600s = ~13 tau is plenty for zeta=0.7)
        assert values[-1] == pytest.approx(120.0, abs=0.5)


# ---------------------------------------------------------------------------
# Package imports
# ---------------------------------------------------------------------------


class TestPackageImports:
    def test_import_from_models_package(self) -> None:
        from factory_simulator.models import FirstOrderLagModel as FOL
        assert FOL is FirstOrderLagModel
