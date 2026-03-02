# Implementation Readiness Review: Collatr Factory Simulator PRD

**Reviewer:** Lead Python Developer (12+ years production Python)
**Date:** 2026-03-02
**PRD Version:** 1.0 (22 files, ~5,300 lines)
**Scope:** Full implementation readiness assessment

---

## Overall Implementation Readiness Grade: B+

## Summary

This is an exceptionally thorough PRD. The signal models are mathematically precise. The protocol specifications leave little room for interpretation. The correlation model and scenario system are well-defined. The two factory profiles share infrastructure cleanly. The main weaknesses are: no test strategy, underspecified error handling and startup/shutdown sequences, optimistic timeline for the F&B profile, and several library-level risks with amqtt and asyncua under the required concurrent load. A team of 2-3 developers can build this, but the 10-week timeline assumes no significant library debugging. Budget 12-14 weeks.

---

## Section-by-Section Implementation Concerns

### Section 1: Overview and Goals

No implementation issues. The reference data constraint is clear. The four use cases are well-scoped. The LLM agent demo use case adds implicit requirements (the data must look convincing in a chat context) that are not formalized anywhere. This is a product concern, not an engineering one.

### Section 2/2b: Factory Layouts

The signal tables are precise. Every signal has an ID, range, unit, rate, and protocol. This is good.

The packaging profile specifies 47 signals across 7 equipment groups. The F&B profile specifies 65 signals across 9 equipment groups. The F&B profile is 40% larger than packaging. Appendix F allocates 2 weeks for F&B (Phase 3). The packaging profile gets 5 weeks (Phases 1-2). This ratio does not match the complexity ratio.

Section 2.4 says the slitter "operates independently from the press" and "processes rolls that the press produced earlier." The scheduling logic for the slitter is not specified anywhere. When does it start? When does it stop? What triggers it? The correlation model in Section 4.3 does not mention the slitter. A developer must invent this behaviour.

Section 2b.4 says fill weight updates "per item." The tick rate is 100ms (Section 4.1). At 120 packs per minute, that is 2 packs per second. The fill weight signal must generate a new value every 500ms. The PRD does not specify how a per-item signal interacts with the tick-based engine. Is it event-driven or sampled?

Section 2b.9 says the F&B coder is "identical" to packaging but "coupled to the filling/sealing line rather than the flexo press." The coder generator in Appendix E is a single file (coder.py). The coupling target must be configurable. This is mentioned but not specified in the configuration reference.

### Section 3: Protocol Endpoints

The Modbus register map is complete. Address ranges are partitioned cleanly. No address collisions.

Section 3.1.2 shows `press.fault_code` at holding register 211. Section 5.8 says "a fault code is written to holding register 210 as a secondary uint16 value." Register 210 is `press.machine_state`. This is a contradiction. The fault code lives at 211 per the register map but the scenario description says 210.

Section 3.1.6 defines multi-slave simulation for oven zones. The Eurotherm slaves serve IR 0 = PV, IR 1 = SP, IR 2 = output power. The "output power" signal does not exist in the F&B signal list (Section 2b.3). It is not in the register map (Appendix A). A developer reading the multi-slave section will expect to implement an output power signal that is not defined.

The OPC-UA namespace puts both profiles under Objects simultaneously. Section 3.2.1 says inactive nodes report `BadNotReadable` with `AccessLevel` 0. Appendix B says they hold last value with `BadNotConnected`. These are different OPC-UA status codes. Pick one.

MQTT QoS 1 message buffering during drops (Section 4.8) specifies a 1000-message buffer limit. What happens when the buffer fills? Drop oldest? Drop newest? Reject new messages? Not specified.

### Section 3a: Network Topology

This section is impressive. The multi-controller topology with per-device quirks (byte order, connection limits, scan cycles, clock drift) is the kind of specification that prevents months of "it works in dev but not in production" issues.

