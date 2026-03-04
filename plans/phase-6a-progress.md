# Phase 6a: Critical Fixes — Progress

## Status: IN PROGRESS

## Tasks
- [x] 6a.1: Wire Ground Truth Logger into CLI (R1)
- [ ] 6a.2: Fix Ground Truth Header — Add Missing Scenarios (R2)
- [ ] 6a.3: Dockerfile Hardening (R3 + R4)
- [ ] 6a.4: OPC-UA EngineeringUnits Property (R5)
- [ ] 6a.5: Fix Oven Gateway UID Routing in Realistic Mode (R6)
- [ ] 6a.6: Fix Severity Weight Key Mismatch (Y1)
- [ ] 6a.7: Fix Double-Logging of Ground Truth Events (Y2)
- [ ] 6a.8: Handle Open Scenarios in Evaluator (Y3)
- [ ] 6a.9: Validate All Fixes — Full Suite + Integration Check

## Notes

Tasks 6a.1-6a.8 are all independent (no dependencies between them). Task 6a.9 depends on all others.

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
