"""Unit tests for the Modbus TCP server module.

Tests encoding/decoding functions, register map building, register
synchronisation, coil and discrete input derivation, FC06 rejection,
and the 125-register read limit.

PRD Reference: Section 3.1, Appendix A (Modbus Register Map)
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from pymodbus.pdu.register_message import ExcCodes

from factory_simulator.config import (
    EquipmentConfig,
    FactoryConfig,
    ModbusProtocolConfig,
    ProtocolsConfig,
    SignalConfig,
    SimulationConfig,
    load_config,
)
from factory_simulator.protocols.modbus_server import (
    FactoryDeviceContext,
    ModbusServer,
    build_register_map,
    decode_float32_abcd,
    decode_float32_cdab,
    decode_int16_x10,
    decode_uint32_abcd,
    decode_uint32_cdab,
    encode_float32_abcd,
    encode_float32_cdab,
    encode_int16_x10,
    encode_uint32_abcd,
    encode_uint32_cdab,
)
from factory_simulator.store import SignalStore

# Path to the default factory config
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "factory.yaml"


# ---------------------------------------------------------------------------
# Encoding / decoding tests
# ---------------------------------------------------------------------------


class TestFloat32Encoding:
    """Test float32 ABCD encoding and decoding."""

    def test_encode_positive(self) -> None:
        hi, lo = encode_float32_abcd(150.0)
        # Verify round-trip
        decoded = decode_float32_abcd([hi, lo])
        assert abs(decoded - 150.0) < 0.01

    def test_encode_zero(self) -> None:
        hi, lo = encode_float32_abcd(0.0)
        assert hi == 0
        assert lo == 0
        assert decode_float32_abcd([hi, lo]) == 0.0

    def test_encode_negative(self) -> None:
        hi, lo = encode_float32_abcd(-42.5)
        decoded = decode_float32_abcd([hi, lo])
        assert abs(decoded - (-42.5)) < 0.01

    def test_encode_small(self) -> None:
        hi, lo = encode_float32_abcd(0.001)
        decoded = decode_float32_abcd([hi, lo])
        assert abs(decoded - 0.001) < 1e-5

    def test_byte_order_is_abcd(self) -> None:
        """Verify ABCD byte order matches big-endian struct.pack('>f')."""
        value = 85.5
        hi, lo = encode_float32_abcd(value)
        # Reconstruct the 4 bytes
        raw = struct.pack(">HH", hi, lo)
        # Should match direct float32 big-endian encoding
        expected = struct.pack(">f", value)
        assert raw == expected


class TestUint32Encoding:
    """Test uint32 ABCD encoding and decoding."""

    def test_encode_small(self) -> None:
        hi, lo = encode_uint32_abcd(42)
        assert hi == 0
        assert lo == 42
        assert decode_uint32_abcd([hi, lo]) == 42

    def test_encode_large(self) -> None:
        value = 1_000_000
        hi, lo = encode_uint32_abcd(value)
        assert decode_uint32_abcd([hi, lo]) == value

    def test_encode_max(self) -> None:
        hi, lo = encode_uint32_abcd(0xFFFFFFFF)
        assert hi == 0xFFFF
        assert lo == 0xFFFF
        assert decode_uint32_abcd([hi, lo]) == 0xFFFFFFFF

    def test_encode_zero(self) -> None:
        hi, lo = encode_uint32_abcd(0)
        assert hi == 0 and lo == 0

    def test_clamp_negative(self) -> None:
        hi, lo = encode_uint32_abcd(-5)
        assert decode_uint32_abcd([hi, lo]) == 0

    def test_clamp_overflow(self) -> None:
        hi, lo = encode_uint32_abcd(0x1_0000_0000)
        assert decode_uint32_abcd([hi, lo]) == 0xFFFFFFFF


class TestInt16X10Encoding:
    """Test int16 x10 (Eurotherm-style) encoding and decoding."""

    def test_encode_positive(self) -> None:
        reg = encode_int16_x10(85.0)
        assert reg == 850
        assert abs(decode_int16_x10(reg) - 85.0) < 0.01

    def test_encode_fractional(self) -> None:
        reg = encode_int16_x10(25.3)
        assert reg == 253
        assert abs(decode_int16_x10(reg) - 25.3) < 0.01

    def test_encode_zero(self) -> None:
        reg = encode_int16_x10(0.0)
        assert reg == 0

    def test_encode_negative(self) -> None:
        """Negative temperatures use two's complement uint16."""
        reg = encode_int16_x10(-10.0)
        # -100 as uint16 = 65436
        assert reg == 65436
        assert abs(decode_int16_x10(reg) - (-10.0)) < 0.01

    def test_rounding(self) -> None:
        """Values are rounded to nearest 0.1."""
        reg = encode_int16_x10(25.05)
        # 25.05 * 10 = 250.5 -> round to 250 or 251
        decoded = decode_int16_x10(reg)
        assert abs(decoded - 25.05) < 0.1

    def test_clamp_high(self) -> None:
        reg = encode_int16_x10(5000.0)
        assert reg == 32767  # int16 max
        assert abs(decode_int16_x10(reg) - 3276.7) < 0.1

    def test_clamp_low(self) -> None:
        reg = encode_int16_x10(-5000.0)
        # -32768 as uint16 = 32768
        decoded = decode_int16_x10(reg)
        assert decoded == -3276.8


# ---------------------------------------------------------------------------
# Register map building tests
# ---------------------------------------------------------------------------


