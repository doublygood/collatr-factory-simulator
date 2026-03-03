# Phase 4: Full Scenario System and Data Quality

Read these files before starting:
1. `CLAUDE.md` (non-negotiable rules)
2. `plans/phase-4-scenarios-data-quality.md` (full plan)
3. `plans/phase-4-tasks.json` (task list)
4. `plans/phase-4-progress.md` (current progress)

## What to build

Phase 4 has three workstreams:

1. **Scheduling engine** (Tasks 4.1-4.2): Replace uniform-random scheduling with Poisson inter-arrival times. Add scenario priority/conflict resolution.
2. **Advanced scenarios** (Tasks 4.4-4.7): Motor bearing wear (exponential degradation), micro-stops (speed dips without state change), contextual anomalies (normal values in wrong state), intermittent faults (3-phase progression).
3. **Data quality injection** (Tasks 4.8-4.12): Communication drops, sensor disconnect/stuck sensor, Modbus exceptions, partial responses, duplicate timestamps, timezone offsets.
4. **Noise calibration** (Tasks 4.13-4.14): Set correct noise parameters for all signals in both profiles per PRD Section 10.3.
5. **Polish** (Tasks 4.15-4.16): Counter rollover support, reproducibility test, final integration.

## Key references

- Scenario system: `prd/05-scenario-system.md` (5.5, 5.13, 5.15, 5.16, 5.17)
- Data quality: `prd/10-data-quality-realism.md` (all sections)
- Config reference: `prd/appendix-d-configuration-reference.md`
- Implementation phases: `prd/appendix-f-implementation-phases.md` (Phase 4)

## Task order

Work through tasks by dependency order. Check `depends_on` in the tasks JSON — skip tasks whose dependencies are not yet complete. Tasks 4.1 and 4.3 can be done in parallel (no dependencies). Tasks 4.13 and 4.14 also have no dependencies.

## Constraints

- All existing 2059+ tests must still pass after each task
- Poisson scheduling changes existing test assertions from exact counts to range checks
- Data quality injection must be deterministic for a given seed
- No `random` module usage — all RNG through `numpy.random.Generator` with `SeedSequence`
- No wall-clock usage in any simulation logic (Rule 6)
