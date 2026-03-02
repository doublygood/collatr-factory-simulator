"""Tests for the RampModel.

PRD Reference: Section 4.2.4 (Ramp Up / Ramp Down)
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factory_simulator.models.noise import NoiseGenerator
from factory_simulator.models.ramp import RampModel

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
) -> RampModel:
    p = params if params is not None else {
        "start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1,
    }
    return RampModel(p, _make_rng(seed), noise=noise)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_defaults(self) -> None:
        model = RampModel({}, _make_rng())
        assert model.start_value == 0.0
        assert model.end_value == 100.0
        assert model.duration == 120.0
        assert model.num_steps == 4  # PRD default
        assert model.value == 0.0
        assert model.elapsed == 0.0
        assert not model.complete

    def test_explicit_params(self) -> None:
        model = _make_model({
            "start": 10.0, "end": 200.0, "duration": 60.0, "steps": 1,
        })
        assert model.start_value == 10.0
        assert model.end_value == 200.0
        assert model.duration == 60.0
        assert model.num_steps == 1

    def test_stepped_params(self) -> None:
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 120.0, "steps": 4,
            "step_overshoot_pct": 0.05, "step_overshoot_decay_s": 5.0,
            "step_dwell_range": [10.0, 30.0],
        })
        assert model.num_steps == 4

    def test_invalid_duration_zero(self) -> None:
        with pytest.raises(ValueError, match="duration must be > 0"):
            RampModel({"duration": 0.0}, _make_rng())

    def test_invalid_duration_negative(self) -> None:
        with pytest.raises(ValueError, match="duration must be > 0"):
            RampModel({"duration": -5.0}, _make_rng())

    def test_invalid_steps_zero(self) -> None:
        with pytest.raises(ValueError, match="steps must be >= 1"):
            RampModel({"steps": 0}, _make_rng())

    def test_invalid_steps_negative(self) -> None:
        with pytest.raises(ValueError, match="steps must be >= 1"):
            RampModel({"steps": -1}, _make_rng())

    def test_invalid_overshoot_decay(self) -> None:
        with pytest.raises(ValueError, match="step_overshoot_decay_s must be > 0"):
            RampModel({"step_overshoot_decay_s": 0.0}, _make_rng())

    def test_invalid_dwell_range_type(self) -> None:
        with pytest.raises(ValueError, match="step_dwell_range must be a list"):
            RampModel({"step_dwell_range": "bad"}, _make_rng())


# ---------------------------------------------------------------------------
# Smooth ramp (steps=1)
# ---------------------------------------------------------------------------


class TestSmoothRamp:
    def test_linear_progression(self) -> None:
        """Smooth ramp interpolates linearly from start to end."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1,
        })
        # At 50% through, value should be ~50
        ticks = int(5.0 / DT)
        for i in range(ticks):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(50.0, abs=1.0)

    def test_reaches_end_at_duration(self) -> None:
        """Value equals end at exactly the duration."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1,
        })
        ticks = int(10.0 / DT)
        for i in range(ticks):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(100.0, abs=0.5)

    def test_holds_at_end_after_duration(self) -> None:
        """After duration, value stays at end."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1,
        })
        ticks = int(20.0 / DT)
        for i in range(ticks):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(100.0)
        assert model.complete

    def test_complete_flag(self) -> None:
        """complete property tracks whether ramp finished."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 5.0, "steps": 1,
        })
        assert not model.complete
        # +1 tick to overcome floating-point accumulation of DT
        for i in range(int(5.0 / DT) + 1):
            model.generate(i * DT, DT)
        assert model.complete

    def test_smooth_is_monotonic_ramp_up(self) -> None:
        """Smooth ramp up is monotonically increasing (no noise)."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1,
        })
        values = [model.generate(i * DT, DT) for i in range(int(10.0 / DT))]
        for i in range(1, len(values)):
            assert values[i] >= values[i - 1] - 1e-10

    def test_smooth_ramp_down(self) -> None:
        """Smooth ramp down works correctly (end < start)."""
        model = _make_model({
            "start": 100.0, "end": 0.0, "duration": 10.0, "steps": 1,
        })
        values = [model.generate(i * DT, DT) for i in range(int(10.0 / DT))]
        # Monotonically decreasing
        for i in range(1, len(values)):
            assert values[i] <= values[i - 1] + 1e-10
        assert model.value == pytest.approx(0.0, abs=0.5)

    def test_25_50_75_percent_progress(self) -> None:
        """Verify specific progress points on smooth ramp."""
        model = _make_model({
            "start": 0.0, "end": 200.0, "duration": 20.0, "steps": 1,
        })
        for pct, expected in [(0.25, 50.0), (0.50, 100.0), (0.75, 150.0)]:
            model.reset()
            ticks = int(20.0 * pct / DT)
            for i in range(ticks):
                model.generate(i * DT, DT)
            assert model.value == pytest.approx(expected, abs=1.0)

    def test_negative_range(self) -> None:
        """Ramp between negative values."""
        model = _make_model({
            "start": -50.0, "end": -10.0, "duration": 10.0, "steps": 1,
        })
        for i in range(int(10.0 / DT)):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(-10.0, abs=0.5)


