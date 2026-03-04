# Protocol Fidelity Review — Collatr Factory Simulator

**Reviewer:** Industrial Protocol Fidelity Engineer (automated deep-dive)
**Date:** 2026-03-04
**Scope:** Modbus TCP, OPC-UA, MQTT — PRD vs implementation alignment
**Files reviewed:**
- `prd/appendix-a-modbus-register-map.md`
- `prd/appendix-b-opcua-node-tree.md`
- `prd/appendix-c-mqtt-topic-map.md`
- `prd/03a-network-topology.md`
- `prd/03-protocol-endpoints.md` (Section 3.1.6, 3.1.7, 3.2)
- `src/factory_simulator/protocols/modbus_server.py`
- `src/factory_simulator/protocols/opcua_server.py`
- `src/factory_simulator/protocols/mqtt_publisher.py`
- `src/factory_simulator/topology.py`
- `config/factory.yaml`
- `config/factory-foodbev.yaml`
- `src/factory_simulator/config.py`

---

## 1. Executive Summary

The simulator's protocol implementations are **substantially correct** for the core data paths. Every Modbus holding register address, OPC-UA node ID, and MQTT topic in the YAML configs matches the PRD appendices. The encoding helpers (ABCD/CDAB float32, uint32, int16_x10) are correctly implemented and thoroughly tested. The multi-slave Eurotherm pattern (UIDs 11-13), FC06 rejection, 125-register limit, and exception injection are all properly coded.

**Key findings:**

| Severity | Count | Summary |
|----------|-------|---------|
| 🔴 RED | 2 | Missing OPC-UA `EngineeringUnits` property; Oven gateway UID routing mismatch in realistic mode |
| 🟡 YELLOW | 5 | 0x06 exception hardcoded to `press.machine_state`; Coil 4 derivation approximation; No `MinimumSamplingInterval` OPC-UA attribute; No `AccessLevel=0` for inactive profile; LWT topic not profile-specific |
| 🟢 GREEN | 7 | Minor naming, documentation, or defensive-code observations |

The simulator will produce correct protocol data for the **vast majority** of integration test scenarios. The RED issues affect OPC-UA metadata quality and one realistic-mode topology edge case. No data-value corruption bugs were found.

---

## 2. Register Map Audit (Modbus)

### 2.1 Packaging Profile — Holding Registers (FC03)

Every address verified against PRD Appendix A:

| PRD Address | PRD Signal | Config `modbus_hr` | Config `modbus_type` | ✅/❌ |
|-------------|-----------|---------------------|----------------------|-------|
| 100-101 | press.line_speed | [100,101] | float32 | ✅ |
| 102-103 | press.web_tension | [102,103] | float32 | ✅ |
| 110-111 | press.ink_viscosity | [110,111] | float32 | ✅ |
| 112-113 | press.ink_temperature | [112,113] | float32 | ✅ |
| 120-121 | press.dryer_temp_zone_1 | [120,121] | float32 | ✅ |
| 122-123 | press.dryer_temp_zone_2 | [122,123] | float32 | ✅ |
| 124-125 | press.dryer_temp_zone_3 | [124,125] | float32 | ✅ |
| 140-141 | press.dryer_setpoint_zone_1 | [140,141] | float32, writable | ✅ |
| 142-143 | press.dryer_setpoint_zone_2 | [142,143] | float32, writable | ✅ |
| 144-145 | press.dryer_setpoint_zone_3 | [144,145] | float32, writable | ✅ |
| 200-201 | press.impression_count | [200,201] | uint32 | ✅ |
| 202-203 | press.good_count | [202,203] | uint32 | ✅ |
| 204-205 | press.waste_count | [204,205] | uint32 | ✅ |
| 210 | press.machine_state | [210] | uint16 | ✅ |
| 211 | press.fault_code | [211] | uint16 | ✅ |
| 300-301 | press.main_drive_current | [300,301] | float32 | ✅ |
| 302-303 | press.main_drive_speed | [302,303] | float32 | ✅ |
| 310-311 | press.nip_pressure | [310,311] | float32 | ✅ |
| 320-321 | press.unwind_diameter | [320,321] | float32 | ✅ |
| 322-323 | press.rewind_diameter | [322,323] | float32 | ✅ |
| 400-401 | laminator.nip_temp | [400,401] | float32 | ✅ |
| 402-403 | laminator.nip_pressure | [402,403] | float32 | ✅ |
| 404-405 | laminator.tunnel_temp | [404,405] | float32 | ✅ |
| 406-407 | laminator.web_speed | [406,407] | float32 | ✅ |
| 408-409 | laminator.adhesive_weight | [408,409] | float32 | ✅ |
| 500-501 | slitter.speed | [500,501] | float32 | ✅ |
| 502-503 | slitter.web_tension | [502,503] | float32 | ✅ |
| 510-511 | slitter.reel_count | [510,511] | uint32 | ✅ |
| 600-601 | energy.line_power | [600,601] | float32 | ✅ |
| 602-603 | energy.cumulative_kwh | [602,603] | float32 | ✅ |

**All 30 packaging HR entries: PASS** ✅

### 2.2 F&B Profile — Holding Registers (FC03)

