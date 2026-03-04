# Phase 5: Network Topology, Evaluation, and Polish — Progress

## Status: IN PROGRESS

## Tasks
- [x] 5.1: Network Topology Manager and Config
- [x] 5.2: Multi-Port Modbus Servers
- [ ] 5.3: Multi-Port OPC-UA Servers and Clock Drift
- [ ] 5.4: Scan Cycle Quantisation and Phase Jitter
- [ ] 5.5: Independent Connection Drops per Controller
- [ ] 5.6: Evaluation Framework: Core Engine
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