# ---------------------------------------------------------------------------
# Stepped ramp (steps > 1)
# ---------------------------------------------------------------------------


class TestSteppedRamp:
    def test_reaches_end_at_duration(self) -> None:
        """Stepped ramp reaches end value at or after duration."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 120.0, "steps": 4,
        })
        # +1 tick to overcome floating-point accumulation of DT
        ticks = int(120.0 / DT) + 1
        for i in range(ticks):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(100.0)

    def test_step_count_visible(self) -> None:
        """Stepped ramp shows distinct step levels.

        Run without noise, after overshoot decays, count distinct
        base levels.
        """
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 120.0, "steps": 4,
            "step_overshoot_pct": 0.0,  # disable overshoot for counting
        })
        # Collect values at steady portions of each step
        # With no overshoot, values at each step are exact
        ticks = int(120.0 / DT)
        values = [model.generate(i * DT, DT) for i in range(ticks)]

        # Find unique plateau values (round to integer for grouping)
        unique_levels = set()
        for v in values:
            rounded = round(v, 0)
            unique_levels.add(rounded)

        # Should see 4 distinct step values: 25, 50, 75, 100
        expected = {25.0, 50.0, 75.0, 100.0}
        assert expected.issubset(unique_levels)

    def test_dwell_times_fit_in_duration(self) -> None:
        """Sum of dwell times does not exceed duration."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 60.0, "steps": 4,
            "step_dwell_range": [15.0, 45.0],
        })
        assert sum(model._dwells) <= model.duration + 1e-10

    def test_dwell_compression(self) -> None:
        """When raw dwell sum exceeds duration, dwells are compressed."""
        # Force compression: very high dwell_min relative to duration
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 20.0, "steps": 4,
            "step_dwell_range": [10.0, 20.0],
        })
        # Raw dwells would be 40-80 total, duration is 20
        assert sum(model._dwells) == pytest.approx(20.0, abs=0.01)

    def test_overshoot_at_step_boundary(self) -> None:
        """Step transitions produce overshoot above the step target."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 120.0, "steps": 4,
            "step_overshoot_pct": 0.03,
            "step_overshoot_decay_s": 7.0,
        })
        # Run a few ticks -- should be at step 0 (target=25) with overshoot
        v = model.generate(0.0, DT)
        # Overshoot: 0.03 * 25 = 0.75, decayed slightly
        assert v > 25.0, "Expected overshoot above step target"

    def test_overshoot_decays_within_step(self) -> None:
        """Overshoot decays exponentially within a step."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 200.0, "steps": 4,
            "step_overshoot_pct": 0.10,  # large overshoot for visibility
            "step_overshoot_decay_s": 2.0,
            "step_dwell_range": [40.0, 50.0],  # long dwell to see decay
        })
        # Collect values for first step
        values = [model.generate(i * DT, DT) for i in range(200)]

        # Step target is 25.0, initial overshoot ~2.5 (10% of 25)
        # Values near the start of the step should be higher than later values
        early = values[0]
        late = values[50]  # 5 seconds in (> 2 decay constants)

        assert early > late, "Overshoot should decay over time"
        # Late value should be very close to step target
        assert late == pytest.approx(25.0, abs=0.5)

    def test_overshoot_direction_ramp_down(self) -> None:
        """For ramp down, overshoot goes below the step target."""
        model = _make_model({
            "start": 100.0, "end": 0.0, "duration": 120.0, "steps": 4,
            "step_overshoot_pct": 0.05,
            "step_dwell_range": [25.0, 35.0],
        })
        v = model.generate(0.0, DT)
        # First step target is 75.0, overshoot is negative (below 75)
        assert v < 75.0, "Ramp down overshoot should go below step target"

    def test_two_steps(self) -> None:
        """Two-step ramp: midpoint then end."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 60.0, "steps": 2,
            "step_overshoot_pct": 0.0,
            "step_dwell_range": [25.0, 35.0],
        })
        # Step targets: [50, 100]
        v_first = model.generate(0.0, DT)
        assert v_first == pytest.approx(50.0, abs=0.5)

        # Run to completion
        for i in range(1, int(60.0 / DT)):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(100.0)

    def test_many_steps(self) -> None:
        """Large step count works correctly."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 120.0, "steps": 10,
            "step_overshoot_pct": 0.0,
            "step_dwell_range": [8.0, 15.0],
        })
        # Run to completion
        for i in range(int(120.0 / DT)):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Noise
