# Phase 3: F&B (Food & Beverage) Chilled Ready Meal Profile

**Timeline:** Weeks 6-8 (expanded from 2 to 3 weeks per PRD Appendix F)
**Goal:** F&B profile fully operational with 68 signals, 6 new equipment generators, 3 shared generators adapted for F&B, F&B-specific protocol endpoints, and 7+ F&B scenarios.

## Overview

Phase 3 adds the second factory profile: a UK chilled ready meal production line. The packaging profile (47 signals, 7 equipment groups, 10 scenarios) is fully operational across Modbus TCP, OPC-UA, and MQTT with ground truth logging. Phase 3 builds on this foundation by adding:

- **Profile infrastructure**: config-driven profile selection, F&B equipment and signal definitions in `factory.yaml`
- **6 new equipment generators**: mixer, oven, filler, sealer, checkweigher (QC), chiller, plus CIP
- **3 shared generators adapted**: coder (tracks filler.state instead of press.machine_state), environment (tighter F&B temp ranges), energy (higher base load from refrigeration)
- **F&B protocol endpoints**: Modbus HR 1000-1599 (CDAB for mixer, ABCD for rest), Modbus IR 100-121, coils 100-102, DI 100, multi-slave UIDs 11-13 for oven zones, OPC-UA FoodBevLine node tree, MQTT topics on foodbev1 line_id
- **7 F&B scenarios**: batch cycle, oven thermal excursion, fill weight drift, seal integrity failure, chiller door alarm, CIP cycle, cold chain break
- **Allergen changeover**: combined with CIP scenario (mandatory CIP triggered by allergen transition)

By the end of Phase 3, all 68 F&B signals are accessible on all three protocols simultaneously, 7+ F&B scenario types fire during simulation runs, and every event is recorded in the ground truth JSONL log.

## PRD References

Read these sections before starting any task group:

| Group | PRD Sections |
|-------|-------------|
| **A: Profile Infrastructure** | `prd/06-configuration.md`, `prd/08-architecture.md` (8.4), `prd/appendix-f-implementation-phases.md` (Phase 3) |
| **B: Equipment Generators** | `prd/02b-factory-layout-food-and-beverage.md` (ALL), `prd/04-data-generation-engine.md` (4.2, 4.3, 4.6) |
| **C: Protocol Endpoints** | `prd/03-protocol-endpoints.md` (3.1.1-3.1.6, 3.2.1, 3.3.3), `prd/appendix-a-modbus-register-map.md` (F&B section), `prd/appendix-b-opcua-node-tree.md` (FoodBevLine), `prd/appendix-c-mqtt-topic-map.md` (F&B section) |
| **D: Scenarios** | `prd/05-scenario-system.md` (5.14.1-5.14.8), `prd/04-data-generation-engine.md` (4.6, 4.7) |
| **E: Integration** | All of the above, `prd/appendix-f-implementation-phases.md` (Phase 3 exit criteria) |

## Carried Forward Items

### From Phase 2 Independent Review

| ID | Description | Disposition |
|----|-------------|-------------|
| R3 | Phase 2 scenarios never auto-scheduled in `_generate_timeline()` | **FIXED in Phase 2.1** — all 10 scenario types now auto-scheduled |
| Y3 (Phase 2.1) | DataEngine doesn't pass `sim_duration_s` to ScenarioEngine — defaults to 8 hours | **NOTE ONLY** — pre-existing limitation, not a Phase 3 task. Document in progress file. Scenarios beyond 8h simulated won't auto-schedule. |
| Y6 | Scenarios access generator private attributes directly | **NOTE ONLY** — acknowledged coupling. F&B scenarios will follow the same pattern for consistency. Consider public API in Phase 5. |
| Y1 (Phase 2) | `_spawn_rng` uses `integers()` not `SeedSequence.spawn()` | **Deferred to Phase 4** per review. |

### From Phase 2.1 Independent Review

| ID | Description | Disposition |
|----|-------------|-------------|
| Y3 | DataEngine doesn't pass `sim_duration_s` | Same as above. Pre-existing, note it. |

## Important Design Decisions

### 1. Profile Switching (Config-Driven)

The profile is selected by which `factory.yaml` config file is loaded. There is no runtime profile switching. The `_GENERATOR_REGISTRY` in `data_engine.py` maps equipment type strings to generator classes. F&B equipment types get new entries in the registry.

- Packaging profile: `config/factory.yaml` (existing)
- F&B profile: `config/factory-foodbev.yaml` (new)

Both configs use the same schema (`FactoryConfig`). The difference is which equipment groups are defined and what protocol mappings they declare. The `line_id` in MQTT config switches the topic prefix (`packaging1` vs `foodbev1`).

The existing ModbusServer, OpcuaServer, and MqttPublisher already build their mappings dynamically from equipment signal configs. Adding F&B equipment requires no changes to protocol adapter code — only register map sizing and F&B-specific features (CDAB, multi-slave, int16_x10 IR entries).

### 2. CDAB Byte Order for Allen-Bradley Mixer

The mixer registers (HR 1000-1099) use CDAB byte order per PRD Section 3.1 / Appendix A. This means the two 16-bit words of a float32 are swapped compared to ABCD:

```
ABCD: register[0] = high word, register[1] = low word
CDAB: register[0] = low word,  register[1] = high word
```

Implementation: add `encode_float32_cdab()` and `decode_float32_cdab()` helper functions to `modbus_server.py`. The register map entries for mixer signals will specify `byte_order: "CDAB"` in their signal config. The sync function checks byte order per entry and calls the appropriate encoder.

