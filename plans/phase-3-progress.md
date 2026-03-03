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
- [x] 3.8: Checkweigher (QC) Generator
- [x] 3.9: Chiller Generator
- [x] 3.10: CIP Generator
- [x] 3.11: Shared Generator Coupling for F&B
- [x] 3.12: F&B Modbus — CDAB Encoding + Dynamic Block Sizing
- [x] 3.13: F&B Modbus — Multi-Slave Oven Eurotherm UIDs
- [x] 3.14: F&B OPC-UA + MQTT Validation Tests
- [x] 3.15: Batch Cycle Scenario (Mixer)
- [x] 3.16: Oven Thermal Excursion Scenario
- [x] 3.17: Fill Weight Drift Scenario
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

### Task 3.8: Checkweigher (QC) Generator
`CheckweigherGenerator` in `src/factory_simulator/generators/checkweigher.py` — 6 signals, per-item generation.
- **actual_weight**: reads `filler.fill_weight` from store on each item arrival, adds `tray_weight_g` (default 10g). Noise from signal config sigma. Clamped to [100, 1000] g. Between arrivals, holds last value.
- **overweight_count**: discrete integer counter, increments by 1 when actual > fill_target + tray + overweight_threshold (default 30g). Capped at 9999.
- **underweight_count**: discrete integer counter, increments by 1 when actual < fill_target + tray - underweight_threshold (default 15g). Capped at 9999.
- **metal_detect_trips**: per-item Bernoulli with probability `metal_detect_prob` (default 0.001 = 1 per 1000 packs as per PRD 2b.6). Capped at 99.
- **throughput**: mirrors `filler.line_speed` from store when Running; 0.0 when inactive. Optional noise. Clamped to [10, 120] items/min when running.
- **reject_total**: running total of all reject types (overweight + underweight + metal detect). Capped at 9999.
- Weight thresholds read from config extras: `tray_weight_g`, `overweight_threshold_g`, `underweight_threshold_g`.
- Item timing: tracks `_time_since_last_item`, item_interval = 60 / line_speed s.
- Graceful fallback: when `filler.fill_target` is absent, derives reference from `last_actual_weight - tray_weight`.
- Registered in `data_engine.py` as `"checkweigher": CheckweigherGenerator`.
- Properties exposed: overweight_count, underweight_count, metal_detect_trips, reject_total, last_actual_weight, tray_weight, overweight_threshold, underweight_threshold.
- 26 tests covering: signal count/IDs, actual_weight offset, per-item holding, overweight/underweight counting, metal detect Bernoulli, reject total accumulation, throughput active/inactive, counter zero when filler off or speed=0, empty store fallback, item timer carry-over, determinism, config defaults.
- All 1668 tests pass (1642 existing + 26 new).

### Task 3.9: Chiller Generator
`ChillerGenerator` in `src/factory_simulator/generators/chiller.py` — 7 signals, bang-bang hysteresis refrigeration model.
- **Bang-bang controller** (PRD 4.2.12): room_temp oscillates around setpoint (default 2°C) in sawtooth pattern. Compressor turns ON when temp > setpoint + 1°C; turns OFF when temp < setpoint - 1°C. Cooling rate 0.5°C/min, heat gain rate 0.2°C/min.
- **Defrost cycles**: periodic every 6h (4 per day), 20 min duration. During defrost, compressor forced OFF and defrost heaters add 3°C/min extra heat. Managed by `_time_since_last_defrost` and `_defrost_elapsed` counters.
- **Door open**: `_door_open` property (default False) set by scenarios. Adds 1.5°C/min heat when open (ChillerDoorAlarmScenario, task 3.19).
- **Compressor lock**: `compressor_forced_off` property allows scenarios (ColdChainBreak, task 3.21) to lock the compressor OFF, overriding bang-bang.
- **Suction/discharge pressure**: first-order lag (τ=60s) toward compressor-state-dependent targets. Suction: 3.0 bar (ON) / 4.5 bar (OFF). Discharge: 16.0 bar (ON) / 12.0 bar (OFF).
- **setpoint**: read from config params; exposed as property for scenarios.
- Registered in `data_engine.py` as `"cold_room": ChillerGenerator`.
- Properties exposed for scenarios: room_temp (R/W), compressor_on (R), compressor_forced_off (R/W), door_open (R/W), defrost_active (R), setpoint (R).
- 37 tests covering: signal count/IDs, initial state, bang-bang cycling (ON→OFF, OFF→ON, sawtooth), defrost activation/duration/compressor-force/heat-rate, door open heat gain, pressure tracking, compressor_forced_off lock/release, signal clamp bounds, compressor binary, determinism, protocol mappings, setpoint writability.
- All 1705 tests pass (1668 existing + 37 new).