class TestBuildRegisterMap:
    """Test register map building from config."""

    def test_packaging_config_hr_count(self) -> None:
        """Packaging config should produce the expected number of HR entries."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        rmap = build_register_map(config)
        # From factory.yaml: press (17 HR signals), laminator (5), slitter (3), energy (2)
        # = 27 total HR entries
        assert len(rmap.hr_entries) >= 25

    def test_packaging_config_ir_count(self) -> None:
        """Packaging config should produce the expected IR entries."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        rmap = build_register_map(config)
        # IR entries: dryer zones 0-2, ink_temp 3, laminator 4-5, energy 10-11
        # = 7 entries (counting float32 at 10-11 as 1)
        assert len(rmap.ir_entries) >= 7

    def test_packaging_config_coils(self) -> None:
        """Packaging profile should have 6 coil definitions."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        rmap = build_register_map(config)
        assert len(rmap.coil_defs) == 6

    def test_packaging_config_di(self) -> None:
        """Packaging profile should have 3 discrete input definitions."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        rmap = build_register_map(config)
        assert len(rmap.di_defs) == 3

    def test_float32_addresses_tracked(self) -> None:
        """Float32 HR addresses should be tracked for FC06 rejection."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        rmap = build_register_map(config)
        # press.line_speed is float32 at HR 100-101
        assert 100 in rmap.float32_hr_addresses
        assert 101 in rmap.float32_hr_addresses

    def test_writable_setpoints(self) -> None:
        """Dryer setpoint registers should be marked writable."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        rmap = build_register_map(config)
        writable = [e for e in rmap.hr_entries if e.writable]
        # Dryer setpoints at HR 140-141, 142-143, 144-145
        writable_addrs = {e.address for e in writable}
        assert 140 in writable_addrs
        assert 142 in writable_addrs
        assert 144 in writable_addrs

    def test_uint16_registers(self) -> None:
        """Machine state (HR 210) should be uint16."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        rmap = build_register_map(config)
        state_entry = next(
            (e for e in rmap.hr_entries if e.address == 210), None,
        )
        assert state_entry is not None
        assert state_entry.data_type == "uint16"
        assert state_entry.signal_id == "press.machine_state"

    def test_disabled_equipment_excluded(self) -> None:
        """Disabled equipment should not appear in the register map."""
        config = FactoryConfig(
            equipment={
                "test_eq": EquipmentConfig(
                    enabled=False,
                    type="test",
                    signals={
                        "signal_a": SignalConfig(
                            model="steady_state",
                            modbus_hr=[100, 101],
                            modbus_type="float32",
                        ),
                    },
                ),
            },
        )
        rmap = build_register_map(config)
        assert len(rmap.hr_entries) == 0

    def test_signal_without_modbus_excluded(self) -> None:
        """Signals without modbus_hr/modbus_ir should not be in map."""
        config = FactoryConfig(
            equipment={
                "test_eq": EquipmentConfig(
                    enabled=True,
                    type="test",
                    signals={
                        "mqtt_only": SignalConfig(
                            model="steady_state",
                            mqtt_topic="test/signal",
                        ),
                    },
                ),
            },
        )
        rmap = build_register_map(config)
        assert len(rmap.hr_entries) == 0
        assert len(rmap.ir_entries) == 0


# ---------------------------------------------------------------------------
# Register synchronisation tests
# ---------------------------------------------------------------------------


def _make_minimal_config() -> FactoryConfig:
    """Build a minimal config with one equipment group for testing."""
    return FactoryConfig(
        simulation=SimulationConfig(tick_interval_ms=100, random_seed=42),
        protocols=ProtocolsConfig(
            modbus=ModbusProtocolConfig(port=15502),
        ),
        equipment={
            "test": EquipmentConfig(
                enabled=True,
                type="test",
                signals={
                    "temp": SignalConfig(
                        model="steady_state",
                        modbus_hr=[100, 101],
                        modbus_type="float32",
                        modbus_ir=[0],
                    ),
                    "count": SignalConfig(
                        model="counter",
                        modbus_hr=[200, 201],
                        modbus_type="uint32",
                    ),
                    "state": SignalConfig(
                        model="state_machine",
                        modbus_hr=[210],
                        modbus_type="uint16",
                    ),
                },
            ),
        },
    )


class TestSyncRegisters:
    """Test synchronisation of store values to Modbus registers."""

    def test_sync_float32(self) -> None:
        """Float32 signal value encodes correctly in HR."""
        config = _make_minimal_config()
        store = SignalStore()
        store.set("test.temp", 85.5, 0.0)

        server = ModbusServer(config, store, port=15503)
        server.sync_registers()

        # +1 offset: ModbusDeviceContext adds 1 to addresses
        regs = server._hr_block.getValues(101, 2)
        decoded = decode_float32_abcd(regs)
        assert abs(decoded - 85.5) < 0.01

    def test_sync_uint32(self) -> None:
        """Uint32 counter encodes correctly in HR."""
        config = _make_minimal_config()
        store = SignalStore()
        store.set("test.count", 500_000.0, 0.0)

        server = ModbusServer(config, store, port=15503)
        server.sync_registers()

        regs = server._hr_block.getValues(201, 2)
        decoded = decode_uint32_abcd(regs)
        assert decoded == 500_000

    def test_sync_uint16(self) -> None:
        """Uint16 state encodes correctly in HR."""
        config = _make_minimal_config()
        store = SignalStore()
        store.set("test.state", 3.0, 0.0)

        server = ModbusServer(config, store, port=15503)
        server.sync_registers()

        regs = server._hr_block.getValues(211, 1)
        assert regs[0] == 3

    def test_sync_int16_x10(self) -> None:
        """Int16 x10 (Eurotherm) temperature encodes correctly in IR."""
        config = _make_minimal_config()
        store = SignalStore()
        store.set("test.temp", 85.3, 0.0)

        server = ModbusServer(config, store, port=15503)
        server.sync_registers()

        regs = server._ir_block.getValues(1, 1)
        decoded = decode_int16_x10(regs[0])
        assert abs(decoded - 85.3) < 0.1

    def test_sync_missing_signal(self) -> None:
        """Missing signal in store should leave register at 0."""
        config = _make_minimal_config()
        store = SignalStore()
        # Don't set any values

        server = ModbusServer(config, store, port=15503)
        server.sync_registers()

        regs = server._hr_block.getValues(101, 2)
        assert regs[0] == 0 and regs[1] == 0

    def test_sync_updates_on_value_change(self) -> None:
        """Registers update when store values change."""
        config = _make_minimal_config()
        store = SignalStore()
        store.set("test.temp", 50.0, 0.0)

        server = ModbusServer(config, store, port=15503)
        server.sync_registers()

        # +1 offset: ModbusDeviceContext adds 1 to addresses
        regs = server._hr_block.getValues(101, 2)
        assert abs(decode_float32_abcd(regs) - 50.0) < 0.01

        # Update store
        store.set("test.temp", 90.0, 1.0)
        server.sync_registers()

        regs = server._hr_block.getValues(101, 2)
        assert abs(decode_float32_abcd(regs) - 90.0) < 0.01


# ---------------------------------------------------------------------------
# Coil derivation tests
# ---------------------------------------------------------------------------


class TestCoilSync:
    """Test coil derivation from machine state."""

    def test_press_running_coil(self) -> None:
        """Coil 0 (press.running) is True when machine_state == 2."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        store = SignalStore()
        store.set("press.machine_state", 2.0, 0.0)  # Running

        server = ModbusServer(config, store, port=15504)
        server.sync_registers()

        coils = server._coil_block.getValues(1, 1)  # +1 offset
        assert coils[0] is True

    def test_press_not_running_coil(self) -> None:
        """Coil 0 is False when machine_state != 2."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        store = SignalStore()
        store.set("press.machine_state", 3.0, 0.0)  # Idle

        server = ModbusServer(config, store, port=15504)
        server.sync_registers()

        coils = server._coil_block.getValues(1, 1)  # +1 offset
        assert coils[0] is False

    def test_press_fault_coil(self) -> None:
        """Coil 1 (press.fault_active) is True when machine_state == 4."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        store = SignalStore()
        store.set("press.machine_state", 4.0, 0.0)  # Fault

        server = ModbusServer(config, store, port=15504)
        server.sync_registers()

        coils = server._coil_block.getValues(2, 1)  # +1 offset
        assert coils[0] is True

    def test_estop_coil_false_normally(self) -> None:
        """Coil 2 (emergency_stop) is False under normal conditions."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        store = SignalStore()

        server = ModbusServer(config, store, port=15504)
        server.sync_registers()

        coils = server._coil_block.getValues(3, 1)  # +1 offset
        assert coils[0] is False

    def test_missing_state_all_coils_false(self) -> None:
        """All coils False when machine_state not in store."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        store = SignalStore()

        server = ModbusServer(config, store, port=15504)
        server.sync_registers()

        for addr in range(6):
            coils = server._coil_block.getValues(addr + 1, 1)  # +1 offset
            assert coils[0] is False, f"Coil {addr} should be False"