The existing `HoldingRegisterEntry` dataclass needs a `byte_order` field (default `"ABCD"`).

### 3. Multi-Slave Modbus for Oven Eurotherm Controllers

PRD Section 3.1.6: Oven zones are addressed by Modbus slave ID (UID 11, 12, 13). Each slave has the same register layout (IR 0 = PV, IR 1 = SP, IR 2 = output power). This is separate from the default UID 1 that serves all other registers.

Implementation approach:
- Extend `ModbusServer` to support additional unit IDs
- Create secondary `ModbusDeviceContext` instances for UIDs 11, 12, 13
- Each secondary context has a small IR block (3 registers: PV, SP, output_power)
- The sync loop updates these secondary blocks alongside the main block
- Output power signals use int16 x10 encoding (0-1000 = 0.0-100.0%)

### 4. Per-Item Signal Generation for Filler

PRD Section 4.6: `filler.fill_weight` generates one value per simulated item arrival, not on every tick. Item arrival rate = `filler.line_speed` (packs/min).

Implementation: The filler generator tracks `_time_since_last_item`. On each tick, it checks if enough time has elapsed for the next item (interval = 60.0 / line_speed seconds). When an item arrives, it draws a new fill_weight from the Gaussian distribution. Between items, the last fill_weight is held.

`filler.fill_deviation` = `fill_weight - fill_target`, computed on each new item.

`qc.actual_weight` mirrors `fill_weight` with a small offset (tray + lid weight).

### 5. Shared Generator Coupling for F&B

The coder, environment, and energy generators are SHARED between profiles. They must work with both packaging and F&B without duplication.

**Coder**: Currently reads `press.machine_state` and `press.line_speed` from the store. For F&B, it needs to read `filler.state` and derive speed from `filler.line_speed`. Solution: make the coupling signal configurable via the equipment config:

```yaml
coder:
  type: "cij_printer"
  coupling:
    state_signal: "filler.state"
    speed_signal: "filler.line_speed"
    running_state: 2  # Filler Running
```

The coder generator reads the coupling signal from config (defaulting to `press.machine_state` / `press.line_speed` for backward compatibility).

**Environment**: Works identically for both profiles. The only difference is the comment in PRD 2b.10 about tighter acceptable ranges in food factories (12-18°C vs 15-35°C). This is a config difference, not a code difference. The F&B `factory-foodbev.yaml` will configure tighter min/max clamps.

**Energy**: Works identically. The F&B line has a different energy profile (refrigeration-heavy). This is modeled via different config values: higher base load, different correlation parent signal. The energy generator reads its parent signal from config (`press.line_speed` for packaging, `filler.line_speed` for F&B).

### 6. Thermal Diffusion Model for Product Core Temperature

PRD Section 4.2.10 defines a truncated Fourier series for heat conduction in a slab. Implementation creates a `ThermalDiffusionModel` class that:

1. Takes parameters: `T_initial`, `T_oven`, `alpha` (thermal diffusivity), `L` (half-thickness), `sigma`
2. Sums Fourier terms until `|T(0) - T_initial| < 1.0°C`
3. Resets when a new product enters the oven (belt_speed-dependent dwell time)
4. Tracks time since product entered oven

Typical ready meal: half-thickness 25mm, α=1.4e-7 m²/s, core reaches 72°C from 4°C in ~15-20 min at 180°C.

### 7. CIP Cycle as Timed Phase Sequence

The CIP system runs through 5 phases (Idle, Pre-rinse, Caustic, Intermediate rinse, Acid wash, Final rinse). Each phase has a defined duration, temperature profile, and conductivity profile. The CIP generator implements a state machine with timed transitions.

During CIP, upstream equipment enters defined states:
- Mixer: state → Idle (or CIP)
- Filler: state → Off or Starved
- Sealer: parameters hold at safe values
- Oven: at temperature but no product (state → Idle)
- Chiller: continues normally

### 8. Allergen Changeover

PRD Section 5.14.8 describes allergen changeover as a mandatory CIP cycle triggered by recipe transitions. This is implemented as part of the CIP scenario rather than a separate scenario class — the CIP cycle has a `mandatory` flag that, when true, cannot be deferred. The batch cycle scenario checks allergen status on recipe changes and triggers mandatory CIP when required.

For Phase 3, allergen changeover logic will be included in the batch cycle and CIP scenarios. If the combined complexity is too high for a single task, allergen logic can be deferred to a follow-up task within Phase 3.

---

## Task Breakdown

Phase 3 is broken into 25 tasks across 5 groups.

---

### Group A: Profile Infrastructure (Tasks 3.1-3.3)

**Task 3.1: F&B Equipment Config Models**

Add Pydantic config models for F&B-specific equipment parameters and scenario configs to `config.py`. Extend `ScenariosConfig` with F&B scenario config classes.

- **Create/modify**: `src/factory_simulator/config.py`
- **Test**: `tests/unit/test_config.py` (extend with F&B config validation)
- **What to add**:
  - `BatchCycleConfig` — frequency_per_shift, batch_duration_seconds
  - `OvenThermalExcursionConfig` — frequency_per_shift, duration_seconds, max_drift_c
  - `FillWeightDriftConfig` — frequency_per_shift, duration_seconds, drift_rate
  - `SealIntegrityFailureConfig` — frequency_per_week, duration_seconds
  - `ChillerDoorAlarmConfig` — frequency_per_week, duration_seconds
  - `CipCycleConfig` — frequency_per_day, cycle_duration_seconds
  - `ColdChainBreakConfig` — frequency_per_month, duration_seconds
  - Add these to `ScenariosConfig` (optional fields, None by default for packaging profile)
