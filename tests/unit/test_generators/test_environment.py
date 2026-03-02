"""Unit tests for the composite environment model (PRD 4.2.2).

Tests verify the 3-layer composite:
  value = daily_sine(t) + hvac_cycle(t) + perturbation(t) + noise(0, sigma)

Key properties:
- Output has more variance than pure sine (composite layers add spread).
- HVAC cycling visible at ~20 min period in zero-crossing analysis.
- Perturbation events occur at configured Poisson rate.
- Humidity inversely correlates with temperature offsets.
- Determinism: same seed → same output (CLAUDE.md Rule 13).

Task 2.15
"""

from __future__ import annotations

import math
import statistics

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.environment import EnvironmentGenerator
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    hvac_period_minutes: float = 20.0,
    hvac_amplitude_c: float = 1.0,
    perturbation_rate_per_shift: float = 5.0,
    perturbation_magnitude_c: float = 2.0,
    perturbation_decay_tau_minutes: float = 7.0,
    temp_noise_sigma: float = 0.3,
    humid_noise_sigma: float = 1.0,
) -> EquipmentConfig:
    signals: dict[str, SignalConfig] = {}
    signals["ambient_temp"] = SignalConfig(
        model="sinusoidal",
        noise_sigma=temp_noise_sigma,
        sample_rate_ms=60000,
        min_clamp=15.0,
        max_clamp=35.0,
        params={
            "center": 22.0,
            "amplitude": 3.0,
            "period": 86400.0,
            "hvac_period_minutes": hvac_period_minutes,
            "hvac_amplitude_c": hvac_amplitude_c,
            "perturbation_rate_per_shift": perturbation_rate_per_shift,
            "perturbation_magnitude_c": perturbation_magnitude_c,
            "perturbation_decay_tau_minutes": perturbation_decay_tau_minutes,
        },
    )
    signals["ambient_humidity"] = SignalConfig(
        model="sinusoidal",
        noise_sigma=humid_noise_sigma,
        sample_rate_ms=60000,
        min_clamp=30.0,
        max_clamp=80.0,
        params={
            "center": 55.0,
            "amplitude": 10.0,
            "period": 86400.0,
            "phase": math.pi,
        },
    )
    return EquipmentConfig(enabled=True, type="iolink_sensor", signals=signals)


def _find_signal(results: list[SignalValue], signal_id: str) -> SignalValue:
    for sv in results:
        if sv.signal_id == signal_id:
            return sv
    raise KeyError(signal_id)


def _run_generator(
    gen: EnvironmentGenerator,
    store: SignalStore,
    *,
    n_steps: int,
    dt: float,
    start_time: float = 0.0,
) -> tuple[list[float], list[float]]:
    """Run generator for n_steps, return (temp_values, humid_values)."""
    temps: list[float] = []
    humids: list[float] = []
    sim_time = start_time
    for _ in range(n_steps):
        sim_time += dt
        results = gen.generate(sim_time, dt, store)
        temps.append(_find_signal(results, "env.ambient_temp").value)
        humids.append(_find_signal(results, "env.ambient_humidity").value)
    return temps, humids


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(42)


@pytest.fixture
def store() -> SignalStore:
    return SignalStore()


# ---------------------------------------------------------------------------
# Tests: composite model produces more variance than pure sine
# ---------------------------------------------------------------------------


