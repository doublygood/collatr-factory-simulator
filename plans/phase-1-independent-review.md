# Phase 1: Independent Review

**Reviewer:** Independent Reviewer (fresh context)
**Date:** 2026-03-02

## Overall Assessment

Phase 1 is a solid, well-structured implementation. The 12 signal models faithfully reproduce the PRD Section 4.2 formulas with only one known discrepancy (thermal diffusion `4L²` — correctly handled). The noise pipeline, Cholesky correlation, and engine architecture all follow the specification. The codebase is clean, consistently structured, and well-documented. All 47 packaging signals are generated, the Modbus register map matches Appendix A, and CLAUDE.md rules are followed with only minor exceptions.

However, the review uncovered several issues the internal review either missed or under-reported. Most significant: the vibration generator uses a uniform correlation matrix (`r=0.6` for all pairs) instead of the PRD-specified asymmetric matrix; the YAML config is missing PRD-mandated noise distribution assignments for key signal categories (vibration should be Student-t, motor current should be Student-t, ink pressure should be Student-t, PID-controlled laminator temps should be AR(1)); and the environment generator omits the composite model layers (HVAC cycling + random perturbations) described in PRD 4.2.2. The energy cumulative_kwh calculation has a subtle dimensional inconsistency. These are not individually blocking but collectively represent a gap between the implementation and the PRD's stated data realism goals.

## Internal Review Quality

**Grade:** B+

The internal review was thorough in its methodology (4 parallel sub-agent reviewers with focused domains) and caught the three most critical issues: R1 (fault_code missing from YAML), R2 (thermal diffusion `4L²` discrepancy), and R3 (NaN propagation through clamp). All three were correctly assessed and the fixes applied were correct.

However, the review missed several significant issues:

1. **Vibration correlation matrix mismatch** — The PRD specifies `[[1.0, 0.2, 0.15], [0.2, 1.0, 0.2], [0.15, 0.2, 1.0]]` but the implementation uses a uniform `r=0.6` for all pairs. This is a concrete formula mismatch.
2. **Missing noise distribution assignments** — The PRD's default noise table (Section 4.2.11) is a specification, not just a suggestion. Vibration should be Student-t(df=5), motor current Student-t(df=8), ink pressure Student-t(df=6), PID temperatures AR(1)(phi=0.7). The YAML only sets AR(1) for press dryer temps — not for laminator temps, printhead temp, or any of the Student-t signals.
3. **Environment composite model** — The PRD 4.2.2 specifies a 3-layer composite (daily sine + HVAC bang-bang + random perturbations). The implementation uses a plain sinusoidal model. This was not even mentioned.
4. **Speed-dependent sigma not configured** — The PRD specifies `sigma_base` and `sigma_scale` for vibration, web tension, registration error, and motor current. None are configured in the YAML.
5. **Energy cumulative_kwh dimensional concern** — The energy generator sets `speed = power` into a CounterModel with `rate = 0.001`, giving `kwh += 0.001 * power * dt`. The correct formula should be `kwh += power * dt / 3600`, which at `rate=1.0` would give `speed = power/3600`. With the current `rate=0.001`, the accumulation is `0.001 * power * dt`, which at dt=0.1s and power=100kW gives 0.01 kWh/tick. The correct rate should be `100 * 0.1 / 3600 = 0.00278 kWh/tick`. The `rate=0.001` is effectively undercounting by ~2.8x.

The internal review's YELLOW findings (Y1-Y10) were mostly accurate characterizations of acceptable simplifications. The waste counter rate on Y9 (scenarios accessing private attributes) is a valid design concern but acceptable in Phase 1.

## RED Findings (Must Fix before Phase 2)

### R1: Vibration correlation matrix does not match PRD 4.3.1

**File:** `src/factory_simulator/generators/vibration.py`, lines 55-59
**PRD reference:** Section 4.3.1 — Vibration axes correlation matrix

**PRD specifies:**
```
R = [[1.0,  0.2,  0.15],
     [0.2,  1.0,  0.2 ],
     [0.15, 0.2,  1.0 ]]
```

**Implementation uses:**
```python
r = self._correlation  # 0.6
corr_matrix = np.array([
    [1.0, r, r],
    [r, 1.0, r],
    [r, r, 1.0],
])
```

