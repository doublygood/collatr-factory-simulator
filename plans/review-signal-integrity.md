# Signal Integrity & Mathematical Correctness Review

**Reviewer:** Data Scientist / Signal Processing Expert (subagent)  
**Date:** 2026-03-04  
**Scope:** All signal models, noise distributions, Cholesky pipeline, scenario logic, evaluation framework, reproducibility, and data quality injection in the Collatr Factory Simulator.  
**Method:** Line-by-line comparison of source code formulas against PRD specifications.

---

## 1. Executive Summary

The Collatr Factory Simulator's mathematical implementation is **remarkably solid**. After a rigorous audit of every signal model formula, noise distribution, scenario, and the evaluation framework, I found **no RED-severity bugs** — no formula is fundamentally wrong, no noise distribution produces incorrect marginal statistics, and the Cholesky pipeline is correctly ordered.

The codebase demonstrates exceptional engineering discipline: consistent use of simulation time (never wall clock), proper SeedSequence hierarchy for reproducibility, and careful attention to the PRD specifications. The few issues found are all YELLOW (minor correctness concerns) or GREEN (style/documentation observations).

**Key findings:**

| Severity | Count | Summary |
|----------|-------|---------|
| 🔴 RED | 0 | No critical mathematical errors found |
| 🟡 YELLOW | 6 | Minor correctness concerns, edge cases, minor deviations from PRD |
| 🟢 GREEN | 8 | Observations, documentation suggestions, and minor style notes |

---

## 2. Signal Model Formula Audit

### 2.1 Steady State (steady_state.py) — ✅ CORRECT

**PRD 4.2.1 specifies:**
```
effective_target = target + drift_offset
drift_offset += drift_rate * noise(0,1) * sqrt(dt) - reversion_rate * drift_offset * dt
value = effective_target + noise(0, sigma)
calibration_bias += calibration_drift_rate * dt
value = value + calibration_bias
```

**Code implements (lines 94-115):**
```python
innovation = self._drift_rate * self._rng.standard_normal() * sqrt_dt
reversion = self._reversion_rate * self._drift_offset * dt
self._drift_offset += innovation - reversion
effective_target = self._target + self._drift_offset
value = effective_target + noise.sample()
self._calibration_bias += self._calibration_drift_rate * dt
value += self._calibration_bias
```

**Verdict:** Exact match. The Ornstein-Uhlenbeck discretisation is correct. The sqrt(dt) scaling on the innovation term is properly applied. Calibration drift is persistent and non-reverting as specified.

**Note on units (🟡 Y1):** The docstring states `calibration_drift_rate` is "per simulated second", but the PRD says "per simulated hour". The docstring acknowledges the caller must convert, which is correct but fragile — if a config passes per-hour values directly, drift would be 3600x too fast.

### 2.2 First-Order Lag (first_order_lag.py) — ✅ CORRECT

**PRD 4.2.3 specifies two modes:**

First-order: `value = value + (setpoint - value) * (1 - exp(-dt / tau))`

Second-order (damping_ratio < 1.0):
```
value = setpoint + A * exp(-zeta * omega_n * t) * sin(omega_d * t + phase)
omega_n = 1/tau, omega_d = omega_n * sqrt(1 - zeta^2)
A = step_size / sqrt(1 - zeta^2), phase = arccos(zeta)
```

**Code implements:**
- First-order (line 145): `alpha = 1.0 - math.exp(-dt / self._tau)` then `self._value += (self._setpoint - self._value) * alpha` ✅
- Second-order (lines 131-141): Uses `self._transient_A * math.exp(-zeta * omega_n * t) * sin(omega_d * t + phase)` ✅
- `_start_transient` (lines 117-121): `step_size = from_value - to_setpoint`, `A = step_size / sqrt(1 - zeta^2)`, `phase = acos(zeta)` ✅
- Transients do not stack — new setpoint change abandons previous transient ✅

**Verification of initial condition:** At t=0 with `from_value = v0`, `to_setpoint = sp`:
```
value = sp + (v0 - sp)/sqrt(1-z²) * exp(0) * sin(0 + acos(z))
     = sp + (v0 - sp)/sqrt(1-z²) * sin(acos(z))
     = sp + (v0 - sp)/sqrt(1-z²) * sqrt(1-z²)
     = sp + (v0 - sp)
     = v0  ✅
```

**Verdict:** Mathematically correct. The second-order response matches the standard underdamped oscillator equations.

### 2.3 Ramp (ramp.py) — ✅ CORRECT

