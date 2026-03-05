# Phase 7 (Polish) — Code Quality Review

**Date:** 2026-03-05 · **Reviewer:** Independent sub-agent (Opus)
**Scope:** `git diff bdae359..7679635` (13 commits, 21 files, +624/-304 lines)

## Overall Assessment

**Phase 7 is high quality.** All 13 tasks are correctly implemented. The biggest refactor (Task 7.3 — OPC-UA helper extraction) faithfully preserves all original behavior. No bugs found. No edge cases missed. Code style is consistent with existing codebase.

---

## Summary Table

| Rating | Count | Items |
|--------|-------|-------|
| 🔴 RED (bug) | 0 | — |
| 🟡 YELLOW | 3 | #12 (redundant `_initial_value` call), #30 (spike test constant not renamed), #36 (regex compiled per-call) |
| 🟢 GREEN | 35 | All other findings |

---

## YELLOW Findings

**Y1. Redundant `_initial_value(vtype)` call (#12)**
- `_create_variable_node()` computes `init_val = _initial_value(vtype)` internally. After calling the helper, `_build_node_tree` recomputes it for `_last_written_setpoints`. Deterministic and trivially cheap — harmless but could be avoided by returning `init_val` from the helper.

**Y2. Spike test constant naming (#30)**
- `test_spike_modbus.py`: Parameter renamed to `dual_register_addresses` but the constant is still `FLOAT32_ADDRESSES` and comments still say "non-float32 register". Since spike tests are standalone/exploratory, low priority but creates naming inconsistency.

**Y3. Regex compiled per-call (#36)**
- `re.compile()` inside `_valid_hhmm` runs on every validation call. Should be a module-level `_HHMM_RE = re.compile(...)`. In practice this only runs once at config load time, so negligible performance impact.

---

## Findings by Task

### Task 7.1 — MQTT Retry Delays
- Loop correctly uses all 3 delays (4 total attempts). `for...else` pattern raises when all fail. `_max_attempts` derived from `len(_delays) + 1`. Test asserts all 3 sleep values. **All GREEN.**

### Task 7.2 — SIGTERM Context Manager
- Correctly removes handler in `finally` via `contextlib.suppress`. Exception sets for add/remove are correct. Windows fallback works. Both callers wrap correctly. **All GREEN.**

### Task 7.3 — OPC-UA Node Creation Helper
- Behaviour preservation verified: folder hierarchy, EURange, EngineeringUnits, MinimumSamplingInterval all match original. Active/inactive paths pass correct config objects. Returns `(var_node, vtype)`. **1 minor YELLOW** (redundant init_val). Rest GREEN.

### Task 7.4 — Overlapping Node Guard
- Checks `node_path in self._node_to_signal` (populated before inactive build). Logs warning with path. Tests verify server starts and active node preserved. **All GREEN.**

### Task 7.5 — Remove Dead timezone
- Clean removal from model, both YAMLs, both test assertions. **GREEN.**

### Task 7.6 — Elevate OPC-UA Log Levels
- Three debug→warning elevations. Setpoint read failure now captures `exc` (previously bare `except Exception: continue`). **All GREEN.**

### Task 7.7 — MappingProxyType
- Return type `Mapping[str, SignalValue]`. All callers verified (none mutate). `MappingProxyType({}) == {}` works. Test covers both setitem and delitem. **All GREEN.**

### Task 7.8 — Ground Truth I/O Handling
- `try/except OSError` correctly scopes to file write/flush. `self._fh = None` for graceful degradation. Test mocks OSError and verifies. **All GREEN.**

### Task 7.9 — Rename float32_hr_addresses
- Consistent rename across RegisterMap, FactoryDeviceContext, all callers, all tests. **1 YELLOW** (spike test constant). Rest GREEN.

### Task 7.10 — Modbus Update Interval
- Derives from `tick_interval_ms / 2000.0`. Preserves existing behavior for default 100ms. Good comment. **GREEN.**

### Task 7.11 — _compute_block_size Documentation
- Clear docstring and inline comment. **GREEN.**

### Task 7.12 — line_id + HH:MM Validator
- Explicit line_id added. Regex + range validation correct. 7 tests with good coverage. **1 minor YELLOW** (regex per-call). Rest GREEN.

### Task 7.13 — CI fail-fast: false
- Correctly placed. **GREEN.**
