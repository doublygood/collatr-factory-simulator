"""Tests for the SinusoidalModel.

PRD Reference: Section 4.2.2 (Sinusoidal with Noise)
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factory_simulator.models.noise import NoiseGenerator
from factory_simulator.models.sinusoidal import SinusoidalModel

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
) -> SinusoidalModel:
    p = params if params is not None else {"center": 20.0, "amplitude": 5.0, "period": 86400.0}
    return SinusoidalModel(p, _make_rng(seed), noise=noise)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_defaults(self) -> None:
        model = SinusoidalModel({}, _make_rng())
        assert model.center == 0.0
        assert model.amplitude == 1.0
        assert model.period == 86400.0
        assert model.phase == 0.0

    def test_explicit_params(self) -> None:
        model = _make_model({
            "center": 22.0,
            "amplitude": 3.0,
            "period": 3600.0,
            "phase": 1.57,
        })
        assert model.center == 22.0
        assert model.amplitude == 3.0
        assert model.period == 3600.0
        assert model.phase == pytest.approx(1.57)

    def test_invalid_period_zero(self) -> None:
        with pytest.raises(ValueError, match="period must be > 0"):
            SinusoidalModel({"period": 0.0}, _make_rng())

    def test_invalid_period_negative(self) -> None:
        with pytest.raises(ValueError, match="period must be > 0"):
            SinusoidalModel({"period": -100.0}, _make_rng())


# ---------------------------------------------------------------------------
# Basic generation (no noise)
# ---------------------------------------------------------------------------


class TestBasicGeneration:
    def test_at_time_zero_phase_zero(self) -> None:
        """sin(0) = 0, so value = center."""
        model = _make_model({"center": 20.0, "amplitude": 5.0, "period": 100.0, "phase": 0.0})
        value = model.generate(0.0, DT)
        assert value == pytest.approx(20.0)

    def test_at_quarter_period(self) -> None:
        """sin(pi/2) = 1, so value = center + amplitude."""
        period = 100.0
        model = _make_model({"center": 20.0, "amplitude": 5.0, "period": period, "phase": 0.0})
        value = model.generate(period / 4.0, DT)
        assert value == pytest.approx(25.0)

    def test_at_half_period(self) -> None:
        """sin(pi) = 0, so value = center."""
        period = 100.0
        model = _make_model({"center": 20.0, "amplitude": 5.0, "period": period, "phase": 0.0})
        value = model.generate(period / 2.0, DT)
        assert value == pytest.approx(20.0, abs=1e-10)

    def test_at_three_quarter_period(self) -> None:
        """sin(3*pi/2) = -1, so value = center - amplitude."""
        period = 100.0
        model = _make_model({"center": 20.0, "amplitude": 5.0, "period": period, "phase": 0.0})
        value = model.generate(3.0 * period / 4.0, DT)
        assert value == pytest.approx(15.0)

    def test_full_period_returns_to_start(self) -> None:
        """sin(2*pi) = 0 = sin(0), so same value at t=0 and t=period."""
        period = 100.0
        model = _make_model({"center": 20.0, "amplitude": 5.0, "period": period, "phase": 0.0})
        v0 = model.generate(0.0, DT)
        v1 = model.generate(period, DT)
        assert v0 == pytest.approx(v1, abs=1e-10)

    def test_negative_amplitude(self) -> None:
        """Negative amplitude inverts the wave."""
        period = 100.0
        model = _make_model({"center": 20.0, "amplitude": -5.0, "period": period, "phase": 0.0})
        value = model.generate(period / 4.0, DT)
        assert value == pytest.approx(15.0)  # center + (-5) * sin(pi/2) = 20 - 5

    def test_zero_amplitude(self) -> None:
        """Zero amplitude produces constant center value."""
        model = _make_model({"center": 42.0, "amplitude": 0.0, "period": 100.0})
        for i in range(100):
            assert model.generate(i * DT, DT) == pytest.approx(42.0)

    def test_negative_center(self) -> None:
        model = _make_model({"center": -10.0, "amplitude": 3.0, "period": 100.0, "phase": 0.0})
        value = model.generate(0.0, DT)
        assert value == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# Phase offset
# ---------------------------------------------------------------------------


class TestPhaseOffset:
    def test_phase_shifts_wave(self) -> None:
        """Phase = pi/2 shifts peak to t=0."""
        period = 100.0
        model = _make_model({
            "center": 20.0,
            "amplitude": 5.0,
            "period": period,
            "phase": math.pi / 2,
        })
        # sin(2*pi*0/period + pi/2) = sin(pi/2) = 1
        value = model.generate(0.0, DT)
        assert value == pytest.approx(25.0)

    def test_phase_pi_inverts(self) -> None:
        """Phase = pi inverts the wave relative to phase=0."""
        period = 100.0
        model_0 = _make_model({
            "center": 20.0, "amplitude": 5.0, "period": period, "phase": 0.0,
        })
        model_pi = _make_model({
            "center": 20.0, "amplitude": 5.0, "period": period, "phase": math.pi,
        })
        # At t=T/4: sin(pi/2)=1 vs sin(pi/2 + pi)=-1
        v0 = model_0.generate(period / 4.0, DT)
        v_pi = model_pi.generate(period / 4.0, DT)
        assert v0 == pytest.approx(25.0)
        assert v_pi == pytest.approx(15.0)

    def test_humidity_inverted_phase(self) -> None:
        """PRD: env.ambient_humidity uses inverted phase.

        Humidity drops when temperature rises.  This is modelled by
        setting phase = pi (or negative amplitude).
        """
        period = 86400.0
        # Temperature: phase=0, peak at T/4 (mid-afternoon if start=dawn)
        temp = _make_model({"center": 20.0, "amplitude": 4.0, "period": period, "phase": 0.0})
        # Humidity: phase=pi, trough at T/4
        humidity = _make_model({
            "center": 60.0, "amplitude": 10.0, "period": period, "phase": math.pi,
        })

        t_quarter = period / 4.0
        temp_val = temp.generate(t_quarter, DT)
        humidity_val = humidity.generate(t_quarter, DT)

        # Temperature at peak, humidity at trough
        assert temp_val == pytest.approx(24.0)
        assert humidity_val == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Periodicity
# ---------------------------------------------------------------------------


class TestPeriodicity:
    def test_values_periodic(self) -> None:
        """Signal repeats exactly every period."""
        period = 60.0  # 1 minute
        model = _make_model({"center": 10.0, "amplitude": 3.0, "period": period, "phase": 0.5})

        for t_offset in [0.0, 7.3, 15.0, 30.0, 59.9]:
            v0 = model.generate(t_offset, DT)
            v1 = model.generate(t_offset + period, DT)
            v2 = model.generate(t_offset + 2 * period, DT)
            assert v0 == pytest.approx(v1, abs=1e-10)
            assert v0 == pytest.approx(v2, abs=1e-10)

    def test_short_period(self) -> None:
        """A 1-second period oscillates rapidly."""
        period = 1.0
        model = _make_model({"center": 0.0, "amplitude": 1.0, "period": period})
        # At t=0.25: sin(pi/2)=1
        assert model.generate(0.25, DT) == pytest.approx(1.0)
        # At t=0.75: sin(3*pi/2)=-1
        assert model.generate(0.75, DT) == pytest.approx(-1.0)

    def test_long_period_24h(self) -> None:
        """PRD daily cycle: 86400s period."""
        period = 86400.0
        model = _make_model({"center": 20.0, "amplitude": 4.0, "period": period})
        # 6 hours = T/4 → peak
        six_hours = 6 * 3600.0
        assert model.generate(six_hours, DT) == pytest.approx(24.0)


# ---------------------------------------------------------------------------
# Output range
# ---------------------------------------------------------------------------


class TestOutputRange:
    def test_range_without_noise(self) -> None:
        """Without noise, output stays within [center-amplitude, center+amplitude]."""
        model = _make_model({"center": 50.0, "amplitude": 10.0, "period": 60.0})
        values = [model.generate(t * 0.1, DT) for t in range(6000)]  # 10 minutes
        assert min(values) >= 50.0 - 10.0 - 1e-10
        assert max(values) <= 50.0 + 10.0 + 1e-10

    def test_max_and_min_achieved(self) -> None:
        """Over a full period, both extremes are approximately reached."""
        period = 100.0
        model = _make_model({"center": 50.0, "amplitude": 10.0, "period": period})
        values = [model.generate(t * 0.1, DT) for t in range(1001)]  # just over 1 period
        assert max(values) == pytest.approx(60.0, abs=0.1)
        assert min(values) == pytest.approx(40.0, abs=0.1)


# ---------------------------------------------------------------------------
# Generation with noise
# ---------------------------------------------------------------------------


class TestNoiseGeneration:
    def test_mean_near_center(self) -> None:
        """Over many full periods, mean converges to center."""
        period = 100.0
        noise = _make_noise(sigma=0.5)
        model = _make_model({"center": 20.0, "amplitude": 5.0, "period": period}, noise=noise)

        # Run for 100 full periods
        n_ticks = int(100 * period / DT)
        values = [model.generate(i * DT, DT) for i in range(n_ticks)]
        mean = np.mean(values)
        assert mean == pytest.approx(20.0, abs=0.5)

    def test_noise_adds_variation(self) -> None:
        """Noise makes values deviate from the pure sine."""
        period = 100.0

        # Generate at a fixed time point with different seeds.
        # Without noise, value would be exactly the same each call,
        # but noise makes it different.
        values = set()
        for seed in range(50):
            n = _make_noise(sigma=2.0, seed=seed)
            m = SinusoidalModel(
                {"center": 20.0, "amplitude": 5.0, "period": period},
                _make_rng(seed),
                noise=n,
            )
            values.add(m.generate(0.0, DT))
        # At t=0, pure sine = center = 20.0.  With noise, values should vary.
        assert len(values) > 1

    def test_zero_sigma_clean_signal(self) -> None:
        """Zero sigma noise produces a clean sinusoidal."""
        period = 100.0
        noise = _make_noise(sigma=0.0)
        model = _make_model({"center": 20.0, "amplitude": 5.0, "period": period}, noise=noise)

        for i in range(1000):
            t = i * DT
            expected = 20.0 + 5.0 * math.sin(2.0 * math.pi * t / period)
            actual = model.generate(t, DT)
            assert actual == pytest.approx(expected, abs=1e-10)


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_resets_ar1_noise(self) -> None:
        noise = _make_noise(sigma=1.0, distribution="ar1", phi=0.9)
        model = _make_model({"center": 0.0, "amplitude": 1.0, "period": 100.0}, noise=noise)
        for i in range(100):
            model.generate(i * DT, DT)
        model.reset()
        assert noise._ar1_prev == 0.0

    def test_reset_without_noise(self) -> None:
        """Reset on a noise-free model doesn't error."""
        model = _make_model()
        model.generate(0.0, DT)
        model.reset()  # should not raise


