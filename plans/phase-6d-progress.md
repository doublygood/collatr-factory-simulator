# Phase 6d: Maintenance & CI — Progress

## Status: IN PROGRESS

## Tasks
- [x] 6d.1: Shared Reference Epoch Constant (Y18)
- [x] 6d.2: _format_time() Performance Fix (Y17) — depends on 6d.1
- [x] 6d.3: Configurable Health Server Port (Y16)
- [x] 6d.4: Server Task Verification After Startup (Y20)
- [x] 6d.5: Narrow Exception Suppression During Shutdown (Y27)
- [ ] 6d.6: Dead Config Cleanup — sparkplug_b, retain (Y22+Y23)
- [ ] 6d.7: Generator Tests: Coder (Y19)
- [ ] 6d.8: Generator Tests: Energy (Y19)
- [ ] 6d.9: Generator Tests: Laminator (Y19)
- [ ] 6d.10: Generator Tests: Slitter (Y19)
- [ ] 6d.11: Generator Tests: Vibration (Y19)
- [ ] 6d.12: CI Matrix: Python 3.13 + Integration Tests (Y21)
- [ ] 6d.13: Validate All Fixes — Full Suite

## Notes

Only dependency: 6d.2 depends on 6d.1 (shared epoch must exist before ground_truth uses it).
All other tasks are fully independent.

Y24 (Dockerfile editable install) was already fixed in Phase 6a — skipped.
Y25 (inactive profile nodes) and Y26 (LWT topic) moved to Phase 6e.

Generator test files (6d.7-6d.11) follow the existing pattern in test_mixer.py, test_press.py:
helpers to create minimal config, run N ticks, assert expected behaviour.

## Task 6d.1 — Shared Reference Epoch Constant

Created `src/factory_simulator/time_utils.py` with:
- `REFERENCE_EPOCH` (datetime) and `REFERENCE_EPOCH_TS` (float) constants
- `sim_time_to_datetime(sim_time, offset_s)` — returns tz-aware datetime
- `sim_time_to_iso(sim_time, offset_s)` — returns ISO 8601 string with ms precision

Updated 4 source files to import from time_utils:
- `mqtt_publisher.py`: removed `_REFERENCE_EPOCH_TS` and `_sim_time_to_iso()`; callers now use `sim_time_to_iso(sim_time, offset_hours * 3600.0)` (hours→seconds conversion at call site)
- `opcua_server.py`: removed `_REFERENCE_EPOCH_TS` and `_sim_time_to_datetime()`; callers use `sim_time_to_datetime()`
- `health/server.py`: removed `_REFERENCE_EPOCH_TS`; uses `REFERENCE_EPOCH_TS` from time_utils
- `engine/ground_truth.py`: `_format_time()` now delegates to `sim_time_to_iso()` (also fixes Y17 per-call allocation)

Updated 2 test files that imported old private functions:
- `test_clock_drift_opcua.py`: `_sim_time_to_datetime` → `sim_time_to_datetime`
- `test_protocols/test_duplicate_timestamps.py`: `_sim_time_to_iso` → `sim_time_to_iso` (with offset_hours→offset_s conversion)

New test file: `tests/unit/test_time_utils.py` (9 tests).
Full suite: 3054 passed.

## Task 6d.2 — _format_time() Performance Fix

Verify-and-mark-done: Task 6d.1 already resolved Y17. `_format_time()` (ground_truth.py:433) delegates to `sim_time_to_iso()` from `time_utils`, which uses the module-level `REFERENCE_EPOCH_TS` constant. No per-call `datetime(2026, 1, 1, ...)` allocation remains anywhere in the file. No code changes needed.

## Task 6d.3 — Configurable Health Server Port

Added `health_port: int = 8080` to `SimulationConfig` with validator (0-65535). Added `SIM_HEALTH_PORT` to env override map in `_apply_env_overrides()`. Updated `cli.py:446` to use `config.simulation.health_port` instead of hardcoded `8080`.

Tests added to `tests/unit/test_config.py`:
- Default value (8080) in `test_defaults`
- Custom port (9090)
- Boundary: port 0 valid, port 65535 valid
- Validation: negative port rejected, port > 65535 rejected
- Env override: `SIM_HEALTH_PORT=9090`

Full suite: 3060 passed.

## Task 6d.4 — Server Task Verification After Startup

Extracted `_start_server(srv, *, settle_time=0.05)` helper in `cli.py` that:
1. Creates an asyncio task for `srv.start()`
2. Sleeps for `settle_time` to let the server bind
3. Checks `task.done()` — if the task completed with an exception, raises `RuntimeError` with the server class name and original error

Applied to all 4 server startup sites in `_run_realtime()`:
- Health server
- Modbus servers (loop)
- OPC-UA servers (loop)
- MQTT publishers (loop)

Tests added to `TestStartServer` in `tests/unit/test_cli.py`:
- `test_successful_server_returns_task` — good server returns running task
- `test_failed_server_raises_runtime_error` — OSError during start propagates as RuntimeError
- `test_failed_server_error_includes_class_name` — error message includes server class name

Full suite: 3063 passed.

## Task 6d.5 — Narrow Exception Suppression During Shutdown

Changed `contextlib.suppress(Exception)` to `contextlib.suppress(asyncio.CancelledError, OSError, ConnectionError)` in the server shutdown loop at `cli.py:507`. This ensures only expected shutdown exceptions are suppressed — `CancelledError` (task cancelled), `OSError` (socket already closed), `ConnectionError` (broker disconnected) — while unexpected errors like `RuntimeError` or `TypeError` propagate for visibility.

Tests added to `TestShutdownExceptionSuppression` in `tests/unit/test_cli.py`:
- `test_shutdown_suppresses_cancelled_error` — CancelledError suppressed
- `test_shutdown_suppresses_oserror` — OSError suppressed
- `test_shutdown_suppresses_connection_error` — ConnectionError suppressed
- `test_shutdown_propagates_runtime_error` — RuntimeError NOT suppressed
- `test_shutdown_propagates_type_error` — TypeError NOT suppressed

Full suite: 3068 passed.