The "realistic" mode requires 15 TCP server instances (Section 3a.4). The asyncio event loop must handle all of them concurrently. Each Modbus server is a pymodbus async server. Running 8+ pymodbus servers in the same event loop has not been tested at scale in the pymodbus documentation. This is a risk.

Section 3a.5 defines connection MTBF per controller type. The mechanism for dropping and reconnecting is not specified. Does the simulator close the TCP socket? Does it stop responding? Does it send a RST? The client behaviour (CollatrEdge) depends on which failure mode the simulator presents.

### Section 4: Data Generation Engine

The 12 signal models are mathematically precise. The formulas are implementable. The parameter tables are complete.

Section 4.1 principle 5 says "all signal models use simulated time." The implementation must enforce this invariant. The random walk model (Section 4.2.5) uses `dt` in the update formula. If a developer accidentally uses wall-clock `dt` at 100x compression, drift rates inflate by sqrt(100) = 10x. The PRD flags this. Good. But there is no architectural mechanism to prevent it. A code review or test must catch it.

Section 4.2.3 defines the second-order response for first-order lag. The formula resets `t` to zero on each setpoint change. What happens if a second setpoint change occurs before the first transient settles? The current transient must be interrupted and a new one started from the current value. The PRD does not state this explicitly. A developer might stack transients (additive oscillation) or might replace them. Both interpretations produce different output.

Section 4.2.6 defines counter increment as `value = value + rate * line_speed * dt`. The `dt` here is in seconds (simulated). At 100x compression with a 100ms tick, `dt` = 10 seconds per tick. The counter increments by `rate * speed * 10` per tick. For impression count at 200 m/min with rate 1.0, that is 2000 per tick. This is correct but produces jerky counter values at high compression. The PRD does not specify whether sub-tick interpolation should smooth counters. For protocol serving this does not matter (10x max). For batch file output it might.

Section 4.2.10 (thermal diffusion) says "sum terms until T(0) falls within 1C of T_initial." This convergence check runs at every tick, not just at initialization. The truncated series is recomputed each tick with the current elapsed time. The convergence is only relevant at t=0. After a few seconds, only the n=0 term matters. The implementation should compute the required number of terms once at product entry time and reuse it. This optimization is not specified but is obvious to a competent developer.

Section 4.2.11 says Student-t variance is `sigma^2 * df / (df - 2)`. At df=3, variance is `3 * sigma^2`. At df=2, variance is infinite. The PRD does not enforce a minimum df. A user could set df=2 or df=1 and get infinite variance or undefined variance. The configuration should validate df >= 3.

Section 4.2.14 defines the string generator for `mixer.batch_id`. The format template uses Python's `str.format()` syntax. The `date` variable is a datetime object. The `seq` variable is an integer. This is clear. What is not clear: is `batch_id` published on OPC-UA as a String node? Section 3.2.1 says yes. Is it published on MQTT? No MQTT topic is defined for `batch_id`. Is it stored in the signal store as a string? The signal store (Section 8.2) stores `float` values. A string does not fit. The store needs a union type or separate string storage.

Section 4.3.1 specifies the signal generation pipeline order: generate independent noise, apply Cholesky, then scale by sigma. This is correct. But the peer correlation groups are defined per profile. The packaging profile has vibration axes and dryer zones. The F&B profile has oven zones. What about the F&B dryer zones? The F&B profile has no dryer. What about mixer signals? No peer correlation is defined for F&B-specific equipment. Is this intentional? Probably. But a developer might wonder.

Section 4.3.2 defines time-varying covariance with a multiplicative random walk on the gain parameter. The log-drift uses `noise(0, 1)`. Which noise distribution? The section does not specify. Gaussian is implied. But all other noise in the PRD is configurable. This one is hardcoded Gaussian by implication.

Section 4.7 (ground truth log) defines the JSONL format. The header record includes per-signal noise parameters. For 65 signals with multiple parameters each, this header line could be 10+ KB. JSONL readers that parse line-by-line will handle it. Streaming JSON parsers that expect small objects may not. Not a problem for Python. Could be a problem for downstream consumers.

