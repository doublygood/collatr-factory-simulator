# Phase 5: Network Topology, Evaluation, and Polish — Progress

## Status: IN PROGRESS

## Tasks
- [x] 5.1: Network Topology Manager and Config
- [x] 5.2: Multi-Port Modbus Servers
- [x] 5.3: Multi-Port OPC-UA Servers and Clock Drift
- [x] 5.4: Scan Cycle Quantisation and Phase Jitter
- [x] 5.5: Independent Connection Drops per Controller
- [x] 5.6: Evaluation Framework: Core Engine
- [ ] 5.7: Evaluation CLI and Run Manifests
- [ ] 5.8: Batch Output: CSV and Parquet
- [ ] 5.9: CLI Entry Point
- [ ] 5.10: Docker Compose with Health Checks
- [ ] 5.11: README and Example Configs
- [ ] 5.12: Performance Profiling
- [ ] 5.13: Final Acceptance Test and CI Pipeline

## Carried Forward Items
- Y2 (Phase 4): IntermittentFault sentinel for current signals — deferred post-MVP
- Scan cycle quantisation: listed in Phase 4 Appendix F but correctly scoped to Phase 5 (per-controller topology required)

## Notes

### Task 5.1: Network Topology Manager and Config
**Files created/modified:**
- `src/factory_simulator/config.py` — Added `ClockDriftConfig`, `ScanCycleConfig`, `ConnectionLimitConfig`, `ConnectionDropConfig`, `NetworkConfig` Pydantic models. Added `network: NetworkConfig | None = None` to `FactoryConfig`. Added `SIM_NETWORK_MODE` env var override. Import of `Literal` added.
- `src/factory_simulator/topology.py` (NEW) — `NetworkTopologyManager` class with collapsed/realistic mode support. `ModbusEndpointSpec`, `OpcuaEndpointSpec`, `MqttEndpointSpec` frozen dataclasses. Default per-controller-type configs from PRD 3a.5/3a.8 tables.
- `config/factory.yaml` — Added commented network section.
- `config/factory-foodbev.yaml` — Added commented network section.
- `tests/unit/test_topology.py` (NEW) — 57 tests covering config validation, collapsed mode, realistic mode (packaging and F&B), config overrides, YAML loading.

**Decisions:**
- Packaging realistic Modbus: 3 server endpoints (press+energy on 5020, laminator on 5021, slitter on 5022). Energy meter shares press port as UID 5, so 3 servers not 4. CollatrEdge makes 4 connections (separate polls to UID 1 and UID 5 on port 5020).
- F&B realistic Modbus: 6 server endpoints (mixer 5030, oven_gw 5031 with UIDs 1/2/3/10, filler 5032, sealer 5033, chiller 5034, CIP 5035).
- `register_range` left as `None` for all endpoints at this stage — register range enforcement is task 5.2.
- `network: None` in FactoryConfig means collapsed defaults (backward compatible).
- Default controller configs use PRD 3a.5/3a.8 values. User can override per controller_name in YAML.

**Test count:** 2516 passed (was 2459+ before).

### Task 5.2: Multi-Port Modbus Servers
**Files created/modified:**
- `src/factory_simulator/topology.py` — Added `equipment_ids` and `uid_equipment_map` fields to `ModbusEndpointSpec`. Populated in `_packaging_modbus()` and `_foodbev_modbus()` to map which equipment IDs and UID→equipment relationships each endpoint serves.
- `src/factory_simulator/protocols/modbus_server.py` — Added `equipment_filter` parameter to `build_register_map()` for per-controller register filtering. Added `valid_hr_addresses`/`valid_ir_addresses` to `FactoryDeviceContext` with 0x02 (IllegalAddress) enforcement for out-of-range reads. Added `endpoint: ModbusEndpointSpec` parameter to `ModbusServer.__init__` for realistic-mode per-controller servers. Multi-UID routing in `start()` maps all endpoint UIDs to the primary device context.
- `src/factory_simulator/engine/data_engine.py` — Added `topology` parameter and property. Added `create_modbus_servers()` method: collapsed mode returns single server, realistic mode returns one per endpoint from topology manager.
- `tests/unit/test_protocols/test_modbus_multiport.py` (NEW) — 39 tests covering register map filtering, 0x02 address validation, endpoint-based server creation, CDAB byte order on mixer, multi-slave UID routing, connection config, DataEngine server creation for both profiles, and backward compatibility.

**Decisions:**
- Register range enforcement uses valid address sets checked in `FactoryDeviceContext.getValues()` rather than switching to `ModbusSparseDataBlock` — keeps backward compatibility with collapsed mode.
- Multi-UID on shared ports (press+energy on 5020, oven gateway on 5031): all UIDs map to the same primary device context which contains both equipment's registers. pymodbus `ModbusServerContext(devices={uid: ctx}, single=False)` handles routing.
- Response latency: config value stored from endpoint but actual per-request delay injection deferred (pymodbus contexts are synchronous; async delay requires custom handler — future task 5.4/5.5).
- Connection limit enforcement: config stored on endpoint, actual TCP limiting deferred (requires custom server class).

