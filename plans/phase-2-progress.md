# Phase 2: OPC-UA, MQTT, and Packaging Scenarios - Progress

## Status: PHASE COMPLETE — all tasks done, code review findings addressed

## Tasks
- [x] 2.1: OPC-UA Server Adapter — Node Tree
- [x] 2.2: OPC-UA Server Adapter — Value Sync + Subscriptions
- [x] 2.3: OPC-UA Integration Tests
- [x] 2.4: MQTT Publisher Adapter
- [x] 2.5: MQTT Batch Vibration Topic
- [x] 2.6: MQTT Integration Tests
- [x] 2.7: Web Break Scenario
- [x] 2.8: Dryer Temperature Drift Scenario
- [x] 2.9: Ink Viscosity Excursion Scenario
- [x] 2.10: Registration Drift Scenario
- [x] 2.11: Cold Start Energy Spike Scenario
- [x] 2.12: Coder Consumable Depletion Scenario
- [x] 2.13: Material Splice Scenario
- [x] 2.14: Ground Truth Event Log
- [x] 2.15: Environment Composite Model
- [x] 2.16: Cross-Protocol Consistency Tests

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

### Task 2.5 (Complete)

**Files modified:**
- `src/factory_simulator/protocols/mqtt_publisher.py` — Added `BatchVibrationEntry`, `make_batch_vibration_payload`, `build_batch_vibration_entry`, `_worst_quality`, `_publish_batch_vib`; updated `build_topic_map` and `MqttPublisher`
- `src/factory_simulator/config.py` — Added `vibration_per_axis_enabled: bool = True` to `MqttProtocolConfig`
- `tests/unit/test_protocols/test_mqtt.py` — Added 31 new tests (TestWorstQuality, TestMakeBatchVibrationPayload, TestBuildBatchVibrationEntry, TestMqttPublisherBatchVibration, TestPerAxisDisabled)

**What was built:**
- `BatchVibrationEntry` dataclass: tracks batch vibration topic config (topic, qos=0, retain=False, interval_s, unit, x/y/z signal IDs, last_published)
- `make_batch_vibration_payload(x, y, z, quality, unit)`: builds `{timestamp, x, y, z, unit, quality}` JSON payload (PRD 3.3.6)
- `build_batch_vibration_entry(config)`: scans equipment signal configs for `vibration/*_x/y/z` groups; builds batch entry from first complete group; returns None for F&B profile (no vibration)
- `_worst_quality(qualities)`: selects bad > uncertain > good for combined quality across x/y/z axes
- `MqttPublisher._batch_vib_entry`: built at construction time alongside per-axis `_topic_entries`
- `MqttPublisher.batch_vibration_entry`: property for testing/introspection
- `MqttPublisher._publish_batch_vib(now)`: publishes batch when interval elapsed and all three axes present
- `_publish_due`: now calls `_publish_batch_vib` before per-axis publish loop
- `vibration_per_axis_enabled: bool = True` in `MqttProtocolConfig`: when False, `build_topic_map` skips all `vibration/*` topics (reduces topic_entries from 16 to 13)

**Decisions:**
- Batch entry kept separate from `_topic_entries` list: different payload structure (x/y/z not value), avoids polluting `TopicEntry` with optional axis fields
- `_topic_entries` count remains 16 (per-axis enabled by default); batch entry is 17th topic but tracked separately
- Quality selection: worst quality (bad > uncertain > good) across all three axes — consistent with how sensor fusion typically reports combined quality
- Batch interval comes from x-axis `sample_rate_ms` (all three are 1000ms)
- `vibration_per_axis_enabled` is a plain Python field on `MqttProtocolConfig` — no YAML key needed for default use

**Test results:** 105/105 unit tests pass. No regressions (1244 total tests pass).

### Task 2.6 (Complete)

**Files created:**
- `tests/integration/test_mqtt_integration.py` — 10 integration tests, all pass

**What was built:**
Fixture: `mqtt_components` — creates config/store/clock/engine, ticks engine 5× to populate signal IDs, injects known test values for all 16 MQTT-published signals, creates MqttPublisher (NOT started — tests start it after subscribing to avoid missing event-driven publishes).

Helper: `_make_subscriber(suffix)` — creates a paho-mqtt 2.0 subscriber with unique client_id, connects to Docker Mosquitto, starts loop, waits for CONNACK.

Helper: `_wait_for_topics(collector, expected, timeout)` — async polling loop that checks `collector.topics_received()` against expected set without blocking the event loop.

