# Appendix A: Full Modbus Register Map

## Holding Registers (FC03 Read, FC06/FC16 Write)

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
| 600-601 | energy.line_power | float32 | ABCD | 1.0 | kW | No |
| 602-603 | energy.cumulative_kwh | float32 | ABCD | 1.0 | kWh | No |

Addresses 0-99 and 700+ are reserved for future equipment.
Addresses 900-999 are reserved for simulator control registers (e.g., trigger scenario, set time scale).

## Input Registers (FC04 Read-Only)

| Address | Signal | Type | Scaling | Units |
|---------|--------|------|---------|-------|
| 0 | press.dryer_temp_zone_1 | int16 | x10 | C |
| 1 | press.dryer_temp_zone_2 | int16 | x10 | C |
| 2 | press.dryer_temp_zone_3 | int16 | x10 | C |
| 3 | press.ink_temperature | int16 | x10 | C |
| 4 | laminator.nip_temp | int16 | x10 | C |
| 5 | laminator.oven_temp | int16 | x10 | C |
| 10-11 | energy.line_power | float32 | 1.0 | kW |

## Coils (FC01 Read, FC05/FC15 Write)

| Address | Signal | Description |
|---------|--------|-------------|
| 0 | press.running | Machine state = Running (2) |
| 1 | press.fault_active | Machine state = Fault (4) |
| 2 | press.emergency_stop | E-stop active |
| 3 | press.web_break | Web break detected |
| 4 | laminator.running | Laminator running |
| 5 | slitter.running | Slitter running |

## Discrete Inputs (FC02 Read-Only)

| Address | Signal | Description |
|---------|--------|-------------|
| 0 | press.guard_door_open | Safety guard state |
| 1 | press.material_present | Web material at infeed |
| 2 | press.cycle_complete | Toggles per impression cycle |
