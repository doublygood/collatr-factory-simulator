# Phase 3 Code Review

## Summary

Phase 3 implements the F&B Chilled Ready Meal profile: 68 signals across 10
equipment groups, 7 new scenario types, shared generator coupling for coder and
energy, CDAB byte-order support for the Allen-Bradley mixer, multi-slave Modbus
UIDs 11-13 for Eurotherm oven controllers, and per-item gravimetric filler signal
generation. The implementation is correct and well-structured.

All 2059 tests pass clean (ruff + mypy clean) at review time.

Two YELLOW observations are noted (dead config parameter `coupling_running_state`
and an undocumented HR gap at 1008-1009). No RED issues were found.

---

## Checklist Results

### 1. All 68 F&B Signals in Store

**Status**: GREEN

Signal count verified against `config/factory-foodbev.yaml`:

| Equipment | Signals | Count |
|-----------|---------|-------|
| mixer | speed, torque, batch_temp, batch_weight, state, batch_id, mix_time_elapsed, lid_closed | 8 |
| oven | zone_1_temp, zone_2_temp, zone_3_temp, zone_1_setpoint, zone_2_setpoint, zone_3_setpoint, belt_speed, product_core_temp, humidity_zone_2, state, zone_1_output_power, zone_2_output_power, zone_3_output_power | 13 |
| filler | line_speed, fill_weight, fill_target, fill_deviation, packs_produced, reject_count, state, hopper_level | 8 |
| sealer | seal_temp, seal_pressure, seal_dwell, gas_co2_pct, gas_n2_pct, vacuum_level | 6 |
| qc | actual_weight, overweight_count, underweight_count, metal_detect_trips, throughput, reject_total | 6 |
| chiller | room_temp, setpoint, compressor_state, suction_pressure, discharge_pressure, defrost_active, door_open | 7 |
| cip | state, wash_temp, flow_rate, conductivity, cycle_time_elapsed | 5 |
| coder | state, prints_total, ink_level, printhead_temp, ink_pump_speed, ink_pressure, ink_viscosity_actual, supply_voltage, ink_consumption_ml, nozzle_health, gutter_fault | 11 |
| environment | ambient_temp, ambient_humidity | 2 |
| energy | line_power, cumulative_kwh | 2 |
| **Total** | | **68** |

Matches the PRD specification for Section 2b. All signal IDs follow the
`{equipment_id}.{signal_name}` convention consistently.

---

### 2. `_AFFECTED_SIGNALS` Match Store Keys

**Status**: GREEN

All 28 signal IDs in the 7 F&B entries of `_AFFECTED_SIGNALS`
(`scenario_engine.py:694–721`) were cross-checked against `factory-foodbev.yaml`.
Zero mismatches found.

- **BatchCycle** (8): mixer.state/speed/torque/batch_temp/batch_weight/batch_id/mix_time_elapsed/lid_closed ✓
- **OvenThermalExcursion** (4): oven.zone_1_temp/zone_2_temp/zone_3_temp/product_core_temp ✓
- **FillWeightDrift** (3): filler.fill_weight/fill_deviation/reject_count ✓
- **SealIntegrityFailure** (4): sealer.seal_temp/seal_pressure/vacuum_level, qc.reject_total ✓
- **ChillerDoorAlarm** (3): chiller.door_open/room_temp/compressor_state ✓
- **CipCycle** (7): cip.state/wash_temp/conductivity/flow_rate/cycle_time_elapsed, mixer.state, filler.state ✓
- **ColdChainBreak** (2): chiller.compressor_state/room_temp ✓

Note: `ShiftChange._AFFECTED_SIGNALS` lists `press.machine_state` and
`press.line_speed`. These signals don't exist in the F&B store. However,
`ShiftChange._on_activate` calls `_find_press()` which returns `None` when
no `PressGenerator` is found, then immediately calls `self.complete()`. This
is safe — the metadata in the ground truth log is slightly misleading but no
functional impact results.

---

### 3. CDAB Encoding Round-Trip

**Status**: GREEN

`encode_float32_cdab` / `decode_float32_cdab` and `encode_uint32_cdab` /
`decode_uint32_cdab` in `modbus_server.py:96–133` are mathematically correct.

**Logic (verified)**:
```
encode_float32_cdab(v):
  packed = struct.pack(">f", v)     # IEEE-754 big-endian bytes [A B C D]
  high = bytes[0:2] = AB            # high word
  low  = bytes[2:4] = CD            # low word
  return (low, high)                # register[0]=CD, register[1]=AB  ← CDAB

decode_float32_cdab([r0, r1]):      # r0=low=CD, r1=high=AB
  raw = struct.pack(">HH", r1, r0)  # reassemble as [AB CD]
  return struct.unpack(">f", raw)   # big-endian decode → correct ✓
```

