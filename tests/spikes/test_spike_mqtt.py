"""Spike: Mosquitto sidecar + paho-mqtt 2.0 validation.

Validates that paho-mqtt 2.0 can publish 50 msg/s to a Mosquitto Docker
sidecar with mixed QoS 0/1, retained messages, and LWT.

Requires Docker Compose to be running:
    docker compose up -d mqtt-broker

Tests:
  - 50 msg/s publish with mixed QoS 0/1 (500 messages over 10 seconds)
  - Retained messages arrive on new subscription
  - LWT fires on unclean disconnect
  - End-to-end latency < 50ms at 50 msg/s
  - JSON payload structure matches PRD Section 3.3.4
"""

from __future__ import annotations

import json
import socket
import time
from datetime import UTC, datetime
from threading import Event, Lock
from typing import Any

import paho.mqtt.client as mqtt
import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 1883
TOPIC_PREFIX = "factory/spike"
LWT_TOPIC = f"{TOPIC_PREFIX}/status"
LWT_PAYLOAD = json.dumps({"status": "offline"})
PUB_CLIENT_ID = "spike-publisher"
SUB_CLIENT_ID = "spike-subscriber"

# Publish parameters
PUBLISH_RATE = 50  # msg/s
PUBLISH_DURATION_S = 10
TOTAL_MESSAGES = PUBLISH_RATE * PUBLISH_DURATION_S  # 500
QOS0_TOPIC = f"{TOPIC_PREFIX}/press/line_speed"
QOS1_TOPIC = f"{TOPIC_PREFIX}/press/machine_state"


def _broker_reachable() -> bool:
    """Check if MQTT broker is reachable on BROKER_HOST:BROKER_PORT."""
    try:
        with socket.create_connection((BROKER_HOST, BROKER_PORT), timeout=2):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _broker_reachable(),
        reason=f"MQTT broker not reachable at {BROKER_HOST}:{BROKER_PORT}. "
        "Run: docker compose up -d mqtt-broker",
    ),
]


def _make_payload(value: float, unit: str = "m/min", quality: str = "good") -> str:
    """Create a JSON payload matching PRD Section 3.3.4 format."""
    return json.dumps({
        "timestamp": datetime.now(UTC).isoformat(),
        "value": value,
        "unit": unit,
        "quality": quality,
    })


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def subscriber() -> mqtt.Client:
    """Create and connect a subscriber client."""
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=SUB_CLIENT_ID,
        protocol=mqtt.MQTTv311,
    )
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()
    time.sleep(0.5)  # Wait for CONNACK from broker
    yield client
    client.loop_stop()
    client.disconnect()


@pytest.fixture
def publisher() -> mqtt.Client:
    """Create and connect a publisher client with LWT configured."""
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=PUB_CLIENT_ID,
        protocol=mqtt.MQTTv311,
    )
    client.will_set(LWT_TOPIC, payload=LWT_PAYLOAD, qos=1, retain=True)
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()
    time.sleep(0.5)  # Wait for CONNACK from broker
    yield client
    client.loop_stop()
    client.disconnect()


