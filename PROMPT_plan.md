# Phase 2.1: Scenario Auto-Scheduling

Read these files before starting:
1. `CLAUDE.md` (non-negotiable rules)
2. `plans/phase-2.1-scenario-auto-scheduling.md` (full plan)
3. `plans/phase-2.1-tasks.json` (task list)
4. `plans/phase-2.1-progress.md` (current progress)

## What to build

Add auto-scheduling for all 7 Phase 2 scenarios in `ScenarioEngine._generate_timeline()`. Currently only Phase 1 scenarios (UnplannedStop, JobChangeover, ShiftChange) are auto-scheduled. Phase 2 scenarios need the same treatment so a default simulator run produces scenario data.

## Two categories

**Time-scheduled (5):** WebBreak, DryerDrift, InkExcursion, RegistrationDrift, ColdStart. Use the same uniform-random start time pattern as `_schedule_unplanned_stops()`. Frequency from config fields.

**Condition-triggered (2):** CoderDepletion, MaterialSplice. These monitor model values. Schedule one monitoring instance per expected trigger cycle (CoderDepletion: ~24h, MaterialSplice: ~3h).

## Key references

- Existing scheduling methods: `_schedule_unplanned_stops()`, `_schedule_job_changeovers()`, `_schedule_shift_changes()` in `scenario_engine.py`
- Config models: `ScenariosConfig` and its children in `config.py`
- Scenario constructors: each in `scenarios/*.py` — check `__init__` params carefully
- Config-to-param mapping is in the plan (Section "Config field mapping")

## Task order

Work through tasks 2.1.1 through 2.1.6 in order. One task per commit. Update `plans/phase-2.1-progress.md` after each task.

## Constraints

- Do NOT implement Poisson inter-arrival or priority rules. That is Phase 4.
- Do NOT modify scenario classes. Only modify `scenario_engine.py` and tests.
- Follow `if not cfg.enabled: return` guard pattern on every scheduling method.
- All existing 1483 tests must still pass after each task.
