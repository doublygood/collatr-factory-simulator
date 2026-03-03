# Phase 3: F&B Profile — Progress

## Status: IN PROGRESS

## Tasks
- [x] 3.1: F&B Equipment Config Models
- [x] 3.2: F&B Factory Config (factory-foodbev.yaml)
- [ ] 3.3: Thermal Diffusion Signal Model
- [ ] 3.4: Mixer Generator
- [ ] 3.5: Oven Generator
- [ ] 3.6: Filler Generator
- [ ] 3.7: Sealer Generator
- [ ] 3.8: Checkweigher (QC) Generator
- [ ] 3.9: Chiller Generator
- [ ] 3.10: CIP Generator
- [ ] 3.11: Shared Generator Coupling for F&B
- [ ] 3.12: F&B Modbus — CDAB Encoding + Dynamic Block Sizing
- [ ] 3.13: F&B Modbus — Multi-Slave Oven Eurotherm UIDs
- [ ] 3.14: F&B OPC-UA + MQTT Validation Tests
- [ ] 3.15: Batch Cycle Scenario (Mixer)
- [ ] 3.16: Oven Thermal Excursion Scenario
- [ ] 3.17: Fill Weight Drift Scenario
- [ ] 3.18: Seal Integrity Failure Scenario
- [ ] 3.19: Chiller Door Alarm Scenario
- [ ] 3.20: CIP Cycle Scenario
- [ ] 3.21: Cold Chain Break Scenario
- [ ] 3.22: F&B Scenario Auto-Scheduling
- [ ] 3.23: F&B Modbus Integration Test
- [ ] 3.24: F&B OPC-UA + MQTT Integration Test
- [ ] 3.25: F&B Cross-Protocol Consistency Test

## Notes

### Task 3.1: F&B Equipment Config Models
Added 7 Pydantic config models for F&B scenarios to `config.py`:
- `BatchCycleConfig` — frequency_per_shift [8,16], batch_duration_seconds [1200,2700]
- `OvenThermalExcursionConfig` — frequency_per_shift [1,2], duration_seconds [1800,5400], max_drift_c [3.0,10.0]
- `FillWeightDriftConfig` — frequency_per_shift [1,3], duration_seconds [600,3600], drift_rate [0.05,0.2]
- `SealIntegrityFailureConfig` — frequency_per_week [1,2], duration_seconds [300,1800]
- `ChillerDoorAlarmConfig` — frequency_per_week [1,3], duration_seconds [300,1200]
- `CipCycleConfig` — frequency_per_day [1,3], cycle_duration_seconds [1800,3600]
- `ColdChainBreakConfig` — frequency_per_month [1,2], duration_seconds [1800,7200]

Extended `ScenariosConfig` with 7 optional fields (None by default for packaging profile).
All follow existing range-pair validation pattern with `_validate_range_pair()`.
22 new tests added. All 1513 tests pass.

### Task 3.2: F&B Factory Config (factory-foodbev.yaml)
Created `config/factory-foodbev.yaml` with all 68 signals across 10 equipment groups:
- **Mixer** (8 signals): `modbus_byte_order: "CDAB"` on HR 1000-1011 for Allen-Bradley CompactLogix, coil 100 for lid_closed
- **Oven** (13 signals): 3-zone temps + setpoints (writable), 3 output_power on multi-slave UIDs 11/12/13 (IR 0-2, int16 x10), belt_speed, state
- **Filler** (8 signals): 7 OPC-UA only (FoodBevLine.Filler1.*), hopper_level sole Modbus signal (HR 1200-1201)
- **Sealer** (6 signals): HR 1300-1311, seal temp/pressure/dwell + MAP gas + vacuum
- **QC** (6 signals): All OPC-UA (FoodBevLine.QC1.*), actual_weight/overweight/underweight/metal_detect/throughput/state
- **Chiller** (7 signals): HR 1400-1407, IR 110-111, coils 101-102, DI 100
- **CIP** (5 signals): state OPC-UA, rest on HR 1500-1507, IR 115
- **Coder** (11 signals, shared): MQTT topics with coupling_state_signal=filler.state, coupling_running_state=Running
- **Environment** (2 signals, shared): MQTT, tighter F&B ranges (center 15°C for food factory)
- **Energy** (2 signals, shared): HR 600-603 (shared registers), OPC-UA FoodBevLine.Energy, parent=filler.line_speed

All 7 F&B scenarios enabled; packaging scenarios disabled except shift_change.
All Modbus addresses cross-referenced against PRD Appendix A. OPC-UA nodes match Appendix B. MQTT topics match Appendix C (13 total: 11 coder + 2 env, no vibration).
22 new tests in `TestFnbConfigLoading`. All 1538 tests pass.
