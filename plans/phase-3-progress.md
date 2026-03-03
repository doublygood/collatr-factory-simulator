# Phase 3: F&B Profile — Progress

## Status: IN PROGRESS

## Tasks
- [x] 3.1: F&B Equipment Config Models
- [x] 3.2: F&B Factory Config (factory-foodbev.yaml)
- [x] 3.3: Thermal Diffusion Signal Model
- [x] 3.4: Mixer Generator
- [x] 3.5: Oven Generator
- [x] 3.6: Filler Generator
- [x] 3.7: Sealer Generator
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

### Task 3.3: Thermal Diffusion Signal Model
`ThermalDiffusionModel` in `src/factory_simulator/models/thermal_diffusion.py` — already implemented in prior session but task JSON not updated.
- Truncated Fourier series for 1D heat conduction in a slab (PRD 4.2.10)
- Uses `4*L^2` in decay denominator (standard physics for slab with half-thickness L)
- Adaptive term count: sums until `|T(0) - T_initial| < 1.0°C`
- `generate(sim_time, dt)` advances elapsed time and returns core temp
- `reset()` clears elapsed time; `restart(T_initial, T_oven)` updates params and recomputes terms
- `set_oven_temp(T_oven)` changes oven temp mid-run without resetting elapsed
- Noise injection via optional `NoiseGenerator`
- 62 tests covering convergence, monotonicity, physical correctness (72°C in ~8-9 min), determinism, time compression, edge cases, Hypothesis property-based tests
- Exported from `factory_simulator.models` package
- All 1538 tests pass.

### Task 3.4: Mixer Generator
`MixerGenerator` in `src/factory_simulator/generators/mixer.py` — 8 signals, 6-state batch cycle state machine.
- **State machine**: Off(0)/Loading(1)/Mixing(2)/Holding(3)/Discharging(4)/CIP(5) via `StateMachineModel`
- **Speed** (RampModel): ramps to target_speed (450 RPM) during Mixing, drops to 150 RPM for Holding, 50 RPM for Loading/Discharging, 0 for Off/CIP
- **Torque** (CorrelatedFollowerModel): follows speed with gain=0.8, lag=2.0s, noise
- **Batch temperature** (FirstOrderLagModel): starts at 4.0°C (chilled ingredients), setpoint 65.0°C during Mixing, tau=120s
- **Batch weight** (RampModel): ramps up to 500kg during Loading (120s), ramps down to 0 during Discharging (90s)
- **Batch ID** (StringGeneratorModel): new batch ID generated on each Loading transition
- **Mix time elapsed** (CounterModel): increments during Mixing/Holding states, resets on Loading
- **Lid closed** (StateMachineModel): binary 0/1, closes on Loading, opens on Off
- State transitions handled by `_handle_state_transition()` — scenario-driven via `force_state()`
- Properties exposed for scenario access: state_machine, speed_model, batch_weight_model, batch_temp_model, batch_id_model, mix_time_model, lid_state_machine
- Registered in `data_engine.py` as `"high_shear_mixer": MixerGenerator`
- 27 tests covering: signal IDs, initial state, all state transitions, torque correlation, batch temperature lag, batch ID string generation, mix time counting, signal bounds, determinism, protocol mappings
- All 1565 tests pass (1538 existing + 27 new).