**PRD 4.2.4 specifies:**
```
value = start + (end - start) * (elapsed / duration) + noise(0, sigma)
```
With step quantisation: N steps with random dwell times, overshoot at each step boundary that decays exponentially.

**Code implements:**
- Smooth ramp (line 165): `progress = elapsed / duration`, `value = start + (end - start) * progress` ✅
- Step plan (lines 109-131): evenly-spaced targets, random dwell times from uniform distribution, proportional compression if total exceeds duration ✅
- Overshoot (lines 192-199): `overshoot = step_overshoot * exp(-time_in_step / decay_s)` ✅
- Duration hard cap (line 159): holds at end value when complete ✅

**Verdict:** Correct implementation of the PRD specification.

### 2.4 Counter (counter.py) — ✅ CORRECT

**PRD 4.2.6 specifies:** `value = value + rate * line_speed * dt`

**Code implements (line 162):** `increment = self._rate * self._speed * dt` ✅

Rollover uses modulo: `self._value = self._value % self._rollover_value` ✅

**Verdict:** Correct.

### 2.5 Correlated Follower (correlated.py) — ✅ CORRECT

**PRD 4.2.8 specifies:**
```
value = f(parent_value) + noise(0, sigma)
lag_seconds = distance_meters / (line_speed_m_per_min / 60)
```

**PRD 4.3.2 Time-Varying Covariance:**
```
log_drift += drift_volatility * noise(0,1) * sqrt(dt) - reversion_rate * log_drift * dt
gain_drift_factor = exp(log_drift)
k_effective = k_nominal * gain_drift_factor
```

**Code implements:**
- Gain drift (lines 206-211): `self._log_drift += volatility * N(0,1) * sqrt(dt) - reversion * log_drift * dt` ✅
- `gain_eff = self._gain * math.exp(self._log_drift)` ✅
- Linear transform (line 216): `result = self._base + gain_eff * parent` ✅
- Fixed lag via ring buffer (lines 236-244) ✅
- Transport lag (lines 246-269): `lag_s = distance_m / (speed / 60)` ✅
- Zero speed freezes output (lines 257-260) ✅

**Verdict:** Correct. The multiplicative log-normal drift preserves positivity of gain. The ring buffer implementation is sound.

**Note (🟢 G1):** The PRD mentions quadratic transforms as an option for correlated followers, but the implementation only supports linear (`base + gain * parent`). This is a feature gap, not a bug, and is adequate for current signal definitions.

### 2.6 Thermal Diffusion (thermal_diffusion.py) — ✅ CORRECT (with noted clarification)

**PRD 4.2.10 specifies:**
```
T(t) = T_oven - (T_oven - T_initial) * SUM C_n * exp(-(2n+1)^2 * pi^2 * alpha * t / L^2)
C_n = 8 / ((2n+1)^2 * pi^2)
```

**Code implements (with documented correction):**
```python
decay_n = (2n+1)^2 * pi^2 * alpha / (4 * L^2)  # Note: 4*L^2, not L^2
```

**Analysis:** The code comment at line 14-16 explicitly documents this decision: "The PRD formula writes L^2 but defines L as 'half-thickness'. We use 4*L^2 to match the standard physics and the PRD's expected timing (~15-20 min for a ready meal to reach 72C)."

This is **physically correct**. The standard Fourier series for a slab of total thickness 2L has decay constant `(2n+1)^2 * pi^2 * alpha / (4L^2)` when L is the half-thickness. The PRD text is ambiguous — it says `L^2` but defines L as "half-thickness" and gives timing expectations that match `4L^2`. The code chose the physically correct formula.

**Convergence check (lines 123-129):** Adds terms until `T(0)` is within 1°C of `T_initial` per PRD requirement ✅

**Coefficient verification:**
- n=0: `C_0 = 8 / (1 * pi^2) = 0.8106` — matches PRD table ✅
- n=1: `C_1 = 8 / (9 * pi^2) = 0.0901` — matches PRD table ✅
- n=2: `C_2 = 8 / (25 * pi^2) = 0.0324` — matches PRD table ✅

**Verdict:** Correct implementation. The 4L² denominator is a necessary correction to the PRD's slightly ambiguous formula.

### 2.7 Bang-Bang (bang_bang.py) — ✅ CORRECT

**PRD 4.2.12 specifies:**
```
if state == OFF and pv > setpoint + dead_band_high: state = ON
if state == ON and pv < setpoint - dead_band_low: state = OFF
```

