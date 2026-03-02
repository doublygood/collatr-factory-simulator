# Implementation Readiness Review: Data Science / Python Expert

**Reviewer:** Data Science/Python Expert (10+ years synthetic data, signal processing, statistical simulation)
**Date:** 2026-03-02
**Scope:** All 22 PRD files (~5,300 lines)
**Focus:** Signal generation engine, noise models, correlation pipeline, scenario scheduling, evaluation framework, reproducibility, performance

---

## Overall Implementation Readiness Grade: A-

## Summary

This is one of the most thoroughly specified simulation PRDs I have reviewed. The signal models are mathematically defined with formulas, parameters, ranges, and default values. The noise pipeline is explicit: generate independent samples, correlate via Cholesky, scale by sigma. The scenario system covers 17+ event types with scheduling rules. The evaluation framework defines event-level metrics with tolerance windows. Two significant gaps prevent an A or A+. First, the scenario scheduler lacks a conflict resolution specification. Second, the transport lag model (Section 4.2.8) requires a circular buffer implementation that is described in one sentence but has real complexity. Everything else is implementable by a competent Python developer with NumPy experience. The PRD is ready for implementation with the caveats documented below.

---

## Signal Model Assessment

The PRD defines 14 signal model types (Sections 4.2.1 through 4.2.14, plus the composite environmental model). I count 12 numeric models, 1 string generator, and 1 composite model built from primitives.

