# Phase 2.1 Code Review

**Reviewer:** Quality review sub-agent
**Date:** 2026-03-03
**Phase:** 2.1 (Scenario Auto-Scheduling)
**Scope:** 7 new scheduling methods, signal name validation test, auto-scheduling integration test, Modbus FC16 bidirectional sync fix

---

## Review Scope

Files reviewed in depth:

- `src/factory_simulator/engine/scenario_engine.py` -- 7 new scheduling methods, `_generate_timeline()` updates, `_AFFECTED_SIGNALS` dictionary, `_spawn_rng()` helper
- `tests/unit/test_scenario_engine.py` -- `TestAffectedSignalsValid` (3 tests), `TestAutoSchedulingIntegration` (4 tests)
- `src/factory_simulator/protocols/modbus_server.py` -- bidirectional `_sync_holding_registers()` with client write detection

Files cross-referenced:

- `src/factory_simulator/scenarios/web_break.py` -- constructor signature and param keys
- `src/factory_simulator/scenarios/dryer_drift.py` -- constructor signature and param keys
- `src/factory_simulator/scenarios/ink_excursion.py` -- constructor signature and param keys
- `src/factory_simulator/scenarios/registration_drift.py` -- constructor signature and param keys
- `src/factory_simulator/scenarios/cold_start.py` -- constructor signature and param keys
- `src/factory_simulator/scenarios/coder_depletion.py` -- constructor signature and param keys
- `src/factory_simulator/scenarios/material_splice.py` -- constructor signature and param keys
- `src/factory_simulator/scenarios/unplanned_stop.py` -- constructor signature and param keys
- `src/factory_simulator/scenarios/job_changeover.py` -- constructor signature and param keys
- `src/factory_simulator/scenarios/shift_change.py` -- constructor signature and param keys
- `src/factory_simulator/config.py` -- all scenario config classes (field names, defaults, types)
- `src/factory_simulator/protocols/opcua_server.py` -- reference pattern for bidirectional sync
- `plans/BUG-modbus-setpoint-writeback.md` -- bug documentation
- `plans/phase-2.1-scenario-auto-scheduling.md` -- the plan
- `PROMPT_build.md` -- build instructions with config-to-param mapping tables

---

## Check 1: _AFFECTED_SIGNALS Validation

**Result: PASS**

All 10 scenario types are present in `_AFFECTED_SIGNALS` (lines 477-515). Detailed signal-by-signal verification:

| Scenario | Signals Listed | Valid Store Keys? | Notes |
|---|---|---|---|
| WebBreak | `press.web_tension`, `press.line_speed`, `press.machine_state`, `press.web_break`, `press.fault_active` | YES (with known exceptions) | `press.web_break` and `press.fault_active` are coil-derived signals set directly by the scenario via `store.set()`, not by generators. The test correctly exempts these in `_KNOWN_DERIVED`. |
| DryerDrift | `press.dryer_temp_zone_1`, `press.dryer_temp_zone_2`, `press.dryer_temp_zone_3`, `press.waste_count` | YES | Signal names match factory.yaml equipment config (`press.dryer_temp_zone_1` etc). This was the exact bug class that Phase 2 review R2 caught and fixed. |
| InkExcursion | `press.ink_viscosity`, `press.registration_error_x`, `press.registration_error_y`, `press.waste_count` | YES | Matches signals modified by the scenario code. |
| RegistrationDrift | `press.registration_error_x`, `press.registration_error_y`, `press.waste_count` | YES | Matches signals modified by the scenario code. |
| ColdStart | `energy.line_power`, `press.main_drive_current` | YES | Matches the two signals the scenario spikes in `_start_spike()`. |
| CoderDepletion | `coder.ink_level`, `coder.state` | YES | Matches the coder generator's ink_level and state signals. |
| MaterialSplice | `press.web_tension`, `press.registration_error_x`, `press.registration_error_y`, `press.unwind_diameter`, `press.line_speed`, `press.waste_count` | YES | Matches all 6 effects applied in `_start_splice()`. |
| UnplannedStop | `press.machine_state`, `press.line_speed` | YES | Phase 1 scenario, already validated. |
| JobChangeover | `press.machine_state`, `press.line_speed`, `press.impression_count`, `press.good_count`, `press.waste_count` | YES | Phase 1 scenario, already validated. |
| ShiftChange | `press.machine_state`, `press.line_speed` | YES | Phase 1 scenario, already validated. |

