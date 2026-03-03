"""Unit tests for SensorDisconnectInjector and StuckSensorInjector (PRD 10.9, 10.10).

Tests cover:
- Disabled config → injector never overrides the store.
- Sentinel value resolution (temperature / pressure / voltage / unknown / override).
- Active disconnect: sentinel value written, quality="bad".
- Duration: injector stops overriding after configured duration.
- Resumption: store accepts normal writes again once event ends.
- Stuck sensor: frozen value held, quality="good".
- Deferred start when signal absent from store.
- Ground truth logging on event start.
- Determinism: same RNG seed → same schedule.

PRD Reference: Section 10.9 (Sensor Disconnect), Section 10.10 (Stuck Sensor)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from factory_simulator.config import (
    SensorDisconnectConfig,
    SensorDisconnectSentinelConfig,
    StuckSensorConfig,
)
from factory_simulator.engine.data_quality import (
    SensorDisconnectInjector,
    StuckSensorInjector,
    _sentinel_for_signal,
)
from factory_simulator.store import SignalStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _disconnect_cfg(
    enabled: bool = True,
    freq: list[float] | None = None,
    dur: list[float] | None = None,
    per_signal: dict[str, float] | None = None,
) -> SensorDisconnectConfig:
    """Construct a SensorDisconnectConfig with sensible test defaults."""
    return SensorDisconnectConfig(
        enabled=enabled,
        frequency_per_24h_per_signal=freq if freq is not None else [1.0, 1.0],
        duration_seconds=dur if dur is not None else [10.0, 10.0],
        per_signal_overrides=per_signal if per_signal is not None else {},
    )


def _stuck_cfg(
    enabled: bool = True,
    freq: list[float] | None = None,
    dur: list[float] | None = None,
) -> StuckSensorConfig:
    """Construct a StuckSensorConfig with sensible test defaults."""
    return StuckSensorConfig(
        enabled=enabled,
        frequency_per_week_per_signal=freq if freq is not None else [1.0, 1.0],
        duration_seconds=dur if dur is not None else [60.0, 60.0],
    )


def _rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def _store_with(signal_id: str, value: float, quality: str = "good") -> SignalStore:
    store = SignalStore()
    store.set(signal_id, value, 0.0, quality)
    return store


# ---------------------------------------------------------------------------
# _sentinel_for_signal
# ---------------------------------------------------------------------------


class TestSentinelResolution:
    """Sentinel value selection logic."""

    @staticmethod
    def _cfg(overrides: dict[str, float] | None = None) -> SensorDisconnectConfig:
        return _disconnect_cfg(per_signal=overrides or {})

    def test_temperature_signal(self) -> None:
        cfg = self._cfg()
        assert _sentinel_for_signal("press.dryer_temp_zone_1", cfg) == 6553.5

    def test_temperature_signal_coder(self) -> None:
        cfg = self._cfg()
        assert _sentinel_for_signal("coder.printhead_temp", cfg) == 6553.5

    def test_pressure_signal(self) -> None:
        cfg = self._cfg()
        assert _sentinel_for_signal("coder.ink_pressure", cfg) == 0.0

    def test_pressure_signal_nip(self) -> None:
        cfg = self._cfg()
        assert _sentinel_for_signal("press.nip_pressure", cfg) == 0.0

    def test_voltage_signal(self) -> None:
        cfg = self._cfg()
        assert _sentinel_for_signal("coder.supply_voltage", cfg) == -32768.0

    def test_unknown_signal_defaults_zero(self) -> None:
        cfg = self._cfg()
        assert _sentinel_for_signal("press.line_speed", cfg) == 0.0

    def test_per_signal_override_takes_precedence(self) -> None:
        cfg = self._cfg(overrides={"press.dryer_temp_zone_1": 9999.0})
        assert _sentinel_for_signal("press.dryer_temp_zone_1", cfg) == 9999.0

    def test_custom_defaults(self) -> None:
        """Non-default sentinel_defaults are respected."""
        cfg = SensorDisconnectConfig(
            enabled=True,
            frequency_per_24h_per_signal=[1.0, 1.0],
            duration_seconds=[10.0, 10.0],
            sentinel_defaults=SensorDisconnectSentinelConfig(
                temperature=999.0,
                pressure=1.0,
                voltage=-1.0,
            ),
        )
        assert _sentinel_for_signal("env.ambient_temp", cfg) == 999.0
        assert _sentinel_for_signal("coder.ink_pressure", cfg) == 1.0
        assert _sentinel_for_signal("coder.supply_voltage", cfg) == -1.0


# ---------------------------------------------------------------------------
# SensorDisconnectInjector — disabled
# ---------------------------------------------------------------------------


class TestSensorDisconnectDisabled:
    """Disabled config → injector never overrides the store."""

    def test_never_active(self) -> None:
        cfg = _disconnect_cfg(enabled=False)
        inj = SensorDisconnectInjector(cfg, ["press.line_speed"], _rng())
        store = _store_with("press.line_speed", 100.0)

        inj.tick(0.0, store)
        # Jump far into the future
        inj.tick(1_000_000.0, store)

        assert not inj.is_active("press.line_speed", 1_000_000.0)
        sv = store.get("press.line_speed")
        assert sv is not None
        assert sv.quality == "good"

    def test_next_event_is_infinity(self) -> None:
        cfg = _disconnect_cfg(enabled=False)
        inj = SensorDisconnectInjector(cfg, ["press.line_speed"], _rng())
        store = _store_with("press.line_speed", 100.0)
        inj.tick(0.0, store)
        assert inj._next_event["press.line_speed"] == float("inf")

    def test_zero_frequency_is_infinity(self) -> None:
        cfg = _disconnect_cfg(enabled=True, freq=[0.0, 0.0])
        inj = SensorDisconnectInjector(cfg, ["press.line_speed"], _rng())
        store = _store_with("press.line_speed", 100.0)
        inj.tick(0.0, store)
        assert inj._next_event["press.line_speed"] == float("inf")


# ---------------------------------------------------------------------------
# SensorDisconnectInjector — enabled
# ---------------------------------------------------------------------------


class TestSensorDisconnectEnabled:
    """Enabled injector: sentinel value, bad quality, duration, resumption."""

    _SIG = "press.dryer_temp_zone_1"  # temperature → 6553.5 sentinel

    def _make(self, seed: int = 0) -> tuple[SensorDisconnectInjector, SignalStore]:
        cfg = _disconnect_cfg(dur=[10.0, 10.0])
        inj = SensorDisconnectInjector(cfg, [self._SIG], _rng(seed))
        store = _store_with(self._SIG, 150.0)
        return inj, store

    def _force_event_at(self, inj: SensorDisconnectInjector, t: float) -> None:
        """Force next disconnect to start at *t* (after initialisation)."""
        inj._next_event[self._SIG] = t
        inj._event_ends[self._SIG] = -float("inf")

    def test_sentinel_written_during_disconnect(self) -> None:
        inj, store = self._make()
        inj.tick(0.0, store)          # initialise
        self._force_event_at(inj, 1.0)
        inj.tick(1.5, store)          # during disconnect

        sv = store.get(self._SIG)
        assert sv is not None
        assert sv.value == pytest.approx(6553.5)

    def test_quality_bad_during_disconnect(self) -> None:
        inj, store = self._make()
        inj.tick(0.0, store)
        self._force_event_at(inj, 1.0)
        inj.tick(1.5, store)

        sv = store.get(self._SIG)
        assert sv is not None
        assert sv.quality == "bad"

    def test_is_active_true_during_event(self) -> None:
        inj, store = self._make()
        inj.tick(0.0, store)
        self._force_event_at(inj, 1.0)
        inj.tick(1.5, store)

        assert inj.is_active(self._SIG, 1.5)

    def test_is_active_false_after_event(self) -> None:
        inj, store = self._make()
        inj.tick(0.0, store)
        self._force_event_at(inj, 1.0)
        inj.tick(1.5, store)          # start event (duration=10s → ends at 11.5)
        inj.tick(12.0, store)         # past event end

        assert not inj.is_active(self._SIG, 12.0)

    def test_store_not_overridden_after_event(self) -> None:
        """After disconnect ends, store accepts normal generator writes."""
        inj, store = self._make()
        inj.tick(0.0, store)
        self._force_event_at(inj, 1.0)
        inj.tick(1.5, store)          # activate disconnect

        # Simulate generator writing a new normal value
        store.set(self._SIG, 155.0, 12.0, "good")
        inj.tick(12.0, store)         # past event end — injector should not override

        sv = store.get(self._SIG)
        assert sv is not None
        assert sv.value == pytest.approx(155.0)
        assert sv.quality == "good"

    def test_multiple_signals_independent(self) -> None:
        """Each signal has its own independent schedule."""
        sigs = ["press.dryer_temp_zone_1", "coder.printhead_temp"]
        cfg = _disconnect_cfg(dur=[10.0, 10.0])
        inj = SensorDisconnectInjector(cfg, sigs, _rng(99))
        store = SignalStore()
        for s in sigs:
            store.set(s, 150.0, 0.0, "good")

        inj.tick(0.0, store)

        # Force sig[0] to disconnect, leave sig[1] alone
        inj._next_event[sigs[0]] = 1.0
        inj._event_ends[sigs[0]] = -float("inf")
        inj._next_event[sigs[1]] = float("inf")

        inj.tick(1.5, store)

        assert inj.is_active(sigs[0], 1.5)
        assert not inj.is_active(sigs[1], 1.5)
        assert store.get(sigs[0]).quality == "bad"    # type: ignore[union-attr]
        assert store.get(sigs[1]).quality == "good"   # type: ignore[union-attr]

    def test_sentinels_property(self) -> None:
        cfg = _disconnect_cfg()
        sigs = ["press.dryer_temp_zone_1", "coder.ink_pressure", "press.line_speed"]
        inj = SensorDisconnectInjector(cfg, sigs, _rng())
        s = inj.sentinels
        assert s["press.dryer_temp_zone_1"] == pytest.approx(6553.5)
        assert s["coder.ink_pressure"] == pytest.approx(0.0)
        assert s["press.line_speed"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# SensorDisconnectInjector — ground truth
# ---------------------------------------------------------------------------


class TestSensorDisconnectGroundTruth:
    """Ground truth is logged exactly once at the start of each disconnect."""

    _SIG = "press.dryer_temp_zone_1"

    def test_log_called_on_start(self) -> None:
        cfg = _disconnect_cfg()
        inj = SensorDisconnectInjector(cfg, [self._SIG], _rng())
        store = _store_with(self._SIG, 150.0)
        gt = MagicMock()

        inj.tick(0.0, store, ground_truth=gt)
        inj._next_event[self._SIG] = 1.0
        inj._event_ends[self._SIG] = -float("inf")

        inj.tick(1.5, store, ground_truth=gt)

        gt.log_sensor_disconnect.assert_called_once_with(
            pytest.approx(1.5), self._SIG, pytest.approx(6553.5)
        )

    def test_log_not_called_when_no_event(self) -> None:
        cfg = _disconnect_cfg()
        inj = SensorDisconnectInjector(cfg, [self._SIG], _rng())
        store = _store_with(self._SIG, 150.0)
        gt = MagicMock()

        inj.tick(0.0, store, ground_truth=gt)
        # next_event is far in the future — no disconnect fires
        inj.tick(1.0, store, ground_truth=gt)

        gt.log_sensor_disconnect.assert_not_called()

    def test_log_called_once_per_event(self) -> None:
        """Repeated tick calls during the same event do not re-log."""
        cfg = _disconnect_cfg(dur=[10.0, 10.0])
        inj = SensorDisconnectInjector(cfg, [self._SIG], _rng())
        store = _store_with(self._SIG, 150.0)
        gt = MagicMock()

        inj.tick(0.0, store, gt)
        inj._next_event[self._SIG] = 1.0
        inj._event_ends[self._SIG] = -float("inf")

        for t in [1.5, 2.0, 3.0, 5.0]:
            inj.tick(t, store, gt)

        assert gt.log_sensor_disconnect.call_count == 1


# ---------------------------------------------------------------------------
# SensorDisconnectInjector — determinism
# ---------------------------------------------------------------------------


class TestSensorDisconnectDeterminism:
    _SIG = "press.line_speed"

    def _collect_events(self, seed: int, steps: int = 500) -> list[float]:
        """Return list of sim_times when disconnect started."""
        cfg = _disconnect_cfg(freq=[864.0, 864.0], dur=[1.0, 2.0])
        inj = SensorDisconnectInjector(cfg, [self._SIG], np.random.default_rng(seed))
        store = _store_with(self._SIG, 100.0)
        starts: list[float] = []
        t = 0.0
        for _ in range(steps):
            t += 0.1
            was_active = inj.is_active(self._SIG, t - 0.1)
            inj.tick(t, store)
            if inj.is_active(self._SIG, t) and not was_active:
                starts.append(t)
        return starts

    def test_same_seed_same_schedule(self) -> None:
        assert self._collect_events(42) == self._collect_events(42)

    def test_different_seeds_different_schedules(self) -> None:
        a = self._collect_events(1)
        b = self._collect_events(2)
        assert a != b


# ---------------------------------------------------------------------------
# StuckSensorInjector — disabled
# ---------------------------------------------------------------------------


class TestStuckSensorDisabled:
    _SIG = "press.line_speed"

    def test_never_active_when_disabled(self) -> None:
        cfg = _stuck_cfg(enabled=False)
        inj = StuckSensorInjector(cfg, [self._SIG], _rng())
        store = _store_with(self._SIG, 100.0)

        inj.tick(0.0, store)
        inj.tick(1_000_000.0, store)

        assert not inj.is_active(self._SIG, 1_000_000.0)
        sv = store.get(self._SIG)
        assert sv is not None
        assert sv.quality == "good"

    def test_zero_frequency_never_fires(self) -> None:
        cfg = _stuck_cfg(enabled=True, freq=[0.0, 0.0])
        inj = StuckSensorInjector(cfg, [self._SIG], _rng())
        store = _store_with(self._SIG, 100.0)
        inj.tick(0.0, store)
        assert inj._next_event[self._SIG] == float("inf")


# ---------------------------------------------------------------------------
# StuckSensorInjector — enabled
# ---------------------------------------------------------------------------


class TestStuckSensorEnabled:
    """Enabled injector: frozen value, good quality, duration, resumption."""

    _SIG = "press.line_speed"

    def _make(
        self, seed: int = 0, dur: list[float] | None = None
    ) -> tuple[StuckSensorInjector, SignalStore]:
        cfg = _stuck_cfg(dur=dur or [60.0, 60.0])
        inj = StuckSensorInjector(cfg, [self._SIG], _rng(seed))
        store = _store_with(self._SIG, 250.0)
        return inj, store

    def _force_event_at(self, inj: StuckSensorInjector, t: float) -> None:
        inj._next_event[self._SIG] = t
        inj._event_ends[self._SIG] = -float("inf")

    def test_frozen_value_captured_from_store(self) -> None:
        inj, store = self._make()
        store.set(self._SIG, 275.0, 0.0, "good")  # update value before stuck starts
        inj.tick(0.0, store)
        self._force_event_at(inj, 1.0)
        inj.tick(1.5, store)

        assert inj.frozen_value_at(self._SIG, 1.5) == pytest.approx(275.0)

    def test_quality_remains_good_during_stuck(self) -> None:
        inj, store = self._make()
        inj.tick(0.0, store)
        self._force_event_at(inj, 1.0)
        inj.tick(1.5, store)

        sv = store.get(self._SIG)
        assert sv is not None
        assert sv.quality == "good"

    def test_value_held_constant(self) -> None:
        """Repeated ticks during stuck event all return the frozen value."""
        inj, store = self._make()
        store.set(self._SIG, 250.0, 0.0, "good")
        inj.tick(0.0, store)
        self._force_event_at(inj, 1.0)
        inj.tick(1.5, store)  # start stuck, frozen_value = 250.0

        # Simulate generator attempting to write a different value
        store.set(self._SIG, 300.0, 2.0, "good")
        inj.tick(2.0, store)  # still within stuck window

        sv = store.get(self._SIG)
        assert sv is not None
        assert sv.value == pytest.approx(250.0), "Stuck injector must override generator"

    def test_is_active_true_during_event(self) -> None:
        inj, store = self._make()
        inj.tick(0.0, store)
        self._force_event_at(inj, 1.0)
        inj.tick(1.5, store)

        assert inj.is_active(self._SIG, 1.5)

    def test_is_active_false_after_event(self) -> None:
        inj, store = self._make(dur=[10.0, 10.0])
        inj.tick(0.0, store)
        self._force_event_at(inj, 1.0)
        inj.tick(1.5, store)   # start (end = 11.5)
        inj.tick(12.0, store)  # past end

        assert not inj.is_active(self._SIG, 12.0)

    def test_frozen_value_at_returns_none_when_not_stuck(self) -> None:
        inj, store = self._make()
        inj.tick(0.0, store)
        # No event forced — frozen_value_at should return None
        assert inj.frozen_value_at(self._SIG, 0.0) is None

    def test_store_not_overridden_after_event(self) -> None:
        """After the stuck event ends the store accepts normal values."""
        inj, store = self._make(dur=[10.0, 10.0])
        inj.tick(0.0, store)
        self._force_event_at(inj, 1.0)
        inj.tick(1.5, store)   # stuck starts

        store.set(self._SIG, 300.0, 12.0, "good")
        inj.tick(12.0, store)  # event over — injector stops overriding

        sv = store.get(self._SIG)
        assert sv is not None
        assert sv.value == pytest.approx(300.0)
        assert sv.quality == "good"

    def test_deferred_when_signal_absent(self) -> None:
        """If signal absent from store, event is deferred (rescheduled)."""
        cfg = _stuck_cfg(dur=[60.0, 60.0])
        inj = StuckSensorInjector(cfg, [self._SIG], _rng())
        store = SignalStore()   # empty — signal not yet in store

        inj.tick(0.0, store)   # initialise with empty store
        inj._next_event[self._SIG] = 1.0
        inj._event_ends[self._SIG] = -float("inf")

        # Signal still absent — event should be deferred
        inj.tick(1.5, store)
        assert not inj.is_active(self._SIG, 1.5)
        # next_event should have been re-scheduled past 1.5
        assert inj._next_event[self._SIG] > 1.5


# ---------------------------------------------------------------------------
# StuckSensorInjector — ground truth
# ---------------------------------------------------------------------------


class TestStuckSensorGroundTruth:
    _SIG = "press.line_speed"

    def test_log_called_on_start(self) -> None:
        cfg = _stuck_cfg(dur=[60.0, 60.0])
        inj = StuckSensorInjector(cfg, [self._SIG], _rng())
        store = _store_with(self._SIG, 250.0)
        gt = MagicMock()

        inj.tick(0.0, store, ground_truth=gt)
        inj._next_event[self._SIG] = 1.0
        inj._event_ends[self._SIG] = -float("inf")

        inj.tick(1.5, store, ground_truth=gt)

        gt.log_stuck_sensor.assert_called_once_with(
            pytest.approx(1.5),
            self._SIG,
            pytest.approx(250.0),
            pytest.approx(60.0),
        )

    def test_log_not_called_when_no_event(self) -> None:
        cfg = _stuck_cfg()
        inj = StuckSensorInjector(cfg, [self._SIG], _rng())
        store = _store_with(self._SIG, 250.0)
        gt = MagicMock()

        inj.tick(0.0, store, ground_truth=gt)
        inj.tick(1.0, store, ground_truth=gt)

        gt.log_stuck_sensor.assert_not_called()

    def test_log_called_once_per_event(self) -> None:
        cfg = _stuck_cfg(dur=[10.0, 10.0])
        inj = StuckSensorInjector(cfg, [self._SIG], _rng())
        store = _store_with(self._SIG, 250.0)
        gt = MagicMock()

        inj.tick(0.0, store, gt)
        inj._next_event[self._SIG] = 1.0
        inj._event_ends[self._SIG] = -float("inf")

        for t in [1.5, 2.0, 3.0, 5.0]:
            inj.tick(t, store, gt)

        assert gt.log_stuck_sensor.call_count == 1

    def test_log_frozen_value_for_string_signal(self) -> None:
        """String signal: GT log receives 0.0 as frozen_value (numeric fallback)."""
        cfg = _stuck_cfg(dur=[60.0, 60.0])
        sig = "mixer.batch_id"
        inj = StuckSensorInjector(cfg, [sig], _rng())
        store = SignalStore()
        store.set(sig, "BATCH-001", 0.0, "good")
        gt = MagicMock()

        inj.tick(0.0, store, gt)
        inj._next_event[sig] = 1.0
        inj._event_ends[sig] = -float("inf")

        inj.tick(1.5, store, gt)

        gt.log_stuck_sensor.assert_called_once()
        _, _, frozen_val, _ = gt.log_stuck_sensor.call_args.args
        assert frozen_val == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# StuckSensorInjector — determinism
# ---------------------------------------------------------------------------


class TestStuckSensorDeterminism:
    _SIG = "press.line_speed"

    def _collect_events(self, seed: int, steps: int = 200) -> list[float]:
        cfg = _stuck_cfg(freq=[6048.0, 6048.0], dur=[1.0, 2.0])
        inj = StuckSensorInjector(cfg, [self._SIG], np.random.default_rng(seed))
        store = _store_with(self._SIG, 100.0)
        starts: list[float] = []
        t = 0.0
        for _ in range(steps):
            t += 0.1
            was_active = inj.is_active(self._SIG, t - 0.1)
            inj.tick(t, store)
            if inj.is_active(self._SIG, t) and not was_active:
                starts.append(t)
        return starts

    def test_same_seed_same_schedule(self) -> None:
        assert self._collect_events(7) == self._collect_events(7)

    def test_different_seeds_different_schedules(self) -> None:
        assert self._collect_events(1) != self._collect_events(2)
