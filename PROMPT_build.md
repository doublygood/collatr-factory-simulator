Read CLAUDE.md for project rules and conventions.

You are implementing Phase 6a (Critical Fixes) of the Collatr Factory Simulator.

## CONTEXT

Phases 0-5 are complete. The simulator is feature-complete and release-ready:
- **Packaging**: 47 signals, 7 equipment generators, 17 scenario types, all 3 protocols
- **F&B**: 68 signals, 10 equipment generators, 7 F&B scenarios, CDAB + multi-slave Modbus
- **Network topology**: collapsed + realistic modes, multi-port Modbus/OPC-UA, scan cycle quantisation, clock drift, independent connection drops
- **Evaluation framework**: event-level matching, severity-weighted metrics, random baseline, CLI
- **Batch output**: CSV and Parquet, CLI entry point, Docker Compose, README
- 2963+ tests passing, ruff + mypy clean

Three independent code reviewers (protocol fidelity, signal integrity, architecture) audited the entire codebase and found 54 issues: 6 RED, 27 YELLOW, 21 GREEN.

**Phase 6a addresses the 6 RED issues + 3 highest-impact YELLOWs (data correctness).**

The full review reports are in:
- `plans/review-architecture.md`
- `plans/review-signal-integrity.md`
- `plans/review-protocol-fidelity.md`
- `plans/consolidated-review-action-plan.md`

The Phase 6a plan with detailed per-task instructions is in `plans/phase-6a-critical-fixes.md`. Read it.

## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read `plans/phase-6a-critical-fixes.md` for the full plan
2. Read `plans/phase-6a-tasks.json` to find the **first** task with `"passes": false`
3. Check `depends_on` — if any dependency has `"passes": false`, skip to the next eligible task
4. Read the relevant review file for full context on the issue (the `review_ref` field tells you which)
5. Read the relevant source files before changing anything
6. Implement ONLY that single task's fix
7. Run the new/modified test file alone first: `ruff check src tests && pytest tests/path/to/test.py -v --tb=short`
8. Run ALL tests: `ruff check src tests && mypy src && pytest` — ALL must pass
9. Update `plans/phase-6a-tasks.json`: set `"passes": true` for your completed task
10. Update `plans/phase-6a-progress.md` with what you fixed and any decisions
11. Commit: `phase-6a: <what> (task 6a.X)`
12. Do NOT push. Pushing is handled externally.
13. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## PHASE-SPECIFIC NOTES

### Ground Truth Logger (Task 6a.1)

This is the biggest bug. The CLI creates the DataEngine but never creates or passes a GroundTruthLogger. The `ground_truth` parameter defaults to `None`, so all scenario events are silently dropped.

Key files:
- `src/factory_simulator/cli.py` — the `_async_run()` function
- `src/factory_simulator/engine/ground_truth.py` — the `GroundTruthLogger` class
- `src/factory_simulator/engine/data_engine.py` — accepts `ground_truth` parameter

The DataEngine already handles the logger correctly when it receives one. The fix is in the CLI: create the logger, pass it in, manage its lifecycle.

### Ground Truth Header (Task 6a.2)

The `write_header()` method in `ground_truth.py` (around line 60-105) uses individual `if scfg.<name>.enabled` checks for each scenario. Only 11 packaging scenarios are listed. Missing: 3 Phase 4 scenarios + 7 F&B scenarios.

F&B scenarios are on optional config fields — the `ScenariosConfig` attributes may be `None` for the packaging profile. Guard with `if scfg.<name> is not None and scfg.<name>.enabled`.

### OPC-UA EngineeringUnits (Task 6a.4)

Use asyncua's `EUInformation` type. The signal config already has a `units` field (string like "m/min", "N", "°C"). Example:

```python
from asyncua import ua
eu = ua.EUInformation(
    NamespaceUri="http://www.opcfoundation.org/UA/units/un/cefact",
    UnitId=-1,
    DisplayName=ua.LocalizedText(sig_cfg.units or ""),
    Description=ua.LocalizedText(sig_cfg.units or ""),
)
await var_node.add_property(ua.NodeId(0, 0), "EngineeringUnits", eu)
```

### Oven Gateway UID Routing (Task 6a.5)

The issue is in the topology builder for realistic mode. Currently, UIDs 1,2,3 on port 5031 map to the primary context (which serves IR 100+). The per-zone IR 0/1/2 registers are on secondary slaves at UIDs 11-13 (from collapsed-mode multi-slave).

In realistic mode, remap secondary slaves to UIDs 1,2,3. In collapsed mode, keep UIDs 11/12/13.

Check `topology.py` around the `_foodbev_modbus()` method and how secondary slaves are configured.

### Severity Weight Keys (Task 6a.6)

Ground truth logs `type(scenario).__name__` which produces PascalCase (`"WebBreak"`, `"BearingWear"`). The severity weight dict in `metrics.py` uses snake_case (`"web_break"`, `"bearing_wear"`).

Fix in the evaluator: normalise PascalCase to snake_case before looking up weights. Apply the same normalisation for latency target lookups.

### Double-Logging (Task 6a.7)

The ScenarioEngine detects PENDING→ACTIVE and ACTIVE→COMPLETED transitions and logs GT events. But several scenarios ALSO log their own start/end events internally. This produces duplicates.

**Decision: ScenarioEngine is the single source of truth.** Remove the duplicate `log_scenario_start()` and `log_scenario_end()` calls from individual scenarios. Keep any scenario-specific detail logging that provides extra information beyond start/end (e.g. phase transitions within intermittent faults).

Scenarios with internal GT logging to clean up:
- `bearing_wear.py`
- `micro_stop.py`
- `contextual_anomaly.py` (end only)
- `intermittent_fault.py`
- `batch_cycle.py`
- `cip_cycle.py`
- `cold_chain_break.py`

### Validation Task (Task 6a.9)

This is the "done gate." No new code — just verify everything works end-to-end. The full test suite must pass, and a batch simulation must produce a ground truth JSONL file that the evaluate subcommand can consume.

## STOPPING RULES

**After completing ONE task:** Output `TASK_COMPLETE` and stop immediately.
Do not look for the next task. Do not start another task.

**If a test cannot pass after 3 genuine attempts:** STOP. Document the issue in `plans/phase-6a-progress.md`. Output `TASK_BLOCKED: <reason>` and stop.

**Dependency check:** If the first `"passes": false` task has unsatisfied dependencies, find the next task whose dependencies are all satisfied. If NO tasks are eligible, output `PHASE_BLOCKED: waiting on <task IDs>` and stop.

## COMPLETION

When ALL tasks in the task JSON have `"passes": true`:
1. Push all commits.
2. Output: PHASE_COMPLETE