### Task 3.10: CIP Generator
`CipGenerator` in `src/factory_simulator/generators/cip.py` — 5 signals, 6-state phase sequence (Idle/Pre-rinse/Caustic/Intermediate/Acid/Final-rinse).
- **State machine**: integer state (0-5) with internal timers for auto-phase progression. Default phase durations: Pre-rinse 300s, Caustic 1080s, Intermediate 300s, Acid 750s, Final rinse 420s. `force_state()` used by CIP scenario (task 3.20) to kick off cycles.
- **Wash temperature**: first-order lag (τ=90s) tracking phase-specific setpoints: Idle 20°C, Pre-rinse/Intermediate/Final 45°C, Caustic 75°C, Acid 65°C.
- **Flow rate**: first-order lag (τ=15s) with phase-specific targets: 0 L/min (Idle), 60-80 L/min (active phases).
- **Conductivity**: asymmetric first-order lag — fast rise (τ=60s) when target > current (caustic injection, acid), slow decay (τ=120s) when target < current (rinse phases). Caustic target 120 mS/cm, Acid 40 mS/cm, rinse/idle 0 mS/cm.
- **cycle_time_elapsed**: monotonically increments while not Idle; resets to 0 when entering Idle.
- **final_rinse_passed**: True if conductivity < 5 mS/cm at end of final rinse phase (PRD 2b.8 acceptance criterion).
- **Auto-progression**: each phase auto-advances to the next after its configured duration via `_NEXT_PHASE` dict. Generator is self-contained once triggered.
- Registered in `data_engine.py` as `"cip_skid": CipGenerator`.
- Properties exposed for scenarios: state (R), cycle_time_elapsed (R), conductivity (R), wash_temp (R), flow_rate (R), final_rinse_passed (R).
- 58 tests covering: signal count/IDs, initial Idle state, force_state (all phases, case-insensitive, same-state noop), auto-progression through all phases, wash_temp lag, flow_rate active/idle, conductivity rise/decay, cycle_time_elapsed increment/reset, final_rinse_passed flag, signal clamp bounds, determinism, _parse_state (valid/invalid inputs), protocol mappings (HR + OPC-UA state).
- All 1660 unit tests pass (1602 existing + 58 new; note: integration tests not run in unit-only suite).

### Task 3.11: Shared Generator Coupling for F&B
Made `CoderGenerator` and `EnergyGenerator` configurable for their coupling signals.

**CoderGenerator** (`src/factory_simulator/generators/coder.py`):
- Reads `coupling_state_signal` from `EquipmentConfig.model_extra` (default `"press.machine_state"`)
- Reads `coupling_speed_signal` from `EquipmentConfig.model_extra` (default `"press.line_speed"`)
- `generate()` uses these signal names to read from the store — no other logic changes
- Packaging profile: no config fields → defaults → press coupling (fully backward-compatible)
- F&B profile: `coupling_state_signal: "filler.state"`, `coupling_speed_signal: "filler.line_speed"` in `factory-foodbev.yaml`

**EnergyGenerator** (`src/factory_simulator/generators/energy.py`):
- Reads `coupling_speed_signal` from `EquipmentConfig.model_extra` (default `"press.line_speed"`)
- `generate()` uses the configured signal name — no other logic changes

**Design decision**: The state condition logic (`press_running`, `press_idle`, `shutdown`, etc.) is unchanged because both press (states 0-5) and filler (states 0-4) use the same state numbering scheme (Running=2, Idle/Starved=3, Off=0, Setup=1). The `shutdown` condition `state in (0, 5)` works correctly for filler since state 5 never occurs.