- **PRD refs**: `prd/05-scenario-system.md` (5.14.1-5.14.7), `prd/06-configuration.md`
- **Dependencies**: None
- **Estimated complexity**: ~150 lines + ~80 lines tests

**Task 3.2: F&B Factory Config (factory-foodbev.yaml)**

Create the F&B factory configuration file with all 9 equipment groups (6 new + 3 shared), 68 signals, protocol mappings, and F&B scenario configs.

- **Create**: `config/factory-foodbev.yaml`
- **Test**: `tests/unit/test_config.py` (add test that loads and validates F&B config)
- **What to define**:
  - Factory info with F&B name, site_id "demo"
  - MQTT line_id: "foodbev1"
  - Equipment: mixer (8 signals, CDAB byte order), oven (13 signals), filler (8 signals), sealer (6 signals), qc (6 signals), chiller (7 signals), cip (5 signals), coder (11 signals, coupling to filler), environment (2 signals), energy (2 signals)
  - All Modbus HR/IR/coil/DI addresses per Appendix A
  - All OPC-UA node paths per Appendix B
  - All MQTT topic paths per Appendix C
  - F&B scenario configs (all enabled with PRD-specified frequencies)
  - Shift config
- **PRD refs**: `prd/02b-factory-layout-food-and-beverage.md` (all equipment tables), `prd/appendix-a-modbus-register-map.md`, `prd/appendix-b-opcua-node-tree.md`, `prd/appendix-c-mqtt-topic-map.md`, `prd/06-configuration.md`
- **Dependencies**: 3.1
- **Estimated complexity**: ~500 lines YAML + ~30 lines test

**Task 3.3: Generator Registry for F&B Equipment**

Extend the `_GENERATOR_REGISTRY` in `data_engine.py` with F&B equipment type mappings. Add stub/placeholder imports that will be filled as generators are implemented.

- **Modify**: `src/factory_simulator/engine/data_engine.py`
- **Test**: `tests/unit/test_data_engine.py` (verify F&B types are registered)
- **What to add**:
  - Registry entries for: `"high_shear_mixer"`, `"tunnel_oven"`, `"gravimetric_filler"`, `"tray_sealer"`, `"checkweigher"`, `"cold_room"`, `"cip_skid"`
  - Import the generator classes (added in subsequent tasks)
- **PRD refs**: `prd/08-architecture.md` (8.4 Plugin Architecture)
- **Dependencies**: None (generator imports can be deferred to when each generator is created)
- **Note**: This task establishes the type strings. The actual generator classes are created in Group B. Initially register with the classes set to None or skip import until Group B. The simplest approach: add the registry entries in each Group B task when the generator is created. This task then just validates the pattern.
- **Estimated complexity**: ~30 lines

**Revised approach for 3.3**: Instead of a separate task, each Group B generator task will add its own registry entry. Task 3.3 is **removed** and merged into Group B tasks. This avoids circular import issues and keeps each task self-contained.

---

### Group B: F&B Equipment Generators (Tasks 3.3-3.10)

**Task 3.3: Thermal Diffusion Signal Model**

Implement the thermal diffusion model (PRD Section 4.2.10) as a reusable signal model class. This is needed by the oven generator for `product_core_temp`.

- **Create**: `src/factory_simulator/models/thermal_diffusion.py`
- **Test**: `tests/unit/test_models/test_thermal_diffusion.py`
- **What to implement**:
  - `ThermalDiffusionModel` class following the same pattern as `FirstOrderLagModel`
  - Constructor takes: `T_initial`, `T_oven`, `alpha` (thermal diffusivity), `L` (half-thickness), `sigma`
  - Fourier series summation with adaptive term count (sum until `|T(0) - T_initial| < 1.0°C`)
  - `generate(sim_time, dt)` returns current core temp based on time in oven
  - `reset(T_initial)` method for when new product enters
  - Property to read current value
- **Tests**:
  - Initial condition: T(0) ≈ T_initial (within 1°C)
  - Asymptotic: T(∞) → T_oven
  - Mid-point: reaches 72°C from 4°C at 180°C oven in ~15-20 min (with default params)
  - S-curve shape: slow start, fast middle, slow end
  - Reset produces correct re-initialization
  - Noise injection works
- **PRD refs**: `prd/04-data-generation-engine.md` (4.2.10)
- **Dependencies**: None
- **Estimated complexity**: ~150 lines + ~120 lines tests

**Task 3.4: Mixer Generator**

Implement the mixer equipment generator with 8 signals and a 6-state batch cycle state machine.

- **Create**: `src/factory_simulator/generators/mixer.py`
- **Test**: `tests/unit/test_generators/test_mixer.py`
- **Register**: Add `"high_shear_mixer": MixerGenerator` to `_GENERATOR_REGISTRY` in `data_engine.py`
- **Signals**: mixer.speed, mixer.torque, mixer.batch_temp, mixer.batch_weight, mixer.state, mixer.batch_id, mixer.mix_time_elapsed, mixer.lid_closed
- **State machine**: Off(0), Loading(1), Mixing(2), Holding(3), Discharging(4), CIP(5)
- **Behaviour**:
  - Loading: weight increases in steps, speed 0 or low (50-100 RPM)
  - Mixing: speed ramps to target (1000-2500 RPM), torque rises with viscosity/load, temp ramps
  - Holding: speed drops to 100-200 RPM, temp holds at setpoint
  - Discharging: weight decreases, speed low
  - Batch cycles: 15-45 min, 8-12 per shift
  - batch_id: string generator with date/line/sequence format