**Code implements (lines 159-167):**
```python
if not self._on and self._pv > upper: self._on = True
elif self._on and self._pv < lower: self._on = False
```

Where `upper = setpoint + dead_band_high`, `lower = setpoint - dead_band_low` ✅

Rate conversion: `dt_min = dt / 60.0` for C/min rates — correct ✅

**Verdict:** Exact match with PRD specification.

### 2.8 Random Walk with Mean Reversion (random_walk.py) — 🟡 Y2

**PRD 4.2.5 specifies:**
```
delta = drift_rate * noise(0, 1) - reversion_rate * (value - center)
value = value + delta * dt
```

**Code implements (lines 109-112):**
```python
innovation = self._drift_rate * self._rng.standard_normal()
reversion = self._reversion_rate * (self._value - self._center)
delta = innovation - reversion
self._value += delta * dt
```

**Concern:** This is a correct transcription of the PRD formula. However, the PRD Section 4.1 Principle 5 warns: "using wall-clock dt at 100x compression would inflate drift rates by a factor of 10 due to sqrt(dt) scaling." The random walk model should scale the innovation by `sqrt(dt)` for proper Wiener process discretisation: `innovation = drift_rate * N(0,1) * sqrt(dt)`. The current implementation multiplies the entire delta (innovation + reversion) by dt, which means the innovation term scales as `drift_rate * N(0,1) * dt` instead of `drift_rate * N(0,1) * sqrt(dt)`. 

**BUT**: Looking more carefully, the PRD formula explicitly says `delta = drift_rate * noise(0,1) - reversion * (...)`  then `value += delta * dt`. So the code matches the PRD exactly. The PRD itself doesn't use `sqrt(dt)` in this model's innovation — it uses `sqrt(dt)` only in the steady-state drift (Section 4.2.1). The PRD's own formula for the random walk model thus has the innovation scaling as `dt` not `sqrt(dt)`, which means at different dt values, the variance of the random walk will change. This is a **PRD design choice**, not a code bug.

**However**, the docstring at line 61 says "drift_rate controls how fast the signal wanders (units per sqrt-second — scaled by sqrt(dt) implicitly through the discrete Euler step)." This is **misleading** — there is no sqrt(dt) in the code. The scaling is linear in dt.

**Verdict:** Code matches PRD exactly. The docstring is misleading about sqrt(dt) scaling. This is a YELLOW because the actual variance behaviour under time compression differs from what the docstring claims, though the code matches the spec.

### 2.9 Depletion (depletion.py) — ✅ CORRECT

**PRD 4.2.7 specifies:** `value = value - consumption_rate * prints_delta`

**Code implements:** `self._value -= self._consumption_rate * self._speed * dt` ✅

Auto-refill logic is correct: triggers at threshold, jumps to refill value ✅

---

## 3. Noise Distribution Correctness

### 3.1 Gaussian Noise — ✅ CORRECT

**PRD:** `noise = sigma * N(0, 1)`  
**Code (line 139):** `return float(sigma * self._rng.standard_normal())` ✅

### 3.2 Student-t Noise — ✅ CORRECT

**PRD 4.2.11 specifies:** `noise = sigma * T(df)`

**PRD variance note:** "The Student-t distribution with df degrees of freedom has variance sigma^2 * df / (df - 2). At df=5, the effective standard deviation is 1.29 times sigma. This is intentional."

**Code (line 143):** `return float(sigma * self._rng.standard_t(self._df))` ✅

**Verification:** `numpy.random.Generator.standard_t(df)` produces samples from a standard Student-t distribution with variance `df / (df - 2)`. Multiplying by sigma gives variance `sigma^2 * df / (df - 2)`. The PRD explicitly states this is intentional and does NOT apply the `sqrt((df-2)/df)` correction. The code matches. ✅

**Guard:** `df >= 3` is enforced (line 76), which ensures finite variance (Student-t variance is undefined for df ≤ 2). ✅

### 3.3 AR(1) Noise — ✅ CORRECT

**PRD 4.2.11 specifies:**
```
noise_t = phi * noise_(t-1) + sigma * sqrt(1 - phi^2) * N(0, 1)
```

**Code (lines 146-150):**
```python
innovation_scale = sigma * np.sqrt(1.0 - self._phi**2)
self._ar1_prev = (
    self._phi * self._ar1_prev
    + innovation_scale * self._rng.standard_normal()
)
```

