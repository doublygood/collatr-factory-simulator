"""Integration tests for oven gateway UID routing in realistic mode (task 6a.5).

In realistic mode the oven gateway at port 5031 serves:
  UIDs 1, 2, 3  — Eurotherm zone controllers (IR 0 = PV, IR 1 = SP, IR 2 = output %)
  UID 10        — energy meter (primary context, IR 120-121 = energy.line_power float32)

In collapsed mode UIDs 11, 12, 13 are used for the secondary slaves (tested by
the existing test_modbus_fnb_integration.py — not duplicated here).

PRD Reference: PRD 03a Section 3a.2 (oven gateway topology), 3a.4 (F&B port table)
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path

import pytest
from pymodbus.client import AsyncModbusTcpClient

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.protocols.modbus_server import (
    ModbusServer,
    decode_float32_abcd,
    decode_int16_x10,
)
from factory_simulator.store import SignalStore
from factory_simulator.topology import ModbusEndpointSpec

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory-foodbev.yaml"

_TEST_PORT = 15540
_HOST = "127.0.0.1"

# Oven gateway endpoint spec matching realistic mode PRD 03a topology:
# UIDs 1/2/3 → Eurotherm zone controllers, UID 10 → energy meter.
# secondary_uid_remap remaps collapsed-mode slave IDs 11/12/13 → UIDs 1/2/3.
_OVEN_ENDPOINT = ModbusEndpointSpec(
    port=_TEST_PORT,
    unit_ids=[1, 2, 3, 10],
    byte_order="ABCD",
    controller_type="Eurotherm",
    controller_name="oven_gateway",
    equipment_ids=["oven", "energy"],
    uid_equipment_map={1: ["oven"], 2: ["oven"], 3: ["oven"], 10: ["energy"]},
    secondary_uid_remap={11: 1, 12: 2, 13: 3},
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def oven_realistic_system() -> (  # type: ignore[override]
    tuple[AsyncModbusTcpClient, ModbusServer, SignalStore]
):
    """Start a ModbusServer with the realistic oven gateway endpoint.

    All data quality injection is disabled so tests only verify register routing,
    not exception/drop behaviour.
    """
    config = load_config(_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = 42
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = 1.0

    # Disable injection (unseeded RNGs cause intermittent failures)
    config.data_quality.exception_probability = 0.0
    config.data_quality.partial_modbus_response.probability = 0.0
    config.data_quality.modbus_drop.enabled = False

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    for _ in range(5):
        engine.tick()

    t = clock.sim_time

    # Oven zone signals (visible via secondary slave IR at UIDs 1/2/3)
    store.set("oven.zone_1_temp", 160.0, t)
    store.set("oven.zone_1_setpoint", 160.0, t)
    store.set("oven.zone_1_output_power", 40.0, t)
    store.set("oven.zone_2_temp", 200.0, t)
    store.set("oven.zone_2_setpoint", 200.0, t)
    store.set("oven.zone_2_output_power", 30.0, t)
    store.set("oven.zone_3_temp", 180.0, t)
    store.set("oven.zone_3_setpoint", 180.0, t)
    store.set("oven.zone_3_output_power", 35.0, t)
    # Other oven HR signals needed for the primary context register map
    store.set("oven.belt_speed", 2.0, t)
    store.set("oven.product_core_temp", 72.0, t)
    store.set("oven.humidity_zone_2", 55.0, t)

    # Energy signals (visible via primary context IR at UID 10)
    store.set("energy.line_power", 42.0, t)
    store.set("energy.cumulative_kwh", 8500.0, t)

    server = ModbusServer(
        config,
        store,
        host=_HOST,
        port=_TEST_PORT,
        endpoint=_OVEN_ENDPOINT,
    )
    server.sync_registers()
    await server.start()
    await asyncio.sleep(0.3)

    client = AsyncModbusTcpClient(_HOST, port=_TEST_PORT)
    await client.connect()
    assert client.connected, f"Could not connect to oven gateway server on {_HOST}:{_TEST_PORT}"

    yield client, server, store

    client.close()
    await server.stop()


# ---------------------------------------------------------------------------
# UID 1 — Zone 1 Eurotherm controller (IR 0 = PV, IR 1 = SP, IR 2 = output)
# ---------------------------------------------------------------------------


class TestUid1Zone1EurothermIR:
    """Realistic mode: UID 1 routes to Eurotherm zone 1 secondary slave."""

    async def test_uid1_ir0_zone1_pv(
        self,
        oven_realistic_system: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore],
    ) -> None:
        """UID 1, IR 0: oven.zone_1_temp = 160.0 °C (int16 x10)."""
        client, *_ = oven_realistic_system
        result = await client.read_input_registers(0, count=1, device_id=1)
        assert not result.isError(), f"UID 1 IR 0 failed: {result}"
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 160.0) < 0.15, f"UID 1 IR 0={decoded}, expected ~160.0"

    async def test_uid1_ir1_zone1_sp(
        self,
        oven_realistic_system: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore],
    ) -> None:
        """UID 1, IR 1: oven.zone_1_setpoint = 160.0 °C (int16 x10)."""
        client, *_ = oven_realistic_system
        result = await client.read_input_registers(1, count=1, device_id=1)
        assert not result.isError(), f"UID 1 IR 1 failed: {result}"
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 160.0) < 0.15, f"UID 1 IR 1={decoded}, expected ~160.0"

    async def test_uid1_ir2_zone1_output_power(
        self,
        oven_realistic_system: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore],
    ) -> None:
        """UID 1, IR 2: oven.zone_1_output_power = 40.0 % (int16 x10)."""
        client, *_ = oven_realistic_system
        result = await client.read_input_registers(2, count=1, device_id=1)
        assert not result.isError(), f"UID 1 IR 2 failed: {result}"
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 40.0) < 0.15, f"UID 1 IR 2={decoded}, expected ~40.0"


# ---------------------------------------------------------------------------
# UID 2 — Zone 2 Eurotherm controller
# ---------------------------------------------------------------------------


class TestUid2Zone2EurothermIR:
    """Realistic mode: UID 2 routes to Eurotherm zone 2 secondary slave."""

    async def test_uid2_ir0_zone2_pv(
        self,
        oven_realistic_system: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore],
    ) -> None:
        """UID 2, IR 0: oven.zone_2_temp = 200.0 °C."""
        client, *_ = oven_realistic_system
        result = await client.read_input_registers(0, count=1, device_id=2)
        assert not result.isError(), f"UID 2 IR 0 failed: {result}"
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 200.0) < 0.15, f"UID 2 IR 0={decoded}, expected ~200.0"

    async def test_uid2_ir1_zone2_sp(
        self,
        oven_realistic_system: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore],
    ) -> None:
        """UID 2, IR 1: oven.zone_2_setpoint = 200.0 °C."""
        client, *_ = oven_realistic_system
        result = await client.read_input_registers(1, count=1, device_id=2)
        assert not result.isError(), f"UID 2 IR 1 failed: {result}"
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 200.0) < 0.15, f"UID 2 IR 1={decoded}, expected ~200.0"

    async def test_uid2_ir2_zone2_output_power(
        self,
        oven_realistic_system: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore],
    ) -> None:
        """UID 2, IR 2: oven.zone_2_output_power = 30.0 %."""
        client, *_ = oven_realistic_system
        result = await client.read_input_registers(2, count=1, device_id=2)
        assert not result.isError(), f"UID 2 IR 2 failed: {result}"
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 30.0) < 0.15, f"UID 2 IR 2={decoded}, expected ~30.0"


# ---------------------------------------------------------------------------
# UID 3 — Zone 3 Eurotherm controller
# ---------------------------------------------------------------------------


class TestUid3Zone3EurothermIR:
    """Realistic mode: UID 3 routes to Eurotherm zone 3 secondary slave."""

    async def test_uid3_ir0_zone3_pv(
        self,
        oven_realistic_system: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore],
    ) -> None:
        """UID 3, IR 0: oven.zone_3_temp = 180.0 °C."""
        client, *_ = oven_realistic_system
        result = await client.read_input_registers(0, count=1, device_id=3)
        assert not result.isError(), f"UID 3 IR 0 failed: {result}"
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 180.0) < 0.15, f"UID 3 IR 0={decoded}, expected ~180.0"

    async def test_uid3_ir1_zone3_sp(
        self,
        oven_realistic_system: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore],
    ) -> None:
        """UID 3, IR 1: oven.zone_3_setpoint = 180.0 °C."""
        client, *_ = oven_realistic_system
        result = await client.read_input_registers(1, count=1, device_id=3)
        assert not result.isError(), f"UID 3 IR 1 failed: {result}"
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 180.0) < 0.15, f"UID 3 IR 1={decoded}, expected ~180.0"

    async def test_uid3_ir2_zone3_output_power(
        self,
        oven_realistic_system: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore],
    ) -> None:
        """UID 3, IR 2: oven.zone_3_output_power = 35.0 %."""
        client, *_ = oven_realistic_system
        result = await client.read_input_registers(2, count=1, device_id=3)
        assert not result.isError(), f"UID 3 IR 2 failed: {result}"
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 35.0) < 0.15, f"UID 3 IR 2={decoded}, expected ~35.0"


# ---------------------------------------------------------------------------
# UID 10 — Energy meter (primary context)
# ---------------------------------------------------------------------------


class TestUid10EnergyMeterPrimaryContext:
    """Realistic mode: UID 10 routes to primary context (energy meter)."""

    async def test_uid10_energy_line_power_ir120(
        self,
        oven_realistic_system: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore],
    ) -> None:
        """UID 10, IR 120-121: energy.line_power = 42.0 kW (float32 ABCD)."""
        client, *_ = oven_realistic_system
        result = await client.read_input_registers(120, count=2, device_id=10)
        assert not result.isError(), f"UID 10 IR 120 failed: {result}"
        value = decode_float32_abcd(result.registers)
        assert not math.isnan(value), "energy.line_power is NaN at UID 10"
        assert abs(value - 42.0) < 0.1, f"UID 10 IR 120={value}, expected ~42.0"

    async def test_uid10_oven_zone_temp_ir100(
        self,
        oven_realistic_system: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore],
    ) -> None:
        """UID 10, IR 100: oven.zone_1_temp (int16 x10) readable via primary context."""
        client, *_ = oven_realistic_system
        result = await client.read_input_registers(100, count=1, device_id=10)
        assert not result.isError(), f"UID 10 IR 100 failed: {result}"
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 160.0) < 0.15, f"UID 10 IR 100={decoded}, expected ~160.0"


# ---------------------------------------------------------------------------
# Verify UIDs 1/2/3 do NOT route to the primary context
# (they should not serve IR 100+ which belongs to the primary context only)
# ---------------------------------------------------------------------------


class TestUidRoutingIsolation:
    """Verify zone UIDs (1/2/3) only serve secondary slave IR, not primary."""

    async def test_uid_1_does_not_serve_primary_ir100(
        self,
        oven_realistic_system: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore],
    ) -> None:
        """UID 1 should NOT serve the primary context's IR 100 block.

        Secondary slaves only serve IR 0, 1, 2.  Reading IR 100 at UID 1
        should either fail (out-of-bounds) or return an error response — it
        must NOT return oven.zone_1_temp decoded as int16_x10 from IR 100.
        """
        client, *_ = oven_realistic_system
        result = await client.read_input_registers(100, count=1, device_id=1)
        # Secondary slave's IR block only has 8 entries (block size 8).
        # Address 100 is beyond the block → returns error or zeros.
        # The key invariant: UID 1 must NOT be the primary context.
        # We accept either an error response or a return of 0 (out-of-range zeros).
        if result.isError():
            pass  # Expected: block too small → Modbus exception
        else:
            # If no error, it must be 0 (block padding), not 1600 (160.0 * 10)
            raw = result.registers[0]
            assert raw != 1600, (
                "UID 1 IR 100 returned 1600 (160.0°C encoded), meaning UID 1 "
                "is incorrectly routed to the primary context instead of the "
                "Eurotherm zone 1 secondary slave."
            )

    async def test_zone_pv_values_differ_per_uid(
        self,
        oven_realistic_system: tuple[AsyncModbusTcpClient, ModbusServer, SignalStore],
    ) -> None:
        """UIDs 1, 2, 3 return different IR 0 values (different zone temperatures).

        If all three UIDs routed to the same context, they'd all return the same
        value.  Different temperatures confirm each UID reaches a distinct slave.
        """
        client, *_ = oven_realistic_system
        zone_pvs: list[float] = []
        for uid in (1, 2, 3):
            result = await client.read_input_registers(0, count=1, device_id=uid)
            assert not result.isError(), f"UID {uid} IR 0 failed: {result}"
            zone_pvs.append(decode_int16_x10(result.registers[0]))

        # Zone 1=160, Zone 2=200, Zone 3=180 → all distinct
        assert len(set(zone_pvs)) == 3, (
            f"Expected 3 distinct zone PV values, got {zone_pvs}. "
            "All UIDs may be routing to the same context."
        )