Section 4.8 says the data engine continues generating during connection drops but protocol adapters stop serving. The engine and adapters share the signal store. The engine writes. The adapters read. During a drop, the adapter stops reading. When it resumes, it reads the current value. The gap is in the adapter, not the engine. This is clean. But the MQTT QoS 1 buffering adds complexity. The adapter must buffer messages during the drop and flush them on recovery. This buffer needs memory limits, ordering guarantees, and overflow handling. None of these are specified.

### Section 5: Scenario System

17 scenarios for packaging, 7 for F&B. Each scenario is a state machine with a defined sequence. The sequences are clear.

Section 5.2 (job changeover) says counters "may reset to 0 (new job) or continue (same batch)." The probability is 0.7 (Appendix D). When counters do not reset, what happens to good_count and waste_count relative to impression_count? The invariant `impression_count = good_count + waste_count` must hold. If impression_count resets but good_count and waste_count do not, the invariant breaks. The PRD does not specify whether all three reset together or independently.

Section 5.4 (dryer drift) says "temperature returns to setpoint (simulates operator correction or auto-correction)." How fast? Instantly? Via first-order lag? Via a new ramp? The recovery dynamics are not specified. A slow recovery produces a different signal shape than an instant snap-back.

Section 5.13a (material splice) is well-specified. The trigger condition (`unwind_diameter < 150mm`) integrates with the depletion model. Good.

Section 5.14.4 (seal integrity failure) references `sealer.seal_strength` and `sealer.gas_leak_rate`. Neither signal exists in the F&B signal list (Section 2b.5). The sealer has 6 signals: seal_temp, seal_pressure, seal_dwell, gas_co2_pct, gas_n2_pct, vacuum_level. The scenario references signals that are not defined. A developer cannot implement this scenario as written.

Section 5.14.8 (allergen changeover) is well-specified. The transition detection logic table is clear. The mandatory CIP tie-in is clean. This is good PRD writing.

Section 5.16 (contextual anomalies) says the engine "waits for the required machine state, then injects." What if the machine state never enters the required state during the scheduled window? Does the event get deferred? Cancelled? The timeout behaviour is not specified.

Section 5.17 (intermittent faults) defines a three-phase progression. The phase durations are specified in Appendix D (e.g., phase1_duration_hours: [168, 336]). At 1x speed, phase 1 takes 1-2 weeks of wall-clock time. Even at 10x, it takes 17-34 hours. This scenario is only testable in batch mode (100x+). The PRD acknowledges this ("at 1x speed, the full progression takes weeks"). But the Phase 4 exit criteria say "all scenario types fire at least once" during a 24-hour 10x run. Intermittent faults cannot reach phase 3 in 24 hours at 10x. The exit criteria are inconsistent with the scenario timescale.

### Section 6: Configuration

The YAML structure is clear. Environment variable overrides are documented. The Docker Compose file is minimal but functional.

The configuration does not specify validation rules. What happens when a developer sets `time_scale: -1`? Or `sigma: -5`? Or `frequency_per_shift: [6, 3]` (min > max)? The PRD mentions "configuration validation" once (Section 8.2, step 1) but does not define what gets validated. A developer must invent all validation rules.

The configuration supports only one active profile. Section 9.3 says the F&B profile was promoted to Phase 1. The implementation note in Section 2b says "both profiles can run simultaneously on different ports for comparison testing." This contradicts the single-profile design. Which is it?

### Section 7: Technology Stack

The Python choice is well-reasoned. The dependency list is appropriate.

The `amqtt` library (formerly HBMQTT) is the embedded MQTT broker. This library has known issues. The last PyPI release (0.11.0b1) is a beta. The GitHub repository has 89 open issues. MQTT 5.0 support is incomplete. QoS 2 is unreliable. The library is not actively maintained. For a production tool, this is a risk.

