# Phase 7: Polish — Progress

## Status: COMPLETE

## Tasks
- [x] 7.1: Fix MQTT Retry Delays Tuple (CQ-Y1)
- [x] 7.2: Extract SIGTERM Handler Context Manager (CQ-Y2)
- [x] 7.3: Extract OPC-UA Node Creation Helper (CQ-Y3)
- [x] 7.4: Guard Overlapping OPC-UA Node Paths + Test (CQ-Y4) — depends on 7.3
- [x] 7.5: Remove Dead FactoryInfo.timezone Field (G-Arch21)
- [x] 7.6: Elevate OPC-UA Error Log Levels (G-Arch23)
- [x] 7.7: Return Defensive Copy from store.get_all() (G-Arch24)
- [x] 7.8: Add I/O Error Handling in Ground Truth _write_line (G-Arch26)
- [x] 7.9: Rename float32_hr_addresses to dual_register_hr_addresses (G-Proto8)
- [x] 7.10: Derive Modbus Update Interval from Config (G-Proto10)
- [x] 7.11: Improve _compute_block_size Documentation (G-Proto13)
- [x] 7.12: Explicit line_id + ShiftChange HH:MM Validator (G-Proto14 + G-Arch-ShiftChange)
- [x] 7.13: CI fail-fast: false + Validate All Fixes

## Task 7.1 Notes

Restructured MQTT retry loop to use all 3 delay values (1s, 2s, 4s) for 4 total connection attempts, up from 3. Changed `_max_attempts` to derive from `len(_delays) + 1` and sleep condition to `attempt < len(_delays)`. Updated existing exhausted-retry test to expect 4 attempts. Added `test_start_succeeds_on_fourth_attempt` verifying all 3 delays are used. 3167 tests pass, ruff + mypy clean.

## Task 7.2 Notes

Extracted `_sigterm_cancels_current_task()` context manager in cli.py to eliminate duplicated SIGTERM handler setup between `_run_batch` (was lines 387-397) and `_run_realtime` (was lines 450-461). The context manager registers a SIGTERM handler that cancels the current asyncio task, and removes the handler on exit via `loop.remove_signal_handler`. Platform safety: `NotImplementedError`/`OSError` suppressed on registration (Windows), `NotImplementedError`/`ValueError` suppressed on removal. Both `_run_batch` and `_run_realtime` now use `with _sigterm_cancels_current_task():` wrapping their main logic. Added `test_sigterm_handler_removed_after_context_exit` verifying the handler is cleaned up. Updated existing source inspection test to also check for `remove_signal_handler`. 3168 tests pass, ruff + mypy clean.

## Task 7.3 Notes