| Model | Implementability | Concerns |
|---|---|---|
| **Steady State with Noise** (4.2.1) | Straightforward | The within-regime drift adds an Ornstein-Uhlenbeck layer. Formula is correct. The `sqrt(dt)` scaling is right for Wiener increments. The mean-reversion term `-reversion_rate * drift_offset * dt` is standard OU. No issues. Calibration drift is a simple linear accumulator. Clean. |
| **Sinusoidal with Noise** (4.2.2) | Straightforward | Standard. The composite environmental model stacks three layers: daily sine, HVAC bang-bang, and Poisson perturbations. Each layer uses an existing model primitive. The perturbation decay via first-order lag needs its own state variable per active perturbation. A perturbation list with exponential decay is the right structure. |
| **First-Order Lag** (4.2.3) | Straightforward | The discrete update `value += (setpoint - value) * (1 - exp(-dt/tau))` is the exact-discretization form. Numerically stable for all positive dt and tau. The second-order response extension is well specified: the damped sinusoidal formula is correct, with amplitude reset on each setpoint change. One subtlety: the PRD says the implementation resets `t` to zero on setpoint change. This means maintaining a per-signal `time_since_last_setpoint_change` variable. Clear enough. |
| **Ramp Up/Down** (4.2.4) | Straightforward | The step quantization layer is well specified. Steps, dwell times, overshoot with decay. The dwell times are drawn from a uniform distribution, which means the total ramp duration is stochastic. The PRD specifies `ramp_up_seconds` as the total duration, but if each step has a random dwell, the sum may exceed the target duration. **Ambiguity:** does the ramp duration cap the total, or does each step dwell independently? The PRD does not resolve this. Assume the total ramp duration is the sum of step dwells and the `ramp_up_seconds` parameter sets the mean. |
| **Random Walk with Mean Reversion** (4.2.5) | Straightforward | Standard Ornstein-Uhlenbeck. The formula omits `sqrt(dt)` on the noise term but includes `* dt` on the delta. This is the Euler-Maruyama discretization with the noise scaled by `drift_rate` rather than `drift_rate * sqrt(dt)`. At fixed dt (100ms ticks), this is fine. At variable dt or under time compression, the drift magnitude changes with tick size. **The PRD explicitly warns about this in Section 4.1 principle 5.** The implementer must use simulated dt, not wall-clock dt. Correct. |
| **Counter Increment** (4.2.6) | Straightforward | Integer accumulator. Rollover is a modulo operation. Reset on job change requires listening to the scenario engine. The `max_before_reset` parameter adds operator-reset simulation. Simple. |
| **Depletion Curve** (4.2.7) | Straightforward | Linear depletion proportional to a counter delta. Refill is a threshold trigger with a value jump. The refill delay needs a state variable (depleted but not yet refilled). Straightforward. |
| **Correlated Follower** (4.2.8) | Moderate complexity | The static follower is trivial: `base + factor * parent + noise`. The transport lag is harder. Dynamic lag that changes every tick based on line speed requires a circular buffer or delay line that resamples. At zero speed, the PRD says "freeze downstream signal at its last value." This is correct but the buffer must handle the transition from moving to frozen and back. **Implementation detail needed:** buffer length must accommodate the maximum lag at minimum nonzero speed. At 50 m/min with 5m distance, lag is 6 seconds = 60 ticks at 100ms. At 400 m/min, lag is 0.75s = 7.5 ticks. A 120-tick ring buffer is safe. The PRD does not specify the buffer size. |
| **State Machine** (4.2.9) | Straightforward | Transition table with triggers, probabilities, and duration constraints. Standard finite state machine. The `min_duration` / `max_duration` on transitions prevents rapid cycling. The PRD defines state enums for press, coder, mixer, oven, filler, and CIP. All complete. |
| **Thermal Diffusion (Sigmoid)** (4.2.10) | Moderate complexity | The truncated Fourier series is mathematically correct. The convergence criterion ("sum terms until T(0) falls within 1C of T_initial") is practical. At T_oven=180, T_initial=4, the difference is 176C. Three terms give T(0)=15.8C, which is 11.8C off. Need ~30 terms. The coefficients `8/((2n+1)^2 * pi^2)` decrease fast enough that 50 terms is the practical ceiling. **Numerical concern:** at large t (product fully cooked), all exponential terms vanish. The formula reduces to T(t) = T_oven. No instability. At small t (initial condition), many terms contribute. The alternating-sign property of the Fourier series means partial sums oscillate around T_initial. More terms reduce the oscillation. This is Gibbs-like behavior at t=0 but the convergence check handles it. **One concern:** the model resets T_initial each time a new product enters the oven. The reset trigger is "driven by belt speed and oven length." The PRD does not specify the oven length parameter. It specifies belt speed (0.5-5.0 m/min) but not oven tunnel length. Without tunnel length, dwell time cannot be computed. |
| **Bang-Bang with Hysteresis** (4.2.12) | Straightforward | On/off controller with dead band. The sawtooth pattern is deterministic given the cooling rate, heat gain rate, and dead band. Simple state toggle. One subtlety: the cooling and heat gain rates should depend on the temperature difference from ambient, not be constant. The PRD uses constant rates, which is a simplification. Acceptable for a demo simulator. |
| **Sensor Quantization** (4.2.13) | Trivial | Round to nearest multiple. One line of code. |
| **String Generator** (4.2.14) | Trivial | Python string formatting. Sequence counter with daily reset. |

### Signal Model Verdict

11 of 14 models are unambiguous and implementable as written. Two models have minor ambiguities (ramp duration semantics, oven tunnel length for thermal diffusion reset). One model (correlated follower with transport lag) requires a non-trivial ring buffer implementation that the PRD mentions but does not fully specify.

---

## Noise and Correlation Pipeline Assessment

### Noise Distribution Models

The three noise distributions (Gaussian, Student-t, AR(1)) are well specified in Section 4.2.11.

**Gaussian.** `sigma * N(0,1)`. No issues.

**Student-t.** `sigma * T(df)`. The PRD correctly notes that Student-t variance is `sigma^2 * df/(df-2)`, making the RMS noise higher than Gaussian at the same sigma. The decision not to normalize is documented and intentional. Use `numpy.random.Generator.standard_t(df)` scaled by sigma. NumPy's implementation is numerically stable for df >= 1. At df=5 (vibration default), no issues.