**Test count:** 2555 passed (was 2516 before).

### Task 5.3: Multi-Port OPC-UA Servers and Clock Drift
**Files created/modified:**
- `src/factory_simulator/protocols/opcua_server.py` — Added `endpoint: OpcuaEndpointSpec | None` and `clock_drift: ClockDriftModel | None` parameters to `OpcuaServer.__init__`. Port resolution: endpoint overrides config, explicit arg overrides both. `_node_tree_root` set from endpoint (empty = serve all nodes). `_sync_values` applies clock drift to `SourceTimestamp` when `_clock_drift` is set — otherwise no SourceTimestamp is written (asyncua uses server receive time).
- `src/factory_simulator/topology.py` — `ClockDriftModel` class added: `drifted_time(sim_time)` formula per PRD 3a.5. `drift_offset(sim_time)` helper. Properties for `initial_offset_s` and `drift_rate_s_per_day`. Already had `OpcuaEndpointSpec` with `clock_drift` field and `_packaging_opcua()` / `_foodbev_opcua()` methods.
- `src/factory_simulator/protocols/mqtt_publisher.py` — `clock_drift: ClockDriftModel | None` parameter added to `MqttPublisher.__init__`. `_publish_entry` and `_publish_batch_vib` apply drift to `sv.timestamp` before calling `make_payload()`.
- `src/factory_simulator/engine/data_engine.py` — `create_opcua_servers()` method added: collapsed mode → single server (full tree, no drift); realistic mode → one server per endpoint with `ClockDriftModel` from endpoint config.
- `tests/unit/test_clock_drift_opcua.py` (NEW) — 38 tests covering: ClockDriftModel formula, `_sim_time_to_datetime`, OPC-UA node tree filtering (filler/QC/full), SourceTimestamp drift visibility, MQTT payload clock drift, ground truth no-drift invariant, DataEngine server creation for both modes and profiles, OpcuaServer construction variants.

**Decisions:**
- Packaging realistic mode: 1 OPC-UA server on port 4840 serving full PackagingLine tree (same as collapsed — press PLC is dual-stack).
- F&B realistic mode: 2 OPC-UA servers — port 4841 for FoodBevLine.Filler1 (7 nodes), port 4842 for FoodBevLine.QC1 (6 nodes).
- Ground truth logger never receives a ClockDriftModel — enforced by construction (no parameter in signature). Verified by test.
- Clock drift for MQTT: applied to `sv.timestamp` before ISO conversion. Timezone offset (PRD 10.7) stacks on top.
- No SourceTimestamp when drift is None: asyncua assigns its own server-side timestamp, which is correct default behaviour per OPC-UA spec.

**Test count:** 2593 passed (was 2555 before).

### Task 5.4: Scan Cycle Quantisation and Phase Jitter
**Files created/modified:**
- `src/factory_simulator/topology.py` — Added `ScanCycleModel` class. `prepare_tick(sim_time)` determines if scan boundary crossed; `get_value(signal_id, current_value)` returns cached stale value or fresh value. Formula: `actual_cycle = cycle_ms * (1.0 + rng.uniform(0, jitter_pct))`. First tick always active (boundary starts at 0.0ms).
- `src/factory_simulator/protocols/modbus_server.py` — Added `scan_cycle_model: ScanCycleModel | None = None` parameter to `ModbusServer.__init__`. Changed `sync_registers()` to `sync_registers(sim_time: float = 0.0)` — calls `prepare_tick(sim_time)` when model is set. Scan quantisation applied in `_sync_holding_registers()`, `_sync_input_registers()`, and `_sync_secondary_slaves()`. `_update_loop()` derives sim_time from max signal timestamp in the store before calling `sync_registers(sim_time)`.
- `src/factory_simulator/engine/data_engine.py` — `create_modbus_servers()` in realistic mode spawns a dedicated `ScanCycleModel` RNG (isolated via `_root_ss.spawn(1)[0]`) and creates a `ScanCycleModel` for each endpoint, passing it to `ModbusServer`.
- `tests/unit/test_scan_cycle.py` (NEW) — 36 tests covering: ScanCycleModel basic operation, stale/active boundary transitions, jitter range and determinism, per-controller PRD defaults (S7-1500/S7-1200/Eurotherm/Danfoss), ModbusServer integration (HR quantisation, stale reads, boundary update), DataEngine server creation in collapsed and realistic mode, consecutive stale read stability.