# ---------------------------------------------------------------------------
# Discrete input derivation tests
# ---------------------------------------------------------------------------


class TestDiscreteInputSync:
    """Test discrete input derivation."""

    def test_guard_door_normally_closed(self) -> None:
        """DI 0 (guard_door_open) is always False (closed)."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        store = SignalStore()

        server = ModbusServer(config, store, port=15505)
        server.sync_registers()

        dis = server._di_block.getValues(1, 1)  # +1 offset
        assert dis[0] is False

    def test_material_present_when_running(self) -> None:
        """DI 1 (material_present) is True when press is Running (state 2)."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        store = SignalStore()
        store.set("press.machine_state", 2.0, 0.0)

        server = ModbusServer(config, store, port=15505)
        server.sync_registers()

        dis = server._di_block.getValues(2, 1)  # +1 offset
        assert dis[0] is True

    def test_material_absent_when_idle(self) -> None:
        """DI 1 is False when press is Idle (state 3)."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        store = SignalStore()
        store.set("press.machine_state", 3.0, 0.0)

        server = ModbusServer(config, store, port=15505)
        server.sync_registers()

        dis = server._di_block.getValues(2, 1)  # +1 offset
        assert dis[0] is False

    def test_cycle_complete_toggles(self) -> None:
        """DI 2 (cycle_complete) toggles based on impression count parity."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        store = SignalStore()

        server = ModbusServer(config, store, port=15505)

        store.set("press.impression_count", 100.0, 0.0)
        server.sync_registers()
        dis = server._di_block.getValues(3, 1)  # +1 offset
        assert dis[0] is False  # 100 % 2 == 0 -> False

        store.set("press.impression_count", 101.0, 1.0)
        server.sync_registers()
        dis = server._di_block.getValues(3, 1)  # +1 offset
        assert dis[0] is True  # 101 % 2 == 1 -> True


# ---------------------------------------------------------------------------
# FactoryDeviceContext tests (FC06 rejection, register limit)
# ---------------------------------------------------------------------------


