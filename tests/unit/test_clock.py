"""Tests for the SimulationClock.

Validates:
- Tick advances sim time correctly
- Time scale produces correct simulated elapsed time at 1x/10x/100x
- dt is deterministic and independent of wall-clock speed
- ISO format output is correct
- reset() works
- from_config() factory method
- Edge cases: validation of bad inputs

PRD Reference: Section 4.1 (Principles 4-5), Section 4.4
CLAUDE.md Rule 6: Simulated Time Invariant
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from factory_simulator.clock import SimulationClock

# ---------------------------------------------------------------------------
# Construction & validation
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_default_values(self) -> None:
        clock = SimulationClock()
        assert clock.tick_interval_ms == 100
        assert clock.time_scale == 1.0
        assert clock.sim_time == 0.0
        assert clock.tick_count == 0

    def test_custom_values(self) -> None:
        clock = SimulationClock(tick_interval_ms=500, time_scale=10.0)
        assert clock.tick_interval_ms == 500
        assert clock.time_scale == 10.0

    def test_negative_tick_interval_rejected(self) -> None:
        with pytest.raises(ValueError, match="tick_interval_ms must be positive"):
            SimulationClock(tick_interval_ms=-1)

    def test_zero_tick_interval_rejected(self) -> None:
        with pytest.raises(ValueError, match="tick_interval_ms must be positive"):
            SimulationClock(tick_interval_ms=0)

    def test_negative_time_scale_rejected(self) -> None:
        with pytest.raises(ValueError, match="time_scale must be positive"):
            SimulationClock(time_scale=-1.0)

    def test_zero_time_scale_rejected(self) -> None:
        with pytest.raises(ValueError, match="time_scale must be positive"):
            SimulationClock(time_scale=0.0)

    def test_custom_start_time(self) -> None:
        clock = SimulationClock(start_time="2025-06-01T08:00:00+00:00")
        assert clock.sim_time_iso() == "2025-06-01T08:00:00+00:00"

    def test_start_time_without_tz_defaults_to_utc(self) -> None:
        clock = SimulationClock(start_time="2025-06-01T08:00:00")
        dt = clock.sim_datetime()
        assert dt.tzinfo == UTC


# ---------------------------------------------------------------------------
# Tick mechanics
# ---------------------------------------------------------------------------


class TestTick:
    def test_single_tick_1x(self) -> None:
        """At 1x, a 100ms tick advances sim time by 0.1 seconds."""
        clock = SimulationClock(tick_interval_ms=100, time_scale=1.0)
        new_time = clock.tick()
        assert new_time == pytest.approx(0.1)
        assert clock.sim_time == pytest.approx(0.1)
        assert clock.tick_count == 1

    def test_multiple_ticks_1x(self) -> None:
        clock = SimulationClock(tick_interval_ms=100, time_scale=1.0)
        for _ in range(10):
            clock.tick()
        assert clock.sim_time == pytest.approx(1.0)
        assert clock.tick_count == 10

    def test_single_tick_10x(self) -> None:
        """At 10x, a 100ms tick advances sim time by 1.0 seconds."""
        clock = SimulationClock(tick_interval_ms=100, time_scale=10.0)
        clock.tick()
        assert clock.sim_time == pytest.approx(1.0)

    def test_single_tick_100x(self) -> None:
        """At 100x, a 100ms tick advances sim time by 10.0 seconds."""
        clock = SimulationClock(tick_interval_ms=100, time_scale=100.0)
        clock.tick()
        assert clock.sim_time == pytest.approx(10.0)

    def test_500ms_tick_interval(self) -> None:
        """PRD 4.1: tick_interval_ms=500 for fastest signals."""
        clock = SimulationClock(tick_interval_ms=500, time_scale=1.0)
        clock.tick()
        assert clock.sim_time == pytest.approx(0.5)

    def test_dt_property(self) -> None:
        clock = SimulationClock(tick_interval_ms=100, time_scale=10.0)
        assert clock.dt == pytest.approx(1.0)

    def test_tick_returns_new_sim_time(self) -> None:
        clock = SimulationClock(tick_interval_ms=100, time_scale=1.0)
        t1 = clock.tick()
        t2 = clock.tick()
        assert t1 == pytest.approx(0.1)
        assert t2 == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# Simulated time invariant (Rule 6)
# ---------------------------------------------------------------------------


class TestSimulatedTimeInvariant:
    def test_1x_and_10x_same_sim_time_after_same_ticks(self) -> None:
        """PRD 4.1 Principle 5: same number of ticks at different time_scale
        produce the same *number of simulated seconds* only when multiplied
        by time_scale.  But the key invariant is: signal values depend on
        sim_time, not wall-clock speed.
        """
        clock_1x = SimulationClock(tick_interval_ms=100, time_scale=1.0)
        clock_10x = SimulationClock(tick_interval_ms=100, time_scale=10.0)

        # Run both for 100 ticks
        for _ in range(100):
            clock_1x.tick()
            clock_10x.tick()

        # 1x: 100 ticks * 0.1s = 10s
        assert clock_1x.sim_time == pytest.approx(10.0)
        # 10x: 100 ticks * 1.0s = 100s
        assert clock_10x.sim_time == pytest.approx(100.0)

    def test_dt_is_deterministic(self) -> None:
        """dt does not change between ticks -- it's purely a function of
        config, not wall-clock timing."""
        clock = SimulationClock(tick_interval_ms=100, time_scale=5.0)
        dt_before = clock.dt
        for _ in range(50):
            clock.tick()
        dt_after = clock.dt
        assert dt_before == dt_after

    def test_no_wall_clock_dependency(self) -> None:
        """Two clocks with the same config produce identical sim_time after
        the same number of ticks, regardless of when they're run."""
        c1 = SimulationClock(tick_interval_ms=200, time_scale=3.0)
        c2 = SimulationClock(tick_interval_ms=200, time_scale=3.0)
        for _ in range(1000):
            c1.tick()
        for _ in range(1000):
            c2.tick()
        assert c1.sim_time == c2.sim_time
        assert c1.tick_count == c2.tick_count


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