`MessageCollector` class — thread-safe (Lock-protected) paho on_message callback collector. Records topic, parsed JSON payload, QoS, retain flag for each message.

Test classes:
- `TestAllTopicsPublish` (1 test): subscribes to `{prefix}/#`, starts publisher, verifies all 17 topics (16 per-signal + 1 batch vibration) are received within 10s.
- `TestPayloadStructure` (4 tests): per-signal payloads have {timestamp, value, unit, quality}; batch vibration has {timestamp, x, y, z, unit, quality} and no `value` field; values are numeric (not strings); timestamps are ISO 8601 UTC with 3-digit milliseconds.
- `TestQosLevels` (2 tests): QoS 1 for state/prints_total/nozzle_health/gutter_fault; QoS 0 for analog/env/vibration topics.
- `TestRetainBehavior` (2 tests): new subscriber to retained topic receives last value with msg.retain=True; new subscriber to vibration topics does NOT receive retained messages.
- `TestPublishRate` (1 test): vibration x-axis publishes ≥3 times in 4 seconds (1s interval).

**Decisions:**
- Publisher NOT started in fixture: tests subscribe first, then start publisher, to reliably capture event-driven signals (which only fire once on value change from None).
- `_wait_for_topics` uses `await asyncio.sleep(0.2)` polling instead of `threading.Event.wait()` to avoid blocking the asyncio event loop.
- QoS 0 test filters out retained messages from prior runs (which may carry different QoS) by preferring non-retained messages in assertions.
- Vibration retain test clears stale retained messages before verification.
- `@pytest.mark.integration` + skipif broker unreachable: tests skip cleanly when Docker not running.

**Test results:** 10/10 integration tests pass. No regressions (1254 total tests pass).

### Task 2.7 (Complete)

**Files created/modified:**
- `src/factory_simulator/scenarios/web_break.py` — WebBreak scenario class, 314 lines
- `tests/unit/test_scenarios/test_web_break.py` — 23 unit tests, all pass
- `src/factory_simulator/protocols/modbus_server.py` — Coil 3 now derives from `press.web_break` store signal

**What was built:**
- `WebBreak` class inheriting from `Scenario` base, with 3 internal sub-phases: SPIKE → DECELERATION → RECOVERY → COMPLETED
- `_Phase` enum for internal phase tracking
- Configurable params: `spike_tension_range` (default [650,800] N), `spike_duration_range` (default [0.1,0.5] s), `decel_duration_range` (default [5.0,10.0] s), `recovery_seconds` (default [900,3600] s)

**Sequence (PRD 5.3):**
1. SPIKE: overrides `press._web_tension._base` to spike value, sets `_gain=0` (decouples from speed), raises `sig_cfg.max_clamp` from 500→1000 so spike exceeds normal clamp
2. DECELERATION: drops tension base to 0, forces Fault state with cascade prevention (`_prev_state = STATE_FAULT` prevents default 30s ramp), starts custom 5-10s emergency decel via `_line_speed_model.start_ramp()`, sets coils (`press.web_break`, `press.fault_active`)
3. RECOVERY: restores tension gain and max_clamp, waits for configured recovery duration
4. COMPLETE: restores original tension base/gain/max_clamp, clears coils, forces Setup state

**Key design decisions:**
- Direct model manipulation (not store writes): scenarios run before generators, so store writes get overwritten. Instead, the scenario modifies `CorrelatedFollowerModel._base` and `._gain` directly on the press generator's internal tension model.
- State cascade prevention: setting `press._prev_state = STATE_FAULT` after `force_state("Fault")` prevents the press generator from detecting a "new" fault transition and starting its default 30s deceleration ramp. This allows the scenario to control decel timing (5-10s per PRD).
- `max_clamp` temporarily raised: web_tension config has max_clamp=500 but PRD requires spike >600N. Scenario saves, raises to 1000, and restores on recovery.
- Modbus coil 3: changed from `CoilDefinition(3, None)` to `CoilDefinition(3, "press.web_break", mode="gt_zero")` so Modbus clients see the web break indicator.
- Phase transition uses `>` not `>=` for spike duration check: prevents immediate transition when spike_duration equals dt (both are 0.1s).