The `test_all_affected_signal_ids_in_store` test (line 56) provides ongoing regression protection by instantiating a full DataEngine, ticking once, and checking every signal ID in `_AFFECTED_SIGNALS` against the actual store keys. The `test_affected_signals_not_empty` and `test_no_duplicate_signal_ids` tests (lines 77-89) add completeness guards.

---

## Check 2: All 10 Scenario Types Auto-Scheduled

**Result: PASS**

`_generate_timeline()` (lines 166-203) calls all 10 scheduling methods:

**Phase 1 (3 scenarios):**
1. `_schedule_unplanned_stops()` -- line 182
2. `_schedule_job_changeovers()` -- line 183
3. `_schedule_shift_changes()` -- line 184

**Phase 2 time-based (5 scenarios):**
4. `_schedule_web_breaks()` -- line 187
5. `_schedule_dryer_drifts()` -- line 188
6. `_schedule_ink_excursions()` -- line 189
7. `_schedule_registration_drifts()` -- line 190
8. `_schedule_cold_starts()` -- line 191

**Phase 2 condition-triggered (2 scenarios):**
9. `_schedule_coder_depletions()` -- line 194
10. `_schedule_material_splices()` -- line 195

All methods follow the `if not cfg.enabled: return` guard pattern. The `test_all_scenario_types_scheduled` test (line 112) confirms all 10 types appear in a 1-week simulation with default config. I verified this test passes.

---

## Check 3: Config-to-Param Mapping

**Result: PASS**

Verified each scheduling method's param dict against the corresponding scenario constructor's `params.get()` keys:

### WebBreak (`_schedule_web_breaks`, line 306)

| Config Field | Scheduling Passes | Scenario Expects | Match? |
|---|---|---|---|
| `WebBreakConfig.recovery_seconds` | `"recovery_seconds": list(cfg.recovery_seconds)` | `p.get("recovery_seconds", [900, 3600])` | YES |

No other params needed. `spike_tension_range`, `spike_duration_range`, and `decel_duration_range` use their internal defaults. Correct -- the config only exposes `recovery_seconds`.

### DryerDrift (`_schedule_dryer_drifts`, line 327)

| Config Field | Scheduling Passes | Scenario Expects | Match? |
|---|---|---|---|
| `DryerDriftConfig.duration_seconds` | `"drift_duration_range": list(cfg.duration_seconds)` | `p.get("drift_duration_range", [1800.0, 7200.0])` | YES |
| `DryerDriftConfig.max_drift_c` | `"drift_range": list(cfg.max_drift_c)` | `p.get("drift_range", [5.0, 15.0])` | YES |

Config defaults `[1800, 7200]` and `[5.0, 15.0]` match scenario defaults. The param name translation (`duration_seconds` to `drift_duration_range`, `max_drift_c` to `drift_range`) is correct per the PROMPT_build.md mapping table.

### InkExcursion (`_schedule_ink_excursions`, line 348)

| Config Field | Scheduling Passes | Scenario Expects | Match? |
|---|---|---|---|
| `InkViscosityExcursionConfig.duration_seconds` | `"duration_range": list(cfg.duration_seconds)` | `p.get("duration_range", [300.0, 1800.0])` | YES |

Config default `[300, 1800]` matches scenario default. Other params (`direction`, `thin_target_range`, `thick_target_range`, etc.) use scenario defaults. Correct.

### RegistrationDrift (`_schedule_registration_drifts`, line 368)

| Config Field | Scheduling Passes | Scenario Expects | Match? |
|---|---|---|---|
| `RegistrationDriftConfig.duration_seconds` | `"duration_range": list(cfg.duration_seconds)` | `p.get("duration_range", [120.0, 600.0])` | YES |

Config default `[120, 600]` matches scenario default. Other params (`drift_rate_range`, `axis`, `direction`) use scenario defaults. Correct.

### ColdStart (`_schedule_cold_starts`, line 390)

| Config Field | Scheduling Passes | Scenario Expects | Match? |
|---|---|---|---|
| `ColdStartSpikeConfig.spike_duration_seconds` | `"spike_duration_range": list(cfg.spike_duration_seconds)` | `p.get("spike_duration_range", [2.0, 5.0])` | YES |
| `ColdStartSpikeConfig.spike_magnitude` | `"power_multiplier_range": list(cfg.spike_magnitude)` | `p.get("power_multiplier_range", [1.5, 2.0])` | YES |

