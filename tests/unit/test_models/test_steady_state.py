"""Tests for the SteadyStateModel and post-processing utilities.

PRD Reference: Section 4.2.1 (Steady State with Noise), Section 4.2.13 (Quantisation)
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factory_simulator.models.base import clamp, quantise
from factory_simulator.models.noise import NoiseGenerator
from factory_simulator.models.steady_state import SteadyStateModel

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
) -> SteadyStateModel:
    p = params if params is not None else {"target": 100.0}
    return SteadyStateModel(p, _make_rng(seed), noise=noise)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_default_target(self) -> None:
        model = SteadyStateModel({}, _make_rng())
        assert model.target == 0.0

    def test_explicit_target(self) -> None:
        model = _make_model({"target": 42.5})
        assert model.target == 42.5

    def test_drift_defaults(self) -> None:
        model = _make_model()
        assert model.drift_offset == 0.0
        assert model.calibration_bias == 0.0

    def test_max_drift_default_three_percent(self) -> None:
        model = _make_model({"target": 200.0})
        assert model._max_drift == pytest.approx(6.0)  # 3% of 200

    def test_max_drift_default_zero_target(self) -> None:
        model = _make_model({"target": 0.0})
        assert model._max_drift == pytest.approx(0.03)  # minimum floor

    def test_max_drift_explicit(self) -> None:
        model = _make_model({"target": 100.0, "max_drift": 5.0})
        assert model._max_drift == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Basic generation (no noise, no drift)
# ---------------------------------------------------------------------------


class TestBasicGeneration:
    def test_returns_target_without_noise(self) -> None:
        model = _make_model({"target": 50.0})
        value = model.generate(0.0, DT)
        assert value == pytest.approx(50.0)

    def test_returns_target_across_multiple_ticks(self) -> None:
        model = _make_model({"target": 75.0})
        for i in range(100):
            value = model.generate(i * DT, DT)
            assert value == pytest.approx(75.0)

    def test_negative_target(self) -> None:
        model = _make_model({"target": -10.0})
        assert model.generate(0.0, DT) == pytest.approx(-10.0)

    def test_zero_target(self) -> None:
        model = _make_model({"target": 0.0})
        assert model.generate(0.0, DT) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Generation with noise
# ---------------------------------------------------------------------------


class TestNoiseGeneration:
    def test_mean_near_target(self) -> None:
        noise = _make_noise(sigma=2.0)
        model = _make_model({"target": 100.0}, noise=noise)
        values = [model.generate(i * DT, DT) for i in range(10_000)]
        mean = np.mean(values)
        assert mean == pytest.approx(100.0, abs=1.0)

    def test_stddev_matches_sigma(self) -> None:
        noise = _make_noise(sigma=5.0)
        model = _make_model({"target": 0.0}, noise=noise)
        values = [model.generate(i * DT, DT) for i in range(10_000)]
        std = np.std(values)
        assert std == pytest.approx(5.0, rel=0.15)

    def test_noise_adds_variation(self) -> None:
        noise = _make_noise(sigma=1.0)
        model = _make_model({"target": 50.0}, noise=noise)
        values = [model.generate(i * DT, DT) for i in range(100)]
        assert len(set(values)) > 1  # not all identical

    def test_zero_sigma_no_noise(self) -> None:
        noise = _make_noise(sigma=0.0)
        model = _make_model({"target": 42.0}, noise=noise)
        values = [model.generate(i * DT, DT) for i in range(100)]
        assert all(v == pytest.approx(42.0) for v in values)


# ---------------------------------------------------------------------------
# Within-regime drift
# ---------------------------------------------------------------------------


class TestWithinRegimeDrift:
    def test_drift_disabled_by_default(self) -> None:
        """With drift_rate=0, drift_offset stays at zero."""
        model = _make_model({"target": 100.0, "drift_rate": 0.0})
        for i in range(1000):
            model.generate(i * DT, DT)
        assert model.drift_offset == pytest.approx(0.0)

    def test_drift_accumulates(self) -> None:
        """With drift_rate > 0, drift_offset changes over time."""
        model = _make_model({"target": 100.0, "drift_rate": 0.01})
        for i in range(5000):
            model.generate(i * DT, DT)
        assert model.drift_offset != 0.0

    def test_drift_clamped_to_max_drift(self) -> None:
        """Drift offset never exceeds max_drift."""
        model = _make_model({
            "target": 100.0,
            "drift_rate": 1.0,  # aggressive drift for testing
            "max_drift": 0.5,
            "reversion_rate": 0.0,  # disable reversion to push limits
        })
        for i in range(10_000):
            model.generate(i * DT, DT)
        assert abs(model.drift_offset) <= 0.5 + 1e-10

    def test_drift_affects_output(self) -> None:
        """Signal value reflects the drift offset."""
        model = _make_model({
            "target": 100.0,
            "drift_rate": 0.5,
            "reversion_rate": 0.0,
            "max_drift": 10.0,
        })
        # Run for a while to accumulate drift
        for i in range(1000):
            model.generate(i * DT, DT)
        # The value should be near target + drift (no noise)
        value = model.generate(1000 * DT, DT)
        assert value == pytest.approx(100.0 + model.drift_offset, abs=0.1)

    def test_reversion_pulls_back(self) -> None:
        """With high reversion rate, drift stays small."""
        model = _make_model({
            "target": 100.0,
            "drift_rate": 0.01,
            "reversion_rate": 10.0,  # strong reversion
            "max_drift": 100.0,
        })
        max_drift_seen = 0.0
        for i in range(5000):
            model.generate(i * DT, DT)
            max_drift_seen = max(max_drift_seen, abs(model.drift_offset))
        # Strong reversion should keep drift very small
        assert max_drift_seen < 0.1

    def test_drift_slow_over_short_time(self) -> None:
        """Over a few minutes, drift is imperceptible (< 1% of target)."""
        model = _make_model({
            "target": 100.0,
            "drift_rate": 0.001,  # PRD default
            "reversion_rate": 0.0001,  # PRD default
        })
        # 5 minutes at 100ms ticks = 3000 ticks
        for i in range(3000):
            model.generate(i * DT, DT)
        # Drift should be small relative to target
        assert abs(model.drift_offset) < 1.0  # < 1% of 100


# ---------------------------------------------------------------------------
# Calibration drift
# ---------------------------------------------------------------------------


class TestCalibrationDrift:
    def test_disabled_by_default(self) -> None:
        model = _make_model({"target": 100.0})
        for i in range(1000):
            model.generate(i * DT, DT)
        assert model.calibration_bias == pytest.approx(0.0)

    def test_accumulates_linearly(self) -> None:
        """Calibration bias = calibration_drift_rate * total_elapsed_time."""
        rate = 0.001  # per second
        model = _make_model({
            "target": 100.0,
            "calibration_drift_rate": rate,
        })
        n_ticks = 1000
        for i in range(n_ticks):
            model.generate(i * DT, DT)
        expected_bias = rate * n_ticks * DT
        assert model.calibration_bias == pytest.approx(expected_bias, rel=1e-10)

    def test_affects_output(self) -> None:
        """Output value includes calibration bias."""
        rate = 0.01
        model = _make_model({
            "target": 50.0,
            "calibration_drift_rate": rate,
        })
        n_ticks = 100
        for i in range(n_ticks):
            model.generate(i * DT, DT)
        # After 100 ticks at 0.1s each = 10s → bias = 0.01 * 10 = 0.1
        # Next value should be target + bias
        value = model.generate(n_ticks * DT, DT)
        # bias after 101 ticks
        expected_bias = rate * (n_ticks + 1) * DT
        assert value == pytest.approx(50.0 + expected_bias, abs=1e-10)

    def test_does_not_revert(self) -> None:
        """Unlike within-regime drift, calibration drift only accumulates."""
        rate = 0.001
        model = _make_model({
            "target": 100.0,
            "calibration_drift_rate": rate,
        })
        biases = []
        for i in range(500):
            model.generate(i * DT, DT)
            biases.append(model.calibration_bias)
        # Should be monotonically increasing
        for j in range(1, len(biases)):
            assert biases[j] > biases[j - 1]

    def test_negative_drift_rate(self) -> None:
        """Negative calibration drift produces decreasing bias."""
        rate = -0.001
        model = _make_model({
            "target": 100.0,
            "calibration_drift_rate": rate,
        })
        for i in range(500):
            model.generate(i * DT, DT)
        assert model.calibration_bias < 0.0


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_resets_drift_offset(self) -> None:
        model = _make_model({
            "target": 100.0,
            "drift_rate": 0.1,
        })
        for i in range(1000):
            model.generate(i * DT, DT)
        assert model.drift_offset != 0.0
        model.reset()
        assert model.drift_offset == 0.0

    def test_resets_calibration_bias(self) -> None:
        model = _make_model({
            "target": 100.0,
            "calibration_drift_rate": 0.01,
        })
        for i in range(100):
            model.generate(i * DT, DT)
        assert model.calibration_bias != 0.0
        model.reset()
        assert model.calibration_bias == 0.0

    def test_resets_noise(self) -> None:
        noise = _make_noise(sigma=1.0, distribution="ar1", phi=0.9)
        model = _make_model({"target": 0.0}, noise=noise)
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
        noise1 = _make_noise(sigma=2.0, seed=99)
        model1 = _make_model({"target": 50.0}, seed=99, noise=noise1)

        noise2 = _make_noise(sigma=2.0, seed=99)
        model2 = _make_model({"target": 50.0}, seed=99, noise=noise2)

        for i in range(500):
            v1 = model1.generate(i * DT, DT)
            v2 = model2.generate(i * DT, DT)
            assert v1 == v2

    def test_same_seed_with_drift(self) -> None:
        """Determinism holds with drift enabled."""
        params: dict[str, object] = {
            "target": 100.0,
            "drift_rate": 0.01,
            "calibration_drift_rate": 0.001,
        }
        noise1 = _make_noise(sigma=1.0, seed=77)
        model1 = _make_model(params, seed=77, noise=noise1)

        noise2 = _make_noise(sigma=1.0, seed=77)
        model2 = _make_model(params, seed=77, noise=noise2)

        for i in range(500):
            v1 = model1.generate(i * DT, DT)
            v2 = model2.generate(i * DT, DT)
            assert v1 == v2

    def test_different_seeds_differ(self) -> None:
        """Different seeds produce different sequences."""
        noise1 = _make_noise(sigma=2.0, seed=1)
        model1 = _make_model({"target": 50.0}, seed=1, noise=noise1)

        noise2 = _make_noise(sigma=2.0, seed=2)
        model2 = _make_model({"target": 50.0}, seed=2, noise=noise2)

        values1 = [model1.generate(i * DT, DT) for i in range(100)]
        values2 = [model2.generate(i * DT, DT) for i in range(100)]
        assert values1 != values2


# ---------------------------------------------------------------------------
# Quantisation (PRD 4.2.13)
# ---------------------------------------------------------------------------


class TestQuantise:
    def test_disabled_when_none(self) -> None:
        assert quantise(3.14159, None) == pytest.approx(3.14159)

    def test_disabled_when_zero(self) -> None:
        assert quantise(3.14159, 0.0) == pytest.approx(3.14159)

    def test_disabled_when_negative(self) -> None:
        assert quantise(3.14159, -1.0) == pytest.approx(3.14159)

    def test_resolution_0_1(self) -> None:
        """Eurotherm int16 x10: 0.1 C resolution."""
        assert quantise(23.456, 0.1) == pytest.approx(23.5)
        assert quantise(23.449, 0.1) == pytest.approx(23.4)
        assert quantise(23.45, 0.1) == pytest.approx(23.4)  # banker's rounding

    def test_resolution_0_024(self) -> None:
        """12-bit ADC, 0-100 C range: 50.0/0.024 = 2083.33 → round to 2083 → 49.992."""
        assert quantise(50.0, 0.024) == pytest.approx(49.992, abs=0.001)

    def test_exact_multiples_unchanged(self) -> None:
        assert quantise(10.0, 0.5) == pytest.approx(10.0)
        assert quantise(10.5, 0.5) == pytest.approx(10.5)

    def test_negative_values(self) -> None:
        assert quantise(-3.7, 0.5) == pytest.approx(-3.5)
        assert quantise(-3.8, 0.5) == pytest.approx(-4.0)

    def test_zero_value(self) -> None:
        assert quantise(0.0, 0.1) == pytest.approx(0.0)

    @given(
        value=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False),
        resolution=st.floats(min_value=0.001, max_value=100.0),
    )
    @settings(max_examples=200)
    def test_quantised_is_multiple_of_resolution(
        self, value: float, resolution: float
    ) -> None:
        """Quantised value is always a multiple of resolution (within fp tolerance)."""
        q = quantise(value, resolution)
        remainder = abs(q / resolution - round(q / resolution))
        # Tolerance scaled for large quotients (value/resolution ~ 1e9 at extremes)
        assert remainder < 1e-6


# ---------------------------------------------------------------------------
# Clamp
# ---------------------------------------------------------------------------


class TestClamp:
    def test_no_bounds(self) -> None:
        assert clamp(999.0, None, None) == pytest.approx(999.0)

    def test_min_only(self) -> None:
        assert clamp(-5.0, 0.0, None) == pytest.approx(0.0)
        assert clamp(5.0, 0.0, None) == pytest.approx(5.0)

    def test_max_only(self) -> None:
        assert clamp(150.0, None, 100.0) == pytest.approx(100.0)
        assert clamp(50.0, None, 100.0) == pytest.approx(50.0)

    def test_both_bounds(self) -> None:
        assert clamp(50.0, 0.0, 100.0) == pytest.approx(50.0)
        assert clamp(-1.0, 0.0, 100.0) == pytest.approx(0.0)
        assert clamp(101.0, 0.0, 100.0) == pytest.approx(100.0)

    def test_at_boundary(self) -> None:
        assert clamp(0.0, 0.0, 100.0) == pytest.approx(0.0)
        assert clamp(100.0, 0.0, 100.0) == pytest.approx(100.0)

    @given(
        value=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False),
        lo=st.floats(min_value=-1e6, max_value=0.0, allow_nan=False),
        hi=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_clamped_within_bounds(
        self, value: float, lo: float, hi: float
    ) -> None:
        result = clamp(value, lo, hi)
        assert result >= lo
        assert result <= hi


# ---------------------------------------------------------------------------
# Hypothesis property-based tests
# ---------------------------------------------------------------------------


class TestPropertyBased:
    @given(
        target=st.floats(min_value=-1000, max_value=1000, allow_nan=False),
        sigma=st.floats(min_value=0.0, max_value=50.0, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_output_finite(self, target: float, sigma: float) -> None:
        """Output is always finite."""
        noise = NoiseGenerator(sigma=sigma, distribution="gaussian", rng=_make_rng())
        model = SteadyStateModel({"target": target}, _make_rng(), noise=noise)
        value = model.generate(0.0, DT)
        assert np.isfinite(value)

    @given(
        target=st.floats(min_value=-100, max_value=100, allow_nan=False),
        lo=st.floats(min_value=-200, max_value=-50, allow_nan=False),
        hi=st.floats(min_value=50, max_value=200, allow_nan=False),
    )
    @settings(max_examples=50)
    def test_clamped_output_within_bounds(
        self, target: float, lo: float, hi: float
    ) -> None:
        """When clamping is applied, output is within bounds."""
        noise = NoiseGenerator(sigma=5.0, distribution="gaussian", rng=_make_rng())
        model = SteadyStateModel({"target": target}, _make_rng(), noise=noise)
        for i in range(50):
            raw = model.generate(i * DT, DT)
            clamped = clamp(raw, lo, hi)
            assert lo <= clamped <= hi

    @given(seed=st.integers(min_value=0, max_value=2**31))
    @settings(max_examples=20)
    def test_determinism_any_seed(self, seed: int) -> None:
        """Any seed produces deterministic output."""
        params: dict[str, object] = {"target": 42.0, "drift_rate": 0.01}

        n1 = NoiseGenerator(sigma=1.0, distribution="gaussian", rng=_make_rng(seed))
        m1 = SteadyStateModel(params, _make_rng(seed), noise=n1)

        n2 = NoiseGenerator(sigma=1.0, distribution="gaussian", rng=_make_rng(seed))
        m2 = SteadyStateModel(params, _make_rng(seed), noise=n2)

        for i in range(20):
            assert m1.generate(i * DT, DT) == m2.generate(i * DT, DT)


# ---------------------------------------------------------------------------
# Integration: full pipeline (generate + noise + quantise + clamp)
# ---------------------------------------------------------------------------


class TestFullPipeline:
    def test_pipeline_order(self) -> None:
        """Simulate the engine pipeline: generate → quantise → clamp."""
        noise = _make_noise(sigma=2.0)
        model = _make_model({"target": 50.0}, noise=noise)

        for i in range(100):
            raw = model.generate(i * DT, DT)
            quantised = quantise(raw, 0.1)
            clamped = clamp(quantised, 0.0, 100.0)
            assert 0.0 <= clamped <= 100.0
            # Quantised values are multiples of 0.1
            assert abs(clamped * 10 - round(clamped * 10)) < 1e-9

    def test_ink_pressure_signal(self) -> None:
        """PRD example: ink pressure, target ~835 mbar, sigma ~60 mbar, range 0-900."""
        noise = _make_noise(sigma=60.0, seed=123)
        model = _make_model({"target": 835.0}, seed=123, noise=noise)

        values = []
        for i in range(10_000):
            raw = model.generate(i * DT, DT)
            clamped = clamp(raw, 0.0, 900.0)
            values.append(clamped)

        arr = np.array(values)
        assert np.mean(arr) == pytest.approx(835.0, abs=5.0)
        assert np.all(arr >= 0.0)
        assert np.all(arr <= 900.0)
        # With sigma=60 and clamp at 900, some values should hit the upper clamp
        assert np.any(arr == 900.0)

    def test_supply_voltage_signal(self) -> None:
        """PRD example: supply voltage, target 24V, sigma 0.1V."""
        noise = _make_noise(sigma=0.1, seed=456)
        model = _make_model({"target": 24.0}, seed=456, noise=noise)

        values = [model.generate(i * DT, DT) for i in range(1000)]
        arr = np.array(values)
        assert np.mean(arr) == pytest.approx(24.0, abs=0.05)
        assert np.std(arr) == pytest.approx(0.1, rel=0.2)


# ---------------------------------------------------------------------------
# Package imports
# ---------------------------------------------------------------------------


class TestPackageImports:
    def test_import_from_models_package(self) -> None:
        from factory_simulator.models import SteadyStateModel as SSM
        assert SSM is SteadyStateModel

    def test_import_quantise(self) -> None:
        from factory_simulator.models import quantise as q
        assert q is quantise

    def test_import_clamp(self) -> None:
        from factory_simulator.models import clamp as c
        assert c is clamp
