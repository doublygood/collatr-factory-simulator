# Phase 4: Full Scenario System and Data Quality — Progress

## Status: In Progress

## Tasks
- [x] 4.1: Poisson Scheduling Engine
- [x] 4.2: Scenario Priority and Conflict Resolution
- [x] 4.3: Phase 4 Config Models
- [x] 4.4: Motor Bearing Wear Scenario
- [x] 4.5: Micro-Stops Scenario
- [x] 4.6: Contextual Anomalies Scenario
- [x] 4.7: Intermittent Faults Scenario
- [x] 4.8: Communication Drop Injection
- [x] 4.9: Sensor Disconnect and Stuck Sensor
- [x] 4.10: Modbus Exception and Partial Response Injection
- [x] 4.11: Duplicate Timestamps and Timezone Offset
- [x] 4.12: Data Quality Engine Integration
- [ ] 4.13: Noise Calibration — Packaging Profile
- [ ] 4.14: Noise Calibration — F&B Profile
- [ ] 4.15: Counter Rollover Testing Support
- [ ] 4.16: Reproducibility Test and Final Integration

## Carried Forward Items
- Y1 (Phase 2): `_spawn_rng` uses `integers()` not `SeedSequence.spawn()` → Fix in Task 4.1
- Y3 (Phase 2.1): DataEngine doesn't pass `sim_duration_s` to ScenarioEngine → Fix in Task 4.1
- gutter_fault probability 18x too high → Fix in Task 4.13

## Notes

### Task 4.12 — Data Quality Engine Integration (COMPLETE)

New class `DataQualityInjector` added to `src/factory_simulator/engine/data_quality.py`.

**`DataQualityInjector`**:
- Wraps `SensorDisconnectInjector` and `StuckSensorInjector` under a single `tick()` entry point
- Sub-injectors created only when their respective config sections are enabled (`cfg.sensor_disconnect.enabled`, `cfg.stuck_sensor.enabled`), providing global and per-section control
- `tick(sim_time, store, ground_truth)` calls both sub-injectors in order (disconnect then stuck); no-op when both are disabled
- Constructor signature: `(cfg: DataQualityConfig, signal_ids: list[str], disconnect_rng: np.random.Generator, stuck_rng: np.random.Generator)`

**`DataEngine` changes** (`data_engine.py`):
- Import added: `from factory_simulator.engine.data_quality import DataQualityInjector`
- `__init__`: after scenario engine construction, collects all signal IDs from enabled generators, spawns two child RNGs (`disconnect_rng`, `stuck_rng`) from `_root_ss`, creates `DataQualityInjector` as `self._data_quality`
- `tick()`: calls `self._data_quality.tick(sim_time, self._store, self._ground_truth)` AFTER `scenario_engine.post_gen_tick()` (PRD 8.2 ordering preserved)
- New `data_quality` property exposes the injector for testing/introspection

Tests: 19 tests in `tests/unit/test_data_quality_injector.py` covering:
- Construction: both/disconnect-only/stuck-only/neither sub-injectors
- `tick()` with both disabled: store not modified
- Disconnect tick: sentinel written, quality=bad after event fires
- Disconnect: restores to good after duration expires
- Stuck tick: value frozen with quality=good
- Determinism: same seed → identical schedule (same-seed test)
- Determinism: different seeds → different first-event time
- DataEngine: `data_quality` property returns `DataQualityInjector`
- DataEngine: sub-injectors absent when disabled
- DataEngine: sub-injectors created when enabled
- DataEngine: tick runs without error with injectors active
- DataEngine: signal count unchanged by injector wiring
- DataEngine: store populated correctly after tick
- DataEngine: full packaging config smoke test

Note: `tests/integration/test_modbus_fnb_integration.py::TestFnbEquipmentHR::test_all_fnb_hr_entries_readable` exhibited one intermittent failure in the full suite run due to pre-existing random Modbus exception injection (task 4.10, probability 0.001). The test passed on immediate re-run. Not caused by task 4.12 changes (no Modbus server code was modified).

2361 total tests passing.

### Task 4.11 — Duplicate Timestamps and Timezone Offset (COMPLETE)

**`mqtt_publisher.py`** changes:
- `_sim_time_to_iso(sim_time, offset_hours=0.0)`: adds `offset_hours` param — shifts the
  effective timestamp by `offset_hours * 3600` seconds. The string still ends in 'Z'
  (appears UTC) but the wall-clock value is shifted, replicating camera/PLC timezone bugs
  (PRD 10.7).
