#!/usr/bin/env python3
"""Phase 3 Acceptance Test — Packaging + F&B profiles.

Programmatic smoke/acceptance test that spins up the DataEngine + all three
protocol servers (Modbus, OPC-UA, MQTT) for each profile, connects as a
client to every protocol, and verifies signal presence, value ranges,
cross-protocol consistency, F&B-specific features, and scenario ground truth.

Usage:
    python3 scripts/acceptance_test.py                     # both profiles
    python3 scripts/acceptance_test.py --profile packaging  # packaging only
    python3 scripts/acceptance_test.py --profile foodbev    # F&B only
    python3 scripts/acceptance_test.py --profile both       # both (default)
    python3 scripts/acceptance_test.py --ticks 50           # more engine ticks

Requirements:
    pip install -e ".[dev]"    (pymodbus, asyncua, paho-mqtt, numpy, pydantic)
    docker compose up -d       (Mosquitto broker — optional, MQTT tests skipped if unavailable)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import socket
import struct
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Any

# Ensure src is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import paho.mqtt.client as mqtt
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
    decode_uint32_abcd,
    decode_uint32_cdab,
)
from factory_simulator.protocols.mqtt_publisher import MqttPublisher
from factory_simulator.protocols.opcua_server import NAMESPACE_INDEX, OpcuaServer
from factory_simulator.store import SignalStore

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACKAGING_CONFIG = _REPO_ROOT / "config" / "factory.yaml"
_FOODBEV_CONFIG = _REPO_ROOT / "config" / "factory-foodbev.yaml"

_HOST = "127.0.0.1"
_BROKER_HOST = "127.0.0.1"
_BROKER_PORT = 1883

# Port assignments (unique per profile to avoid conflicts)
_PORTS = {
    "packaging": {"modbus": 15600, "opcua": 0},  # opcua=0 → OS-assigned
    "foodbev": {"modbus": 15610, "opcua": 0},
}

DEFAULT_TICKS = 30  # Engine ticks before protocol reads


# ---------------------------------------------------------------------------
# Signal definitions: (min, max, units)
# ---------------------------------------------------------------------------

PACKAGING_SIGNALS: dict[str, tuple[float, float, str]] = {
    # Press (21 signals)
    "press.line_speed": (0, 400, "m/min"),
    "press.web_tension": (0, 600, "N"),
    "press.registration_error_x": (-0.5, 0.5, "mm"),
    "press.registration_error_y": (-0.5, 0.5, "mm"),
    "press.ink_viscosity": (1, 65, "seconds"),
    "press.ink_temperature": (5, 50, "C"),
    "press.dryer_temp_zone_1": (10, 140, "C"),
    "press.dryer_temp_zone_2": (10, 140, "C"),
    "press.dryer_temp_zone_3": (10, 140, "C"),
    "press.dryer_setpoint_zone_1": (30, 130, "C"),
    "press.dryer_setpoint_zone_2": (30, 130, "C"),
    "press.dryer_setpoint_zone_3": (30, 130, "C"),
    "press.impression_count": (0, 1e10, "count"),
    "press.good_count": (0, 1e10, "count"),
    "press.waste_count": (0, 1e6, "count"),
    "press.machine_state": (0, 5, "enum"),
    "press.fault_code": (0, 1000, "code"),
    "press.main_drive_current": (0, 300, "A"),
    "press.main_drive_speed": (0, 4000, "RPM"),
    "press.nip_pressure": (0, 12, "bar"),
    "press.unwind_diameter": (0, 1600, "mm"),
    "press.rewind_diameter": (0, 1600, "mm"),
    # Laminator (5 signals)
    "laminator.nip_temp": (10, 110, "C"),
    "laminator.nip_pressure": (0, 10, "bar"),
    "laminator.tunnel_temp": (10, 130, "C"),
    "laminator.web_speed": (0, 400, "m/min"),
    "laminator.adhesive_weight": (0, 8, "g/m2"),
    # Slitter (3 signals)
    "slitter.speed": (0, 800, "m/min"),
    "slitter.web_tension": (0, 200, "N"),
    "slitter.reel_count": (0, 1e6, "count"),
    # Coder (11 signals)
    "coder.state": (0, 4, "enum"),
    "coder.prints_total": (0, 1e10, "count"),
    "coder.ink_level": (0, 100, "%"),
    "coder.printhead_temp": (15, 60, "C"),
    "coder.ink_pump_speed": (0, 600, "RPM"),
    "coder.ink_pressure": (0, 950, "mbar"),
    "coder.ink_viscosity_actual": (1, 18, "cP"),
    "coder.supply_voltage": (18, 30, "V"),
    "coder.ink_consumption_ml": (0, 1e6, "ml"),
    "coder.nozzle_health": (0, 100, "%"),
    "coder.gutter_fault": (0, 1, "bool"),
    # Environment (2 signals)
    "environment.ambient_temp": (5, 40, "C"),
    "environment.ambient_humidity": (20, 90, "%RH"),
    # Energy (2 signals)
    "energy.line_power": (0, 300, "kW"),
    "energy.cumulative_kwh": (0, 1e7, "kWh"),
    # Vibration (3 signals)
    "vibration.main_drive_x": (0, 50, "mm/s"),
    "vibration.main_drive_y": (0, 50, "mm/s"),
    "vibration.main_drive_z": (0, 50, "mm/s"),
}

FOODBEV_SIGNALS: dict[str, tuple[float, float, str]] = {
    # Mixer (8 signals)
    "mixer.speed": (0, 3000, "RPM"),
    "mixer.torque": (0, 100, "%"),
    "mixer.batch_temp": (-5, 95, "C"),
    "mixer.batch_weight": (0, 2000, "kg"),
    "mixer.state": (0, 5, "enum"),
    "mixer.batch_id": (0, 0, "string"),  # string signal, range ignored
    "mixer.mix_time_elapsed": (0, 3600, "s"),
    "mixer.lid_closed": (0, 1, "bool"),
    # Oven (13 signals)
    "oven.zone_1_temp": (15, 280, "C"),
    "oven.zone_2_temp": (15, 280, "C"),
    "oven.zone_3_temp": (15, 280, "C"),
    "oven.zone_1_setpoint": (80, 280, "C"),
    "oven.zone_2_setpoint": (80, 280, "C"),
    "oven.zone_3_setpoint": (80, 280, "C"),
    "oven.belt_speed": (0, 10, "m/min"),
    "oven.product_core_temp": (-5, 95, "C"),
    "oven.humidity_zone_2": (20, 95, "%RH"),
    "oven.state": (0, 4, "enum"),
    "oven.zone_1_output_power": (0, 100, "%"),
    "oven.zone_2_output_power": (0, 100, "%"),
    "oven.zone_3_output_power": (0, 100, "%"),
    # Filler (8 signals)
    "filler.line_speed": (0, 120, "packs/min"),
    "filler.fill_weight": (0, 1000, "g"),
    "filler.fill_target": (100, 800, "g"),
    "filler.fill_deviation": (-50, 50, "g"),
    "filler.packs_produced": (0, 1e8, "count"),
    "filler.reject_count": (0, 1e6, "count"),
    "filler.state": (0, 4, "enum"),
    "filler.hopper_level": (0, 100, "%"),
    # Sealer (6 signals)
    "sealer.seal_temp": (90, 260, "C"),
    "sealer.seal_pressure": (0, 10, "bar"),
    "sealer.seal_dwell": (0, 5.5, "s"),
    "sealer.gas_co2_pct": (0, 100, "%"),
    "sealer.gas_n2_pct": (0, 100, "%"),
    "sealer.vacuum_level": (-1, 0.1, "bar"),
    # QC (6 signals)
    "qc.actual_weight": (0, 1000, "g"),
    "qc.overweight_count": (0, 1e6, "count"),
    "qc.underweight_count": (0, 1e6, "count"),
    "qc.metal_detect_trips": (0, 1000, "count"),
    "qc.throughput": (0, 200, "items/min"),
    "qc.reject_total": (0, 1e6, "count"),
    # Chiller (7 signals)
    "chiller.room_temp": (-5, 25, "C"),
    "chiller.setpoint": (-5, 15, "C"),
    "chiller.compressor_state": (0, 1, "bool"),
    "chiller.suction_pressure": (0, 30, "bar"),
    "chiller.discharge_pressure": (0, 30, "bar"),
    "chiller.defrost_active": (0, 1, "bool"),
    "chiller.door_open": (0, 1, "bool"),
    # CIP (5 signals)
    "cip.state": (0, 5, "enum"),
    "cip.wash_temp": (10, 90, "C"),
    "cip.flow_rate": (0, 200, "L/min"),
    "cip.conductivity": (0, 200, "mS/cm"),
    "cip.cycle_time_elapsed": (0, 7200, "s"),
    # Coder (11 signals — shared with packaging)
    "coder.state": (0, 4, "enum"),
    "coder.prints_total": (0, 1e10, "count"),
    "coder.ink_level": (0, 100, "%"),
    "coder.printhead_temp": (15, 60, "C"),
    "coder.ink_pump_speed": (0, 600, "RPM"),
    "coder.ink_pressure": (0, 950, "mbar"),
    "coder.ink_viscosity_actual": (1, 18, "cP"),
    "coder.supply_voltage": (18, 30, "V"),
    "coder.ink_consumption_ml": (0, 1e6, "ml"),
    "coder.nozzle_health": (0, 100, "%"),
    "coder.gutter_fault": (0, 1, "bool"),
    # Environment (2 signals — shared)
    "environment.ambient_temp": (5, 40, "C"),
    "environment.ambient_humidity": (20, 90, "%RH"),
    # Energy (2 signals — shared)
    "energy.line_power": (0, 300, "kW"),
    "energy.cumulative_kwh": (0, 1e7, "kWh"),
}

# F&B Modbus holding register map: signal -> (address, data_type, byte_order)
FNB_MODBUS_HR: dict[str, tuple[int, str, str]] = {
    "mixer.speed": (1000, "float32", "CDAB"),
    "mixer.torque": (1002, "float32", "CDAB"),
    "mixer.batch_temp": (1004, "float32", "CDAB"),
    "mixer.batch_weight": (1006, "float32", "CDAB"),
    "mixer.mix_time_elapsed": (1010, "uint32", "CDAB"),
    "oven.zone_1_temp": (1100, "float32", "ABCD"),
    "oven.zone_2_temp": (1102, "float32", "ABCD"),
    "oven.zone_3_temp": (1104, "float32", "ABCD"),
    "oven.zone_1_setpoint": (1110, "float32", "ABCD"),
    "oven.zone_2_setpoint": (1112, "float32", "ABCD"),
    "oven.zone_3_setpoint": (1114, "float32", "ABCD"),
    "oven.belt_speed": (1120, "float32", "ABCD"),
    "oven.product_core_temp": (1122, "float32", "ABCD"),
    "oven.humidity_zone_2": (1124, "float32", "ABCD"),
    "filler.hopper_level": (1200, "float32", "ABCD"),
    "sealer.seal_temp": (1300, "float32", "ABCD"),
    "sealer.seal_pressure": (1302, "float32", "ABCD"),
    "sealer.seal_dwell": (1304, "float32", "ABCD"),
    "sealer.gas_co2_pct": (1306, "float32", "ABCD"),
    "sealer.gas_n2_pct": (1308, "float32", "ABCD"),
    "sealer.vacuum_level": (1310, "float32", "ABCD"),
    "chiller.room_temp": (1400, "float32", "ABCD"),
    "chiller.setpoint": (1402, "float32", "ABCD"),
    "chiller.suction_pressure": (1404, "float32", "ABCD"),
    "chiller.discharge_pressure": (1406, "float32", "ABCD"),
    "cip.wash_temp": (1500, "float32", "ABCD"),
    "cip.flow_rate": (1502, "float32", "ABCD"),
    "cip.conductivity": (1504, "float32", "ABCD"),
    "cip.cycle_time_elapsed": (1506, "uint32", "ABCD"),
    "energy.line_power": (600, "float32", "ABCD"),
    "energy.cumulative_kwh": (602, "float32", "ABCD"),
}

FNB_MODBUS_COILS: dict[str, int] = {
    "mixer.lid_closed": 100,
    "chiller.compressor_state": 101,
    "chiller.defrost_active": 102,
}

FNB_MODBUS_DI: dict[str, int] = {
    "chiller.door_open": 100,
}

# F&B OPC-UA nodes: signal -> (node_path, expected_type)
FNB_OPCUA_NODES: dict[str, tuple[str, str]] = {
    "mixer.state": ("FoodBevLine.Mixer1.State", "UInt16"),
    "mixer.batch_id": ("FoodBevLine.Mixer1.BatchId", "String"),
    "oven.state": ("FoodBevLine.Oven1.State", "UInt16"),
    "filler.line_speed": ("FoodBevLine.Filler1.LineSpeed", "Double"),
    "filler.fill_weight": ("FoodBevLine.Filler1.FillWeight", "Double"),
    "filler.fill_target": ("FoodBevLine.Filler1.FillTarget", "Double"),
    "filler.fill_deviation": ("FoodBevLine.Filler1.FillDeviation", "Double"),
    "filler.packs_produced": ("FoodBevLine.Filler1.PacksProduced", "UInt32"),
    "filler.reject_count": ("FoodBevLine.Filler1.RejectCount", "UInt32"),
    "filler.state": ("FoodBevLine.Filler1.State", "UInt16"),
    "qc.actual_weight": ("FoodBevLine.QC1.ActualWeight", "Double"),
    "qc.overweight_count": ("FoodBevLine.QC1.OverweightCount", "UInt32"),
    "qc.underweight_count": ("FoodBevLine.QC1.UnderweightCount", "UInt32"),
    "qc.metal_detect_trips": ("FoodBevLine.QC1.MetalDetectTrips", "UInt32"),
    "qc.throughput": ("FoodBevLine.QC1.Throughput", "Double"),
    "qc.reject_total": ("FoodBevLine.QC1.RejectTotal", "UInt32"),
    "cip.state": ("FoodBevLine.CIP1.State", "UInt16"),
    "energy.line_power": ("FoodBevLine.Energy.LinePower", "Double"),
    "energy.cumulative_kwh": ("FoodBevLine.Energy.CumulativeKwh", "Double"),
}

# F&B MQTT topics
FNB_MQTT_TOPICS: set[str] = {
    "coder/state", "coder/prints_total", "coder/ink_level",
    "coder/printhead_temp", "coder/ink_pump_speed", "coder/ink_pressure",
    "coder/ink_viscosity_actual", "coder/supply_voltage",
    "coder/ink_consumption_ml", "coder/nozzle_health", "coder/gutter_fault",
    "env/ambient_temp", "env/ambient_humidity",
}

# Multi-slave UIDs for Eurotherm oven zone controllers
EUROTHERM_UIDS = {11: "zone_1", 12: "zone_2", 13: "zone_3"}


# ---------------------------------------------------------------------------
# Test results tracker
# ---------------------------------------------------------------------------

class TestResults:
    """Accumulate pass/fail counts with messages."""

    def __init__(self) -> None:
        self.passes: int = 0
        self.fails: int = 0
        self.warnings: int = 0
        self.messages: list[str] = []

    def passed(self, msg: str) -> None:
        self.passes += 1
        self.messages.append(f"  PASS: {msg}")

    def failed(self, msg: str) -> None:
        self.fails += 1
        self.messages.append(f"  FAIL: {msg}")

    def warn(self, msg: str) -> None:
        self.warnings += 1
        self.messages.append(f"  WARN: {msg}")

    def section(self, title: str) -> None:
        self.messages.append(f"\n{'=' * 60}")
        self.messages.append(f"  {title}")
        self.messages.append(f"{'=' * 60}")

    def print_all(self) -> None:
        for msg in self.messages:
            print(msg)

    def summary(self) -> str:
        return f"{self.passes} passed, {self.fails} failed, {self.warnings} warnings"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _broker_reachable() -> bool:
    """Check if MQTT broker is reachable."""
    try:
        with socket.create_connection((_BROKER_HOST, _BROKER_PORT), timeout=2):
            return True
    except OSError:
        return False


class MqttCollector:
    """Thread-safe MQTT message collector."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._lock = Lock()

    def on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        with self._lock:
            try:
                payload = json.loads(msg.payload.decode()) if msg.payload else {}
            except Exception:
                payload = {}
            self.messages.append({"topic": msg.topic, "payload": payload})

    def topics_received(self) -> set[str]:
        with self._lock:
            return {m["topic"] for m in self.messages}

    def get_messages(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.messages)