# ---------------------------------------------------------------------------
# Determinism (Rule 13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_same_output(self) -> None:
        """Two models with same seed produce identical sequences."""
        params: dict[str, object] = {
            "center": 20.0, "amplitude": 5.0, "period": 100.0,
        }
        noise1 = _make_noise(sigma=2.0, seed=99)
        model1 = SinusoidalModel(params, _make_rng(99), noise=noise1)

        noise2 = _make_noise(sigma=2.0, seed=99)
        model2 = SinusoidalModel(params, _make_rng(99), noise=noise2)

        for i in range(500):
            v1 = model1.generate(i * DT, DT)
            v2 = model2.generate(i * DT, DT)
            assert v1 == v2

    def test_different_seeds_differ(self) -> None:
        """Different seeds produce different sequences (noise differs)."""
        params: dict[str, object] = {"center": 20.0, "amplitude": 5.0, "period": 100.0}
        noise1 = _make_noise(sigma=2.0, seed=1)
        model1 = SinusoidalModel(params, _make_rng(1), noise=noise1)

        noise2 = _make_noise(sigma=2.0, seed=2)
        model2 = SinusoidalModel(params, _make_rng(2), noise=noise2)

        values1 = [model1.generate(i * DT, DT) for i in range(100)]
        values2 = [model2.generate(i * DT, DT) for i in range(100)]
        assert values1 != values2

    def test_no_noise_always_deterministic(self) -> None:
        """Without noise, output depends only on sim_time (pure function)."""
        params: dict[str, object] = {"center": 20.0, "amplitude": 5.0, "period": 100.0}
        model1 = SinusoidalModel(params, _make_rng(1))
        model2 = SinusoidalModel(params, _make_rng(999))  # different seed irrelevant

        for i in range(200):
            assert model1.generate(i * DT, DT) == model2.generate(i * DT, DT)


