# Round 2 Review: Collatr Factory Simulator PRD

**Date:** 2 March 2026
**Reviewer:** Senior Industrial Automation Engineer (20+ years, UK manufacturing)
**Scope:** Full PRD, 22 files, 5,112 lines
**Previous grade:** B+
**Context:** All 32 items from the Round 1 combined review have been marked RESOLVED.

---

## Overall Grade: A-

## Summary

The team fixed every item from Round 1. Most fixes are correct and well integrated. The PRD has matured from a strong draft into a credible engineering specification. The network topology section is new and good. The F&B profile is thorough. The data generation engine now handles noise distributions, scan cycle artefacts, peer correlation matrices, time-varying covariance, and sensor quantisation. These additions close the statistical gaps that the data scientist flagged. A production engineer at a food company or a packaging converter would read this and recognise their factory. Three issues remain that would cause a prospect to question the document. None are blockers. All are fixable in a day.

---

## Section-by-Section Findings

### 01 - Overview and Goals

Clean. No issues. The LLM agent demo use case is well justified. The Progrow competitive positioning is clear.

### 02 - Simulated Factory Layout (Packaging)

The signal table is solid. Forty-seven signals across seven equipment groups. The ranges, units, and sample rates match real equipment.

**Laminator oven temperature.** Section 2.3 says "adhesive drying oven temp" with range 40-100C. This is labelled as a solvent-free laminator. Solvent-free laminators do not have a drying oven. They have a heated nip roller and sometimes a conditioning tunnel. The signal name `laminator.oven_temp` would confuse anyone who runs a Nordmeccanica or Comexi solventless machine. On a solvent-based laminator, the oven is real and 40-100C is correct. On solvent-free, the relevant temperature is the adhesive application roller, typically 40-60C. Rename to `laminator.tunnel_temp` or clarify that this models a solvent-based machine. Medium issue.

**Signal count verification.** The summary table in 2.11 lists protocol assignments. I counted the Modbus-only column. It says 19 signals but lists 17 individual signal names. The discrepancy: `press.machine_state` and `press.line_speed` appear in the "Modbus TCP + OPC-UA" row (7 signals) but `press.line_speed` also appears in the Modbus-only list. The text in section 2.2 says `press.line_speed` is "Modbus HR + OPC-UA". The summary table lists it under Modbus TCP only. One of these is wrong. Low issue but would annoy an integrator counting registers.

### 02b - Factory Layout (Food and Beverage)

Strong section. The equipment selection matches a real UK chilled ready meal line. Compleat Food Group, Greencore, Raynor Foods, Samworth Brothers are the right reference companies. The signal definitions are realistic.

**Mixer speed range.** Section 2b.2 says mixer speed range 0-3000 RPM and describes a high-shear mixer (Silverson, GEA). A Silverson high-shear mixer does run at those speeds. But in Appendix D the batch_cycle scenario says `mixer_speed_rpm: [30, 120]`. That is a low-speed paddle mixer range, not a high-shear mixer. The configuration contradicts the equipment description. Pick one. If this models a Silverson, production mixing speeds are 1000-3000 RPM with loading at 50-100 RPM. If this models a low-shear paddle mixer (more common for ready meal sauce batches), the 0-3000 range in the signal table is wrong and should be 0-200 RPM. A prospect running a Greencore sauce kitchen would notice. Medium issue.

**Sealer vacuum level.** Range is -0.9 to 0 bar. This is gauge pressure (vacuum). Correct convention. The unit "bar" for vacuum is acceptable in UK food manufacturing. No issue.

**Missing: product weight check.** Section 2b.6 describes the checkweigher and mentions the Weights and Measures Act 1985 and the "e" mark rules. This is correct and shows good regulatory knowledge. One gap: the PRD does not mention the Three Packers Rules (TN/28 or WELMEC 6.7). These define the tolerable negative error (TNE) thresholds by nominal weight band. For a 400g ready meal, TNE is 15g (3.75%). The checkweigher rejects anything below nominal minus 2xTNE (370g). This could be added to the fill weight drift scenario parameters for extra credibility. Low issue.

