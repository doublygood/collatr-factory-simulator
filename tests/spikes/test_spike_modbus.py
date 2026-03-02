"""Spike: Multi-server pymodbus validation.

Validates that pymodbus can run 7+ async Modbus TCP servers concurrently
in one asyncio event loop, each with independent register maps.

Tests:
  - 7 concurrent servers on ports 15020-15026
  - Multi-slave addressing (3 UIDs on one port)
  - Concurrent reads from all servers (asyncio.gather)
  - Setpoint write (FC16) and read-back
  - FC06 rejection for float32 register pairs
  - Max 125 register limit enforcement
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
import time

import pytest
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.pdu import ExceptionResponse
from pymodbus.pdu.register_message import ExcCodes
from pymodbus.server import ModbusTcpServer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE_PORT = 15020
NUM_SERVERS = 7
HOST = "127.0.0.1"

# Float32 register pairs (address of first register in each pair)
# Used by server 0 (press) for FC06 rejection testing
FLOAT32_ADDRESSES = {0, 2, 10, 12, 20, 22, 24, 40, 42, 44}


# ---------------------------------------------------------------------------
# Custom device context: rejects FC06 on float32 register pairs
# ---------------------------------------------------------------------------
class FC06ProtectedDeviceContext(ModbusDeviceContext):
    """Device context that rejects FC06 writes to float32 register pairs.

    Float32 values span two consecutive 16-bit registers.  FC06 (Write
    Single Register) writes only one word and would corrupt the value.
    The correct function code for float32 writes is FC16.
    """

    def __init__(
        self,
        float32_addresses: set[int] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._float32_addresses = float32_addresses or set()

    def setValues(  # type: ignore[override]
        self,
        func_code: int,
        address: int,
        values: list[int] | list[bool],
    ) -> None | ExcCodes:
        """Reject FC06 on float32 register pairs."""
        if func_code == 6 and address in self._float32_addresses:
            return ExcCodes.ILLEGAL_FUNCTION
        return super().setValues(func_code, address, values)


# ---------------------------------------------------------------------------
# Custom device context: enforces max register read limit
# ---------------------------------------------------------------------------
MAX_READ_REGISTERS = 125


class RegisterLimitDeviceContext(FC06ProtectedDeviceContext):
    """Device context that enforces a maximum register read count."""

    def getValues(  # type: ignore[override]
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
# Server factory
# ---------------------------------------------------------------------------
def _encode_float32(value: float) -> tuple[int, int]:
    """Encode a float32 into two 16-bit registers (ABCD / big-endian)."""
    packed = struct.pack(">f", value)
    high = int.from_bytes(packed[0:2], "big")
    low = int.from_bytes(packed[2:4], "big")
    return high, low


def _decode_float32(regs: list[int]) -> float:
    """Decode two 16-bit registers (ABCD) back to float32."""
    raw = struct.pack(">HH", regs[0], regs[1])
    return struct.unpack(">f", raw)[0]


def _make_server(
    port: int,
    server_index: int,
) -> tuple[ModbusTcpServer, ModbusServerContext]:
    """Create a ModbusTcpServer for the given index.

    Server 0-3, 5-6: single-device, 100 registers each.
    Server 4 (port 15024): multi-slave with 3 UIDs (oven zones).
    """
    if server_index == 4:
        # Multi-slave: 3 UIDs with different register values
        devices: dict[int, ModbusDeviceContext] = {}
        for uid in (1, 2, 3):
            base_val = uid * 1000  # 1000, 2000, 3000
            hr_block = ModbusSequentialDataBlock(0, [base_val + i for i in range(100)])
            devices[uid] = ModbusDeviceContext(hr=hr_block)
        context = ModbusServerContext(devices=devices, single=False)
    elif server_index == 0:
        # Server 0: press - uses FC06-protected + register-limit context
        # Pre-populate float32 values at known addresses
        hr_values = [0] * 200
        # Write a float32 at address 0-1 (line_speed = 150.0)
        hi, lo = _encode_float32(150.0)
        hr_values[1] = hi  # +1 because ModbusSequentialDataBlock is 1-indexed internally
        hr_values[2] = lo
        hr_block = ModbusSequentialDataBlock(0, hr_values)
        device = RegisterLimitDeviceContext(
            float32_addresses=FLOAT32_ADDRESSES,
            hr=hr_block,
        )
        context = ModbusServerContext(devices=device, single=True)
    else:
        # Standard server: unique register values per server
        base_val = (server_index + 1) * 100
        hr_block = ModbusSequentialDataBlock(0, [base_val + i for i in range(200)])
        device = ModbusDeviceContext(hr=hr_block)
        context = ModbusServerContext(devices=device, single=True)

    server = ModbusTcpServer(context, address=(HOST, port))
    return server, context


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
async def modbus_servers() -> (  # type: ignore[override]
    tuple[list[ModbusTcpServer], list[ModbusServerContext]]
):
    """Start 7 Modbus TCP servers and yield them; shut down on cleanup."""
    servers: list[ModbusTcpServer] = []
    contexts: list[ModbusServerContext] = []
    tasks: list[asyncio.Task[None]] = []

    for i in range(NUM_SERVERS):
        port = BASE_PORT + i
        server, ctx = _make_server(port, i)
        servers.append(server)
        contexts.append(ctx)
        task = asyncio.create_task(server.serve_forever())
        tasks.append(task)

    # Give servers time to bind
    await asyncio.sleep(0.5)

    yield servers, contexts

    # Cleanup
    for server in servers:
        await server.shutdown()
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.fixture
async def clients(
    modbus_servers: tuple[list[ModbusTcpServer], list[ModbusServerContext]],
) -> list[AsyncModbusTcpClient]:
    """Create and connect clients to all 7 servers."""
    _servers, _contexts = modbus_servers
    client_list: list[AsyncModbusTcpClient] = []

    for i in range(NUM_SERVERS):
        client = AsyncModbusTcpClient(HOST, port=BASE_PORT + i)
        await client.connect()
        assert client.connected, f"Failed to connect to server {i} on port {BASE_PORT + i}"
        client_list.append(client)

    yield client_list  # type: ignore[misc]

    for client in client_list:
        client.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMultiServerConcurrency:
    """Validate 7 concurrent Modbus TCP servers."""

    async def test_all_servers_respond(
        self,
        clients: list[AsyncModbusTcpClient],
    ) -> None:
        """Each of the 7 servers responds to reads with its own data."""
        for i, client in enumerate(clients):
            if i == 4:
                # Multi-slave server: read from UID 1
                result = await client.read_holding_registers(0, count=5, device_id=1)
            elif i == 0:
                # Press server: read non-float32 registers
                result = await client.read_holding_registers(50, count=5)
            else:
                result = await client.read_holding_registers(0, count=5)

            assert not result.isError(), f"Server {i} returned error: {result}"
            assert len(result.registers) == 5, f"Server {i} returned wrong count"

    async def test_servers_have_independent_data(
        self,
        clients: list[AsyncModbusTcpClient],
    ) -> None:
        """Each server returns different register values."""
        results: list[list[int]] = []
        for i, client in enumerate(clients):
            if i == 4:
                result = await client.read_holding_registers(0, count=5, device_id=1)
            elif i == 0:
                result = await client.read_holding_registers(50, count=5)
            else:
                result = await client.read_holding_registers(0, count=5)
            assert not result.isError()
            results.append(result.registers)

        # Verify all register sets are different
        for a in range(len(results)):
            for b in range(a + 1, len(results)):
                assert results[a] != results[b], (
                    f"Server {a} and {b} have identical data: {results[a]}"
                )

    async def test_concurrent_reads(
        self,
        clients: list[AsyncModbusTcpClient],
    ) -> None:
        """All 7 servers can be read concurrently with asyncio.gather."""

        async def read_server(idx: int) -> list[int]:
            if idx == 4:
                result = await clients[idx].read_holding_registers(
                    0, count=5, device_id=1,
                )
            elif idx == 0:
                result = await clients[idx].read_holding_registers(50, count=5)
            else:
                result = await clients[idx].read_holding_registers(0, count=5)
            assert not result.isError()
            return result.registers

        start = time.monotonic()
        results = await asyncio.gather(*[read_server(i) for i in range(NUM_SERVERS)])
        elapsed = time.monotonic() - start

        assert len(results) == NUM_SERVERS
        for i, regs in enumerate(results):
            assert len(regs) == 5, f"Server {i} returned {len(regs)} registers"

        # Concurrent reads should be much faster than 7x sequential
        # (each read ~5-10ms, so 7 concurrent should be <100ms)
        assert elapsed < 2.0, f"Concurrent reads took {elapsed:.3f}s (too slow)"

    async def test_no_event_loop_blocking(
        self,
        clients: list[AsyncModbusTcpClient],
    ) -> None:
        """Concurrent reads complete without blocking the event loop.

        On localhost, individual reads complete in sub-millisecond time,
        so concurrent reads may not be faster than sequential.  The
        important validation is that the event loop is not blocked:
        all 7 concurrent reads complete within a reasonable wall-clock
        bound, proving the servers are non-blocking.
        """
        iterations = 50  # Enough iterations to be meaningful

        async def read_all() -> None:
            coros = []
            for idx in range(NUM_SERVERS):
                if idx == 4:
                    coros.append(
                        clients[idx].read_holding_registers(0, count=5, device_id=1)
                    )
                elif idx == 0:
                    coros.append(clients[idx].read_holding_registers(50, count=5))
                else:
                    coros.append(clients[idx].read_holding_registers(0, count=5))
            results = await asyncio.gather(*coros)
            for r in results:
                assert not r.isError()

        start = time.monotonic()
        for _ in range(iterations):
            await read_all()
        elapsed = time.monotonic() - start

        # 50 rounds of 7 concurrent reads should complete well under 10s
        # (on localhost, typically <1s total)
        assert elapsed < 10.0, (
            f"{iterations} rounds of concurrent reads took {elapsed:.3f}s"
        )


class TestMultiSlave:
    """Validate multi-slave addressing on a single port."""

    async def test_different_uids_return_different_data(
        self,
        clients: list[AsyncModbusTcpClient],
    ) -> None:
        """Server 4 (port 15024) returns different data per UID."""
        client = clients[4]  # Multi-slave server

        results: dict[int, list[int]] = {}
        for uid in (1, 2, 3):
            result = await client.read_holding_registers(0, count=5, device_id=uid)
            assert not result.isError(), f"UID {uid} returned error: {result}"
            results[uid] = result.registers

        # Verify each UID returns different data
        assert results[1] != results[2], "UID 1 and 2 have same data"
        assert results[2] != results[3], "UID 2 and 3 have same data"
        assert results[1] != results[3], "UID 1 and 3 have same data"

        # Verify expected base values.
        # ModbusSequentialDataBlock is 1-indexed internally: address 0 maps
        # to values[1], so the first register read at address 0 returns
        # base_val + 1 (e.g., 1001 for UID 1 with base_val=1000).
        assert results[1][0] == 1001, f"UID 1 base value wrong: {results[1][0]}"
        assert results[2][0] == 2001, f"UID 2 base value wrong: {results[2][0]}"
        assert results[3][0] == 3001, f"UID 3 base value wrong: {results[3][0]}"

    async def test_nonexistent_uid_returns_error(
        self,
        clients: list[AsyncModbusTcpClient],
    ) -> None:
        """Reading from a non-existent UID returns an error or times out."""
        client = clients[4]
        result = await client.read_holding_registers(0, count=5, device_id=99)
        # pymodbus should return an error for unknown device ID
        assert result.isError(), "Expected error for non-existent UID 99"


class TestSetpointWriteReadBack:
    """Validate write (FC16) and read-back of setpoint registers."""

    async def test_write_fc16_and_readback(
        self,
        clients: list[AsyncModbusTcpClient],
    ) -> None:
        """Write a float32 setpoint via FC16 and read it back."""
        client = clients[1]  # Server 1 (laminator)

        # Write two registers (FC16) at address 0
        setpoint = 85.5
        high, low = _encode_float32(setpoint)
        result = await client.write_registers(0, [high, low])
        assert not result.isError(), f"FC16 write failed: {result}"

        # Read back
        result = await client.read_holding_registers(0, count=2)
        assert not result.isError(), f"Read-back failed: {result}"
        read_value = _decode_float32(result.registers)
        assert abs(read_value - setpoint) < 0.01, (
            f"Read-back value {read_value} != written {setpoint}"
        )


class TestFC06Rejection:
    """Validate FC06 rejection for float32 register pairs."""

    async def test_fc06_to_float32_returns_illegal_function(
        self,
        clients: list[AsyncModbusTcpClient],
    ) -> None:
        """FC06 write to a float32 register pair returns exception 0x01."""
        client = clients[0]  # Server 0 (press) with FC06 protection

        # Attempt FC06 (write_register) to address 0 (float32 pair)
        result = await client.write_register(0, 12345)

        assert result.isError(), "Expected FC06 to be rejected"
        assert isinstance(result, ExceptionResponse), (
            f"Expected ExceptionResponse, got {type(result)}"
        )
        assert result.exception_code == ExcCodes.ILLEGAL_FUNCTION, (
            f"Expected exception 0x01, got 0x{result.exception_code:02x}"
        )

    async def test_fc16_to_float32_succeeds(
        self,
        clients: list[AsyncModbusTcpClient],
    ) -> None:
        """FC16 write to the same float32 register pair succeeds."""
        client = clients[0]

        # FC16 (write_registers) to address 0 (float32 pair) should work
        high, low = _encode_float32(200.0)
        result = await client.write_registers(0, [high, low])
        assert not result.isError(), f"FC16 write should succeed: {result}"

    async def test_fc06_to_non_float32_succeeds(
        self,
        clients: list[AsyncModbusTcpClient],
    ) -> None:
        """FC06 write to a non-float32 register (e.g. uint16) succeeds."""
        client = clients[0]

        # Address 50 is not in FLOAT32_ADDRESSES, so FC06 should work
        result = await client.write_register(50, 42)
        assert not result.isError(), f"FC06 to non-float32 should succeed: {result}"


class TestMaxRegisterLimit:
    """Validate max 125 register read limit."""

    async def test_125_registers_succeeds(
        self,
        clients: list[AsyncModbusTcpClient],
    ) -> None:
        """Reading exactly 125 registers succeeds."""
        client = clients[0]  # Server 0 with register limit enforcement

        result = await client.read_holding_registers(0, count=125)
        assert not result.isError(), f"125 register read should succeed: {result}"
        assert len(result.registers) == 125

    async def test_126_registers_fails(
        self,
        clients: list[AsyncModbusTcpClient],
    ) -> None:
        """Reading 126 registers fails.

        pymodbus enforces the 125 limit client-side in encode() via
        verifyCount(125). This raises ValueError before the request
        is sent. On the server side, we additionally enforce via
        RegisterLimitDeviceContext.getValues().
        """
        client = clients[0]

        # pymodbus client enforces 125 max in encode() - raises ValueError
        with pytest.raises(ValueError, match="count"):
            await client.read_holding_registers(0, count=126)