**AR(1).** `noise_t = phi * noise_{t-1} + sigma * sqrt(1 - phi^2) * N(0,1)`. The scaling factor `sqrt(1 - phi^2)` preserves marginal variance at sigma^2. This is the standard AR(1) formulation. **State management:** each AR(1) signal needs a persistent `noise_previous` state variable. At phi=0.99, the autocorrelation decays slowly. This is intentional for PID loops. **Numerical concern at phi close to 1:** `sqrt(1 - phi^2)` approaches zero. At phi=0.99, the factor is 0.141. At phi=0.999, it is 0.045. The innovation term becomes tiny and the process becomes nearly deterministic. The PRD caps phi at 0.99. Safe.

**Speed-dependent sigma.** `effective_sigma = sigma_base + sigma_scale * abs(parent_value)`. Linear in parent value. Simple. Requires reading the parent signal from the store each tick. The pipeline order (Section 4.3.1) correctly applies speed-dependent scaling after Cholesky correlation.

### Cholesky Correlation Pipeline

Section 4.3.1 specifies the pipeline:

1. Generate N independent N(0,1) samples.
2. Multiply by Cholesky factor L: `correlated = L @ independent`.
3. Scale each component by its effective sigma.

This is correct. The order preserves correlations because scaling is diagonal. The correlation matrices are small (3x3) and their Cholesky decomposition is computed once at startup.

**Numerical stability of Cholesky.** The three specified matrices are:

- Vibration: smallest eigenvalue is approximately 0.65. Condition number ~2.3. Well conditioned.
- Dryer zones: smallest eigenvalue is approximately 0.88. Condition number ~1.3. Well conditioned.
- Oven zones: smallest eigenvalue is approximately 0.80. Condition number ~1.5. Well conditioned.

None of these are near-singular. `numpy.linalg.cholesky` will not fail on any of them. **Risk:** if a user configures a custom correlation matrix with correlations near 1.0 (e.g., 0.99 between two signals), the matrix may become numerically singular. The implementation should validate positive-definiteness at startup and raise a clear error. The PRD does not mention this validation. Add it.

**Interaction with non-Gaussian noise.** The Cholesky method assumes Gaussian marginals. The PRD applies Cholesky to generate correlated Gaussian samples, then scales by sigma. For signals using Student-t noise, the pipeline generates correlated Gaussian samples and then... what? The PRD says the pipeline is: generate independent, correlate via Cholesky, scale by sigma. But Student-t signals need Student-t marginals, not Gaussian.

**This is a specification gap.** The Cholesky pipeline produces correlated Gaussian noise. If vibration signals use Student-t noise AND are peer-correlated, the current pipeline produces correlated Gaussian noise, not correlated Student-t noise. Generating correlated Student-t random variables is harder. One approach: use a Gaussian copula (generate correlated Gaussians, transform each marginal to uniform via the Gaussian CDF, then invert through the Student-t CDF). The PRD does not address this interaction. For the specified use case (vibration axes with df=5 and weak correlations of 0.15-0.2), the practical difference is small. Gaussian copula with Student-t marginals would be more correct. Recommend documenting this as a known approximation or implementing the copula transform.

### Time-Varying Covariance

Section 4.3.2 specifies a multiplicative random walk on the gain parameter of correlated followers. The log-normal drift (`exp(log_drift)`) keeps the gain positive. The mean-reversion on `log_drift` pulls back toward zero. This is a standard stochastic volatility model. Clean formulation. The `sqrt(dt)` scaling on the drift term is correct for a Wiener process increment.

### Pipeline Summary

The noise/correlation pipeline is 90% unambiguous. The gap is the interaction between Cholesky (Gaussian) and Student-t marginals for peer-correlated groups. For the 3x3 matrices with weak correlations, the approximation error is small. Document it.

---

## Scenario Engine Implementation Concerns

### Scheduling

The PRD defines scenarios with frequency ranges (e.g., "3-6 per 8h shift") and duration ranges. Section 5.13 shows the YAML configuration. The scenario engine must:

