# Data Generation Engine: Combined Expert Review

**Date:** 2 March 2026
**Sections reviewed:** 01, 02, 02b, 03, 03a, 04, 10
**Requested by:** Lee
**Compiled by:** Dex

---

## Reviewer Profiles

### Reviewer A: Senior Industrial Automation Engineer

20+ years integrating PLCs, SCADA systems, and industrial IoT platforms in UK manufacturing. Deep hands-on experience with Siemens S7, Allen-Bradley, Eurotherm controllers, Modbus TCP, OPC-UA, and MQTT in real factory deployments. Extensive work in packaging/printing (flexographic presses, laminators) and food and beverage (ready meal lines, CIP systems). Evaluates from the perspective of someone who has wired, commissioned, and maintained this exact equipment on real factory floors.

### Reviewer B: Industrial Data Scientist

15+ years building predictive maintenance models, digital twins, and synthetic data generators for manufacturing. Published research on industrial signal generation. Worked with benchmark datasets including SKAB, DAMADICS, NASA bearing run-to-failure, MetroPT, and Paderborn bearing. Evaluates from the perspective of statistical rigour, synthetic data quality, and whether the output would fool someone who works with real industrial time series every day.

---

## Overall Grades

| Reviewer | Grade | One-Line Justification |
|---|---|---|
| Automation Engineer | **B+** | Sound industrial knowledge, accurate correlations, three fixable blockers |
| Data Scientist | **B-** | Strong architecture, weak statistical detail in generators |

---

## 1. Signal Model Accuracy

### Automation Engineer Assessment

The nine signal models cover the primary behaviours on a packaging or food line. Steady state, first-order lag, ramp, and counter are the workhorses. The random walk with mean reversion is a good choice for ink viscosity and registration error. The correlated follower handles the obvious parent-child relationships.

**PID overshoot and oscillation.** The first-order lag model tracks a setpoint with exponential decay. Real Eurotherm controllers produce overshoot on step changes. Underdamped loops produce decaying oscillation. The PRD mentions an "overshoot_factor" parameter but does not define the oscillatory component. A real dryer zone recovering from a door-open event or setpoint change does not follow a pure exponential. It rings. A second-order model (damping ratio + natural frequency) would fix this. Anyone who has tuned a Eurotherm will notice the absence.

**Dead band and hysteresis.** Real temperature controllers have configurable dead bands. The compressor cycling in the chiller section describes this behaviour but no signal model supports it. A bang-bang controller model (on/off with hysteresis) is needed for the chiller compressor at minimum. The state machine model can approximate it, but a dedicated hysteresis model would be cleaner.

**Verdict:** The nine models are sufficient for a first release. Second-order response is the most important gap. Nice-to-have for Phase 1, blocker for Phase 2 if the simulator is meant to impress engineers at prospect sites.

### Data Scientist Assessment

The PRD specifies Gaussian noise for every analog signal. This is wrong for many industrial sensors.

Real accelerometers produce noise with heavy tails. Mechanical impulses (bearing spalls, gear mesh events) create outliers that a Gaussian model cannot produce. The Paderborn bearing dataset and IMS/NASA run-to-failure data both show kurtosis well above 3 in vibration channels. A Gaussian generator will never produce the isolated spikes that real vibration data shows between anomaly events.

Pressure signals like `coder.ink_pressure` may have skewed distributions during transient states. Fill weight distributions on the F&B line are Gaussian by design (central limit theorem applies to multi-head weighers), so that choice is correct for `filler.fill_weight`.

Temperature signals from Eurotherm controllers have correlated noise from the PID control loop. The noise is not independent sample to sample. It has autocorrelation structure at the control loop frequency. A white Gaussian model misses this entirely.

**Recommendation:** Add a configurable noise distribution parameter per signal. Support at minimum: Gaussian (default), Student-t (heavy tails, configurable degrees of freedom), and first-order autoregressive noise (AR(1) with configurable coefficient). Student-t with 3-5 degrees of freedom produces realistic outlier rates for vibration and pressure. AR(1) noise produces the autocorrelated residuals that real PID-controlled temperatures exhibit.

