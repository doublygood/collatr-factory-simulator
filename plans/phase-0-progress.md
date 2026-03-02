# Phase 0: Validation Spikes - Progress

## Status: In Progress

## Tasks
- [x] 0.1: Project scaffolding
- [x] 0.2: Spike: Multi-server pymodbus
- [ ] 0.3: Spike: Mosquitto sidecar + paho-mqtt
- [ ] 0.4: Spike: asyncua multiple instances
- [ ] 0.5: Document spike results

## Task 0.1: Project Scaffolding

**Completed:** 2026-03-02

**Created:**
- `pyproject.toml` -- project metadata, pytest config (`asyncio_mode = "auto"`, `integration` marker), ruff config (py312, line-length 100, select E/W/F/I/UP/B/SIM/RUF), mypy config (strict, ignore missing imports for pymodbus/asyncua/paho/uvloop)
- `requirements.txt` -- production deps: pymodbus, asyncua, paho-mqtt, numpy, pydantic, pyyaml, uvloop (linux-only)
- `requirements-dev.txt` -- includes requirements.txt + pytest, pytest-asyncio, hypothesis, ruff, mypy
- `src/factory_simulator/__init__.py` -- package init with `__version__`
- `tests/` -- conftest.py + `__init__.py` in tests/, unit/, integration/, spikes/
- `.gitignore` -- Python, IDE, testing, Docker artifacts

**Verified:**
- `ruff check src tests` -- All checks passed
- `mypy src` -- Success: no issues found in 1 source file
- `pytest` -- discovers test directories (0 items collected, no errors)

**Decisions:**
- All tool config consolidated in `pyproject.toml` (no separate ruff.toml, mypy.ini, etc.)
- Using `src/factory_simulator/` layout per PRD appendix-e (not flat `src/` with `__init__.py`)
- uvloop dependency conditional on `sys_platform == "linux"` per PRD 7.5 platform note

## Task 0.2: Spike: Multi-server pymodbus

**Completed:** 2026-03-02
**Result:** PASS -- all 6 validation criteria met

**Library versions:**
- pymodbus 3.12.1
- pytest-asyncio 1.3.0

**Test file:** `tests/spikes/test_spike_modbus.py` (12 tests, all passing)

**Validated:**
1. **7 concurrent servers** -- 7 `ModbusTcpServer` instances on ports 15020-15026, all serving concurrently in one asyncio event loop. Each with independent `ModbusServerContext` and register maps.
2. **Multi-slave addressing** -- Server 4 (port 15024) configured with `ModbusServerContext(devices={1: ctx1, 2: ctx2, 3: ctx3}, single=False)`. Client reads from UIDs 1, 2, 3 on same port and gets different register values. Non-existent UID (99) returns error.
3. **Concurrent reads** -- `asyncio.gather` across all 7 servers completes without errors. 50 rounds of 7 concurrent reads in <10s. No event loop blocking.
4. **Setpoint write/read-back** -- FC16 (`write_registers`) writes float32 value, FC03 reads it back correctly.
5. **FC06 rejection** -- Custom `FC06ProtectedDeviceContext` subclass overrides `setValues()` to return `ExcCodes.ILLEGAL_FUNCTION` when func_code=6 targets a float32 register pair. FC16 to same addresses succeeds. FC06 to non-float32 addresses succeeds.
6. **Max 125 register limit** -- Client-side: pymodbus `verifyCount(125)` in `encode()` raises `ValueError` for count>125. Server-side: Custom `RegisterLimitDeviceContext` overrides `getValues()` to return `ExcCodes.ILLEGAL_VALUE` for count>125.

**pymodbus 3.12 API quirks discovered:**
- `ModbusSlaveContext` renamed to `ModbusDeviceContext`
- `slave` parameter renamed to `device_id` everywhere
- `read_holding_registers(address, *, count=1, device_id=1)` -- count is keyword-only
- `ModbusServerContext(devices=..., single=...)` -- parameter renamed from `slaves`
- `ModbusSequentialDataBlock` is 1-indexed internally: address 0 maps to values[1]
- Concurrent vs sequential timing on localhost shows no speedup (sub-ms per read means `asyncio.gather` overhead dominates), but this is expected -- the spike validates non-blocking behavior, not throughput

**Reference patterns for Phase 1:**
- Custom device context subclassing for FC06 rejection and register limits
- Multi-slave via `ModbusServerContext(devices={uid: ctx, ...}, single=False)`
- Server lifecycle: `asyncio.create_task(server.serve_forever())` + `server.shutdown()`
- Float32 encoding: `struct.pack(">f", value)` → split into two 16-bit registers (ABCD)

## Notes

_(Updated by the implementation agent as work progresses)_
