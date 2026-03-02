# Protocol Endpoints

> The simulator serves data over three protocols simultaneously. Each factory profile (packaging or F&B) populates the same protocol servers with different signal sets. Shared equipment (coder, environmental sensors, energy monitoring) uses the same register addresses, OPC-UA nodes, and MQTT topics regardless of which profile is active. Profile-specific equipment occupies reserved address ranges that do not overlap.
>
> Full register maps, node trees, and topic lists are in [Appendix A](appendix-a-modbus-register-map.md), [Appendix B](appendix-b-opcua-node-tree.md), and [Appendix C](appendix-c-mqtt-topic-map.md). The physical controller infrastructure and network layout are in [Section 3a: Network Topology](03a-network-topology.md).

## 3.1 Modbus TCP

**Server address:** `0.0.0.0:502` (configurable)
**Unit ID:** 1 (configurable, additional unit IDs for multi-slave simulation)
**Byte order:** Big-endian (ABCD) for Siemens-style registers. Configurable to CDAB (word-swapped) for Allen-Bradley emulation.

The Modbus server exposes four register types: holding registers (FC03/FC06/FC16), input registers (FC04), coils (FC01/FC05/FC15), and discrete inputs (FC02).

### 3.1.1 Address Space Layout

The register address space is partitioned by factory profile:

| Address Range | Assignment |
|---|---|
| 0-99 | Reserved (future use) |
| 100-699 | Packaging profile equipment (press, laminator, slitter, energy) |
| 700-899 | Shared equipment (coder signals on Modbus, if any future expansion) |
| 900-999 | Simulator control registers |
| 1000-1999 | F&B profile equipment (mixer, oven, filler, sealer, chiller, CIP) |

When the packaging profile is active, addresses 1000-1999 return Modbus exception code 0x02 (Illegal Data Address). When the F&B profile is active, addresses 100-599 return the same exception. Energy registers (600-699) are always active regardless of profile. This simulates the real-world scenario where a CollatrEdge agent discovers which registers exist on a target PLC.

### 3.1.2 Holding Registers (FC03 Read, FC06/FC16 Write)

Holding registers contain process values, setpoints, counters, and state variables. Float32 values occupy two consecutive registers (high word first in ABCD order). Uint32 counters occupy two consecutive registers.

#### Packaging Profile Registers

**Press registers (HR 100-399):**

