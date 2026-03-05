# Phase 6 Code Quality Review — Collatr Factory Simulator

**Date:** 2026-03-05 · **Reviewer:** Independent sub-agent (Opus)

## Executive Summary

The Phase 6 remediation work is **well-executed**. Across 52 commits and 101 files, the code quality is consistently high: well-documented, defensively coded, and follows the project's established patterns. No critical bugs (RED) were found. I identified 4 YELLOW (should-fix) items and 6 GREEN (noted) observations.

---

## Summary Table

| # | Severity | File(s) | Finding |
|---|----------|---------|---------|
| 1 | 🟡 YELLOW | `mqtt_publisher.py` | MQTT retry `_delays` tuple has 3 elements but only 2 are ever used |
| 2 | 🟡 YELLOW | `cli.py` | SIGTERM handler closures capture `_this_task` from enclosing scope — fragile if refactored |
| 3 | 🟡 YELLOW | `opcua_server.py` | `_build_inactive_nodes` duplicates ~40 lines of folder/variable creation from `_build_node_tree` |
| 4 | 🟡 YELLOW | `test_opcua_inactive.py` | Tests don't cover overlapping `opcua_node` paths between profiles (edge case) |
| 5 | 🟢 GREEN | `press.py`, `oven.py` | Dryer/oven Cholesky calls `effective_sigma()` without parent — correct for current configs but silently ignores speed-dependent sigma if ever added |
| 6 | 🟢 GREEN | `config.py` | `ClockDriftConfig` allows negative `drift_rate_s_per_day` — intentional per commit message but no validator docstring explaining why |
| 7 | 🟢 GREEN | `cli.py` | `_start_server` settle_time=0.05s is a heuristic; may miss slow server failures |
| 8 | 🟢 GREEN | `ground_truth.py` | `write_header` scenario list is manually enumerated — if a new scenario is added to `ScenariosConfig`, it must be manually added here too |
| 9 | 🟢 GREEN | `modbus_server.py` | `float32_hr_addresses` name is misleading — it also includes uint32 addresses |
| 10 | 🟢 GREEN | `writer.py` | `CsvWriter.close()` idempotency relies on `self._file.closed` attribute from Python's file API — correct but undocumented assumption |

---

## Detailed Findings

### 🟡 YELLOW Issues (Should Fix)

**Y1. MQTT retry `_delays` tuple mismatch**
- **File:** `src/factory_simulator/protocols/mqtt_publisher.py`, lines ~430-445
- **Issue:** `_delays = (1.0, 2.0, 4.0)` has 3 elements but the loop only accesses `_delays[0]` and `_delays[1]` (the third attempt goes straight to `raise last_exc`). The `4.0` value is dead data.
- **Impact:** Minor — misleading to future maintainers who might expect 3 retry delays.
- **Fix:** Either use `_delays = (1.0, 2.0)` or restructure to use all 3 values if 4 attempts are desired.

**Y2. SIGTERM handler closure captures task reference**
- **File:** `src/factory_simulator/cli.py`, lines ~386-398 and ~449-461
- **Issue:** Both `_run_batch` and `_run_realtime` register a SIGTERM handler via `loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)`. The handler captures `_this_task = asyncio.current_task()` from the enclosing scope. This works correctly but:
  1. The handler is NOT removed when the function exits.
  2. On Windows, `add_signal_handler` raises `NotImplementedError`, correctly caught.
- **Impact:** Low — works correctly in the current flow. The pattern is duplicated between batch and realtime modes; could be extracted into a context manager.

**Y3. Code duplication in `_build_inactive_nodes`**
- **File:** `src/factory_simulator/protocols/opcua_server.py`, lines 415-510
- **Issue:** `_build_inactive_nodes` duplicates the folder hierarchy creation, EURange property, EngineeringUnits property, and MinimumSamplingInterval logic from `_build_node_tree`. If any property is added or changed in `_build_node_tree`, it must also be updated in `_build_inactive_nodes`.
- **Impact:** Medium — maintenance burden. A future change to the active node creation (e.g., adding a new OPC-UA property) could be forgotten in the inactive path.
- **Fix:** Extract shared node creation logic into a helper method that both active and inactive paths call, differing only in AccessLevel and StatusCode settings.

**Y4. Missing test for overlapping `opcua_node` paths**
- **File:** `tests/unit/test_protocols/test_opcua_inactive.py`
- **Issue:** No test for the scenario where a custom config accidentally uses the same `opcua_node` path in both active and inactive profiles. The `_build_inactive_nodes` code would crash with a `BadNodeIdAlreadyExists` exception from asyncua.
- **Impact:** Low — unlikely with bundled configs, but defensively important for user-provided configs.
- **Fix:** Add a guard in `_build_inactive_nodes` that skips nodes whose `node_path` is already in `self._nodes`.

---

### 🟢 GREEN Observations (Noted)

**G5. Cholesky `effective_sigma()` called without parent_value**
- **Files:** `press.py` line 532, `oven.py` line 401
- Both call `ng.effective_sigma()` (no `parent_value`), which returns the base `self._sigma`. Correct for current configs; would silently ignore speed-dependent sigma if ever added.

**G6. `ClockDriftConfig` negative drift allowed**
- **File:** `config.py`, `ClockDriftConfig` class
- Intentional per commit message. Documentation-only improvement.

**G7. `_start_server` settle time**
- The 0.05s settle time is arbitrary. Acceptable pragmatic trade-off.

**G8. Ground truth header scenario enumeration**
- `write_header` manually enumerates all scenario types. Maintenance risk if new scenarios added.

**G9. `float32_hr_addresses` naming**
- Also includes uint32 addresses. Rename to `dual_register_hr_addresses` for clarity.

**G10. CsvWriter close idempotency**
- Relies on Python's `self._file.closed` attribute. Standard pattern, correct.

---

## Specific Correctness Assessments

### Cholesky Implementations (Dryer/Oven Zones)
✅ **Correct.** Both follow the same pipeline as vibration. All three correlation matrices (vibration, dryer, oven) are symmetric, positive-definite, with unit diagonal. Verified numerically.

### Inactive OPC-UA Node Implementation
✅ **Correct.** AccessLevel=0, BadNotReadable, not in sync loop, collapsed mode only, folder cache reused.

### MQTT Startup Retry
✅ **Correct.** Handles connection refused, DNS failure, all attempts exhausted.

### SIGTERM Handler
✅ **Correct for both modes.** Batch and realtime paths handle CancelledError correctly. Windows fallback works.

### Writer Idempotent Close
✅ **Correct.** Both CsvWriter and ParquetWriter guard against double-close.

---

## Overall Assessment

**PASS — No RED issues.** The Phase 6 work is production-quality. The 4 YELLOW items are non-blocking quality improvements.
