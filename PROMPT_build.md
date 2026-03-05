Read CLAUDE.md for project rules and conventions.

You are implementing Phase 6c (Validation & Protocol Polish) of the Collatr Factory Simulator.

## CONTEXT

Phases 0-5 are complete (feature-complete simulator). Phase 6a fixed all RED issues and high-priority data correctness YELLOWs. Phase 6b fixed robustness YELLOWs (Y4-Y8).

Current state:
- **Packaging**: 47 signals, 7 equipment generators, 17 scenario types, all 3 protocols
- **F&B**: 68 signals, 10 equipment generators, 7 F&B scenarios, CDAB + multi-slave Modbus
- **Network topology**: collapsed + realistic modes, multi-port Modbus/OPC-UA, scan cycle quantisation, clock drift
- **Ground truth**: logger wired into CLI, header complete, double-logging fixed, open scenarios handled
- **Evaluation framework**: severity weights normalised, open scenarios handled, EvaluationConfig wired into FactoryConfig
- **Docker**: `.dockerignore`, non-root user, SIGTERM handler, OPC-UA EngineeringUnits
- **Robustness**: MQTT startup retry, CsvWriter idempotent close, profile-aware 0x06
- 3024+ tests passing, ruff + mypy clean

**Phase 6c addresses 7 medium-priority YELLOW issues (Y9-Y15) from the code review.**

The full review reports are in:
- `plans/review-architecture.md`
- `plans/review-protocol-fidelity.md`
- `plans/review-signal-integrity.md`
- `plans/consolidated-review-action-plan.md`

The Phase 6c plan with detailed per-task instructions is in `plans/phase-6c-validation-protocol-polish.md`. Read it.

## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read `plans/phase-6c-validation-protocol-polish.md` for the full plan
2. Read `plans/phase-6c-tasks.json` to find the **first** task with `"passes": false`
3. Check `depends_on` — if any dependency has `"passes": false`, skip to the next eligible task
4. Read the relevant review file for full context on the issue (the `review_ref` field tells you which)
5. Read the relevant source files before changing anything
6. Implement ONLY that single task's fix
7. Run the new/modified test file alone first: `ruff check src tests && pytest tests/path/to/test.py -v --tb=short`
8. Run ALL tests: `ruff check src tests && mypy src && pytest` — ALL must pass
9. Update `plans/phase-6c-tasks.json`: set `"passes": true` for your completed task
10. Update `plans/phase-6c-progress.md` with what you fixed and any decisions
11. Commit: `phase-6c: <what> (task 6c.X)`
12. Do NOT push. Pushing is handled externally.
13. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## PHASE-SPECIFIC NOTES

### SignalConfig Clamp Validator (Task 6c.1)

`SignalConfig` has `min_clamp` and `max_clamp` as optional floats (config.py lines 242-243) but no validator checks their ordering. Add a `@model_validator(mode="after")` that raises `ValueError` when both are set and `min_clamp > max_clamp`. Single-sided clamp (only one set) is valid. Equal values are valid.

**Important:** Use `model_validator` not `field_validator` — you need access to both fields simultaneously. The validator must be defined on the `SignalConfig` class. Check if `model_validator` is already imported; if not, add it to the pydantic import.

### ClockDriftConfig Negative Values (Task 6c.2)

`ClockDriftConfig` (config.py lines 1264-1285) has two field_validators that reject negative values for `initial_offset_ms` and `drift_rate_s_per_day`. Both reject `v < 0`. Negative values represent valid real-world scenarios (clock behind, clock losing time).

**Fix:** Remove both validators entirely, or change them to reject only non-finite values (`math.isnan` or `math.isinf`). The `ClockDriftModel` in `topology.py` already handles negative values correctly in its arithmetic.

**Test impact:** Check if any existing tests assert that negative values raise. If so, update them.

### Calibration Drift Docstring (Task 6c.3)

`SteadyStateModel` docstring (steady_state.py lines 54-58) says `calibration_drift_rate` is "per simulated second" but the PRD says "per simulated hour" (Section 4.2.1, Appendix D). The code correctly applies `rate * dt` where dt is seconds. The docstring already partially acknowledges this but is ambiguous.

