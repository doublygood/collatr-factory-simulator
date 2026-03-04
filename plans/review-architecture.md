# Architecture Review: Collatr Factory Simulator

**Reviewer:** Senior Software Engineer  
**Date:** 2026-03-04  
**Scope:** Full codebase deep-dive — async patterns, error handling, config validation, test coverage, code quality, Docker/CI, ground truth, store/clock  

---

## 1. Executive Summary

The Collatr Factory Simulator is a well-structured codebase with clear separation of concerns, thorough Pydantic configuration validation, and solid async patterns. The signal model → generator → store → protocol adapter pipeline is clean and the tick atomicity guarantee (Rule 8) is correctly maintained.

However, several issues would cause real problems in production use or maintenance:

**Critical (RED):**
- Ground truth logger is never instantiated in the CLI — no scenario events are recorded in real-time or batch mode
- Ground truth header `write_header()` omits Phase 4 and all F&B scenarios from the scenarios list
- No `.dockerignore` — Docker builds include `.git/`, test files, and all dev artifacts
- Container runs as root — security concern for production deployment

**Significant (YELLOW):**
- MQTT publisher has no reconnection logic — if the broker drops, `paho.connect()` is called once in `start()` and never retried
- `CsvWriter.close()` is not idempotent — double-close raises `ValueError`
- `SignalConfig` has no `min_clamp <= max_clamp` validator
- `EvaluationConfig` is defined but never wired into `FactoryConfig`
- `ClockDriftConfig.initial_offset_ms` validation prevents negative offsets (clock behind) — this is valid in the real world
- Health server port 8080 is hardcoded in CLI, not configurable
- 5 generator modules and 3 Phase-1 scenario classes lack dedicated test files
- `_format_time()` re-creates a `datetime` object on every call (performance in tight loops)
- No SIGTERM handler — Docker stop sends SIGTERM, which falls through to `asyncio.run()` default handling that may not clean up protocol servers

---

## 2. Async Patterns and Resource Management

### 2.1 Tick Atomicity (Rule 8) ✅
`DataEngine.tick()` is fully synchronous — no `await` between signal updates. This is correctly maintained through the entire tick pipeline: scenarios → generators → post_gen_inject → data_quality → batch_writer.

### 2.2 Task Cancellation ⚠️
The CLI's `_run_realtime()` creates protocol server tasks with `asyncio.create_task()` and cancels them in the `finally` block. This is correct but has a gap:

**Issue:** `contextlib.suppress(Exception)` at `cli.py:435` swallows ALL exceptions during server shutdown, including `RuntimeError` or `OSError` that indicate failed cleanup. This should be narrower:
```python
# cli.py:435 — too broad
with contextlib.suppress(Exception):
    await srv.stop()
# Better:
with contextlib.suppress(asyncio.CancelledError, OSError):
    await srv.stop()
```

### 2.3 Resource Cleanup on Shutdown ⚠️
- **MQTT:** `MqttPublisher.stop()` calls `loop_stop()` + `disconnect()` — correct.
- **Modbus:** `ModbusServer.stop()` calls `tcp_server.shutdown()` — correct.
- **OPC-UA:** `OpcuaServer.stop()` calls `server.stop()` — correct.
- **Health:** Health task is cancelled in finally block — correct.
- **Batch writer:** Closed in both `_run_batch` finally block and `DataEngine.run()` finally block — but `CsvWriter.close()` is not idempotent (see §3.2).

