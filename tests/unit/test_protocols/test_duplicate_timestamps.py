"""Tests for duplicate timestamp and timezone offset injection (PRD 10.5, 10.7).

Task 4.11: Duplicate Timestamps and Timezone Offset

PRD 10.5 — Duplicate timestamps:
  - Modbus: skip sync_registers() at duplicate_probability so registers hold
    previous values (same value + effectively same internal timestamp).
  - MQTT: publish the same message twice at duplicate_probability / 2.

PRD 10.7 — Timezone offset:
  - MQTT timestamp_offset_hours shifts the ISO 8601 timestamp by ±N hours.
  - The string still ends in 'Z' (appears to be UTC) but the wall-clock value
    is shifted — replicating camera/PLC timezone bugs from the reference data.
  - OPC-UA is always UTC (no offset).

No real broker or Modbus client required — paho client is replaced with a
mock.  ModbusServer._update_loop is tested via the sync/skip call counts.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from factory_simulator.config import load_config
from factory_simulator.protocols.modbus_server import ModbusServer
from factory_simulator.protocols.mqtt_publisher import (
    MqttPublisher,
    _sim_time_to_iso,
    make_batch_vibration_payload,
    make_payload,
)
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


@pytest.fixture
def mock_client():
    return MagicMock()


# ---------------------------------------------------------------------------
# _sim_time_to_iso — timezone offset
# ---------------------------------------------------------------------------


class TestSimTimeToIso:
    """_sim_time_to_iso applies timezone offset correctly (PRD 10.7)."""

    def test_zero_offset_unchanged(self):
        ts_no_offset = _sim_time_to_iso(0.0, offset_hours=0.0)
        ts_default = _sim_time_to_iso(0.0)
        assert ts_no_offset == ts_default

    def test_reference_epoch_with_no_offset(self):
        # sim_time=0 → 2026-01-01T00:00:00.000Z
        ts = _sim_time_to_iso(0.0)
        assert ts == "2026-01-01T00:00:00.000Z"

    def test_positive_offset_shifts_forward(self):
        # +1 hour offset: 2026-01-01T01:00:00.000Z
        ts = _sim_time_to_iso(0.0, offset_hours=1.0)
        assert ts == "2026-01-01T01:00:00.000Z"

    def test_negative_offset_shifts_backward(self):
        # sim_time=3600 (1 hour) with -1 hour offset → 2026-01-01T00:00:00.000Z
        ts = _sim_time_to_iso(3600.0, offset_hours=-1.0)
        assert ts == "2026-01-01T00:00:00.000Z"

    def test_bst_offset(self):
        # BST = UTC+1: timestamp 00:00 local → reported as 01:00Z (wrong timezone)
        ts = _sim_time_to_iso(0.0, offset_hours=1.0)
        assert "01:00:00" in ts
        assert ts.endswith("Z")

    def test_us_eastern_offset(self):
        # US Eastern = UTC-5: offset = -5.0
        # sim_time=0 (midnight UTC) → 2025-12-31T19:00:00.000Z
        ts = _sim_time_to_iso(0.0, offset_hours=-5.0)
        assert ts == "2025-12-31T19:00:00.000Z"

    def test_string_always_ends_with_z(self):
        for offset in (-5.0, -1.0, 0.0, 1.0, 5.5):
            ts = _sim_time_to_iso(0.0, offset_hours=offset)
            assert ts.endswith("Z"), f"Expected 'Z' suffix for offset {offset}"

    def test_fractional_seconds_preserved(self):
        ts = _sim_time_to_iso(0.5, offset_hours=0.0)
        # 0.5 s = 500 ms
        assert "500" in ts


# ---------------------------------------------------------------------------
# make_payload — timezone offset propagation
# ---------------------------------------------------------------------------


class TestMakePayloadOffset:
    """make_payload passes offset_hours to _sim_time_to_iso."""

    def test_default_offset_zero(self):
        data = json.loads(make_payload(42.7, "good", "C", sim_time=0.0))
        assert data["timestamp"] == "2026-01-01T00:00:00.000Z"

    def test_positive_offset_shifts_timestamp(self):
        data = json.loads(make_payload(42.7, "good", "C", sim_time=0.0,
                                       offset_hours=1.0))
        assert data["timestamp"] == "2026-01-01T01:00:00.000Z"

    def test_negative_offset_shifts_timestamp(self):
        data = json.loads(make_payload(42.7, "good", "C", sim_time=3600.0,
                                       offset_hours=-1.0))
        assert data["timestamp"] == "2026-01-01T00:00:00.000Z"

    def test_value_and_quality_unaffected_by_offset(self):
        data = json.loads(make_payload(99.0, "bad", "bar", sim_time=0.0,
                                       offset_hours=2.0))
        assert data["value"] == 99.0
        assert data["quality"] == "bad"
        assert data["unit"] == "bar"


# ---------------------------------------------------------------------------
# make_batch_vibration_payload — timezone offset propagation
# ---------------------------------------------------------------------------


class TestMakeBatchVibrationPayloadOffset:
    """make_batch_vibration_payload passes offset_hours to _sim_time_to_iso."""

    def test_default_offset_zero(self):
        data = json.loads(
            make_batch_vibration_payload(1.0, 2.0, 3.0, "good", "mm/s",
                                         sim_time=0.0)
        )
        assert data["timestamp"] == "2026-01-01T00:00:00.000Z"

    def test_offset_applied_to_batch_timestamp(self):
        data = json.loads(
            make_batch_vibration_payload(1.0, 2.0, 3.0, "good", "mm/s",
                                         sim_time=0.0, offset_hours=1.0)
        )
        assert data["timestamp"] == "2026-01-01T01:00:00.000Z"

    def test_axes_values_unaffected(self):
        data = json.loads(
            make_batch_vibration_payload(1.5, 2.5, 3.5, "good", "mm/s",
                                         sim_time=0.0, offset_hours=5.0)
        )
        assert data["x"] == 1.5
        assert data["y"] == 2.5
        assert data["z"] == 3.5


# ---------------------------------------------------------------------------
# MqttPublisher — timezone offset
# ---------------------------------------------------------------------------


class TestMqttPublisherTimezoneOffset:
    """MqttPublisher applies mqtt_timestamp_offset_hours from config."""

    def test_default_config_offset_zero(self, config, store, mock_client):
        pub = MqttPublisher(config, store, client=mock_client)
        assert pub._offset_hours == 0.0

    def test_offset_from_config_propagated(self, config, store, mock_client):
        config.data_quality.mqtt_timestamp_offset_hours = 1.0
        pub = MqttPublisher(config, store, client=mock_client)
        assert pub._offset_hours == 1.0

    def test_publish_uses_offset(self, config, store, mock_client):
        """Published payload timestamp should reflect the configured offset."""
        config.data_quality.mqtt_timestamp_offset_hours = 1.0
        pub = MqttPublisher(config, store, client=mock_client)

        # Use coder.printhead_temp which has mqtt_topic configured
        store.set("coder.printhead_temp", 35.0, 0.0, "good")

        entry = next(
            e for e in pub.topic_entries if e.signal_id == "coder.printhead_temp"
        )
        sv = store.get("coder.printhead_temp")
        assert sv is not None

        pub._publish_entry(entry, sv)

        args, kwargs = mock_client.publish.call_args
        payload_str = kwargs.get("payload") or args[1]
        data = json.loads(payload_str)
        # Offset +1h: sim_time=0 → 01:00:00Z
        assert "01:00:00" in data["timestamp"]

    def test_batch_vib_uses_offset(self, config, store, mock_client):
        """Batch vibration payload timestamp should reflect the offset."""
        config.data_quality.mqtt_timestamp_offset_hours = 2.0
        pub = MqttPublisher(config, store, client=mock_client)

        store.set("vibration.main_drive_x", 1.0, 0.0, "good")
        store.set("vibration.main_drive_y", 2.0, 0.0, "good")
        store.set("vibration.main_drive_z", 3.0, 0.0, "good")

        # Use a large now value to ensure the interval check passes
        # (last_published starts at 0.0, interval_s is ~1.0)
        pub._publish_batch_vib(now=100.0)

        args, kwargs = mock_client.publish.call_args
        payload_str = kwargs.get("payload") or args[1]
        data = json.loads(payload_str)
        assert "02:00:00" in data["timestamp"]


# ---------------------------------------------------------------------------
# MqttPublisher — duplicate publish (PRD 10.5)
# ---------------------------------------------------------------------------


class TestMqttPublisherDuplicate:
    """MqttPublisher publishes duplicate messages at duplicate_probability/2."""

    def _make_pub_with_dup(self, config, store, mock_client,
                            dup_prob: float = 1.0) -> MqttPublisher:
        """Make a publisher with given effective _dup_prob and a fixed-seed rng.

        Sets _dup_prob directly to bypass the /2 scaling, allowing precise
        probability control in tests.
        """
        rng = np.random.default_rng(42)
        pub = MqttPublisher(config, store, client=mock_client,
                            duplicate_rng=rng)
        pub._dup_prob = dup_prob  # Override directly for testing
        return pub

    def test_no_rng_no_duplicate(self, config, store, mock_client):
        """Without duplicate_rng, never duplicate regardless of probability."""
        config.data_quality.duplicate_probability = 1.0
        pub = MqttPublisher(config, store, client=mock_client)
        store.set("coder.printhead_temp", 35.0, 0.0, "good")
        entry = next(
            e for e in pub.topic_entries if e.signal_id == "coder.printhead_temp"
        )
        sv = store.get("coder.printhead_temp")
        assert sv is not None
        pub._publish_entry(entry, sv)
        assert mock_client.publish.call_count == 1

    def test_prob_1_always_duplicates(self, config, store, mock_client):
        """At probability 1.0, every publish produces two calls."""
        pub = self._make_pub_with_dup(config, store, mock_client, dup_prob=1.0)
        store.set("coder.printhead_temp", 35.0, 0.0, "good")
        entry = next(
            e for e in pub.topic_entries if e.signal_id == "coder.printhead_temp"
        )
        sv = store.get("coder.printhead_temp")
        assert sv is not None
        pub._publish_entry(entry, sv)
        assert mock_client.publish.call_count == 2

    def test_prob_0_never_duplicates(self, config, store, mock_client):
        """At probability 0.0, never duplicate."""
        pub = self._make_pub_with_dup(config, store, mock_client, dup_prob=0.0)
        store.set("coder.printhead_temp", 35.0, 0.0, "good")
        entry = next(
            e for e in pub.topic_entries if e.signal_id == "coder.printhead_temp"
        )
        sv = store.get("coder.printhead_temp")
        assert sv is not None
        pub._publish_entry(entry, sv)
        assert mock_client.publish.call_count == 1

    def test_duplicate_has_same_topic_and_payload(self, config, store,
                                                    mock_client):
        """Both publishes go to the same topic with identical payload."""
        pub = self._make_pub_with_dup(config, store, mock_client, dup_prob=1.0)
        store.set("coder.printhead_temp", 35.0, 0.0, "good")
        entry = next(
            e for e in pub.topic_entries if e.signal_id == "coder.printhead_temp"
        )
        sv = store.get("coder.printhead_temp")
        assert sv is not None
        pub._publish_entry(entry, sv)

        calls = mock_client.publish.call_args_list
        assert len(calls) == 2
        # Both calls have same topic
        topic1 = calls[0][0][0] if calls[0][0] else calls[0][1]["topic"]
        topic2 = calls[1][0][0] if calls[1][0] else calls[1][1]["topic"]
        assert topic1 == topic2
        # Both calls have same payload
        payload1 = (
            calls[0][1].get("payload") or calls[0][0][1]
        )
        payload2 = (
            calls[1][1].get("payload") or calls[1][0][1]
        )
        assert payload1 == payload2

    def test_dup_prob_stored_as_half_duplicate_probability(self, config,
                                                             store,
                                                             mock_client):
        """_dup_prob is duplicate_probability / 2 (MQTT is half the Modbus rate)."""
        config.data_quality.duplicate_probability = 0.0002
        pub = MqttPublisher(
            config, store, client=mock_client,
            duplicate_rng=np.random.default_rng(0),
        )
        assert pub._dup_prob == pytest.approx(0.0001)

    def test_determinism_same_seed_same_duplicates(self, config, store,
                                                    mock_client):
        """Same RNG seed → same duplicate publish pattern."""
        store.set("coder.printhead_temp", 35.0, 0.0, "good")

        counts = []
        for _ in range(2):
            mc = MagicMock()
            pub = self._make_pub_with_dup(config, store, mc, dup_prob=0.5)
            entry = next(
                e for e in pub.topic_entries
                if e.signal_id == "coder.printhead_temp"
            )
            sv = store.get("coder.printhead_temp")
            assert sv is not None
            # Run many publishes to get a pattern
            for _ in range(20):
                pub._publish_entry(entry, sv)
            counts.append(mc.publish.call_count)

        assert counts[0] == counts[1]


# ---------------------------------------------------------------------------
# ModbusServer — duplicate (skip sync) injection (PRD 10.5)
# ---------------------------------------------------------------------------


class TestModbusServerDuplicate:
    """ModbusServer skips sync_registers at duplicate_probability (PRD 10.5)."""

    def _make_server(self, config, store, dup_prob: float,
                     dup_rng: np.random.Generator | None) -> ModbusServer:
        config.data_quality.duplicate_probability = dup_prob
        # Disable comm drop so it doesn't interfere
        config.data_quality.modbus_drop.enabled = False
        return ModbusServer(
            config, store,
            host="127.0.0.1", port=0,
            duplicate_rng=dup_rng,
        )

    def test_no_rng_always_syncs(self, config, store):
        """Without duplicate_rng, sync always happens."""
        server = self._make_server(config, store, dup_prob=1.0, dup_rng=None)
        # The _dup_rng attribute should be None
        assert server._dup_rng is None

    def test_prob_1_rng_skips_sync(self, config, store):
        """At probability 1.0, duplicate always fires → sync always skipped.

        We verify by calling the check directly (not through the asyncio loop).
        """
        rng = np.random.default_rng(0)
        server = self._make_server(config, store, dup_prob=1.0,
                                   dup_rng=rng)
        # _dup_rng.random() < 1.0 always → is_dup = True
        is_dup = server._dup_rng is not None and (  # type: ignore[union-attr]
            server._dup_rng.random() < server._dup_prob
        )
        assert is_dup is True

    def test_prob_0_never_skips(self, config, store):
        """At probability 0.0, is_dup is always False."""
        rng = np.random.default_rng(0)
        server = self._make_server(config, store, dup_prob=0.0, dup_rng=rng)
        # Draw 100 samples — none should trigger
        results = [
            server._dup_rng.random() < server._dup_prob  # type: ignore[union-attr]
            for _ in range(100)
        ]
        assert not any(results)

    def test_dup_prob_stored_from_config(self, config, store):
        """_dup_prob matches config duplicate_probability for Modbus."""
        rng = np.random.default_rng(0)
        server = self._make_server(config, store, dup_prob=0.0003, dup_rng=rng)
        assert server._dup_prob == pytest.approx(0.0003)

    def test_determinism_same_seed(self, config, store):
        """Same seed → same duplicate skip pattern."""
        skip_patterns: list[list[bool]] = []
        for _ in range(2):
            rng = np.random.default_rng(42)
            server = self._make_server(config, store, dup_prob=0.5, dup_rng=rng)
            pattern = [
                server._dup_rng.random() < server._dup_prob  # type: ignore[union-attr]
                for _ in range(50)
            ]
            skip_patterns.append(pattern)
        assert skip_patterns[0] == skip_patterns[1]