Alternative: `gmqtt` for the client side plus Mosquitto in a sidecar container for the broker. This trades "embedded broker" for "reliable broker." The PRD already supports external broker mode. The embedded mode could be deferred.

`asyncua` is more mature. It has 400+ stars, active maintenance, and handles server mode well for small-to-medium node counts. With 65 nodes (F&B profile), performance should be fine. With subscriptions from multiple clients, it may struggle. The PRD does not specify expected client count. If only CollatrEdge connects (one client), asyncua is fine.

`uvloop` is listed as a dependency. It requires Linux. The Dockerfile uses `python:3.12-slim` (Debian-based). This is fine for Docker. But developers on macOS will use the default asyncio loop. The performance difference is 2-4x. Signal generation at 10x may work on uvloop but fail on the default loop. The PRD should note this.

### Section 8: Architecture

The component diagram is clear. The data flow is well-defined. The concurrency model is appropriate.

The signal store "uses no locks" because "the engine is the sole writer." This is true in asyncio's cooperative multitasking. But the Modbus server responds to external client requests. Each client read triggers a coroutine that reads the store. If the engine is mid-write when a Modbus read fires, does the reader see a partial update? In asyncio, a coroutine runs until it hits an `await`. If the engine updates 47 signals in a loop without awaiting between them, the update is atomic from the reader's perspective. If it awaits between signal updates, a reader could see a mix of old and new values. The PRD does not specify whether the engine updates are batched (atomic) or interleaved.

Section 8.4 defines the plugin architecture with `EquipmentGenerator`. The interface is clean. But the `generate()` method returns `list[SignalValue]` where `SignalValue.value` is `float`. The string generator (Section 4.2.14) produces strings. The store needs to handle both. The interface needs a union type or a separate method for string signals.

The `get_protocol_mappings()` method returns a dict. The structure of this dict is not defined. Is it `{signal_id: {modbus: {...}, opcua: {...}, mqtt: {...}}}`? Or `{modbus: [{signal_id: ..., address: ...}]}`? A developer must guess.

### Section 10: Data Quality Realism

This section is excellent. The imperfections are catalogued with causes, frequencies, and implementation notes. Sensor disconnect sentinels, stuck sensors, partial Modbus responses, and duplicate timestamps are all well-specified.

Section 10.11 (partial Modbus responses) says the simulator returns fewer registers than requested. The pymodbus server API may not support this easily. The standard server handler builds a complete response. Injecting a truncated response requires hooking into the response serialization. This is doable but requires understanding pymodbus internals.

### Section 11: Success Criteria

The criteria are measurable. "RSS stays within 2x of initial" is testable. "No NaN, no infinity" is testable. "Byte-identical signal sequences" is testable.

Section 11.6 (reproducibility) says "byte-identical signal sequences for the first 1 million data points." This requires deterministic floating-point arithmetic. NumPy operations are deterministic on the same platform with the same random seed. But float64 arithmetic is not bitwise identical across x86 and ARM (different FMA behaviour). The Docker image pins the platform. This should work. But the criterion should say "on the same platform."

### Section 12: Evaluation Protocol

This section goes beyond what a simulator PRD normally contains. The evaluation framework (clean/impaired pairing, event-level matching, tolerance windows, severity weights, statistical significance) is a research contribution. It is well-specified and implementable.

The random baseline definition is good. Reporting it alongside detector results prevents false confidence.

---

## Ambiguity List