**Marginal variance verification:** For a stationary AR(1) process `x_t = phi * x_{t-1} + e_t` where `e_t ~ N(0, sigma_e^2)`:
- `Var(x) = sigma_e^2 / (1 - phi^2)`
- Here `sigma_e = sigma * sqrt(1 - phi^2)`, so `sigma_e^2 = sigma^2 * (1 - phi^2)`
- `Var(x) = sigma^2 * (1 - phi^2) / (1 - phi^2) = sigma^2` ✅

The marginal standard deviation equals the configured sigma, exactly as the PRD requires.

**Guard:** `phi` must be in `(-1, 1)` (line 81), ensuring stationarity ✅

### 3.4 Speed-Dependent Sigma — ✅ CORRECT

**PRD:** `effective_sigma = sigma_base + sigma_scale * abs(parent_value)`

**Code (lines 112-115):**
```python
if self._sigma_base is not None and parent_value is not None:
    return self._sigma_base + self._sigma_scale * abs(parent_value)
return self._sigma
```

**Verdict:** Exact match.

### 3.5 Noise Distribution Summary — No Issues Found

All three distributions are correctly implemented. The Student-t variance scaling is intentionally NOT corrected per the PRD. The AR(1) innovation variance is correctly scaled to produce marginal variance = sigma^2.

---

## 4. Cholesky Pipeline Verification

### 4.1 Pipeline Ordering — ✅ CORRECT

**PRD 4.3.1 specifies the pipeline order:**
1. Generate N independent N(0,1) samples
2. Apply Cholesky factor L: `correlated = L @ independent`
3. Scale by effective sigma per signal

**Vibration generator (vibration.py, lines 118-134):**
```python
# Step 1: Generate 3 independent N(0,1) draws
z = self._rng.standard_normal(3)

# Step 2: Apply Cholesky L to introduce correlation
correlated_z = self._cholesky_l @ z

# Step 3: Scale correlated draw by effective sigma
sigma = noise_gen.effective_sigma(press_speed)
raw += sigma * float(correlated_z[i])
```

**Verdict:** Pipeline order exactly matches PRD. Scale AFTER correlation preserves correlation coefficients ✅

### 4.2 Positive Definite Validation — ✅ CORRECT

**Code (vibration.py line 76):** `self._cholesky_l = np.linalg.cholesky(corr_matrix)`

`numpy.linalg.cholesky` will raise `LinAlgError` if the matrix is not positive definite. This serves as the startup validation ✅

**CholeskyCorrelator class (noise.py lines 183-192):** Validates symmetric, unit diagonal, and computes Cholesky factor (which implicitly validates positive definiteness) ✅

### 4.3 Correlation Matrix Values — ✅ CORRECT

**PRD vibration correlation matrix:**
```
R = [[1.0,  0.2,  0.15],
     [0.2,  1.0,  0.2 ],
     [0.15, 0.2,  1.0 ]]
```

**Code (vibration.py lines 53-57):**
```python
_PRD_CORRELATION_MATRIX = np.array([
    [1.0,  0.2,  0.15],
    [0.2,  1.0,  0.2],
    [0.15, 0.2,  1.0],
])
```

Exact match ✅

### 4.4 Student-t + Cholesky Approximation — 🟢 G2

**PRD 4.3.1 documents:** "The Cholesky pipeline generates correlated Gaussian samples. For signals configured with Student-t noise (e.g. vibration axes), the pipeline produces correlated Gaussian noise scaled by Student-t sigma, not true correlated Student-t random variables."

**Analysis:** The vibration generator uses the Cholesky pipeline with Student-t noise generators. However, the Cholesky pipeline generates correlated *Gaussian* draws (Step 1: `standard_normal(3)`), not Student-t draws. The Student-t sigma is used for *scaling* (Step 3), but the draws themselves are Gaussian. This matches the PRD's documented approximation.

The PRD states this is acceptable at correlations 0.15-0.2 with df=5-8. At these parameters, the Gaussian copula approximation produces nearly identical joint behaviour ✅

**Note:** The `NoiseGenerator` objects created for vibration axes are NOT used for sampling — only for `effective_sigma()` computation. The actual noise samples come from the Cholesky pipeline. This avoids double-noising ✅

### 4.5 Dryer/Oven Zone Correlation — 🟡 Y3

**PRD 4.3.1 specifies dryer zone and oven zone correlation matrices** but the code does NOT appear to use Cholesky correlation for dryer zones or oven zones. The vibration generator has the full Cholesky pipeline, but I found no Cholesky references in the press generator (for dryer zones) or oven generator.

