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
- [ ] 4.8: Communication Drop Injection
- [ ] 4.9: Sensor Disconnect and Stuck Sensor
- [ ] 4.10: Modbus Exception and Partial Response Injection
- [ ] 4.11: Duplicate Timestamps and Timezone Offset
- [ ] 4.12: Data Quality Engine Integration
- [ ] 4.13: Noise Calibration — Packaging Profile
- [ ] 4.14: Noise Calibration — F&B Profile
- [ ] 4.15: Counter Rollover Testing Support
- [ ] 4.16: Reproducibility Test and Final Integration

## Carried Forward Items
- Y1 (Phase 2): `_spawn_rng` uses `integers()` not `SeedSequence.spawn()` → Fix in Task 4.1
- Y3 (Phase 2.1): DataEngine doesn't pass `sim_duration_s` to ScenarioEngine → Fix in Task 4.1
- gutter_fault probability 18x too high → Fix in Task 4.13

## Notes

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
