Read CLAUDE.md for project rules and conventions.

You are implementing Phase 7 (Polish) of the Collatr Factory Simulator.

## CONTEXT

Phases 0-5 are feature-complete. Phase 6 (a-e) resolved all 33 RED+YELLOW issues from a three-reviewer deep code review. A post-Phase 6 triple review found 4 new YELLOW issues and confirmed all fixes.

Current state:
- **All protocols**: Modbus, OPC-UA, MQTT fully implemented with EngineeringUnits, MinimumSamplingInterval, Cholesky correlation, profile-aware exceptions
- **Inactive OPC-UA nodes**: Collapsed mode creates AccessLevel=0 / BadNotReadable nodes for inactive profile
- **All generators tested**: dedicated test files for all 15 generators
- **Shared time_utils**: REFERENCE_EPOCH_TS and sim-time converters centralised
- **CI**: Python 3.12 + 3.13 matrix, expanded integration tests
- **Config**: health port configurable, dead fields removed, clamp/drift validators fixed
- **Error handling**: server startup verification, narrow exception suppression, MQTT retry, SIGTERM handler
- 3166+ tests passing, ruff + mypy clean

**Phase 7 addresses 4 new YELLOW issues from the post-Phase 6 quality review plus 9 actionable GREEN items from the original reviews.**

The post-Phase 6 review reports are in:
- `plans/review-post-phase6-code-quality.md` (primary for tasks 7.1-7.4)
- `plans/review-post-phase6-test-ci.md` (task 7.13)
- `plans/review-architecture.md` (tasks 7.5-7.8)
- `plans/review-protocol-fidelity.md` (tasks 7.9-7.12)

The Phase 7 plan with detailed per-task instructions is in `plans/phase-7-polish.md`. Read it.

## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read `plans/phase-7-polish.md` for the full plan
2. Read `plans/phase-7-tasks.json` to find the **first** task with `"passes": false`
3. Check `depends_on` — if any dependency has `"passes": false`, skip to the next eligible task
4. Read the relevant review file for full context on the issue (the `review_ref` field tells you which)
5. Read the relevant source files before changing anything
6. Implement ONLY that single task's fix
7. Run the new/modified test file alone first: `ruff check src tests && pytest tests/path/to/test.py -v --tb=short`
8. Run ALL tests: `ruff check src tests && mypy src && pytest` — ALL must pass
9. Update `plans/phase-7-tasks.json`: set `"passes": true` for your completed task
10. Update `plans/phase-7-progress.md` with what you fixed and any decisions
11. Commit: `phase-7: <what> (task 7.X)`
12. Do NOT push. Pushing is handled externally.
13. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## PHASE-SPECIFIC NOTES

### MQTT Retry Delays (Task 7.1)

The retry loop is in `mqtt_publisher.py` around line 652. `_delays = (1.0, 2.0, 4.0)` but the loop only makes 3 attempts (uses indices 0 and 1, then raises on 3rd failure). Restructure to use all 3 delays = 4 total attempts. The key change is the loop range and the conditional sleep logic. Existing retry tests must be updated to expect 4 attempts instead of 3.

### SIGTERM Context Manager (Task 7.2)

The duplicated SIGTERM handler code is at `cli.py` lines 387-397 and 450-461. Create a `_sigterm_cancels_current_task()` context manager. Important: the context manager should **remove** the signal handler on exit (via `loop.remove_signal_handler`). Handle `NotImplementedError` (Windows) and `ValueError` (handler already removed) in the cleanup.

### OPC-UA Node Helper (Task 7.3)

This is the most substantial refactor. `_build_node_tree` (line 279) and `_build_inactive_nodes` (line 415) share folder creation, variable node creation, EURange, EngineeringUnits, and MinimumSamplingInterval logic. Extract a `_create_variable_node()` helper method. The active path adds nodes to `self._nodes` and `self._node_to_signal` *after* calling the helper; the inactive path does not. The helper itself should just create and configure the node. **This is a pure refactor — all 11 existing OPC-UA inactive tests plus all active OPC-UA tests must continue to pass.**

### Overlapping Node Guard (Task 7.4)

Depends on 7.3. In `_build_inactive_nodes`, before creating each node, check `if sig.opcua_node in self._node_to_signal`. If so, log a warning and `continue`. Test with a synthetic config where one signal has the same `opcua_node` in both profiles.

### Store Defensive Copy (Task 7.7)

`MappingProxyType` is preferred over `dict.copy()` — zero-copy, read-only. Change return type from `dict[str, SignalValue]` to `Mapping[str, SignalValue]`. Import `Mapping` from `collections.abc`. Check all callers: `modbus_server.py` sync loop, `opcua_server.py` sync, `mqtt_publisher.py` publish, `health/server.py`. All should work fine with `Mapping` since they only iterate. **If mypy complains about callers expecting `dict`, fix the caller type hints too.**

### Ground Truth I/O (Task 7.8)

Wrap the body of `_write_line` in `try/except OSError`. On failure: log `logger.warning(...)`, set `self._fh = None`. Subsequent `_write_line` calls will return early (existing `if self._fh is None: return` guard). Import `logging` if not already present.

### Modbus Interval (Task 7.10)

The 50ms at line 1270 is already effectively `tick_interval_ms / 2` for the default 100ms tick. Make it derive from config: `self._config.simulation.tick_interval_ms / 2000.0`. Add a brief comment: `# Sync at half the tick interval to minimise staleness`.

## STOPPING RULES

**After completing ONE task:** Output `TASK_COMPLETE` and stop immediately.
Do not look for the next task. Do not start another task.

**If a test cannot pass after 3 genuine attempts:** STOP. Document the issue in `plans/phase-7-progress.md`. Output `TASK_BLOCKED: <reason>` and stop.

**Dependency check:** If the first `"passes": false` task has unsatisfied dependencies, find the next task whose dependencies are all satisfied. If NO tasks are eligible, output `PHASE_BLOCKED: waiting on <task IDs>` and stop.

## COMPLETION

When ALL tasks in the task JSON have `"passes": true`:
1. Output: PHASE_COMPLETE

**Phase 7 is the final polish phase.** After completion, only genuinely cosmetic or by-design observations remain from the code reviews.