`encode_uint32_cdab(v)` → `(low, high)` where `low = v & 0xFFFF`, `high = (v >> 16) & 0xFFFF`.
`decode_uint32_cdab([r0, r1])` → `(r1 << 16) | r0` → correct.

All 7 mixer HR signals (HR 1000-1011 except the two OPC-UA/coil-only signals)
have `modbus_byte_order: "CDAB"` in the YAML config. The register map builder
reads `sig_cfg.modbus_byte_order` per-signal and stores it in
`HoldingRegisterEntry.byte_order`. The sync and decode paths branch on this
field. Integration test `TestMixerHoldingRegistersCdab` (6 tests) explicitly
confirms CDAB decode gives the injected value while ABCD decode does not.

`mix_time_elapsed` uses `modbus_type: "uint32"` + `modbus_byte_order: "CDAB"` at
HR 1010-1011. The `encode_uint32_cdab` / `decode_uint32_cdab` functions handle
this correctly.

---

### 4. Multi-Slave UIDs 11-13

**Status**: GREEN

Each Eurotherm zone controller exposes IR 0 (PV), IR 1 (SP), IR 2 (output power):

| UID | IR 0 (PV) | IR 1 (SP) | IR 2 (output_power) |
|-----|-----------|-----------|---------------------|
| 11 | oven.zone_1_temp | oven.zone_1_setpoint | oven.zone_1_output_power |
| 12 | oven.zone_2_temp | oven.zone_2_setpoint | oven.zone_2_output_power |
| 13 | oven.zone_3_temp | oven.zone_3_setpoint | oven.zone_3_output_power |

Config routing is correct:
- `zone_X_temp` / `zone_X_setpoint` have both `modbus_slave_id` + `modbus_slave_ir`
  → appear in **both** main UID-1 IR block and secondary slave IR block.
- `zone_X_output_power` has `modbus_slave_id` + no `modbus_slave_ir`
  → excluded from main UID-1 IR (guard at `build_register_map:324`), exclusive
  to secondary slave using `modbus_ir` as the slave address.

All secondary slave IR entries use `data_type="int16_x10"`. `encode_int16_x10`
handles negative temperatures and two's-complement correctly.

`ModbusServer.start()` uses `single=False` multi-slave mode when secondary
slave contexts exist, routing by unit ID. Secondary contexts have stub
HR/coil/DI (secondary controllers serve IR only).

Integration test `TestMultiSlaveOvenControllers` (10 tests) exercises all three
UIDs and all three IR addresses.

---

### 5. Per-Item Filler Signal Generation

**Status**: GREEN

`FillerGenerator.generate()` (`filler.py:333-377`) correctly implements per-item
generation:

1. **Interval**: `item_interval = 60.0 / line_speed` seconds ✓
2. **Gating**: fill_weight drawn from Gaussian only when
   `_time_since_last_item >= item_interval`; between arrivals `_last_fill_weight`
   is returned unchanged ✓
3. **Remainder carry**: `_time_since_last_item -= item_interval` (not reset to 0)
   prevents accumulation errors at high speeds ✓
4. **Reject logic**: `|fill_weight - fill_target| > fill_tolerance` ✓
5. **Hopper depletion**: `hopper_model.set_speed(line_speed / 60.0)` ✓
6. **State guard**: per-item logic only when `is_running and line_speed > 0.0` ✓
7. **No wall clock**: all timing via `dt`. Rule 6 compliant ✓

`CheckweigherGenerator` mirrors the same pattern, tracking item arrivals
independently and reading `filler.fill_weight` from store on each arrival.

---

### 6. Shared Generator Coupling

**Status**: YELLOW

Both generators are correctly refactored for configurable coupling:

**CoderGenerator** (`coder.py:101-107`) reads from `EquipmentConfig.model_extra`:
```python
self._state_signal = str(extras.get("coupling_state_signal", "press.machine_state"))
self._speed_signal  = str(extras.get("coupling_speed_signal", "press.line_speed"))
```
F&B config sets `coupling_state_signal: filler.state` and
`coupling_speed_signal: filler.line_speed`. Packaging defaults apply when
these keys are absent. 20 coupling tests confirm cross-isolation.

**EnergyGenerator** reads `coupling_speed_signal` (default `"press.line_speed"`).
F&B config sets it to `"filler.line_speed"`. Correct and clean.