| PRD Address | PRD Signal | Config `modbus_hr` | Byte Order | ✅/❌ |
|-------------|-----------|---------------------|------------|-------|
| 1000-1001 | mixer.speed | [1000,1001] | CDAB | ✅ |
| 1002-1003 | mixer.torque | [1002,1003] | CDAB | ✅ |
| 1004-1005 | mixer.batch_temp | [1004,1005] | CDAB | ✅ |
| 1006-1007 | mixer.batch_weight | [1006,1007] | CDAB | ✅ |
| 1010-1011 | mixer.mix_time_elapsed | [1010,1011] | CDAB (uint32) | ✅ |
| 1100-1101 | oven.zone_1_temp | [1100,1101] | ABCD | ✅ |
| 1102-1103 | oven.zone_2_temp | [1102,1103] | ABCD | ✅ |
| 1104-1105 | oven.zone_3_temp | [1104,1105] | ABCD | ✅ |
| 1110-1111 | oven.zone_1_setpoint | [1110,1111] | ABCD, writable | ✅ |
| 1112-1113 | oven.zone_2_setpoint | [1112,1113] | ABCD, writable | ✅ |
| 1114-1115 | oven.zone_3_setpoint | [1114,1115] | ABCD, writable | ✅ |
| 1120-1121 | oven.belt_speed | [1120,1121] | ABCD | ✅ |
| 1122-1123 | oven.product_core_temp | [1122,1123] | ABCD | ✅ |
| 1124-1125 | oven.humidity_zone_2 | [1124,1125] | ABCD | ✅ |
| 1200-1201 | filler.hopper_level | [1200,1201] | ABCD | ✅ |
| 1300-1301 | sealer.seal_temp | [1300,1301] | ABCD | ✅ |
| 1302-1303 | sealer.seal_pressure | [1302,1303] | ABCD | ✅ |
| 1304-1305 | sealer.seal_dwell | [1304,1305] | ABCD | ✅ |
| 1306-1307 | sealer.gas_co2_pct | [1306,1307] | ABCD | ✅ |
| 1308-1309 | sealer.gas_n2_pct | [1308,1309] | ABCD | ✅ |
| 1310-1311 | sealer.vacuum_level | [1310,1311] | ABCD | ✅ |
| 1400-1401 | chiller.room_temp | [1400,1401] | ABCD | ✅ |
| 1402-1403 | chiller.setpoint | [1402,1403] | ABCD, writable | ✅ |
| 1404-1405 | chiller.suction_pressure | [1404,1405] | ABCD | ✅ |
| 1406-1407 | chiller.discharge_pressure | [1406,1407] | ABCD | ✅ |
| 1500-1501 | cip.wash_temp | [1500,1501] | ABCD | ✅ |
| 1502-1503 | cip.flow_rate | [1502,1503] | ABCD | ✅ |
| 1504-1505 | cip.conductivity | [1504,1505] | ABCD | ✅ |
| 1506-1507 | cip.cycle_time_elapsed | [1506,1507] | uint32 ABCD | ✅ |
| 600-601 | energy.line_power | [600,601] | ABCD | ✅ |
| 602-603 | energy.cumulative_kwh | [602,603] | ABCD | ✅ |

**All 31 F&B HR entries: PASS** ✅

### 2.3 Input Registers (FC04)

**Packaging Profile:**

| PRD IR | PRD Signal | Config `modbus_ir` | Type | ✅/❌ |
|--------|-----------|---------------------|------|-------|
| 0 | press.dryer_temp_zone_1 | [0] | int16_x10 | ✅ |
| 1 | press.dryer_temp_zone_2 | [1] | int16_x10 | ✅ |
| 2 | press.dryer_temp_zone_3 | [2] | int16_x10 | ✅ |
| 3 | press.ink_temperature | [3] | int16_x10 | ✅ |
| 4 | laminator.nip_temp | [4] | int16_x10 | ✅ |
| 5 | laminator.tunnel_temp | [5] | int16_x10 | ✅ |
| 10-11 | energy.line_power | [10,11] | float32 | ✅ |

**F&B Profile (main UID block):**

| PRD IR | PRD Signal | Config `modbus_ir` | Type | ✅/❌ |
|--------|-----------|---------------------|------|-------|
| 100 | oven.zone_1_temp | [100] | int16_x10 | ✅ |
| 101 | oven.zone_2_temp | [101] | int16_x10 | ✅ |
| 102 | oven.zone_3_temp | [102] | int16_x10 | ✅ |
| 103 | oven.zone_1_setpoint | [103] | int16_x10 | ✅ |
| 104 | oven.zone_2_setpoint | [104] | int16_x10 | ✅ |
| 105 | oven.zone_3_setpoint | [105] | int16_x10 | ✅ |
| 106 | oven.product_core_temp | [106] | int16_x10 | ✅ |
| 110 | chiller.room_temp | [110] | int16_x10 | ✅ |
| 111 | chiller.setpoint | [111] | int16_x10 | ✅ |
| 115 | cip.wash_temp | [115] | int16_x10 | ✅ |
| 120-121 | energy.line_power | [120,121] | float32 | ✅ |

**F&B Profile (secondary slaves UID 11-13):**

| PRD | Signal | Config slave_id / slave_ir | ✅/❌ |
|-----|--------|---------------------------|-------|
| IR 0 (UID 11) | oven.zone_1_temp (PV) | slave_id=11, slave_ir=[0] | ✅ |
| IR 1 (UID 11) | oven.zone_1_setpoint (SP) | slave_id=11, slave_ir=[1] | ✅ |
| IR 2 (UID 11) | oven.zone_1_output_power | slave_id=11, ir=[2] | ✅ |
| IR 0 (UID 12) | oven.zone_2_temp (PV) | slave_id=12, slave_ir=[0] | ✅ |
| IR 1 (UID 12) | oven.zone_2_setpoint (SP) | slave_id=12, slave_ir=[1] | ✅ |
| IR 2 (UID 12) | oven.zone_2_output_power | slave_id=12, ir=[2] | ✅ |
| IR 0 (UID 13) | oven.zone_3_temp (PV) | slave_id=13, slave_ir=[0] | ✅ |
| IR 1 (UID 13) | oven.zone_3_setpoint (SP) | slave_id=13, slave_ir=[1] | ✅ |
| IR 2 (UID 13) | oven.zone_3_output_power | slave_id=13, ir=[2] | ✅ |

