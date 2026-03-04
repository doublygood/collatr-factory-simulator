# Phase 6a: Critical Fixes

**Source:** `plans/consolidated-review-action-plan.md` (Batches 1 + 2)
**Scope:** All 6 RED issues + 3 high-priority YELLOW issues (data correctness)
**Goal:** Fix every bug that produces incorrect output or blocks core functionality

---

## Context

Three independent code reviewers (protocol fidelity, signal integrity, architecture) found 54 issues across the codebase. Phase 6a addresses the 6 REDs (must-fix) plus the 3 highest-impact YELLOWs that affect data correctness. Later sub-phases (6b, 6c) will handle the remaining YELLOW and GREEN issues.

Review files with full detail:
- `plans/review-architecture.md`
- `plans/review-signal-integrity.md`
- `plans/review-protocol-fidelity.md`

---

## Tasks

### Task 6a.1: Wire Ground Truth Logger into CLI

**Issue:** R1 (Architecture review) — Ground truth logger is never instantiated.
**File:** `src/factory_simulator/cli.py`

The CLI's `_async_run()` creates a `DataEngine` without a `GroundTruthLogger`. The `ground_truth` parameter defaults to `None`, so no scenario events are ever recorded in real-time or batch mode. The `evaluate` subcommand references ground truth files that the `run` subcommand can never produce.

**What to do:**
1. In `_async_run()`, create a `GroundTruthLogger` instance and pass it to `DataEngine`
2. The ground truth output path should default to `<batch_output_dir>/ground_truth.jsonl` in batch mode
3. In real-time mode, use a configurable path (or a sensible default like `./ground_truth.jsonl`)
4. Add `--ground-truth-path` CLI argument to override the default path
5. Call `write_header(config)` after construction
6. Ensure `close()` is called in the `finally` block
7. **Test:** Run a short batch simulation and verify the JSONL file is created, contains a header line, and contains scenario events. Verify the evaluate subcommand can consume the file.

**PRD refs:** PRD 4.7 (ground truth format), PRD 12 (evaluation protocol)

---

### Task 6a.2: Fix Ground Truth Header — Add Missing Scenarios

**Issue:** R2 (Architecture review) — `write_header()` only lists 11 packaging scenarios.
**File:** `src/factory_simulator/engine/ground_truth.py`

The `write_header()` method (around line 62-105) hardcodes a list of 11 packaging scenarios using individual `if scfg.<name>.enabled` checks. It is missing:

**Phase 4 scenarios (3):**
- `micro_stop`
- `contextual_anomaly`
- `intermittent_fault`

**F&B scenarios (7):**
- `batch_cycle`
- `oven_thermal_excursion`
- `fill_weight_drift`
- `seal_integrity_failure`
- `chiller_door_alarm`
- `cip_cycle`
- `cold_chain_break`

**What to do:**
1. Add all 10 missing scenario types to the header's scenarios list
2. Check each against `scfg` — F&B scenarios are on optional config fields (may be `None`), so guard with `if scfg.<name> is not None and scfg.<name>.enabled`
3. **Test:** Load both packaging and F&B configs, call `write_header()`, parse the JSON header line, and verify the scenarios list is complete for each profile.

**PRD refs:** PRD 4.7, `config/factory.yaml`, `config/factory-foodbev.yaml`

---

### Task 6a.3: Dockerfile Hardening — .dockerignore and Non-Root User

**Issues:** R3 + R4 (Architecture review) — No `.dockerignore`, container runs as root.
**Files:** `.dockerignore` (new), `Dockerfile`

**What to do:**

1. Create `.dockerignore`:
```
.git
.github
tests
output
plans
prd
*.egg-info
__pycache__
.mypy_cache
.ruff_cache
.pytest_cache
*.md
!README.md
```

2. Update `Dockerfile`:
   - Add a non-root user: `RUN useradd -m -r simulator && mkdir -p /app/output && chown simulator:simulator /app/output`
   - Add `USER simulator` before `CMD`/`ENTRYPOINT`
   - Change `pip install -e .` to `pip install --no-cache-dir .` (not editable in production)
   - Ensure the output directory is writable by the non-root user