**Decisions:**
- Scan quantisation only applies to numeric HR and IR values, not coils/discrete inputs (booleans don't benefit from scan stale modelling).
- `_update_loop()` reads max signal timestamp from the store as a proxy for current sim_time. This is correct since the engine always updates signals to the current tick's sim_time before protocols read the store.
- Collapsed mode: `scan_cycle_model=None` always, no quantisation — existing tests unchanged.
- Secondary slave (Eurotherm) IR blocks also quantised since they share the same controller endpoint.

**Test count:** 2629 passed (was 2593 before).

### Task 5.5: Independent Connection Drops per Controller
**Files created/modified:**
- `src/factory_simulator/protocols/modbus_server.py` — Added `_connection_drop_to_comm_drop()` helper: converts `ConnectionDropConfig` (MTBF-based) to `CommDropConfig` (frequency-based) by mapping `frequency = 1/mtbf_hours`. In `ModbusServer.__init__`, when `endpoint` is provided (realistic mode), the drop scheduler is created from the endpoint's `connection_drop` spec instead of the global `config.data_quality.modbus_drop`. Added runtime import of `CommDropConfig`; added `ConnectionDropConfig` to TYPE_CHECKING block.
- `src/factory_simulator/engine/data_engine.py` — In `create_modbus_servers()` realistic mode, each endpoint now spawns an isolated `comm_drop_rng` (`self._root_ss.spawn(1)[0]`) and passes it to `ModbusServer`, giving each controller an independent, reproducible drop RNG (Rule 13).
- `tests/unit/test_protocols/test_independent_comm_drops.py` (NEW) — 36 tests covering: MTBF→CommDropConfig conversion (Eurotherm, S7-1500, Danfoss), frequency ordering (short MTBF = high freq), duration mapping, drop scheduler independence (distinct objects, one drop does not affect others), DataEngine realistic mode server counts (packaging=3, F&B=6), per-controller MTBF rates verified (press S7-1500 at 1/72, oven Eurotherm at 1/8, chiller Danfoss at 1/24), RNG isolation, collapsed mode backward compatibility.

**Decisions:**
- `_connection_drop_to_comm_drop`: freq_min = 1/mtbf_max, freq_max = 1/mtbf_min — inverted because higher MTBF = lower drop frequency. Duration maps 1:1 from reconnection_delay to CommDropConfig.duration_seconds.
- Collapsed mode unchanged: `endpoint is None` → uses `config.data_quality.modbus_drop` exactly as before. No existing tests regressed.
- Each server in DataEngine realistic mode gets two separate `SeedSequence.spawn()` calls — one for scan_cycle, one for drop_rng — preserving independence from the scan cycle RNG stream.

**Test count:** 2665 passed (was 2629 before).

### Task 5.6: Evaluation Framework: Core Engine
**Files created/modified:**
- `src/factory_simulator/evaluation/evaluator.py` (NEW) — `Evaluator` class with `load_ground_truth()` (JSONL parser, FIFO start/end pairing), `load_detections()` (CSV parser with ISO or float UNIX timestamps), `evaluate_from_data()`, and internal `_compute()`. `match_events()` function implements PRD 12.4 tolerance windows: effective window `[start - pre_margin, end + post_margin]`, overlapping window tie-breaking by nearest start, multi-detection deduplication (one TP per event).
- `src/factory_simulator/evaluation/metrics.py` (NEW) — Data classes: `EventMatch`, `ScenarioMetrics`, `RandomBaseline`, `EvaluationResult`. `DEFAULT_SEVERITY_WEIGHTS` and `DEFAULT_LATENCY_TARGETS` from PRD 12.4.
- `src/factory_simulator/evaluation/__init__.py` (NEW) — Public API exports.
- `src/factory_simulator/config.py` — Added `EvaluationConfig` Pydantic model with `pre_margin_seconds`, `post_margin_seconds`, `severity_weights`, `seeds`, `latency_targets`, and `@field_validator` rejecting negative margins and non-positive seeds.
- `tests/unit/test_evaluator.py` (NEW) — 58 tests covering: match_events (boundary conditions, overlapping windows, FP/TP/FN), overall metrics (perfect/no/partial/mixed detections), severity-weighted recall and F1, detection latency (median, p90, negative latency), per-scenario breakdown, random baseline (structure, density, determinism), JSONL loading (pairs, open events, FIFO, non-scenario events), CSV loading (ISO/float timestamps, minimal columns), EvaluationConfig validation.

**Decisions:**
- FIFO pairing for overlapping same-type scenarios (first start → first end). This matches the natural chronological order of scenario injection.
- Overlapping window tie-breaking: `|detection_time - event.start_time|` as distance metric per PRD 12.4.
- Random baseline uses seeded `np.random.default_rng` for reproducibility. Anomaly density computed over time range extended by margins.
- `_parse_iso()` handles both `Z` suffix (replace with `+00:00`) and proper ISO 8601 offsets.
- `EvaluationConfig` lives in `config.py` alongside all other config models (not in `evaluation/`).

**Test count:** 2723 passed (was 2665 before).