**Tests** (`tests/unit/test_generators/test_fnb_coupling.py`):
- 20 new tests: 12 for coder (default/F&B coupling, pump speed tracking), 8 for energy (default/F&B coupling)
- Key tests: `test_not_driven_by_filler` (packaging coder ignores filler), `test_not_driven_by_press` (F&B coder ignores press), `test_not_driven_by_filler` (packaging energy ignores filler), `test_not_driven_by_press` (F&B energy ignores press)
- All 1783 tests pass (1763 existing + 20 new).

### Task 3.12: F&B Modbus — CDAB Encoding + Dynamic Block Sizing

**Files changed**: `src/factory_simulator/config.py`, `src/factory_simulator/protocols/modbus_server.py`, `tests/unit/test_protocols/test_modbus.py`, `tests/unit/test_config.py`

**config.py**: Added 4 explicit fields to `SignalConfig` (previously in `model_extra`):
- `modbus_byte_order: str = "ABCD"` — "CDAB" for Allen-Bradley mixer registers
- `modbus_coil: int | None = None` — coil address for binary signals (F&B: lid_closed 100, compressor_state 101, defrost_active 102)
- `modbus_di: int | None = None` — discrete input address (F&B: door_open 100)
- `modbus_slave_id: int | None = None` — secondary slave UID (F&B oven zones, used in task 3.13)

**modbus_server.py**: Full set of changes:
1. **CDAB encode/decode**: `encode_float32_cdab`, `decode_float32_cdab`, `encode_uint32_cdab`, `decode_uint32_cdab` — Allen-Bradley word swap (low word in register[0], high word in register[1])
2. **`byte_order` field on `HoldingRegisterEntry`**: default `"ABCD"`, set to `"CDAB"` for mixer signals
3. **Dynamic block sizing**: `_compute_block_size(addresses, min_size=16)` function computes required block size from max register address + 3. Replaces hardcoded constants. F&B profile HR block grows to ~1510 entries; packaging stays at ~606.
4. **F&B coils/DIs from config**: `build_register_map` scans all signals for `modbus_coil`/`modbus_di` and appends dynamic `CoilDefinition`/`DiscreteInputDefinition` using "gt_zero" mode. Packaging hardcoded coils/DIs unchanged.
5. **Secondary slave exclusion**: signals with `modbus_slave_id` set are skipped from main IR block (they go to multi-slave UIDs 11-13 in task 3.13).
6. **Sync respects byte order**: `_sync_holding_registers` uses per-entry `byte_order` to call CDAB or ABCD encoders. `_decode_hr_value` accepts `byte_order` parameter.
7. **`DiscreteInputDefinition` gets "gt_zero" mode**: added to `_sync_discrete_inputs` for dynamic DIs.

**Tests added** (28 new tests):
- `TestFloat32CdabEncoding` (6): round-trip, word order verification vs ABCD, mixer range
- `TestUint32CdabEncoding` (5): round-trip, max value, word order
- `TestDynamicBlockSizing` (4): packaging stays small, F&B HR/coil/DI blocks grow correctly
- `TestCdabSync` (3): float32 and uint32 CDAB syncs to correct registers, ABCD decoding gives wrong value
- `TestFnbDynamicCoilsDI` (5): coil/DI registered from config, sync True/False correctly from store
- Fixed 4 existing tests in `test_config.py` that used `model_extra.get()` for now-explicit fields

All 1806 tests pass.

**Decision**: Kept packaging profile hardcoded coil/DI defs unchanged — they'll just be "False" coils in F&B mode since `press.*` signals don't exist in the F&B store. This is correct behaviour and avoids profile-sniffing logic.

### Task 3.14: F&B OPC-UA + MQTT Validation Tests

`tests/unit/test_protocols/test_opcua_fnb.py` — 31 tests validating F&B protocol endpoints.

