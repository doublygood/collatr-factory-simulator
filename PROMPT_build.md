Read CLAUDE.md for project rules and conventions.

You are implementing Phase 4 (Full Scenario System and Data Quality) of the Collatr Factory Simulator.

## CONTEXT

Phases 0-3 are complete. Both profiles are fully operational:
- **Packaging**: 47 signals, 7 equipment generators, 10 scenario types, all 3 protocols
- **F&B**: 68 signals, 10 equipment generators (6 new + 4 shared), 7 F&B scenarios, CDAB + multi-slave Modbus
- 2059+ tests passing, ruff + mypy clean
- Ground truth JSONL logging operational

Phase 4 adds three major workstreams:
1. **Advanced scenarios**: bearing wear (exponential degradation), micro-stops, contextual anomalies, intermittent faults
2. **Poisson scheduling engine**: replaces uniform-random scheduling, adds priority/conflict rules
3. **Data quality injection**: comm drops, sensor disconnect/stuck, Modbus exceptions/partial responses, duplicate timestamps, timezone offsets, noise calibration

The full plan is in `plans/phase-4-scenarios-data-quality.md`. Read it.

## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read `plans/phase-4-scenarios-data-quality.md` for the full plan
2. Read `plans/phase-4-tasks.json` to find the **first** task with `"passes": false`
3. Check `depends_on` — if any dependency has `"passes": false`, skip to the next eligible task
4. Read the relevant source files and PRD sections referenced in that task
5. Implement ONLY that single task
6. Run tests: `ruff check src tests && mypy src && pytest` — ALL must pass
7. Update `plans/phase-4-tasks.json`: set `"passes": true` for your completed task
8. Update `plans/phase-4-progress.md` with what you built and any decisions
9. Commit: `phase-4: <what> (task 4.X)`
10. Do NOT push. Pushing is handled externally.
11. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## PHASE-SPECIFIC NOTES

### Poisson Scheduling (Task 4.1)

Replace ALL `_schedule_*` methods. The core change:

```python
# OLD: uniform-random start times within a period
start = rng.uniform(period_start, period_end)

# NEW: Poisson inter-arrival times
mean_interval = period_seconds / ((freq_min + freq_max) / 2)
t = period_start
while t < period_end:
    gap = rng.exponential(mean_interval)
    t += max(gap, min_duration)  # enforce min gap
    if t < period_end:
        schedule_instance(t)
```

Also fix these carried-forward items in the same task:
- **Y1**: Replace `rng.integers(0, 2**63)` with `SeedSequence.spawn()` for child RNG creation
- **Y3**: Add `sim_duration_s` parameter to `ScenarioEngine.__init__()`. DataEngine should pass `config.simulation.sim_duration_s` (or default to `_SHIFT_SECONDS` if not set). Update `DataEngine.__init__` accordingly.

**Important**: Existing scheduling tests use exact count assertions. These need to become range checks (Poisson is stochastic). Use `assert min_expected <= count <= max_expected` style.

### Priority System (Task 4.2)

Add a `priority` class attribute to the `Scenario` base class:

```python
class Scenario(ABC):
    priority: str = "non_state_changing"  # default
```

Override in specific scenario classes:
- `"state_changing"`: WebBreak, UnplannedStop, JobChangeover, CipCycle, ColdChainBreak, SealIntegrityFailure
- `"non_state_changing"`: DryerDrift, InkExcursion, RegistrationDrift, FillWeightDrift, OvenThermalExcursion, ChillerDoorAlarm, BatchCycle
- `"background"`: BearingWear, IntermittentFault
- `"micro"`: MicroStop

In `ScenarioEngine.tick()`:
1. Sort pending scenarios by priority (state_changing first)
2. Before activating a state-changing scenario, call `complete()` on active non-state-changing scenarios
3. Before activating a non-state-changing scenario, check if a state-changing is active — if so, defer
4. Background and micro scenarios activate without checks

### Bearing Wear (Task 4.4)

The exponential model:
```python
vibration_increase = base_rate * math.exp(k * elapsed_hours)
```
Where `elapsed_hours` is simulation hours since scenario start. This produces the hockey-stick curve from the IMS/NASA bearing data.

The motor current increase follows the same curve at smaller magnitude:
```python
current_pct_increase = current_factor * math.exp(k * elapsed_hours)
```

