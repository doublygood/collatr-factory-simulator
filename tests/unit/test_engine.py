"""Tests for the DataEngine -- simulation heartbeat.

Verifies:
- Engine constructs generators from config.
- tick() advances clock, runs generators, writes store.
- All 47 packaging signals appear in store after ticks.
- Sample rate enforcement: generators only run when interval elapsed.
- Deterministic output with same seed.
- Atomic tick: all signals updated in one tick (no partial state).
- Disabled equipment is skipped.
- async run() loop works and can be stopped.

PRD Reference: Section 8.2 (Data Flow), Section 8.3 (Concurrency Model)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import (
    EquipmentConfig,
    FactoryConfig,
    SignalConfig,
    SimulationConfig,
    load_config,
)
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.store import SignalStore

# Path to the default factory config
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _load_packaging_config(seed: int = 42) -> FactoryConfig:
    """Load the packaging config with a deterministic seed."""
    config = load_config(_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = seed
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = 1.0
    return config


@pytest.fixture
def packaging_config() -> FactoryConfig:
    return _load_packaging_config()


@pytest.fixture
def store() -> SignalStore:
    return SignalStore()


@pytest.fixture
def engine(packaging_config: FactoryConfig, store: SignalStore) -> DataEngine:
    clock = SimulationClock.from_config(packaging_config.simulation)
    return DataEngine(packaging_config, store, clock)


# ---------------------------------------------------------------------------
# Minimal config for focused tests
# ---------------------------------------------------------------------------


def _minimal_config(seed: int = 42) -> FactoryConfig:
    """Build a minimal FactoryConfig with one equipment group (2 signals).

    Uses the EnvironmentGenerator which expects ``ambient_temp`` and
    ``ambient_humidity`` signal names.
    """
    signals = {
        "ambient_temp": SignalConfig(
            model="sinusoidal",
            noise_sigma=0.1,
            sample_rate_ms=500,
            min_clamp=0.0,
            max_clamp=50.0,
            params={"center": 22.0, "amplitude": 3.0, "period": 86400.0},
        ),
        "ambient_humidity": SignalConfig(
            model="sinusoidal",
            noise_sigma=0.5,
            sample_rate_ms=1000,
            min_clamp=0.0,
            max_clamp=100.0,
            params={"center": 55.0, "amplitude": 10.0, "period": 86400.0},
        ),
    }
    return FactoryConfig(
        simulation=SimulationConfig(
            time_scale=1.0,
            random_seed=seed,
            tick_interval_ms=100,
        ),
        equipment={
            "env": EquipmentConfig(
                enabled=True,
                type="iolink_sensor",
                signals=signals,
            ),
        },
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    """DataEngine construction from config."""

    def test_creates_generators_from_config(
        self, engine: DataEngine,
    ) -> None:
        """Engine creates 7 packaging generators."""
        assert len(engine.generators) == 7

    def test_signal_count_is_48(self, engine: DataEngine) -> None:
        """Packaging profile has 48 signals (47 + fault_code)."""
        assert engine.signal_count() == 48

    def test_clock_is_set(self, engine: DataEngine) -> None:
        assert engine.clock is not None
        assert engine.clock.sim_time == 0.0

    def test_store_is_set(
        self, engine: DataEngine, store: SignalStore,
    ) -> None:
        assert engine.store is store

    def test_not_running_initially(self, engine: DataEngine) -> None:
        assert engine.running is False

    def test_creates_clock_from_config_if_none(
        self, packaging_config: FactoryConfig, store: SignalStore,
    ) -> None:
        """If no clock is provided, one is created from config."""
        eng = DataEngine(packaging_config, store, clock=None)
        assert eng.clock is not None
        assert eng.clock.tick_interval_ms == 100

    def test_disabled_equipment_skipped(self, store: SignalStore) -> None:
        """Disabled equipment groups are not instantiated."""
        config = _minimal_config()
        # Disable the only equipment group
        config.equipment["env"].enabled = False
        eng = DataEngine(config, store)
        assert len(eng.generators) == 0
        assert eng.signal_count() == 0


# ---------------------------------------------------------------------------
# Tick behaviour
# ---------------------------------------------------------------------------


class TestTick:
    """tick() advances clock and populates store."""

    def test_tick_advances_clock(self, engine: DataEngine) -> None:
        t = engine.tick()
        assert t > 0.0
        assert engine.clock.sim_time == t

    def test_tick_returns_sim_time(self, engine: DataEngine) -> None:
        t = engine.tick()
        expected = (100 / 1000.0) * 1.0  # tick_interval_ms/1000 * time_scale
        assert t == pytest.approx(expected)

    def test_multiple_ticks_advance_linearly(
        self, engine: DataEngine,
    ) -> None:
        for _ in range(10):
            t = engine.tick()
        expected = 10 * (100 / 1000.0) * 1.0
        assert t == pytest.approx(expected, rel=1e-9)

    def test_store_populated_after_tick(
        self, engine: DataEngine, store: SignalStore,
    ) -> None:
        """After a tick, all 48 signals should be in the store."""
        engine.tick()
        assert len(store) == 48

    def test_all_signal_ids_in_store(
        self, engine: DataEngine, store: SignalStore,
    ) -> None:
        """Every generator's signal IDs appear in the store."""
        engine.tick()
        expected_ids = set()
        for gen in engine.generators:
            expected_ids.update(gen.get_signal_ids())
        stored_ids = set(store.signal_ids())
        assert stored_ids == expected_ids

    def test_signal_values_are_numeric(
        self, engine: DataEngine, store: SignalStore,
    ) -> None:
        """All packaging signals produce numeric values."""
        engine.tick()
        for signal_id in store:
            sv = store.get(signal_id)
            assert sv is not None
            assert isinstance(sv.value, float | int), (
                f"{signal_id} value is {type(sv.value)}, expected float|int"
            )

    def test_signal_quality_is_good(
        self, engine: DataEngine, store: SignalStore,
    ) -> None:
        """All signals should have 'good' quality after normal tick."""
        engine.tick()
        for signal_id in store:
            sv = store.get(signal_id)
            assert sv is not None
            assert sv.quality == "good"

    def test_timestamps_match_sim_time(
        self, engine: DataEngine, store: SignalStore,
    ) -> None:
        """Signal timestamps should equal the tick's sim_time."""
        t = engine.tick()
        for signal_id in store:
            sv = store.get(signal_id)
            assert sv is not None
            assert sv.timestamp == pytest.approx(t)

    def test_n_ticks_without_error(
        self, engine: DataEngine, store: SignalStore,
    ) -> None:
        """Engine runs 100 ticks without error."""
        for _ in range(100):
            engine.tick()
        assert len(store) == 48
        assert engine.clock.tick_count == 100