- **Models used**: state_machine, ramp, steady_state, correlated_follower, string_generator, counter
- **PRD refs**: `prd/02b-factory-layout-food-and-beverage.md` (2b.2), `prd/04-data-generation-engine.md` (4.6)
- **Dependencies**: 3.1, 3.2
- **Estimated complexity**: ~400 lines + ~200 lines tests

**Task 3.5: Oven Generator**

Implement the oven equipment generator with 13 signals including thermal diffusion for product core temp, PID-controlled zone temperatures, and correlated output power signals.

- **Create**: `src/factory_simulator/generators/oven.py`
- **Test**: `tests/unit/test_generators/test_oven.py`
- **Register**: Add `"tunnel_oven": OvenGenerator` to registry
- **Signals**: oven.zone_1/2/3_temp, oven.zone_1/2/3_setpoint, oven.belt_speed, oven.product_core_temp, oven.humidity_zone_2, oven.state, oven.zone_1/2/3_output_power
- **State machine**: Off(0), Preheat(1), Running(2), Idle(3), Cooldown(4)
- **Key features**:
  - Zone temps track setpoints via first_order_lag (tau 120-300s, damping_ratio ~0.5)
  - Zone thermal coupling via oven correlation matrix (PRD 4.3.1)
  - Product core temp via ThermalDiffusionModel (Task 3.3)
  - Output power = correlated follower of (setpoint - actual), clamped 0-100%
  - Belt speed determines dwell time: `tunnel_length / (belt_speed / 60)`
  - Product resets when dwell time expires (new product enters)
- **PRD refs**: `prd/02b-factory-layout-food-and-beverage.md` (2b.3), `prd/04-data-generation-engine.md` (4.2.3, 4.2.10, 4.3.1, 4.6)
- **Dependencies**: 3.3 (ThermalDiffusionModel), 3.1, 3.2
- **Estimated complexity**: ~450 lines + ~250 lines tests

**Task 3.6: Filler Generator**

Implement the filler equipment generator with 8 signals including per-item fill weight generation.

- **Create**: `src/factory_simulator/generators/filler.py`
- **Test**: `tests/unit/test_generators/test_filler.py`
- **Register**: Add `"gravimetric_filler": FillerGenerator` to registry
- **Signals**: filler.line_speed, filler.fill_weight, filler.fill_target, filler.fill_deviation, filler.packs_produced, filler.reject_count, filler.state, filler.hopper_level
- **State machine**: Off(0), Setup(1), Running(2), Starved(3), Fault(4)
- **Key features**:
  - fill_weight: per-item Gaussian (mean = target + giveaway, sigma = 2-4g)
  - fill_deviation = fill_weight - fill_target
  - packs_produced: counter incremented per item
  - reject_count: counter incremented when fill_weight outside tolerance
  - hopper_level: sawtooth (depletes, refills in batches)
  - Item arrival rate derived from line_speed (packs/min)
- **PRD refs**: `prd/02b-factory-layout-food-and-beverage.md` (2b.4), `prd/04-data-generation-engine.md` (4.6)
- **Dependencies**: 3.1, 3.2
- **Estimated complexity**: ~350 lines + ~200 lines tests

**Task 3.7: Sealer Generator**

Implement the sealer equipment generator with 6 signals.

- **Create**: `src/factory_simulator/generators/sealer.py`
- **Test**: `tests/unit/test_generators/test_sealer.py`
- **Register**: Add `"tray_sealer": SealerGenerator` to registry
- **Signals**: sealer.seal_temp, sealer.seal_pressure, sealer.seal_dwell, sealer.gas_co2_pct, sealer.gas_n2_pct, sealer.vacuum_level
- **Behaviour**:
  - seal_temp, seal_pressure, seal_dwell: steady_state models during production
  - gas_co2_pct + gas_n2_pct: steady_state, typically 30/70 split
  - vacuum_level: steady_state around -0.7 bar during production
  - All signals go to safe/zero values when filler is not Running
- **PRD refs**: `prd/02b-factory-layout-food-and-beverage.md` (2b.5)
- **Dependencies**: 3.1, 3.2
- **Estimated complexity**: ~200 lines + ~120 lines tests

**Task 3.8: Checkweigher (QC) Generator**

Implement the checkweigher/metal detection generator with 6 signals.

- **Create**: `src/factory_simulator/generators/checkweigher.py`
- **Test**: `tests/unit/test_generators/test_checkweigher.py`
- **Register**: Add `"checkweigher": CheckweigherGenerator` to registry
- **Signals**: qc.actual_weight, qc.overweight_count, qc.underweight_count, qc.metal_detect_trips, qc.throughput, qc.reject_total
- **Behaviour**:
  - actual_weight mirrors filler.fill_weight + tray/lid offset
  - overweight/underweight counts increment based on weight thresholds
  - metal_detect_trips: rare counter (< 1 per 1000 packs)
  - throughput mirrors filler.line_speed
  - reject_total = overweight + underweight + metal_detect
  - Per-item generation (triggered by filler item arrival)
- **PRD refs**: `prd/02b-factory-layout-food-and-beverage.md` (2b.6)
- **Dependencies**: 3.6 (reads filler signals from store)
- **Estimated complexity**: ~250 lines + ~150 lines tests

**Task 3.9: Chiller Generator**

Implement the refrigeration generator with 7 signals including bang-bang hysteresis compressor control.

