# Implementation Readiness Review: Round 2

**Reviewer:** Lead Python Developer  
**Date:** 2026-03-02  
**PRD Version:** 1.1 (post-implementation-review updates)  
**Scope:** All 23 PRD files (README + 13 sections + 7 appendices), consolidated issues file  

## Overall Grade: A-

## Summary

The PRD has improved substantially from the B+ grade in Round 1. All 52 issues from the consolidated review have been addressed, and the resolutions are uniformly sound. The additions of Section 13 (Test Strategy), the Mosquitto sidecar decision, the 13-week timeline, and the detailed Phase 0 validation spikes transform this from a document with significant gaps into one that a development team can actually build from. The signal models, protocol specifications, register maps, OPC-UA node trees, and MQTT topic maps are thorough enough to implement without guesswork in the vast majority of cases.

The remaining issues are minor. There are a handful of data type inconsistencies between sections and appendices, a few underspecified edge cases in the scenario scheduling engine, and some areas where the configuration reference doesn't quite match the signal model definitions. None of these block implementation. They would cause a developer to pause for 10-30 minutes to resolve, not days. The PRD is in "ship it with a punch list" territory.

The architecture is well-suited to the problem. The decision to use asyncio with a single-writer signal store eliminates concurrency bugs. The plugin-based equipment generator pattern makes adding the F&B profile straightforward. The Mosquitto sidecar eliminates the amqtt risk entirely. The validation spikes in Phase 0 are an excellent addition that de-risks the most uncertain technical choices before any architecture is committed.

## Previous Issues Resolution Assessment

All 40 of my original issues were resolved. Quality of resolutions:

**Excellent resolutions (fully specified, no ambiguity):**
- I-H1 (Test Strategy): New Section 13 is comprehensive and pragmatic. Property-based testing for signal models is the right call.
- I-H2 (Startup/Shutdown): Startup sequence, readiness gates, and SIGTERM handling fully specified in Appendix F Phase 1.
- I-H3 (Config Validation): Pydantic models + specific validation rules (df >= 3, positive-definiteness, range constraints) are exactly what's needed.
- I-H7 (amqtt): Mosquitto sidecar is the right call. Both IIoT and I agreed. The decision rationale is documented.
- I-H8 (Timeline): 13 weeks with explicit phase boundaries and exit criteria. The expansion of Phases 3, 4, 5 addresses the original underestimates.
- I-M8 (Engine Atomicity): "No await between signals within a tick" is the precise specification needed.

**Good resolutions (adequate for implementation):**
- I-M6 (Second Setpoint Change): "Replace, not stack" is clear. New transient from current value.
- I-M13 (Transport Lag Buffer): Ring buffer sized 2x max lag. Zero-speed freeze. Concrete and implementable.
- I-M22 (Intermittent Fault Exit Criteria): 7-day batch run at 100x covers full progression. Pragmatic.

**Resolutions that could be slightly more specific but are acceptable:**
- I-H4 (Scenario Scheduling): "Poisson inter-arrival times and minimum gap equal to scenario minimum duration" is specified but the implementation of "scenarios crossing shift boundaries continue into the next shift" could use one more sentence about what happens when a scenario from the previous shift prevents a new-shift scenario from starting. This is a minor gap (see NEW-M2 below).

No resolutions were inadequate or contradictory.

## New Issues Found

### HIGH

None. There are no issues that block implementation.

### MEDIUM

**NEW-M1: `mixer.mix_time_elapsed` data type conflict between Section 3.1.2 and Appendix A.**

Section 3.1.2 (F&B Profile Registers, Mixer registers table) defines `mixer.mix_time_elapsed` at HR 1010-1011 as `float32` with CDAB byte order. Appendix A (Mixer holding registers table) defines the same register at HR 1010-1011 as `uint32` with CDAB byte order. These are different wire formats. A `float32` IEEE 754 encoding of 1800.0 seconds and a `uint32` encoding of 1800 produce different byte patterns. The developer needs to pick one.