**All IR entries: PASS** ✅

### 2.4 Coils (FC01)

**Packaging:**

| PRD | Signal | Code derivation | ✅/❌ |
|-----|--------|-----------------|-------|
| 0 | press.running | machine_state == 2 | ✅ |
| 1 | press.fault_active | machine_state == 4 | ✅ |
| 2 | press.emergency_stop | always False | ✅ (sentinel) |
| 3 | press.web_break | press.web_break > 0 | ✅ |
| 4 | laminator.running | press.machine_state == 2 | ⚠️ see §2.8 |
| 5 | slitter.running | slitter.speed > 0 | ✅ |

**F&B (dynamic):**

| PRD | Signal | Config `modbus_coil` | ✅/❌ |
|-----|--------|---------------------|-------|
| 100 | mixer.lid_closed | 100 | ✅ |
| 101 | chiller.compressor_state | 101 | ✅ |
| 102 | chiller.defrost_active | 102 | ✅ |

### 2.5 Discrete Inputs (FC02)

**Packaging:**

| PRD | Signal | Code derivation | ✅/❌ |
|-----|--------|-----------------|-------|
| 0 | press.guard_door_open | always False | ✅ (sentinel) |
| 1 | press.material_present | machine_state == 2 | ✅ |
| 2 | press.cycle_complete | impression_count % 2 | ✅ |

**F&B (dynamic):**

| PRD | Signal | Config `modbus_di` | ✅/❌ |
|-----|--------|-------------------|-------|
| 100 | chiller.door_open | 100 | ✅ |

### 2.6 CDAB Byte Order (Allen-Bradley Mixer)

The CDAB encoding functions are correctly implemented:
- `encode_float32_cdab()` returns `(low_word, high_word)` — register[0] = low word, register[1] = high word
- `encode_uint32_cdab()` follows same pattern
- Each mixer signal in config has `modbus_byte_order: "CDAB"`
- The `_sync_holding_registers()` method checks `entry.byte_order` per-entry

**Verified with manual trace:**
- `float32 value 100.0` → packed big-endian → `0x42C80000` → `high=0x42C8, low=0x0000`
- ABCD: reg[0]=0x42C8, reg[1]=0x0000
- CDAB: reg[0]=0x0000, reg[1]=0x42C8 ✅ (words swapped)

### 2.7 int16_x10 Scaling (Eurotherm)

`encode_int16_x10(85.0)` → `round(85.0 * 10)` = `850` → `850 & 0xFFFF` = `0x0352` ✅
`encode_int16_x10(-10.0)` → `round(-10.0 * 10)` = `-100` → `-100 & 0xFFFF` = `65436` ✅
`decode_int16_x10(65436)` → `65436 >= 0x8000` → `65436 - 65536` = `-100` → `-100 / 10.0` = `-10.0` ✅

### 2.8 Coil 4 (laminator.running) — Approximation

`modbus_server.py:539` derives Coil 4 from `press.machine_state == 2` rather than `laminator.web_speed > 0`. This means the laminator coil tracks the press state, not the laminator's own operating status. Since the laminator is inline and always follows the press, this is a reasonable approximation but could cause incorrect readings if:
1. The laminator starts independently during a web-break recovery
2. Test scenarios stop the laminator while the press runs

The slitter (Coil 5) correctly uses `slitter.speed > 0`, which is the more accurate pattern. For consistency, Coil 4 should use `laminator.web_speed > 0` (mode="gt_zero").

### 2.9 FC06 Rejection

`FactoryDeviceContext.setValues()` correctly rejects FC06 (Write Single Register) on float32 register addresses by returning `ExcCodes.ILLEGAL_FUNCTION`. The tracked set (`float32_hr_addresses`) includes both words of float32 AND uint32 register pairs, which is correct — FC06 should not write a single word of any 32-bit register.

### 2.10 125-Register Read Limit

`FactoryDeviceContext.getValues()` returns `ExcCodes.ILLEGAL_VALUE` when `count > 125` for FC03/FC04. This matches Modbus specification and PRD 3.1.7. ✅

---

## 3. OPC-UA Node Tree Audit

### 3.1 Node ID Verification

**Packaging Profile** — all 32 nodes match PRD Appendix B:

| PRD Node ID | Config `opcua_node` | Data Type | ✅/❌ |
|-------------|---------------------|-----------|-------|
| ns=2;s=PackagingLine.Press1.LineSpeed | PackagingLine.Press1.LineSpeed | Double | ✅ |
| ns=2;s=PackagingLine.Press1.WebTension | PackagingLine.Press1.WebTension | Double | ✅ |
| ns=2;s=PackagingLine.Press1.State | PackagingLine.Press1.State | UInt16 | ✅ |
| ns=2;s=PackagingLine.Press1.FaultCode | PackagingLine.Press1.FaultCode | UInt16 | ✅ |
| ns=2;s=PackagingLine.Press1.ImpressionCount | PackagingLine.Press1.ImpressionCount | UInt32 | ✅ |
| ns=2;s=PackagingLine.Press1.GoodCount | PackagingLine.Press1.GoodCount | UInt32 | ✅ |
| ns=2;s=PackagingLine.Press1.WasteCount | PackagingLine.Press1.WasteCount | UInt32 | ✅ |
| ns=2;s=PackagingLine.Press1.Registration.ErrorX | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.Registration.ErrorY | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.Ink.Viscosity | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.Ink.Temperature | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.Dryer.Zone1.Temperature | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.Dryer.Zone1.Setpoint | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.Dryer.Zone2.Temperature | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.Dryer.Zone2.Setpoint | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.Dryer.Zone3.Temperature | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.Dryer.Zone3.Setpoint | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.MainDrive.Current | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.MainDrive.Speed | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.NipPressure | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.Unwind.Diameter | ... | Double | ✅ |
| ns=2;s=PackagingLine.Press1.Rewind.Diameter | ... | Double | ✅ |
| ns=2;s=PackagingLine.Laminator1.NipTemperature | ... | Double | ✅ |
| ns=2;s=PackagingLine.Laminator1.NipPressure | ... | Double | ✅ |
| ns=2;s=PackagingLine.Laminator1.TunnelTemperature | ... | Double | ✅ |
| ns=2;s=PackagingLine.Laminator1.WebSpeed | ... | Double | ✅ |
| ns=2;s=PackagingLine.Laminator1.AdhesiveWeight | ... | Double | ✅ |
| ns=2;s=PackagingLine.Slitter1.Speed | ... | Double | ✅ |
| ns=2;s=PackagingLine.Slitter1.WebTension | ... | Double | ✅ |
| ns=2;s=PackagingLine.Slitter1.ReelCount | ... | UInt32 | ✅ |
| ns=2;s=PackagingLine.Energy.LinePower | ... | Double | ✅ |
| ns=2;s=PackagingLine.Energy.CumulativeKwh | ... | Double | ✅ |

**F&B Profile** — all 19 nodes match PRD Appendix B ✅

### 3.2 EURange Property

The code adds an `EURange` property with `Low` and `High` from `min_clamp`/`max_clamp` to every variable node (`opcua_server.py:263-271`). This matches the PRD attribute convention. ✅

### 3.3 Missing: EngineeringUnits Property 🔴

**PRD Appendix B states:** "EngineeringUnits: Set to the signal's unit string"

The `_build_node_tree()` method (`opcua_server.py:230-297`) does NOT set the `EngineeringUnits` property on any node. The signal config has `units` available (e.g. "m/min", "N", "C") but it is never used in the OPC-UA node construction.

A real asyncua client using `node.get_properties()` or browsing `HasProperty` references will not find an `EngineeringUnits` property. Any OPC-UA client (e.g. KepServerEX, Ignition, or a custom asyncua client) that relies on `EngineeringUnits` for display or scaling will see no unit information.

**Impact:** OPC-UA clients that auto-discover engineering units (common in SCADA/HMI) will display raw numbers without context. This affects the simulator's realism for integration testing where unit metadata matters.

**Fix:** Add `EngineeringUnits` as an `EUInformation` property. In asyncua:
```python
eu = ua.EUInformation(
    NamespaceUri="http://www.opcfoundation.org/UA/units/un/cefact",
    UnitId=-1,
    DisplayName=ua.LocalizedText(sig_cfg.units or ""),
    Description=ua.LocalizedText(sig_cfg.units or ""),
)
await var_node.add_property(ua.NodeId(0, 0), "EngineeringUnits", eu)
```

### 3.4 Missing: MinimumSamplingInterval Attribute 🟡

**PRD Appendix B states:** "MinimumSamplingInterval: Matches the signal's configured sample rate in milliseconds"

The code does not set `MinimumSamplingInterval` on any variable node. asyncua defaults this to 0 (fastest possible), which misrepresents the signal's actual update rate. An OPC-UA client that uses `MinimumSamplingInterval` to optimize subscription parameters will over-subscribe.

**Impact:** Low-to-moderate. Most OPC-UA clients ignore this attribute and use their own configured subscription intervals. However, well-behaved clients (especially Ignition) do use it.

### 3.5 StatusCode Mapping

The `_sync_values()` method correctly maps quality strings to StatusCodes:
- `"good"` → `StatusCode.Good` (or omitted for efficiency) ✅
- `"uncertain"` → `StatusCode.UncertainLastUsableValue` ✅
- `"bad"` → `StatusCode.BadSensorFailure` ✅

### 3.6 SourceTimestamp vs ServerTimestamp

When `clock_drift` is configured (realistic mode), `SourceTimestamp` is set to the drifted time via `_sim_time_to_datetime()`. When no clock drift, the `SourceTimestamp` is left unset (asyncua uses server time). The PRD says: "SourceTimestamp uses the controller's drifted clock. ServerTimestamp uses the true simulation clock."

In collapsed mode (no clock drift), both timestamps effectively use the same clock, which is acceptable for development testing. ✅

### 3.7 Comm Drop → UncertainLastUsableValue

`_freeze_all_nodes()` correctly writes `UncertainLastUsableValue` StatusCode with the last known value during comm drops. This is the correct OPC-UA behavior — values freeze but are marked as stale. ✅

### 3.8 Setpoint Write-Back

The `_sync_values()` Phase 1 correctly detects client OPC-UA writes on setpoint nodes (marked with `modbus_writable=True`) and propagates them back to the SignalStore. This matches PRD 3.2.4. ✅

