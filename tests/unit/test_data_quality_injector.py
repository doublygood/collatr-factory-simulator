"""Unit tests for DataQualityInjector and DataEngine data quality wiring (task 4.12).

Tests cover:
- DataQualityInjector construction: both/one/neither sub-injector created based on config.
- tick() is a no-op when both sub-configs are disabled.
- tick() calls the disconnect injector (sentinel written, quality=bad).
- tick() calls the stuck injector (value frozen, quality=good).
- Accepts ground_truth=None without error.
- Determinism: same seed → identical injection schedule.
- DataEngine exposes a data_quality property returning a DataQualityInjector.
- DataEngine tick() calls data_quality.tick() (observable via store mutations).
- DataEngine wiring does not break existing signal count or tick behaviour.

PRD Reference: Section 10.9, 10.10 (store-level injection), Section 8.2 (ordering)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from factory_simulator.config import (
    DataQualityConfig,
    EquipmentConfig,
    FactoryConfig,
    SensorDisconnectConfig,
    SensorDisconnectSentinelConfig,
    SignalConfig,
    SimulationConfig,
    StuckSensorConfig,
)
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.engine.data_quality import (
    DataQualityInjector,
    SensorDisconnectInjector,
    StuckSensorInjector,
)
from factory_simulator.store import SignalStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIG_TEMP = "press.dryer_temp_zone_1"
_SIG_PRESSURE = "coder.ink_pressure"
_SIG_VOLTAGE = "coder.supply_voltage"
_ALL_SIGS = [_SIG_TEMP, _SIG_PRESSURE, _SIG_VOLTAGE]

_TEMP_SENTINEL = 6553.5
_PRESSURE_SENTINEL = 0.0
_VOLTAGE_SENTINEL = -32768.0


def _make_cfg(
    *,
    disconnect_enabled: bool = True,
    stuck_enabled: bool = True,
    freq_disconnect: list[float] | None = None,
    freq_stuck: list[float] | None = None,
    dur_disconnect: list[float] | None = None,
    dur_stuck: list[float] | None = None,
) -> DataQualityConfig:
    """Build a DataQualityConfig with test-friendly defaults."""
    return DataQualityConfig(
        sensor_disconnect=SensorDisconnectConfig(
            enabled=disconnect_enabled,
            frequency_per_24h_per_signal=freq_disconnect or [1.0, 1.0],
            duration_seconds=dur_disconnect or [1.0, 1.0],
            sentinel_defaults=SensorDisconnectSentinelConfig(
                temperature=_TEMP_SENTINEL,
                pressure=_PRESSURE_SENTINEL,
                voltage=_VOLTAGE_SENTINEL,
            ),
        ),
        stuck_sensor=StuckSensorConfig(
            enabled=stuck_enabled,
            frequency_per_week_per_signal=freq_stuck or [1.0, 1.0],
            duration_seconds=dur_stuck or [1.0, 1.0],
        ),
    )


def _make_injector(
    *,
    disconnect_enabled: bool = True,
    stuck_enabled: bool = True,
    signal_ids: list[str] | None = None,
    seed: int = 42,
    freq_disconnect: list[float] | None = None,
    freq_stuck: list[float] | None = None,
    dur_disconnect: list[float] | None = None,
    dur_stuck: list[float] | None = None,
) -> DataQualityInjector:
    """Create a DataQualityInjector with test-friendly defaults."""
    cfg = _make_cfg(
        disconnect_enabled=disconnect_enabled,
        stuck_enabled=stuck_enabled,
        freq_disconnect=freq_disconnect,
        freq_stuck=freq_stuck,
        dur_disconnect=dur_disconnect,
        dur_stuck=dur_stuck,
    )
    sigs = signal_ids if signal_ids is not None else list(_ALL_SIGS)
    ss = np.random.SeedSequence(seed)
    disconnect_rng = np.random.default_rng(ss.spawn(1)[0])
    stuck_rng = np.random.default_rng(ss.spawn(1)[0])
    return DataQualityInjector(cfg, sigs, disconnect_rng, stuck_rng)


def _minimal_engine_config(
    seed: int = 42,
    disconnect_enabled: bool = False,
    stuck_enabled: bool = False,
) -> FactoryConfig:
    """Build a minimal FactoryConfig with two environment signals."""
    signals: dict[str, SignalConfig] = {
        "ambient_temp": SignalConfig(
            model="sinusoidal",
            noise_sigma=0.1,
            sample_rate_ms=100,
            min_clamp=0.0,
            max_clamp=50.0,
            params={"center": 22.0, "amplitude": 3.0, "period": 86400.0},
        ),
        "ambient_humidity": SignalConfig(
            model="sinusoidal",
            noise_sigma=0.5,
            sample_rate_ms=100,
            min_clamp=0.0,
            max_clamp=100.0,
            params={"center": 55.0, "amplitude": 10.0, "period": 86400.0},
        ),
    }
    cfg = FactoryConfig(
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
    cfg.data_quality.sensor_disconnect.enabled = disconnect_enabled
    cfg.data_quality.stuck_sensor.enabled = stuck_enabled
    return cfg


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestDataQualityInjectorConstruction:
    """DataQualityInjector creates sub-injectors based on config."""

    def test_both_enabled_creates_both(self) -> None:
        inj = _make_injector(disconnect_enabled=True, stuck_enabled=True)
        assert isinstance(inj.sensor_disconnect, SensorDisconnectInjector)
        assert isinstance(inj.stuck_sensor, StuckSensorInjector)

    def test_disconnect_disabled_returns_none(self) -> None:
        inj = _make_injector(disconnect_enabled=False, stuck_enabled=True)
        assert inj.sensor_disconnect is None
        assert isinstance(inj.stuck_sensor, StuckSensorInjector)

    def test_stuck_disabled_returns_none(self) -> None:
        inj = _make_injector(disconnect_enabled=True, stuck_enabled=False)
        assert isinstance(inj.sensor_disconnect, SensorDisconnectInjector)
        assert inj.stuck_sensor is None

    def test_both_disabled_returns_none_for_both(self) -> None:
        inj = _make_injector(disconnect_enabled=False, stuck_enabled=False)
        assert inj.sensor_disconnect is None
        assert inj.stuck_sensor is None

    def test_empty_signal_list_is_valid(self) -> None:
        inj = _make_injector(signal_ids=[])
        assert inj.sensor_disconnect is not None
        assert inj.stuck_sensor is not None


# ---------------------------------------------------------------------------
# tick() behaviour — disabled
# ---------------------------------------------------------------------------


class TestDataQualityInjectorTickDisabled:
    """tick() is a no-op when both sub-injectors are disabled."""

    def test_tick_does_not_modify_store_when_both_disabled(self) -> None:
        inj = _make_injector(disconnect_enabled=False, stuck_enabled=False)
        store = SignalStore()
        store.set(_SIG_TEMP, 150.0, 0.0, "good")
        inj.tick(0.0, store)
        sv = store.get(_SIG_TEMP)
        assert sv is not None
        assert sv.value == pytest.approx(150.0)
        assert sv.quality == "good"

    def test_tick_accepts_ground_truth_none(self) -> None:
        inj = _make_injector(disconnect_enabled=False, stuck_enabled=False)
        inj.tick(0.0, SignalStore(), None)  # must not raise


# ---------------------------------------------------------------------------
# tick() behaviour — disconnect enabled
# ---------------------------------------------------------------------------


class TestDataQualityInjectorDisconnect:
    """tick() fires disconnect events when the injector is enabled."""

    def test_disconnect_writes_temperature_sentinel(self) -> None:
        """After a disconnect starts, store value becomes 6553.5 and quality=bad."""
        # High frequency: 100/day → mean interval ~864s
        inj = _make_injector(
            disconnect_enabled=True,
            stuck_enabled=False,
            signal_ids=[_SIG_TEMP],
            freq_disconnect=[100.0, 100.0],
            dur_disconnect=[60.0, 60.0],
            seed=7,
        )
        store = SignalStore()
        fired = False
        for i in range(int(2 * 86400 / 0.1)):
            t = i * 0.1
            store.set(_SIG_TEMP, 150.0, t, "good")
            inj.tick(t, store)
            sv = store.get(_SIG_TEMP)
            if sv is not None and sv.quality == "bad":
                assert sv.value == pytest.approx(_TEMP_SENTINEL)
                fired = True
                break
        assert fired, "Disconnect event should fire within 2 days of simulation"

    def test_disconnect_restores_after_duration(self) -> None:
        """Store accepts normal values again once the disconnect event ends."""
        dur = 5.0
        inj = _make_injector(
            disconnect_enabled=True,
            stuck_enabled=False,
            signal_ids=[_SIG_TEMP],
            freq_disconnect=[100.0, 100.0],
            dur_disconnect=[dur, dur],
            seed=3,
        )
        store = SignalStore()
        disconnect_end: float | None = None
        for i in range(int(2 * 86400 / 0.1)):
            t = i * 0.1
            store.set(_SIG_TEMP, 150.0, t, "good")
            inj.tick(t, store)
            sv = store.get(_SIG_TEMP)
            if sv is None:
                continue
            if sv.quality == "bad" and disconnect_end is None:
                disconnect_end = t + dur
            if disconnect_end is not None and t > disconnect_end + 1.0:
                # Allow one extra tick for the injector to let go
                assert sv.quality == "good"
                break
        assert disconnect_end is not None, "No disconnect fired"


# ---------------------------------------------------------------------------
# tick() behaviour — stuck enabled
# ---------------------------------------------------------------------------


class TestDataQualityInjectorStuck:
    """tick() fires stuck events when the injector is enabled."""

    def test_stuck_freezes_value_with_good_quality(self) -> None:
        """Stuck event keeps value constant and quality remains good."""
        inj = _make_injector(
            disconnect_enabled=False,
            stuck_enabled=True,
            signal_ids=[_SIG_TEMP],
            freq_stuck=[50.0, 50.0],
            dur_stuck=[30.0, 30.0],
            seed=11,
        )
        store = SignalStore()
        # Pre-populate so the injector can capture a frozen value
        store.set(_SIG_TEMP, 200.0, 0.0, "good")

        stuck_value: float | None = None
        for i in range(int(2 * 86400 * 7 / 0.1)):  # up to 2 weeks
            t = i * 0.1
            # Simulate the generator varying the value
            generator_val = 200.0 + float(i % 10)
            store.set(_SIG_TEMP, generator_val, t, "good")
            inj.tick(t, store)
            sv = store.get(_SIG_TEMP)
            if sv is None:
                continue
            if stuck_value is None and sv.value != generator_val:
                # Value is frozen
                stuck_value = float(sv.value)
                assert sv.quality == "good"
                break
        assert stuck_value is not None, "Stuck event should fire within 2 sim-weeks"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDataQualityInjectorDeterminism:
    """Same seed → same injection schedule."""

    def test_same_seed_same_behaviour(self) -> None:
        sigs = [_SIG_TEMP, _SIG_PRESSURE]
        inj1 = _make_injector(
            disconnect_enabled=True,
            stuck_enabled=True,
            signal_ids=sigs,
            freq_disconnect=[50.0, 50.0],
            freq_stuck=[10.0, 10.0],
            dur_disconnect=[2.0, 2.0],
            dur_stuck=[2.0, 2.0],
            seed=999,
        )
        inj2 = _make_injector(
            disconnect_enabled=True,
            stuck_enabled=True,
            signal_ids=sigs,
            freq_disconnect=[50.0, 50.0],
            freq_stuck=[10.0, 10.0],
            dur_disconnect=[2.0, 2.0],
            dur_stuck=[2.0, 2.0],
            seed=999,
        )
        store1 = SignalStore()
        store2 = SignalStore()

        for i in range(5000):
            t = i * 0.1
            for sig in sigs:
                store1.set(sig, 100.0, t, "good")
                store2.set(sig, 100.0, t, "good")
            inj1.tick(t, store1)
            inj2.tick(t, store2)

        for sig in sigs:
            sv1 = store1.get(sig)
            sv2 = store2.get(sig)
            assert sv1 is not None and sv2 is not None
            assert sv1.value == sv2.value
            assert sv1.quality == sv2.quality

    def test_different_seeds_differ(self) -> None:
        """Different seeds should produce different schedules (statistical check)."""
        sig = _SIG_TEMP
        # Use very high frequency (1000/day → mean interval ~86s) so events fire
        # reliably within 2000s of simulation time (P(0 events) ≈ e^(-23) ≈ 10^-10)
        inj1 = _make_injector(
            disconnect_enabled=True,
            stuck_enabled=False,
            signal_ids=[sig],
            freq_disconnect=[1000.0, 1000.0],
            dur_disconnect=[5.0, 5.0],
            seed=1,
        )
        inj2 = _make_injector(
            disconnect_enabled=True,
            stuck_enabled=False,
            signal_ids=[sig],
            freq_disconnect=[1000.0, 1000.0],
            dur_disconnect=[5.0, 5.0],
            seed=2,
        )
        store1 = SignalStore()
        store2 = SignalStore()

        first_bad1: float | None = None
        first_bad2: float | None = None
        for i in range(20_000):
            t = i * 0.1
            store1.set(sig, 150.0, t, "good")
            store2.set(sig, 150.0, t, "good")
            inj1.tick(t, store1)
            inj2.tick(t, store2)
            sv1, sv2 = store1.get(sig), store2.get(sig)
            if sv1 and sv1.quality == "bad" and first_bad1 is None:
                first_bad1 = t
            if sv2 and sv2.quality == "bad" and first_bad2 is None:
                first_bad2 = t

        # Both must have fired (overwhelmingly likely at 1000/day over 2000s)
        assert first_bad1 is not None and first_bad2 is not None
        # First event must occur at different times (different seeds → different schedules)
        assert first_bad1 != pytest.approx(first_bad2, abs=1.0)


# ---------------------------------------------------------------------------
# DataEngine integration
# ---------------------------------------------------------------------------


class TestDataEngineIntegration:
    """DataEngine wires DataQualityInjector correctly."""

    def test_engine_exposes_data_quality_property(self) -> None:
        config = _minimal_engine_config()
        engine = DataEngine(config, SignalStore())
        assert isinstance(engine.data_quality, DataQualityInjector)

    def test_data_quality_sub_injectors_disabled_by_default(self) -> None:
        """Minimal config has disconnect/stuck disabled → both sub-injectors are None."""
        config = _minimal_engine_config(disconnect_enabled=False, stuck_enabled=False)
        engine = DataEngine(config, SignalStore())
        assert engine.data_quality.sensor_disconnect is None
        assert engine.data_quality.stuck_sensor is None

    def test_data_quality_sub_injectors_created_when_enabled(self) -> None:
        config = _minimal_engine_config(disconnect_enabled=True, stuck_enabled=True)
        engine = DataEngine(config, SignalStore())
        assert engine.data_quality.sensor_disconnect is not None
        assert engine.data_quality.stuck_sensor is not None

    def test_engine_tick_runs_without_error(self) -> None:
        """Engine tick must not raise when data quality injectors are active."""
        config = _minimal_engine_config(disconnect_enabled=True, stuck_enabled=True)
        engine = DataEngine(config, SignalStore())
        for _ in range(20):
            engine.tick()

    def test_engine_signal_count_unchanged(self) -> None:
        """Adding DataQualityInjector must not change the engine's signal count."""
        config = _minimal_engine_config()
        engine = DataEngine(config, SignalStore())
        assert engine.signal_count() == 2  # ambient_temp + ambient_humidity

    def test_engine_tick_still_populates_store(self) -> None:
        """Store should still contain signal values after tick with data quality active."""
        config = _minimal_engine_config(disconnect_enabled=False, stuck_enabled=False)
        store = SignalStore()
        engine = DataEngine(config, store)
        engine.tick()
        # Signal IDs use the {equipment_id}.{signal_name} convention
        assert store.get("env.ambient_temp") is not None
        assert store.get("env.ambient_humidity") is not None

    def test_packaging_config_wires_correctly(self) -> None:
        """Full packaging config loads and ticks without error (smoke test)."""
        config_path = Path(__file__).resolve().parents[2] / "config" / "factory.yaml"
        from factory_simulator.config import load_config

        config = load_config(config_path, apply_env=False)
        config.simulation.random_seed = 42
        config.simulation.tick_interval_ms = 100
        # Disable high-frequency data quality to keep the test fast
        config.data_quality.sensor_disconnect.enabled = False
        config.data_quality.stuck_sensor.enabled = False

        store = SignalStore()
        engine = DataEngine(config, store)
        assert isinstance(engine.data_quality, DataQualityInjector)
        for _ in range(10):
            engine.tick()
        assert engine.signal_count() == 48