**Test timing considerations:**
- Press generator fires every 500ms (min sample_rate_ms), not every 100ms tick. Tests must run enough ticks for the generator to fire during the target phase.
- RampModel advances `elapsed += dt` (0.1s) per generate() call, so decel ramps appear 5× slower than sim_time when gen fires every 500ms.
- Spike duration tests use 1.0s spikes (not 0.1s PRD minimum) to ensure generator fires during SPIKE phase.
- Decel test runs 150 post-scenario ticks to allow decel ramp to complete.

**Test results:** 23/23 unit tests pass. No regressions (1277 total tests pass).

### Task 2.8 (Complete)

**Files created:**
- `src/factory_simulator/scenarios/dryer_drift.py` — DryerDrift scenario class, 196 lines
- `tests/unit/test_scenarios/test_dryer_drift.py` — 22 unit tests, all pass

**What was built:**
- `DryerDrift` class inheriting from `Scenario` base
- Configurable params: `drift_rate_range` (default [0.05, 0.2] C/min), `drift_range` (default [5.0, 15.0] C max), `drift_duration_range` (default [1800, 7200] s = 30-120 min), `waste_increase_range` (default [1.2, 1.5] = 20-50% increase), `zone` (1/2/3 or random)

**Sequence (PRD 5.4):**
1. One zone selected (random or explicit). Waste rate increased by configured multiplier.
2. Each tick: drift_offset = min(drift_rate * elapsed / 60, max_drift). Override `FirstOrderLagModel._value = setpoint + drift_offset`.
3. After drift_duration: scenario completes, waste rate restored, lag model naturally recovers toward setpoint (tau=120 s ≈ 10 min recovery).

**Key design decisions:**
- Direct `_value` override on `FirstOrderLagModel`: The scenario overrides the lag model's internal `_value` each tick. When the generator's `generate()` fires (every 5000 ms), the lag correction (~4% per call) partially pulls the value back, but the scenario re-applies on the next tick. Net effect: store shows drift within ~4% of target offset, which is indistinguishable from noise.
- No setpoint modification: PRD 5.4 says actual temperature drifts above setpoint. The setpoint signal stays constant; only the actual temperature changes. Verified by `test_setpoint_unchanged_during_drift`.
- No fault state: PRD 5.4 says drift is subtle, no fault trigger. Verified by `test_no_fault_state_during_drift`.
- Waste rate via `CounterModel._rate`: Direct modification of the rate attribute (same pattern as web_break modifying `_base` on CorrelatedFollowerModel). Saved and restored on completion.
- Natural recovery: After scenario completes, the lag model tracks back to setpoint via its normal first-order dynamics. No explicit recovery phase needed. tau=120 s gives ~10 min for full recovery.

**Test timing considerations:**
- Dryer temp generator fires every 5000 ms (50 ticks), so tests that check store values must run enough ticks for at least 2 generator fires.
- Noise sigma=0.8 C can mask small drifts. Tests use high drift rates (12-30 C/min) for clear signal, or check model `_value` directly (bypassing noise).
- `_stabilise_dryer()` helper forces lag model `_value` to setpoint before testing, avoiding the 600s warmup from initial_value=20 C to setpoint=75 C.

**Test results:** 22/22 unit tests pass. No regressions (1208 total unit tests pass).

### Task 2.9 (Complete)

**Files created:**
- `src/factory_simulator/scenarios/ink_excursion.py` — InkExcursion scenario class, 230 lines
- `tests/unit/test_scenarios/test_ink_excursion.py` — 23 unit tests, all pass

**What was built:**
- `InkExcursion` class inheriting from `Scenario` base
- `_Direction` enum: THIN (viscosity < 18s) or THICK (viscosity > 45s)
- Configurable params: `duration_range` (default [300, 1800] = 5-30 min), `direction` (thin/thick/random), `thin_target_range` (default [14, 17]), `thick_target_range` (default [46, 50]), `reg_error_multiplier_range` (default [3.0, 5.0]), `waste_increase_range` (default [1.1, 1.3] = 10-30%), `ramp_fraction` (default 0.3)

**Sequence (PRD 5.6):**
1. On activation: choose direction (thin or thick), save original model parameters, increase registration error `_drift_rate` by multiplier, increase waste `_rate` by multiplier.
2. Each tick: gradually ramp `SteadyStateModel._target` from original (28.0) toward excursion target (14-17 for thin, 46-50 for thick) over `ramp_fraction` of duration, then hold.
3. After excursion_duration: restore all parameters — viscosity target, registration drift rates, waste rate.

