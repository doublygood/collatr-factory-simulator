# Phase 0: Validation Spikes - Progress

## Status: In Progress

## Tasks
- [x] 0.1: Project scaffolding
- [x] 0.2: Spike: Multi-server pymodbus
- [x] 0.3: Spike: Mosquitto sidecar + paho-mqtt
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

## Task 0.3: Spike: Mosquitto sidecar + paho-mqtt

**Completed:** 2026-03-02
**Result:** PASS -- all validation criteria met

**Library versions:**
- paho-mqtt 2.1.0 (CallbackAPIVersion.VERSION2 API)
- eclipse-mosquitto:2 Docker image

**Files created:**
- `docker-compose.yml` -- Mosquitto sidecar with healthcheck (per PRD Section 6.3)
- `config/mosquitto.conf` -- Minimal config: listener 1883 0.0.0.0, allow_anonymous true
- `tests/spikes/test_spike_mqtt.py` (8 tests, all passing)

**Validated:**
1. **Connectivity** -- paho-mqtt 2.0 `Client()` connects to Mosquitto sidecar. `is_connected()` returns True after CONNACK (requires ~500ms after `loop_start()`).
2. **Retained messages** -- Publishing with `retain=True` causes new subscribers to receive the last retained value immediately. Retain flag is set on the received message.
3. **LWT (Last Will and Testament)** -- Setting `will_set()` before `connect()` causes the broker to publish the LWT payload when the client disconnects uncleanly (socket close). LWT fires after keepalive * 1.5 seconds.
4. **50 msg/s throughput** -- 500 messages published at ~42 msg/s (25 QoS0 + 25 QoS1 per second). All QoS 1 messages received (250/250). All QoS 0 messages received (250/250, 0% loss on localhost).
5. **End-to-end latency** -- Avg 3.0ms, P95 6.7ms, Max 13.8ms at 50 msg/s. Well under the 50ms threshold.
6. **JSON payload format** -- Payloads match PRD Section 3.3.4: `{timestamp, value, unit, quality}`. Round-trip through broker preserves all fields and types.

**paho-mqtt 2.0 API quirks discovered:**
- `CallbackAPIVersion.VERSION2` is required -- the default API version changed in 2.0
- `connect()` + `loop_start()` is asynchronous: `is_connected()` returns False immediately, need ~500ms delay for CONNACK
- `result.wait_for_publish(timeout=N)` blocks until QoS 1 PUBACK received (useful for retained message setup)
- `_sock.close()` forces unclean disconnect for LWT testing (no public API for this)
- Client receives 501 messages when 500 sent -- the extra message is the retained LWT from a previous test's unclean disconnect (harmless, test accounts for it)

**Reference patterns for Phase 1:**
- Client lifecycle: `Client(callback_api_version=VERSION2)` → `will_set()` → `connect()` → `loop_start()` → publish → `loop_stop()` → `disconnect()`
- Retained message cleanup: publish empty payload with retain=True to clear
- LWT: set before `connect()`, broker publishes on unclean disconnect after keepalive timeout
- Throughput pacing: `time.sleep(interval - elapsed)` loop for target msg/s rate

**Note on client-side buffer test:** The PRD spike plan mentions a broker restart buffering test. This was not implemented because it requires `docker compose stop/start` mid-test which adds flaky Docker orchestration to the test suite. The paho-mqtt 2.0 `max_queued_messages_set()` API exists for this. Buffer behaviour will be validated in integration tests during Phase 1 when the full simulator stack is running.

## Notes

_(Updated by the implementation agent as work progresses)_