class TestFactoryDeviceContext:
    """Test FC06 rejection and register limit enforcement."""

    def _make_context(self) -> FactoryDeviceContext:
        """Create a device context with test data and float32 protection."""
        from pymodbus.datastore import ModbusSequentialDataBlock

        hr_block = ModbusSequentialDataBlock(0, [0] * 200)
        ir_block = ModbusSequentialDataBlock(0, [0] * 20)

        # Float32 addresses: 100-101, 102-103
        float32_addrs = {100, 101, 102, 103}

        return FactoryDeviceContext(
            float32_addresses=float32_addrs,
            hr=hr_block,
            ir=ir_block,
        )

    def test_fc06_rejected_on_float32(self) -> None:
        """FC06 to float32 address returns ILLEGAL_FUNCTION."""
        ctx = self._make_context()
        result = ctx.setValues(6, 100, [12345])
        assert result == ExcCodes.ILLEGAL_FUNCTION

    def test_fc06_rejected_on_second_word(self) -> None:
        """FC06 to second word of float32 pair also rejected."""
        ctx = self._make_context()
        result = ctx.setValues(6, 101, [12345])
        assert result == ExcCodes.ILLEGAL_FUNCTION

    def test_fc16_succeeds_on_float32(self) -> None:
        """FC16 to float32 address succeeds."""
        ctx = self._make_context()
        hi, lo = encode_float32_abcd(85.5)
        result = ctx.setValues(16, 100, [hi, lo])
        # FC16 should succeed (returns None, not ExcCodes)
        assert result is None or result != ExcCodes.ILLEGAL_FUNCTION

    def test_fc06_succeeds_on_non_float32(self) -> None:
        """FC06 to a non-float32 address succeeds."""
        ctx = self._make_context()
        result = ctx.setValues(6, 50, [42])
        assert result is None or result != ExcCodes.ILLEGAL_FUNCTION

    def test_read_125_succeeds(self) -> None:
        """Reading exactly 125 registers should succeed."""
        ctx = self._make_context()
        result = ctx.getValues(3, 0, 125)
        assert not isinstance(result, ExcCodes)
        assert len(result) == 125  # type: ignore[arg-type]

    def test_read_126_fails(self) -> None:
        """Reading > 125 registers should return ILLEGAL_VALUE."""
        ctx = self._make_context()
        result = ctx.getValues(3, 0, 126)
        assert result == ExcCodes.ILLEGAL_VALUE

    def test_ir_read_limit(self) -> None:
        """FC04 also enforces the 125 limit."""
        ctx = self._make_context()
        result = ctx.getValues(4, 0, 126)
        assert result == ExcCodes.ILLEGAL_VALUE


# ---------------------------------------------------------------------------
# Full packaging register map test
# ---------------------------------------------------------------------------


class TestPackagingRegisterMap:
    """Verify the packaging config produces the correct register addresses."""

    @pytest.fixture
    def packaging_server(self) -> ModbusServer:
        config = load_config(_CONFIG_PATH, apply_env=False)
        store = SignalStore()
        return ModbusServer(config, store, port=15506)

    def test_press_hr_addresses(self, packaging_server: ModbusServer) -> None:
        """Press HR addresses match Appendix A."""
        rmap = packaging_server.register_map
        hr_addrs = {e.address: e for e in rmap.hr_entries}

        # Press process values
        assert 100 in hr_addrs  # line_speed
        assert hr_addrs[100].data_type == "float32"
        assert 102 in hr_addrs  # web_tension
        assert 110 in hr_addrs  # ink_viscosity
        assert 112 in hr_addrs  # ink_temperature

        # Press dryer temps
        assert 120 in hr_addrs  # dryer_temp_zone_1
        assert 122 in hr_addrs  # dryer_temp_zone_2
        assert 124 in hr_addrs  # dryer_temp_zone_3

        # Press dryer setpoints (writable)
        assert 140 in hr_addrs
        assert hr_addrs[140].writable is True
        assert 142 in hr_addrs
        assert 144 in hr_addrs

        # Press counters
        assert 200 in hr_addrs  # impression_count
        assert hr_addrs[200].data_type == "uint32"
        assert 202 in hr_addrs  # good_count
        assert 204 in hr_addrs  # waste_count

        # Press state
        assert 210 in hr_addrs  # machine_state
        assert hr_addrs[210].data_type == "uint16"
        assert 211 in hr_addrs  # fault_code
        assert hr_addrs[211].data_type == "uint16"

        # Press drive, nip, reels
        assert 300 in hr_addrs  # main_drive_current
        assert 302 in hr_addrs  # main_drive_speed
        assert 310 in hr_addrs  # nip_pressure
        assert 320 in hr_addrs  # unwind_diameter
        assert 322 in hr_addrs  # rewind_diameter

    def test_laminator_hr_addresses(self, packaging_server: ModbusServer) -> None:
        """Laminator HR addresses match Appendix A."""
        rmap = packaging_server.register_map
        hr_addrs = {e.address for e in rmap.hr_entries}

        assert 400 in hr_addrs  # nip_temp
        assert 402 in hr_addrs  # nip_pressure
        assert 404 in hr_addrs  # tunnel_temp
        assert 406 in hr_addrs  # web_speed
        assert 408 in hr_addrs  # adhesive_weight

    def test_slitter_hr_addresses(self, packaging_server: ModbusServer) -> None:
        """Slitter HR addresses match Appendix A."""
        rmap = packaging_server.register_map
        hr_addrs = {e.address for e in rmap.hr_entries}

        assert 500 in hr_addrs  # speed
        assert 502 in hr_addrs  # web_tension
        assert 510 in hr_addrs  # reel_count (uint32)

    def test_energy_hr_addresses(self, packaging_server: ModbusServer) -> None:
        """Energy HR addresses match Appendix A."""
        rmap = packaging_server.register_map
        hr_addrs = {e.address for e in rmap.hr_entries}

        assert 600 in hr_addrs  # line_power
        assert 602 in hr_addrs  # cumulative_kwh

    def test_ir_addresses(self, packaging_server: ModbusServer) -> None:
        """IR addresses match Appendix A."""
        rmap = packaging_server.register_map
        ir_addrs = {e.address: e for e in rmap.ir_entries}

        # Temperature registers (int16 x10)
        assert 0 in ir_addrs   # dryer_temp_zone_1
        assert ir_addrs[0].data_type == "int16_x10"
        assert 1 in ir_addrs   # dryer_temp_zone_2
        assert 2 in ir_addrs   # dryer_temp_zone_3
        assert 3 in ir_addrs   # ink_temperature
        assert 4 in ir_addrs   # laminator.nip_temp
        assert 5 in ir_addrs   # laminator.tunnel_temp

        # Energy (float32)
        assert 10 in ir_addrs  # energy.line_power
        assert ir_addrs[10].data_type == "float32"