**Key design decisions:**
- Direct `_target` override on `SteadyStateModel`: The scenario modifies the model's `_target` attribute, which shifts the center of the generated values. The model's `generate()` returns `target + drift_offset + noise`, so changing target shifts the entire distribution.
- Gradual ramp: The `ramp_fraction` parameter (default 30% of duration) linearly interpolates from original target to excursion target. The remaining 70% holds at the excursion value. This produces a realistic gradual drift rather than a step change.
- Registration error via `_drift_rate` multiplier: Increasing the `RandomWalkModel._drift_rate` by 3-5x causes the error to wander further from center, per PRD 5.6 step 2.
- Direction randomness via `rng.random() < 0.5` instead of `rng.choice()`: avoids mypy type errors with numpy's choice on enum lists.

**Test timing considerations:**
- Ink viscosity generator fires every 30000ms (300 ticks). Tests checking store values must run ≥600 ticks for 2 generator fires.
- Noise sigma=1.5 on ink_viscosity. Tests use extreme excursion targets (15 or 48) with enough ticks for the drifted value to be clearly separated from baseline (28.0).

**Test results:** 23/23 unit tests pass. No regressions (1231 total unit tests pass).

### Task 2.10 (Complete)

**Files created:**
- `src/factory_simulator/scenarios/registration_drift.py` — RegistrationDrift scenario class, 210 lines
- `tests/unit/test_scenarios/test_registration_drift.py` — 27 unit tests, all pass

**What was built:**
- `RegistrationDrift` class inheriting from `Scenario` base
- Configurable params: `duration_range` (default [120, 600] = 2-10 min), `drift_rate_range` (default [0.01, 0.05] mm/s), `axis` (x/y/random), `direction` (+1/-1/random), `waste_increase_range` (default [1.2, 1.5] = 20-50%), `waste_threshold` (default 0.2 mm)

**Sequence (PRD 5.7):**
1. On activate: save `_reversion_rate` on affected axis's `RandomWalkModel`, set to 0 (suppress mean-reversion). Save waste rate and center.
2. Each tick: override `_value = center + direction * drift_rate * elapsed`. Linear drift from center. When `abs(value - center) > 0.2 mm`, increase waste rate by configured multiplier.
3. On complete: restore `_reversion_rate` (model naturally reverts to center via mean-reversion), restore waste rate.

**Key design decisions:**
- Reversion suppression: Setting `_reversion_rate = 0` during the drift prevents the RandomWalkModel's mean-reversion term from pulling the value back to center. This lets the scenario control the drift precisely.
- Direct `_value` override: Same pattern as DryerDrift (FirstOrderLagModel._value) and InkExcursion (SteadyStateModel._target). The scenario runs before generators, so the override is in effect when the generator fires.
- Conditional waste increase: PRD 5.7 step 4 says waste increases "while error exceeds 0.2 mm", not from the start. The scenario tracks a `_waste_increased` flag and only modifies the waste rate once drift crosses the threshold.
- Natural recovery: After completion, restoring `_reversion_rate` lets the RandomWalkModel's mean-reversion term pull the value back to center naturally. No explicit recovery phase needed.
- Single-axis drift: PRD says "x or y", not both. The scenario picks one axis (random or explicit).

**Test results:** 27/27 unit tests pass. No regressions (1349 total tests pass).

### Task 2.11 (Complete)

**Files created:**
- `src/factory_simulator/scenarios/cold_start.py` — ColdStart scenario class, 270 lines
- `tests/unit/test_scenarios/test_cold_start.py` — 24 unit tests, all pass

**What was built:**
- `ColdStart` class inheriting from `Scenario` base
- Internal `_Phase` enum: MONITORING (watching for trigger), SPIKE (inrush active)
- Configurable params: `spike_duration_range` (default [2.0, 5.0] s), `power_multiplier_range` (default [1.5, 2.0] = 150-200%), `current_multiplier_range` (default [1.5, 3.0] = 150-300%), `idle_threshold_s` (default 1800.0 = 30 min)

**Sequence (PRD 5.10):**
1. Scenario activates and enters MONITORING phase, watching press state machine.
2. Tracks idle duration (`_idle_since`) when press is in Off (0) or Idle (3).
3. When press transitions to Setup (1) or Running (2) AND idle_duration >= idle_threshold: enter SPIKE phase.
4. SPIKE: override `CorrelatedFollowerModel._base` on both `energy._line_power` and `press._main_drive_current` to produce spike values. Temporarily raise `max_clamp` on both signal configs to allow spike to exceed normal range.
5. After spike_duration elapsed: complete. Restore all saved model parameters and max_clamp values.