**Blast chiller vs cold room.** Section 2b.7 is titled "Refrigeration" and describes a cold room with compressor cycling. In Appendix E, the generator file is named `chiller.py` and described as "Blast chiller signals." A blast chiller and a cold room are different equipment. A blast chiller rapidly drops product temperature from 70C to below 5C in 90 minutes. A cold room holds product at 0-5C for storage. The signal behaviour described (sawtooth cycling, door open events, defrost) matches a cold room, not a blast chiller. The naming should be consistent. The file should be `cold_room.py` or the section should describe blast chilling behaviour (no cycling, continuous compressor, product temperature as the primary signal). A food production engineer would catch this. Medium issue.

### 03 - Protocol Endpoints

Well structured. The address space partitioning is sensible. Packaging at 100-699, F&B at 1000-1999, shared energy at 600-699. The multi-slave simulation for oven zones is correct.

**Oven setpoint register addresses.** In Section 3.1.2, the oven setpoint registers are at HR 1120-1125. In Appendix A, they are at HR 1110-1115. These do not match. One was updated and the other was not. The implementer will build the wrong thing. High issue.

**F&B input register count.** Section 3.1.3 says the F&B input registers are at addresses 100+. The packaging profile input registers are at 0-11. The F&B table in Section 3.1.3 lists 6 entries (oven temps x3, product core temp, CIP wash temp, energy). Appendix A lists 11 F&B input registers (oven temps x3, oven setpoints x3, product core temp, chiller room temp, chiller setpoint, CIP wash temp, energy). Appendix A adds oven setpoints, chiller temps to the input register map that Section 3 does not mention. Appendix A is the more complete version. Section 3 should be updated to match. Low issue.

### 03a - Network Topology

This is the strongest new section. It reads like someone who has crawled around factory floor cabinets.

**Controller selection is credible.** Siemens S7-1500 for the main press (dual Modbus + OPC-UA), S7-1200s for auxiliary machines (Modbus only), Allen-Bradley CompactLogix with CDAB byte order for the mixer, Eurotherm 3504s behind a Moxa gateway for oven zones, Danfoss for refrigeration, Schneider PM5560 for energy. These are the exact brands and models I would expect to find in a UK food or packaging factory.

**Connection limits.** S7-1200 at 3 connections, S7-1500 at 16, Eurotherm at 2 per slave behind gateway. Correct. The S7-1200 connection limit is the one that catches people in the real world. Three is not many when you have a SCADA system, an HMI, and CollatrEdge all trying to connect.

**Clock drift.** Eurotherm 3504 at 2-10 seconds per day initial offset and 5 s/day drift rate. I have seen worse. Real Eurotherms drift badly. Five seconds per day is conservative. Ten would be more realistic. The Siemens S7-1500 at 0.1-0.5 s/day is right for an NTP-synced PLC. Good.

**Scan cycle artefacts.** Section 3a.8 describes register update quantisation, phase jitter, and inter-signal skew. This is a detail that most simulator PRDs miss. It matters for sub-second analytics. The 100ms Eurotherm scan cycle is correct. The 10ms S7-1500 scan is typical for a fast CPU. Good.

**One observation.** The F&B network diagram shows the QC station (Mettler Toledo) on OPC-UA at 10.0.2.50:4840 but CollatrEdge is also at .50. CollatrEdge and the QC station cannot share the same IP. This is the diagram only; the port mapping table in 3a.4 correctly maps QC to simulator port 4842. The diagram is misleading. Low issue.

### 04 - Data Generation Engine

Substantial improvement from Round 1. The team added: noise distributions (Gaussian, Student-t, AR(1)), speed-dependent sigma, sensor quantisation, within-regime drift, long-term calibration drift, thermal diffusion for product core temperature, bang-bang hysteresis for compressor cycling, string generator for batch IDs, peer correlation mixing matrices, time-varying covariance, and transport lag.

**Thermal diffusion model.** Section 4.2.10 uses the first term of the Fourier series for 1D heat conduction. The formula is correct. Thermal diffusivity of 1.4e-7 m^2/s for a meat-based product is in the right range (literature values for minced beef: 1.3-1.5e-7). Half-thickness of 25mm is reasonable for a single-portion tray. The note that it is simplified is honest. A food engineer would accept this for a demo.

**CIP conductivity profile.** Section 4.6 explicitly describes the conductivity curve through each CIP phase. Pre-rinse near zero, caustic ramp up, hold at 80-150 mS/cm, exponential decay during rinse, final rinse below 5 mS/cm. This is correct. The first-order lag for the rinse decay is the right model. On a real CIP skid, the conductivity during rinse drops as a dilution curve, which is first-order.

