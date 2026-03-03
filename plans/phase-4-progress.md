# Phase 4: Full Scenario System and Data Quality — Progress

## Status: In Progress

## Tasks
- [x] 4.1: Poisson Scheduling Engine
- [ ] 4.2: Scenario Priority and Conflict Resolution
- [ ] 4.3: Phase 4 Config Models
- [ ] 4.4: Motor Bearing Wear Scenario
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
