# Appendix A: Full Modbus Register Map

---

## Packaging Profile Registers

### Holding Registers (FC03 Read, FC06/FC16 Write)

| Address | Signal | Type | Byte Order | Scaling | Units | Writable |
|---------|--------|------|------------|---------|-------|----------|
| 100-101 | press.line_speed | float32 | ABCD | 1.0 | m/min | No |
| 102-103 | press.web_tension | float32 | ABCD | 1.0 | N | No |
| 110-111 | press.ink_viscosity | float32 | ABCD | 1.0 | seconds | No |
| 112-113 | press.ink_temperature | float32 | ABCD | 1.0 | C | No |
| 120-121 | press.dryer_temp_zone_1 | float32 | ABCD | 1.0 | C | No |
| 122-123 | press.dryer_temp_zone_2 | float32 | ABCD | 1.0 | C | No |
| 124-125 | press.dryer_temp_zone_3 | float32 | ABCD | 1.0 | C | No |
| 140-141 | press.dryer_setpoint_zone_1 | float32 | ABCD | 1.0 | C | Yes |
| 142-143 | press.dryer_setpoint_zone_2 | float32 | ABCD | 1.0 | C | Yes |
| 144-145 | press.dryer_setpoint_zone_3 | float32 | ABCD | 1.0 | C | Yes |
| 200-201 | press.impression_count | uint32 | ABCD | 1 | count | No |
| 202-203 | press.good_count | uint32 | ABCD | 1 | count | No |
| 204-205 | press.waste_count | uint32 | ABCD | 1 | count | No |
| 210 | press.machine_state | uint16 | - | 1 | enum | No |
| 211 | press.fault_code | uint16 | - | 1 | code | No |
| 300-301 | press.main_drive_current | float32 | ABCD | 1.0 | A | No |
| 302-303 | press.main_drive_speed | float32 | ABCD | 1.0 | RPM | No |
| 310-311 | press.nip_pressure | float32 | ABCD | 1.0 | bar | No |
| 320-321 | press.unwind_diameter | float32 | ABCD | 1.0 | mm | No |
| 322-323 | press.rewind_diameter | float32 | ABCD | 1.0 | mm | No |
| 400-401 | laminator.nip_temp | float32 | ABCD | 1.0 | C | No |
| 402-403 | laminator.nip_pressure | float32 | ABCD | 1.0 | bar | No |
| 404-405 | laminator.oven_temp | float32 | ABCD | 1.0 | C | No |
| 406-407 | laminator.web_speed | float32 | ABCD | 1.0 | m/min | No |
| 408-409 | laminator.adhesive_weight | float32 | ABCD | 1.0 | g/m2 | No |
| 500-501 | slitter.speed | float32 | ABCD | 1.0 | m/min | No |
| 502-503 | slitter.web_tension | float32 | ABCD | 1.0 | N | No |
| 510-511 | slitter.reel_count | uint32 | ABCD | 1 | count | No |

### Input Registers (FC04 Read-Only)

| Address | Signal | Type | Scaling | Units |
|---------|--------|------|---------|-------|
| 0 | press.dryer_temp_zone_1 | int16 | x10 | C |
| 1 | press.dryer_temp_zone_2 | int16 | x10 | C |
| 2 | press.dryer_temp_zone_3 | int16 | x10 | C |
| 3 | press.ink_temperature | int16 | x10 | C |
| 4 | laminator.nip_temp | int16 | x10 | C |
| 5 | laminator.oven_temp | int16 | x10 | C |
| 10-11 | energy.line_power | float32 | 1.0 | kW |

### Coils (FC01 Read, FC05/FC15 Write)

| Address | Signal | Description |
|---------|--------|-------------|
| 0 | press.running | Machine state = Running (2) |
| 1 | press.fault_active | Machine state = Fault (4) |
| 2 | press.emergency_stop | E-stop active |
| 3 | press.web_break | Web break detected |
| 4 | laminator.running | Laminator running |
| 5 | slitter.running | Slitter running |

### Discrete Inputs (FC02 Read-Only)

| Address | Signal | Description |
|---------|--------|-------------|
| 0 | press.guard_door_open | Safety guard state |
| 1 | press.material_present | Web material at infeed |
| 2 | press.cycle_complete | Toggles per impression cycle |

---

## Shared Registers (Both Profiles)

### Holding Registers — Energy Monitoring (FC03 Read)

