# Phase 6a: Critical Fixes — Progress

## Status: IN PROGRESS

## Tasks
- [x] 6a.1: Wire Ground Truth Logger into CLI (R1)
- [x] 6a.2: Fix Ground Truth Header — Add Missing Scenarios (R2)
- [ ] 6a.3: Dockerfile Hardening (R3 + R4)
- [ ] 6a.4: OPC-UA EngineeringUnits Property (R5)
- [ ] 6a.5: Fix Oven Gateway UID Routing in Realistic Mode (R6)
- [ ] 6a.6: Fix Severity Weight Key Mismatch (Y1)
- [ ] 6a.7: Fix Double-Logging of Ground Truth Events (Y2)
- [ ] 6a.8: Handle Open Scenarios in Evaluator (Y3)
- [ ] 6a.9: Validate All Fixes — Full Suite + Integration Check

## Notes

Tasks 6a.1-6a.8 are all independent (no dependencies between them). Task 6a.9 depends on all others.

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
