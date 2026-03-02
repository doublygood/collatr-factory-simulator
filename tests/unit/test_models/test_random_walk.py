"""Tests for the RandomWalkModel.

PRD Reference: Section 4.2.5 (Random Walk with Mean Reversion)
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factory_simulator.models.noise import NoiseGenerator
from factory_simulator.models.random_walk import RandomWalkModel

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
) -> RandomWalkModel:
    p = params if params is not None else {"center": 0.0, "drift_rate": 1.0}
    return RandomWalkModel(p, _make_rng(seed), noise=noise)


def _run_ticks(model: RandomWalkModel, n: int, dt: float = DT) -> list[float]:
    """Run n ticks and return the values."""
    t = 0.0
    values = []
    for _ in range(n):
        v = model.generate(t, dt)
        values.append(v)
        t += dt
    return values


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_defaults(self) -> None:
        model = RandomWalkModel({}, _make_rng())
        assert model.center == 0.0
        assert model.drift_rate == 1.0
        assert model.reversion_rate == 0.1
        assert model.value == 0.0

    def test_explicit_params(self) -> None:
        model = RandomWalkModel(
            {"center": 5.0, "drift_rate": 2.0, "reversion_rate": 0.5},
            _make_rng(),
        )
        assert model.center == 5.0
        assert model.drift_rate == 2.0
        assert model.reversion_rate == 0.5
        assert model.value == 5.0  # defaults to center

    def test_initial_value_defaults_to_center(self) -> None:
        model = RandomWalkModel({"center": 10.0}, _make_rng())
        assert model.value == 10.0

    def test_initial_value_explicit(self) -> None:
        model = RandomWalkModel(
            {"center": 10.0, "initial_value": 7.0}, _make_rng()
        )
        assert model.value == 7.0

    def test_clamp_bounds(self) -> None:
        model = RandomWalkModel(
            {"center": 5.0, "min_clamp": 0.0, "max_clamp": 10.0},
            _make_rng(),
        )
        # No error -- bounds accepted
        assert model.center == 5.0

    def test_negative_drift_rate_rejected(self) -> None:
        with pytest.raises(ValueError, match="drift_rate must be >= 0"):
            RandomWalkModel({"drift_rate": -1.0}, _make_rng())

    def test_negative_reversion_rate_rejected(self) -> None:
        with pytest.raises(ValueError, match="reversion_rate must be >= 0"):
            RandomWalkModel({"reversion_rate": -0.5}, _make_rng())

    def test_zero_drift_rate_allowed(self) -> None:
        model = RandomWalkModel({"drift_rate": 0.0}, _make_rng())
        assert model.drift_rate == 0.0

    def test_zero_reversion_rate_allowed(self) -> None:
        model = RandomWalkModel({"reversion_rate": 0.0}, _make_rng())
        assert model.reversion_rate == 0.0


# ---------------------------------------------------------------------------
# Basic generation
# ---------------------------------------------------------------------------


class TestBasicGeneration:
    def test_no_drift_stays_at_center(self) -> None:
        """With drift_rate=0 and starting at center, value stays at center."""
        model = RandomWalkModel(
            {"center": 5.0, "drift_rate": 0.0, "reversion_rate": 0.1},
            _make_rng(),
        )
        values = _run_ticks(model, 100)
        for v in values:
            assert v == pytest.approx(5.0)

    def test_value_changes_with_nonzero_drift(self) -> None:
        """With nonzero drift_rate, values should vary."""
        model = _make_model({"center": 0.0, "drift_rate": 5.0})
        values = _run_ticks(model, 100)
        # Not all the same
        assert len(set(values)) > 1

    def test_mean_near_center_over_long_run(self) -> None:
        """Over many ticks, the mean value should be close to center."""
        center = 10.0
        model = RandomWalkModel(
            {"center": center, "drift_rate": 1.0, "reversion_rate": 0.5},
            _make_rng(),
        )
        values = _run_ticks(model, 50_000, dt=0.1)
        mean_val = np.mean(values)
        # Should be within a reasonable range of center
        assert abs(mean_val - center) < 2.0

    def test_negative_center(self) -> None:
        """Model works correctly with negative center."""
        model = RandomWalkModel(
            {"center": -5.0, "drift_rate": 1.0, "reversion_rate": 0.5},
            _make_rng(),
        )
        values = _run_ticks(model, 10_000)
        mean_val = np.mean(values)
        assert abs(mean_val - (-5.0)) < 2.0

    def test_zero_center(self) -> None:
        """Model works with zero center."""
        model = RandomWalkModel(
            {"center": 0.0, "drift_rate": 1.0, "reversion_rate": 0.5},
            _make_rng(),
        )
        values = _run_ticks(model, 10_000)
        mean_val = np.mean(values)
        assert abs(mean_val) < 2.0


# ---------------------------------------------------------------------------
# Mean reversion behaviour
# ---------------------------------------------------------------------------


class TestMeanReversion:
    def test_strong_reversion_stays_close_to_center(self) -> None:
        """With strong reversion rate, variance is lower."""
        strong = RandomWalkModel(
            {"center": 0.0, "drift_rate": 1.0, "reversion_rate": 5.0},
            _make_rng(1),
        )
        weak = RandomWalkModel(
            {"center": 0.0, "drift_rate": 1.0, "reversion_rate": 0.01},
            _make_rng(1),
        )
        strong_vals = _run_ticks(strong, 5_000)
        weak_vals = _run_ticks(weak, 5_000)
        strong_std = np.std(strong_vals)
        weak_std = np.std(weak_vals)
        assert strong_std < weak_std

    def test_reversion_pulls_back_from_displacement(self) -> None:
        """Starting far from center, the signal should revert toward it."""
        model = RandomWalkModel(
            {
                "center": 0.0,
                "drift_rate": 0.0,
                "reversion_rate": 1.0,
                "initial_value": 10.0,
            },
            _make_rng(),
        )
        # With drift_rate=0, the only force is reversion
        values = _run_ticks(model, 100, dt=0.1)
        # Value should decrease toward 0
        assert values[-1] < 10.0
        # Should approach center with exponential decay
        # After enough time, should be much closer to 0
        assert abs(values[-1]) < abs(10.0)

    def test_pure_reversion_exponential_decay(self) -> None:
        """Without drift, reversion from initial_value decays exponentially.

        value(t) = center + (initial - center) * exp(-reversion_rate * t)
        """
        center = 0.0
        initial = 10.0
        reversion_rate = 2.0
        model = RandomWalkModel(
            {
                "center": center,
                "drift_rate": 0.0,
                "reversion_rate": reversion_rate,
                "initial_value": initial,
            },
            _make_rng(),
        )
        dt = 0.001  # small dt for accurate Euler approximation
        t = 0.0
        for _ in range(1000):
            model.generate(t, dt)
            t += dt
        # After 1.0 seconds with reversion_rate=2.0:
        # expected ~= 10 * exp(-2 * 1) = 10 * 0.1353 = 1.353
        expected = initial * np.exp(-reversion_rate * t)
        assert model.value == pytest.approx(expected, rel=0.05)

    def test_zero_reversion_pure_random_walk(self) -> None:
        """Without reversion, the signal is a pure random walk (no pull-back)."""
        model = RandomWalkModel(
            {"center": 0.0, "drift_rate": 1.0, "reversion_rate": 0.0},
            _make_rng(),
        )
        values = _run_ticks(model, 5_000)
        # A pure random walk should exhibit growing variance over time
        # With no reversion, the walk is unbounded -- check it wanders
        max_displacement = max(abs(v) for v in values)
        assert max_displacement > 0.5  # Should wander away from 0


# ---------------------------------------------------------------------------
# Clamping
# ---------------------------------------------------------------------------


class TestClamping:
    def test_min_clamp(self) -> None:
        """Value cannot go below min_clamp."""
        model = RandomWalkModel(
            {
                "center": 0.0,
                "drift_rate": 5.0,
                "reversion_rate": 0.0,
                "min_clamp": -1.0,
            },
            _make_rng(),
        )
        values = _run_ticks(model, 5_000)
        # Walk value should never go below -1.0
        assert all(model.value >= -1.0 for _ in [0])
        # Check all *walk* values stayed in bounds
        # (observation noise could push the returned value below, but the
        #  internal walk state is clamped)
        for v in values:
            assert v >= -1.0  # no observation noise, so result = walk value

    def test_max_clamp(self) -> None:
        """Value cannot go above max_clamp."""
        model = RandomWalkModel(
            {
                "center": 0.0,
                "drift_rate": 5.0,
                "reversion_rate": 0.0,
                "max_clamp": 1.0,
            },
            _make_rng(),
        )
        values = _run_ticks(model, 5_000)
        for v in values:
            assert v <= 1.0

    def test_both_clamps(self) -> None:
        """Value stays within [min_clamp, max_clamp]."""
        model = RandomWalkModel(
            {
                "center": 5.0,
                "drift_rate": 10.0,
                "reversion_rate": 0.0,
                "min_clamp": 0.0,
                "max_clamp": 10.0,
            },
            _make_rng(),
        )
        values = _run_ticks(model, 5_000)
        for v in values:
            assert 0.0 <= v <= 10.0

    def test_no_clamps_by_default(self) -> None:
        """Without clamp params, value can go anywhere."""
        model = RandomWalkModel(
            {"center": 0.0, "drift_rate": 5.0, "reversion_rate": 0.0},
            _make_rng(),
        )
        values = _run_ticks(model, 5_000)
        # Should have some values above and below 0
        has_positive = any(v > 0 for v in values)
        has_negative = any(v < 0 for v in values)
        assert has_positive and has_negative


# ---------------------------------------------------------------------------
# Noise (observation noise on top of walk)
# ---------------------------------------------------------------------------


class TestNoise:
    def test_noise_adds_variation(self) -> None:
        """Observation noise adds variation around the walk value."""
        noise = _make_noise(sigma=2.0, seed=99)
        model = RandomWalkModel(
            {"center": 0.0, "drift_rate": 0.0, "reversion_rate": 0.0},
            _make_rng(),
            noise=noise,
        )
        # drift_rate=0, reversion=0 => walk stays at 0
        # All variation comes from noise
        values = _run_ticks(model, 1_000)
        assert np.std(values) > 1.0

    def test_zero_sigma_clean_signal(self) -> None:
        """Noise with sigma=0 does not affect the output."""
        noise = _make_noise(sigma=0.0, seed=99)
        no_noise_model = _make_model(
            {"center": 5.0, "drift_rate": 1.0}, seed=42
        )
        noise_model = RandomWalkModel(
            {"center": 5.0, "drift_rate": 1.0},
            _make_rng(42),
            noise=noise,
        )
        vals_no_noise = _run_ticks(no_noise_model, 100)
        vals_with_noise = _run_ticks(noise_model, 100)
        np.testing.assert_allclose(vals_no_noise, vals_with_noise)

    def test_noise_does_not_affect_walk_state(self) -> None:
        """Observation noise changes the returned value but not the walk state."""
        noise = _make_noise(sigma=10.0, seed=99)
        model = RandomWalkModel(
            {"center": 0.0, "drift_rate": 0.0, "reversion_rate": 0.0},
            _make_rng(),
            noise=noise,
        )
        model.generate(0.0, DT)
        # Walk state should still be 0 (no drift, no reversion)
        assert model.value == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# set_center
# ---------------------------------------------------------------------------


class TestSetCenter:
    def test_set_center_changes_reversion_target(self) -> None:
        """After set_center, the walk reverts toward the new center."""
        model = RandomWalkModel(
            {
                "center": 0.0,
                "drift_rate": 0.0,
                "reversion_rate": 2.0,
                "initial_value": 0.0,
            },
            _make_rng(),
        )
        # Shift center to 10
        model.set_center(10.0)
        assert model.center == 10.0
        # With drift_rate=0, only reversion force pulls toward 10
        _run_ticks(model, 500, dt=0.01)
        # Should have moved toward 10
        assert model.value > 5.0


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_restores_initial_value(self) -> None:
        model = RandomWalkModel(
            {"center": 5.0, "drift_rate": 2.0, "initial_value": 3.0},
            _make_rng(),
        )
        _run_ticks(model, 100)
        assert model.value != pytest.approx(3.0)
        model.reset()
        assert model.value == pytest.approx(3.0)

    def test_reset_defaults_to_center(self) -> None:
        model = RandomWalkModel(
            {"center": 5.0, "drift_rate": 2.0}, _make_rng()
        )
        _run_ticks(model, 100)
        model.reset()
        assert model.value == pytest.approx(5.0)

    def test_reset_clears_noise_state(self) -> None:
        """Reset clears AR(1) memory so noise starts from zero history."""
        noise = _make_noise(sigma=1.0, distribution="ar1", phi=0.9, seed=99)
        model = RandomWalkModel(
            {"center": 0.0, "drift_rate": 0.0},
            _make_rng(),
            noise=noise,
        )
        # Run enough for AR(1) state to build up (phi=0.9 => strong memory)
        _run_ticks(model, 100)
        # Record the AR(1) internal state before reset
        ar1_before_reset = noise._ar1_prev
        assert ar1_before_reset != 0.0  # should have accumulated state
        model.reset()
        # After reset, the AR(1) prev state should be zero
        assert noise._ar1_prev == 0.0


# ---------------------------------------------------------------------------
# Determinism (Rule 13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_identical_sequences(self) -> None:
        m1 = _make_model({"center": 5.0, "drift_rate": 2.0}, seed=123)
        m2 = _make_model({"center": 5.0, "drift_rate": 2.0}, seed=123)
        v1 = _run_ticks(m1, 200)
        v2 = _run_ticks(m2, 200)
        np.testing.assert_array_equal(v1, v2)

    def test_different_seeds_differ(self) -> None:
        m1 = _make_model({"center": 5.0, "drift_rate": 2.0}, seed=1)
        m2 = _make_model({"center": 5.0, "drift_rate": 2.0}, seed=2)
        v1 = _run_ticks(m1, 200)
        v2 = _run_ticks(m2, 200)
        assert not np.array_equal(v1, v2)

    def test_no_drift_deterministic(self) -> None:
        """With drift_rate=0, output is fully deterministic and equal for any seed."""
        m1 = RandomWalkModel(
            {"center": 5.0, "drift_rate": 0.0}, _make_rng(1)
        )
        m2 = RandomWalkModel(
            {"center": 5.0, "drift_rate": 0.0}, _make_rng(999)
        )
        v1 = _run_ticks(m1, 50)
        v2 = _run_ticks(m2, 50)
        np.testing.assert_array_equal(v1, v2)

    def test_with_noise_same_seed_identical(self) -> None:
        """With observation noise, same seeds produce identical output."""
        n1 = _make_noise(sigma=1.0, seed=77)
        n2 = _make_noise(sigma=1.0, seed=77)
        m1 = RandomWalkModel(
            {"center": 0.0, "drift_rate": 1.0},
            _make_rng(42),
            noise=n1,
        )
        m2 = RandomWalkModel(
            {"center": 0.0, "drift_rate": 1.0},
            _make_rng(42),
            noise=n2,
        )
        v1 = _run_ticks(m1, 100)
        v2 = _run_ticks(m2, 100)
        np.testing.assert_array_equal(v1, v2)


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis)
# ---------------------------------------------------------------------------


class TestPropertyBased:
    @given(
        center=st.floats(min_value=-1000, max_value=1000, allow_nan=False),
        drift=st.floats(min_value=0.0, max_value=100, allow_nan=False),
        reversion=st.floats(min_value=0.0, max_value=10, allow_nan=False),
        seed=st.integers(min_value=0, max_value=2**31),
    )
    @settings(max_examples=50)
    def test_output_always_finite(
        self, center: float, drift: float, reversion: float, seed: int
    ) -> None:
        model = RandomWalkModel(
            {"center": center, "drift_rate": drift, "reversion_rate": reversion},
            _make_rng(seed),
        )
        for i in range(20):
            v = model.generate(i * DT, DT)
            assert np.isfinite(v)

    @given(
        seed=st.integers(min_value=0, max_value=2**31),
    )
    @settings(max_examples=20)
    def test_determinism_any_seed(self, seed: int) -> None:
        params: dict[str, object] = {
            "center": 5.0,
            "drift_rate": 2.0,
            "reversion_rate": 0.3,
        }
        m1 = RandomWalkModel(params, _make_rng(seed))
        m2 = RandomWalkModel(params, _make_rng(seed))
        v1 = _run_ticks(m1, 50)
        v2 = _run_ticks(m2, 50)
        np.testing.assert_array_equal(v1, v2)

    @given(
        min_c=st.floats(min_value=-100, max_value=0, allow_nan=False),
        max_c=st.floats(min_value=0, max_value=100, allow_nan=False),
        seed=st.integers(min_value=0, max_value=2**31),
    )
    @settings(max_examples=30)
    def test_clamped_output_within_bounds(
        self, min_c: float, max_c: float, seed: int
    ) -> None:
        if min_c >= max_c:
            return  # skip degenerate case
        center = (min_c + max_c) / 2
        model = RandomWalkModel(
            {
                "center": center,
                "drift_rate": 5.0,
                "reversion_rate": 0.0,
                "min_clamp": min_c,
                "max_clamp": max_c,
            },
            _make_rng(seed),
        )
        values = _run_ticks(model, 100)
        for v in values:
            assert min_c <= v <= max_c


# ---------------------------------------------------------------------------
# PRD examples
# ---------------------------------------------------------------------------


class TestPrdExamples:
    def test_ink_viscosity(self) -> None:
        """PRD: press.ink_viscosity -- random walk with mean reversion.

        Mean reversion around target viscosity, sigma 0.3 cP.
        """
        center = 25.0  # typical ink viscosity in cP
        model = RandomWalkModel(
            {
                "center": center,
                "drift_rate": 0.3,
                "reversion_rate": 0.2,
                "min_clamp": 15.0,
                "max_clamp": 35.0,
            },
            _make_rng(),
        )
        values = _run_ticks(model, 10_000)
        mean_val = np.mean(values)
        assert abs(mean_val - center) < 3.0
        # All within physical bounds
        assert all(15.0 <= v <= 35.0 for v in values)

    def test_registration_error(self) -> None:
        """PRD: press.registration_error_x/y -- random walk around 0.

        Small drift, moderate reversion, bounded by physical tolerances.
        """
        model = RandomWalkModel(
            {
                "center": 0.0,
                "drift_rate": 0.05,
                "reversion_rate": 0.3,
                "min_clamp": -0.5,
                "max_clamp": 0.5,
            },
            _make_rng(),
        )
        values = _run_ticks(model, 10_000)
        # Mean should be near 0
        assert abs(np.mean(values)) < 0.2
        # All within tolerance
        assert all(-0.5 <= v <= 0.5 for v in values)

    def test_coder_ink_viscosity(self) -> None:
        """PRD: coder.ink_viscosity_actual -- sigma 0.3 cP."""
        noise = _make_noise(sigma=0.3, seed=77)
        center = 20.0
        model = RandomWalkModel(
            {
                "center": center,
                "drift_rate": 0.2,
                "reversion_rate": 0.15,
            },
            _make_rng(),
            noise=noise,
        )
        values = _run_ticks(model, 5_000)
        mean_val = np.mean(values)
        assert abs(mean_val - center) < 3.0


# ---------------------------------------------------------------------------
# Package imports
# ---------------------------------------------------------------------------


class TestPackageImports:
    def test_import_from_package(self) -> None:
        from factory_simulator.models import RandomWalkModel as Imported

        assert Imported is RandomWalkModel