### 2.4 SIGTERM/SIGINT Handling ⚠️
**No explicit signal handlers.** The CLI relies on `asyncio.run()` which installs a default `KeyboardInterrupt` handler for SIGINT. However:
- **SIGTERM** (Docker's default stop signal) is not handled. `asyncio.run()` does NOT handle SIGTERM by default — the process will be killed without cleanup after Docker's 10-second grace period.
- Should add `loop.add_signal_handler(signal.SIGTERM, ...)` in `_run_realtime()`.

### 2.5 Blocking Calls in Async Code ⚠️
- **`GroundTruthLogger._write_line()`** — synchronous `self._fh.write()` + `self._fh.flush()` in what is called from the sync `tick()` method. This is acceptable because `tick()` itself is synchronous and runs between `asyncio.sleep()` calls. However, the flush-per-line pattern may become a bottleneck at high time scales.
- **`CsvWriter.write_tick()`** — also synchronous file I/O, but called from the sync tick. Same analysis.
- **`config.load_config()`** — synchronous YAML parse + file read. Called once at startup, acceptable.

### 2.6 MQTT Reconnection ⚠️ 
**The MQTT publisher has no reconnection logic.** `paho.connect()` is called once in `MqttPublisher.start()`. If the broker is temporarily unreachable:
- `paho.loop_start()` will internally attempt reconnects (paho's built-in reconnect)
- But the initial `connect()` call in `start()` will raise `ConnectionRefusedError` and crash the startup

There is no retry loop around the initial connection, and no `on_disconnect` callback to handle mid-run broker restarts. This means:
1. If the MQTT broker starts slowly, the simulator fails to start
2. If the broker restarts mid-simulation, paho's internal reconnect may work but there's no monitoring or logging of this state

### 2.7 Connection Handling — Client Disconnect
- **Modbus (pymodbus):** Handled internally by `ModbusTcpServer` — clients can connect/disconnect freely.
- **OPC-UA (asyncua):** Handled internally by `asyncua.Server` — sessions are managed.
- **MQTT:** Client-side publisher, no inbound connections to manage.
- **Health server:** `HealthServer._handle()` properly catches `TimeoutError`, `ConnectionResetError`, `OSError` and closes the writer in a `finally` block. ✅

### 2.8 CommDropScheduler — Wall Clock vs Sim Time
`CommDropScheduler` uses `time.monotonic()` (wall-clock) for scheduling, which is correct for network-level events. However, at high time scales (100x), a 10-second sim drop would last 10 real-world seconds, not 0.1 seconds. This is documented as intentional (PRD 10.2), but should be noted for users running batch mode — comm drops last much longer relative to simulated time.

---

## 3. Error Handling Audit

### 3.1 YAML Config — Missing or Malformed ✅
`load_config()` raises `FileNotFoundError` for missing files and `pydantic.ValidationError` for invalid schema. The YAML parser (`yaml.safe_load`) will raise `yaml.YAMLError` for malformed YAML. These propagate cleanly to the CLI.

### 3.2 Protocol Server Port Binding Failures ⚠️
If a Modbus or OPC-UA server fails to bind its port (address already in use), the exception propagates through `asyncio.create_task()`:
- The task fails immediately
- But the CLI's `_run_realtime()` doesn't check if the task is healthy after `await asyncio.sleep(0.05)` — it just proceeds
- The failure becomes visible only when the task raises its exception (which may be much later)

**Recommended:** After each server start, check `task.done()` and handle the exception.

### 3.3 MQTT Broker Unreachable at Startup 🔴
`MqttPublisher.start()` calls `self._client.connect()` which will raise `ConnectionRefusedError` or `OSError`. This propagates through `asyncio.create_task()` and may crash the simulator. There's no retry logic.

### 3.4 NaN/Inf in Signal Models ✅
`clamp()` in `models/base.py` guards against NaN by returning `min_clamp`, `max_clamp`, or `0.0`. The batch writers also filter NaN/Inf. However:
- **Protocol registers:** If `clamp` is not called (store value bypasses it), NaN could leak to Modbus registers. `encode_float32_abcd(NaN)` produces a valid IEEE 754 NaN encoding, which is technically correct but may confuse Modbus clients.
- **MQTT payloads:** `json.dumps(float('nan'))` produces `NaN` which is not valid JSON (only JavaScript). This would silently produce invalid MQTT payloads.

### 3.5 Scenario References Non-Existent Signals ⚠️
Scenarios reference signal IDs like `"press.line_speed"` hardcoded in `_AFFECTED_SIGNALS`. If a signal doesn't exist in the store (e.g., F&B profile running packaging scenarios), `store.get()` returns `None` and scenarios skip the operation. This is **silent** — no warning logged when a scenario tries to modify a signal that doesn't exist.

### 3.6 Parquet Writer — Missing pyarrow ✅
`ParquetWriter.__init__()` raises `ImportError` with a helpful message. This propagates cleanly.

### 3.7 Batch Output Directory ✅
`_async_run()` creates the directory with `out_dir.mkdir(parents=True, exist_ok=True)`. If the path isn't writable, `OSError` propagates.

### 3.8 Silent Failures
| Location | Issue |
|----------|-------|
| `opcua_server.py:412` | `except Exception as exc: logger.debug(...)` — OPC-UA freeze failures logged at DEBUG only |
| `opcua_server.py:481` | `except Exception: continue` — setpoint read failures silently skipped |
| `opcua_server.py:553` | `except Exception as exc: logger.debug(...)` — value sync failures logged at DEBUG only |
| `ground_truth.py:_write_line()` | If `self._fh` is None (never opened or already closed), writes are silently dropped |

### 3.9 CsvWriter Double-Close 🔴
`CsvWriter.close()` calls `self._file.close()` unconditionally. A second call raises `ValueError: I/O operation on closed file`. This is reachable if code calls `close()` in both `_run_batch` finally block and elsewhere.

**Fix:** Add a guard:
```python
def close(self) -> None:
    if self._buffer:
        self._flush()
    if not self._file.closed:
        self._file.close()
```

---

## 4. Configuration Validation

### 4.1 Missing Validators

| Config Class | Missing Validation | Severity |
|-------------|-------------------|----------|
| `SignalConfig` | `min_clamp <= max_clamp` when both are set | YELLOW |
| `SignalConfig` | `sigma_parent` references a valid signal ID | YELLOW |
| `SignalConfig` | `parent` references a valid signal ID | YELLOW |
| `ClockDriftConfig` | `initial_offset_ms` rejects negative values — but negative offsets (clock behind) are a real-world scenario | YELLOW |
| `ClockDriftConfig` | `drift_rate_s_per_day` rejects negative values — but negative drift (clock losing time) is valid | YELLOW |
| `ShiftChangeConfig` | `times` values not validated as HH:MM format | GREEN |

### 4.2 Orphaned Config Classes
- **`EvaluationConfig`** — Fully defined with validators (pre_margin, post_margin, seeds, severity_weights, latency_targets) but **never used as a field in `FactoryConfig`**. It's only referenced in test files. Either wire it into `FactoryConfig.evaluation` or remove it.

### 4.3 Config Fields Defined but Potentially Unused
- `MqttProtocolConfig.sparkplug_b` — defaults to `False`, no code in the MQTT publisher checks this flag
- `MqttProtocolConfig.retain` — defined as a config field but `_retain_for_topic()` derives retain per-topic from the suffix, ignoring this global setting
- `SimulationConfig.start_time` — parsed in `SimulationClock` but the protocol timestamp formatters use a hardcoded 2026-01-01 reference epoch, so `start_time` only affects `clock.sim_datetime()` (which nothing in the engine calls)
- `FactoryInfo.timezone` — defined as `"Europe/London"` in config but no code reads this field from the config

### 4.4 Environment Variable Gaps
The env override map covers: `SIM_TIME_SCALE`, `SIM_RANDOM_SEED`, `SIM_LOG_LEVEL`, `MODBUS_ENABLED`, `MODBUS_PORT`, `MODBUS_BYTE_ORDER`, `OPCUA_ENABLED`, `OPCUA_PORT`, `MQTT_ENABLED`, `MQTT_BROKER_HOST`, `MQTT_BROKER_PORT`, `MQTT_TOPIC_PREFIX`, `SIM_NETWORK_MODE`.

**Missing env overrides:**
- `SIM_TICK_INTERVAL_MS` — no override for tick interval
- `MQTT_CLIENT_ID` — can't be overridden (relevant for multiple instances)
- `OPCUA_SECURITY_MODE` — can't be overridden
- `SIM_DURATION_S` — no override for batch duration
- Health server port — hardcoded to 8080, no env override

### 4.5 Config Inconsistencies Between Profiles
Both `config/factory.yaml` and `config/factory-foodbev.yaml` exist. The packaging profile is well-documented. F&B-specific equipment and scenarios are correctly gated behind `None` checks in `ScenariosConfig`.

---

## 5. Test Coverage Gaps

### 5.1 Source Modules Without Test Files

| Source Module | Test Coverage |
|--------------|---------------|
| `generators/coder.py` | ❌ No dedicated test file |
| `generators/energy.py` | ❌ No dedicated test file |
| `generators/laminator.py` | ❌ No dedicated test file |
| `generators/slitter.py` | ❌ No dedicated test file |
| `generators/vibration.py` | ❌ No dedicated test file |
| `topology.py` | ✅ `test_topology.py` exists |
| `output/writer.py` | ✅ `test_batch_output.py` exists |
| `evaluation/evaluator.py` | ✅ `test_evaluator.py` exists |
| `evaluation/cli.py` | ✅ `test_evaluation_cli.py` exists |

### 5.2 Scenario Coverage Gaps
Phase 1 scenarios (ShiftChange, UnplannedStop, JobChangeover) are tested in `test_basic_scenarios.py` but lack dedicated test files with deep edge-case coverage. Phase 2-4 scenarios each have dedicated test files. ✅

### 5.3 Untested Error Paths

| Error Path | Tested? |
|-----------|---------|
| Config file missing | ✅ (test_config.py) |
| Malformed YAML config | ❌ Not tested |
| Port binding failure (address in use) | ❌ Not tested |
| MQTT broker unreachable | ❌ Not tested |
| Parquet writer without pyarrow | ❌ Not tested (ImportError path) |
| Batch output dir not writable | ❌ Not tested |
| Ground truth file not writable | ❌ Not tested |
| `CsvWriter` double-close | ❌ Not tested |

### 5.4 Test Isolation Issues

**Port collisions:** Most tests use `port=0` (OS-assigned) — good. However, `test_modbus_multiport.py` uses hardcoded ports 5020, 5021. These don't actually start servers (they test register map construction), so collision risk is low.

**Timing-dependent tests:** `test_comm_drop.py`, `test_health.py`, and integration tests that use `asyncio.sleep()` could be flaky under CI load. The use of `asyncio.sleep(0.05)` for server binding is a common source of flakiness.

**Module-level state:** `conftest.py` is minimal (empty). No shared global state detected.

### 5.5 CI Coverage Gaps
- **Python version matrix:** Only tests on `3.12`. Given `requires-python = ">=3.12"`, at minimum `3.13` should be in the matrix for forward compatibility.
- **Integration tests:** Only runs `test_acceptance.py` with `-m "acceptance and not slow"`. The other 8 integration test files (`test_cross_protocol.py`, `test_fnb_*`, `test_modbus_integration.py`, etc.) are never run in CI.
- **Performance tests:** Never run in CI.
- **Missing pip caching key:** `cache: "pip"` without `cache-dependency-path` may cause stale caches.

---

## 6. Code Quality and Maintainability

### 6.1 Dead Code
- **`EvaluationConfig`** — 70+ lines of Pydantic model never wired into `FactoryConfig`
- **`FactoryInfo.timezone`** — config field never read by any code
- **`MqttProtocolConfig.sparkplug_b`** — config field, no implementation
- **`MqttProtocolConfig.retain`** — global setting overridden by per-topic logic

### 6.2 Copy-Paste Patterns
The `_validate_range_pair()` helper is used extensively (good). However:
- `_REFERENCE_EPOCH_TS` is defined identically in 3 files: `mqtt_publisher.py:52`, `opcua_server.py:53`, `health/server.py:42`. Should be a shared constant.
- `_sim_time_to_iso()` and `_sim_time_to_datetime()` are separate implementations of the same conversion. Should share a single implementation.

### 6.3 Magic Numbers
- `health = HealthServer(port=8080, ...)` — hardcoded in `cli.py:391`
- `await asyncio.sleep(0.05)` — server binding wait time in multiple places
- `await asyncio.sleep(0.1)` — MQTT publish granularity
- `await asyncio.sleep(0.05)` — Modbus update interval
- `8 * 3600` — shift duration used as default sim_duration_s in DataEngine
- `keepalive=60` — MQTT keepalive in `MqttPublisher.start()`

### 6.4 Function Size
All functions are under 100 lines. `build_register_map()` is the largest at ~120 lines (including docstring), which is reasonable for its complexity.

### 6.5 Type Annotations ✅
Type annotations are comprehensive. `mypy --strict` is configured in CI. `TYPE_CHECKING` imports are used correctly to avoid circular imports.

### 6.6 Docstrings ✅
All public classes and methods have docstrings with parameter descriptions and PRD references. This is excellent.

### 6.7 `_format_time()` Performance
`GroundTruthLogger._format_time()` creates a new `datetime(2026, 1, 1, tzinfo=UTC)` object on every call. At 10 Hz tick rate × 100x time scale = 1000 calls/sec, this is wasteful. The `_REFERENCE_EPOCH` should be a class-level constant with `.timestamp()` precomputed (like `mqtt_publisher.py` does correctly).

---

## 7. Docker and CI

### 7.1 Missing `.dockerignore` 🔴
No `.dockerignore` file exists. The Docker build context includes:
- `.git/` (potentially hundreds of MB)
- `tests/` (unnecessary in production image)
- `__pycache__/` directories
- `output/` directories from dev runs

**Fix:** Create `.dockerignore`:
```
.git
.github
tests
output
*.egg-info
__pycache__
.mypy_cache
.ruff_cache
.pytest_cache
```

### 7.2 Container Runs as Root 🔴
The Dockerfile has no `USER` directive. The container runs as root, which is a security concern. Should add:
```dockerfile
RUN useradd -m simulator
USER simulator
```

### 7.3 Editable Install in Docker ⚠️
`pip install --no-cache-dir -e .` in the Dockerfile creates an editable install. This is unusual for a production image — should be `pip install --no-cache-dir .` for a regular install.

### 7.4 Health Check Timing ✅
`start-period: 15s` is reasonable for a Python application that needs to start protocol servers. The health server starts before the protocol servers, so it should be reachable quickly.

### 7.5 CI Gaps
- Only Python 3.12 in the test matrix (should add 3.13)
- Integration tests only run `test_acceptance.py` — 8 other integration test files are never validated in CI
- No Docker build validation in CI
- No security scanning (Trivy, Snyk, etc.)

### 7.6 Test Markers ✅
All markers used in CI (`acceptance`, `slow`, `performance`, `integration`) are defined in `pyproject.toml`.

---

## 8. Ground Truth Logger

### 8.1 Logger Never Instantiated in CLI 🔴
**This is the most significant bug found.** The CLI's `_async_run()` function creates a `DataEngine` but never creates or passes a `GroundTruthLogger`:

```python
# cli.py:456
engine = DataEngine(config, store, topology=topology, batch_writer=batch_writer)
```

The `ground_truth` parameter defaults to `None`. This means:
- **No ground truth events are ever recorded** in either real-time or batch mode
- All scenario start/end, state change, sensor disconnect, counter rollover, and data quality events are silently dropped
- The `evaluate` subcommand references a `--ground-truth` file that can never be produced by the `run` subcommand

### 8.2 Header Omits Phase 4 and F&B Scenarios 🔴
`write_header()` only lists the 11 packaging scenarios in the `scenarios` field. Missing:
- `micro_stop` (Phase 4)
- `contextual_anomaly` (Phase 4)
- `intermittent_fault` (Phase 4)
- `batch_cycle` (F&B)
- `oven_thermal_excursion` (F&B)
- `fill_weight_drift` (F&B)
- `seal_integrity_failure` (F&B)
- `chiller_door_alarm` (F&B)
- `cip_cycle` (F&B)
- `cold_chain_break` (F&B)

This means even if the logger were instantiated, the header record would not accurately represent which scenarios are active.

### 8.3 Event Format Consistency ✅
All event types produce consistent JSON: `sim_time` (ISO 8601), `event` (type string), plus type-specific fields. Start/end pairing is maintained for scenarios via `log_scenario_start()` / `log_scenario_end()`.

### 8.4 JSONL Validity ✅
`json.dumps(record, separators=(",", ":"))` produces valid JSON per line. No trailing comma issues.

### 8.5 File Handle Cleanup ⚠️
`close()` sets `self._fh = None` after closing. But `_write_line()` silently returns if `_fh is None`, so writes after close are silently dropped rather than raising an error.

**More concerning:** There's no context manager pattern and no `__del__` fallback. If `close()` is never called (e.g., crash), the file handle leaks. On CPython this is cleaned up by the GC, but it's not guaranteed.

### 8.6 Write Errors ⚠️
`_write_line()` does not catch I/O exceptions (disk full, permissions changed). An `OSError` during `self._fh.write()` would propagate through `log_scenario_start()` up to the engine tick, potentially crashing the simulator.

---

## 9. Store and Clock

### 9.1 Thread Safety ✅
The `SignalStore` is designed for single-writer (engine) access within a single asyncio event loop. No locks are needed and none are present. The MQTT publisher's `paho.publish()` runs from the asyncio thread, not from paho's network thread — correct per the docstring.

### 9.2 Read Before Write ✅
`store.get()` returns `None` for unwritten signals. All callers check for `None`:
- Protocol servers skip sync for missing signals
- Scenarios check before modifying
- Data quality injectors skip signals not in store

### 9.3 Clock — Time Compression ✅
`SimulationClock.dt` = `tick_interval_ms / 1000.0 * time_scale`. At time_scale=100 with tick_interval_ms=100: dt=10.0 seconds per tick. This is correct and all models use `dt` for their calculations.

### 9.4 Integer Overflow Risks ⚠️
- `tick_count` (Python `int`) — unbounded, no overflow possible in Python
- `sim_time` (Python `float`) — at 100x time scale, 100ms ticks: sim_time grows by 10 per real second. After 1 year of real-time: sim_time = ~3.15×10⁸ seconds. IEEE 754 double has 15-16 significant digits, so precision loss starts around 10¹⁵. **No practical risk.**
- **Counter models:** `CounterModel` values can overflow their configured rollover (that's the point — PRD 10.4). The rollover wrap-around is intentional.

### 9.5 Timestamp Reference Epoch Inconsistency ⚠️
Two different time reference systems exist:
1. **Clock start time:** `2024-01-15T06:00:00` (used by `SimulationClock.sim_datetime()`)
2. **Protocol epoch:** `2026-01-01T00:00:00Z` (used by MQTT, OPC-UA, ground truth, health server)

At sim_time=0:
- `clock.sim_datetime()` returns `2024-01-15T06:00:00`
- `_sim_time_to_iso(0)` returns `2026-01-01T00:00:00.000Z`

This is confusing but not a bug per se — `sim_datetime()` is never called in production code (only in the clock class itself). However, it would confuse anyone debugging timestamps.

### 9.6 Store `get_all()` — Direct Dict Reference ⚠️
`get_all()` returns `self._signals` directly (not a copy) for performance. The docstring warns "callers must not mutate." This is fine for the current codebase (all callers are well-behaved), but fragile for future contributors.

---

## 10. Issues Table

| # | Severity | File:Line | Issue |
|---|----------|-----------|-------|
| 1 | 🔴 RED | `cli.py:456` | Ground truth logger never instantiated — no events recorded in any mode |
| 2 | 🔴 RED | `ground_truth.py:62-105` | `write_header()` omits Phase 4 + F&B scenarios from header |
| 3 | 🔴 RED | `Dockerfile` (all) | Missing `.dockerignore` — bloated build context |
| 4 | 🔴 RED | `Dockerfile` (all) | Container runs as root — no `USER` directive |
| 5 | 🟡 YELLOW | `mqtt_publisher.py:595` | No retry logic around initial `connect()` — startup crash if broker is slow |
| 6 | 🟡 YELLOW | `mqtt_publisher.py:595-614` | No reconnection monitoring — silent failure on broker restart |
| 7 | 🟡 YELLOW | `output/writer.py:143` | `CsvWriter.close()` not idempotent — double-close raises ValueError |
| 8 | 🟡 YELLOW | `config.py:219-243` | `SignalConfig` missing `min_clamp <= max_clamp` validator |
| 9 | 🟡 YELLOW | `config.py:1160-1207` | `EvaluationConfig` defined but never wired into `FactoryConfig` |
| 10 | 🟡 YELLOW | `config.py:1303-1307` | `ClockDriftConfig` rejects negative `initial_offset_ms` — prevents valid "clock behind" scenario |
| 11 | 🟡 YELLOW | `cli.py:391` | Health server port 8080 hardcoded, not configurable via config or env var |
| 12 | 🟡 YELLOW | `cli.py` (all) | No SIGTERM handler — Docker stop may not clean up protocol servers |
| 13 | 🟡 YELLOW | `ground_truth.py:408-420` | `_format_time()` creates datetime object per call — performance issue at scale |
| 14 | 🟡 YELLOW | CI (`ci.yml`) | Only Python 3.12 in matrix; integration tests barely covered |
| 15 | 🟡 YELLOW | `mqtt_publisher.py:52` / `opcua_server.py:53` / `health/server.py:42` | `_REFERENCE_EPOCH_TS` duplicated in 3 files — should be shared constant |
| 16 | 🟡 YELLOW | generators | 5 generator modules (coder, energy, laminator, slitter, vibration) have no dedicated tests |
| 17 | 🟡 YELLOW | `cli.py:430-440` | `_run_realtime()` doesn't verify server tasks started successfully |
| 18 | 🟡 YELLOW | `config.py:163` | `MqttProtocolConfig.sparkplug_b` defined but never implemented |
| 19 | 🟡 YELLOW | `config.py:160` | `MqttProtocolConfig.retain` global flag overridden by per-topic logic — misleading |
| 20 | 🟡 YELLOW | `Dockerfile:25` | `pip install -e .` (editable) in production image — should be regular install |
| 21 | 🟢 GREEN | `config.py:23-25` | `FactoryInfo.timezone` defined but never read |
| 22 | 🟢 GREEN | `config.py:35` | `SimulationConfig.start_time` parsed but protocol timestamps use different epoch |
| 23 | 🟢 GREEN | `opcua_server.py:412,481,553` | OPC-UA errors logged at DEBUG — may need INFO for production debugging |
| 24 | 🟢 GREEN | `store.py:97` | `get_all()` returns internal dict, not copy — fragile for future changes |
| 25 | 🟢 GREEN | `clock.py:50` | Default start time (2024) vs protocol reference epoch (2026) — confusing but not a bug |
| 26 | 🟢 GREEN | `ground_truth.py:_write_line()` | No error handling for I/O errors (disk full, permissions) |
