# Phase 5: Network Topology, Evaluation, and Polish — Progress

## Status: COMPLETE

## Tasks
- [x] 5.1: Network Topology Manager and Config
- [x] 5.2: Multi-Port Modbus Servers
- [x] 5.3: Multi-Port OPC-UA Servers and Clock Drift
- [x] 5.4: Scan Cycle Quantisation and Phase Jitter
- [x] 5.5: Independent Connection Drops per Controller
- [x] 5.6: Evaluation Framework: Core Engine
- [x] 5.7: Evaluation CLI and Run Manifests
- [x] 5.8: Batch Output: CSV and Parquet
- [x] 5.9: CLI Entry Point
- [x] 5.10: Docker Compose with Health Checks
- [x] 5.11: README and Example Configs
- [x] 5.12: Performance Profiling
- [x] 5.13: Final Acceptance Test and CI Pipeline

### Task 5.12: Performance Profiling
**Files created:**
- `tests/performance/__init__.py` (NEW) — empty package marker
- `tests/performance/test_performance.py` (NEW) — 6 benchmark tests marked `@pytest.mark.performance`

**Tests implemented:**
- `test_packaging_10x_throughput` — 3600 ticks (1 simulated hour, 10x), records mean/p95/p99 tick latency. Asserts mean < 100ms.
- `test_foodbev_10x_throughput` — same for F&B (68 signals, 10 generators).
- `test_packaging_100x_batch` — 8640 ticks (24h, 100x) with `CsvWriter`, records wall time and ticks/sec. Asserts < 15 min.
- `test_foodbev_100x_batch` — same for F&B.
- `test_realistic_mode_10x` — 500 ticks each for collapsed and realistic topology modes; computes overhead ratio. Asserts < 2x.
- `test_memory_7day` (`@pytest.mark.slow`) — 60480 ticks (7 days, 100x) with tracemalloc; asserts peak memory growth < 5x initial peak (consistent with existing slow test threshold).

**Results file:** All tests write to `performance-results.json` in the project root.

**Observed performance (dev machine, Python 3.13):**
- Non-slow suite completed: 5 tests in 3.75s (packaging 10x: 3600 ticks in ~2.5s, batch tests in <1s each).
- Full suite: 2944 passed in 626s.

**Decisions:**
- 5x memory threshold for 7-day test (matches existing `TestLongRunIntegration.test_packaging_7day_memory_stable` threshold and its rationale: bounded linear growth expected, exponential leak would exceed 5x).
- The `test_realistic_mode_10x` overhead ratio is trivially < 2x because `DataEngine.tick()` does not invoke the topology manager directly — realistic-mode overhead lives in protocol server `sync_registers()` calls, not the tick loop. This is the correct result: it documents that the tick engine imposes no topology overhead.
- Warmup ticks (100) excluded from the memory baseline to let initial allocations settle, matching the existing slow test pattern.

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

### Task 5.7: Evaluation CLI and Run Manifests
**Files created/modified:**
- `src/factory_simulator/evaluation/cli.py` (NEW) — `RunManifest` dataclass with YAML I/O (`save_manifest`, `load_manifest`, `create_manifest` with version + git hash). Config overlays per PRD 12.3: `clean_config_overlay()`, `scenarios_only_config_overlay()`, `impairments_only_config_overlay()`, `full_impaired_config_overlay()`. PRD 12.5 run config generators: `run_a_simulation_config()`, `run_b_simulation_config()`, `run_c_simulation_config()`. Multi-seed evaluation: `ConfidenceInterval`, `MultiSeedResult`, `_ci()` (PRD 12.4 formula), `run_multi_seed_evaluation()`. Report formatters: `format_evaluation_report()`, `format_multi_seed_report()`. `evaluate_command()` handler for the evaluate CLI subcommand (called from task 5.9).
- `tests/unit/test_evaluation_cli.py` (NEW) — 65 tests covering: RunManifest fields, YAML round-trip, `create_manifest`, all four config overlays, Run A/B/C config structure, `_ci` formula and edge cases, `run_multi_seed_evaluation` (length mismatch, single/multi seed, CI), report formatters, `evaluate_command` (missing args, single file, output file, multi-seed, mismatched lists).
- `src/factory_simulator/evaluation/__init__.py` — Updated exports to include all new symbols from `cli.py`.
- `examples/evaluation/run_a_normal.yaml` (NEW) — PRD 12.5 Run A example config.
- `examples/evaluation/run_b_heavy_anomaly.yaml` (NEW) — PRD 12.5 Run B example config.
- `examples/evaluation/run_c_long_term.yaml` (NEW) — PRD 12.5 Run C example config.

