# Round 3 Review: Collatr Factory Simulator PRD

**Date:** 2 March 2026
**Reviewer:** Senior Industrial Automation Engineer (20+ years, UK manufacturing)
**Scope:** Full PRD, 22 files, 5,275 lines
**Previous grades:** Round 1: B+. Round 2: A-.
**Context:** All 27 Round 2 issues (6 High, 9 Medium, 12 Low) marked RESOLVED. This is the sign-off review.

---

## Overall Grade: A

## Summary

The team resolved all 27 issues from Round 2. Every fix is correct. The oven setpoint registers now agree across Section 3 and Appendix A at HR 1110-1115. The MQTT topic prefix is `packaging1` everywhere. The success criteria match the Section 3a.3 network topology. The cold room is named `cold_room.py` in the project structure. The laminator signal is `tunnel_temp`. The mixer speed range is 1000-2500 RPM. The OPC-UA Energy node sits under each profile tree. The CIP conductivity threshold is 5.0 mS/cm. The material splice scenario exists. The TNE thresholds are documented. The F&B input register list matches Appendix A. The F&B network diagram IP collision is fixed. The config naming is consistent. I found two new issues during cross-referencing. Both are Low severity. Neither blocks implementation. This PRD is ready to build.

---

## Round 2 Fix Verification

### High Issues (6)

| # | Issue | Status | Notes |
|---|-------|--------|-------|
| H1 | Oven setpoint registers: Section 3 said HR 1120-1125, Appendix A said HR 1110-1115 | FIXED | Section 3.1.2 now says HR 1110-1115. Appendix A says HR 1110-1115. Both match. Verified in the oven register table: addresses 1110-1111, 1112-1113, 1114-1115 for zone 1/2/3 setpoints. |
| H2 | MQTT topic prefix: Section 3 used `packaging1`, Appendix C used `line3` | FIXED | Appendix C now uses `collatr/factory/demo/packaging1/` throughout. Section 3.3.1 says `packaging1`. The prefix table in 3.3.1 says packaging line_id is `packaging1`. All 17 packaging topics in Appendix C use `packaging1`. Consistent. |
| H3 | Success criteria: wrong Modbus slave assignments for F&B | FIXED | Section 11.1 now lists the full F&B topology: mixer at 10.0.2.10:502 UID 1 (CDAB), oven gateway at 10.0.2.20:502 UID 1/2/3 and UID 10, filler at 10.0.2.30:502 UID 1, sealer at 10.0.2.31:502 UID 1, chiller at 10.0.2.40:502 UID 1, CIP at 10.0.2.32:502 UID 1. Matches Section 3a.3 exactly. |
| H4 | Thermal diffusion initial condition: first-term Fourier gives T(0)=37C not 4C | FIXED | Section 4.2.10 now uses three terms (n=0,1,2) and documents the convergence issue. Sum of three terms is 0.9331. T(0) with three terms is 15.8C. The text says implementations must sum terms until T(0) falls within 1C of T_initial, typically 5-10 terms. Correct approach. |
| H5 | Evaluation protocol: tolerance windows for early/late detections | FIXED | Section 12.4 now defines pre_margin_seconds (default 30) and post_margin_seconds (default 60). Early detections produce negative latency. Negative latency is reported as-is. The matching logic assigns a detection to the nearest event by start time when windows overlap. Sound design. |
| H6 | Cross-run statistical significance: N=10 seeds | FIXED | Section 12.4 now specifies N=10 seeds for published benchmarking. Seeds 1-10. Report mean and standard deviation. 95% CI via mean +/- 1.96 * std / sqrt(N). Single seed acceptable for internal regression. Correct. |

### Medium Issues (9)

