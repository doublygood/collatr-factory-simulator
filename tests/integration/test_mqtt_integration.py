"""Integration tests for the MQTT publisher adapter.

Starts the DataEngine + MqttPublisher, connects to a real Mosquitto broker
(via Docker Compose), and verifies all 17 packaging MQTT topics are published
with correct JSON payloads, QoS levels, and retain flags.

Requires Docker Compose to be running:
    docker compose up -d mqtt-broker

PRD Reference: Section 3.3, Appendix C (MQTT Topic Map), Section 13.2
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path
from threading import Lock
from typing import Any

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.enums import CallbackAPIVersion

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.protocols.mqtt_publisher import MqttPublisher
from factory_simulator.store import SignalStore

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory.yaml"
_BROKER_HOST = "127.0.0.1"
_BROKER_PORT = 1883
_TOPIC_PREFIX = "collatr/factory/demo/packaging1"


def _broker_reachable() -> bool:
    """Check if MQTT broker is reachable on _BROKER_HOST:_BROKER_PORT."""
    try:
        with socket.create_connection((_BROKER_HOST, _BROKER_PORT), timeout=2):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _broker_reachable(),
        reason=f"MQTT broker not reachable at {_BROKER_HOST}:{_BROKER_PORT}. "
        "Run: docker compose up -d mqtt-broker",
    ),
]


# ---------------------------------------------------------------------------
# Message collector (thread-safe for paho callbacks)
# ---------------------------------------------------------------------------


class MessageCollector:
    """Thread-safe message collector for paho subscriber callbacks.

    NOTE: Lock is required because paho-mqtt's loop_start() runs callbacks
    on a background thread.  CLAUDE.md Rule 9 (no locks) applies to the
    asyncio signal engine, not to threaded paho-mqtt callbacks.
    """

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._lock = Lock()

    def on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        with self._lock:
            payload = json.loads(msg.payload.decode()) if msg.payload else {}
            self.messages.append({
                "topic": msg.topic,
                "payload": payload,
                "qos": msg.qos,
                "retain": msg.retain,
            })

    @property
    def count(self) -> int:
        with self._lock:
            return len(self.messages)

    def get_messages(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.messages)

    def topics_received(self) -> set[str]:
        with self._lock:
            return {m["topic"] for m in self.messages}


async def _wait_for_topics(
    collector: MessageCollector,
    expected: set[str],
    timeout: float = 10.0,
) -> bool:
    """Poll until all expected topics are seen without blocking the event loop."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if expected <= collector.topics_received():
            return True
        await asyncio.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# Expected topics per PRD Appendix C (packaging profile)
# ---------------------------------------------------------------------------

# All 16 per-signal topics
EXPECTED_PER_SIGNAL_TOPICS: set[str] = {
    f"{_TOPIC_PREFIX}/coder/state",
    f"{_TOPIC_PREFIX}/coder/prints_total",
    f"{_TOPIC_PREFIX}/coder/ink_level",
    f"{_TOPIC_PREFIX}/coder/printhead_temp",
    f"{_TOPIC_PREFIX}/coder/ink_pump_speed",
    f"{_TOPIC_PREFIX}/coder/ink_pressure",
    f"{_TOPIC_PREFIX}/coder/ink_viscosity_actual",
    f"{_TOPIC_PREFIX}/coder/supply_voltage",
    f"{_TOPIC_PREFIX}/coder/ink_consumption_ml",
    f"{_TOPIC_PREFIX}/coder/nozzle_health",
    f"{_TOPIC_PREFIX}/coder/gutter_fault",
    f"{_TOPIC_PREFIX}/env/ambient_temp",
    f"{_TOPIC_PREFIX}/env/ambient_humidity",
    f"{_TOPIC_PREFIX}/vibration/main_drive_x",
    f"{_TOPIC_PREFIX}/vibration/main_drive_y",
    f"{_TOPIC_PREFIX}/vibration/main_drive_z",
}