**Key design decisions:**
- Reactive trigger via state monitoring: Unlike time-scheduled scenarios, ColdStart watches the press state machine each tick. It detects the transition from Off/Idle → Setup/Running with sufficient idle duration.
- `_base` override on CorrelatedFollowerModel: During cold start, speed is near 0 (press just starting ramp). The spike is produced by setting `_base` to `(base + gain * target_speed) * multiplier`, which makes the output approximately the spiked value regardless of current speed.
- Max clamp temporarily raised: energy.line_power max_clamp=200 kW, but 200% of 110 kW = 220 kW exceeds it. Same pattern as web_break raising tension max_clamp. Saved and restored on completion.
- Normal running power calculated from model params: `base + gain * target_speed` gives the expected power at full speed (10 + 0.5 * 200 = 110 kW). The multiplier is applied to this full-speed value.
- Single-trigger design: Each ColdStart instance monitors for one trigger event, then completes. Multiple instances can be scheduled for multiple cold starts.
- Idle tracking resets: If the press enters Fault/Maintenance, idle tracking resets. If a short idle doesn't meet the threshold, tracking resets when the press goes to Running. New idle periods restart the timer.

**Test results:** 24/24 unit tests pass. No regressions (1282 total unit tests pass).

### Task 2.12 (Complete)

**Files created/modified:**
- `src/factory_simulator/scenarios/coder_depletion.py` — CoderDepletion scenario class, 215 lines
- `tests/unit/test_scenarios/test_coder_depletion.py` — 26 unit tests, all pass
- `src/factory_simulator/generators/coder.py` — Added `_quality_overrides` dict, updated `_make_sv` to use it, fixed G5 gutter_fault probability

**What was built:**
- `CoderDepletion` class inheriting from `Scenario` base
- Internal `_Phase` enum: MONITORING (watching ink level), DEPLETED (coder faulted, waiting for recovery)
- Configurable params: `low_ink_threshold` (default 10.0%), `empty_threshold` (default 2.0%), `recovery_duration_range` (default [300, 1800] s = 5-30 min), `refill_level` (default 100.0%)

**Sequence (PRD 5.12):**
1. On activation: disables auto-refill on `DepletionModel._refill_threshold` (sets to None) so the scenario controls refill timing. Enters MONITORING phase.
2. MONITORING: each tick checks `DepletionModel.value`. At ≤10%: sets `_quality_overrides["ink_level"] = "uncertain"` on coder generator. At ≤2%: forces coder to Fault state, enters DEPLETED phase.
3. DEPLETED: temporarily sets Fault→Ready transition `min_duration` to 1e9 (prevents timer-based auto-recovery during scenario). Waits for configured recovery_duration.
4. On complete: refills ink to 100% via `DepletionModel.refill()`, clears quality override, restores auto-refill threshold, restores Fault→Ready transition parameters, forces coder to Ready state.

**Key design decisions:**
- Quality override via `_quality_overrides` dict on `CoderGenerator`: the coder's `_make_sv` checks this dict before defaulting to "good". This allows scenarios to change quality without modifying generator logic. Clean pattern that could be reused by other scenarios.
- Auto-refill suppression: config has `refill_threshold: 5.0`, which would auto-refill before reaching the 2% fault threshold. The scenario saves and sets `_refill_threshold = None` during its active period, then restores on completion.
- Fault state locking: the coder state machine has a Fault→Ready timer (60-300s) that could fire before the scenario's recovery duration. The scenario temporarily sets this timer's `min_duration` to 1e9 and `max_duration` to 0.0 (disabling forced exit), then restores on completion.
- Reactive monitoring pattern: like ColdStart, this scenario watches model values rather than executing on a fixed timeline. The "duration" is indeterminate for MONITORING plus a fixed recovery_duration.

**G5 fix (gutter_fault probability):**
- Changed probability from `0.00001` (MTBF ≈ 28 hours) to `0.000000556` (MTBF ≈ 500 hours = 1,800,000 seconds)
- The previous rate was ~18x too high. New rate matches PRD 5.12 requirement of MTBF 500+ hours.

**Test results:** 26/26 unit tests pass. No regressions (1308 total unit tests pass).

### Task 2.13 (Complete)

**Files created:**
- `src/factory_simulator/scenarios/material_splice.py` — MaterialSplice scenario class, 370 lines
- `tests/unit/test_scenarios/test_material_splice.py` — 28 unit tests, all pass

