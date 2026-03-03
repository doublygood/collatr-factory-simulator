Read CLAUDE.md for project rules and conventions.

You are implementing Phase 2.1 (Scenario Auto-Scheduling) of the Collatr Factory Simulator.

## CONTEXT

Phase 2 is complete. All 7 Phase 2 scenarios are implemented and tested (WebBreak, DryerDrift, InkExcursion, RegistrationDrift, ColdStart, CoderDepletion, MaterialSplice). The independent review found 3 RED issues; R1 (MQTT timestamps) and R2 (DryerDrift signal names) are fixed. R3 remains: Phase 2 scenarios are never auto-scheduled in `_generate_timeline()`. A default simulator run produces no Phase 2 scenario data.

Phase 2.1 adds auto-scheduling for all 7 Phase 2 scenarios so they fire without manual `add_scenario()` calls. This uses the same simple uniform-random pattern as existing Phase 1 scheduling methods. This is NOT the full Poisson inter-arrival engine (that is Phase 4).

The full plan is in `plans/phase-2.1-scenario-auto-scheduling.md`. Read it.

## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read `plans/phase-2.1-scenario-auto-scheduling.md` for the full plan
2. Read `plans/phase-2.1-tasks.json` to find the **first** task with `"passes": false`
3. Read the relevant source files and PRD sections referenced in that task
4. Implement ONLY that single task
5. Run tests: `ruff check src tests && mypy src && pytest` -- ALL must pass
6. Update `plans/phase-2.1-tasks.json`: set `"passes": true` for your completed task
7. Update `plans/phase-2.1-progress.md` with what you built and any decisions
8. Commit: `phase-2.1: <what> (task 2.1.X)`
9. Do NOT push. Pushing is handled externally.
10. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## PHASE-SPECIFIC NOTES

### Task 2.1.1: Time-based scheduling (5 scenarios)

Add 5 new methods to `ScenarioEngine` in `scenario_engine.py`:
- `_schedule_web_breaks()` -- uses `frequency_per_week`, convert to sim duration
- `_schedule_dryer_drifts()` -- uses `frequency_per_shift`, same pattern as unplanned stops
- `_schedule_ink_excursions()` -- uses `frequency_per_shift`
- `_schedule_registration_drifts()` -- uses `frequency_per_shift`
- `_schedule_cold_starts()` -- ColdStart is reactive (monitors state), schedule N monitoring instances with uniform random start times

Call all 5 from `_generate_timeline()`. Add imports for all 5 scenario classes.

**Config-to-param mapping (these names differ):**

| Config field | Scenario param |
|---|---|
| `DryerDriftConfig.duration_seconds` | `drift_duration_range` |
| `DryerDriftConfig.max_drift_c` | `drift_range` |
| `InkViscosityExcursionConfig.duration_seconds` | `duration_range` |
| `RegistrationDriftConfig.duration_seconds` | `duration_range` |
| `WebBreakConfig.recovery_seconds` | `recovery_seconds` |
| `ColdStartSpikeConfig.spike_duration_seconds` | `spike_duration_range` |
| `ColdStartSpikeConfig.spike_magnitude` | `power_multiplier_range` |

Follow the existing `_schedule_unplanned_stops()` pattern exactly. Use `if not cfg.enabled: return` guard.

### Task 2.1.2: Condition-triggered scheduling (2 scenarios)

- `_schedule_coder_depletions()` -- schedule one monitoring instance per ~24h of sim time
- `_schedule_material_splices()` -- schedule one monitoring instance per ~3h of sim time

These scenarios watch model values, not clocks. They need continuous monitoring instances.

| Config field | Scenario param |
|---|---|
| `MaterialSpliceConfig.trigger_diameter_mm` | `trigger_diameter` |
| `MaterialSpliceConfig.splice_duration_seconds` | `splice_duration_range` |
| `CoderDepletionConfig.low_ink_threshold` | `low_ink_threshold` |
| `CoderDepletionConfig.empty_threshold` | `empty_threshold` |
| `CoderDepletionConfig.recovery_duration_seconds` | `recovery_duration_range` |

### Task 2.1.3: Signal name validation test

Add a test in `tests/unit/test_scenario_engine.py` that verifies every signal ID in `_AFFECTED_SIGNALS` exists as a valid key in the store. Create a DataEngine, tick once, then check all signal names. This catches the DryerDrift naming bug class permanently.

### Task 2.1.4: Auto-scheduling integration test

Test that `ScenarioEngine` with default config and `sim_duration_s=7*86400` produces instances of all 10 scenario types (3 Phase 1 + 7 Phase 2). Check sorted by start_time.

### Task 2.1.5: Update acceptance test procedure

Update `plans/phase-2-acceptance-test.md` Scenario Evidence section with Phase 2 scenario expectations per tier.

### Task 2.1.6: Docstring cleanup

Update module docstring and `_generate_timeline()` docstring in `scenario_engine.py`.

## STOPPING RULES

**After completing ONE task:** Output `TASK_COMPLETE` and stop immediately.
Do not look for the next task. Do not start another task.
The ralph.sh loop will call you again for the next iteration.

**When ALL tasks in the task JSON have "passes": true:**
1. Do NOT output PHASE_COMPLETE yet.
2. Spawn a sub-agent code review.
3. Write the review to `plans/phase-2.1-review.md`
4. Review checks: all `_AFFECTED_SIGNALS` entries match store keys, all 10 scenario types auto-scheduled, config-to-param mapping correct, no regressions.
5. Address all RED Must Fix findings. Re-run `ruff check src tests && mypy src && pytest` after each fix.
6. Commit fixes: `phase-2.1: address code review findings`
7. Push all commits.
8. THEN output: PHASE_COMPLETE
