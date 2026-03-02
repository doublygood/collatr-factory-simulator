"""Modbus TCP server for the Collatr Factory Simulator.

Reads signal values from the SignalStore and serves them over Modbus TCP
using pymodbus.  Implements the packaging profile register map from PRD
Appendix A with proper encoding for float32 (ABCD), uint32, uint16, and
int16 x10 (Eurotherm-style temperature registers).

Features:
- FC06 rejection for float32 register pairs (PRD 3.1.2)
- 125-register read limit per request (PRD 3.1.7)
- Derived coils from machine state (PRD Appendix A)
- Derived discrete inputs (PRD Appendix A)
- Periodic register synchronisation from the SignalStore

PRD Reference: Section 3.1, Appendix A (Modbus Register Map)
CLAUDE.md Rule 9: No locks (single writer, asyncio single-threaded).
CLAUDE.md Rule 10: Configuration via Pydantic.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.pdu.register_message import ExcCodes  # type: ignore[attr-defined]
from pymodbus.server import ModbusTcpServer

if TYPE_CHECKING:
    from factory_simulator.config import FactoryConfig
    from factory_simulator.store import SignalStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_READ_REGISTERS = 125  # PRD 3.1.7: Modbus spec limit per FC03/FC04 read

# Data block sizes -- enough to cover all packaging + shared registers.
# ModbusSequentialDataBlock is 1-indexed internally: address N maps to
# values[N+1].  Size must be > max_address + 2 for two-register pairs.
HR_BLOCK_SIZE = 705   # HR 0-703 (packaging 100-603, shared 600-603)
IR_BLOCK_SIZE = 25    # IR 0-23 (packaging 0-11)
COIL_BLOCK_SIZE = 15  # Coils 0-13 (packaging 0-5)
DI_BLOCK_SIZE = 15    # DI 0-13 (packaging 0-2)


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------


def encode_float32_abcd(value: float) -> tuple[int, int]:
    """Encode a float32 value into two uint16 registers (ABCD / big-endian).

    Returns ``(high_word, low_word)``.
    """
    packed = struct.pack(">f", value)
    high = int.from_bytes(packed[0:2], "big")
    low = int.from_bytes(packed[2:4], "big")
    return high, low


def decode_float32_abcd(regs: list[int]) -> float:
    """Decode two uint16 registers (ABCD) back to float32."""
    raw = struct.pack(">HH", regs[0], regs[1])
    return float(struct.unpack(">f", raw)[0])


def encode_uint32_abcd(value: int) -> tuple[int, int]:
    """Encode a uint32 value into two uint16 registers (big-endian).

    Returns ``(high_word, low_word)``.
    """
    clamped = max(0, min(int(value), 0xFFFFFFFF))
    high = (clamped >> 16) & 0xFFFF
    low = clamped & 0xFFFF
    return high, low


def decode_uint32_abcd(regs: list[int]) -> int:
    """Decode two uint16 registers (big-endian) back to uint32."""
    return (regs[0] << 16) | regs[1]


def encode_int16_x10(value: float) -> int:
    """Encode a float as int16 with x10 scaling, stored as uint16.

    Example: ``85.0`` C -> ``850``.  Stored as unsigned register value.
    Negative values use two's complement (e.g. ``-10.0`` -> ``65436``).
    """
    scaled = round(value * 10)
    scaled = max(-32768, min(32767, scaled))
    return scaled & 0xFFFF


def decode_int16_x10(reg: int) -> float:
    """Decode a uint16 register (int16 x10) back to float."""
    if reg >= 0x8000:
        reg -= 0x10000
    return reg / 10.0


# ---------------------------------------------------------------------------
# Register map entries
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HoldingRegisterEntry:
    """Mapping from a signal to one or two holding registers."""

    signal_id: str
    address: int          # Start address of the register(s)
    data_type: str        # "float32", "uint32", "uint16"
    writable: bool = False


@dataclass(slots=True)
class InputRegisterEntry:
    """Mapping from a signal to one or two input registers."""

    signal_id: str
    address: int          # Start address of the register(s)
    data_type: str        # "int16_x10", "float32"


@dataclass(slots=True)
class CoilDefinition:
    """A derived coil whose value comes from a signal in the store."""

    address: int
    signal_id: str | None  # None = always False
    derive_value: int = 0  # Coil is True when int(signal_value) == derive_value


@dataclass(slots=True)
class DiscreteInputDefinition:
    """A derived discrete input whose value comes from a signal in the store."""

    address: int
    signal_id: str | None  # None = always False
    mode: str = "false"    # "false", "eq", "modulo"
    eq_value: int = 0      # For mode="eq": True when int(signal_value) == eq_value


# ---------------------------------------------------------------------------
# Custom device context
# ---------------------------------------------------------------------------


class FactoryDeviceContext(ModbusDeviceContext):
    """Device context with FC06 rejection for float32 pairs and register limit.

    PRD 3.1.2: FC06 (Write Single Register) to float32 register pairs
    returns Modbus exception code 0x01 (Illegal Function).  Use FC16.

    PRD 3.1.7: Each read request is limited to 125 registers.  Reads
    requesting more return exception code 0x03 (Illegal Data Value).
    """

    def __init__(
        self,
        float32_addresses: set[int] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._float32_addresses = float32_addresses or set()

    def setValues(
        self,
        func_code: int,
        address: int,
        values: list[int] | list[bool],
    ) -> None | ExcCodes:
        """Reject FC06 on float32 register pairs."""
        if func_code == 6 and address in self._float32_addresses:
            return ExcCodes.ILLEGAL_FUNCTION
        return super().setValues(func_code, address, values)

    def getValues(
        self,
        func_code: int,
        address: int,
        count: int = 1,
    ) -> list[int] | list[bool] | ExcCodes:
        """Reject reads exceeding MAX_READ_REGISTERS."""
        if func_code in (3, 4) and count > MAX_READ_REGISTERS:
            return ExcCodes.ILLEGAL_VALUE
        return super().getValues(func_code, address, count)


# ---------------------------------------------------------------------------
# ModbusServer
# ---------------------------------------------------------------------------


@dataclass
class RegisterMap:
    """Collected register mappings for one factory profile."""

    hr_entries: list[HoldingRegisterEntry] = field(default_factory=list)
    ir_entries: list[InputRegisterEntry] = field(default_factory=list)
    coil_defs: list[CoilDefinition] = field(default_factory=list)
    di_defs: list[DiscreteInputDefinition] = field(default_factory=list)
    float32_hr_addresses: set[int] = field(default_factory=set)


def build_register_map(config: FactoryConfig) -> RegisterMap:
    """Build the complete register map from factory configuration.

    Scans all equipment signal configs for ``modbus_hr`` and ``modbus_ir``
    fields.  Coils and discrete inputs are hardcoded per PRD Appendix A
    (packaging profile).
    """
    rmap = RegisterMap()

    # -- Holding and input registers from signal configs --
    for eq_id, eq_cfg in config.equipment.items():
        if not eq_cfg.enabled:
            continue
        for sig_name, sig_cfg in eq_cfg.signals.items():
            signal_id = f"{eq_id}.{sig_name}"

            # Holding registers
            if sig_cfg.modbus_hr is not None and len(sig_cfg.modbus_hr) > 0:
                data_type = sig_cfg.modbus_type or "float32"
                rmap.hr_entries.append(HoldingRegisterEntry(
                    signal_id=signal_id,
                    address=sig_cfg.modbus_hr[0],
                    data_type=data_type,
                    writable=sig_cfg.modbus_writable,
                ))
                # Track float32 addresses for FC06 rejection
                if data_type == "float32":
                    rmap.float32_hr_addresses.add(sig_cfg.modbus_hr[0])
                    rmap.float32_hr_addresses.add(sig_cfg.modbus_hr[0] + 1)
                # uint32 also spans two registers: reject FC06 too
                if data_type == "uint32":
                    rmap.float32_hr_addresses.add(sig_cfg.modbus_hr[0])
                    rmap.float32_hr_addresses.add(sig_cfg.modbus_hr[0] + 1)

            # Input registers
            if sig_cfg.modbus_ir is not None and len(sig_cfg.modbus_ir) > 0:
                # 1 address = int16_x10 (temperature), 2 addresses = float32
                ir_type = "int16_x10" if len(sig_cfg.modbus_ir) == 1 else "float32"
                rmap.ir_entries.append(InputRegisterEntry(
                    signal_id=signal_id,
                    address=sig_cfg.modbus_ir[0],
                    data_type=ir_type,
                ))

    # -- Packaging profile coils (PRD Appendix A) --
    rmap.coil_defs = [
        CoilDefinition(0, "press.machine_state", derive_value=2),   # press.running
        CoilDefinition(1, "press.machine_state", derive_value=4),   # press.fault_active
        CoilDefinition(2, None),                                    # press.emergency_stop
        CoilDefinition(3, None),                                    # press.web_break
        CoilDefinition(4, "press.machine_state", derive_value=2),   # laminator.running
        CoilDefinition(5, "press.machine_state", derive_value=2),   # slitter.running
    ]

    # -- Packaging profile discrete inputs (PRD Appendix A) --
    rmap.di_defs = [
        DiscreteInputDefinition(0, None, mode="false"),              # guard_door_open
        DiscreteInputDefinition(                                     # material_present
            1, "press.machine_state", mode="eq", eq_value=2,
        ),
        DiscreteInputDefinition(                                     # cycle_complete
            2, "press.impression_count", mode="modulo",
        ),
    ]

    logger.info(
        "Modbus register map built: %d HR, %d IR, %d coils, %d DI",
        len(rmap.hr_entries), len(rmap.ir_entries),
        len(rmap.coil_defs), len(rmap.di_defs),
    )
    return rmap


class ModbusServer:
    """Modbus TCP server that reads from the SignalStore.

    Builds register maps from the factory configuration and periodically
    syncs signal values from the store to pymodbus data blocks.

    Parameters
    ----------
    config:
        Validated :class:`FactoryConfig`.
    store:
        Shared :class:`SignalStore` instance.
    host:
        Bind address override (for testing).  Defaults to config value.
    port:
        Port override (for testing).  Defaults to config value.
    """

    def __init__(
        self,
        config: FactoryConfig,
        store: SignalStore,
        *,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._modbus_cfg = config.protocols.modbus
        self._host = host or self._modbus_cfg.bind_address
        self._port = port or self._modbus_cfg.port

        # Build register map from config
        self._rmap = build_register_map(config)

        # Create pymodbus data blocks (no event loop required)
        self._hr_block = ModbusSequentialDataBlock(0, [0] * HR_BLOCK_SIZE)  # type: ignore[no-untyped-call]
        self._ir_block = ModbusSequentialDataBlock(0, [0] * IR_BLOCK_SIZE)  # type: ignore[no-untyped-call]
        self._coil_block = ModbusSequentialDataBlock(0, [False] * COIL_BLOCK_SIZE)  # type: ignore[no-untyped-call]
        self._di_block = ModbusSequentialDataBlock(0, [False] * DI_BLOCK_SIZE)  # type: ignore[no-untyped-call]

        # Custom device context with FC06 rejection + register limit
        self._device_context = FactoryDeviceContext(
            float32_addresses=self._rmap.float32_hr_addresses,
            hr=self._hr_block,
            ir=self._ir_block,
            co=self._coil_block,
            di=self._di_block,
        )

        # TCP server deferred to start() (requires running event loop)
        self._tcp_server: ModbusTcpServer | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._update_task: asyncio.Task[None] | None = None

    # -- Properties -----------------------------------------------------------

    @property
    def host(self) -> str:
        """Server bind address."""
        return self._host

    @property
    def port(self) -> int:
        """Server port."""
        return self._port

    @property
    def register_map(self) -> RegisterMap:
        """The register map (for testing and introspection)."""
        return self._rmap

    # -- Register synchronisation ---------------------------------------------

    def sync_registers(self) -> None:
        """Synchronise all signal values from store to Modbus registers.

        Call this periodically or explicitly before reading in tests.
        """
        self._sync_holding_registers()
        self._sync_input_registers()
        self._sync_coils()
        self._sync_discrete_inputs()

    def _sync_holding_registers(self) -> None:
        """Copy HR signal values from store into the data block.

        Note: ModbusDeviceContext adds +1 to addresses when mapping Modbus PDU
        addresses to data block offsets. We write directly to the data block,
        so we must apply the same +1 offset.
        """
        for entry in self._rmap.hr_entries:
            sv = self._store.get(entry.signal_id)
            if sv is None:
                continue
            value = sv.value
            if isinstance(value, str):
                continue

            addr = entry.address + 1  # +1: ModbusDeviceContext offset
            if entry.data_type == "float32":
                hi, lo = encode_float32_abcd(float(value))
                self._hr_block.setValues(addr, [hi, lo])
            elif entry.data_type == "uint32":
                hi, lo = encode_uint32_abcd(int(value))
                self._hr_block.setValues(addr, [hi, lo])
            elif entry.data_type == "uint16":
                self._hr_block.setValues(addr, [int(value) & 0xFFFF])

    def _sync_input_registers(self) -> None:
        """Copy IR signal values from store into the data block."""
        for entry in self._rmap.ir_entries:
            sv = self._store.get(entry.signal_id)
            if sv is None:
                continue
            value = sv.value
            if isinstance(value, str):
                continue

            addr = entry.address + 1  # +1: ModbusDeviceContext offset
            if entry.data_type == "int16_x10":
                encoded = encode_int16_x10(float(value))
                self._ir_block.setValues(addr, [encoded])
            elif entry.data_type == "float32":
                hi, lo = encode_float32_abcd(float(value))
                self._ir_block.setValues(addr, [hi, lo])

    def _sync_coils(self) -> None:
        """Derive coil values from signal store."""
        for coil_def in self._rmap.coil_defs:
            addr = coil_def.address + 1  # +1: ModbusDeviceContext offset
            if coil_def.signal_id is None:
                self._coil_block.setValues(addr, [False])
                continue
            sv = self._store.get(coil_def.signal_id)
            if sv is None:
                self._coil_block.setValues(addr, [False])
                continue
            value = sv.value
            if isinstance(value, str):
                self._coil_block.setValues(addr, [False])
                continue
            is_active = int(value) == coil_def.derive_value
            self._coil_block.setValues(addr, [is_active])

    def _sync_discrete_inputs(self) -> None:
        """Derive discrete input values from signal store."""
        for di_def in self._rmap.di_defs:
            addr = di_def.address + 1  # +1: ModbusDeviceContext offset
            if di_def.signal_id is None or di_def.mode == "false":
                self._di_block.setValues(addr, [False])
                continue
            sv = self._store.get(di_def.signal_id)
            if sv is None:
                self._di_block.setValues(addr, [False])
                continue
            value = sv.value
            if isinstance(value, str):
                self._di_block.setValues(addr, [False])
                continue

            if di_def.mode == "eq":
                result = int(value) == di_def.eq_value
            elif di_def.mode == "modulo":
                result = bool(int(value) % 2)
            else:
                result = False
            self._di_block.setValues(addr, [result])

    # -- Async lifecycle ------------------------------------------------------

    async def start(self) -> None:
        """Start the Modbus TCP server and register update loop."""
        server_context = ModbusServerContext(  # type: ignore[no-untyped-call]
            devices=self._device_context, single=True,
        )
        self._tcp_server = ModbusTcpServer(
            server_context,
            address=(self._host, self._port),
        )
        self._server_task = asyncio.create_task(
            self._tcp_server.serve_forever(),
        )
        self._update_task = asyncio.create_task(self._update_loop())
        logger.info("Modbus server started on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        """Stop the Modbus TCP server and update loop."""
        if self._update_task is not None:
            self._update_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._update_task
            self._update_task = None

        if self._tcp_server is not None:
            await self._tcp_server.shutdown()  # type: ignore[no-untyped-call]

        if self._server_task is not None:
            self._server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._server_task
            self._server_task = None

        logger.info("Modbus server stopped")

    async def _update_loop(self) -> None:
        """Periodically sync register values from the store."""
        try:
            while True:
                self.sync_registers()
                await asyncio.sleep(0.05)  # 50ms update interval
        except asyncio.CancelledError:
            pass
