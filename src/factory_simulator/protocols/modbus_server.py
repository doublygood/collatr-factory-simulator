"""Modbus TCP server for the Collatr Factory Simulator.

Reads signal values from the SignalStore and serves them over Modbus TCP
using pymodbus.  Implements the packaging and F&B profile register maps from
PRD Appendix A with proper encoding for float32 (ABCD or CDAB), uint32,
uint16, and int16 x10 (Eurotherm-style temperature registers).

Features:
- FC06 rejection for float32 register pairs (PRD 3.1.2)
- 125-register read limit per request (PRD 3.1.7)
- Derived coils from machine state (PRD Appendix A)
- Derived discrete inputs (PRD Appendix A)
- Dynamic coils/DIs from signal ``modbus_coil``/``modbus_di`` config fields
- CDAB byte order for Allen-Bradley CompactLogix mixer (PRD 3.1 / Appendix A)
- Dynamic data block sizing from the register map (no hardcoded limits)
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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.pdu.register_message import ExcCodes  # type: ignore[attr-defined]
from pymodbus.server import ModbusTcpServer

from factory_simulator.protocols.comm_drop import CommDropScheduler

if TYPE_CHECKING:
    from factory_simulator.config import (
        FactoryConfig,
        PartialModbusResponseConfig,
    )
    from factory_simulator.store import SignalStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_READ_REGISTERS = 125  # PRD 3.1.7: Modbus spec limit per FC03/FC04 read


# ---------------------------------------------------------------------------
# Encoding helpers — ABCD (big-endian, default)
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


# ---------------------------------------------------------------------------
# Encoding helpers — CDAB (Allen-Bradley word-swap)
# ---------------------------------------------------------------------------


def encode_float32_cdab(value: float) -> tuple[int, int]:
    """Encode a float32 value into two uint16 registers (CDAB / word-swap).

    Allen-Bradley CompactLogix word order: low word first, high word second.
    Returns ``(low_word, high_word)`` so register[0]=low, register[1]=high.
    """
    packed = struct.pack(">f", value)
    high = int.from_bytes(packed[0:2], "big")
    low = int.from_bytes(packed[2:4], "big")
    return low, high  # CDAB: low word in register[0]


def decode_float32_cdab(regs: list[int]) -> float:
    """Decode two uint16 registers (CDAB) back to float32.

    regs[0] = low word, regs[1] = high word (Allen-Bradley convention).
    """
    raw = struct.pack(">HH", regs[1], regs[0])
    return float(struct.unpack(">f", raw)[0])


def encode_uint32_cdab(value: int) -> tuple[int, int]:
    """Encode a uint32 value into two uint16 registers (CDAB / word-swap).

    Returns ``(low_word, high_word)``.
    """
    clamped = max(0, min(int(value), 0xFFFFFFFF))
    high = (clamped >> 16) & 0xFFFF
    low = clamped & 0xFFFF
    return low, high  # CDAB: low word in register[0]


def decode_uint32_cdab(regs: list[int]) -> int:
    """Decode two uint16 registers (CDAB) back to uint32.

    regs[0] = low word, regs[1] = high word.
    """
    return (regs[1] << 16) | regs[0]


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
    byte_order: str = "ABCD"  # "ABCD" (default) or "CDAB" (Allen-Bradley)


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
    mode: str = "eq"  # "eq" = value == derive_value, "gt_zero" = value > 0


@dataclass(slots=True)
class DiscreteInputDefinition:
    """A derived discrete input whose value comes from a signal in the store."""

    address: int
    signal_id: str | None  # None = always False
    mode: str = "false"    # "false", "eq", "modulo", "gt_zero"
    eq_value: int = 0      # For mode="eq": True when int(signal_value) == eq_value


# ---------------------------------------------------------------------------
# Exception / partial response injector
# ---------------------------------------------------------------------------


class ModbusExceptionInjector:
    """Injects Modbus exception responses and partial responses (PRD 10.6, 10.11).

    Exception 0x04 (Device Failure) is injected randomly at
    ``exception_probability`` per read request.  Exception 0x06 (Device Busy)
    fires when the caller signals a machine-state transition.  Partial
    responses truncate multi-register reads at ``partial_cfg.probability``.

    All draws use the supplied numpy RNG for deterministic reproduction.

    Parameters
    ----------
    rng:
        Numpy RNG for deterministic draws.
    exception_probability:
        Probability of injecting a 0x04 exception per read request.
    partial_cfg:
        :class:`~factory_simulator.config.PartialModbusResponseConfig`.
    """

    def __init__(
        self,
        rng: np.random.Generator,
        exception_probability: float,
        partial_cfg: PartialModbusResponseConfig,
    ) -> None:
        self._rng = rng
        self._exception_prob = exception_probability
        self._partial_cfg = partial_cfg

        # Counters and event log (for testing and ground truth plumbing)
        self.exception_0x04_count: int = 0
        self.exception_0x06_count: int = 0
        self.partial_response_count: int = 0
        # Each entry: {controller_id, start_address, requested_count, returned_count}
        self.partial_events: list[dict[str, object]] = []

    def check_exception_0x04(self) -> bool:
        """Return True if a 0x04 (Device Failure) exception should fire.

        Performs one random draw; increments ``exception_0x04_count`` on hit.
        """
        if self._exception_prob <= 0.0:
            return False
        if bool(self._rng.random() < self._exception_prob):
            self.exception_0x04_count += 1
            return True
        return False

    def check_exception_0x06(self, transition_active: bool) -> bool:
        """Return True if a 0x06 (Device Busy) exception should fire.

        Fires deterministically whenever *transition_active* is True; no
        random draw is made.  Increments ``exception_0x06_count`` on hit.
        """
        if transition_active:
            self.exception_0x06_count += 1
            return True
        return False

    def check_partial(self, count: int) -> int | None:
        """Return a truncated register count, or ``None`` if no injection.

        PRD 10.11: Single-register reads are never partial (count < 2).
        For multi-register reads the truncated count N is drawn uniformly
        from 1 to count-1 inclusive.  Increments ``partial_response_count``
        on hit.
        """
        if not self._partial_cfg.enabled or count < 2:
            return None
        if not bool(self._rng.random() < self._partial_cfg.probability):
            return None
        # integers(low, high) returns [low, high) → gives 1 to count-1
        truncated = int(self._rng.integers(1, count))
        self.partial_response_count += 1
        return truncated

    def record_partial(
        self,
        controller_id: int,
        address: int,
        requested: int,
        returned: int,
    ) -> None:
        """Record a partial response event for ground truth logging.

        Called by :class:`FactoryDeviceContext` after injecting a partial
        response.  The event is stored in ``partial_events`` so that callers
        (e.g. :class:`ModbusServer`) can flush it to the ground truth log.
        """
        self.partial_events.append({
            "controller_id": controller_id,
            "start_address": address,
            "requested_count": requested,
            "returned_count": returned,
        })
        logger.debug(
            "partial_modbus_response: ctrl=%d addr=%d req=%d ret=%d",
            controller_id, address, requested, returned,
        )


# ---------------------------------------------------------------------------
# Custom device context
# ---------------------------------------------------------------------------


class FactoryDeviceContext(ModbusDeviceContext):
    """Device context with FC06 rejection, register limit, and data quality injection.

    PRD 3.1.2: FC06 (Write Single Register) to float32 register pairs
    returns Modbus exception code 0x01 (Illegal Function).  Use FC16.

    PRD 3.1.7: Each read request is limited to 125 registers.  Reads
    requesting more return exception code 0x03 (Illegal Data Value).

    PRD 10.6, 10.11: Optional exception and partial response injection via
    :class:`ModbusExceptionInjector`.  Injection only applies to FC03/FC04.
    """

    def __init__(
        self,
        float32_addresses: set[int] | None = None,
        exception_injector: ModbusExceptionInjector | None = None,
        transition_active_fn: Callable[[], bool] | None = None,
        unit_id: int = 1,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._float32_addresses = float32_addresses or set()
        self._exception_injector = exception_injector
        self._transition_active_fn = transition_active_fn
        self._unit_id = unit_id

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
        """Reject over-limit reads; inject exceptions and partial responses.

        Check order (PRD 10.6, 10.11):
        1. Register limit (0x03) — always enforced.
        2. 0x06 Device Busy — during machine state transitions (deterministic).
        3. 0x04 Device Failure — random draw at exception_probability.
        4. Partial response — random draw; returns truncated register slice.
        5. Normal response — full data returned.
        """
        if func_code in (3, 4) and count > MAX_READ_REGISTERS:
            return ExcCodes.ILLEGAL_VALUE

        if self._exception_injector is not None and func_code in (3, 4):
            # 0x06: Device Busy during machine state transitions
            transition = (
                self._transition_active_fn()
                if self._transition_active_fn is not None
                else False
            )
            if self._exception_injector.check_exception_0x06(transition):
                return ExcCodes.DEVICE_BUSY

            # 0x04: Random device failure
            if self._exception_injector.check_exception_0x04():
                return ExcCodes.DEVICE_FAILURE

            # Partial response (PRD 10.11): truncate multi-register reads
            partial = self._exception_injector.check_partial(count)
            if partial is not None:
                result = super().getValues(func_code, address, partial)
                self._exception_injector.record_partial(
                    self._unit_id, address, count, partial,
                )
                return result

        return super().getValues(func_code, address, count)


# ---------------------------------------------------------------------------
# ModbusServer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SecondarySlaveEntry:
    """An input register entry on a secondary Modbus slave (Eurotherm zone controller)."""

    signal_id: str
    address: int       # IR address on the secondary slave
    data_type: str = "int16_x10"


@dataclass
class SecondarySlaveRegisterMap:
    """Register mapping for one secondary Modbus slave (one Eurotherm unit)."""

    slave_id: int
    ir_entries: list[SecondarySlaveEntry] = field(default_factory=list)


@dataclass
class RegisterMap:
    """Collected register mappings for one factory profile."""

    hr_entries: list[HoldingRegisterEntry] = field(default_factory=list)
    ir_entries: list[InputRegisterEntry] = field(default_factory=list)
    coil_defs: list[CoilDefinition] = field(default_factory=list)
    di_defs: list[DiscreteInputDefinition] = field(default_factory=list)
    float32_hr_addresses: set[int] = field(default_factory=set)
    secondary_slaves: list[SecondarySlaveRegisterMap] = field(default_factory=list)


def build_register_map(config: FactoryConfig) -> RegisterMap:
    """Build the complete register map from factory configuration.

    Scans all equipment signal configs for ``modbus_hr``, ``modbus_ir``,
    ``modbus_coil``, and ``modbus_di`` fields.

    Packaging profile coils (0-5) and discrete inputs (0-2) are hardcoded
    per PRD Appendix A.  F&B profile coils (100-102) and DI (100) are derived
    dynamically from the ``modbus_coil``/``modbus_di`` signal config fields.

    Signals with ``modbus_slave_id`` set are excluded from the main IR block
    (they belong to secondary Modbus slaves, handled by multi-slave support
    in task 3.13).
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
                byte_order = sig_cfg.modbus_byte_order
                rmap.hr_entries.append(HoldingRegisterEntry(
                    signal_id=signal_id,
                    address=sig_cfg.modbus_hr[0],
                    data_type=data_type,
                    writable=sig_cfg.modbus_writable,
                    byte_order=byte_order,
                ))
                # Track float32 and uint32 addresses for FC06 rejection
                # (both span two registers; FC06 must not write to either word)
                if data_type in ("float32", "uint32"):
                    rmap.float32_hr_addresses.add(sig_cfg.modbus_hr[0])
                    rmap.float32_hr_addresses.add(sig_cfg.modbus_hr[0] + 1)

            # Input registers — skip signals that belong *exclusively* to a
            # secondary slave (modbus_slave_id set, but no modbus_slave_ir
            # means modbus_ir holds the secondary-slave address, not a main
            # UID-1 address).  Signals with modbus_slave_ir serve both the
            # main UID-1 block (via modbus_ir) and a secondary slave block.
            if sig_cfg.modbus_ir is not None and len(sig_cfg.modbus_ir) > 0:
                if (
                    sig_cfg.modbus_slave_id is not None
                    and sig_cfg.modbus_slave_ir is None
                ):
                    # Exclusive to secondary slave — skip from main IR block
                    continue
                # 1 address = int16_x10 (temperature), 2 addresses = float32
                ir_type = "int16_x10" if len(sig_cfg.modbus_ir) == 1 else "float32"
                rmap.ir_entries.append(InputRegisterEntry(
                    signal_id=signal_id,
                    address=sig_cfg.modbus_ir[0],
                    data_type=ir_type,
                ))

    # -- Packaging profile coils (PRD Appendix A, addresses 0-5) --
    rmap.coil_defs = [
        CoilDefinition(0, "press.machine_state", derive_value=2),   # press.running
        CoilDefinition(1, "press.machine_state", derive_value=4),   # press.fault_active
        CoilDefinition(2, None),                                    # press.emergency_stop
        CoilDefinition(3, "press.web_break", mode="gt_zero"),        # press.web_break
        CoilDefinition(4, "press.machine_state", derive_value=2),   # laminator.running
        CoilDefinition(5, "slitter.speed", mode="gt_zero"),          # slitter.running
    ]

    # -- Packaging profile discrete inputs (PRD Appendix A, addresses 0-2) --
    rmap.di_defs = [
        DiscreteInputDefinition(0, None, mode="false"),              # guard_door_open
        DiscreteInputDefinition(                                     # material_present
            1, "press.machine_state", mode="eq", eq_value=2,
        ),
        DiscreteInputDefinition(                                     # cycle_complete
            2, "press.impression_count", mode="modulo",
        ),
    ]

    # -- Dynamic coils and discrete inputs from signal configs (F&B profile) --
    # Signals with modbus_coil or modbus_di fields (e.g. mixer.lid_closed at
    # coil 100, chiller.compressor_state at coil 101, chiller.door_open at
    # DI 100).  Use "gt_zero" mode: value > 0 maps to True.
    for eq_id, eq_cfg in config.equipment.items():
        if not eq_cfg.enabled:
            continue
        for sig_name, sig_cfg in eq_cfg.signals.items():
            signal_id = f"{eq_id}.{sig_name}"
            if sig_cfg.modbus_coil is not None:
                rmap.coil_defs.append(
                    CoilDefinition(sig_cfg.modbus_coil, signal_id, mode="gt_zero"),
                )
            if sig_cfg.modbus_di is not None:
                rmap.di_defs.append(
                    DiscreteInputDefinition(
                        sig_cfg.modbus_di, signal_id, mode="gt_zero",
                    ),
                )

    # -- Secondary slave IR maps (F&B oven Eurotherm zone controllers) --
    # Signals with modbus_slave_id contribute to secondary slave IR blocks.
    # The IR address is taken from modbus_slave_ir (if set) or modbus_ir
    # (backward-compatible for signals that use modbus_ir as the slave address).
    secondary_slave_dict: dict[int, SecondarySlaveRegisterMap] = {}
    for eq_id, eq_cfg in config.equipment.items():
        if not eq_cfg.enabled:
            continue
        for sig_name, sig_cfg in eq_cfg.signals.items():
            if sig_cfg.modbus_slave_id is None:
                continue
            # Determine the IR address on the secondary slave
            if sig_cfg.modbus_slave_ir is not None and len(sig_cfg.modbus_slave_ir) > 0:
                secondary_ir_addr = sig_cfg.modbus_slave_ir[0]
            elif sig_cfg.modbus_ir is not None and len(sig_cfg.modbus_ir) > 0:
                secondary_ir_addr = sig_cfg.modbus_ir[0]
            else:
                continue  # No IR address configured for this slave signal

            slave_id = sig_cfg.modbus_slave_id
            if slave_id not in secondary_slave_dict:
                secondary_slave_dict[slave_id] = SecondarySlaveRegisterMap(
                    slave_id=slave_id,
                )
            signal_id = f"{eq_id}.{sig_name}"
            secondary_slave_dict[slave_id].ir_entries.append(
                SecondarySlaveEntry(
                    signal_id=signal_id,
                    address=secondary_ir_addr,
                    data_type="int16_x10",
                ),
            )

    rmap.secondary_slaves = list(secondary_slave_dict.values())

    logger.info(
        "Modbus register map built: %d HR, %d IR, %d coils, %d DI, %d secondary slaves",
        len(rmap.hr_entries), len(rmap.ir_entries),
        len(rmap.coil_defs), len(rmap.di_defs),
        len(rmap.secondary_slaves),
    )
    return rmap