def _decode_modbus_value(
    regs: list[int], data_type: str, byte_order: str
) -> float | int:
    """Decode Modbus registers to a numeric value."""
    if data_type == "float32":
        if byte_order == "CDAB":
            return decode_float32_cdab(regs)
        return decode_float32_abcd(regs)
    elif data_type == "uint32":
        if byte_order == "CDAB":
            return decode_uint32_cdab(regs)
        return decode_uint32_abcd(regs)
    elif data_type == "uint16":
        return regs[0]
    return 0


# ---------------------------------------------------------------------------
# Test suites
# ---------------------------------------------------------------------------

async def test_packaging_modbus(
    client: AsyncModbusTcpClient, store: SignalStore, results: TestResults
) -> None:
    """Test packaging profile Modbus registers."""
    results.section("Packaging — Modbus Holding Registers")

    # Key HR addresses: (addr, count, signal, data_type)
    hr_checks = [
        (100, 2, "press.line_speed", "float32"),
        (102, 2, "press.web_tension", "float32"),
        (110, 2, "press.ink_viscosity", "float32"),
        (120, 2, "press.dryer_temp_zone_1", "float32"),
        (200, 2, "press.impression_count", "uint32"),
        (210, 1, "press.machine_state", "uint16"),
        (300, 2, "press.main_drive_current", "float32"),
        (400, 2, "laminator.nip_temp", "float32"),
        (500, 2, "slitter.speed", "float32"),
        (600, 2, "energy.line_power", "float32"),
    ]

    for addr, count, signal, dtype in hr_checks:
        result = await client.read_holding_registers(addr, count=count)
        if result.isError():
            results.failed(f"HR {addr} ({signal}): read error: {result}")
        else:
            if dtype == "float32":
                val = decode_float32_abcd(result.registers)
            elif dtype == "uint32":
                val = decode_uint32_abcd(result.registers)
            else:
                val = result.registers[0]

            if math.isnan(val) if isinstance(val, float) else False:
                results.failed(f"HR {addr} ({signal}): NaN")
            else:
                results.passed(f"HR {addr} ({signal}) = {val}")

    results.section("Packaging — Modbus Input Registers")
    # IR 0-5: dryer/laminator int16x10 temps
    for ir_addr in range(6):
        result = await client.read_input_registers(ir_addr, count=1)
        if result.isError():
            results.failed(f"IR {ir_addr}: read error")
        else:
            results.passed(f"IR {ir_addr} = {decode_int16_x10(result.registers[0])}")