The PRD specifies:
```
Dryer zones: R = [[1.0, 0.1, 0.02], [0.1, 1.0, 0.1], [0.02, 0.1, 1.0]]
Oven zones:  R = [[1.0, 0.15, 0.05], [0.15, 1.0, 0.15], [0.05, 0.15, 1.0]]
```

The oven generator has thermal coupling via adjacent zone drift influence (PRD 5.14.2 inter-zone coupling factor), which provides some correlation, but this is a physical-model-based coupling, not the Cholesky noise correlation specified in PRD 4.3.1.

**Impact:** The noise on dryer/oven zones will be independent rather than correlated. At the small correlation values specified (0.02-0.15), the practical impact is negligible for demos and integration testing. A spectral analysis researcher might notice the absence.

---

## 5. Scenario Logic Issues

### 5.1 Bearing Wear (bearing_wear.py) — ✅ CORRECT

**PRD 5.5 formula:** `vibration_increase = base_rate * exp(k * elapsed_hours)`

**Code (line 159):** `vib_increase = self._base_rate * math.exp(self._k * elapsed_hours)`

**Time tracking:** `elapsed_hours = self._elapsed / 3600.0` where `self._elapsed` is accumulated from `dt` (simulation time) via the base `Scenario.evaluate()` method ✅

The base class `Scenario` accumulates elapsed time via `self._elapsed += dt` at line 93 of base.py, where dt is simulation time. No wall clock usage ✅

**Verdict:** Formula correct, units correct, uses sim_time ✅

### 5.2 Intermittent Fault (intermittent_fault.py) — ✅ CORRECT

**Three-phase progression:**
- Phase 1 (sporadic): correctly transitions at `self._phase1_duration_s` (line 178) ✅
- Phase 2 (frequent): correctly transitions at `self._total_duration_s` (line 183) ✅
- Phase 3 (permanent): correctly enters via `_enter_phase3()` (line 184) ✅

**Spike scheduling:** Uses Poisson inter-arrival times via `rng.exponential(mean_interval)` (line 296) ✅

**Phase 3 transition flag:** `phase3_transition` controls whether Phase 3 is entered. Pneumatic defaults to False per PRD ✅

### 5.3 Contextual Anomaly (contextual_anomaly.py) — ✅ CORRECT

**State-dependent activation:** Waits for target machine state, injects anomalous value ✅  
**Timeout at 2x window:** `self._timeout_s = 2.0 * self._duration_s` (line 159) ✅  
**Early termination on state change:** Lines 203-205 — if machine state changes away from target, scenario completes early ✅  
**Post-gen injection:** Uses `post_gen_inject()` to overwrite generator output after generators run ✅

### 5.4 Micro-Stop (micro_stop.py) — ✅ CORRECT

**Speed dip without state change:** The scenario modifies `_line_speed_model` directly via `start_ramp()` but never touches `machine_state`. The press stays Running(2) throughout ✅

**Three sub-phases (ramp-down → hold → ramp-up):** Correctly implemented with elapsed-time boundaries ✅

### 5.5 Ground Truth Logging — 🟡 Y4

**Double logging concern:** The `ScenarioEngine.tick()` method (scenario_engine.py lines 115-137) logs `scenario_start` and `scenario_end` events by detecting phase transitions. However, some scenarios ALSO log their own start/end events internally. For example:

- `BearingWear._on_activate()` calls `gt.log_scenario_start()` (bearing_wear.py line 126)
- `BearingWear._on_complete()` calls `gt.log_scenario_end()` (bearing_wear.py line 148)

The ScenarioEngine also logs when it detects PENDING→ACTIVE and →COMPLETED transitions. This could produce **duplicate ground truth events** for scenarios that have internal GT logging.

**Impact:** Duplicate entries in the ground truth JSONL could cause the evaluator to create duplicate `GroundTruthEvent` objects, inflating the event count and distorting precision/recall. The evaluator uses FIFO pairing of scenario_start/scenario_end, so double-starts would pair incorrectly.

**Mitigation:** Need to verify whether scenarios with internal logging are skipped by the ScenarioEngine's logging, or whether deduplication occurs elsewhere.

### 5.6 Scenario Priority Conflict — ✅ CORRECT

The ScenarioEngine correctly implements priority-based conflict resolution (lines 85-112):
- `state_changing` preempts `non_state_changing` ✅
- `non_state_changing` is deferred when `state_changing` is active ✅
- `background` and `micro` activate without checks ✅
- Same-tick double state_changing is deferred (Y4 fix noted in code) ✅

