# Protocol Endpoints

## 3.1 Modbus TCP

**Server address:** `0.0.0.0:502` (configurable)
**Unit ID:** 1 (configurable, additional unit IDs for multi-slave simulation)
**Byte order:** Big-endian (ABCD) for Siemens-style registers. Configurable to CDAB (word-swapped) for Allen-Bradley emulation.

The Modbus server exposes four register types: holding registers (FC03/FC06/FC16), input registers (FC04), coils (FC01/FC05/FC15), and discrete inputs (FC02).

### 3.1.1 Holding Registers (FC03 Read, FC06/FC16 Write)

Holding registers contain process values, setpoints, counters, and state variables. Float32 values occupy two consecutive registers (high word first in ABCD order). Uint32 counters occupy two consecutive registers.

**Press registers:**

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
| 210 | press.machine_state | uint16 | 1 | enum | 0-5, see state table |
| 300-301 | press.main_drive_current | float32 | 1.0 | A | |
| 302-303 | press.main_drive_speed | float32 | 1.0 | RPM | |
| 310-311 | press.nip_pressure | float32 | 1.0 | bar | |
| 320-321 | press.unwind_diameter | float32 | 1.0 | mm | Decreases during run |
| 322-323 | press.rewind_diameter | float32 | 1.0 | mm | Increases during run |

**Laminator registers:**

| Address | Signal | Data Type | Scaling | Units |
|---------|--------|-----------|---------|-------|
| 400-401 | laminator.nip_temp | float32 | 1.0 | C |
| 402-403 | laminator.nip_pressure | float32 | 1.0 | bar |
| 404-405 | laminator.oven_temp | float32 | 1.0 | C |
| 406-407 | laminator.web_speed | float32 | 1.0 | m/min |
| 408-409 | laminator.adhesive_weight | float32 | 1.0 | g/m2 |

**Slitter registers:**

| Address | Signal | Data Type | Scaling | Units |
|---------|--------|-----------|---------|-------|
| 500-501 | slitter.speed | float32 | 1.0 | m/min |
| 502-503 | slitter.web_tension | float32 | 1.0 | N |
| 510-511 | slitter.reel_count | uint32 | 1 | count |

**Energy registers:**

| Address | Signal | Data Type | Scaling | Units |
|---------|--------|-----------|---------|-------|
| 600-601 | energy.line_power | float32 | 1.0 | kW |
| 602-603 | energy.cumulative_kwh | float32 | 1.0 | kWh |

### 3.1.2 Input Registers (FC04 Read-Only)

Input registers mirror selected holding register values with different data encoding. This simulates the common industrial pattern where analog inputs from 4-20mA sensors are exposed as input registers with integer scaling.

| Address | Signal | Data Type | Scaling | Units | Notes |
|---------|--------|-----------|---------|-------|-------|
| 0 | press.dryer_temp_zone_1 | int16 | x10 | C | 850 = 85.0C |
| 1 | press.dryer_temp_zone_2 | int16 | x10 | C | Eurotherm-style encoding |
| 2 | press.dryer_temp_zone_3 | int16 | x10 | C | |
| 3 | press.ink_temperature | int16 | x10 | C | |
| 4 | laminator.nip_temp | int16 | x10 | C | |
| 5 | laminator.oven_temp | int16 | x10 | C | |
| 10-11 | energy.line_power | float32 | 1.0 | kW | Duplicate of HR 600-601 |

The int16 x10 encoding matches the Eurotherm temperature controller pattern documented in the customer profiles research. This is the most common encoding for temperature readings from industrial controllers. CollatrEdge must handle the scaling correctly.

### 3.1.3 Coils (FC01 Read, FC05/FC15 Write)

Coils represent boolean states.

| Address | Signal | Description |
|---------|--------|-------------|
| 0 | press.running | True when machine_state = 2 (Running) |
| 1 | press.fault_active | True when machine_state = 4 (Fault) |
| 2 | press.emergency_stop | True during e-stop condition |
| 3 | press.web_break | True during web break event |
| 4 | laminator.running | True when laminator is running |
| 5 | slitter.running | True when slitter is running |

### 3.1.4 Discrete Inputs (FC02 Read-Only)

Discrete inputs represent physical sensor states.

| Address | Signal | Description |
|---------|--------|-------------|
| 0 | press.guard_door_open | Safety guard door state |
| 1 | press.material_present | Web material detected at infeed |
| 2 | press.cycle_complete | Toggles each impression cycle |

### 3.1.5 Modbus Error Simulation

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

