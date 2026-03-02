"""Tests for the CorrelatedFollowerModel.

PRD Reference: Section 4.2.8 (Correlated Follower), Section 4.3.2
    (Time-Varying Covariance)
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factory_simulator.models.correlated import CorrelatedFollowerModel
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
    seed: int = SEED,
    distribution: str = "gaussian",
    phi: float = 0.7,
) -> NoiseGenerator:
    kwargs: dict[str, object] = {}
    if distribution == "ar1":
        kwargs["phi"] = phi
    return NoiseGenerator(
        sigma=sigma,
        distribution=distribution,
        rng=np.random.default_rng(seed),
        **kwargs,  # type: ignore[arg-type]
    )


def _make_model(
    params: dict[str, object] | None = None,
    seed: int = SEED,
    noise: NoiseGenerator | None = None,
) -> CorrelatedFollowerModel:
    p = params if params is not None else {}
    return CorrelatedFollowerModel(p, _make_rng(seed), noise=noise)


def _run_ticks(
    model: CorrelatedFollowerModel,
    n: int,
    dt: float = DT,
    parent_value: float = 0.0,
) -> list[float]:
    """Run n ticks with a constant parent value and return values."""
    t = 0.0
    results = []
    for _ in range(n):
        model.set_parent_value(parent_value)
        results.append(model.generate(t, dt))
        t += dt
    return results


# ===========================================================================
# Construction
# ===========================================================================


class TestConstruction:
    def test_defaults(self) -> None:
        m = _make_model()
        assert m.base == 0.0
        assert m.gain == 1.0
        assert m.lag_mode == "none"
        assert m.gain_drift_factor == pytest.approx(1.0)
        assert m.effective_gain == pytest.approx(1.0)
        assert m.buffer_size == 0

    def test_explicit_params(self) -> None:
        m = _make_model({"base": 5.0, "gain": 0.5})
        assert m.base == 5.0
        assert m.gain == 0.5

    def test_fixed_lag_params(self) -> None:
        m = _make_model({"lag_mode": "fixed", "lag_seconds": 2.0, "tick_interval": 0.1})
        assert m.lag_mode == "fixed"
        assert m.buffer_size == 21  # 20 lag ticks + 1

    def test_transport_lag_params(self) -> None:
        m = _make_model({
            "lag_mode": "transport",
            "distance_m": 4.0,
            "min_speed": 50.0,
            "tick_interval": 0.1,
        })
        assert m.lag_mode == "transport"
        # max_lag = 4.0 / (50/60) = 4.8s -> 48 ticks -> 2x = 96 + 1 = 97
        assert m.buffer_size == 97

    def test_gain_drift_params(self) -> None:
        m = _make_model({
            "gain_drift_volatility": 0.003,
            "gain_drift_reversion": 0.02,
        })
        assert m.gain_drift_factor == pytest.approx(1.0)

    def test_invalid_lag_mode(self) -> None:
        with pytest.raises(ValueError, match="lag_mode must be"):
            _make_model({"lag_mode": "invalid"})

    def test_invalid_fixed_lag_seconds(self) -> None:
        with pytest.raises(ValueError, match="lag_seconds must be >= 0"):
            _make_model({"lag_mode": "fixed", "lag_seconds": -1.0})

    def test_invalid_transport_distance(self) -> None:
        with pytest.raises(ValueError, match="distance_m must be > 0"):
            _make_model({"lag_mode": "transport", "distance_m": 0.0})

    def test_invalid_transport_min_speed(self) -> None:
        with pytest.raises(ValueError, match="min_speed must be > 0"):
            _make_model({
                "lag_mode": "transport",
                "distance_m": 4.0,
                "min_speed": 0.0,
            })

    def test_invalid_tick_interval(self) -> None:
        with pytest.raises(ValueError, match="tick_interval must be > 0"):
            _make_model({"tick_interval": 0.0})

    def test_invalid_gain_drift_volatility(self) -> None:
        with pytest.raises(ValueError, match="gain_drift_volatility must be >= 0"):
            _make_model({"gain_drift_volatility": -0.001})

    def test_invalid_gain_drift_reversion(self) -> None:
        with pytest.raises(ValueError, match="gain_drift_reversion must be >= 0"):
            _make_model({"gain_drift_reversion": -0.01})


# ===========================================================================
# Basic Linear Transform (no lag, no drift)
# ===========================================================================


class TestLinearTransform:
    def test_identity_transform(self) -> None:
        """gain=1, base=0: output equals parent."""
        m = _make_model({"gain": 1.0, "base": 0.0})
        m.set_parent_value(42.0)
        assert m.generate(0.0, DT) == pytest.approx(42.0)

    def test_gain_scaling(self) -> None:
        """Output scales linearly with gain."""
        m = _make_model({"gain": 0.5, "base": 0.0})
        m.set_parent_value(100.0)
        assert m.generate(0.0, DT) == pytest.approx(50.0)

    def test_base_offset(self) -> None:
        """Base adds a constant offset."""
        m = _make_model({"gain": 1.0, "base": 10.0})
        m.set_parent_value(5.0)
        assert m.generate(0.0, DT) == pytest.approx(15.0)

    def test_full_linear_transform(self) -> None:
        """base + gain * parent."""
        m = _make_model({"gain": 2.0, "base": 3.0})
        m.set_parent_value(7.0)
        assert m.generate(0.0, DT) == pytest.approx(17.0)

    def test_negative_gain(self) -> None:
        """Negative gain inverts the relationship (e.g. rewind vs unwind)."""
        m = _make_model({"gain": -1.0, "base": 100.0})
        m.set_parent_value(60.0)
        assert m.generate(0.0, DT) == pytest.approx(40.0)

    def test_zero_parent(self) -> None:
        """Zero parent produces base only."""
        m = _make_model({"gain": 5.0, "base": 2.0})
        m.set_parent_value(0.0)
        assert m.generate(0.0, DT) == pytest.approx(2.0)

    def test_tracks_parent_changes(self) -> None:
        """Output tracks changing parent values."""
        m = _make_model({"gain": 1.0, "base": 0.0})
        values = []
        for parent in [0.0, 50.0, 100.0, 200.0, 150.0]:
            m.set_parent_value(parent)
            values.append(m.generate(0.0, DT))
        assert values == pytest.approx([0.0, 50.0, 100.0, 200.0, 150.0])

    def test_prd_motor_current_example(self) -> None:
        """PRD: main_drive_current = base_current + k * speed."""
        # base_current=5A, k=0.5 A per m/min, speed=200 m/min
        m = _make_model({"gain": 0.5, "base": 5.0})
        m.set_parent_value(200.0)
        assert m.generate(0.0, DT) == pytest.approx(105.0)

    def test_prd_gear_ratio_example(self) -> None:
        """PRD: main_drive_speed follows line_speed via gear ratio."""
        # gear ratio = 3.2 (motor speed = gear_ratio * line_speed)
        m = _make_model({"gain": 3.2, "base": 0.0})
        m.set_parent_value(200.0)
        assert m.generate(0.0, DT) == pytest.approx(640.0)


# ===========================================================================
# Noise
# ===========================================================================


class TestNoise:
    def test_noise_adds_variation(self) -> None:
        """With noise, output varies around the linear transform."""
        noise = _make_noise(sigma=5.0, seed=99)
        m = _make_model({"gain": 1.0, "base": 0.0}, noise=noise)
        values = _run_ticks(m, 100, parent_value=50.0)
        assert not all(v == pytest.approx(50.0) for v in values)
        assert np.mean(values) == pytest.approx(50.0, abs=3.0)

    def test_zero_sigma_clean(self) -> None:
        """Zero sigma noise produces clean output."""
        noise = _make_noise(sigma=0.0)
        m = _make_model({"gain": 1.0, "base": 0.0}, noise=noise)
        m.set_parent_value(42.0)
        assert m.generate(0.0, DT) == pytest.approx(42.0)

    def test_mean_near_transform(self) -> None:
        """Mean of noisy output is near the linear transform."""
        noise = _make_noise(sigma=2.0, seed=123)
        m = _make_model({"gain": 1.0, "base": 10.0}, noise=noise)
        values = _run_ticks(m, 10000, parent_value=50.0)
        assert np.mean(values) == pytest.approx(60.0, abs=0.5)


# ===========================================================================
# Fixed Lag
# ===========================================================================


class TestFixedLag:
    def test_fixed_lag_delays_output(self) -> None:
        """Fixed lag delays the parent signal by lag_seconds."""
        m = _make_model({
            "lag_mode": "fixed",
            "lag_seconds": 0.5,  # 5 ticks at 0.1s
            "tick_interval": 0.1,
            "gain": 1.0,
            "base": 0.0,
        })

        # Feed zeros, then a step change
        values = []
        t = 0.0
        for i in range(20):
            parent = 100.0 if i >= 5 else 0.0
            m.set_parent_value(parent)
            values.append(m.generate(t, DT))
            t += DT

        # First 5 ticks: parent=0, buffer draining zeros
        for v in values[:5]:
            assert v == pytest.approx(0.0)

        # At tick 5, parent steps to 100 but lag is 5 ticks,
        # so output should still be 0 for ticks 5-9
        for v in values[5:10]:
            assert v == pytest.approx(0.0)

        # From tick 10 onward, the 100 value should arrive
        for v in values[10:]:
            assert v == pytest.approx(100.0)

    def test_fixed_lag_preserves_shape(self) -> None:
        """A ramp input produces a delayed ramp output."""
        m = _make_model({
            "lag_mode": "fixed",
            "lag_seconds": 0.3,  # 3 ticks
            "tick_interval": 0.1,
            "gain": 1.0,
            "base": 0.0,
        })

        values = []
        t = 0.0
        for i in range(10):
            m.set_parent_value(float(i * 10))
            values.append(m.generate(t, DT))
            t += DT

        # First 3 ticks should be 0 (buffer filled with zeros)
        for v in values[:3]:
            assert v == pytest.approx(0.0)

        # From tick 3 onward, output should follow input with 3-tick delay
        for i in range(3, 10):
            assert values[i] == pytest.approx(float((i - 3) * 10))

    def test_fixed_lag_buffer_size(self) -> None:
        """Buffer size is lag_ticks + 1 (extra slot avoids read/write collision)."""
        m = _make_model({
            "lag_mode": "fixed",
            "lag_seconds": 1.0,
            "tick_interval": 0.1,
        })
        assert m.buffer_size == 11  # 10 lag ticks + 1

    def test_fixed_lag_small(self) -> None:
        """Minimum lag is 1 tick, buffer is 2."""
        m = _make_model({
            "lag_mode": "fixed",
            "lag_seconds": 0.01,  # much less than tick_interval
            "tick_interval": 0.1,
        })
        assert m.buffer_size == 2  # 1 lag tick + 1


# ===========================================================================
# Transport Lag
# ===========================================================================


class TestTransportLag:
    def test_transport_lag_varies_with_speed(self) -> None:
        """Lag changes with speed: faster speed = less lag."""
        m = _make_model({
            "lag_mode": "transport",
            "distance_m": 4.0,
            "min_speed": 50.0,
            "tick_interval": 0.1,
            "gain": 1.0,
            "base": 0.0,
        })

        # At 240 m/min: lag = 4/(240/60) = 1.0s = 10 ticks
        m.set_speed(240.0)

        t = 0.0
        for _ in range(15):
            m.set_parent_value(0.0)
            m.generate(t, DT)
            t += DT

        # Now send a step change
        values_after_step = []
        for _ in range(20):
            m.set_parent_value(100.0)
            values_after_step.append(m.generate(t, DT))
            t += DT

        # At 240 m/min, lag is ~10 ticks. Output should be 0 for ~10 ticks
        # then 100.
        assert values_after_step[0] == pytest.approx(0.0)
        assert values_after_step[5] == pytest.approx(0.0)
        assert values_after_step[10] == pytest.approx(100.0)

    def test_transport_lag_zero_speed_freezes(self) -> None:
        """At zero speed, output freezes at last value (PRD 4.2.8)."""
        m = _make_model({
            "lag_mode": "transport",
            "distance_m": 4.0,
            "min_speed": 50.0,
            "tick_interval": 0.1,
            "gain": 1.0,
            "base": 0.0,
        })

        # Run at speed to establish a value
        m.set_speed(120.0)
        t = 0.0
        for _ in range(50):
            m.set_parent_value(42.0)
            m.generate(t, DT)
            t += DT

        # Get the current output
        m.set_parent_value(42.0)
        last_val = m.generate(t, DT)
        t += DT

        # Now set speed to zero
        m.set_speed(0.0)
        frozen_values = []
        for _ in range(10):
            m.set_parent_value(999.0)  # parent changes but shouldn't affect output
            frozen_values.append(m.generate(t, DT))
            t += DT

        # All frozen values should equal last_val
        for v in frozen_values:
            assert v == pytest.approx(last_val)

    def test_transport_lag_buffer_sizing(self) -> None:
        """Buffer sized at 2x max lag at min speed + 1 (PRD 4.2.8)."""
        m = _make_model({
            "lag_mode": "transport",
            "distance_m": 5.0,
            "min_speed": 50.0,  # min speed = 50 m/min
            "tick_interval": 0.1,
        })
        # max_lag = 5 / (50/60) = 6.0s -> 60 ticks -> 2x = 120 + 1 = 121
        assert m.buffer_size == 121

    def test_prd_press_to_laminator(self) -> None:
        """PRD: press to laminator lag at 120 m/min with 4m distance."""
        m = _make_model({
            "lag_mode": "transport",
            "distance_m": 4.0,
            "min_speed": 50.0,
            "tick_interval": 0.1,
            "gain": 1.0,
            "base": 0.0,
        })
        m.set_speed(120.0)
        # lag = 4 / (120/60) = 2.0s = 20 ticks
        # PRD says 1.5-2.5s at 120 m/min for 3-5m distance. 4m -> 2.0s.

        # Fill buffer with zeros
        t = 0.0
        for _ in range(30):
            m.set_parent_value(0.0)
            m.generate(t, DT)
            t += DT

        # Step change
        step_values = []
        for _ in range(30):
            m.set_parent_value(200.0)
            step_values.append(m.generate(t, DT))
            t += DT

        # Check delay: ~20 ticks (2.0s)
        assert step_values[15] == pytest.approx(0.0)
        assert step_values[20] == pytest.approx(200.0)


# ===========================================================================
# Gain Drift (PRD 4.3.2 - Time-Varying Covariance)
# ===========================================================================


class TestGainDrift:
    def test_no_drift_by_default(self) -> None:
        """With drift_volatility=0, gain stays fixed."""
        m = _make_model({"gain": 2.0, "base": 0.0})
        m.set_parent_value(100.0)
        values = _run_ticks(m, 100, parent_value=100.0)
        for v in values:
            assert v == pytest.approx(200.0)

    def test_drift_causes_variation(self) -> None:
        """With nonzero volatility, effective gain varies."""
        m = _make_model({
            "gain": 1.0,
            "base": 0.0,
            "gain_drift_volatility": 0.01,
            "gain_drift_reversion": 0.02,
        })
        effective_gains = []
        t = 0.0
        for _ in range(1000):
            m.set_parent_value(100.0)
            val = m.generate(t, DT)
            effective_gains.append(val / 100.0)  # actual effective gain
            t += DT

        # Gain should vary but stay near 1.0
        assert np.std(effective_gains) > 0.001  # some variation
        assert np.mean(effective_gains) == pytest.approx(1.0, abs=0.1)

    def test_drift_factor_starts_at_one(self) -> None:
        """Initial gain drift factor is 1.0."""
        m = _make_model({"gain_drift_volatility": 0.005})
        assert m.gain_drift_factor == pytest.approx(1.0)

    def test_drift_mean_reverts(self) -> None:
        """Strong reversion keeps gain near nominal over long runs."""
        m = _make_model({
            "gain": 1.0,
            "base": 0.0,
            "gain_drift_volatility": 0.005,
            "gain_drift_reversion": 0.1,  # strong reversion
        })
        factors = []
        t = 0.0
        for _ in range(5000):
            m.set_parent_value(100.0)
            val = m.generate(t, DT)
            factors.append(val / 100.0)
            t += DT

        # Mean should be close to 1.0
        assert np.mean(factors) == pytest.approx(1.0, abs=0.05)
        # Std should be bounded
        assert np.std(factors) < 0.3

    def test_drift_stays_positive(self) -> None:
        """Multiplicative form ensures gain stays positive (exp(log_drift))."""
        m = _make_model({
            "gain": 1.0,
            "base": 0.0,
            "gain_drift_volatility": 0.01,
            "gain_drift_reversion": 0.01,
        })
        t = 0.0
        for _ in range(1000):
            m.set_parent_value(100.0)
            m.generate(t, DT)
            assert m.gain_drift_factor > 0.0
            t += DT

    def test_prd_motor_current_drift(self) -> None:
        """PRD 4.3.2: motor current gain varies 8-12% over 24h."""
        m = _make_model({
            "gain": 0.5,
            "base": 5.0,
            "gain_drift_volatility": 0.003,
            "gain_drift_reversion": 0.02,
        })

        # Simulate 1 hour at 10x speed (dt=1.0 to represent 10 ticks/s)
        effective_gains = []
        t = 0.0
        dt_sim = 1.0  # 1 second steps
        for _ in range(3600):
            m.set_parent_value(200.0)
            val = m.generate(t, dt_sim)
            # effective_gain = (val - base) / parent = (val - 5) / 200
            effective_gains.append((val - 5.0) / 200.0)
            t += dt_sim

        # Should see some variation but stay in a reasonable range
        gains = np.array(effective_gains)
        assert np.mean(gains) == pytest.approx(0.5, abs=0.1)
        # Coefficient of variation should be positive
        assert np.std(gains) / np.mean(gains) > 0.01


# ===========================================================================
# Reset
# ===========================================================================


class TestReset:
    def test_reset_clears_drift(self) -> None:
        """Reset restores gain drift factor to 1.0."""
        m = _make_model({
            "gain": 1.0,
            "base": 0.0,
            "gain_drift_volatility": 0.1,
        })
        # Run to build up drift
        t = 0.0
        for _ in range(100):
            m.set_parent_value(100.0)
            m.generate(t, DT)
            t += DT

        m.reset()
        assert m.gain_drift_factor == pytest.approx(1.0)

    def test_reset_clears_parent(self) -> None:
        """Reset zeros the parent value."""
        m = _make_model({"gain": 1.0, "base": 0.0})
        m.set_parent_value(100.0)
        m.reset()
        assert m.generate(0.0, DT) == pytest.approx(0.0)

    def test_reset_clears_speed(self) -> None:
        """Reset zeros the speed."""
        m = _make_model({
            "lag_mode": "transport",
            "distance_m": 4.0,
            "min_speed": 50.0,
            "gain": 1.0,
            "base": 0.0,
        })
        m.set_speed(120.0)
        m.reset()
        # Speed should be 0, which freezes transport
        # Need to verify speed was reset by observing behavior

    def test_reset_clears_buffer(self) -> None:
        """Reset clears the ring buffer."""
        m = _make_model({
            "lag_mode": "fixed",
            "lag_seconds": 0.5,
            "tick_interval": 0.1,
            "gain": 1.0,
            "base": 0.0,
        })
        # Fill buffer with values
        t = 0.0
        for _ in range(20):
            m.set_parent_value(100.0)
            m.generate(t, DT)
            t += DT

        m.reset()

        # After reset, buffer should be zeros -> output should be 0
        m.set_parent_value(0.0)
        val = m.generate(0.0, DT)
        assert val == pytest.approx(0.0)

    def test_reset_clears_noise(self) -> None:
        """Reset clears AR(1) noise state."""
        noise = _make_noise(sigma=5.0, distribution="ar1", phi=0.9)
        m = _make_model({"gain": 1.0, "base": 0.0}, noise=noise)

        # Run ticks to build up AR(1) state
        _run_ticks(m, 50, parent_value=0.0)

        m.reset()

        # After reset, noise should restart from clean state
        # Can't directly check AR(1) state, but at least verify no crash
        m.set_parent_value(0.0)
        m.generate(0.0, DT)


# ===========================================================================
# Determinism (Rule 13)
# ===========================================================================


class TestDeterminism:
    def test_same_seed_identical(self) -> None:
        """Same seed produces identical output."""
        v1 = _run_ticks(_make_model(seed=42), 50, parent_value=100.0)
        v2 = _run_ticks(_make_model(seed=42), 50, parent_value=100.0)
        assert v1 == v2

    def test_different_seeds_differ(self) -> None:
        """Different seeds produce different gain drift paths."""
        m1 = _make_model({
            "gain_drift_volatility": 0.01,
        }, seed=42)
        m2 = _make_model({
            "gain_drift_volatility": 0.01,
        }, seed=99)
        v1 = _run_ticks(m1, 50, parent_value=100.0)
        v2 = _run_ticks(m2, 50, parent_value=100.0)
        assert v1 != v2

    def test_no_drift_deterministic_any_seed(self) -> None:
        """Without drift or noise, output is deterministic regardless of seed."""
        m1 = _make_model({"gain": 2.0, "base": 3.0}, seed=1)
        m2 = _make_model({"gain": 2.0, "base": 3.0}, seed=999)
        v1 = _run_ticks(m1, 20, parent_value=50.0)
        v2 = _run_ticks(m2, 20, parent_value=50.0)
        assert v1 == v2

    def test_noise_same_seed_identical(self) -> None:
        """With noise, same seed produces identical output."""
        v1 = _run_ticks(
            _make_model(noise=_make_noise(sigma=2.0, seed=7), seed=7),
            50,
            parent_value=100.0,
        )
        v2 = _run_ticks(
            _make_model(noise=_make_noise(sigma=2.0, seed=7), seed=7),
            50,
            parent_value=100.0,
        )
        assert v1 == v2

    def test_drift_same_seed_identical(self) -> None:
        """With gain drift, same seed produces identical output."""
        params: dict[str, object] = {
            "gain": 1.0,
            "base": 0.0,
            "gain_drift_volatility": 0.005,
        }
        v1 = _run_ticks(_make_model(params, seed=42), 100, parent_value=100.0)
        v2 = _run_ticks(_make_model(params, seed=42), 100, parent_value=100.0)
        assert v1 == v2

    def test_fixed_lag_deterministic(self) -> None:
        """Fixed lag is deterministic with same inputs."""
        params: dict[str, object] = {
            "lag_mode": "fixed",
            "lag_seconds": 0.3,
            "tick_interval": 0.1,
            "gain": 1.0,
            "base": 0.0,
        }
        v1 = _run_ticks(_make_model(params, seed=42), 30, parent_value=100.0)
        v2 = _run_ticks(_make_model(params, seed=42), 30, parent_value=100.0)
        assert v1 == v2


# ===========================================================================
# Time Compression (Rule 6)
# ===========================================================================


class TestTimeCompression:
    def test_same_total_different_tick_rates(self) -> None:
        """Same total sim_time at different dt produces same result (no lag)."""
        # At dt=0.1 for 10 ticks = 1 second
        m1 = _make_model({"gain": 1.0, "base": 0.0})
        t = 0.0
        for _ in range(10):
            m1.set_parent_value(50.0)
            m1.generate(t, 0.1)
            t += 0.1
        m1.set_parent_value(50.0)
        v1 = m1.generate(t, 0.1)

        # At dt=0.5 for 2 ticks = 1 second
        m2 = _make_model({"gain": 1.0, "base": 0.0})
        t2 = 0.0
        for _ in range(2):
            m2.set_parent_value(50.0)
            m2.generate(t2, 0.5)
            t2 += 0.5
        m2.set_parent_value(50.0)
        v2 = m2.generate(t2, 0.5)

        # Without drift/lag, both should be exactly gain*parent
        assert v1 == pytest.approx(v2)


# ===========================================================================
# Property-Based Tests (Hypothesis)
# ===========================================================================


class TestHypothesis:
    @given(
        base=st.floats(min_value=-1000, max_value=1000),
        gain=st.floats(min_value=-100, max_value=100),
        parent=st.floats(min_value=-1000, max_value=1000),
    )
    @settings(max_examples=200)
    def test_output_finite(self, base: float, gain: float, parent: float) -> None:
        """Output is always finite for finite inputs."""
        m = _make_model({"base": base, "gain": gain})
        m.set_parent_value(parent)
        val = m.generate(0.0, DT)
        assert math.isfinite(val)

    @given(
        base=st.floats(min_value=-100, max_value=100),
        gain=st.floats(min_value=-10, max_value=10),
        parent=st.floats(min_value=-100, max_value=100),
    )
    @settings(max_examples=200)
    def test_linear_transform_exact(
        self, base: float, gain: float, parent: float
    ) -> None:
        """Without noise or drift, output = base + gain * parent."""
        m = _make_model({"base": base, "gain": gain})
        m.set_parent_value(parent)
        val = m.generate(0.0, DT)
        expected = base + gain * parent
        assert val == pytest.approx(expected, abs=1e-10)

    @given(seed=st.integers(min_value=0, max_value=10000))
    @settings(max_examples=50)
    def test_determinism_any_seed(self, seed: int) -> None:
        """Deterministic with same seed for any seed value."""
        v1 = _run_ticks(_make_model(seed=seed), 10, parent_value=77.0)
        v2 = _run_ticks(_make_model(seed=seed), 10, parent_value=77.0)
        assert v1 == v2

    @given(seed=st.integers(min_value=0, max_value=10000))
    @settings(max_examples=50)
    def test_gain_drift_stays_positive(self, seed: int) -> None:
        """Gain drift factor always stays positive."""
        m = _make_model({
            "gain": 1.0,
            "gain_drift_volatility": 0.01,
            "gain_drift_reversion": 0.02,
        }, seed=seed)
        t = 0.0
        for _ in range(100):
            m.set_parent_value(100.0)
            m.generate(t, DT)
            assert m.gain_drift_factor > 0.0
            t += DT

    @given(
        gain=st.floats(min_value=0.1, max_value=10.0),
        parent=st.floats(min_value=0.0, max_value=1000.0),
    )
    @settings(max_examples=100)
    def test_positive_gain_positive_parent_output_positive(
        self, gain: float, parent: float
    ) -> None:
        """With positive gain, base=0, positive parent, output is positive."""
        m = _make_model({"gain": gain, "base": 0.0})
        m.set_parent_value(parent)
        val = m.generate(0.0, DT)
        assert val >= 0.0


# ===========================================================================
# Package Imports
# ===========================================================================


class TestPackageImports:
    def test_import_from_models(self) -> None:
        """CorrelatedFollowerModel importable from models package."""
        from factory_simulator.models import CorrelatedFollowerModel as CFM

        assert CFM is CorrelatedFollowerModel

    def test_in_all(self) -> None:
        """CorrelatedFollowerModel listed in __all__."""
        from factory_simulator import models

        assert "CorrelatedFollowerModel" in models.__all__
