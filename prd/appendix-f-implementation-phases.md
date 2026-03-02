# Appendix F: Implementation Phases

**Total timeline: 13 weeks.**

The original estimate was 10 weeks. Implementation reviewers identified that the F&B profile complexity (40% more signals, CDAB byte ordering, multi-slave Modbus, 7 new scenarios) was underestimated, and the scenario system with intermittent faults, contextual anomalies, and evaluation framework needed more time. The revised plan adds 3 weeks of capacity distributed across Phases 3, 4, and 5.

## Phase 0: Validation Spikes (Week 1, first 2 days)

**Goal:** Confirm library feasibility before committing to architecture.

Three spikes, each 2-3 hours:

1. **Multi-server pymodbus.** 7+ async Modbus servers on different ports, each with a different register map and unit ID set, all in one asyncio event loop. Verify concurrent serving under load from multiple clients.

2. **Mosquitto sidecar integration.** Docker Compose with Mosquitto sidecar and a Python publisher using paho-mqtt. Publish 50 msg/s with mixed QoS 0/1, retained messages. Subscribe from an external client. Measure latency and message loss. Verify retained message behaviour on subscriber reconnect.

3. **asyncua multiple instances.** 3 asyncua servers on different ports, each with a small node tree, all in one event loop. Subscribe from an external client. Verify data change notifications arrive at the correct rate.

**Exit criteria:** All three spikes pass. If any spike fails, redesign the affected component before proceeding. Document spike results in `docs/validation-spikes.md`.

## Phase 1: Core Engine, Modbus, and Test Infrastructure (Weeks 1-3)

**Goal:** Simulator starts, generates all 47 packaging signals, serves them over Modbus TCP. Test infrastructure established from day one.

- Configuration loader (YAML parsing, Pydantic validation models).
- Configuration validation rules: type checking, range validation (no negative sigma, no negative time_scale), constraint validation (min <= max for all range pairs), required field checking, correlation matrix positive-definiteness, Student-t df >= 3.
- Profile manager (packaging profile active by default).
- Simulation clock with time compression (simulated time invariant enforced at architecture level).
- Signal value store (float and string value support via union type).
- All 12 signal models (steady_state, sinusoidal, first_order_lag, ramp, random_walk, counter, depletion, correlated_follower, state_machine, thermal_diffusion, bang_bang_hysteresis, string_generator).
- All 7 packaging equipment generators (press, laminator, slitter, coder, environment, energy, vibration).
- Coder generator produces all 11 signals.
- Cholesky correlation pipeline with correct ordering (generate independent, correlate, scale).
- Noise distributions (Gaussian, Student-t, AR(1)) with speed-dependent sigma.
- Modbus TCP server with full packaging register map.
- Basic scenario support (job changeover, shift change, unplanned stop).
- Startup sequence: config validation, signal store init, engine init, protocol servers start in order (Modbus, OPC-UA connect to broker, health check last). Readiness gates: each component signals ready before the next starts.
- Graceful shutdown: SIGTERM handler drains protocol connections, flushes ground truth log, writes final state.
- Structured logging: JSON format, per-component log levels, correlation IDs for request tracing.
- Docker container.
- **Test infrastructure from day one:**
  - pytest + hypothesis + pytest-asyncio configured.
  - Unit tests for every signal model (property-based with Hypothesis).
  - Unit tests for Cholesky pipeline and noise distributions.
  - Unit tests for configuration validation (valid and invalid cases).
  - Integration test: pymodbus client reads all registers and verifies value ranges.
  - CI pipeline: ruff, mypy, unit tests, integration tests, smoke test.

**Exit criteria:** CollatrEdge connects via Modbus TCP and collects data from all holding registers, input registers, coils, and discrete inputs for 1 hour. All 47 packaging signals produce values within expected ranges. Counters increment. State transitions occur. All unit and integration tests pass. CI pipeline runs under 5 minutes.

## Phase 2: OPC-UA, MQTT, and Packaging Scenarios (Weeks 4-5)

**Goal:** All three protocols serve packaging data. Core packaging scenarios operational.

- OPC-UA server with full `PackagingLine` node tree.
- OPC-UA subscriptions and data change notifications.
- OPC-UA engineering units, EURange, MinimumSamplingInterval on all variable nodes.
- OPC-UA minimum server-side publishing interval: 500ms (matches fastest signal rate).
- MQTT publishing via paho-mqtt to Mosquitto sidecar.
- MQTT JSON payloads on all 17 packaging topics.
- Retained messages. QoS 0 and QoS 1 support.
- MQTT message buffering during connection loss (1000 message limit, drop oldest on overflow).
- Web break scenario with tension spike.
- Dryer temperature drift (recovery via first-order lag with configured tau).
- Ink viscosity excursion.
- Registration drift.
- Cold start energy spike (motor inrush physics, not dataset reference).
- Coder consumable depletion.
- Material splice scenario.
- Ground truth event log (JSONL sidecar).
- Integration tests: OPC-UA client + MQTT subscriber + cross-protocol consistency.

**Exit criteria:** CollatrEdge collects data from Modbus, OPC-UA, and MQTT simultaneously for 24 hours. No protocol server crashes. Data from all three protocols correlates (same machine state, same line speed across protocols). All 47 packaging signals are accessible on all three protocols. Ground truth log is well-formed.

## Phase 3: F&B Profile (Weeks 6-8)

**Goal:** F&B profile fully operational with 65 signals and F&B-specific scenarios.

Expanded from 2 weeks to 3. The F&B profile is 40% larger than packaging with additional complexity: CDAB byte ordering on the Allen-Bradley mixer, multi-slave Eurotherm Modbus addressing, 6 new equipment generators with independent state machines, and 7 new scenario types.

