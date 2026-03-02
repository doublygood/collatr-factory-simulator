# Data Generation Engine: Round 3 Expert Review

**Date:** 2 March 2026
**Reviewer:** Industrial Data Scientist (Reviewer B)
**Sections reviewed:** All 22 files (5,275 lines)
**Context:** Final sign-off review. Round 1 grade: B-. Round 2 grade: A-. All 27 consolidated issues resolved.

---

## Overall Grade: A

This PRD is implementation-ready. The team resolved all 27 issues from the Round 2 consolidated list. They resolved them correctly. The Cholesky decomposition replaces the broken mixing matrix. The multi-term Fourier series fixes the thermal diffusion initial condition. The evaluation protocol now includes tolerance windows, multi-seed significance testing, severity-weighted metrics, per-scenario latency targets, and a random baseline. The Student-t variance inflation is documented as intentional. The AR(1) state continuity during connection drops is specified. The signal generation pipeline order is locked down. Two minor issues prevent an A+. Neither blocks implementation.

---

## Round 2 Fix Verification

I verified each of the 27 issues against the current text. The table below records the result.

| # | Issue | Severity | Status | Notes |
|---|-------|----------|--------|-------|
| H1 | Oven setpoint register addresses (HR 1120-1125 vs 1110-1115) | High | FIXED | Appendix A now shows HR 1110-1115 for setpoints. Section 3 matches. Consistent. |
| H2 | MQTT topic prefix mismatch (packaging1 vs line3) | High | FIXED | Both Section 3 and Appendix C use `packaging1` and `foodbev1`. Consistent. |
| H3 | Success criteria wrong Modbus slave assignments | High | FIXED | Section 11 now references Section 3a.3 topology. Slave assignments match. |
| H4 | Thermal diffusion T(0) = 37C | High | FIXED | Section 4.2.10 now uses a truncated Fourier series with convergence check. Three terms give T(0) = 15.8C. Implementation must add terms until T(0) is within 1C of T_initial. Five to ten terms suffice. The math is correct. See detailed check below. |
| H5 | Evaluation tolerance windows | High | FIXED | Section 12.4 defines pre_margin (30s) and post_margin (60s). Effective window is [start - pre, end + post]. Overlapping windows assign to nearest event by start time. Early detections produce negative latency, reported as-is. Correct. |
| H6 | Cross-run statistical significance | High | FIXED | Section 12.4 specifies N=10 seeds for benchmarking. Reports mean and std dev. 95% CI via mean +/- 1.96 * std / sqrt(N). Non-overlapping CIs indicate significance at p < 0.05. Correct. |
| M1 | Blast chiller vs cold room naming | Medium | FIXED | Consistent "chiller" naming throughout. Section 2b.7 uses "Refrigeration" for the equipment group and "chiller.*" for signals. |
| M2 | Laminator drying oven (solvent-free) | Medium | FIXED | Section 2.3 describes "conditioning tunnel temp" not "drying oven". The laminator is solvent-free. The tunnel conditions the adhesive bond. No solvent reference. |
| M3 | Mixer speed 1000-3000 RPM vs 30-120 RPM | Medium | FIXED | Section 2b.2 specifies 0-3000 RPM range. Appendix D batch_cycle config uses mixer_speed_rpm: [1000, 2500]. No 30-120 RPM reference remains. |
| M4 | OPC-UA Energy node placement | Medium | FIXED | Section 3.2.1 places Energy under each profile tree (PackagingLine.Energy, FoodBevLine.Energy). Appendix B matches. No top-level peer node. |
| M5 | CIP conductivity threshold (50 uS/cm vs 5 mS/cm) | Medium | FIXED | Appendix D shows final_rinse_conductivity_max: 5.0 mS/cm. Section 4.6 describes conductivity dropping below 5 mS/cm. Consistent. |
| M6 | Mixing matrix correlation inflation | Medium | FIXED | Section 4.3.1 now uses Cholesky decomposition. L = cholesky(R). noise_correlated = L @ noise_independent. Covariance = L @ L^T = R. The matrices are the desired correlation matrices, not mixing matrices. The fix is correct. See detailed check below. |
| M7 | Signal generation pipeline order | Medium | FIXED | Section 4.3.1 specifies the pipeline: (1) generate N(0,1), (2) apply Cholesky factor, (3) scale by effective sigma. The text explains why this order preserves correlations. Correct. |
| M8 | Severity weighting in evaluation | Medium | FIXED | Section 12.4 defines weighted_recall and weighted F1. Default severity weights for 14 scenario types. Web break: 10.0. Micro-stop: 1.0. Unweighted metrics remain primary. Weighted metrics are supplementary. Reasonable design. |
| M9 | Detection latency targets | Medium | FIXED | Section 12.4 defines per-scenario latency targets. Web break < 2s. Bearing wear < 24h before failure. Cold chain break < 5 min. Targets are aspirational, documented as such. First-generation detectors are not expected to meet all targets. Correct framing. |
| L1 | press.line_speed protocol assignment | Low | FIXED | Section 2.11 lists press.line_speed under "Modbus TCP + OPC-UA" (dual protocol). Consistent with Section 3 register map and OPC-UA node tree. |
| L2 | F&B network diagram IP collision (.50) | Low | FIXED | Section 3a.3 network diagram shows CollatrEdge at .60 and QC Station at .50. No collision. |
| L3 | F&B input register list shorter than Appendix A | Low | FIXED | Section 3.1.3 F&B input registers now list 11 entries. Appendix A matches. Consistent. |
| L4 | Config naming drift_degrees vs max_drift_c | Low | FIXED | Appendix D uses max_drift_c consistently. Section 6 uses max_drift_c in the dryer_drift scenario config. No drift_degrees reference remains. |
| L5 | No material splice scenario | Low | FIXED | Section 5.13a defines the material splice scenario. Flying splice, tension spike, registration disturbance, unwind diameter reset. Ground truth event type: material_splice. Complete. |
| L6 | Checkweigher TNE thresholds | Low | FIXED | Section 2b.6 documents TN/28 tolerable negative error. For 400g product, TNE is 15g. Rejects below nominal minus 2xTNE (370g). Overweight:underweight ratio discussed. Correct. |
| L7 | Second-order response t reset | Low | FIXED | Section 4.2.3 states: "The implementation resets t to zero on each setpoint change. The amplitude A is recomputed as the difference between the new setpoint and the current value at the moment of change." Explicit. Correct. |
| L8 | Student-t variance inflation | Low | FIXED | Section 4.2.11 now includes a "Variance note" paragraph. Documents that at df=5, effective std dev is 1.29 * sigma. States this is intentional. Provides the correction formula for exact RMS matching. Default config does not apply the correction. This is a defensible design choice. |
| L9 | AR(1) state after connection gap | Low | FIXED | Section 4.2.11 states: "During a controller connection drop, the AR(1) noise process continues generating internally. The autocorrelation state is maintained across the gap." Explicit. Correct. |
| L10 | Ground truth header record | Low | FIXED | Section 4.7 defines the header record. First line has event_type: "config". Contains simulator version, seed, profile, per-signal noise parameters, and active scenario list. Self-contained for KS testing. |
| L11 | Random baseline in evaluation | Low | FIXED | Section 12.4 defines the random baseline. Fires with probability p = anomaly density. Reports baseline alongside detector results. "Any useful detector must exceed the random baseline on both precision and F1." Correct. |
| L12 | 1/f noise absent | Low | FIXED | Section 4.2.11 includes a "Known limitation: no 1/f (pink) noise" paragraph. Documents the limitation, explains its impact (detectable only in multi-day PSD analysis), and defers to a future phase. This is the right approach. |