- `make_payload(..., offset_hours=0.0)`: threads `offset_hours` to `_sim_time_to_iso`.
  Backward-compatible default of 0.0.
- `make_batch_vibration_payload(..., offset_hours=0.0)`: same.
- `MqttPublisher.__init__`: new `duplicate_rng` kwarg. Stores:
  - `_dup_rng`: the RNG (or None if injection disabled)
  - `_dup_prob = duplicate_probability / 2.0` (MQTT rate is half Modbus: ~0.005% default)
  - `_offset_hours = config.data_quality.mqtt_timestamp_offset_hours`
- `_publish_entry`: applies `_offset_hours` to payload; after each publish, draws from
  `_dup_rng` and publishes the same payload again if `< _dup_prob` (PRD 10.5).
- `_publish_batch_vib`: applies `_offset_hours` to batch payload.

**`modbus_server.py`** changes:
- `ModbusServer.__init__`: new `duplicate_rng` kwarg. Stores `_dup_rng` and
  `_dup_prob = config.data_quality.duplicate_probability`.
- `_update_loop`: adds `is_dup` check — if `_dup_rng.random() < _dup_prob`, skips
  `sync_registers()`. Registers hold their previous values so the next Modbus read returns
  identical data to the previous read (same value, same effective internal timestamp).
  Neither comm drop nor duplicate skip interact (both independently suppress sync).

Tests: 30 tests in `test_duplicate_timestamps.py` covering:
- `_sim_time_to_iso` offset (8 tests): zero offset, +1h BST, -5h US Eastern, Z-suffix,
  fractional seconds
- `make_payload` offset propagation (4 tests)
- `make_batch_vibration_payload` offset propagation (3 tests)
- `MqttPublisher` timezone (4 tests): config propagation, publish payload, batch payload
- `MqttPublisher` duplicate (6 tests): no-rng guard, prob=1 always duplicates, prob=0 never,
  same topic+payload, _dup_prob halving, determinism
- `ModbusServer` duplicate (5 tests): no-rng always syncs, prob=1 always skips, prob=0 never,
  config storage, determinism

2343 total tests passing.

### Task 4.10 — Modbus Exception and Partial Response Injection (COMPLETE)

New class `ModbusExceptionInjector` in `modbus_server.py` implements PRD 10.6 and 10.11:

**Exception injection (PRD 10.6)**:
- `check_exception_0x04()`: random draw at `config.data_quality.exception_probability`
  (default 0.001). Returns `ExcCodes.DEVICE_FAILURE` when triggered.
- `check_exception_0x06(transition_active)`: deterministic — fires during machine state
  transitions. `ModbusServer` tracks `press.machine_state` changes in `sync_registers()`
  and sets `_transition_ts`; a 0.5s window thereafter marks the transition active.
  Returns `ExcCodes.DEVICE_BUSY` when transition is active.
- Priority: 0x06 is checked before 0x04 (transition preempts random failure).

**Partial response injection (PRD 10.11)**:
- `check_partial(count)`: draws at `partial_modbus_response.probability` (default 0.0001).
  Single-register reads (count < 2) are never partial. For multi-register reads, truncated
  count N drawn uniformly from 1 to count-1 via `rng.integers(1, count)`.
- Returns N from `super().getValues(fc, address, N)` — pymodbus naturally encodes the
  shorter register list with correct byte count in the response PDU.
- Partial events stored in `injector.partial_events` list for ground truth plumbing.
- `record_partial(controller_id, address, requested, returned)` adds event to list.

**`FactoryDeviceContext` changes**:
- New parameters: `exception_injector`, `transition_active_fn`, `unit_id`.
- `getValues()`: enforces register limit first, then checks 0x06, 0x04, partial (in order).
- Only applied to FC03/FC04 reads; FC01/FC02 coil reads and writes are unaffected.

**`ModbusServer` changes**:
- New parameter: `exception_rng` (independent RNG from comm drop).
- Creates `ModbusExceptionInjector` in `__init__`.
- `_check_machine_state_transition()`: reads `press.machine_state` from store; on change,
  sets `_transition_ts = time.monotonic()`. Window = 0.5s.
- `sync_registers()` calls `_check_machine_state_transition()` first.
- `exception_injector` property exposed for testing.
- `_is_transition_active()` lambda passed to `FactoryDeviceContext`.