### 3.9 Missing: AccessLevel=0 for Inactive Profile 🟡

**PRD Section 3.2.1 states:** "Nodes for the inactive profile report StatusCode.BadNotReadable and have AccessLevel set to 0."

The code builds node trees filtered by profile (using `_node_tree_root` in realistic mode), but there is no mechanism to create the inactive profile's nodes with `AccessLevel=0`. In collapsed mode with a single OPC-UA server, only the active profile's nodes are created. The inactive profile's nodes simply don't exist in the address space.

**Impact:** An OPC-UA client browsing the server won't discover nodes for the other profile. This makes it harder to test CollatrEdge's ability to distinguish active vs inactive profiles via OPC-UA browsing.

### 3.10 Node Tree Filtering in Realistic Mode

In realistic mode, OPC-UA servers use `node_tree_root` to filter nodes:
- Filler server (port 4841): `node_tree_root="FoodBevLine.Filler1"` — only Filler1 nodes
- QC server (port 4842): `node_tree_root="FoodBevLine.QC1"` — only QC1 nodes

The filtering uses `startswith()` which correctly matches all nodes under these subtrees. ✅

---

## 4. MQTT Topic Audit

### 4.1 Topic Path Verification

**Packaging Profile** — all 17 topics match PRD Appendix C:

The `build_topic_map()` function constructs topics as:
```
{topic_prefix}/{site_id}/{line_id}/{mqtt_topic}
```
= `collatr/factory/demo/packaging1/{relative_topic}`

| PRD Topic | Config `mqtt_topic` | Full Path | ✅/❌ |
|-----------|---------------------|-----------|-------|
| `.../packaging1/coder/state` | coder/state | ✅ | ✅ |
| `.../packaging1/coder/prints_total` | coder/prints_total | ✅ | ✅ |
| `.../packaging1/coder/ink_level` | coder/ink_level | ✅ | ✅ |
| `.../packaging1/coder/printhead_temp` | coder/printhead_temp | ✅ | ✅ |
| `.../packaging1/coder/ink_pump_speed` | coder/ink_pump_speed | ✅ | ✅ |
| `.../packaging1/coder/ink_pressure` | coder/ink_pressure | ✅ | ✅ |
| `.../packaging1/coder/ink_viscosity_actual` | coder/ink_viscosity_actual | ✅ | ✅ |
| `.../packaging1/coder/supply_voltage` | coder/supply_voltage | ✅ | ✅ |
| `.../packaging1/coder/ink_consumption_ml` | coder/ink_consumption_ml | ✅ | ✅ |
| `.../packaging1/coder/nozzle_health` | coder/nozzle_health | ✅ | ✅ |
| `.../packaging1/coder/gutter_fault` | coder/gutter_fault | ✅ | ✅ |
| `.../packaging1/env/ambient_temp` | env/ambient_temp | ✅ | ✅ |
| `.../packaging1/env/ambient_humidity` | env/ambient_humidity | ✅ | ✅ |
| `.../packaging1/vibration/main_drive_x` | vibration/main_drive_x | ✅ | ✅ |
| `.../packaging1/vibration/main_drive_y` | vibration/main_drive_y | ✅ | ✅ |
| `.../packaging1/vibration/main_drive_z` | vibration/main_drive_z | ✅ | ✅ |
| `.../packaging1/vibration/main_drive` | (batch, auto-built) | ✅ | ✅ |

**F&B Profile** — all 13 topics match PRD Appendix C:

| PRD Topic | Config `mqtt_topic` | Full Path | ✅/❌ |
|-----------|---------------------|-----------|-------|
| `.../foodbev1/coder/state` | coder/state | ✅ | ✅ |
| ... (11 coder topics) | ... | ✅ | ✅ |
| `.../foodbev1/env/ambient_temp` | env/ambient_temp | ✅ | ✅ |
| `.../foodbev1/env/ambient_humidity` | env/ambient_humidity | ✅ | ✅ |

F&B config correctly sets `vibration_per_axis_enabled: false` and the batch vibration builder returns `None` for the F&B profile (no vibration equipment). ✅

### 4.2 QoS Levels

The `_QOS1_SUFFIXES` frozenset contains exactly the 4 event-driven topics:
- `coder/state` → QoS 1 ✅
- `coder/prints_total` → QoS 1 ✅
- `coder/nozzle_health` → QoS 1 ✅
- `coder/gutter_fault` → QoS 1 ✅

All other topics → QoS 0 ✅

### 4.3 Retain Flags

`_retain_for_topic()` returns `False` only for topics starting with `"vibration/"`. All other topics return `True`. This matches the PRD: vibration topics are not retained; all others are retained. ✅

### 4.4 JSON Payload Format

`make_payload()` produces:
```json
{"timestamp": "2026-03-01T14:30:00.000Z", "value": 42.7, "unit": "C", "quality": "good"}
```

- `timestamp`: ISO 8601 with milliseconds and Z suffix ✅
- `value`: JSON number (float64) ✅
- `unit`: string ✅
- `quality`: string, one of "good"/"uncertain"/"bad" ✅

The batch vibration payload matches PRD 3.3.6:
```json
{"timestamp": "...", "x": 4.2, "y": 3.8, "z": 5.1, "unit": "mm/s", "quality": "good"}
```
✅

### 4.5 LWT Configuration

- Topic: `collatr/factory/status` ✅
- Payload: `{"status": "offline"}` ✅
- QoS: 1 ✅
- Retain: True ✅