**Atomic recipe changes.** Section 4.6 describes grouping setpoint changes into a recipe that updates at the same simulation tick. This is correct. A Eurotherm recipe download writes all parameters in a burst.

**AR(1) noise scaling.** Section 4.2.11 defines AR(1) noise with scaling `sqrt(1 - phi^2)` to maintain marginal variance. Correct. Without this scaling, AR(1) noise at phi=0.9 would have marginal variance 5.3x the intended sigma. Good attention to detail.

### 05 - Scenario System

Comprehensive. The packaging scenarios cover the standard operating events. The F&B scenarios are well chosen.

**Bearing wear exponential model.** Section 5.5 now uses an exponential degradation curve with base_rate and acceleration constant k. The description of the hockey-stick shape (slow for weeks, rapid in final days) matches real IMS/NASA data. Good fix from Round 1.

**Allergen changeover.** Section 5.14.8 describes mandatory CIP on allergen transitions. The transition logic table is correct: allergen-true to allergen-false requires CIP, allergen-true to allergen-true (different allergen set) requires CIP. This is a BRC requirement. A food safety manager would approve this logic.

**Intermittent faults.** Section 5.17 describes three-phase progression: sporadic, frequent, permanent. This matches real bearing behaviour. The connection to the bearing wear scenario (intermittent precedes degradation) is well designed.

**Micro-stops.** Section 5.15 is correct. Machine state stays Running. Only speed dips. This is the behaviour that real OEE systems miss. Poisson process with configurable mean interval. Good.

**Missing scenario: material splice.** On a real flexo press, the operator splices a new roll onto the running web when the unwind roll runs low. The splice passes through the press and causes a brief tension disturbance and sometimes a registration blip. The PRD tracks unwind_diameter depleting but never triggers a splice event. The splice is the link between reel change and transient disturbance. A packaging converter would expect to see splice events in the data. Low issue.

### 06 - Configuration

Adequate. The YAML structure is clear. The quick start section is helpful.

**Inconsistency with Appendix D.** Section 6 shows a `factory.yaml` with scenario parameters. Appendix D shows a more detailed configuration reference. Some parameter names differ. Section 6 uses `drift_degrees` for dryer drift. Appendix D uses `max_drift_c`. These should be consistent. Low issue.

### 07 - Technology Stack

Python is the right choice. `pymodbus` and `asyncua` are mature. No issues.

### 08 - Architecture

The component diagram and data flow are clear. The single-writer asyncio model avoids concurrency issues. The plugin architecture for equipment generators is clean.

### 09 - Non-Goals

Well scoped. The deferrals (CNC, pharma, Sparkplug B, EtherNet/IP, MTConnect) are reasonable. The F&B promotion to Phase 1 is noted.

### 10 - Data Quality Realism

Good coverage. Communication drops, sensor noise, counter rollovers, duplicates, exceptions, timezone offsets, sensor disconnects with sentinel values, stuck sensors, partial Modbus responses. The stuck sensor description is clear: value freezes, status codes remain Good. This is the hard detection case.

**Partial Modbus responses.** Section 10.11 describes returning fewer registers than requested. This does happen with serial gateways. The description of causes is accurate. The probability of 0.01% is reasonable for a gateway under load.

### 11 - Success Criteria

Clear and measurable. The 24-hour connectivity test, the domain expert visual test, the 7-day continuous operation test. The F&B-specific checks (mixer batch cycles, fill weight distribution, CIP profiles) are well chosen.

**MQTT topic count.** Section 11.1 says "17 packaging MQTT topics." The MQTT topic map in Appendix C lists 16 per-signal topics plus 1 batch vibration topic = 17. But the F&B section says "13 F&B MQTT topics." The topic map in Appendix C lists 11 coder + 2 env = 13. The counts match. Good.

**Modbus slave count.** Section 11.1 says "mixer on slave 1, oven on slave 2, filler on slave 3, sealer on slave 4." But Section 3a.3 shows the oven using three slaves (UID 1/2/3 on the oven gateway at .20) and the filler on a separate IP (.30) with UID 1. The success criteria description does not match the network topology. The success criteria should say: "mixer at .10 UID 1 (CDAB), oven gateway at .20 UID 1/2/3/10, filler at .30 UID 1, sealer at .31 UID 1, chiller at .40 UID 1, CIP at .32 UID 1." High issue because someone will write the wrong integration test.

### 12 - Evaluation Protocol

