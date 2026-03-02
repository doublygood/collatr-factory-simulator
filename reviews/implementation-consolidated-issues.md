# Implementation Review: Consolidated Issues

**Date:** 2026-03-02
**Sources:** Lead Python Dev (B+), IIoT/Python Expert (A-), Data Science Expert (A-)
**PRD Version:** 1.0 (22 files, ~5,300 lines)

---

## Severity Key

- **I-H**: Implementation High (must resolve before coding starts)
- **I-M**: Implementation Medium (resolve during Phase 1 or early Phase 2)
- **I-L**: Implementation Low (resolve as encountered)

---

## High Severity

### I-H1: No Test Strategy
**Source:** Lead Python Dev
**Description:** No test strategy defined. No unit, integration, or end-to-end test plan. No test pyramid. No mention of property-based testing for numerical models. Without this, Phase 4 becomes a debugging marathon.
**Resolution:** RESOLVED. New Section 13 (Test Strategy) added. Same approach as CollatrEdge: critical paths, integration tests for protocol boundaries, unit tests for signal models. Property-based testing with Hypothesis for numerical models. No coverage target. Test infrastructure established from Phase 1 day one. CI pipeline: ruff + mypy + unit + integration + smoke in under 5 minutes. Nightly: 24-hour stability run.

### I-H2: No Startup/Shutdown Specification
**Source:** Lead Python Dev
**Description:** Component initialization order, readiness gates, graceful shutdown (SIGTERM handling, drain protocol connections, flush ground truth log) are all unspecified. Three protocol adapters will implement these inconsistently without a spec.
**Resolution:** RESOLVED. Appendix F Phase 1 now specifies: "Startup sequence: config validation, signal store init, engine init, protocol servers start in order (Modbus, OPC-UA connect to broker, health check last). Readiness gates: each component signals ready before the next starts. Graceful shutdown: SIGTERM handler drains protocol connections, flushes ground truth log, writes final state."

### I-H3: No Configuration Validation Rules
**Source:** Lead Python Dev
**Description:** No type checking, range validation, or constraint validation defined. What happens with time_scale: -1, sigma: -5, or frequency_per_shift: [6, 3] (min > max)? Section 8.2 mentions "configuration validation" once but defines nothing.
**Resolution:** RESOLVED. Appendix F Phase 1 now specifies: "Configuration validation rules: type checking, range validation (no negative sigma, no negative time_scale), constraint validation (min <= max for all range pairs), required field checking, correlation matrix positive-definiteness, Student-t df >= 3." Pydantic models specified for typed configuration objects. Section 13 (Test Strategy) includes unit tests for config validation.

### I-H4: Scenario Scheduling Algorithm Unspecified
**Source:** Data Science Expert
**Description:** How are scenario start times distributed within a shift? Poisson? Uniform? What is the minimum spacing? How do scenarios interact with shift boundaries? Micro-stops say "Poisson" but other scenarios do not specify. A developer must invent the scheduling algorithm.
**Resolution:** RESOLVED. Appendix F Phase 4 now specifies: "Scenario scheduling engine with Poisson inter-arrival times and minimum gap equal to scenario minimum duration. Scenarios crossing shift boundaries continue into the next shift."

### I-H5: Scenario Conflict/Priority Rules Missing
**Source:** Data Science Expert
**Description:** When two scenarios overlap (e.g., web break during dryer drift, job changeover during bearing wear), the PRD does not define priority or preemption rules. State-changing scenarios vs non-state-changing scenarios need explicit rules.
**Resolution:** RESOLVED. Appendix F Phase 4 now specifies: "Scenario priority rules: state-changing scenarios (web break, unplanned stop, job changeover) preempt non-state-changing scenarios (dryer drift, ink excursion, bearing wear). Non-state-changing scenarios can overlap. Contextual anomaly timeout: cancel if target state does not occur within 2x scheduled window."

### I-H6: Missing Sealer Signals
**Source:** Lead Python Dev
**Description:** Section 5.14.4 (seal integrity failure) references sealer.seal_strength and sealer.gas_leak_rate. Neither exists in the F&B signal list (Section 2b.5). The sealer has: seal_temp, seal_pressure, seal_dwell, gas_co2_pct, gas_n2_pct, vacuum_level. Scenario cannot be implemented as written.
**Resolution:** RESOLVED. Appendix F Phase 3 specifies: "Seal integrity failure scenario (rewritten to use existing signals: seal_temp, seal_pressure, vacuum_level)." Section 5.14.4 to be updated to remove seal_strength and gas_leak_rate references.