| Address | Signal | Data Type | Scaling | Units | Notes |
|---------|--------|-----------|---------|-------|-------|
| 100-101 | press.line_speed | float32 | 1.0 | m/min | ABCD byte order |
| 102-103 | press.web_tension | float32 | 1.0 | N | Also on OPC-UA |
| 110-111 | press.ink_viscosity | float32 | 1.0 | seconds | Zahn cup equivalent |
| 112-113 | press.ink_temperature | float32 | 1.0 | C | |
| 120-121 | press.dryer_temp_zone_1 | float32 | 1.0 | C | Actual temperature |
| 122-123 | press.dryer_temp_zone_2 | float32 | 1.0 | C | |
| 124-125 | press.dryer_temp_zone_3 | float32 | 1.0 | C | |
| 140-141 | press.dryer_setpoint_zone_1 | float32 | 1.0 | C | Writable by client |
| 142-143 | press.dryer_setpoint_zone_2 | float32 | 1.0 | C | Writable by client |
| 144-145 | press.dryer_setpoint_zone_3 | float32 | 1.0 | C | Writable by client |
| 200-201 | press.impression_count | uint32 | 1 | count | Monotonic counter |
| 202-203 | press.good_count | uint32 | 1 | count | Monotonic counter |
| 204-205 | press.waste_count | uint32 | 1 | count | Monotonic counter |
| 210 | press.machine_state | uint16 | 1 | enum | 0-5, see [02](02-simulated-factory-layout.md#22-equipment-flexographic-press) |
| 211 | press.fault_code | uint16 | 1 | code | |
| 300-301 | press.main_drive_current | float32 | 1.0 | A | |
| 302-303 | press.main_drive_speed | float32 | 1.0 | RPM | |
| 310-311 | press.nip_pressure | float32 | 1.0 | bar | |
| 320-321 | press.unwind_diameter | float32 | 1.0 | mm | Decreases during run |
| 322-323 | press.rewind_diameter | float32 | 1.0 | mm | Increases during run |

**Laminator registers (HR 400-499):**

| Address | Signal | Data Type | Scaling | Units |
|---------|--------|-----------|---------|-------|
| 400-401 | laminator.nip_temp | float32 | 1.0 | C |
| 402-403 | laminator.nip_pressure | float32 | 1.0 | bar |
| 404-405 | laminator.tunnel_temp | float32 | 1.0 | C |
| 406-407 | laminator.web_speed | float32 | 1.0 | m/min |
| 408-409 | laminator.adhesive_weight | float32 | 1.0 | g/m2 |

**Slitter registers (HR 500-599):**

| Address | Signal | Data Type | Scaling | Units |
|---------|--------|-----------|---------|-------|
| 500-501 | slitter.speed | float32 | 1.0 | m/min |
| 502-503 | slitter.web_tension | float32 | 1.0 | N |
| 510-511 | slitter.reel_count | uint32 | 1 | count |

#### F&B Profile Registers

The F&B equipment uses the Allen-Bradley CompactLogix convention for the mixer (CDAB byte order) and Siemens/Eurotherm convention for everything else (ABCD). This intentional mix tests CollatrEdge's per-device byte order configuration.

**Mixer registers (HR 1000-1099, CDAB byte order):**

| Address | Signal | Data Type | Byte Order | Scaling | Units | Notes |
|---------|--------|-----------|------------|---------|-------|-------|
| 1000-1001 | mixer.speed | float32 | CDAB | 1.0 | RPM | Allen-Bradley convention |
| 1002-1003 | mixer.torque | float32 | CDAB | 1.0 | % | |
| 1004-1005 | mixer.batch_temp | float32 | CDAB | 1.0 | C | |
| 1006-1007 | mixer.batch_weight | float32 | CDAB | 1.0 | kg | Load cells |
| 1010-1011 | mixer.mix_time_elapsed | float32 | CDAB | 1.0 | s | |

**Oven registers (HR 1100-1199, ABCD byte order):**

| Address | Signal | Data Type | Scaling | Units | Notes |
|---------|--------|-----------|---------|-------|-------|
| 1100-1101 | oven.zone_1_temp | float32 | 1.0 | C | Actual temperature |
| 1102-1103 | oven.zone_2_temp | float32 | 1.0 | C | |
| 1104-1105 | oven.zone_3_temp | float32 | 1.0 | C | |
| 1110-1111 | oven.zone_1_setpoint | float32 | 1.0 | C | Writable |
| 1112-1113 | oven.zone_2_setpoint | float32 | 1.0 | C | Writable |
| 1114-1115 | oven.zone_3_setpoint | float32 | 1.0 | C | Writable |
| 1120-1121 | oven.belt_speed | float32 | 1.0 | m/min | |
| 1122-1123 | oven.product_core_temp | float32 | 1.0 | C | BRC critical control point |
| 1124-1125 | oven.humidity_zone_2 | float32 | 1.0 | %RH | |

**Filler registers (HR 1200-1299):**

| Address | Signal | Data Type | Scaling | Units |
|---------|--------|-----------|---------|-------|
| 1200-1201 | filler.hopper_level | float32 | 1.0 | % |

Note: Most filler signals are on OPC-UA. Only the hopper level (analog sensor via ADC) is on Modbus.

**Sealer registers (HR 1300-1399):**

| Address | Signal | Data Type | Scaling | Units |
|---------|--------|-----------|---------|-------|
| 1300-1301 | sealer.seal_temp | float32 | 1.0 | C |
| 1302-1303 | sealer.seal_pressure | float32 | 1.0 | bar |
| 1304-1305 | sealer.seal_dwell | float32 | 1.0 | s |
| 1306-1307 | sealer.gas_co2_pct | float32 | 1.0 | % |
| 1308-1309 | sealer.gas_n2_pct | float32 | 1.0 | % |
| 1310-1311 | sealer.vacuum_level | float32 | 1.0 | bar |

**Chiller registers (HR 1400-1499):**

| Address | Signal | Data Type | Scaling | Units |
|---------|--------|-----------|---------|-------|
| 1400-1401 | chiller.room_temp | float32 | 1.0 | C |
| 1402-1403 | chiller.setpoint | float32 | 1.0 | C |
| 1410-1411 | chiller.suction_pressure | float32 | 1.0 | bar |
| 1412-1413 | chiller.discharge_pressure | float32 | 1.0 | bar |

**CIP registers (HR 1500-1599):**

| Address | Signal | Data Type | Scaling | Units |
|---------|--------|-----------|---------|-------|
| 1500-1501 | cip.wash_temp | float32 | 1.0 | C |
| 1502-1503 | cip.flow_rate | float32 | 1.0 | L/min |
| 1504-1505 | cip.conductivity | float32 | 1.0 | mS/cm |
| 1506-1507 | cip.cycle_time_elapsed | float32 | 1.0 | s |

#### Shared Registers (Both Profiles)

**Energy registers (HR 600-699):**

| Address | Signal | Data Type | Scaling | Units |
|---------|--------|-----------|---------|-------|
| 600-601 | energy.line_power | float32 | 1.0 | kW |
| 602-603 | energy.cumulative_kwh | float32 | 1.0 | kWh |

### 3.1.3 Input Registers (FC04 Read-Only)

Input registers mirror selected holding register values with different data encoding. This simulates the common industrial pattern where analog inputs from 4-20mA sensors are exposed as input registers with integer scaling.

#### Packaging Profile

| Address | Signal | Data Type | Scaling | Units | Notes |
|---------|--------|-----------|---------|-------|-------|
| 0 | press.dryer_temp_zone_1 | int16 | x10 | C | 850 = 85.0C |
| 1 | press.dryer_temp_zone_2 | int16 | x10 | C | Eurotherm-style encoding |
| 2 | press.dryer_temp_zone_3 | int16 | x10 | C | |
| 3 | press.ink_temperature | int16 | x10 | C | |
| 4 | laminator.nip_temp | int16 | x10 | C | |
| 5 | laminator.tunnel_temp | int16 | x10 | C | |
| 10-11 | energy.line_power | float32 | 1.0 | kW | Duplicate of HR 600-601 |

#### F&B Profile

| Address | Signal | Data Type | Scaling | Units | Notes |
|---------|--------|-----------|---------|-------|-------|
| 100 | oven.zone_1_temp | int16 | x10 | C | Eurotherm-style |
| 101 | oven.zone_2_temp | int16 | x10 | C | |
| 102 | oven.zone_3_temp | int16 | x10 | C | |
| 103 | oven.product_core_temp | int16 | x10 | C | BRC critical |
| 104 | cip.wash_temp | int16 | x10 | C | |
| 110-111 | energy.line_power | float32 | 1.0 | kW | Shared with packaging |

The int16 x10 encoding matches the Eurotherm temperature controller pattern documented in the customer profiles research. This is the most common encoding for temperature readings from industrial controllers. CollatrEdge must handle the scaling correctly. The F&B profile uses input register addresses 100+ to avoid collision with the packaging profile addresses 0-11.

### 3.1.4 Coils (FC01 Read, FC05/FC15 Write)

Coils represent boolean states.

#### Packaging Profile

| Address | Signal | Description |
|---------|--------|-------------|
| 0 | press.running | True when machine_state = 2 (Running) |
| 1 | press.fault_active | True when machine_state = 4 (Fault) |
| 2 | press.emergency_stop | True during e-stop condition |
| 3 | press.web_break | True during web break event |
| 4 | laminator.running | True when laminator is running |
| 5 | slitter.running | True when slitter is running |

#### F&B Profile

| Address | Signal | Description |
|---------|--------|-------------|
| 100 | mixer.lid_closed | Safety interlock: true when lid is closed |
| 101 | chiller.compressor_state | True when compressor is running |
| 102 | chiller.defrost_active | True during defrost cycle |

### 3.1.5 Discrete Inputs (FC02 Read-Only)

Discrete inputs represent physical sensor states.

#### Packaging Profile

| Address | Signal | Description |
|---------|--------|-------------|
| 0 | press.guard_door_open | Safety guard door state |
| 1 | press.material_present | Web material detected at infeed |
| 2 | press.cycle_complete | Toggles each impression cycle |

#### F&B Profile

| Address | Signal | Description |
|---------|--------|-------------|
| 100 | chiller.door_open | Cold room door state |

### 3.1.6 Multi-Slave Simulation

The F&B oven uses Eurotherm temperature controllers that are addressed by Modbus slave ID (one slave per zone). When multi-slave mode is enabled:

| Unit ID | Equipment | Notes |
|---------|-----------|-------|
| 1 | Default (all registers) | Standard single-slave mode |
| 11 | Oven Zone 1 | IR 0 = zone 1 PV, IR 1 = zone 1 SP, IR 2 = output power |
| 12 | Oven Zone 2 | IR 0 = zone 2 PV, IR 1 = zone 2 SP, IR 2 = output power |
| 13 | Oven Zone 3 | IR 0 = zone 3 PV, IR 1 = zone 3 SP, IR 2 = output power |

This replicates the Eurotherm addressing pattern where each controller has the same register layout (PV at IR 0, SP at IR 1) but a different slave ID. CollatrEdge must be configured with one Modbus input per zone. This is the most common multi-device Modbus pattern in UK food manufacturing.

### 3.1.7 Modbus Error Simulation

The server supports configurable error injection:

- **Exception response on specific registers.** Configure register addresses that return Modbus exception code 0x02 (Illegal Data Address) or 0x04 (Slave Device Failure) at a configurable probability (default: 0.1% of reads).
- **Timeout simulation.** Configure a probability of not responding at all (default: 0.05% of reads), forcing the client to handle timeouts.
- **Slow response.** Configure a response delay range (default: 0-50ms, configurable to 0-2000ms) to simulate network latency or slow PLC scan cycles.

## 3.2 OPC-UA

**Server endpoint:** `opc.tcp://0.0.0.0:4840` (configurable)
**Security:** Accept all client certificates (development mode). Configurable to require authentication.
**Authentication:** Anonymous access enabled. Optional username/password: `collatr` / `collatr123`.

### 3.2.1 Namespace Structure

The server uses namespace index 2 for the factory simulation. The node hierarchy follows the OPC-UA for Machinery companion specification (OPC 40001) structure where practical.

Both factory profiles are present in the namespace simultaneously. Nodes for the inactive profile report `StatusCode.BadNotReadable` and have `AccessLevel` set to 0. This allows an OPC-UA client to browse the full address space and discover which nodes are active.

#### Packaging Profile Nodes

```
Root
  Objects (i=85)
    Server (i=2253)
    PackagingLine (ns=2;s=PackagingLine)
      Press1 (ns=2;s=PackagingLine.Press1)
        LineSpeed              Double, m/min
        WebTension             Double, N
        State                  UInt16, enum
        FaultCode              UInt16
        ImpressionCount        UInt32
        GoodCount              UInt32
        WasteCount             UInt32
        Registration
          ErrorX               Double, mm
          ErrorY               Double, mm
        Ink
          Viscosity            Double, seconds
          Temperature          Double, C
        Dryer
          Zone1
            Temperature        Double, C
            Setpoint           Double, C (writable)
          Zone2
            Temperature        Double, C
            Setpoint           Double, C (writable)
          Zone3
            Temperature        Double, C
            Setpoint           Double, C (writable)
        MainDrive
          Current              Double, A
          Speed                Double, RPM
        NipPressure            Double, bar
        Unwind
          Diameter             Double, mm
        Rewind
          Diameter             Double, mm
      Laminator1 (ns=2;s=PackagingLine.Laminator1)
        NipTemperature         Double, C
        NipPressure            Double, bar
        TunnelTemperature      Double, C
        WebSpeed               Double, m/min
        AdhesiveWeight         Double, g/m2
      Slitter1 (ns=2;s=PackagingLine.Slitter1)
        Speed                  Double, m/min
        WebTension             Double, N
        ReelCount              UInt32
      Energy (ns=2;s=PackagingLine.Energy)
        LinePower              Double, kW
        CumulativeKwh          Double, kWh
```

#### F&B Profile Nodes

```
    FoodBevLine (ns=2;s=FoodBevLine)
      Mixer1 (ns=2;s=FoodBevLine.Mixer1)
        State                  UInt16, enum (0-5)
        BatchId                String
      Oven1 (ns=2;s=FoodBevLine.Oven1)
        State                  UInt16, enum (0-4)
      Filler1 (ns=2;s=FoodBevLine.Filler1)
        LineSpeed              Double, packs/min
        FillWeight             Double, g
        FillTarget             Double, g
        FillDeviation          Double, g
        PacksProduced          UInt32
        RejectCount            UInt32
        State                  UInt16, enum (0-4)
      QC1 (ns=2;s=FoodBevLine.QC1)
        ActualWeight           Double, g
        OverweightCount        UInt32
        UnderweightCount       UInt32
        MetalDetectTrips       UInt32
        Throughput             Double, items/min
        RejectTotal            UInt32
      CIP1 (ns=2;s=FoodBevLine.CIP1)
        State                  UInt16, enum (0-5)
      Energy (ns=2;s=FoodBevLine.Energy)
        LinePower              Double, kW
        CumulativeKwh          Double, kWh
```

#### Shared Nodes (Both Profiles)

Energy nodes sit under each profile tree, not at the top level. See the packaging and F&B node trees above for their placement (PackagingLine.Energy, FoodBevLine.Energy). Both profiles expose the same two signals: LinePower (Double, kW) and CumulativeKwh (Double, kWh). See Appendix B for the full node paths.

The Energy node sits under each profile tree (PackagingLine.Energy, FoodBevLine.Energy) because energy meters are per-line in real factories. See Appendix B for the full node paths. When no factory profile is loaded, neither Energy node is active.

### 3.2.2 Node Data Types

| Node | OPC-UA Type | Range | Profile |
|------|-------------|-------|---------|
| All analog values | Double | varies | Both |
| All counters | UInt32 | 0-999,999,999 | Both |
| All state enums | UInt16 | varies | Both |
| BatchId | String | — | F&B |
| LinePower | Double | 0-200 | Shared |
| CumulativeKwh | Double | 0-999,999 | Shared |

All analog values use OPC-UA `Double` type. Counters use `UInt32`. State enums use `UInt16`. String nodes use `String`. This matches the pattern documented in the customer profiles research for Siemens S7-1500 OPC-UA servers.

### 3.2.3 Status Codes

Nodes normally report `StatusCode.Good`. Under error injection scenarios:

- `StatusCode.BadCommunicationError` when simulating a PLC communication drop
- `StatusCode.BadSensorFailure` on specific nodes during sensor fault scenarios
- `StatusCode.UncertainLastUsableValue` when the data engine pauses updates to a node (stale data)
- `StatusCode.BadNotReadable` for nodes belonging to the inactive factory profile

### 3.2.4 OPC-UA Server Implementation

We use a custom server built with `opcua-asyncio` (Python). The Microsoft OPC PLC Server was evaluated but its configuration model does not support the correlated signal generation our factory model requires. A custom server gives full control over node structure, value updates, and status code behaviour.

## 3.3 MQTT

**Broker:** The simulator runs an embedded MQTT broker on `0.0.0.0:1883` (configurable). Alternatively, it publishes to an external broker.
**Protocol version:** MQTT 3.1.1. Optional MQTT 5.0 support.
**Authentication:** Anonymous by default. Configurable username/password.

### 3.3.1 Topic Structure

```
collatr/factory/{site_id}/{line_id}/{equipment}/{signal}
```

The `line_id` distinguishes factory profiles:

| Profile | line_id | Example |
|---|---|---|
| Packaging | `packaging1` (configurable) | `collatr/factory/demo/packaging1/coder/state` |
| F&B | `foodbev1` (configurable) | `collatr/factory/demo/foodbev1/coder/state` |

### 3.3.2 Packaging Profile Topics

**Coder topics (11 signals):**

| Topic | Signal | QoS | Retain | Publish Rate |
|-------|--------|-----|--------|-------------|
| `.../coder/state` | coder.state | 1 | Yes | Event-driven |
| `.../coder/prints_total` | coder.prints_total | 1 | Yes | Event-driven |
| `.../coder/ink_level` | coder.ink_level | 0 | Yes | 60s |
| `.../coder/printhead_temp` | coder.printhead_temp | 0 | Yes | 30s |
| `.../coder/ink_pump_speed` | coder.ink_pump_speed | 0 | Yes | 5s |
| `.../coder/ink_pressure` | coder.ink_pressure | 0 | Yes | 5s |
| `.../coder/ink_viscosity_actual` | coder.ink_viscosity_actual | 0 | Yes | 30s |
| `.../coder/supply_voltage` | coder.supply_voltage | 0 | Yes | 60s |
| `.../coder/ink_consumption_ml` | coder.ink_consumption_ml | 0 | Yes | 60s |
| `.../coder/nozzle_health` | coder.nozzle_health | 1 | Yes | Event-driven |
| `.../coder/gutter_fault` | coder.gutter_fault | 1 | Yes | Event-driven |

**Environmental topics (2 signals):**

| Topic | Signal | QoS | Retain | Publish Rate |
|-------|--------|-----|--------|-------------|
| `.../env/ambient_temp` | env.ambient_temp | 0 | Yes | 60s |
| `.../env/ambient_humidity` | env.ambient_humidity | 0 | Yes | 60s |

**Vibration topics (3 signals):**

| Topic | Signal | QoS | Retain | Publish Rate |
|-------|--------|-----|--------|-------------|
| `.../vibration/main_drive_x` | vibration.main_drive_x | 0 | No | 1s |
| `.../vibration/main_drive_y` | vibration.main_drive_y | 0 | No | 1s |
| `.../vibration/main_drive_z` | vibration.main_drive_z | 0 | No | 1s |

### 3.3.3 F&B Profile Topics

The F&B profile publishes coder and environmental topics (shared equipment) but does not have vibration topics. The coder topic list is identical to the packaging profile.

**Coder topics (11 signals):** Same as packaging profile, using the F&B `line_id`.

**Environmental topics (2 signals):** Same as packaging profile, using the F&B `line_id`.

The F&B profile does not include vibration MQTT signals. The F&B factory layout has no vibration monitoring equipment group. If vibration monitoring is added to the F&B profile in future, it would use the same topic structure.

### 3.3.4 Payload Format

Each message is a JSON object:

```json
{
  "timestamp": "2026-03-01T14:30:00.000Z",
  "value": 42.7,
  "unit": "C",
  "quality": "good"
}
```

Fields:
- `timestamp`: ISO 8601 UTC timestamp of generation
- `value`: Numeric value (number type, not string). Boolean signals use 0/1.
- `unit`: Engineering unit string
- `quality`: One of `"good"`, `"uncertain"`, `"bad"`

### 3.3.5 QoS Levels

| Topic Pattern | QoS | Rationale |
|---------------|-----|-----------|
| `*/coder/state`, `*/coder/nozzle_health`, `*/coder/gutter_fault` | 1 (at least once) | State changes and fault signals must not be lost |
| `*/coder/prints_total` | 1 (at least once) | Counter events must not be lost |
| `*/coder/*` (remaining) | 0 (at most once) | Periodic analog readings are loss-tolerant |
| `*/env/*` | 0 (at most once) | Environmental data is low-value, high-frequency |
| `*/vibration/*` | 0 (at most once) | Vibration data is high-frequency, loss-tolerant |

### 3.3.6 Batch Vibration Topic (Packaging Only)

For high-frequency vibration data, an alternative batch topic publishes all three axes in one message:

```
collatr/factory/demo/packaging1/vibration/main_drive
```

```json
{
  "timestamp": "2026-03-01T14:30:00.000Z",
  "x": 4.2,
  "y": 3.8,
  "z": 5.1,
  "unit": "mm/s",
  "quality": "good"
}
```

This reduces MQTT message count by 3x for vibration data at the cost of a non-standard payload structure. Both the per-axis and batch formats are published simultaneously by default. The per-axis topics can be disabled via configuration.

### 3.3.7 Sparkplug B Support

Phase 1 does not implement Sparkplug B. The topic structure above uses plain JSON payloads.

Phase 2 adds a Sparkplug B mode where the simulator publishes to the `spBv1.0/` namespace with protobuf-encoded payloads. When Sparkplug B mode is enabled:

**Packaging profile:**

```
spBv1.0/FactoryDemo/NBIRTH/PackagingLine
spBv1.0/FactoryDemo/NDATA/PackagingLine
spBv1.0/FactoryDemo/DBIRTH/PackagingLine/Press1
spBv1.0/FactoryDemo/DDATA/PackagingLine/Press1
spBv1.0/FactoryDemo/DBIRTH/PackagingLine/Coder1
spBv1.0/FactoryDemo/DDATA/PackagingLine/Coder1
```

**F&B profile:**

```
spBv1.0/FactoryDemo/NBIRTH/FoodBevLine
spBv1.0/FactoryDemo/NDATA/FoodBevLine
spBv1.0/FactoryDemo/DBIRTH/FoodBevLine/Mixer1
spBv1.0/FactoryDemo/DDATA/FoodBevLine/Mixer1
spBv1.0/FactoryDemo/DBIRTH/FoodBevLine/Filler1
spBv1.0/FactoryDemo/DDATA/FoodBevLine/Filler1
spBv1.0/FactoryDemo/DBIRTH/FoodBevLine/Coder1
spBv1.0/FactoryDemo/DDATA/FoodBevLine/Coder1
```

Each DDATA message contains all metrics for that device in a single protobuf-encoded payload. Metric names match the signal IDs (e.g., `press.line_speed`, `coder.ink_level`, `filler.fill_weight`).

### 3.3.8 Retained Messages

The most recent message on each topic is published with the retained flag set. This means a new CollatrEdge subscriber immediately receives the latest value for every signal without waiting for the next publish cycle. This matches common industrial MQTT gateway behaviour.

## 3.4 Protocol Coverage by Factory Profile

| Signal | Packaging | F&B |
|---|---|---|
| **Modbus HR** | 19 registers (press, laminator, slitter) | 31 registers (mixer, oven, filler, sealer, chiller, CIP) |
| **Modbus HR (shared)** | 2 (energy) | 2 (energy) |
| **Modbus IR** | 7 (Eurotherm temps + energy) | 6 (oven temps + CIP temp + energy) |
| **Modbus coils** | 6 (press, laminator, slitter) | 3 (mixer, chiller) |
| **Modbus DI** | 3 (press) | 1 (chiller door) |
| **OPC-UA** | 4 unique + dual (press tension, reg error, slitter tension) + shared (laminator, slitter, energy) | 17 (mixer, oven, filler, QC, CIP) + shared (energy) |
| **MQTT** | 16 (coder ×11, env ×2, vibration ×3) | 13 (coder ×11, env ×2) |
| **Total signals** | 47 | 65 |

This ensures CollatrEdge exercises all three protocol input plugins (Modbus, OPC-UA, MQTT) regardless of which factory profile is active. The F&B profile has a heavier Modbus and OPC-UA footprint and no vibration MQTT signals, reflecting the equipment mix of a real food factory.
