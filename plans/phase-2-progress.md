# Phase 2: OPC-UA, MQTT, and Packaging Scenarios - Progress

## Status: In Progress (4/16 tasks complete)

## Tasks
- [x] 2.1: OPC-UA Server Adapter — Node Tree
- [x] 2.2: OPC-UA Server Adapter — Value Sync + Subscriptions
- [x] 2.3: OPC-UA Integration Tests
- [x] 2.4: MQTT Publisher Adapter
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

### Task 2.2 (Complete)

**Files modified:**
- `src/factory_simulator/protocols/opcua_server.py` — Added `_cast_to_opcua_value` helper, `_setpoint_nodes`/`_last_written_setpoints` tracking, full `_sync_values` implementation, updated `_update_loop`
- `tests/unit/test_protocols/test_opcua.py` — Added `TestCastToOpcuaValue` (7 tests) and `TestValueSync` (6 tests)

**What was built:**
- `_cast_to_opcua_value(value, vtype)`: casts SignalStore float/str values to correct Python type (float/int/str) for the OPC-UA VariantType
- `_update_loop`: now calls `_sync_values()` immediately on start, then every 500ms (PRD 3.2 minimum publishing interval)
- `_sync_values()`: two-phase sync:
  - Phase 1 (setpoint write-back): reads each writable setpoint node; if value differs from last server-written value, a client wrote it → propagates new value to SignalStore
  - Phase 2 (store → OPC-UA): for every registered node, reads from store; writes with `StatusCode.Good` for good/uncertain quality, `StatusCode.BadSensorFailure` for bad quality; updates `_last_written_setpoints` for setpoint nodes
- `_build_node_tree`: now clears all node state dicts before rebuild (clean restart support); populates `_setpoint_nodes` and initialises `_last_written_setpoints` to zero for each setpoint

**Decisions:**
- Setpoint write detection uses "last written" tracking rather than raw comparison: avoids false positives when engine and client both write setpoints within the same cycle
- Phase 1 (read from OPC-UA) runs before Phase 2 (write to OPC-UA) so client writes are detected before being overwritten by the store value
- `store_val` timestamp is 0.0 for client-written setpoints (engine controls timing; it overwrites on next tick)
- Only `quality="bad"` maps to `BadSensorFailure`; "uncertain" maps to Good (PRD: "Phase 4 will add more")

**Test results:** 38/38 unit tests pass. No regressions (1058 total unit tests pass).

### Task 2.3 (Complete)

**Files created:**
- `tests/integration/test_opcua_integration.py` — 19 integration tests, all pass

**What was built:**
Two fixtures:
- `opcua_static`: engine ticked 5× synchronously, known values injected into store, OpcuaServer started, engine NOT running async. Used for node structure, value range, and setpoint write tests.
- `opcua_live`: engine running as an asyncio task (100ms ticks), OpcuaServer syncing every 500ms. Used for subscription delivery tests.

Test classes:
- `TestHierarchicalBrowse` (5 tests): traverses the OPC-UA folder hierarchy from `client.nodes.objects` down through PackagingLine → equipment folders → sub-folders. Verifies Press1 sub-folders (Registration, Ink, Dryer, MainDrive, Unwind, Rewind) and Dryer zones (Zone1-3) exist.
- `TestAllNodesAccessible` (7 tests): all 32 Appendix B nodes readable by NodeId, node count=32, Double nodes finite and within EURange, UInt16 nodes within clamp range, UInt32 counters non-negative, key injected values match OPC-UA readback (validates full sync path), StatusCode.Good for good-quality signals.
- `TestSetpointWrite` (3 tests): single zone setpoint write propagates to store, all three zone setpoints independent, read-only node write rejected.
- `TestSubscriptionsWithLiveEngine` (3 tests): initial subscription notification fires, store-injected change appears in subscription events, three-node subscription all deliver notifications.
- `TestNamespaceConfiguration` (1 test): NAMESPACE_URI registered at ns=2.

**Decisions:**
- Two fixtures instead of one: `opcua_static` isolates value-specific assertions from engine interference; `opcua_live` provides live value changes for subscription tests.
- `_base_config()` helper DRY-ups fixture setup (config, store, clock, engine).
- Read-only write test uses bare `except Exception` with `pytest.fail` rather than `pytest.raises` to avoid importing asyncua exception classes (which have `ignore_missing_imports` in mypy config).
- `await asyncio.sleep(0.6)` in `opcua_static` ensures at least one full 500ms sync cycle completes before client connects.

**Test results:** 19/19 integration tests pass. No regressions (1139 total tests pass).

### Task 2.4 (Complete)

**Files created/modified:**
- `src/factory_simulator/protocols/mqtt_publisher.py` — MqttPublisher class, 260 lines
- `tests/unit/test_protocols/test_mqtt.py` — 74 unit tests, all pass
- `src/factory_simulator/config.py` — Added `line_id: str = "packaging1"` to `MqttProtocolConfig`
- `config/factory.yaml` — Fixed `mqtt_topic` for environment signals: `"environment/"` → `"env/"` to match PRD Appendix C

**What was built:**
- `TopicEntry` dataclass: captures signal_id, full topic path, QoS, retain, publish interval, unit, and mutable scheduling state (last_published, last_value)
- `build_topic_map(config)`: scans all equipment signal configs for `mqtt_topic` field; constructs `{topic_prefix}/{site_id}/{line_id}/{mqtt_topic}` full paths; derives QoS/retain/event-driven from relative topic suffix per PRD 3.3.5 and 3.3.8
- `make_payload(value, quality, unit)`: returns UTF-8 JSON bytes with `{timestamp, value, unit, quality}` (PRD 3.3.4); timestamp in `YYYY-MM-DDTHH:MM:SS.mmmZ` format
- `MqttPublisher` class: constructor takes config + store + optional client injection; `start()`/`stop()` lifecycle; `_publish_loop()` async task at 100ms granularity; `_publish_due(now)` dispatches timed and event-driven publishes
- paho-mqtt 2.0 `CallbackAPIVersion.VERSION2` imported from `paho.mqtt.enums` directly (avoids mypy attr-defined error)
- LWT configured via `client.will_set()` before `client.connect()` per spike pattern
- Buffer limit set via `client.max_queued_messages_set(buffer_limit)` per PRD 3.3

**QoS rules implemented (PRD 3.3.5):**
- QoS 1: `coder/state`, `coder/prints_total`, `coder/nozzle_health`, `coder/gutter_fault`
- QoS 0: all other coder, env, vibration topics

**Retain rules (PRD 3.3.8):**
- No retain: all `vibration/*` topics
- Retain=True: all other topics

**Event-driven vs timed publish:**
- Event-driven (publish on value change): `coder/state`, `coder/prints_total`, `coder/nozzle_health`, `coder/gutter_fault`
- Timed: all others, interval from `sample_rate_ms` in signal config

**Topic count:** 16 for packaging profile (11 coder + 2 env + 3 vibration), matching PRD Appendix C

**Decisions:**
- `CallbackAPIVersion` imported from `paho.mqtt.enums` not re-exported from `paho.mqtt.client` — avoids mypy attr-defined error without type: ignore
- `mqtt_topic: "environment/..."` in YAML was a config bug (PRD says `env/`); fixed in factory.yaml — PRD is canon (CLAUDE.md Rule 4)
- Publish scheduling uses `time.monotonic()` (wall clock), not simulated time — MQTT publish rate is wall-clock based per PRD
- Client injection pattern (`client=None` default) enables unit tests without a real broker

**Test results:** 74/74 unit tests pass. No regressions (1181 total tests pass).
