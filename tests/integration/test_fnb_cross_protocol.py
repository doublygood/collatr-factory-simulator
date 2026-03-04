"""F&B Cross-Protocol Consistency Integration Tests.

Verifies that the same signal value, injected into the SignalStore, is
observable consistently across Modbus HR, Modbus IR, OPC-UA, and MQTT
for the F&B (Food & Beverage) chilled ready meal profile.

Tests groups:

* ``TestFnbModbusHrVsIr`` — signals served on both Modbus HR and IR
  return consistent values (no Mosquitto broker required).
* ``TestFnbCdabVsAbcdEncoding`` — mixer CDAB registers decode correctly;
  ABCD decode gives wrong value (confirming word-swap is active).
* ``TestFnbModbusOpcuaConsistency`` — Modbus HR float32 matches OPC-UA
  Double for shared energy signals (no broker required).
* ``TestFnbAllThreeProtocols`` — Modbus, OPC-UA, and MQTT all serve from
  the same store simultaneously (requires Mosquitto broker).
* ``TestFnbGroundTruthScenarioEvents`` — GroundTruthLogger records
  F&B scenario lifecycle events (no external services required).

Requires Docker Compose for MQTT tests::

    docker compose up -d mqtt-broker

PRD Reference: Appendix F (Phase 3 exit criteria), Section 13.2
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

import numpy as np
import paho.mqtt.client as mqtt
import pytest
from asyncua import Client as OpcuaClient
from asyncua import ua
from paho.mqtt.enums import CallbackAPIVersion
from pymodbus.client import AsyncModbusTcpClient

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.engine.ground_truth import GroundTruthLogger
from factory_simulator.protocols.modbus_server import (
    ModbusServer,
    decode_float32_abcd,
    decode_float32_cdab,
    decode_int16_x10,
)
from factory_simulator.protocols.mqtt_publisher import MqttPublisher
from factory_simulator.protocols.opcua_server import NAMESPACE_INDEX, OpcuaServer
from factory_simulator.scenarios.batch_cycle import BatchCycle
from factory_simulator.store import SignalStore

_FNB_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory-foodbev.yaml"
_HOST = "127.0.0.1"
_MODBUS_PORT = 15520  # Unique port for F&B cross-protocol tests
_BROKER_HOST = "127.0.0.1"
_BROKER_PORT = 1883
_TOPIC_PREFIX = "collatr/factory/demo/foodbev1"


def _broker_reachable() -> bool:
    """Return True if the MQTT broker is reachable."""
    try:
        with socket.create_connection((_BROKER_HOST, _BROKER_PORT), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.integration

# Applied to test classes that need the MQTT broker
_needs_broker = pytest.mark.skipif(
    not _broker_reachable(),
    reason=(
        f"MQTT broker not reachable at {_BROKER_HOST}:{_BROKER_PORT}. "
        "Run: docker compose up -d mqtt-broker"
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _float32_roundtrip(value: float) -> float:
    """Round-trip a float64 through float32 encoding (Modbus precision)."""
    return float(struct.unpack(">f", struct.pack(">f", value))[0])


def _populate_fnb_store(store: SignalStore, t: float) -> None:
    """Inject known values for all Modbus- and OPC-UA-accessible F&B signals."""
    # Mixer — CDAB HR 1000-1011
    store.set("mixer.speed", 450.0, t)
    store.set("mixer.torque", 35.0, t)
    store.set("mixer.batch_temp", 65.0, t)
    store.set("mixer.batch_weight", 500.0, t)
    store.set("mixer.state", 2.0, t)          # Mixing
    store.set("mixer.batch_id", "BATCH-001", t)
    store.set("mixer.mix_time_elapsed", 300.0, t)
    store.set("mixer.lid_closed", 1.0, t)

    # Oven — ABCD HR 1100-1125; secondary slave UIDs 11-13
    store.set("oven.zone_1_temp", 160.0, t)
    store.set("oven.zone_2_temp", 200.0, t)
    store.set("oven.zone_3_temp", 180.0, t)
    store.set("oven.zone_1_setpoint", 160.0, t)
    store.set("oven.zone_2_setpoint", 200.0, t)
    store.set("oven.zone_3_setpoint", 180.0, t)
    store.set("oven.belt_speed", 2.0, t)
    store.set("oven.product_core_temp", 65.0, t)
    store.set("oven.humidity_zone_2", 55.0, t)
    store.set("oven.state", 2.0, t)           # Running
    store.set("oven.zone_1_output_power", 45.0, t)
    store.set("oven.zone_2_output_power", 55.0, t)
    store.set("oven.zone_3_output_power", 50.0, t)

    # Filler — OPC-UA nodes
    store.set("filler.line_speed", 60.0, t)
    store.set("filler.fill_weight", 405.0, t)
    store.set("filler.fill_target", 400.0, t)
    store.set("filler.fill_deviation", 5.0, t)
    store.set("filler.packs_produced", 1000.0, t)
    store.set("filler.reject_count", 3.0, t)
    store.set("filler.state", 2.0, t)         # Running
    store.set("filler.hopper_level", 75.0, t)

    # Chiller — HR 1400-1407, IR 110-111
    store.set("chiller.room_temp", 2.5, t)
    store.set("chiller.setpoint", 2.0, t)
    store.set("chiller.compressor_state", 1.0, t)
    store.set("chiller.suction_pressure", 3.0, t)
    store.set("chiller.discharge_pressure", 16.0, t)
    store.set("chiller.defrost_active", 0.0, t)
    store.set("chiller.door_open", 0.0, t)

    # CIP — HR 1500-1507, IR 115
    store.set("cip.state", 0.0, t)
    store.set("cip.wash_temp", 20.0, t)
    store.set("cip.flow_rate", 0.0, t)
    store.set("cip.conductivity", 0.0, t)
    store.set("cip.cycle_time_elapsed", 0.0, t)

    # Energy — HR 600-603, IR 120-121, OPC-UA FoodBevLine.Energy.*
    store.set("energy.line_power", 180.0, t)
    store.set("energy.cumulative_kwh", 9500.0, t)

    # Coder — MQTT (coupling follows filler.state)
    store.set("coder.state", 2.0, t)
    store.set("coder.prints_total", 5000.0, t)
    store.set("coder.ink_level", 72.0, t)
    store.set("coder.printhead_temp", 40.0, t)
    store.set("coder.ink_pump_speed", 1200.0, t)
    store.set("coder.ink_pressure", 2.5, t)
    store.set("coder.ink_viscosity_actual", 27.0, t)
    store.set("coder.supply_voltage", 230.0, t)
    store.set("coder.ink_consumption_ml", 100.0, t)
    store.set("coder.nozzle_health", 95.0, t)
    store.set("coder.gutter_fault", 0.0, t)

    # Environment — MQTT
    store.set("environment.ambient_temp", 15.0, t)
    store.set("environment.ambient_humidity", 55.0, t)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def fnb_modbus_only() -> (  # type: ignore[override]
    tuple[AsyncModbusTcpClient, ModbusServer, SignalStore]
):
    """Start F&B Modbus server with pre-populated store; yield (client, server, store)."""
    config = load_config(_FNB_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = 42
    config.data_quality.exception_probability = 0.0
    config.data_quality.partial_modbus_response.probability = 0.0
    config.data_quality.modbus_drop.enabled = False

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    for _ in range(5):
        engine.tick()

    t = clock.sim_time
    _populate_fnb_store(store, t)

    server = ModbusServer(config, store, host=_HOST, port=_MODBUS_PORT)
    server.sync_registers()
    await server.start()
    await asyncio.sleep(0.3)  # Give server time to bind

    client = AsyncModbusTcpClient(_HOST, port=_MODBUS_PORT)
    await client.connect()
    assert client.connected, f"Failed to connect Modbus client on port {_MODBUS_PORT}"

    yield client, server, store

    client.close()
    await server.stop()


@pytest.fixture
async def fnb_modbus_opcua() -> (  # type: ignore[override]
    tuple[AsyncModbusTcpClient, OpcuaClient, ModbusServer, OpcuaServer, SignalStore]
):
    """Start F&B Modbus + OPC-UA servers; yield (modbus_client, opcua_client, modbus, opcua, store).
    """
    config = load_config(_FNB_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = 42
    config.data_quality.exception_probability = 0.0
    config.data_quality.partial_modbus_response.probability = 0.0
    config.data_quality.modbus_drop.enabled = False
    config.data_quality.opcua_stale.enabled = False

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    for _ in range(5):
        engine.tick()

    t = clock.sim_time
    _populate_fnb_store(store, t)

    modbus = ModbusServer(config, store, host=_HOST, port=_MODBUS_PORT)
    modbus.sync_registers()
    await modbus.start()

    opcua = OpcuaServer(config, store, host=_HOST, port=0)
    await opcua.start()
    await asyncio.sleep(0.8)  # let OPC-UA sync cycle run

    modbus_client = AsyncModbusTcpClient(_HOST, port=_MODBUS_PORT)
    await modbus_client.connect()
    assert modbus_client.connected, "Failed to connect Modbus client"

    opcua_port = opcua.actual_port
    assert opcua_port > 0, "OPC-UA server did not bind to a port"
    opcua_client = OpcuaClient(f"opc.tcp://{_HOST}:{opcua_port}/")
    await opcua_client.connect()

    yield modbus_client, opcua_client, modbus, opcua, store

    modbus_client.close()
    await opcua_client.disconnect()
    await opcua.stop()
    await modbus.stop()


class _MqttCollector:
    """Thread-safe MQTT message collector."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._lock = Lock()

    def on_message(
        self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage
    ) -> None:
        with self._lock:
            payload = json.loads(msg.payload.decode()) if msg.payload else {}
            self.messages.append({"topic": msg.topic, "payload": payload})

    def topics_received(self) -> set[str]:
        with self._lock:
            return {m["topic"] for m in self.messages}

    def get_messages(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.messages)