**Verdict:** Nice-to-have for integration testing and demos. Blocker if the simulator evaluates anomaly detection algorithms. Gaussian-only noise makes anomaly detection trivially easy because real outliers never appear in the normal baseline.

### Combined Signal Model Grade: B-

---

## 2. Correlation Model Realism

### Automation Engineer Assessment

The state transition cascade in Section 4.3 is good. It captures the main chain: state drives speed, speed drives current, tension fluctuates during ramp, registration error spikes during acceleration. This matches what happens on a real CI flexo press.

**Correct correlations:**
- Line speed ramp causing registration error increase: correct. The anilox roller and impression cylinder take time to synchronise. Registration wanders during acceleration.
- Web tension spike then drop on web break: correct. Tension spikes because the web stops moving at the break point while the rollers keep turning. Then it drops to zero.
- Dryer zones pre-heated during Setup: correct. No press operator starts a run with cold dryers.
- Coder following press state: correct. The coder waits for the press to be running before it prints.

**Missing correlations:**

**Ink viscosity and dryer temperature interaction.** The PRD describes temperature-viscosity coupling through ambient temperature. This is incomplete. On a real press, the dryer zones radiate heat onto the web, which heats the ink on the anilox roller. A dryer running at 120C in zone 1 raises ink temperature faster than ambient alone. The dryer-to-ink coupling is stronger than the ambient-to-ink coupling.

**Unwind tension compensation.** As unwind_diameter decreases, the press tension controller compensates by adjusting the unwind brake torque. A fresh roll at 1500mm diameter has different inertia characteristics than a near-empty roll at 100mm. The PRD has diameter depleting but does not describe the effect on web_tension.

**Nip pressure and impression quality.** Nip pressure on a CI press directly affects print density and dot gain. When the operator changes substrate thickness, nip pressure must be re-set. Not described.

### Data Scientist Assessment

The PRD describes a cascade model. Machine state drives line speed. Line speed drives motor current, web tension, energy, and downstream signals. This is a tree structure. The approach is sound for the primary causal chain. Three gaps exist.

**No lagged cross-correlations.** The correlated follower has an optional `lag` parameter. But the cascade description does not specify lag values for most relationships. Material takes time to travel from the press to the laminator. A web tension disturbance at the press appears at the laminator 2-10 seconds later depending on speed. The lag should be `distance / line_speed`, not a fixed constant.

**No residual correlations.** After removing state machine and parent-child effects, real signals still show residual correlations. The three dryer zones share thermal mass. The three vibration axes share mechanical coupling. These are peer correlations. The engine has no general mechanism for peer coupling except the F&B oven thermal coupling factor (0.05 between adjacent zones). This should be generalised.

**No time-varying covariance.** The relationship between motor current and speed is not constant. It changes with load, temperature, and wear. A fixed linear transform (`current = base + k * speed`) produces correlation that is too clean. Real scatter plots of current vs speed show a cloud that widens at higher speeds and shifts over days.

**Recommendation:** Quantify transport lags as a function of line speed. Add peer correlation support for sensor groups sharing physical coupling (e.g., a 3x3 mixing matrix with off-diagonal terms of 0.1-0.3 for vibration axes). Add slow multiplicative drift to the gain parameter of correlated followers.

### Combined Correlation Grade: B

---

## 3. Parameter Values

### Automation Engineer Assessment

**Line speed range (0-400 m/min).** Correct for a CI flexo press. Modern BOBST or W&H presses run up to 450-500 m/min on film, but 400 is a reasonable upper bound. The default operating range of 120-250 m/min is realistic for typical commercial packaging.

**Ramp duration (0 to target over 2-5 minutes).** Correct. A flexo press accelerates slowly. Two to five minutes from threading speed to production speed is right.