1. At the start of each shift, sample how many instances of each scenario will occur.
2. Distribute their start times across the shift.
3. Ensure minimum spacing between instances.
4. Handle overlap with other scenario types.

**The PRD does not specify the scheduling algorithm.** It says the engine "can generate a random timeline from a statistical profile." It does not say how. Questions:

- How are scenario start times distributed within a shift? Uniform? Poisson process? The micro-stop scenario explicitly says "Poisson process." Other scenarios do not specify.
- What is the minimum spacing between instances of the same scenario type? If job changeovers are 3-6 per shift and each takes 10-30 minutes, what prevents two changeovers from being scheduled back-to-back?
- How do scenario durations interact with shift boundaries? Does a 90-minute bearing drift that starts 30 minutes before shift end continue into the next shift?

These are implementable decisions, but the developer must make them. Recommend: Poisson process for all scenario inter-arrival times (consistent with micro-stop), with a minimum gap equal to the scenario's minimum duration. Scenarios that cross shift boundaries continue into the next shift.

### Conflict Resolution

**The PRD does not fully specify priority rules for overlapping scenarios.** Section 5.15 says "If a micro-stop coincides with a scenario start, the scenario takes priority." This implies a priority ordering. But what about:

- A web break fires during a dryer drift. Does the dryer drift pause? Continue? Reset?
- A job changeover starts during a bearing wear ramp. The changeover sets machine state to Setup. Does bearing vibration reset to baseline?
- Two unplanned stops are scheduled 5 minutes apart. Does the second fire during the first?

The state machine provides implicit conflict resolution: if the machine is already in Fault (4), a new fault cannot fire because the transition from Fault to Fault is not defined. But dryer drift operates during Running state without changing the state machine. Two Running-state scenarios can overlap.

**Recommendation:** Define a priority ordering for all scenarios. Higher-priority scenarios preempt lower-priority ones. State-changing scenarios (web break, unplanned stop, job changeover) preempt non-state-changing scenarios (dryer drift, ink excursion, bearing wear). Non-state-changing scenarios can overlap. Document this.

### F&B Scenarios

The F&B scenarios (Section 5.14) are well specified. The batch cycle has clear phase transitions. The CIP conductivity profile (Section 4.6) is described phase by phase. The allergen changeover logic (Section 5.14.8) has a clean transition table. The cold chain break sequence is complete.

One concern: the F&B profile has multiple independent state machines (mixer, filler, oven, CIP, chiller). The PRD says each generator reads its own state from the store. Scenarios that stop production (CIP cycle, allergen changeover) must set all production equipment to idle. The CIP scenario description says "production stops" but does not list which state machines transition. Implement this as: CIP start sets mixer, filler, and sealer states to Idle. Oven enters Idle (at temperature, no product). Chiller continues independently.

### Scenario Count

I count 17 scenario types across both profiles:

Packaging: job changeover, web break, dryer drift, bearing wear, ink excursion, registration drift, unplanned stop, shift change, cold start spike, material splice, micro-stop, contextual anomaly, intermittent fault (4 subtypes).

F&B: batch cycle, oven excursion, fill weight drift, seal failure, chiller door, CIP cycle, cold chain break, allergen changeover.

Shared: shift change, cold start spike, coder depletion.

Total unique: ~20 scenario types. All have clear sequence descriptions. All are implementable.

---

## Evaluation Framework Implementation Plan

Section 12 defines a clean evaluation protocol.

**Data pipeline:** Simulator produces signal CSV/Parquet + ground truth JSONL. The evaluator loads both. It parses ground truth events into an event list with `[start, end]` windows. It loads detector alerts with timestamps. It runs the matching algorithm (detection within `[start - pre_margin, end + post_margin]`). It computes precision, recall, F1, detection latency (median and p90), severity-weighted variants, and random baseline.

**Implementation steps:**

