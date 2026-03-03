# Phase 2.1: Scenario Auto-Scheduling - Progress

## Status: IN PROGRESS (tasks 2.1.1-2.1.4 complete, 2.1.5-2.1.6 remaining)

## Tasks
- [x] 2.1.1: Schedule time-based Phase 2 scenarios (WebBreak, DryerDrift, InkExcursion, RegistrationDrift, ColdStart)
- [x] 2.1.2: Schedule condition-triggered Phase 2 scenarios (CoderDepletion, MaterialSplice)
- [x] 2.1.3: Signal name validation test
- [x] 2.1.4: Auto-scheduling integration test
- [ ] 2.1.5: Update acceptance test procedure
- [ ] 2.1.6: Update docstrings and create progress file

## Notes

### Tasks 2.1.1-2.1.3 (completed in commit d6e5d42 through c8b6f78)

Local agent completed all 3 tasks in a single session (before PROMPT_build.md was fixed to enforce one-task-per-session). The bookkeeping files were not updated but the code is correct:

- 7 new scheduling methods added to `scenario_engine.py` (~193 lines)
- Signal name validation test in `test_scenario_engine.py`
- Auto-scheduling integration test in `test_scenario_engine.py`
- 11 files changed, 400 insertions
- All existing tests plus new tests pass

### PROMPT_build.md fix (commit 78bd5fa)

The original Phase 2.1 PROMPT_build.md said "for each task... move to the next task" which caused the local agent to do all tasks in one session without updating bookkeeping. Fixed to match Phase 2 pattern: "ONE TASK PER SESSION", TASK_COMPLETE signal, explicit stopping rules.
