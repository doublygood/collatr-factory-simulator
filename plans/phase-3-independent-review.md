# Phase 3 Independent Code Review

**Reviewer:** Main session agent (independent)
**Date:** 2026-03-03
**Scope:** F&B Chilled Ready Meal Profile (Phase 3)
**Method:** Full PRD cross-reference + source code audit

---

## 1. Executive Summary

Phase 3 implements the complete F&B Chilled Ready Meal profile: 68 signals across 10 equipment groups (6 new, 3 shared, 1 new thermal model), 7 new scenario types, CDAB byte-order for Allen-Bradley mixer, multi-slave Modbus UIDs 11-13 for Eurotherm oven controllers, and per-item gravimetric filler generation. The implementation is thorough, well-structured, and correct against the PRD.

**Verdict: GO**

No RED issues. Two YELLOW issues identified (same two the local agent found). No issues missed by the local agent's review.

---

## 2. Local Agent Self-Review Grade

### Grade: **A**

The local agent's self-review (`phase-3-review.md`) is excellent. It:

- Correctly identified all 8 exit criteria and verified each one
- Found both YELLOW issues (dead `coupling_running_state` parameter, undocumented HR 1008-1009 gap)
- Provided precise file/line references for every finding
- Included correct CDAB encoding verification with worked byte-level proof
- Correctly analysed the multi-slave routing logic (exclusive vs dual-mapped IR signals)
- Noted the pre-existing `sim_duration_s` limitation without misattributing it to Phase 3
- Correctly identified `ShiftChange._AFFECTED_SIGNALS` as functionally harmless for F&B
- Verified 2,059 tests passing with clean ruff + mypy

