# Phase 5: Network Topology, Evaluation, and Polish — Progress

## Status: IN PROGRESS

## Tasks
- [x] 5.1: Network Topology Manager and Config
- [ ] 5.2: Multi-Port Modbus Servers
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