| Address | Signal | Type | Byte Order | Scaling | Units | Writable |
|---------|--------|------|------------|---------|-------|----------|
| 600-601 | energy.line_power | float32 | ABCD | 1.0 | kW | No |
| 602-603 | energy.cumulative_kwh | float32 | ABCD | 1.0 | kWh | No |

Energy monitoring registers are shared between the packaging and F&B profiles. The energy meter (Schneider PM5xxx) sits at the line level and uses the same Modbus addresses regardless of which production profile is active. In the packaging profile, `energy.line_power` and `energy.cumulative_kwh` also appear at addresses 600-601 / 602-603 (identical to the addresses listed in the packaging holding register table above for backwards compatibility).

---

## F&B Profile Registers

The F&B profile adds six equipment groups to the Modbus register map. Addresses are allocated from HR 1000 onwards to avoid collision with the packaging registers (HR 100-599) and shared energy registers (HR 600-603).

### Holding Registers — Mixer (FC03 Read, FC06/FC16 Write)

Allen-Bradley CompactLogix PLC accessed via Modbus TCP gateway. **Byte order: CDAB** (Allen-Bradley word swap).

| Address | Signal | Type | Byte Order | Scaling | Units | Writable |
|---------|--------|------|------------|---------|-------|----------|
| 1000-1001 | mixer.speed | float32 | CDAB | 1.0 | RPM | No |
| 1002-1003 | mixer.torque | float32 | CDAB | 1.0 | % | No |
| 1004-1005 | mixer.batch_temp | float32 | CDAB | 1.0 | C | No |
| 1006-1007 | mixer.batch_weight | float32 | CDAB | 1.0 | kg | No |
| 1010-1011 | mixer.mix_time_elapsed | uint32 | CDAB | 1 | s | No |

### Holding Registers — Oven (FC03 Read, FC06/FC16 Write)

Eurotherm temperature controllers (one per zone) accessed via Modbus TCP. **Byte order: ABCD** (Eurotherm standard).

| Address | Signal | Type | Byte Order | Scaling | Units | Writable |
|---------|--------|------|------------|---------|-------|----------|
| 1100-1101 | oven.zone_1_temp | float32 | ABCD | 1.0 | C | No |
| 1102-1103 | oven.zone_2_temp | float32 | ABCD | 1.0 | C | No |
| 1104-1105 | oven.zone_3_temp | float32 | ABCD | 1.0 | C | No |
| 1110-1111 | oven.zone_1_setpoint | float32 | ABCD | 1.0 | C | Yes |
| 1112-1113 | oven.zone_2_setpoint | float32 | ABCD | 1.0 | C | Yes |
| 1114-1115 | oven.zone_3_setpoint | float32 | ABCD | 1.0 | C | Yes |
| 1120-1121 | oven.belt_speed | float32 | ABCD | 1.0 | m/min | No |
| 1122-1123 | oven.product_core_temp | float32 | ABCD | 1.0 | C | No |
| 1124-1125 | oven.humidity_zone_2 | float32 | ABCD | 1.0 | %RH | No |

### Holding Registers — Filler (FC03 Read)

Siemens S7-1200 PLC. **Byte order: ABCD**.

| Address | Signal | Type | Byte Order | Scaling | Units | Writable |
|---------|--------|------|------------|---------|-------|----------|
| 1200-1201 | filler.hopper_level | float32 | ABCD | 1.0 | % | No |

> The remaining filler signals (line_speed, fill_weight, fill_target, fill_deviation, packs_produced, reject_count, state) are served exclusively via OPC-UA. Only `filler.hopper_level` uses Modbus.

### Holding Registers — Sealer (FC03 Read)

Siemens S7-1200 PLC (or integrated sealer controller). **Byte order: ABCD**.

| Address | Signal | Type | Byte Order | Scaling | Units | Writable |
|---------|--------|------|------------|---------|-------|----------|
| 1300-1301 | sealer.seal_temp | float32 | ABCD | 1.0 | C | No |
| 1302-1303 | sealer.seal_pressure | float32 | ABCD | 1.0 | bar | No |
| 1304-1305 | sealer.seal_dwell | float32 | ABCD | 1.0 | s | No |
| 1306-1307 | sealer.gas_co2_pct | float32 | ABCD | 1.0 | % | No |
| 1308-1309 | sealer.gas_n2_pct | float32 | ABCD | 1.0 | % | No |
| 1310-1311 | sealer.vacuum_level | float32 | ABCD | 1.0 | bar | No |

