# Phase 5: Network Topology, Evaluation, and Polish

**Timeline:** Weeks 12-13 (2 weeks)
**Goal:** Realistic multi-controller network topology. Evaluation framework. Batch output. CLI. Docker Compose with health checks. README. Ready for engineering team use and demo deployment.

## Overview

Phase 5 is the integration and polish phase. All signal generation, scenarios, and data quality injection are complete (Phases 0-4). Phase 5 adds three major capabilities:

1. **Network topology** — Multi-controller Modbus/OPC-UA servers with per-controller connection behaviour (connection limits, response latency, independent drops, clock drift, scan cycle quantisation)
2. **Evaluation framework** — Event-level anomaly detection metrics per PRD Section 12
3. **Productisation** — CLI, batch output (CSV/Parquet), Docker Compose, README, example configs

By end of Phase 5, a new engineer can clone the repo, run `docker compose up`, and connect CollatrEdge to either profile within 15 minutes.

## PRD References

| Group | PRD Sections |
|-------|-------------|
| **A: Network Topology** | `prd/03a-network-topology.md` (ALL), `prd/appendix-d-configuration-reference.md` (Network Topology Parameters) |
| **B: Scan Cycle** | `prd/03a-network-topology.md` (3a.8), `prd/appendix-d-configuration-reference.md` (Scan Cycle Artefacts) |
| **C: Evaluation** | `prd/12-evaluation-protocol.md` (ALL) |
| **D: Batch Output** | `prd/appendix-f-implementation-phases.md` (Phase 5) |
| **E: CLI & Polish** | `prd/06-configuration.md`, `prd/08-architecture.md`, `prd/11-success-criteria.md` |

## Carried Forward Items

| ID | Source | Description | Disposition |
|----|--------|-------------|-------------|
| Scan cycle quantisation | Phase 4 PRD / Phase 4 independent review | Listed in Phase 4 Appendix F bullet but no task created; independent review noted likely Phase 5 scope | **Task 5.4** |
| Y2 (Phase 4) | IntermittentFault sentinel for current signals | sensor_intermittent disabled by default; edge case | **Deferred** (post-MVP) |

## Task Groups

### Group A: Network Topology Manager (Tasks 5.1-5.5)

The core infrastructure for multi-controller simulation. Collapsed mode (current behaviour) preserved as default for development. Realistic mode adds per-controller ports, connection limits, latency, drops, and clock drift.

### Group B: Evaluation Framework (Tasks 5.6-5.7)

Event-level matching, tolerance windows, severity weighting, random baseline, multi-seed support. Both a library module and a standalone evaluator script.

### Group C: Batch Output (Task 5.8)

CSV and Parquet file output for batch-mode runs. Event-driven signals written only on change.

### Group D: CLI and Productisation (Tasks 5.9-5.13)

Command-line interface, Docker Compose with health checks, README, example CollatrEdge configs, performance profiling, and final acceptance test.

---

## Task Details

### 5.1 — Network Topology Manager and Config

**Group:** A: Network Topology
**Files:** `src/factory_simulator/topology.py` (new), `src/factory_simulator/config.py`
**PRD refs:** `prd/03a-network-topology.md` (3a.4), `prd/appendix-d-configuration-reference.md`

Create the `NetworkTopologyManager` class that maps logical controller endpoints to simulator port bindings.

**Implementation:**

1. Add `NetworkConfig` Pydantic model to `config.py`:
   - `mode: Literal["collapsed", "realistic"]` (default `"collapsed"`)
   - `clock_drift: dict[str, ClockDriftConfig]` — per-controller initial offset and drift rate
   - `scan_cycle: dict[str, ScanCycleConfig]` — per-controller cycle time and jitter
   - `connection_limits: dict[str, ConnectionLimitConfig]` — max connections and response timeout per controller type
   - `connection_drops: dict[str, ConnectionDropConfig]` — MTBF and reconnection delay per controller type

2. Add `ClockDriftConfig`, `ScanCycleConfig`, `ConnectionLimitConfig`, `ConnectionDropConfig` Pydantic models