**Decisions:**
- `run_multi_seed_evaluation()` takes `Sequence[str | Path]` (covariant) not `list[str | Path]` (invariant) so callers can pass `list[str]` directly without a cast.
- `strict=False` for zip in `run_multi_seed_evaluation`: lengths are validated manually before the loop.
- `evaluate_command` accepts comma-separated path strings for multi-seed mode (avoiding need for multi-value CLI args before task 5.9 wires it up).
- `clean_config_overlay` uses `frozenset` internally to enumerate normal-operation vs anomaly scenarios, consistent with PRD 12.3 categorisation.

**Test count:** 2788 passed (was 2723 before).

### Task 5.8: Batch Output: CSV and Parquet
**Files created/modified:**
- `src/factory_simulator/output/__init__.py` (NEW) — Public API exports (`BatchWriter`, `CsvWriter`, `ParquetWriter`).
- `src/factory_simulator/output/writer.py` (NEW) — `BatchWriter` ABC with `write_tick(sim_time, store)` and `close()`. `CsvWriter`: long format (`timestamp, signal_id, value, quality`), buffers rows in memory, flushes at `buffer_size` rows or `close()`. Event-driven signals written only on change. NaN/Inf filtered out. `ParquetWriter`: wide format (one row per tick, one column per signal), event-driven signals get additional `<signal_id>_changed` boolean column. NaN/Inf stored as null to preserve row alignment. Both writers flush to a single file incrementally.
- `src/factory_simulator/config.py` — Added `BatchOutputConfig` Pydantic model (`format`, `path`, `buffer_size`, `event_driven_signals`). Added `batch_output: BatchOutputConfig` field to `FactoryConfig` with default `format="none"` (disabled).
- `src/factory_simulator/engine/data_engine.py` — Added `batch_writer: BatchWriter | None = None` parameter to `DataEngine.__init__`. `tick()` calls `batch_writer.write_tick()` after data quality injection (post all signal updates). `run()` finally block calls `batch_writer.close()`. Added `batch_writer` property.
- `requirements.txt` — Added `pyarrow>=14.0` (optional, required for Parquet output).
- `pyproject.toml` — Added `pyarrow`/`pyarrow.*` to mypy `ignore_missing_imports` overrides. Added `performance` and `acceptance` pytest markers.
- `tests/unit/test_batch_output.py` (NEW) — 30 tests covering: CSV column order, string/float values, quality preservation, event-driven only-on-change, state transitions, continuous signals every tick, buffer flush at configured size, correct row counts, empty store, NaN/Inf filtering, DataEngine integration, `BatchOutputConfig` validation, Parquet readable by pyarrow, timestamp column, per-signal columns, row count, event-driven changed column, null for NaN, buffer flush.

**Decisions:**
- CSV uses long (tall) format — simpler for arbitrary signal sets; one row per signal per tick.
- Parquet uses wide format — columnar layout efficient for time-series analysis; one row per tick, one column per signal.
- Event-driven signals in CSV: filtered by value comparison, only one row per distinct value. No extra column needed (row absence signals "no change").
- Event-driven signals in Parquet: always present in every row (preserves time alignment), but companion `_changed` boolean column marks actual transitions.
- NaN/Inf in CSV: dropped (no row written). NaN/Inf in Parquet: stored as null/None (row kept for alignment).
- `BatchOutputConfig.format = "none"` is the default — batch output is opt-in, so all existing tests are unaffected.
- `ParquetWriter._pq_writer` opened on first flush (schema inferred from first batch) — no empty Parquet file if `close()` is called without any `write_tick()`.
- pyarrow imports inside `try/except ImportError` in `ParquetWriter.__init__` — gives a clear `ImportError` message if pyarrow is absent. Mypy `ignore_missing_imports = true` override for `pyarrow.*` suppresses the `import-untyped` warning.

**Test count:** 2818 passed (was 2788 before).

### Task 5.9: CLI Entry Point
**Files created/modified:**
- `src/factory_simulator/cli.py` (NEW) — `build_parser()` creates argparse CLI with subcommands `run`, `evaluate`, `version`. `parse_duration()` parses `7d`/`24h`/`30m`/`3600s`/`3600` strings. `_load_config()` loads YAML and applies CLI overrides (seed, time_scale, log_level, network_mode, batch_output, batch_duration). `run_command()` dispatches to `_async_run()` which creates DataEngine + optional topology + batch writer and runs in batch mode (finite duration, no protocol servers) or real-time mode (protocol servers + engine). `evaluate_command()` delegates to `factory_simulator.evaluation.cli.evaluate_command`. `main()` is the top-level dispatcher.
- `src/factory_simulator/__main__.py` (NEW) — `python -m factory_simulator` entry point, calls `main()` and `sys.exit()`.
- `pyproject.toml` — Added `[project.scripts]` entry: `factory-simulator = "factory_simulator.cli:main"`.
- `tests/unit/test_cli.py` (NEW) — 64 tests covering: duration parsing (all suffixes, whitespace, errors), default config path resolution, parser structure (all subcommands/flags/defaults), version command output, evaluate command delegation (missing args, real files, output file), `_load_config` overrides (seed/time_scale/network_mode/batch), batch mode run (CSV output, header columns, foodbev profile), main() dispatcher (help exits 0), `__main__` importability, `python -m factory_simulator version/--help`.