**`GroundTruthLogger`**:
- Added `log_partial_modbus_response(sim_time, controller_id, start_address, requested_count,
  returned_count)` — event type `partial_modbus_response` per PRD 10.11 spec.

Tests: 38 tests in `test_modbus_exceptions.py` covering all injection modes, priority order,
partial count range, state transition detection, determinism, and GT logging.
2313 total tests passing.

### Task 4.9 — Sensor Disconnect and Stuck Sensor (COMPLETE)

New file: `src/factory_simulator/engine/data_quality.py`.

Two classes implement PRD 10.9 and 10.10:

**`SensorDisconnectInjector`** (PRD 10.9):
- Poisson inter-arrival scheduling per signal using `frequency_per_24h_per_signal`
- Sentinel value resolution priority: `per_signal_overrides` → name-based type detection
  (`"temp"` → 6553.5, `"pressure"` → 0.0, `"voltage"` → -32768.0) → 0.0 default
- During active disconnect: `store.set(sig_id, sentinel, sim_time, "bad")`
- OPC-UA reads `quality="bad"` → `BadSensorFailure` (via existing OpcuaServer mapping)
- MQTT publishes `quality` field from store (existing MqttPublisher behaviour)
- Ground truth: `log_sensor_disconnect()` called at event start (not each tick)
- State machine per signal: `_next_event`, `_event_ends` dicts
- Initialized lazily on first `tick()` call; first event always starts after a gap

**`StuckSensorInjector`** (PRD 10.10):
- Poisson inter-arrival scheduling per signal using `frequency_per_week_per_signal`
- At event start: captures `store.get(sig_id).value` as frozen value
- During stuck: `store.set(sig_id, frozen_value, sim_time, "good")` — quality stays Good
- Deferred start if signal absent from store (rescheduled from current sim_time)
- String signals: GT log receives `frozen_value=0.0` (numeric fallback)
- Ground truth: `log_stuck_sensor()` called at event start with frozen_value and duration

Helper: `_sentinel_for_signal(sig_id, cfg)` resolves sentinel value (module-level,
exported for testing).

Both injectors use simulation time (not wall-clock) — sensor events are tied to the
simulated factory timeline, not the host machine. Deterministic for same-seed RNG.

Tests: 39 tests in `tests/unit/test_sensor_disconnect.py` covering sentinel resolution
(8 cases), disabled/zero-frequency guards, sentinel value written, quality flag, active
duration, resumption after event, multiple independent signals, ground truth logging
(called-once, not-called, per-event), determinism, deferred start, string signal GT
handling. 2275 total tests passing.

### Task 4.8 — Communication Drop Injection (COMPLETE)

New file: `src/factory_simulator/protocols/comm_drop.py`.

`CommDropScheduler` implements PRD 10.2 with:
- Poisson inter-arrival times via `rng.exponential(mean_interval_s)`
- Duration drawn uniformly from `cfg.duration_seconds` range
- Wall-clock time (`time.monotonic()`) used for scheduling (drops are network
  events, not simulation-time events)
- `update(t)` / `is_active(t)` state machine — idempotent, no locks needed
- Disabled config → `next_drop_at = inf` (never fires)

Protocol adapter changes (all optional `comm_drop_rng` parameter):
- **ModbusServer**: `_update_loop` skips `sync_registers()` during drop;
  register values freeze at last-synced state
- **OpcuaServer**: `_freeze_all_nodes()` writes `UncertainLastUsableValue` to
  all nodes when a drop starts; `_update_loop` skips `_sync_values()` during
  drop; values return to Good on normal sync after drop ends
- **MqttPublisher**: `_publish_loop` skips `_publish_due()` during drop

Each adapter exposes `comm_drop_active: bool` property and references its
own protocol-specific config: `modbus_drop`, `opcua_stale`, `mqtt_drop`.

Tests: 23 tests in `test_comm_drop.py` covering scheduler disabled/enabled
states, drop activation/deactivation, multi-drop sequences, same-seed
determinism, and protocol-level freeze/suppress behaviour for all three
adapters. 2236 total tests passing.

### Task 4.7 — Intermittent Faults Scenario (COMPLETE)

New file: `src/factory_simulator/scenarios/intermittent_fault.py`.