| Location | Ambiguity | Suggested Resolution |
|----------|-----------|---------------------|
| Section 2.4 (Slitter) | Slitter "operates independently" but scheduling logic is not specified. When does it start/stop? | Define a slitter state machine with triggers based on accumulated press output or time-of-day schedule. |
| Section 2b.4 (Filler) | "Per item" fill weight update rate vs tick-based engine. How do per-item events map to 100ms ticks? | Specify that per-item signals generate one value per simulated item arrival, gated by filler.line_speed. |
| Section 3.1.2 / 5.8 | Fault code register is 211 in the map but scenario says "register 210." | Fix Section 5.8 to say register 211. |
| Section 3.2.1 / Appendix B | Inactive profile OPC-UA status: `BadNotReadable` (Section 3.2.1) vs `BadNotConnected` (Appendix B). | Pick `BadNotReadable` (it matches the AccessLevel=0 semantics). Update Appendix B. |
| Section 4.2.3 | Second setpoint change before first transient settles. Stack or replace? | Specify: replace. New transient starts from current value. Old transient is abandoned. |
| Section 4.2.14 / 8.4 | Signal store holds `float` values. `mixer.batch_id` is a string. Where does it go? | Add a `value: float | str` union to `SignalValue`. Or add a separate `string_values` dict to the store. |
| Section 4.8 | MQTT QoS 1 buffer overflow behaviour when 1000-message limit is reached. | Specify: drop oldest messages. Log a warning. |
| Section 5.2 | Counter reset: do impression_count, good_count, and waste_count reset together or independently? | Specify: all three reset together on job changeover. The invariant `impression = good + waste` must hold. |
| Section 5.4 | Dryer drift recovery dynamics. How fast does temperature return to setpoint? | Specify: recovery uses the same first-order lag model (Section 4.2.3) with the signal's configured tau. |
| Section 5.14.4 | `sealer.seal_strength` and `sealer.gas_leak_rate` referenced but not in signal list. | Remove these references. Rewrite the scenario to use existing signals (seal_temp, seal_pressure, vacuum_level). |
| Section 5.16 | Contextual anomaly timeout: what if the required machine state never occurs? | Specify: if the target state does not occur within 2x the scheduled window, cancel the event. Log it as skipped. |
| Section 6 / 9.3 | Single-profile config vs "both profiles can run simultaneously." | Clarify: Phase 1 supports one profile at a time. Simultaneous profiles are a future extension. Remove the contradicting note. |
| Section 8.4 | `get_protocol_mappings()` return type is `dict` with unspecified structure. | Define a `ProtocolMapping` dataclass with modbus, opcua, and mqtt fields. |
| Section 8.3 | Engine update atomicity: does the engine await between individual signal updates? | Specify: the engine updates all signals for one tick before yielding. No await between signals within a tick. |

---

## Missing Specifications List

