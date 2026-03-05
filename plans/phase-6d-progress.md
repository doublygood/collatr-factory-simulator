# Phase 6d: Maintenance & CI — Progress

## Status: IN PROGRESS

## Tasks
- [x] 6d.1: Shared Reference Epoch Constant (Y18)
- [x] 6d.2: _format_time() Performance Fix (Y17) — depends on 6d.1
- [ ] 6d.3: Configurable Health Server Port (Y16)
- [ ] 6d.4: Server Task Verification After Startup (Y20)
- [ ] 6d.5: Narrow Exception Suppression During Shutdown (Y27)
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