Solid. Event-level metrics (not point-level) are the right choice. The clean/impaired pairing design enables ablation studies. The three recommended run configurations (Normal, Heavy Anomaly, Long-Term Degradation) cover the key evaluation axes.

### Appendix A - Modbus Register Map

Complete. Both profiles mapped. Address ranges do not overlap. Byte order annotations are correct (CDAB for mixer, ABCD everywhere else).

**Oven setpoint register mismatch.** As noted above, Section 3 says HR 1120-1125 for oven setpoints. Appendix A says HR 1110-1115. The appendix is the register map. It should be authoritative. Section 3 should be corrected.

### Appendix B - OPC-UA Node Tree

Complete. Node IDs follow a consistent naming convention. Both packaging and F&B trees are present. The `Energy` node placement under `PackagingLine` in the tree differs from Section 3.2.1 where it sits at the top level as a peer. Appendix B shows `PackagingLine.Energy` and `FoodBevLine.Energy`. Section 3.2.1 says "The Energy node sits at the top level (peer to PackagingLine and FoodBevLine)." These are different structures. The implementation will follow one. The other is wrong. Medium issue.

### Appendix C - MQTT Topic Map

Complete. The packaging topic prefix uses `line3` (`collatr/factory/demo/line3/`). Section 3.3.1 says the packaging line_id is `packaging1`. These do not match. Section 3 says `collatr/factory/demo/packaging1/coder/state`. Appendix C says `collatr/factory/demo/line3/coder/state`. One was updated and the other was not. High issue because CollatrEdge needs the correct topic filter.

### Appendix D - Configuration Reference

Thorough. Every signal model has a configuration example. Clock drift, scan cycle, peer correlation, and scenario parameters are all documented. The CIP conductivity `final_rinse_conductivity_max` is set to 50 in the config but described as "below 5 mS/cm" in Sections 2b.8 and 4.6. The config value 50 has units "uS/cm" (per the comment). 50 uS/cm = 0.05 mS/cm, which is far below the 5 mS/cm described in the text. Either the config should be 5000 uS/cm (= 5 mS/cm) or the units in the config should be mS/cm with value 5.0. Medium issue.

### Appendix E - Project Structure

Clean. The file naming matches the equipment described in the PRD. The blast chiller/cold room naming issue applies here too (`chiller.py` in the project structure).

### Appendix F - Implementation Phases

Realistic timeline. Ten weeks for a full implementation of both profiles, all protocols, all scenarios, and documentation. Phase 4 includes the network topology manager with realistic mode. This is the critical phase for integration testing quality.

### Appendix G - Reference Documents

Complete list of supporting research.

---

## Consolidated Issue List

| # | Section | Severity | Issue |
|---|---------|----------|-------|
| 1 | 03 vs App A | **High** | Oven setpoint register addresses differ: Section 3 says HR 1120-1125, Appendix A says HR 1110-1115. |
| 2 | 03 vs App C | **High** | MQTT topic prefix mismatch: Section 3 uses `packaging1`, Appendix C uses `line3`. |
| 3 | 11 | **High** | Success criteria lists wrong Modbus slave assignments for F&B. Does not match Section 3a.3 network topology. |
| 4 | 02b vs App E | **Medium** | Blast chiller vs cold room naming inconsistency. Section 2b.7 describes cold room behaviour but Appendix E names generator `chiller.py` and says "Blast chiller." |
| 5 | 02 | **Medium** | Laminator described as solvent-free but has a drying oven signal. Solvent-free laminators do not have drying ovens. |
| 6 | 02b vs App D | **Medium** | Mixer speed: equipment description says high-shear (1000-3000 RPM), scenario config says 30-120 RPM. |
| 7 | 03 vs App B | **Medium** | OPC-UA Energy node placement: Section 3 says top-level peer, Appendix B shows it under each profile tree. |
| 8 | App D | **Medium** | CIP conductivity threshold units: config says 50 (uS/cm = 0.05 mS/cm) but text says "below 5 mS/cm." |
| 9 | 02 | **Low** | Signal count in summary table 2.11: `press.line_speed` listed as Modbus-only but described as Modbus HR + OPC-UA. |
| 10 | 03a | **Low** | F&B network diagram shows CollatrEdge and QC station both at .50. |
| 11 | 03 vs App A | **Low** | F&B input register list in Section 3.1.3 is shorter than Appendix A. Appendix A adds oven setpoints and chiller temps. |
| 12 | 06 vs App D | **Low** | Configuration parameter naming: Section 6 uses `drift_degrees`, Appendix D uses `max_drift_c`. |
| 13 | 05 | **Low** | No material splice scenario. Unwind diameter depletes but splice event is not modelled. |
| 14 | 02b | **Low** | Checkweigher description does not mention TNE thresholds from TN/28 or WELMEC 6.7. |