This produces `[[1, 0.6, 0.6], [0.6, 1, 0.6], [0.6, 1, 0.6]]` — a uniform correlation at 3x the PRD-specified values and lacking the asymmetry between X-Z (0.15) and X-Y/Y-Z (0.2). The PRD matrix reflects real mechanical coupling (X-Z has minimal direct coupling); the uniform 0.6 is physically unrealistic.

**Impact:** Vibration axis correlation structure is wrong. Any downstream analysis comparing axis correlations will see much stronger and more uniform coupling than the PRD intends.

**Fix:** Replace the uniform correlation with the PRD's asymmetric matrix. Read it from config or hardcode the PRD values. Keep the configurable fallback for non-default profiles.

### R2: Noise distribution assignments missing from YAML config

**File:** `config/factory.yaml`
**PRD reference:** Section 4.2.11 — Default noise distribution assignments table

The PRD specifies default noise distributions per signal category. Only 3 of ~15 required assignments are configured:

| Signal | PRD Requirement | YAML Config | Status |
|--------|----------------|-------------|--------|
| `vibration.main_drive_x/y/z` | Student-t, df=5 | gaussian (default) | ❌ MISSING |
| `press.main_drive_current` | Student-t, df=8 | gaussian (default) | ❌ MISSING |
| `coder.ink_pressure` | Student-t, df=6 | gaussian (default) | ❌ MISSING |
| `press.dryer_temp_zone_1/2/3` | AR(1), phi=0.7 | AR(1), phi=0.7 | ✅ |
| `laminator.nip_temp` | AR(1), phi=0.7 | gaussian (default) | ❌ MISSING |
| `laminator.tunnel_temp` | AR(1), phi=0.7 | gaussian (default) | ❌ MISSING |
| `coder.printhead_temp` | AR(1), phi=0.7 | gaussian (default) | ❌ MISSING |

**Impact:** The noise pipeline code correctly supports all three distributions, but the YAML config doesn't activate them for the right signals. The data will look "too Gaussian" — vibration won't have the heavy-tail outliers, PID temperatures won't have autocorrelated residuals. This undermines the PRD's stated goal (Section 4.1 Principle 3): "Clean signals look fake."

**Fix:** Add `noise_type`, `noise_df`, and `noise_phi` to the relevant signal configs in `factory.yaml`.

### R3: Speed-dependent sigma not configured for any signal

**File:** `config/factory.yaml`
**PRD reference:** Section 4.2.11 — Speed-dependent sigma table

The PRD specifies `sigma_base` and `sigma_scale` for 4 signal groups:

| Signal | sigma_base | sigma_scale | Parent |
|--------|-----------|-------------|--------|
| vibration.main_drive_x/y/z | 0.2 mm/s | 0.015 mm/s per m/min | press.line_speed |
| press.web_tension | 2.0 N | 0.02 N per m/min | press.line_speed |
| press.registration_error_x/y | 0.005 mm | 0.00005 mm per m/min | press.line_speed |
| press.main_drive_current | 0.3 A | 0.002 A per m/min | press.line_speed |

None of these are configured in the YAML. The code supports speed-dependent sigma (NoiseGenerator accepts `sigma_base` and `sigma_scale`), but the config doesn't use it.

**Impact:** Noise envelope is unnaturally uniform across all operating speeds. At idle, vibration has the same noise as at 200 m/min. The PRD explicitly warns: "Constant sigma produces an unnaturally uniform noise envelope."

**Fix:** Add `sigma_base`, `sigma_scale`, and `sigma_parent` to the relevant signal configs.

## YELLOW Findings (Should Fix)

### Y1: Environment generator lacks composite model (HVAC + perturbations)

**File:** `src/factory_simulator/generators/environment.py`
**PRD reference:** Section 4.2.2 — Composite environmental model

The PRD specifies three layers for `env.ambient_temp`:
1. Daily sinusoidal cycle ✅ (implemented)
2. HVAC cycling via bang-bang with 15-30 min period ❌ (not implemented)
3. Random perturbations (Poisson process, 3-8 per shift) ❌ (not implemented)