**Minor observation:** The LWT topic does not include the profile/line identifier. Both packaging and F&B simulators would publish to the same LWT topic. If both run simultaneously (future "both profiles" mode), they'd conflict. This is acceptable for current single-profile operation.

### 4.6 Message Buffering

`max_queued_messages_set(buffer_limit)` with `buffer_limit=1000` from config. ✅
This uses paho-mqtt's built-in message queue limit. When the queue is full, the oldest message is dropped (paho default behavior matches `buffer_overflow: "drop_oldest"`). ✅

### 4.7 Comm Drop Behavior

During an active MQTT comm drop, `_publish_loop()` skips the `_publish_due()` call entirely. No empty payloads are published. Messages are simply not sent. ✅

---

## 5. Multi-Controller Topology Protocol Issues

### 5.1 Realistic Mode Port Mapping

Verified against PRD 3a.4 table:

| PRD Endpoint | Code Port | Code UIDs | ✅/❌ |
|-------------|-----------|-----------|-------|
| Press PLC (5020) | 5020 | [1, 5] | ✅ |
| Laminator PLC (5021) | 5021 | [1] | ✅ |
| Slitter PLC (5022) | 5022 | [1] | ✅ |
| Mixer PLC (5030) | 5030 | [1] | ✅ |
| Oven Gateway (5031) | 5031 | [1, 2, 3, 10] | ✅ |
| Filler Modbus (5032) | 5032 | [1] | ✅ |
| Sealer PLC (5033) | 5033 | [1] | ✅ |
| Chiller (5034) | 5034 | [1] | ✅ |
| CIP PLC (5035) | 5035 | [1] | ✅ |
| Press OPC-UA (4840) | 4840 | n/a | ✅ |
| Filler OPC-UA (4841) | 4841 | n/a | ✅ |
| QC OPC-UA (4842) | 4842 | n/a | ✅ |

### 5.2 Address Range Isolation in Realistic Mode

Each endpoint in realistic mode builds its register map filtered by `equipment_ids`. The `valid_hr_addresses` and `valid_ir_addresses` sets restrict reads to only the equipment's addresses. Out-of-range reads return 0x02 (Illegal Data Address).

**Example:** Laminator PLC (port 5021, equipment_ids=["laminator"]) only serves HR 400-409. A read to HR 100 (press register) returns 0x02. ✅

### 5.3 Oven Gateway UID Routing — Mismatch 🔴

**PRD 03a states:** The oven gateway at 10.0.2.20:502 serves UIDs 1, 2, 3 for zone controllers and UID 10 for the energy meter. UIDs 1,2,3 are individual Eurotherm 3504 controllers, each with IR 0 (PV), IR 1 (SP), IR 2 (output power).

**Code behavior:** In `_foodbev_modbus()`, UIDs [1, 2, 3, 10] all map to the **same** primary `FactoryDeviceContext`. This context has the main IR block containing addresses 100-121 (oven zone temps, chiller temps, etc.). Reading IR 0 at UID 1 on port 5031 hits the primary context's IR block — but IR address 0 is NOT in the `valid_ir_addresses` set (which only includes 100-121), so it returns **0x02 (Illegal Address)**.

Meanwhile, the secondary slave mechanism maps UIDs 11-13 to separate contexts that DO serve IR 0, 1, 2 per zone. But a pymodbus client configured per the PRD 03a topology (UIDs 1, 2, 3) would never reach these secondary contexts.

**Root cause:** The collapsed-mode multi-slave feature (UIDs 11-13 per Section 3.1.6) and the realistic-mode topology (UIDs 1, 2, 3 per Section 3a) use **different UID assignments** for the same physical Eurotherm controllers. The code doesn't translate between them.

**Impact:** In realistic mode, a client following the PRD 03a topology table cannot read per-zone Eurotherm input registers at UIDs 1/2/3. The workaround is to use UIDs 11/12/13, but this contradicts the topology specification.

**Fix options:**
1. In realistic mode, remap secondary slaves to UIDs 1/2/3 instead of 11/12/13
2. Add a UID translation table that maps topology UIDs to secondary slave UIDs
3. Register the secondary slave contexts under both UID sets (1↔11, 2↔12, 3↔13) on port 5031

### 5.4 OPC-UA Dual-Server Isolation

The `node_tree_root` filtering ensures:
- Port 4841 (Filler): only `FoodBevLine.Filler1.*` nodes are created
- Port 4842 (QC): only `FoodBevLine.QC1.*` nodes are created

A subscription on port 4841 will never receive QC data, and vice versa. ✅

### 5.5 Controller-Type Configuration in Topology

Each endpoint correctly maps to its controller type for connection limits, clock drift, scan cycle, and connection drop parameters. Verified:
- Press PLC → S7-1500 (16 max conn, 10ms scan, 72h+ MTBF)
- Laminator/Slitter → S7-1200 (3 max conn, 20ms scan)
- Mixer → CompactLogix (8 max conn, 15ms scan, CDAB)
- Oven Gateway → Eurotherm (2 max conn, 100ms scan, 8-24h MTBF)
- Chiller → Danfoss (2 max conn, 100ms scan)
- Energy → PM5560 on shared port (embedded in press or oven gateway)

✅

---

## 6. Data Quality Protocol Effects

### 6.1 Modbus Comm Drop → Register Freeze

During a comm drop, `_update_loop()` checks `self._drop_scheduler.is_active(now)` and skips `sync_registers()`. The register block retains its last-written values (pymodbus data blocks hold state). Client reads continue to return **stale but valid** data. Registers are **not zeroed**. ✅