# ---------------------------------------------------------------------------
# Hypothesis property-based tests
# ---------------------------------------------------------------------------


class TestPropertyBased:
    @given(
        center=st.floats(min_value=-1000, max_value=1000, allow_nan=False),
        amplitude=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
        period=st.floats(min_value=0.1, max_value=1e6, allow_nan=False),
        sim_time=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_output_finite(
        self, center: float, amplitude: float, period: float, sim_time: float
    ) -> None:
        """Output is always finite for valid inputs."""
        model = SinusoidalModel(
            {"center": center, "amplitude": amplitude, "period": period},
            _make_rng(),
        )
        value = model.generate(sim_time, DT)
        assert np.isfinite(value)

    @given(
        center=st.floats(min_value=-100, max_value=100, allow_nan=False),
        amplitude=st.floats(min_value=0.0, max_value=50.0, allow_nan=False),
        period=st.floats(min_value=0.1, max_value=1e4, allow_nan=False),
        sim_time=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_output_within_bounds_no_noise(
        self, center: float, amplitude: float, period: float, sim_time: float
    ) -> None:
        """Without noise, output is within [center-|amplitude|, center+|amplitude|]."""
        model = SinusoidalModel(
            {"center": center, "amplitude": amplitude, "period": period},
            _make_rng(),
        )
        value = model.generate(sim_time, DT)
        assert value >= center - abs(amplitude) - 1e-10
        assert value <= center + abs(amplitude) + 1e-10

    @given(seed=st.integers(min_value=0, max_value=2**31))
    @settings(max_examples=20)
    def test_determinism_any_seed(self, seed: int) -> None:
        """Any seed produces deterministic output."""
        params: dict[str, object] = {"center": 20.0, "amplitude": 5.0, "period": 100.0}

        n1 = NoiseGenerator(sigma=1.0, distribution="gaussian", rng=_make_rng(seed))
        m1 = SinusoidalModel(params, _make_rng(seed), noise=n1)

        n2 = NoiseGenerator(sigma=1.0, distribution="gaussian", rng=_make_rng(seed))
        m2 = SinusoidalModel(params, _make_rng(seed), noise=n2)

        for i in range(20):
            assert m1.generate(i * DT, DT) == m2.generate(i * DT, DT)

    @given(
        period=st.floats(min_value=1.0, max_value=1000.0, allow_nan=False),
    )
    @settings(max_examples=50)
    def test_periodic(self, period: float) -> None:
        """Signal value repeats exactly at t and t + period."""
        model = SinusoidalModel(
            {"center": 10.0, "amplitude": 3.0, "period": period, "phase": 0.7},
            _make_rng(),
        )
        t = period * 0.37  # arbitrary time within a period
        v0 = model.generate(t, DT)
        v1 = model.generate(t + period, DT)
        assert v0 == pytest.approx(v1, abs=1e-8)


# ---------------------------------------------------------------------------
# PRD example: ambient humidity daily cycle
# ---------------------------------------------------------------------------


class TestPRDExamples:
    def test_ambient_humidity_daily_cycle(self) -> None:
        """PRD 4.2.2: env.ambient_humidity uses sinusoidal with inverted phase.

        Center ~60%, amplitude ~10%, 24h period, phase=pi for inversion.
        """
        period = 86400.0  # 24 hours
        model = SinusoidalModel(
            {
                "center": 60.0,
                "amplitude": 10.0,
                "period": period,
                "phase": math.pi,
            },
            _make_rng(),
        )

        # At t=0 (dawn): sin(pi)=0, value=center=60%
        v_dawn = model.generate(0.0, DT)
        assert v_dawn == pytest.approx(60.0, abs=1e-10)

        # At T/4 (mid-morning → mid-afternoon): sin(pi/2 + pi) = -1
        # value = 60 + 10*(-1) = 50%
        v_afternoon = model.generate(period / 4.0, DT)
        assert v_afternoon == pytest.approx(50.0, abs=1e-10)

        # At T*3/4: sin(3pi/2 + pi) = sin(5pi/2) = 1
        # value = 60 + 10*(1) = 70%
        v_night = model.generate(3.0 * period / 4.0, DT)
        assert v_night == pytest.approx(70.0, abs=1e-10)

    def test_daily_temp_base_layer(self) -> None:
        """PRD 4.2.2: env.ambient_temp daily cycle base layer.

        Center 20-22C, amplitude 3-5C, 24h period.
        """
        period = 86400.0
        noise = _make_noise(sigma=0.3)
        model = SinusoidalModel(
            {"center": 21.0, "amplitude": 4.0, "period": period},
            _make_rng(),
            noise=noise,
        )

        # Run one full day at 10s ticks
        dt_10s = 10.0
        n_ticks = int(period / dt_10s)
        values = [model.generate(i * dt_10s, dt_10s) for i in range(n_ticks)]

        arr = np.array(values)
        # Mean should be near center
        assert np.mean(arr) == pytest.approx(21.0, abs=1.0)
        # Min/max should approximately reach center +/- amplitude
        assert np.min(arr) < 18.0  # center - amplitude - noise
        assert np.max(arr) > 24.0  # center + amplitude + noise


# ---------------------------------------------------------------------------
# Package imports
# ---------------------------------------------------------------------------


class TestPackageImports:
    def test_import_from_models_package(self) -> None:
        from factory_simulator.models import SinusoidalModel as SM
        assert SM is SinusoidalModel