- **Create**: `src/factory_simulator/generators/chiller.py`
- **Test**: `tests/unit/test_generators/test_chiller.py`
- **Register**: Add `"cold_room": ChillerGenerator` to registry
- **Signals**: chiller.room_temp, chiller.setpoint, chiller.compressor_state, chiller.suction_pressure, chiller.discharge_pressure, chiller.defrost_active, chiller.door_open
- **Behaviour**:
  - room_temp: bang_bang_hysteresis model (sawtooth around setpoint ± 1°C)
  - compressor_state: binary, driven by bang_bang threshold crossings
  - suction/discharge pressure: steady_state during operation, correlated with compressor state
  - defrost_active: periodic (2-4 per day, 15-30 min each)
  - door_open: normally false (scenarios trigger true)
- **PRD refs**: `prd/02b-factory-layout-food-and-beverage.md` (2b.7), `prd/04-data-generation-engine.md` (4.2.12)
- **Dependencies**: 3.1, 3.2
- **Estimated complexity**: ~300 lines + ~180 lines tests

**Task 3.10: CIP Generator**

Implement the CIP (Clean-in-Place) generator with 5 signals and a 6-state phase sequence.

- **Create**: `src/factory_simulator/generators/cip.py`
- **Test**: `tests/unit/test_generators/test_cip.py`
- **Register**: Add `"cip_skid": CipGenerator` to registry
- **Signals**: cip.state, cip.wash_temp, cip.flow_rate, cip.conductivity, cip.cycle_time_elapsed
- **State machine**: Idle(0), Pre-rinse(1), Caustic wash(2), Intermediate rinse(3), Acid wash(4), Final rinse(5)
- **Behaviour**:
  - Each phase has defined duration, temp profile, and conductivity profile
  - wash_temp: ramp to phase target, hold, ramp to next
  - flow_rate: target during active phases, 0 during idle
  - conductivity: rises during caustic (80-150 mS/cm), drops during rinse (first_order_lag toward 0)
  - cycle_time_elapsed: counter from cycle start
  - Total cycle: 40-60 minutes
  - Normally idle; CIP scenario triggers the cycle
- **PRD refs**: `prd/02b-factory-layout-food-and-beverage.md` (2b.8), `prd/04-data-generation-engine.md` (4.6)
- **Dependencies**: 3.1, 3.2
- **Estimated complexity**: ~350 lines + ~180 lines tests

---

### Group C: F&B Protocol Endpoints (Tasks 3.11-3.14)

**Task 3.11: Shared Generator Coupling for F&B**

Make the coder and energy generators configurable for their coupling signals so they work with both profiles.

- **Modify**: `src/factory_simulator/generators/coder.py`, `src/factory_simulator/generators/energy.py`
- **Test**: `tests/unit/test_generators/test_coder.py` (extend), `tests/unit/test_generators/test_energy.py` (extend)
- **What to change**:
  - Coder: read coupling config (`state_signal`, `speed_signal`, `running_state`) from equipment config extras
  - Default to `press.machine_state` / `press.line_speed` / `2` for backward compat
  - F&B config sets `filler.state` / `filler.line_speed` / `2`
  - Energy: read parent signal from config (default `press.line_speed`)
  - F&B config sets `filler.line_speed` as parent signal
- **Tests**: coder follows filler state when configured for F&B; energy follows filler speed
- **PRD refs**: `prd/02b-factory-layout-food-and-beverage.md` (2b.9, 2b.11)
- **Dependencies**: 3.6 (filler generator exists in store)
- **Estimated complexity**: ~80 lines changes + ~60 lines tests

**Task 3.12: F&B Modbus Register Map — CDAB Encoding and Block Sizing**

Extend the Modbus server to support CDAB byte order per-signal, larger data blocks for F&B addresses (HR 1000-1599), and the F&B coil/DI/IR mappings.

- **Modify**: `src/factory_simulator/protocols/modbus_server.py`
- **Test**: `tests/unit/test_protocols/test_modbus.py` (extend)
- **What to add**:
  - `encode_float32_cdab()` and `decode_float32_cdab()` functions
  - `encode_uint32_cdab()` and `decode_uint32_cdab()` functions
  - Add `byte_order` field to `HoldingRegisterEntry` (default "ABCD")
  - Dynamic block sizing: calculate HR/IR/coil/DI block sizes from actual register addresses in config (instead of hardcoded sizes)
  - `_sync_holding_registers()` checks `entry.byte_order` and uses CDAB/ABCD encoder accordingly
  - For writable registers, decode also respects byte order
  - Build F&B coils (100-102: mixer.lid_closed, chiller.compressor_state, chiller.defrost_active) and DI (100: chiller.door_open) from config or profile-aware build function
- **Tests**:
  - CDAB encode/decode round-trip
  - Mixer float32 at HR 1000 encoded as CDAB
  - Oven float32 at HR 1100 encoded as ABCD
  - F&B coils and DI derived correctly
  - Dynamic block sizing covers F&B addresses
- **PRD refs**: `prd/03-protocol-endpoints.md` (3.1), `prd/appendix-a-modbus-register-map.md`
- **Dependencies**: 3.2 (F&B config exists)
- **Estimated complexity**: ~200 lines + ~150 lines tests

**Task 3.13: F&B Modbus Multi-Slave — Oven Eurotherm UIDs**

Add multi-slave support to the Modbus server for oven zone controllers (UIDs 11, 12, 13).

