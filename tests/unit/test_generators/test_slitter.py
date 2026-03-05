"""Unit tests for the SlitterGenerator (PRD 2.4).

Tests verify:
- 3 signals produced per tick (speed, web_tension, reel_count)
- Schedule-based operation (runs within configured shift window)
- Speed ramps up when scheduled, 0 outside schedule
- Web tension correlates with slitter speed
- Reel count increments proportional to speed when running
- Off state produces zeros / near-zeros
- Determinism (same seed -> same output)

Task 6d.10
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.slitter import SlitterGenerator
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Default schedule: offset=2h (7200s), duration=4h (14400s)
# Slitter runs from sim_time 7200s to 21600s within each 8h shift.
_SCHEDULE_START = 7200.0
_SCHEDULE_END = 21600.0


def _make_slitter_config(
    *,
    schedule_offset_hours: float = 2.0,
    run_duration_hours: float = 4.0,
    target_speed: float = 500.0,
) -> EquipmentConfig:
    """Create a minimal slitter config with all 3 required signals."""
    signals: dict[str, SignalConfig] = {}

    signals["speed"] = SignalConfig(
        model="ramp",
        noise_sigma=5.0,
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=800.0,
        units="m/min",
        params={"ramp_duration_s": 10.0},
    )
    signals["web_tension"] = SignalConfig(
        model="correlated_follower",
        noise_sigma=2.0,
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=500.0,
        units="N",
        params={"base": 0.0, "factor": 0.5},
    )
    signals["reel_count"] = SignalConfig(
        model="counter",
        noise_sigma=0.0,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=9999.0,
        units="count",
        params={"rate": 0.01},
    )

    return EquipmentConfig(
        enabled=True,
        type="slitter",
        signals=signals,
        schedule_offset_hours=schedule_offset_hours,
        run_duration_hours=run_duration_hours,
        target_speed=target_speed,
    )


def _find_signal(results: list[SignalValue], signal_id: str) -> SignalValue:
    for sv in results:
        if sv.signal_id == signal_id:
            return sv
    raise KeyError(f"Signal {signal_id} not found in results")


def _run_ticks(
    gen: SlitterGenerator,
    store: SignalStore,
    *,
    n_ticks: int,
    dt: float = 0.1,
    start_time: float = 0.0,
) -> list[list[SignalValue]]:
    """Run generator for n_ticks, return list of result lists."""
    all_results: list[list[SignalValue]] = []
    sim_time = start_time
    for _ in range(n_ticks):
        sim_time += dt
        results = gen.generate(sim_time, dt, store)
        for sv in results:
            store.set(sv.signal_id, sv.value, sv.timestamp, sv.quality)
        all_results.append(results)
    return all_results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(42)


@pytest.fixture
def store() -> SignalStore:
    return SignalStore()


@pytest.fixture
def slitter(rng: np.random.Generator) -> SlitterGenerator:
    return SlitterGenerator("slitter", _make_slitter_config(), rng)


# ---------------------------------------------------------------------------
# Tests: signal IDs
# ---------------------------------------------------------------------------


class TestSignalIds:
    """Verify all 3 slitter signals are registered."""

    def test_signal_count(self, slitter: SlitterGenerator) -> None:
        assert len(slitter.get_signal_ids()) == 3

    def test_signal_names(self, slitter: SlitterGenerator) -> None:
        ids = set(slitter.get_signal_ids())
        expected = {
            "slitter.speed",
            "slitter.web_tension",
            "slitter.reel_count",
        }
        assert ids == expected


# ---------------------------------------------------------------------------
# Tests: off state (outside schedule window)
# ---------------------------------------------------------------------------


class TestOffState:
    """When outside schedule window, slitter is inactive."""

    def test_speed_zero_outside_schedule(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        """Speed should be 0 when sim_time is before schedule offset."""
        # sim_time=0.1 is well before 7200s schedule start
        results = slitter.generate(0.1, 0.1, store)
        speed = _find_signal(results, "slitter.speed").value
        assert speed == 0.0

    def test_web_tension_zero_outside_schedule(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        """Web tension should be near 0 when speed is 0."""
        results = slitter.generate(0.1, 0.1, store)
        tension = _find_signal(results, "slitter.web_tension").value
        # base=0, gain*0=0, clamped at min_clamp=0
        assert tension == pytest.approx(0.0, abs=5.0)

    def test_reel_count_zero_outside_schedule(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        """Reel count should not increment when speed is 0."""
        results_list = _run_ticks(slitter, store, n_ticks=20, dt=0.1)
        reel = _find_signal(results_list[-1], "slitter.reel_count").value
        assert reel == 0.0

    def test_is_running_false_outside_schedule(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        """is_running property should be False outside schedule."""
        slitter.generate(0.1, 0.1, store)
        assert slitter.is_running is False


# ---------------------------------------------------------------------------
# Tests: running state (inside schedule window)
# ---------------------------------------------------------------------------


class TestRunningState:
    """When inside schedule window, slitter is active."""

    def test_speed_ramps_up_in_schedule(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        """Speed should ramp up toward target when schedule starts."""
        # Run a few ticks outside schedule first
        _run_ticks(slitter, store, n_ticks=5, dt=0.1, start_time=0.0)
        # Jump to schedule start and run ramp-up ticks
        # ramp_duration_s=10, so 200 ticks at dt=0.1 = 20s > ramp
        results_list = _run_ticks(
            slitter, store, n_ticks=200, dt=0.1,
            start_time=_SCHEDULE_START - 0.1,
        )
        final_speed = _find_signal(results_list[-1], "slitter.speed").value
        # Should have ramped up significantly toward target (500)
        assert final_speed > 100.0, f"Speed should ramp up in schedule: {final_speed}"

    def test_is_running_true_in_schedule(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        """is_running property should be True inside schedule."""
        # Place sim_time inside the schedule window
        slitter.generate(_SCHEDULE_START + 1.0, 0.1, store)
        assert slitter.is_running is True

    def test_web_tension_follows_speed(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        """Web tension should increase when speed increases."""
        # Pre-schedule tick
        _run_ticks(slitter, store, n_ticks=5, dt=0.1, start_time=0.0)
        # Enter schedule and let speed ramp up
        results_list = _run_ticks(
            slitter, store, n_ticks=200, dt=0.1,
            start_time=_SCHEDULE_START - 0.1,
        )
        tension = _find_signal(results_list[-1], "slitter.web_tension").value
        # base=0, factor=0.5 * speed (~500) = ~250, plus noise
        assert tension > 50.0, f"Tension should follow speed: {tension}"

    def test_reel_count_increments_when_running(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        """Reel count should increment while slitter is running."""
        # Pre-schedule tick
        _run_ticks(slitter, store, n_ticks=5, dt=0.1, start_time=0.0)
        # Enter schedule and run for a while
        results_list = _run_ticks(
            slitter, store, n_ticks=300, dt=0.1,
            start_time=_SCHEDULE_START - 0.1,
        )
        reel = _find_signal(results_list[-1], "slitter.reel_count").value
        assert reel > 0.0, f"Reel count should increment: {reel}"


# ---------------------------------------------------------------------------
# Tests: schedule transitions
# ---------------------------------------------------------------------------


class TestScheduleTransitions:
    """Speed ramps down when schedule ends."""

    def test_speed_ramps_down_after_schedule(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        """Speed should ramp down toward 0 when schedule window ends."""
        # Run inside schedule to build up speed
        results_in = _run_ticks(
            slitter, store, n_ticks=200, dt=0.1,
            start_time=_SCHEDULE_END - 25.0,
        )
        speed_before = _find_signal(results_in[-1], "slitter.speed").value
        assert speed_before > 50.0, f"Should be running: {speed_before}"

        # Now run past the end of the schedule
        results_out = _run_ticks(
            slitter, store, n_ticks=500, dt=0.1,
            start_time=_SCHEDULE_END - 5.0,
        )
        speed_after = _find_signal(results_out[-1], "slitter.speed").value
        assert speed_after < speed_before, (
            f"Speed should decrease after schedule: {speed_before} -> {speed_after}"
        )


# ---------------------------------------------------------------------------
# Tests: all signals present per tick
# ---------------------------------------------------------------------------


class TestAllSignals:
    """Every tick produces exactly 3 signals."""

    def test_signal_count_per_tick(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        results = slitter.generate(_SCHEDULE_START + 1.0, 0.1, store)
        assert len(results) == 3

    def test_all_signals_have_quality_good(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        results = slitter.generate(_SCHEDULE_START + 1.0, 0.1, store)
        for sv in results:
            assert sv.quality == "good"


# ---------------------------------------------------------------------------
# Tests: determinism (CLAUDE.md Rule 13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed -> identical output sequence."""

    def test_slitter_deterministic(self, store: SignalStore) -> None:
        cfg = _make_slitter_config()
        gen1 = SlitterGenerator("slitter", cfg, np.random.default_rng(99))
        gen2 = SlitterGenerator("slitter", cfg, np.random.default_rng(99))

        sim_time = _SCHEDULE_START
        dt = 0.1
        r1: list[SignalValue] = []
        r2: list[SignalValue] = []
        for _ in range(50):
            sim_time += dt
            r1 = gen1.generate(sim_time, dt, store)
            r2 = gen2.generate(sim_time, dt, store)

        for sv1, sv2 in zip(r1, r2, strict=True):
            assert sv1.signal_id == sv2.signal_id
            assert sv1.value == sv2.value, (
                f"{sv1.signal_id}: {sv1.value} != {sv2.value}"
            )