`IntermittentFault` implements PRD 5.17 with:
- `priority = "background"` (never preempted, never deferred)
- Three-phase model: Phase 1 (sporadic) → Phase 2 (frequent) → Phase 3 (permanent, optional)
- Four subtypes with subtype-specific effects:
  - `bearing`: modifies `VibrationGenerator._models[axis]._target` during each spike
  - `electrical`: modifies `PressGenerator._main_drive_current._base` during each spike
  - `sensor`: writes sentinel value (6553.5 for temp, 0.0 for pressure) via `post_gen_inject` hook
  - `pneumatic`: sets `CoderGenerator._ink_pressure._target = 0` during each spike (no Phase 3)
- Pre-generated `_spike_queue: list[tuple[float, float]]` at construction for reproducibility
- Poisson inter-arrival spike scheduling per phase via `rng.exponential(mean_interval_s)`
- Phase transitions triggered by `_elapsed` crossing `_phase1_duration_s` and `_total_duration_s`
- `_phase3_active` flag: scenario stays ACTIVE forever, spike remains applied permanently
- Ground truth: `log_intermittent_fault()` called at each spike start and phase transition

`scenario_engine.py` changes:
- Import added (alphabetical between FillWeightDrift and InkExcursion)
- `_schedule_intermittent_faults()`: 4 explicit per-subtype blocks (avoids mypy generic-object
  type errors from a loop); each subtype checked for `enabled` and `start_after_hours < sim_duration_s`
- `_generate_timeline()` calls `_schedule_intermittent_faults()` after `_schedule_contextual_anomalies()`
- `_AFFECTED_SIGNALS["IntermittentFault"]` entry added

`ground_truth.py`: Added `log_intermittent_fault()` with fields: subtype, phase, affected_signals,
magnitude, duration, permanent, and optional note (used for phase transition labels).

11 test `_make_engine()` helpers updated to disable `intermittent_fault`.

Tests: 33 tests in `test_intermittent_fault.py` covering priority, durations, spike queue,
all 4 subtypes (bearing/electrical/sensor/pneumatic), phase transitions, Phase 3 permanence,
spike count, ground truth JSONL output, and auto-scheduling. 2213 total tests passing.

### Task 4.6 — Contextual Anomalies Scenario (COMPLETE)

New file: `src/factory_simulator/scenarios/contextual_anomaly.py`.

`ContextualAnomaly` implements PRD 5.16 with:
- `priority = "non_state_changing"` (deferred if a state_changing scenario is active)
- Five anomaly types in `_TYPE_META`: `heater_stuck` (coder.printhead_temp 40-42°C during
  coder Off/Standby), `pressure_bleed` (coder.ink_pressure 800-850 mbar during coder Off),
  `counter_false_trigger` (press.impression_count increments during press Idle),
  `hot_during_maintenance` (press.dryer_temp_zone_1 at 100°C during Maintenance),
  `vibration_during_off` (vibration.main_drive_x 3-5 mm/s during press Off)
- Type selected at construction via probability-weighted categorical draw (cumsum + uniform)
- Duration and injected value drawn at construction for reproducibility
- State machine: PENDING → ACTIVE (waiting) → ACTIVE (injecting) → COMPLETED
- Timeout at 2× duration if target state never arrives
- Early termination if machine state leaves target state during injection
- `post_gen_inject()` hook overwrites store AFTER generators run (PRD 8.2 ordering)

Infrastructure added:
- `base.py`: `post_gen_inject(sim_time, dt, store)` no-op hook on Scenario base class
- `scenario_engine.py`: `post_gen_tick()` iterates active scenarios; `_schedule_contextual_anomalies()`
  uses Poisson scheduling (2-5 events/week = rate from `events_per_week_range`); sorted import
- `data_engine.py`: `scenario_engine.post_gen_tick(sim_time, dt, store)` called after generator loop
- `ground_truth.py`: `log_contextual_anomaly()` logs event with anomaly_type, signal,
  injected_value, expected_state, actual_state

Tests: 18 tests in `test_contextual_anomaly.py` covering priority, type selection (forced + all
5 types from 50 seeds), lifecycle (pending/waiting/injecting/complete), timeout, early termination,
injection values for all 5 types, ground truth JSONL output, and auto-scheduling. 2180 total tests
passing.

### Task 4.5 — Micro-Stops Scenario (COMPLETE)

New file: `src/factory_simulator/scenarios/micro_stop.py`.

`MicroStop` implements PRD 5.15 with:
- `priority = "micro"` (activates without checks, never preempted, never deferred)
- Three sub-phases tracked via `_elapsed`: RAMP_DOWN, HOLD, RAMP_UP
- Parameters drawn at construction from config ranges for reproducibility
- `_on_activate`: saves `press._target_speed`, computes `low_speed = target * (1 - drop_pct/100)`,
  calls `press._line_speed_model.start_ramp(current, low_speed, ramp_down_s)`
