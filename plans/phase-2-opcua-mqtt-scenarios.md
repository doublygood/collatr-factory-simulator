# Phase 2: OPC-UA, MQTT, and Packaging Scenarios

**Timeline:** Weeks 4-5
**Goal:** All three protocols serve packaging data. Core packaging scenarios operational. Ground truth event log active.

## Overview

Phase 2 adds the remaining two protocol adapters (OPC-UA and MQTT) and the remaining packaging scenarios (web break, dryer drift, ink viscosity excursion, registration drift, cold start energy spike, coder consumable depletion, material splice). It also adds the ground truth event log and the composite environment model (carried forward from Phase 1 Y1).

By the end of Phase 2, all 47 packaging signals are accessible on all three protocols simultaneously, 10 scenario types fire during simulation runs, and every scenario event is recorded in the ground truth JSONL log.

## PRD References

Read these sections before starting any task:

- **Section 3** (`prd/03-protocol-endpoints.md`): OPC-UA server (3.2), MQTT publishing (3.3), QoS/retain, payload format
- **Appendix B** (`prd/appendix-b-opcua-node-tree.md`): Full OPC-UA node tree for packaging profile
- **Appendix C** (`prd/appendix-c-mqtt-topic-map.md`): Full MQTT topic map, payload schema, QoS/retain per topic
- **Section 4.7** (`prd/04-data-generation-engine.md`): Ground truth event log format
- **Section 5** (`prd/05-scenario-system.md`): Web break (5.3), dryer drift (5.4), ink viscosity excursion (5.6), registration drift (5.7), cold start spike (5.10), coder depletion (5.12), material splice (5.13a)
- **Section 4.2.2** (`prd/04-data-generation-engine.md`): Composite environmental model (carried from Phase 1 Y1)
- **Phase 0 spike patterns**: `docs/validation-spikes.md` (asyncua and paho-mqtt reference patterns)

## Carried Forward Items

These were deferred from Phase 1 and should be addressed in Phase 2:

- **Y1 (environment composite model)**: Add HVAC cycling + random perturbations to environment generator (PRD 4.2.2)
- **G5 (gutter_fault probability)**: MTBF should be 500+ hours, current rate is ~18x too high. Fix in coder generator defaults or config.

## Task Breakdown

Phase 2 is broken into 16 tasks across 4 groups.

### Group A: OPC-UA Server (Tasks 2.1-2.3)

**Task 2.1: OPC-UA Server Adapter — Node Tree**
- Create `src/factory_simulator/protocols/opcua_server.py`
- Build the full `PackagingLine` node tree per Appendix B:
  - Press1 (21 nodes including Registration, Ink, Dryer, MainDrive sub-folders)
  - Laminator1 (5 nodes)
  - Slitter1 (3 nodes)
  - Energy (2 nodes)
- String NodeIDs: `ns=2;s=PackagingLine.Press1.LineSpeed` etc.
- Data types: Double for analogs, UInt32 for counters, UInt16 for state/enum
- Attributes: EURange from signal config min/max, MinimumSamplingInterval from sample_rate_ms, AccessLevel read-only (read-write for setpoints)
- EnumStrings on State nodes: investigate asyncua support (PRD says SHOULD, budget 0.5 days). If problematic, skip for MVP.
- Use asyncua patterns from `docs/validation-spikes.md`
- Tests: node tree structure matches Appendix B, all nodes browsable, correct data types, EURange set, setpoint nodes writable
- PRD: Section 3.2, Appendix B

