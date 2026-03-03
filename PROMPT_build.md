# Build Instructions: Phase 2.1

Read `PROMPT_plan.md` first, then `CLAUDE.md`, then the plan file.

## Per-task loop

For each task in `plans/phase-2.1-tasks.json` where `passes: false`:

1. Read the task description from the plan
2. Implement the changes
3. Run checks: `ruff check src tests && mypy src && python -m pytest tests/ -x -q`
4. If checks pass: commit with message `phase-2.1: <task description> (task N.N.N)`
5. Update `plans/phase-2.1-progress.md` (check off the task)
6. Update `plans/phase-2.1-tasks.json` (set `passes: true`)
7. Move to the next task

## After all tasks pass

Run the full test suite one final time: `python -m pytest tests/ -v`

Do NOT push to git. Dex will review and push.

## Config-to-param mapping (critical)

These config field names differ from scenario constructor param names:

| Config field | Scenario param |
|---|---|
| `DryerDriftConfig.duration_seconds` | `drift_duration_range` |
| `DryerDriftConfig.max_drift_c` | `drift_range` |
| `InkViscosityExcursionConfig.duration_seconds` | `duration_range` |
| `RegistrationDriftConfig.duration_seconds` | `duration_range` |
| `WebBreakConfig.recovery_seconds` | `recovery_seconds` |
| `ColdStartSpikeConfig.spike_duration_seconds` | `spike_duration_range` |
| `ColdStartSpikeConfig.spike_magnitude` | `power_multiplier_range` |
| `MaterialSpliceConfig.trigger_diameter_mm` | `trigger_diameter` |
| `MaterialSpliceConfig.splice_duration_seconds` | `splice_duration_range` |
| `CoderDepletionConfig.low_ink_threshold` | `low_ink_threshold` |
| `CoderDepletionConfig.empty_threshold` | `empty_threshold` |
| `CoderDepletionConfig.recovery_duration_seconds` | `recovery_duration_range` |