1. Parse ground truth JSONL: extract `scenario_start` / `scenario_end` pairs. Each pair becomes an event with type, start, end, affected signals, and severity weight.
2. Parse detector output: list of `(timestamp, alert_type)` tuples.
3. Match: for each event, check if any detection falls in the effective window. Binary match. Assign ambiguous detections to nearest event by start time.
4. Compute metrics: TP, FP, FN counts. Precision, recall, F1. Per-scenario breakdown. Latency distribution. Severity-weighted variants.
5. Random baseline: compute anomaly density `p = total_anomaly_ticks / total_ticks`. Generate random alerts at probability p. Compute the same metrics.
6. Multi-seed: run N=10 seeds, compute mean and standard deviation of all metrics.

This is a standalone Python script. Maybe 300-500 lines. Straightforward. The ground truth format is well defined. The metric definitions are unambiguous.

**One gap:** The PRD defines event-level matching but does not specify how to handle overlapping events from different scenario types. If a dryer drift and an ink excursion overlap in time, and a single detection falls in the overlap, which event gets credit? The PRD says "assigned to the nearest event by start time." Good enough.

---

## Reproducibility Risks

Section 4.5 states: "With the same seed and configuration, the engine produces identical output." Section 11.6 strengthens this: "byte-identical signal sequences for the first 1 million data points."

### NumPy Random Generator

The PRD should mandate `numpy.random.Generator` (new API) with a specific BitGenerator. `numpy.random.default_rng(seed)` uses PCG64 by default. PCG64 produces identical sequences across platforms for the same seed.

**Risk 1: NumPy version.** NumPy guarantees stream compatibility within a major version. Between major versions (e.g., 1.x to 2.x), the default BitGenerator or distribution sampling algorithms may change. Pin the NumPy version in requirements.txt. This is not mentioned in the PRD.

**Risk 2: Python version.** NumPy's random generators do not depend on Python's `random` module. As long as NumPy is pinned, Python version does not affect the random stream. But if any code uses `random.uniform()` or similar, reproducibility breaks. The PRD should mandate: all randomness goes through a single `numpy.random.Generator` instance. No `random` module.

**Risk 3: Floating-point arithmetic.** IEEE 754 guarantees identical results for the same operations on the same platform. Cross-platform (x86 vs ARM), intermediate precision may differ. Extended precision on x86 (80-bit) vs strict 64-bit on ARM can produce different results for chained floating-point operations. The PRD says "byte-identical." This is achievable on the same platform. Cross-platform byte-identical output requires careful FP discipline: no extended precision, no fused multiply-add differences. Python/NumPy on x86-64 Linux is the practical target. Document this constraint.

**Risk 4: Random state branching for independent subsystems.** The PRD has 47-65 signals, each with independent noise, plus scenarios with random scheduling, plus data quality injection with random timing. If all randomness draws from a single Generator, the sequence is deterministic. But if the order of draws changes (e.g., a new signal is added), all downstream values change. The PRD should recommend a seeded-spawn approach: one root Generator spawns child Generators for each subsystem (signal noise, scenario scheduling, data quality injection). `numpy.random.SeedSequence` supports this. A new signal added to one subsystem does not affect the random stream of other subsystems.

**Risk 5: Student-t sampling.** NumPy's `standard_t(df)` uses a ratio of a standard normal and a chi-squared variate. The algorithm is deterministic for a given Generator state. No issue.

**Risk 6: Cholesky decomposition.** `numpy.linalg.cholesky` produces deterministic output for the same input matrix. The decomposition is computed once at startup. No reproducibility issue.

### Reproducibility Verdict

Byte-identical output is achievable with discipline: pin NumPy, use a single Generator (or spawned children from SeedSequence), avoid the `random` module, and constrain to one platform. The PRD should specify these requirements explicitly. The current text ("same seed and configuration produces identical output") is correct in intent but lacks the implementation constraints.

---

## Performance Estimates

### Signal Generation per Tick

At 100ms tick interval (10x mode), the engine must generate values for 47-65 signals per tick.