**Summary: 27/27 issues resolved. All fixes verified as correct.**

---

## Statistical Correctness Check

### Thermal Diffusion: Multi-Term Fourier Series

Section 4.2.10 presents the volume-averaged temperature for 1D heat conduction in a slab:

```
T(t) = T_oven - (T_oven - T_initial) * SUM [ C_n * exp(-(2n+1)^2 * pi^2 * alpha * t / L^2) ]
```

with C_n = 8 / ((2n+1)^2 * pi^2). I verified the coefficient table:

- n=0: C_0 = 8/pi^2 = 0.8106. Correct.
- n=1: C_1 = 8/(9*pi^2) = 0.0901. Correct.
- n=2: C_2 = 8/(25*pi^2) = 0.0324. Correct.
- Three-term sum: 0.8106 + 0.0901 + 0.0324 = 0.9331. Correct.

At t=0, T(0) = 180 - 0.9331 * 176 = 180 - 164.2 = 15.8C. This is 11.8C above T_initial. Still not exact, but the convergence check requires adding terms until T(0) is within 1C of T_initial. The full series sums to 1.0 (this is the Fourier series for a constant function). At five terms the sum is approximately 0.964. At ten terms, approximately 0.982. The convergence is slow because the coefficients decay as 1/(2n+1)^2, which is O(1/n^2). To reach within 1C of T_initial for a 176C difference, we need the residual below 1/176 = 0.0057, which requires the sum to exceed 0.9943. This needs roughly 20 terms.

