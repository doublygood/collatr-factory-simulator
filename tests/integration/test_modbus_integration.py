"""Integration tests for the Modbus TCP server.

Starts the DataEngine + ModbusServer, connects a real pymodbus client,
and verifies every register address in the packaging profile register
map (Appendix A).

PRD Reference: Section 3.1, Appendix A (Modbus Register Map)
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path

import pytest
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.pdu import ExceptionResponse
from pymodbus.pdu.register_message import ExcCodes

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.protocols.modbus_server import (
    ModbusServer,
    decode_float32_abcd,
    decode_int16_x10,
    decode_uint32_abcd,
    encode_float32_abcd,
)
from factory_simulator.store import SignalStore

# Path to the default factory config
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory.yaml"

# Integration test port (avoid 502 which requires root)
_TEST_PORT = 15502
_HOST = "127.0.0.1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def modbus_system() -> (  # type: ignore[override]
    tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore]
):
    """Start engine + Modbus server, yield connected client, clean up."""
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

    # Tick engine to populate signal IDs in the store
    for _ in range(5):
        engine.tick()

    # Override with known test values (press starts Off, so signals are ~0).
    # This isolates the Modbus protocol layer test from engine behaviour.
    # Signal IDs must match factory.yaml exactly: {equipment_id}.{signal_name}
    t = clock.sim_time
    store.set("press.machine_state", 2.0, t)         # Running
    store.set("press.line_speed", 150.0, t)
    store.set("press.web_tension", 50.0, t)
    store.set("press.ink_viscosity", 28.0, t)
    store.set("press.ink_temperature", 25.0, t)
    store.set("press.dryer_temp_zone_1", 75.0, t)
    store.set("press.dryer_temp_zone_2", 80.0, t)
    store.set("press.dryer_temp_zone_3", 85.0, t)
    store.set("press.dryer_setpoint_zone_1", 75.0, t)
    store.set("press.dryer_setpoint_zone_2", 80.0, t)
    store.set("press.dryer_setpoint_zone_3", 85.0, t)
    store.set("press.impression_count", 1000.0, t)
    store.set("press.good_count", 5000.0, t)
    store.set("press.waste_count", 50.0, t)
    store.set("press.main_drive_current", 65.0, t)
    store.set("press.main_drive_speed", 1200.0, t)
    store.set("press.registration_error_x", 0.02, t)
    store.set("press.registration_error_y", 0.01, t)
    store.set("press.nip_pressure", 3.5, t)
    store.set("press.unwind_diameter", 800.0, t)
    store.set("press.rewind_diameter", 400.0, t)
    store.set("laminator.nip_temp", 95.0, t)
    store.set("laminator.nip_pressure", 4.0, t)
    store.set("laminator.tunnel_temp", 60.0, t)
    store.set("laminator.web_speed", 140.0, t)
    store.set("laminator.adhesive_weight", 2.5, t)
    store.set("slitter.speed", 145.0, t)
    store.set("slitter.web_tension", 45.0, t)
    store.set("slitter.reel_count", 100.0, t)
    store.set("energy.line_power", 85.0, t)
    store.set("energy.cumulative_kwh", 12000.0, t)

    # Create and start Modbus server
    server = ModbusServer(config, store, host=_HOST, port=_TEST_PORT)
    server.sync_registers()  # Sync before starting to ensure data is present
    await server.start()
    await asyncio.sleep(0.3)  # Give server time to bind

    # Connect client
    client = AsyncModbusTcpClient(_HOST, port=_TEST_PORT)
    await client.connect()
    assert client.connected, f"Failed to connect to Modbus server on {_HOST}:{_TEST_PORT}"

    yield client, engine, server, store

    # Cleanup
    client.close()
    await server.stop()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _read_float32(regs: list[int]) -> float:
    """Decode two registers as float32 ABCD."""
    return decode_float32_abcd(regs)


def _read_uint32(regs: list[int]) -> int:
    """Decode two registers as uint32 big-endian."""
    return decode_uint32_abcd(regs)


# ---------------------------------------------------------------------------
# Holding register tests
# ---------------------------------------------------------------------------


class TestHoldingRegisters:
    """Verify all packaging profile holding registers via live Modbus."""

    async def test_press_line_speed(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 100-101: press.line_speed (float32)."""
        client, engine, server, store = modbus_system
        result = await client.read_holding_registers(100, count=2)
        assert not result.isError(), f"Read HR 100-101 failed: {result}"
        value = _read_float32(result.registers)
        assert not math.isnan(value), "line_speed is NaN"
        assert 0.0 <= value <= 400.0, f"line_speed={value} out of range"

    async def test_press_web_tension(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 102-103: press.web_tension (float32)."""
        client, *_ = modbus_system
        result = await client.read_holding_registers(102, count=2)
        assert not result.isError()
        value = _read_float32(result.registers)
        assert not math.isnan(value)

    async def test_press_ink_viscosity(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 110-111: press.ink_viscosity (float32)."""
        client, *_ = modbus_system
        result = await client.read_holding_registers(110, count=2)
        assert not result.isError()
        value = _read_float32(result.registers)
        assert not math.isnan(value)
        # Ink viscosity target is 28.0 (from config)
        assert 15.0 <= value <= 60.0, f"ink_viscosity={value} out of range"

    async def test_press_ink_temperature(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 112-113: press.ink_temperature (float32)."""
        client, *_ = modbus_system
        result = await client.read_holding_registers(112, count=2)
        assert not result.isError()
        value = _read_float32(result.registers)
        assert not math.isnan(value)
        assert 18.0 <= value <= 35.0

    async def test_press_dryer_temperatures(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 120-125: press dryer zone temperatures (float32)."""
        client, *_ = modbus_system
        for addr in (120, 122, 124):
            result = await client.read_holding_registers(addr, count=2)
            assert not result.isError(), f"Read HR {addr} failed"
            value = _read_float32(result.registers)
            assert not math.isnan(value), f"Dryer temp at HR {addr} is NaN"
            assert 15.0 <= value <= 150.0, f"HR {addr} value={value} out of range"

    async def test_press_dryer_setpoints(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 140-145: press dryer zone setpoints (float32, writable)."""
        client, *_ = modbus_system
        expected_targets = {140: 75.0, 142: 80.0, 144: 85.0}
        for addr, target in expected_targets.items():
            result = await client.read_holding_registers(addr, count=2)
            assert not result.isError(), f"Read HR {addr} failed"
            value = _read_float32(result.registers)
            assert not math.isnan(value)
            # Setpoints should be at or near target (no noise configured)
            assert abs(value - target) < 1.0, f"HR {addr} setpoint={value}, expected ~{target}"

    async def test_press_counters(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 200-205: press counters (uint32)."""
        client, *_ = modbus_system
        for addr in (200, 202, 204):
            result = await client.read_holding_registers(addr, count=2)
            assert not result.isError(), f"Read HR {addr} failed"
            value = _read_uint32(result.registers)
            assert 0 <= value < 1_000_000_000, f"Counter at HR {addr}={value}"

    async def test_press_machine_state(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 210: press.machine_state (uint16)."""
        client, *_ = modbus_system
        result = await client.read_holding_registers(210, count=1)
        assert not result.isError()
        state = result.registers[0]
        assert 0 <= state <= 5, f"Machine state={state} invalid (expected 0-5)"

    async def test_press_fault_code_default(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 211: press.fault_code (uint16) -- defaults to 0 (no fault)."""
        client, *_ = modbus_system
        result = await client.read_holding_registers(211, count=1)
        assert not result.isError()
        assert result.registers[0] == 0, "fault_code should be 0 (no active fault)"

    async def test_press_drive_nip_reels(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 300-323: press drive, nip, reel signals (float32)."""
        client, *_ = modbus_system
        for addr in (300, 302, 310, 320, 322):
            result = await client.read_holding_registers(addr, count=2)
            assert not result.isError(), f"Read HR {addr} failed"
            value = _read_float32(result.registers)
            assert not math.isnan(value), f"HR {addr} is NaN"

    async def test_laminator_registers(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 400-409: laminator signals (float32)."""
        client, *_ = modbus_system
        for addr in (400, 402, 404, 406, 408):
            result = await client.read_holding_registers(addr, count=2)
            assert not result.isError(), f"Read HR {addr} failed"
            value = _read_float32(result.registers)
            assert not math.isnan(value), f"HR {addr} is NaN"

    async def test_slitter_registers(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 500-511: slitter signals (float32 + uint32)."""
        client, *_ = modbus_system
        # Float32 signals
        for addr in (500, 502):
            result = await client.read_holding_registers(addr, count=2)
            assert not result.isError(), f"Read HR {addr} failed"
            value = _read_float32(result.registers)
            assert not math.isnan(value)

        # Uint32 counter
        result = await client.read_holding_registers(510, count=2)
        assert not result.isError()
        value = _read_uint32(result.registers)
        assert 0 <= value < 100_000

    async def test_energy_registers(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 600-603: energy monitoring (float32)."""
        client, *_ = modbus_system
        for addr in (600, 602):
            result = await client.read_holding_registers(addr, count=2)
            assert not result.isError(), f"Read HR {addr} failed"
            value = _read_float32(result.registers)
            assert not math.isnan(value)

    async def test_all_hr_entries_readable(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """Every HR entry in the register map should be readable."""
        client, _, server, _ = modbus_system
        rmap = server.register_map
        for entry in rmap.hr_entries:
            count = 2 if entry.data_type in ("float32", "uint32") else 1
            result = await client.read_holding_registers(
                entry.address, count=count,
            )
            assert not result.isError(), (
                f"HR {entry.address} ({entry.signal_id}) failed: {result}"
            )


# ---------------------------------------------------------------------------
# Input register tests
# ---------------------------------------------------------------------------


class TestInputRegisters:
    """Verify all packaging profile input registers via live Modbus."""

    async def test_dryer_temperature_ir(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """IR 0-2: press dryer zone temperatures (int16 x10)."""
        client, _, _, store = modbus_system
        for ir_addr in (0, 1, 2):
            result = await client.read_input_registers(ir_addr, count=1)
            assert not result.isError(), f"Read IR {ir_addr} failed"
            decoded = decode_int16_x10(result.registers[0])
            assert 10.0 <= decoded <= 150.0, f"IR {ir_addr} temp={decoded} out of range"

    async def test_ink_temperature_ir(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """IR 3: press.ink_temperature (int16 x10)."""
        client, *_ = modbus_system
        result = await client.read_input_registers(3, count=1)
        assert not result.isError()
        decoded = decode_int16_x10(result.registers[0])
        assert 15.0 <= decoded <= 40.0, f"IR 3 ink_temp={decoded}"

    async def test_laminator_temperature_ir(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """IR 4-5: laminator temperatures (int16 x10)."""
        client, *_ = modbus_system
        for ir_addr in (4, 5):
            result = await client.read_input_registers(ir_addr, count=1)
            assert not result.isError(), f"Read IR {ir_addr} failed"
            decoded = decode_int16_x10(result.registers[0])
            assert 10.0 <= decoded <= 150.0, f"IR {ir_addr} temp={decoded}"

    async def test_energy_ir(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """IR 10-11: energy.line_power (float32)."""
        client, *_ = modbus_system
        result = await client.read_input_registers(10, count=2)
        assert not result.isError()
        value = _read_float32(result.registers)
        assert not math.isnan(value)
        assert 0.0 <= value <= 200.0, f"IR 10-11 energy={value}"

    async def test_all_ir_entries_readable(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """Every IR entry in the register map should be readable."""
        client, _, server, _ = modbus_system
        rmap = server.register_map
        for entry in rmap.ir_entries:
            count = 2 if entry.data_type == "float32" else 1
            result = await client.read_input_registers(
                entry.address, count=count,
            )
            assert not result.isError(), (
                f"IR {entry.address} ({entry.signal_id}) failed: {result}"
            )


# ---------------------------------------------------------------------------
# Coil tests
# ---------------------------------------------------------------------------


class TestCoils:
    """Verify packaging profile coils via live Modbus."""

    async def test_read_all_coils(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """All 6 packaging coils should be readable."""
        client, *_ = modbus_system
        result = await client.read_coils(0, count=6)
        assert not result.isError()
        assert len(result.bits) >= 6

    async def test_press_running_coil_matches_state(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """Coil 0 should match press.machine_state == Running (2)."""
        client, _, server, store = modbus_system

        # Read current machine state
        state_result = await client.read_holding_registers(210, count=1)
        assert not state_result.isError()
        state = state_result.registers[0]

        # Read running coil
        coil_result = await client.read_coils(0, count=1)
        assert not coil_result.isError()

        expected_running = (state == 2)
        assert coil_result.bits[0] == expected_running, (
            f"Coil 0 (running) = {coil_result.bits[0]}, "
            f"but machine_state = {state}"
        )


# ---------------------------------------------------------------------------
# Discrete input tests
# ---------------------------------------------------------------------------


class TestDiscreteInputs:
    """Verify packaging profile discrete inputs via live Modbus."""

    async def test_read_all_dis(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """All 3 packaging discrete inputs should be readable."""
        client, *_ = modbus_system
        result = await client.read_discrete_inputs(0, count=3)
        assert not result.isError()
        assert len(result.bits) >= 3

    async def test_guard_door_closed(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """DI 0 (guard_door_open) should be False (door closed)."""
        client, *_ = modbus_system
        result = await client.read_discrete_inputs(0, count=1)
        assert not result.isError()
        assert not result.bits[0]


# ---------------------------------------------------------------------------
# Write tests (FC16 setpoint write)
# ---------------------------------------------------------------------------


class TestSetpointWrite:
    """Test writing setpoints via FC16 and reading back."""

    async def test_fc16_write_and_readback(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """FC16 write to dryer setpoint HR 140-141 and read back."""
        client, _, server, store = modbus_system

        new_setpoint = 92.5
        hi, lo = encode_float32_abcd(new_setpoint)
        result = await client.write_registers(140, [hi, lo])
        assert not result.isError(), f"FC16 write failed: {result}"

        # Read back from the register (not synced yet, so it's the raw write)
        result = await client.read_holding_registers(140, count=2)
        assert not result.isError()
        readback = _read_float32(result.registers)
        assert abs(readback - new_setpoint) < 0.01, (
            f"Readback={readback}, expected={new_setpoint}"
        )


# ---------------------------------------------------------------------------
# FC06 rejection tests
# ---------------------------------------------------------------------------


class TestFC06Rejection:
    """Test FC06 rejection for float32 register pairs over live TCP."""

    async def test_fc06_to_float32_rejected(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """FC06 write to float32 register pair returns exception 0x01."""
        client, *_ = modbus_system
        # HR 100 is press.line_speed (float32)
        result = await client.write_register(100, 12345)
        assert result.isError(), "FC06 to float32 should be rejected"
        assert isinstance(result, ExceptionResponse)
        assert result.exception_code == ExcCodes.ILLEGAL_FUNCTION

    async def test_fc16_to_same_address_succeeds(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """FC16 write to the same float32 register pair succeeds."""
        client, *_ = modbus_system
        hi, lo = encode_float32_abcd(200.0)
        result = await client.write_registers(100, [hi, lo])
        assert not result.isError(), f"FC16 to float32 should succeed: {result}"


# ---------------------------------------------------------------------------
# Register limit test
# ---------------------------------------------------------------------------


class TestRegisterLimit:
    """Test the 125-register read limit enforcement."""

    async def test_read_125_succeeds(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """Reading exactly 125 registers should succeed."""
        client, *_ = modbus_system
        result = await client.read_holding_registers(0, count=125)
        assert not result.isError()
        assert len(result.registers) == 125

    async def test_read_126_fails_client_side(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """Reading > 125 registers fails (pymodbus enforces client-side)."""
        client, *_ = modbus_system
        # pymodbus client enforces 125 max in encode() - raises ValueError
        with pytest.raises(ValueError, match="count"):
            await client.read_holding_registers(0, count=126)


# ---------------------------------------------------------------------------
# Consistency tests (HR vs IR for same signal)
# ---------------------------------------------------------------------------


class TestCrossRegisterConsistency:
    """Verify that HR and IR for the same signal show consistent values."""

    async def test_dryer_zone1_hr_vs_ir(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 120-121 (float32) and IR 0 (int16 x10) for dryer zone 1 match."""
        client, *_ = modbus_system

        # Read HR (float32)
        hr_result = await client.read_holding_registers(120, count=2)
        assert not hr_result.isError()
        hr_value = _read_float32(hr_result.registers)

        # Read IR (int16 x10)
        ir_result = await client.read_input_registers(0, count=1)
        assert not ir_result.isError()
        ir_value = decode_int16_x10(ir_result.registers[0])

        # Values should agree within the x10 quantisation error (0.1 C)
        assert abs(hr_value - ir_value) < 0.2, (
            f"HR={hr_value}, IR={ir_value} disagree"
        )

    async def test_energy_hr_vs_ir(
        self,
        modbus_system: tuple[AsyncModbusTcpClient, DataEngine, ModbusServer, SignalStore],
    ) -> None:
        """HR 600-601 and IR 10-11 for energy.line_power should match."""
        client, *_ = modbus_system

        hr_result = await client.read_holding_registers(600, count=2)
        assert not hr_result.isError()
        hr_value = _read_float32(hr_result.registers)

        ir_result = await client.read_input_registers(10, count=2)
        assert not ir_result.isError()
        ir_value = _read_float32(ir_result.registers)

        assert abs(hr_value - ir_value) < 0.01, (
            f"Energy HR={hr_value}, IR={ir_value} disagree"
        )