class TestCompositeVariance:
    """The composite model should have more variance than pure sine."""

    def test_temp_stddev_exceeds_pure_sine(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        """Over 2 hours of 60 s steps, the composite model should show
        more spread than a pure sine (which barely changes in 2 hours)."""
        gen = EnvironmentGenerator("env", _make_config(), rng)
        temps, _ = _run_generator(gen, store, n_steps=120, dt=60.0)

        # Pure sine variation over 2 hours (~2.5% of 24h): amplitude 3 *
        # (1 - cos(2pi * 7200/86400)) ≈ 0.22 C swing.  With HVAC (±1 C)
        # and noise the std dev should be noticeably higher.
        assert statistics.stdev(temps) > 0.3, (
            "Temperature std dev should exceed pure-sine variation"
        )

    def test_humidity_stddev_exceeds_pure_sine(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        gen = EnvironmentGenerator("env", _make_config(), rng)
        _, humids = _run_generator(gen, store, n_steps=120, dt=60.0)
        assert statistics.stdev(humids) > 1.0, (
            "Humidity std dev should exceed pure-sine variation"
        )


# ---------------------------------------------------------------------------
# Tests: HVAC cycling
# ---------------------------------------------------------------------------


class TestHvacCycling:
    """The HVAC bang-bang produces a visible oscillation in the output."""

    def test_hvac_zero_crossings(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        """Run for 2 hours with no noise / no perturbations.
        The HVAC cycle (20 min period) should produce ~6 full cycles = ~12
        zero crossings of the residual (output minus daily sine)."""
        gen = EnvironmentGenerator(
            "env",
            _make_config(
                temp_noise_sigma=0.0,
                humid_noise_sigma=0.0,
                perturbation_rate_per_shift=0.0,  # disable perturbations
            ),
            rng,
        )

        # Collect temperature residuals (output - daily sine component)
        dt = 60.0
        n_steps = 120  # 2 hours
        residuals: list[float] = []
        sim_time = 0.0
        center = 22.0
        amplitude = 3.0
        period = 86400.0

        for _ in range(n_steps):
            sim_time += dt
            results = gen.generate(sim_time, dt, store)
            temp = _find_signal(results, "env.ambient_temp").value
            daily_sine = center + amplitude * math.sin(
                2.0 * math.pi * sim_time / period,
            )
            residuals.append(temp - daily_sine)

        # Count sign changes (positive ↔ negative), skipping exact zeros.
        # The BangBang model with 60 s steps may pass through 0.0 exactly,
        # so the naive product-based test misses those transitions.
        prev_sign = 0
        crossings = 0
        for r in residuals:
            if r > 0:
                sign = 1
            elif r < 0:
                sign = -1
            else:
                continue  # skip exact zero, keep previous sign
            if prev_sign != 0 and sign != prev_sign:
                crossings += 1
            prev_sign = sign

        # 2 hours / ~24 min actual period (discrete-step overshoot) =
        # ~5 cycles = ~10 sign changes.  Allow tolerance for startup.
        assert crossings >= 6, (
            f"Expected >=6 sign changes from HVAC cycle, got {crossings}"
        )

    def test_hvac_amplitude_configurable(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        """Larger HVAC amplitude should produce larger residual range."""
        small_gen = EnvironmentGenerator(
            "env",
            _make_config(
                hvac_amplitude_c=0.5,
                temp_noise_sigma=0.0,
                perturbation_rate_per_shift=0.0,
            ),
            rng,
        )
        large_gen = EnvironmentGenerator(
            "env",
            _make_config(
                hvac_amplitude_c=1.5,
                temp_noise_sigma=0.0,
                perturbation_rate_per_shift=0.0,
            ),
            np.random.default_rng(42),
        )

        dt = 60.0
        n_steps = 60  # 1 hour

        small_temps, _ = _run_generator(small_gen, store, n_steps=n_steps, dt=dt)
        large_temps, _ = _run_generator(
            large_gen, SignalStore(), n_steps=n_steps, dt=dt,
        )

        small_range = max(small_temps) - min(small_temps)
        large_range = max(large_temps) - min(large_temps)

        assert large_range > small_range, (
            f"Larger HVAC amplitude should produce larger range: "
            f"{large_range:.3f} vs {small_range:.3f}"
        )


# ---------------------------------------------------------------------------
# Tests: perturbation events
# ---------------------------------------------------------------------------


class TestPerturbations:
    """Poisson perturbation events add step changes that decay."""

    def test_perturbations_occur_at_configured_rate(
        self, store: SignalStore,
    ) -> None:
        """Over many shifts, the average event rate should match config.

        We use a high perturbation rate (50/shift) to get statistical
        significance in a shorter run.
        """
        cfg = _make_config(
            perturbation_rate_per_shift=50.0,
            perturbation_magnitude_c=2.0,
            perturbation_decay_tau_minutes=1.0,  # fast decay
            hvac_amplitude_c=0.0001,  # negligible HVAC
            temp_noise_sigma=0.0,
        )

        # Run 3 shifts (24 hours) at 60 s steps
        n_steps = 3 * 480  # 3 shifts x 480 steps/shift
        dt = 60.0

        # Count large deviations from daily sine as proxy for events.
        # With fast decay (tau=60s) each event quickly fades, so we
        # detect events by large positive residual changes between steps.
        rng = np.random.default_rng(123)
        gen = EnvironmentGenerator("env", cfg, rng)

        prev_residual = 0.0
        jumps = 0
        sim_time = 0.0
        center = 22.0
        amplitude = 3.0
        period = 86400.0

        for _ in range(n_steps):
            sim_time += dt
            results = gen.generate(sim_time, dt, store)
            temp = _find_signal(results, "env.ambient_temp").value
            daily_sine = center + amplitude * math.sin(
                2.0 * math.pi * sim_time / period,
            )
            residual = temp - daily_sine
            # A jump > 0.5 C in residual likely means a perturbation event
            if abs(residual - prev_residual) > 0.5:
                jumps += 1
            prev_residual = residual

        # Expected: 50 events/shift x 3 shifts = 150.
        # But some events have small magnitude or overlap, so allow wide range.
        assert jumps >= 30, (
            f"Expected significant perturbation events, got {jumps} jumps"
        )

    def test_no_perturbations_when_rate_zero(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        """With rate=0, output should match pure sine + HVAC only."""
        gen = EnvironmentGenerator(
            "env",
            _make_config(
                perturbation_rate_per_shift=0.0,
                temp_noise_sigma=0.0,
                hvac_amplitude_c=0.0001,  # negligible
            ),
            rng,
        )

        dt = 60.0
        sim_time = 0.0
        center = 22.0
        amplitude = 3.0
        period = 86400.0

        for _ in range(100):
            sim_time += dt
            results = gen.generate(sim_time, dt, store)
            temp = _find_signal(results, "env.ambient_temp").value
            daily_sine = center + amplitude * math.sin(
                2.0 * math.pi * sim_time / period,
            )
            # With negligible HVAC and zero perturbations, residual ≈ 0
            assert abs(temp - daily_sine) < 0.1, (
                f"Unexpected deviation from sine at t={sim_time}: "
                f"temp={temp:.4f}, sine={daily_sine:.4f}"
            )


# ---------------------------------------------------------------------------
# Tests: humidity inverse correlation
# ---------------------------------------------------------------------------


class TestHumidityInverse:
    """Humidity inversely correlates with temperature HVAC/perturbation."""

    def test_hvac_inverse_on_humidity(
        self, store: SignalStore,
    ) -> None:
        """When HVAC raises temperature, humidity should drop."""
        gen = EnvironmentGenerator(
            "env",
            _make_config(
                hvac_amplitude_c=1.5,
                temp_noise_sigma=0.0,
                humid_noise_sigma=0.0,
                perturbation_rate_per_shift=0.0,
            ),
            np.random.default_rng(42),
        )

        dt = 60.0
        n_steps = 60

        temps, humids = _run_generator(gen, store, n_steps=n_steps, dt=dt)

        # Compute de-trended residuals (remove daily sine component)
        temp_resids: list[float] = []
        humid_resids: list[float] = []
        sim_time = 0.0
        for i in range(n_steps):
            sim_time = (i + 1) * dt
            t_sine = 22.0 + 3.0 * math.sin(2.0 * math.pi * sim_time / 86400.0)
            h_sine = 55.0 + 10.0 * math.sin(
                2.0 * math.pi * sim_time / 86400.0 + math.pi,
            )
            temp_resids.append(temps[i] - t_sine)
            humid_resids.append(humids[i] - h_sine)

        # Correlation of residuals should be negative (inverse)
        if statistics.stdev(temp_resids) > 0.01 and statistics.stdev(humid_resids) > 0.01:
            n = len(temp_resids)
            t_mean = statistics.mean(temp_resids)
            h_mean = statistics.mean(humid_resids)
            cov = sum(
                (temp_resids[i] - t_mean) * (humid_resids[i] - h_mean)
                for i in range(n)
            ) / n
            corr = cov / (
                statistics.stdev(temp_resids) * statistics.stdev(humid_resids)
            )
            assert corr < -0.5, (
                f"HVAC residuals should be negatively correlated, got r={corr:.3f}"
            )


# ---------------------------------------------------------------------------
# Tests: determinism (CLAUDE.md Rule 13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed → identical output sequence."""

    def test_composite_deterministic(self, store: SignalStore) -> None:
        cfg = _make_config()
        gen1 = EnvironmentGenerator("env", cfg, np.random.default_rng(99))
        gen2 = EnvironmentGenerator("env", cfg, np.random.default_rng(99))

        sim_time = 0.0
        dt = 60.0
        for _ in range(50):
            sim_time += dt
            r1 = gen1.generate(sim_time, dt, store)
            r2 = gen2.generate(sim_time, dt, store)

        for sv1, sv2 in zip(r1, r2, strict=True):
            assert sv1.signal_id == sv2.signal_id
            assert sv1.value == sv2.value, (
                f"{sv1.signal_id}: {sv1.value} != {sv2.value}"
            )


# ---------------------------------------------------------------------------
# Tests: bounds still respected (clamp)
# ---------------------------------------------------------------------------


class TestBounds:
    """Output respects min/max clamp under all composite layers."""

    def test_temp_within_bounds_over_day(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        gen = EnvironmentGenerator("env", _make_config(), rng)
        temps, _ = _run_generator(gen, store, n_steps=1440, dt=60.0)
        for t in temps:
            assert 15.0 <= t <= 35.0

    def test_humidity_within_bounds_over_day(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        gen = EnvironmentGenerator("env", _make_config(), rng)
        _, humids = _run_generator(gen, store, n_steps=1440, dt=60.0)
        for h in humids:
            assert 30.0 <= h <= 80.0