That is more than the "5 to 10 terms" the PRD estimates. At T_initial=4C and T_oven=180C, the temperature difference is 176C. One degree of error requires the partial sum to reach 0.9943. The convergence of the series sum(1/(2n+1)^2) is well-known. Twenty terms gives approximately 0.995. Ten terms gives approximately 0.982, which leaves a residual of 0.018 * 176 = 3.2C. That puts T(0) at 7.2C, still 3.2C above T_initial.

This is a minor inaccuracy in the estimate. The convergence check itself is correct: "add next term and recheck." The implementation will converge regardless. The estimate of "5 to 10 terms" is optimistic for large temperature differences. For T_initial=4C and T_oven=60C (mild case), 10 terms gives T(0) within 1C. For T_initial=4C and T_oven=180C (extreme case), roughly 20 terms are needed.

I record this as a new Low issue. The convergence check is correct. The estimate is misleading. An implementer who caps at 10 terms will produce T(0) = 7.2C instead of 4C for the extreme case. The fix is trivial: remove the estimate or change it to "10 to 30 terms depending on the temperature difference."

### Cholesky Decomposition for Peer Correlation

Section 4.3.1 specifies L = cholesky(R), noise_correlated = L @ noise_independent. The covariance of the output is E[L z z^T L^T] = L I L^T = L L^T = R. Correct.

I verified that all three correlation matrices are symmetric and positive definite.

Vibration R:
```
R = [[1.0,  0.2,  0.15],
     [0.2,  1.0,  0.2 ],
     [0.15, 0.2,  1.0 ]]
```

Eigenvalues: computed from the characteristic polynomial. The matrix is diagonally dominant (each diagonal entry exceeds the sum of absolute off-diagonal entries in its row: 1.0 > 0.2 + 0.15 = 0.35). Diagonally dominant matrices are positive definite. Cholesky decomposition will succeed. Correct.

Dryer R:
```
R = [[1.0,  0.1,  0.02],
     [0.1,  1.0,  0.1 ],
     [0.02, 0.1,  1.0 ]]
```

Same argument. Diagonal dominance holds. Positive definite. Correct.

Oven R:
```
R = [[1.0,  0.15, 0.05],
     [0.15, 1.0,  0.15],
     [0.05, 0.15, 1.0 ]]
```

Same argument. Diagonal dominance holds. Positive definite. Correct.

### Signal Generation Pipeline

Section 4.3.1 specifies: (1) generate independent N(0,1), (2) apply L, (3) scale by sigma_i. The text states: "Scaling after correlation is correct because scaling is a diagonal transformation. It changes covariance magnitudes but does not change correlation coefficients."

I verify this claim. Let D = diag(sigma_1, ..., sigma_N). The output is D L z. The covariance is D L E[z z^T] L^T D^T = D L L^T D = D R D. The (i,j) entry of D R D is sigma_i * R_{ij} * sigma_j. The correlation coefficient is (sigma_i * R_{ij} * sigma_j) / sqrt(sigma_i^2 * sigma_j^2) = R_{ij}. The correlation is preserved. Correct.

If the order were reversed (scale then correlate: L D z), the covariance would be L D D^T L^T = L D^2 L^T. This is not D R D. The correlations would depend on the sigma ratios. The PRD correctly identifies that the current order is the only correct one.

### AR(1) Variance Preservation

The formula noise_t = phi * noise_{t-1} + sigma * sqrt(1 - phi^2) * N(0,1) has marginal variance sigma^2. I verify: Var(noise_t) = phi^2 * Var(noise_{t-1}) + sigma^2 * (1 - phi^2). At stationarity, Var = phi^2 * Var + sigma^2 * (1 - phi^2). Solving: Var * (1 - phi^2) = sigma^2 * (1 - phi^2). Var = sigma^2. Correct.

### Student-t Variance Documentation

At df=5, Var = sigma^2 * 5/3 = 1.667 * sigma^2. Effective std dev = sigma * sqrt(5/3) = 1.291 * sigma. The PRD rounds to 1.29. Correct. The decision to leave this uncorrected is documented and intentional. Defensible.

### Second-Order Response