| # | Issue | Status | Notes |
|---|-------|--------|-------|
| M1 | Blast chiller vs cold room naming | FIXED | Section 2b.7 is titled "Refrigeration" and describes a cold room. Appendix E lists `cold_room.py` in the generators directory. No mention of "blast chiller" anywhere. Consistent. |
| M2 | Laminator drying oven signal on solvent-free machine | FIXED | Section 2.3 now says `laminator.tunnel_temp` with description "Conditioning tunnel temp." Range 40-100C. The equipment description says "solvent-free laminator." A conditioning tunnel on a solventless machine is correct. Nordmeccanica and Comexi both use conditioning tunnels. Signal name and description match the equipment. |
| M3 | Mixer speed: equipment says 1000-3000 RPM, config says 30-120 RPM | FIXED | Section 2b.2 describes a high-shear Silverson at 0-3000 RPM full range. Production mixing speed is 1000-2500 RPM. Loading speed is 50-100 RPM. Appendix D batch_cycle config says `mixer_speed_rpm: [1000, 2500]` for production mixing. The text in 2b.2 says "typically 1000-2500 RPM for high-shear." Consistent. |
| M4 | OPC-UA Energy node: Section 3 said top-level peer, Appendix B showed under profile tree | FIXED | Section 3.2.1 now says "Energy nodes sit under each profile tree, not at the top level." The node paths are PackagingLine.Energy and FoodBevLine.Energy. Appendix B shows the same structure. Both match. |
| M5 | CIP conductivity threshold: config said 50 uS/cm, text said 5 mS/cm | FIXED | Appendix D cip_cycle config now says `final_rinse_conductivity_max: 5.0` with comment "mS/cm acceptance threshold (must fall below 5 mS/cm)." Section 2b.8 says "conductivity must fall below 5 mS/cm." Section 4.6 says "below 5 mS/cm." All three agree. Units are mS/cm throughout. |
| M6 | Mixing matrix inflated correlations, need Cholesky decomposition | FIXED | Section 4.3.1 now specifies Cholesky decomposition. The formula is L = cholesky(R), then noise_correlated = L @ noise_independent. The signal generation pipeline is explicitly ordered: generate independent samples, apply Cholesky, then scale by sigma. This order preserves correlation coefficients. Correct. |
| M7 | Peer correlation + sigma ordering unspecified | FIXED | Section 4.3.1 now has a "Signal generation pipeline" subsection. The order is: (1) generate N independent N(0,1) samples, (2) apply Cholesky factor, (3) scale by effective sigma. The text explains why this order is correct: scaling is a diagonal transformation that preserves correlation coefficients. Good. |
| M8 | Severity weighting in evaluation | FIXED | Section 12.4 now includes a severity-weighted metrics subsection. Default weights range from 1.0 (micro_stop) to 10.0 (web_break, cold_chain_break). Weighted recall formula is documented. Unweighted metrics remain primary. Weighted metrics are supplementary. Sensible approach. |
| M9 | Detection latency targets not defined | FIXED | Section 12.4 now includes a latency targets table. Web break < 2 seconds. Bearing wear < 24 hours before failure. Fill weight drift < 10 minutes. The targets are marked as aspirational. Actual latency reported alongside targets. Correct. |

### Low Issues (12)