- `_on_tick`: transitions HOLD→RAMP_UP at `elapsed >= ramp_down_s + hold_s`; completes at
  `elapsed >= total_s`
- `_on_complete`: restores speed with a quick ramp if not fully recovered
- Machine state stays Running (2) throughout — no fault code written
- Default ranges: hold 5-30s, drop 30-80%, ramp_down 2-5s, ramp_up 5-15s
- Ground truth logging on activate and complete

Engine wiring:
- Added `from factory_simulator.scenarios.micro_stop import MicroStop` to `scenario_engine.py`
- `_schedule_micro_stops()` uses Poisson scheduling (`_poisson_starts()`) with
  `frequency_per_shift = cfg.frequency_per_shift`, min_gap from min param values
- `_generate_timeline()` calls `_schedule_micro_stops()` after `_schedule_bearing_wear()`
- Added `"MicroStop"` entry to `_AFFECTED_SIGNALS` dict

Key implementation detail: `low_speed` is based on `press._target_speed` (configured baseline),
not the current ramp value. This ensures consistent drop magnitude even if the scenario fires
during ramp-up when actual speed may be far below target.

Tests: 16 new tests in `test_micro_stop.py` covering priority, default ranges, duration formula,
lifecycle (pending→active→completed), speed dip, machine state invariant, speed recovery, and
auto-scheduling. Fixed 9 packaging scenario test `_make_engine()` helpers to disable `micro_stop`
(and `bearing_wear` where missing) to prevent interference with existing tests. 2162 total tests
passing.

### Task 4.4 — Motor Bearing Wear Scenario (COMPLETE)

New file: `src/factory_simulator/scenarios/bearing_wear.py`.

`BearingWear` implements PRD 5.5 with:
- `priority = "background"` (never preempted, never deferred)
- Exponential vibration model: `vibration_increase = base_rate * exp(k * elapsed_hours)` applied
  each tick to `VibrationGenerator._models["main_drive_x/y/z"]._target`
- Current increase: `saved_base * current_factor * exp(k * elapsed_hours)` added to
  `PressGenerator._main_drive_current._base`
- Warning / alarm threshold flags (`_warning_logged`, `_alarm_logged`) set once each;
  ground truth `log_signal_anomaly` fired independently of `engine.ground_truth` being None
- Optional failure culmination: `force_state("Fault")` + `press._prev_state = STATE_FAULT`
  when `culminate_in_failure=True` and `vib_increase >= failure_vibration`
- On completion, original `_target` and `_base` values are restored

Engine wiring:
- Added `from factory_simulator.scenarios.bearing_wear import BearingWear` to `scenario_engine.py`
- `_schedule_bearing_wear()` creates one BearingWear at `start_after_hours * 3600` (single event,
  not Poisson, per PRD — bearing wear is a one-shot event, not recurring)
- `_generate_timeline()` calls `_schedule_bearing_wear()` in the Phase 4 section
- Added `"BearingWear"` entry to `_AFFECTED_SIGNALS` dict

Tests: 28 new tests in `test_bearing_wear.py` covering priority, defaults, lifecycle,
vibration exponential shape, current formula, failure culmination, threshold logging,
and auto-scheduling. 2146 total tests passing.

### Task 4.3 — Phase 4 Config Models (COMPLETE)

Added to `src/factory_simulator/config.py`:
- **Updated `BearingWearConfig`**: added `base_rate`, `acceleration_k`, `warning_threshold`,
  `alarm_threshold`, `current_increase_percent`, `failure_vibration` fields with validators.
- **`MicroStopConfig`**: frequency_per_shift, duration_seconds, speed_drop_percent, ramp
  down/up seconds.
- **`ContextualAnomalyConfig`** + 5 nested type configs: `HeaterStuckConfig`,
  `PressureBleedConfig`, `CounterFalseTriggerConfig`, `HotDuringMaintenanceConfig`,
  `VibrationDuringOffConfig`. All nested in `ContextualAnomalyTypesConfig`.
- **`IntermittentFaultConfig`** + 4 subtypes: `BearingIntermittentConfig`,
  `ElectricalIntermittentConfig`, `SensorIntermittentConfig`, `PneumaticIntermittentConfig`.
  Nested in `IntermittentFaultFaultsConfig`. Sensor starts disabled; pneumatic has
  phase3_transition=False.