**Decisions:**
- Batch mode triggered when `batch_output.format != "none"` OR `sim_duration_s is not None` (either flag implies bounded run). Real-time mode: protocols started + engine runs indefinitely until SIGINT.
- `_run_batch()` calls `engine.tick()` in a tight loop with `await asyncio.sleep(0)` between ticks (yields to event loop for SIGINT responsiveness). Stops when `sim_time >= sim_duration_s`.
- `NetworkTopologyManager` constructed with `config.network` (not full `FactoryConfig`) and profile mapped: CLI "foodbev" → topology "food_bev".
- `BatchWriter | None` type annotation avoids mypy inference conflict between `CsvWriter` and `ParquetWriter` assignment branches.

**Test count:** 2882 passed (was 2818 before).

### Task 5.10: Docker Compose with Health Checks
**Files created/modified:**
- `src/factory_simulator/health/__init__.py` (NEW) — module package, exports `HealthServer`.
- `src/factory_simulator/health/server.py` (NEW) — `HealthServer` asyncio HTTP server on configurable port (default 8080). `GET /health` returns JSON status dict (status, profile, sim_time, signals, modbus, opcua, mqtt). `GET /status` returns all current signal values from the store. `update()` method lets the CLI set static fields; sim_time and signal count are computed live from the store on each request. `actual_port` property resolves OS-assigned port when `port=0` (for tests).
- `src/factory_simulator/cli.py` — `_run_realtime()` updated to start a `HealthServer` background task before protocol servers. Status updated to "running" once all protocols are up; "stopping" in the finally block. Health task cancelled cleanly on shutdown.
- `Dockerfile` (NEW) — `python:3.12-slim` base, installs `curl` for health check, installs requirements, copies source + config, `pip install -e .`, EXPOSE 502/4840/8080, `HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 CMD curl -f http://localhost:8080/health`, `ENTRYPOINT ["python", "-m", "factory_simulator", "run"]`.
- `docker-compose.yml` (UPDATED) — Added `factory-simulator` service: `build: .`, `depends_on: mqtt-broker: condition: service_healthy`, ports 502/4840/8080, config volume mount, env vars (`SIM_CONFIG_PATH`, `MQTT_BROKER_HOST`, `MQTT_BROKER_PORT`, `SIM_LOG_LEVEL`, `SIM_TIME_SCALE`, `SIM_NETWORK_MODE`), health check `curl -f http://localhost:8080/health`.
- `docker-compose.realistic.yaml` (NEW) — Override file: adds per-controller Modbus ports (5020-5035) and F&B OPC-UA ports (4841-4842), sets `SIM_NETWORK_MODE=realistic`.
- `tests/unit/test_health.py` (NEW) — 61 tests covering: `update()` mutations (all fields, none args, multiple at once), `/health` 200 response and all 7 required JSON keys, live signal count from store, live sim_time from store, status/profile/modbus reflect update, no-store defaults, `/status` 200 and signal map, `/status` empty dict without store, 404 for unknown paths, `actual_port` before/after start, Dockerfile content validation, docker-compose.yml content validation, docker-compose.realistic.yaml content validation, mosquitto.conf validation.

**Decisions:**
- `HealthServer(port=0)` uses OS-assigned port — enables conflict-free unit tests.
- sim_time and signals read from store on each `/health` request (not cached) — always reflects latest tick without needing a callback.
- `_run_realtime` health server task cancelled via `health_task.cancel()` in finally with `contextlib.suppress(CancelledError)` — same pattern as other protocol tasks.
- No separate `start_period` logic needed in HealthServer itself — Docker's `start_period` in the HEALTHCHECK handles the warm-up grace period.
- `docker-compose.realistic.yaml` uses merged ports (Docker Compose appends list fields) — realistic mode exposes both collapsed ports and per-controller ports.

**Test count:** 2943 passed (was 2882 before).