The only minor improvement would be explicitly cross-referencing every register address against Appendix A (which the review implies but doesn't enumerate). This is a stylistic nit — the actual verification was clearly done.

---

## 3. Independent Verification Results

### 3.1 Signal Count and Identity (68 signals)

**Status: GREEN** ✓

Verified by counting `model:` entries in `factory-foodbev.yaml`: exactly 68.

Cross-referenced against PRD 2b.14 signal summary:

| Equipment | Count | Matches PRD |
|-----------|-------|-------------|
| mixer | 8 | ✓ (speed, torque, batch_temp, batch_weight, state, batch_id, mix_time_elapsed, lid_closed) |
| oven | 13 | ✓ (3 zone temps, 3 setpoints, belt_speed, product_core_temp, humidity_zone_2, state, 3 output_power) |
| filler | 8 | ✓ (line_speed, fill_weight, fill_target, fill_deviation, packs_produced, reject_count, state, hopper_level) |
| sealer | 6 | ✓ |
| qc | 6 | ✓ |
| chiller | 7 | ✓ |
| cip | 5 | ✓ |
| coder | 11 | ✓ (shared) |
| environment | 2 | ✓ (shared) |
| energy | 2 | ✓ (shared) |
| **Total** | **68** | ✓ |

Protocol distribution matches PRD 2b.14:
- Modbus HR: 31 signals (mixer 5 + oven 9 + filler 1 + sealer 6 + chiller 4 + cip 4 + energy 2) ✓
- OPC-UA: 17 signals (mixer 2 + oven 1 + filler 7 + qc 6 + cip 1) ✓
- MQTT: 13 signals (coder 11 + env 2) ✓
- Modbus IR multi-slave: 3 (oven output_power) ✓
- Modbus coils/DI: 4 (mixer.lid_closed, chiller.compressor_state, chiller.defrost_active, chiller.door_open) ✓

### 3.2 Register Address Verification (Appendix A)

**Status: GREEN** ✓

Every Modbus address in `factory-foodbev.yaml` was cross-referenced against PRD Appendix A. Zero mismatches:

**Holding Registers:**

| PRD Address | PRD Signal | YAML Match |
|-------------|-----------|------------|
| HR 1000-1001 | mixer.speed (CDAB) | ✓ |
| HR 1002-1003 | mixer.torque (CDAB) | ✓ |
| HR 1004-1005 | mixer.batch_temp (CDAB) | ✓ |
| HR 1006-1007 | mixer.batch_weight (CDAB) | ✓ |
| HR 1010-1011 | mixer.mix_time_elapsed (CDAB) | ✓ |
| HR 1100-1101 | oven.zone_1_temp (ABCD) | ✓ |
| HR 1102-1103 | oven.zone_2_temp (ABCD) | ✓ |
| HR 1104-1105 | oven.zone_3_temp (ABCD) | ✓ |
| HR 1110-1111 | oven.zone_1_setpoint (ABCD, writable) | ✓ |
| HR 1112-1113 | oven.zone_2_setpoint (ABCD, writable) | ✓ |
| HR 1114-1115 | oven.zone_3_setpoint (ABCD, writable) | ✓ |
| HR 1120-1121 | oven.belt_speed (ABCD) | ✓ |
| HR 1122-1123 | oven.product_core_temp (ABCD) | ✓ |
| HR 1124-1125 | oven.humidity_zone_2 (ABCD) | ✓ |
| HR 1200-1201 | filler.hopper_level (ABCD) | ✓ |
| HR 1300-1301 | sealer.seal_temp (ABCD) | ✓ |
| HR 1302-1303 | sealer.seal_pressure (ABCD) | ✓ |
| HR 1304-1305 | sealer.seal_dwell (ABCD) | ✓ |
| HR 1306-1307 | sealer.gas_co2_pct (ABCD) | ✓ |
| HR 1308-1309 | sealer.gas_n2_pct (ABCD) | ✓ |
| HR 1310-1311 | sealer.vacuum_level (ABCD) | ✓ |
| HR 1400-1401 | chiller.room_temp (ABCD) | ✓ |
| HR 1402-1403 | chiller.setpoint (ABCD, writable) | ✓ |
| HR 1404-1405 | chiller.suction_pressure (ABCD) | ✓ |
| HR 1406-1407 | chiller.discharge_pressure (ABCD) | ✓ |
| HR 1500-1501 | cip.wash_temp (ABCD) | ✓ |
| HR 1502-1503 | cip.flow_rate (ABCD) | ✓ |
| HR 1504-1505 | cip.conductivity (ABCD) | ✓ |
| HR 1506-1507 | cip.cycle_time_elapsed (ABCD, uint32) | ✓ |
| HR 600-601 | energy.line_power (shared, ABCD) | ✓ |
| HR 602-603 | energy.cumulative_kwh (shared, ABCD) | ✓ |

**Input Registers:**

| PRD Address | PRD Signal | YAML Match |
|-------------|-----------|------------|
| IR 100 | oven.zone_1_temp (int16 x10) | ✓ |
| IR 101 | oven.zone_2_temp | ✓ |
| IR 102 | oven.zone_3_temp | ✓ |
| IR 103 | oven.zone_1_setpoint | ✓ |
| IR 104 | oven.zone_2_setpoint | ✓ |
| IR 105 | oven.zone_3_setpoint | ✓ |
| IR 106 | oven.product_core_temp | ✓ |
| IR 110 | chiller.room_temp | ✓ |
| IR 111 | chiller.setpoint | ✓ |
| IR 115 | cip.wash_temp | ✓ |
| IR 120-121 | energy.line_power (float32) | ✓ |

**Multi-slave (PRD 3.1.6):**

| UID | IR 0 (PV) | IR 1 (SP) | IR 2 (Power) |
|-----|-----------|-----------|--------------|
| 11 | oven.zone_1_temp ✓ | oven.zone_1_setpoint ✓ | oven.zone_1_output_power ✓ |
| 12 | oven.zone_2_temp ✓ | oven.zone_2_setpoint ✓ | oven.zone_2_output_power ✓ |
| 13 | oven.zone_3_temp ✓ | oven.zone_3_setpoint ✓ | oven.zone_3_output_power ✓ |

**Coils:** 100 (mixer.lid_closed) ✓, 101 (chiller.compressor_state) ✓, 102 (chiller.defrost_active) ✓
**Discrete Inputs:** 100 (chiller.door_open) ✓

### 3.3 OPC-UA Node Verification (Appendix B)

**Status: GREEN** ✓

All 19 OPC-UA node paths in the YAML match Appendix B exactly:

- `FoodBevLine.Mixer1.State` (UInt16) ✓
- `FoodBevLine.Mixer1.BatchId` (String) ✓
- `FoodBevLine.Oven1.State` (UInt16) ✓
- `FoodBevLine.Filler1.LineSpeed` (Double) ✓
- `FoodBevLine.Filler1.FillWeight` (Double) ✓
- `FoodBevLine.Filler1.FillTarget` (Double) ✓
- `FoodBevLine.Filler1.FillDeviation` (Double) ✓
- `FoodBevLine.Filler1.PacksProduced` (UInt32) ✓
- `FoodBevLine.Filler1.RejectCount` (UInt32) ✓
- `FoodBevLine.Filler1.State` (UInt16) ✓
- `FoodBevLine.QC1.ActualWeight` (Double) ✓
- `FoodBevLine.QC1.OverweightCount` (UInt32) ✓
- `FoodBevLine.QC1.UnderweightCount` (UInt32) ✓
- `FoodBevLine.QC1.MetalDetectTrips` (UInt32) ✓
- `FoodBevLine.QC1.Throughput` (Double) ✓
- `FoodBevLine.QC1.RejectTotal` (UInt32) ✓
- `FoodBevLine.CIP1.State` (UInt16) ✓
- `FoodBevLine.Energy.LinePower` (Double) ✓
- `FoodBevLine.Energy.CumulativeKwh` (Double) ✓

Data types match PRD Appendix B for every node.

### 3.4 MQTT Topic Verification (Appendix C)

**Status: GREEN** ✓

All 13 MQTT topic suffixes in the YAML match Appendix C:

- coder/state, coder/prints_total, coder/ink_level, coder/printhead_temp, coder/ink_pump_speed, coder/ink_pressure, coder/ink_viscosity_actual, coder/supply_voltage, coder/ink_consumption_ml, coder/nozzle_health, coder/gutter_fault (11) ✓
- env/ambient_temp, env/ambient_humidity (2) ✓

F&B profile correctly uses `foodbev1/` prefix path (configured at factory level, not per-signal). No vibration topics present for F&B, matching PRD.

### 3.5 CDAB Byte-Order Encoding

**Status: GREEN** ✓

Independently verified `encode_float32_cdab` / `decode_float32_cdab` logic:
- Encode: `struct.pack(">f", v)` → `[A,B,C,D]` → returns `(CD, AB)` = CDAB register order ✓
- Decode: inputs `(r0=CD, r1=AB)` → `struct.pack(">HH", r1, r0)` = `[A,B,C,D]` → `struct.unpack(">f")` ✓

Round-trip correctness is mathematically sound. The `_sync_holding_registers` method branches on `entry.byte_order` per-signal. Mixer signals have `modbus_byte_order: "CDAB"` in the YAML. All other F&B equipment uses default ABCD.

### 3.6 Multi-Slave Modbus Architecture

**Status: GREEN** ✓

The multi-slave implementation is clean:

1. `build_register_map()` correctly handles dual-mapped signals (zone temps/setpoints appear in BOTH main UID-1 IR block via `modbus_ir` AND secondary slave block via `modbus_slave_ir`)
2. Output power signals (with `modbus_slave_id` but no `modbus_slave_ir`) are correctly excluded from the main IR block
3. `ModbusServer.__init__()` creates `FactoryDeviceContext` per secondary slave with stub HR/coil/DI blocks (secondary slaves serve IR only)
4. `ModbusServer.start()` uses `ModbusServerContext(single=False)` for F&B (multi-slave) and `single=True` for packaging (no secondary slaves) — clean isolation
5. `_sync_secondary_slaves()` correctly encodes values as `int16_x10` to secondary IR blocks

### 3.7 Per-Item Filler Generation (PRD 4.6)

**Status: GREEN** ✓

`FillerGenerator.generate()` correctly implements per-item generation:
- Item arrivals gated by `60.0 / line_speed` interval
- Gaussian fill weight: `N(fill_target + fill_giveaway, fill_sigma)` per PRD
- Remainder carry: `_time_since_last_item -= item_interval` (prevents drift)
- Reject logic: deviation exceeds tolerance → increment reject_count
- Hopper sawtooth depletion with auto-refill
- CheckweigherGenerator mirrors the per-item pattern, reading `filler.fill_weight` from store

### 3.8 Scenario Frequency/Duration Verification (PRD 5.14)

**Status: GREEN** ✓

All 7 scenario config defaults match PRD 5.14 specifications:

| Scenario | PRD Frequency | Code Default | PRD Duration | Code Default |
|----------|--------------|--------------|-------------|--------------|
| Batch Cycle | 8-16/shift | `[8,16]` ✓ | 20-45 min | `[1200,2700]` ✓ |
| Oven Thermal | 1-2/shift | `[1,2]` ✓ | 30-90 min | `[1800,5400]` ✓ |
| Fill Weight Drift | 1-3/shift | `[1,3]` ✓ | 10-60 min | `[600,3600]` ✓ |
| Seal Integrity | 1-2/week | `[1,2]` ✓ | 5-30 min | `[300,1800]` ✓ |
| Chiller Door | 1-3/week | `[1,3]` ✓ | 5-20 min | `[300,1200]` ✓ |
| CIP Cycle | 1-3/day | `[1,3]` ✓ | 30-60 min | `[1800,3600]` ✓ |
| Cold Chain | 1-2/month | `[1,2]` ✓ | 30-120 min | `[1800,7200]` ✓ |

### 3.9 Shared Generator Coupling

**Status: GREEN** ✓

Both shared generators (coder, energy) correctly read coupling signals from `model_extra`:

- **CoderGenerator:** reads `coupling_state_signal` (default `press.machine_state`) and `coupling_speed_signal` (default `press.line_speed`). F&B config sets `filler.state` and `filler.line_speed`.
- **EnergyGenerator:** reads `coupling_speed_signal` (default `press.line_speed`). F&B config sets `filler.line_speed`.
- Energy base load is 25.0 kW for F&B (vs 10.0 for packaging) — accounts for refrigeration.

### 3.10 Generator Registration

**Status: GREEN** ✓

All 7 new generator types are registered in `data_engine.py` `_GENERATOR_TYPES`:

```
"high_shear_mixer" → MixerGenerator
"tunnel_oven" → OvenGenerator
"gravimetric_filler" → FillerGenerator
"tray_sealer" → SealerGenerator
"checkweigher" → CheckweigherGenerator
"cold_room" → ChillerGenerator
"cip_skid" → CipGenerator
```

These type strings match the `type:` field in each equipment block of `factory-foodbev.yaml`.

### 3.11 State Machine Enums

**Status: GREEN** ✓

All state machine enums in generators match PRD:

| Equipment | PRD States | Code States |
|-----------|-----------|-------------|
| mixer | Off/Loading/Mixing/Holding/Discharging/CIP | ✓ (6 states) |
| oven | Off/Preheat/Running/Idle/Cooldown | ✓ (5 states) |
| filler | Off/Setup/Running/Idle/Fault | ✓ (5 states) |
| cip | Idle/Pre-rinse/Caustic/Intermediate/Acid/Final rinse | ✓ (6 states) |

### 3.12 Wall-Clock Compliance (CLAUDE.md Rule 6)

**Status: GREEN** ✓

Grep for `time.time()`, `datetime.now`, `datetime.utcnow`, `time.monotonic` across all 7 generators and 7 scenarios: zero matches. All timing uses `sim_time` and `dt` parameters.

### 3.13 Ground Truth Logging

**Status: GREEN** ✓

All 7 scenarios access `engine.ground_truth` for logging scenario activations and completions. This enables the LLM agent evaluation use case (PRD requirement for anomaly detection ground truth).

### 3.14 Packaging Regression

**Status: GREEN** ✓

Local agent reports 2,059 tests passing, ruff + mypy clean. Packaging scenarios correctly disabled in F&B config. F&B scenario config fields are `Optional` (default `None`) in `ScenariosConfig`, ensuring packaging config loads without error. Multi-slave mode activates only when secondary slave contexts exist (packaging has none → `single=True`).

---

## 4. Issues Found

### RED — Must Fix

**None.**

### YELLOW — Should Fix

**Y-1: Dead `coupling_running_state` config parameter** (agrees with local review)

- File: `config/factory-foodbev.yaml:821`
- The YAML sets `coupling_running_state: 2` on coder, but `CoderGenerator.__init__` never reads it
- `_update_conditions_from_press` hardcodes `press_state == 2` as the running check
- Works accidentally because both press (Running=2) and filler (Running=2) share the same ordinal
- **Risk:** Low. If a future equipment type uses a different ordinal for "Running", this would silently break.
- **Fix:** Read from `model_extra` and use `press_state == self._running_state`, or remove from YAML with a comment.

**Y-2: Undocumented HR 1008-1009 gap in mixer register block** (agrees with local review)

- File: `config/factory-foodbev.yaml` mixer signals
- HR block jumps from 1006-1007 (batch_weight) to 1010-1011 (mix_time_elapsed)
- Those addresses return 0x0000 (dynamic block sizing handles it)
- PRD Appendix A also shows this gap (no signal at 1008-1009), so the implementation matches the spec
- **Risk:** None functional. Minor documentation clarity issue.
- **Fix:** Add YAML comment noting 1008-1009 are reserved/unused.

### GREEN — Observations

- **CDAB isolation is clean:** byte-order branching is entirely contained in `_sync_holding_registers` and `_decode_hr_value`. No leakage to other code paths.
- **Multi-slave packaging isolation is clean:** `single=True` for packaging, `single=False` for F&B. Zero packaging test changes required.
- **F&B scenario graceful degradation:** all `_on_activate` hooks call `self.complete()` when the required generator is absent, preventing cross-profile errors.
- **Per-item remainder carry** is the correct accumulator pattern — avoids item count drift at high line speeds.
- **Pre-existing limitation (not Phase 3):** `DataEngine` defaults `sim_duration_s` to 8 hours. Rare F&B scenarios (cold_chain_break: 1-2/month) may not trigger in short simulations. This is a Phase 1 issue.

---

## 5. Verdict

| Criterion | Result |
|-----------|--------|
| 68 F&B signals present and correct | ✅ |
| All register addresses match PRD Appendix A | ✅ |
| All OPC-UA nodes match PRD Appendix B | ✅ |
| All MQTT topics match PRD Appendix C | ✅ |
| CDAB encoding correct | ✅ |
| Multi-slave UIDs 11-13 correct | ✅ |
| Per-item filler generation correct | ✅ |
| 7 scenario types with correct frequencies | ✅ |
| Shared generator coupling correct | ✅ |
| No wall-clock usage (Rule 6) | ✅ |
| No packaging regressions | ✅ |
| Ground truth logging present | ✅ |

### **GO**

Phase 3 is complete and correct. Zero RED issues. Two YELLOW issues are cosmetic/documentation-level with no functional impact. The implementation faithfully follows the PRD across all three protocol appendices, all 10 equipment groups, and all 7 scenario types.

### Local Agent Self-Review Grade: **A**

The self-review was thorough, accurate, and identified the same two YELLOW issues found independently. No issues were missed. The review demonstrated clear understanding of the codebase architecture and PRD requirements.