**Recommendation:** Use `uint32` (consistent with other elapsed-time signals like `cip.cycle_time_elapsed` at HR 1506-1507 which is `uint32` in Appendix A). Update Section 3.1.2 to match Appendix A. However, Appendix A also shows `cip.cycle_time_elapsed` as `uint32` but Section 3.1.2 shows it as `float32`. Both need alignment.

**Actually, on further inspection:** Section 3.1.2 lists `cip.cycle_time_elapsed` at HR 1506-1507 as `float32`, and Appendix A lists it as `uint32`. These are the same kind of discrepancy. The developer implementing the register map will hit this on both signals. Pick one type and update both locations. I suggest `float32` for consistency with all other non-counter holding registers, since elapsed time is a continuous measurement, not a monotonic counter. But the PRD author should decide.

**NEW-M2: Scenario scheduling gap — what happens when overlapping scenarios span a shift change?**

Appendix F Phase 4 specifies: "Scenarios crossing shift boundaries continue into the next shift." Section 5.9 specifies that shift changes create a 5-15 minute Idle period. If a dryer drift scenario is active during a shift change, does the drift continue during the Idle period? The drift is temperature-based, not state-dependent — the oven doesn't cool down during a brief idle. But the machine is Idle, so should the waste rate increase (Section 5.4 step 4) pause during Idle because no production occurs? The developer will need to decide whether scenario effects that depend on production state (waste rate) pause during state-driven idle periods while physical effects (temperature drift) continue.

**Recommendation:** Add a one-sentence clarification in Section 5 or Appendix F Phase 4: "Physical scenarios (temperature drift, bearing wear) continue their progression during non-Running states. Production-impact effects (waste rate increase) only apply during Running state."

**NEW-M3: F&B Input Register addresses for energy overlap with oven registers.**

Section 3.1.3 (F&B Input Registers) shows:
- IR 100-106: Oven temperatures and setpoints
- IR 110-111: Chiller temps
- IR 115: CIP wash temp
- IR 120-121: `energy.line_power` (float32)

Appendix A (F&B Input Registers) shows the identical layout. This is consistent, but note that the packaging profile uses IR 10-11 for `energy.line_power` and the F&B profile uses IR 120-121. The address space is separated correctly. No actual conflict — this is confirmed consistent across both documents. **Retracted — not an issue on re-examination.**

**NEW-M3 (replacement): Oven output power signal — specified in Appendix F Phase 3 but not in any register map or signal list.**

Appendix F Phase 3 specifies: "Oven output power signal added (IR 2 on Eurotherm multi-slave per Section 3.1.6)." Section 3.1.6 references IR 2 = output power for each Eurotherm zone controller (UID 11, 12, 13). But:
- The F&B signal list (Section 2b.3) does not include an `oven.zone_N_output_power` signal.
- Appendix A (F&B Input Registers) does not list IR 2 for output power.
- The multi-slave IR 0 = PV and IR 1 = SP are documented but IR 2 is only mentioned in Section 3.1.6 and the Phase 3 resolution note.

The developer will create the Eurotherm multi-slave servers per Section 3.1.6 and expect to serve IR 2 (output power), but there's no signal model, no range, no units, and no equipment signal definition for it. They'll need to invent one.

**Recommendation:** Either add `oven.zone_1_output_power` / `zone_2` / `zone_3` to Section 2b.3's signal table (range 0-100%, units %, rate 5s, Modbus IR) and Appendix A, or explicitly defer it and remove the Phase 3 line item. Output power is a simple correlated follower of (setpoint - actual) / max_output, so the model is trivial, but the signal definition is missing.

**NEW-M4: Energy registers — packaging profile has energy at HR 600-603, but Section 3.1.1 says addresses 100-599 return exception 0x02 for F&B profile.**

Section 3.1.1 says: "When the F&B profile is active, addresses 100-599 return the same exception [0x02]. Energy registers (600-699) are always active regardless of profile."

