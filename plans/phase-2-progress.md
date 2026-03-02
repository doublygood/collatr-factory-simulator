# Phase 2: OPC-UA, MQTT, and Packaging Scenarios - Progress

## Status: In Progress (1/16 tasks complete)

## Tasks
- [x] 2.1: OPC-UA Server Adapter — Node Tree
- [ ] 2.2: OPC-UA Server Adapter — Value Sync + Subscriptions
- [ ] 2.3: OPC-UA Integration Tests
- [ ] 2.4: MQTT Publisher Adapter
- [ ] 2.5: MQTT Batch Vibration Topic
- [ ] 2.6: MQTT Integration Tests
- [ ] 2.7: Web Break Scenario
- [ ] 2.8: Dryer Temperature Drift Scenario
- [ ] 2.9: Ink Viscosity Excursion Scenario
- [ ] 2.10: Registration Drift Scenario
- [ ] 2.11: Cold Start Energy Spike Scenario
- [ ] 2.12: Coder Consumable Depletion Scenario
- [ ] 2.13: Material Splice Scenario
- [ ] 2.14: Ground Truth Event Log
- [ ] 2.15: Environment Composite Model
- [ ] 2.16: Cross-Protocol Consistency Tests

## Notes

### Task 2.1 (Complete)

**Files created/modified:**
- `src/factory_simulator/protocols/opcua_server.py` — OpcuaServer class
- `tests/unit/test_protocols/test_opcua.py` — 25 unit tests, all pass
- `config/factory.yaml` — added `opcua_node`/`opcua_type` to `press1.ink_viscosity` and `press1.ink_temperature` (both were missing from PRD Appendix B)

**What was built:**
- `OpcuaServer` class: `start()` / `stop()` lifecycle (same pattern as `ModbusServer`)
- Node tree built dynamically from signal config `opcua_node` fields
- Dot-separated paths (`PackagingLine.Press1.Dryer.Zone1.Setpoint`) create folder hierarchy via `folder_cache` to avoid duplicate folder creation
- 32 leaf variable nodes matching PRD Appendix B (22 Press1 + 5 Laminator1 + 3 Slitter1 + 2 Energy)
- String NodeIDs: `ns=2;s=PackagingLine.Press1.LineSpeed` etc.
- EURange property on every variable node from `min_clamp`/`max_clamp`
- AccessLevel 3 (read-write) for `modbus_writable=True` setpoints (3 dryer zone setpoints); AccessLevel 1 (read-only) for all others
- `actual_port` property resolves OS-assigned port (port=0) after start
- `_update_loop()` placeholder for task 2.2 value sync; runs at 500ms interval

**Decisions:**
- Function-scoped fixtures in test file: pytest-asyncio 1.3.0 with `asyncio_default_test_loop_scope=function` causes asyncio event loop mismatch when using module-scoped async fixtures. Function-scoped avoids this entirely.
- `ua.NodeId(0, 0)` for EURange property NodeID: asyncua generates an auto-assigned NodeId for property nodes; passing `(0, 0)` works correctly.

**Test results:** 25/25 unit tests pass. No regressions (1045 total unit tests pass).