async def test_packaging_opcua(
    opcua_client: OpcuaClient, results: TestResults
) -> None:
    """Test packaging profile OPC-UA nodes."""
    results.section("Packaging — OPC-UA Nodes")

    nodes_to_check = [
        "PackagingLine.Press1.LineSpeed",
        "PackagingLine.Press1.WebTension",
        "PackagingLine.Press1.State",
        "PackagingLine.Press1.ImpressionCount",
        "PackagingLine.Press1.Registration.ErrorX",
        "PackagingLine.Press1.Ink.Viscosity",
        "PackagingLine.Press1.Dryer.Zone1.Temperature",
        "PackagingLine.Press1.MainDrive.Current",
        "PackagingLine.Laminator1.NipTemperature",
        "PackagingLine.Slitter1.Speed",
        "PackagingLine.Energy.LinePower",
        "PackagingLine.Energy.CumulativeKwh",
    ]

    for node_path in nodes_to_check:
        try:
            node = opcua_client.get_node(
                ua.NodeId(node_path, NAMESPACE_INDEX)
            )
            val = await node.read_value()
            if val is None:
                results.failed(f"OPC-UA {node_path}: None")
            else:
                results.passed(f"OPC-UA {node_path} = {val}")
        except Exception as exc:
            results.failed(f"OPC-UA {node_path}: {exc}")