- Profile manager supports switching between packaging and food_bev profiles.
- Six F&B equipment generators (mixer, oven, filler, sealer, chiller, CIP).
- Shared generators (coder, environment, energy) work with both profiles.
- F&B Modbus register map with CDAB byte order and multi-slave addressing.
- Oven output power signal added (IR 2 on Eurotherm multi-slave per Section 3.1.6).
- F&B OPC-UA `FoodBevLine` node tree.
- F&B MQTT publishing on all F&B topics.
- Thermal diffusion model for product core temperature with oven tunnel length parameter.
- Per-item signal handling for filler: fill_weight generates one value per simulated item arrival, gated by filler.line_speed.
- Batch cycle scenario (mixer).
- Oven thermal excursion scenario.
- Fill weight drift scenario.
- Seal integrity failure scenario (rewritten to use existing signals: seal_temp, seal_pressure, vacuum_level).
- Chiller door alarm scenario.
- CIP cycle scenario with production stop cascade (mixer, filler, sealer to Idle; oven at temperature with no product; chiller continues).
- Cold chain break scenario.
- Allergen changeover with mandatory CIP.
- Unit tests for all F&B generators and scenarios.
- Integration test: CollatrEdge connects to F&B profile and collects all 65 signals.

**Exit criteria:** CollatrEdge connects to the F&B profile via all three protocols and collects 65 signals for 24 hours. Mixer batch cycles complete in 20-45 minutes. Oven zones show thermal coupling. Fill weight follows Gaussian distribution around target. All F&B scenario types fire. All tests pass.

## Phase 4: Full Scenario System and Data Quality (Weeks 9-11)

**Goal:** All scenarios operational for both profiles. Data quality injection active. Scenario scheduling engine complete.

Expanded from 2 weeks to 3. The scenario scheduling engine, data quality injection, intermittent faults, and contextual anomalies are more complex than originally estimated.

- Scenario scheduling engine with Poisson inter-arrival times and minimum gap equal to scenario minimum duration. Scenarios crossing shift boundaries continue into the next shift.
- Scenario priority rules: state-changing scenarios (web break, unplanned stop, job changeover) preempt non-state-changing scenarios (dryer drift, ink excursion, bearing wear). Non-state-changing scenarios can overlap. Contextual anomaly timeout: cancel if target state does not occur within 2x scheduled window.
- Motor bearing wear (long-term degradation, packaging profile).
- Micro-stops (Poisson process, speed dips only).
- Contextual anomalies (5 types).
- Intermittent faults (3-phase progression, 4 subtypes).
- Random seed support for reproducible runs (numpy.random.Generator with SeedSequence for subsystem isolation, no random module).
- Data quality injection (communication drops, sensor noise, exceptions).
- Sensor disconnect with sentinel values.
- Stuck sensor / frozen value injection.
- Noise calibration for all 47 packaging signals and 65 F&B signals.
- Counter rollover testing support.
- Duplicate timestamp injection.
- Modbus exception responses.
- Partial Modbus responses (subclass ReadHoldingRegistersResponse).
- Timezone offset simulation for MQTT.
- Scan cycle quantisation and phase jitter per controller.

**Exit criteria:** Run each profile for 7 days at 100x in batch mode (under 2 real hours). All scenario types fire at least once (note: intermittent fault Phase 3 requires batch mode to reach within test window). Anomaly patterns are detectable by threshold-based checks. No divergent values. Memory stable (RSS < 2x initial). Reproducibility test passes (byte-identical output for same seed on same platform).

## Phase 5: Network Topology, Evaluation, and Polish (Weeks 12-13)

**Goal:** Realistic multi-controller network topology. Evaluation framework. Ready for engineering team use and demo deployment.

- Network topology manager with "collapsed" and "realistic" modes.
- Per-controller Modbus TCP servers (4 ports packaging, 7 ports F&B).
- Per-endpoint OPC-UA servers (1 packaging, 2 F&B).
- Connection limits per controller type (S7-1500: 16, S7-1200: 3, Eurotherm gateway: 2).
- Response latency simulation per controller type.
- Independent connection drop/reconnect per controller with configurable MTBF.
- Per-controller clock drift.
- Multi-slave Modbus polling (oven gateway: UID 1/2/3/10 on same port).
- CDAB byte order enforcement on Allen-Bradley mixer endpoint.
- Evaluation framework (Section 12): event-level matching, tolerance windows, severity weighting, random baseline, multi-seed N=10 runs.
- Batch mode output: CSV and Parquet. CSV column order: timestamp, signal_id, value, quality. Parquet schema with columnar per-signal layout. Event-driven signals (machine_state) written only on change, with a `changed` flag column.
- CLI: `--config`, `--profile`, `--seed`, `--time-scale`, `--batch-output`, `--batch-duration`.
- README with quick start guide covering both profiles.
- Configuration documentation for profile selection and all parameters.
- Example CollatrEdge configuration files for both packaging and F&B profiles.
- Docker Compose with health checks and Mosquitto sidecar.
- CI pipeline running integration tests against both profiles.
- Performance profiling at 10x (protocol serving) and 100x (batch generation).
- Nightly CI: 24-hour long-run stability test.

**Exit criteria:** A new engineer can clone the repo, run `docker compose up`, and connect CollatrEdge to either profile within 15 minutes following the README. CollatrEdge maintains connections to all controllers simultaneously in realistic mode. One controller dropping does not affect data collection from others. Evaluation framework produces correct metrics against a known test dataset.