This scenario operates at a MUCH longer timescale than other scenarios. At 1x: weeks. At 100x batch: hours. The scenario must work correctly at any time scale — use `sim_time` not wall clock.

### Micro-Stops (Task 4.5)

Key distinction: micro-stops do NOT change `press.machine_state`. The machine stays Running (2). Only `press.line_speed` dips. This is what makes them hard to detect — OEE systems see "running" but throughput drops.

The micro-stop needs to temporarily override the press generator's speed target, then restore it. Use the same pattern as other scenarios that modify generator state: save original, override, restore on completion.

### Contextual Anomalies (Task 4.6)

These are state-dependent: the anomaly only makes sense in a specific machine state. The scenario engine must:
1. Pick an anomaly type (weighted by probability)
2. Wait for the target machine state to occur
3. Once in the target state, inject the anomalous value
4. If the state doesn't occur within 2x the scheduled window, cancel

This requires a new activation pattern — most scenarios activate at a fixed time. Contextual anomalies activate when a state condition is met.

### Intermittent Faults (Task 4.7)

The three-phase model:
- **Phase 1**: Spikes are rare (1-3/day) and short (10-60s). Duration: weeks.
- **Phase 2**: Spikes are frequent (5-20/day) and longer (30-300s). Duration: days.
- **Phase 3**: Fault becomes continuous (permanent).

The scenario tracks its own internal clock for phase transitions. Each spike within a phase is a brief event — the scenario doesn't "complete" after each spike. It runs for the full multi-week duration, producing spikes at increasing frequency.

### Data Quality Injection (Tasks 4.8-4.12)

The key architectural decision: data quality injection happens at TWO levels:

1. **Protocol level** (Tasks 4.8, 4.10, 4.11): comm drops, Modbus exceptions, partial responses, duplicate timestamps, timezone offsets. These are handled by the protocol adapters themselves.

2. **Store level** (Tasks 4.9, 4.12): sensor disconnect, stuck sensor. These override values in the SignalStore AFTER generators write but BEFORE protocol servers read. The `DataQualityInjector` class manages this.

Both levels use the engine's RNG hierarchy for determinism.

### Noise Calibration (Tasks 4.13-4.14)

This is primarily a YAML config update, not a code change. The signal models already support `noise_distribution`, `noise_df`, `noise_phi` params. You're setting the correct values per PRD Section 10.3.

For AR(1) noise: set `noise_distribution: "ar1"` and `noise_phi: 0.7` (typical PID autocorrelation).
For Student-t noise: set `noise_distribution: "student_t"` and `noise_df: 5-8`.
For Gaussian noise: omit `noise_distribution` (default) or set to `"gaussian"`.

### Reproducibility (Task 4.16)

The test must be STRICT: same seed → byte-identical output. This means:
- No floating-point non-determinism (no `set()` iteration, no dict ordering issues pre-3.7)
- All RNG usage goes through `numpy.random.Generator` with `SeedSequence`
- No `random` module usage
- No time-dependent behaviour in signal generation

## STOPPING RULES

**After completing ONE task:** Output `TASK_COMPLETE` and stop immediately.
Do not look for the next task. Do not start another task.
The ralph.sh loop will call you again for the next iteration.

**If a test cannot pass after 3 genuine attempts:** STOP. Document the issue in `plans/phase-4-progress.md`. Output `TASK_BLOCKED: <reason>` and stop.

**Dependency check:** If the first `"passes": false` task has unsatisfied dependencies, find the next task whose dependencies are all satisfied. If NO tasks are eligible, output `PHASE_BLOCKED: waiting on <task IDs>` and stop.

## COMPLETION

When ALL tasks in the task JSON have `"passes": true`:
1. Do NOT output PHASE_COMPLETE yet.
2. Spawn a sub-agent code review.
3. Write the review to `plans/phase-4-review.md`
4. Review checks:
   - Poisson scheduling produces exponentially-distributed inter-arrival times
   - Priority rules work: state-changing preempts non-state-changing
   - All 4 new scenario types fire correctly
   - Data quality injections are deterministic for same seed
   - Noise calibration matches PRD Section 10.3 for both profiles
   - Reproducibility test passes
   - No regressions in packaging or F&B tests
5. Address all RED Must Fix findings. Re-run `ruff check src tests && mypy src && pytest` after each fix.
6. Commit fixes: `phase-4: address code review findings`
7. Push all commits.
8. THEN output: PHASE_COMPLETE
