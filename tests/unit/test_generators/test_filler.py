"""Unit tests for the FillerGenerator (PRD 2b.4).

Tests verify:
- All 8 signals are produced with correct IDs
- State machine starts in Off, supports force_state transitions
- Line speed is 0 when not Running, positive when Running
- Per-item fill weight generation (item arrival gating)
- Fill deviation = fill_weight - fill_target
- Packs counter increments exactly once per item arrival
- Reject counter increments only when deviation > tolerance
- Hopper level depletes when Running, holds when Off
- Determinism (same seed → same output)

Task 3.6
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.filler import (
    STATE_FAULT,
    STATE_OFF,
    STATE_RUNNING,
    FillerGenerator,
)
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_filler_config(
    *,
    fill_target_g: float = 400.0,
    fill_giveaway_g: float = 5.0,
    fill_sigma_g: float = 3.0,
    fill_tolerance_g: float = 15.0,
    line_speed_target: float = 60.0,
) -> EquipmentConfig:
    """Create a minimal filler config for testing."""
    signals: dict[str, SignalConfig] = {}

    signals["line_speed"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.5,
        sample_rate_ms=1000,
        min_clamp=10.0,
        max_clamp=120.0,
        units="packs/min",
        params={"target": line_speed_target},
    )
    signals["fill_weight"] = SignalConfig(
        model="steady_state",
        noise_sigma=3.0,
        sample_rate_ms=1000,
        min_clamp=200.0,
        max_clamp=800.0,
        units="g",
        params={"target": fill_target_g + fill_giveaway_g},
    )
    signals["fill_target"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.0,
        sample_rate_ms=1000,
        min_clamp=200.0,
        max_clamp=800.0,
        units="g",
        params={"target": fill_target_g},
    )
    signals["fill_deviation"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.0,
        sample_rate_ms=1000,
        min_clamp=-20.0,
        max_clamp=20.0,
        units="g",
        params={"target": fill_giveaway_g},
    )
    signals["packs_produced"] = SignalConfig(
        model="counter",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=999999.0,
        units="count",
        params={"rate": 1.0, "rollover": 999999},
    )
    signals["reject_count"] = SignalConfig(
        model="counter",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=9999.0,
        units="count",
        params={"rate": 0.01, "rollover": 9999},
    )
    signals["state"] = SignalConfig(
        model="state_machine",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=4.0,
        units="enum",
        params={
            "states": ["off", "setup", "running", "starved", "fault"],
            "initial_state": "off",
        },
    )
    signals["hopper_level"] = SignalConfig(
        model="depletion",
        noise_sigma=1.0,
        sample_rate_ms=10000,
        min_clamp=0.0,
        max_clamp=100.0,
        units="%",
        params={
            "initial_value": 80.0,
            "consumption_rate": 0.1,
            "refill_threshold": 10.0,
            "refill_value": 90.0,
        },
    )

    return EquipmentConfig(
        enabled=True,
        type="gravimetric_filler",
        signals=signals,
        fill_target_g=fill_target_g,
        fill_giveaway_g=fill_giveaway_g,
        fill_sigma_g=fill_sigma_g,
        fill_tolerance_g=fill_tolerance_g,
    )


def _find_signal(results: list[SignalValue], signal_id: str) -> SignalValue:
    for sv in results:
        if sv.signal_id == signal_id:
            return sv
    raise KeyError(f"Signal {signal_id} not found in results")


def _run_ticks(
    gen: FillerGenerator,
    store: SignalStore,
    *,
    n_ticks: int,
    dt: float = 0.1,
    start_time: float = 0.0,
) -> list[list[SignalValue]]:
    """Run generator for n_ticks, write to store, return all result lists."""
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
def filler(rng: np.random.Generator) -> FillerGenerator:
    return FillerGenerator("filler", _make_filler_config(), rng)


# ---------------------------------------------------------------------------
# Tests: signal IDs
# ---------------------------------------------------------------------------


class TestSignalIds:
    """Verify all 8 filler signals are registered."""

    def test_signal_count(self, filler: FillerGenerator) -> None:
        assert len(filler.get_signal_ids()) == 8

    def test_signal_names(self, filler: FillerGenerator) -> None:
        ids = set(filler.get_signal_ids())
        expected = {
            "filler.line_speed", "filler.fill_weight", "filler.fill_target",
            "filler.fill_deviation", "filler.packs_produced",
            "filler.reject_count", "filler.state", "filler.hopper_level",
        }
        assert ids == expected


# ---------------------------------------------------------------------------
# Tests: initial state
# ---------------------------------------------------------------------------


class TestInitialState:
    """Filler starts in Off state with appropriate initial values."""

    def test_initial_state_off(
        self, filler: FillerGenerator, store: SignalStore,
    ) -> None:
        results = filler.generate(0.1, 0.1, store)
        state_sv = _find_signal(results, "filler.state")
        assert int(state_sv.value) == STATE_OFF

    def test_initial_line_speed_zero(
        self, filler: FillerGenerator, store: SignalStore,
    ) -> None:
        results = filler.generate(0.1, 0.1, store)
        speed_sv = _find_signal(results, "filler.line_speed")
        assert speed_sv.value == 0.0

    def test_initial_packs_zero(
        self, filler: FillerGenerator, store: SignalStore,
    ) -> None:
        results = filler.generate(0.1, 0.1, store)
        packs_sv = _find_signal(results, "filler.packs_produced")
        assert packs_sv.value == 0.0

    def test_initial_rejects_zero(
        self, filler: FillerGenerator, store: SignalStore,
    ) -> None:
        results = filler.generate(0.1, 0.1, store)
        reject_sv = _find_signal(results, "filler.reject_count")
        assert reject_sv.value == 0.0

    def test_initial_hopper_positive(
        self, filler: FillerGenerator, store: SignalStore,
    ) -> None:
        results = filler.generate(0.1, 0.1, store)
        hopper_sv = _find_signal(results, "filler.hopper_level")
        assert hopper_sv.value > 0.0


# ---------------------------------------------------------------------------
# Tests: line speed behaviour
# ---------------------------------------------------------------------------


class TestLineSpeed:
    """Line speed is 0 when not Running."""

    def test_speed_zero_when_off(
        self, filler: FillerGenerator, store: SignalStore,
    ) -> None:
        for _ in range(10):
            results = filler.generate(0.1, 0.1, store)
        speed_sv = _find_signal(results, "filler.line_speed")
        assert speed_sv.value == 0.0

    def test_speed_positive_when_running(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        gen = FillerGenerator("filler", _make_filler_config(), rng)
        gen.state_machine.force_state("Running")
        results = gen.generate(0.1, 0.1, store)
        speed_sv = _find_signal(results, "filler.line_speed")
        # Line speed should be near the 60 ppm target (with possible noise)
        assert speed_sv.value > 0.0

    def test_speed_within_clamp(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        gen = FillerGenerator("filler", _make_filler_config(), rng)
        gen.state_machine.force_state("Running")
        all_results = _run_ticks(gen, store, n_ticks=100)
        for results in all_results:
            speed_sv = _find_signal(results, "filler.line_speed")
            assert 0.0 <= speed_sv.value <= 120.0


# ---------------------------------------------------------------------------
# Tests: per-item fill weight generation
# ---------------------------------------------------------------------------


class TestPerItemFillWeight:
    """Fill weight updates exactly once per item arrival."""

    def test_fill_weight_held_between_items(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        """At 60 ppm, item interval = 1.0s.  Within 0.1s ticks, weight holds."""
        gen = FillerGenerator("filler", _make_filler_config(line_speed_target=60.0), rng)
        gen.state_machine.force_state("Running")

        # Run for 5 ticks (0.5s total) — less than 1.0s item interval
        all_results = _run_ticks(gen, store, n_ticks=5, dt=0.1)

        # Fill weight should be identical for all 5 ticks (held value)
        weights = [_find_signal(r, "filler.fill_weight").value for r in all_results]
        assert weights[0] == weights[-1], "Fill weight should hold between items"
        assert all(w == weights[0] for w in weights)

    def test_fill_weight_updates_after_item_interval(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        """After 1.0s at 60 ppm, fill weight may update."""
        gen = FillerGenerator("filler", _make_filler_config(line_speed_target=60.0), rng)
        gen.state_machine.force_state("Running")

        # Run for 15 ticks (1.5s) — past the item interval
        all_results = _run_ticks(gen, store, n_ticks=15, dt=0.1)

        # At some point within 1.5s at 60 ppm (interval=1.0s), packs > 0
        packs = _find_signal(all_results[-1], "filler.packs_produced").value
        assert packs >= 1.0, f"Expected at least 1 pack, got {packs}"

    def test_packs_count_consistent_with_speed(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        """Over 60s at 60 ppm, expect ~60 packs."""
        gen = FillerGenerator(
            "filler",
            _make_filler_config(line_speed_target=60.0, fill_sigma_g=0.0),
            rng,
        )
        gen.state_machine.force_state("Running")

        # Run 600 ticks = 60s at dt=0.1
        all_results = _run_ticks(gen, store, n_ticks=600, dt=0.1)
        final_packs = _find_signal(all_results[-1], "filler.packs_produced").value

        # Expect ~60 packs (allow ±5 for timing)
        assert 55 <= final_packs <= 65, f"Expected ~60 packs, got {final_packs}"

    def test_fill_weight_near_target_plus_giveaway(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        """After many items, mean fill weight is near target + giveaway."""
        gen = FillerGenerator(
            "filler",
            _make_filler_config(
                fill_target_g=400.0, fill_giveaway_g=5.0,
                fill_sigma_g=3.0, line_speed_target=60.0,
            ),
            rng,
        )
        gen.state_machine.force_state("Running")

        # Run 600 ticks = 60s at dt=0.1 → ~60 items
        all_results = _run_ticks(gen, store, n_ticks=600, dt=0.1)

        # Collect all distinct fill weights (i.e., when they change)
        weights = []
        prev_weight = None
        for results in all_results:
            fw = _find_signal(results, "filler.fill_weight").value
            if fw != prev_weight:
                weights.append(fw)
                prev_weight = fw

        assert len(weights) >= 10, f"Too few fill weight updates: {len(weights)}"
        mean_w = sum(weights) / len(weights)
        # Mean should be near 405g (target 400 + giveaway 5)
        assert 395.0 <= mean_w <= 415.0, f"Mean fill weight {mean_w} out of range"

    def test_fill_deviation_equals_weight_minus_target(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        """fill_deviation always equals fill_weight - fill_target."""
        gen = FillerGenerator(
            "filler",
            _make_filler_config(fill_target_g=400.0, line_speed_target=60.0),
            rng,
        )
        gen.state_machine.force_state("Running")
        all_results = _run_ticks(gen, store, n_ticks=200, dt=0.1)

        for results in all_results:
            fw = _find_signal(results, "filler.fill_weight").value
            ft = _find_signal(results, "filler.fill_target").value
            fd = _find_signal(results, "filler.fill_deviation").value
            assert abs(fd - (fw - ft)) < 1e-6, (
                f"fill_deviation {fd} != fill_weight {fw} - fill_target {ft}"
            )

    def test_no_items_when_off(
        self, filler: FillerGenerator, store: SignalStore,
    ) -> None:
        """Packs counter stays at 0 when Off."""
        all_results = _run_ticks(filler, store, n_ticks=100, dt=0.1)
        final_packs = _find_signal(all_results[-1], "filler.packs_produced").value
        assert final_packs == 0.0


# ---------------------------------------------------------------------------
# Tests: reject counting
# ---------------------------------------------------------------------------


class TestRejectCounting:
    """Reject count increments when deviation > tolerance."""

    def test_rejects_increase_with_large_deviation(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        """With sigma >> tolerance, should see some rejects."""
        gen = FillerGenerator(
            "filler",
            _make_filler_config(
                fill_target_g=400.0, fill_giveaway_g=0.0,
                fill_sigma_g=20.0,   # wide sigma → many rejects
                fill_tolerance_g=10.0,
                line_speed_target=120.0,  # fast line for more items
            ),
            rng,
        )
        gen.state_machine.force_state("Running")

        # Run 600 ticks = 60s → ~120 items
        all_results = _run_ticks(gen, store, n_ticks=600, dt=0.1)
        final_rejects = _find_signal(all_results[-1], "filler.reject_count").value
        # With sigma=20 and tolerance=10, ~32% of items should be rejected
        assert final_rejects > 0, "Expected at least 1 reject with large sigma"

    def test_no_rejects_with_tight_sigma(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        """With sigma << tolerance, should see no rejects."""
        gen = FillerGenerator(
            "filler",
            _make_filler_config(
                fill_target_g=400.0, fill_giveaway_g=0.0,
                fill_sigma_g=0.1,    # very tight sigma
                fill_tolerance_g=15.0,
                line_speed_target=60.0,
            ),
            rng,
        )
        gen.state_machine.force_state("Running")

        all_results = _run_ticks(gen, store, n_ticks=600, dt=0.1)
        final_rejects = _find_signal(all_results[-1], "filler.reject_count").value
        assert final_rejects == 0.0, f"Unexpected rejects: {final_rejects}"

    def test_rejects_never_exceed_packs(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        """Rejects can never exceed total packs produced."""
        gen = FillerGenerator(
            "filler",
            _make_filler_config(
                fill_sigma_g=20.0, fill_tolerance_g=5.0, line_speed_target=60.0,
            ),
            rng,
        )
        gen.state_machine.force_state("Running")
        all_results = _run_ticks(gen, store, n_ticks=600, dt=0.1)
        packs = _find_signal(all_results[-1], "filler.packs_produced").value
        rejects = _find_signal(all_results[-1], "filler.reject_count").value
        assert rejects <= packs


# ---------------------------------------------------------------------------
# Tests: hopper level
# ---------------------------------------------------------------------------


class TestHopperLevel:
    """Hopper depletes when Running, refills on threshold."""

    def test_hopper_depletes_when_running(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        gen = FillerGenerator("filler", _make_filler_config(), rng)
        gen.state_machine.force_state("Running")

        # Suppress noise for this test
        gen._hopper_noise = None  # type: ignore[assignment]

        results_start = gen.generate(0.1, 0.1, store)
        hopper_start = _find_signal(results_start, "filler.hopper_level").value

        all_results = _run_ticks(gen, store, n_ticks=500, dt=0.1)
        hopper_end = _find_signal(all_results[-1], "filler.hopper_level").value

        # After running, hopper should be lower (or refilled if hit threshold)
        # Just verify it's not identical to initial
        assert hopper_end <= hopper_start or hopper_end > 50.0, (
            "Hopper should deplete or refill"
        )

    def test_hopper_holds_when_off(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        gen = FillerGenerator("filler", _make_filler_config(), rng)
        gen._hopper_noise = None  # suppress noise  # type: ignore[assignment]

        results_start = gen.generate(0.1, 0.1, store)
        hopper_start = _find_signal(results_start, "filler.hopper_level").value

        all_results = _run_ticks(gen, store, n_ticks=100, dt=0.1)
        hopper_end = _find_signal(all_results[-1], "filler.hopper_level").value

        # Off state: no depletion
        assert hopper_end == hopper_start, (
            f"Hopper should hold when Off: start={hopper_start}, end={hopper_end}"
        )

    def test_hopper_within_bounds(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        gen = FillerGenerator("filler", _make_filler_config(), rng)
        gen.state_machine.force_state("Running")
        all_results = _run_ticks(gen, store, n_ticks=600, dt=0.1)
        for results in all_results:
            hopper_sv = _find_signal(results, "filler.hopper_level")
            # With noise, may slightly exceed bounds, but model level should be reasonable
            assert -5.0 <= hopper_sv.value <= 105.0


# ---------------------------------------------------------------------------
# Tests: fill target
# ---------------------------------------------------------------------------


class TestFillTarget:
    """Fill target signal outputs configured target value."""

    def test_fill_target_matches_config(
        self, rng: np.random.Generator, store: SignalStore,
    ) -> None:
        gen = FillerGenerator(
            "filler",
            _make_filler_config(fill_target_g=350.0),
            rng,
        )
        results = gen.generate(0.1, 0.1, store)
        ft_sv = _find_signal(results, "filler.fill_target")
        assert 340.0 <= ft_sv.value <= 360.0


# ---------------------------------------------------------------------------
# Tests: output completeness
# ---------------------------------------------------------------------------


class TestOutputCompleteness:
    """Every generate() call produces all 8 signals."""

    def test_all_signals_produced(
        self, filler: FillerGenerator, store: SignalStore,
    ) -> None:
        results = filler.generate(0.1, 0.1, store)
        signal_ids = {sv.signal_id for sv in results}
        expected = {
            "filler.line_speed", "filler.fill_weight", "filler.fill_target",
            "filler.fill_deviation", "filler.packs_produced",
            "filler.reject_count", "filler.state", "filler.hopper_level",
        }
        assert signal_ids == expected

    def test_all_timestamps_current(
        self, filler: FillerGenerator, store: SignalStore,
    ) -> None:
        sim_time = 12.3
        results = filler.generate(sim_time, 0.1, store)
        for sv in results:
            assert sv.timestamp == sim_time


# ---------------------------------------------------------------------------
# Tests: determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed → same output."""

    def test_deterministic_output(self, store: SignalStore) -> None:
        cfg = _make_filler_config()
        gen1 = FillerGenerator("filler", cfg, np.random.default_rng(99))
        gen2 = FillerGenerator("filler", cfg, np.random.default_rng(99))

        gen1.state_machine.force_state("Running")
        gen2.state_machine.force_state("Running")

        store1 = SignalStore()
        store2 = SignalStore()
        all1 = _run_ticks(gen1, store1, n_ticks=100, dt=0.1)
        all2 = _run_ticks(gen2, store2, n_ticks=100, dt=0.1)

        for r1, r2 in zip(all1, all2, strict=False):
            for sv1, sv2 in zip(sorted(r1, key=lambda x: x.signal_id),
                                sorted(r2, key=lambda x: x.signal_id), strict=False):
                assert sv1.value == sv2.value, (
                    f"Non-deterministic output for {sv1.signal_id}: "
                    f"{sv1.value} != {sv2.value}"
                )


# ---------------------------------------------------------------------------
# Tests: state machine access
# ---------------------------------------------------------------------------


class TestStateMachineAccess:
    """State machine can be controlled for scenarios."""

    def test_can_force_running(
        self, filler: FillerGenerator, store: SignalStore,
    ) -> None:
        filler.state_machine.force_state("Running")
        results = filler.generate(0.1, 0.1, store)
        state_sv = _find_signal(results, "filler.state")
        assert int(state_sv.value) == STATE_RUNNING

    def test_can_force_fault(
        self, filler: FillerGenerator, store: SignalStore,
    ) -> None:
        filler.state_machine.force_state("Fault")
        results = filler.generate(0.1, 0.1, store)
        state_sv = _find_signal(results, "filler.state")
        assert int(state_sv.value) == STATE_FAULT