- **Modify**: `src/factory_simulator/protocols/modbus_server.py`
- **Test**: `tests/unit/test_protocols/test_modbus.py` (extend)
- **What to add**:
  - `MultiSlaveContext` that maps unit IDs to different device contexts
  - UIDs 11, 12, 13 each with IR block: IR 0 = zone PV (int16 x10), IR 1 = zone SP (int16 x10), IR 2 = output power (int16 x10)
  - Sync loop updates multi-slave IR blocks from store
  - UID 1 continues to serve all main registers
  - Config-driven: multi-slave entries defined in factory-foodbev.yaml
- **Tests**:
  - Read UID 11 IR 0 returns zone_1_temp as int16 x10
  - Read UID 12 IR 2 returns zone_2_output_power as int16 x10
  - Read UID 1 returns main registers as before
  - Invalid UID returns exception
- **PRD refs**: `prd/03-protocol-endpoints.md` (3.1.6), `prd/appendix-a-modbus-register-map.md`
- **Dependencies**: 3.5 (oven generator produces zone signals), 3.12
- **Estimated complexity**: ~200 lines + ~120 lines tests

**Task 3.14: F&B OPC-UA Node Tree and MQTT Topics**

Verify that the existing OPC-UA server and MQTT publisher dynamically build correct F&B endpoints from the F&B config. No code changes should be needed — the protocol adapters already read from signal configs. This task validates that F&B config is correctly wired.

- **Create**: `tests/unit/test_protocols/test_opcua_fnb.py`, `tests/unit/test_protocols/test_mqtt_fnb.py`
- **What to test**:
  - OPC-UA: FoodBevLine node tree built correctly from F&B config (Mixer1, Oven1, Filler1, QC1, CIP1, Energy nodes with correct paths, types, EURange)
  - MQTT: topic map built with foodbev1 line_id, 13 topics (11 coder + 2 env), no vibration topics
  - All OPC-UA node paths match Appendix B FoodBevLine tree
  - All MQTT topics match Appendix C F&B section
- **PRD refs**: `prd/appendix-b-opcua-node-tree.md` (FoodBevLine), `prd/appendix-c-mqtt-topic-map.md` (F&B)
- **Dependencies**: 3.2 (F&B config), 3.4-3.10 (generators produce signals)
- **Estimated complexity**: ~200 lines tests

---

### Group D: F&B Scenarios (Tasks 3.15-3.22)

**Task 3.15: Batch Cycle Scenario (Mixer)**

Implement the batch cycle scenario that drives mixer state transitions through a complete batch sequence.

- **Create**: `src/factory_simulator/scenarios/batch_cycle.py`
- **Test**: `tests/unit/test_scenarios/test_batch_cycle.py`
- **Sequence** (PRD 5.14.1):
  1. Mixer → Loading: ingredient valves open, weight increases
  2. Mixer → Mixing: speed ramps to target, torque follows
  3. Mixer → Holding: speed drops, temp holds
  4. Mixer → Discharging: weight decreases
  5. Back to Loading for next batch
- **Parameters**: batch duration 20-45 min, batch count per shift 8-16, batch-to-batch variation
- **Ground truth**: log batch_start, batch_complete events
- **PRD refs**: `prd/05-scenario-system.md` (5.14.1)
- **Dependencies**: 3.4 (mixer generator)
- **Estimated complexity**: ~300 lines + ~180 lines tests

**Task 3.16: Oven Thermal Excursion Scenario**

Implement the oven thermal excursion scenario — analogous to packaging DryerDrift but at oven scale.

- **Create**: `src/factory_simulator/scenarios/oven_thermal_excursion.py`
- **Test**: `tests/unit/test_scenarios/test_oven_thermal_excursion.py`
- **Sequence** (PRD 5.14.2):
  1. One zone drifts from setpoint (0.1-0.3°C/min)
  2. Adjacent zones respond via thermal coupling (0.05 factor)
  3. Product core temp deviates
  4. Recovery after 30-90 min
- **PRD refs**: `prd/05-scenario-system.md` (5.14.2)
- **Dependencies**: 3.5 (oven generator)
- **Estimated complexity**: ~250 lines + ~150 lines tests

**Task 3.17: Fill Weight Drift Scenario**

Implement the fill weight drift scenario.

- **Create**: `src/factory_simulator/scenarios/fill_weight_drift.py`
- **Test**: `tests/unit/test_scenarios/test_fill_weight_drift.py`
- **Sequence** (PRD 5.14.3):
  1. fill_weight mean drifts from target at 0.05-0.2 g/min
  2. reject_count increases proportionally
  3. Recovery after 10-60 min
- **PRD refs**: `prd/05-scenario-system.md` (5.14.3)
- **Dependencies**: 3.6 (filler generator)
- **Estimated complexity**: ~200 lines + ~120 lines tests

**Task 3.18: Seal Integrity Failure Scenario**

Implement the seal integrity failure scenario.

- **Create**: `src/factory_simulator/scenarios/seal_integrity.py`
- **Test**: `tests/unit/test_scenarios/test_seal_integrity.py`
- **Sequence** (PRD 5.14.4):
  1. seal_temp drops below threshold
  2. seal_pressure decreases
  3. vacuum_level degrades
  4. qc.reject_total spikes
  5. Line stops for seal bar replacement
- **PRD refs**: `prd/05-scenario-system.md` (5.14.4)
- **Dependencies**: 3.7 (sealer generator), 3.8 (QC generator)
- **Estimated complexity**: ~250 lines + ~150 lines tests

**Task 3.19: Chiller Door Alarm Scenario**

Implement the chiller door alarm scenario.

