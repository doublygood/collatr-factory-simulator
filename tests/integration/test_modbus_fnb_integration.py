"""Integration tests for the Modbus TCP server — F&B profile.

Starts the DataEngine + ModbusServer with factory-foodbev.yaml, connects a
real pymodbus client, and verifies all F&B register addresses from PRD
Appendix A including:

- Mixer CDAB HR (1000-1011, Allen-Bradley CompactLogix)
- Oven ABCD HR (1100-1125) with writable setpoints
- Filler HR (1200-1201, hopper_level only)
- Sealer HR (1300-1311)
- Chiller HR (1400-1407)
- CIP HR (1500-1507)
- Shared energy HR (600-603)
- F&B IR (100-121): oven/chiller/CIP temps + energy
- F&B coils (100-102): mixer.lid_closed, chiller.compressor_state, defrost_active
- F&B DI (100): chiller.door_open
- Multi-slave UIDs 11-13 IR: oven zone PV/SP/output_power

PRD Reference: Section 3.1, Appendix A (F&B Register Map)
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
    decode_float32_cdab,
    decode_int16_x10,
    decode_uint32_abcd,
    decode_uint32_cdab,
    encode_float32_abcd,
)
from factory_simulator.store import SignalStore

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory-foodbev.yaml"

_TEST_PORT = 15503
_HOST = "127.0.0.1"

# Secondary slave UIDs for Eurotherm oven zone controllers (PRD 3.1.6)
_EUROTHERM_UIDS = {
    "zone_1": 11,
    "zone_2": 12,
    "zone_3": 13,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def fnb_modbus_system() -> (  # type: ignore[override]
    tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore]
):
    """Start engine + Modbus server with F&B config, yield connected client."""
    config = load_config(_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = 42
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = 1.0

    # Disable all data quality injection: these tests check register encoding,
    # not exception/drop injection.  Exception injection uses an unseeded RNG
    # (no exception_rng passed to ModbusServer) so non-zero probability causes
    # intermittent ExceptionResponse failures.
    config.data_quality.exception_probability = 0.0
    config.data_quality.partial_modbus_response.probability = 0.0
    config.data_quality.modbus_drop.enabled = False

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)

    # Tick engine to populate all F&B signal IDs in the store
    for _ in range(5):
        engine.tick()

    # Set known test values for each F&B Modbus-accessible signal.
    # This isolates the Modbus protocol layer from engine behaviour.
    t = clock.sim_time

    # Mixer — CDAB registers HR 1000-1011
    store.set("mixer.speed", 450.0, t)
    store.set("mixer.torque", 35.0, t)
    store.set("mixer.batch_temp", 65.0, t)
    store.set("mixer.batch_weight", 500.0, t)
    store.set("mixer.mix_time_elapsed", 600.0, t)   # 10 min elapsed
    store.set("mixer.lid_closed", 1.0, t)            # coil 100 = True

    # Oven — ABCD registers HR 1100-1125; also secondary slaves
    store.set("oven.zone_1_temp", 160.0, t)
    store.set("oven.zone_2_temp", 200.0, t)
    store.set("oven.zone_3_temp", 180.0, t)
    store.set("oven.zone_1_setpoint", 160.0, t)
    store.set("oven.zone_2_setpoint", 200.0, t)
    store.set("oven.zone_3_setpoint", 180.0, t)
    store.set("oven.belt_speed", 2.0, t)
    store.set("oven.product_core_temp", 72.0, t)
    store.set("oven.humidity_zone_2", 55.0, t)
    # Output powers: exclusive to secondary slaves (no HR)
    store.set("oven.zone_1_output_power", 40.0, t)
    store.set("oven.zone_2_output_power", 30.0, t)
    store.set("oven.zone_3_output_power", 35.0, t)

    # Filler — HR 1200-1201 (hopper_level only)
    store.set("filler.hopper_level", 75.0, t)

    # Sealer — HR 1300-1311
    store.set("sealer.seal_temp", 180.0, t)
    store.set("sealer.seal_pressure", 3.5, t)
    store.set("sealer.seal_dwell", 2.0, t)
    store.set("sealer.gas_co2_pct", 30.0, t)
    store.set("sealer.gas_n2_pct", 70.0, t)
    store.set("sealer.vacuum_level", -0.7, t)

    # Chiller — HR 1400-1407; coils 101-102; DI 100; IR 110-111
    store.set("chiller.room_temp", 2.5, t)
    store.set("chiller.setpoint", 2.0, t)
    store.set("chiller.suction_pressure", 3.0, t)
    store.set("chiller.discharge_pressure", 16.0, t)
    store.set("chiller.compressor_state", 1.0, t)    # coil 101 = True
    store.set("chiller.defrost_active", 0.0, t)      # coil 102 = False
    store.set("chiller.door_open", 0.0, t)           # DI 100 = False

    # CIP — HR 1500-1507; IR 115
    store.set("cip.wash_temp", 20.0, t)
    store.set("cip.flow_rate", 0.0, t)
    store.set("cip.conductivity", 0.0, t)
    store.set("cip.cycle_time_elapsed", 0.0, t)

    # Shared energy — HR 600-603; IR 120-121
    store.set("energy.line_power", 42.0, t)
    store.set("energy.cumulative_kwh", 8500.0, t)

    # Create and start Modbus server
    server = ModbusServer(config, store, host=_HOST, port=_TEST_PORT)
    server.sync_registers()
    await server.start()
    await asyncio.sleep(0.3)  # Give server time to bind

    # Connect client (to primary unit — UID 1)
    client = AsyncModbusTcpClient(_HOST, port=_TEST_PORT)
    await client.connect()
    assert client.connected, f"Failed to connect to F&B Modbus server on {_HOST}:{_TEST_PORT}"

    yield client, engine, server, store

    # Cleanup
    client.close()
    await server.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _f32_abcd(regs: list[int]) -> float:
    return decode_float32_abcd(regs)


def _f32_cdab(regs: list[int]) -> float:
    return decode_float32_cdab(regs)


def _u32_abcd(regs: list[int]) -> int:
    return decode_uint32_abcd(regs)


def _u32_cdab(regs: list[int]) -> int:
    return decode_uint32_cdab(regs)


# ---------------------------------------------------------------------------
# Mixer holding registers (CDAB)
# ---------------------------------------------------------------------------


class TestMixerHoldingRegistersCdab:
    """Verify Allen-Bradley CDAB mixer holding registers HR 1000-1011."""

    async def test_mixer_speed_hr_1000(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1000-1001: mixer.speed (float32 CDAB)."""
        client, _, _, _store = fnb_modbus_system
        result = await client.read_holding_registers(1000, count=2)
        assert not result.isError(), f"HR 1000-1001 failed: {result}"
        value = _f32_cdab(result.registers)
        assert not math.isnan(value), "mixer.speed is NaN"
        assert abs(value - 450.0) < 0.1, f"mixer.speed={value}, expected ~450.0"

    async def test_mixer_torque_hr_1002(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1002-1003: mixer.torque (float32 CDAB)."""
        client, *_ = fnb_modbus_system
        result = await client.read_holding_registers(1002, count=2)
        assert not result.isError()
        value = _f32_cdab(result.registers)
        assert not math.isnan(value)
        assert abs(value - 35.0) < 0.1, f"mixer.torque={value}, expected ~35.0"

    async def test_mixer_batch_temp_hr_1004(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1004-1005: mixer.batch_temp (float32 CDAB)."""
        client, *_ = fnb_modbus_system
        result = await client.read_holding_registers(1004, count=2)
        assert not result.isError()
        value = _f32_cdab(result.registers)
        assert not math.isnan(value)
        assert abs(value - 65.0) < 0.1, f"mixer.batch_temp={value}, expected ~65.0"

    async def test_mixer_batch_weight_hr_1006(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1006-1007: mixer.batch_weight (float32 CDAB)."""
        client, *_ = fnb_modbus_system
        result = await client.read_holding_registers(1006, count=2)
        assert not result.isError()
        value = _f32_cdab(result.registers)
        assert not math.isnan(value)
        assert abs(value - 500.0) < 0.1, f"mixer.batch_weight={value}, expected ~500.0"

    async def test_mixer_mix_time_hr_1010(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1010-1011: mixer.mix_time_elapsed (uint32 CDAB)."""
        client, *_ = fnb_modbus_system
        result = await client.read_holding_registers(1010, count=2)
        assert not result.isError()
        value = _u32_cdab(result.registers)
        assert value == 600, f"mix_time_elapsed={value}, expected 600"

    async def test_cdab_differs_from_abcd(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """CDAB raw registers differ from ABCD for non-zero values.

        This verifies word-swap is actually applied (not just ABCD encoding).
        """
        client, _, _, _store = fnb_modbus_system
        # mixer.speed=450.0 — raw registers should decode differently for ABCD vs CDAB
        result = await client.read_holding_registers(1000, count=2)
        assert not result.isError()
        cdab_value = _f32_cdab(result.registers)
        abcd_value = _f32_abcd(result.registers)
        # CDAB decodes to correct value; ABCD decodes to something else
        assert abs(cdab_value - 450.0) < 0.1, f"CDAB decode={cdab_value}"
        # The two decodings should differ (word-swap produces different register layout)
        assert abs(abcd_value - cdab_value) > 1.0, (
            "ABCD and CDAB decoded the same value — word-swap not applied"
        )


# ---------------------------------------------------------------------------
# Oven holding registers (ABCD)
# ---------------------------------------------------------------------------


class TestOvenHoldingRegistersAbcd:
    """Verify Eurotherm oven HR 1100-1125 (float32 ABCD)."""

    async def test_oven_zone_temps(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1100-1105: oven zone temperatures (float32 ABCD)."""
        client, *_ = fnb_modbus_system
        expected = {1100: 160.0, 1102: 200.0, 1104: 180.0}
        for addr, target in expected.items():
            result = await client.read_holding_registers(addr, count=2)
            assert not result.isError(), f"HR {addr} failed"
            value = _f32_abcd(result.registers)
            assert not math.isnan(value)
            assert abs(value - target) < 0.1, f"HR {addr}={value}, expected ~{target}"

    async def test_oven_zone_setpoints(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1110-1115: oven zone setpoints (float32 ABCD, writable)."""
        client, *_ = fnb_modbus_system
        expected = {1110: 160.0, 1112: 200.0, 1114: 180.0}
        for addr, target in expected.items():
            result = await client.read_holding_registers(addr, count=2)
            assert not result.isError(), f"HR {addr} failed"
            value = _f32_abcd(result.registers)
            assert abs(value - target) < 0.1, f"HR {addr}={value}, expected ~{target}"

    async def test_oven_belt_speed_hr_1120(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1120-1121: oven.belt_speed (float32 ABCD)."""
        client, *_ = fnb_modbus_system
        result = await client.read_holding_registers(1120, count=2)
        assert not result.isError()
        value = _f32_abcd(result.registers)
        assert abs(value - 2.0) < 0.1, f"belt_speed={value}"

    async def test_oven_product_core_temp_hr_1122(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1122-1123: oven.product_core_temp (float32 ABCD)."""
        client, *_ = fnb_modbus_system
        result = await client.read_holding_registers(1122, count=2)
        assert not result.isError()
        value = _f32_abcd(result.registers)
        assert abs(value - 72.0) < 0.1, f"product_core_temp={value}"

    async def test_oven_humidity_hr_1124(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1124-1125: oven.humidity_zone_2 (float32 ABCD)."""
        client, *_ = fnb_modbus_system
        result = await client.read_holding_registers(1124, count=2)
        assert not result.isError()
        value = _f32_abcd(result.registers)
        assert 30.0 <= value <= 90.0, f"humidity={value} out of range"


# ---------------------------------------------------------------------------
# Remaining F&B equipment HR
# ---------------------------------------------------------------------------


class TestFnbEquipmentHR:
    """Filler, sealer, chiller, CIP, and shared energy holding registers."""

    async def test_filler_hopper_level_hr_1200(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1200-1201: filler.hopper_level (float32 ABCD)."""
        client, *_ = fnb_modbus_system
        result = await client.read_holding_registers(1200, count=2)
        assert not result.isError()
        value = _f32_abcd(result.registers)
        assert abs(value - 75.0) < 0.1, f"hopper_level={value}"

    async def test_sealer_registers_hr_1300_1311(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1300-1311: sealer signals (float32 ABCD)."""
        client, *_ = fnb_modbus_system
        expected = {
            1300: 180.0,   # seal_temp
            1302: 3.5,     # seal_pressure
            1304: 2.0,     # seal_dwell
            1306: 30.0,    # gas_co2_pct
            1308: 70.0,    # gas_n2_pct
            1310: -0.7,    # vacuum_level
        }
        for addr, target in expected.items():
            result = await client.read_holding_registers(addr, count=2)
            assert not result.isError(), f"HR {addr} failed"
            value = _f32_abcd(result.registers)
            assert abs(value - target) < 0.1, f"HR {addr}={value}, expected ~{target}"

    async def test_chiller_registers_hr_1400_1407(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1400-1407: chiller signals (float32 ABCD)."""
        client, *_ = fnb_modbus_system
        expected = {
            1400: 2.5,    # room_temp
            1402: 2.0,    # setpoint
            1404: 3.0,    # suction_pressure
            1406: 16.0,   # discharge_pressure
        }
        for addr, target in expected.items():
            result = await client.read_holding_registers(addr, count=2)
            assert not result.isError(), f"HR {addr} failed"
            value = _f32_abcd(result.registers)
            assert abs(value - target) < 0.1, f"HR {addr}={value}, expected ~{target}"

    async def test_cip_float_registers_hr_1500_1505(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1500-1505: cip wash_temp, flow_rate, conductivity (float32 ABCD)."""
        client, *_ = fnb_modbus_system
        for addr in (1500, 1502, 1504):
            result = await client.read_holding_registers(addr, count=2)
            assert not result.isError(), f"HR {addr} failed"
            value = _f32_abcd(result.registers)
            assert not math.isnan(value), f"HR {addr} is NaN"
            assert value >= 0.0, f"HR {addr}={value} is negative"

    async def test_cip_cycle_time_hr_1506(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1506-1507: cip.cycle_time_elapsed (uint32 ABCD)."""
        client, *_ = fnb_modbus_system
        result = await client.read_holding_registers(1506, count=2)
        assert not result.isError()
        value = _u32_abcd(result.registers)
        assert 0 <= value < 100_000, f"cycle_time_elapsed={value}"

    async def test_shared_energy_hr_600_603(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 600-603: shared energy registers (float32 ABCD)."""
        client, *_ = fnb_modbus_system
        result = await client.read_holding_registers(600, count=2)
        assert not result.isError()
        line_power = _f32_abcd(result.registers)
        assert abs(line_power - 42.0) < 0.1, f"line_power={line_power}"

        result = await client.read_holding_registers(602, count=2)
        assert not result.isError()
        cumulative = _f32_abcd(result.registers)
        assert abs(cumulative - 8500.0) < 0.1, f"cumulative_kwh={cumulative}"

    async def test_all_fnb_hr_entries_readable(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """Every HR entry in the F&B register map should be readable."""
        client, _, server, _ = fnb_modbus_system
        rmap = server.register_map
        for entry in rmap.hr_entries:
            count = 2 if entry.data_type in ("float32", "uint32") else 1
            result = await client.read_holding_registers(entry.address, count=count)
            assert not result.isError(), (
                f"HR {entry.address} ({entry.signal_id}) failed: {result}"
            )


# ---------------------------------------------------------------------------
# F&B input registers
# ---------------------------------------------------------------------------


class TestFnbInputRegisters:
    """Verify F&B input registers (IR 100-121)."""

    async def test_oven_zone_temp_ir_100_102(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """IR 100-102: oven zone temperatures (int16 x10)."""
        client, _, _, _store = fnb_modbus_system
        expected = {100: 160.0, 101: 200.0, 102: 180.0}
        for ir_addr, target in expected.items():
            result = await client.read_input_registers(ir_addr, count=1)
            assert not result.isError(), f"IR {ir_addr} failed"
            decoded = decode_int16_x10(result.registers[0])
            assert abs(decoded - target) < 0.15, (
                f"IR {ir_addr}={decoded}, expected ~{target}"
            )

    async def test_oven_setpoint_ir_103_105(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """IR 103-105: oven zone setpoints (int16 x10)."""
        client, *_ = fnb_modbus_system
        expected = {103: 160.0, 104: 200.0, 105: 180.0}
        for ir_addr, target in expected.items():
            result = await client.read_input_registers(ir_addr, count=1)
            assert not result.isError(), f"IR {ir_addr} failed"
            decoded = decode_int16_x10(result.registers[0])
            assert abs(decoded - target) < 0.15, (
                f"IR {ir_addr}={decoded}, expected ~{target}"
            )

    async def test_oven_product_core_temp_ir_106(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """IR 106: oven.product_core_temp (int16 x10)."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(106, count=1)
        assert not result.isError()
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 72.0) < 0.15, f"product_core_temp IR={decoded}"

    async def test_chiller_room_temp_ir_110(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """IR 110: chiller.room_temp (int16 x10)."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(110, count=1)
        assert not result.isError()
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 2.5) < 0.15, f"chiller.room_temp IR={decoded}"

    async def test_chiller_setpoint_ir_111(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """IR 111: chiller.setpoint (int16 x10)."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(111, count=1)
        assert not result.isError()
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 2.0) < 0.15, f"chiller.setpoint IR={decoded}"

    async def test_cip_wash_temp_ir_115(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """IR 115: cip.wash_temp (int16 x10)."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(115, count=1)
        assert not result.isError()
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 20.0) < 0.15, f"cip.wash_temp IR={decoded}"

    async def test_energy_ir_120_121_float32(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """IR 120-121: energy.line_power (float32 ABCD) — same as HR 600-601."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(120, count=2)
        assert not result.isError()
        value = _f32_abcd(result.registers)
        assert not math.isnan(value)
        assert abs(value - 42.0) < 0.1, f"energy IR={value}"

    async def test_all_fnb_ir_entries_readable(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """Every IR entry in the F&B register map should be readable."""
        client, _, server, _ = fnb_modbus_system
        rmap = server.register_map
        for entry in rmap.ir_entries:
            count = 2 if entry.data_type == "float32" else 1
            result = await client.read_input_registers(entry.address, count=count)
            assert not result.isError(), (
                f"IR {entry.address} ({entry.signal_id}) failed: {result}"
            )


# ---------------------------------------------------------------------------
# F&B coils
# ---------------------------------------------------------------------------


class TestFnbCoils:
    """Verify F&B coils 100-102 (mixer + chiller)."""

    async def test_mixer_lid_closed_coil_100_true(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """Coil 100: mixer.lid_closed = True when signal > 0."""
        client, _, _, _store = fnb_modbus_system
        # store has mixer.lid_closed = 1.0 → coil True
        result = await client.read_coils(100, count=1)
        assert not result.isError()
        assert result.bits[0] is True, "Coil 100 (lid_closed) should be True"

    async def test_compressor_state_coil_101_true(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """Coil 101: chiller.compressor_state = True when compressor on."""
        client, *_ = fnb_modbus_system
        result = await client.read_coils(101, count=1)
        assert not result.isError()
        assert result.bits[0] is True, "Coil 101 (compressor_state) should be True"

    async def test_defrost_active_coil_102_false(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """Coil 102: chiller.defrost_active = False when not defrosting."""
        client, *_ = fnb_modbus_system
        result = await client.read_coils(102, count=1)
        assert not result.isError()
        assert result.bits[0] is False, "Coil 102 (defrost_active) should be False"

    async def test_read_all_fnb_coils_100_102(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """Batch read of F&B coils 100-102 should succeed."""
        client, *_ = fnb_modbus_system
        result = await client.read_coils(100, count=3)
        assert not result.isError()
        assert len(result.bits) >= 3

    async def test_lid_closed_coil_reflects_store_change(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """Coil 100 updates when store value changes (lid opens)."""
        client, _, server, store = fnb_modbus_system
        t = 1.0
        store.set("mixer.lid_closed", 0.0, t)   # lid opens
        server.sync_registers()

        result = await client.read_coils(100, count=1)
        assert not result.isError()
        assert result.bits[0] is False, "Coil 100 should be False when lid open"


# ---------------------------------------------------------------------------
# F&B discrete inputs
# ---------------------------------------------------------------------------


class TestFnbDiscreteInputs:
    """Verify F&B discrete input DI 100 (chiller.door_open)."""

    async def test_door_open_di_100_false(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """DI 100: chiller.door_open = False when door closed."""
        client, *_ = fnb_modbus_system
        result = await client.read_discrete_inputs(100, count=1)
        assert not result.isError()
        assert result.bits[0] is False, "DI 100 (door_open) should be False"

    async def test_door_open_di_100_true(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """DI 100: chiller.door_open = True when door is opened."""
        client, _, server, store = fnb_modbus_system
        store.set("chiller.door_open", 1.0, 1.0)
        server.sync_registers()

        result = await client.read_discrete_inputs(100, count=1)
        assert not result.isError()
        assert result.bits[0] is True, "DI 100 (door_open) should be True"


# ---------------------------------------------------------------------------
# Multi-slave Eurotherm oven zone controllers (UIDs 11-13)
# ---------------------------------------------------------------------------


class TestMultiSlaveOvenControllers:
    """Verify secondary slave IR for oven zone PV/SP/output_power.

    Per PRD 3.1.6, each Eurotherm unit serves:
      IR 0 = zone PV (temperature)
      IR 1 = zone setpoint
      IR 2 = output power
    All as int16 x10.
    """

    async def test_uid11_zone1_pv_ir0(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """UID 11, IR 0: oven.zone_1_temp = 160.0 C."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(0, count=1, device_id=11)
        assert not result.isError(), f"UID 11 IR 0 failed: {result}"
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 160.0) < 0.15, f"UID 11 IR 0={decoded}, expected ~160"

    async def test_uid11_zone1_sp_ir1(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """UID 11, IR 1: oven.zone_1_setpoint = 160.0 C."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(1, count=1, device_id=11)
        assert not result.isError(), f"UID 11 IR 1 failed: {result}"
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 160.0) < 0.15, f"UID 11 IR 1={decoded}, expected ~160"

    async def test_uid11_zone1_output_power_ir2(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """UID 11, IR 2: oven.zone_1_output_power = 40.0 %."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(2, count=1, device_id=11)
        assert not result.isError(), f"UID 11 IR 2 failed: {result}"
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 40.0) < 0.15, f"UID 11 IR 2={decoded}, expected ~40"

    async def test_uid12_zone2_pv_ir0(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """UID 12, IR 0: oven.zone_2_temp = 200.0 C."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(0, count=1, device_id=12)
        assert not result.isError()
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 200.0) < 0.15, f"UID 12 IR 0={decoded}"

    async def test_uid12_zone2_sp_ir1(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """UID 12, IR 1: oven.zone_2_setpoint = 200.0 C."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(1, count=1, device_id=12)
        assert not result.isError()
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 200.0) < 0.15, f"UID 12 IR 1={decoded}"

    async def test_uid12_zone2_output_power_ir2(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """UID 12, IR 2: oven.zone_2_output_power = 30.0 %."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(2, count=1, device_id=12)
        assert not result.isError()
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 30.0) < 0.15, f"UID 12 IR 2={decoded}"

    async def test_uid13_zone3_pv_ir0(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """UID 13, IR 0: oven.zone_3_temp = 180.0 C."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(0, count=1, device_id=13)
        assert not result.isError()
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 180.0) < 0.15, f"UID 13 IR 0={decoded}"

    async def test_uid13_zone3_sp_ir1(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """UID 13, IR 1: oven.zone_3_setpoint = 180.0 C."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(1, count=1, device_id=13)
        assert not result.isError()
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 180.0) < 0.15, f"UID 13 IR 1={decoded}"

    async def test_uid13_zone3_output_power_ir2(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """UID 13, IR 2: oven.zone_3_output_power = 35.0 %."""
        client, *_ = fnb_modbus_system
        result = await client.read_input_registers(2, count=1, device_id=13)
        assert not result.isError()
        decoded = decode_int16_x10(result.registers[0])
        assert abs(decoded - 35.0) < 0.15, f"UID 13 IR 2={decoded}"

    async def test_all_three_slaves_have_3_ir_entries(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """Each secondary slave (UID 11-13) should have exactly 3 IR entries."""
        _, _, server, _ = fnb_modbus_system
        rmap = server.register_map
        assert len(rmap.secondary_slaves) == 3, (
            f"Expected 3 secondary slaves, got {len(rmap.secondary_slaves)}"
        )
        for slave_map in rmap.secondary_slaves:
            assert len(slave_map.ir_entries) == 3, (
                f"UID {slave_map.slave_id}: expected 3 IR entries, "
                f"got {len(slave_map.ir_entries)}"
            )


# ---------------------------------------------------------------------------
# Oven setpoint write (FC16)
# ---------------------------------------------------------------------------


class TestFnbOvenSetpointWrite:
    """Test FC16 write to oven zone setpoints."""

    async def test_fc16_write_zone1_setpoint_hr_1110(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """FC16 write to zone_1_setpoint (HR 1110-1111) and read back."""
        client, _, _server, _ = fnb_modbus_system
        new_setpoint = 170.0
        hi, lo = encode_float32_abcd(new_setpoint)
        result = await client.write_registers(1110, [hi, lo])
        assert not result.isError(), f"FC16 write to HR 1110 failed: {result}"

        result = await client.read_holding_registers(1110, count=2)
        assert not result.isError()
        readback = _f32_abcd(result.registers)
        assert abs(readback - new_setpoint) < 0.01, (
            f"Readback={readback}, expected={new_setpoint}"
        )

    async def test_fc16_write_zone2_setpoint_hr_1112(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """FC16 write to zone_2_setpoint (HR 1112-1113) and read back."""
        client, *_ = fnb_modbus_system
        new_setpoint = 210.0
        hi, lo = encode_float32_abcd(new_setpoint)
        result = await client.write_registers(1112, [hi, lo])
        assert not result.isError()
        result = await client.read_holding_registers(1112, count=2)
        assert not result.isError()
        readback = _f32_abcd(result.registers)
        assert abs(readback - new_setpoint) < 0.01


# ---------------------------------------------------------------------------
# Cross-register consistency: same signal in HR and IR
# ---------------------------------------------------------------------------


class TestFnbCrossRegisterConsistency:
    """Verify HR float32 and IR int16_x10 agree for the same F&B signal."""

    async def test_oven_zone1_hr_vs_ir(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1100-1101 (float32) and IR 100 (int16 x10) for zone_1_temp agree."""
        client, *_ = fnb_modbus_system
        hr_result = await client.read_holding_registers(1100, count=2)
        assert not hr_result.isError()
        hr_value = _f32_abcd(hr_result.registers)

        ir_result = await client.read_input_registers(100, count=1)
        assert not ir_result.isError()
        ir_value = decode_int16_x10(ir_result.registers[0])

        assert abs(hr_value - ir_value) < 0.2, (
            f"zone_1_temp: HR={hr_value}, IR={ir_value} disagree"
        )

    async def test_chiller_room_temp_hr_vs_ir(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1400-1401 and IR 110 for chiller.room_temp agree."""
        client, *_ = fnb_modbus_system
        hr_result = await client.read_holding_registers(1400, count=2)
        assert not hr_result.isError()
        hr_value = _f32_abcd(hr_result.registers)

        ir_result = await client.read_input_registers(110, count=1)
        assert not ir_result.isError()
        ir_value = decode_int16_x10(ir_result.registers[0])

        assert abs(hr_value - ir_value) < 0.2, (
            f"chiller.room_temp: HR={hr_value}, IR={ir_value} disagree"
        )

    async def test_oven_zone1_temp_hr_vs_uid11_ir(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 1100-1101 and UID 11 IR 0 for zone_1_temp agree."""
        client, *_ = fnb_modbus_system
        hr_result = await client.read_holding_registers(1100, count=2)
        assert not hr_result.isError()
        hr_value = _f32_abcd(hr_result.registers)

        ir_result = await client.read_input_registers(0, count=1, device_id=11)
        assert not ir_result.isError()
        ir_value = decode_int16_x10(ir_result.registers[0])

        assert abs(hr_value - ir_value) < 0.2, (
            f"zone_1_temp: HR={hr_value}, UID11 IR={ir_value} disagree"
        )

    async def test_energy_hr_vs_ir(
        self,
        fnb_modbus_system: tuple[
            AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore
        ],
    ) -> None:
        """HR 600-601 and IR 120-121 for energy.line_power agree."""
        client, *_ = fnb_modbus_system
        hr_result = await client.read_holding_registers(600, count=2)
        assert not hr_result.isError()
        hr_value = _f32_abcd(hr_result.registers)

        ir_result = await client.read_input_registers(120, count=2)
        assert not ir_result.isError()
        ir_value = _f32_abcd(ir_result.registers)

        assert abs(hr_value - ir_value) < 0.01, (
            f"energy.line_power: HR={hr_value}, IR={ir_value} disagree"
        )