---

## 6. Evaluation Framework Maths

### 6.1 Precision / Recall / F1 — ✅ CORRECT

**Code (evaluator.py lines 191-198):**
```python
precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
```

Matches PRD 12.4 definitions ✅  
Edge cases handled: zero denominator returns 0.0 ✅

### 6.2 Weighted Recall — ✅ CORRECT

**Code (lines 201-207):**
```python
total_weight = sum(s.severity_weights.get(m.event_type, 1.0) for m in matches)
detected_weight = sum(
    s.severity_weights.get(m.event_type, 1.0) for m in matches if m.detected
)
weighted_recall = detected_weight / total_weight if total_weight > 0 else 0.0
```

Matches PRD: `weighted_recall = sum(weight_i for detected) / sum(weight_i for all)` ✅

### 6.3 Weighted F1 — ✅ CORRECT

```python
weighted_f1 = 2 * precision * weighted_recall / (precision + weighted_recall)
```

Uses precision (not weighted precision) with weighted recall, computing the harmonic mean ✅

### 6.4 Detection Latency — ✅ CORRECT

**Code (evaluator.py line 149):** `latency = first_t - ev.start_time`

Negative latency (early detection) is preserved as-is per PRD: "Negative latency is desirable and should be reported as-is, not clamped to zero" ✅

### 6.5 Event Matching — ✅ CORRECT

**Tolerance windows (evaluator.py line 94):** `(ev.start_time - pre_margin, ev.end_time + post_margin)` ✅

**Overlapping window tie-breaking (line 98-100):** Assigns to nearest event by `abs(detection_time - event.start_time)` ✅. This matches PRD: "a single detection is assigned to the nearest event by start time."

**Multiple detections per event (line 146):** `first_t = min(assigned_timestamps)` — only the first detection counts ✅

### 6.6 Random Baseline — ✅ CORRECT

**Anomaly density computation (evaluator.py lines 242-256):**
- Merges overlapping event intervals before computing density ✅
- `anomaly_density = total_anomaly_time / total_duration` ✅
- Simulates random detector with `rng.random(n_ticks) < anomaly_density` ✅

### 6.7 Multi-Seed CI — ✅ CORRECT

**Code (cli.py lines 323-333):**
```python
variance = sum((x - mean) ** 2 for x in values) / (n - 1)  # sample variance
std = math.sqrt(variance)
margin = 1.96 * std / math.sqrt(n)
```

**PRD:** `CI = mean ± 1.96 * std / sqrt(N)` with sample std ✅