@pytest.fixture
async def fnb_all_protocols() -> (  # type: ignore[override]
    tuple[AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore]
):
    """Start all three F&B protocol adapters. Requires the MQTT broker."""
    config = load_config(_FNB_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = 42
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = 1.0
    config.data_quality.exception_probability = 0.0
    config.data_quality.partial_modbus_response.probability = 0.0
    config.data_quality.modbus_drop.enabled = False
    config.data_quality.opcua_stale.enabled = False
    config.data_quality.mqtt_drop.enabled = False

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    for _ in range(5):
        engine.tick()

    t = clock.sim_time
    _populate_fnb_store(store, t)

    modbus = ModbusServer(config, store, host=_HOST, port=_MODBUS_PORT)
    modbus.sync_registers()
    await modbus.start()

    opcua = OpcuaServer(config, store, host=_HOST, port=0)
    await opcua.start()

    # Subscribe before publisher starts to capture event-driven topics
    collector = _MqttCollector()
    cid = f"test-fnb-cross-{int(time.monotonic() * 1000) % 100000}"
    mqtt_sub = mqtt.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=cid,
        protocol=mqtt.MQTTv311,
    )
    mqtt_sub.on_message = collector.on_message
    mqtt_sub.connect(_BROKER_HOST, _BROKER_PORT, keepalive=60)
    mqtt_sub.loop_start()
    time.sleep(0.5)
    mqtt_sub.subscribe(f"{_TOPIC_PREFIX}/#", qos=1)
    time.sleep(0.3)

    publisher = MqttPublisher(config, store, host=_BROKER_HOST, port=_BROKER_PORT)
    await publisher.start()

    await asyncio.sleep(0.8)  # wait for OPC-UA sync + Modbus settle

    modbus_client = AsyncModbusTcpClient(_HOST, port=_MODBUS_PORT)
    await modbus_client.connect()
    assert modbus_client.connected, "Failed to connect Modbus client"

    opcua_port = opcua.actual_port
    assert opcua_port > 0
    opcua_client = OpcuaClient(f"opc.tcp://{_HOST}:{opcua_port}/")
    await opcua_client.connect()

    await asyncio.sleep(2.0)  # wait for timed MQTT topics to publish

    yield modbus_client, opcua_client, collector, store

    modbus_client.close()
    await opcua_client.disconnect()
    mqtt_sub.loop_stop()
    mqtt_sub.disconnect()
    await publisher.stop()
    await opcua.stop()
    await modbus.stop()