# ---------------------------------------------------------------------------
# CDAB encoding tests (F&B Allen-Bradley byte order)
# ---------------------------------------------------------------------------


class TestFloat32CdabEncoding:
    """Test float32 CDAB (Allen-Bradley word-swap) encoding and decoding."""

    def test_round_trip_positive(self) -> None:
        lo, hi = encode_float32_cdab(150.0)
        decoded = decode_float32_cdab([lo, hi])
        assert abs(decoded - 150.0) < 0.01

    def test_round_trip_zero(self) -> None:
        lo, hi = encode_float32_cdab(0.0)
        assert lo == 0 and hi == 0
        assert decode_float32_cdab([lo, hi]) == 0.0

    def test_round_trip_negative(self) -> None:
        lo, hi = encode_float32_cdab(-42.5)
        decoded = decode_float32_cdab([lo, hi])
        assert abs(decoded - (-42.5)) < 0.01

    def test_cdab_word_order_differs_from_abcd(self) -> None:
        """CDAB must swap the two 16-bit words compared to ABCD."""
        value = 85.5
        abcd_hi, abcd_lo = encode_float32_abcd(value)
        cdab_lo, cdab_hi = encode_float32_cdab(value)
        # CDAB: register[0] = low word, register[1] = high word
        assert cdab_lo == abcd_lo  # low word is in position 0
        assert cdab_hi == abcd_hi  # high word is in position 1
        # So the two registers are swapped: CDAB[0] == ABCD[1]
        assert cdab_lo == abcd_lo
        assert cdab_hi == abcd_hi

    def test_cdab_not_equal_to_abcd_for_nonzero(self) -> None:
        """For a non-trivial value, CDAB and ABCD produce different register sequences."""
        value = 3.14
        abcd_hi, abcd_lo = encode_float32_abcd(value)
        cdab_r0, cdab_r1 = encode_float32_cdab(value)
        # CDAB[0] (low word) != ABCD[0] (high word) for 3.14
        assert (cdab_r0, cdab_r1) != (abcd_hi, abcd_lo)

    def test_round_trip_mixer_range(self) -> None:
        """Spot-check values in the mixer RPM range."""
        for rpm in [0.0, 500.0, 2000.0, 2999.9]:
            lo, hi = encode_float32_cdab(rpm)
            assert abs(decode_float32_cdab([lo, hi]) - rpm) < 0.01


class TestUint32CdabEncoding:
    """Test uint32 CDAB (Allen-Bradley word-swap) encoding and decoding."""

    def test_round_trip_small(self) -> None:
        lo, hi = encode_uint32_cdab(42)
        assert hi == 0
        assert lo == 42
        assert decode_uint32_cdab([lo, hi]) == 42

    def test_round_trip_large(self) -> None:
        value = 1_000_000
        lo, hi = encode_uint32_cdab(value)
        assert decode_uint32_cdab([lo, hi]) == value

    def test_round_trip_max(self) -> None:
        lo, hi = encode_uint32_cdab(0xFFFFFFFF)
        assert lo == 0xFFFF
        assert hi == 0xFFFF
        assert decode_uint32_cdab([lo, hi]) == 0xFFFFFFFF

    def test_clamp_negative(self) -> None:
        lo, hi = encode_uint32_cdab(-5)
        assert decode_uint32_cdab([lo, hi]) == 0

    def test_word_order_vs_abcd(self) -> None:
        """CDAB register[0] = low word, ABCD register[0] = high word."""
        value = 0x00010002  # high=0x0001, low=0x0002
        abcd_hi, abcd_lo = encode_uint32_abcd(value)
        cdab_lo, cdab_hi = encode_uint32_cdab(value)
        assert abcd_hi == 0x0001  # ABCD: high word first
        assert cdab_lo == 0x0002  # CDAB: low word first


# ---------------------------------------------------------------------------
# Dynamic block sizing tests
# ---------------------------------------------------------------------------


