"""Cross-protocol consistency integration tests.

Starts DataEngine + ModbusServer + OpcuaServer + MqttPublisher simultaneously,
reads the same signals via all applicable protocols, and verifies that values
are consistent across the entire stack.

Architecture::

    Engine -> SignalStore -> ModbusServer (press/laminator/slitter/energy)
                          -> OpcuaServer  (press/laminator/slitter/energy)
                          -> MqttPublisher (coder/environment/vibration)

No single signal exists on all three protocols in the packaging profile.
Modbus and OPC-UA serve the same 30 press/laminator/slitter/energy signals.
MQTT serves the 16+1 coder/environment/vibration signals.

The capstone test verifies that all three adapters operate simultaneously
from a single store without interference, and that signals shared between
Modbus and OPC-UA are value-consistent within float32 encoding precision.

Requires Docker Compose to be running::

    docker compose up -d mqtt-broker

PRD Reference: Section 13.2 (cross-protocol consistency), Phase 2 exit criteria
"""

from __future__ import annotations

import asyncio
import json
import socket
import struct
import time
from pathlib import Path
from threading import Lock
from typing import Any

import paho.mqtt.client as mqtt
import pytest
from asyncua import Client as OpcuaClient
from asyncua import ua
from paho.mqtt.enums import CallbackAPIVersion
from pymodbus.client import AsyncModbusTcpClient

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.protocols.modbus_server import ModbusServer, decode_float32_abcd
from factory_simulator.protocols.mqtt_publisher import MqttPublisher
from factory_simulator.protocols.opcua_server import NAMESPACE_INDEX, OpcuaServer
from factory_simulator.store import SignalStore

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory.yaml"
_HOST = "127.0.0.1"
_MODBUS_PORT = 15503  # Unique port to avoid conflict with other integration tests
_BROKER_HOST = "127.0.0.1"
_BROKER_PORT = 1883
_MQTT_TOPIC_PREFIX = "collatr/factory/demo/packaging1"


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
# Helpers
# ---------------------------------------------------------------------------


def _float32_roundtrip(value: float) -> float:
    """Round-trip a float64 through float32 encoding (Modbus precision)."""
    return float(struct.unpack(">f", struct.pack(">f", value))[0])


class _MqttCollector:
    """Thread-safe message collector for paho subscriber callbacks."""

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
            })

    def topics_received(self) -> set[str]:
        with self._lock:
            return {m["topic"] for m in self.messages}

    def get_messages(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.messages)