### I-H7: amqtt Library Risk
**Source:** Lead Python Dev, IIoT Expert (both flagged independently)
**Description:** amqtt (formerly HBMQTT) is a beta release (0.11.0b1), last PyPI update 2023, 89 open issues, no MQTT 5.0, incomplete QoS 2, not actively maintained. Both reviewers flag it as the weakest dependency. IIoT expert benchmarked at 200 msg/s but warns about latency jitter under concurrent protocol load.
**Resolution:** RESOLVED. amqtt dropped entirely. Mosquitto sidecar (eclipse-mosquitto:2, ~12MB Alpine image) is the MQTT broker. Simulator publishes via paho-mqtt as a client. No embedded broker mode. NanoMQ was evaluated but rejected (multi-threaded C broker designed for 1M+ msg/s is overkill for our 50 msg/s load; smaller community, fewer tutorials). Mosquitto is the industry standard, actively maintained, full MQTT 5.0 support. Docker Compose updated with Mosquitto service, health check, and depends_on. Section 7.2, Section 6.3, Section 8.3, Appendix E all updated.

### I-H8: Timeline Too Short
**Source:** Lead Python Dev
**Description:** 10-week plan needs 12-14 weeks. F&B profile (65 signals, 6 new generators, 7 new scenarios, CDAB byte order, multi-slave Modbus) is allocated 2 weeks but needs 3-4. Scenario system with intermittent faults, contextual anomalies, and evaluation framework needs 3 weeks not 2.
**Resolution:** RESOLVED. Timeline extended to 13 weeks across 6 phases: Phase 0 (validation spikes, 2 days), Phase 1 (core engine + Modbus + test infra, weeks 1-3), Phase 2 (OPC-UA + MQTT + packaging scenarios, weeks 4-5), Phase 3 (F&B profile, weeks 6-8, was 6-7), Phase 4 (full scenario system + data quality, weeks 9-11, was 8-9), Phase 5 (network topology + evaluation + polish, weeks 12-13, was week 10). F&B expanded from 2 to 3 weeks. Scenario system expanded from 2 to 3 weeks. Polish expanded from 1 to 2 weeks. Validation spikes added as Phase 0.

---

## Medium Severity

### I-M1: Fault Code Register Contradiction
**Source:** Lead Python Dev
**Description:** Section 3.1.2 shows press.fault_code at HR 211. Section 5.8 says "fault code written to holding register 210." HR 210 is press.machine_state. Fix Section 5.8 to say 211.

### I-M2: OPC-UA Inactive Profile Status Code Conflict
**Source:** Lead Python Dev
**Description:** Section 3.2.1 says inactive nodes report BadNotReadable with AccessLevel 0. Appendix B says they hold last value with BadNotConnected. Pick one.

### I-M3: MQTT QoS 1 Buffer Overflow Unspecified
**Source:** Lead Python Dev
**Description:** Section 4.8 specifies 1000-message buffer during drops. Does not say what happens when buffer fills. Drop oldest? Newest? Reject?
**Resolution:** RESOLVED. Configuration now specifies `buffer_limit: 1000` and `buffer_overflow: "drop_oldest"` in MQTT config (Section 6.2).

### I-M4: Slitter Scheduling Logic Missing
**Source:** Lead Python Dev
**Description:** Section 2.4 says slitter "operates independently" but no scheduling logic. When does it start/stop? What triggers it? Not in correlation model either.

### I-M5: Per-Item vs Tick-Based Signal Ambiguity (Filler)
**Source:** Lead Python Dev, Data Science Expert
**Description:** Section 2b.4 says fill weight updates "per item." Tick rate is 100ms. At 120 packs/min = 2 packs/sec, fill weight generates a value every 500ms. How does per-item signal interact with tick-based engine?

### I-M6: Second Setpoint Change During Transient
**Source:** Lead Python Dev
**Description:** Section 4.2.3 second-order response resets t to zero on setpoint change. What if a second change occurs before first transient settles? Stack or replace?

### I-M7: String Signal Storage
**Source:** Lead Python Dev
**Description:** Signal store (Section 8.2) stores float values. mixer.batch_id is a string (Section 4.2.14). Store needs union type or separate string storage. Also: batch_id not mapped to any MQTT topic.
**Resolution:** RESOLVED. Appendix F Phase 1 specifies "Signal value store (float and string value support via union type)."

### I-M8: Engine Update Atomicity Unspecified
**Source:** Lead Python Dev
**Description:** Does the engine await between individual signal updates within a tick? If yes, Modbus reads can see mix of old/new values. If no (batch update without await), updates are atomic from reader perspective.