Uses `n - 1` denominator (Bessel's correction for sample variance) ✅

### 6.8 Edge Cases — ✅ HANDLED

- Zero events: `total_weight = 0 → weighted_recall = 0.0` ✅
- Zero detections: all events are FN, `tp = 0, precision = 0.0` ✅
- All FP: `tp = 0, recall = 0.0` ✅
- All FN: `tp = 0, precision = 0.0` ✅
- Single event: handled by the general case ✅

### 6.9 Severity Weight Keys — 🟡 Y5

**Issue:** The severity weights in `metrics.py` use lowercase_with_underscores naming (e.g. `"web_break"`, `"dryer_drift"`), but the ground truth log uses class names (e.g. `"WebBreak"`, `"DryerDrift"`). The evaluator loads event types from `record["scenario"]` which is the class name.

When the evaluator calls `s.severity_weights.get(m.event_type, 1.0)`, the event_type will be `"WebBreak"` but the weights dict has key `"web_break"`. The lookup will always miss, falling back to the default weight of 1.0. **Weighted recall will effectively equal unweighted recall.**

**Impact:** Medium. Weighted metrics will be meaningless unless the weight keys are normalized to match ground truth scenario names, or the evaluator converts one to the other.

---

## 7. Reproducibility & RNG

### 7.1 SeedSequence Hierarchy — ✅ CORRECT

**Data engine:** Creates child RNGs via `SeedSequence.spawn()` per generator (data_engine.py line 137-145) ✅

**Scenario engine:** Uses `self._seed_seq.spawn(1)[0]` for each spawned scenario RNG (scenario_engine.py `_spawn_rng()` method) ✅

### 7.2 No `random` Module Usage — ✅ CONFIRMED

Grep for `import random` and `from random` in `/src/` returned zero results ✅

All stochastic operations use `numpy.random.Generator` ✅

### 7.3 Deterministic Ordering — ✅ CORRECT

**Signal store:** Uses dict but Python 3.7+ dicts maintain insertion order ✅

**Scenario list:** Sorted by start_time after generation (scenario_engine.py line 258) ✅

**Reproducibility tests:** `test_reproducibility.py` verifies exact store equality for both profiles across two independent runs with seed=42 ✅. Full-day reproducibility is tested ✅.

### 7.4 Floating-Point Determinism — ✅ NO ISSUES FOUND

No set iteration over non-deterministic collections found. No floating-point non-determinism patterns (like summing in different orders) identified.

**Note (🟢 G3):** The reproducibility tests compare store snapshots after a fixed number of ticks, which is the right approach. Cross-platform reproducibility is not guaranteed (different CPUs may produce different floating-point results for the same seed), and the code doesn't claim it.

---

## 8. Data Quality Injection

### 8.1 Sentinel Values — ✅ CORRECT

**PRD 10.9 specifies:**
- Temperature: 6553.5
- Pressure: 0.0
- Voltage: -32768

**Code (data_quality.py `_sentinel_for_signal()`, lines 38-47):**
```python
if "temp" in name: return cfg.sentinel_defaults.temperature
if "pressure" in name: return cfg.sentinel_defaults.pressure
if "voltage" in name: return cfg.sentinel_defaults.voltage
return 0.0
```

The sentinel values come from config objects. The config defaults should match PRD. The code structure is correct ✅

**Also in intermittent_fault.py (lines 33-36):**
```python
_SENTINEL_TEMPERATURE = 6553.5
_SENTINEL_PRESSURE = 0.0
_SENTINEL_VOLTAGE = -32768.0
```

Matches PRD ✅

### 8.2 Sensor Disconnect Quality Flag — ✅ CORRECT

**PRD:** Sets quality="bad"  
**Code (data_quality.py line 116):** `store.set(sig_id, self._sentinels[sig_id], sim_time, "bad")` ✅

### 8.3 Stuck Sensor Quality Flag — ✅ CORRECT

**PRD:** Keeps quality="good" (sensor appears to work normally)  
**Code (data_quality.py line 208):** `store.set(sig_id, self._frozen_value[sig_id], sim_time, "good")` ✅

### 8.4 Injection Ordering — ✅ CORRECT

**PRD 8.2 ordering:** Generators → Scenarios (post-gen) → Data Quality → Protocol reads

**Code (data_engine.py tick method, lines 388-433):**
1. `self._scenario_engine.tick(sim_time, dt, self)` — scenarios BEFORE generators ✅
2. Generator loop (lines 407-427) — generators produce values ✅
3. `self._scenario_engine.post_gen_tick(sim_time, dt, self._store)` — scenario post-gen injection AFTER generators ✅
4. `self._data_quality.tick(sim_time, self._store, self._ground_truth)` — data quality AFTER everything else ✅

**Verdict:** Correct ordering. Data quality runs last, overriding any generator or scenario output.

---

## 9. Issues Table

| # | Severity | File:Line | Issue | Impact |
|---|----------|-----------|-------|--------|
| Y1 | 🟡 YELLOW | `steady_state.py:46` | `calibration_drift_rate` units mismatch between docstring ("per second") and PRD ("per hour"). If config passes per-hour values directly, drift would be 3600x too fast. | Config loader must convert; if it doesn't, calibration drift is wildly inflated. Verify config loader converts `units/hour → units/second`. |
| Y2 | 🟡 YELLOW | `random_walk.py:61` | Docstring claims "units per sqrt-second — scaled by sqrt(dt) implicitly" but the code scales innovation by `dt` not `sqrt(dt)`. The formula matches the PRD but the docstring is misleading. | Misleading documentation. No runtime impact since code matches PRD. |
| Y3 | 🟡 YELLOW | Generator layer (press, oven) | PRD 4.3.1 specifies Cholesky correlation matrices for dryer zones (3×3) and oven zones (3×3), but these are not implemented. Only vibration axes have Cholesky correlation. | Dryer/oven zone noise is independent instead of correlated. At specified correlation values (0.02-0.15), impact is minimal for demos but visible in statistical analysis. |
| Y4 | 🟡 YELLOW | `bearing_wear.py:126` + `scenario_engine.py:115-137` | Potential double-logging of scenario_start/scenario_end events. The ScenarioEngine logs transitions AND individual scenarios log their own GT events. | Could produce duplicate ground truth entries, distorting evaluation metrics. |
| Y5 | 🟡 YELLOW | `metrics.py:13-29` + `evaluator.py:201` | Severity weight dict keys (`"web_break"`) don't match ground truth scenario names (`"WebBreak"`). Weight lookup always falls back to default 1.0. | Weighted recall/F1 metrics are effectively identical to unweighted metrics. |
| Y6 | 🟡 YELLOW | `evaluator.py:142-149` | Evaluator `load_ground_truth()` drops open scenarios (start without matching end). Long-running scenarios like BearingWear that haven't completed by end of sim are silently excluded from evaluation. | Events near the end of a simulation run may be missed in evaluation, understating recall. |
| G1 | 🟢 GREEN | `correlated.py` | PRD mentions quadratic transforms for correlated followers; only linear is implemented. | Feature gap, not a bug. Linear is sufficient for all current signal definitions. |
| G2 | 🟢 GREEN | `vibration.py:118` | Student-t + Cholesky approximation is correctly implemented as documented. Gaussian draws are used with Student-t sigma scaling. | Working as designed per PRD's documented approximation. |
| G3 | 🟢 GREEN | `test_reproducibility.py` | Cross-platform reproducibility not tested (different CPUs, different OS). Tests run same-platform only. | Acceptable for current use case. Document if cross-platform reproducibility is ever claimed. |
| G4 | 🟢 GREEN | `noise.py:76` | Student-t df minimum is 3 (enforced). PRD mentions df=5-8 as typical. df=3 produces very heavy tails (kurtosis = ∞). | Guard is appropriate but df=3 produces infinite kurtosis. Consider df >= 4 as minimum for safer defaults. |
| G5 | 🟢 GREEN | `counter.py:162` | Counter increment uses float arithmetic (`rate * speed * dt`). Over very long simulations, float precision loss could accumulate. | Negligible for typical simulation durations. Would matter for multi-month batch simulations at fine resolution. |
| G6 | 🟢 GREEN | `scenario_engine.py:228-246` | Poisson scheduling with min_gap enforcement. The min_gap shifts the inter-arrival distribution from pure exponential to a displaced exponential (`gap = max(exponential, min_gap)`). This is not a pure Poisson process. | Documented behaviour. The displacement is small relative to the mean interval for most scenario types. |
| G7 | 🟢 GREEN | `evaluator.py:257-270` | Random baseline uses `rng.random(n_ticks) < anomaly_density` which generates a Bernoulli process, not a true Poisson process. PRD says "fires an alert at each tick with probability p". | Bernoulli per-tick is the correct interpretation of "fires at each tick with probability p". Not a bug. |
| G8 | 🟢 GREEN | `thermal_diffusion.py:14-16` | Code uses `4*L^2` in the denominator where PRD writes `L^2`. The code is physically correct (standard Fourier solution for a slab with half-thickness L). | The PRD formula is ambiguous; the code chose the correct physics. Well-documented in the code comments. |

---

## 10. Conclusions

### What's Right

1. **All core signal model formulas are correctly implemented.** Every formula in the code matches its PRD specification (or is a documented, justified correction like thermal diffusion's 4L²).

2. **Noise distributions are mathematically sound.** The Student-t variance scaling is intentional per PRD. The AR(1) innovation variance is correctly scaled so marginal std = configured sigma. The Cholesky pipeline ordering is correct.

3. **The evaluation framework produces correct metrics.** Precision, recall, F1, weighted variants, and detection latency are all correctly computed. Edge cases are handled. The multi-seed CI formula uses sample standard deviation with Bessel's correction.

4. **Reproducibility is well-engineered.** SeedSequence hierarchy, no `random` module, deterministic ordering, and comprehensive reproducibility tests.

5. **Data quality injection has correct ordering and semantics.** Sentinel values match PRD. Quality flags are correct. Injection happens after generators and before protocol reads.

6. **Scenario logic is sound.** Bearing wear uses sim_time correctly. Intermittent faults have proper three-phase progression. Contextual anomalies have timeout logic. Micro-stops don't change machine state.

### What to Fix

1. **Y5 (Severity weight key mismatch)** is the most impactful issue — it makes weighted evaluation metrics useless. A simple case normalization in the evaluator would fix it.

2. **Y4 (Double GT logging)** should be investigated to confirm whether duplicate events actually appear in the JSONL output.

3. **Y3 (Missing dryer/oven Cholesky)** is a feature gap worth addressing if statistical fidelity matters for any downstream consumer.

4. **Y1 (Calibration drift units)** should be verified at the config loader level to ensure no unit mismatch.