**Dryer temperatures (40-120C).** Correct for water-based and solvent-based ink drying on a packaging press.

**Dryer time constant (30-120 seconds).** Realistic. Thirty seconds is fast (small zone, gas-fired). One hundred twenty seconds is slow (large zone, electric).

**Ink viscosity (15-60 seconds Zahn cup).** Correct. Flexographic water-based inks typically run 18-25 seconds on a Zahn #2 cup.

**Registration error (+/- 0.5 mm).** Correct. Modern presses hold within +/- 0.1mm steady state. The +/- 0.5mm range covers startup transients.

**Web tension (20-500 N).** Correct. Film at 20-80N, paper and board at 100-500N.

**BLOCKER: Coder ink pressure target is wrong.** The PRD text says target ~1500 mbar in section 4, but reference data says 830-840 mbar and section 2.5 says range 0-900 mbar. Someone copied the wrong number. Fix to: target ~835 mbar, sigma ~60 mbar, range 0-900 mbar.

**Vibration (3-8 mm/s RMS normal running).** Correct per ISO 10816 Class III. Well-balanced motor at 3-5 mm/s, 8 mm/s prompts investigation.

**Oven temperatures (80-280C).** For ready meals, 160-200C is typical production. The 280C upper bound is high for chilled food but covers bakery. Acceptable.

**Belt speed (0.5-5.0 m/min).** Correct for a tunnel oven. Dwell time of 8-25 minutes at these speeds matches real production.

### Combined Parameter Grade: B+ (one blocker: ink pressure typo)

---

## 4. Time Compression

### Automation Engineer Assessment

The approach is sound. Advancing the simulation clock faster and publishing more frequently is the right design.

**Counter overflow at 100x.** Bounded counters like waste_count (range 0-99,999) will hit their ceiling in about 8 real minutes at 100x. Define reset behaviour for bounded counters under time compression.

**First-order lag at 100x.** A dryer with tau=120s settles in 1.2 real seconds at 100x. About 24 data points during the transient. Enough for the shape to be visible. No problem.

**BLOCKER: Random walk at 100x.** If the implementation uses real-time dt rather than simulated dt, the walk will move 10x too fast (sqrt of 100x scaling). The PRD must specify that all models use simulated time, not wall-clock time. This is probably the intent but it is not stated explicitly.

**Protocol throughput at 100x.** A 1-second signal becomes a 10ms poll interval. With 4 Modbus connections and 50-200ms response latencies, the client cannot keep up. The PRD acknowledges batching but does not define the mechanism. Define how Modbus serves compressed-time data.

### Combined Time Compression Grade: B (one blocker: simulated vs wall-clock time)

---

## 5. Synthetic Data Detectability

### Data Scientist Assessment

If a domain expert examines 24 hours of output on a time series chart, several things will give it away.

**Tell 1: Too-clean transitions.** The ramp-up model produces a smooth linear ramp from 0 to target speed. Real press startups are jerky. The operator adjusts speed in steps. The drive controller overshoots slightly. A real speed ramp has 3-5 visible "steps" and small overshoots at each step.

**Tell 2: Noise is too uniform.** Gaussian noise with constant sigma produces a constant-width band. Real noise characteristics change with operating conditions. Vibration noise is lower at low speed and higher at high speed. Temperature noise increases during transients. Constant-sigma noise creates an unnaturally uniform envelope.

**Tell 3: No micro-stops.** Real production lines have frequent micro-stops lasting 5-30 seconds. A web break sensor false-triggers. An operator pauses to inspect a print. A splice passes through. These happen 10-50 times per shift. The state machine has no mechanism for micro-stops within Running state.

**Tell 4: Perfect periodicity in environmental signals.** The sinusoidal model for ambient temperature produces a mathematically perfect sine wave. Real ambient temperature has weather-driven irregularity, HVAC cycling, and door-open step changes.

**Tell 5: No measurement quantisation.** Real sensors have finite resolution. A 12-bit ADC on a 0-50 mm/s range gives 0.012 mm/s steps. The simulator produces continuous floating-point values.