**OPC-UA tests (TestFnbNodeTreeStructure, TestFnbNodeDataTypes, TestFnbEURangeAttribute, TestFnbAccessLevel, TestFnbServerConstruction)**:
- Verified 19 FoodBevLine OPC-UA nodes per Appendix B (Mixer1: State+BatchId, Oven1: State, Filler1: 7 nodes, QC1: 6 nodes, CIP1: State, Energy: 2 nodes)
- Confirmed no PackagingLine nodes built when F&B config is used
- All nodes browsable via string NodeId, all node_to_signal mappings present
- Data types: Double/UInt32/UInt16 for numeric nodes, String for BatchId
- EURange present on all nodes (including String); key values match config (Filler1.LineSpeed [10,120], FillWeight [200,800], QC1.ActualWeight [100,1000], Energy.LinePower [0,300])
- All 19 nodes read-only (F&B setpoints accessed via Modbus only)

**MQTT tests (TestFnbMqttTopicMap)**:
- Verified exactly 13 topics (11 coder + 2 env), no vibration topics
- All topics use `foodbev1` prefix (`collatr/factory/demo/foodbev1/`)
- coder/state, prints_total, nozzle_health, gutter_fault → QoS 1, retain=True, event-driven
- Analog coder and env topics → QoS 0, retain=True, timed intervals match sample_rate_ms
- `build_batch_vibration_entry` returns None for F&B (no vibration equipment)
- Modbus-only signals (mixer.speed, oven.zone_1_temp, filler.hopper_level, etc.) absent from topic map

All 1780 non-integration tests pass (1748 unit + 32 other non-integration).

### Task 3.13: F&B Modbus — Multi-Slave Oven Eurotherm UIDs

**Files changed**: `src/factory_simulator/config.py`, `src/factory_simulator/protocols/modbus_server.py`, `config/factory-foodbev.yaml`, `tests/unit/test_protocols/test_modbus.py`, `tests/unit/test_config.py`

**Design**: Multi-slave support adds secondary `ModbusDeviceContext` instances for Eurotherm UIDs 11-13. A new config field `modbus_slave_ir` on `SignalConfig` allows signals to appear in BOTH the main UID-1 IR block AND a secondary slave IR block. Signals with `modbus_slave_id` but NO `modbus_slave_ir` remain exclusive to the secondary slave (backward-compatible with existing output_power config using `modbus_ir`).

**config.py**: Added `modbus_slave_ir: list[int] | None = None` to `SignalConfig`. This is the IR address on the secondary slave (separate from `modbus_ir` which is the main UID-1 address). When both `modbus_slave_id` and `modbus_slave_ir` are set, the signal appears in BOTH blocks.

**factory-foodbev.yaml**: Added `modbus_slave_id` + `modbus_slave_ir` to 6 oven signals:
- `zone_1_temp`: slave_id=11, slave_ir=[0] (PV at IR 0 of UID 11)
- `zone_1_setpoint`: slave_id=11, slave_ir=[1] (SP at IR 1 of UID 11)
- `zone_2_temp`: slave_id=12, slave_ir=[0] (PV at IR 0 of UID 12)
- `zone_2_setpoint`: slave_id=12, slave_ir=[1] (SP at IR 1 of UID 12)
- `zone_3_temp`: slave_id=13, slave_ir=[0] (PV at IR 0 of UID 13)
- `zone_3_setpoint`: slave_id=13, slave_ir=[1] (SP at IR 1 of UID 13)
Existing output_power signals keep `modbus_slave_id` + `modbus_ir: [2]` (no `modbus_slave_ir`).

**modbus_server.py** additions:
1. `SecondarySlaveEntry`: signal_id, address, data_type ("int16_x10") for one IR entry on a secondary slave
2. `SecondarySlaveRegisterMap`: slave_id + list of SecondarySlaveEntry
3. `RegisterMap.secondary_slaves`: new field holding all secondary slave maps
4. `build_register_map`: updated skip logic (skip from main IR only if `modbus_slave_id is not None AND modbus_slave_ir is None`); added secondary slave discovery loop
5. `ModbusServer.__init__`: builds `_secondary_ir_blocks` and `_secondary_contexts` (FactoryDeviceContext with minimal stub HR/coil/DI) per slave
6. `_sync_secondary_slaves()`: copies signal values from store to secondary IR blocks as int16_x10
7. `sync_registers()`: calls `_sync_secondary_slaves()`
8. `start()`: uses `ModbusServerContext(devices=dict, single=False)` when secondary slaves exist; `single=True` for packaging profile (preserves existing behavior)