# ---------------------------------------------------------------------------
# Group 1: Modbus HR vs IR consistency
# ---------------------------------------------------------------------------


class TestFnbModbusHrVsIr:
    """Signals on both Modbus HR and IR return consistent values (no broker needed)."""

    async def test_oven_zone_temp_hr_matches_ir_int16(
        self, fnb_modbus_only: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore]
    ) -> None:
        """oven.zone_1_temp: HR 1100-1101 (float32 ABCD) == IR 100 (int16x10).

        The same store value is encoded two ways and served on two register
        types.  Both should agree within int16x10 precision (0.1 degrees C).
        """
        client, _, _ = fnb_modbus_only

        hr = await client.read_holding_registers(1100, count=2)
        assert not hr.isError(), f"HR 1100 read failed: {hr}"
        hr_val = decode_float32_abcd(hr.registers)

        ir = await client.read_input_registers(100, count=1)
        assert not ir.isError(), f"IR 100 read failed: {ir}"
        ir_val = decode_int16_x10(ir.registers[0])

        assert abs(hr_val - 160.0) < 0.1, f"HR zone_1_temp: {hr_val}, expected 160.0"
        assert abs(ir_val - 160.0) < 0.2, f"IR zone_1_temp: {ir_val}, expected 160.0"
        assert abs(hr_val - ir_val) < 0.2, (
            f"HR={hr_val} != IR={ir_val} for oven.zone_1_temp"
        )

    async def test_chiller_room_temp_hr_matches_ir_int16(
        self, fnb_modbus_only: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore]
    ) -> None:
        """chiller.room_temp: HR 1400-1401 (float32) == IR 110 (int16x10)."""
        client, _, _ = fnb_modbus_only

        hr = await client.read_holding_registers(1400, count=2)
        assert not hr.isError()
        hr_val = decode_float32_abcd(hr.registers)

        ir = await client.read_input_registers(110, count=1)
        assert not ir.isError()
        ir_val = decode_int16_x10(ir.registers[0])

        assert abs(hr_val - 2.5) < 0.1, f"HR room_temp: {hr_val}, expected 2.5"
        assert abs(ir_val - 2.5) < 0.2, f"IR room_temp: {ir_val}, expected 2.5"
        assert abs(hr_val - ir_val) < 0.2, f"HR={hr_val} != IR={ir_val} for room_temp"

    async def test_energy_line_power_hr_matches_ir_float32(
        self, fnb_modbus_only: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore]
    ) -> None:
        """energy.line_power: HR 600-601 (float32 ABCD) == IR 120-121 (float32 ABCD).

        The energy signal is served as float32 on BOTH HR and IR blocks.
        Both reads should decode to the same value within float32 precision.
        """
        client, _, _ = fnb_modbus_only

        hr = await client.read_holding_registers(600, count=2)
        assert not hr.isError()
        hr_val = decode_float32_abcd(hr.registers)

        ir = await client.read_input_registers(120, count=2)
        assert not ir.isError()
        ir_val = decode_float32_abcd(ir.registers)

        assert abs(hr_val - 180.0) < 0.1, f"HR line_power: {hr_val}"
        assert abs(ir_val - 180.0) < 0.1, f"IR line_power: {ir_val}"
        assert abs(hr_val - ir_val) < 0.01, f"HR={hr_val} != IR={ir_val}"