---

## Specific Parameter Corrections

| Section | Parameter | Current Value | Recommended Value | Rationale |
|---------|-----------|---------------|-------------------|-----------|
| App D | `cip_cycle.final_rinse_conductivity_max` | 50 (uS/cm) | 5000 uS/cm or 5.0 mS/cm | Text says "below 5 mS/cm." 50 uS/cm is 100x too low. |
| App D | `batch_cycle.mixer_speed_rpm` | [30, 120] | [1000, 2500] or change equipment to low-shear | Must match mixer type described in Section 2b.2 (high-shear). |
| 03 | Oven setpoint HR | 1120-1125 | 1110-1115 | Match Appendix A register map. Appendix is authoritative. |
| 03a | Eurotherm clock drift | 2-10 s/day | 5-15 s/day | Real Eurotherms drift worse. 10 s/day is common. 15 is not unusual. |
| App C | Packaging topic prefix | `line3` | `packaging1` | Match Section 3.3.1 convention. Or update Section 3 to match. |

---

## Fixes Verified as Correct

All eight original blockers are resolved correctly:

1. **Ink pressure target.** Section 4.2.1 now uses target ~835 mbar, sigma ~60 mbar, range 0-900 mbar. Correct.
2. **Simulated time invariant.** Section 4.1 principle 5 explicitly states all models use simulated time. The random walk warning about `sqrt(dt)` scaling is a good addition.
3. **Product core temperature.** Section 4.2.10 defines a thermal diffusion model with Fourier series solution. The physics is correct.
4. **Step-wise ramps.** Section 4.2.4 adds configurable step quantisation with overshoot and dwell time. This produces realistic operator-driven speed ramps.
5. **Micro-stops.** Section 5.15 defines brief speed dips without state change. Poisson process scheduling. Correct.
6. **Ground truth event log.** Section 4.7 defines JSONL sidecar with all event types. Section 12 defines evaluation protocol against ground truth.
7. **Within-regime drift.** Section 4.2.1 adds slow random walk with mean reversion on steady-state targets.
8. **Sentinel values.** Section 10.9 defines sensor disconnect with configurable sentinel values (6553.5, -32768, 9999.0, 0.0).

Both conditional blockers are resolved:

9. **Noise distributions.** Section 4.2.11 adds Student-t and AR(1). Default assignments per signal category. Speed-dependent sigma.
10. **Contextual anomalies and intermittent faults.** Sections 5.16 and 5.17 add both. Five contextual anomaly types. Four intermittent fault types with three-phase progression.

All 22 nice-to-have items are resolved. I verified the implementations are correct.

---

## What Would Make a Prospect Say "That Is Not How It Works"

1. **The mixer speed range.** A food production engineer would look at the scenario config, see 30-120 RPM, and know that is a paddle mixer. Then they would read the equipment description saying Silverson high-shear at 3000 RPM and lose confidence. Fix the mismatch.

2. **The blast chiller naming.** A technical buyer from Greencore or Samworth would see "blast chiller" in the project structure but read cold room behaviour in the spec. They are different machines. Blast chilling is rapid cooling of cooked product. Cold storage is holding product at temperature. The buyer would wonder if the team knows the difference.

3. **The laminator oven.** A converter running a Nordmeccanica ML2 would say "we do not have an oven on the laminator, it is solventless." If the simulator is meant to model a solvent-free machine, remove the oven signal. If it models a solvent-based machine, say so.

Everything else passes the sniff test. The signal ranges are right. The equipment vendors are right. The compliance references (BRC, Weights and Measures Act, HACCP) are right. The fault codes look real. The CIP profile is right. The thermal diffusion physics is right. The scenario frequencies and durations are realistic.

---

## Final Assessment

The PRD has moved from B+ to A-. The three High issues are all cross-reference inconsistencies introduced during the expansion from 3,900 to 5,112 lines. They are copy errors, not design errors. Fix the register address mismatch, the MQTT topic prefix, and the success criteria slave assignments. Resolve the Medium naming and parameter inconsistencies. The underlying engineering is sound. A production engineer would read this PRD and recognise their factory.
