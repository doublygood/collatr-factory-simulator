# Phase 6a: Critical Fixes — Progress

## Status: IN PROGRESS

## Tasks
- [x] 6a.1: Wire Ground Truth Logger into CLI (R1)
- [x] 6a.2: Fix Ground Truth Header — Add Missing Scenarios (R2)
- [x] 6a.3: Dockerfile Hardening (R3 + R4)
- [x] 6a.4: OPC-UA EngineeringUnits Property (R5)
- [x] 6a.5: Fix Oven Gateway UID Routing in Realistic Mode (R6)
- [x] 6a.6: Fix Severity Weight Key Mismatch (Y1)
- [x] 6a.7: Fix Double-Logging of Ground Truth Events (Y2)
- [ ] 6a.8: Handle Open Scenarios in Evaluator (Y3)
- [ ] 6a.9: Validate All Fixes — Full Suite + Integration Check

## Notes

Tasks 6a.1-6a.8 are all independent (no dependencies between them). Task 6a.9 depends on all others.

## Task 6a.3 — Dockerfile Hardening

**What was fixed:**
- Created `.dockerignore` excluding `.git`, `.github`, `tests`, `output`, `plans`, `prd`, `*.egg-info`, `__pycache__`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, and `*.md` (with `!README.md` exception)
- Changed `pip install --no-cache-dir -e .` to `pip install --no-cache-dir .` (regular install for production)
- Added `useradd -m -r simulator` to create non-root user
- Created `/app/output` with `chown simulator:simulator` so batch output is writable
- Added `USER simulator` directive before `ENTRYPOINT`

**Decisions:**
- Non-root user created as a system user (`-r`) with home directory (`-m`) per review recommendation
- Output dir `/app/output` pre-created so the non-root user can write batch output without needing root
- `.md` files excluded from Docker context except `README.md` (plans/prd docs not needed in image)

## Task 6a.2 — Fix Ground Truth Header — Add Missing Scenarios

**What was fixed:**
- Added 10 missing scenario types to `write_header()` in `ground_truth.py`
- Phase 4 optionals (`micro_stop`, `contextual_anomaly`, `intermittent_fault`): guarded with `if scfg.X is not None and scfg.X.enabled`
- F&B optionals (`batch_cycle`, `oven_thermal_excursion`, `fill_weight_drift`, `seal_integrity_failure`, `chiller_door_alarm`, `cip_cycle`, `cold_chain_break`): same None guard pattern
- Added `TestHeaderScenarioCompleteness` class with 6 new tests to `test_ground_truth.py`
- Updated imports in test file to include all new config classes

**Decisions:**
- Used `is not None` guard for all optional fields (Phase 4 + F&B) since they default to `None` in `ScenariosConfig` when not present in the YAML
- The packaging profile test verifies none of the optional scenario names appear when fields are `None`
- The F&B config file test (`test_fb_config_produces_complete_scenario_list`) loads the real YAML to verify end-to-end

## Task 6a.4 — OPC-UA EngineeringUnits Property

**What was fixed:**
- Added `EngineeringUnits` property to every OPC-UA variable node in `_build_node_tree()` in `opcua_server.py`
- Uses `ua.EUInformation` with `NamespaceUri="http://www.opcfoundation.org/UA/units/un/cefact"`, `UnitId=-1`, and `DisplayName`/`Description` from `sig_cfg.units or ""`
- Property added immediately after the existing `EURange` property
- Added `TestEngineeringUnitsAttribute` class to `test_opcua.py` with 4 tests: presence on all nodes, namespace URI, UnitId=-1, and key unit string values
- Note: `press.ink_viscosity` unit is `"seconds"` (Zahn cup efflux time) per PRD Section 02 — not cP

**Decisions:**
- `UnitId=-1` as specified in the plan (no UNECE code mapping needed for simulator)
- Helper `_read_engineering_units()` follows same pattern as existing `_read_eurange()` helper

## Task 6a.1 — Wire Ground Truth Logger into CLI

**What was fixed:**
- Added `--ground-truth-path` CLI argument to the `run` subcommand
- In `_async_run()`, always create a `GroundTruthLogger` before building the engine:
  - Explicit `--ground-truth-path` arg takes precedence
  - Batch mode defaults to `<batch-output>/ground_truth.jsonl`
  - Real-time mode defaults to `./ground_truth.jsonl`