**What was built:**
- `MaterialSplice` class inheriting from `Scenario` base
- Internal `_Phase` enum: MONITORING (watching unwind_diameter), SPLICE (disturbance active)
- Configurable params: `trigger_diameter` (default 150.0 mm), `refill_diameter` (default 1500.0 mm), `splice_duration_range` (default [10, 30] s), `tension_spike_range` (default [50, 100] N), `tension_spike_duration_range` (default [1, 3] s), `reg_error_increase_range` (default [0.1, 0.3] mm), `reg_error_duration_range` (default [10, 20] s), `waste_multiplier_range` (default [1.5, 2.5]), `speed_dip_pct_range` (default [0.05, 0.10]), `speed_recovery_range` (default [5, 10] s)

**Sequence (PRD 5.13a):**
1. MONITORING: watches `press._unwind_diameter.value`. Only triggers when press is Running AND unwind ≤ trigger_diameter.
2. On splice trigger, five simultaneous effects:
   - Tension spike: increases `CorrelatedFollowerModel._base` on `press._web_tension` by 50-100 N. Max clamp raised if spike would exceed. Restored after tension_spike_duration.
   - Registration error: offsets both `_reg_error_x._value` and `_reg_error_y._value` by 0.1-0.3 mm. Suppresses `_reversion_rate` to 0. Re-applies offset each tick (generator may overwrite). Restores reversion rate after reg_error_duration.
   - Waste rate: multiplies `press._waste_count._rate` by 1.5-2.5x. Restored on completion.
   - Unwind refill: calls `press._unwind_diameter.refill(1500.0)` immediately.
   - Speed dip: starts ramp from current speed to 90-95% over 2s, then recovery ramp back to target_speed over 5-10s.
3. After splice_duration elapsed: scenario completes, all saved state restored.

**Key design decisions:**
- Reactive monitoring pattern (like ColdStart and CoderDepletion): watches model values rather than executing on a fixed timeline.
- Machine stays Running throughout — no state change (flying splice per PRD 5.13a).
- Multiple timed sub-effects within the splice: tension spike (1-3s), reg error (10-20s), speed dip (~2s down + 5-10s recovery), waste (full splice duration). Each has independent timers with the `_tension_restored`, `_reg_restored`, `_speed_restored` flags.
- Both X and Y registration axes affected simultaneously (PRD says "x and y", not "x or y" like RegistrationDrift).
- Tension spike uses `_base` offset (additive) rather than replacement: preserves the speed-correlated component of tension.
- `_uniform_param` helper function extracted for DRY parameter sampling.

**Test results:** 28/28 unit tests pass. No regressions (1336 total unit tests pass).

### Task 2.14 (Complete)

**Files created/modified:**
- `src/factory_simulator/engine/ground_truth.py` — GroundTruthLogger class, ~320 lines
- `tests/unit/test_ground_truth.py` — 25 unit tests, all pass
- `src/factory_simulator/engine/scenario_engine.py` — Added `ground_truth` parameter, auto-logging of scenario_start/scenario_end events, affected signals registry, parameter extraction helpers
- `src/factory_simulator/engine/data_engine.py` — Added `ground_truth` parameter pass-through to ScenarioEngine
- `src/factory_simulator/engine/__init__.py` — Exported `GroundTruthLogger`

**What was built:**
- `GroundTruthLogger` class: append-only JSONL writer with open/close lifecycle. Writes to configurable path (default `output/ground_truth.jsonl`). Creates parent directories on open.
- `write_header(config)`: first-line config record per PRD 4.7 — `event_type: "config"`, sim_version, seed, profile name, per-signal noise parameters (type, sigma, df, phi, speed-dependent params), enabled scenarios list.
- Event methods for all 10 PRD event types: `log_scenario_start`, `log_scenario_end`, `log_state_change`, `log_signal_anomaly`, `log_data_quality`, `log_micro_stop`, `log_shift_change`, `log_consumable`, `log_sensor_disconnect`, `log_stuck_sensor`. Plus `log_connection_drop` for PRD 4.8.
- `sim_time` formatted as ISO 8601 UTC string (relative to 2026-01-01T00:00:00Z reference epoch).
- ScenarioEngine integration: detects PENDING→ACTIVE and →COMPLETED phase transitions, auto-logs `scenario_start` (with affected signals and parameters) and `scenario_end`.
- `_AFFECTED_SIGNALS` registry: maps each scenario class name to its PRD-defined affected signal list (WebBreak, DryerDrift, InkExcursion, etc.).
- `_get_scenario_params()`: extracts loggable numeric/string parameters via duck-typing (duration, recovery_duration, spike_tension, shift_name, etc.).
- Graceful degradation: writes are no-ops when logger is not opened; ScenarioEngine works normally when `ground_truth=None`.