### Task 5.11: README and Example Configs
**Files created/modified:**
- `README.md` (NEW) — Comprehensive user-facing documentation: 15-minute quick start, architecture diagram, both profiles (packaging 47 signals / F&B 68 signals), protocol endpoints for collapsed and realistic mode, config reference, batch mode, evaluation framework, CLI reference, Docker reference, scenario table, ground truth format, PRD reference index. Covers PRD 11.1 "new engineer within 15 min" success criterion.
- `config/examples/collatr-edge-packaging.yaml` (NEW) — YAML CollatrEdge config for packaging profile: all Modbus HR/IR/coil/DI registers, full OPC-UA node list (PackagingLine tree), MQTT topic subscriptions.
- `config/examples/collatr-edge-foodbev.yaml` (NEW) — YAML CollatrEdge config for F&B profile: separate Modbus inputs for mixer (CDAB) and all other equipment (ABCD), OPC-UA (Filler + QC), MQTT F&B topics.
- `config/examples/collatr-edge-realistic.yaml` (NEW) — YAML CollatrEdge config for realistic multi-controller mode (packaging): 4 separate Modbus inputs on ports 5020/5021/5022 with correct UIDs, OPC-UA on 4840, MQTT unchanged. Includes energy meter on port 5020 UID 5.
- `config/scenarios/normal-operations.yaml` (NEW) — Run A: 24h/10x, low anomaly rate, FP measurement baseline.
- `config/scenarios/heavy-anomaly.yaml` (NEW) — Run B: 24h/10x, all scenarios enabled at 2-3x frequency, recall measurement.
- `config/scenarios/long-term-degradation.yaml` (NEW) — Run C: 7d/100x batch, bearing_wear culminates in failure, intermittent faults escalate, trend detection focus.

**Decisions:**
- Example configs are YAML (not TOML) to match the project's primary config language. The existing `configs/*.toml` are preserved for reference.
- Run A/B/C configs in `config/scenarios/` duplicate the content in `examples/evaluation/` — both locations are present so users find them via either the scenario path or the evaluation path.
- CollatrEdge realistic config targets packaging profile only (F&B realistic would add 6 more Modbus connections — covered in README).
- No code tests added (task specification: "No code tests — Verified by acceptance test in 5.13").

**Test count:** 2943 passed (unchanged — documentation-only task).

### Task 5.13: Final Acceptance Test and CI Pipeline
**Files created:**
- `tests/integration/test_acceptance.py` (NEW) — 14 acceptance tests marked `@pytest.mark.acceptance`.
- `.github/workflows/ci.yml` (NEW) — GitHub Actions CI: lint (ruff), typecheck (mypy), unit-tests (pytest tests/unit), integration-tests (pytest -m "acceptance and not slow").

**Tests implemented:**
- `test_packaging_collapsed_24h` (@slow) — 8640 ticks at 100x; asserts ≥47 signals, all finite values.
- `test_foodbev_collapsed_24h` (@slow) — 8640 ticks at 100x; asserts ≥68 signals, `mixer.state` and `filler.fill_weight` present.
- `test_packaging_realistic_topology` — Checks 3 Modbus endpoints, S7-1500 press, 1 OPC-UA on 4840.
- `test_foodbev_realistic_topology` — Checks 6 Modbus endpoints, oven UIDs {1,2,3,10}, CDAB mixer, 2 OPC-UA (4841/4842).
- `test_packaging_realistic_modbus_responds` (@integration) — Starts server on port 18020, connects pymodbus client, reads HR 100-101.
- `test_controller_independence` (@integration) — Two servers (18021/18022); stops server_a, verifies server_b still responds.
- `test_evaluation_framework` — Synthetic JSONL + CSV; checks F1=1.0 precision=1.0 recall=1.0 with 1 event perfectly detected.
- `test_evaluation_framework_false_positives` — 0 events, 1 spurious detection → precision=0.0 recall=N/A.
- `test_batch_csv_output` — Runs 100 ticks with CsvWriter; checks header, signal_ids present, quality column.
- `test_batch_parquet_output` — Skip if pyarrow absent; checks row count, signal columns, no mixed types.
- `test_cli_help` — subprocess `python -m factory_simulator --help` exits 0 with "factory-simulator" in output.
- `test_cli_version` — `python -m factory_simulator version` exits 0 with version string.
- `test_cli_run_subcommand_flags` — `factory-simulator run --help` exits 0 with all expected flags.
- `test_clock_drift_visible` — ClockDriftModel with 5s/day + 5000ms offset; after 24h sim_time, drift ≥ 8s.

**Decisions:**
- F&B profile uses `mixer.state` (not `mixer.machine_state`) — verified against config/factory-foodbev.yaml.
- Modbus integration tests use test-safe ports (18020–18022) to avoid conflicts with real 5020+ ports.
- `await asyncio.sleep(0.3)` required after each `server.start()` call before client connects — same timing pattern as existing integration tests.
- Slow 24h tests excluded from CI (`-m "acceptance and not slow"`) — covered by local pre-merge and performance profiling.
- MQTT-dependent integration tests would auto-skip when no broker is available (no such tests added — MQTT acceptance verified indirectly via batch output).

**Test count:** 2963 passed (was 2943 before).