async def test_packaging_mqtt(
    collector: MqttCollector, results: TestResults
) -> None:
    """Test packaging MQTT topics."""
    results.section("Packaging — MQTT Topics")

    expected_prefixes = {"coder/", "env/", "vibration/"}
    received = collector.topics_received()

    if not received:
        results.failed("No MQTT messages received")
        return

    # Check coder topics
    coder_topics = [t for t in received if "/coder/" in t]
    if len(coder_topics) >= 8:
        results.passed(f"Coder topics received: {len(coder_topics)}")
    else:
        results.failed(f"Expected ≥8 coder topics, got {len(coder_topics)}")

    # Check env topics
    env_topics = [t for t in received if "/env/" in t]
    if len(env_topics) >= 2:
        results.passed(f"Env topics received: {len(env_topics)}")
    else:
        results.failed(f"Expected ≥2 env topics, got {len(env_topics)}")

    # Check vibration topics (packaging only)
    vib_topics = [t for t in received if "/vibration/" in t]
    if vib_topics:
        results.passed(f"Vibration topics received: {len(vib_topics)}")
    else:
        results.warn("No vibration topics (may be disabled in config)")


async def test_fnb_modbus(
    client: AsyncModbusTcpClient, store: SignalStore, results: TestResults
) -> None:
    """Test F&B Modbus registers (HR, IR, coils, DI, multi-slave)."""
    results.section("F&B — Modbus Holding Registers")

    for signal, (addr, dtype, byte_order) in FNB_MODBUS_HR.items():
        count = 2 if dtype in ("float32", "uint32") else 1
        result = await client.read_holding_registers(addr, count=count)
        if result.isError():
            results.failed(f"HR {addr} ({signal}): read error")
            continue
        val = _decode_modbus_value(result.registers, dtype, byte_order)
        if isinstance(val, float) and math.isnan(val):
            results.failed(f"HR {addr} ({signal}): NaN")
        else:
            results.passed(f"HR {addr} ({signal}) = {val}")

    results.section("F&B — CDAB Encoding Verification")
    # Verify mixer uses CDAB by checking CDAB != ABCD decode
    result = await client.read_holding_registers(1000, count=2)
    if not result.isError():
        cdab_val = decode_float32_cdab(result.registers)
        abcd_val = decode_float32_abcd(result.registers)
        if abs(cdab_val) > 0.01:  # non-zero value
            if abs(cdab_val - abcd_val) > 0.1:
                results.passed(
                    f"CDAB word-swap active: CDAB={cdab_val:.2f}, ABCD={abcd_val:.2f}"
                )
            else:
                results.warn(
                    f"CDAB and ABCD decode similarly (value may be special): {cdab_val}"
                )
        else:
            results.warn(f"mixer.speed is ~0, cannot verify CDAB encoding")
    else:
        results.failed("Cannot read mixer.speed for CDAB verification")

    results.section("F&B — Modbus Coils")
    for signal, addr in FNB_MODBUS_COILS.items():
        result = await client.read_coils(addr, count=1)
        if result.isError():
            results.failed(f"Coil {addr} ({signal}): read error")
        else:
            results.passed(f"Coil {addr} ({signal}) = {result.bits[0]}")

    results.section("F&B — Modbus Discrete Inputs")
    for signal, addr in FNB_MODBUS_DI.items():
        result = await client.read_discrete_inputs(addr, count=1)
        if result.isError():
            results.failed(f"DI {addr} ({signal}): read error")
        else:
            results.passed(f"DI {addr} ({signal}) = {result.bits[0]}")

    results.section("F&B — Modbus Input Registers (main UID)")
    # F&B IR: oven temps, chiller temps, CIP temps, energy
    ir_checks = [
        (100, "oven.zone_1_temp"), (101, "oven.zone_2_temp"),
        (102, "oven.zone_3_temp"), (103, "oven.zone_1_setpoint"),
        (104, "oven.zone_2_setpoint"), (105, "oven.zone_3_setpoint"),
        (106, "oven.product_core_temp"), (110, "chiller.room_temp"),
        (111, "chiller.setpoint"), (115, "cip.wash_temp"),
    ]
    for ir_addr, signal in ir_checks:
        result = await client.read_input_registers(ir_addr, count=1)
        if result.isError():
            results.failed(f"IR {ir_addr} ({signal}): read error")
        else:
            val = decode_int16_x10(result.registers[0])
            results.passed(f"IR {ir_addr} ({signal}) = {val}")

    # Energy IR (float32 at IR 120-121)
    result = await client.read_input_registers(120, count=2)
    if not result.isError():
        val = decode_float32_abcd(result.registers)
        results.passed(f"IR 120-121 (energy.line_power) = {val}")
    else:
        results.failed("IR 120-121 (energy.line_power): read error")

    results.section("F&B — Multi-Slave Eurotherm UIDs 11-13")
    for uid, zone in EUROTHERM_UIDS.items():
        for ir_addr, label in [(0, "PV"), (1, "SP"), (2, "Output Power")]:
            result = await client.read_input_registers(
                ir_addr, count=1, slave=uid
            )
            if result.isError():
                results.failed(f"UID {uid} IR {ir_addr} ({zone} {label}): read error")
            else:
                val = decode_int16_x10(result.registers[0])
                results.passed(f"UID {uid} IR {ir_addr} ({zone} {label}) = {val}")


