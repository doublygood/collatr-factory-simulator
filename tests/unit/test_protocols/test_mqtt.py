"""Unit tests for the MQTT publisher module.

Tests topic map construction (Appendix C), payload format (PRD 3.3.4),
QoS assignment (PRD 3.3.5), retain flags (PRD 3.3.8), and publish
scheduling logic (timed vs event-driven).  No real broker required --
the paho client is replaced with a mock.

PRD Reference: Section 3.3, Appendix C (MQTT Topic Map)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory_simulator.config import load_config
from factory_simulator.protocols.mqtt_publisher import (
    BatchVibrationEntry,
    MqttPublisher,
    _is_event_driven,
    _qos_for_topic,
    _retain_for_topic,
    _worst_quality,
    build_batch_vibration_entry,
    build_topic_map,
    make_batch_vibration_payload,
    make_payload,
)
from factory_simulator.store import SignalStore

# Path to the shared factory config
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "factory.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    """Load the real factory config."""
    return load_config(_CONFIG_PATH)


@pytest.fixture
def store():
    """Empty SignalStore for testing."""
    return SignalStore()


@pytest.fixture
def mock_client():
    """A MagicMock standing in for paho.mqtt.Client."""
    return MagicMock()


@pytest.fixture
def publisher(config, store, mock_client):
    """MqttPublisher with injected mock client."""
    return MqttPublisher(config, store, client=mock_client)


# ---------------------------------------------------------------------------
# Helper functions: QoS and retain
# ---------------------------------------------------------------------------


class TestQosForTopic:
    """_qos_for_topic returns correct QoS level per PRD 3.3.5."""

    @pytest.mark.parametrize("relative", [
        "coder/state",
        "coder/prints_total",
        "coder/nozzle_health",
        "coder/gutter_fault",
    ])
    def test_qos1_for_critical_coder_topics(self, relative):
        assert _qos_for_topic(relative) == 1

    @pytest.mark.parametrize("relative", [
        "coder/ink_level",
        "coder/printhead_temp",
        "coder/ink_pump_speed",
        "coder/ink_pressure",
        "coder/ink_viscosity_actual",
        "coder/supply_voltage",
        "coder/ink_consumption_ml",
        "env/ambient_temp",
        "env/ambient_humidity",
        "vibration/main_drive_x",
        "vibration/main_drive_y",
        "vibration/main_drive_z",
    ])
    def test_qos0_for_analog_env_vibration(self, relative):
        assert _qos_for_topic(relative) == 0


class TestRetainForTopic:
    """_retain_for_topic reflects PRD 3.3.8 retain rules."""

    @pytest.mark.parametrize("relative", [
        "coder/state",
        "coder/prints_total",
        "coder/ink_level",
        "coder/nozzle_health",
        "env/ambient_temp",
        "env/ambient_humidity",
    ])
    def test_retain_true_for_non_vibration(self, relative):
        assert _retain_for_topic(relative) is True

    @pytest.mark.parametrize("relative", [
        "vibration/main_drive_x",
        "vibration/main_drive_y",
        "vibration/main_drive_z",
    ])
    def test_retain_false_for_vibration(self, relative):
        assert _retain_for_topic(relative) is False


class TestIsEventDriven:
    """_is_event_driven identifies event-triggered topics."""

    @pytest.mark.parametrize("relative", [
        "coder/state",
        "coder/prints_total",
        "coder/nozzle_health",
        "coder/gutter_fault",
    ])
    def test_event_driven_for_state_and_fault(self, relative):
        assert _is_event_driven(relative) is True

    @pytest.mark.parametrize("relative", [
        "coder/ink_level",
        "coder/printhead_temp",
        "env/ambient_temp",
        "vibration/main_drive_x",
    ])
    def test_timed_for_analog_signals(self, relative):
        assert _is_event_driven(relative) is False


# ---------------------------------------------------------------------------
# build_topic_map
# ---------------------------------------------------------------------------


class TestBuildTopicMap:
    """build_topic_map constructs correct TopicEntry objects from config."""

    def test_returns_16_topics_for_packaging_profile(self, config):
        # Packaging: 11 coder + 2 env + 3 vibration = 16 topics
        entries = build_topic_map(config)
        assert len(entries) == 16

    def test_all_entries_have_correct_prefix(self, config):
        entries = build_topic_map(config)
        expected_prefix = (
            f"{config.protocols.mqtt.topic_prefix}/"
            f"{config.factory.site_id}/"
            f"{config.protocols.mqtt.line_id}/"
        )
        for entry in entries:
            assert entry.topic.startswith(expected_prefix), (
                f"Topic {entry.topic!r} does not start with {expected_prefix!r}"
            )

    def test_coder_state_topic_path(self, config):
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        entry = by_sig["coder.state"]
        assert entry.topic == "collatr/factory/demo/packaging1/coder/state"

    def test_env_ambient_temp_topic_path(self, config):
        # PRD Appendix C: env/ not environment/
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        entry = by_sig["environment.ambient_temp"]
        assert entry.topic == "collatr/factory/demo/packaging1/env/ambient_temp"

    def test_vibration_topic_path(self, config):
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        entry = by_sig["vibration.main_drive_x"]
        assert entry.topic == (
            "collatr/factory/demo/packaging1/vibration/main_drive_x"
        )

    def test_coder_state_is_qos1(self, config):
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        assert by_sig["coder.state"].qos == 1

    def test_coder_prints_total_is_qos1(self, config):
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        assert by_sig["coder.prints_total"].qos == 1

    def test_coder_ink_level_is_qos0(self, config):
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        assert by_sig["coder.ink_level"].qos == 0

    def test_env_signals_are_qos0(self, config):
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        assert by_sig["environment.ambient_temp"].qos == 0
        assert by_sig["environment.ambient_humidity"].qos == 0

    def test_vibration_signals_are_qos0(self, config):
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        assert by_sig["vibration.main_drive_x"].qos == 0

    def test_vibration_signals_no_retain(self, config):
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        assert by_sig["vibration.main_drive_x"].retain is False
        assert by_sig["vibration.main_drive_y"].retain is False
        assert by_sig["vibration.main_drive_z"].retain is False

    def test_coder_signals_retained(self, config):
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        for sig in [
            "coder.state", "coder.prints_total", "coder.ink_level",
            "coder.nozzle_health", "coder.gutter_fault",
        ]:
            assert by_sig[sig].retain is True, f"{sig} should be retained"

    def test_env_signals_retained(self, config):
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        assert by_sig["environment.ambient_temp"].retain is True
        assert by_sig["environment.ambient_humidity"].retain is True

    def test_event_driven_signals_have_zero_interval(self, config):
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        for sig in [
            "coder.state", "coder.prints_total",
            "coder.nozzle_health", "coder.gutter_fault",
        ]:
            assert by_sig[sig].interval_s == 0.0, (
                f"{sig} should be event-driven (interval_s == 0.0)"
            )

    def test_timed_signals_have_nonzero_interval(self, config):
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        assert by_sig["coder.ink_level"].interval_s == 60.0
        assert by_sig["coder.printhead_temp"].interval_s == 30.0
        assert by_sig["coder.ink_pump_speed"].interval_s == 5.0
        assert by_sig["vibration.main_drive_x"].interval_s == 1.0
        assert by_sig["environment.ambient_temp"].interval_s == 60.0

    def test_unit_strings_populated(self, config):
        entries = build_topic_map(config)
        by_sig = {e.signal_id: e for e in entries}
        assert by_sig["environment.ambient_temp"].unit == "C"
        assert by_sig["vibration.main_drive_x"].unit == "mm/s"
        assert by_sig["coder.ink_level"].unit == "%"

    def test_no_topic_entries_for_signals_without_mqtt_topic(self, config):
        # Press signals have no mqtt_topic; they must not appear in the map
        entries = build_topic_map(config)
        signal_ids = {e.signal_id for e in entries}
        assert "press.line_speed" not in signal_ids
        assert "press.dryer_temp_zone_1" not in signal_ids
        assert "energy.line_power" not in signal_ids


# ---------------------------------------------------------------------------
# make_payload
# ---------------------------------------------------------------------------


class TestMakePayload:
    """make_payload produces compliant PRD 3.3.4 JSON payloads."""

    def test_payload_is_valid_json(self):
        data = json.loads(make_payload(42.7, "good", "C", sim_time=0.0))
        assert isinstance(data, dict)

    def test_payload_has_required_fields(self):
        data = json.loads(make_payload(42.7, "good", "C", sim_time=0.0))
        assert set(data.keys()) == {"timestamp", "value", "unit", "quality"}

    def test_value_is_numeric_not_string(self):
        data = json.loads(make_payload(42.7, "good", "C", sim_time=0.0))
        assert isinstance(data["value"], float | int)
        assert data["value"] == pytest.approx(42.7)

    def test_integer_value(self):
        data = json.loads(make_payload(100, "good", "count", sim_time=0.0))
        assert isinstance(data["value"], int | float)
        assert data["value"] == 100

    def test_quality_field(self):
        for quality in ("good", "uncertain", "bad"):
            data = json.loads(make_payload(1.0, quality, "C", sim_time=0.0))
            assert data["quality"] == quality

    def test_unit_field(self):
        data = json.loads(make_payload(35.0, "good", "m/min", sim_time=0.0))
        assert data["unit"] == "m/min"

    def test_timestamp_is_iso8601_utc(self):
        from datetime import datetime
        data = json.loads(make_payload(1.0, "good", "C", sim_time=0.0))
        ts = data["timestamp"]
        # Must end in Z (UTC) and contain T
        assert ts.endswith("Z")
        assert "T" in ts
        # Should be parseable
        # Remove trailing Z, add +00:00 for fromisoformat
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    def test_timestamp_has_milliseconds(self):
        data = json.loads(make_payload(1.0, "good", "C", sim_time=0.0))
        ts = data["timestamp"]
        # Format: ...T14:30:00.000Z  -- 3 digits after the dot
        parts = ts.split(".")
        assert len(parts) == 2
        ms_part = parts[1].rstrip("Z")
        assert len(ms_part) == 3, f"Expected 3-digit ms, got {ms_part!r}"

    def test_timestamp_uses_sim_time_not_wall_clock(self):
        """Payload timestamp must reflect sim_time, not wall clock (Rule 6)."""
        # sim_time=3600 = 1 hour from reference epoch (2026-01-01T00:00:00Z)
        data = json.loads(make_payload(1.0, "good", "C", sim_time=3600.0))
        ts = data["timestamp"]
        assert ts == "2026-01-01T01:00:00.000Z"


# ---------------------------------------------------------------------------
# MqttPublisher.publish_due
# ---------------------------------------------------------------------------


class TestPublishDue:
    """_publish_due triggers correct publishes (timed and event-driven)."""

    def _make_store_with_values(self, signal_values: dict[str, float]) -> SignalStore:
        store = SignalStore()
        for sig_id, val in signal_values.items():
            store.set(sig_id, val, timestamp=0.0, quality="good")
        return store

    def test_timed_signal_publishes_after_interval(self, config, mock_client):
        store = self._make_store_with_values({"coder.ink_level": 85.0})
        pub = MqttPublisher(config, store, client=mock_client)
        by_sig = {e.signal_id: e for e in pub.topic_entries}
        entry = by_sig["coder.ink_level"]
        # Set last_published far in the past to force publish
        entry.last_published = 0.0
        now = entry.interval_s + 1.0  # > interval
        pub._publish_due(now)
        mock_client.publish.assert_called_once()
        call_kwargs = mock_client.publish.call_args
        topic_arg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("topic")
        # Verify correct topic used
        assert "coder/ink_level" in topic_arg or (
            call_kwargs[0][0] == entry.topic
        )

    def test_timed_signal_does_not_publish_before_interval(
        self, config, mock_client
    ):
        store = self._make_store_with_values({"coder.ink_level": 85.0})
        pub = MqttPublisher(config, store, client=mock_client)
        by_sig = {e.signal_id: e for e in pub.topic_entries}
        entry = by_sig["coder.ink_level"]
        # Just published
        now = time.monotonic()
        entry.last_published = now
        pub._publish_due(now + 1.0)  # 1s later, interval is 60s
        mock_client.publish.assert_not_called()

    def test_event_driven_publishes_on_value_change(self, config, mock_client):
        store = self._make_store_with_values({"coder.state": 2.0})
        pub = MqttPublisher(config, store, client=mock_client)
        by_sig = {e.signal_id: e for e in pub.topic_entries}
        entry = by_sig["coder.state"]
        entry.last_value = None  # Not published yet
        pub._publish_due(time.monotonic())
        mock_client.publish.assert_called_once()

    def test_event_driven_skips_when_value_unchanged(self, config, mock_client):
        store = self._make_store_with_values({"coder.state": 2.0})
        pub = MqttPublisher(config, store, client=mock_client)
        by_sig = {e.signal_id: e for e in pub.topic_entries}
        entry = by_sig["coder.state"]
        entry.last_value = 2.0  # Same as current
        pub._publish_due(time.monotonic())
        mock_client.publish.assert_not_called()

    def test_skips_signals_not_in_store(self, config, mock_client):
        store = SignalStore()  # Empty -- no signals
        pub = MqttPublisher(config, store, client=mock_client)
        # Force all entries to be past their interval
        now = time.monotonic()
        for entry in pub.topic_entries:
            entry.last_published = 0.0
        pub._publish_due(now + 9999.0)
        mock_client.publish.assert_not_called()

    def test_publish_uses_correct_qos(self, config, mock_client):
        store = self._make_store_with_values({"coder.state": 1.0})
        pub = MqttPublisher(config, store, client=mock_client)
        by_sig = {e.signal_id: e for e in pub.topic_entries}

        # Freeze all entries so only coder.state (event-driven) fires
        now = time.monotonic()
        for entry in pub.topic_entries:
            entry.last_published = now + 9999.0   # far future, won't fire
            entry.last_value = entry.last_value   # keep as-is

        # Reset only coder.state so it fires on value change
        state_entry = by_sig["coder.state"]
        state_entry.last_value = None  # Will differ from 1.0 → publish
        pub._publish_due(now)

        # Exactly one publish call for coder.state
        assert mock_client.publish.call_count == 1
        call_kwargs = mock_client.publish.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        assert kwargs.get("qos") == 1

    def test_publish_retain_false_for_vibration(self, config, mock_client):
        store = self._make_store_with_values({"vibration.main_drive_x": 4.2})
        pub = MqttPublisher(config, store, client=mock_client)
        by_sig = {e.signal_id: e for e in pub.topic_entries}
        entry = by_sig["vibration.main_drive_x"]
        entry.last_published = 0.0  # Force publish
        pub._publish_due(entry.interval_s + 1.0)
        assert mock_client.publish.called
        call_kwargs = mock_client.publish.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        assert kwargs.get("retain") is False

    def test_publish_retain_true_for_coder(self, config, mock_client):
        store = self._make_store_with_values({"coder.ink_level": 75.0})
        pub = MqttPublisher(config, store, client=mock_client)
        by_sig = {e.signal_id: e for e in pub.topic_entries}
        entry = by_sig["coder.ink_level"]
        entry.last_published = 0.0
        pub._publish_due(entry.interval_s + 1.0)
        assert mock_client.publish.called
        call_kwargs = mock_client.publish.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        assert kwargs.get("retain") is True

    def test_payload_json_structure_in_publish(self, config, mock_client):
        """Payload passed to client.publish is valid JSON with required fields."""
        store = self._make_store_with_values({"coder.ink_level": 80.0})
        pub = MqttPublisher(config, store, client=mock_client)
        by_sig = {e.signal_id: e for e in pub.topic_entries}
        entry = by_sig["coder.ink_level"]
        entry.last_published = 0.0
        pub._publish_due(entry.interval_s + 1.0)

        assert mock_client.publish.called
        call_kwargs = mock_client.publish.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        payload_bytes = kwargs.get("payload")
        assert payload_bytes is not None
        data = json.loads(payload_bytes)
        assert "timestamp" in data
        assert "value" in data
        assert "unit" in data
        assert "quality" in data
        assert data["value"] == pytest.approx(80.0)
        assert data["quality"] == "good"


# ---------------------------------------------------------------------------
# MqttPublisher construction
# ---------------------------------------------------------------------------


class TestMqttPublisherConstruction:
    """MqttPublisher initialises correctly and exposes the topic map."""

    def test_topic_entries_populated(self, publisher):
        assert len(publisher.topic_entries) == 16  # packaging profile

    def test_publisher_host_from_config(self, config, store, mock_client):
        pub = MqttPublisher(config, store, client=mock_client)
        assert pub._host == config.protocols.mqtt.broker_host

    def test_publisher_host_override(self, config, store, mock_client):
        pub = MqttPublisher(config, store, host="testbroker", client=mock_client)
        assert pub._host == "testbroker"

    def test_publisher_port_override(self, config, store, mock_client):
        pub = MqttPublisher(config, store, port=18883, client=mock_client)
        assert pub._port == 18883

    def test_create_client_sets_lwt(self, config, store):
        """_create_client configures the LWT before connect."""
        with patch("paho.mqtt.client.Client") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            # Call _create_client indirectly by not passing a client
            MqttPublisher(config, store)
            mock_instance.will_set.assert_called_once()
            lwt_call = mock_instance.will_set.call_args
            topic_arg = lwt_call[0][0] if lwt_call[0] else lwt_call[1].get("topic")
            # PRD: LWT on the status topic
            assert "status" in topic_arg or topic_arg == config.protocols.mqtt.lwt_topic

    def test_create_client_sets_buffer(self, config, store):
        with patch("paho.mqtt.client.Client") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            MqttPublisher(config, store)
            mock_instance.max_queued_messages_set.assert_called_once_with(
                config.protocols.mqtt.buffer_limit
            )


# ---------------------------------------------------------------------------
# Async lifecycle (smoke test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_and_stop(config, store, mock_client):
    """start() connects and loop_start(); stop() cancels and disconnects."""
    pub = MqttPublisher(config, store, client=mock_client)
    await pub.start()
    mock_client.connect.assert_called_once_with(
        pub._host, pub._port, keepalive=60
    )
    mock_client.loop_start.assert_called_once()
    assert pub._publish_task is not None

    await pub.stop()
    mock_client.loop_stop.assert_called_once()
    mock_client.disconnect.assert_called_once()
    assert pub._publish_task is None


# ---------------------------------------------------------------------------
# Batch Vibration Topic (PRD 3.3.6)
# ---------------------------------------------------------------------------


class TestWorstQuality:
    """_worst_quality selects the worst quality across a list."""

    def test_all_good(self):
        assert _worst_quality(["good", "good", "good"]) == "good"

    def test_one_uncertain(self):
        assert _worst_quality(["good", "uncertain", "good"]) == "uncertain"

    def test_one_bad(self):
        assert _worst_quality(["good", "uncertain", "bad"]) == "bad"

    def test_all_bad(self):
        assert _worst_quality(["bad", "bad", "bad"]) == "bad"


class TestMakeBatchVibrationPayload:
    """make_batch_vibration_payload produces PRD 3.3.6 compliant payloads."""

    def test_payload_is_valid_json(self):
        data = json.loads(make_batch_vibration_payload(4.2, 3.8, 5.1, "good", "mm/s", sim_time=0.0))
        assert isinstance(data, dict)

    def test_payload_has_required_fields(self):
        data = json.loads(make_batch_vibration_payload(4.2, 3.8, 5.1, "good", "mm/s", sim_time=0.0))
        assert set(data.keys()) == {"timestamp", "x", "y", "z", "unit", "quality"}

    def test_no_value_field(self):
        """Batch payload uses x/y/z, not the single 'value' field."""
        data = json.loads(make_batch_vibration_payload(4.2, 3.8, 5.1, "good", "mm/s", sim_time=0.0))
        assert "value" not in data

    def test_x_y_z_values(self):
        data = json.loads(make_batch_vibration_payload(4.2, 3.8, 5.1, "good", "mm/s", sim_time=0.0))
        assert data["x"] == pytest.approx(4.2)
        assert data["y"] == pytest.approx(3.8)
        assert data["z"] == pytest.approx(5.1)

    def test_unit_field(self):
        data = json.loads(make_batch_vibration_payload(1.0, 2.0, 3.0, "good", "mm/s", sim_time=0.0))
        assert data["unit"] == "mm/s"

    def test_quality_field(self):
        for quality in ("good", "uncertain", "bad"):
            raw = make_batch_vibration_payload(
                1.0, 2.0, 3.0, quality, "mm/s", sim_time=0.0,
            )
            data = json.loads(raw)
            assert data["quality"] == quality

    def test_timestamp_iso8601_utc(self):
        from datetime import datetime
        data = json.loads(make_batch_vibration_payload(1.0, 2.0, 3.0, "good", "mm/s", sim_time=0.0))
        ts = data["timestamp"]
        assert ts.endswith("Z")
        assert "T" in ts
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    def test_timestamp_has_milliseconds(self):
        data = json.loads(make_batch_vibration_payload(1.0, 2.0, 3.0, "good", "mm/s", sim_time=0.0))
        ts = data["timestamp"]
        parts = ts.split(".")
        assert len(parts) == 2
        ms_part = parts[1].rstrip("Z")
        assert len(ms_part) == 3

    def test_timestamp_uses_sim_time_not_wall_clock(self):
        """Batch vibration timestamp must reflect sim_time (Rule 6)."""
        raw = make_batch_vibration_payload(
            1.0, 2.0, 3.0, "good", "mm/s", sim_time=7200.0,
        )
        data = json.loads(raw)
        assert data["timestamp"] == "2026-01-01T02:00:00.000Z"


class TestBuildBatchVibrationEntry:
    """build_batch_vibration_entry creates correct BatchVibrationEntry from config."""

    def test_returns_entry_for_packaging_profile(self, config):
        entry = build_batch_vibration_entry(config)
        assert entry is not None
        assert isinstance(entry, BatchVibrationEntry)

    def test_batch_topic_path(self, config):
        entry = build_batch_vibration_entry(config)
        assert entry is not None
        assert entry.topic == "collatr/factory/demo/packaging1/vibration/main_drive"

    def test_batch_topic_no_retain(self, config):
        entry = build_batch_vibration_entry(config)
        assert entry is not None
        assert entry.retain is False

    def test_batch_topic_qos0(self, config):
        entry = build_batch_vibration_entry(config)
        assert entry is not None
        assert entry.qos == 0

    def test_batch_topic_interval_1s(self, config):
        entry = build_batch_vibration_entry(config)
        assert entry is not None
        assert entry.interval_s == pytest.approx(1.0)

    def test_batch_signal_ids(self, config):
        entry = build_batch_vibration_entry(config)
        assert entry is not None
        assert entry.x_signal_id == "vibration.main_drive_x"
        assert entry.y_signal_id == "vibration.main_drive_y"
        assert entry.z_signal_id == "vibration.main_drive_z"

    def test_batch_unit_mm_per_s(self, config):
        entry = build_batch_vibration_entry(config)
        assert entry is not None
        assert entry.unit == "mm/s"


class TestMqttPublisherBatchVibration:
    """MqttPublisher batch vibration integration: entry property and publish."""

    def _make_store_with_vib(self) -> SignalStore:
        store = SignalStore()
        store.set("vibration.main_drive_x", 4.2, timestamp=0.0, quality="good")
        store.set("vibration.main_drive_y", 3.8, timestamp=0.0, quality="good")
        store.set("vibration.main_drive_z", 5.1, timestamp=0.0, quality="good")
        return store

    def test_batch_vibration_entry_exposed(self, publisher):
        """publisher.batch_vibration_entry is not None for packaging profile."""
        assert publisher.batch_vibration_entry is not None

    def test_batch_publishes_when_all_axes_present(self, config, mock_client):
        store = self._make_store_with_vib()
        pub = MqttPublisher(config, store, client=mock_client)
        # Force batch entry to be due
        pub.batch_vibration_entry.last_published = 0.0  # type: ignore[union-attr]
        pub._publish_batch_vib(now=9999.0)  # well past interval
        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args
        topic = call_args[0][0] if call_args[0] else call_args[1].get("topic")
        assert topic == pub.batch_vibration_entry.topic  # type: ignore[union-attr]

    def test_batch_payload_has_xyz_fields(self, config, mock_client):
        store = self._make_store_with_vib()
        pub = MqttPublisher(config, store, client=mock_client)
        pub.batch_vibration_entry.last_published = 0.0  # type: ignore[union-attr]
        pub._publish_batch_vib(now=9999.0)
        call_args = mock_client.publish.call_args
        kwargs = call_args.kwargs if call_args.kwargs else {}
        payload_bytes = kwargs.get("payload")
        assert payload_bytes is not None
        data = json.loads(payload_bytes)
        assert "x" in data and "y" in data and "z" in data
        assert data["x"] == pytest.approx(4.2)
        assert data["y"] == pytest.approx(3.8)
        assert data["z"] == pytest.approx(5.1)

    def test_batch_not_published_before_interval(self, config, mock_client):
        store = self._make_store_with_vib()
        pub = MqttPublisher(config, store, client=mock_client)
        import time
        now = time.monotonic()
        pub.batch_vibration_entry.last_published = now  # type: ignore[union-attr]
        pub._publish_batch_vib(now + 0.5)  # 0.5s later, interval is 1.0s
        mock_client.publish.assert_not_called()

    def test_batch_not_published_when_axes_missing(self, config, mock_client):
        store = SignalStore()  # Empty -- no vibration signals
        pub = MqttPublisher(config, store, client=mock_client)
        pub.batch_vibration_entry.last_published = 0.0  # type: ignore[union-attr]
        pub._publish_batch_vib(now=9999.0)
        mock_client.publish.assert_not_called()

    def test_batch_no_retain(self, config, mock_client):
        store = self._make_store_with_vib()
        pub = MqttPublisher(config, store, client=mock_client)
        pub.batch_vibration_entry.last_published = 0.0  # type: ignore[union-attr]
        pub._publish_batch_vib(now=9999.0)
        call_args = mock_client.publish.call_args
        kwargs = call_args.kwargs if call_args.kwargs else {}
        assert kwargs.get("retain") is False

    def test_batch_qos0(self, config, mock_client):
        store = self._make_store_with_vib()
        pub = MqttPublisher(config, store, client=mock_client)
        pub.batch_vibration_entry.last_published = 0.0  # type: ignore[union-attr]
        pub._publish_batch_vib(now=9999.0)
        call_args = mock_client.publish.call_args
        kwargs = call_args.kwargs if call_args.kwargs else {}
        assert kwargs.get("qos") == 0

    def test_batch_worst_quality_used(self, config, mock_client):
        """Batch payload quality is worst across x/y/z axes."""
        store = SignalStore()
        store.set("vibration.main_drive_x", 4.2, timestamp=0.0, quality="good")
        store.set("vibration.main_drive_y", 3.8, timestamp=0.0, quality="bad")
        store.set("vibration.main_drive_z", 5.1, timestamp=0.0, quality="uncertain")
        pub = MqttPublisher(config, store, client=mock_client)
        pub.batch_vibration_entry.last_published = 0.0  # type: ignore[union-attr]
        pub._publish_batch_vib(now=9999.0)
        call_args = mock_client.publish.call_args
        kwargs = call_args.kwargs if call_args.kwargs else {}
        data = json.loads(kwargs.get("payload"))
        assert data["quality"] == "bad"

    def test_publish_due_triggers_batch(self, config, mock_client):
        """_publish_due also triggers batch publish when due."""
        store = self._make_store_with_vib()
        pub = MqttPublisher(config, store, client=mock_client)
        pub.batch_vibration_entry.last_published = 0.0  # type: ignore[union-attr]
        # Force all per-axis timed entries to not fire
        for entry in pub.topic_entries:
            entry.last_published = 9999.0
        pub._publish_due(now=9999.0)  # only batch should fire
        # batch publish must have fired
        assert mock_client.publish.call_count >= 1
        topics = [
            (c[0][0] if c[0] else c[1].get("topic"))
            for c in mock_client.publish.call_args_list
        ]
        batch_topic = pub.batch_vibration_entry.topic  # type: ignore[union-attr]
        assert batch_topic in topics


class TestPerAxisDisabled:
    """vibration_per_axis_enabled=False removes per-axis from topic map."""

    def test_per_axis_disabled_excludes_vibration_topics(self, config, store):
        # Patch vibration_per_axis_enabled = False
        import copy
        cfg = copy.deepcopy(config)
        cfg.protocols.mqtt.vibration_per_axis_enabled = False
        entries = build_topic_map(cfg)
        topics = [e.topic for e in entries]
        assert not any("vibration/" in t for t in topics)

    def test_per_axis_disabled_reduces_count(self, config, store):
        import copy
        cfg = copy.deepcopy(config)
        cfg.protocols.mqtt.vibration_per_axis_enabled = False
        entries = build_topic_map(cfg)
        # 16 - 3 per-axis = 13 topics
        assert len(entries) == 13

    def test_per_axis_enabled_default_16_topics(self, config, store):
        # Default: per-axis enabled → 16 per-axis topics (batch is separate)
        entries = build_topic_map(config)
        assert len(entries) == 16