### 6.2 OPC-UA Comm Drop → UncertainLastUsableValue

On drop start, `_freeze_all_nodes()` writes `UncertainLastUsableValue` with the last known value. During the drop, `_update_loop()` skips `_sync_values()`. On drop end, normal sync resumes and StatusCode returns to Good. ✅

### 6.3 MQTT Comm Drop → Stop Publishing

During a comm drop, `_publish_loop()` skips `_publish_due()`. No messages are published — no empty payloads, no zero values. The broker retains the last published message for retained topics. ✅

### 6.4 Modbus Exception Injection

**0x04 (Device Failure):** Random draw at `exception_probability` (0.001 default). Returns `ExcCodes.DEVICE_FAILURE`. This is a valid Modbus exception code. pymodbus sends a standard exception response PDU: [slave_id, function_code | 0x80, 0x04]. ✅

**0x06 (Device Busy):** Fires deterministically during machine state transitions (within 0.5s window). Returns `ExcCodes.DEVICE_BUSY`. Valid Modbus exception. ✅

**Issue:** The 0x06 injection hardcodes `press.machine_state` as the transition source (`modbus_server.py:887-896`). For F&B endpoints (mixer, oven, filler, etc.), `press.machine_state` doesn't exist in the store, so `sv` is None and transitions are never detected. 0x06 exceptions will **never fire** on F&B endpoints.

### 6.5 Partial Modbus Response

`check_partial()` only triggers for multi-register reads (count >= 2). Returns a truncated slice of the requested registers. The byte count in the response PDU is handled by pymodbus (it counts the returned values). ✅

### 6.6 Duplicate Timestamp Injection

**Modbus:** `_update_loop()` skips sync at `duplicate_probability`, causing registers to hold identical values on consecutive reads. Valid protocol behavior (same values, same register state). ✅

**MQTT:** `_publish_entry()` publishes the same payload twice (within 1ms) at `duplicate_probability / 2`. Both messages have identical timestamps, values, and payloads. Valid MQTT (duplicate messages are allowed; QoS 0 has no dedup). ✅

---

## 7. Cross-Protocol Consistency

### 7.1 Shared Signals: Modbus + OPC-UA

Signals served on both protocols draw from the same SignalStore. The value path is:

```
Signal Engine → SignalStore → {Modbus sync, OPC-UA sync}
```

Both protocol adapters read from the same `SignalStore.get(signal_id)` call. This ensures **value identity** at the store level.

**Precision considerations:**
- Modbus float32: 32-bit IEEE 754 (7 significant digits)
- OPC-UA Double: 64-bit IEEE 754 (15 significant digits)
- The Modbus server encodes `float(value)` through `struct.pack(">f", value)`, which truncates to float32 precision
- The OPC-UA server passes `float(value)` directly as Double

For shared signals (e.g. `press.line_speed`), the OPC-UA value will have higher precision than the Modbus value. **This is realistic behavior** — a real S7-1500 serving the same signal via Modbus and OPC-UA would show the same precision difference. ✅

### 7.2 Shared Signals: Modbus HR + Modbus IR (Dual Representation)

Press dryer temperatures appear in:
- HR as float32 (e.g. HR 120-121 = press.dryer_temp_zone_1, full precision)
- IR as int16_x10 (e.g. IR 0 = press.dryer_temp_zone_1, 0.1°C resolution)

Both read from the same signal in the store. The int16_x10 encoding loses precision (rounds to nearest 0.1). **This is the expected Eurotherm dual-representation behavior** per PRD Section 3a.2. ✅

### 7.3 Update Timing

| Protocol | Sync Interval | Notes |
|----------|--------------|-------|
| Modbus | 50ms (`_update_loop` sleep) | All registers synced atomically per pass |
| OPC-UA | 500ms (`MIN_PUBLISHING_INTERVAL_MS`) | All nodes synced per pass |
| MQTT | 100ms (`_publish_loop` sleep) | Each topic has its own interval check |

A Modbus client polling at 100ms will see values up to 50ms newer than what an OPC-UA client sees (OPC-UA updates at 500ms). This temporal skew is realistic — different protocols have different update rates in real factories. ✅

### 7.4 Cross-Protocol Signal Coverage

| Signal | Modbus HR | Modbus IR | OPC-UA | MQTT | Notes |
|--------|-----------|-----------|--------|------|-------|
| press.line_speed | HR 100-101 | — | ✅ | — | Modbus + OPC-UA |
| press.dryer_temp_zone_1 | HR 120-121 | IR 0 | ✅ | — | Triple-served |
| press.ink_temperature | HR 112-113 | IR 3 | ✅ | — | Triple-served |
| energy.line_power | HR 600-601 | IR 10-11 / IR 120-121 | ✅ | — | Quad-served (pkg+f&b) |
| coder.state | — | — | — | ✅ | MQTT-only |
| vibration.main_drive_x | — | — | — | ✅ | MQTT-only |
| filler.line_speed | — | — | ✅ | — | OPC-UA only |
| registration_error_x | — | — | ✅ | — | OPC-UA only |

**No missing cross-protocol signals detected.** Each signal is served on the protocols specified by the PRD. ✅

### 7.5 Packaging vs F&B Energy Register Sharing

The PRD states energy monitoring registers (HR 600-603) are "shared between the packaging and F&B profiles." Both configs use the same addresses:
- Packaging: energy.line_power at HR [600,601], energy.cumulative_kwh at HR [602,603]
- F&B: energy.line_power at HR [600,601], energy.cumulative_kwh at HR [602,603]