### Task 3.5: Oven Generator
`OvenGenerator` in `src/factory_simulator/generators/oven.py` — 13 signals, 5-state machine.
- **State machine**: Off(0)/Preheat(1)/Running(2)/Idle(3)/Cooldown(4) via `StateMachineModel`
- **Zone temperatures** (3×, FirstOrderLagModel): start at ambient (20°C); setpoints set to configured targets (160/200/180°C) on Preheat/Running/Idle entry, reset to ambient on Off/Cooldown
- **Zone setpoints** (3×, SteadyStateModel): output configured targets each tick (modbus_writable=True for operator override)
- **Thermal coupling**: adjacent zones influence each other with factor 0.05 using previous-tick zone temps to avoid circular deps
- **Product core temp** (ThermalDiffusionModel): restarts from 4°C (chilled entry) on Running entry; advances only in Running; held constant in other states; T_oven driven by zone_2_temp
- **Belt speed** (SteadyStateModel): target 2.0 m/min during Preheat/Running/Idle; 0 when Off/Cooldown
- **Humidity zone 2** (SteadyStateModel): steady ambient humidity in cooking zone
- **Output powers** (3×, CorrelatedFollowerModel): base=50%, gain=-0.3 of zone temp; clamped to [0, 100]%; high when cold, low at setpoint
- Registered in `data_engine.py` as `"tunnel_oven": OvenGenerator`
- Properties exposed: state_machine, zone_temp_models, zone_setpoint_models, thermal_diffusion_model, thermal_coupling
- 29 tests covering: signal IDs, initial state, all state transitions, belt speed behavior, product core temp (rise during Running, hold during Off, trend), output power bounds and direction, all 13 signals per tick, determinism, protocol mappings
- All 1594 tests pass (1565 existing + 29 new).

### Task 3.6: Filler Generator
`FillerGenerator` in `src/factory_simulator/generators/filler.py` — 8 signals, 5-state machine.
- **State machine**: Off(0)/Setup(1)/Running(2)/Starved(3)/Fault(4) via `StateMachineModel`
- **Line speed** (SteadyStateModel): target 60 ppm during Running; 0 when not Running
- **Per-item fill weight**: Gaussian(mean=fill_target+fill_giveaway, sigma=fill_sigma). One value per item arrival (interval = 60/line_speed seconds). Between arrivals, last value is held.
- **fill_deviation**: = fill_weight - fill_target, always consistent (no separate model)
- **packs_produced**: simple float counter, incremented by 1 on each item arrival, capped at 999999
- **reject_count**: incremented by 1 when |deviation| > fill_tolerance, capped at 9999
- **fill_target** (SteadyStateModel): steady setpoint from config
- **hopper_level** (DepletionModel): depletes proportional to packs/s (line_speed/60); auto-refills at threshold; sawtooth pattern
- Fill parameters from config extras: fill_target_g=400, fill_giveaway_g=5, fill_sigma_g=3, fill_tolerance_g=15
- Registered in `data_engine.py` as `"gravimetric_filler": FillerGenerator`
- Properties exposed for scenarios: state_machine, hopper_model, packs_produced, reject_count, last_fill_weight, line_speed_model
- 28 tests covering: signal IDs, initial state, line speed per state, per-item timing, packs counting, fill deviation consistency, reject counting, hopper depletion, fill target output, output completeness, determinism, state machine access
- All 1622 tests pass (1594 existing + 28 new).

### Task 3.7: Sealer Generator
`SealerGenerator` in `src/factory_simulator/generators/sealer.py` — 6 signals, follows filler state.
- **seal_temp** (first-order lag on internal continuous state): converges toward target (180°C) when filler is Running (state==2), decays toward ambient (20°C) via tau=180s when inactive. Min clamp 100°C enforced on output.
- **seal_pressure** (SteadyStateModel): target 3.5 bar when active; 0.0 when inactive.
- **seal_dwell** (SteadyStateModel): always at target 2.0 s (process parameter, always generated).
- **gas_co2_pct / gas_n2_pct** (SteadyStateModel): always generated (hold at 30%/70% targets, representing standby gas supply).
- **vacuum_level** (SteadyStateModel): target -0.7 bar when active; 0.0 when inactive.
- Registered in `data_engine.py` as `"tray_sealer": SealerGenerator`.
- Graceful fallback: when `filler.state` is absent from store, behaves as inactive.
- 20 tests covering: signal count, signal IDs, seal temp convergence, seal temp decay, seal pressure active/inactive, vacuum active/inactive, dwell always generated, gas mix always generated, clamping, no-filler-state fallback, determinism, different seeds.
- All 1642 tests pass (1622 existing + 20 new).