async def _wait_for_topics(
    collector: _MqttCollector,
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
# Fixture: all three protocols running simultaneously
# ---------------------------------------------------------------------------


@pytest.fixture
async def all_protocols() -> (  # type: ignore[override]
    tuple[
        AsyncModbusTcpClient,
        OpcuaClient,
        _MqttCollector,
        SignalStore,
    ]
):
    """Start all three protocol adapters against a pre-populated store.

    Pre-populates the SignalStore with known values for all signals, then
    starts ModbusServer, OpcuaServer, and MqttPublisher simultaneously.
    Connects Modbus and OPC-UA clients and creates an MQTT subscriber.

    Yields ``(modbus_client, opcua_client, mqtt_collector, store)``.
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

    # Inject known test values for signals on Modbus/OPC-UA
    t = clock.sim_time
    store.set("press.machine_state", 2.0, t)         # Running (2)
    store.set("press.line_speed", 150.0, t)
    store.set("press.web_tension", 50.0, t)
    store.set("press.fault_code", 0.0, t)
    store.set("press.impression_count", 1000.0, t)
    store.set("press.good_count", 5000.0, t)
    store.set("press.waste_count", 50.0, t)
    store.set("press.nip_pressure", 3.5, t)
    store.set("press.registration_error_x", 0.02, t)
    store.set("press.registration_error_y", 0.01, t)
    store.set("press.ink_viscosity", 28.0, t)
    store.set("press.ink_temperature", 25.0, t)
    store.set("press.dryer_temp_zone_1", 75.0, t)
    store.set("press.dryer_setpoint_zone_1", 75.0, t)
    store.set("press.dryer_temp_zone_2", 80.0, t)
    store.set("press.dryer_setpoint_zone_2", 80.0, t)
    store.set("press.dryer_temp_zone_3", 85.0, t)
    store.set("press.dryer_setpoint_zone_3", 85.0, t)
    store.set("press.main_drive_current", 65.0, t)
    store.set("press.main_drive_speed", 1200.0, t)
    store.set("press.unwind_diameter", 800.0, t)
    store.set("press.rewind_diameter", 400.0, t)
    store.set("laminator.nip_temp", 85.0, t)
    store.set("laminator.nip_pressure", 4.0, t)
    store.set("laminator.tunnel_temp", 60.0, t)
    store.set("laminator.web_speed", 140.0, t)
    store.set("laminator.adhesive_weight", 2.5, t)
    store.set("slitter.speed", 145.0, t)
    store.set("slitter.web_tension", 45.0, t)
    store.set("slitter.reel_count", 100.0, t)
    store.set("energy.line_power", 85.0, t)
    store.set("energy.cumulative_kwh", 12000.0, t)

    # Inject known test values for signals on MQTT
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
    store.set("environment.ambient_temp", 22.5, t)
    store.set("environment.ambient_humidity", 45.0, t)
    store.set("vibration.main_drive_x", 4.2, t)
    store.set("vibration.main_drive_y", 3.8, t)
    store.set("vibration.main_drive_z", 5.1, t)

    # -- Start all three protocol servers ------------------------------------
    modbus = ModbusServer(config, store, host=_HOST, port=_MODBUS_PORT)
    modbus.sync_registers()
    await modbus.start()

    opcua = OpcuaServer(config, store, host=_HOST, port=0)
    await opcua.start()

    # MQTT subscriber: subscribe BEFORE starting publisher to capture
    # event-driven topics that only fire once on initial value change.
    collector = _MqttCollector()
    cid = f"test-cross-{int(time.monotonic() * 1000) % 100000}"
    mqtt_sub = mqtt.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=cid,
        protocol=mqtt.MQTTv311,
    )
    mqtt_sub.on_message = collector.on_message
    mqtt_sub.connect(_BROKER_HOST, _BROKER_PORT, keepalive=60)
    mqtt_sub.loop_start()
    time.sleep(0.5)
    mqtt_sub.subscribe(f"{_MQTT_TOPIC_PREFIX}/#", qos=1)
    time.sleep(0.3)

    publisher = MqttPublisher(config, store, host=_BROKER_HOST, port=_BROKER_PORT)
    await publisher.start()

    # Wait for OPC-UA sync cycle (500ms) + Modbus update loop settle
    await asyncio.sleep(0.8)

    # Connect Modbus client
    modbus_client = AsyncModbusTcpClient(_HOST, port=_MODBUS_PORT)
    await modbus_client.connect()
    assert modbus_client.connected, "Failed to connect Modbus client"

    # Connect OPC-UA client
    opcua_port = opcua.actual_port
    assert opcua_port > 0, "OPC-UA server did not bind to a port"
    opcua_client = OpcuaClient(f"opc.tcp://{_HOST}:{opcua_port}/")
    await opcua_client.connect()

    # Wait for MQTT messages to arrive (timed signals at ~1s intervals)
    await asyncio.sleep(2.0)

    yield modbus_client, opcua_client, collector, store

    # -- Cleanup -------------------------------------------------------------
    modbus_client.close()
    await opcua_client.disconnect()
    mqtt_sub.loop_stop()
    mqtt_sub.disconnect()
    await publisher.stop()
    await opcua.stop()
    await modbus.stop()


# ---------------------------------------------------------------------------
# Tests: cross-protocol value consistency (Modbus <-> OPC-UA)
# ---------------------------------------------------------------------------


class TestModbusOpcuaConsistency:
    """Signals shared between Modbus and OPC-UA return consistent values."""

    async def test_line_speed_matches(
        self,
        all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """press.line_speed: Modbus HR 100-101 (float32) == OPC-UA Double."""
        modbus_client, opcua_client, _, _ = all_protocols

        # Modbus: float32 from HR 100-101
        result = await modbus_client.read_holding_registers(100, count=2)
        assert not result.isError(), f"Modbus read HR 100-101 failed: {result}"
        modbus_val = decode_float32_abcd(result.registers)

        # OPC-UA: Double
        node = opcua_client.get_node(
            ua.NodeId("PackagingLine.Press1.LineSpeed", NAMESPACE_INDEX)
        )
        opcua_val = float(await node.read_value())

        # Compare: OPC-UA Double truncated to float32 precision
        assert abs(modbus_val - _float32_roundtrip(opcua_val)) < 0.01, (
            f"line_speed mismatch: Modbus={modbus_val}, OPC-UA={opcua_val}"
        )

    async def test_machine_state_matches(
        self,
        all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """press.machine_state: Modbus HR 210 (uint16) == OPC-UA UInt16."""
        modbus_client, opcua_client, _, _ = all_protocols

        # Modbus: uint16 from HR 210
        result = await modbus_client.read_holding_registers(210, count=1)
        assert not result.isError(), f"Modbus read HR 210 failed: {result}"
        modbus_state = result.registers[0]

        # OPC-UA: UInt16
        node = opcua_client.get_node(
            ua.NodeId("PackagingLine.Press1.State", NAMESPACE_INDEX)
        )
        opcua_state = int(await node.read_value())

        assert modbus_state == opcua_state, (
            f"machine_state mismatch: Modbus={modbus_state}, OPC-UA={opcua_state}"
        )

    async def test_web_tension_matches(
        self,
        all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """press.web_tension: Modbus HR 102-103 (float32) == OPC-UA Double."""
        modbus_client, opcua_client, _, _ = all_protocols

        result = await modbus_client.read_holding_registers(102, count=2)
        assert not result.isError()
        modbus_val = decode_float32_abcd(result.registers)

        node = opcua_client.get_node(
            ua.NodeId("PackagingLine.Press1.WebTension", NAMESPACE_INDEX)
        )
        opcua_val = float(await node.read_value())

        assert abs(modbus_val - _float32_roundtrip(opcua_val)) < 0.01, (
            f"web_tension mismatch: Modbus={modbus_val}, OPC-UA={opcua_val}"
        )

    async def test_energy_line_power_matches(
        self,
        all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """energy.line_power: Modbus HR 600-601 (float32) == OPC-UA Double."""
        modbus_client, opcua_client, _, _ = all_protocols

        result = await modbus_client.read_holding_registers(600, count=2)
        assert not result.isError()
        modbus_val = decode_float32_abcd(result.registers)

        node = opcua_client.get_node(
            ua.NodeId("PackagingLine.Energy.LinePower", NAMESPACE_INDEX)
        )
        opcua_val = float(await node.read_value())

        assert abs(modbus_val - _float32_roundtrip(opcua_val)) < 0.01, (
            f"line_power mismatch: Modbus={modbus_val}, OPC-UA={opcua_val}"
        )

    async def test_multiple_float32_signals_consistent(
        self,
        all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """Multiple float32 signals match between Modbus HR and OPC-UA Double.

        Tests a representative set of signals across all equipment groups
        to verify the full sync path: store -> Modbus registers and
        store -> OPC-UA nodes produce consistent values.
        """
        modbus_client, opcua_client, _, _ = all_protocols

        # (modbus_hr_addr, opcua_node_path, injected_value, tolerance)
        checks: list[tuple[int, str, float, float]] = [
            (100, "PackagingLine.Press1.LineSpeed", 150.0, 0.01),
            (102, "PackagingLine.Press1.WebTension", 50.0, 0.01),
            (110, "PackagingLine.Press1.Ink.Viscosity", 28.0, 0.01),
            (120, "PackagingLine.Press1.Dryer.Zone1.Temperature", 75.0, 0.01),
            (300, "PackagingLine.Press1.MainDrive.Current", 65.0, 0.01),
            (400, "PackagingLine.Laminator1.NipTemperature", 85.0, 0.01),
            (500, "PackagingLine.Slitter1.Speed", 145.0, 0.01),
            (600, "PackagingLine.Energy.LinePower", 85.0, 0.01),
        ]

        errors: list[str] = []
        for hr_addr, node_path, expected, tol in checks:
            # Modbus
            result = await modbus_client.read_holding_registers(hr_addr, count=2)
            if result.isError():
                errors.append(f"Modbus HR {hr_addr} read failed: {result}")
                continue
            modbus_val = decode_float32_abcd(result.registers)

            # OPC-UA
            node = opcua_client.get_node(ua.NodeId(node_path, NAMESPACE_INDEX))
            opcua_val = float(await node.read_value())

            # Cross-protocol comparison
            if abs(modbus_val - _float32_roundtrip(opcua_val)) > tol:
                errors.append(
                    f"{node_path}: Modbus={modbus_val}, OPC-UA={opcua_val}"
                )

            # Both should match the injected value (within float32 precision)
            if abs(modbus_val - _float32_roundtrip(expected)) > tol:
                errors.append(
                    f"{node_path}: Modbus={modbus_val}, expected={expected}"
                )
            if abs(opcua_val - expected) > tol:
                errors.append(
                    f"{node_path}: OPC-UA={opcua_val}, expected={expected}"
                )

        assert not errors, (
            "Cross-protocol value mismatches:\n" + "\n".join(errors)
        )


# ---------------------------------------------------------------------------
# Tests: MQTT signals from the same store
# ---------------------------------------------------------------------------


class TestMqttFromSameStore:
    """MQTT signals reflect the same store as Modbus and OPC-UA."""

    async def test_mqtt_coder_ink_level_matches_store(
        self,
        all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """coder/ink_level MQTT value matches the injected store value."""
        _, _, collector, _ = all_protocols

        target_topic = f"{_MQTT_TOPIC_PREFIX}/coder/ink_level"
        msgs = [
            m for m in collector.get_messages() if m["topic"] == target_topic
        ]
        assert msgs, f"No MQTT messages received for {target_topic}"

        mqtt_val = float(msgs[-1]["payload"]["value"])
        # Injected value is 85.0
        assert abs(mqtt_val - 85.0) < 0.01, (
            f"coder/ink_level: MQTT={mqtt_val}, expected=85.0"
        )

    async def test_mqtt_environment_temp_matches_store(
        self,
        all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """env/ambient_temp MQTT value matches the injected store value."""
        _, _, collector, _ = all_protocols

        target_topic = f"{_MQTT_TOPIC_PREFIX}/env/ambient_temp"
        msgs = [
            m for m in collector.get_messages() if m["topic"] == target_topic
        ]
        assert msgs, f"No MQTT messages received for {target_topic}"

        mqtt_val = float(msgs[-1]["payload"]["value"])
        assert abs(mqtt_val - 22.5) < 0.01, (
            f"env/ambient_temp: MQTT={mqtt_val}, expected=22.5"
        )

    async def test_mqtt_vibration_matches_store(
        self,
        all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """Batch vibration MQTT x/y/z values match injected store values."""
        _, _, collector, _ = all_protocols

        batch_topic = f"{_MQTT_TOPIC_PREFIX}/vibration/main_drive"
        msgs = [
            m for m in collector.get_messages() if m["topic"] == batch_topic
        ]
        assert msgs, f"No MQTT messages received for {batch_topic}"

        payload = msgs[-1]["payload"]
        assert abs(float(payload["x"]) - 4.2) < 0.01, (
            f"vibration x: MQTT={payload['x']}, expected=4.2"
        )
        assert abs(float(payload["y"]) - 3.8) < 0.01, (
            f"vibration y: MQTT={payload['y']}, expected=3.8"
        )
        assert abs(float(payload["z"]) - 5.1) < 0.01, (
            f"vibration z: MQTT={payload['z']}, expected=5.1"
        )


# ---------------------------------------------------------------------------
# Tests: all three protocols operating simultaneously
# ---------------------------------------------------------------------------


class TestSimultaneousOperation:
    """All three protocol adapters run simultaneously without interference."""

    async def test_all_three_protocols_serve_data(
        self,
        all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """Modbus, OPC-UA, and MQTT all serve data in the same test session."""
        modbus_client, opcua_client, collector, _ = all_protocols

        # Modbus: read a holding register
        result = await modbus_client.read_holding_registers(100, count=2)
        assert not result.isError(), "Modbus failed to respond"

        # OPC-UA: read a variable node
        node = opcua_client.get_node(
            ua.NodeId("PackagingLine.Press1.LineSpeed", NAMESPACE_INDEX)
        )
        opcua_val = await node.read_value()
        assert opcua_val is not None, "OPC-UA returned None"

        # MQTT: verify messages were received
        received = collector.topics_received()
        assert len(received) > 0, "No MQTT messages received"

    async def test_store_change_propagates_to_modbus_and_opcua(
        self,
        all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """A store value change propagates to both Modbus and OPC-UA.

        Verifies the full path: store.set() -> Modbus sync -> OPC-UA sync,
        both showing the new value within one sync cycle.
        """
        modbus_client, opcua_client, _, store = all_protocols

        # Inject a distinctive new value
        new_speed = 222.5
        store.set("press.line_speed", new_speed, 999.0)

        # Wait for both sync cycles:
        # - Modbus update loop: 50ms
        # - OPC-UA update loop: 500ms
        await asyncio.sleep(1.0)

        # Modbus readback
        result = await modbus_client.read_holding_registers(100, count=2)
        assert not result.isError()
        modbus_val = decode_float32_abcd(result.registers)

        # OPC-UA readback
        node = opcua_client.get_node(
            ua.NodeId("PackagingLine.Press1.LineSpeed", NAMESPACE_INDEX)
        )
        opcua_val = float(await node.read_value())

        # Both should show the new value
        assert abs(modbus_val - _float32_roundtrip(new_speed)) < 0.01, (
            f"Modbus did not update: got {modbus_val}, expected ~{new_speed}"
        )
        assert abs(opcua_val - new_speed) < 0.01, (
            f"OPC-UA did not update: got {opcua_val}, expected ~{new_speed}"
        )

        # And they should match each other
        assert abs(modbus_val - _float32_roundtrip(opcua_val)) < 0.01, (
            f"Modbus={modbus_val} != OPC-UA={opcua_val} after store change"
        )

    async def test_mqtt_topics_received_alongside_modbus_opcua(
        self,
        all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """MQTT publishes continue while Modbus and OPC-UA are also active.

        Verifies no interference between the three protocol adapters
        sharing the same store and event loop.
        """
        _, _, collector, _ = all_protocols

        # Verify a representative set of MQTT topics were received
        expected_topics = {
            f"{_MQTT_TOPIC_PREFIX}/coder/state",
            f"{_MQTT_TOPIC_PREFIX}/coder/ink_level",
            f"{_MQTT_TOPIC_PREFIX}/env/ambient_temp",
        }
        received = collector.topics_received()
        missing = expected_topics - received
        assert not missing, (
            f"MQTT topics missing while all protocols active: {sorted(missing)}\n"
            f"Received: {sorted(received)}"
        )

    async def test_state_observable_from_all_protocols(
        self,
        all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """Machine state is observable from Modbus/OPC-UA; coder state from MQTT.

        Press machine_state (injected as 2 = Running) should be consistent
        between Modbus HR 210 and OPC-UA Press1.State.  Coder state on MQTT
        (also injected as 2) confirms all state signals come from the same store.
        """
        modbus_client, opcua_client, collector, _ = all_protocols

        # Modbus: press machine_state
        result = await modbus_client.read_holding_registers(210, count=1)
        assert not result.isError()
        modbus_press_state = result.registers[0]

        # OPC-UA: press machine_state
        node = opcua_client.get_node(
            ua.NodeId("PackagingLine.Press1.State", NAMESPACE_INDEX)
        )
        opcua_press_state = int(await node.read_value())

        # MQTT: coder state
        coder_topic = f"{_MQTT_TOPIC_PREFIX}/coder/state"
        coder_msgs = [
            m for m in collector.get_messages() if m["topic"] == coder_topic
        ]
        assert coder_msgs, f"No MQTT messages for {coder_topic}"
        mqtt_coder_state = coder_msgs[-1]["payload"]["value"]

        # Press states must match (Modbus == OPC-UA)
        assert modbus_press_state == opcua_press_state, (
            f"Press state: Modbus={modbus_press_state}, OPC-UA={opcua_press_state}"
        )

        # Both press and coder states should be 2 (Running/Ready)
        # since we injected 2.0 for both
        assert modbus_press_state == 2, (
            f"Press state expected Running (2), got {modbus_press_state}"
        )
        assert mqtt_coder_state == 2.0 or mqtt_coder_state == 2, (
            f"Coder state expected 2, got {mqtt_coder_state}"
        )
