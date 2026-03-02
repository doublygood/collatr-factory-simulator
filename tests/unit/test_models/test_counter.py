"""Tests for the CounterModel.

PRD Reference: Section 4.2.6 (Counter Increment)
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factory_simulator.models.counter import CounterModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEED = 42
DT = 0.1  # 100 ms tick


def _make_rng(seed: int = SEED) -> np.random.Generator:
    return np.random.default_rng(seed)


def _make_model(
    params: dict[str, object] | None = None,
    seed: int = SEED,
) -> CounterModel:
    p = params if params is not None else {"rate": 1.0}
    return CounterModel(p, _make_rng(seed))


def _run_ticks(
    model: CounterModel, n: int, dt: float = DT, speed: float | None = None
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
        m = _make_model({"rate": 1.0})
        assert m.rate == 1.0
        assert m.rollover_value is None
        assert m.reset_on_job_change is False
        assert m.max_before_reset is None
        assert m.value == 0.0
        assert m.speed == 0.0

    def test_explicit_params(self) -> None:
        m = _make_model({
            "rate": 0.5,
            "rollover_value": 1000.0,
            "reset_on_job_change": True,
            "max_before_reset": 500.0,
            "initial_value": 10.0,
        })
        assert m.rate == 0.5
        assert m.rollover_value == 1000.0
        assert m.reset_on_job_change is True
        assert m.max_before_reset == 500.0
        assert m.value == 10.0

    def test_rollover_alias(self) -> None:
        """Config uses 'rollover' not 'rollover_value' -- both should work."""
        m = _make_model({"rate": 1.0, "rollover": 999})
        assert m.rollover_value == 999.0

    def test_rollover_value_takes_precedence(self) -> None:
        """rollover_value takes precedence over rollover alias."""
        m = _make_model({"rate": 1.0, "rollover_value": 500, "rollover": 999})
        assert m.rollover_value == 500.0

    def test_invalid_rate_negative(self) -> None:
        with pytest.raises(ValueError, match="rate must be >= 0"):
            _make_model({"rate": -1.0})

    def test_zero_rate_allowed(self) -> None:
        m = _make_model({"rate": 0.0})
        assert m.rate == 0.0

    def test_invalid_rollover_zero(self) -> None:
        with pytest.raises(ValueError, match="rollover_value must be > 0"):
            _make_model({"rate": 1.0, "rollover_value": 0.0})

    def test_invalid_rollover_negative(self) -> None:
        with pytest.raises(ValueError, match="rollover_value must be > 0"):
            _make_model({"rate": 1.0, "rollover_value": -10.0})

    def test_invalid_max_before_reset_zero(self) -> None:
        with pytest.raises(ValueError, match="max_before_reset must be > 0"):
            _make_model({"rate": 1.0, "max_before_reset": 0.0})

    def test_invalid_initial_value_negative(self) -> None:
        with pytest.raises(ValueError, match="initial_value must be >= 0"):
            _make_model({"rate": 1.0, "initial_value": -5.0})


# ===================================================================
# Basic Incrementing
# ===================================================================


class TestBasicIncrement:
    def test_zero_speed_no_increment(self) -> None:
        """When speed is zero, counter does not change."""
        m = _make_model({"rate": 1.0})
        vals = _run_ticks(m, 10, speed=0.0)
        assert all(v == 0.0 for v in vals)

    def test_constant_speed_linear_increment(self) -> None:
        """Counter should increase linearly with constant speed."""
        m = _make_model({"rate": 1.0})
        m.set_speed(100.0)  # 100 m/min
        vals = _run_ticks(m, 10)
        # Each tick: 1.0 * 100 * 0.1 = 10.0 increment
        for i, v in enumerate(vals, 1):
            assert v == pytest.approx(i * 10.0)

    def test_rate_scaling(self) -> None:
        """Different rates produce proportional counts."""
        m1 = _make_model({"rate": 1.0})
        m2 = _make_model({"rate": 0.5})
        m1.set_speed(100.0)
        m2.set_speed(100.0)
        v1 = _run_ticks(m1, 10)
        v2 = _run_ticks(m2, 10)
        for a, b in zip(v1, v2, strict=True):
            assert a == pytest.approx(b * 2.0)

    def test_speed_scaling(self) -> None:
        """Higher speed produces proportionally higher counts."""
        m1 = _make_model({"rate": 1.0})
        m2 = _make_model({"rate": 1.0})
        m1.set_speed(100.0)
        m2.set_speed(200.0)
        v1 = _run_ticks(m1, 10)
        v2 = _run_ticks(m2, 10)
        for a, b in zip(v1, v2, strict=True):
            assert b == pytest.approx(a * 2.0)

    def test_dt_scaling(self) -> None:
        """Larger dt produces proportionally larger increments."""
        m1 = _make_model({"rate": 1.0})
        m2 = _make_model({"rate": 1.0})
        m1.set_speed(100.0)
        m2.set_speed(100.0)
        v1 = _run_ticks(m1, 10, dt=0.1)
        v2 = _run_ticks(m2, 5, dt=0.2)
        # After 1 second of sim time both should agree
        assert v1[-1] == pytest.approx(v2[-1])

    def test_accumulates_across_ticks(self) -> None:
        """Counter should accumulate, not reset each tick."""
        m = _make_model({"rate": 1.0})
        m.set_speed(60.0)  # 60 m/min
        # After 10 ticks at dt=0.1: total = 10 * 1.0 * 60 * 0.1 = 60
        vals = _run_ticks(m, 10)
        assert vals[-1] == pytest.approx(60.0)

    def test_zero_rate_no_increment(self) -> None:
        """Zero rate means counter stays at initial value."""
        m = _make_model({"rate": 0.0, "initial_value": 42.0})
        m.set_speed(200.0)
        vals = _run_ticks(m, 10)
        assert all(v == pytest.approx(42.0) for v in vals)

    def test_initial_value_offset(self) -> None:
        """Counter starts from initial_value, not zero."""
        m = _make_model({"rate": 1.0, "initial_value": 100.0})
        m.set_speed(100.0)
        vals = _run_ticks(m, 5)
        # First value: 100 + 1.0 * 100 * 0.1 = 110
        assert vals[0] == pytest.approx(110.0)


# ===================================================================
# Speed Changes
# ===================================================================


class TestSpeedChanges:
    def test_set_speed(self) -> None:
        m = _make_model({"rate": 1.0})
        m.set_speed(150.0)
        assert m.speed == 150.0

    def test_speed_change_affects_increment(self) -> None:
        """Changing speed mid-run changes increment rate."""
        m = _make_model({"rate": 1.0})
        m.set_speed(100.0)
        v1 = m.generate(0.0, DT)  # +10
        assert v1 == pytest.approx(10.0)

        m.set_speed(200.0)
        v2 = m.generate(DT, DT)  # +20
        assert v2 == pytest.approx(30.0)  # 10 + 20

    def test_speed_to_zero_stops_counting(self) -> None:
        """Setting speed to zero pauses the counter."""
        m = _make_model({"rate": 1.0})
        m.set_speed(100.0)
        _run_ticks(m, 5)
        val_before = m.value

        m.set_speed(0.0)
        _run_ticks(m, 10)
        assert m.value == pytest.approx(val_before)


# ===================================================================
# Rollover
# ===================================================================


class TestRollover:
    def test_no_rollover_by_default(self) -> None:
        """Without rollover, counter grows without bound."""
        m = _make_model({"rate": 1.0})
        m.set_speed(1000.0)
        _run_ticks(m, 10000, dt=1.0)  # 10 million
        assert m.value == pytest.approx(10_000_000.0)

    def test_rollover_wraps_to_zero(self) -> None:
        """Counter wraps to zero at rollover_value."""
        m = _make_model({"rate": 1.0, "rollover": 100.0})
        m.set_speed(100.0)
        # Each tick: +10. After 10 ticks: 100 -> wraps to 0
        vals = _run_ticks(m, 10)
        assert vals[-1] == pytest.approx(0.0)

    def test_rollover_preserves_remainder(self) -> None:
        """On rollover, excess is preserved via modulo."""
        m = _make_model({"rate": 1.0, "rollover": 100.0})
        m.set_speed(100.0)
        # Each tick: +10. After 11 ticks: 110 % 100 = 10
        vals = _run_ticks(m, 11)
        assert vals[-1] == pytest.approx(10.0)

    def test_rollover_multiple_wraps(self) -> None:
        """Counter can wrap multiple times in a single tick."""
        m = _make_model({"rate": 1.0, "rollover": 50.0})
        m.set_speed(100.0)
        # Single tick at dt=1.0: increment = 100. 100 % 50 = 0
        v = m.generate(0.0, 1.0)
        assert v == pytest.approx(0.0)

    def test_rollover_999(self) -> None:
        """PRD: FPGA_Head_PrintedTotal wrapping at 999."""
        m = _make_model({"rate": 1.0, "rollover": 999})
        m.set_speed(100.0)
        # Run until wraps: 999 / (100 * 0.1) = 99.9 ticks -> wrap at tick 100
        vals = _run_ticks(m, 100)
        assert vals[-1] == pytest.approx(1000.0 % 999)  # 1.0

    def test_rollover_large_uint32(self) -> None:
        """Press counters use uint32 (max ~4.3 billion)."""
        m = _make_model({"rate": 1.0, "rollover": 4_294_967_295})
        m.set_speed(200.0)
        # Even at 200 m/min for 1000 ticks at dt=1.0: 200000 -- well below rollover
        _run_ticks(m, 1000, dt=1.0)
        assert m.value == pytest.approx(200_000.0)


# ===================================================================
# Max Before Reset
# ===================================================================


class TestMaxBeforeReset:
    def test_auto_resets_at_threshold(self) -> None:
        """Counter resets to zero when reaching max_before_reset."""
        m = _make_model({"rate": 1.0, "max_before_reset": 100.0})
        m.set_speed(100.0)
        # Each tick: +10. After 10 ticks: 100 -> resets to 0
        vals = _run_ticks(m, 10)
        assert vals[-1] == pytest.approx(0.0)

    def test_continues_after_reset(self) -> None:
        """Counter continues incrementing after max_before_reset."""
        m = _make_model({"rate": 1.0, "max_before_reset": 50.0})
        m.set_speed(100.0)
        # Tick 1-5: 10,20,30,40,50->0
        # Tick 6: 0+10=10
        vals = _run_ticks(m, 6)
        assert vals[4] == pytest.approx(0.0)  # reset at 50
        assert vals[5] == pytest.approx(10.0)  # continues

    def test_max_before_reset_disabled_by_default(self) -> None:
        m = _make_model({"rate": 1.0})
        m.set_speed(100.0)
        _run_ticks(m, 100)
        assert m.value > 0.0  # never resets


# ===================================================================
# Rollover + Max Before Reset Interaction
# ===================================================================


class TestRolloverAndMaxBeforeReset:
    def test_max_before_reset_takes_effect_after_rollover(self) -> None:
        """Both rollover and max_before_reset can be configured.
        Rollover is applied first (modulo), then max_before_reset check."""
        # rollover=1000, max_before_reset=500
        # If counter reaches 500 after rollover, it resets
        m = _make_model({
            "rate": 1.0,
            "rollover": 1000.0,
            "max_before_reset": 500.0,
        })
        m.set_speed(100.0)
        # Each tick: +10. After 50 ticks: 500 -> max_before_reset fires
        vals = _run_ticks(m, 50)
        assert vals[-1] == pytest.approx(0.0)


# ===================================================================
# Reset on Job Change
# ===================================================================


class TestResetOnJobChange:
    def test_reset_counter_zeros_value(self) -> None:
        m = _make_model({"rate": 1.0, "reset_on_job_change": True})
        m.set_speed(100.0)
        _run_ticks(m, 10)
        assert m.value > 0.0
        m.reset_counter()
        assert m.value == 0.0

    def test_reset_counter_continues_counting(self) -> None:
        """After reset_counter, counting resumes from zero."""
        m = _make_model({"rate": 1.0, "reset_on_job_change": True})
        m.set_speed(100.0)
        _run_ticks(m, 5)
        m.reset_counter()
        vals = _run_ticks(m, 3)
        assert vals[0] == pytest.approx(10.0)  # Fresh start: 0 + 10

    def test_reset_counter_works_regardless_of_flag(self) -> None:
        """reset_counter() works even without reset_on_job_change.
        The flag is informational for the scenario engine."""
        m = _make_model({"rate": 1.0, "reset_on_job_change": False})
        m.set_speed(100.0)
        _run_ticks(m, 5)
        m.reset_counter()
        assert m.value == 0.0


# ===================================================================
# Reset (full model reset)
# ===================================================================


class TestReset:
    def test_reset_restores_initial_value(self) -> None:
        m = _make_model({"rate": 1.0, "initial_value": 50.0})
        m.set_speed(100.0)
        _run_ticks(m, 10)
        assert m.value != 50.0
        m.reset()
        assert m.value == pytest.approx(50.0)

    def test_reset_zeros_speed(self) -> None:
        m = _make_model({"rate": 1.0})
        m.set_speed(200.0)
        m.reset()
        assert m.speed == 0.0

    def test_reset_defaults_to_zero(self) -> None:
        m = _make_model({"rate": 1.0})
        m.set_speed(100.0)
        _run_ticks(m, 10)
        m.reset()
        assert m.value == 0.0


# ===================================================================
# Determinism (Rule 13)
# ===================================================================


class TestDeterminism:
    def test_same_seed_same_output(self) -> None:
        """Counter is fully deterministic -- same inputs = same output."""
        m1 = _make_model({"rate": 1.0}, seed=99)
        m2 = _make_model({"rate": 1.0}, seed=99)
        m1.set_speed(150.0)
        m2.set_speed(150.0)
        v1 = _run_ticks(m1, 20)
        v2 = _run_ticks(m2, 20)
        assert v1 == v2

    def test_counter_is_deterministic_regardless_of_seed(self) -> None:
        """Counter model has no stochastic component -- different seeds
        produce identical output."""
        m1 = _make_model({"rate": 1.0}, seed=1)
        m2 = _make_model({"rate": 1.0}, seed=999)
        m1.set_speed(100.0)
        m2.set_speed(100.0)
        v1 = _run_ticks(m1, 20)
        v2 = _run_ticks(m2, 20)
        assert v1 == v2


# ===================================================================
# Time Compression (PRD 4.2.6 note)
# ===================================================================


class TestTimeCompression:
    def test_same_total_at_different_tick_rates(self) -> None:
        """At different dt values, same total sim time gives same count.
        Rule 6: simulated time invariant."""
        m1 = _make_model({"rate": 1.0})
        m2 = _make_model({"rate": 1.0})
        m1.set_speed(100.0)
        m2.set_speed(100.0)

        # 10 seconds of sim time
        _run_ticks(m1, 100, dt=0.1)  # 100 ticks at 0.1s
        _run_ticks(m2, 10, dt=1.0)  # 10 ticks at 1.0s

        # Both should give: rate * speed * total_time = 1.0 * 100 * 10 = 1000
        assert m1.value == pytest.approx(1000.0)
        assert m2.value == pytest.approx(1000.0)

    def test_compressed_run_high_count(self) -> None:
        """PRD: At 100x speed, 200/min counter reaches 99999 in ~8 real min.
        Sim: 200 m/min * rate 1.0 * 800s sim_time = 160000 counts."""
        m = _make_model({"rate": 1.0})
        m.set_speed(200.0)
        # 800 seconds sim time = 8 minutes real at 100x
        _run_ticks(m, 800, dt=1.0)
        assert m.value == pytest.approx(160_000.0)


# ===================================================================
# PRD Examples
# ===================================================================


class TestPrdExamples:
    def test_impression_count(self) -> None:
        """press.impression_count: rate=1.0, rollover=999999999."""
        m = _make_model({"rate": 1.0, "rollover": 999_999_999})
        m.set_speed(200.0)  # 200 m/min
        # 1 hour at dt=1.0: 200 * 3600 = 720000 impressions
        _run_ticks(m, 3600, dt=1.0)
        assert m.value == pytest.approx(720_000.0)

    def test_good_count(self) -> None:
        """press.good_count: rate=0.97, rollover=999999999.
        97% of impressions are good."""
        m = _make_model({"rate": 0.97, "rollover": 999_999_999})
        m.set_speed(200.0)
        _run_ticks(m, 3600, dt=1.0)
        expected = 0.97 * 200.0 * 3600.0
        assert m.value == pytest.approx(expected)

    def test_waste_count(self) -> None:
        """press.waste_count: rate=0.03, rollover=99999.
        3% of impressions are waste."""
        m = _make_model({"rate": 0.03, "rollover": 99_999})
        m.set_speed(200.0)
        _run_ticks(m, 3600, dt=1.0)
        expected = 0.03 * 200.0 * 3600.0
        assert m.value == pytest.approx(expected)

    def test_good_plus_waste_equals_impression(self) -> None:
        """Good + waste rates should sum to impression rate."""
        speed = 200.0
        dt = 1.0
        ticks = 3600

        m_impression = _make_model({"rate": 1.0})
        m_good = _make_model({"rate": 0.97})
        m_waste = _make_model({"rate": 0.03})

        for m in (m_impression, m_good, m_waste):
            m.set_speed(speed)
            _run_ticks(m, ticks, dt=dt)

        assert m_good.value + m_waste.value == pytest.approx(m_impression.value)

    def test_ink_consumption(self) -> None:
        """coder.ink_consumption_ml: rate=0.01, rollover=999999."""
        m = _make_model({"rate": 0.01, "rollover": 999_999})
        m.set_speed(200.0)
        # 1 hour: 0.01 * 200 * 3600 = 7200 ml
        _run_ticks(m, 3600, dt=1.0)
        assert m.value == pytest.approx(7200.0)

    def test_cumulative_kwh(self) -> None:
        """energy.cumulative_kwh: rate=0.001, rollover=999999.
        Speed here represents power not line_speed -- but the model
        is the same accumulator."""
        m = _make_model({"rate": 0.001, "rollover": 999_999})
        m.set_speed(100.0)  # 100 kW
        # 1 hour: 0.001 * 100 * 3600 = 360 kWh
        _run_ticks(m, 3600, dt=1.0)
        assert m.value == pytest.approx(360.0)


# ===================================================================
# Property-Based Tests (Hypothesis)
# ===================================================================


class TestPropertyBased:
    @given(
        rate=st.floats(min_value=0.0, max_value=100.0),
        speed=st.floats(min_value=0.0, max_value=1000.0),
        dt=st.floats(min_value=0.001, max_value=10.0),
    )
    @settings(max_examples=100)
    def test_output_always_finite(
        self, rate: float, speed: float, dt: float
    ) -> None:
        m = _make_model({"rate": rate})
        m.set_speed(speed)
        v = m.generate(0.0, dt)
        assert np.isfinite(v)

    @given(
        rate=st.floats(min_value=0.0, max_value=10.0),
        speed=st.floats(min_value=0.0, max_value=500.0),
    )
    @settings(max_examples=100)
    def test_counter_never_negative_from_zero(
        self, rate: float, speed: float
    ) -> None:
        """Counter starting at 0 with non-negative rate and speed
        should never go negative."""
        m = _make_model({"rate": rate})
        m.set_speed(speed)
        vals = _run_ticks(m, 10)
        assert all(v >= 0.0 for v in vals)

    @given(
        rate=st.floats(min_value=0.01, max_value=10.0),
        speed=st.floats(min_value=1.0, max_value=500.0),
    )
    @settings(max_examples=50)
    def test_monotonically_increasing(self, rate: float, speed: float) -> None:
        """With positive rate and speed, counter is strictly increasing."""
        m = _make_model({"rate": rate})
        m.set_speed(speed)
        vals = _run_ticks(m, 20)
        for i in range(1, len(vals)):
            assert vals[i] > vals[i - 1]

    @given(
        rollover=st.floats(min_value=1.0, max_value=10000.0),
        rate=st.floats(min_value=0.1, max_value=10.0),
        speed=st.floats(min_value=1.0, max_value=500.0),
    )
    @settings(max_examples=50)
    def test_rollover_keeps_value_below_threshold(
        self, rollover: float, rate: float, speed: float
    ) -> None:
        """With rollover, counter stays below rollover_value."""
        m = _make_model({"rate": rate, "rollover": rollover})
        m.set_speed(speed)
        vals = _run_ticks(m, 100)
        assert all(v < rollover for v in vals)

    @given(seed=st.integers(min_value=0, max_value=2**32 - 1))
    @settings(max_examples=50)
    def test_determinism_any_seed(self, seed: int) -> None:
        """Counter output is deterministic regardless of seed
        (no stochastic component)."""
        m1 = _make_model({"rate": 1.0}, seed=seed)
        m2 = _make_model({"rate": 1.0}, seed=seed + 1)
        m1.set_speed(100.0)
        m2.set_speed(100.0)
        v1 = _run_ticks(m1, 10)
        v2 = _run_ticks(m2, 10)
        assert v1 == v2


# ===================================================================
# Package Imports
# ===================================================================


class TestPackageImports:
    def test_import_from_models_package(self) -> None:
        from factory_simulator.models import CounterModel as CM

        assert CM is CounterModel

    def test_in_all(self) -> None:
        import factory_simulator.models as models

        assert "CounterModel" in models.__all__