- **Create**: `src/factory_simulator/scenarios/chiller_door_alarm.py`
- **Test**: `tests/unit/test_scenarios/test_chiller_door_alarm.py`
- **Sequence** (PRD 5.14.5):
  1. door_open → true
  2. room_temp rises at 0.5-2°C/min
  3. compressor cycles more frequently
  4. Door close → first_order_lag recovery to setpoint
- **PRD refs**: `prd/05-scenario-system.md` (5.14.5)
- **Dependencies**: 3.9 (chiller generator)
- **Estimated complexity**: ~200 lines + ~120 lines tests

**Task 3.20: CIP Cycle Scenario**

Implement the CIP cycle scenario that triggers the CIP generator through a complete wash sequence.

- **Create**: `src/factory_simulator/scenarios/cip_cycle.py`
- **Test**: `tests/unit/test_scenarios/test_cip_cycle.py`
- **Sequence** (PRD 5.14.6):
  1. Production stops. CIP state → Pre-Rinse
  2. Phases progress: Pre-rinse → Caustic → Rinse → Acid → Final rinse
  3. Upstream equipment enters idle/safe states
  4. CIP completes. Production resumes.
- **Duration**: 30-60 min per cycle, 1-3 per day
- **Production cascade**: mixer → Idle, filler → Off, sealer at safe values
- **Ground truth**: cip_start, cip_complete events
- **PRD refs**: `prd/05-scenario-system.md` (5.14.6)
- **Dependencies**: 3.10 (CIP generator), 3.4 (mixer), 3.6 (filler)
- **Estimated complexity**: ~300 lines + ~180 lines tests

**Task 3.21: Cold Chain Break Scenario**

Implement the cold chain break scenario.

- **Create**: `src/factory_simulator/scenarios/cold_chain_break.py`
- **Test**: `tests/unit/test_scenarios/test_cold_chain_break.py`
- **Sequence** (PRD 5.14.7):
  1. compressor_state locks to 0 (failure)
  2. room_temp rises from setpoint toward ambient (0.5-1.5°C/min)
  3. Crosses alarm threshold (8°C)
  4. After repair, compressor restarts, recovery via first_order_lag
- **Frequency**: Rare (1-2 per month)
- **Duration**: 30-120 min
- **PRD refs**: `prd/05-scenario-system.md` (5.14.7)
- **Dependencies**: 3.9 (chiller generator)
- **Estimated complexity**: ~200 lines + ~120 lines tests

**Task 3.22: F&B Scenario Auto-Scheduling**

Add scheduling methods for all F&B scenarios to the ScenarioEngine, following the same pattern as Phase 2.1 auto-scheduling.

- **Modify**: `src/factory_simulator/engine/scenario_engine.py`
- **Test**: `tests/unit/test_scenario_engine.py` (extend)
- **What to add**:
  - `_schedule_batch_cycles()` — frequency_per_shift
  - `_schedule_oven_excursions()` — frequency_per_shift
  - `_schedule_fill_weight_drifts()` — frequency_per_shift
  - `_schedule_seal_failures()` — frequency_per_week
  - `_schedule_chiller_door_alarms()` — frequency_per_week
  - `_schedule_cip_cycles()` — frequency_per_day
  - `_schedule_cold_chain_breaks()` — frequency_per_month
  - Call all from `_generate_timeline()` when F&B scenario configs are present
  - Update `_AFFECTED_SIGNALS` with all F&B scenario signal sets
- **Tests**: all F&B scenario types appear in auto-generated timeline; affected signals valid
- **PRD refs**: `prd/05-scenario-system.md` (5.14)
- **Dependencies**: 3.15-3.21 (all F&B scenarios), 3.1 (F&B config models)
- **Estimated complexity**: ~200 lines + ~100 lines tests

---

### Group E: Integration & Acceptance (Tasks 3.23-3.25)

**Task 3.23: F&B Modbus Integration Test**

End-to-end test: start DataEngine with F&B config, start Modbus server, connect pymodbus client, read all F&B registers.

- **Create**: `tests/integration/test_modbus_fnb_integration.py`
- **What to test**:
  - All F&B HR addresses (1000-1599) return valid float32 values
  - Mixer registers (1000-1011) encode as CDAB
  - Oven registers (1100-1125) encode as ABCD
  - F&B IR addresses (100-121) return valid int16 x10 values
  - Multi-slave UIDs 11-13 return oven zone data
  - F&B coils (100-102) return correct boolean states
  - F&B DI (100) returns chiller door state
  - Shared energy registers (600-603) still work
- **PRD refs**: Appendix A (full F&B register map)
- **Dependencies**: All Group B + Group C tasks
- **Estimated complexity**: ~250 lines tests

**Task 3.24: F&B OPC-UA and MQTT Integration Test**

End-to-end test: start DataEngine with F&B config, start OPC-UA server and MQTT publisher, connect clients.

- **Create**: `tests/integration/test_fnb_opcua_mqtt_integration.py`
- **What to test**:
  - OPC-UA: all FoodBevLine nodes accessible, correct types, values within range
  - MQTT: all 13 F&B topics publish (11 coder + 2 env), correct payloads
  - No vibration topics for F&B
  - Energy nodes at FoodBevLine.Energy path
- **PRD refs**: Appendix B (FoodBevLine), Appendix C (F&B topics)
- **Dependencies**: All Group B + Group C tasks
- **Estimated complexity**: ~200 lines tests

**Task 3.25: F&B Cross-Protocol Consistency Test**

Verify that the same signal read via Modbus, OPC-UA, and MQTT returns consistent values for F&B.

