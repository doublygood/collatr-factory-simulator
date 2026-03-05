# Phase 7: Polish — Progress

## Status: IN PROGRESS

## Tasks
- [x] 7.1: Fix MQTT Retry Delays Tuple (CQ-Y1)
- [x] 7.2: Extract SIGTERM Handler Context Manager (CQ-Y2)
- [x] 7.3: Extract OPC-UA Node Creation Helper (CQ-Y3)
- [x] 7.4: Guard Overlapping OPC-UA Node Paths + Test (CQ-Y4) — depends on 7.3
- [ ] 7.5: Remove Dead FactoryInfo.timezone Field (G-Arch21)
- [ ] 7.6: Elevate OPC-UA Error Log Levels (G-Arch23)
- [ ] 7.7: Return Defensive Copy from store.get_all() (G-Arch24)
- [ ] 7.8: Add I/O Error Handling in Ground Truth _write_line (G-Arch26)
- [ ] 7.9: Rename float32_hr_addresses to dual_register_hr_addresses (G-Proto8)
- [ ] 7.10: Derive Modbus Update Interval from Config (G-Proto10)
- [ ] 7.11: Improve _compute_block_size Documentation (G-Proto13)
- [ ] 7.12: Explicit line_id + ShiftChange HH:MM Validator (G-Proto14 + G-Arch-ShiftChange)
- [ ] 7.13: CI fail-fast: false + Validate All Fixes

## Task 7.1 Notes

Restructured MQTT retry loop to use all 3 delay values (1s, 2s, 4s) for 4 total connection attempts, up from 3. Changed `_max_attempts` to derive from `len(_delays) + 1` and sleep condition to `attempt < len(_delays)`. Updated existing exhausted-retry test to expect 4 attempts. Added `test_start_succeeds_on_fourth_attempt` verifying all 3 delays are used. 3167 tests pass, ruff + mypy clean.

## Task 7.2 Notes

Extracted `_sigterm_cancels_current_task()` context manager in cli.py to eliminate duplicated SIGTERM handler setup between `_run_batch` (was lines 387-397) and `_run_realtime` (was lines 450-461). The context manager registers a SIGTERM handler that cancels the current asyncio task, and removes the handler on exit via `loop.remove_signal_handler`. Platform safety: `NotImplementedError`/`OSError` suppressed on registration (Windows), `NotImplementedError`/`ValueError` suppressed on removal. Both `_run_batch` and `_run_realtime` now use `with _sigterm_cancels_current_task():` wrapping their main logic. Added `test_sigterm_handler_removed_after_context_exit` verifying the handler is cleaned up. Updated existing source inspection test to also check for `remove_signal_handler`. 3168 tests pass, ruff + mypy clean.

## Task 7.3 Notes

Extracted `_create_variable_node()` helper method in `opcua_server.py` that handles all shared node creation logic: folder hierarchy traversal/creation via `folder_cache`, variable node creation with correct data type, EURange property, EngineeringUnits property, and MinimumSamplingInterval attribute. The helper takes optional `access_level` (for inactive nodes' AccessLevel=0) and `status_code` (for BadNotReadable) parameters, plus a `tick_interval_ms` parameter since active and inactive paths use different config objects for the fallback. `_build_node_tree` now calls the helper then handles setpoint writability and node registration. `_build_inactive_nodes` now calls the helper with `access_level=0` and `status_code=BadNotReadable`. Pure refactor — 3168 tests pass, ruff + mypy clean.

## Task 7.4 Notes

Added overlap guard in `_build_inactive_nodes` (`opcua_server.py`): before creating each inactive node, check if `sig_cfg.opcua_node` already exists in `self._node_to_signal` (populated by active node creation). If so, log a warning and `continue` — the active node is preserved, the duplicate inactive node is skipped. Added two tests in `test_opcua_inactive.py` using synthetic configs with a shared `opcua_node` path: `test_overlapping_opcua_node_skipped` (server starts OK, active node remains readable) and `test_overlapping_opcua_node_logged` (warning logged with node path). 3170 tests pass, ruff + mypy clean.

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