**Per-signal cost:**
- Steady state: 1 random draw + arithmetic. ~200ns.
- First-order lag with AR(1) noise: 1 random draw + exponential + arithmetic. ~500ns.
- Random walk: 1 random draw + arithmetic. ~200ns.
- Correlated follower: 1 read from store + arithmetic + 1 random draw. ~300ns.
- Counter: 1 read + integer add. ~100ns.
- State machine: condition checks. ~100ns.
- Thermal diffusion: 30-term series evaluation. ~2us.

**Cholesky application:** 3x3 matrix-vector multiply for each peer group. Three groups defined. ~1us total.

**Total per tick:** ~50 signals * ~400ns average = ~20us for signal generation. Plus ~3us for correlation. Plus overhead (store writes, timestamp management). Estimate: ~50-100us per tick.

**Budget at 100ms tick:** 100ms available. 0.1ms used. **Utilization: 0.1%.** Signal generation is not the bottleneck. Not even close.

### Protocol Serving

The bottleneck at 10x mode is protocol I/O, not signal generation.

- Modbus: each client read takes 1-5ms round-trip including TCP overhead. At 12 polls/second (packaging), this is ~12-60ms of Modbus I/O per second. `pymodbus` async server handles this easily.
- OPC-UA: subscription updates at 100ms intervals for the fastest signals. `asyncua` publishes data change notifications. At 8 data changes/second, this is negligible.
- MQTT: 5 messages/second. Each JSON serialize + publish is ~100us. Total ~500us/second.

**Total I/O budget:** <100ms per second at 10x mode. The asyncio event loop has margin.

### Batch Mode (100x+)

At 100x, the engine generates 10 ticks per wall-clock millisecond. Signal generation at ~100us per tick means ~1ms per 10 ticks. The engine can sustain 1000 ticks per second on one core. At 100ms simulated interval, 1000 ticks covers 100 seconds of simulated time per wall-clock second. That is 100x. This matches the PRD's target.

At 1000x: 10,000 ticks per second. Still ~10ms of compute. Easy.

**The hot loop** is the tick function: generate all signals, update store, check scenarios, log ground truth. Profile this first. The Cholesky multiply and thermal diffusion series are the most expensive per-tick operations, but both are sub-microsecond at these matrix sizes and term counts.

### Memory

47 signals at 100ms for 24 hours = 47 * 864,000 = 40.6M data points. At 8 bytes each (float64) plus 8 bytes timestamp = ~650 MB if stored in memory.

**But the signal store only holds the current value.** The store is a dictionary of current values, not a time series buffer. Memory is O(N_signals), not O(N_signals * N_ticks). The store uses ~47 * 100 bytes = ~5 KB. Negligible.

For batch output, data writes to CSV/Parquet on disk, not memory. The Parquet writer can flush periodically. Memory is bounded.

The transport lag ring buffer for the correlated follower model adds ~120 ticks * 8 bytes per lagged signal. A few kilobytes total.

**Memory verdict:** No concerns. RSS will be dominated by Python interpreter overhead, NumPy, and protocol library buffers. Estimate 100-200 MB total.

---

## Batch Generation: Data Volume Estimates

A 7-day simulation at 100x with 47 packaging signals:

- Signal data points: 47 signals * variable rates. Average aggregate rate: ~3 samples/second (from Section 2.11). Over 7 days: 3 * 86400 * 7 = 1,814,400 data points.
- Per data point in CSV: ~40 bytes (timestamp + signal_id + value + quality). Total: ~72 MB.
- Per data point in Parquet (columnar, compressed): ~10 bytes effective. Total: ~18 MB.

For the F&B profile (65 signals, ~4 samples/second aggregate):
- Data points: 4 * 86400 * 7 = 2,419,200.
- CSV: ~97 MB. Parquet: ~24 MB.

**Ground truth log:** At ~50-100 events per simulated day (scenario starts, ends, state changes), 7 days produces ~350-700 JSONL lines. Negligible size.