**Fix:** Clarify the docstring to be unambiguous about units (per second internally, per hour in PRD, caller must convert). Add an inline comment at the application line (line 143). This is documentation only — no logic change.

### Random Walk Docstring (Task 6c.4)

`RandomWalkModel` docstring (random_walk.py lines 41-43) incorrectly claims `sqrt(dt)` scaling. The code uses linear `dt` per the PRD Section 4.2.5 formula. The docstring is misleading.

**Fix:** Replace the incorrect text with accurate description of the scaling. Mention the difference from the steady-state O-U drift model which DOES use `sqrt(dt)`. This is documentation only — no logic change.

### Dryer Zone Cholesky Correlation (Task 6c.5)

PRD Section 4.3.1 specifies a correlation matrix for dryer zones but only vibration axes implement Cholesky correlation. The press generator produces dryer zone temps with independent noise.

**Key pattern (from vibration.py):**
1. Build signal models WITHOUT noise (pass `noise=None`)
2. Store `NoiseGenerator` instances separately for `effective_sigma()` computation
3. Each tick: generate N(0,1) draws → apply Cholesky L → scale by sigma → add to raw values

**Check `_build_first_order_lag()`** in the press generator. If it creates noise generators and passes them to the lag model, you need to refactor: extract the noise generator before passing to the model, and pass `noise=None` to the lag model constructor. The lag model should produce clean deterministic output; correlated noise is added externally.

The `CholeskyCorrelator` class already exists in `factory_simulator.models.noise` — use it.

PRD dryer zone correlation matrix:
```
R = [[1.0,  0.1,  0.02],
     [0.1,  1.0,  0.1 ],
     [0.02, 0.1,  1.0 ]]
```

### Oven Zone Cholesky Correlation (Task 6c.6)

Same pattern as 6c.5 but for `OvenGenerator` with the oven zone matrix:
```
R = [[1.0,  0.15, 0.05],
     [0.15, 1.0,  0.15],
     [0.05, 0.15, 1.0 ]]
```

**Additional note:** The oven generator already has inter-zone thermal coupling (physical model). This is SEPARATE from Cholesky noise correlation. Both should coexist — thermal coupling operates on setpoints/lags, Cholesky operates on noise.

Check `_build_zone_temp()` in the oven generator for how noise is currently handled.

### Coil 4 Derivation (Task 6c.7)

`modbus_server.py:539` has:
```python
CoilDefinition(4, "press.machine_state", derive_value=2),   # laminator.running
```

Change to:
```python
CoilDefinition(4, "laminator.web_speed", mode="gt_zero"),    # laminator.running
```

This is consistent with Coil 5 (`slitter.speed`, `mode="gt_zero"`) and derives the laminator's running state from its own speed signal rather than the press state.

**Check:** The signal store key is `laminator.web_speed` (with equipment prefix). Verify this matches the laminator generator's output signal ID.

### OPC-UA MinimumSamplingInterval (Task 6c.8)

PRD Appendix B requires `MinimumSamplingInterval` on all variable nodes. asyncua defaults to 0.

**Signal sample rate priority:**
1. `sig_cfg.sample_rate_ms` if set (per-signal override)
2. `self._config.simulation.tick_interval_ms` (default 100ms)

**asyncua API:** `MinimumSamplingInterval` is an OPC-UA node attribute (not a property like EURange). Use:
```python
await var_node.write_attribute(
    ua.AttributeIds.MinimumSamplingInterval,
    ua.DataValue(ua.Variant(min_sampling_ms, ua.VariantType.Double)),
)
```

If `write_attribute` doesn't work on the server side, try the internal node API. Research the asyncua source if needed.

## STOPPING RULES

**After completing ONE task:** Output `TASK_COMPLETE` and stop immediately.
Do not look for the next task. Do not start another task.

**If a test cannot pass after 3 genuine attempts:** STOP. Document the issue in `plans/phase-6c-progress.md`. Output `TASK_BLOCKED: <reason>` and stop.

**Dependency check:** If the first `"passes": false` task has unsatisfied dependencies, find the next task whose dependencies are all satisfied. If NO tasks are eligible, output `PHASE_BLOCKED: waiting on <task IDs>` and stop.

## COMPLETION

When ALL tasks in the task JSON have `"passes": true`:
1. Push all commits.
2. Output: PHASE_COMPLETE
