# Phase 4: Full Scenario System and Data Quality — Progress

## Status: In Progress

## Tasks
- [x] 4.1: Poisson Scheduling Engine
- [x] 4.2: Scenario Priority and Conflict Resolution
- [x] 4.3: Phase 4 Config Models
- [x] 4.4: Motor Bearing Wear Scenario
- [ ] 4.5: Micro-Stops Scenario
- [ ] 4.6: Contextual Anomalies Scenario
- [ ] 4.7: Intermittent Faults Scenario
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