BATCH_VIBRATION_TOPIC = f"{_TOPIC_PREFIX}/vibration/main_drive"

ALL_EXPECTED_TOPICS = EXPECTED_PER_SIGNAL_TOPICS | {BATCH_VIBRATION_TOPIC}

# Topics that should use QoS 1 (PRD 3.3.5)
QOS1_TOPICS: set[str] = {
    f"{_TOPIC_PREFIX}/coder/state",
    f"{_TOPIC_PREFIX}/coder/prints_total",
    f"{_TOPIC_PREFIX}/coder/nozzle_health",
    f"{_TOPIC_PREFIX}/coder/gutter_fault",
}

# Topics that should NOT be retained (PRD 3.3.8: vibration)
NO_RETAIN_TOPICS: set[str] = {
    f"{_TOPIC_PREFIX}/vibration/main_drive_x",
    f"{_TOPIC_PREFIX}/vibration/main_drive_y",
    f"{_TOPIC_PREFIX}/vibration/main_drive_z",
    BATCH_VIBRATION_TOPIC,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def mqtt_components() -> (  # type: ignore[override]
    tuple[MqttPublisher, DataEngine, SignalStore]
):
    """Create engine + MQTT publisher (NOT started).  Clean up on teardown.

    Pre-populates the SignalStore with known values for all MQTT-published
    signals.  The publisher is intentionally NOT started so tests can
    subscribe *before* the first publish cycle.
    """
    config = load_config(_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = 42
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = 1.0

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)

    # Tick engine to populate all signal IDs in the store
    for _ in range(5):
        engine.tick()

    # Inject known test values for all MQTT-published signals
    t = clock.sim_time
    # Coder signals
    store.set("coder.state", 2.0, t)
    store.set("coder.prints_total", 5000.0, t)
    store.set("coder.ink_level", 85.0, t)
    store.set("coder.printhead_temp", 42.0, t)
    store.set("coder.ink_pump_speed", 1500.0, t)
    store.set("coder.ink_pressure", 2.8, t)
    store.set("coder.ink_viscosity_actual", 28.0, t)
    store.set("coder.supply_voltage", 230.5, t)
    store.set("coder.ink_consumption_ml", 150.0, t)
    store.set("coder.nozzle_health", 95.0, t)
    store.set("coder.gutter_fault", 0.0, t)
    # Environment signals
    store.set("environment.ambient_temp", 22.5, t)
    store.set("environment.ambient_humidity", 45.0, t)
    # Vibration signals
    store.set("vibration.main_drive_x", 4.2, t)
    store.set("vibration.main_drive_y", 3.8, t)
    store.set("vibration.main_drive_z", 5.1, t)

    publisher = MqttPublisher(config, store, host=_BROKER_HOST, port=_BROKER_PORT)

    yield publisher, engine, store

    # Stop publisher if it was started during the test
    if publisher._publish_task is not None:
        await publisher.stop()


def _make_subscriber(suffix: str = "") -> mqtt.Client:
    """Create and connect a paho subscriber to Docker Mosquitto."""
    cid = f"test-sub-{int(time.monotonic() * 1000) % 100000}{suffix}"
    client = mqtt.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=cid,
        protocol=mqtt.MQTTv311,
    )
    client.connect(_BROKER_HOST, _BROKER_PORT, keepalive=60)
    client.loop_start()
    time.sleep(0.5)  # Wait for CONNACK
    return client


# ---------------------------------------------------------------------------
# Tests: all topics publish
# ---------------------------------------------------------------------------