class TestDynamicBlockSizing:
    """Verify data blocks resize correctly for packaging vs F&B profiles."""

    def test_packaging_profile_uses_small_blocks(self) -> None:
        """Packaging profile max HR address is ~602: block must fit it."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        store = SignalStore()
        server = ModbusServer(config, store, port=15507)
        # The HR block must accommodate HR address 602-603 (energy)
        # With +1 offset, that's indices 603-604, so block size >= 605
        regs = server._hr_block.getValues(604, 1)
        assert regs is not None

    def test_fnb_profile_uses_large_blocks(self) -> None:
        """F&B profile max HR address is ~1506: block must be large enough."""
        from pathlib import Path
        fnb_config_path = Path(__file__).resolve().parents[3] / "config" / "factory-foodbev.yaml"
        from factory_simulator.config import load_config as lc
        config = lc(fnb_config_path, apply_env=False)
        store = SignalStore()
        server = ModbusServer(config, store, port=15508)
        # HR 1506-1507: index 1508 must exist in the block
        regs = server._hr_block.getValues(1508, 1)
        assert regs is not None

    def test_fnb_coil_block_covers_102(self) -> None:
        """F&B coil block must accommodate coil 102 (chiller.defrost_active)."""
        from pathlib import Path
        fnb_config_path = Path(__file__).resolve().parents[3] / "config" / "factory-foodbev.yaml"
        from factory_simulator.config import load_config as lc
        config = lc(fnb_config_path, apply_env=False)
        store = SignalStore()
        server = ModbusServer(config, store, port=15509)
        # Coil 102: index 103 must exist
        coils = server._coil_block.getValues(103, 1)
        assert coils is not None

    def test_fnb_di_block_covers_100(self) -> None:
        """F&B DI block must accommodate DI 100 (chiller.door_open)."""
        from pathlib import Path
        fnb_config_path = Path(__file__).resolve().parents[3] / "config" / "factory-foodbev.yaml"
        from factory_simulator.config import load_config as lc
        config = lc(fnb_config_path, apply_env=False)
        store = SignalStore()
        server = ModbusServer(config, store, port=15510)
        # DI 100: index 101 must exist
        dis = server._di_block.getValues(101, 1)
        assert dis is not None


# ---------------------------------------------------------------------------
# CDAB sync tests
# ---------------------------------------------------------------------------


class TestCdabSync:
    """Verify CDAB byte order is applied correctly during register sync."""

    def _make_cdab_config(self) -> FactoryConfig:
        return FactoryConfig(
            simulation=SimulationConfig(tick_interval_ms=100, random_seed=42),
            protocols=ProtocolsConfig(
                modbus=ModbusProtocolConfig(port=15511),
            ),
            equipment={
                "mixer": EquipmentConfig(
                    enabled=True,
                    type="test",
                    signals={
                        "speed": SignalConfig(
                            model="steady_state",
                            modbus_hr=[1000, 1001],
                            modbus_type="float32",
                            modbus_byte_order="CDAB",
                        ),
                        "mix_time": SignalConfig(
                            model="counter",
                            modbus_hr=[1010, 1011],
                            modbus_type="uint32",
                            modbus_byte_order="CDAB",
                        ),
                    },
                ),
            },
        )

    def test_float32_cdab_sync(self) -> None:
        """float32 CDAB signal syncs with word-swapped register layout."""
        config = self._make_cdab_config()
        store = SignalStore()
        store.set("mixer.speed", 2000.0, 0.0)

        server = ModbusServer(config, store, port=15512)
        server.sync_registers()

        # +1 offset: address 1000 -> index 1001
        regs = server._hr_block.getValues(1001, 2)
        decoded = decode_float32_cdab(list(regs))  # type: ignore[arg-type]
        assert abs(decoded - 2000.0) < 0.01

    def test_uint32_cdab_sync(self) -> None:
        """uint32 CDAB signal syncs with word-swapped register layout."""
        config = self._make_cdab_config()
        store = SignalStore()
        store.set("mixer.mix_time", 3600.0, 0.0)

        server = ModbusServer(config, store, port=15512)
        server.sync_registers()

        # +1 offset: address 1010 -> index 1011
        regs = server._hr_block.getValues(1011, 2)
        decoded = decode_uint32_cdab(list(regs))  # type: ignore[arg-type]
        assert decoded == 3600

    def test_cdab_not_interpreted_as_abcd(self) -> None:
        """CDAB-encoded registers must not decode correctly as ABCD."""
        config = self._make_cdab_config()
        store = SignalStore()
        store.set("mixer.speed", 2000.0, 0.0)

        server = ModbusServer(config, store, port=15512)
        server.sync_registers()

        regs = server._hr_block.getValues(1001, 2)
        # Decoding as ABCD should give a wrong value (unless the value is 0)
        wrong_decoded = decode_float32_abcd(list(regs))  # type: ignore[arg-type]
        assert abs(wrong_decoded - 2000.0) > 1.0  # must differ significantly


# ---------------------------------------------------------------------------
# F&B dynamic coil / DI tests
# ---------------------------------------------------------------------------


class TestFnbDynamicCoilsDI:
    """Verify F&B coil and DI defs are built from signal config fields."""

    def _make_fnb_coil_config(self) -> FactoryConfig:
        return FactoryConfig(
            simulation=SimulationConfig(tick_interval_ms=100, random_seed=42),
            protocols=ProtocolsConfig(
                modbus=ModbusProtocolConfig(port=15513),
            ),
            equipment={
                "chiller": EquipmentConfig(
                    enabled=True,
                    type="test",
                    signals={
                        "compressor_state": SignalConfig(
                            model="state_machine",
                            modbus_coil=101,
                            params={"states": ["off", "on"], "initial_state": "on"},
                        ),
                        "door_open": SignalConfig(
                            model="state_machine",
                            modbus_di=100,
                            params={"states": ["closed", "open"], "initial_state": "closed"},
                        ),
                    },
                ),
            },
        )

    def test_dynamic_coil_registered(self) -> None:
        """modbus_coil field creates a coil definition at the specified address."""
        config = self._make_fnb_coil_config()
        rmap = build_register_map(config)
        coil_addrs = {c.address for c in rmap.coil_defs}
        assert 101 in coil_addrs

    def test_dynamic_di_registered(self) -> None:
        """modbus_di field creates a DI definition at the specified address."""
        config = self._make_fnb_coil_config()
        rmap = build_register_map(config)
        di_addrs = {d.address for d in rmap.di_defs}
        assert 100 in di_addrs

    def test_coil_syncs_from_store(self) -> None:
        """Dynamic coil (gt_zero mode) syncs True when signal > 0."""
        config = self._make_fnb_coil_config()
        store = SignalStore()
        store.set("chiller.compressor_state", 1.0, 0.0)  # on

        server = ModbusServer(config, store, port=15514)
        server.sync_registers()

        coils = server._coil_block.getValues(102, 1)  # addr 101 + offset 1
        assert coils[0] is True

    def test_coil_off_when_zero(self) -> None:
        """Dynamic coil is False when signal == 0."""
        config = self._make_fnb_coil_config()
        store = SignalStore()
        store.set("chiller.compressor_state", 0.0, 0.0)  # off

        server = ModbusServer(config, store, port=15514)
        server.sync_registers()

        coils = server._coil_block.getValues(102, 1)  # addr 101 + offset 1
        assert coils[0] is False

    def test_di_syncs_from_store(self) -> None:
        """Dynamic DI (gt_zero mode) syncs True when signal > 0."""
        config = self._make_fnb_coil_config()
        store = SignalStore()
        store.set("chiller.door_open", 1.0, 0.0)  # open

        server = ModbusServer(config, store, port=15514)
        server.sync_registers()

        dis = server._di_block.getValues(101, 1)  # addr 100 + offset 1
        assert dis[0] is True


# ---------------------------------------------------------------------------
# Multi-slave Eurotherm zone controller tests (PRD 3.1.6)
# ---------------------------------------------------------------------------

_FNB_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "factory-foodbev.yaml"


def _make_multi_slave_config() -> FactoryConfig:
    """Build a minimal config with a secondary Modbus slave for testing.

    Simulates a single Eurotherm zone controller at UID 11 with:
    - IR 0: zone PV (int16 x10) — appears in BOTH main and secondary slave
    - IR 1: zone SP (int16 x10) — appears in BOTH main and secondary slave
    - IR 2: output power (int16 x10) — ONLY on secondary slave UID 11
    """
    return FactoryConfig(
        simulation=SimulationConfig(tick_interval_ms=100, random_seed=42),
        protocols=ProtocolsConfig(
            modbus=ModbusProtocolConfig(port=15515, unit_id=1),
        ),
        equipment={
            "oven": EquipmentConfig(
                enabled=True,
                type="test",
                signals={
                    "zone_1_temp": SignalConfig(
                        model="steady_state",
                        modbus_ir=[100],
                        modbus_slave_id=11,
                        modbus_slave_ir=[0],
                        params={"target": 160.0},
                    ),
                    "zone_1_setpoint": SignalConfig(
                        model="steady_state",
                        modbus_ir=[103],
                        modbus_slave_id=11,
                        modbus_slave_ir=[1],
                        params={"target": 160.0},
                    ),
                    "zone_1_output_power": SignalConfig(
                        model="steady_state",
                        modbus_slave_id=11,
                        modbus_ir=[2],  # exclusive to secondary slave
                        params={"target": 50.0},
                    ),
                },
            ),
        },
    )


class TestMultiSlaveRegisterMap:
    """Tests for secondary Modbus slave map building (PRD 3.1.6)."""

    def test_secondary_slave_discovered(self) -> None:
        """A signal with modbus_slave_id creates a secondary slave map."""
        config = _make_multi_slave_config()
        rmap = build_register_map(config)
        assert len(rmap.secondary_slaves) == 1
        assert rmap.secondary_slaves[0].slave_id == 11

    def test_secondary_slave_has_three_ir_entries(self) -> None:
        """Secondary slave UID 11 has PV (IR 0), SP (IR 1), output (IR 2)."""
        config = _make_multi_slave_config()
        rmap = build_register_map(config)
        slave = rmap.secondary_slaves[0]
        ir_addrs = {e.address: e.signal_id for e in slave.ir_entries}
        assert 0 in ir_addrs
        assert ir_addrs[0] == "oven.zone_1_temp"
        assert 1 in ir_addrs
        assert ir_addrs[1] == "oven.zone_1_setpoint"
        assert 2 in ir_addrs
        assert ir_addrs[2] == "oven.zone_1_output_power"

    def test_dual_mapped_signal_in_main_ir_block(self) -> None:
        """zone_1_temp (has modbus_slave_ir) also appears in main IR block."""
        config = _make_multi_slave_config()
        rmap = build_register_map(config)
        main_ir_addrs = {e.address: e.signal_id for e in rmap.ir_entries}
        # zone_1_temp at IR 100 and zone_1_setpoint at IR 103 must be present
        assert 100 in main_ir_addrs
        assert main_ir_addrs[100] == "oven.zone_1_temp"
        assert 103 in main_ir_addrs
        assert main_ir_addrs[103] == "oven.zone_1_setpoint"

    def test_exclusive_slave_signal_absent_from_main_ir(self) -> None:
        """zone_1_output_power (no modbus_slave_ir) is NOT in main IR block."""
        config = _make_multi_slave_config()
        rmap = build_register_map(config)
        main_ir_signals = {e.signal_id for e in rmap.ir_entries}
        assert "oven.zone_1_output_power" not in main_ir_signals

    def test_secondary_slave_ir_syncs(self) -> None:
        """Secondary slave IR block is populated from the signal store."""
        config = _make_multi_slave_config()
        store = SignalStore()
        store.set("oven.zone_1_temp", 170.5, 0.0)
        store.set("oven.zone_1_setpoint", 160.0, 0.0)
        store.set("oven.zone_1_output_power", 42.7, 0.0)

        server = ModbusServer(config, store, port=15516)
        server.sync_registers()

        # Secondary slave IR block for UID 11
        ir_block = server._secondary_ir_blocks[11]
        # IR 0 = zone_1_temp = 170.5 → int16 x10 = 1705
        pv_regs = ir_block.getValues(1, 1)  # addr 0 + offset 1
        assert abs(decode_int16_x10(pv_regs[0]) - 170.5) < 0.1

        # IR 1 = zone_1_setpoint = 160.0 → 1600
        sp_regs = ir_block.getValues(2, 1)  # addr 1 + offset 1
        assert abs(decode_int16_x10(sp_regs[0]) - 160.0) < 0.1

        # IR 2 = zone_1_output_power = 42.7 → 427
        pwr_regs = ir_block.getValues(3, 1)  # addr 2 + offset 1
        assert abs(decode_int16_x10(pwr_regs[0]) - 42.7) < 0.1

    def test_secondary_slave_context_created(self) -> None:
        """ModbusServer creates a secondary device context for each slave."""
        config = _make_multi_slave_config()
        store = SignalStore()
        server = ModbusServer(config, store, port=15517)
        assert 11 in server._secondary_contexts
        assert 11 in server._secondary_ir_blocks

    def test_packaging_config_has_no_secondary_slaves(self) -> None:
        """Packaging profile must have zero secondary slaves."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        rmap = build_register_map(config)
        assert len(rmap.secondary_slaves) == 0

    def test_missing_signal_does_not_crash_secondary_sync(self) -> None:
        """Secondary slave sync is tolerant of missing store signals."""
        config = _make_multi_slave_config()
        store = SignalStore()
        # Don't set any signal values

        server = ModbusServer(config, store, port=15518)
        # Should not raise
        server.sync_registers()
        ir_block = server._secondary_ir_blocks[11]
        regs = ir_block.getValues(1, 3)  # addr 0-2 + offset 1
        assert all(r == 0 for r in regs)