| # | Issue | Status | Notes |
|---|-------|--------|-------|
| L1 | Signal count in table 2.11: `press.line_speed` protocol assignment | FIXED | Section 2.11 summary table now lists `press.line_speed` under "Modbus TCP + OPC-UA" (8 signals). The Modbus-only column lists 18 signals. I counted the Modbus-only list: 18 signal names. The count matches. The laminator and slitter signals that are dual-protocol are correctly in the "Modbus TCP + OPC-UA" row. |
| L2 | F&B network diagram: CollatrEdge and QC station both at .50 | FIXED | Section 3a.3 F&B network diagram now shows CollatrEdge at .60 and QC Station at .50:4840. No IP collision. The port mapping table still correctly maps QC to simulator port 4842. Consistent. |
| L3 | F&B input register list in Section 3 shorter than Appendix A | FIXED | Section 3.1.3 F&B input register table now lists 11 entries: oven zone 1/2/3 temps (IR 100-102), oven zone 1/2/3 setpoints (IR 103-105), product core temp (IR 106), chiller room temp (IR 110), chiller setpoint (IR 111), CIP wash temp (IR 115), and energy (IR 120-121). Appendix A lists the same 11 entries. Both match. |
| L4 | Config naming: Section 6 used `drift_degrees`, Appendix D used `max_drift_c` | FIXED | Section 6 scenario config now uses `max_drift_c: [5, 15]` for dryer drift. Appendix D also uses `max_drift_c`. Consistent naming throughout. |
| L5 | No material splice scenario | FIXED | Section 5.13a now defines the material splice scenario. Trigger: unwind_diameter below 150 mm. Duration: 10-30 seconds. Web tension spike 50-100 N. Registration error increase 0.1-0.3 mm. Unwind diameter resets to 1500 mm. Ground truth event type is `material_splice`. Frequency 2-4 per shift. This is a well-specified splice event. |
| L6 | Checkweigher missing TNE thresholds | FIXED | Section 2b.6 now includes a paragraph on the Three Packers Rules (TN/28). For a 400g ready meal, TNE is 15g (3.75%). Reject threshold is nominal minus 2xTNE = 370g. The fill weight drift scenario (Section 5.14.3) is referenced for how these thresholds drive reject rates. Correct. |
| L7 | Second-order response must reset t on setpoint change | FIXED | Section 4.2.3 now states: "The implementation resets t to zero on each setpoint change. The amplitude A is recomputed as the difference between the new setpoint and the current value at the moment of change." Explicit. |
| L8 | Student-t variance 29% higher than sigma at df=5 | FIXED | Section 4.2.11 now includes a "Variance note" paragraph. It documents that at df=5, effective standard deviation is 1.29x sigma. It states this is intentional. It explains how to match RMS noise between distributions if needed: scale by sqrt((df-2)/df). The default does not apply this correction. Documented. |
| L9 | AR(1) state after connection gap | FIXED | Section 4.2.11 AR(1) section now states: "During a controller connection drop (Section 4.8), the AR(1) noise process continues generating internally. The autocorrelation state is maintained across the gap." Explicit. |
| L10 | Ground truth log should include noise parameters in header | FIXED | Section 4.7 now specifies a header record with event_type "config." The example shows per-signal noise parameters: distribution type, sigma, df. The text says the header makes the log self-contained for KS tests and spectral analysis. Good. |
| L11 | No random baseline in evaluation protocol | FIXED | Section 12.4 now defines a random baseline. The baseline fires alerts at probability p equal to the anomaly density. The text requires any useful detector to exceed the random baseline on both precision and F1. Correct. |
| L12 | 1/f noise absent | FIXED | Section 4.2.11 now includes a "Known limitation" paragraph. It acknowledges no 1/f component. It explains the impact is weak at the 1-60 second sampling rates used here. It notes the limitation affects multi-day PSD analysis. It defers to a future phase. Honest and adequate. |

**All 27 issues verified as correctly fixed.**

---

## New Issues Found

| # | Section(s) | Severity | Issue | Recommendation |
|---|------------|----------|-------|----------------|
| N1 | 03 vs App A | **Low** | Chiller suction pressure register address: Section 3.1.2 says HR 1410-1411, Appendix A says HR 1404-1405. Discharge pressure: Section 3 says HR 1412-1413, Appendix A says HR 1406-1407. Both files agree on chiller room_temp (1400-1401) and setpoint (1402-1403). The divergence starts at suction pressure. Appendix A packs the chiller registers contiguously (1400-1407). Section 3 leaves a gap (1400-1403, then jumps to 1410). | Align to Appendix A (1404-1407). The appendix is the register map. It should be authoritative. |
| N2 | 11 | **Low** | MQTT subscription filter for F&B: Section 11.1 says CollatrEdge subscribes to `collatr/foodbev/#`. But Section 3.3.1 and Appendix C use the prefix `collatr/factory/demo/foodbev1/`. The correct subscription filter is `collatr/factory/demo/foodbev1/#` or `collatr/factory/#` to catch both profiles. The success criteria filter would miss all messages. | Update Section 11.1 to use `collatr/factory/demo/foodbev1/#` or the broader `collatr/factory/#`. |

---

## Cross-Reference Consistency Check

I verified the following across all 22 files.

**Signal counts.** Section 2.11 says 47 packaging signals. I counted the signal table: 21 press + 5 laminator + 3 slitter + 11 coder + 2 env + 2 energy + 3 vibration = 47. Section 2b.14 says 65 F&B signals (50 unique + 15 shared). I counted: 8 mixer + 10 oven + 8 filler + 6 sealer + 6 QC + 7 chiller + 5 CIP + 11 coder + 2 env + 2 energy = 65. Both correct.