The current implementation uses a plain `SinusoidalModel`. The BangBangModel exists and could be composed, and the PRD even provides parameter defaults (`hvac_period_minutes: 20`, `hvac_amplitude_c: 1.0`, etc.).

**Impact:** Ambient temperature looks "too clean" — a perfect sine wave is immediately identifiable as synthetic, which the PRD explicitly flags. This matters for demos.

**Fix:** Compose the sinusoidal model with a bang-bang HVAC layer and random perturbation events inside the EnvironmentGenerator.

### Y2: Energy cumulative_kwh accumulation may have dimensional inconsistency

**File:** `src/factory_simulator/generators/energy.py`, lines 98-103
**PRD reference:** Section 2.8 — Energy Monitoring

The energy generator computes `cumulative_kwh` by setting `speed = power` (in kW) into a CounterModel with `rate = 0.001`. This gives:

```
kwh_per_tick = rate * power * dt = 0.001 * power * dt
```

For 100 kW at dt=0.1s: `0.001 * 100 * 0.1 = 0.01 kWh/tick`.

The correct formula is `kWh = kW × hours`, so per tick: `kWh = power × dt / 3600`.
At 100 kW, dt=0.1s: `100 × 0.1 / 3600 = 0.00278 kWh/tick`.

The configured `rate=0.001` produces `0.01` instead of `0.00278` — it overcounts by 3.6×. The `rate` should be `1/3600 ≈ 0.000278` to be dimensionally correct.

**Impact:** Cumulative energy values will be approximately 3.6x too high. For demos and integration testing this may not matter, but for evaluation datasets (batch mode) the energy values will be unrealistic.

**Fix:** Change the `cumulative_kwh` rate in factory.yaml to `0.000278` (= 1/3600), or adjust the EnergyGenerator to divide by 3600 before passing speed to the counter.

### Y3: Vibration generator doesn't apply speed-dependent noise correctly

**File:** `src/factory_simulator/generators/vibration.py`, lines 87-98

When running, the vibration generator generates correlated noise but scales by `noise_gen.sigma` (the fixed base sigma), not by `noise_gen.effective_sigma(parent_value)`. Even if speed-dependent sigma were configured, the vibration generator wouldn't use it because it bypasses the NoiseGenerator's `sample()` method and directly uses `sigma` to scale the correlated Cholesky output.

**Impact:** Even if R3 (speed-dependent sigma config) is fixed, vibration noise won't scale with speed until this code path is also updated.

**Fix:** Use `noise_gen.effective_sigma(press_speed)` instead of `noise_gen.sigma` in the Cholesky scaling.

### Y4: Vibration generator uses steady_state model + external Cholesky, creating dual noise

**File:** `src/factory_simulator/generators/vibration.py`, lines 84-97

The vibration generator calls `self._models[name].generate(sim_time, dt)` which returns `target + noise` (if noise is configured in the SteadyStateModel), then adds *another* layer of Cholesky-correlated noise. If the SteadyStateModel has a NoiseGenerator, the signal gets both independent noise from the model AND correlated noise from the Cholesky layer. This double-noising is likely unintentional.

The PRD 4.3.1 pipeline is: generate N(0,1) → apply L → scale by sigma. The noise should come entirely from the Cholesky pipeline, not from both the model and external correlation.

**Impact:** Vibration noise variance will be higher than intended (roughly doubled). The correlation structure will be partially diluted by the independent noise component.

**Fix:** Either (a) pass `noise=None` to SteadyStateModel for vibration signals and apply all noise via Cholesky, or (b) remove the external Cholesky noise and use only the model's internal noise (losing correlation). Option (a) is correct per PRD.

### Y5: Coils 4 (laminator.running) and 5 (slitter.running) derived incorrectly

**File:** `src/factory_simulator/protocols/modbus_server.py`, lines 214-215

Both coils derive from `press.machine_state == 2`. The laminator and slitter have independent running states — the laminator follows press speed but could be running while the press is in Setup, and the slitter runs on an independent schedule.

The correct derivation should be:
- Coil 4: `laminator.web_speed > 0` (or similar active indicator)
- Coil 5: slitter is in its scheduled window

The internal review flagged this as Y3 but classified it as "Acceptable until independent state machines are added." Since the slitter already HAS an independent schedule (SlitterGenerator), the derivation is definitely wrong for coil 5.

