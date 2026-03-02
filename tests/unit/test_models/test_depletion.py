"""Tests for the DepletionModel.

PRD Reference: Section 4.2.7 (Depletion Curve)
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factory_simulator.models.depletion import DepletionModel
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
) -> DepletionModel:
    p = params if params is not None else {}
    return DepletionModel(p, _make_rng(seed), noise=noise)


def _run_ticks(
    model: DepletionModel, n: int, dt: float = DT, speed: float | None = None
) -> list[float]:
    """Run n ticks and return the values."""
    if speed is not None:
        model.set_speed(speed)
    t = 0.0
    values: list[float] = []
    for _ in range(n):
        values.append(model.generate(t, dt))
        t += dt
    return values


# ===================================================================
# Construction
# ===================================================================


class TestConstruction:
    def test_defaults(self) -> None:
        m = _make_model()
        assert m.initial_value == 100.0
        assert m.consumption_rate == 0.01
        assert m.refill_threshold is None
        assert m.refill_value is None
        assert m.value == 100.0
        assert m.speed == 0.0

    def test_explicit_params(self) -> None:
        m = _make_model({
            "initial_value": 1500.0,
            "consumption_rate": 0.05,
            "refill_threshold": 50.0,
            "refill_value": 1500.0,
        })
        assert m.initial_value == 1500.0
        assert m.consumption_rate == 0.05
        assert m.refill_threshold == 50.0
        assert m.refill_value == 1500.0
        assert m.value == 1500.0

    def test_invalid_consumption_rate_negative(self) -> None:
        with pytest.raises(ValueError, match="consumption_rate must be >= 0"):
            _make_model({"consumption_rate": -0.01})

    def test_zero_consumption_rate_allowed(self) -> None:
        m = _make_model({"consumption_rate": 0.0})
        assert m.consumption_rate == 0.0

    def test_invalid_refill_threshold_negative(self) -> None:
        with pytest.raises(ValueError, match="refill_threshold must be >= 0"):
            _make_model({"refill_threshold": -1.0, "refill_value": 100.0})

    def test_zero_refill_threshold_allowed(self) -> None:
        """A threshold of 0 means refill when level hits zero."""
        m = _make_model({"refill_threshold": 0.0, "refill_value": 100.0})
        assert m.refill_threshold == 0.0

    def test_invalid_refill_value_zero(self) -> None:
        with pytest.raises(ValueError, match="refill_value must be > 0"):
            _make_model({"refill_threshold": 10.0, "refill_value": 0.0})

    def test_invalid_refill_value_negative(self) -> None:
        with pytest.raises(ValueError, match="refill_value must be > 0"):
            _make_model({"refill_threshold": 10.0, "refill_value": -50.0})

    def test_invalid_refill_threshold_ge_refill_value(self) -> None:
        with pytest.raises(ValueError, match="refill_threshold must be < refill_value"):
            _make_model({"refill_threshold": 100.0, "refill_value": 100.0})

    def test_invalid_refill_threshold_gt_refill_value(self) -> None:
        with pytest.raises(ValueError, match="refill_threshold must be < refill_value"):
            _make_model({"refill_threshold": 150.0, "refill_value": 100.0})

    def test_refill_threshold_without_refill_value(self) -> None:
        """Only threshold set -- refill disabled (both needed)."""
        m = _make_model({"refill_threshold": 10.0})
        assert m.refill_threshold == 10.0
        assert m.refill_value is None

    def test_refill_value_without_refill_threshold(self) -> None:
        """Only value set -- refill disabled (both needed)."""
        m = _make_model({"refill_value": 100.0})
        assert m.refill_threshold is None
        assert m.refill_value == 100.0


# ===================================================================
# Basic Depletion
# ===================================================================


class TestBasicDepletion:
    def test_zero_speed_no_depletion(self) -> None:
        """When speed is zero, level does not change."""
        m = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        vals = _run_ticks(m, 10, speed=0.0)
        assert all(v == pytest.approx(100.0) for v in vals)

    def test_constant_speed_linear_depletion(self) -> None:
        """Level should decrease linearly with constant speed."""
        m = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m.set_speed(100.0)  # 100 units/tick-driver
        vals = _run_ticks(m, 10)
        # Each tick: 0.01 * 100 * 0.1 = 0.1 decrement
        for i, v in enumerate(vals, 1):
            assert v == pytest.approx(100.0 - i * 0.1)

    def test_consumption_rate_scaling(self) -> None:
        """Higher consumption_rate depletes faster."""
        m1 = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m2 = _make_model({"initial_value": 100.0, "consumption_rate": 0.02})
        m1.set_speed(100.0)
        m2.set_speed(100.0)
        _run_ticks(m1, 10)
        _run_ticks(m2, 10)
        # m2 depleted twice as fast
        remaining_1 = 100.0 - m1.value
        remaining_2 = 100.0 - m2.value
        assert remaining_2 == pytest.approx(remaining_1 * 2.0)

    def test_speed_scaling(self) -> None:
        """Higher speed depletes faster."""
        m1 = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m2 = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m1.set_speed(100.0)
        m2.set_speed(200.0)
        _run_ticks(m1, 10)
        _run_ticks(m2, 10)
        depleted_1 = 100.0 - m1.value
        depleted_2 = 100.0 - m2.value
        assert depleted_2 == pytest.approx(depleted_1 * 2.0)

    def test_dt_scaling(self) -> None:
        """Larger dt produces proportionally larger depletion per tick."""
        m1 = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m2 = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m1.set_speed(100.0)
        m2.set_speed(100.0)
        # 1 second of sim time at different tick rates
        _run_ticks(m1, 10, dt=0.1)
        _run_ticks(m2, 5, dt=0.2)
        assert m1.value == pytest.approx(m2.value)

    def test_zero_consumption_rate_no_depletion(self) -> None:
        """Zero consumption rate means level stays constant."""
        m = _make_model({"initial_value": 100.0, "consumption_rate": 0.0})
        m.set_speed(200.0)
        vals = _run_ticks(m, 100)
        assert all(v == pytest.approx(100.0) for v in vals)

    def test_depletion_can_go_negative_without_clamp(self) -> None:
        """Without external clamping, depletion can go below zero."""
        m = _make_model({"initial_value": 10.0, "consumption_rate": 1.0})
        m.set_speed(100.0)
        # Each tick: 1.0 * 100 * 0.1 = 10.0 decrement
        # After 2 ticks: 10 - 20 = -10
        vals = _run_ticks(m, 2)
        assert vals[1] == pytest.approx(-10.0)


# ===================================================================
# Speed Changes
# ===================================================================


class TestSpeedChanges:
    def test_set_speed(self) -> None:
        m = _make_model()
        m.set_speed(150.0)
        assert m.speed == 150.0

    def test_speed_change_affects_depletion(self) -> None:
        """Changing speed mid-run changes depletion rate."""
        m = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m.set_speed(100.0)
        v1 = m.generate(0.0, DT)  # depletes 0.1: 99.9
        assert v1 == pytest.approx(99.9)

        m.set_speed(200.0)
        v2 = m.generate(DT, DT)  # depletes 0.2: 99.7
        assert v2 == pytest.approx(99.7)

    def test_speed_to_zero_stops_depletion(self) -> None:
        """Setting speed to zero pauses depletion."""
        m = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m.set_speed(100.0)
        _run_ticks(m, 5)
        val_before = m.value

        m.set_speed(0.0)
        _run_ticks(m, 10)
        assert m.value == pytest.approx(val_before)


# ===================================================================
# Auto-Refill
# ===================================================================


class TestAutoRefill:
    def test_refill_triggers_at_threshold(self) -> None:
        """Level jumps to refill_value when it drops to threshold."""
        m = _make_model({
            "initial_value": 100.0,
            "consumption_rate": 1.0,
            "refill_threshold": 10.0,
            "refill_value": 100.0,
        })
        m.set_speed(100.0)
        # Each tick: 1.0 * 100 * 0.1 = 10.0 decrement
        # After 9 ticks: 100 - 90 = 10 -> at threshold -> refill to 100
        vals = _run_ticks(m, 9)
        assert vals[-1] == pytest.approx(100.0)

    def test_refill_triggers_below_threshold(self) -> None:
        """Level jumps to refill_value when it drops below threshold."""
        m = _make_model({
            "initial_value": 100.0,
            "consumption_rate": 1.0,
            "refill_threshold": 15.0,
            "refill_value": 100.0,
        })
        m.set_speed(100.0)
        # After 9 ticks: 100 - 90 = 10 < 15 -> refill to 100
        vals = _run_ticks(m, 9)
        assert vals[-1] == pytest.approx(100.0)

    def test_refill_cycles(self) -> None:
        """Multiple refill cycles work correctly."""
        m = _make_model({
            "initial_value": 100.0,
            "consumption_rate": 1.0,
            "refill_threshold": 10.0,
            "refill_value": 100.0,
        })
        m.set_speed(100.0)
        # Each tick depletes 10. After 9 ticks: 10 -> refill to 100.
        # Then depletes again.
        vals = _run_ticks(m, 20)
        # Count how many times we see 100.0 (refill events)
        refill_count = sum(1 for v in vals if v == pytest.approx(100.0))
        assert refill_count >= 2  # At least 2 refill cycles

    def test_no_refill_when_both_none(self) -> None:
        """With both threshold and value None, no refill occurs."""
        m = _make_model({
            "initial_value": 100.0,
            "consumption_rate": 1.0,
        })
        m.set_speed(100.0)
        # After 10 ticks: 100 - 100 = 0
        vals = _run_ticks(m, 10)
        assert vals[-1] == pytest.approx(0.0)
        # No refill -- goes negative next tick
        vals2 = _run_ticks(m, 1)
        assert vals2[0] == pytest.approx(-10.0)

    def test_no_refill_when_only_threshold_set(self) -> None:
        """With only threshold set (no value), no refill occurs."""
        m = _make_model({
            "initial_value": 100.0,
            "consumption_rate": 1.0,
            "refill_threshold": 10.0,
        })
        m.set_speed(100.0)
        vals = _run_ticks(m, 11)
        # Should go below threshold without refilling
        assert vals[-1] < 10.0

    def test_no_refill_when_only_value_set(self) -> None:
        """With only refill_value set (no threshold), no refill occurs."""
        m = _make_model({
            "initial_value": 100.0,
            "consumption_rate": 1.0,
            "refill_value": 100.0,
        })
        m.set_speed(100.0)
        vals = _run_ticks(m, 11)
        # Should deplete below zero without refilling
        assert vals[-1] < 0.0

    def test_refill_value_different_from_initial(self) -> None:
        """Refill value can be different from initial value."""
        m = _make_model({
            "initial_value": 100.0,
            "consumption_rate": 1.0,
            "refill_threshold": 10.0,
            "refill_value": 80.0,
        })
        m.set_speed(100.0)
        # Deplete until <=10, refill to 80 (not 100)
        vals = _run_ticks(m, 9)
        assert vals[-1] == pytest.approx(80.0)

    def test_refill_threshold_zero_triggers_at_zero(self) -> None:
        """Threshold 0 means refill when level drops to exactly zero."""
        m = _make_model({
            "initial_value": 100.0,
            "consumption_rate": 1.0,
            "refill_threshold": 0.0,
            "refill_value": 100.0,
        })
        m.set_speed(100.0)
        # After 10 ticks: 100 - 100 = 0 -> at threshold -> refill
        vals = _run_ticks(m, 10)
        assert vals[-1] == pytest.approx(100.0)


# ===================================================================
# Manual Refill
# ===================================================================


class TestManualRefill:
    def test_refill_to_specified_level(self) -> None:
        """refill(level) sets value to the specified level."""
        m = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m.set_speed(100.0)
        _run_ticks(m, 10)
        m.refill(80.0)
        assert m.value == pytest.approx(80.0)

    def test_refill_to_refill_value(self) -> None:
        """refill() without arg uses refill_value if set."""
        m = _make_model({
            "initial_value": 100.0,
            "consumption_rate": 0.01,
            "refill_value": 90.0,
        })
        m.set_speed(100.0)
        _run_ticks(m, 10)
        m.refill()
        assert m.value == pytest.approx(90.0)

    def test_refill_defaults_to_initial_value(self) -> None:
        """refill() without arg and no refill_value uses initial_value."""
        m = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m.set_speed(100.0)
        _run_ticks(m, 10)
        m.refill()
        assert m.value == pytest.approx(100.0)

    def test_refill_continues_depletion(self) -> None:
        """After manual refill, depletion continues."""
        m = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m.set_speed(100.0)
        _run_ticks(m, 10)
        m.refill(100.0)
        vals = _run_ticks(m, 5)
        assert vals[-1] < 100.0


# ===================================================================
# Noise
# ===================================================================


class TestNoise:
    def test_noise_adds_variation(self) -> None:
        """With noise, output varies around the depletion level."""
        noise = _make_noise(sigma=5.0)
        m = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.0},
            noise=noise,
        )
        vals = _run_ticks(m, 100)
        # Without depletion, all noise-free values would be 100.0
        # With noise, there should be variation
        assert max(vals) != min(vals)

    def test_noise_mean_near_level(self) -> None:
        """Over many samples, noisy output should average near the level."""
        noise = _make_noise(sigma=2.0)
        m = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.0},
            noise=noise,
        )
        vals = _run_ticks(m, 10000)
        assert np.mean(vals) == pytest.approx(100.0, abs=0.5)

    def test_zero_sigma_clean_signal(self) -> None:
        """With sigma=0 noise, output equals the level exactly."""
        noise = _make_noise(sigma=0.0)
        m = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.0},
            noise=noise,
        )
        vals = _run_ticks(m, 10)
        assert all(v == pytest.approx(100.0) for v in vals)

    def test_noise_does_not_affect_internal_level(self) -> None:
        """Noise is observation noise -- it does not change the stored level."""
        noise = _make_noise(sigma=5.0)
        m = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.0},
            noise=noise,
        )
        _run_ticks(m, 100)
        # Internal level should still be exactly 100.0
        assert m.value == pytest.approx(100.0)

    def test_ar1_noise_resets(self) -> None:
        """AR(1) noise state is cleared on reset."""
        noise = _make_noise(sigma=2.0, distribution="ar1")
        m = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.0},
            noise=noise,
        )
        _run_ticks(m, 50)
        m.reset()
        # After reset, noise should be fresh -- test that it doesn't crash
        vals = _run_ticks(m, 10)
        assert all(np.isfinite(v) for v in vals)


# ===================================================================
# Reset
# ===================================================================


class TestReset:
    def test_reset_restores_initial_value(self) -> None:
        m = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m.set_speed(100.0)
        _run_ticks(m, 100)
        assert m.value != pytest.approx(100.0)
        m.reset()
        assert m.value == pytest.approx(100.0)

    def test_reset_zeros_speed(self) -> None:
        m = _make_model()
        m.set_speed(200.0)
        m.reset()
        assert m.speed == 0.0

    def test_reset_defaults_initial_value(self) -> None:
        m = _make_model({"initial_value": 42.0})
        m.set_speed(100.0)
        _run_ticks(m, 10)
        m.reset()
        assert m.value == pytest.approx(42.0)

    def test_reset_clears_noise_state(self) -> None:
        """Reset should clear AR(1) noise autocorrelation."""
        noise = _make_noise(sigma=1.0, distribution="ar1")
        m = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.0},
            noise=noise,
        )
        _run_ticks(m, 50)
        m.reset()
        # After reset, noise is fresh -- generate should still work
        vals = _run_ticks(m, 10)
        assert all(np.isfinite(v) for v in vals)


# ===================================================================
# Determinism (Rule 13)
# ===================================================================


class TestDeterminism:
    def test_same_seed_same_output(self) -> None:
        """Same seed and same inputs produce identical output."""
        m1 = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.01},
            seed=99,
        )
        m2 = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.01},
            seed=99,
        )
        m1.set_speed(150.0)
        m2.set_speed(150.0)
        v1 = _run_ticks(m1, 20)
        v2 = _run_ticks(m2, 20)
        assert v1 == v2

    def test_no_noise_deterministic_regardless_of_seed(self) -> None:
        """Without noise, the depletion model is purely deterministic."""
        m1 = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.01},
            seed=1,
        )
        m2 = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.01},
            seed=999,
        )
        m1.set_speed(100.0)
        m2.set_speed(100.0)
        v1 = _run_ticks(m1, 20)
        v2 = _run_ticks(m2, 20)
        assert v1 == v2

    def test_noise_same_seed_same_output(self) -> None:
        """With noise, same seed produces identical output."""
        n1 = _make_noise(sigma=2.0, seed=42)
        n2 = _make_noise(sigma=2.0, seed=42)
        m1 = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.01},
            seed=42,
            noise=n1,
        )
        m2 = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.01},
            seed=42,
            noise=n2,
        )
        m1.set_speed(100.0)
        m2.set_speed(100.0)
        v1 = _run_ticks(m1, 50)
        v2 = _run_ticks(m2, 50)
        assert v1 == v2

    def test_noise_different_seeds_differ(self) -> None:
        """Different noise seeds produce different output."""
        n1 = _make_noise(sigma=2.0, seed=1)
        n2 = _make_noise(sigma=2.0, seed=999)
        m1 = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.01},
            seed=1,
            noise=n1,
        )
        m2 = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.01},
            seed=999,
            noise=n2,
        )
        m1.set_speed(100.0)
        m2.set_speed(100.0)
        v1 = _run_ticks(m1, 50)
        v2 = _run_ticks(m2, 50)
        assert v1 != v2


# ===================================================================
# Time Compression (Rule 6)
# ===================================================================


class TestTimeCompression:
    def test_same_depletion_at_different_tick_rates(self) -> None:
        """At different dt values, same total sim time gives same depletion.
        Rule 6: simulated time invariant."""
        m1 = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m2 = _make_model({"initial_value": 100.0, "consumption_rate": 0.01})
        m1.set_speed(100.0)
        m2.set_speed(100.0)

        # 10 seconds of sim time
        _run_ticks(m1, 100, dt=0.1)  # 100 ticks at 0.1s
        _run_ticks(m2, 10, dt=1.0)  # 10 ticks at 1.0s

        # Both: 100 - 0.01 * 100 * 10 = 100 - 10 = 90
        assert m1.value == pytest.approx(90.0)
        assert m2.value == pytest.approx(90.0)

    def test_compressed_run(self) -> None:
        """At high speed compression, depletion is correct."""
        m = _make_model({"initial_value": 100.0, "consumption_rate": 0.005})
        m.set_speed(200.0)
        # 1 hour: 0.005 * 200 * 3600 = 3600 depletion
        # 100 - 3600 = -3500 (no clamp in the model itself)
        _run_ticks(m, 3600, dt=1.0)
        assert m.value == pytest.approx(100.0 - 3600.0)


# ===================================================================
# PRD Examples
# ===================================================================


class TestPrdExamples:
    def test_ink_level(self) -> None:
        """coder.ink_level: consumption_rate=0.005, refill at 5%, refill to 100%.
        PRD 4.2.7: ink level depletes proportional to printing."""
        m = _make_model({
            "initial_value": 100.0,
            "consumption_rate": 0.005,
            "refill_threshold": 5.0,
            "refill_value": 100.0,
        })
        # Simulate at 200 prints/min for a while
        m.set_speed(200.0)
        # Each tick: 0.005 * 200 * 0.1 = 0.1 depletion
        # After ~951 ticks: 100 - 95.1 = 4.9 -> below threshold -> refill
        # (use 960 to be safe with fp accumulation)
        vals = _run_ticks(m, 960)
        # Should have refilled at least once -- last value well above threshold
        assert vals[-1] > 5.0

    def test_ink_level_multiple_refills(self) -> None:
        """Ink level should cycle through multiple refills during long production."""
        m = _make_model({
            "initial_value": 100.0,
            "consumption_rate": 0.005,
            "refill_threshold": 5.0,
            "refill_value": 100.0,
        })
        m.set_speed(200.0)
        # Each tick depletes 0.1. Full cycle: 950 ticks (100->5->100).
        # After 2000 ticks: should have refilled at least twice
        vals = _run_ticks(m, 2000)
        refill_events = sum(1 for v in vals if v == pytest.approx(100.0))
        assert refill_events >= 2

    def test_unwind_diameter(self) -> None:
        """press.unwind_diameter: depletes as material is consumed.
        No refill -- reel changeover is a scenario event."""
        m = _make_model({
            "initial_value": 1500.0,
            "consumption_rate": 0.01,
        })
        m.set_speed(200.0)
        # After 10 seconds: 1500 - 0.01 * 200 * 10 = 1500 - 20 = 1480
        _run_ticks(m, 100, dt=0.1)
        assert m.value == pytest.approx(1480.0)

    def test_nozzle_health(self) -> None:
        """coder.nozzle_health: very slow degradation, no refill.
        consumption_rate=0.001 -- health degrades over hours of printing."""
        m = _make_model({
            "initial_value": 100.0,
            "consumption_rate": 0.001,
        })
        m.set_speed(200.0)
        # After 1 hour (3600s): 100 - 0.001 * 200 * 3600 = 100 - 720 = ...
        # Actually at this rate it depletes fast. In reality speed may
        # represent a different metric. Let's just verify the formula.
        _run_ticks(m, 100, dt=1.0)  # 100 seconds
        # 100 - 0.001 * 200 * 100 = 100 - 20 = 80
        assert m.value == pytest.approx(80.0)


# ===================================================================
# Property-Based Tests (Hypothesis)
# ===================================================================


class TestPropertyBased:
    @given(
        consumption_rate=st.floats(min_value=0.0, max_value=100.0),
        speed=st.floats(min_value=0.0, max_value=1000.0),
        dt=st.floats(min_value=0.001, max_value=10.0),
    )
    @settings(max_examples=100)
    def test_output_always_finite(
        self, consumption_rate: float, speed: float, dt: float
    ) -> None:
        m = _make_model({"consumption_rate": consumption_rate})
        m.set_speed(speed)
        v = m.generate(0.0, dt)
        assert np.isfinite(v)

    @given(
        consumption_rate=st.floats(min_value=0.01, max_value=10.0),
        speed=st.floats(min_value=1.0, max_value=500.0),
    )
    @settings(max_examples=50)
    def test_monotonically_decreasing_without_refill(
        self, consumption_rate: float, speed: float
    ) -> None:
        """With positive rate and speed and no refill, level is strictly decreasing."""
        m = _make_model({
            "initial_value": 1000.0,
            "consumption_rate": consumption_rate,
        })
        m.set_speed(speed)
        vals = _run_ticks(m, 20)
        for i in range(1, len(vals)):
            assert vals[i] < vals[i - 1]

    @given(seed=st.integers(min_value=0, max_value=2**32 - 1))
    @settings(max_examples=50)
    def test_determinism_any_seed(self, seed: int) -> None:
        """Without noise, output is deterministic regardless of seed."""
        m1 = _make_model({"initial_value": 100.0, "consumption_rate": 0.01}, seed=seed)
        m2 = _make_model(
            {"initial_value": 100.0, "consumption_rate": 0.01}, seed=seed + 1
        )
        m1.set_speed(100.0)
        m2.set_speed(100.0)
        v1 = _run_ticks(m1, 10)
        v2 = _run_ticks(m2, 10)
        assert v1 == v2

    @given(
        initial=st.floats(min_value=10.0, max_value=1000.0),
        consumption_rate=st.floats(min_value=0.0, max_value=10.0),
        speed=st.floats(min_value=0.0, max_value=500.0),
    )
    @settings(max_examples=50)
    def test_depletion_formula(
        self, initial: float, consumption_rate: float, speed: float
    ) -> None:
        """Verify depletion follows the exact PRD formula."""
        m = _make_model({
            "initial_value": initial,
            "consumption_rate": consumption_rate,
        })
        m.set_speed(speed)
        dt = 0.1
        v = m.generate(0.0, dt)
        expected = initial - consumption_rate * speed * dt
        assert v == pytest.approx(expected)

    @given(
        threshold=st.floats(min_value=1.0, max_value=49.0),
        refill_val=st.floats(min_value=50.0, max_value=100.0),
    )
    @settings(max_examples=50)
    def test_refill_keeps_value_above_threshold(
        self, threshold: float, refill_val: float
    ) -> None:
        """With refill enabled, after many ticks the value stays above
        the threshold (it refills before going too low)."""
        m = _make_model({
            "initial_value": refill_val,
            "consumption_rate": 1.0,
            "refill_threshold": threshold,
            "refill_value": refill_val,
        })
        m.set_speed(100.0)
        vals = _run_ticks(m, 200)
        # All values should be >= threshold (the refill fires at/below threshold)
        # Actually, the value at the moment of refill was <= threshold,
        # but then it was set to refill_value. So all *returned* values
        # are either depleting above threshold or exactly refill_value.
        for v in vals:
            assert v >= threshold or v == pytest.approx(refill_val)


# ===================================================================
# Package Imports
# ===================================================================


class TestPackageImports:
    def test_import_from_models_package(self) -> None:
        from factory_simulator.models import DepletionModel as DM

        assert DM is DepletionModel

    def test_in_all(self) -> None:
        import factory_simulator.models as models

        assert "DepletionModel" in models.__all__