The formula: value = setpoint + A * exp(-zeta * omega_n * t) * sin(omega_d * t + phase). With omega_n = 1/tau, omega_d = omega_n * sqrt(1 - zeta^2), and t resetting on each setpoint change. For zeta = 0.6 and tau = 60s, omega_n = 0.0167 rad/s. omega_d = 0.0167 * sqrt(1 - 0.36) = 0.0167 * 0.8 = 0.0133 rad/s. The oscillation period is 2*pi/omega_d = 472s, approximately 8 minutes. The decay time constant is 1/(zeta * omega_n) = 1/(0.6 * 0.0167) = 100s. This produces 1-2 visible oscillations before settling. That matches real Eurotherm tuning with moderate underdamping. Correct.

### Ornstein-Uhlenbeck (Random Walk with Mean Reversion)

Section 4.2.5: delta = drift_rate * noise(0,1) - reversion_rate * (value - center); value += delta * dt. This is Euler-Maruyama discretisation of dX = -theta*(X-mu)*dt + sigma*dW. With reversion_rate = theta, drift_rate = sigma, center = mu. The discretisation is first-order and valid for the dt values used (0.1s to 1s). At these step sizes, the bias from Euler-Maruyama is negligible for theta < 0.1 and sigma < 1.0. Correct.

### Exponential Bearing Degradation

Section 5.5: vibration_increase = base_rate * exp(k * elapsed_hours). I verified the worked examples:

- At 500h with base_rate=0.001, k=0.005: 0.001 * exp(2.5) = 0.001 * 12.18 = 0.01218 mm/s per hour. The cumulative integral is (0.001/0.005) * (exp(2.5) - 1) = 0.2 * 11.18 = 2.24 mm/s. Correct.
- At 300h with base_rate=0.005, k=0.01: total = (0.005/0.01) * (exp(3) - 1) = 0.5 * 19.09 = 9.5 mm/s. Correct.

The hockey-stick matches IMS/NASA bearing data qualitatively. The parameter ranges produce failure timelines of 2-6 weeks. Correct.

---

## Evaluation Protocol Assessment

The evaluation protocol is now rigorous. I assess each component.

**Tolerance windows (H5 fix).** Pre-margin 30s, post-margin 60s. The asymmetry is correct: precursor detection (before annotated start) deserves a shorter window because the signal change genuinely precedes the event. Post-event detection needs a longer window because of processing delay and because some events have tails. The overlap resolution rule (assign to nearest start time) is simple and deterministic. One edge case: two events separated by less than 90s (30+60 margin overlap). The nearest-start rule handles this but may misattribute a detection between two events. At the event frequencies specified (web breaks 1-2 per week, micro-stops 10-50 per shift), adjacent events within 90s are rare for serious scenarios. Adequate.

**Multi-seed significance (H6 fix).** N=10 seeds with 95% CI. The formula mean +/- 1.96 * std / sqrt(N) assumes approximate normality of the metric distribution across seeds. With N=10, this is marginal. A bootstrap CI or Wilcoxon rank-sum test would be more robust. For a PRD, the normal approximation is acceptable. The important thing is that single-seed results are flagged as insufficient for published benchmarking. They are.

**Severity weighting (M8 fix).** The weight assignments are reasonable. Web break and cold chain break at 10.0. Micro-stop at 1.0. The 10:1 ratio reflects the real cost differential. The weights are configurable. Unweighted metrics remain primary. This avoids the trap of optimizing for severity weights that may not match every deployment.

**Latency targets (M9 fix).** Per-scenario targets range from 2 seconds (web break) to 48 hours (intermittent fault phase 1). The framing as "aspirational targets for a mature system" is correct. A first-generation detector will not meet the web break target at 2 seconds. Reporting actual latency alongside targets enables gap analysis. The latency targets are defensible.

**Random baseline (L11 fix).** A random detector with probability p = anomaly density. This is the right baseline. Any detector that cannot beat random has no predictive value. The baseline also serves as a sanity check on the evaluation setup: if a detector scores below random, something is wrong.

**Overall evaluation protocol verdict.** The protocol covers the five essential elements: paired design, event-level metrics, tolerance windows, statistical significance, and a baseline. It does not cover partial detection credit for multi-signal events (Round 2 Gap 6, Low severity, not in the consolidated list). This is a minor gap. The protocol is sufficient for internal evaluation, published benchmarking, and comparative studies. It exceeds the evaluation methodology of NAB (no tolerance windows, no severity weighting), SKAB (no formal protocol), and DAMADICS (fault detection rates only).

---

## Remaining Tells / Detectability Assessment

I revisit the detectability analysis from Round 2.

**Round 1 tells (5 original):** All five are resolved. Stepped ramps, heteroscedastic noise, micro-stops, composite environmental model, and sensor quantisation are all in place.

**Round 2 tells (3 new):**