3. Create `src/factory_simulator/topology.py`:
   - `NetworkTopologyManager(config, profile)` — resolves port mappings based on mode and profile
   - `modbus_endpoints() -> list[ModbusEndpointSpec]` — returns port, unit_ids, register_range, byte_order, controller_type per endpoint
   - `opcua_endpoints() -> list[OpcuaEndpointSpec]` — returns port, node_tree_root, controller_type per endpoint
   - `mqtt_endpoint() -> MqttEndpointSpec` — single MQTT broker config (shared)
   - In collapsed mode: single Modbus port (5020 packaging / 5030 F&B), single OPC-UA port (4840), single MQTT broker (1883)
   - In realistic mode: per-controller ports per PRD 3a.4 table

4. Add `network: NetworkConfig | None = None` to `FactoryConfig`

5. Update both YAML configs with network topology defaults (disabled / collapsed by default)

**Tests:**
- Collapsed mode returns single endpoints per protocol
- Realistic mode returns correct port count (4 Modbus + 1 OPC-UA for packaging; 7 Modbus + 2 OPC-UA for F&B)
- Port mapping matches PRD 3a.4 table
- Config validation rejects negative MTBF, negative offsets
- Both YAML configs load with new network section

---

### 5.2 — Multi-Port Modbus Servers

**Group:** A: Network Topology
**Files:** `src/factory_simulator/protocols/modbus_server.py`, `src/factory_simulator/engine/data_engine.py`
**PRD refs:** `prd/03a-network-topology.md` (3a.2, 3a.3, 3a.4, 3a.5)
**Depends on:** 5.1

Wire the topology manager into `DataEngine` to spawn multiple Modbus server instances in realistic mode.

**Implementation:**