**Impact:** Slitter coil always reports its running state based on press state, not its actual scheduled state.

**Fix:** Derive coil 5 from `slitter.speed > 0` (or a slitter running signal).

### Y6: Scenario engine timeline generation has no conflict resolution

**File:** `src/factory_simulator/engine/scenario_engine.py`

The scenario engine generates random start times for unplanned stops and job changeovers independently. Two scenarios could start at the same simulated time or overlap. The PRD Section 5.13a mentions a `minimum_gap` concept, and the Phase 4 plan specifies "minimum gap equal to scenario minimum duration" and priority rules. The current implementation just sorts by start time with no overlap checking.

**Impact:** Overlapping scenarios could both try to force the press state simultaneously, leading to unpredictable behavior. In Phase 1 this is acceptable since scenario count is low, but it should be documented.

### Y7: Job changeover does not change dryer setpoints

**File:** `src/factory_simulator/scenarios/job_changeover.py`
**PRD reference:** Section 5.2, step 5

The PRD states: "After setup duration: press.dryer_setpoint_zone_* may change (new product requires different temperature)." The job changeover scenario changes `press._target_speed` but never modifies dryer setpoints. The `speed_change_probability` controls whether speed changes, but no equivalent logic exists for setpoint changes.

**Impact:** All jobs run at the same dryer temperature. In a real factory, different substrates require different drying temperatures. This reduces scenario realism.

**Fix:** Add logic to optionally vary dryer setpoints within configured ranges during job changeover.

### Y8: Modbus sync interval is a magic number

**File:** `src/factory_simulator/protocols/modbus_server.py`, line 332

```python
await asyncio.sleep(0.05)  # 50ms update interval
```

This 50ms interval is not from any config parameter or PRD reference. Per CLAUDE.md Rule 10: "All configuration flows through Pydantic validation models. No hardcoded values that should come from config."

**Impact:** Minor. The sync interval works fine at 50ms but should be configurable.

**Fix:** Add a `sync_interval_ms` parameter to the Modbus protocol config, defaulting to 50.

### Y9: Vibration generator residual noise uses hardcoded values

**File:** `src/factory_simulator/generators/vibration.py`, lines 101-103

```python
residual = float(self._rng.normal(0.2, 0.05))
```

The `0.2` mean and `0.05` stddev for idle vibration are not from config or named constants. Per CLAUDE.md Rule 10.

**Impact:** Minor magic numbers.

**Fix:** Extract to named constants with PRD references, or make configurable.

### Y10: PressGenerator.get_signal_ids() comment says "22" but config has 22 signals

**File:** `src/factory_simulator/generators/press.py`, line 242

The docstring says "Return all 22 press signal IDs" but the PRD Section 2.2 lists 21 signals. After the R1 fix (adding `fault_code`), the press now produces 22 signals, which is correct, but should be documented as "21 PRD signals + 1 fault_code from Appendix A."

**Impact:** Documentation only.

## GREEN Findings (Suggestions)

### G1: Consider using CholeskyCorrelator class in vibration generator
The vibration generator manually computes and applies the Cholesky factor. The `CholeskyCorrelator` class in `noise.py` already does exactly this with validation. Using it would reduce code duplication and get matrix validation for free.

### G2: Press rewind_diameter uses CounterModel
`press.rewind_diameter` uses `CounterModel` which monotonically increases. This correctly models the rewind reel growing, but unlike `impression_count`, diameter doesn't reset on job change — it would keep growing past `max_clamp`. Consider adding reset logic on reel/material change.