**Wall-clock time:** 7 simulated days at 100x = 7 * 86400 / 100 = 6,048 seconds = ~100 minutes. The PRD says "under 2 hours." Consistent.

**For 100 runs (multi-seed benchmarking):** 100 * 18 MB = 1.8 GB Parquet. Manageable.

---

## Missing Specifications

| What is Missing | Severity | Section | Impact |
|---|---|---|---|
| **Scenario scheduling algorithm** (Poisson vs uniform, minimum spacing, shift boundary handling) | High | 5.13 | Developer must make design decisions without spec. Different choices produce different scenario densities and overlap patterns. |
| **Scenario conflict/priority rules** (what happens when two scenarios overlap) | High | 5.x | Without rules, overlapping scenarios produce undefined signal behavior. The developer will invent rules. They may not match intent. |
| **Oven tunnel length** for thermal diffusion model reset timing | Medium | 4.2.10 | Cannot compute product dwell time without tunnel length. Belt speed alone is insufficient. |
| **Ramp duration semantics** with step quantization (total vs sum of dwells) | Medium | 4.2.4 | Developer must decide whether ramp_up_seconds is a hard cap or a mean. |
| **Cholesky + Student-t interaction** for peer-correlated non-Gaussian signals | Medium | 4.3.1, 4.2.11 | Correlated Gaussian samples scaled to Student-t sigma do not have Student-t marginals. The approximation is adequate but should be documented. |
| **Reproducibility implementation constraints** (pin NumPy, no random module, SeedSequence branching, platform constraint) | Medium | 4.5, 11.6 | "Byte-identical" requires explicit discipline. Current spec is intent only. |
| **Transport lag buffer size** and zero-speed-to-nonzero transition handling | Medium | 4.2.8 | Ring buffer implementation needs max-lag calculation and freeze/thaw logic. |
| **CIP production stop cascade** (which F&B state machines transition to idle) | Low | 5.14.6 | Implicit from context but not enumerated. |
| **Positive-definiteness validation** for user-configured correlation matrices | Low | 4.3.1, Appendix D | A bad matrix crashes the Cholesky decomposition with a cryptic LinAlgError. |
| **Event-driven signal timing** in batch mode (how do event-rate signals like `press.machine_state` emit in CSV/Parquet) | Low | 4.4, 12.2 | Event-driven signals do not have a fixed tick. In batch mode CSV, are they written on every tick (repeating the last value) or only on change? |
| **Fill weight as per-item event vs continuous signal** | Low | 4.6 | The F&B filler produces one fill_weight per pack (event-level). The packaging signals are continuous. The signal store holds "current value." For fill_weight, is "current" the last fill, or the running mean? |
| **1/f noise** acknowledged as missing | Info | 4.2.11 | Documented as a known limitation. Acceptable for the stated use case. |

---

## Verdict

**Ready to implement.** The two high-severity gaps (scenario scheduling algorithm and conflict resolution) are design decisions, not specification errors. A senior developer can make these decisions in a few hours. Everything else is either unambiguous or has a clear path to resolution.

The signal models are mathematically correct. The noise pipeline is specified in the right order. The correlation matrices are well conditioned. The evaluation framework is clean and implementable. Performance is not a concern at any time scale. Memory is not a concern. Disk output for batch mode is modest.

The PRD author understands the numerical pitfalls. Section 4.1 principle 5 explicitly warns about `sqrt(dt)` scaling under time compression. Section 4.2.11 documents the Student-t variance inflation. Section 4.3.1 specifies the correct pipeline order (correlate then scale). These are the mistakes that junior developers make and that take days to debug. The PRD prevents them.

Start building. Address the high-severity gaps in a design document before writing the scenario scheduler. Pin NumPy in requirements.txt. Use `numpy.random.default_rng(seed)` with `SeedSequence` for subsystem isolation. Validate correlation matrices at startup. Add the oven tunnel length parameter. The rest is execution.