def _compute_block_size(addresses: list[int], min_size: int = 16) -> int:
    """Compute the data block size needed to hold all register addresses.

    ModbusDeviceContext adds +1 to addresses (Modbus PDU address 0 -> block
    index 1).  A float32/uint32 entry at address N uses block indices N+1
    and N+2.  Adding 3 to the maximum start address covers this safely.
    """
    if not addresses:
        return min_size
    return max(max(addresses) + 3, min_size)


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
        comm_drop_rng: np.random.Generator | None = None,
        exception_rng: np.random.Generator | None = None,
        duplicate_rng: np.random.Generator | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._modbus_cfg = config.protocols.modbus
        self._host = host or self._modbus_cfg.bind_address
        self._port = port or self._modbus_cfg.port

        # Communication drop scheduler (PRD 10.2)
        _rng = comm_drop_rng if comm_drop_rng is not None else np.random.default_rng()
        self._drop_scheduler = CommDropScheduler(
            config.data_quality.modbus_drop, _rng,
        )

        # Exception / partial response injector (PRD 10.6, 10.11)
        _exc_rng = exception_rng if exception_rng is not None else np.random.default_rng()
        self._exception_injector = ModbusExceptionInjector(
            _exc_rng,
            config.data_quality.exception_probability,
            config.data_quality.partial_modbus_response,
        )

        # Duplicate timestamp injection (PRD 10.5): skip register sync at
        # duplicate_probability so registers hold identical values on the next
        # client read (same value + effectively same internal timestamp).
        self._dup_rng: np.random.Generator | None = duplicate_rng
        self._dup_prob: float = config.data_quality.duplicate_probability

        # Machine state transition tracking for 0x06 injection
        self._last_machine_state: int = -1
        self._transition_ts: float = -float("inf")
        _TRANSITION_WINDOW_S: float = 0.5  # seconds window after transition
        self._transition_window_s: float = _TRANSITION_WINDOW_S

        # Build register map from config
        self._rmap = build_register_map(config)

        # Dynamic block sizes: computed from the actual register map so
        # both the packaging profile (max HR ~603) and the F&B profile
        # (max HR ~1507) are accommodated without hardcoded constants.
        hr_size = _compute_block_size(
            [e.address for e in self._rmap.hr_entries], min_size=16,
        )
        ir_size = _compute_block_size(
            [e.address for e in self._rmap.ir_entries], min_size=16,
        )
        coil_size = _compute_block_size(
            [c.address for c in self._rmap.coil_defs], min_size=16,
        )
        di_size = _compute_block_size(
            [d.address for d in self._rmap.di_defs], min_size=16,
        )

        # Create pymodbus data blocks (no event loop required)
        self._hr_block = ModbusSequentialDataBlock(0, [0] * hr_size)  # type: ignore[no-untyped-call]
        self._ir_block = ModbusSequentialDataBlock(0, [0] * ir_size)  # type: ignore[no-untyped-call]
        self._coil_block = ModbusSequentialDataBlock(0, [False] * coil_size)  # type: ignore[no-untyped-call]
        self._di_block = ModbusSequentialDataBlock(0, [False] * di_size)  # type: ignore[no-untyped-call]

        # Custom device context with FC06 rejection, register limit, and injection
        self._device_context = FactoryDeviceContext(
            float32_addresses=self._rmap.float32_hr_addresses,
            exception_injector=self._exception_injector,
            transition_active_fn=self._is_transition_active,
            unit_id=self._modbus_cfg.unit_id,
            hr=self._hr_block,
            ir=self._ir_block,
            co=self._coil_block,
            di=self._di_block,
        )

        # Build secondary slave IR blocks and device contexts.
        # Each secondary slave (Eurotherm UID 11-13) gets its own IR block.
        # HR/coil/DI are minimal stubs (secondary slaves expose IR only).
        self._secondary_ir_blocks: dict[int, ModbusSequentialDataBlock] = {}
        self._secondary_contexts: dict[int, FactoryDeviceContext] = {}
        for slave_map in self._rmap.secondary_slaves:
            ir_addrs = [e.address for e in slave_map.ir_entries]
            ir_size = _compute_block_size(ir_addrs, min_size=8)
            ir_block: ModbusSequentialDataBlock = ModbusSequentialDataBlock(  # type: ignore[no-untyped-call]
                0, [0] * ir_size,
            )
            self._secondary_ir_blocks[slave_map.slave_id] = ir_block
            # Minimal stubs for HR/coil/DI — secondary slaves only serve IR
            _stub_hr = ModbusSequentialDataBlock(0, [0] * 8)  # type: ignore[no-untyped-call]
            _stub_co = ModbusSequentialDataBlock(0, [False] * 8)  # type: ignore[no-untyped-call]
            _stub_di = ModbusSequentialDataBlock(0, [False] * 8)  # type: ignore[no-untyped-call]
            self._secondary_contexts[slave_map.slave_id] = FactoryDeviceContext(
                float32_addresses=set(),
                hr=_stub_hr,
                ir=ir_block,
                co=_stub_co,
                di=_stub_di,
            )

        # Track last-synced register values for writable HR entries.
        # Used to detect client FC16 writes: if the register block value
        # differs from what we last wrote, a client must have changed it.
        # Same pattern as OpcuaServer._last_written_setpoints.
        self._last_hr_sync: dict[int, list[int]] = {}

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

    @property
    def comm_drop_active(self) -> bool:
        """True if a Modbus communication drop is currently active (PRD 10.2)."""
        t = time.monotonic()
        self._drop_scheduler.update(t)
        return self._drop_scheduler.is_active(t)

    @property
    def exception_injector(self) -> ModbusExceptionInjector:
        """Exception / partial response injector (for testing and introspection)."""
        return self._exception_injector

    # -- Exception injection helpers ------------------------------------------

    def _is_transition_active(self) -> bool:
        """Return True if a machine state transition occurred within the window.

        Called by :class:`FactoryDeviceContext` to determine whether to inject
        a 0x06 (Device Busy) exception on the current read request.
        """
        return time.monotonic() - self._transition_ts < self._transition_window_s

    def _check_machine_state_transition(self) -> None:
        """Update transition tracking based on the current machine state.

        Reads ``press.machine_state`` from the store.  If the state has
        changed since the last call, records the transition timestamp so that
        :meth:`_is_transition_active` returns True for the next window.
        """
        sv = self._store.get("press.machine_state")
        if sv is None:
            return
        state = int(sv.value) if isinstance(sv.value, int | float) else -1
        if self._last_machine_state >= 0 and state != self._last_machine_state:
            self._transition_ts = time.monotonic()
            logger.debug(
                "modbus: machine state transition %d → %d",
                self._last_machine_state, state,
            )
        self._last_machine_state = state

    # -- Register synchronisation ---------------------------------------------

    def sync_registers(self) -> None:
        """Synchronise all signal values from store to Modbus registers.

        Also updates machine state transition tracking so that the
        exception injector can fire 0x06 on reads that follow a state change.

        Call this periodically or explicitly before reading in tests.
        """
        self._check_machine_state_transition()
        self._sync_holding_registers()
        self._sync_input_registers()
        self._sync_coils()
        self._sync_discrete_inputs()
        self._sync_secondary_slaves()

    def _sync_holding_registers(self) -> None:
        """Sync HR values between store and the data block.

        Read-only registers: store -> register (one-way).
        Writable registers: bidirectional.  Detects client FC16 writes by
        comparing the current register value with the last value we synced
        from the store.  If the register changed, a client wrote it and the
        new value is propagated to the store.  Then the normal store ->
        register sync runs (which now contains the client-written value).

        The byte order (ABCD or CDAB) is read per-entry from the register map
        so that Allen-Bradley CDAB registers are encoded and decoded correctly.

        Note: ModbusDeviceContext adds +1 to addresses when mapping Modbus PDU
        addresses to data block offsets. We write directly to the data block,
        so we must apply the same +1 offset.
        """
        for entry in self._rmap.hr_entries:
            addr = entry.address + 1  # +1: ModbusDeviceContext offset

            # Phase 1: Detect client writes on writable registers
            if entry.writable:
                reg_count = 2 if entry.data_type in ("float32", "uint32") else 1
                raw = self._hr_block.getValues(addr, reg_count)
                current_regs: list[int] = list(raw)  # type: ignore[arg-type]
                last_synced = self._last_hr_sync.get(entry.address)

                if last_synced is not None and current_regs != last_synced:
                    # Register changed since our last sync -> client FC16 write.
                    # Propagate the new value to the store (PRD 3.1.7).
                    decoded = self._decode_hr_value(
                        entry.data_type, current_regs, entry.byte_order,
                    )
                    if decoded is not None:
                        self._store.set(
                            entry.signal_id, float(decoded), 0.0, "good",
                        )
                        logger.debug(
                            "Modbus setpoint write-back: %s = %s",
                            entry.signal_id, decoded,
                        )

            # Phase 2: Store -> register (normal sync for all entries)
            sv = self._store.get(entry.signal_id)
            if sv is None:
                continue
            value = sv.value
            if isinstance(value, str):
                continue

            if entry.data_type == "float32":
                if entry.byte_order == "CDAB":
                    lo, hi = encode_float32_cdab(float(value))
                    regs = [lo, hi]
                else:
                    hi, lo = encode_float32_abcd(float(value))
                    regs = [hi, lo]
            elif entry.data_type == "uint32":
                if entry.byte_order == "CDAB":
                    lo, hi = encode_uint32_cdab(int(value))
                    regs = [lo, hi]
                else:
                    hi, lo = encode_uint32_abcd(int(value))
                    regs = [hi, lo]
            elif entry.data_type == "uint16":
                regs = [int(value) & 0xFFFF]
            else:
                continue

            self._hr_block.setValues(addr, regs)

            # Track writable registers for next-cycle client write detection
            if entry.writable:
                self._last_hr_sync[entry.address] = regs

    @staticmethod
    def _decode_hr_value(
        data_type: str,
        regs: list[int],
        byte_order: str = "ABCD",
    ) -> float | int | None:
        """Decode register values back to a store-compatible numeric value."""
        if data_type == "float32":
            return (
                decode_float32_cdab(regs)
                if byte_order == "CDAB"
                else decode_float32_abcd(regs)
            )
        if data_type == "uint32":
            return (
                decode_uint32_cdab(regs)
                if byte_order == "CDAB"
                else decode_uint32_abcd(regs)
            )
        if data_type == "uint16":
            return regs[0]
        return None

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
            if coil_def.mode == "gt_zero":
                is_active = float(value) > 0.0
            else:
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
            elif di_def.mode == "gt_zero":
                result = float(value) > 0.0
            else:
                result = False
            self._di_block.setValues(addr, [result])

    def _sync_secondary_slaves(self) -> None:
        """Copy signal values into secondary slave IR blocks (Eurotherm controllers).

        Each secondary slave (UID 11-13) serves int16 x10 input registers for
        oven zone PV (IR 0), SP (IR 1), and output power (IR 2).
        Note: ModbusDeviceContext adds +1 to addresses; apply same +1 offset.
        """
        for slave_map in self._rmap.secondary_slaves:
            ir_block = self._secondary_ir_blocks[slave_map.slave_id]
            for entry in slave_map.ir_entries:
                sv = self._store.get(entry.signal_id)
                if sv is None:
                    continue
                value = sv.value
                if isinstance(value, str):
                    continue
                addr = entry.address + 1  # +1: ModbusDeviceContext offset
                if entry.data_type == "int16_x10":
                    encoded = encode_int16_x10(float(value))
                    ir_block.setValues(addr, [encoded])

    # -- Async lifecycle ------------------------------------------------------

    async def start(self) -> None:
        """Start the Modbus TCP server and register update loop.

        Uses single-slave mode for packaging profiles (no secondary slaves)
        and multi-slave mode for F&B profiles (secondary slaves for Eurotherm
        zone controllers at UIDs 11-13).  Multi-slave mode routes requests by
        unit ID; single-slave mode ignores unit ID (preserves packaging tests).
        """
        if self._secondary_contexts:
            # F&B multi-slave mode: route by unit ID
            devices: dict[int, FactoryDeviceContext] = {
                self._modbus_cfg.unit_id: self._device_context,
                **self._secondary_contexts,
            }
            server_context = ModbusServerContext(  # type: ignore[no-untyped-call]
                devices=devices, single=False,
            )
        else:
            # Packaging single-slave mode: all requests go to primary context
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
        """Periodically sync register values from the store.

        Skips register synchronisation during an active communication drop
        (PRD 10.2): Modbus register values freeze at their last-synced values.

        At ``duplicate_probability`` (PRD 10.5), also skips synchronisation
        so registers hold their previous values.  A subsequent client read
        returns identical data to the previous read — same value with the same
        effective internal timestamp — replicating the PLC scan-cycle race
        condition described in PRD 10.5.
        """
        try:
            while True:
                now = time.monotonic()
                self._drop_scheduler.update(now)
                is_drop = self._drop_scheduler.is_active(now)
                is_dup = (
                    self._dup_rng is not None
                    and self._dup_rng.random() < self._dup_prob
                )
                if not is_drop and not is_dup:
                    self.sync_registers()
                await asyncio.sleep(0.05)  # 50ms update interval
        except asyncio.CancelledError:
            pass