class TestFnbMultiSlaveConfig:
    """Verify F&B config creates correct secondary slaves for oven zones."""

    @pytest.fixture
    def fnb_rmap(self) -> object:
        config = load_config(_FNB_CONFIG_PATH, apply_env=False)
        return build_register_map(config)

    def test_three_secondary_slaves(self, fnb_rmap: object) -> None:
        """F&B config must produce 3 secondary slaves: UIDs 11, 12, 13."""
        assert hasattr(fnb_rmap, "secondary_slaves")
        slave_ids = {s.slave_id for s in fnb_rmap.secondary_slaves}  # type: ignore[union-attr]
        assert slave_ids == {11, 12, 13}

    def test_each_slave_has_pv_sp_output_power(self, fnb_rmap: object) -> None:
        """Each Eurotherm slave has IR 0 (PV), IR 1 (SP), IR 2 (output power)."""
        for slave in fnb_rmap.secondary_slaves:  # type: ignore[union-attr]
            ir_addrs = {e.address for e in slave.ir_entries}
            assert 0 in ir_addrs, f"UID {slave.slave_id} missing IR 0 (PV)"
            assert 1 in ir_addrs, f"UID {slave.slave_id} missing IR 1 (SP)"
            assert 2 in ir_addrs, f"UID {slave.slave_id} missing IR 2 (output power)"

    def test_zone_temps_still_in_main_ir_block(self, fnb_rmap: object) -> None:
        """Zone temps (dual-mapped) remain in main UID-1 IR block at 100-102."""
        main_ir_addrs = {e.address for e in fnb_rmap.ir_entries}  # type: ignore[union-attr]
        assert 100 in main_ir_addrs  # oven.zone_1_temp
        assert 101 in main_ir_addrs  # oven.zone_2_temp
        assert 102 in main_ir_addrs  # oven.zone_3_temp

    def test_zone_setpoints_still_in_main_ir_block(self, fnb_rmap: object) -> None:
        """Zone setpoints (dual-mapped) remain in main UID-1 IR block at 103-105."""
        main_ir_addrs = {e.address for e in fnb_rmap.ir_entries}  # type: ignore[union-attr]
        assert 103 in main_ir_addrs  # oven.zone_1_setpoint
        assert 104 in main_ir_addrs  # oven.zone_2_setpoint
        assert 105 in main_ir_addrs  # oven.zone_3_setpoint

    def test_output_power_not_in_main_ir_block(self, fnb_rmap: object) -> None:
        """Output power signals are exclusive to secondary slaves."""
        main_ir_signals = {e.signal_id for e in fnb_rmap.ir_entries}  # type: ignore[union-attr]
        assert "oven.zone_1_output_power" not in main_ir_signals
        assert "oven.zone_2_output_power" not in main_ir_signals
        assert "oven.zone_3_output_power" not in main_ir_signals

    def test_fnb_secondary_slave_sync(self) -> None:
        """F&B server syncs zone PV/SP/output to secondary slave IR blocks."""
        config = load_config(_FNB_CONFIG_PATH, apply_env=False)
        store = SignalStore()
        store.set("oven.zone_1_temp", 165.0, 0.0)
        store.set("oven.zone_1_setpoint", 160.0, 0.0)
        store.set("oven.zone_1_output_power", 55.0, 0.0)

        server = ModbusServer(config, store, port=15519)
        server.sync_registers()

        ir_block = server._secondary_ir_blocks[11]
        pv_regs = ir_block.getValues(1, 1)   # IR 0 + offset = PV
        sp_regs = ir_block.getValues(2, 1)   # IR 1 + offset = SP
        pwr_regs = ir_block.getValues(3, 1)  # IR 2 + offset = output power

        assert abs(decode_int16_x10(pv_regs[0]) - 165.0) < 0.1
        assert abs(decode_int16_x10(sp_regs[0]) - 160.0) < 0.1
        assert abs(decode_int16_x10(pwr_regs[0]) - 55.0) < 0.1