# ---------------------------------------------------------------------------


class TestNoiseGeneration:
    def test_noise_adds_variation(self) -> None:
        """Noise causes variation around the ramp trajectory."""
        noise = _make_noise(sigma=5.0)
        model = _make_model(
            {"start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1},
            noise=noise,
        )
        values = [model.generate(i * DT, DT) for i in range(100)]
        # Compute deviations from expected linear ramp
        deviations = [
            v - (i + 1) * DT / 10.0 * 100.0
            for i, v in enumerate(values)
        ]
        assert np.std(deviations) > 1.0

    def test_zero_sigma_clean_signal(self) -> None:
        """Zero sigma noise produces clean ramp."""
        noise = _make_noise(sigma=0.0)
        model_noisy = _make_model(
            {"start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1},
            noise=noise,
        )
        model_clean = _make_model(
            {"start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1},
        )
        for i in range(100):
            v_noisy = model_noisy.generate(i * DT, DT)
            v_clean = model_clean.generate(i * DT, DT)
            assert v_noisy == pytest.approx(v_clean, abs=1e-10)


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_restores_start(self) -> None:
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1,
        })
        for i in range(50):
            model.generate(i * DT, DT)
        assert model.value > 0.0

        model.reset()
        assert model.value == pytest.approx(0.0)
        assert model.elapsed == pytest.approx(0.0)
        assert not model.complete

    def test_reset_preserves_step_plan(self) -> None:
        """Reset keeps the same step plan (dwell times)."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 120.0, "steps": 4,
        })
        dwells_before = list(model._dwells)

        for i in range(100):
            model.generate(i * DT, DT)
        model.reset()

        assert model._dwells == dwells_before

    def test_reset_clears_noise_state(self) -> None:
        noise = _make_noise(sigma=1.0, distribution="ar1", phi=0.9)
        model = _make_model(
            {"start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1},
            noise=noise,
        )
        for i in range(100):
            model.generate(i * DT, DT)
        model.reset()
        assert noise._ar1_prev == 0.0


# ---------------------------------------------------------------------------
# start_ramp() -- dynamic reconfiguration
# ---------------------------------------------------------------------------


class TestStartRamp:
    def test_start_ramp_new_params(self) -> None:
        """start_ramp reconfigures and resets the ramp."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1,
        })
        for i in range(50):
            model.generate(i * DT, DT)

        model.start_ramp(start=100.0, end=0.0, duration=5.0)
        assert model.start_value == 100.0
        assert model.end_value == 0.0
        assert model.duration == 5.0
        assert model.elapsed == pytest.approx(0.0)
        assert model.value == pytest.approx(100.0)

    def test_start_ramp_partial_update(self) -> None:
        """start_ramp with only some params keeps others."""
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1,
        })
        model.start_ramp(end=200.0)
        assert model.end_value == 200.0
        assert model.start_value == 0.0  # unchanged
        assert model.duration == 10.0  # unchanged

    def test_start_ramp_invalid_duration(self) -> None:
        model = _make_model()
        with pytest.raises(ValueError, match="duration must be > 0"):
            model.start_ramp(duration=-1.0)

    def test_start_ramp_reaches_new_end(self) -> None:
        model = _make_model({
            "start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1,
        })
        model.start_ramp(start=50.0, end=200.0, duration=10.0)
        for i in range(int(10.0 / DT)):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(200.0, abs=0.5)