# ---------------------------------------------------------------------------
# Helper: collect messages
# ---------------------------------------------------------------------------
class MessageCollector:
    """Thread-safe message collector for subscriber callbacks."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._lock = Lock()
        self.target_count = 0
        self.done = Event()

    def on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        with self._lock:
            self.messages.append({
                "topic": msg.topic,
                "payload": json.loads(msg.payload.decode()),
                "qos": msg.qos,
                "retain": msg.retain,
                "received_at": time.monotonic(),
            })
            if self.target_count > 0 and len(self.messages) >= self.target_count:
                self.done.set()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self.messages)

    def get_messages(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.messages)

    def clear(self) -> None:
        with self._lock:
            self.messages.clear()
            self.done.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestMqttConnectivity:
    """Basic connectivity tests."""

    def test_publisher_connects(self, publisher: mqtt.Client) -> None:
        """Publisher connects to broker successfully."""
        assert publisher.is_connected()

    def test_subscriber_connects(self, subscriber: mqtt.Client) -> None:
        """Subscriber connects to broker successfully."""
        assert subscriber.is_connected()


class TestRetainedMessages:
    """Retained message behaviour."""

    def test_retained_message_arrives_on_new_subscription(
        self, publisher: mqtt.Client
    ) -> None:
        """A new subscriber receives the last retained message immediately."""
        retain_topic = f"{TOPIC_PREFIX}/retained_test"
        payload = _make_payload(42.7, unit="C")

        # Publish a retained message
        result = publisher.publish(retain_topic, payload, qos=1, retain=True)
        result.wait_for_publish(timeout=5)

        # Small delay for broker to process
        time.sleep(0.5)

        # Create a NEW subscriber and subscribe
        collector = MessageCollector()
        collector.target_count = 1

        sub2 = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="spike-sub-retained",
            protocol=mqtt.MQTTv311,
        )
        sub2.on_message = collector.on_message
        sub2.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
        sub2.loop_start()
        sub2.subscribe(retain_topic, qos=1)

        # Wait for the retained message
        assert collector.done.wait(timeout=5), "Retained message not received within 5s"

        msgs = collector.get_messages()
        assert len(msgs) >= 1
        assert msgs[0]["retain"] is True
        assert msgs[0]["payload"]["value"] == 42.7

        sub2.loop_stop()
        sub2.disconnect()

        # Cleanup: clear retained message
        publisher.publish(retain_topic, b"", qos=1, retain=True).wait_for_publish(
            timeout=5
        )


class TestLWT:
    """Last Will and Testament behaviour."""

    def test_lwt_fires_on_unclean_disconnect(self, subscriber: mqtt.Client) -> None:
        """LWT message is published when client disconnects uncleanly."""
        collector = MessageCollector()
        collector.target_count = 1
        subscriber.on_message = collector.on_message
        subscriber.subscribe(LWT_TOPIC, qos=1)
        time.sleep(0.5)

        # Create a publisher with LWT, then kill its socket
        lwt_pub = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="spike-lwt-publisher",
            protocol=mqtt.MQTTv311,
        )
        lwt_pub.will_set(LWT_TOPIC, payload=LWT_PAYLOAD, qos=1, retain=True)
        lwt_pub.connect(BROKER_HOST, BROKER_PORT, keepalive=1)
        lwt_pub.loop_start()
        time.sleep(0.5)

        # Force unclean disconnect by closing the socket directly
        if lwt_pub._sock is not None:
            lwt_pub._sock.close()
        lwt_pub.loop_stop()

        # Wait for LWT -- broker publishes after keepalive * 1.5
        assert collector.done.wait(timeout=10), "LWT message not received within 10s"

        msgs = collector.get_messages()
        lwt_msgs = [m for m in msgs if m["topic"] == LWT_TOPIC]
        assert len(lwt_msgs) >= 1
        assert lwt_msgs[0]["payload"]["status"] == "offline"


class TestThroughput:
    """50 msg/s throughput test with mixed QoS."""

    def test_50_msgs_per_second_mixed_qos(
        self, publisher: mqtt.Client, subscriber: mqtt.Client
    ) -> None:
        """Publish 500 messages at 50 msg/s (25 QoS0 + 25 QoS1) and verify receipt."""
        collector = MessageCollector()
        # QoS 0 may lose some messages, so we set target to QoS 1 count only
        collector.target_count = TOTAL_MESSAGES // 2  # 250 QoS 1 guaranteed
        subscriber.on_message = collector.on_message
        subscriber.subscribe(f"{TOPIC_PREFIX}/#", qos=1)
        time.sleep(0.5)

        interval = 1.0 / PUBLISH_RATE  # 20ms between messages
        qos0_count = 0
        qos1_count = 0

        start_time = time.monotonic()

        for i in range(TOTAL_MESSAGES):
            send_time = time.monotonic()
            value = float(i)
            payload = _make_payload(value)

            if i % 2 == 0:
                # QoS 0
                publisher.publish(QOS0_TOPIC, payload, qos=0, retain=False)
                qos0_count += 1
            else:
                # QoS 1
                publisher.publish(QOS1_TOPIC, payload, qos=1, retain=False)
                qos1_count += 1

            # Pace to target rate
            elapsed = time.monotonic() - send_time
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        publish_duration = time.monotonic() - start_time

        # Wait for remaining messages (give extra time for QoS 1 delivery)
        time.sleep(2.0)

        msgs = collector.get_messages()
        total_received = len(msgs)
        qos0_received = sum(1 for m in msgs if m["topic"] == QOS0_TOPIC)
        qos1_received = sum(1 for m in msgs if m["topic"] == QOS1_TOPIC)

        # Actual publish rate
        actual_rate = TOTAL_MESSAGES / publish_duration

        print("\n--- Throughput Results ---")
        print(f"Published: {TOTAL_MESSAGES} messages in {publish_duration:.2f}s")
        print(f"Actual rate: {actual_rate:.1f} msg/s")
        print(f"Sent: QoS0={qos0_count}, QoS1={qos1_count}")
        print(f"Received: total={total_received}, QoS0={qos0_received}, QoS1={qos1_received}")

        # All QoS 1 messages must arrive
        assert qos1_received == qos1_count, (
            f"QoS 1 loss: sent {qos1_count}, received {qos1_received}"
        )
        # QoS 0 >= 99% (allow 1% loss)
        min_qos0 = int(qos0_count * 0.99)
        assert qos0_received >= min_qos0, (
            f"QoS 0 excessive loss: sent {qos0_count}, received {qos0_received}, "
            f"min expected {min_qos0}"
        )
        # Rate should be close to target
        assert actual_rate >= 40, f"Rate too slow: {actual_rate:.1f} msg/s < 40"

    def test_end_to_end_latency(
        self, publisher: mqtt.Client, subscriber: mqtt.Client
    ) -> None:
        """End-to-end latency should be < 50ms at 50 msg/s."""
        latency_topic = f"{TOPIC_PREFIX}/latency_test"
        collector = MessageCollector()
        collector.target_count = 100
        subscriber.on_message = collector.on_message
        subscriber.subscribe(latency_topic, qos=1)
        time.sleep(0.5)

        # Publish 100 messages at 50 msg/s with monotonic send timestamps
        send_times: dict[int, float] = {}
        for i in range(100):
            send_times[i] = time.monotonic()
            payload = json.dumps({
                "timestamp": datetime.now(UTC).isoformat(),
                "value": float(i),
                "unit": "test",
                "quality": "good",
                "seq": i,
            })
            publisher.publish(latency_topic, payload, qos=1, retain=False)
            time.sleep(0.02)  # 50 msg/s

        # Wait for all messages
        assert collector.done.wait(timeout=10), "Not all latency test messages received"

        msgs = collector.get_messages()
        latencies = []
        for msg in msgs:
            seq = msg["payload"].get("seq")
            if seq is not None and seq in send_times:
                latency_ms = (msg["received_at"] - send_times[seq]) * 1000
                latencies.append(latency_ms)

        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            max_latency = max(latencies)
            p95_latency = sorted(latencies)[int(len(latencies) * 0.95)]
            print("\n--- Latency Results ---")
            print(f"Samples: {len(latencies)}")
            print(f"Avg: {avg_latency:.2f}ms")
            print(f"P95: {p95_latency:.2f}ms")
            print(f"Max: {max_latency:.2f}ms")

            assert avg_latency < 50, f"Average latency {avg_latency:.2f}ms exceeds 50ms"


class TestPayloadFormat:
    """JSON payload format validation per PRD Section 3.3.4."""

    def test_payload_structure(self, publisher: mqtt.Client) -> None:
        """Payload matches PRD format: timestamp, value, unit, quality."""
        payload_str = _make_payload(42.7, unit="C", quality="good")
        payload = json.loads(payload_str)

        assert "timestamp" in payload
        assert "value" in payload
        assert "unit" in payload
        assert "quality" in payload

        assert isinstance(payload["value"], float)
        assert payload["unit"] == "C"
        assert payload["quality"] in ("good", "uncertain", "bad")

        # Verify timestamp is valid ISO 8601
        datetime.fromisoformat(payload["timestamp"])

    def test_payload_round_trip(
        self, publisher: mqtt.Client, subscriber: mqtt.Client
    ) -> None:
        """Payload survives publish/subscribe round-trip intact."""
        rt_topic = f"{TOPIC_PREFIX}/roundtrip"
        collector = MessageCollector()
        collector.target_count = 1
        subscriber.on_message = collector.on_message
        subscriber.subscribe(rt_topic, qos=1)
        time.sleep(0.5)

        original_value = 123.456
        payload = _make_payload(original_value, unit="m/min", quality="good")
        publisher.publish(rt_topic, payload, qos=1, retain=False)

        assert collector.done.wait(timeout=5), "Round-trip message not received"
        msg = collector.get_messages()[0]
        assert msg["payload"]["value"] == original_value
        assert msg["payload"]["unit"] == "m/min"
        assert msg["payload"]["quality"] == "good"