This is clear. But the energy registers at HR 600-601 and 602-603 also appear in Appendix A under "Shared Registers (Both Profiles)" and separately under Packaging profile at addresses 600-603 "for backwards compatibility." The note says this is intentional. Good. No conflict. **Retracted — confirmed consistent.**

**NEW-M4 (replacement): `slitter.web_tension` listed as OPC-UA only in Section 2.11 but has Modbus HR 502-503 in Section 3.1.2 and Appendix A.**

Section 2.11 (Signal Summary table) lists `slitter.web_tension` under "OPC-UA only" with 4 OPC-UA-only signals: `press.web_tension, press.registration_error_x, press.registration_error_y, slitter.web_tension`. But Section 3.1.2 (Slitter registers table) shows `slitter.web_tension` at HR 502-503 as float32, and Appendix A confirms this.

Furthermore, Section 2.11 also lists `slitter.speed` under "Modbus TCP + OPC-UA" with 8 dual-protocol signals. So `slitter.web_tension` should be in that category too, since it appears in both Modbus HR and OPC-UA.

This is a signal summary table error. The detailed register map and node tree are correct. The summary table understates the Modbus HR signal count and overstates the OPC-UA-only count. The developer looking only at Section 2.11 would incorrectly skip the Modbus mapping for slitter.web_tension.

**Recommendation:** Move `slitter.web_tension` from "OPC-UA only" to "Modbus TCP + OPC-UA" in Section 2.11. OPC-UA only count drops to 3 (press.web_tension, registration_error_x, registration_error_y). Modbus+OPC-UA count rises to 9.

**NEW-M5: `press.web_tension` is listed as Modbus HR 102-103 in Section 3.1.2 but listed as "OPC-UA only" in Section 2.11.**

Same issue as NEW-M4 but for press.web_tension. Section 3.1.2 shows HR 102-103 for `press.web_tension`. Appendix A confirms this. But Section 2.11 says it's OPC-UA only. The Section 2.2 signal table says it's OPC-UA at 500ms rate, and doesn't mention Modbus — but the register map clearly has it.

Looking at Section 2.2's signal table column "Protocol", `press.web_tension` says "OPC-UA" and `press.line_speed` says "Modbus HR + OPC-UA". But both appear in the Modbus register map.