# ---------------------------------------------------------------------------
# Determinism (Rule 13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_same_output_smooth(self) -> None:
        """Two smooth ramp models with same seed produce identical output."""
        params: dict[str, object] = {
            "start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1,
        }
        noise1 = _make_noise(sigma=2.0, seed=99)
        model1 = RampModel(params, _make_rng(99), noise=noise1)

        noise2 = _make_noise(sigma=2.0, seed=99)
        model2 = RampModel(params, _make_rng(99), noise=noise2)

        for i in range(100):
            v1 = model1.generate(i * DT, DT)
            v2 = model2.generate(i * DT, DT)
            assert v1 == v2

    def test_same_seed_same_output_stepped(self) -> None:
        """Two stepped ramp models with same seed produce identical output."""
        params: dict[str, object] = {
            "start": 0.0, "end": 100.0, "duration": 120.0, "steps": 4,
        }
        noise1 = _make_noise(sigma=2.0, seed=99)
        model1 = RampModel(params, _make_rng(99), noise=noise1)

        noise2 = _make_noise(sigma=2.0, seed=99)
        model2 = RampModel(params, _make_rng(99), noise=noise2)

        for i in range(500):
            v1 = model1.generate(i * DT, DT)
            v2 = model2.generate(i * DT, DT)
            assert v1 == v2

    def test_different_seeds_differ(self) -> None:
        """Different seeds produce different step plans."""
        params: dict[str, object] = {
            "start": 0.0, "end": 100.0, "duration": 120.0, "steps": 4,
        }
        noise1 = _make_noise(sigma=2.0, seed=1)
        model1 = RampModel(params, _make_rng(1), noise=noise1)

        noise2 = _make_noise(sigma=2.0, seed=2)
        model2 = RampModel(params, _make_rng(2), noise=noise2)

        values1 = [model1.generate(i * DT, DT) for i in range(100)]
        values2 = [model2.generate(i * DT, DT) for i in range(100)]
        assert values1 != values2

    def test_no_noise_deterministic(self) -> None:
        """Without noise, smooth ramp output is deterministic for any seed."""
        params: dict[str, object] = {
            "start": 0.0, "end": 100.0, "duration": 10.0, "steps": 1,
        }
        model1 = RampModel(params, _make_rng(1))
        model2 = RampModel(params, _make_rng(999))

        for i in range(100):
            assert model1.generate(i * DT, DT) == model2.generate(i * DT, DT)


# ---------------------------------------------------------------------------
# Hypothesis property-based tests
# ---------------------------------------------------------------------------