**Task 2.2: OPC-UA Server Adapter — Value Sync + Subscriptions**
- Extend `opcua_server.py` with value sync from SignalStore
- On each engine tick (or at configured interval), update all OPC-UA node values from the store
- Support subscriptions: clients creating subscriptions at 500ms+ intervals receive data change notifications
- Minimum server-side publishing interval: 500ms (PRD 3.2)
- StatusCode: Good for normal values, BadSensorFailure for bad quality signals (Phase 4 will add more)
- Setpoint write handling: OPC-UA client writes to setpoint nodes update the signal store (and thus the signal model's target)
- Tests: value round-trip (set in store, read via asyncua client), subscription notifications received, setpoint write propagates, StatusCode correct
- PRD: Section 3.2.3, 3.2.4

**Task 2.3: OPC-UA Integration Tests**
- Create `tests/integration/test_opcua_integration.py`
- Start engine + OPC-UA server, connect asyncua client
- Browse full node tree, verify structure matches Appendix B
- Read all 47 packaging signal values, verify within expected ranges
- Create subscriptions, verify data change notifications arrive
- Write to setpoint nodes, verify value propagation
- Tests: all packaging nodes accessible, values update, subscriptions work, cross-reference with Appendix B
- PRD: Section 13.2 (integration test requirements)

### Group B: MQTT Publisher (Tasks 2.4-2.6)

**Task 2.4: MQTT Publisher Adapter**
- Create `src/factory_simulator/protocols/mqtt_publisher.py`
- MqttPublisher class: connects to Mosquitto sidecar via paho-mqtt 2.0
- Publishes all 16 packaging MQTT signals (11 coder + 2 env + 3 vibration) per Appendix C
- Topic structure: `collatr/factory/{site_id}/{line_id}/{equipment}/{signal}`
- JSON payload: `{"timestamp": "...", "value": ..., "unit": "...", "quality": "..."}`
- Per-signal publish rate from config (event-driven for state/counters, timed for analogs)
- QoS per topic per Appendix C (QoS 1 for state/fault/counter, QoS 0 for analogs)
- Retain flag per topic per Appendix C (Yes for all except vibration)
- LWT: topic `collatr/factory/{site_id}/{line_id}/status`, payload `{"status": "offline"}`
- Client-side message buffer: 1000 limit, drop oldest on overflow (PRD 3.3)
- Use paho-mqtt 2.0 patterns from `docs/validation-spikes.md`
- Tests: correct topic names, correct JSON payload structure, correct QoS/retain per topic, LWT configured
- PRD: Section 3.3, Appendix C

**Task 2.5: MQTT Batch Vibration Topic**
- Add batch vibration topic: `collatr/factory/.../vibration/main_drive`
- Payload: `{"timestamp": "...", "x": ..., "y": ..., "z": ..., "unit": "mm/s", "quality": "..."}`
- Published simultaneously with per-axis topics (both enabled by default, per-axis configurable off)
- Tests: batch topic published with all three axes, per-axis and batch both fire
- PRD: Section 3.3.6

**Task 2.6: MQTT Integration Tests**
- Create `tests/integration/test_mqtt_integration.py`
- Start engine + MQTT publisher + Mosquitto sidecar (Docker)
- Subscribe to all packaging topics, verify messages arrive
- Verify JSON payload structure, QoS, retain flags
- Verify publish rates are approximately correct
- Test retained message: new subscriber receives last value immediately
- Tests: all 16+1 topics publish, correct payloads, retained messages work
- PRD: Section 13.2

### Group C: Packaging Scenarios (Tasks 2.7-2.13)

Each task adds one scenario type.

**Task 2.7: Web Break Scenario**
- Create `src/factory_simulator/scenarios/web_break.py`
- Sequence per PRD 5.3: tension spike >600N for 100-500ms, tension drops to 0, machine_state to Fault, emergency deceleration 5-10s, coils set, recovery 15-60 min
- Must set coil 3 (web_break) to true during event
- Tests: tension spike magnitude and duration, state transitions, coil states, recovery sequence
- PRD: Section 5.3

**Task 2.8: Dryer Temperature Drift Scenario**
- Create `src/factory_simulator/scenarios/dryer_drift.py`
- Sequence per PRD 5.4: one zone drifts 5-15C above setpoint over 30-120 min, waste rate increases 20-50%, recovery
- Tests: drift rate correct, waste rate increase, recovery to setpoint
- PRD: Section 5.4

**Task 2.9: Ink Viscosity Excursion Scenario**
- Create `src/factory_simulator/scenarios/ink_excursion.py`
- Sequence per PRD 5.6: viscosity drifts below 18s or above 45s, registration error increases, waste rate up 10-30%, recovery
- Tests: viscosity outside normal range, registration error increase, waste correlation, recovery
- PRD: Section 5.6

**Task 2.10: Registration Drift Scenario**
- Create `src/factory_simulator/scenarios/registration_drift.py`
- Sequence per PRD 5.7: x or y error drifts beyond +/-0.3mm at 0.01-0.05 mm/s, waste increases when >0.2mm, auto-correction
- Tests: drift rate, waste threshold, recovery
- PRD: Section 5.7

**Task 2.11: Cold Start Energy Spike Scenario**
- Create `src/factory_simulator/scenarios/cold_start.py`
- Sequence per PRD 5.10: after >30min idle, energy spikes 150-200% for 2-5s, current spikes 150-300%, settle to normal
- Trigger: state transition from Off/Idle to Setup/Running after idle threshold
- Tests: spike magnitude and duration, trigger condition (idle >30 min), settle behavior
- PRD: Section 5.10

**Task 2.12: Coder Consumable Depletion Scenario**
- Create `src/factory_simulator/scenarios/coder_depletion.py`
- Sequence per PRD 5.12: ink level depletes linearly, quality "uncertain" at 10%, Fault at 2%, refill to 100%
- Fix G5: correct gutter_fault probability (MTBF 500+ hours = rate ~0.000000556/s)
- Tests: depletion rate, quality flag transitions, fault state, refill behavior
- PRD: Section 5.12

**Task 2.13: Material Splice Scenario**
- Create `src/factory_simulator/scenarios/material_splice.py`
- Sequence per PRD 5.13a: trigger at unwind_diameter <150mm, tension spike 50-100N for 1-3s, registration error increase 0.1-0.3mm for 10-20s, waste spike, unwind reset to 1500mm, speed dip 5-10%
- Machine stays Running (flying splice, no state change)
- Tests: trigger condition, tension spike, registration recovery, unwind reset, speed dip
- PRD: Section 5.13a

### Group D: Ground Truth, Environment Fix, Cross-Protocol (Tasks 2.14-2.16)

**Task 2.14: Ground Truth Event Log**
- Create `src/factory_simulator/engine/ground_truth.py`
- GroundTruthLogger class: writes JSONL to configurable path (default `output/ground_truth.jsonl`)
- First line: config header record per PRD 4.7 (version, seed, profile, signal configs, active scenarios)
- Event types: scenario_start, scenario_end, state_change, signal_anomaly, data_quality, micro_stop, shift_change, consumable, material_splice
- Wire into scenario engine: each scenario start/end writes to the log
- Wire into state machine: state transitions write to the log
- Tests: JSONL format valid, header record structure, event records for each type, all scenario events logged
- PRD: Section 4.7

**Task 2.15: Environment Composite Model**
- Update `src/factory_simulator/generators/environment.py`
- Replace plain sinusoidal with 3-layer composite per PRD 4.2.2:
  1. Daily sinusoidal cycle (existing)
  2. HVAC cycling via BangBangModel with 15-30 min period, 0.5-1.5C amplitude
  3. Random perturbations: Poisson process, 3-8 per shift, 1-3C magnitude, decay via first_order_lag (tau 5-10 min)
- Humidity inversely correlates with temperature
- Add config parameters: hvac_period_minutes, hvac_amplitude_c, perturbation_rate_per_shift, perturbation_magnitude_c, perturbation_decay_tau_minutes
- Tests: output has more variance than pure sine, HVAC cycle visible in FFT or zero-crossing analysis, perturbation events occur at configured rate
- PRD: Section 4.2.2

**Task 2.16: Cross-Protocol Consistency Tests**
- Create `tests/integration/test_cross_protocol.py`
- Start engine + all three protocol adapters simultaneously
- Read the same signal via Modbus (float32), OPC-UA (Double), and MQTT (JSON)
- Verify all three return the same value (within float32 precision for Modbus)
- Verify machine_state is consistent across Modbus HR 210, OPC-UA Press1.State, and observable from coder MQTT topic behavior
- Tests: value consistency across protocols, state consistency, timing within one engine tick
- PRD: Section 13.2 (cross-protocol consistency), Phase 2 exit criteria

## Exit Criteria

From PRD Appendix F:
1. CollatrEdge collects data from Modbus, OPC-UA, and MQTT simultaneously for 24 hours (simulated).
2. No protocol server crashes.
3. Data from all three protocols correlates (same machine state, same line speed across protocols).
4. All 47 packaging signals are accessible on all three protocols.
5. Ground truth log is well-formed JSONL.

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| asyncua server startup slow (~2-5s per server from spikes) | Pre-initialize in fixture, generous test timeouts |
| MQTT integration tests need Docker (Mosquitto) | Mark with `@pytest.mark.integration`, skip when Docker unavailable |
| 7 scenarios is a lot of code | Each is independent. Follow the base scenario pattern from Phase 1. |
| Cross-protocol timing | Engine tick is atomic. All adapters read from same store. Consistency is architectural. |
| EnumStrings on asyncua | Budget 0.5 days (Task 2.1). If problematic, skip and document. |
| Environment composite model complexity | Compose existing models (sinusoidal + bang_bang + first_order_lag). No new math. |

## Notes for Implementation Agent

- OPC-UA and MQTT adapters follow the same pattern as ModbusServer: read from SignalStore, encode for protocol.
- Use spike patterns from `docs/validation-spikes.md` for asyncua and paho-mqtt API usage.
- All scenarios follow the base scenario pattern from `src/factory_simulator/scenarios/base.py`.
- Ground truth logger is write-only, append-only. No reads during simulation.
- The environment composite model composes existing signal models. No new model types needed.
- Run `ruff check src tests && mypy src && pytest` after every change.
- Commit format: `phase-2: <what> (task 2.X)`