Extracted `_create_variable_node()` helper method in `opcua_server.py` that handles all shared node creation logic: folder hierarchy traversal/creation via `folder_cache`, variable node creation with correct data type, EURange property, EngineeringUnits property, and MinimumSamplingInterval attribute. The helper takes optional `access_level` (for inactive nodes' AccessLevel=0) and `status_code` (for BadNotReadable) parameters, plus a `tick_interval_ms` parameter since active and inactive paths use different config objects for the fallback. `_build_node_tree` now calls the helper then handles setpoint writability and node registration. `_build_inactive_nodes` now calls the helper with `access_level=0` and `status_code=BadNotReadable`. Pure refactor — 3168 tests pass, ruff + mypy clean.

## Task 7.4 Notes

Added overlap guard in `_build_inactive_nodes` (`opcua_server.py`): before creating each inactive node, check if `sig_cfg.opcua_node` already exists in `self._node_to_signal` (populated by active node creation). If so, log a warning and `continue` — the active node is preserved, the duplicate inactive node is skipped. Added two tests in `test_opcua_inactive.py` using synthetic configs with a shared `opcua_node` path: `test_overlapping_opcua_node_skipped` (server starts OK, active node remains readable) and `test_overlapping_opcua_node_logged` (warning logged with node path). 3170 tests pass, ruff + mypy clean.

## Task 7.5 Notes

Removed dead `FactoryInfo.timezone` field from `config.py`. The field was defined with default `"Europe/London"` but never read by any code — all timestamps use UTC via `time_utils`. Removed the `timezone` key from both `config/factory.yaml` and `config/factory-foodbev.yaml`. Updated `test_config.py` to remove the timezone assertion from `test_defaults` and the timezone parameter from `test_custom_values`. 3170 tests pass, ruff + mypy clean.

## Task 7.6 Notes

Elevated three OPC-UA error log levels from `logger.debug` to `logger.warning` for operational visibility:
1. `_sync_values` freeze failed (line 554): `logger.debug` → `logger.warning`
2. `_sync_values` setpoint read failed (line 622): bare `except Exception: continue` → `except Exception as exc:` with `logger.warning` before `continue`
3. `_sync_values` write failed (line 695): `logger.debug` → `logger.warning`

These are genuine error conditions (OPC-UA node operations failing) that should be visible in production logs, not hidden at DEBUG level. 3170 tests pass, ruff + mypy clean.

## Task 7.7 Notes

Changed `store.get_all()` to return `types.MappingProxyType` wrapping the internal `_signals` dict instead of returning it directly. This provides a zero-copy, read-only view — callers can iterate and read but cannot accidentally mutate the store. Return type changed from `dict[str, SignalValue]` to `Mapping[str, SignalValue]` (from `collections.abc`). All callers (modbus_server, opcua_server, mqtt_publisher, health/server, output/writer, tests) only iterate/read, so no caller changes needed. Added `test_get_all_not_mutable` verifying that `__setitem__` and `__delitem__` raise `TypeError`. 3171 tests pass, ruff + mypy clean.

## Task 7.8 Notes

Wrapped the body of `_write_line` in `ground_truth.py` with `try/except OSError`. On I/O failure (disk full, permission error, etc.), the logger logs a warning ("Ground truth write failed — disabling logger") and sets `self._fh = None`, which causes all subsequent `_write_line` calls to return early via the existing `if self._fh is None: return` guard. This degrades gracefully — the simulation continues running but stops writing ground truth events. Added `test_write_line_io_error_disables_logger` which mocks `_fh.write` to raise `OSError`, verifies the warning is logged, confirms `_fh` is set to `None`, and verifies subsequent calls are no-ops. 3172 tests pass, ruff + mypy clean.

## Task 7.9 Notes

Renamed `float32_hr_addresses` to `dual_register_hr_addresses` in `RegisterMap` dataclass and all `.add()` calls in `build_register_map()`. Renamed `float32_addresses` parameter/attribute in `FactoryDeviceContext` to `dual_register_addresses` — this is the constructor param, internal `_dual_register_addresses` attr, and all pass-through sites (main context at line 799, secondary contexts at line 828). Updated `setValues` docstring to "Reject FC06 on dual-register (float32/uint32) pairs." Updated all test references in `test_modbus.py` (field access, param name, test name/docstring), `test_modbus_exceptions.py` (param name, comment), and `tests/spikes/test_spike_modbus.py` (standalone copy of the class). The set correctly tracks both words of float32 AND uint32 register pairs — the old name was misleading since it only mentioned float32. 3172 tests pass, ruff + mypy clean.

## Task 7.10 Notes

Replaced hardcoded `asyncio.sleep(0.05)` (50ms) in Modbus sync loop with `self._config.simulation.tick_interval_ms / 2000.0`, deriving the update interval from config. For the default 100ms tick this evaluates to the same 0.05s. Added comment explaining the rationale: "Sync at half the tick interval to minimise staleness." No test changes needed — no tests asserted on the specific sleep value. 3172 tests pass, ruff + mypy clean.

## Task 7.11 Notes

Improved `_compute_block_size` docstring and added inline comment per G-Proto13. Docstring now explains the three components clearly: pymodbus 1-based indexing (address → index = address+1), 32-bit values spanning 2 registers (indices N+1 and N+2), therefore block needs max(address)+3 entries. Added inline comment on the return line: `# +3: pymodbus 1-based indexing (+1) + 32-bit value spans 2 registers (+2)`. Documentation-only change — 3172 tests pass, ruff + mypy clean.

## Task 7.12 Notes

Two small fixes per G-Proto14 and G-Arch-ShiftChange:

1. **Explicit `line_id` in packaging config**: Added `line_id: "packaging1"` to `config/factory.yaml` MQTT section, matching how `factory-foodbev.yaml` already specifies `line_id: "foodbev1"`. The field had a correct default in the Pydantic model, but making it explicit in the YAML eliminates ambiguity.

2. **ShiftChange HH:MM validator**: Added `field_validator("times")` to `ShiftChangeConfig` that validates each time string against `^\d{2}:\d{2}$` regex format and checks `0 <= HH <= 23`, `0 <= MM <= 59`. Added `import re` to config.py. Invalid formats like `"6:00"`, `"abc"` raise "Shift time must be HH:MM format"; out-of-range values like `"25:00"`, `"12:60"` raise "Invalid shift time".

Added 7 tests in `TestShiftChangeConfig`: valid times accepted, boundary times (00:00, 23:59) accepted, invalid format rejected, non-numeric rejected, invalid hour rejected, invalid minute rejected, packaging config has line_id. 3179 tests pass, ruff + mypy clean.

## Task 7.13 Notes

Added `fail-fast: false` to the CI unit test matrix strategy in `.github/workflows/ci.yml`. This ensures that a failure on Python 3.12 does not cancel the 3.13 run (and vice versa), providing better diagnostics when issues are version-specific. Full validation: ruff clean, mypy clean, 3179 tests pass in 235s.

## Notes

Phase 7 addresses 4 new YELLOWs from the post-Phase 6 code quality review plus 9 actionable GREENs from the original three-reviewer deep review.

12 GREEN items were deliberately skipped as not worth the effort or risk:
- G-Arch22 (start_time vs protocol epoch) — confusing but correct
- G-Arch25 (clock default 2024 vs epoch 2026) — documenting is enough
- G-Proto9 (OPC-UA namespace assertion) — extremely unlikely edge case
- G-Proto11 (MQTT 100ms sleep granularity) — acceptable real-world jitter
- G-Proto12 (clock drift direction) — config-dependent, works correctly
- G-Sig-G1 through G-Sig-G8 — all working as designed or negligible impact
- CQ-G5 through CQ-G10 — all correct or low-value documentation changes (except CQ-G9 which is task 7.9)