**Mitigation recommendations:**
- Replace linear ramps with step-wise ramps (3-5 steps with small overshoots)
- Make noise sigma a function of operating state or parent signal level
- Add micro-stop events as a configurable Poisson process within Running state
- Add HVAC cycling and random perturbations to the environmental sine wave
- Add optional quantisation (configurable bit depth per signal)

### Combined Detectability Grade: C+ (step-wise ramps and micro-stops are demo blockers)

---

## 6. F&B Profile Gaps

### Automation Engineer Assessment

**Recipe-driven setpoint changes.** A recipe change on the oven means new zone setpoints, new belt speed, new filler target weight. The data generation engine needs a mechanism to accept a batch of setpoint changes atomically. If setpoints change one at a time, the oven model will show zone 1 at the new temperature while zone 2 is still at the old temperature. In real life the delay between setpoint changes on a Eurotherm is milliseconds (batch write), not seconds.

**CIP chemical concentration curves.** The PRD says conductivity during caustic wash rises to 80-150 mS/cm and during rinse drops below 5 mS/cm. Real CIP conductivity during rinse follows an exponential decay, not a linear ramp. The first-order lag model can handle this if the "setpoint" is 0 mS/cm and tau is 30-60 seconds. This should be stated explicitly.

**BLOCKER: HACCP critical control points.** Product core temperature (oven.product_core_temp) is the most important signal on a food line. BRC requires documented evidence that product reached 72C for 2 minutes. The data generation engine must produce a realistic core temperature profile: cold product enters, core temp rises following a thermal diffusion curve (not first-order lag), reaches target, holds. Thermal diffusion in a solid food product has a characteristic S-shape (slow start, rapid middle, slow approach to equilibrium). If the product core temperature looks wrong, no food manufacturing prospect will take the demo seriously.

**Batch ID generation.** The mixer has a batch_id signal (string type). None of the nine signal models produce strings. A simple format template would suffice (e.g., date code + line number + sequential batch: "260302-L1-007").

**Allergen changeover.** When a food line changes from a recipe containing allergens to one that does not, a full CIP cycle is mandatory. The correlation between recipe change and CIP is not described.

### Combined F&B Grade: B- (one blocker: product core temperature model)

---

## 7. Anomaly Injection Quality

### Data Scientist Assessment

The state machine and scenario system handle anomaly patterns well. The state cascade for a web break fault is well designed with multi-signal correlated response. The F&B scenarios describe gradual degradation patterns. The following anomaly types are missing or underspecified.

**Missing: Contextual anomalies.** A temperature of 42C is normal during Running state. The same 42C during Off state is anomalous. The state machine enables this distinction, but the PRD does not describe injecting contextual anomalies where a signal value is normal in one context and anomalous in another. This is the hardest class of anomaly for detection algorithms.

**Missing: Collective anomalies.** A sequence of individually normal values that forms an anomalous pattern. For example, `press.registration_error_x` oscillating at a specific frequency for 30 seconds (normal values, abnormal temporal pattern). No mechanism for injecting temporal pattern anomalies.

**Missing: Concept drift.** The relationship between signals gradually changes. Motor current at a given speed slowly increases over weeks as bearings wear. Partially covered by degradation scenarios, but not described as a change in the inter-signal relationship.

**Missing: Intermittent faults.** A fault that appears, disappears, and reappears over days before becoming permanent. Real bearing faults show intermittent vibration spikes for weeks before continuous elevation. The state machine supports this through probabilistic transitions, but no scenario describes the intermittent pattern explicitly.

**Missing: Partial observability.** A real fault affects 5 signals, but one sensor is offline due to a communication drop. CollatrEdge must detect the anomaly from 4 of 5 expected signatures. The PRD does not describe combining anomaly injection with communication drops.