### Holding Registers — Chiller (FC03 Read, FC06/FC16 Write)

Danfoss controller accessed via Modbus TCP. **Byte order: ABCD**.

| Address | Signal | Type | Byte Order | Scaling | Units | Writable |
|---------|--------|------|------------|---------|-------|----------|
| 1400-1401 | chiller.room_temp | float32 | ABCD | 1.0 | C | No |
| 1402-1403 | chiller.setpoint | float32 | ABCD | 1.0 | C | Yes |
| 1404-1405 | chiller.suction_pressure | float32 | ABCD | 1.0 | bar | No |
| 1406-1407 | chiller.discharge_pressure | float32 | ABCD | 1.0 | bar | No |

### Holding Registers — CIP (FC03 Read)

Siemens S7-1200 PLC. **Byte order: ABCD**.

| Address | Signal | Type | Byte Order | Scaling | Units | Writable |
|---------|--------|------|------------|---------|-------|----------|
| 1500-1501 | cip.wash_temp | float32 | ABCD | 1.0 | C | No |
| 1502-1503 | cip.flow_rate | float32 | ABCD | 1.0 | L/min | No |
| 1504-1505 | cip.conductivity | float32 | ABCD | 1.0 | mS/cm | No |
| 1506-1507 | cip.cycle_time_elapsed | uint32 | ABCD | 1 | s | No |

### Input Registers — F&B (FC04 Read-Only)

Eurotherm-style int16 x10 scaling for oven temperature registers. This mirrors the pattern used for press dryer temperatures in the packaging profile.

| Address | Signal | Type | Scaling | Units |
|---------|--------|------|---------|-------|
| 100 | oven.zone_1_temp | int16 | x10 | C |
| 101 | oven.zone_2_temp | int16 | x10 | C |
| 102 | oven.zone_3_temp | int16 | x10 | C |
| 103 | oven.zone_1_setpoint | int16 | x10 | C |
| 104 | oven.zone_2_setpoint | int16 | x10 | C |
| 105 | oven.zone_3_setpoint | int16 | x10 | C |
| 106 | oven.product_core_temp | int16 | x10 | C |
| 110 | chiller.room_temp | int16 | x10 | C |
| 111 | chiller.setpoint | int16 | x10 | C |
| 115 | cip.wash_temp | int16 | x10 | C |
| 120-121 | energy.line_power | float32 | 1.0 | kW |

### Coils — F&B (FC01 Read, FC05/FC15 Write)

| Address | Signal | Description |
|---------|--------|-------------|
| 100 | mixer.lid_closed | Safety interlock: mixer lid state (1 = closed) |
| 101 | chiller.compressor_state | Compressor running (1 = on) |
| 102 | chiller.defrost_active | Defrost cycle active (1 = defrosting) |

### Discrete Inputs — F&B (FC02 Read-Only)

| Address | Signal | Description |
|---------|--------|-------------|
| 100 | chiller.door_open | Cold room door state (1 = open) |

---

## Address Map Summary

| Address Range | Profile | Equipment |
|---------------|---------|-----------|
| HR 100-199 | Packaging | Flexographic Press (process values) |
| HR 200-299 | Packaging | Flexographic Press (counters, state) |
| HR 300-399 | Packaging | Flexographic Press (drive, nip, reels) |
| HR 400-499 | Packaging | Laminator |
| HR 500-599 | Packaging | Slitter |
| HR 600-699 | Shared | Energy monitoring |
| HR 700-899 | — | Reserved for future equipment |
| HR 900-999 | — | Reserved for simulator control |
| HR 1000-1099 | F&B | Mixer (CDAB byte order) |
| HR 1100-1199 | F&B | Oven |
| HR 1200-1299 | F&B | Filler |
| HR 1300-1399 | F&B | Sealer |
| HR 1400-1499 | F&B | Chiller |
| HR 1500-1599 | F&B | CIP |
| Coils 0-99 | Packaging | Press, laminator, slitter |
| Coils 100-199 | F&B | Mixer, chiller |
| DI 0-99 | Packaging | Press |
| DI 100-199 | F&B | Chiller |
| IR 0-99 | Packaging | Press dryer temps, laminator temps, energy |
| IR 100-199 | F&B | Oven temps, chiller temps, CIP temps, energy |
