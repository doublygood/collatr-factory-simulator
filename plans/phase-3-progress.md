# Phase 3: F&B Profile — Progress

## Status: IN PROGRESS

## Tasks
- [x] 3.1: F&B Equipment Config Models
- [ ] 3.2: F&B Factory Config (factory-foodbev.yaml)
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