**Recommendation:** The signal summary table (Section 2.11) and the per-signal protocol columns in Section 2.2 need to match the register map. Either remove `press.web_tension` from the Modbus register map (if it's truly OPC-UA only) or update Sections 2.2 and 2.11 to reflect dual-protocol status. Given that the register map is the canonical source for Modbus and it consistently includes these signals, update the summary tables.

### LOW

**NEW-L1: Section 2.11 signal count says "Modbus TCP + OPC-UA: 8" but lists `press.line_speed, press.machine_state, laminator.nip_temp, laminator.nip_pressure, laminator.tunnel_temp, laminator.web_speed, laminator.adhesive_weight, slitter.speed`.**

That's 8 signals. But as noted in NEW-M4 and NEW-M5, `press.web_tension` and `slitter.web_tension` also appear in both Modbus and OPC-UA. If those are moved, the count becomes 10. Additionally, `slitter.reel_count` has both Modbus HR (510-511) and OPC-UA (`Slitter1.ReelCount`), so it should also be counted as dual-protocol. The summary table needs a pass to reconcile with the detailed register/node maps.

**NEW-L2: Docker Compose file in Section 6.3 still has `version: "3.8"` which is deprecated.**

Docker Compose V2 (the current standard) ignores the `version` field. Including it generates a deprecation warning. Trivial cosmetic fix: remove the `version: "3.8"` line.

**NEW-L3: Appendix B says `EnumStrings` property is set on state enum nodes, but I-L20 resolution said "Budget 0.5 days in Phase 2. If asyncua doesn't support it cleanly, skip."**

Appendix B states definitively: "State enum nodes (`*.State`) use `UInt16` data type with `EnumStrings` property listing the valid state names." But the consolidated issues resolution says this is contingent on asyncua support. The PRD should either: (a) make EnumStrings a SHOULD rather than a MUST, or (b) remove the contingency language from the issues document. Since the issues document is non-normative, this is cosmetic — the developer will try to implement EnumStrings and fall back if asyncua can't do it. But it's a contradiction between two documents.

**NEW-L4: Section 3.3 says "The simulator runs an embedded MQTT broker on 0.0.0.0:1883" as the first option, but the I-H7 resolution dropped the embedded broker entirely.**

Section 3.3 opens with: "The simulator runs an embedded MQTT broker on `0.0.0.0:1883` (configurable). Alternatively, it publishes to an external broker." This language was written pre-I-H7 resolution. The consolidated issues say amqtt is dropped and Mosquitto sidecar is the broker. Section 7.2 and 6.3 correctly reflect the Mosquitto sidecar. Section 3.3's opening sentence should be updated to say "The simulator publishes to an external MQTT broker (Mosquitto sidecar, default `mqtt-broker:1883`)" to match the architecture decision.

**NEW-L5: Appendix E project structure lists `src/models/` with 9 model files but Section 4.2 defines 12 signal model types (plus bang_bang_hysteresis, thermal_diffusion, string_generator).**

Appendix E lists: `steady_state.py, sinusoidal.py, first_order_lag.py, ramp.py, random_walk.py, counter.py, depletion.py, correlated.py, state.py`. That's 9 files. The 12 model types include: steady_state, sinusoidal, first_order_lag, ramp, random_walk, counter, depletion, correlated_follower, state_machine, thermal_diffusion, bang_bang_hysteresis, string_generator. Missing from the project structure: `thermal_diffusion.py`, `bang_bang.py`, `string_generator.py`. The developer will create them anyway, but Appendix E should list all 12 for completeness.

**NEW-L6: Slitter scheduling parameters `slitter.schedule_offset_hours` and `slitter.run_duration_hours` not in Appendix D Configuration Reference.**

Section 2.4 specifies these configuration parameters for slitter scheduling (added in I-M4 resolution). Appendix D's signal model parameters and scenario parameters don't include them. The developer will look in Appendix D for slitter scheduling config and not find it. They should be added to Appendix D under equipment-level parameters.

**NEW-L7: The `chiller.compressor_power` signal referenced in Section 4.6 (F&B correlation model) does not exist in the F&B signal list.**

Section 4.6 says: "`chiller.compressor_power` correlates inversely with `chiller.room_temp` delta from setpoint." The F&B signal list (Section 2b.7) has `chiller.compressor_state` (bool, on/off coil) but no `chiller.compressor_power` signal. Similarly, Section 5.14.5 (Chiller Door Alarm) references `chiller.compressor_power` increasing. Section 5.14.7 (Cold Chain Break) references `chiller.compressor_power` dropping to 0.

This signal simply doesn't exist. The scenarios and correlation model reference a phantom signal. The developer will need to either: (a) add `chiller.compressor_power` as a new signal (straightforward — derived from compressor state and room temp delta), or (b) rewrite the correlation model and scenarios to use `chiller.compressor_state` (bool) and derive compressor effort from the temperature dynamics instead. Option (b) is simpler and more consistent with the existing signal list.

**Recommendation:** Replace references to `chiller.compressor_power` with `chiller.compressor_state` in Sections 4.6, 5.14.5, and 5.14.7. The compressor runs harder by running longer, not by varying a continuous power signal. The bang-bang model already captures this: longer ON cycles = harder work.

**NEW-L8: Section 5.14.4 (Seal Integrity Failure) references `sealer.reject_count` which doesn't exist.**

Section 5.14.4 step 4 says: "`sealer.reject_count` spikes as the quality system detects failed seals." The F&B signal list (Section 2b.5) has no `sealer.reject_count`. The closest signal is `filler.reject_count` (Section 2b.4) which is the filler's reject counter. In a real factory, seal rejects would be caught by the checkweigher/QC station downstream, incrementing `qc.reject_total`. The scenario should reference `qc.reject_total` or `filler.reject_count` rather than a non-existent sealer counter.

**NEW-L9: README.md still says "Version: 1.0" and "Status: Draft" — should reflect the post-review version.**

The README hasn't been updated to version 1.1 as noted in this review's header. The consolidated issues document refers to "PRD Version: 1.0 (22 files, ~5,300 lines)." After all the updates, the PRD should be versioned 1.1.

## Section-by-Section Notes

### Section 1 (Overview and Goals)
Clean. The LLM agent demo use case is well-motivated. The reference data constraint is clear and repeated enough that no developer will accidentally include proprietary reference data. No issues.

### Section 2 (Packaging Layout)
Signal table for the press (21 signals) is well-defined. Equipment descriptions include reference data justification. The Section 2.11 summary table has the dual-protocol counting issues noted in NEW-M4/M5/L1 but otherwise comprehensive. The 47-signal total is correct when reconciled against the detailed tables.

### Section 2b (F&B Layout)
65 signals across 9 equipment groups. Each equipment section has clear signal tables, state machines, and behavioural descriptions. The "How the data behaves" sections are excellent — they tell the developer exactly what the signal should look like, not just its range. The F&B scenario sketches (Section 2b.15) are consistent with the full scenario definitions in Section 5.14. No issues beyond the phantom signals noted above.

### Section 3 (Protocol Endpoints)
Comprehensive. Modbus register maps, OPC-UA node tree, and MQTT topics are all cross-referenced to appendices. The multi-slave simulation (Section 3.1.6), FC06 rejection for float32 registers (Section 3.1.2), and error injection (Section 3.1.7) are precisely specified. The embedded broker language in Section 3.3 needs updating per NEW-L4.

### Section 3a (Network Topology)
This is one of the strongest sections in the PRD. The realistic vs collapsed mode, per-controller connection behaviour, clock drift model, scan cycle artefacts, and load profile summary give the developer everything needed. The network diagrams are clear. The port mapping table is complete.

### Section 4 (Data Generation Engine)
The signal models are well-defined with explicit formulas. The thermal diffusion model (Section 4.2.10) convergence criterion ("sum terms until T(0) falls within 1C of T_initial") is clear and implementable. The Cholesky pipeline ordering (Section 4.3.1) is correctly specified. The time-varying covariance model (Section 4.3.2) uses a log-space random walk which is the right approach. The simulated time invariant (Section 4.1, principle 5) is clearly called out and will prevent the most common signal model bug.

### Section 5 (Scenario System)
17 scenario types across both profiles. Each has frequency, duration, and a numbered sequence of signal effects. The scenario scheduling (Section 5.13), contextual anomalies (Section 5.16), and intermittent faults (Section 5.17) are thorough. The allergen changeover (Section 5.14.8) with its transition matrix is a nice touch.

### Section 6 (Configuration)
YAML config structure is clear. Docker Compose is complete with health checks and depends_on. Environment variable override table is complete. Quick start commands are practical.

### Section 7 (Technology Stack)
Python decision is well-justified. The amqtt rejection and Mosquitto selection are documented with rationale. Dependency table is complete with minimum versions. The uvloop conditional import is specified.

### Section 8 (Architecture)
Component diagram and data flow are clear. The single-writer, no-lock concurrency model is correct for asyncio. The EquipmentGenerator interface is well-defined. The `get_protocol_mappings()` return type is now specified (resolved from I-M9).

### Section 9 (Non-Goals)
Clear boundaries. The "not a digital twin (yet)" framing is honest. The future direction section (9.4) provides useful context without scope-creeping the MVP.

### Section 10 (Data Quality Realism)
11 data quality injection types, each with clear specification. Sensor disconnect sentinel values are per-signal configurable. Stuck sensor injection is well-specified. Partial Modbus responses are a nice realistic touch.

### Section 11 (Success Criteria)
Measurable criteria for each category. The 7-day continuous operation criterion with specific bounds (RSS < 2x, CPU < 20%) is testable. The reproducibility criterion (byte-identical for 1M data points) is precise.

### Section 12 (Evaluation Protocol)
The event-level matching approach is correct (point-level metrics inflate scores). Tolerance windows with pre-margin and post-margin are well-specified. The severity-weighted metrics and latency targets add value for future benchmarking. The random baseline requirement is excellent — it prevents overinterpreting mediocre detectors.

### Section 13 (Test Strategy)
New section added per I-H1 resolution. The test pyramid is practical: property-based testing for signal models, real client libraries for protocol integration tests, smoke test for end-to-end. The CI pipeline target (under 5 minutes) is realistic. The nightly 24-hour stability run is a good addition. The explicit "what we do not test" section prevents scope creep.

### Appendix A (Modbus Register Map)
Complete for both profiles. The data type inconsistency noted in NEW-M1 is the only issue. Address ranges are non-overlapping. Shared energy registers are clearly documented. The summary table at the end is useful.

### Appendix B (OPC-UA Node Tree)
Full tree for both profiles with string NodeIDs. Attribute conventions (AccessLevel, MinimumSamplingInterval, EURange, EngineeringUnits) are specified. The EnumStrings contingency is noted in NEW-L3.

### Appendix C (MQTT Topic Map)
Complete topic list with QoS and retain flags. The batch vibration topic alternative is documented. The JSON payload schema is clear.

### Appendix D (Configuration Reference)
Exhaustive. Every signal model type has a configuration example. Noise distribution parameters, peer correlation matrices, clock drift, scan cycle artefacts, scenario parameters, and data quality injection parameters are all covered. The slitter scheduling omission (NEW-L6) is the only gap.

### Appendix E (Project Structure)
Clear directory layout. Missing 3 model files (NEW-L5) is the only issue. The test directory structure parallels the source structure.

### Appendix F (Implementation Phases)
13-week timeline with 6 phases. Each phase has a clear goal, deliverables, and exit criteria. Phase 0 validation spikes are an excellent addition. The phase boundaries and exit criteria make it possible to track progress without ambiguity.

### Appendix G (Reference Documents)
Complete list of supporting research documents with paths and relevance descriptions.

## Issue Summary

| Severity | Count | Impact |
|----------|-------|--------|
| HIGH     | 0     | —      |
| MEDIUM   | 5 (2 retracted → 3 real) | Minutes of developer investigation each |
| LOW      | 9     | Cosmetic or trivial fixes |

## Verdict

**Ship it.** 

The PRD is implementation-ready. The three MEDIUM issues (register data type conflict, oven output power signal gap, signal summary table miscount) should be fixed in a cleanup pass before handing to the development team — they'll take 30 minutes total. The LOW issues can be fixed as encountered during implementation.

The quality of this PRD is well above what I typically see for production software, let alone a development tool. The signal models are mathematically specified, the protocol specifications are precise down to byte order and register address, the scenarios are described with numbered sequences, and the test strategy is pragmatic. A developer reading this document will know what to build and can start writing code on day one.

My confidence that this PRD will survive contact with implementation without major rework: **high**. The Phase 0 validation spikes will catch any library feasibility issues before architecture is committed. The 13-week timeline has appropriate buffer for the F&B profile and scenario system complexity. The test infrastructure is established from Phase 1, which prevents the late-stage "we have no tests" crisis.

One suggestion for the development team: start with the packaging profile only through Phase 2 before touching F&B. The packaging profile exercises all three protocol adapters, all signal model types, and the core scenario engine. Get that working end-to-end first. The F&B profile is an incremental addition of equipment generators and scenarios — it should not require any architectural changes if the plugin pattern is implemented correctly.