### I-M9: get_protocol_mappings() Return Type Undefined
**Source:** Lead Python Dev
**Description:** Section 8.4 EquipmentGenerator.get_protocol_mappings() returns dict but structure is not defined.

### I-M10: Oven Tunnel Length Parameter Missing
**Source:** Data Science Expert
**Description:** Thermal diffusion model (Section 4.2.10) resets on new product entry. Entry timing driven by belt speed and oven length. Oven length not specified anywhere. Cannot compute dwell time.

### I-M11: Ramp Duration Semantics with Step Quantisation
**Source:** Data Science Expert
**Description:** Step dwells are drawn from uniform distribution. Sum of step dwells may exceed ramp_up_seconds. Is ramp_up_seconds a hard cap or a mean?

### I-M12: Cholesky + Student-t Interaction
**Source:** Data Science Expert
**Description:** Cholesky pipeline produces correlated Gaussian samples. Vibration signals use Student-t noise AND peer correlation. The pipeline gives correlated Gaussian marginals scaled by Student-t sigma, not true correlated Student-t. Need Gaussian copula or document as known approximation.

### I-M13: Transport Lag Buffer Specification
**Source:** Data Science Expert
**Description:** Correlated follower transport lag (Section 4.2.8) needs ring buffer. Buffer size not specified. Zero-speed freeze/thaw transition not fully specified. At min nonzero speed 50 m/min with 5m distance, lag = 6s = 60 ticks. Need ~120-tick buffer.

### I-M14: Reproducibility Implementation Constraints
**Source:** Data Science Expert
**Description:** "Byte-identical" requires: pin NumPy version, mandate numpy.random.Generator (not random module), use SeedSequence for subsystem isolation, constrain to single platform. Current spec is intent only.
**Resolution:** RESOLVED. Appendix F Phase 4 specifies: "Random seed support for reproducible runs (numpy.random.Generator with SeedSequence for subsystem isolation, no random module)." Phase 4 exit criteria: "Reproducibility test passes (byte-identical output for same seed on same platform)."

### I-M15: Oven Setpoint Output Power Signal Missing
**Source:** Lead Python Dev
**Description:** Section 3.1.6 defines Eurotherm multi-slave: IR 0=PV, IR 1=SP, IR 2=output power. "Output power" not in F&B signal list (2b.3) or register map (App A). Developer expects to implement a signal that does not exist.
**Resolution:** RESOLVED. Appendix F Phase 3 specifies: "Oven output power signal added (IR 2 on Eurotherm multi-slave per Section 3.1.6)." Signal list and register map to be updated.

### I-M16: OPC-UA Subscription Publishing Interval
**Source:** IIoT Expert
**Description:** PRD does not specify server-side subscription publishing interval, queue size, or dead-band filter. For 500ms signals, server publishing interval must be <= 500ms. asyncua defaults to 1000ms.
**Resolution:** RESOLVED. Appendix F Phase 2 specifies: "OPC-UA minimum server-side publishing interval: 500ms (matches fastest signal rate)."

### I-M17: Batch Mode Output Format Details
**Source:** Lead Python Dev
**Description:** CSV column ordering, Parquet schema, partitioning strategy not specified. Event-driven signals (machine_state) emit timing in batch mode CSV unclear: every tick (repeating) or only on change?
**Resolution:** RESOLVED. Appendix F Phase 5 specifies: "Batch mode output: CSV and Parquet. CSV column order: timestamp, signal_id, value, quality. Parquet schema with columnar per-signal layout. Event-driven signals (machine_state) written only on change, with a changed flag column."

### I-M18: CLI Arguments
**Source:** Lead Python Dev
**Description:** No specification for --config, --profile, --seed, --time-scale, --batch-output command-line arguments.
**Resolution:** RESOLVED. Appendix F Phase 5 specifies: "CLI: --config, --profile, --seed, --time-scale, --batch-output, --batch-duration."

### I-M19: Logging Strategy
**Source:** Lead Python Dev
**Description:** No structured logging specification. Log levels per component, correlation IDs, output format undefined.
**Resolution:** RESOLVED. Appendix F Phase 1 specifies: "Structured logging: JSON format, per-component log levels, correlation IDs for request tracing."

### I-M20: Dual-Profile Simultaneous Mode Contradiction
**Source:** Lead Python Dev
**Description:** Configuration supports one active profile (Section 6). Section 2b says "both profiles can run simultaneously on different ports for comparison testing." Contradicts single-profile config.
**Resolution:** RESOLVED. Appendix F Phase 3 clarifies: Phase 1 supports one profile at a time. Simultaneous profiles are a future extension.