# ---------------------------------------------------------------------------
# Group 2: CDAB vs ABCD encoding
# ---------------------------------------------------------------------------


class TestFnbCdabVsAbcdEncoding:
    """Mixer CDAB word-swap is applied; ABCD decode gives wrong result."""

    async def test_mixer_speed_decoded_as_cdab_matches_store(
        self, fnb_modbus_only: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore]
    ) -> None:
        """mixer.speed at HR 1000-1001: CDAB decode gives injected value 450.0."""
        client, _, _ = fnb_modbus_only

        result = await client.read_holding_registers(1000, count=2)
        assert not result.isError()
        val = decode_float32_cdab(result.registers)

        assert abs(val - 450.0) < 0.1, f"CDAB mixer.speed: {val}, expected 450.0"

    async def test_mixer_speed_abcd_decode_gives_wrong_value(
        self, fnb_modbus_only: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore]
    ) -> None:
        """mixer.speed HR 1000-1001: ABCD decode gives wrong value (confirms word-swap)."""
        client, _, _ = fnb_modbus_only

        result = await client.read_holding_registers(1000, count=2)
        assert not result.isError()
        cdab_val = decode_float32_cdab(result.registers)
        abcd_val = decode_float32_abcd(result.registers)

        # CDAB correct; ABCD wrong for non-trivial floats
        assert abs(cdab_val - 450.0) < 0.1, f"CDAB should give 450.0, got {cdab_val}"
        assert abs(abcd_val - 450.0) > 1.0, (
            "ABCD should NOT give 450.0 for mixer.speed — CDAB word-swap expected"
        )
        assert abs(cdab_val - abcd_val) > 1.0, (
            f"CDAB ({cdab_val}) and ABCD ({abcd_val}) should differ for mixer.speed"
        )

    async def test_oven_zone_abcd_correct_cdab_wrong(
        self, fnb_modbus_only: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore]
    ) -> None:
        """oven.zone_1_temp HR 1100-1101: ABCD gives 160.0; CDAB gives wrong value.

        Oven (Eurotherm/Siemens) uses ABCD byte order — verifies the byte
        order is per-register, not a global switch.
        """
        client, _, _ = fnb_modbus_only

        result = await client.read_holding_registers(1100, count=2)
        assert not result.isError()
        abcd_val = decode_float32_abcd(result.registers)
        cdab_val = decode_float32_cdab(result.registers)

        assert abs(abcd_val - 160.0) < 0.1, (
            f"ABCD oven.zone_1_temp: {abcd_val}, expected 160.0"
        )
        assert abs(cdab_val - 160.0) > 1.0, (
            f"CDAB should NOT give 160.0 for oven.zone_1_temp: {cdab_val}"
        )