```
Root
  Objects (i=85)
    Server (i=2253)
    PackagingLine (ns=2;s=PackagingLine)
      Press1 (ns=2;s=PackagingLine.Press1)
        LineSpeed (ns=2;s=PackagingLine.Press1.LineSpeed)
        WebTension (ns=2;s=PackagingLine.Press1.WebTension)
        State (ns=2;s=PackagingLine.Press1.State)
        ImpressionCount (ns=2;s=PackagingLine.Press1.ImpressionCount)
        GoodCount (ns=2;s=PackagingLine.Press1.GoodCount)
        WasteCount (ns=2;s=PackagingLine.Press1.WasteCount)
        Registration (ns=2;s=PackagingLine.Press1.Registration)
          ErrorX (ns=2;s=PackagingLine.Press1.Registration.ErrorX)
          ErrorY (ns=2;s=PackagingLine.Press1.Registration.ErrorY)
        Ink (ns=2;s=PackagingLine.Press1.Ink)
          Viscosity (ns=2;s=PackagingLine.Press1.Ink.Viscosity)
          Temperature (ns=2;s=PackagingLine.Press1.Ink.Temperature)
        Dryer (ns=2;s=PackagingLine.Press1.Dryer)
          Zone1 (ns=2;s=PackagingLine.Press1.Dryer.Zone1)
            Temperature (ns=2;s=PackagingLine.Press1.Dryer.Zone1.Temperature)
            Setpoint (ns=2;s=PackagingLine.Press1.Dryer.Zone1.Setpoint)
          Zone2 (ns=2;s=PackagingLine.Press1.Dryer.Zone2)
            Temperature (ns=2;s=PackagingLine.Press1.Dryer.Zone2.Temperature)
            Setpoint (ns=2;s=PackagingLine.Press1.Dryer.Zone2.Setpoint)
          Zone3 (ns=2;s=PackagingLine.Press1.Dryer.Zone3)
            Temperature (ns=2;s=PackagingLine.Press1.Dryer.Zone3.Temperature)
            Setpoint (ns=2;s=PackagingLine.Press1.Dryer.Zone3.Setpoint)
        MainDrive (ns=2;s=PackagingLine.Press1.MainDrive)
          Current (ns=2;s=PackagingLine.Press1.MainDrive.Current)
          Speed (ns=2;s=PackagingLine.Press1.MainDrive.Speed)
        NipPressure (ns=2;s=PackagingLine.Press1.NipPressure)
        Unwind (ns=2;s=PackagingLine.Press1.Unwind)
          Diameter (ns=2;s=PackagingLine.Press1.Unwind.Diameter)
        Rewind (ns=2;s=PackagingLine.Press1.Rewind)
          Diameter (ns=2;s=PackagingLine.Press1.Rewind.Diameter)
      Laminator1 (ns=2;s=PackagingLine.Laminator1)
        NipTemperature (ns=2;s=PackagingLine.Laminator1.NipTemperature)
        NipPressure (ns=2;s=PackagingLine.Laminator1.NipPressure)
        OvenTemperature (ns=2;s=PackagingLine.Laminator1.OvenTemperature)
        WebSpeed (ns=2;s=PackagingLine.Laminator1.WebSpeed)
        AdhesiveWeight (ns=2;s=PackagingLine.Laminator1.AdhesiveWeight)
      Slitter1 (ns=2;s=PackagingLine.Slitter1)
        Speed (ns=2;s=PackagingLine.Slitter1.Speed)
        WebTension (ns=2;s=PackagingLine.Slitter1.WebTension)
        ReelCount (ns=2;s=PackagingLine.Slitter1.ReelCount)
      Energy (ns=2;s=PackagingLine.Energy)
        LinePower (ns=2;s=PackagingLine.Energy.LinePower)
        CumulativeKwh (ns=2;s=PackagingLine.Energy.CumulativeKwh)
```

### 3.2.2 Node Data Types

| Node | OPC-UA Type | Range |
|------|-------------|-------|
| LineSpeed | Double | 0-400 |
| WebTension | Double | 20-500 |
| State | UInt16 | 0-5 |
| ImpressionCount | UInt32 | 0-999999999 |
| GoodCount | UInt32 | 0-999999999 |
| WasteCount | UInt32 | 0-99999 |
| Registration.ErrorX | Double | -0.5 to 0.5 |
| Registration.ErrorY | Double | -0.5 to 0.5 |
| Temperature nodes | Double | varies |
| Setpoint nodes | Double | varies |
| Current | Double | 0-200 |
| Speed (drive) | Double | 0-3000 |
| Pressure nodes | Double | varies |
| Diameter nodes | Double | 50-1500 |
| LinePower | Double | 0-200 |
| CumulativeKwh | Double | 0-999999 |

