"""Unit tests for communication drop injection (PRD Section 10.2).

Tests the CommDropScheduler state machine and verify that ModbusServer,
OpcuaServer, and MqttPublisher each honour the drop window by freezing
updates / suppressing publishes.

No live protocol servers are started here — Modbus and MQTT adapters are
tested via their public sync/publish methods; OPC-UA drop state is verified
via the scheduler and property alone (full OPC-UA server behaviour is
covered in the integration tests).

PRD Reference: Section 10.2 (Communication Drops)
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from factory_simulator.config import CommDropConfig, load_config
from factory_simulator.protocols.comm_drop import CommDropScheduler
from factory_simulator.protocols.modbus_server import (
    ModbusServer,
    decode_float32_abcd,
)
from factory_simulator.protocols.mqtt_publisher import MqttPublisher
from factory_simulator.protocols.opcua_server import OpcuaServer
from factory_simulator.store import SignalStore

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "factory.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return load_config(_CONFIG_PATH)


@pytest.fixture
def store():
    return SignalStore()


# ---------------------------------------------------------------------------
# CommDropScheduler
# ---------------------------------------------------------------------------


class TestCommDropSchedulerDisabled:
    """Disabled config → drop never becomes active."""

    @staticmethod
    def _disabled_cfg() -> CommDropConfig:
        return CommDropConfig(
            enabled=False,
            frequency_per_hour=[1.0, 2.0],
            duration_seconds=[1.0, 10.0],
        )

    def test_never_active(self) -> None:
        cfg = self._disabled_cfg()
        sched = CommDropScheduler(cfg, np.random.default_rng(0))
        t = time.monotonic()
        sched.update(t)
        assert not sched.is_active(t)

    def test_next_drop_infinity(self) -> None:
        cfg = self._disabled_cfg()
        sched = CommDropScheduler(cfg, np.random.default_rng(0))
        sched.update(time.monotonic())
        assert sched.next_drop_at == float("inf")


class TestCommDropSchedulerEnabled:
    """Enabled config → drops are scheduled and become active."""

    @staticmethod
    def _fast_cfg() -> CommDropConfig:
        # 1 drop per ~6 seconds average; 50-100 ms duration
        return CommDropConfig(
            enabled=True,
            frequency_per_hour=[600.0, 600.0],
            duration_seconds=[0.05, 0.1],
        )

    def _make_sched(self, seed: int = 42) -> CommDropScheduler:
        return CommDropScheduler(self._fast_cfg(), np.random.default_rng(seed))

    def test_initializes_next_drop(self) -> None:
        sched = self._make_sched()
        t = time.monotonic()
        sched.update(t)
        assert t < sched.next_drop_at < t + 60

    def test_drop_activates_after_interval(self) -> None:
        """Manually advance t past next_drop_at and verify drop is active."""
        sched = self._make_sched(seed=7)
        t0 = time.monotonic()
        sched.update(t0)
        t1 = sched.next_drop_at + 0.001
        sched.update(t1)
        assert sched.is_active(t1)

    def test_drop_ends_after_duration(self) -> None:
        """After the drop duration expires, is_active returns False."""
        sched = self._make_sched(seed=3)
        t0 = time.monotonic()
        sched.update(t0)
        t1 = sched.next_drop_at + 0.001
        sched.update(t1)
        assert sched.is_active(t1)
        t2 = sched.drop_ends_at + 0.001
        sched.update(t2)
        assert not sched.is_active(t2)

    def test_next_drop_rescheduled_after_drop(self) -> None:
        """After a drop ends, the scheduler schedules another drop."""
        sched = self._make_sched(seed=11)
        t0 = time.monotonic()
        sched.update(t0)
        t1 = sched.next_drop_at + 0.001
        sched.update(t1)
        drop_end = sched.drop_ends_at
        t2 = drop_end + 0.001
        sched.update(t2)
        assert sched.next_drop_at > drop_end

    def test_multiple_drops_occur(self) -> None:
        """Scheduler produces multiple drops when time is advanced far enough."""
        sched = self._make_sched(seed=99)
        t = time.monotonic()
        sched.update(t)
        drop_count = 0
        for _ in range(240):
            t += 0.5
            sched.update(t)
            if sched.is_active(t):
                drop_count += 1
        # ~1 drop per 6s over 120s → expect several; allow generous range
        assert drop_count >= 5


class TestCommDropSchedulerDeterminism:
    """Same seed → identical drop schedule."""

    def test_same_seed_same_schedule(self) -> None:
        cfg = CommDropConfig(
            enabled=True,
            frequency_per_hour=[60.0, 60.0],
            duration_seconds=[1.0, 2.0],
        )
        t0 = 1000.0

        def collect_drops(seed: int) -> list[tuple[float, float]]:
            sched = CommDropScheduler(cfg, np.random.default_rng(seed))
            drops: list[tuple[float, float]] = []
            t = t0
            for _ in range(200):
                t += 1.0
                sched.update(t)
                if sched.is_active(t) and (
                    not drops or drops[-1][1] < t - 0.5
                ):
                    drops.append((t, sched.drop_ends_at))
            return drops

        assert collect_drops(42) == collect_drops(42)

    def test_different_seeds_different_schedules(self) -> None:
        cfg = CommDropConfig(
            enabled=True,
            frequency_per_hour=[60.0, 60.0],
            duration_seconds=[1.0, 2.0],
        )
        t0 = 1000.0
        sched_a = CommDropScheduler(cfg, np.random.default_rng(1))
        sched_b = CommDropScheduler(cfg, np.random.default_rng(2))
        t = t0
        for _ in range(20):
            t += 1.0
            sched_a.update(t)
            sched_b.update(t)
        # Different seeds → different next_drop times (statistically guaranteed)
        assert sched_a.next_drop_at != sched_b.next_drop_at


# ---------------------------------------------------------------------------
# ModbusServer comm drop integration
# ---------------------------------------------------------------------------


class TestModbusCommDrop:
    """ModbusServer pauses sync_registers during an active drop."""

    def _make_server(self, seed: int = 0) -> ModbusServer:
        config = load_config(_CONFIG_PATH)
        store = SignalStore()
        return ModbusServer(config, store, comm_drop_rng=np.random.default_rng(seed))

    def _hr_entry(self, server: ModbusServer, signal_id: str):  # type: ignore[return]
        return next(
            e for e in server.register_map.hr_entries
            if e.signal_id == signal_id
        )

    def test_comm_drop_active_disabled(self) -> None:
        """comm_drop_active is always False when drops are disabled."""
        config = load_config(_CONFIG_PATH)
        config.data_quality.modbus_drop.enabled = False
        server = ModbusServer(config, SignalStore())
        assert not server.comm_drop_active

    def test_comm_drop_active_forced(self) -> None:
        """Forcing the scheduler into a drop makes comm_drop_active True."""
        server = self._make_server()
        server._drop_scheduler._drop_end = time.monotonic() + 3600.0
        assert server.comm_drop_active

    def test_registers_freeze_during_drop(self) -> None:
        """Registers do not update while a drop is active."""
        server = self._make_server()
        store = server._store

        store.set("press.line_speed", 100.0, 0.0, "good")
        server.sync_registers()

        entry = self._hr_entry(server, "press.line_speed")
        addr = entry.address + 1
        regs_before = list(server._hr_block.getValues(addr, 2))

        # Activate drop
        server._drop_scheduler._drop_end = time.monotonic() + 3600.0
        store.set("press.line_speed", 999.0, 1.0, "good")

        # Simulate _update_loop: skip sync_registers when drop active
        now = time.monotonic()
        server._drop_scheduler.update(now)
        if not server._drop_scheduler.is_active(now):
            server.sync_registers()

        regs_during = list(server._hr_block.getValues(addr, 2))
        assert regs_during == regs_before

    def test_registers_update_after_drop(self) -> None:
        """Registers update normally once the drop ends."""
        server = self._make_server()
        store = server._store

        store.set("press.line_speed", 100.0, 0.0, "good")
        server.sync_registers()

        # Drop already ended
        server._drop_scheduler._drop_end = time.monotonic() - 1.0
        store.set("press.line_speed", 200.0, 1.0, "good")

        now = time.monotonic()
        server._drop_scheduler.update(now)
        if not server._drop_scheduler.is_active(now):
            server.sync_registers()

        entry = self._hr_entry(server, "press.line_speed")
        addr = entry.address + 1
        regs = list(server._hr_block.getValues(addr, 2))
        decoded = decode_float32_abcd(regs)
        assert abs(decoded - 200.0) < 0.1


# ---------------------------------------------------------------------------
# MqttPublisher comm drop integration
# ---------------------------------------------------------------------------


class TestMqttCommDrop:
    """MqttPublisher skips _publish_due during an active drop."""

    def _make_publisher(self) -> tuple[MqttPublisher, MagicMock]:
        config = load_config(_CONFIG_PATH)
        store = SignalStore()
        mock_client = MagicMock()
        pub = MqttPublisher(
            config, store,
            client=mock_client,
            comm_drop_rng=np.random.default_rng(0),
        )
        return pub, mock_client

    def test_comm_drop_active_disabled(self) -> None:
        config = load_config(_CONFIG_PATH)
        config.data_quality.mqtt_drop.enabled = False
        pub = MqttPublisher(config, SignalStore(), client=MagicMock())
        assert not pub.comm_drop_active

    def test_comm_drop_active_forced(self) -> None:
        pub, _ = self._make_publisher()
        pub._drop_scheduler._drop_end = time.monotonic() + 3600.0
        assert pub.comm_drop_active

    def test_no_publish_during_drop(self) -> None:
        """_publish_due is not called while a drop is active."""
        pub, mock_client = self._make_publisher()

        entry = pub.topic_entries[0]
        pub._store.set(entry.signal_id, 42.0, 0.0, "good")

        pub._drop_scheduler._drop_end = time.monotonic() + 3600.0

        now = time.monotonic()
        pub._drop_scheduler.update(now)
        if not pub._drop_scheduler.is_active(now):
            pub._publish_due(now)

        mock_client.publish.assert_not_called()

    def test_publishes_after_drop(self) -> None:
        """publish() is called once the drop ends."""
        pub, mock_client = self._make_publisher()

        entry = pub.topic_entries[0]
        pub._store.set(entry.signal_id, 42.0, 0.0, "good")

        # Drop already ended
        pub._drop_scheduler._drop_end = time.monotonic() - 1.0

        now = time.monotonic()
        pub._drop_scheduler.update(now)
        if not pub._drop_scheduler.is_active(now):
            pub._publish_due(now)

        assert mock_client.publish.call_count >= 1

    def test_drop_frequency_config_respected(self) -> None:
        """Scheduler uses mqtt_drop config frequency, not other protocols'."""
        config = load_config(_CONFIG_PATH)
        config.data_quality.mqtt_drop.frequency_per_hour = [600.0, 600.0]
        config.data_quality.mqtt_drop.duration_seconds = [0.01, 0.02]
        config.data_quality.mqtt_drop.enabled = True
        pub = MqttPublisher(
            config, SignalStore(),
            client=MagicMock(),
            comm_drop_rng=np.random.default_rng(7),
        )
        t = time.monotonic()
        pub._drop_scheduler.update(t)
        # 600/h → mean interval 6s → next drop is scheduled within 120s
        assert t < pub._drop_scheduler.next_drop_at < t + 120