# ---------------------------------------------------------------------------
# Group 3: Modbus HR vs OPC-UA consistency
# ---------------------------------------------------------------------------


class TestFnbModbusOpcuaConsistency:
    """Signals on both Modbus HR and OPC-UA return consistent values (no broker needed)."""

    async def test_energy_line_power_modbus_matches_opcua(
        self,
        fnb_modbus_opcua: tuple[
            AsyncModbusTcpClient, OpcuaClient, ModbusServer, OpcuaServer, SignalStore
        ],
    ) -> None:
        """energy.line_power: HR 600-601 (ABCD float32) == FoodBevLine.Energy.LinePower."""
        modbus_client, opcua_client, _, _, _ = fnb_modbus_opcua

        hr = await modbus_client.read_holding_registers(600, count=2)
        assert not hr.isError()
        modbus_val = decode_float32_abcd(hr.registers)

        node = opcua_client.get_node(
            ua.NodeId("FoodBevLine.Energy.LinePower", NAMESPACE_INDEX)
        )
        opcua_val = float(await node.read_value())

        assert abs(modbus_val - _float32_roundtrip(opcua_val)) < 0.01, (
            f"energy.line_power: Modbus={modbus_val}, OPC-UA={opcua_val}"
        )
        assert abs(modbus_val - 180.0) < 0.1, f"Modbus line_power: {modbus_val}"
        assert abs(opcua_val - 180.0) < 0.01, f"OPC-UA line_power: {opcua_val}"

    async def test_energy_cumulative_kwh_modbus_matches_opcua(
        self,
        fnb_modbus_opcua: tuple[
            AsyncModbusTcpClient, OpcuaClient, ModbusServer, OpcuaServer, SignalStore
        ],
    ) -> None:
        """energy.cumulative_kwh: HR 602-603 == FoodBevLine.Energy.CumulativeKwh."""
        modbus_client, opcua_client, _, _, _ = fnb_modbus_opcua

        hr = await modbus_client.read_holding_registers(602, count=2)
        assert not hr.isError()
        modbus_val = decode_float32_abcd(hr.registers)

        node = opcua_client.get_node(
            ua.NodeId("FoodBevLine.Energy.CumulativeKwh", NAMESPACE_INDEX)
        )
        opcua_val = float(await node.read_value())

        assert abs(modbus_val - _float32_roundtrip(opcua_val)) < 1.0, (
            f"cumulative_kwh: Modbus={modbus_val}, OPC-UA={opcua_val}"
        )
        # Both reflect injected 9500.0 (float32 precision at large values ~±1)
        assert abs(modbus_val - 9500.0) < 2.0, f"Modbus cumulative_kwh: {modbus_val}"
        assert abs(opcua_val - 9500.0) < 0.01, f"OPC-UA cumulative_kwh: {opcua_val}"

    async def test_cdab_does_not_corrupt_opcua_mixer_state(
        self,
        fnb_modbus_opcua: tuple[
            AsyncModbusTcpClient, OpcuaClient, ModbusServer, OpcuaServer, SignalStore
        ],
    ) -> None:
        """CDAB encoding on mixer HR does not affect OPC-UA FoodBevLine.Mixer1.State.

        OPC-UA reads directly from the signal store — CDAB word-swapping is
        applied only in the Modbus sync path, so it must not corrupt the
        OPC-UA representation of mixer signals.
        """
        modbus_client, opcua_client, _, _, _ = fnb_modbus_opcua

        # OPC-UA: mixer.state injected as 2 (Mixing)
        node = opcua_client.get_node(
            ua.NodeId("FoodBevLine.Mixer1.State", NAMESPACE_INDEX)
        )
        opcua_state = int(await node.read_value())
        assert opcua_state == 2, (
            f"OPC-UA mixer.state should be 2 (Mixing), got {opcua_state}"
        )

        # Modbus CDAB mixer registers are active (word-swap visible)
        hr = await modbus_client.read_holding_registers(1000, count=2)
        assert not hr.isError()
        assert abs(decode_float32_cdab(hr.registers) - 450.0) < 0.1, (
            "CDAB mixer.speed should decode to 450.0"
        )

    async def test_store_change_propagates_to_modbus_and_opcua(
        self,
        fnb_modbus_opcua: tuple[
            AsyncModbusTcpClient, OpcuaClient, ModbusServer, OpcuaServer, SignalStore
        ],
    ) -> None:
        """A store change propagates to both Modbus HR and OPC-UA within one sync cycle."""
        modbus_client, opcua_client, modbus_server, _, store = fnb_modbus_opcua

        new_power = 222.5
        store.set("energy.line_power", new_power, 9999.0)
        modbus_server.sync_registers()
        await asyncio.sleep(0.7)  # OPC-UA sync interval is 500ms

        hr = await modbus_client.read_holding_registers(600, count=2)
        assert not hr.isError()
        modbus_val = decode_float32_abcd(hr.registers)

        node = opcua_client.get_node(
            ua.NodeId("FoodBevLine.Energy.LinePower", NAMESPACE_INDEX)
        )
        opcua_val = float(await node.read_value())

        assert abs(modbus_val - _float32_roundtrip(new_power)) < 0.01, (
            f"Modbus did not update: {modbus_val} != {new_power}"
        )
        assert abs(opcua_val - new_power) < 0.01, (
            f"OPC-UA did not update: {opcua_val} != {new_power}"
        )
        assert abs(modbus_val - _float32_roundtrip(opcua_val)) < 0.01, (
            f"Modbus={modbus_val} != OPC-UA={opcua_val} after store change"
        )