### G3: StringGeneratorModel is not used in Phase 1
The StringGeneratorModel is implemented and tested but not used by any packaging generator (it's for F&B `mixer.batch_id`). This is fine — it's built ahead of need per the phase plan. No action required.

### G4: Config doesn't specify damping_ratio for dryer temperatures
The PRD suggests `damping_ratio ~0.6` for press dryers and `~0.7` for laminator temps. The config doesn't set `damping_ratio`, so it defaults to 1.0 (critically damped = no overshoot). The underdamped model is implemented and tested. Adding realistic damping_ratio values to the config would improve realism.

### G5: Coder gutter_fault probability discrepancy
PRD Section 2.5 says gutter fault MTBF is 500+ hours of printing time. The implementation uses `probability: 0.00001` per second, giving MTBF ≈ 100,000 seconds ≈ 27.8 hours. At 500 hours, the rate should be `1/(500×3600) = 0.000000556` per second. The current rate is ~18× too high. This should be adjusted in the coder generator's `_DEFAULT_CODER_TRANSITIONS` or made configurable.

## Signal Model Compliance Table

| Model | PRD Section | Formula Match | Edge Cases | Notes |
|---|---|---|---|---|
| steady_state | 4.2.1 | ✅ Exact | ✅ Zero target, drift clamp, calibration | Within-regime drift, calibration drift both correct |
| sinusoidal | 4.2.2 | ✅ Exact | ✅ Zero amplitude, negative center | Pure function of sim_time, no internal state |
| first_order_lag | 4.2.3 | ✅ Exact | ✅ Value at setpoint, mid-transient setpoint change | Second-order underdamped correctly implemented |
| ramp | 4.2.4 | ✅ Exact | ✅ Duration complete (holds at end), dwell compression | Step overshoot decay, dwell time compression both correct |
| random_walk | 4.2.5 | ✅ Exact | ✅ Zero drift, zero reversion, clamp bounds | Observation noise separate from walk state |
| counter | 4.2.6 | ✅ Exact | ✅ Zero speed, rollover modulo, max_before_reset | Deterministic regardless of RNG |
| depletion | 4.2.7 | ⚠️ Uses `speed * dt` not `prints_delta` | ✅ Auto-refill, manual refill, zero speed | Generalised formula is mathematically equivalent (Y1 from internal review) |
| correlated | 4.2.8 | ✅ Exact | ✅ Zero speed freezes transport lag, ring buffer | Time-varying covariance (PRD 4.3.2) implemented |
| state | 4.2.9 | ✅ Exact | ✅ Self-transitions, competing timers, min_duration gate | One transition per tick, priority by list order |
| thermal_diffusion | 4.2.10 | ⚠️ Uses `4L²` not PRD `L²` | ✅ Convergence within 1°C, dynamic terms | Physically correct; PRD has notation error (documented) |
| bang_bang | 4.2.12 | ✅ Exact | ✅ Exact threshold, asymmetric dead band | Rates in C/min correctly converted to C/s via dt/60 |
| string_generator | 4.2.14 | ✅ Exact | ✅ Midnight reset, day crossing, naive timezone | Not used in Phase 1 (F&B only) |

**Noise Pipeline (4.2.11, 4.3.1):**

| Component | PRD Match | Notes |
|---|---|---|
| Gaussian distribution | ✅ | `sigma * N(0,1)` |
| Student-t distribution | ✅ | `sigma * T(df)` — higher RMS intentional per PRD |
| AR(1) distribution | ✅ | `phi * prev + sigma * sqrt(1-phi²) * N(0,1)` — marginal variance preserved |
| Speed-dependent sigma | ✅ Code | `sigma_base + sigma_scale * abs(parent_value)` — but not configured (R3) |
| Cholesky pipeline order | ✅ | generate N(0,1) → apply L → scale by sigma (correct order) |
| SeedSequence isolation | ✅ | Child RNGs spawned per subsystem via `rng.integers(0, 2**63)` |

**Quantisation (4.2.13):**
- ✅ Implemented once in `base.py`, applied per-signal via config `resolution` field
- No signals currently configure `resolution` in factory.yaml, but the mechanism works

## Modbus Register Verification

### Holding Registers (FC03)

| Address | PRD Signal | Config Signal | Type | Match |
|---------|-----------|---------------|------|-------|
| 100-101 | press.line_speed | press.line_speed | float32 ABCD | ✅ |
| 102-103 | press.web_tension | press.web_tension | float32 ABCD | ✅ |
| 110-111 | press.ink_viscosity | press.ink_viscosity | float32 ABCD | ✅ |
| 112-113 | press.ink_temperature | press.ink_temperature | float32 ABCD | ✅ |
| 120-121 | press.dryer_temp_zone_1 | press.dryer_temp_zone_1 | float32 ABCD | ✅ |
| 122-123 | press.dryer_temp_zone_2 | press.dryer_temp_zone_2 | float32 ABCD | ✅ |
| 124-125 | press.dryer_temp_zone_3 | press.dryer_temp_zone_3 | float32 ABCD | ✅ |
| 140-141 | press.dryer_setpoint_zone_1 | press.dryer_setpoint_zone_1 | float32 ABCD Writable | ✅ |
| 142-143 | press.dryer_setpoint_zone_2 | press.dryer_setpoint_zone_2 | float32 ABCD Writable | ✅ |
| 144-145 | press.dryer_setpoint_zone_3 | press.dryer_setpoint_zone_3 | float32 ABCD Writable | ✅ |
| 200-201 | press.impression_count | press.impression_count | uint32 ABCD | ✅ |
| 202-203 | press.good_count | press.good_count | uint32 ABCD | ✅ |
| 204-205 | press.waste_count | press.waste_count | uint32 ABCD | ✅ |
| 210 | press.machine_state | press.machine_state | uint16 | ✅ |
| 211 | press.fault_code | press.fault_code | uint16 | ✅ (fix applied) |
| 300-301 | press.main_drive_current | press.main_drive_current | float32 ABCD | ✅ |
| 302-303 | press.main_drive_speed | press.main_drive_speed | float32 ABCD | ✅ |
| 310-311 | press.nip_pressure | press.nip_pressure | float32 ABCD | ✅ |
| 320-321 | press.unwind_diameter | press.unwind_diameter | float32 ABCD | ✅ |
| 322-323 | press.rewind_diameter | press.rewind_diameter | float32 ABCD | ✅ |
| 400-401 | laminator.nip_temp | laminator.nip_temp | float32 ABCD | ✅ |
| 402-403 | laminator.nip_pressure | laminator.nip_pressure | float32 ABCD | ✅ |
| 404-405 | laminator.tunnel_temp | laminator.tunnel_temp | float32 ABCD | ✅ |
| 406-407 | laminator.web_speed | laminator.web_speed | float32 ABCD | ✅ |
| 408-409 | laminator.adhesive_weight | laminator.adhesive_weight | float32 ABCD | ✅ |
| 500-501 | slitter.speed | slitter.speed | float32 ABCD | ✅ |
| 502-503 | slitter.web_tension | slitter.web_tension | float32 ABCD | ✅ |
| 510-511 | slitter.reel_count | slitter.reel_count | uint32 ABCD | ✅ |
| 600-601 | energy.line_power | energy.line_power | float32 ABCD | ✅ |
| 602-603 | energy.cumulative_kwh | energy.cumulative_kwh | float32 ABCD | ✅ |

**All 30 HR entries match PRD Appendix A.** ✅

### Input Registers (FC04)

| Address | PRD Signal | Config Signal | Type | Match |
|---------|-----------|---------------|------|-------|
| 0 | press.dryer_temp_zone_1 | press.dryer_temp_zone_1 | int16 x10 | ✅ |
| 1 | press.dryer_temp_zone_2 | press.dryer_temp_zone_2 | int16 x10 | ✅ |
| 2 | press.dryer_temp_zone_3 | press.dryer_temp_zone_3 | int16 x10 | ✅ |
| 3 | press.ink_temperature | press.ink_temperature | int16 x10 | ✅ |
| 4 | laminator.nip_temp | laminator.nip_temp | int16 x10 | ✅ |
| 5 | laminator.tunnel_temp | laminator.tunnel_temp | int16 x10 | ✅ |
| 10-11 | energy.line_power | energy.line_power | float32 | ✅ |

**All 7 IR entries match PRD Appendix A.** ✅

### Coils (FC01)

| Address | PRD Signal | Implementation | Match |
|---------|-----------|---------------|-------|
| 0 | press.running (state=2) | press.machine_state == 2 | ✅ |
| 1 | press.fault_active (state=4) | press.machine_state == 4 | ✅ |
| 2 | press.emergency_stop | Always False | ⚠️ Y2 (internal review) |
| 3 | press.web_break | Always False | ⚠️ Y2 (internal review) |
| 4 | laminator.running | press.machine_state == 2 | ⚠️ Y5 (should derive from laminator) |
| 5 | slitter.running | press.machine_state == 2 | ⚠️ Y5 (should derive from slitter) |

### Discrete Inputs (FC02)

| Address | PRD Signal | Implementation | Match |
|---------|-----------|---------------|-------|
| 0 | press.guard_door_open | Always False | ✅ (reasonable default) |
| 1 | press.material_present | press.machine_state == 2 | ✅ |
| 2 | press.cycle_complete | impression_count % 2 | ✅ |

### Modbus Protocol Features

| Feature | PRD Reference | Status |
|---------|--------------|--------|
| Float32 ABCD encoding | Appendix A | ✅ Verified at byte level |
| Int16 x10 scaling | Appendix A (IR) | ✅ Verified including negative values |
| Uint32 ABCD encoding | Appendix A | ✅ Verified |
| FC06 rejection on float32 | Section 3.1.2 | ✅ Returns ILLEGAL_FUNCTION |
| FC06 rejection on uint32 | Inferred | ✅ Also rejected (good) |
| 125-register read limit | Section 3.1.7 | ✅ Returns ILLEGAL_VALUE |
| Writable setpoint registers | Appendix A | ✅ 140-145 are writable |

## Exit Criteria Verification

Per PRD Appendix F, Phase 1 exit criteria:

| Criterion | Status | Evidence |
|-----------|--------|----------|
| CollatrEdge connects via Modbus TCP | ✅ | Integration tests verify pymodbus client reads |
| All holding registers readable | ✅ | 30 HR entries, all tested |
| All input registers readable | ✅ | 7 IR entries, all tested |
| All coils readable | ✅ | 6 coils, tested |
| All discrete inputs readable | ✅ | 3 DIs, tested |
| All 47 packaging signals produce values in range | ✅ | 1078 tests pass, bounds checked |
| Counters increment | ✅ | impression/good/waste_count tested |
| State transitions occur | ✅ | State machine with 6 states tested |
| All unit and integration tests pass | ✅ | 1078 tests, ruff + mypy clean |
| CI pipeline runs under 5 minutes | Unverified | Not measured in review |
| Pydantic validation models | ✅ | 69 config tests |
| Simulation clock (time-scale invariant) | ✅ | Clock deterministic, no wall-clock |
| All 12 signal models | ✅ | All implemented and tested |
| All 7 packaging equipment generators | ✅ | Press, laminator, slitter, coder, env, energy, vibration |
| Cholesky correlation pipeline | ✅ | Correct order verified |
| Noise distributions (3 types) | ✅ Code, ⚠️ Config | Code supports all 3, config only activates AR(1) for dryers |
| Basic scenarios (job changeover, shift change, unplanned stop) | ✅ | All three implemented |
| Docker container | Unverified | Not checked in this review |

## GO/NO-GO

**Conditional GO for Phase 2.**

**Justification:** The core architecture is sound. All 12 signal models are mathematically correct. The engine, store, clock, and Modbus server all work correctly. The test coverage is excellent (1078 tests). The three RED findings (R1-R3) are all config/data issues that can be fixed without architectural changes — they require updating `factory.yaml` and one line in `vibration.py`.

**Conditions for GO:**

1. **Must fix R1** (vibration correlation matrix) — change the matrix to match PRD 4.3.1. This is a one-line code fix.
2. **Must fix R2** (noise distribution assignments) — add `noise_type`, `noise_df`, `noise_phi` to the appropriate signals in `factory.yaml`. This is YAML-only changes.
3. **Must fix R3** (speed-dependent sigma) — add `sigma_base`, `sigma_scale` to the appropriate signals in `factory.yaml`. This is YAML-only changes.

**Should fix before Phase 2 but not blocking:**

- Y3 (vibration effective_sigma usage) — needed for R3 to actually work
- Y4 (vibration double-noising) — affects vibration data quality
- Y5 (coil 5 slitter derivation) — the slitter already has independent scheduling

**Can defer to Phase 2:**

- Y1 (environment composite model) — Phase 2 adds more scenario types; this fits naturally
- Y2 (energy kWh rate) — adjust config value
- Y6-Y10 — minor issues

The implementation quality is high. The fixes are straightforward. Phase 2 can proceed once R1-R3 are addressed.