**Recommendation:** Add contextual anomaly injection. Add collective anomaly injection for temporal patterns. Describe intermittent fault scenarios explicitly. Consider combining anomaly injection with data quality injection for partial observability testing.

### Combined Anomaly Grade: B- (contextual anomalies and intermittent faults are benchmarking blockers)

---

## 8. Data Quality Realism

### Data Scientist Assessment

Section 10 is strong. Communication drops, configurable noise, counter rollovers, duplicate timestamps, Modbus exceptions, timezone offsets, and stale values. This covers the major industrial data quality issues.

**Missing: Value clamping and saturation.** Real sensors clip at range limits. A temperature sensor rated for 0-120C reports exactly 120.0 when actual temperature is 125C. The PRD mentions `min_clamp` and `max_clamp` but does not describe saturated output behaviour.

**Missing: Sensor drift.** A thermocouple that reads 0.5C high after a year. A slow, persistent bias accumulating over weeks. Not a sudden failure. Not described in section 10 as distinct from signal model drift.

**Missing: Out-of-range sentinel values.** The reference data showed 6553.5 (uint16 max / 10) when a temperature sensor was disconnected. PLCs report specific sentinel values for sensor faults. The simulator should inject sensor disconnect events with appropriate sentinel values.

**Missing: Network-level data quality.** Modbus TCP frames out of order. Transaction ID mismatches. Partial responses (fewer registers than requested). Rare but real TCP-level issues.

### Combined Data Quality Grade: B+ (sentinel values are a blocker given the reference data documents this pattern)

---

## 9. Reproducibility and Evaluation

### Data Scientist Assessment

Random seed reproducibility is necessary but not sufficient.

**BLOCKER: No ground truth labels.** The engine injects anomalies but does not output a ground truth file saying "anomaly X started at T1, ended at T2, affected signals S1-S5, root cause was bearing degradation." Without ground truth labels, you cannot compute precision, recall, or F1 for anomaly detection. Every serious benchmark (SKAB, NAB, Exathlon, MetroPT) includes ground truth labels.

**No evaluation protocol.** How many scenarios to run? What ratio of normal to anomalous data? What evaluation metrics to report? Not defined.

**No baseline comparison.** No mention of generating a "clean" dataset alongside an "impaired" dataset for ablation studies.

**Recommendation:** Add a ground truth event log emitted alongside the data stream. Format: JSONL with start time, end time, event type, severity, affected signals, injected parameters. This transforms the simulator from a demo tool into a benchmarking platform.

### Combined Reproducibility Grade: C (ground truth labels are a benchmarking blocker)

---

## 10. Network Topology Impact

### Automation Engineer Assessment

Section 3a describes a realistic multi-controller architecture. The data generation engine does not reference it. This is a gap.

**Clock synchronisation.** Each PLC has its own clock. Clock drift between PLCs of 1-5 seconds is common. Between a Siemens PLC and a Eurotherm controller, drift of 10+ seconds is normal. The data generation engine uses a single simulation clock. The protocol layer should add per-controller timestamp offsets.

**Scan cycle phase differences.** Press PLC at 10ms, laminator at 20ms, Eurotherm at 100ms. These are not phase-locked. The effect is small (tens of milliseconds) and only matters for sub-second correlation analysis.

**Independent controller failure.** Section 3a.5 describes independent connection drops. The data generation engine does not specify what happens to signal generation when a controller is "down." Do signals freeze at last value? Continue generating internally? Reset to zero? This matters because CollatrEdge will see stale data, then a jump to current values on reconnect.

### Combined Network Topology Grade: B (no blockers, clock drift is highest value addition)

---

## Consolidated Blocker List