**Register addresses.** Packaging HR 100-599 for equipment, 600-603 for energy. F&B HR 1000-1507. No overlaps. Energy at HR 600-603 is shared. Input registers: packaging at IR 0-11, F&B at IR 100-121. No overlap. Coils: packaging at 0-5, F&B at 100-102. No overlap. Discrete inputs: packaging at 0-2, F&B at 100. No overlap. Addresses are clean except the N1 issue above.

**MQTT topic counts.** Appendix C: packaging has 11 coder + 2 env + 3 vibration per-axis + 1 batch vibration = 17 topics. F&B has 11 coder + 2 env = 13 topics. Section 11.1 says 17 packaging and 13 F&B. Matches.

**OPC-UA node IDs.** Appendix B lists full node paths for both profiles. Section 3.2.1 inline node trees match Appendix B. Energy nodes are under PackagingLine.Energy and FoodBevLine.Energy in both places.

**Controller assignments.** Section 3a.2 packaging: 7 controllers at specific IPs. Section 3a.3 F&B: 10 controllers at specific IPs. Section 3a.4 port mapping table maps each to a simulator port. The port mapping table has 15 entries covering both profiles. No collisions.

**Byte order.** Mixer is CDAB everywhere: Section 3.1.2, Section 3a.3, Appendix A, Appendix D. Everything else is ABCD. Consistent.

**Configuration parameter names.** Section 6 and Appendix D use the same names: `max_drift_c` for dryer drift, `mixer_speed_rpm` for mixer speed, `final_rinse_conductivity_max` for CIP threshold. No discrepancies found.

**Scenario names and parameters.** Section 5 scenario definitions match Appendix D configuration parameters. Section 6 scenario block uses the same parameter names as Appendix D. The F&B scenarios in Section 5.14 match the Appendix D F&B scenario section.

**Project structure.** Appendix E lists `cold_room.py` for the chiller generator. Section 2b.7 describes cold room behaviour. No blast chiller references remain.

---

## Final Assessment

**Implementation-ready: Yes, with two Low-severity notes.**

The two new issues (N1 and N2) are minor. N1 is a register address gap in Section 3 that differs from the contiguous layout in Appendix A. The implementer will follow Appendix A because it is the register map. N2 is a wrong MQTT filter string in the success criteria. An engineer writing the test will notice because they will reference Section 3.3.1 for the actual topic structure.

Neither issue affects the data generation engine, the scenario system, the protocol server architecture, or the signal models. They are documentation alignment items. Fix them before printing the spec for the engineering team. They do not block implementation kickoff.

**What makes this PRD strong:**

The signal definitions are correct for the equipment described. The register maps are complete and well-partitioned. The network topology section reads like someone who has done factory floor integrations. The data generation engine handles noise distributions, scan cycle artefacts, peer correlations, and time-varying covariance. These are details that most simulator specs miss. The F&B profile is thorough and would be recognised by a food production engineer. The evaluation protocol is statistically sound. The scenario system covers the operational events that matter.

**What would make it stronger (not blocking, future work):**

One area not covered: Modbus write-back testing. Section 3 marks several registers as writable (dryer setpoints, oven setpoints, chiller setpoint). The scenarios and success criteria do not test the write path. CollatrEdge is described as read-only, so this is not a current requirement. But when an operator HMI or recipe management system writes a setpoint to the simulator, the data engine should respond. This is a Phase 2 concern.

**Grade justification:** A- in Round 2 reflected three High issues and five Medium issues. All are fixed. The two remaining Low issues are not enough to hold the grade. The 163 additional lines since Round 2 (from 5,112 to 5,275) added the material splice scenario, the TNE thresholds, the Cholesky decomposition specification, the severity weights, the latency targets, the random baseline, and the convergence check for the thermal diffusion model. Every addition is technically correct. The document has matured from a credible engineering specification to one I would hand to an integration team and say "build this."

This earns an A. Not A+ because two cross-reference errors survived the fix cycle. A clean spec has zero. But the engineering content is sound. A production engineer at a food company or a packaging converter would read this and trust it.