In collapsed mode, only one profile is active, so there's no conflict. In realistic mode, the energy meter is served on different ports (5020 UID 5 for packaging, 5031 UID 10 for F&B). ✅

---

## 8. Issues Table

| # | Severity | Issue | File:Line | Impact | Fix Effort |
|---|----------|-------|-----------|--------|------------|
| 1 | 🔴 RED | **Missing OPC-UA `EngineeringUnits` property.** PRD Appendix B requires it on all variable nodes. No unit metadata available to OPC-UA clients. | `opcua_server.py:263-271` | OPC-UA clients cannot auto-discover signal units | 2h |
| 2 | 🔴 RED | **Oven gateway UID routing mismatch in realistic mode.** PRD 03a says UIDs 1,2,3 for zones, but code maps them to primary context (which doesn't serve IR 0/1/2). Secondary slaves at UIDs 11-13 are unreachable via PRD-specified UIDs. | `topology.py:578-579` / `modbus_server.py:560-580` | pymodbus clients following PRD 03a topology cannot read per-zone Eurotherm IRs | 4h |
| 3 | 🟡 YELLOW | **0x06 (Device Busy) exception only triggers on `press.machine_state`.** F&B endpoints never fire 0x06 because `press.machine_state` doesn't exist in F&B profile store. | `modbus_server.py:887-896` | F&B integration tests never see Device Busy exceptions | 2h |
| 4 | 🟡 YELLOW | **Coil 4 (laminator.running) derived from press state, not laminator state.** Uses `press.machine_state == 2` instead of `laminator.web_speed > 0`. Inconsistent with Coil 5 pattern. | `modbus_server.py:539` | Laminator coil tracks press, not laminator. Minor realism gap. | 15min |
| 5 | 🟡 YELLOW | **Missing OPC-UA `MinimumSamplingInterval` attribute.** PRD Appendix B requires it. Defaults to 0 (fastest). | `opcua_server.py:263-297` | Well-behaved OPC-UA clients over-subscribe | 1h |
| 6 | 🟡 YELLOW | **No `AccessLevel=0` nodes for inactive profile.** PRD 3.2.1 says inactive profile nodes should exist with `AccessLevel=0` and `BadNotReadable`. | `opcua_server.py` (node tree build) | OPC-UA clients cannot discover inactive profile's address space | 4h |
| 7 | 🟡 YELLOW | **LWT topic not profile-specific.** Both profiles publish LWT to `collatr/factory/status`. Would conflict in future dual-profile mode. | `config.py:163` / YAML configs | Cosmetic for MVP; blocks dual-profile support | 30min |
| 8 | 🟢 GREEN | **Variable naming: `float32_hr_addresses` includes uint32 addresses.** The set tracks all 32-bit register pairs, not just float32. Name is misleading. | `modbus_server.py:497` | No functional impact; readability only | 5min |
| 9 | 🟢 GREEN | **Namespace index assertion is a soft warning.** Code warns if `ns != 2` but continues. Could cause NodeID mismatches if asyncua assigns ns=3. | `opcua_server.py:196-201` | Extremely unlikely; asyncua assigns ns=2 for first registered namespace | 10min |
| 10 | 🟢 GREEN | **Modbus update interval (50ms) is faster than tick_interval_ms (100ms).** The Modbus update loop sleeps 50ms, but new signal values only arrive every 100ms. Half the syncs are no-ops. | `modbus_server.py:1245` | Wastes ~50% of sync CPU cycles; no correctness impact | 5min |
| 11 | 🟢 GREEN | **MQTT `_publish_loop` 100ms sleep granularity.** The fastest MQTT topic (vibration) has 1000ms interval. 100ms granularity means up to 100ms jitter on publish times. | `mqtt_publisher.py:440` | Acceptable; real IoT gateways have similar jitter | N/A |
| 12 | 🟢 GREEN | **Clock drift `drifted_time` always returns `>= sim_time`.** Negative drift (clock behind sim) is possible if `initial_offset_s < 0`, but config defaults are always positive. | `topology.py:139-142` | Only affects custom configs with negative offsets | N/A |
| 13 | 🟢 GREEN | **`_compute_block_size` adds +3 safety margin.** This handles the worst case (float32 at max address uses N+1 and N+2 internally). Correct but could be documented. | `modbus_server.py:583-589` | No impact | 5min |
| 14 | 🟢 GREEN | **Packaging config missing explicit `line_id` field.** Relies on Pydantic default `"packaging1"`. Works correctly but is implicit. | `config/factory.yaml` MQTT section | Works fine via default; could be explicit for clarity | 1min |

---

## Summary Recommendations

### Must-Fix Before Integration Testing
1. **Add `EngineeringUnits` property to OPC-UA nodes** (#1) — any serious OPC-UA client test will fail metadata validation
2. **Fix oven gateway UID routing** (#2) — realistic-mode integration tests with pymodbus clients per PRD 03a topology will fail

### Should-Fix Before Demo
3. **Make 0x06 injection profile-aware** (#3) — read the active profile's state signal(s) instead of hardcoding `press.machine_state`
4. **Fix Coil 4 derivation** (#4) — use `laminator.web_speed > 0` for consistency with Coil 5 pattern
5. **Add `MinimumSamplingInterval`** (#5) — enhances OPC-UA realism

### Defer to Post-MVP
6. Inactive profile node creation (#6)
7. Profile-specific LWT topic (#7)