async def test_fnb_opcua(
    opcua_client: OpcuaClient, results: TestResults
) -> None:
    """Test F&B OPC-UA nodes."""
    results.section("F&B — OPC-UA Nodes")

    for signal, (node_path, expected_type) in FNB_OPCUA_NODES.items():
        try:
            node = opcua_client.get_node(
                ua.NodeId(node_path, NAMESPACE_INDEX)
            )
            val = await node.read_value()
            if val is None:
                results.failed(f"OPC-UA {node_path}: None")
            else:
                results.passed(f"OPC-UA {node_path} = {val}")
        except Exception as exc:
            results.failed(f"OPC-UA {node_path}: {exc}")

    # Check FoodBevLine folder exists
    results.section("F&B — OPC-UA Node Tree Structure")
    try:
        fl_node = opcua_client.get_node(
            ua.NodeId("FoodBevLine", NAMESPACE_INDEX)
        )
        children = await fl_node.get_children()
        child_names = {(await c.read_browse_name()).Name for c in children}
        expected = {"Mixer1", "Oven1", "Filler1", "QC1", "CIP1", "Energy"}
        missing = expected - child_names
        if not missing:
            results.passed(f"FoodBevLine has all equipment folders: {sorted(child_names)}")
        else:
            results.failed(f"FoodBevLine missing folders: {missing}")
    except Exception as exc:
        results.failed(f"FoodBevLine folder browse: {exc}")