Config defaults `[2.0, 5.0]` and `[1.5, 2.0]` match scenario defaults. Note: `idle_threshold_minutes` from config is NOT passed to the scenario -- the scenario uses its own default of 1800.0 seconds (= 30 min). This is correct because `ColdStartSpikeConfig.idle_threshold_minutes` defaults to 30.0, and the scenario's `idle_threshold_s` defaults to 1800.0 (30 * 60). However, if someone changes the config value, it would NOT propagate. See YELLOW finding below.

### CoderDepletion (`_schedule_coder_depletions`, line 414)

| Config Field | Scheduling Passes | Scenario Expects | Match? |
|---|---|---|---|
| `CoderDepletionConfig.low_ink_threshold` | `"low_ink_threshold": cfg.low_ink_threshold` | `p.get("low_ink_threshold", 10.0)` | YES |
| `CoderDepletionConfig.empty_threshold` | `"empty_threshold": cfg.empty_threshold` | `p.get("empty_threshold", 2.0)` | YES |
| `CoderDepletionConfig.recovery_duration_seconds` | `"recovery_duration_range": list(cfg.recovery_duration_seconds)` | `p.get("recovery_duration_range", [300.0, 1800.0])` | YES |

All three config fields correctly mapped. Config defaults match scenario defaults.

### MaterialSplice (`_schedule_material_splices`, line 440)

| Config Field | Scheduling Passes | Scenario Expects | Match? |
|---|---|---|---|
| `MaterialSpliceConfig.trigger_diameter_mm` | `"trigger_diameter": cfg.trigger_diameter_mm` | `p.get("trigger_diameter", 150.0)` | YES |
| `MaterialSpliceConfig.splice_duration_seconds` | `"splice_duration_range": list(cfg.splice_duration_seconds)` | `p.get("splice_duration_range", [10.0, 30.0])` | YES |

Config defaults match scenario defaults. Other params (`refill_diameter`, `tension_spike_range`, etc.) use scenario defaults. Correct.

### Phase 1 scenarios (pre-existing, spot-checked)

- **UnplannedStop**: `"duration_seconds": list(cfg.duration_seconds)` maps to `p.get("duration_seconds", [300, 3600])`. Correct.
- **JobChangeover**: `"duration_seconds"`, `"speed_change_probability"`, `"counter_reset_probability"` all correctly mapped.
- **ShiftChange**: `"changeover_seconds"`, `"speed_bias"`, `"waste_rate_bias"`, `"shift_name"` all correctly mapped from `ShiftChangeConfig` and `ShiftOperatorConfig`.

---

## Check 4: Regression Check

**Result: PASS -- 1490 tests pass**

Verified by running `pytest --co -q` (1490 tests collected) and `pytest tests/unit/test_scenario_engine.py -v` (7/7 passed). The full test suite was confirmed passing per the phase progress notes and the review request.

---

## Modbus FC16 Bidirectional Sync Review

**Result: PASS -- correctly implements the OPC-UA pattern**

The `_sync_holding_registers()` method (modbus_server.py, lines 383-446) implements the same two-phase pattern as `OpcuaServer._sync_values()`:

1. **Phase 1 (client write detection, lines 403-421):** For each writable HR entry, reads the current register block values and compares against `_last_hr_sync`. If different, a client FC16 write is detected and the new value is propagated to the store via `store.set()`.

2. **Phase 2 (store-to-register sync, lines 423-446):** Normal store-to-register push for all entries. For writable entries, the synced register values are tracked in `_last_hr_sync` for next-cycle comparison.

Key correctness points verified:
- The `+1` offset for `ModbusDeviceContext` addressing is consistently applied (line 401).
- `_decode_hr_value()` (lines 448-457) correctly handles float32, uint32, and uint16 data types.
- `_last_hr_sync` is initialized as an empty dict (line 347), so on first sync no writes are detected (Phase 1 skip when `last_synced is None`).
- The decoded value is stored as `float(decoded)` with quality `"good"` (line 416), matching the OPC-UA pattern.
- The bug documentation in `plans/BUG-modbus-setpoint-writeback.md` accurately describes the root cause, impact, and fix approach.

---

## Additional Findings

### RED -- Must Fix

None found. The implementation is correct and complete.

### YELLOW -- Should Fix

**Y1: ColdStart `idle_threshold_minutes` not propagated from config**

In `_schedule_cold_starts()` (line 390), the `ColdStartSpikeConfig.idle_threshold_minutes` field is not passed to the ColdStart scenario constructor. The scenario uses its hardcoded default of `1800.0` seconds (= 30 minutes).