- **`DataQualityConfig`**: `CommDropConfig` (modbus_drop/opcua_stale/mqtt_drop with
  per-protocol duration defaults), `NoiseConfig`, `SensorDisconnectConfig` (with
  `SensorDisconnectSentinelConfig` sub-model), `StuckSensorConfig`,
  `PartialModbusResponseConfig`. Plus scalar fields: duplicate_probability,
  exception_probability, timeout_probability, response_delay_ms, counter_rollover dict,
  mqtt_timestamp_offset_hours.
- **`ScenariosConfig`**: added `micro_stop`, `contextual_anomaly`, `intermittent_fault`
  (all `| None = None`, following F&B scenario pattern).
- **`FactoryConfig`**: added `data_quality: DataQualityConfig`.

Updated `config/factory.yaml`:
- bearing_wear: added base_rate, acceleration_k, warning_threshold, alarm_threshold,
  current_increase_percent, failure_vibration
- Added micro_stop, contextual_anomaly, intermittent_fault scenario sections (enabled)
- Added data_quality section with all defaults from PRD Appendix D

Updated `config/factory-foodbev.yaml`:
- bearing_wear: added new fields (enabled=false)
- Added micro_stop, contextual_anomaly, intermittent_fault (all disabled)
- Added data_quality section (sensor/stuck enabled, packaging-specific counters omitted)

37 new tests in `TestBearingWearConfigUpdated`, `TestMicroStopConfig`,
`TestContextualAnomalyConfig`, `TestIntermittentFaultConfig`, `TestCommDropConfig`,
`TestDataQualityConfig` covering defaults, validation, and YAML loading.

2118 tests passing.

### Task 4.2 — Scenario Priority and Conflict Resolution (COMPLETE)

Added `priority: ClassVar[str]` to the `Scenario` base class (default `"non_state_changing"`).
Set `priority = "state_changing"` on: WebBreak, UnplannedStop, JobChangeover, CipCycle,
ColdChainBreak, SealIntegrityFailure.

Modified `ScenarioEngine.tick()` with two-phase logic:
1. **Priority pass**: pending-due scenarios sorted by `_PRIORITY_ORDER`. Activating a
   `state_changing` scenario calls `complete()` on all active `non_state_changing` scenarios
   (preemption). Pending `non_state_changing` scenarios are added to a `skip_ids` set if any
   `state_changing` is currently active or about to activate this tick.
2. **Evaluate pass**: all non-skipped, non-preempted, non-COMPLETED scenarios are evaluated.
   Ground truth logging is unchanged.

Added `_PRIORITY_ORDER` module-level constant (`state_changing=0, non_state_changing=1,
background=2, micro=3`) and exported it for tests.

11 new tests in `TestScenarioPriority` covering:
- Priority attribute values on all 6 state_changing classes
- Priority values on non_state_changing classes
- Priority ordering dict
- Preemption of multiple active non_state_changing by a state_changing
- Deferral of pending non_state_changing when state_changing is active
- Recovery: non_state_changing activates after state_changing completes
- Background and micro always activate (no preemption, no deferral)
- Background NOT preempted when state_changing activates

Decision: `background` and `micro` priorities added to `_PRIORITY_ORDER` now (ready for
Tasks 4.4/4.5/4.7 which will set these on BearingWear, MicroStop, IntermittentFault).

2081 tests passing.

### Task 4.1 — Poisson Scheduling Engine (COMPLETE)

Implementation was already present in `scenario_engine.py` and `data_engine.py` from prior work:
- `_poisson_starts()` generates Poisson inter-arrival times via `rng.exponential(mean_interval)`
- `_spawn_rng()` uses `SeedSequence.spawn(1)[0]` (Y1 fix)
- `ScenarioEngine.__init__` accepts `sim_duration_s` parameter (Y3 fix)
- `DataEngine` passes `config.simulation.sim_duration_s` (or 8h default) to `ScenarioEngine`
- 21 new tests in `test_scenario_engine.py` covering KS test, min-gap, cross-shift, determinism, sim_duration

One test fix required: `test_generates_timeline_from_config` in `test_basic_scenarios.py` used
`sim_duration_s=8*3600`. With Poisson scheduling, P(0 UnplannedStops in 8h) ≈ 22% for the
default frequency [1,2]/shift. Extended to `sim_duration_s=7*86400` (1 week) to make the
presence assertion statistically robust.
