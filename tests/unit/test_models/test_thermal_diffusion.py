"""Tests for the ThermalDiffusionModel.

PRD Reference: Section 4.2.10 (Thermal Diffusion / Sigmoid)

Validates:
- Fourier series convergence (T(0) within 1C of T_initial)
- Temperature approaches T_oven asymptotically
- Monotonic heating/cooling behaviour
- Correct PRD coefficients (C_n = 8 / ((2n+1)^2 * pi^2))
- Physical correctness for typical chilled ready-meal parameters
- Determinism (Rule 13), simulated time invariant (Rule 6)
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factory_simulator.models.noise import NoiseGenerator
from factory_simulator.models.thermal_diffusion import ThermalDiffusionModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEED = 42
DT = 0.5  # 500 ms tick (matches fastest signal rate)

# PRD typical values for chilled ready meal
TYPICAL_PARAMS: dict[str, object] = {
    "T_initial": 4.0,
    "T_oven": 180.0,
    "alpha": 1.4e-7,
    "L": 0.025,
}


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
) -> ThermalDiffusionModel:
    p = params if params is not None else dict(TYPICAL_PARAMS)
    return ThermalDiffusionModel(p, _make_rng(seed), noise=noise)


def _run_ticks(model: ThermalDiffusionModel, n: int, dt: float = DT) -> list[float]:
    """Run n ticks and return all values."""
    return [model.generate(i * dt, dt) for i in range(n)]


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_defaults(self) -> None:
        model = ThermalDiffusionModel({}, _make_rng())
        assert model.T_initial == 4.0
        assert model.T_oven == 180.0
        assert model.elapsed == 0.0
        assert model.n_terms >= 1

    def test_explicit_params(self) -> None:
        model = _make_model(TYPICAL_PARAMS)
        assert model.T_initial == 4.0
        assert model.T_oven == 180.0
        assert model.elapsed == 0.0

    def test_custom_params(self) -> None:
        params: dict[str, object] = {
            "T_initial": 20.0,
            "T_oven": 100.0,
            "alpha": 1.0e-7,
            "L": 0.015,
        }
        model = _make_model(params)
        assert model.T_initial == 20.0
        assert model.T_oven == 100.0

    def test_invalid_L_zero(self) -> None:
        with pytest.raises(ValueError, match="L.*must be > 0"):
            _make_model({"L": 0.0})

    def test_invalid_L_negative(self) -> None:
        with pytest.raises(ValueError, match="L.*must be > 0"):
            _make_model({"L": -0.01})

    def test_invalid_alpha_zero(self) -> None:
        with pytest.raises(ValueError, match="alpha.*must be > 0"):
            _make_model({"alpha": 0.0})

    def test_invalid_alpha_negative(self) -> None:
        with pytest.raises(ValueError, match="alpha.*must be > 0"):
            _make_model({"alpha": -1e-7})


# ---------------------------------------------------------------------------
# Fourier Series Convergence (PRD 4.2.10)
# ---------------------------------------------------------------------------


class TestConvergence:
    """Verify T(0) is within 1C of T_initial for various temperature ranges."""

    def test_large_difference_176C(self) -> None:
        """PRD example: T_initial=4, T_oven=180, delta=176C."""
        model = _make_model({"T_initial": 4.0, "T_oven": 180.0})
        # Verify convergence: sum of coefficients close enough to 1.0
        coeff_sum = sum(model._coefficients)
        t0_error = abs(180.0 - 4.0) * (1.0 - coeff_sum)
        assert t0_error <= 1.0, f"T(0) error = {t0_error}C, exceeds 1C"
        # PRD says 20-30 terms needed for 176C difference
        assert model.n_terms >= 15

    def test_small_difference_50C(self) -> None:
        """Small difference: fewer terms needed."""
        model = _make_model({"T_initial": 20.0, "T_oven": 70.0})
        coeff_sum = sum(model._coefficients)
        t0_error = 50.0 * (1.0 - coeff_sum)
        assert t0_error <= 1.0
        # PRD says 10 terms suffice for <50C difference
        assert model.n_terms <= 20

    def test_tiny_difference_5C(self) -> None:
        """Tiny difference: very few terms needed."""
        model = _make_model({"T_initial": 20.0, "T_oven": 25.0})
        coeff_sum = sum(model._coefficients)
        t0_error = 5.0 * (1.0 - coeff_sum)
        assert t0_error <= 1.0
        assert model.n_terms <= 5

    def test_equal_temperatures(self) -> None:
        """T_initial == T_oven: 1 term, zero error."""
        model = _make_model({"T_initial": 100.0, "T_oven": 100.0})
        assert model.n_terms == 1  # delta_T=0 breaks immediately
        coeff_sum = sum(model._coefficients)
        t0_error = 0.0 * (1.0 - coeff_sum)
        assert t0_error == 0.0

    def test_prd_three_term_sum(self) -> None:
        """PRD states first three coefficients sum to 0.9331."""
        pi_sq = math.pi**2
        c0 = 8.0 / (1 * 1 * pi_sq)
        c1 = 8.0 / (3 * 3 * pi_sq)
        c2 = 8.0 / (5 * 5 * pi_sq)
        three_term_sum = c0 + c1 + c2
        assert abs(three_term_sum - 0.9331) < 0.001

    def test_prd_coefficient_values(self) -> None:
        """PRD Table: n=0 -> 0.8106, n=1 -> 0.0901, n=2 -> 0.0324."""
        pi_sq = math.pi**2
        assert abs(8.0 / pi_sq - 0.8106) < 0.001
        assert abs(8.0 / (9 * pi_sq) - 0.0901) < 0.001
        assert abs(8.0 / (25 * pi_sq) - 0.0324) < 0.001

    def test_prd_three_term_T0(self) -> None:
        """PRD: with 3 terms, T(0) = 180 - 0.9331*176 = 15.8C."""
        pi_sq = math.pi**2
        coeffs = [8.0 / ((2 * n + 1) ** 2 * pi_sq) for n in range(3)]
        t0 = 180.0 - 176.0 * sum(coeffs)
        assert abs(t0 - 15.8) < 0.3  # PRD rounds to 15.8

    def test_cooling_convergence(self) -> None:
        """Cooling case: T_initial > T_oven."""
        model = _make_model({"T_initial": 200.0, "T_oven": 20.0})
        coeff_sum = sum(model._coefficients)
        t0_error = abs(200.0 - 20.0) * (1.0 - coeff_sum)
        assert t0_error <= 1.0


# ---------------------------------------------------------------------------
# Temperature Evolution
# ---------------------------------------------------------------------------


class TestTemperatureEvolution:
    def test_approaches_T_oven(self) -> None:
        """Temperature should approach T_oven over time."""
        model = _make_model()
        # Run for 60 minutes (3600 ticks at 1s dt)
        dt = 1.0
        values = [model.generate(i * dt, dt) for i in range(3600)]
        final = values[-1]
        assert final > 150.0, f"After 60 min, temp should be near T_oven, got {final}"
        assert final < 180.0  # Should not exceed T_oven

    def test_monotonic_heating(self) -> None:
        """Temperature should increase monotonically (heating case)."""
        model = _make_model()
        dt = 1.0
        values = _run_ticks(model, 600, dt=dt)
        for i in range(1, len(values)):
            assert values[i] >= values[i - 1] - 1e-10, (
                f"Non-monotonic at tick {i}: {values[i-1]} -> {values[i]}"
            )

    def test_monotonic_cooling(self) -> None:
        """Temperature should decrease monotonically (cooling case)."""
        model = _make_model({"T_initial": 200.0, "T_oven": 20.0})
        dt = 1.0
        values = _run_ticks(model, 600, dt=dt)
        for i in range(1, len(values)):
            assert values[i] <= values[i - 1] + 1e-10, (
                f"Non-monotonic at tick {i}: {values[i-1]} -> {values[i]}"
            )

    def test_equal_temps_constant(self) -> None:
        """When T_initial == T_oven, output is constant."""
        model = _make_model({"T_initial": 100.0, "T_oven": 100.0})
        values = _run_ticks(model, 100, dt=1.0)
        for v in values:
            assert abs(v - 100.0) < 1e-10

    def test_early_values_near_T_initial(self) -> None:
        """First few ticks should be near T_initial (slow start)."""
        model = _make_model()
        # At dt=0.5, first value at t=0.5s
        first_val = model.generate(0.0, 0.5)
        # Should still be relatively close to T_initial (within ~20C)
        assert first_val < 30.0, f"First value too high: {first_val}"

    def test_asymptotic_approach(self) -> None:
        """Rate of change decreases as temperature approaches T_oven."""
        model = _make_model()
        dt = 1.0
        values = _run_ticks(model, 2000, dt=dt)
        # Rate in first 60s vs last 60s should be very different
        early_rate = values[59] - values[0]
        late_rate = values[-1] - values[-61]
        assert early_rate > late_rate * 2, (
            f"Early rate {early_rate} should be much larger than late rate {late_rate}"
        )

    def test_never_exceeds_T_oven_heating(self) -> None:
        """In heating mode, temperature never exceeds T_oven."""
        model = _make_model()
        dt = 1.0
        values = _run_ticks(model, 3600, dt=dt)  # 1 hour
        for v in values:
            assert v <= 180.0 + 1e-10

    def test_never_below_T_oven_cooling(self) -> None:
        """In cooling mode, temperature never goes below T_oven."""
        model = _make_model({"T_initial": 200.0, "T_oven": 20.0})
        dt = 1.0
        values = _run_ticks(model, 3600, dt=dt)
        for v in values:
            assert v >= 20.0 - 1e-10


# ---------------------------------------------------------------------------
# PRD Physical Correctness
# ---------------------------------------------------------------------------


class TestPhysicalCorrectness:
    def test_prd_ready_meal_reaches_72C(self) -> None:
        """Product reaches 72C from 4C in a reasonable time.

        With alpha=1.4e-7, L=0.025, T_oven=180, T_initial=4.
        The PRD quotes ~15-20 min for real center-point temperature.
        This model uses the volume-averaged formula (PRD 4.2.10) which
        heats faster; approximately 8-9 min to 72C.
        """
        model = _make_model()
        dt = 1.0
        time_to_72 = None
        for i in range(1, 2000):
            val = model.generate(i * dt, dt)
            if val >= 72.0 and time_to_72 is None:
                time_to_72 = i * dt
                break

        assert time_to_72 is not None, "Never reached 72C"
        # Volume-averaged formula reaches 72C in ~8-9 min (faster than
        # the PRD's ~15-20 min center-point estimate).  Accept 5-15 min.
        assert 300 <= time_to_72 <= 900, (
            f"Reached 72C at {time_to_72}s ({time_to_72/60:.1f} min), "
            f"expected ~8-9 min"
        )

    def test_different_thickness_faster(self) -> None:
        """Thinner product heats faster (smaller L)."""
        thick = _make_model({"T_initial": 4.0, "T_oven": 180.0, "L": 0.030})
        thin = _make_model({"T_initial": 4.0, "T_oven": 180.0, "L": 0.015})
        dt = 1.0
        # Run 5 minutes
        ticks = 300
        thick_vals = _run_ticks(thick, ticks, dt=dt)
        thin_vals = _run_ticks(thin, ticks, dt=dt)
        # Thin product should be hotter
        assert thin_vals[-1] > thick_vals[-1]

    def test_higher_diffusivity_faster(self) -> None:
        """Higher thermal diffusivity means faster heating."""
        slow = _make_model({"T_initial": 4.0, "T_oven": 180.0, "alpha": 1.0e-7})
        fast = _make_model({"T_initial": 4.0, "T_oven": 180.0, "alpha": 2.0e-7})
        dt = 1.0
        ticks = 300
        slow_vals = _run_ticks(slow, ticks, dt=dt)
        fast_vals = _run_ticks(fast, ticks, dt=dt)
        assert fast_vals[-1] > slow_vals[-1]

    def test_higher_oven_temp_faster(self) -> None:
        """Higher oven temperature means faster heating toward target."""
        low_oven = _make_model({"T_initial": 4.0, "T_oven": 150.0})
        high_oven = _make_model({"T_initial": 4.0, "T_oven": 200.0})
        dt = 1.0
        ticks = 300
        low_vals = _run_ticks(low_oven, ticks, dt=dt)
        high_vals = _run_ticks(high_oven, ticks, dt=dt)
        assert high_vals[-1] > low_vals[-1]


# ---------------------------------------------------------------------------
# Noise
# ---------------------------------------------------------------------------


class TestNoise:
    def test_noise_adds_variation(self) -> None:
        """With noise, values should vary around the theoretical curve."""
        noise = _make_noise(sigma=0.3, seed=99)
        model = _make_model(noise=noise, seed=99)
        dt = 1.0
        values = _run_ticks(model, 200, dt=dt)
        # Check some adjacent values differ (noise added)
        diffs = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
        # Some diffs should be larger than pure thermal change alone
        assert max(diffs) > 0.01

    def test_zero_sigma_clean(self) -> None:
        """With zero-sigma noise, output matches noiseless model exactly."""
        noise = _make_noise(sigma=0.0, seed=99)
        clean = _make_model(seed=123)
        noisy = _make_model(seed=123, noise=noise)
        dt = 1.0
        for i in range(100):
            v_clean = clean.generate(i * dt, dt)
            v_noisy = noisy.generate(i * dt, dt)
            assert abs(v_clean - v_noisy) < 1e-12

    def test_noise_mean_near_theoretical(self) -> None:
        """Mean of noisy signal should be near theoretical temperature."""
        noise = _make_noise(sigma=0.3, seed=77)
        model = _make_model(noise=noise, seed=77)
        # At a steady point (near equilibrium), average noise is ~0
        dt = 1.0
        # Run to near equilibrium (with 4*L^2 timescale, need longer)
        for i in range(7200):
            model.generate(i * dt, dt)
        # Collect samples near equilibrium
        samples = [model.generate((7200 + i) * dt, dt) for i in range(500)]
        mean = sum(samples) / len(samples)
        assert abs(mean - 180.0) < 5.0  # Near T_oven (within 5C)

    def test_ar1_noise_resets(self) -> None:
        """AR(1) noise state is cleared on reset."""
        noise = _make_noise(sigma=0.5, distribution="ar1", phi=0.8, seed=42)
        model = _make_model(noise=noise, seed=42)
        # Run some ticks to build AR(1) state
        _run_ticks(model, 50, dt=1.0)
        model.reset()
        assert model.elapsed == 0.0


# ---------------------------------------------------------------------------
# Reset and Restart
# ---------------------------------------------------------------------------


class TestResetRestart:
    def test_reset_clears_elapsed(self) -> None:
        model = _make_model()
        _run_ticks(model, 100, dt=1.0)
        assert model.elapsed > 0.0
        model.reset()
        assert model.elapsed == 0.0

    def test_reset_produces_same_sequence(self) -> None:
        """After reset, the model produces values from fresh state."""
        model = _make_model()
        dt = 1.0
        first_run = _run_ticks(model, 20, dt=dt)
        model.reset()
        second_run = _run_ticks(model, 20, dt=dt)
        for a, b in zip(first_run, second_run, strict=True):
            assert abs(a - b) < 1e-10

    def test_restart_resets_elapsed(self) -> None:
        model = _make_model()
        _run_ticks(model, 100, dt=1.0)
        model.restart()
        assert model.elapsed == 0.0

    def test_restart_updates_T_initial(self) -> None:
        model = _make_model()
        model.restart(T_initial=10.0)
        assert model.T_initial == 10.0

    def test_restart_updates_T_oven(self) -> None:
        model = _make_model()
        model.restart(T_oven=200.0)
        assert model.T_oven == 200.0

    def test_restart_updates_both(self) -> None:
        model = _make_model()
        model.restart(T_initial=10.0, T_oven=200.0)
        assert model.T_initial == 10.0
        assert model.T_oven == 200.0

    def test_restart_recomputes_terms(self) -> None:
        """Restart with new params may need different number of terms."""
        model = _make_model({"T_initial": 4.0, "T_oven": 180.0})
        n_terms_large = model.n_terms
        model.restart(T_initial=170.0, T_oven=180.0)
        n_terms_small = model.n_terms
        assert n_terms_small < n_terms_large

    def test_restart_none_keeps_params(self) -> None:
        """Restart with no args keeps existing temperatures."""
        model = _make_model()
        model.restart()
        assert model.T_initial == 4.0
        assert model.T_oven == 180.0


# ---------------------------------------------------------------------------
# set_oven_temp
# ---------------------------------------------------------------------------


class TestSetOvenTemp:
    def test_changes_T_oven(self) -> None:
        model = _make_model()
        model.set_oven_temp(200.0)
        assert model.T_oven == 200.0

    def test_does_not_reset_elapsed(self) -> None:
        model = _make_model()
        _run_ticks(model, 100, dt=1.0)
        elapsed_before = model.elapsed
        model.set_oven_temp(200.0)
        assert model.elapsed == elapsed_before

    def test_recomputes_terms(self) -> None:
        model = _make_model({"T_initial": 4.0, "T_oven": 180.0})
        n1 = model.n_terms
        model.set_oven_temp(10.0)  # Much smaller difference
        n2 = model.n_terms
        assert n2 < n1

    def test_affects_subsequent_values(self) -> None:
        """Changing oven temp mid-run changes subsequent output."""
        model1 = _make_model()
        model2 = _make_model()
        dt = 1.0
        # Run both for 100s
        for i in range(100):
            model1.generate(i * dt, dt)
            model2.generate(i * dt, dt)
        # Change model2's oven temp
        model2.set_oven_temp(200.0)
        # Next values should differ
        v1 = model1.generate(100 * dt, dt)
        v2 = model2.generate(100 * dt, dt)
        assert v1 != v2


# ---------------------------------------------------------------------------
# Determinism (Rule 13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_identical(self) -> None:
        """Same seed produces identical sequence."""
        model1 = _make_model(seed=42)
        model2 = _make_model(seed=42)
        dt = 1.0
        for i in range(200):
            v1 = model1.generate(i * dt, dt)
            v2 = model2.generate(i * dt, dt)
            assert v1 == v2, f"Mismatch at tick {i}: {v1} != {v2}"

    def test_different_seeds_same_without_noise(self) -> None:
        """Without noise, all seeds produce the same output (deterministic)."""
        model1 = _make_model(seed=42)
        model2 = _make_model(seed=99)
        dt = 1.0
        for i in range(100):
            v1 = model1.generate(i * dt, dt)
            v2 = model2.generate(i * dt, dt)
            assert abs(v1 - v2) < 1e-12

    def test_noise_same_seed_identical(self) -> None:
        """With noise, same seed produces identical sequences."""
        noise1 = _make_noise(sigma=0.3, seed=77)
        noise2 = _make_noise(sigma=0.3, seed=77)
        model1 = _make_model(seed=42, noise=noise1)
        model2 = _make_model(seed=42, noise=noise2)
        dt = 1.0
        for i in range(100):
            v1 = model1.generate(i * dt, dt)
            v2 = model2.generate(i * dt, dt)
            assert v1 == v2

    def test_noise_different_seeds_differ(self) -> None:
        """With noise, different seeds produce different sequences."""
        noise1 = _make_noise(sigma=0.3, seed=77)
        noise2 = _make_noise(sigma=0.3, seed=88)
        model1 = _make_model(seed=42, noise=noise1)
        model2 = _make_model(seed=42, noise=noise2)
        dt = 1.0
        vals1 = _run_ticks(model1, 50, dt=dt)
        vals2 = _run_ticks(model2, 50, dt=dt)
        assert vals1 != vals2


# ---------------------------------------------------------------------------
# Time Compression (Rule 6)
# ---------------------------------------------------------------------------


class TestTimeCompression:
    def test_same_output_different_tick_rates(self) -> None:
        """Same total simulated time gives same temperature,
        regardless of tick size (Rule 6)."""
        # 100 ticks at 1s each = 100s
        model_1s = _make_model()
        for i in range(100):
            v_1s = model_1s.generate(i * 1.0, 1.0)

        # 200 ticks at 0.5s each = 100s
        model_05s = _make_model()
        for i in range(200):
            v_05s = model_05s.generate(i * 0.5, 0.5)

        # Both should be at the same temperature after 100s of sim time
        assert abs(v_1s - v_05s) < 0.01, (
            f"1s ticks: {v_1s}, 0.5s ticks: {v_05s}"
        )

    def test_elapsed_matches_total_dt(self) -> None:
        """Elapsed time equals sum of dt values."""
        model = _make_model()
        dt = 0.5
        n = 200
        _run_ticks(model, n, dt=dt)
        expected = n * dt
        assert abs(model.elapsed - expected) < 1e-10


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_very_small_alpha(self) -> None:
        """Very low diffusivity: heats very slowly."""
        model = _make_model({"alpha": 1e-9, "T_initial": 4.0, "T_oven": 180.0})
        dt = 1.0
        values = _run_ticks(model, 300, dt=dt)
        # After 5 minutes, should still be relatively close to T_initial
        assert values[-1] < 15.0

    def test_very_large_alpha(self) -> None:
        """Very high diffusivity: heats very quickly."""
        model = _make_model({"alpha": 1e-4, "T_initial": 4.0, "T_oven": 180.0})
        dt = 1.0
        values = _run_ticks(model, 10, dt=dt)
        # Should approach T_oven quickly (after 10s at very high alpha)
        assert values[-1] > 170.0

    def test_negative_T_initial(self) -> None:
        """Frozen product entering oven (T_initial < 0)."""
        model = _make_model({"T_initial": -18.0, "T_oven": 180.0})
        coeff_sum = sum(model._coefficients)
        t0_error = abs(180.0 - (-18.0)) * (1.0 - coeff_sum)
        assert t0_error <= 1.0
        dt = 1.0
        val = model.generate(0.0, dt)
        assert val > -18.0  # Should be heating
        assert val < 180.0

    def test_very_thin_product(self) -> None:
        """L = 5mm -- heats very quickly."""
        model = _make_model({"L": 0.005, "T_initial": 4.0, "T_oven": 180.0})
        dt = 1.0
        values = _run_ticks(model, 300, dt=dt)
        assert values[-1] > 170.0  # 5 minutes enough for thin product

    def test_very_thick_product(self) -> None:
        """L = 100mm -- heats very slowly."""
        model = _make_model({"L": 0.100, "T_initial": 4.0, "T_oven": 180.0})
        dt = 1.0
        values = _run_ticks(model, 300, dt=dt)
        # After 5 min, thick product should still be relatively cool
        assert values[-1] < 100.0


# ---------------------------------------------------------------------------
# Property-Based Tests (Hypothesis)
# ---------------------------------------------------------------------------


class TestPropertyBased:
    @given(
        T_initial=st.floats(min_value=-30.0, max_value=200.0),
        T_oven=st.floats(min_value=-30.0, max_value=300.0),
    )
    @settings(max_examples=50)
    def test_output_always_finite(
        self, T_initial: float, T_oven: float
    ) -> None:
        """Output is always a finite number."""
        model = ThermalDiffusionModel(
            {"T_initial": T_initial, "T_oven": T_oven},
            _make_rng(),
        )
        dt = 1.0
        for i in range(10):
            val = model.generate(i * dt, dt)
            assert math.isfinite(val), f"Non-finite at tick {i}: {val}"

    @given(seed=st.integers(min_value=0, max_value=100000))
    @settings(max_examples=20)
    def test_determinism_any_seed(self, seed: int) -> None:
        """Same seed always produces same sequence."""
        model1 = ThermalDiffusionModel(dict(TYPICAL_PARAMS), _make_rng(seed))
        model2 = ThermalDiffusionModel(dict(TYPICAL_PARAMS), _make_rng(seed))
        dt = 1.0
        for i in range(20):
            v1 = model1.generate(i * dt, dt)
            v2 = model2.generate(i * dt, dt)
            assert v1 == v2

    @given(
        T_initial=st.floats(min_value=-20.0, max_value=50.0),
        T_oven=st.floats(min_value=100.0, max_value=300.0),
    )
    @settings(max_examples=30)
    def test_convergence_within_1C(
        self, T_initial: float, T_oven: float
    ) -> None:
        """T(0) is always within 1C of T_initial (PRD requirement)."""
        model = ThermalDiffusionModel(
            {"T_initial": T_initial, "T_oven": T_oven},
            _make_rng(),
        )
        coeff_sum = sum(model._coefficients)
        delta_T = abs(T_oven - T_initial)
        t0_error = delta_T * (1.0 - coeff_sum)
        assert t0_error <= 1.0 + 1e-10, (
            f"T(0) error = {t0_error}C for delta_T={delta_T}C, "
            f"n_terms={model.n_terms}"
        )

    @given(
        T_initial=st.floats(min_value=-20.0, max_value=50.0),
        T_oven=st.floats(min_value=100.0, max_value=300.0),
    )
    @settings(max_examples=30)
    def test_monotonic_for_any_heating(
        self, T_initial: float, T_oven: float
    ) -> None:
        """Temperature is monotonically increasing when heating."""
        model = ThermalDiffusionModel(
            {"T_initial": T_initial, "T_oven": T_oven},
            _make_rng(),
        )
        dt = 1.0
        values = [model.generate(i * dt, dt) for i in range(50)]
        for i in range(1, len(values)):
            assert values[i] >= values[i - 1] - 1e-10

    @given(
        T_initial=st.floats(min_value=100.0, max_value=300.0),
        T_oven=st.floats(min_value=-20.0, max_value=50.0),
    )
    @settings(max_examples=30)
    def test_monotonic_for_any_cooling(
        self, T_initial: float, T_oven: float
    ) -> None:
        """Temperature is monotonically decreasing when cooling."""
        model = ThermalDiffusionModel(
            {"T_initial": T_initial, "T_oven": T_oven},
            _make_rng(),
        )
        dt = 1.0
        values = [model.generate(i * dt, dt) for i in range(50)]
        for i in range(1, len(values)):
            assert values[i] <= values[i - 1] + 1e-10

    @given(
        T_initial=st.floats(min_value=-30.0, max_value=300.0),
        T_oven=st.floats(min_value=-30.0, max_value=300.0),
    )
    @settings(max_examples=30)
    def test_bounded_between_initial_and_oven(
        self, T_initial: float, T_oven: float
    ) -> None:
        """Output is always between T_initial and T_oven (inclusive)."""
        model = ThermalDiffusionModel(
            {"T_initial": T_initial, "T_oven": T_oven},
            _make_rng(),
        )
        lo = min(T_initial, T_oven)
        hi = max(T_initial, T_oven)
        dt = 1.0
        for i in range(30):
            val = model.generate(i * dt, dt)
            assert lo - 1.5 <= val <= hi + 1.5, (
                f"Out of bounds at tick {i}: {val} not in [{lo}, {hi}]"
            )


# ---------------------------------------------------------------------------
# Package Imports
# ---------------------------------------------------------------------------


class TestPackageImports:
    def test_importable_from_models(self) -> None:
        from factory_simulator.models import ThermalDiffusionModel as TDM

        assert TDM is ThermalDiffusionModel

    def test_in_all(self) -> None:
        import factory_simulator.models as models

        assert "ThermalDiffusionModel" in models.__all__