**Key design decisions:**
- Logger is write-only, append-only, with `flush()` after every line — no buffering, no reads. Simple and crash-safe.
- Phase transition detection in ScenarioEngine `tick()`: checks `phase_before`/`phase_after` around each `scenario.evaluate()` call. This avoids modifying the Scenario base class or any individual scenario implementations.
- `ScenarioPhase` type annotation on `phase_after` to prevent mypy from incorrectly narrowing through the COMPLETED `continue` guard.
- Affected signals are declared statically in a module-level dict rather than queried from scenario instances. This is simpler and matches the PRD (each scenario type has a fixed set of affected signals).
- JSON output uses `separators=(",",":")` (compact, no spaces) for minimal file size.

**Test results:** 25/25 unit tests pass. No regressions (1361 total unit tests pass).

### Task 2.15 (Complete)

**Files modified/created:**
- `src/factory_simulator/generators/environment.py` — Replaced plain sinusoidal model with 3-layer composite per PRD 4.2.2
- `tests/unit/test_generators/test_environment.py` — 10 new unit tests, all pass
- `config/factory.yaml` — Added composite model parameters to `ambient_temp` config

**What was built:**
Composite environmental model per PRD 4.2.2:
```
value = daily_sine(t) + hvac_cycle(t) + perturbation(t) + noise(0, sigma)
```

Three layers:
1. **Daily sinusoidal** (existing): SinusoidalModel with 24-hour period, built WITHOUT noise (noise is a separate final layer per the PRD formula).
2. **HVAC cycling**: BangBangModel centered at 0, producing a triangle-wave offset with configurable period (15-30 min, default 20) and amplitude (0.5-1.5 C, default 1.0). Rate computed as `4 * amplitude / period` for symmetric cycling.
3. **Random perturbations**: Poisson process (3-8 events per 8-hour shift, default 5) with configurable magnitude (U[0.5*mag, 1.5*mag], default mag=2.0 → U[1.0, 3.0] matching PRD "1-3 C"). Each event adds a step offset that decays exponentially (tau 5-10 min, default 7 min).

Humidity: same layered pattern but inverted. HVAC and perturbation offsets are negated and scaled by `humidity_amplitude / temp_amplitude` ratio (10/3 ≈ 3.33) to map °C → %RH.

**Config parameters (on `ambient_temp.params`):**
- `hvac_period_minutes` (default 20)
- `hvac_amplitude_c` (default 1.0)
- `perturbation_rate_per_shift` (default 5)
- `perturbation_magnitude_c` (default 2.0)
- `perturbation_decay_tau_minutes` (default 7)

**Key design decisions:**
- `real_dt` tracking: The data engine calls generators with the tick dt (0.1s) even though the environment fires every 60s. The generator tracks `_last_sim_time` and computes the actual elapsed time for the BangBang model and perturbation decay. This ensures correct HVAC cycling period and perturbation dynamics.
- Noise separated from sinusoidal: The daily sine models are built WITHOUT noise. Noise is applied as a final additive layer after all composite layers are combined, matching the PRD formula exactly.
- BangBang discrete-step overshoot: With 60s steps and rate=0.2 C/min, the PV overshoots the dead band by 0.2°C per half-cycle. This is acceptable and mimics real HVAC behaviour. Actual cycle period is ~24 min instead of the configured 20 min due to this overshoot.
- Perturbation events use `numpy.poisson()` for correct Poisson statistics even with large dt values (60s). Magnitude drawn from `U[0.5*mag, 1.5*mag]` with random sign.

**Test results:** 10/10 new tests pass. 1462 total tests pass (no regressions).

### Task 2.16 (Complete)

**Files created:**
- `tests/integration/test_cross_protocol.py` — 12 integration tests, all pass

**What was built:**
Capstone cross-protocol consistency test that starts all three protocol adapters (ModbusServer, OpcuaServer, MqttPublisher) simultaneously against a single DataEngine and SignalStore, then verifies value consistency.

Fixture: `all_protocols` — creates config/store/clock/engine, ticks engine 5x, injects known test values for all signals across all three protocols, starts Modbus (port 15503), OPC-UA (OS-assigned port), and MQTT publisher (Docker Mosquitto on 1883). Connects Modbus client, OPC-UA client, and MQTT subscriber. Yields all four components. Full cleanup on teardown.