async def test_fnb_mqtt(
    collector: MqttCollector, results: TestResults
) -> None:
    """Test F&B MQTT topics."""
    results.section("F&B — MQTT Topics")

    received = collector.topics_received()

    if not received:
        results.failed("No MQTT messages received")
        return

    # Check for foodbev1 prefix
    fnb_topics = [t for t in received if "foodbev1" in t]
    if fnb_topics:
        results.passed(f"F&B topics with foodbev1 prefix: {len(fnb_topics)}")
    else:
        results.failed("No topics with foodbev1 prefix")

    # Check no packaging1 topics
    pkg_topics = [t for t in received if "packaging1" in t]
    if not pkg_topics:
        results.passed("No packaging1 topics in F&B mode")
    else:
        results.failed(f"Unexpected packaging1 topics: {pkg_topics}")

    # Check no vibration topics
    vib_topics = [t for t in received if "vibration" in t]
    if not vib_topics:
        results.passed("No vibration topics in F&B mode (correct)")
    else:
        results.failed(f"Unexpected vibration topics in F&B: {vib_topics}")

    # Check coder + env topics present
    coder_topics = [t for t in fnb_topics if "/coder/" in t]
    env_topics = [t for t in fnb_topics if "/env/" in t]
    if len(coder_topics) >= 8:
        results.passed(f"F&B coder topics: {len(coder_topics)}")
    else:
        results.failed(f"Expected ≥8 F&B coder topics, got {len(coder_topics)}")
    if len(env_topics) >= 2:
        results.passed(f"F&B env topics: {len(env_topics)}")
    else:
        results.failed(f"Expected ≥2 F&B env topics, got {len(env_topics)}")


async def test_cross_protocol_consistency(
    modbus_client: AsyncModbusTcpClient,
    opcua_client: OpcuaClient,
    profile: str,
    results: TestResults,
) -> None:
    """Verify the same signal returns the same value from Modbus and OPC-UA."""
    results.section(f"{profile.title()} — Cross-Protocol Consistency (Modbus vs OPC-UA)")

    if profile == "foodbev":
        # energy.line_power: HR 600-601 vs FoodBevLine.Energy.LinePower
        hr = await modbus_client.read_holding_registers(600, count=2)
        if hr.isError():
            results.failed("Cannot read HR 600-601 for cross-protocol check")
            return
        modbus_val = decode_float32_abcd(hr.registers)

        try:
            node = opcua_client.get_node(
                ua.NodeId("FoodBevLine.Energy.LinePower", NAMESPACE_INDEX)
            )
            opcua_val = float(await node.read_value())
        except Exception as exc:
            results.failed(f"Cannot read OPC-UA energy.line_power: {exc}")
            return

        # Float32 roundtrip precision
        modbus_f32 = struct.unpack(">f", struct.pack(">f", modbus_val))[0]
        diff = abs(modbus_f32 - opcua_val)
        if diff < 0.1:
            results.passed(
                f"energy.line_power: Modbus={modbus_val:.2f}, OPC-UA={opcua_val:.2f} (diff={diff:.4f})"
            )
        else:
            results.failed(
                f"energy.line_power mismatch: Modbus={modbus_val:.2f}, OPC-UA={opcua_val:.2f}"
            )

        # energy.cumulative_kwh: HR 602-603 vs FoodBevLine.Energy.CumulativeKwh
        hr2 = await modbus_client.read_holding_registers(602, count=2)
        if not hr2.isError():
            modbus_kwh = decode_float32_abcd(hr2.registers)
            try:
                node2 = opcua_client.get_node(
                    ua.NodeId("FoodBevLine.Energy.CumulativeKwh", NAMESPACE_INDEX)
                )
                opcua_kwh = float(await node2.read_value())
                modbus_f32_kwh = struct.unpack(">f", struct.pack(">f", modbus_kwh))[0]
                diff2 = abs(modbus_f32_kwh - opcua_kwh)
                if diff2 < 1.0:  # float32 precision at large values
                    results.passed(
                        f"energy.cumulative_kwh: Modbus={modbus_kwh:.1f}, OPC-UA={opcua_kwh:.1f}"
                    )
                else:
                    results.failed(
                        f"energy.cumulative_kwh mismatch: Modbus={modbus_kwh:.1f}, OPC-UA={opcua_kwh:.1f}"
                    )
            except Exception as exc:
                results.failed(f"OPC-UA cumulative_kwh: {exc}")

    elif profile == "packaging":
        # energy.line_power: HR 600-601 vs PackagingLine.Energy.LinePower
        hr = await modbus_client.read_holding_registers(600, count=2)
        if hr.isError():
            results.failed("Cannot read HR 600-601 for cross-protocol check")
            return
        modbus_val = decode_float32_abcd(hr.registers)

        try:
            node = opcua_client.get_node(
                ua.NodeId("PackagingLine.Energy.LinePower", NAMESPACE_INDEX)
            )
            opcua_val = float(await node.read_value())
        except Exception as exc:
            results.failed(f"Cannot read OPC-UA energy.line_power: {exc}")
            return

        modbus_f32 = struct.unpack(">f", struct.pack(">f", modbus_val))[0]
        diff = abs(modbus_f32 - opcua_val)
        if diff < 0.1:
            results.passed(
                f"energy.line_power: Modbus={modbus_val:.2f}, OPC-UA={opcua_val:.2f}"
            )
        else:
            results.failed(
                f"energy.line_power mismatch: Modbus={modbus_val:.2f}, OPC-UA={opcua_val:.2f}"
            )


