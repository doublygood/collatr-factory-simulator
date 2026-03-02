# Appendix F: Implementation Phases

## Phase 1: Core Engine and Modbus (Weeks 1-3)

**Goal:** Simulator starts, generates all 47 packaging signals, serves them over Modbus TCP.

- Configuration loader (YAML parsing, validation).
- Profile manager (packaging profile active by default).
- Simulation clock with time compression.
- Signal value store.
- All 9 signal models (steady_state through state_machine).
- All 7 packaging equipment generators (press, laminator, slitter, coder, environment, energy, vibration).
- Coder generator produces all 11 signals (including ink_pump_speed, ink_pressure, ink_viscosity_actual, supply_voltage, ink_consumption_ml, nozzle_health, gutter_fault).
- Correlation model linking generators.
- Modbus TCP server with full packaging register map.
- Basic scenario support (job changeover, shift change, unplanned stop).
- Docker container.
- Integration test: pymodbus client reads all registers and verifies value ranges.

**Exit criteria:** CollatrEdge connects via Modbus TCP and collects data from all holding registers, input registers, coils, and discrete inputs for 1 hour. All 47 packaging signals produce values within expected ranges. Counters increment. State transitions occur.

## Phase 2: OPC-UA, MQTT, and Packaging Scenarios (Weeks 4-5)

**Goal:** All three protocols serve packaging data. Core packaging scenarios operational.

- OPC-UA server with full `PackagingLine` node tree.
- OPC-UA subscriptions and data change notifications.
- Embedded MQTT broker.
- MQTT publishing with JSON payloads on all 17 packaging topics.
- Retained messages. QoS 0 and QoS 1 support.
- Web break scenario with tension spike.
- Dryer temperature drift.
- Ink viscosity excursion.
- Registration drift.
- Cold start energy spike.
- Coder consumable depletion.
- Integration test: CollatrEdge connects to all three protocols simultaneously.

**Exit criteria:** CollatrEdge collects data from Modbus, OPC-UA, and MQTT simultaneously for 24 hours. No protocol server crashes. Data from all three protocols correlates (same machine state, same line speed across protocols). All 47 packaging signals are accessible on all three protocols.

## Phase 3: F&B Profile (Weeks 6-7)

**Goal:** F&B profile fully operational with 65 signals and F&B-specific scenarios.

- Profile manager supports switching between packaging and food_bev profiles.
- Six F&B equipment generators (mixer, oven, filler, sealer, chiller, CIP).
- Shared generators (coder, environment, energy) work with both profiles.
- F&B Modbus register map with CDAB byte order and multi-slave addressing.
- F&B OPC-UA `FoodBevLine` node tree.
- F&B MQTT publishing on all 13 F&B topics.
- Batch cycle scenario (mixer).
- Oven thermal excursion scenario.
- Fill weight drift scenario.
- Seal integrity failure scenario.
- Chiller door alarm scenario.
- CIP cycle scenario.
- Cold chain break scenario.
- Integration test: CollatrEdge connects to F&B profile and collects all 65 signals.

**Exit criteria:** CollatrEdge connects to the F&B profile via all three protocols and collects 65 signals for 24 hours. Mixer batch cycles complete in 20-45 minutes. Oven zones show thermal coupling. Fill weight follows Gaussian distribution around target.

## Phase 4: Full Scenario System, Data Quality, and Network Topology (Weeks 8-9)

**Goal:** All scenarios operational for both profiles. Data quality injection active. Realistic multi-controller network topology.

- Motor bearing wear (long-term degradation, packaging profile).
- Scenario scheduling engine (statistical profiles for both packaging and F&B).
- Random seed support for reproducible runs.
- Data quality injection (communication drops, sensor noise, exceptions).
- Noise calibration for all 47 packaging signals and 65 F&B signals.
- Counter rollover testing support.
- Duplicate timestamp injection.
- Modbus exception responses.
- Timezone offset simulation for MQTT.
- Network topology manager with "collapsed" and "realistic" modes.
- Per-controller Modbus TCP servers (4 ports packaging, 7 ports F&B).
- Per-endpoint OPC-UA servers (1 packaging, 2 F&B).
- Connection limits per controller type (S7-1500: 16, S7-1200: 3, Eurotherm gateway: 2).
- Response latency simulation per controller type.
- Independent connection drop/reconnect per controller with configurable MTBF.
- Multi-slave Modbus polling (oven gateway: UID 1/2/3/10 on same port).
- CDAB byte order enforcement on Allen-Bradley mixer endpoint.

**Exit criteria:** Run each profile for 7 days at 100x speed (1.68 real hours). All scenario types fire at least once. Anomaly patterns are detectable by threshold-based checks. No divergent values. Memory stable. CollatrEdge maintains connections to all controllers simultaneously in realistic mode. One controller dropping does not affect data collection from others.

## Phase 5: Polish, Documentation, and Demo (Week 10)

**Goal:** Ready for engineering team use and demo deployment.

- README with quick start guide covering both profiles.
- Configuration documentation for profile selection and all parameters.
- Example CollatrEdge configuration files for both packaging and F&B profiles.
- Docker Compose with health checks.
- CI pipeline running integration tests against both profiles.
- Performance profiling at 100x speed (47 signals and 65 signals).
- Web dashboard showing current signal values and active scenarios (optional).

**Exit criteria:** A new engineer can clone the repo, run `docker compose up`, and connect CollatrEdge to either profile within 15 minutes following the README.