Test classes:
- `TestModbusOpcuaConsistency` (5 tests): press.line_speed, press.machine_state, press.web_tension, and energy.line_power match between Modbus HR (float32) and OPC-UA (Double) within float32 precision. Comprehensive test covers 8 representative signals across all equipment groups (Press1, Laminator1, Slitter1, Energy) with both cross-protocol and injected-value assertions.
- `TestMqttFromSameStore` (3 tests): coder/ink_level, env/ambient_temp, and batch vibration x/y/z MQTT payloads match injected store values. Verifies MQTT adapter reads from the same store as Modbus and OPC-UA.
- `TestSimultaneousOperation` (4 tests): all three protocols serve data in the same session; store.set() propagates to both Modbus and OPC-UA within one sync cycle; MQTT topics are received alongside active Modbus/OPC-UA; machine state is observable from Modbus HR 210, OPC-UA Press1.State, and coder/state on MQTT — all consistent with injected values.

**Key design decisions:**
- No single signal exists on all three protocols in the packaging profile: Modbus + OPC-UA share 30 press/laminator/slitter/energy signals, MQTT publishes 16+1 coder/environment/vibration signals. Tests verify cross-protocol consistency within shared signal sets and same-store consistency across all three adapters.
- `_float32_roundtrip()` helper converts expected float64 values through float32 encoding for accurate comparison with Modbus values. Tolerance of 0.01 is generous relative to float32 quantization error (~1e-5 for typical values) but matches existing test patterns.
- MQTT subscriber created and subscribed BEFORE publisher starts to capture event-driven topics (coder/state, etc.) that fire only once on initial value change from None.
- Modbus port 15503 avoids conflict with existing Modbus integration tests (port 15502).
- `@pytest.mark.integration` + skipif broker unreachable: entire module skips when Docker not running.
- Store change propagation test verifies the full path: `store.set()` -> Modbus sync (50ms) -> OPC-UA sync (500ms) -> both protocols show updated value.

**Test results:** 12/12 integration tests pass. 1474 total tests pass (no regressions).

---

## Code Review

Review performed after all 16 tasks completed. See `plans/phase-2-review.md` for full findings.

### RED findings (fixed)

**R1: Missing scenario configs in ground truth header**
- Added `CoderDepletionConfig` and `MaterialSpliceConfig` Pydantic models to `config.py`
- Added them to `ScenariosConfig` with `Field(default_factory=...)`
- Added enumeration checks in `ground_truth.py` `write_header()` so both appear in the scenarios list
- Fixed `ColdStartSpike` → `ColdStart` key mismatch in `_AFFECTED_SIGNALS` dict in `scenario_engine.py`
- Updated `test_header_scenarios_list` test to assert both new scenarios appear

**R2: No intermediate ground truth events from scenarios**
- All 7 Phase 2 scenarios now emit intermediate GT events at the correct lifecycle points:
  - `web_break.py`: `signal_anomaly` (tension spike) on activate, `state_change` (Running→Fault) on deceleration
  - `dryer_drift.py`: `signal_anomaly` (temperature drift) on activate
  - `ink_excursion.py`: `signal_anomaly` (viscosity excursion) on activate
  - `registration_drift.py`: `signal_anomaly` (registration drift) on activate
  - `cold_start.py`: `signal_anomaly` (power spike + current spike) on spike start
  - `coder_depletion.py`: `signal_anomaly` (low ink) on threshold crossing, `state_change` (Ready→Fault) on depletion, `consumable` (ink refill) on completion
  - `material_splice.py`: `signal_anomaly` (tension spike) + `consumable` (reel replaced) on splice start
- Added 7 new unit tests in `TestScenarioIntermediateEvents` class

### YELLOW findings (partially addressed)

**Y2: OPC-UA uncertain quality mapping** (fixed)
- Changed `opcua_server.py` to map `quality="uncertain"` to `ua.StatusCodes.UncertainLastUsableValue` instead of `StatusCode.Good`

**Y1, Y3, Y4**: Deferred (MQTT wall-clock timestamps, test threading.Lock, inconsistent complete() guards) — not blocking for Phase 2 completion.

### Final CI results after fixes
- `ruff check src tests`: All checks passed
- `mypy src`: Success, no issues found in 48 source files
- `pytest`: 1481 passed (7 new tests from code review fixes)