1. `ModbusServer` changes:
   - Accept `endpoint: ModbusEndpointSpec` parameter (port, unit_ids, register_range, byte_order, controller_type)
   - In realistic mode, each server only serves registers in its assigned range
   - Reads to addresses outside the range return Modbus exception 0x02 (Illegal Data Address)
   - `byte_order` configurable per endpoint (CDAB for mixer, ABCD for others)
   - Connection limit enforcement: reject connections beyond `max_connections` for the controller type
   - Response latency injection: add configurable delay per read (drawn from controller type's range)

2. `DataEngine` changes:
   - Accept optional `NetworkTopologyManager` in constructor
   - `_create_protocol_servers()` method:
     - In collapsed mode: create one `ModbusServer` as before
     - In realistic mode: create one `ModbusServer` per `topology.modbus_endpoints()`
   - Track all servers in a list for cleanup

3. Multi-slave support on shared port:
   - Oven gateway (port 5031): UIDs 1, 2, 3 (oven zones) + UID 10 (energy meter)
   - Press port (5020): UID 1 (press) + UID 5 (energy meter) — packaging only
   - Each UID routes to different register ranges within the same server

**Tests:**
- Collapsed mode: single server serves all registers
- Realistic mode: each server only serves its register range
- Out-of-range reads return exception 0x02
- CDAB byte order on mixer endpoint
- Multi-slave UID routing on oven gateway port
- Connection limit enforcement (reject excess connections)
- Response latency within configured range

---

### 5.3 — Multi-Port OPC-UA Servers and Clock Drift

**Group:** A: Network Topology
**Files:** `src/factory_simulator/protocols/opcua_server.py`, `src/factory_simulator/engine/data_engine.py`
**PRD refs:** `prd/03a-network-topology.md` (3a.2, 3a.3, 3a.5)
**Depends on:** 5.1

Wire topology manager for OPC-UA servers. Implement per-controller clock drift.

**Implementation:**

1. `OpcuaServer` changes:
   - Accept `endpoint: OpcuaEndpointSpec` (port, node_tree_root, controller_type)
   - In realistic mode: each server serves only its node subtree
   - Packaging: 1 OPC-UA server (port 4840, full PackagingLine tree)
   - F&B: 2 OPC-UA servers (port 4841 for Filler, port 4842 for QC/Checkweigher)
   - Session timeout enforcement per PRD 3a.5

2. Clock drift implementation (applies to both OPC-UA and MQTT):
   - `ClockDriftModel(initial_offset_ms, drift_rate_s_per_day)` class
   - Formula: `drifted_time = sim_time + initial_offset/1000 + drift_rate * elapsed_hours/24`
   - Each protocol adapter receives its controller's `ClockDriftModel`
   - OPC-UA: `SourceTimestamp` uses drifted time
   - MQTT: JSON timestamp field uses drifted time (already uses sim_time; add drift offset)
   - Modbus: no timestamps, drift does not apply
   - Ground truth log always uses true sim_time (not drifted)

3. `DataEngine` changes:
   - Create OPC-UA servers per topology in realistic mode
   - Pass clock drift models to protocol adapters

**Tests:**
- Collapsed mode: single OPC-UA server
- Realistic F&B: two OPC-UA servers on different ports
- Clock drift formula produces correct offsets
- Eurotherm drift (5-10 s/day) visible after 24h simulation
- OPC-UA SourceTimestamp uses drifted time
- MQTT timestamp uses drifted time
- Ground truth uses true sim_time
- Session timeout enforcement

---

### 5.4 — Scan Cycle Quantisation and Phase Jitter

**Group:** A: Network Topology
**Files:** `src/factory_simulator/protocols/modbus_server.py`, `src/factory_simulator/topology.py`
**PRD refs:** `prd/03a-network-topology.md` (3a.8), `prd/appendix-d-configuration-reference.md`
**Depends on:** 5.1, 5.2

Implement scan cycle artefacts per PRD Section 3a.8.

**Implementation:**

1. `ScanCycleModel` class (in `topology.py` or new `scan_cycle.py`):
   - `cycle_ms: float` — base scan cycle time
   - `jitter_pct: float` — uniform jitter range (0-10%)
   - `next_scan_boundary: float` — next update time (sim_time)
   - `last_scan_values: dict[str, float]` — register values snapped to last scan output
   - `tick(sim_time, current_values) -> dict[str, float]` — returns quantised values
   - Formula: `actual_cycle = cycle_ms * (1.0 + uniform(0, jitter_pct))`
   - Values update at scan boundaries; between boundaries, return stale values

2. Wire into Modbus server:
   - In realistic mode: each `ModbusServer` has a `ScanCycleModel` for its controller type
   - `sync_registers()` passes values through `ScanCycleModel.tick()` before writing to context
   - In collapsed mode: no scan cycle quantisation (direct passthrough as currently)

3. Per-controller scan cycle times from PRD:
   - S7-1500 (press): 10ms, jitter 5%
   - S7-1200 (laminator, slitter, filler, sealer, CIP): 20ms, jitter 8%
   - CompactLogix (mixer): 15ms, jitter 6%
   - Eurotherm 3504 (oven zones): 100ms, jitter 10%
   - Danfoss (chiller): 100ms, jitter 10%
   - PM5560 (energy): 50ms, jitter 5% (note: shares port with oven in F&B)

**Tests:**
- Values are stale between scan boundaries
- Values update at scan boundaries
- Jitter produces non-identical cycle times
- Eurotherm 100ms scan cycle produces more stale reads than S7-1500 10ms
- Zero jitter produces perfectly periodic cycles
- Collapsed mode: no quantisation applied
- Deterministic with seeded RNG

---

### 5.5 — Independent Connection Drops per Controller

**Group:** A: Network Topology
**Files:** `src/factory_simulator/protocols/modbus_server.py`, `src/factory_simulator/protocols/opcua_server.py`
**PRD refs:** `prd/03a-network-topology.md` (3a.5)
**Depends on:** 5.2, 5.3

In realistic mode, each controller endpoint has independent connection drop behaviour with configurable MTBF and reconnection delay.

**Implementation:**

1. Reuse `CommDropScheduler` from Phase 4 (`comm_drop.py`):
   - Each endpoint gets its own `CommDropScheduler` instance with controller-specific MTBF
   - MTBF values from PRD 3a.5: S7-1500 72h+, S7-1200 48h+, CompactLogix 48h+, Eurotherm GW 8-24h, Danfoss 24-48h, PM5560 72h+
   - Reconnection delay: S7-1500 1-3s, S7-1200 2-5s, etc.

2. The existing `CommDropScheduler` works at protocol level (one per protocol). In realistic mode, create one per *controller endpoint* instead.

3. Modbus: during drop, server stops responding on that port (existing freeze behaviour)
4. OPC-UA: during drop, server writes `UncertainLastUsableValue` on that port's nodes only
5. One controller dropping does NOT affect other controllers

**Tests:**
- Eurotherm gateway drops independently of press PLC
- Drop on one Modbus port does not affect reads on other ports
- MTBF-based scheduling produces drops at expected frequency
- Reconnection delay enforced
- Collapsed mode: single comm drop per protocol (existing Phase 4 behaviour)

---

### 5.6 — Evaluation Framework: Core Engine

**Group:** B: Evaluation Framework
**Files:** `src/factory_simulator/evaluation/__init__.py` (new), `src/factory_simulator/evaluation/evaluator.py` (new), `src/factory_simulator/evaluation/metrics.py` (new)
**PRD refs:** `prd/12-evaluation-protocol.md` (ALL)
**Depends on:** none

Implement the event-level anomaly detection evaluation engine per PRD Section 12.

**Implementation:**

1. `evaluation/metrics.py`:
   - `EventMatch` dataclass: event_type, start_time, end_time, detected (bool), detection_time (optional), latency (optional)
   - `EvaluationResult` dataclass: precision, recall, f1, weighted_recall, weighted_f1, per_scenario_results, detection_latency_median, detection_latency_p90, random_baseline
   - `compute_metrics(matches, severity_weights) -> EvaluationResult`

2. `evaluation/evaluator.py`:
   - `Evaluator(config: EvaluationConfig)` class
   - `load_ground_truth(path) -> list[GroundTruthEvent]` — parse JSONL sidecar
   - `load_detections(path) -> list[Detection]` — parse detection alert file (CSV: timestamp, alert_type, signal_id, confidence)
   - `match_events(events, detections) -> list[EventMatch]` — event-level matching with tolerance windows
   - `evaluate(ground_truth_path, detections_path) -> EvaluationResult`
   - Tolerance windows: `pre_margin_seconds` (default 30), `post_margin_seconds` (default 60)
   - Overlapping windows: detection assigned to nearest event by start time
   - Random baseline: compute anomaly density, generate random detections, compute baseline metrics

3. `EvaluationConfig` Pydantic model in `config.py`:
   - `pre_margin_seconds: float = 30.0`
   - `post_margin_seconds: float = 60.0`
   - `severity_weights: dict[str, float]` — defaults from PRD 12.4 table
   - `seeds: int = 1` (1 for development, 10 for benchmarking)
   - `latency_targets: dict[str, float]` — per-scenario targets from PRD 12.4

4. Severity weights from PRD:
   - web_break: 10.0, unplanned_stop: 5.0, seal_integrity_failure: 8.0, cold_chain_break: 10.0
   - bearing_wear: 8.0, dryer_drift: 3.0, oven_excursion: 3.0, fill_weight_drift: 3.0
   - ink_viscosity_excursion: 2.0, registration_drift: 2.0, contextual_anomaly: 5.0
   - intermittent_fault: 4.0, micro_stop: 1.0, sensor_disconnect: 2.0, stuck_sensor: 3.0

**Tests:**
- Perfect detector: all events detected, precision=1.0, recall=1.0, F1=1.0
- No detections: precision undefined (0), recall=0.0
- False positives only: recall=0.0, precision=0.0
- Tolerance window matching: early detection (within pre_margin) counts as TP
- Late detection (within post_margin) counts as TP
- Detection outside all windows is FP
- Overlapping windows: detection assigned to nearest event
- Severity-weighted recall computed correctly
- Random baseline produces non-zero recall, low precision
- Per-scenario breakdown correct
- Detection latency computation (median, p90)

---

### 5.7 — Evaluation CLI and Run Manifests

**Group:** B: Evaluation Framework
**Files:** `src/factory_simulator/evaluation/cli.py` (new), `src/factory_simulator/evaluation/manifest.py` (new)
**PRD refs:** `prd/12-evaluation-protocol.md` (12.2, 12.3, 12.5)
**Depends on:** 5.6

Add CLI commands for evaluation workflows and run manifest generation.

**Implementation:**

1. `evaluation/manifest.py`:
   - `RunManifest` dataclass: config_path, seed, profile, sim_duration_s, wall_clock_start, wall_clock_end, simulator_version, git_hash
   - `write_manifest(path, manifest)` — write YAML manifest
   - `read_manifest(path) -> RunManifest` — read and validate manifest

2. `evaluation/cli.py`:
   - `evaluate` subcommand: `python -m factory_simulator evaluate --ground-truth <path> --detections <path> [--config <eval_config>]`
   - Prints results table (per-scenario and overall)
   - `--format json` for machine-readable output
   - `--multi-seed N` for N-seed evaluation with confidence intervals

3. Clean/impaired pairing support:
   - Config helper to generate clean config (scenarios disabled except job_changeover and shift_change)
   - Three pairing modes per PRD 12.3: scenarios-only, impairments-only, full impaired

4. Recommended run configs from PRD 12.5:
   - Run A (Normal Operations, 24h, 10x)
   - Run B (Heavy Anomaly, 24h, 10x)
   - Run C (Long-Term Degradation, 7d, 100x batch)
   - Provide example config files in `config/evaluation/`

**Tests:**
- Manifest writes and reads round-trip correctly
- CLI produces valid output for sample ground truth + detections
- Clean config disables all scenarios except operational ones
- Multi-seed evaluation computes mean/std/CI

---

### 5.8 — Batch Output: CSV and Parquet

**Group:** C: Batch Output
**Files:** `src/factory_simulator/output/__init__.py` (new), `src/factory_simulator/output/writer.py` (new)
**PRD refs:** `prd/appendix-f-implementation-phases.md` (Phase 5 — batch output)
**Depends on:** none

Add file output for batch-mode runs (time compression > 10x, no live protocol serving).

**Implementation:**

1. `output/writer.py`:
   - `BatchWriter(config: BatchOutputConfig)` abstract base
   - `CsvWriter(path, signals)` — writes CSV: timestamp, signal_id, value, quality
   - `ParquetWriter(path, signals)` — writes Parquet with columnar per-signal layout
   - Event-driven signals (machine_state, fault_code) written only on change, with `changed: bool` column
   - Flush to disk periodically (configurable buffer size, default 10,000 rows)

2. `BatchOutputConfig` Pydantic model:
   - `format: Literal["csv", "parquet", "none"]` (default `"none"`)
   - `path: str` — output directory
   - `buffer_size: int = 10000`
   - `event_driven_signals: list[str]` — signals that only write on change

3. Wire into `DataEngine`:
   - When `batch_output.format != "none"` and time_scale > 10: disable protocol adapters, enable batch writer
   - `BatchWriter.write_tick(sim_time, store)` called after each engine tick
   - `BatchWriter.close()` called on shutdown (flush remaining buffer)

4. Parquet dependency: add `pyarrow` to requirements (optional dependency group)

**Tests:**
- CSV output has correct column order
- Parquet output readable by pyarrow
- Event-driven signals only written on change
- Buffer flushes at configured size
- Correct number of rows for configured duration
- No NaN or Inf in output

---

### 5.9 — CLI Entry Point

**Group:** D: CLI and Productisation
**Files:** `src/factory_simulator/cli.py` (new), `src/factory_simulator/__main__.py` (new or update)
**PRD refs:** `prd/appendix-f-implementation-phases.md` (Phase 5 — CLI)
**Depends on:** 5.8

Create the command-line interface for the simulator.

**Implementation:**

1. CLI arguments (using `argparse` or `click`):
   - `--config <path>` — path to YAML config file (default: `config/factory.yaml`)
   - `--profile <name>` — override profile selection (`packaging` or `food_bev`)
   - `--seed <int>` — override random seed
   - `--time-scale <float>` — override time compression factor
   - `--batch-output <path>` — enable batch output to directory
   - `--batch-duration <duration>` — simulation duration for batch mode (e.g. `7d`, `24h`, `1h`)
   - `--batch-format <format>` — `csv` or `parquet` (default: `csv`)
   - `--network-mode <mode>` — `collapsed` or `realistic`
   - `--log-level <level>` — override log level
   - `--version` — print version and exit

2. `__main__.py`:
   - `python -m factory_simulator [run|evaluate|version]`
   - `run` subcommand starts the simulator
   - `evaluate` subcommand runs evaluation (delegates to evaluation CLI)

3. Entry point in `pyproject.toml`:
   - `[project.scripts]` entry: `factory-sim = "factory_simulator.cli:main"`

**Tests:**
- `--version` prints version string
- `--config` loads specified config
- `--profile` overrides profile selection
- `--seed` overrides random seed
- `--time-scale` overrides time compression
- Invalid arguments produce helpful error messages
- Batch mode arguments work together

---

### 5.10 — Docker Compose with Health Checks

**Group:** D: CLI and Productisation
**Files:** `Dockerfile`, `docker-compose.yaml`, `config/mosquitto.conf`
**PRD refs:** `prd/06-configuration.md` (6.3)
**Depends on:** 5.9

Create production-ready Docker deployment.

**Implementation:**

1. `Dockerfile`:
   - Base: `python:3.12-slim`
   - Install dependencies from `requirements.txt`
   - Copy source
   - Entrypoint: `python -m factory_simulator run`
   - Health check: `curl -f http://localhost:8080/health`

2. `docker-compose.yaml`:
   - Mosquitto sidecar (eclipse-mosquitto:2) with health check
   - Factory simulator with health check
   - Depends on mqtt-broker (condition: service_healthy)
   - Port mappings for collapsed mode: 502 (Modbus), 4840 (OPC-UA), 1883 (MQTT), 8080 (health)
   - Port mappings for realistic mode: ranges 5020-5035 (Modbus), 4840-4842 (OPC-UA)
   - Environment variable overrides per PRD Section 6.4
   - Volume mount for config directory

3. `docker-compose.realistic.yaml` (override file):
   - Adds realistic-mode port mappings
   - Sets `SIM_NETWORK_MODE=realistic`

4. `config/mosquitto.conf`:
   - Listener 1883 on 0.0.0.0
   - Allow anonymous
   - No persistence (default)

5. Health endpoint implementation:
   - `src/factory_simulator/health/server.py` — simple HTTP server on port 8080
   - `GET /health` → 200 with status JSON
   - `GET /status` → current signal values

**Tests:**
- Dockerfile builds successfully
- Health endpoint responds with correct JSON structure
- Mosquitto conf is valid

---

### 5.11 — README and Example Configs

**Group:** D: CLI and Productisation
**Files:** `README.md`, `config/examples/`
**PRD refs:** `prd/11-success-criteria.md` (11.1)
**Depends on:** 5.9, 5.10

Write the user-facing documentation.

**Implementation:**

1. `README.md`:
   - Quick start (clone, docker compose up, verify data)
   - Architecture overview (link to PRD)
   - Two profiles: packaging and F&B
   - Protocol endpoints and how to connect
   - Configuration reference (link to Appendix D)
   - Batch mode usage
   - Evaluation framework usage
   - Development setup (Python 3.12+, pytest, ruff, mypy)

2. Example CollatrEdge configs:
   - `config/examples/collatr-edge-packaging.yaml` — CollatrEdge config for packaging profile
   - `config/examples/collatr-edge-foodbev.yaml` — CollatrEdge config for F&B profile
   - `config/examples/collatr-edge-realistic.yaml` — CollatrEdge config for realistic multi-controller mode

3. Example scenario configs:
   - `config/scenarios/normal-operations.yaml` — Run A config from PRD 12.5
   - `config/scenarios/heavy-anomaly.yaml` — Run B config
   - `config/scenarios/long-term-degradation.yaml` — Run C config

**Tests:**
- No code tests (documentation only)
- Verified by acceptance test in 5.13

---

### 5.12 — Performance Profiling

**Group:** D: CLI and Productisation
**Files:** `tests/performance/test_performance.py` (new)
**PRD refs:** `prd/appendix-f-implementation-phases.md` (Phase 5 — performance profiling)
**Depends on:** 5.2, 5.3, 5.4

Profile the simulator at 10x (protocol serving) and 100x (batch generation) to establish baselines.

**Implementation:**

1. `tests/performance/test_performance.py`:
   - `test_packaging_10x_throughput`: 1-hour sim at 10x, measure tick latency (mean, p95, p99)
   - `test_foodbev_10x_throughput`: same for F&B
   - `test_packaging_100x_batch`: 24h batch output, measure total wall time and throughput (ticks/sec)
   - `test_foodbev_100x_batch`: same for F&B
   - `test_realistic_mode_10x`: packaging at 10x with realistic network topology, measure overhead
   - `test_memory_7day`: 7-day batch run, verify RSS < 2x initial (echoes Phase 4 slow test)

2. All marked `@pytest.mark.performance` (separate from normal test runs)

3. Output: write results to `performance-results.json` for tracking across commits

**Tests:**
- Performance tests run without error (assertions on reasonable bounds, not hard targets)
- Results file written successfully

---

### 5.13 — Final Acceptance Test and CI Pipeline

**Group:** D: CLI and Productisation
**Files:** `tests/integration/test_acceptance.py` (new), `.github/workflows/ci.yml` (new or update)
**PRD refs:** `prd/11-success-criteria.md` (ALL), `prd/appendix-f-implementation-phases.md` (Phase 5 exit criteria)
**Depends on:** 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 5.12

The final acceptance test verifies Phase 5 exit criteria from the PRD.

**Implementation:**

1. `tests/integration/test_acceptance.py`:
   - `test_packaging_collapsed_24h`: run packaging in collapsed mode for 24 simulated hours at 100x, verify all 47 signals present, no NaN/Inf, counters increment, state transitions occur
   - `test_foodbev_collapsed_24h`: same for F&B (68 signals)
   - `test_packaging_realistic_topology`: start in realistic mode, verify per-controller Modbus ports respond, out-of-range reads return 0x02
   - `test_foodbev_realistic_topology`: verify 7 Modbus endpoints, 2 OPC-UA endpoints, multi-slave oven gateway
   - `test_controller_independence`: drop one controller, verify others continue serving
   - `test_evaluation_framework`: run simulator, generate ground truth, create synthetic detections, run evaluator, verify metrics computed
   - `test_batch_csv_output`: batch mode produces valid CSV
   - `test_batch_parquet_output`: batch mode produces valid Parquet (requires pyarrow)
   - `test_cli_help`: CLI --help works without error
   - `test_clock_drift_visible`: 24h run with Eurotherm drift, verify timestamp offset > 0

2. CI pipeline (`.github/workflows/ci.yml`):
   - Trigger: push to main, PR to main
   - Jobs: lint (ruff), type check (mypy), unit tests, integration tests
   - Matrix: Python 3.12
   - Cache: pip dependencies

3. Mark acceptance tests `@pytest.mark.acceptance`

**Tests:**
- All acceptance criteria from PRD 11 are verified
- CI pipeline configuration is valid YAML

---

## Exit Criteria (from PRD Appendix F)

1. A new engineer can clone the repo, run `docker compose up`, and connect CollatrEdge to either profile within 15 minutes following the README
2. CollatrEdge maintains connections to all controllers simultaneously in realistic mode
3. One controller dropping does not affect data collection from others
4. Evaluation framework produces correct metrics against a known test dataset
5. Both profiles run for 7 days at 100x in batch mode without divergent values or memory leaks
6. All unit, integration, and acceptance tests pass
7. CI pipeline passes on push

## Dependencies Graph

```
5.1 (Topology Config)
 ├── 5.2 (Multi-Port Modbus)
 │    ├── 5.4 (Scan Cycle)
 │    ├── 5.5 (Independent Drops)
 │    └── 5.12 (Performance)
 ├── 5.3 (Multi-Port OPC-UA + Clock Drift)
 │    ├── 5.5 (Independent Drops)
 │    └── 5.12 (Performance)
 └── 5.13 (Acceptance) ← all tasks

5.6 (Evaluation Core) → 5.7 (Evaluation CLI)

5.8 (Batch Output) → 5.9 (CLI)

5.9 (CLI) → 5.10 (Docker) → 5.11 (README)

5.12 (Performance) → 5.13 (Acceptance)
```

Tasks with no dependencies: 5.1, 5.6, 5.8
Parallelisable groups: (5.1 chain) and (5.6-5.7) and (5.8-5.9)