**Tests added** (17 new tests in test_modbus.py):
- `TestMultiSlaveRegisterMap` (8): secondary slave discovery, PV/SP/output at correct IR addresses, dual-mapped signals in both main and secondary IR, exclusive signals not in main IR, IR block sync, no secondary slaves for packaging, tolerant of missing store signals
- `TestFnbMultiSlaveConfig` (7): F&B config has UIDs 11-13, each has IR 0-2, zone temps/setpoints in main IR block, output powers NOT in main IR, sync check for UID 11

**Also fixed**: Pre-existing E501 ruff violation in `test_config.py:763` (line too long in assertion).

All 1820 tests pass.

### Task 3.15: Batch Cycle Scenario (Mixer)

**File**: `src/factory_simulator/scenarios/batch_cycle.py`

**Design**: One scenario instance = one batch cycle (Loading → Mixing → Holding → Discharging). The scheduler creates 8-16 per shift. This matches the existing pattern (e.g., dryer_drift = one anomaly occurrence).

**Implementation**:
- `BatchCycle(Scenario)` with internal `_Phase` enum (LOADING/MIXING/HOLDING/DISCHARGING)
- Phase durations drawn at construction from configured `[min, max]` ranges — batch-to-batch variation
- Default ranges from PRD 5.14.1: loading 2-5 min, mixing 10-25 min, holding 5-10 min, discharging 2-5 min (total 19-45 min ≈ 20-45 min requirement)
- `_on_activate`: finds MixerGenerator, calls `force_state("Loading")`, logs `scenario_start` + `state_change` events
- `_on_tick`: tracks `_phase_elapsed`, calls `_transition()` at each threshold
- `_on_complete`: calls `force_state("Off")`, logs `state_change` + `scenario_end`
- Graceful completion if no MixerGenerator found (packaging profile protection)

**Tests** (`tests/unit/test_scenarios/test_batch_cycle.py`):
- 14 tests in 4 classes: Lifecycle, StateTransitions, Variation, NoMixer
- Key tests: full phase sequence in order, mixer returns to Off after completion, durations within PRD range, different seeds produce different durations, graceful exit without mixer
- Uses F&B config (factory-foodbev.yaml); packages config used for NoMixer test

All 1865 tests pass.

### Task 3.17: Fill Weight Drift Scenario

`FillWeightDrift` in `src/factory_simulator/scenarios/fill_weight_drift.py` — fill weight mean drifts from target.

- **Mechanism**: modifies `filler._fill_giveaway` each tick to shift the Gaussian mean used for per-item draw `Normal(fill_target + fill_giveaway, sigma)`. Drift is a linear ramp capped at `max_drift`.
- **Direction**: +1 for over-weight drift, -1 for under-weight, random by default
- **Drift rate**: 0.05-0.2 g/min (PRD 5.14.3)
- **Duration**: 10-60 min (PRD 5.14.3)
- **Recovery**: on `_on_complete`, saved `_fill_giveaway` is restored (operator recalibration)
- **Reject count**: increases naturally as more items fall outside fill_tolerance (no explicit intervention needed — driven by shifted mean)
- **Ground truth**: logs `signal_anomaly` on `filler.fill_weight` at activation
- **Graceful exit**: if no FillerGenerator found in engine (e.g. packaging config), scenario completes immediately

**Tests** (`tests/unit/test_scenarios/test_fill_weight_drift.py`):
- 17 tests in 5 classes: Lifecycle, Effect, Recovery, Defaults, NoFiller
- Key tests: giveaway increases/decreases per direction, drift proportional to rate×elapsed, capped at max_drift, giveaway restored exactly after completion, mid-scenario elevation observed then restored, default ranges match PRD, graceful completion without filler

All 1830 non-integration tests pass (17 new).