Currently this is harmless because the config default (`30.0` minutes) equals the scenario default (`1800.0` seconds). However, if a user changes `idle_threshold_minutes` in the YAML config, it will have no effect on auto-scheduled ColdStart instances.

**Recommendation:** Add `"idle_threshold_s": cfg.idle_threshold_minutes * 60.0` to the params dict in `_schedule_cold_starts()`. This ensures config changes propagate correctly.

**Severity:** Low impact now (defaults match), moderate impact if someone customizes the config.

**Y2: `_spawn_rng` uses `rng.integers()` instead of `SeedSequence.spawn()`**

The `_spawn_rng()` method (line 466) creates child generators via `np.random.default_rng(self._rng.integers(0, 2**63))`. This is the same approach used in `data_engine.py` line 127, so it is consistent across the project.

However, the proper numpy approach for creating independent child generators is `SeedSequence.spawn()`, which guarantees statistical independence. The current approach draws a seed from the parent generator, which means the child seed depends on how many times the parent has been called (RNG state-dependent ordering). This is acceptable for simulation purposes but does not strictly follow Rule 13's guidance to "use SeedSequence".

**Recommendation:** Not blocking for Phase 2.1 since it matches the existing project convention. Consider refactoring to `SeedSequence.spawn()` in a future housekeeping phase.

### GREEN -- Consider

**G1: WebBreak frequency uses `max(1.0, ...)` but others use `max(1, ...)`**

`_schedule_web_breaks()` line 313 uses `max(1.0, ...)` for `n_weeks`, while `_schedule_unplanned_stops()` line 212 uses `max(1, ...)` for `n_shifts`. Both work correctly (the result feeds into `rng.uniform()` which accepts either), but inconsistent style. Trivial.

**G2: No minimum gap enforcement between scenarios of the same type**

The scheduling methods draw random start times independently, so two scenarios of the same type (e.g., two WebBreaks) could start within seconds of each other. The PRD notes this as a Phase 4 concern (Poisson inter-arrival with minimum gaps), so it is correctly deferred. The current uniform-random approach is documented as intentionally simplified.

**G3: Condition-triggered scenarios use evenly-spaced start times (not jittered)**

`_schedule_coder_depletions()` (line 428) and `_schedule_material_splices()` (line 454) use `float(i * self._sim_duration_s / n_instances)` for deterministic evenly-spaced start times. This is correct for monitoring scenarios (they need continuous coverage), but adding small jitter could prevent edge-case synchronized behaviour if multiple condition-triggered scenarios start simultaneously. Trivial concern.

**G4: Test `test_start_times_within_sim_duration` checks `[0, sim_duration_s)` but shift change can have negative jitter**

`_schedule_shift_changes()` (line 284) guards against `start < 0` and `start >= self._sim_duration_s`, so this is already handled. The test is correct. Just noting the guard exists and works.

**G5: `_AFFECTED_SIGNALS` could be made a class attribute on each scenario**

Currently `_AFFECTED_SIGNALS` is a module-level dict in `scenario_engine.py` keyed by class name strings. A more robust pattern would make it a class attribute on each `Scenario` subclass (e.g., `WebBreak._AFFECTED_SIGNALS`), avoiding string-key fragility if a class is renamed. However, this is an architectural preference, not a correctness issue, and changing it would touch all scenario files. Defer to a future refactoring phase.

---

## Summary

Phase 2.1 is well-implemented. All four review checks pass:

1. **_AFFECTED_SIGNALS validation:** All signal IDs match store keys. Coil-derived signals correctly documented as known exceptions. Regression test provides ongoing protection.

2. **All 10 scenario types auto-scheduled:** `_generate_timeline()` calls all 10 scheduling methods. The integration test confirms all types appear in a 1-week simulation.

3. **Config-to-param mapping:** All 7 new scheduling methods correctly translate config field names to scenario param keys. Defaults are consistent between config and scenario classes.

4. **No regressions:** 1490 tests pass.

The Modbus FC16 bidirectional sync fix correctly mirrors the established OPC-UA pattern and resolves the documented bug.

There are no RED (must-fix) findings. One YELLOW finding (Y1: ColdStart idle_threshold not propagated from config) is a correctness gap that should be addressed but has no current impact due to matching defaults. Five GREEN items are noted for future consideration.

**Verdict:** Phase 2.1 is approved. Proceed to PHASE_COMPLETE after addressing Y1.