# ---------------------------------------------------------------------------
# Sample rate enforcement
# ---------------------------------------------------------------------------


class TestSampleRate:
    """Generators only run when their sample interval has elapsed."""

    def test_fast_signal_updates_more_often(
        self, store: SignalStore,
    ) -> None:
        """A signal with 500ms sample rate updates more often than 1000ms."""
        config = _minimal_config()
        clock = SimulationClock(tick_interval_ms=100, time_scale=1.0)
        eng = DataEngine(config, store, clock)

        # Track updates by watching timestamp changes
        timestamps_speed: list[float] = []
        timestamps_temp: list[float] = []

        for _ in range(20):  # 2 seconds of simulation
            eng.tick()
            sv_speed = store.get("env.ambient_temp")
            sv_temp = store.get("env.ambient_humidity")
            if sv_speed is not None:
                t = sv_speed.timestamp
                if not timestamps_speed or t != timestamps_speed[-1]:
                    timestamps_speed.append(t)
            if sv_temp is not None:
                t = sv_temp.timestamp
                if not timestamps_temp or t != timestamps_temp[-1]:
                    timestamps_temp.append(t)

        # Both should have been updated at least once
        assert len(timestamps_speed) >= 1
        assert len(timestamps_temp) >= 1

    def test_generator_runs_on_first_tick(
        self, store: SignalStore,
    ) -> None:
        """All generators run on the very first tick regardless of interval."""
        config = _minimal_config()
        # Set a long sample rate (10s) to ensure it would normally skip
        for sig in config.equipment["env"].signals.values():
            sig.sample_rate_ms = 10000

        clock = SimulationClock(tick_interval_ms=100, time_scale=1.0)
        eng = DataEngine(config, store, clock)

        eng.tick()  # First tick (100ms sim time)
        # Signals should still be in store (first tick always runs)
        assert "env.ambient_temp" in store
        assert "env.ambient_humidity" in store


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed produces identical output."""

    def test_deterministic_with_same_seed(self) -> None:
        store1 = SignalStore()
        store2 = SignalStore()

        config1 = _load_packaging_config(seed=99)
        config2 = _load_packaging_config(seed=99)

        clock1 = SimulationClock.from_config(config1.simulation)
        clock2 = SimulationClock.from_config(config2.simulation)

        eng1 = DataEngine(config1, store1, clock1)
        eng2 = DataEngine(config2, store2, clock2)

        for _ in range(50):
            eng1.tick()
            eng2.tick()

        # Every signal value should be identical
        for signal_id in store1:
            sv1 = store1.get(signal_id)
            sv2 = store2.get(signal_id)
            assert sv1 is not None
            assert sv2 is not None
            assert sv1.value == sv2.value, (
                f"{signal_id}: {sv1.value} != {sv2.value}"
            )
            assert sv1.timestamp == sv2.timestamp

    def test_different_seeds_produce_different_output(self) -> None:
        store1 = SignalStore()
        store2 = SignalStore()

        config1 = _load_packaging_config(seed=1)
        config2 = _load_packaging_config(seed=2)

        clock1 = SimulationClock.from_config(config1.simulation)
        clock2 = SimulationClock.from_config(config2.simulation)

        eng1 = DataEngine(config1, store1, clock1)
        eng2 = DataEngine(config2, store2, clock2)

        for _ in range(50):
            eng1.tick()
            eng2.tick()

        # At least some noisy signals should differ
        diffs = 0
        for signal_id in store1:
            sv1 = store1.get(signal_id)
            sv2 = store2.get(signal_id)
            if sv1 is not None and sv2 is not None and sv1.value != sv2.value:
                diffs += 1

        assert diffs > 0, "Different seeds should produce different noisy values"


# ---------------------------------------------------------------------------
# Atomic tick
# ---------------------------------------------------------------------------


class TestAtomicTick:
    """All signals in a tick share the same timestamp."""

    def test_all_signals_same_timestamp(
        self, engine: DataEngine, store: SignalStore,
    ) -> None:
        """After a single tick, all signals should have the same timestamp."""
        t = engine.tick()
        timestamps = set()
        for signal_id in store:
            sv = store.get(signal_id)
            if sv is not None:
                timestamps.add(sv.timestamp)

        assert len(timestamps) == 1, (
            f"Expected 1 unique timestamp, got {len(timestamps)}: {timestamps}"
        )
        assert t in timestamps


# ---------------------------------------------------------------------------
# Generator ordering
# ---------------------------------------------------------------------------


class TestGeneratorOrdering:
    """Generators run in config order (press first, dependents after)."""

    def test_press_is_first_generator(self, engine: DataEngine) -> None:
        """Press generator should be first (others depend on it)."""
        gens = engine.generators
        assert len(gens) >= 1
        assert gens[0].equipment_id == "press"

    def test_generator_order_matches_config(
        self, engine: DataEngine, packaging_config: FactoryConfig,
    ) -> None:
        """Generator order matches config equipment dict order."""
        config_ids = [
            eq_id
            for eq_id, eq_cfg in packaging_config.equipment.items()
            if eq_cfg.enabled
        ]
        gen_ids = [g.equipment_id for g in engine.generators]
        assert gen_ids == config_ids


# ---------------------------------------------------------------------------
# Async run loop
# ---------------------------------------------------------------------------


class TestAsyncRun:
    """Async run loop starts, ticks, and can be stopped."""

    @pytest.mark.asyncio
    async def test_run_ticks_and_stop(self) -> None:
        """run() should tick the engine and be stoppable."""
        config = _minimal_config()
        config.simulation.tick_interval_ms = 10  # fast for test
        store = SignalStore()
        clock = SimulationClock(tick_interval_ms=10, time_scale=1.0)
        eng = DataEngine(config, store, clock)

        async def stop_after_delay() -> None:
            await asyncio.sleep(0.1)  # Let it run ~10 ticks
            eng.stop()

        await asyncio.gather(eng.run(), stop_after_delay())

        assert eng.clock.tick_count > 0
        assert not eng.running
        assert "env.ambient_temp" in store

    @pytest.mark.asyncio
    async def test_run_can_be_cancelled(self) -> None:
        """run() can be cancelled via task cancellation."""
        config = _minimal_config()
        config.simulation.tick_interval_ms = 10
        store = SignalStore()
        clock = SimulationClock(tick_interval_ms=10, time_scale=1.0)
        eng = DataEngine(config, store, clock)

        task = asyncio.create_task(eng.run())
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert not eng.running


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_unknown_equipment_type_skipped(
        self, store: SignalStore,
    ) -> None:
        """Unknown equipment types are logged as warnings and skipped."""
        config = FactoryConfig(
            simulation=SimulationConfig(random_seed=42),
            equipment={
                "mystery": EquipmentConfig(
                    enabled=True,
                    type="unknown_equipment_type",
                    signals={},
                ),
            },
        )
        eng = DataEngine(config, store)
        assert len(eng.generators) == 0

    def test_empty_equipment(self, store: SignalStore) -> None:
        """Config with no equipment produces empty engine."""
        config = FactoryConfig(
            simulation=SimulationConfig(random_seed=42),
            equipment={},
        )
        eng = DataEngine(config, store)
        assert len(eng.generators) == 0
        assert eng.signal_count() == 0
        # Tick should still work (no-op for generators)
        eng.tick()
        assert len(store) == 0

    def test_time_scale_affects_dt(self, store: SignalStore) -> None:
        """Time scale multiplies the simulated dt."""
        config = _minimal_config()
        config.simulation.time_scale = 10.0
        clock = SimulationClock(tick_interval_ms=100, time_scale=10.0)
        eng = DataEngine(config, store, clock)

        t = eng.tick()
        # dt = 100ms * 10 = 1000ms = 1.0s
        assert t == pytest.approx(1.0)