Tell 1: Mixing matrix correlation inflation. RESOLVED. Cholesky decomposition produces exact correlations.

Tell 2: Thermal diffusion initial jump. RESOLVED. Multi-term Fourier with convergence check. The output starts within 1C of T_initial (if enough terms are used; see caveat above about the term count estimate).

Tell 3: 1/f noise absence. DOCUMENTED. Acknowledged as a known limitation. Deferred to a future phase. This is the correct treatment. Adding 1/f noise to a PRD is straightforward to specify but nontrivial to implement correctly (fractional Gaussian noise or spectral shaping). The limitation matters only for multi-day PSD analysis.

**Remaining tells after Round 3:**

1. **No 1/f noise (documented).** An analyst computing the PSD of 7 days of ambient temperature will see a flat spectrum. Detectable only with spectral analysis. Not visible in time-domain charts. Severity: Low.

2. **Perfectly stationary noise parameters within a machine state.** Real factories have non-stationary noise even during steady-state production. Bearing temperature slowly changes friction. Substrate properties vary roll to roll. The within-regime drift addresses this partially (1-3% drift over hours). But the noise distribution parameters (sigma, df, phi) remain fixed within a state. In real data, the kurtosis of vibration noise changes with bearing temperature. The PRD's time-varying covariance (Section 4.3.2) addresses gain drift but not distribution shape drift. An analyst fitting a Student-t to successive 1-hour windows will find constant df. In real data, df varies by 1-2 units over a shift. Severity: Low. Detectable only with rolling distribution fitting.

3. **Perfectly periodic shift changes.** Section 5.9 specifies fixed shift times: 06:00, 14:00, 22:00. Real factories have 5-15 minutes of variation. The changeover duration is random (5-15 min) but the start time is fixed. An analyst looking at the start-of-idle timestamps across 30 days will see exact 8-hour spacing. Real data shows +/- 10 minutes of jitter on shift boundaries. Severity: Low. Trivial to fix (add uniform jitter of +/- 10 minutes to shift times). I note this as a new issue.

**Net assessment.** Three tells remain after Round 3. All are Low severity. None are visible on time-domain charts. All require statistical analysis of multi-day runs to detect. The output would pass visual inspection by a domain expert. It would pass basic statistical tests (mean, variance, autocorrelation, cross-correlation). It would not pass rolling distribution fitting or PSD analysis of week-long recordings. For a demo and integration testing tool, this is excellent. For a published benchmark, the 1/f noise limitation should be addressed before multi-day evaluation runs.

---

## New Issues Found

| # | Issue | Severity | Section | Description |
|---|-------|----------|---------|-------------|
| N1 | Fourier term count estimate optimistic | Low | 4.2.10 | The text says "5 to 10 terms suffice." For T_initial=4C and T_oven=180C, 10 terms give T(0) = 7.2C (3.2C above T_initial). Approximately 20 terms are needed for the 1C convergence criterion with this temperature difference. The convergence check itself is correct. The estimate should say "10 to 30 terms depending on the temperature difference." |
| N2 | Shift change times have no jitter | Low | 5.9 | Shift changes fire at exactly 06:00, 14:00, 22:00. Real factories vary by 5-15 minutes. Add uniform jitter of +/- 10 minutes to each shift change start time. |

Both issues are Low severity. Neither blocks implementation. N1 is a documentation clarification. N2 is a one-line code change.

---

## Final Assessment

**Is this implementation-ready?** Yes.

The PRD specifies a synthetic data generator with 22 files and 5,275 lines. It covers 112 signals across two factory profiles. It defines 9 signal models, 3 noise distributions, peer correlation via Cholesky decomposition, time-varying covariance, 17+ scenario types, 8 data quality injection types, a multi-controller network topology, and a formal evaluation protocol with tolerance windows, multi-seed significance, severity weighting, latency targets, and a random baseline.

The statistical foundations are correct. The formulas check out. The fixes from Round 2 were implemented properly. The Cholesky decomposition produces exact correlations. The multi-term Fourier series converges to the correct initial condition. The signal generation pipeline preserves correlation structure under per-signal scaling. The evaluation protocol is more rigorous than any published industrial benchmark I have reviewed.

Two Low issues remain from this round. Thirteen Low items were documented as known limitations or Phase 2 work in earlier rounds. None of these block implementation or demos. The output will fool a packaging engineer looking at time-domain charts. It will pass basic statistical validation. It will serve its three stated purposes: integration testing, demonstrations, and development.

I sign off on this PRD for implementation.

**Grade: A.**

The two Low issues prevent an A+. Fix them during implementation and this is an A+ specification.
