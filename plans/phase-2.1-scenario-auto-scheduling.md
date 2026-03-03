# Phase 2.1: Scenario Auto-Scheduling

**Goal:** All 7 Phase 2 scenarios auto-schedule in `_generate_timeline()` so a default simulator run produces scenario data without manual `add_scenario()` calls.

**Why:** The Phase 2 acceptance test requires scenario evidence at Medium and Full tiers. Without auto-scheduling, Phase 2 scenarios never fire in a default run. The acceptance test cannot pass.

**Scope:** Simple frequency-based scheduling using the same uniform-random pattern as existing Phase 1 scenarios. This is NOT the full Poisson inter-arrival engine with priority rules and minimum gaps (that remains Phase 4 per PRD Appendix F).

**Approach:** Two categories of Phase 2 scenarios:

1. **Time-scheduled** (5 scenarios): WebBreak, DryerDrift, InkExcursion, RegistrationDrift, ColdStart. These have PRD-defined frequencies. Schedule N instances with random start times across the simulation duration. Same pattern as `_schedule_unplanned_stops()`.

2. **Condition-triggered** (2 scenarios): CoderDepletion and MaterialSplice. These monitor model values (ink level, unwind diameter) and fire when thresholds are crossed. They need a single long-lived monitoring instance, not discrete start times. Schedule one instance at t=0 that monitors continuously.

---

## Tasks

### Task 2.1.1: Schedule time-based Phase 2 scenarios

**Files:** `src/factory_simulator/engine/scenario_engine.py`

Add 5 new scheduling methods to `ScenarioEngine`:

**`_schedule_web_breaks()`**
- Config: `self._config.web_break`
- Frequency: `frequency_per_week` (default [1, 2])
- Convert to simulation duration: `n_weeks = sim_duration_s / (7 * 86400)`
- Draw count: `round(rng.uniform(min_f, max_f) * n_weeks)`
- For short sims (< 1 week): still draw at least 0-1 using the fractional week
- Params: `recovery_seconds` from config
- Import: `WebBreak` from `factory_simulator.scenarios.web_break`

**`_schedule_dryer_drifts()`**
- Config: `self._config.dryer_drift`
- Frequency: `frequency_per_shift` (default [1, 2])
- Same n_shifts pattern as unplanned_stops
- Params: `drift_duration_range` from `duration_seconds`, `drift_range` from `max_drift_c`
- Import: `DryerDrift` from `factory_simulator.scenarios.dryer_drift`

**`_schedule_ink_excursions()`**
- Config: `self._config.ink_viscosity_excursion`
- Frequency: `frequency_per_shift` (default [2, 3])
- Same n_shifts pattern
- Params: `duration_range` from `duration_seconds`
- Import: `InkExcursion` from `factory_simulator.scenarios.ink_excursion`

**`_schedule_registration_drifts()`**
- Config: `self._config.registration_drift`
- Frequency: `frequency_per_shift` (default [1, 3])
- Same n_shifts pattern
- Params: `duration_range` from `duration_seconds`
- Import: `RegistrationDrift` from `factory_simulator.scenarios.registration_drift`

**`_schedule_cold_starts()`**
- Config: `self._config.cold_start_spike`
- ColdStart is reactive (monitors press state for idle-to-active transitions), not time-triggered
- Schedule N monitoring instances spread across the simulation
- Each ColdStart enters MONITORING phase and watches for ONE trigger, then completes
- Frequency: 1-2 per day means we need 1-2 monitoring instances per day
- `n_days = max(1, sim_duration_s / 86400)`
- Draw count: `round(rng.uniform(1, 2) * n_days)`
- Start times: spread uniformly but biased toward shift-change times (most cold starts happen after a shift change idle period)
- Simpler approach: uniform random start times, same as other scenarios. The ColdStart will monitor from its start_time onward and trigger on the first qualifying idle-to-active transition it sees.
- Params: `spike_duration_seconds`, `spike_magnitude` from config

**All methods:** follow the `if not cfg.enabled: return` guard pattern.

**Update `_generate_timeline()`:** Call all 5 new methods. Update the docstring to reflect Phase 2 scenarios are now auto-scheduled.

### Task 2.1.2: Schedule condition-triggered Phase 2 scenarios

**Files:** `src/factory_simulator/engine/scenario_engine.py`

**`_schedule_coder_depletions()`**
- Config: `self._config.coder_depletion`
- CoderDepletion monitors ink level continuously; fires when level drops below threshold
- Schedule ONE instance at `start_time=0.0` that monitors from the start
- It will trigger when ink naturally depletes to the threshold
- After completion (ink refilled), the scenario is done. For long sims, we may want multiple instances.
- For sims > 24h: schedule additional instances at 24h intervals (typical refill cycle)
- Simpler: schedule `max(1, round(sim_duration_s / 86400))` instances at evenly spaced start times. Each monitors for one depletion-refill cycle.
- Params: `low_ink_threshold`, `empty_threshold`, `recovery_duration_seconds` from config