All analog values use OPC-UA `Double` type. Counters use `UInt32`. State enums use `UInt16`. This matches the pattern documented in the customer profiles research for Siemens S7-1500 OPC-UA servers.

### 3.2.3 Status Codes

Nodes normally report `StatusCode.Good`. Under error injection scenarios:

- `StatusCode.BadCommunicationError` when simulating a PLC communication drop
- `StatusCode.BadSensorFailure` on specific nodes during sensor fault scenarios
- `StatusCode.UncertainLastUsableValue` when the data engine pauses updates to a node (stale data)

The Microsoft OPC PLC Server (documented in the public datasources research) generates alternating Good/Bad/Uncertain status codes. Our simulator replicates this capability for testing CollatrEdge status code handling.

### 3.2.4 OPC-UA Server Implementation

Two options were evaluated:

**Option A: Extend Microsoft OPC PLC Server.** The OPC PLC server (Docker: `mcr.microsoft.com/iotedge/opc-plc`) already generates anomaly patterns, status code changes, and configurable nodes. We would add our factory-specific node structure via its JSON configuration interface.

**Option B: Build custom server using node-opcua or opcua-asyncio.**

We choose Option B. The OPC PLC server is designed for Azure IoT Edge testing and its configuration model does not support the correlated signal generation our factory model requires. A custom server gives full control over node structure, value updates, and status code behaviour.

## 3.3 MQTT

**Broker:** The simulator runs an embedded MQTT broker on `0.0.0.0:1883` (configurable). Alternatively, it publishes to an external broker.
**Protocol version:** MQTT 3.1.1. Optional MQTT 5.0 support.
**Authentication:** Anonymous by default. Configurable username/password.

### 3.3.1 Topic Structure

```
collatr/factory/{site_id}/{line_id}/{equipment}/{signal}
```

Concrete topics:

```
collatr/factory/demo/line3/coder/state
collatr/factory/demo/line3/coder/prints_total
collatr/factory/demo/line3/coder/ink_level
collatr/factory/demo/line3/coder/printhead_temp
collatr/factory/demo/line3/env/ambient_temp
collatr/factory/demo/line3/env/ambient_humidity
collatr/factory/demo/line3/vibration/main_drive_x
collatr/factory/demo/line3/vibration/main_drive_y
collatr/factory/demo/line3/vibration/main_drive_z
```

### 3.3.2 Payload Format

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
- `value`: Numeric value (number type, not string)
- `unit`: Engineering unit string
- `quality`: One of `"good"`, `"uncertain"`, `"bad"`

### 3.3.3 QoS Levels

| Topic Pattern | QoS | Rationale |
|---------------|-----|-----------|
| `*/coder/*` | 1 (at least once) | Coder state changes must not be lost |
| `*/env/*` | 0 (at most once) | Environmental data is low-value, high-frequency |
| `*/vibration/*` | 0 (at most once) | Vibration data is high-frequency, loss-tolerant |

### 3.3.4 Sparkplug B Support

Phase 1 does not implement Sparkplug B. The topic structure above uses plain JSON payloads.

Phase 2 adds a Sparkplug B mode where the simulator publishes to the `spBv1.0/` namespace with protobuf-encoded payloads. The MIMIC MQTT Lab (documented in the public datasources research) already publishes Sparkplug B data to public brokers. CollatrEdge needs to decode Sparkplug B. The simulator will generate it.

When Sparkplug B mode is enabled:

```
spBv1.0/FactoryDemo/NBIRTH/PackagingLine
spBv1.0/FactoryDemo/NDATA/PackagingLine
spBv1.0/FactoryDemo/DBIRTH/PackagingLine/Press1
spBv1.0/FactoryDemo/DDATA/PackagingLine/Press1
spBv1.0/FactoryDemo/DBIRTH/PackagingLine/Coder1
spBv1.0/FactoryDemo/DDATA/PackagingLine/Coder1
```

Each DDATA message contains all metrics for that device in a single protobuf-encoded payload. Metric names match the signal IDs (e.g., `press.line_speed`, `coder.ink_level`).

### 3.3.5 Retained Messages

The most recent message on each topic is published with the retained flag set. This means a new CollatrEdge subscriber immediately receives the latest value for every signal without waiting for the next publish cycle. This matches common industrial MQTT gateway behaviour.