- **Create**: `tests/integration/test_fnb_cross_protocol.py`
- **What to test**:
  - Read signals served on multiple protocols (e.g., oven zone temps on Modbus HR + Modbus IR + OPC-UA State node for oven.state)
  - Verify consistency within one engine tick
  - Test CDAB encoding does not corrupt values visible on OPC-UA
  - Ground truth log records F&B scenario events
- **PRD refs**: Phase 3 exit criteria
- **Dependencies**: All previous tasks
- **Estimated complexity**: ~150 lines tests

---

## Task Summary

| ID | Name | Group | Dependencies | Complexity |
|----|------|-------|-------------|------------|
| 3.1 | F&B Equipment Config Models | A: Profile Infrastructure | — | ~230 lines |
| 3.2 | F&B Factory Config (YAML) | A: Profile Infrastructure | 3.1 | ~530 lines |
| 3.3 | Thermal Diffusion Signal Model | B: Equipment Generators | — | ~270 lines |
| 3.4 | Mixer Generator | B: Equipment Generators | 3.1, 3.2 | ~600 lines |
| 3.5 | Oven Generator | B: Equipment Generators | 3.3, 3.1, 3.2 | ~700 lines |
| 3.6 | Filler Generator | B: Equipment Generators | 3.1, 3.2 | ~550 lines |
| 3.7 | Sealer Generator | B: Equipment Generators | 3.1, 3.2 | ~320 lines |
| 3.8 | Checkweigher (QC) Generator | B: Equipment Generators | 3.6 | ~400 lines |
| 3.9 | Chiller Generator | B: Equipment Generators | 3.1, 3.2 | ~480 lines |
| 3.10 | CIP Generator | B: Equipment Generators | 3.1, 3.2 | ~530 lines |
| 3.11 | Shared Generator Coupling | C: Protocol Endpoints | 3.6 | ~140 lines |
| 3.12 | F&B Modbus — CDAB + Block Sizing | C: Protocol Endpoints | 3.2 | ~350 lines |
| 3.13 | F&B Modbus — Multi-Slave Eurotherm | C: Protocol Endpoints | 3.5, 3.12 | ~320 lines |
| 3.14 | F&B OPC-UA + MQTT Validation | C: Protocol Endpoints | 3.2, 3.4-3.10 | ~200 lines |
| 3.15 | Batch Cycle Scenario | D: Scenarios | 3.4 | ~480 lines |
| 3.16 | Oven Thermal Excursion Scenario | D: Scenarios | 3.5 | ~400 lines |
| 3.17 | Fill Weight Drift Scenario | D: Scenarios | 3.6 | ~320 lines |
| 3.18 | Seal Integrity Failure Scenario | D: Scenarios | 3.7, 3.8 | ~400 lines |
| 3.19 | Chiller Door Alarm Scenario | D: Scenarios | 3.9 | ~320 lines |
| 3.20 | CIP Cycle Scenario | D: Scenarios | 3.10, 3.4, 3.6 | ~480 lines |
| 3.21 | Cold Chain Break Scenario | D: Scenarios | 3.9 | ~320 lines |
| 3.22 | F&B Scenario Auto-Scheduling | D: Scenarios | 3.15-3.21, 3.1 | ~300 lines |
| 3.23 | F&B Modbus Integration Test | E: Integration | All B+C | ~250 lines |
| 3.24 | F&B OPC-UA + MQTT Integration Test | E: Integration | All B+C | ~200 lines |
| 3.25 | F&B Cross-Protocol Consistency Test | E: Integration | All | ~150 lines |

**Total: 25 tasks, ~8,260 estimated lines**

---

## Exit Criteria

From PRD Appendix F:

1. CollatrEdge connects to the F&B profile via all three protocols and collects 68 signals for 24 hours (simulated).
2. Mixer batch cycles complete in 20-45 minutes.
3. Oven zones show thermal coupling.
4. Fill weight follows Gaussian distribution around target.
5. All F&B scenario types fire.
6. All tests pass.

## Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| Thermal diffusion model numerical instability | Low | High | Test with extreme parameters; clamp output; verify convergence |
| CDAB encoding bugs corrupt mixer values | Medium | High | Round-trip encode/decode tests; integration test reads via pymodbus client |
| Multi-slave Modbus requires pymodbus API not previously used | Medium | Medium | Reference pymodbus multi-slave examples; test with real pymodbus client |
| Per-item filler generation creates timing edge cases | Medium | Medium | Test at various line speeds; test zero-speed case |
| CIP scenario cascading to other equipment creates complex interactions | Medium | Medium | Test cascade effects in isolation; test CIP + batch cycle overlap |
| Shared generator coupling breaks packaging profile | Low | High | Run ALL tests (packaging + F&B) after every change |
| F&B config file is 500+ lines and error-prone | High | Medium | Validate config programmatically; test that all 68 signals load |
| Scenario auto-scheduling requires F&B config detection in ScenarioEngine | Low | Low | Check for None on F&B scenario configs before scheduling |

## Notes for Implementation Agent

- **Follow existing patterns.** Every F&B generator follows the same `EquipmentGenerator` base class. Every F&B scenario follows the same `Scenario` base class. Look at `press.py` for the most complex generator reference. Look at `dryer_drift.py` for the closest scenario reference to oven thermal excursion.
- **Run ALL tests after every change.** This includes packaging tests. Shared code changes (coder coupling, Modbus block sizing) can break packaging.
- **The F&B config is large.** Take care with Modbus addresses, OPC-UA node paths, and MQTT topics. Cross-reference against Appendix A, B, C for every signal.
- **Commit format**: `phase-3: <what> (task 3.X)`
- **One task per session.** Complete one task, commit, output TASK_COMPLETE, stop.