- Call `ground_truth.open()` + `ground_truth.write_header(config)` after construction
- Pass logger to `DataEngine(ground_truth=ground_truth)`
- `ground_truth.close()` called in `finally` block wrapping `_run_batch`/`_run_realtime`
- Updated `_run_args` test helper to include `ground_truth_path=None`
- Added parser tests, batch-mode JSONL existence/header tests, and path-override test

**Decisions:**
- Always create the logger (not only in batch mode) so real-time runs also record events
- Used `getattr(args, "ground_truth_path", None)` for backward compat with tests that predate the new field

## Task 6a.5 — Fix Oven Gateway UID Routing in Realistic Mode

**What was fixed:**
- Added `secondary_uid_remap: dict[int, int]` field to `ModbusEndpointSpec` in `topology.py` (default `{}`). Maps collapsed-mode slave IDs to the UIDs they should appear under in realistic mode.
- In `_foodbev_modbus()`, added `secondary_uid_remap={11: 1, 12: 2, 13: 3}` to the oven gateway `ModbusEndpointSpec`. Eurotherm zone controllers (UIDs 11-13 in collapsed mode) now appear as UIDs 1, 2, 3 in realistic mode per PRD 03a.
- Updated `ModbusServer.start()` UID routing logic: when `endpoint.secondary_uid_remap` is non-empty, endpoint UIDs claimed by remapped secondaries are NOT mapped to the primary context; instead secondary contexts are registered under their realistic-mode UIDs. Collapsed mode (no endpoint) is unchanged.
- Added `tests/integration/test_oven_uid_routing_realistic.py` with 13 tests verifying UIDs 1/2/3 return zone PV/SP/output IR data, UID 10 returns energy IR 120-121, and UIDs 1/2/3 are isolated from the primary context.

**Decisions:**
- `secondary_uid_remap` field placed alongside `uid_equipment_map` in the dataclass since both are UID-related mapping fields.
- Collapsed mode preserves existing behaviour: secondary slaves remain at UIDs 11/12/13 (tested by `test_modbus_fnb_integration.py`). No behaviour change for existing tests.
- In realistic mode, when no `secondary_uid_remap` is set (empty dict), all endpoint UIDs map to primary context as before — backward compatible.
- 2998 tests passed after the change.

## Task 6a.7 — Fix Double-Logging of Ground Truth Events

**What was fixed:**
- Removed duplicate `log_scenario_start()` calls from `_on_activate()` in: `bearing_wear.py`, `micro_stop.py`, `intermittent_fault.py`, `batch_cycle.py`, `cip_cycle.py`, `cold_chain_break.py`
- Removed duplicate `log_scenario_end()` calls from `_on_complete()` in all 7 scenarios: above 6 plus `contextual_anomaly.py`
- Preserved all scenario-specific detail logging: `log_state_change()` in batch_cycle/cip_cycle/cold_chain_break, `log_signal_anomaly()` in cold_chain_break, phase transition logging in intermittent_fault, `log_contextual_anomaly()` in contextual_anomaly
- Added `TestNoDoubleLogging` class (3 tests) to `test_scenario_engine.py`: MicroStop, BearingWear, and deferred-start scenario each produce exactly 1 start + 1 end event
- Fixed one integration test: `test_fnb_cross_protocol.py` was asserting `"batch_cycle"` (snake_case), updated to `"BatchCycle"` (PascalCase, from `type().__name__`)

**Decisions:**
- ScenarioEngine logs `type(scenario).__name__` (PascalCase) — this is canonical; scenarios that previously used snake_case strings (batch_cycle, cip_cycle, cold_chain_break) now produce PascalCase events
- All `log_state_change()` and `log_signal_anomaly()` calls preserved (detail logging, not start/end duplicates)
- 2999 tests passed after the change

## Task 6a.6 — Fix Severity Weight Key Mismatch

**What was fixed:**
- Added `import re` to `evaluator.py`
- Added `_pascal_to_snake(name)` helper: `re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()` — converts PascalCase to snake_case, leaves already-snake_case strings unchanged
- Applied normalisation in `_compute()` for both `severity_weights.get(...)` lookups (total_weight and detected_weight)
- Added `TestSeverityWeightKeyNormalisation` class to `test_evaluator.py` with 4 tests: helper conversion, PascalCase GT → snake_case weight lookup (weighted_recall ≠ recall), backward compat with snake_case event types, unknown PascalCase → default weight 1.0

**Decisions:**
- Only the weight lookups in `_compute()` were normalised — no latency target lookups exist in the evaluator currently, so no other changes were needed
- The `_pascal_to_snake` function is a module-level helper (not a method) since it's a pure string transform with no state
- 2996 tests passed after the change