| # | Issue | Reviewer | Status | Commit |
|---|---|---|---|---|
| 1 | Ink pressure target wrong (1500 vs 835 mbar) | Automation | **RESOLVED** | `036ea3f` |
| 2 | Specify simulated time, not wall-clock time, for all models | Automation | **RESOLVED** | `036ea3f` |
| 3 | Product core temperature needs thermal diffusion model (S-curve) | Automation | **RESOLVED** | `036ea3f` |
| 4 | Step-wise ramps, not smooth linear (3-5 steps with overshoots) | Data Sci | **RESOLVED** | `036ea3f` |
| 5 | Micro-stops (5-30s pauses, 10-50 per shift, Poisson process) | Data Sci | **RESOLVED** | `036ea3f` |
| 6 | Ground truth event log (JSONL with anomaly annotations) | Data Sci | **RESOLVED** | `036ea3f` |
| 7 | Within-regime drift for long production runs | Data Sci | **RESOLVED** | `036ea3f` |
| 8 | Sentinel values for sensor disconnects (6553.5 pattern) | Data Sci | **RESOLVED** | `036ea3f` |

### Conditional Blockers (depending on use case)

| # | Issue | Condition | Status | Commit |
|---|---|---|---|---|
| 9 | Configurable noise distributions (Student-t, AR(1)) | Blocker if evaluating anomaly detection | **RESOLVED** | `f0d3240` |
| 10 | Contextual anomalies and intermittent faults | Blocker if benchmarking | **RESOLVED** | `f0d3240` |

---

## Consolidated Nice-to-Have List

### Phase 1 (High Value)

| # | Issue | Reviewer | Status | Commit |
|---|---|---|---|---|
| 11 | Second-order response (overshoot + oscillation) for temperature controllers | Automation | **RESOLVED** | `1c20824` |
| 12 | Stuck sensor / frozen value fault injection | Automation | **RESOLVED** | `1c20824` |
| 13 | CIP conductivity decay as first-order lag toward zero (explicit) | Automation | **RESOLVED** | `1c20824` |
| 14 | Dead band / hysteresis model for compressor cycling | Automation | **RESOLVED** | `1c20824` |
| 15 | Counter reset behaviour under time compression | Automation | **RESOLVED** | `1c20824` |
| 16 | Speed-dependent noise sigma | Data Sci | **RESOLVED** | `1c20824` |
| 17 | HVAC cycling and random perturbations on environmental signals | Data Sci | **RESOLVED** | `1c20824` |
| 18 | Exponential/logistic vibration degradation curve (not linear) | Data Sci | **RESOLVED** | `1c20824` |
| 19 | Lagged cross-correlations as function of line speed | Data Sci | **RESOLVED** | `1c20824` |
| 20 | Explicit controller-down signal behaviour | Automation | **RESOLVED** | `1c20824` |

### Phase 2 (Lower Priority)

| # | Issue | Reviewer | Status | Commit |
|---|---|---|---|---|
| 21 | Sensor quantisation step after noise generation | Both | **RESOLVED** | `80e4c55` |
| 22 | Long-term sensor calibration drift | Both | **RESOLVED** | `80e4c55` |
| 23 | Per-controller clock drift offsets | Automation | **RESOLVED** | `80e4c55` |
| 24 | Batch ID string generator | Automation | **RESOLVED** | `80e4c55` |
| 25 | Atomic setpoint batch writes for recipe changes | Automation | **RESOLVED** | `80e4c55` |
| 26 | Peer correlation mixing matrices (vibration axes, dryer zones) | Data Sci | **RESOLVED** | `80e4c55` |
| 27 | Time-varying covariance on correlated followers | Data Sci | **RESOLVED** | `b3f11c7` |
| 28 | Evaluation protocol document | Data Sci | **RESOLVED** | `b3f11c7` |
| 29 | Clean/impaired dataset pairing for ablation studies | Data Sci | **RESOLVED** | `b3f11c7` |
| 30 | Allergen changeover triggering mandatory CIP | Automation | **RESOLVED** | `b3f11c7` |
| 31 | Partial Modbus responses (fewer registers than requested) | Data Sci | **RESOLVED** | `b3f11c7` |
| 32 | PLC scan cycle artefacts and phase jitter | Automation | **RESOLVED** | `b3f11c7` |