| What is Missing | Where It Should Go | Severity |
|----------------|-------------------|----------|
| Test strategy (unit, integration, end-to-end, test pyramid) | New section or Appendix | High |
| Error handling: what happens when pymodbus fails to bind port 502? | Section 8 (Architecture) | High |
| Startup sequence: order of component initialization, readiness gates | Section 8 (Architecture) | High |
| Graceful shutdown: signal handling, drain protocol connections, flush ground truth log | Section 8 (Architecture) | High |
| Configuration validation rules (types, ranges, constraints) | Section 6 or Appendix D | High |
| Logging strategy: structured logging, log levels per component, correlation IDs | Section 8 or new section | Medium |
| Health check failure modes: what does /health return when one protocol server is down? | Section 8.5 | Medium |
| Hot reload: can configuration change without restart? | Section 6 | Low |
| Signal store memory management for long runs (7+ days at 1x) | Section 8 | Medium |
| Batch mode output format: CSV column ordering, Parquet schema, partitioning | Section 4.4 | Medium |
| CLI arguments: --config, --profile, --seed, --time-scale, --batch-output | Section 6 or new section | Medium |
| Monitoring: what Prometheus metrics to export, metric names, label conventions | Section 8.5 | Low |
| Type hints for configuration objects (Pydantic models or dataclasses) | Section 8 | Medium |
| Minimum df validation for Student-t distribution | Section 4.2.11 or Appendix D | Medium |
| Slitter scheduling logic | Section 2.4 or Section 5 | Medium |
| F&B scenario timing vs Phase 4 exit criteria mismatch | Appendix F | Medium |

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `amqtt` instability under QoS 1 load with retained messages | High | High | Use Mosquitto sidecar. Keep external broker mode as primary. Defer embedded broker. |
| Multiple pymodbus servers in one asyncio loop cause resource contention | Medium | High | Prototype early in Phase 1. Test 8 concurrent servers. Fall back to collapsed mode if needed. |
| asyncua server performance with 65 nodes and multiple subscriptions | Low | Medium | One CollatrEdge client is the expected load. Test with 3 concurrent clients. |
| 10-week timeline is 2-3 weeks short for F&B profile + full scenario system | High | Medium | Add 2-week buffer. Alternatively, defer intermittent faults and contextual anomalies to Phase 5. |
| Floating-point reproducibility across platforms (Section 11.6) | Medium | Low | Pin Docker platform to linux/amd64. Document limitation. |
| Scan cycle quantisation and phase jitter add implementation complexity with no visible benefit at 1s polling | Medium | Low | Implement scan cycle quantisation in Phase 4, not Phase 1. It can be added after core engine works. |
| Time-varying covariance (Section 4.3.2) produces gain excursions that make integration tests flaky | Medium | Medium | Use fixed gain (drift_volatility=0) for deterministic tests. Enable drift only in demo and evaluation configs. |
| No test strategy means bugs accumulate through Phases 1-3 and explode in Phase 4 | High | High | Write a test strategy before starting. Define the test pyramid. Target 80% unit coverage on signal models. |
| Per-item signals (filler, checkweigher) do not fit the tick-based engine cleanly | Medium | Medium | Prototype the filler generator early in Phase 3. Resolve the per-item vs per-tick ambiguity first. |
| Signal count grows: packaging 47 + F&B 65 + shared = 112 unique signal definitions. Manual config is error-prone. | Medium | Medium | Generate default configs from the signal tables in the PRD. Validate configs against a signal registry. |

---

## Verdict

**Needs targeted specification before implementation. Not ready as-is.**

The PRD is 90% ready. The signal models, protocol maps, and scenario definitions are implementable. A competent Python developer can read Section 4 and write code. The remaining 10% will cause rework if not addressed first.

Fix these five things before starting:

1. **Write a test strategy.** Define unit tests for each signal model (property-based testing with Hypothesis is ideal for numerical models). Define integration tests for each protocol server. Define end-to-end tests for each scenario. Without this, Phase 4 becomes a debugging marathon.

2. **Specify startup and shutdown sequences.** What order do components initialize? What happens when one protocol server fails to start? How does the process handle SIGTERM? These questions will be answered during implementation regardless. Answer them now to avoid inconsistent implementations across the three protocol adapters.

3. **Resolve the 14 ambiguities listed above.** Each one will cost 30-60 minutes of developer time when discovered during coding. Resolving them now costs 2 hours of PRD editing. The fault code register number, the OPC-UA status code, and the missing sealer signals are the highest priority.

4. **Add configuration validation rules.** At minimum: type checking, range validation, constraint validation (min <= max for all range pairs), and required field checking. Use Pydantic. Define it before Phase 1 starts. Validation bugs that surface in Phase 4 when the F&B config is loaded are expensive.

5. **Budget 12-14 weeks, not 10.** The F&B profile (65 signals, 6 new equipment generators, 7 new scenarios, CDAB byte order, multi-slave Modbus) is allocated 2 weeks. It needs 3-4. The scenario system with intermittent faults, contextual anomalies, and the evaluation framework needs 3 weeks, not 2. Add buffer.

After these fixes, the PRD is ready for implementation. The signal models are the strongest part. The protocol specifications are close to reference-quality. The domain knowledge embedded in the equipment descriptions (Eurotherm controller behaviour, Allen-Bradley byte ordering, IOLink gateway patterns) will save weeks of research during implementation.
