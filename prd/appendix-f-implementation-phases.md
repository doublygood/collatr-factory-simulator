# Appendix F: Implementation Phases

## Phase 1: Core Engine and Modbus (Weeks 1-3)

**Goal:** Simulator starts, generates all 40 signals, serves them over Modbus TCP.

- Configuration loader (YAML parsing, validation).
- Simulation clock with time compression.
- Signal value store.
- All 9 signal models (steady_state through state_machine).
- All 7 equipment generators.
- Correlation model linking generators.
- Modbus TCP server with full register map.
- Basic scenario support (job changeover, shift change, unplanned stop).
- Docker container.
- Integration test: pymodbus client reads all registers and verifies value ranges.

**Exit criteria:** CollatrEdge connects via Modbus TCP and collects data from all holding registers, input registers, coils, and discrete inputs for 1 hour. Values are within expected ranges. Counters increment. State transitions occur.

## Phase 2: OPC-UA and MQTT (Weeks 4-5)

**Goal:** All three protocols serve data simultaneously.

- OPC-UA server with full node tree.
- OPC-UA subscriptions and data change notifications.
- Embedded MQTT broker.
- MQTT publishing with JSON payloads.
- Retained messages.
- QoS 0 and QoS 1 support.
- Integration test: CollatrEdge connects to all three protocols simultaneously.

**Exit criteria:** CollatrEdge collects data from Modbus, OPC-UA, and MQTT simultaneously for 24 hours. No protocol server crashes. Data from all three protocols correlates (same machine state, same line speed across protocols).

## Phase 3: Full Scenario System (Weeks 6-7)

**Goal:** All 10 scenario types operational with configurable scheduling.

- Web break scenario with tension spike.
- Dryer temperature drift.
- Motor bearing wear (long-term degradation).
- Ink viscosity excursion.
- Registration drift.
- Cold start energy spike.
- Coder consumable depletion.
- Data quality injection (communication drops, sensor noise, exceptions).
- Scenario scheduling engine (statistical profiles).
- Random seed support for reproducible runs.

**Exit criteria:** Run the simulator for 7 days at 100x speed (1.68 real hours). All scenario types fire at least once. Anomaly patterns are detectable by threshold-based checks. No divergent values. Memory stable.

## Phase 4: Polish and Documentation (Week 8)

**Goal:** Ready for engineering team use and demo deployment.

- README with quick start guide.
- Configuration documentation.
- Example CollatrEdge configuration files for connecting to the simulator.
- Docker Compose with health checks.
- CI pipeline running integration tests against the simulator.
- Performance profiling at 100x speed.
- Web dashboard showing current signal values and active scenarios (optional).

**Exit criteria:** A new engineer can clone the repo, run `docker compose up`, and connect CollatrEdge within 15 minutes following the README.