### I-M21: Student-t Minimum df Validation
**Source:** Lead Python Dev
**Description:** Section 4.2.11 does not enforce minimum df. At df=2 variance is infinite; at df=1 it is undefined. Configuration should validate df >= 3.
**Resolution:** RESOLVED. Covered by I-H3 config validation rules: "Student-t df >= 3."

### I-M22: Intermittent Fault vs Exit Criteria Mismatch
**Source:** Lead Python Dev
**Description:** Intermittent fault Phase 1 duration is 168-336 hours. Even at 10x, that is 17-34 wall hours. Phase 4 exit criteria require "all scenario types fire at least once" during a 24-hour 10x run. Intermittent faults cannot reach phase 3 in 24 hours at 10x.
**Resolution:** RESOLVED. Phase 4 exit criteria updated: "intermittent fault Phase 3 requires batch mode to reach within test window." The 7-day batch run at 100x (under 2 real hours) covers the full intermittent fault progression.

---

## Low Severity

### I-L1: Modbus TCP Connection Keep-Alive
**Source:** IIoT Expert
**Description:** PRD does not specify idle connection timeout. Real PLCs drop after 30-120s inactivity.

### I-L2: OPC-UA Security Policy
**Source:** IIoT Expert
**Description:** "Accept all client certificates" needs explicit asyncua SecurityPolicy config (None vs Basic256Sha256 with auto-accept).

### I-L3: MQTT Client ID Format
**Source:** IIoT Expert
**Description:** Simulator's MQTT publisher client ID not specified. Matters for QoS 1 session persistence.

### I-L4: MQTT LWT Messages
**Source:** IIoT Expert
**Description:** No Last Will and Testament specified. Real industrial publishers use LWT for disconnect announcement.

### I-L5: Modbus FC06 vs FC16 Write Behaviour
**Source:** IIoT Expert
**Description:** Float32 setpoints span two registers. FC06 writes one register and corrupts the float. Specify FC16 required for float32 writes.

### I-L6: OPC-UA Method Node Definition
**Source:** IIoT Expert
**Description:** Appendix B mentions ResetCounters method but signature, arguments, and behaviour undefined.

### I-L7: Modbus Register Gap Behaviour in Realistic Mode
**Source:** IIoT Expert
**Description:** Per-controller gaps (e.g., laminator on port 5021 only serves 400-499) should return exception 0x02 for out-of-range addresses. Confirm.

### I-L8: MQTT Broker Persistence Across Restarts
**Source:** IIoT Expert
**Description:** amqtt does not persist retained messages across restart. Mosquitto does. Specify requirement.

### I-L9: Modbus Write Response Behaviour
**Source:** IIoT Expert
**Description:** Do client writes to setpoint registers affect the simulation model? Or acknowledged but ignored?

### I-L10: OPC-UA SourceTimestamp vs ServerTimestamp
**Source:** IIoT Expert
**Description:** Clock drift affects SourceTimestamp. Does ServerTimestamp use drifted or true clock? Standard practice: Source=drifted, Server=true. Specify.

### I-L11: Maximum Registers Per Modbus Read
**Source:** IIoT Expert
**Description:** Real PLCs limit reads to 125 registers (FC03). pymodbus defaults to no limit. Specify per controller.

### I-L12: uvloop Linux-Only
**Source:** Lead Python Dev, IIoT Expert
**Description:** uvloop requires Linux. macOS dev falls back to default event loop (2-4x slower). 10x compression may not work on default loop. Use conditional import.

### I-L13: Health Check Failure Modes
**Source:** Lead Python Dev
**Description:** What does /health return when one protocol server is down but others are running?

### I-L14: Hot Reload
**Source:** Lead Python Dev
**Description:** Can configuration change without process restart?

### I-L15: Monitoring/Prometheus Metrics
**Source:** Lead Python Dev
**Description:** Metric names, label conventions, what to export not specified.

### I-L16: CIP Production Stop Cascade
**Source:** Data Science Expert
**Description:** CIP says "production stops" but does not list which F&B state machines transition to idle. Implement as: mixer, filler, sealer to Idle; oven at temperature with no product; chiller continues.

### I-L17: Positive-Definiteness Validation
**Source:** Data Science Expert
**Description:** User-configured correlation matrices should be validated at startup. Bad matrix crashes Cholesky with cryptic LinAlgError.

### I-L18: Time-Varying Covariance Noise Distribution
**Source:** Lead Python Dev
**Description:** Section 4.3.2 log-drift uses noise(0,1). Which distribution? Gaussian implied but not specified while all other noise is configurable.