**YELLOW — `coupling_running_state` is a dead config parameter:**

The F&B YAML sets `coupling_running_state: 2` on the coder, but
`CoderGenerator.__init__` never reads this from `model_extra`. The
`_update_conditions_from_press` method hardcodes `press_state == 2` as
the "running" check.

This works accidentally because both press (Running=2) and filler (Running=2)
share the same state ordinal. The dead parameter creates a false impression of
configurability but causes no functional defect in the current implementation.

---

### 7. F&B Scenario Auto-Scheduling

**Status**: GREEN

All 7 scheduling methods are present in `ScenarioEngine._generate_timeline()`:

| Method | Frequency | Guard |
|--------|-----------|-------|
| `_schedule_batch_cycles` | per shift | `if cfg is None or not cfg.enabled` |
| `_schedule_oven_thermal_excursions` | per shift | same |
| `_schedule_fill_weight_drifts` | per shift | same |
| `_schedule_seal_integrity_failures` | per week | same |
| `_schedule_chiller_door_alarms` | per week | same |
| `_schedule_cip_cycles` | per day | same |
| `_schedule_cold_chain_breaks` | per month | same |

All F&B config fields in `ScenariosConfig` are `Optional` (default `None`)
→ guards prevent scheduling when packaging config is loaded → full
backward compatibility.

`TestFnbAutoSchedulingIntegration` confirms all 7 types appear in a 1-month F&B
sim and none appear in a packaging sim.

---

### 8. No Packaging Regressions

**Status**: GREEN

Full CI pipeline: `ruff check src tests && mypy src && pytest`
→ **2059 passed, 0 failed, 1 warning** (unregistered `pytest.mark.unit` mark
from `superclaude` plugin — pre-existing, not a project issue).

---

## Issues Found

### RED — Must Fix

None.

---

### YELLOW — Should Fix

**Y-1: `coupling_running_state` config parameter is not read by `CoderGenerator`**

File: `src/factory_simulator/generators/coder.py:345`
Config: `config/factory-foodbev.yaml` coder block

The F&B config declares `coupling_running_state: 2`. The config model test
verifies it lands in `model_extra`. But `CoderGenerator.__init__` does not read
it; instead `_update_conditions_from_press` hardcodes `press_state == 2`.

Behavior is accidentally correct (both press Running=2 and filler Running=2).
The dead parameter is misleading.

**Recommended fix**: Read it in `__init__`:
```python
self._running_state: int = int(extras.get("coupling_running_state", 2))
```
and use `press_state == self._running_state` in `_update_conditions_from_press`.
Or remove the field from the YAML and add a comment documenting the assumption.

---

**Y-2: Undocumented register address gap HR 1008-1009 in mixer block**

File: `config/factory-foodbev.yaml` mixer signals

The mixer HR block jumps from 1006-1007 (batch_weight) to 1010-1011
(mix_time_elapsed), leaving HR 1008-1009 unallocated. This causes no functional
issue (dynamic block sizing handles it; those registers return 0x0000). But it
is undocumented — likely reserved for future signals.

**Recommended fix**: Add a YAML comment noting 1008-1009 are reserved.

---

### GREEN — Notes

- **CDAB isolation**: the byte-order branching is cleanly contained in
  `_sync_holding_registers` and `_decode_hr_value`. No other code paths are
  affected.

- **Multi-slave packaging isolation**: `if self._secondary_contexts:` in
  `ModbusServer.start()` uses `single=True` for packaging (no secondary slaves
  built at all). Zero packaging test changes required.

- **F&B scenario graceful degradation**: all `_on_activate` hooks call
  `self.complete()` immediately when the required generator is absent, preventing
  cross-profile errors.

- **Per-item remainder carry** (`_time_since_last_item -= item_interval`) is the
  correct accumulator pattern and prevents item-count drift at high line speeds.

- **ScenariosConfig Optional fields**: F&B fields default to `None` so the
  packaging config loads without error and all schedule methods short-circuit
  cleanly on `cfg is None`.

- **Pre-existing limitation (Y3, documented in progress)**: `DataEngine` does
  not pass `sim_duration_s` to `ScenarioEngine` — defaults to 8 hours. Rare
  F&B scenarios (cold_chain_break: 1-2/month) may not appear in short sims.
  This is pre-existing from Phase 1 and is NOT a Phase 3 defect.

---

## Verdict

**PASS**

No RED issues. Two YELLOW observations (both low priority, no functional impact
in the current implementation). All 8 exit criteria are met. Phase 3 is ready
for `PHASE_COMPLETE`.
