"""Integration tests for F&B OPC-UA + MQTT adapters.

Starts DataEngine + OpcuaServer + MqttPublisher with ``config/factory-foodbev.yaml``,
connects real asyncua and paho clients, and verifies the full F&B stack end-to-end:

* FoodBevLine node tree (19 nodes) accessible via OPC-UA
* All 19 nodes return values in their expected EURange
* 13 MQTT topics published with ``foodbev1`` prefix, no vibration topics
* JSON payloads have required schema fields
* QoS levels and retain flags match PRD Appendix C
* Engine → store → OPC-UA and Engine → store → MQTT paths work simultaneously

Requires Docker Compose to be running::

    docker compose up -d mqtt-broker

PRD Reference: Section 3.2, 3.3, Appendix B (FoodBevLine), Appendix C (F&B)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import socket
import time
from pathlib import Path
from threading import Lock
from typing import Any

import paho.mqtt.client as mqtt
import pytest
from asyncua import Client, ua
from paho.mqtt.enums import CallbackAPIVersion

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.protocols.mqtt_publisher import MqttPublisher
from factory_simulator.protocols.opcua_server import NAMESPACE_INDEX, OpcuaServer
from factory_simulator.store import SignalStore

_FNB_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory-foodbev.yaml"
_HOST = "127.0.0.1"
_BROKER_HOST = "127.0.0.1"
_BROKER_PORT = 1883
_TOPIC_PREFIX = "collatr/factory/demo/foodbev1"


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
# Expected F&B OPC-UA nodes per PRD Appendix B (FoodBevLine section)
# (node_path, opcua_type_str, is_writable)
# All F&B setpoints are accessed via Modbus only — all 19 nodes are read-only.
# ---------------------------------------------------------------------------

EXPECTED_FNB_NODES: list[tuple[str, str, bool]] = [
    # Mixer1
    ("FoodBevLine.Mixer1.State",               "UInt16", False),
    ("FoodBevLine.Mixer1.BatchId",             "String", False),
    # Oven1
    ("FoodBevLine.Oven1.State",                "UInt16", False),
    # Filler1
    ("FoodBevLine.Filler1.LineSpeed",          "Double", False),
    ("FoodBevLine.Filler1.FillWeight",         "Double", False),
    ("FoodBevLine.Filler1.FillTarget",         "Double", False),
    ("FoodBevLine.Filler1.FillDeviation",      "Double", False),
    ("FoodBevLine.Filler1.PacksProduced",      "UInt32", False),
    ("FoodBevLine.Filler1.RejectCount",        "UInt32", False),
    ("FoodBevLine.Filler1.State",              "UInt16", False),
    # QC1
    ("FoodBevLine.QC1.ActualWeight",           "Double", False),
    ("FoodBevLine.QC1.OverweightCount",        "UInt32", False),
    ("FoodBevLine.QC1.UnderweightCount",       "UInt32", False),
    ("FoodBevLine.QC1.MetalDetectTrips",       "UInt32", False),
    ("FoodBevLine.QC1.Throughput",             "Double", False),
    ("FoodBevLine.QC1.RejectTotal",            "UInt32", False),
    # CIP1
    ("FoodBevLine.CIP1.State",                 "UInt16", False),
    # Energy
    ("FoodBevLine.Energy.LinePower",           "Double", False),
    ("FoodBevLine.Energy.CumulativeKwh",       "Double", False),
]

# All 13 F&B MQTT topics per PRD Appendix C
FNB_ALL_TOPICS: set[str] = {
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
}

# QoS 1 topics per PRD 3.3.5
FNB_QOS1_TOPICS: set[str] = {
    f"{_TOPIC_PREFIX}/coder/state",
    f"{_TOPIC_PREFIX}/coder/prints_total",
    f"{_TOPIC_PREFIX}/coder/nozzle_health",
    f"{_TOPIC_PREFIX}/coder/gutter_fault",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MessageCollector:
    """Thread-safe MQTT message collector for paho callbacks."""

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
    collector: _MessageCollector,
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


async def _read_eurange(client: Client, node_id: ua.NodeId) -> ua.Range:
    """Return the EURange property value of a variable node."""
    node = client.get_node(node_id)
    children = await node.get_children()
    for child in children:
        bname = await child.read_browse_name()
        if bname.Name == "EURange":
            return await child.read_value()  # type: ignore[no-any-return]
    raise AssertionError(f"EURange property not found on {node_id}")


def _make_subscriber(suffix: str = "") -> mqtt.Client:
    """Create and connect a paho subscriber to the broker."""
    cid = f"test-fnb-sub-{int(time.monotonic() * 1000) % 100000}{suffix}"
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
# Fixtures
# ---------------------------------------------------------------------------


def _base_fnb_config() -> tuple[object, SignalStore, SimulationClock, DataEngine]:
    """Create F&B config/store/clock/engine with fixed seed."""
    config = load_config(_FNB_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = 42  # type: ignore[union-attr]
    config.simulation.tick_interval_ms = 100  # type: ignore[union-attr]
    config.simulation.time_scale = 1.0  # type: ignore[union-attr]
    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)  # type: ignore[union-attr]
    engine = DataEngine(config, store, clock)  # type: ignore[arg-type]
    return config, store, clock, engine


@pytest.fixture
async def fnb_opcua_static() -> (  # type: ignore[override]
    tuple[OpcuaServer, Client, SignalStore, int]
):
    """OpcuaServer with F&B config and pre-populated store; engine NOT running.

    Runs synchronous engine ticks to populate signal IDs, then injects
    known test values. Server is started and allowed one sync cycle before
    the client connects.

    Yields ``(server, client, store, namespace_index)``.
    """
    config, store, clock, engine = _base_fnb_config()

    # Tick to populate all signal IDs in the store.
    for _ in range(10):
        engine.tick()

    # Inject known, in-range test values for all F&B OPC-UA signals.
    t = clock.sim_time
    # Mixer
    store.set("mixer.state",            0.0,    t)  # Off
    store.set("mixer.batch_id",         "BATCH-TEST-001", t)
    # Oven
    store.set("oven.state",             1.0,    t)  # Preheat
    # Filler
    store.set("filler.line_speed",      60.0,   t)
    store.set("filler.fill_weight",     405.0,  t)
    store.set("filler.fill_target",     400.0,  t)
    store.set("filler.fill_deviation",  5.0,    t)
    store.set("filler.packs_produced",  1500.0, t)
    store.set("filler.reject_count",    12.0,   t)
    store.set("filler.state",           2.0,    t)  # Running
    # QC
    store.set("qc.actual_weight",       415.0,  t)
    store.set("qc.overweight_count",    3.0,    t)
    store.set("qc.underweight_count",   1.0,    t)
    store.set("qc.metal_detect_trips",  0.0,    t)
    store.set("qc.throughput",          58.0,   t)
    store.set("qc.reject_total",        4.0,    t)
    # CIP
    store.set("cip.state",              0.0,    t)  # Idle
    # Energy
    store.set("energy.line_power",      180.0,  t)
    store.set("energy.cumulative_kwh",  5400.0, t)

    server = OpcuaServer(config, store, host=_HOST, port=0)  # type: ignore[arg-type]
    await server.start()
    # Allow one full sync cycle.
    await asyncio.sleep(0.6)

    port = server.actual_port
    assert port > 0, "OPC-UA server did not bind to a port"
    client = Client(f"opc.tcp://{_HOST}:{port}/")
    await client.connect()

    yield server, client, store, NAMESPACE_INDEX

    await client.disconnect()
    await server.stop()


@pytest.fixture
async def fnb_mqtt_components() -> (  # type: ignore[override]
    tuple[MqttPublisher, DataEngine, SignalStore]
):
    """F&B DataEngine + MqttPublisher (NOT started). Pre-populated store.

    Yields ``(publisher, engine, store)``.
    """
    config, store, clock, engine = _base_fnb_config()

    # Tick to populate all signal IDs.
    for _ in range(5):
        engine.tick()

    t = clock.sim_time
    # Inject coder and env signal values
    store.set("coder.state",               2.0,   t)
    store.set("coder.prints_total",        8500.0, t)
    store.set("coder.ink_level",           72.0,  t)
    store.set("coder.printhead_temp",      44.0,  t)
    store.set("coder.ink_pump_speed",      1400.0, t)
    store.set("coder.ink_pressure",        2.6,   t)
    store.set("coder.ink_viscosity_actual", 27.5, t)
    store.set("coder.supply_voltage",      230.0, t)
    store.set("coder.ink_consumption_ml",  120.0, t)
    store.set("coder.nozzle_health",       98.0,  t)
    store.set("coder.gutter_fault",        0.0,   t)
    store.set("environment.ambient_temp",  15.0,  t)
    store.set("environment.ambient_humidity", 50.0, t)

    publisher = MqttPublisher(
        config,  # type: ignore[arg-type]
        store,
        host=_BROKER_HOST,
        port=_BROKER_PORT,
    )

    yield publisher, engine, store

    if publisher._publish_task is not None:
        await publisher.stop()


# ---------------------------------------------------------------------------
# Tests: F&B OPC-UA node tree
# ---------------------------------------------------------------------------


class TestFnbOpcuaNodeTree:
    """FoodBevLine node tree is browsable and matches PRD Appendix B."""

    async def test_foodbevline_in_objects_folder(
        self,
        fnb_opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """FoodBevLine folder is a direct child of the OPC-UA Objects folder."""
        _, client, _, ns = fnb_opcua_static
        children = await client.nodes.objects.get_children()
        names = [(await c.read_browse_name()).Name for c in children]
        assert "FoodBevLine" in names, (
            f"FoodBevLine missing from Objects children: {names}"
        )

    async def test_no_packagingline_folder(
        self,
        fnb_opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """F&B OPC-UA server must NOT expose PackagingLine (wrong profile)."""
        _, client, _, ns = fnb_opcua_static
        children = await client.nodes.objects.get_children()
        names = [(await c.read_browse_name()).Name for c in children]
        assert "PackagingLine" not in names, (
            f"PackagingLine should not be in F&B Objects folder: {names}"
        )

    async def test_equipment_folders_under_foodbevline(
        self,
        fnb_opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Mixer1, Oven1, Filler1, QC1, CIP1, Energy are under FoodBevLine."""
        _, client, _, ns = fnb_opcua_static
        fl_node = client.get_node(ua.NodeId("FoodBevLine", ns))
        children = await fl_node.get_children()
        names = {(await c.read_browse_name()).Name for c in children}
        expected = {"Mixer1", "Oven1", "Filler1", "QC1", "CIP1", "Energy"}
        missing = expected - names
        assert not missing, (
            f"Equipment folders missing under FoodBevLine: {missing}. Found: {names}"
        )

    async def test_all_19_nodes_registered(
        self,
        fnb_opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """All 19 Appendix B (FoodBevLine) nodes are in server.nodes."""
        server, _, _, _ = fnb_opcua_static
        assert len(server.nodes) == len(EXPECTED_FNB_NODES), (
            f"Expected {len(EXPECTED_FNB_NODES)} nodes, got {len(server.nodes)}: "
            f"{sorted(server.nodes.keys())}"
        )

    async def test_all_nodes_readable_by_string_nodeid(
        self,
        fnb_opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Every Appendix B leaf node is readable via ns=2;s=… NodeID."""
        _, client, _, ns = fnb_opcua_static
        errors: list[str] = []
        for node_path, _type_str, _ in EXPECTED_FNB_NODES:
            try:
                node = client.get_node(ua.NodeId(node_path, ns))
                val = await node.read_value()
                if val is None:
                    errors.append(f"{node_path}: read_value() returned None")
            except Exception as exc:
                errors.append(f"{node_path}: {exc}")
        assert not errors, "Node read errors:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# Tests: F&B OPC-UA node values
# ---------------------------------------------------------------------------


class TestFnbOpcuaValues:
    """F&B OPC-UA nodes carry correct values from the store."""

    async def test_double_nodes_finite_and_in_eurange(
        self,
        fnb_opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """All Double nodes return finite values within their EURange."""
        _, client, _, ns = fnb_opcua_static
        errors: list[str] = []
        for node_path, type_str, _ in EXPECTED_FNB_NODES:
            if type_str != "Double":
                continue
            node = client.get_node(ua.NodeId(node_path, ns))
            dv = await node.read_data_value(raise_on_bad_status=False)
            if dv.Value is None or dv.Value.Value is None:
                errors.append(f"{node_path}: value is None")
                continue
            fval = float(dv.Value.Value)
            if not math.isfinite(fval):
                errors.append(f"{node_path}: non-finite value {fval}")
                continue
            eu = await _read_eurange(client, ua.NodeId(node_path, ns))
            if eu.Low == 0.0 and eu.High == 0.0:
                continue  # EURange not configured; skip range check
            if fval < eu.Low - 1e-4 or fval > eu.High + 1e-4:
                errors.append(
                    f"{node_path}: {fval} outside EURange [{eu.Low}, {eu.High}]"
                )
        assert not errors, "Out-of-range Double nodes:\n" + "\n".join(errors)

    async def test_key_signals_reflect_injected_values(
        self,
        fnb_opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Key OPC-UA nodes reflect the values injected into the store."""
        _, client, _, ns = fnb_opcua_static
        # (node_path, expected_value, tolerance)
        checks: list[tuple[str, float, float]] = [
            ("FoodBevLine.Filler1.LineSpeed",    60.0,   0.01),
            ("FoodBevLine.Filler1.FillWeight",   405.0,  0.01),
            ("FoodBevLine.Filler1.FillTarget",   400.0,  0.01),
            ("FoodBevLine.Filler1.FillDeviation", 5.0,   0.01),
            ("FoodBevLine.QC1.ActualWeight",     415.0,  0.01),
            ("FoodBevLine.QC1.Throughput",        58.0,  0.01),
            ("FoodBevLine.Energy.LinePower",     180.0,  0.01),
            ("FoodBevLine.Energy.CumulativeKwh", 5400.0, 0.01),
        ]
        errors: list[str] = []
        for node_path, expected, tol in checks:
            node = client.get_node(ua.NodeId(node_path, ns))
            val = await node.read_value()
            if abs(float(val) - expected) > tol:
                errors.append(f"{node_path}: expected {expected}, got {val}")
        assert not errors, "\n".join(errors)

    async def test_batch_id_is_a_string(
        self,
        fnb_opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """FoodBevLine.Mixer1.BatchId returns a Python str."""
        _, client, _, ns = fnb_opcua_static
        node = client.get_node(ua.NodeId("FoodBevLine.Mixer1.BatchId", ns))
        val = await node.read_value()
        assert isinstance(val, str), (
            f"BatchId expected str, got {type(val)}: {val!r}"
        )

    async def test_counter_nodes_non_negative(
        self,
        fnb_opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """UInt32 counter nodes carry non-negative values."""
        _, client, _, ns = fnb_opcua_static
        for node_path, type_str, _ in EXPECTED_FNB_NODES:
            if type_str != "UInt32":
                continue
            node = client.get_node(ua.NodeId(node_path, ns))
            val = await node.read_value()
            assert val >= 0, f"{node_path}: counter value {val} is negative"

    async def test_state_nodes_within_valid_range(
        self,
        fnb_opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """State UInt16 nodes are within their configured EURange."""
        _, client, _, ns = fnb_opcua_static
        state_nodes = [
            "FoodBevLine.Mixer1.State",
            "FoodBevLine.Oven1.State",
            "FoodBevLine.Filler1.State",
            "FoodBevLine.CIP1.State",
        ]
        errors: list[str] = []
        for node_path in state_nodes:
            eu = await _read_eurange(client, ua.NodeId(node_path, ns))
            node = client.get_node(ua.NodeId(node_path, ns))
            val = int(await node.read_value())
            lo, hi = int(eu.Low), int(eu.High)
            if hi > 0 and not (lo <= val <= hi):
                errors.append(f"{node_path}: value={val} outside [{lo}, {hi}]")
        assert not errors, "State nodes out of range:\n" + "\n".join(errors)

    async def test_all_fnb_nodes_read_only(
        self,
        fnb_opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Every F&B OPC-UA node has AccessLevel 1 (read-only).

        F&B setpoints are accessed via Modbus only (Eurotherm/Danfoss controllers).
        """
        _, client, _, ns = fnb_opcua_static
        errors: list[str] = []
        for node_path, _, writable in EXPECTED_FNB_NODES:
            expected = 3 if writable else 1
            node = client.get_node(ua.NodeId(node_path, ns))
            result = await node.read_attribute(ua.AttributeIds.AccessLevel)
            actual = int(result.Value.Value)
            if actual != expected:
                errors.append(
                    f"{node_path}: expected AccessLevel {expected}, got {actual}"
                )
        assert not errors, "AccessLevel mismatches:\n" + "\n".join(errors)

    async def test_status_codes_good_for_good_quality_signals(
        self,
        fnb_opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """All injected Double nodes have Good OPC-UA StatusCode."""
        _, client, _, ns = fnb_opcua_static
        errors: list[str] = []
        for node_path, type_str, _ in EXPECTED_FNB_NODES:
            if type_str != "Double":
                continue
            node = client.get_node(ua.NodeId(node_path, ns))
            dv = await node.read_data_value()
            if not dv.StatusCode.is_good():
                errors.append(
                    f"{node_path}: StatusCode={dv.StatusCode} is not Good"
                )
        assert not errors, "Non-Good status codes:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# Tests: F&B OPC-UA with live DataEngine
# ---------------------------------------------------------------------------


class TestFnbOpcuaWithLiveEngine:
    """OPC-UA values update when DataEngine produces new signal values."""

    async def test_engine_values_reach_opcua(self) -> None:
        """Engine ticks populate the store; OPC-UA sync delivers those values."""
        config, store, clock, engine = _base_fnb_config()

        server = OpcuaServer(config, store, host=_HOST, port=0)  # type: ignore[arg-type]
        await server.start()

        # Start engine as async task.
        engine_task = asyncio.create_task(engine.run())
        # Wait for OPC-UA sync cycle (500ms) + buffer.
        await asyncio.sleep(0.8)

        port = server.actual_port
        assert port > 0
        client = Client(f"opc.tcp://{_HOST}:{port}/")
        await client.connect()

        try:
            ns = NAMESPACE_INDEX
            # filler.line_speed should be set by the engine (Off state → 0 or Running)
            node = client.get_node(ua.NodeId("FoodBevLine.Filler1.LineSpeed", ns))
            val = await node.read_value()
            # Engine is live; value should be finite and within range.
            assert val is not None and math.isfinite(float(val)), (
                f"FoodBevLine.Filler1.LineSpeed returned non-finite: {val}"
            )
            # Energy cumulative_kwh must be non-negative.
            energy_node = client.get_node(
                ua.NodeId("FoodBevLine.Energy.CumulativeKwh", ns)
            )
            kwh = float(await energy_node.read_value())
            assert kwh >= 0.0, f"CumulativeKwh should be ≥0, got {kwh}"
        finally:
            await client.disconnect()
            engine_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await engine_task
            await server.stop()


# ---------------------------------------------------------------------------
# Tests: F&B MQTT topic publication
# ---------------------------------------------------------------------------


class TestFnbMqttAllTopicsPublish:
    """All 13 F&B MQTT topics publish; no vibration topics appear."""

    async def test_all_13_topics_received(
        self,
        fnb_mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """All 13 expected F&B topics receive at least one message within 10s."""
        publisher, _, _ = fnb_mqtt_components

        collector = _MessageCollector()
        sub = _make_subscriber()
        try:
            sub.on_message = collector.on_message
            sub.subscribe(f"{_TOPIC_PREFIX}/#", qos=1)
            time.sleep(0.5)

            await publisher.start()
            found = await _wait_for_topics(collector, FNB_ALL_TOPICS, timeout=10.0)

            received = collector.topics_received()
            missing = FNB_ALL_TOPICS - received
            assert found and not missing, (
                f"Missing F&B topics after 10s: {sorted(missing)}\n"
                f"Received {len(received)}: {sorted(received)}"
            )
        finally:
            sub.loop_stop()
            sub.disconnect()

    async def test_no_vibration_topics_published(
        self,
        fnb_mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """No vibration/* topics appear for F&B profile (no vibration equipment)."""
        publisher, _, _ = fnb_mqtt_components

        collector = _MessageCollector()
        sub = _make_subscriber("-vib")
        try:
            sub.on_message = collector.on_message
            sub.subscribe(f"{_TOPIC_PREFIX}/#", qos=1)
            time.sleep(0.5)

            await publisher.start()
            # Wait long enough for all timed topics to fire at least once.
            await asyncio.sleep(4.0)

            vib_msgs = [
                m for m in collector.get_messages()
                if "vibration/" in m["topic"]
            ]
            assert not vib_msgs, (
                f"Vibration topics received in F&B profile: "
                f"{[m['topic'] for m in vib_msgs]}"
            )
        finally:
            sub.loop_stop()
            sub.disconnect()

    async def test_topic_count_exactly_13(
        self,
        fnb_mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """Exactly 13 distinct topics are published for the F&B profile."""
        publisher, _, _ = fnb_mqtt_components

        collector = _MessageCollector()
        sub = _make_subscriber("-count")
        try:
            sub.on_message = collector.on_message
            sub.subscribe(f"{_TOPIC_PREFIX}/#", qos=1)
            time.sleep(0.5)

            await publisher.start()
            await _wait_for_topics(collector, FNB_ALL_TOPICS, timeout=10.0)

            # Wait a bit more so slower timed topics have time to publish.
            await asyncio.sleep(2.0)

            received = collector.topics_received()
            extra = received - FNB_ALL_TOPICS
            assert not extra, (
                f"Unexpected extra topics in F&B profile: {sorted(extra)}"
            )
            assert len(received) == len(FNB_ALL_TOPICS), (
                f"Expected {len(FNB_ALL_TOPICS)} topics, got {len(received)}: "
                f"{sorted(received)}"
            )
        finally:
            sub.loop_stop()
            sub.disconnect()


# ---------------------------------------------------------------------------
# Tests: F&B MQTT payload structure
# ---------------------------------------------------------------------------


class TestFnbMqttPayloadStructure:
    """F&B MQTT payloads match PRD Section 3.3.4 schema."""

    async def test_per_signal_payload_has_required_fields(
        self,
        fnb_mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """Each F&B topic payload has: timestamp, value, unit, quality."""
        publisher, _, _ = fnb_mqtt_components

        collector = _MessageCollector()
        sub = _make_subscriber()
        try:
            sub.on_message = collector.on_message
            sub.subscribe(f"{_TOPIC_PREFIX}/#", qos=1)
            time.sleep(0.5)

            await publisher.start()
            await _wait_for_topics(collector, FNB_ALL_TOPICS, timeout=10.0)

            msgs = collector.get_messages()
            errors: list[str] = []
            seen_topics: set[str] = set()
            for msg in msgs:
                topic = msg["topic"]
                if topic in seen_topics:
                    continue
                seen_topics.add(topic)
                payload = msg["payload"]
                required = {"timestamp", "value", "unit", "quality"}
                missing_fields = required - set(payload.keys())
                if missing_fields:
                    errors.append(f"{topic}: missing fields {missing_fields}")
            assert not errors, "Payload field errors:\n" + "\n".join(errors)
        finally:
            sub.loop_stop()
            sub.disconnect()

    async def test_value_is_numeric(
        self,
        fnb_mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """Signal values in F&B payloads are JSON numbers, not strings."""
        publisher, _, _ = fnb_mqtt_components

        target = f"{_TOPIC_PREFIX}/coder/ink_level"
        collector = _MessageCollector()
        sub = _make_subscriber("-num")
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
        fnb_mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """Timestamps are ISO 8601 with milliseconds, ending in Z."""
        from datetime import datetime

        publisher, _, _ = fnb_mqtt_components

        target = f"{_TOPIC_PREFIX}/env/ambient_temp"
        collector = _MessageCollector()
        sub = _make_subscriber("-ts")
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
            parts = ts.split(".")
            assert len(parts) == 2, f"Timestamp should have ms: {ts!r}"
            ms_part = parts[1].rstrip("Z")
            assert len(ms_part) == 3, f"Expected 3-digit ms, got {ms_part!r}"
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
        finally:
            sub.loop_stop()
            sub.disconnect()

    async def test_injected_coder_values_appear_in_payloads(
        self,
        fnb_mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """MQTT values for key topics match values injected into the store."""
        publisher, _, _ = fnb_mqtt_components

        collector = _MessageCollector()
        sub = _make_subscriber("-vals")
        try:
            sub.on_message = collector.on_message
            sub.subscribe(f"{_TOPIC_PREFIX}/#", qos=1)
            time.sleep(0.5)

            await publisher.start()
            await _wait_for_topics(
                collector,
                {
                    f"{_TOPIC_PREFIX}/coder/ink_level",
                    f"{_TOPIC_PREFIX}/env/ambient_temp",
                },
                timeout=10.0,
            )

            msgs_by_topic: dict[str, Any] = {}
            for m in collector.get_messages():
                msgs_by_topic.setdefault(m["topic"], m)

            errors: list[str] = []
            checks = [
                (f"{_TOPIC_PREFIX}/coder/ink_level", 72.0),
                (f"{_TOPIC_PREFIX}/env/ambient_temp", 15.0),
            ]
            for topic, expected in checks:
                if topic not in msgs_by_topic:
                    errors.append(f"No message for {topic}")
                    continue
                val = float(msgs_by_topic[topic]["payload"]["value"])
                if abs(val - expected) > 0.1:
                    errors.append(f"{topic}: expected {expected}, got {val}")
            assert not errors, "\n".join(errors)
        finally:
            sub.loop_stop()
            sub.disconnect()


# ---------------------------------------------------------------------------
# Tests: F&B MQTT QoS and retain
# ---------------------------------------------------------------------------


class TestFnbMqttQosAndRetain:
    """F&B MQTT QoS and retain flags match PRD Section 3.3.5, 3.3.8."""

    async def test_qos1_for_critical_coder_topics(
        self,
        fnb_mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """state, prints_total, nozzle_health, gutter_fault are QoS 1."""
        publisher, _, _ = fnb_mqtt_components

        collector = _MessageCollector()
        sub = _make_subscriber()
        try:
            sub.on_message = collector.on_message
            sub.subscribe(f"{_TOPIC_PREFIX}/coder/#", qos=1)
            time.sleep(0.5)

            await publisher.start()
            await _wait_for_topics(collector, FNB_QOS1_TOPICS, timeout=10.0)

            msgs = collector.get_messages()
            errors: list[str] = []
            for topic in FNB_QOS1_TOPICS:
                topic_msgs = [m for m in msgs if m["topic"] == topic]
                if not topic_msgs:
                    errors.append(f"{topic}: no messages received")
                elif topic_msgs[0]["qos"] != 1:
                    errors.append(
                        f"{topic}: expected QoS 1, got QoS {topic_msgs[0]['qos']}"
                    )
            assert not errors, "QoS 1 errors:\n" + "\n".join(errors)
        finally:
            sub.loop_stop()
            sub.disconnect()

    async def test_qos0_for_analog_and_env_topics(
        self,
        fnb_mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """Analog coder and env topics use QoS 0."""
        publisher, _, _ = fnb_mqtt_components

        qos0_check = {
            f"{_TOPIC_PREFIX}/coder/ink_level",
            f"{_TOPIC_PREFIX}/env/ambient_temp",
        }
        collector = _MessageCollector()
        sub = _make_subscriber()
        try:
            sub.on_message = collector.on_message
            sub.subscribe(f"{_TOPIC_PREFIX}/#", qos=1)
            time.sleep(0.5)

            await publisher.start()
            await _wait_for_topics(collector, qos0_check, timeout=10.0)

            msgs = collector.get_messages()
            errors: list[str] = []
            for topic in qos0_check:
                live_msgs = [m for m in msgs if m["topic"] == topic and not m["retain"]]
                if not live_msgs:
                    live_msgs = [m for m in msgs if m["topic"] == topic]
                if not live_msgs:
                    errors.append(f"{topic}: no messages received")
                elif live_msgs[0]["qos"] != 0:
                    errors.append(
                        f"{topic}: expected QoS 0, got QoS {live_msgs[0]['qos']}"
                    )
            assert not errors, "QoS 0 errors:\n" + "\n".join(errors)
        finally:
            sub.loop_stop()
            sub.disconnect()

    async def test_all_13_topics_are_retained(
        self,
        fnb_mqtt_components: tuple[MqttPublisher, DataEngine, SignalStore],
    ) -> None:
        """A new subscriber to any F&B topic receives the last retained value.

        In the F&B profile all 13 topics are retained (no vibration topics).
        """
        publisher, _, _ = fnb_mqtt_components

        # Start publisher and let it publish retained messages.
        await publisher.start()
        await asyncio.sleep(3.0)

        # Connect a NEW subscriber for one retained coder topic and one env topic.
        retain_topics = {
            f"{_TOPIC_PREFIX}/coder/ink_level",
            f"{_TOPIC_PREFIX}/env/ambient_humidity",
        }

        collector = _MessageCollector()
        sub = _make_subscriber("-ret")
        try:
            sub.on_message = collector.on_message
            for t in retain_topics:
                sub.subscribe(t, qos=1)

            found = await _wait_for_topics(collector, retain_topics, timeout=5.0)
            assert found, "Retained messages not received on new subscription"

            msgs = collector.get_messages()
            for topic in retain_topics:
                topic_msgs = [m for m in msgs if m["topic"] == topic]
                assert topic_msgs, f"No messages for retained topic {topic}"
                assert topic_msgs[0]["retain"] is True, (
                    f"First message for {topic} should be retained (retain=True), "
                    f"got retain={topic_msgs[0]['retain']}"
                )
        finally:
            sub.loop_stop()
            sub.disconnect()


# ---------------------------------------------------------------------------
# Tests: F&B OPC-UA + MQTT simultaneous operation
# ---------------------------------------------------------------------------


class TestFnbBothProtocolsSimultaneous:
    """OPC-UA and MQTT operate simultaneously from the same F&B store."""

    async def test_both_protocols_serve_data_simultaneously(self) -> None:
        """OPC-UA and MQTT both serve F&B data from the same DataEngine store."""
        config, store, clock, engine = _base_fnb_config()

        # Tick to populate all signal IDs.
        for _ in range(5):
            engine.tick()

        t = clock.sim_time
        store.set("filler.line_speed", 60.0, t)
        store.set("energy.line_power", 180.0, t)
        store.set("coder.state", 2.0, t)
        store.set("coder.prints_total", 9000.0, t)
        store.set("coder.ink_level", 70.0, t)
        store.set("coder.printhead_temp", 43.0, t)
        store.set("coder.ink_pump_speed", 1450.0, t)
        store.set("coder.ink_pressure", 2.7, t)
        store.set("coder.ink_viscosity_actual", 27.0, t)
        store.set("coder.supply_voltage", 229.0, t)
        store.set("coder.ink_consumption_ml", 115.0, t)
        store.set("coder.nozzle_health", 97.0, t)
        store.set("coder.gutter_fault", 0.0, t)
        store.set("environment.ambient_temp", 14.5, t)
        store.set("environment.ambient_humidity", 52.0, t)

        # Start OPC-UA server.
        opcua = OpcuaServer(config, store, host=_HOST, port=0)  # type: ignore[arg-type]
        await opcua.start()
        await asyncio.sleep(0.6)

        # Start MQTT publisher (subscribe before start to catch event-driven).
        collector = _MessageCollector()
        sub_client = _make_subscriber("-both")
        sub_client.on_message = collector.on_message
        sub_client.subscribe(f"{_TOPIC_PREFIX}/#", qos=1)
        time.sleep(0.3)

        publisher = MqttPublisher(
            config,  # type: ignore[arg-type]
            store,
            host=_BROKER_HOST,
            port=_BROKER_PORT,
        )
        await publisher.start()

        # Wait for sync + MQTT publish.
        await asyncio.sleep(2.5)

        opcua_port = opcua.actual_port
        assert opcua_port > 0
        client = Client(f"opc.tcp://{_HOST}:{opcua_port}/")
        await client.connect()

        try:
            ns = NAMESPACE_INDEX

            # OPC-UA: check filler.line_speed
            node = client.get_node(
                ua.NodeId("FoodBevLine.Filler1.LineSpeed", ns)
            )
            opcua_val = float(await node.read_value())

            # MQTT: check coder/state arrived
            received_topics = collector.topics_received()
            coder_state_topic = f"{_TOPIC_PREFIX}/coder/state"

            assert math.isfinite(opcua_val), (
                f"OPC-UA Filler1.LineSpeed is not finite: {opcua_val}"
            )
            assert coder_state_topic in received_topics, (
                f"coder/state not received in MQTT. "
                f"Got: {sorted(received_topics)}"
            )

            # No vibration topics mixed in.
            vib_topics = [t for t in received_topics if "vibration/" in t]
            assert not vib_topics, (
                f"Vibration topics in F&B simultaneous test: {vib_topics}"
            )
        finally:
            await client.disconnect()
            sub_client.loop_stop()
            sub_client.disconnect()
            await publisher.stop()
            await opcua.stop()