**`_schedule_material_splices()`**
- Config: `self._config.material_splice`
- MaterialSplice monitors unwind_diameter; fires when diameter drops below trigger threshold
- Same continuous-monitoring pattern as CoderDepletion
- A reel lasts 2-4 hours. At 10x speed, that is 12-24 min wall clock.
- Schedule `max(1, round(sim_duration_s / (3 * 3600)))` instances (one per ~3 hours of sim time)
- Start times: evenly spaced with small jitter, or uniform random
- Params: `trigger_diameter_mm`, `splice_duration_seconds` from config

### Task 2.1.3: Signal name validation test

**Files:** `tests/unit/test_scenario_engine.py` (new or existing)

Add a test that verifies every signal ID in `_AFFECTED_SIGNALS` exists as a valid signal key in the store after engine initialisation. This catches the DryerDrift naming bug class permanently.

- Create a minimal DataEngine with packaging config
- Tick once to populate the store
- For each scenario type in `_AFFECTED_SIGNALS`, assert every signal ID matches a key in `store.signals()` or `store.get(sig_id) is not None`
- Exception: `press.web_break` and `press.fault_active` are coil-derived signals that may not have store entries. Document these as known exceptions if needed.

### Task 2.1.4: Auto-scheduling integration test

**Files:** `tests/unit/test_scenario_engine.py` (new or existing)

Test that `ScenarioEngine.__init__()` with default config and a multi-day `sim_duration_s` produces instances of all 10 scenario types (3 Phase 1 + 7 Phase 2).

- Create ScenarioEngine with `sim_duration_s = 7 * 86400` (one week) and all scenarios enabled
- Check `engine.scenarios` contains at least one instance of each type:
  - UnplannedStop, JobChangeover, ShiftChange (Phase 1)
  - WebBreak, DryerDrift, InkExcursion, RegistrationDrift, ColdStart, CoderDepletion, MaterialSplice (Phase 2)
- Check that scenario count is reasonable (not 0, not 10000)
- Check scenarios are sorted by start_time

### Task 2.1.5: Update acceptance test procedure

**Files:** `plans/phase-2-acceptance-test.md`

Update the "Scenario Evidence" section to list the specific Phase 2 scenarios expected at each tier:

- **Medium (1 sim hour at 10x):** At least one state transition, at least one job changeover. DryerDrift and InkExcursion may fire (1-3 per shift frequency). WebBreak unlikely (1-2 per week). ColdStart possible if shift change causes idle period.
- **Full (24 sim hours at 10x):** All Phase 1 scenarios fired. DryerDrift, InkExcursion, RegistrationDrift multiple times. At least one WebBreak. At least one CoderDepletion cycle. Multiple MaterialSplices. ColdStart triggered at least once.

Update the verification script expectations accordingly.

### Task 2.1.6: Update docstring and progress file

**Files:**
- `src/factory_simulator/engine/scenario_engine.py` — Update module docstring and `_generate_timeline()` docstring
- `plans/phase-2.1-progress.md` — Create progress tracking file

---

## Implementation Notes

**Import changes in scenario_engine.py:** Tasks 2.1.1 and 2.1.2 add imports for all 7 Phase 2 scenario classes. Currently only Phase 1 classes are imported at module level. Add the Phase 2 imports alongside them (not inside TYPE_CHECKING, since they are instantiated at runtime).

**Config field mapping:** The config field names do not always match scenario param names. Map carefully:
- `DryerDriftConfig.duration_seconds` maps to scenario param `drift_duration_range`
- `DryerDriftConfig.max_drift_c` maps to scenario param `drift_range`
- `InkViscosityExcursionConfig.duration_seconds` maps to scenario param `duration_range`
- `RegistrationDriftConfig.duration_seconds` maps to scenario param `duration_range`
- `WebBreakConfig.recovery_seconds` maps to scenario param `recovery_seconds`
- `ColdStartSpikeConfig.spike_duration_seconds` maps to scenario param `spike_duration_range`
- `ColdStartSpikeConfig.spike_magnitude` maps to scenario param `power_multiplier_range`
- `MaterialSpliceConfig.trigger_diameter_mm` maps to scenario param `trigger_diameter`
- `MaterialSpliceConfig.splice_duration_seconds` maps to scenario param `splice_duration_range`
- `CoderDepletionConfig.low_ink_threshold`, `empty_threshold`, `recovery_duration_seconds` map directly

**Estimated scope:**
- scenario_engine.py: ~120-180 new lines (7 scheduling methods + imports + timeline update)
- Tests: ~80-120 new lines (signal validation + auto-scheduling integration)
- Acceptance test: ~20 lines updated
- Total: ~220-320 lines of changes

**Risk:** Low. The scheduling pattern is proven (3 existing methods work). The scenarios are proven (1483 tests pass). This is plumbing.

---

## Exit Criteria

1. `ScenarioEngine._generate_timeline()` schedules instances of all 10 scenario types when all are enabled.
2. Signal name validation test passes (all `_AFFECTED_SIGNALS` entries match store keys).
3. Auto-scheduling integration test passes (all 10 types present in a 1-week sim).
4. All existing 1483 tests still pass (no regressions).
5. `ruff check`, `mypy` clean.
6. Acceptance test procedure updated for Phase 2 scenario expectations.