3. **Test:** Verify `docker compose build` succeeds (if Docker is available). At minimum, verify `.dockerignore` exists and `Dockerfile` has `USER` directive. The CI doesn't test Docker builds currently, so a syntax check is sufficient.

---

### Task 6a.4: OPC-UA EngineeringUnits Property

**Issue:** R5 (Protocol review) — PRD Appendix B requires `EngineeringUnits` on all OPC-UA variable nodes. The code never sets it.
**File:** `src/factory_simulator/protocols/opcua_server.py`

The `_build_node_tree()` method (around line 230-297) creates variable nodes with `EURange` but not `EngineeringUnits`. Any OPC-UA client that auto-discovers unit metadata (common in SCADA/HMI systems like Ignition, KepServerEX) will see no units.

**What to do:**
1. After creating each variable node and adding `EURange`, add an `EngineeringUnits` property using `asyncua`'s `EUInformation` type
2. The signal config has a `units` field (e.g. `"m/min"`, `"N"`, `"°C"`) — use this for the `DisplayName` and `Description`
3. Use the OPC Foundation UNECE namespace: `"http://www.opcfoundation.org/UA/units/un/cefact"`
4. Set `UnitId=-1` (no standard UNECE code mapping — acceptable for simulator)
5. **Test:** Create an OPC-UA server with a few test signals, connect an asyncua client, browse node properties, and verify `EngineeringUnits` is present with the correct unit string.

**PRD refs:** PRD Appendix B (OPC-UA node tree), PRD 3.2

---

### Task 6a.5: Fix Oven Gateway UID Routing in Realistic Mode

**Issue:** R6 (Protocol review) — Oven gateway UIDs 1,2,3 can't reach per-zone Eurotherm input registers.
**Files:** `src/factory_simulator/topology.py`, `src/factory_simulator/protocols/modbus_server.py`

In realistic mode, PRD 03a says the oven gateway at port 5031 serves UIDs 1,2,3 for zone controllers and UID 10 for energy. Each zone controller (UID 1/2/3) should serve IR 0 (PV), IR 1 (SP), IR 2 (output power).

Currently, UIDs 1,2,3 map to the primary `FactoryDeviceContext` which serves IR 100+ (main block). The per-zone IR 0/1/2 registers are on secondary slaves at UIDs 11-13 (from collapsed-mode multi-slave feature). A pymodbus client following the PRD 03a topology table cannot read per-zone Eurotherm IRs at UIDs 1/2/3.

**What to do:**
1. In realistic mode, remap the secondary slaves for the oven gateway port to UIDs 1/2/3 instead of 11/12/13
2. Keep collapsed mode behaviour unchanged (UIDs 11/12/13 on shared port)
3. UID 10 (energy meter) remains on the primary context
4. **Test:** In realistic mode, verify that reading IR 0 at UID 1 on port 5031 returns zone 1 PV. Verify UID 10 returns energy registers. Verify UIDs not in {1,2,3,10} return Modbus exception. Verify collapsed mode still works with UIDs 11/12/13.

**PRD refs:** PRD 03a (Section 3a.2, 3a.4)

---

### Task 6a.6: Fix Severity Weight Key Mismatch

**Issue:** Y1 (Signal integrity review) — Severity weight dict keys use `snake_case` but ground truth uses `PascalCase`.
**Files:** `src/factory_simulator/evaluation/metrics.py`, `src/factory_simulator/evaluation/evaluator.py`

The `DEFAULT_SEVERITY_WEIGHTS` dict uses keys like `"web_break"`, `"bearing_wear"`. But ground truth scenario names come from `type(scenario).__name__` which produces `"WebBreak"`, `"BearingWear"`. The evaluator's `severity_weights.get(m.event_type, 1.0)` always misses, falling back to default weight 1.0. Weighted recall/F1 are silently identical to unweighted.