# ---------------------------------------------------------------------------
# OpcuaServer comm drop state
# ---------------------------------------------------------------------------


class TestOpcuaCommDrop:
    """OpcuaServer exposes comm_drop_active and uses opcua_stale config."""

    def test_comm_drop_active_disabled(self) -> None:
        config = load_config(_CONFIG_PATH)
        config.data_quality.opcua_stale.enabled = False
        server = OpcuaServer(config, SignalStore())
        assert not server.comm_drop_active

    def test_comm_drop_active_forced(self) -> None:
        config = load_config(_CONFIG_PATH)
        server = OpcuaServer(config, SignalStore())
        server._drop_scheduler._drop_end = time.monotonic() + 3600.0
        assert server.comm_drop_active

    def test_comm_drop_not_active_initially(self) -> None:
        """No drop is active immediately after construction."""
        config = load_config(_CONFIG_PATH)
        # Very low frequency → first drop won't happen for hours
        config.data_quality.opcua_stale.frequency_per_hour = [0.01, 0.01]
        server = OpcuaServer(
            config, SignalStore(), comm_drop_rng=np.random.default_rng(42),
        )
        assert not server.comm_drop_active

    def test_drop_scheduler_uses_opcua_stale_config(self) -> None:
        """OpcuaServer uses opcua_stale config (not modbus_drop)."""
        config = load_config(_CONFIG_PATH)
        config.data_quality.opcua_stale.enabled = False
        config.data_quality.modbus_drop.enabled = True
        server = OpcuaServer(config, SignalStore())
        server._drop_scheduler.update(time.monotonic())
        assert server._drop_scheduler.next_drop_at == float("inf")

    def test_deterministic_with_same_rng(self) -> None:
        """Two OpcuaServer instances with same seed produce same schedule."""
        config = load_config(_CONFIG_PATH)
        store = SignalStore()
        s1 = OpcuaServer(config, store, comm_drop_rng=np.random.default_rng(5))
        s2 = OpcuaServer(config, store, comm_drop_rng=np.random.default_rng(5))
        t = time.monotonic()
        s1._drop_scheduler.update(t)
        s2._drop_scheduler.update(t)
        assert s1._drop_scheduler.next_drop_at == s2._drop_scheduler.next_drop_at