class TestAllTopicsPublish:
    """All 17 packaging MQTT topics (16 per-signal + 1 batch) publish."""

    async def test_all_17_topics_received(
        self,
        mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """All expected topics receive at least one message within 10s."""
        publisher, _, _ = mqtt_components

        collector = MessageCollector()
        sub = _make_subscriber()
        try:
            sub.on_message = collector.on_message
            sub.subscribe(f"{_TOPIC_PREFIX}/#", qos=1)
            time.sleep(0.5)

            await publisher.start()
            found = await _wait_for_topics(collector, ALL_EXPECTED_TOPICS, timeout=10.0)

            received = collector.topics_received()
            missing = ALL_EXPECTED_TOPICS - received
            assert found and not missing, (
                f"Missing topics after 10s: {sorted(missing)}\n"
                f"Received {len(received)}: {sorted(received)}"
            )
        finally:
            sub.loop_stop()
            sub.disconnect()


# ---------------------------------------------------------------------------
# Tests: JSON payload structure (PRD 3.3.4)
# ---------------------------------------------------------------------------


class TestPayloadStructure:
    """JSON payloads match PRD Section 3.3.4 schema."""

    async def test_per_signal_payload_has_required_fields(
        self,
        mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """Per-signal payloads have: timestamp, value, unit, quality."""
        publisher, _, _ = mqtt_components

        collector = MessageCollector()
        sub = _make_subscriber()
        try:
            sub.on_message = collector.on_message
            sub.subscribe(f"{_TOPIC_PREFIX}/#", qos=1)
            time.sleep(0.5)

            await publisher.start()
            await _wait_for_topics(
                collector, EXPECTED_PER_SIGNAL_TOPICS, timeout=10.0,
            )

            msgs = collector.get_messages()
            errors: list[str] = []
            for msg in msgs:
                topic = msg["topic"]
                if topic == BATCH_VIBRATION_TOPIC:
                    continue  # Batch has different schema
                payload = msg["payload"]
                required = {"timestamp", "value", "unit", "quality"}
                missing_fields = required - set(payload.keys())
                if missing_fields:
                    errors.append(f"{topic}: missing fields {missing_fields}")
            assert not errors, "Payload field errors:\n" + "\n".join(errors)
        finally:
            sub.loop_stop()
            sub.disconnect()

    async def test_batch_vibration_payload_has_xyz_fields(
        self,
        mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """Batch vibration payload has: timestamp, x, y, z, unit, quality."""
        publisher, _, _ = mqtt_components

        collector = MessageCollector()
        sub = _make_subscriber()
        try:
            sub.on_message = collector.on_message
            sub.subscribe(BATCH_VIBRATION_TOPIC, qos=1)
            time.sleep(0.5)

            await publisher.start()
            await _wait_for_topics(
                collector, {BATCH_VIBRATION_TOPIC}, timeout=10.0,
            )

            msgs = collector.get_messages()
            batch_msgs = [m for m in msgs if m["topic"] == BATCH_VIBRATION_TOPIC]
            assert batch_msgs, "No batch vibration messages received"

            payload = batch_msgs[0]["payload"]
            required = {"timestamp", "x", "y", "z", "unit", "quality"}
            missing_fields = required - set(payload.keys())
            assert not missing_fields, (
                f"Batch payload missing fields: {missing_fields}"
            )
            assert "value" not in payload, (
                "Batch payload should not have 'value' field"
            )
        finally:
            sub.loop_stop()
            sub.disconnect()

    async def test_value_is_numeric_not_string(
        self,
        mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """Signal values are JSON numbers, not strings."""
        publisher, _, _ = mqtt_components

        target = f"{_TOPIC_PREFIX}/coder/ink_level"
        collector = MessageCollector()
        sub = _make_subscriber()
        try:
            sub.on_message = collector.on_message
            sub.subscribe(target, qos=1)
            time.sleep(0.5)

            await publisher.start()
            await _wait_for_topics(collector, {target}, timeout=10.0)

            msgs = collector.get_messages()
            assert msgs, "No ink_level messages received"
            value = msgs[0]["payload"]["value"]
            assert isinstance(value, int | float), (
                f"value should be numeric, got {type(value).__name__}: {value!r}"
            )
        finally:
            sub.loop_stop()
            sub.disconnect()

    async def test_timestamp_is_iso8601_utc(
        self,
        mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """Timestamps are ISO 8601 with milliseconds, ending in Z (UTC)."""
        from datetime import datetime

        publisher, _, _ = mqtt_components

        target = f"{_TOPIC_PREFIX}/coder/ink_level"
        collector = MessageCollector()
        sub = _make_subscriber()
        try:
            sub.on_message = collector.on_message
            sub.subscribe(target, qos=1)
            time.sleep(0.5)

            await publisher.start()
            await _wait_for_topics(collector, {target}, timeout=10.0)

            msgs = collector.get_messages()
            assert msgs
            ts = msgs[0]["payload"]["timestamp"]
            assert ts.endswith("Z"), f"Timestamp should end with Z: {ts!r}"
            assert "T" in ts, f"Timestamp should contain T: {ts!r}"
            # 3-digit milliseconds
            parts = ts.split(".")
            assert len(parts) == 2, f"Timestamp should have ms: {ts!r}"
            ms_part = parts[1].rstrip("Z")
            assert len(ms_part) == 3, f"Expected 3-digit ms, got {ms_part!r}"
            # Parseable
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
        finally:
            sub.loop_stop()
            sub.disconnect()


# ---------------------------------------------------------------------------
# Tests: QoS levels (PRD 3.3.5)
# ---------------------------------------------------------------------------


class TestQosLevels:
    """QoS levels match PRD Section 3.3.5 per topic."""

    async def test_qos1_for_critical_coder_topics(
        self,
        mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """State, prints_total, nozzle_health, gutter_fault use QoS 1."""
        publisher, _, _ = mqtt_components

        collector = MessageCollector()
        sub = _make_subscriber()
        try:
            sub.on_message = collector.on_message
            sub.subscribe(f"{_TOPIC_PREFIX}/coder/#", qos=1)
            time.sleep(0.5)

            await publisher.start()
            await _wait_for_topics(collector, QOS1_TOPICS, timeout=10.0)

            msgs = collector.get_messages()
            errors: list[str] = []
            for topic in QOS1_TOPICS:
                topic_msgs = [m for m in msgs if m["topic"] == topic]
                if not topic_msgs:
                    errors.append(f"{topic}: no messages received")
                elif topic_msgs[0]["qos"] != 1:
                    errors.append(
                        f"{topic}: expected QoS 1, got QoS {topic_msgs[0]['qos']}"
                    )
            assert not errors, "QoS 1 verification:\n" + "\n".join(errors)
        finally:
            sub.loop_stop()
            sub.disconnect()

    async def test_qos0_for_analog_env_vibration(
        self,
        mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """Non-critical analog, env, and vibration topics use QoS 0."""
        publisher, _, _ = mqtt_components

        qos0_check_topics = {
            f"{_TOPIC_PREFIX}/coder/ink_level",
            f"{_TOPIC_PREFIX}/env/ambient_temp",
            f"{_TOPIC_PREFIX}/vibration/main_drive_x",
        }

        collector = MessageCollector()
        sub = _make_subscriber()
        try:
            sub.on_message = collector.on_message
            # Subscribe at QoS 1 so QoS 0 publishes are still delivered at QoS 0
            sub.subscribe(f"{_TOPIC_PREFIX}/#", qos=1)
            time.sleep(0.5)

            await publisher.start()
            await _wait_for_topics(collector, qos0_check_topics, timeout=10.0)

            msgs = collector.get_messages()
            errors: list[str] = []
            for topic in qos0_check_topics:
                # Find the first non-retained message for this topic (retained
                # messages from prior test runs may carry a different QoS)
                topic_msgs = [
                    m for m in msgs
                    if m["topic"] == topic and not m["retain"]
                ]
                if not topic_msgs:
                    # Fall back to any message for this topic
                    topic_msgs = [m for m in msgs if m["topic"] == topic]
                if not topic_msgs:
                    errors.append(f"{topic}: no messages received")
                elif topic_msgs[0]["qos"] != 0:
                    errors.append(
                        f"{topic}: expected QoS 0, got QoS {topic_msgs[0]['qos']}"
                    )
            assert not errors, "QoS 0 verification:\n" + "\n".join(errors)
        finally:
            sub.loop_stop()
            sub.disconnect()


# ---------------------------------------------------------------------------
# Tests: retained messages (PRD 3.3.8)
# ---------------------------------------------------------------------------


class TestRetainBehavior:
    """Retained message behaviour per PRD Section 3.3.8."""

    async def test_retained_message_arrives_on_new_subscription(
        self,
        mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """A new subscriber to a retained topic receives the last value."""
        publisher, _, _ = mqtt_components

        retain_topic = f"{_TOPIC_PREFIX}/coder/ink_level"

        # Start publisher and let it publish retained messages
        await publisher.start()
        await asyncio.sleep(3.0)

        # Now create a NEW subscriber and subscribe to the retained topic
        collector = MessageCollector()
        sub = _make_subscriber("-retained")
        try:
            sub.on_message = collector.on_message
            sub.subscribe(retain_topic, qos=1)

            found = await _wait_for_topics(
                collector, {retain_topic}, timeout=5.0,
            )
            assert found, "Retained message not received on new subscription"

            msgs = collector.get_messages()
            retain_msgs = [m for m in msgs if m["topic"] == retain_topic]
            assert retain_msgs, "No messages for ink_level"
            # The first message should be the retained one
            assert retain_msgs[0]["retain"] is True, (
                "First message on new subscription should be retained "
                "(msg.retain=True)"
            )
        finally:
            sub.loop_stop()
            sub.disconnect()

    async def test_vibration_not_retained_for_new_subscriber(
        self,
        mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """Vibration topics (per-axis and batch) are NOT retained."""
        publisher, _, _ = mqtt_components

        # Clear any stale retained messages from prior runs
        cleaner = _make_subscriber("-cleaner")
        for topic in NO_RETAIN_TOPICS:
            cleaner.publish(topic, b"", qos=0, retain=True)
        time.sleep(0.5)
        cleaner.loop_stop()
        cleaner.disconnect()

        # Start publisher and let it publish vibration (retain=False)
        await publisher.start()
        await asyncio.sleep(3.0)

        # Create a new subscriber for vibration topics
        collector = MessageCollector()
        sub = _make_subscriber("-vib-retain")
        try:
            sub.on_message = collector.on_message
            for topic in NO_RETAIN_TOPICS:
                sub.subscribe(topic, qos=1)

            # Wait briefly — retained messages would arrive immediately
            await asyncio.sleep(2.0)

            msgs = collector.get_messages()
            retained_msgs = [m for m in msgs if m["retain"] is True]
            assert not retained_msgs, (
                f"Vibration topics should not have retained messages. "
                f"Got retained: {[m['topic'] for m in retained_msgs]}"
            )
        finally:
            sub.loop_stop()
            sub.disconnect()


# ---------------------------------------------------------------------------
# Tests: approximate publish rate
# ---------------------------------------------------------------------------


class TestPublishRate:
    """Publish rates are approximately correct for key signal groups."""

    async def test_vibration_publishes_approximately_every_1s(
        self,
        mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """Vibration x-axis publishes ~3+ times in ~4 seconds (1s interval)."""
        publisher, _, _ = mqtt_components

        vib_topic = f"{_TOPIC_PREFIX}/vibration/main_drive_x"
        collector = MessageCollector()
        sub = _make_subscriber()
        try:
            sub.on_message = collector.on_message
            sub.subscribe(vib_topic, qos=1)
            time.sleep(0.5)

            await publisher.start()
            # Wait ~5s to collect multiple publish cycles (extra second for startup jitter)
            await asyncio.sleep(5.0)

            msgs = [m for m in collector.get_messages() if m["topic"] == vib_topic]
            # 5s with 1s interval → expect ≥3 messages (tolerates ~1s of startup delay)
            assert len(msgs) >= 3, (
                f"Expected ≥3 vibration messages in 5s (1s interval), "
                f"got {len(msgs)}"
            )
        finally:
            sub.loop_stop()
            sub.disconnect()