**What to do:**
1. Normalise the lookup in the evaluator: convert the ground truth event type to snake_case before looking up in severity weights. A simple helper: `re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()`
2. OR: change the weight dict keys to PascalCase to match ground truth. (Normalising in the evaluator is preferable — it's more defensive.)
3. Also normalise `DEFAULT_LATENCY_TARGETS` the same way (same file, same issue).
4. **Test:** Create a ground truth file with PascalCase scenario names, run the evaluator with non-default severity weights, verify the weighted metrics differ from unweighted metrics. A test with `"WebBreak"` events and `{"web_break": 10.0}` weight should produce weighted_recall ≠ recall.

**PRD refs:** PRD 12.4 (severity-weighted metrics)

---

### Task 6a.7: Fix Double-Logging of Ground Truth Events

**Issue:** Y2 (Signal integrity review) — Both `ScenarioEngine` and individual scenarios log start/end GT events.
**Files:** `src/factory_simulator/engine/scenario_engine.py`, various scenario files in `src/factory_simulator/scenarios/`

The `ScenarioEngine.tick()` detects PENDING→ACTIVE transitions and calls `gt.log_scenario_start()`. But several scenarios also call `gt.log_scenario_start()` in their own `_on_activate()` or `tick()` methods. This produces duplicate entries that would distort evaluation metrics.

Scenarios with internal GT logging (from grep):
- `bearing_wear.py` (log_scenario_start + log_scenario_end)
- `micro_stop.py` (log_scenario_start + log_scenario_end)
- `contextual_anomaly.py` (log_scenario_end only)
- `intermittent_fault.py` (log_scenario_start + log_scenario_end)
- `batch_cycle.py` (log_scenario_start + log_scenario_end)
- `cip_cycle.py` (log_scenario_start + log_scenario_end)
- `cold_chain_break.py` (log_scenario_start + log_scenario_end)

**What to do:**
1. Pick ONE source of truth for GT logging. The `ScenarioEngine` is the right place — it has visibility into all scenario transitions.
2. Remove the `log_scenario_start()` and `log_scenario_end()` calls from individual scenario files listed above.
3. Keep any scenario-specific GT logging that adds extra detail (e.g. phase transitions within intermittent faults, specific parameter changes) — just remove the duplicate start/end events.
4. Verify that `ScenarioEngine` logs end events on ACTIVE→COMPLETED transitions (check that this already works).
5. **Test:** Run a short simulation, parse the ground truth JSONL, verify each scenario instance has exactly one `scenario_start` and one `scenario_end` event (no duplicates). Use a known seed for determinism.

**PRD refs:** PRD 4.7

---

### Task 6a.8: Handle Open Scenarios in Evaluator

**Issue:** Y3 (Signal integrity review) — Evaluator drops open scenarios (start without matching end).
**File:** `src/factory_simulator/evaluation/evaluator.py`

`load_ground_truth()` pairs scenario_start with scenario_end events. If a scenario is still active at simulation end (e.g. BearingWear that hasn't completed), the start event has no matching end. Currently these are silently dropped, meaning long-running scenarios near simulation end are excluded from evaluation — understating recall.

**What to do:**
1. For scenario_start events with no matching scenario_end, treat the simulation end time as the end time
2. The simulation end time can be inferred from the last event timestamp in the ground truth file
3. Mark these as `open=True` in the `GroundTruthEvent` dataclass (add the field if needed) so they can be optionally filtered
4. **Test:** Create a ground truth file where BearingWear starts but never ends. Verify the evaluator includes it as an event (using sim end as end time). Verify a detection within the event window counts as TP.

**PRD refs:** PRD 12.3 (event matching)

---

### Task 6a.9: Validate All Fixes — Full Suite + Integration Check

**Depends on:** 6a.1-6a.8

This is the final validation task. No new code — just verification.

**What to do:**
1. Run `ruff check src tests && mypy src && pytest --tb=short -q` — ALL must pass
2. Run a short batch simulation end-to-end: `python -m factory_simulator run --batch-output /tmp/test-batch --batch-duration 1h --batch-format csv --seed 42`
3. Verify ground truth JSONL was produced and contains events
4. If the evaluate subcommand works with the produced ground truth, run a quick evaluation test
5. Verify no regressions in existing tests
6. If any test fails, fix it before committing

---

## Completion Criteria

All 9 tasks pass. Full test suite green. Ground truth pipeline works end-to-end (run → ground truth JSONL → evaluate).