async def test_fnb_specific_features(
    modbus_client: AsyncModbusTcpClient,
    opcua_client: OpcuaClient,
    store: SignalStore,
    results: TestResults,
) -> None:
    """Test F&B-specific features: CDAB, multi-slave, per-item filler, BatchId."""
    results.section("F&B — Specific Feature Checks")

    # 1. BatchId is a string in OPC-UA
    try:
        node = opcua_client.get_node(
            ua.NodeId("FoodBevLine.Mixer1.BatchId", NAMESPACE_INDEX)
        )
        val = await node.read_value()
        if isinstance(val, str):
            results.passed(f"BatchId is a string: '{val}'")
        else:
            results.failed(f"BatchId is not a string: type={type(val)}, val={val}")
    except Exception as exc:
        results.failed(f"BatchId read: {exc}")

    # 2. Per-item filler generation: fill_weight has non-zero value
    try:
        node = opcua_client.get_node(
            ua.NodeId("FoodBevLine.Filler1.FillWeight", NAMESPACE_INDEX)
        )
        fill_wt = float(await node.read_value())
        node2 = opcua_client.get_node(
            ua.NodeId("FoodBevLine.Filler1.FillTarget", NAMESPACE_INDEX)
        )
        fill_tgt = float(await node2.read_value())
        if fill_wt > 0:
            results.passed(f"Filler fill_weight={fill_wt:.1f}g, target={fill_tgt:.1f}g")
        else:
            results.warn(f"Filler fill_weight is 0 (equipment may be off)")
    except Exception as exc:
        results.failed(f"Filler weight read: {exc}")

    # 3. Verify all 3 multi-slave UIDs respond
    for uid in [11, 12, 13]:
        result = await modbus_client.read_input_registers(0, count=1, slave=uid)
        if not result.isError():
            results.passed(f"Multi-slave UID {uid} responds (zone PV = {decode_int16_x10(result.registers[0])})")
        else:
            results.failed(f"Multi-slave UID {uid} does not respond")


async def test_signal_ranges(
    store: SignalStore,
    signal_defs: dict[str, tuple[float, float, str]],
    profile_name: str,
    results: TestResults,
) -> None:
    """Verify all signals in the store are within expected ranges."""
    results.section(f"{profile_name} — Signal Value Ranges")

    in_range = 0
    out_of_range = 0
    missing = 0

    for signal_id, (vmin, vmax, units) in signal_defs.items():
        sv = store.get(signal_id)
        if sv is None:
            missing += 1
            results.failed(f"{signal_id}: not in store")
            continue

        val = sv.value
        if isinstance(val, str):
            # String signals (e.g. batch_id) — just check presence
            results.passed(f"{signal_id} = '{val}' (string)")
            in_range += 1
            continue

        if math.isnan(val) or math.isinf(val):
            results.failed(f"{signal_id}: NaN/Inf")
            out_of_range += 1
            continue

        if vmin <= val <= vmax:
            in_range += 1
        else:
            results.failed(f"{signal_id} = {val} outside [{vmin}, {vmax}] {units}")
            out_of_range += 1

    if out_of_range == 0 and missing == 0:
        results.passed(f"All {in_range} signals within expected ranges")
    else:
        if missing > 0:
            results.failed(f"{missing} signals missing from store")
        if out_of_range > 0:
            results.failed(f"{out_of_range} signals out of range")


def test_ground_truth(gt_path: Path, profile_name: str, results: TestResults) -> None:
    """Check ground truth log for scenario events."""
    results.section(f"{profile_name} — Ground Truth Events")

    if not gt_path.exists():
        results.failed(f"Ground truth file not found: {gt_path}")
        return

    lines = gt_path.read_text().splitlines()
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    if not records:
        results.failed("Ground truth file is empty")
        return

    # Count event types
    events = [r.get("event") for r in records if "event" in r]
    scenarios = [r.get("scenario") for r in records if r.get("event") == "scenario_start"]

    results.passed(f"Ground truth log: {len(records)} records, {len(events)} events")

    if "scenario_start" in events:
        results.passed(f"scenario_start events found ({len(scenarios)})")
    else:
        results.warn("No scenario_start events (may need more ticks)")

    if "state_change" in events:
        state_changes = [r for r in records if r.get("event") == "state_change"]
        results.passed(f"state_change events: {len(state_changes)}")
    else:
        results.warn("No state_change events")


# ---------------------------------------------------------------------------
# Profile test runners
# ---------------------------------------------------------------------------