# ---------------------------------------------------------------------------
# Group 4: All three protocols simultaneously (requires MQTT broker)
# ---------------------------------------------------------------------------


@_needs_broker
class TestFnbAllThreeProtocols:
    """All three F&B protocol adapters serve from the same store without interference."""

    async def test_all_three_protocols_serve_data(
        self,
        fnb_all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """Modbus HR, OPC-UA, and MQTT all respond with F&B data."""
        modbus_client, opcua_client, collector, _ = fnb_all_protocols

        # Modbus: energy HR readable
        hr = await modbus_client.read_holding_registers(600, count=2)
        assert not hr.isError(), "Modbus failed to read F&B HR 600"
        modbus_val = decode_float32_abcd(hr.registers)
        assert 0.0 < modbus_val < 1000.0, f"energy.line_power out of range: {modbus_val}"

        # OPC-UA: FoodBevLine.Energy.LinePower readable
        node = opcua_client.get_node(
            ua.NodeId("FoodBevLine.Energy.LinePower", NAMESPACE_INDEX)
        )
        opcua_val = await node.read_value()
        assert opcua_val is not None, "OPC-UA returned None"

        # MQTT: F&B topics received, no packaging topics
        received = collector.topics_received()
        assert len(received) > 0, "No MQTT messages received on F&B topics"
        assert not any("packaging1" in t for t in received), (
            f"Unexpected packaging1 topics in F&B mode: "
            f"{[t for t in received if 'packaging1' in t]}"
        )

    async def test_mqtt_coder_value_matches_injected_store(
        self,
        fnb_all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """coder/ink_level MQTT value matches the injected store value (72.0)."""
        _, _, collector, _ = fnb_all_protocols

        target = f"{_TOPIC_PREFIX}/coder/ink_level"
        msgs = [m for m in collector.get_messages() if m["topic"] == target]
        assert msgs, f"No MQTT messages received for {target}"

        mqtt_val = float(msgs[-1]["payload"]["value"])
        assert abs(mqtt_val - 72.0) < 0.01, (
            f"coder/ink_level: MQTT={mqtt_val}, expected=72.0"
        )

    async def test_energy_consistent_across_modbus_and_opcua(
        self,
        fnb_all_protocols: tuple[
            AsyncModbusTcpClient, OpcuaClient, _MqttCollector, SignalStore
        ],
    ) -> None:
        """energy.line_power from Modbus HR matches OPC-UA Double for F&B profile."""
        modbus_client, opcua_client, _, _ = fnb_all_protocols

        hr = await modbus_client.read_holding_registers(600, count=2)
        assert not hr.isError()
        modbus_val = decode_float32_abcd(hr.registers)

        node = opcua_client.get_node(
            ua.NodeId("FoodBevLine.Energy.LinePower", NAMESPACE_INDEX)
        )
        opcua_val = float(await node.read_value())

        assert abs(modbus_val - _float32_roundtrip(opcua_val)) < 0.01, (
            f"energy.line_power: Modbus={modbus_val} != OPC-UA={opcua_val}"
        )


# ---------------------------------------------------------------------------
# Group 5: Ground truth log records F&B scenario events
# (No external services required)
# ---------------------------------------------------------------------------


class TestFnbGroundTruthScenarioEvents:
    """GroundTruthLogger records F&B scenario lifecycle events."""

    async def test_batch_cycle_events_recorded(self, tmp_path: Path) -> None:
        """BatchCycle scenario logs scenario_start and state_change events."""
        log_path = tmp_path / "gt.jsonl"

        config = load_config(_FNB_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 42

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        gt = GroundTruthLogger(log_path)
        gt.open()

        engine = DataEngine(config, store, clock, ground_truth=gt)
        for _ in range(5):
            engine.tick()

        # Add a batch cycle with start_time=0 so it activates on the next tick
        # (sim_time is already > 0 after warm-up).
        batch = BatchCycle(start_time=0.0, rng=np.random.default_rng(123))
        engine.scenario_engine.add_scenario(batch)

        for _ in range(20):
            engine.tick()

        gt.close()

        lines = log_path.read_text().splitlines()
        records = [json.loads(line) for line in lines if line.strip()]

        events = [r.get("event") for r in records if "event" in r]
        assert "scenario_start" in events, (
            f"Expected 'scenario_start' in events; got: {events}"
        )
        assert "state_change" in events, (
            f"Expected 'state_change' in events; got: {events}"
        )

        start_events = [r for r in records if r.get("event") == "scenario_start"]
        assert any(r.get("scenario") == "batch_cycle" for r in start_events), (
            f"No batch_cycle scenario_start event found: {start_events}"
        )

    async def test_ground_truth_events_have_required_fields(
        self, tmp_path: Path
    ) -> None:
        """All ground truth events contain 'event', 'sim_time', and scenario fields."""
        log_path = tmp_path / "gt2.jsonl"

        config = load_config(_FNB_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 42

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        gt = GroundTruthLogger(log_path)
        gt.open()

        engine = DataEngine(config, store, clock, ground_truth=gt)
        for _ in range(5):
            engine.tick()

        batch = BatchCycle(start_time=0.0, rng=np.random.default_rng(456))
        engine.scenario_engine.add_scenario(batch)

        for _ in range(20):
            engine.tick()

        gt.close()

        lines = log_path.read_text().splitlines()
        records = [json.loads(line) for line in lines if line.strip()]

        scenario_events = [
            r for r in records
            if r.get("event") in ("scenario_start", "state_change", "scenario_end")
        ]
        assert scenario_events, (
            "Expected at least some scenario events in the ground truth log"
        )
        for ev in scenario_events:
            assert "event" in ev, f"Missing 'event' field: {ev}"
            assert "sim_time" in ev, f"Missing 'sim_time' field: {ev}"