class TestTimeHelpers:
    def test_elapsed_seconds(self) -> None:
        clock = SimulationClock(tick_interval_ms=100, time_scale=1.0)
        for _ in range(10):
            clock.tick()
        assert clock.elapsed_seconds() == pytest.approx(1.0)

    def test_sim_datetime_advances(self) -> None:
        clock = SimulationClock(
            tick_interval_ms=100,
            time_scale=1.0,
            start_time="2024-01-15T06:00:00+00:00",
        )
        # 36000 ticks at 100ms/1x = 3600s = 1 hour
        for _ in range(36000):
            clock.tick()
        expected = datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        assert clock.sim_datetime() == expected

    def test_sim_time_iso_format(self) -> None:
        clock = SimulationClock(start_time="2024-01-15T06:00:00+00:00")
        assert clock.sim_time_iso() == "2024-01-15T06:00:00+00:00"
        clock.tick()  # +0.1s
        iso = clock.sim_time_iso()
        assert iso.startswith("2024-01-15T06:00:00")
        assert "+00:00" in iso

    def test_sim_datetime_at_10x(self) -> None:
        """At 10x, 3600 ticks * 100ms * 10 = 3600s = 1 hour sim."""
        clock = SimulationClock(
            tick_interval_ms=100,
            time_scale=10.0,
            start_time="2024-01-15T06:00:00+00:00",
        )
        for _ in range(3600):
            clock.tick()
        expected = datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        assert clock.sim_datetime() == expected

    def test_sim_datetime_preserves_start_timezone(self) -> None:
        clock = SimulationClock(start_time="2024-01-15T06:00:00+01:00")
        dt = clock.sim_datetime()
        assert dt.isoformat() == "2024-01-15T06:00:00+01:00"


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_zeroes_sim_time(self) -> None:
        clock = SimulationClock()
        for _ in range(100):
            clock.tick()
        assert clock.sim_time > 0
        clock.reset()
        assert clock.sim_time == 0.0
        assert clock.tick_count == 0

    def test_reset_then_tick(self) -> None:
        clock = SimulationClock(tick_interval_ms=100, time_scale=1.0)
        for _ in range(50):
            clock.tick()
        clock.reset()
        clock.tick()
        assert clock.sim_time == pytest.approx(0.1)
        assert clock.tick_count == 1


# ---------------------------------------------------------------------------
# from_config factory
# ---------------------------------------------------------------------------


class TestFromConfig:
    def test_from_simulation_config(self) -> None:
        from factory_simulator.config import SimulationConfig

        cfg = SimulationConfig(
            tick_interval_ms=200,
            time_scale=5.0,
            start_time="2025-03-01T12:00:00+00:00",
        )
        clock = SimulationClock.from_config(cfg)
        assert clock.tick_interval_ms == 200
        assert clock.time_scale == 5.0
        assert clock.sim_time_iso() == "2025-03-01T12:00:00+00:00"

    def test_from_config_defaults(self) -> None:
        from factory_simulator.config import SimulationConfig

        cfg = SimulationConfig()
        clock = SimulationClock.from_config(cfg)
        assert clock.tick_interval_ms == 100
        assert clock.time_scale == 1.0


# ---------------------------------------------------------------------------
# Large simulation runs
# ---------------------------------------------------------------------------


class TestLargeRuns:
    def test_one_simulated_hour_at_100x(self) -> None:
        """100x: each 100ms tick = 10s sim. 360 ticks = 3600s = 1 hour."""
        clock = SimulationClock(tick_interval_ms=100, time_scale=100.0)
        for _ in range(360):
            clock.tick()
        assert clock.sim_time == pytest.approx(3600.0)
        assert clock.tick_count == 360

    def test_one_simulated_day_at_1000x(self) -> None:
        """1000x: each 100ms tick = 100s sim. 864 ticks = 86400s = 1 day."""
        clock = SimulationClock(tick_interval_ms=100, time_scale=1000.0)
        for _ in range(864):
            clock.tick()
        assert clock.sim_time == pytest.approx(86400.0)

    def test_floating_point_accumulation(self) -> None:
        """Verify sim_time doesn't accumulate significant float error
        over many ticks."""
        clock = SimulationClock(tick_interval_ms=100, time_scale=1.0)
        for _ in range(100_000):
            clock.tick()
        # 100,000 ticks * 0.1s = 10,000s
        # Allow tiny float tolerance
        assert clock.sim_time == pytest.approx(10_000.0, rel=1e-9)