async def run_profile_test(
    profile: str,
    config_path: Path,
    num_ticks: int,
    results: TestResults,
) -> None:
    """Run the full test suite for one profile."""
    mqtt_available = _broker_reachable()
    modbus_port = _PORTS[profile]["modbus"]
    topic_prefix = (
        "collatr/factory/demo/packaging1"
        if profile == "packaging"
        else "collatr/factory/demo/foodbev1"
    )

    results.section(f"PROFILE: {profile.upper()}")
    print(f"\n>>> Testing profile: {profile}")
    print(f"    Config: {config_path}")
    print(f"    Modbus port: {modbus_port}")
    print(f"    MQTT broker: {'available' if mqtt_available else 'NOT available (MQTT tests skipped)'}")

    # --- Setup: engine + servers ---
    config = load_config(config_path, apply_env=False)
    config.simulation.random_seed = 42
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = 1.0

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)

    gt_path = Path(f"/tmp/acceptance-test-{profile}-gt.jsonl")
    gt = GroundTruthLogger(gt_path)
    gt.open()

    engine = DataEngine(config, store, clock, ground_truth=gt)

    # Tick engine to populate signals
    print(f"    Running {num_ticks} engine ticks...")
    for _ in range(num_ticks):
        engine.tick()

    # Start Modbus server
    modbus_server = ModbusServer(config, store, host=_HOST, port=modbus_port)
    modbus_server.sync_registers()
    await modbus_server.start()
    await asyncio.sleep(0.3)

    # Start OPC-UA server
    opcua_server = OpcuaServer(config, store, host=_HOST, port=0)
    await opcua_server.start()
    await asyncio.sleep(0.8)

    opcua_port = opcua_server.actual_port

    # Start MQTT publisher (if broker available)
    mqtt_publisher = None
    mqtt_collector = None
    mqtt_sub = None

    if mqtt_available:
        mqtt_collector = MqttCollector()
        cid = f"acceptance-{profile}-{int(time.monotonic() * 1000) % 100000}"
        mqtt_sub = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=cid,
            protocol=mqtt.MQTTv311,
        )
        mqtt_sub.on_message = mqtt_collector.on_message
        mqtt_sub.connect(_BROKER_HOST, _BROKER_PORT, keepalive=60)
        mqtt_sub.loop_start()
        time.sleep(0.5)
        mqtt_sub.subscribe(f"{topic_prefix}/#", qos=1)
        time.sleep(0.3)

        mqtt_publisher = MqttPublisher(
            config, store, host=_BROKER_HOST, port=_BROKER_PORT
        )
        await mqtt_publisher.start()
        await asyncio.sleep(3.0)  # Let timed MQTT topics publish

    # Connect Modbus client
    modbus_client = AsyncModbusTcpClient(_HOST, port=modbus_port)
    await modbus_client.connect()
    assert modbus_client.connected, f"Modbus client failed to connect on port {modbus_port}"

    # Connect OPC-UA client
    opcua_client = OpcuaClient(f"opc.tcp://{_HOST}:{opcua_port}/")
    await opcua_client.connect()

    # --- Run tests ---
    try:
        # Signal ranges
        signal_defs = PACKAGING_SIGNALS if profile == "packaging" else FOODBEV_SIGNALS
        await test_signal_ranges(store, signal_defs, profile.title(), results)

        # Protocol-specific tests
        if profile == "packaging":
            await test_packaging_modbus(modbus_client, store, results)
            await test_packaging_opcua(opcua_client, results)
            if mqtt_available and mqtt_collector:
                await test_packaging_mqtt(mqtt_collector, results)
            else:
                results.warn("MQTT tests skipped (broker not available)")
        else:
            await test_fnb_modbus(modbus_client, store, results)
            await test_fnb_opcua(opcua_client, results)
            if mqtt_available and mqtt_collector:
                await test_fnb_mqtt(mqtt_collector, results)
            else:
                results.warn("MQTT tests skipped (broker not available)")
            await test_fnb_specific_features(modbus_client, opcua_client, store, results)

        # Cross-protocol consistency
        await test_cross_protocol_consistency(modbus_client, opcua_client, profile, results)

        # Ground truth
        gt.close()
        test_ground_truth(gt_path, profile.title(), results)

    finally:
        # --- Cleanup ---
        modbus_client.close()
        await opcua_client.disconnect()

        if mqtt_sub:
            mqtt_sub.loop_stop()
            mqtt_sub.disconnect()
        if mqtt_publisher:
            await mqtt_publisher.stop()

        await opcua_server.stop()
        await modbus_server.stop()

    print(f"    Profile {profile} complete.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def async_main(profiles: list[str], num_ticks: int) -> int:
    """Run acceptance tests for the specified profiles."""
    results = TestResults()

    print("=" * 60)
    print("  Collatr Factory Simulator — Phase 3 Acceptance Test")
    print("=" * 60)

    for profile in profiles:
        config_path = _PACKAGING_CONFIG if profile == "packaging" else _FOODBEV_CONFIG
        if not config_path.exists():
            results.failed(f"Config not found: {config_path}")
            continue
        await run_profile_test(profile, config_path, num_ticks, results)

    # --- Final summary ---
    results.section("FINAL RESULTS")
    results.print_all()

    print("\n" + "=" * 60)
    print(f"  {results.summary()}")
    print("=" * 60)

    if results.fails > 0:
        print("\n  VERDICT: FAIL")
        return 1
    else:
        print("\n  VERDICT: PASS")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 3 Acceptance Test for Collatr Factory Simulator"
    )
    parser.add_argument(
        "--profile",
        choices=["packaging", "foodbev", "both"],
        default="both",
        help="Which profile(s) to test (default: both)",
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=DEFAULT_TICKS,
        help=f"Number of engine ticks to run before testing (default: {DEFAULT_TICKS})",
    )
    args = parser.parse_args()

    if args.profile == "both":
        profiles = ["packaging", "foodbev"]
    else:
        profiles = [args.profile]

    return asyncio.run(async_main(profiles, args.ticks))


if __name__ == "__main__":
    sys.exit(main())