class TestPropertyBased:
    @given(
        start=st.floats(min_value=-1000, max_value=1000, allow_nan=False),
        end=st.floats(min_value=-1000, max_value=1000, allow_nan=False),
        duration=st.floats(min_value=0.1, max_value=1000, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_output_finite_smooth(
        self, start: float, end: float, duration: float
    ) -> None:
        """Output is always finite for valid smooth ramp inputs."""
        model = RampModel(
            {"start": start, "end": end, "duration": duration, "steps": 1},
            _make_rng(),
        )
        for i in range(10):
            value = model.generate(i * DT, DT)
            assert np.isfinite(value)

    @given(
        start=st.floats(min_value=-100, max_value=100, allow_nan=False),
        end=st.floats(min_value=-100, max_value=100, allow_nan=False),
        duration=st.floats(min_value=1.0, max_value=100, allow_nan=False),
    )
    @settings(max_examples=50)
    def test_reaches_end_smooth(
        self, start: float, end: float, duration: float
    ) -> None:
        """Smooth ramp always reaches end value at duration."""
        model = RampModel(
            {"start": start, "end": end, "duration": duration, "steps": 1},
            _make_rng(),
        )
        ticks = int(duration / DT) + 1
        for i in range(ticks):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(end, abs=0.1)

    @given(
        start=st.floats(min_value=-100, max_value=100, allow_nan=False),
        end=st.floats(min_value=-100, max_value=100, allow_nan=False),
        duration=st.floats(min_value=10.0, max_value=200, allow_nan=False),
        steps=st.integers(min_value=2, max_value=10),
    )
    @settings(max_examples=50)
    def test_reaches_end_stepped(
        self, start: float, end: float, duration: float, steps: int
    ) -> None:
        """Stepped ramp reaches end value at or after duration."""
        model = RampModel(
            {"start": start, "end": end, "duration": duration, "steps": steps},
            _make_rng(),
        )
        ticks = int(duration / DT) + 1
        for i in range(ticks):
            model.generate(i * DT, DT)
        assert model.value == pytest.approx(end, abs=0.1)

    @given(seed=st.integers(min_value=0, max_value=2**31))
    @settings(max_examples=20)
    def test_determinism_any_seed(self, seed: int) -> None:
        """Any seed produces deterministic output for stepped ramps."""
        params: dict[str, object] = {
            "start": 0.0, "end": 100.0, "duration": 60.0, "steps": 4,
        }
        n1 = NoiseGenerator(sigma=1.0, distribution="gaussian", rng=_make_rng(seed))
        m1 = RampModel(params, _make_rng(seed), noise=n1)

        n2 = NoiseGenerator(sigma=1.0, distribution="gaussian", rng=_make_rng(seed))
        m2 = RampModel(params, _make_rng(seed), noise=n2)

        for i in range(20):
            assert m1.generate(i * DT, DT) == m2.generate(i * DT, DT)

    @given(
        start=st.floats(min_value=-100, max_value=100, allow_nan=False),
        end=st.floats(min_value=-100, max_value=100, allow_nan=False),
        duration=st.floats(min_value=1.0, max_value=100, allow_nan=False),
    )
    @settings(max_examples=50)
    def test_smooth_ramp_monotonic(
        self, start: float, end: float, duration: float
    ) -> None:
        """Smooth ramp is monotonic (no overshoot without noise)."""
        model = RampModel(
            {"start": start, "end": end, "duration": duration, "steps": 1},
            _make_rng(),
        )
        ticks = int(duration / DT)
        if ticks < 2:
            return  # Not enough ticks to check monotonicity
        values = [model.generate(i * DT, DT) for i in range(ticks)]
        if end >= start:
            for i in range(1, len(values)):
                assert values[i] >= values[i - 1] - 1e-10
        else:
            for i in range(1, len(values)):
                assert values[i] <= values[i - 1] + 1e-10

    @given(
        duration=st.floats(min_value=5.0, max_value=200, allow_nan=False),
        steps=st.integers(min_value=2, max_value=10),
    )
    @settings(max_examples=50)
    def test_dwell_sum_within_duration(
        self, duration: float, steps: int
    ) -> None:
        """Dwell times never exceed the configured duration."""
        model = RampModel(
            {"start": 0.0, "end": 100.0, "duration": duration, "steps": steps},
            _make_rng(),
        )
        assert sum(model._dwells) <= duration + 1e-10


# ---------------------------------------------------------------------------
# PRD examples
# ---------------------------------------------------------------------------


class TestPRDExamples:
    def test_press_startup_stepped(self) -> None:
        """PRD 4.2.4: press.line_speed startup -- 0 to target, stepped.

        0 to 200 m/min over 3 minutes (180s) with 4 operator steps.
        """
        noise = _make_noise(sigma=2.0)
        model = RampModel(
            {
                "start": 0.0,
                "end": 200.0,
                "duration": 180.0,
                "steps": 4,
                "step_overshoot_pct": 0.03,
                "step_overshoot_decay_s": 7.0,
                "step_dwell_range": [15.0, 45.0],
            },
            _make_rng(),
            noise=noise,
        )
        dt = 0.5  # 500ms ticks
        values = []
        for i in range(int(180.0 / dt)):
            values.append(model.generate(i * dt, dt))

        arr = np.array(values)
        # Should reach near 200 m/min
        assert np.mean(arr[-20:]) == pytest.approx(200.0, abs=10.0)
        # Should see stepped behaviour (not perfectly smooth)
        # Check that values are not perfectly linearly interpolated
        expected_linear = np.linspace(0.0, 200.0, len(values))
        deviations = arr - expected_linear
        assert np.max(np.abs(deviations)) > 5.0  # stepped deviates from linear

    def test_press_shutdown_smooth(self) -> None:
        """PRD 4.2.4: press.line_speed shutdown -- smooth ramp.

        Target to 0 over 45 seconds, smooth (steps=1).
        """
        model = RampModel(
            {
                "start": 200.0,
                "end": 0.0,
                "duration": 45.0,
                "steps": 1,
            },
            _make_rng(),
        )
        dt = 0.5
        values = [model.generate(i * dt, dt) for i in range(int(45.0 / dt))]
        arr = np.array(values)
        # Monotonically decreasing
        assert all(arr[i] <= arr[i - 1] + 1e-10 for i in range(1, len(arr)))
        # Reaches 0
        assert arr[-1] == pytest.approx(0.0, abs=1.0)

    def test_overshoot_magnitude(self) -> None:
        """Overshoot is 3% of step size per PRD default."""
        model = RampModel(
            {
                "start": 0.0,
                "end": 100.0,
                "duration": 200.0,
                "steps": 4,
                "step_overshoot_pct": 0.03,
                "step_overshoot_decay_s": 7.0,
                "step_dwell_range": [40.0, 50.0],
            },
            _make_rng(),
        )
        # First tick: step target = 25, overshoot = 0.03 * 25 = 0.75
        v = model.generate(0.0, DT)
        step_target = 25.0
        overshoot = v - step_target
        expected_overshoot = 0.03 * 25.0 * math.exp(-DT / 7.0)
        assert overshoot == pytest.approx(expected_overshoot, abs=0.01)

    def test_overshoot_decay_time_constant(self) -> None:
        """Overshoot decays with configured time constant."""
        decay_s = 5.0
        model = RampModel(
            {
                "start": 0.0,
                "end": 100.0,
                "duration": 200.0,
                "steps": 4,
                "step_overshoot_pct": 0.10,
                "step_overshoot_decay_s": decay_s,
                "step_dwell_range": [40.0, 50.0],
            },
            _make_rng(),
        )
        step_target = 25.0  # first step
        step_size = 25.0
        initial_overshoot = 0.10 * step_size

        # Collect values for first 10 seconds
        values = [model.generate(i * DT, DT) for i in range(int(10.0 / DT))]

        # At 1 time constant (~5s), overshoot should be ~37% of initial
        idx_1tau = int(decay_s / DT)
        overshoot_1tau = values[idx_1tau] - step_target
        expected_1tau = initial_overshoot * math.exp(-1.0)
        assert overshoot_1tau == pytest.approx(expected_1tau, abs=0.1)


# ---------------------------------------------------------------------------
# Package imports
# ---------------------------------------------------------------------------


class TestPackageImports:
    def test_import_from_models_package(self) -> None:
        from factory_simulator.models import RampModel as RM
        assert RM is RampModel