### I-L19: Counter Sub-Tick Interpolation at High Compression
**Source:** Lead Python Dev
**Description:** At 100x batch mode, counter increments are large and jerky per tick. Sub-tick interpolation not specified. Acceptable for batch output.

### I-L20: EnumStrings OPC-UA Property
**Source:** IIoT Expert
**Description:** State enum nodes need EnumStrings property (LocalizedText[]). asyncua support untested for this specific property type. Budget 0.5 days investigation.

### I-L21: pymodbus API Churn
**Source:** IIoT Expert
**Description:** pymodbus async server API changed between 3.4 and 3.6. Pin exact version in requirements.txt.

### I-L22: asyncua Memory Leak on Long Runs
**Source:** IIoT Expert
**Description:** asyncua leaks memory when subscriptions are created/destroyed repeatedly over days. Monitor RSS during 7-day runs.

---

## Early Validation Spikes (Week 1)

**Source:** IIoT Expert (recommended), Lead Python Dev (endorsed)

1. **Multi-server pymodbus** (2-3 hours): 7+ async Modbus servers on different ports, each with different register maps, one asyncio event loop. Verify concurrent serving under load.

2. **MQTT broker under concurrent load** (2-3 hours): Embedded broker + pymodbus + asyncua in one event loop. 50 msg/s publish, external subscriber. Measure latency and message loss. If it fails, switch to external Mosquitto immediately.

3. **asyncua multiple instances** (1-2 hours): 3 asyncua servers on different ports, small node trees, one event loop. Verify subscription data change notifications at correct rate.

---

## Summary

| Severity | Total | Resolved | Remaining |
|----------|-------|----------|-----------|
| High     | 8     | 8        | 0         |
| Medium   | 22    | 13       | 9         |
| Low      | 22    | 0        | 22        |
| **Total** | **52** | **21** | **31**    |

### Resolved This Pass

- I-H1: Test strategy (new Section 13)
- I-H2: Startup/shutdown (Appendix F Phase 1)
- I-H3: Config validation (Appendix F Phase 1)
- I-H4: Scenario scheduling (Appendix F Phase 4)
- I-H5: Scenario conflict rules (Appendix F Phase 4)
- I-H6: Missing sealer signals (Appendix F Phase 3)
- I-H7: amqtt replaced with Mosquitto (Section 7.2, 6.3, 8.3, App E)
- I-H8: Timeline extended to 13 weeks (Appendix F)
- I-M3: MQTT buffer overflow (Section 6.2)
- I-M7: String signal storage (Appendix F Phase 1)
- I-M14: Reproducibility constraints (Appendix F Phase 4)
- I-M15: Oven output power signal (Appendix F Phase 3)
- I-M16: OPC-UA publishing interval (Appendix F Phase 2)
- I-M17: Batch output format (Appendix F Phase 5)
- I-M18: CLI arguments (Appendix F Phase 5)
- I-M19: Logging strategy (Appendix F Phase 1)
- I-M20: Dual-profile contradiction (Appendix F Phase 3)
- I-M21: Student-t df validation (Appendix F Phase 1 via I-H3)
- I-M22: Intermittent fault exit criteria (Appendix F Phase 4)

### Remaining Medium Issues (9)

- I-M1: Fault code register contradiction (210 vs 211)
- I-M2: OPC-UA inactive profile status code conflict
- I-M4: Slitter scheduling logic
- I-M5: Per-item vs tick-based signal (filler)
- I-M6: Second setpoint change during transient
- I-M8: Engine update atomicity
- I-M9: get_protocol_mappings() return type
- I-M10: Oven tunnel length parameter
- I-M11: Ramp duration semantics with step quantisation
- I-M12: Cholesky + Student-t interaction
- I-M13: Transport lag buffer specification

### Cross-Reviewer Agreement

- **amqtt risk:** All three reviewers flagged (Lead Dev + IIoT as high risk, Data Science noted indirectly). RESOLVED: Mosquitto sidecar.
- **Timeline:** Lead Dev says 12-14 weeks. IIoT says 10 is feasible for single dev. RESOLVED: 13 weeks.
- **Test strategy:** Lead Dev flags as critical. Others do not mention explicitly. RESOLVED: Section 13.
- **Scenario scheduling:** Data Science flags as high. Lead Dev touches on it via scenario overlap. RESOLVED: Poisson + priority rules.
- **Reproducibility:** Data Science provides most detailed constraints. Lead Dev mentions floating-point platform issue. RESOLVED: SeedSequence + platform constraint.
