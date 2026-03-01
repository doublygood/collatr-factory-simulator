# Collatr Factory Simulator: Product Requirements Document

**Version:** 1.0
**Date:** 2026-03-01
**Author:** Dex (Engineering)
**Status:** Draft
**Audience:** Engineering, Product, QA

---

## Table of Contents

1. [Overview and Goals](#1-overview-and-goals)
2. [Simulated Factory Layout](#2-simulated-factory-layout)
3. [Protocol Endpoints](#3-protocol-endpoints)
4. [Data Generation Engine](#4-data-generation-engine)
5. [Scenario System](#5-scenario-system)
6. [Configuration](#6-configuration)
7. [Technology Stack](#7-technology-stack)
8. [Architecture](#8-architecture)
9. [Non-Goals and Out of Scope](#9-non-goals-and-out-of-scope)
10. [Data Quality Realism](#10-data-quality-realism)
11. [Success Criteria](#11-success-criteria)
12. [Appendix A: Full Modbus Register Map](#appendix-a-full-modbus-register-map)
13. [Appendix B: Full OPC-UA Node Tree](#appendix-b-full-opc-ua-node-tree)
14. [Appendix C: Full MQTT Topic Map](#appendix-c-full-mqtt-topic-map)
15. [Appendix D: Configuration Reference](#appendix-d-configuration-reference)

---

## 1. Overview and Goals

### 1.1 What This Is

The Collatr Factory Simulator is a standalone tool that generates synthetic industrial data and serves it over three live protocols: Modbus TCP, OPC-UA, and MQTT. CollatrEdge connects to the simulator the same way it connects to a real factory floor. No code changes. No special modes. The simulator looks and behaves like a packaging and printing factory running a production shift.

### 1.2 Why It Exists

CollatrEdge needs a test target. A real factory is unavailable for development. Public OPC-UA and Modbus servers exist but serve generic data with no industrial context. We need a data source that produces realistic packaging line signals with proper correlations, anomaly patterns, and protocol-specific quirks.

Three use cases drive this project:

**Integration testing.** Engineers connect CollatrEdge to the simulator and verify that data collection works across all three protocols. The simulator produces known patterns. Tests assert that CollatrEdge captures those patterns correctly. Regression tests run against the simulator in CI.

**Demonstrations.** Sales shows the simulator to prospects. The data looks real. Charts show a flexographic press running production. Anomalies appear on schedule. The prospect sees their factory reflected in the demo. This is more convincing than random sine waves.

**Development.** Engineers building new CollatrEdge features need a data source running on localhost. The simulator starts in one command. It produces 40 signals across three protocols. No cloud dependencies. No VPN to a customer site. No waiting for a real machine to produce interesting data.

### 1.3 The Reference Data Constraint

We have access to real CIJ vendor printing equipment data in a local reference database. This data comes from two sources:

**Public schema (VisionLog trial).** 14.8 million rows from AX350i continuous inkjet printers, R-Series vision inspection systems, and Balluff IOLink environmental sensors. Two customer sites (Site A and Site B). Ten months of data. 60-second polling intervals. Event-driven vision streams.

**Equipment telemetry.** 28.7 million metric data points from industrial digital presses. Print head temperatures, pneumatic system pressures, ink pump speeds, production counters. Sub-second sampling on some sensors.

This data is reference material only. We study it. We learn the ranges, distributions, correlations, noise characteristics, and anomaly shapes. We then build synthetic generators that produce original data with the same statistical properties.

**No actual proprietary reference data may be included in or distributed with the simulator.** No raw values. No sampled rows. No replay of real timeseries. The generators produce new data every time they run.

What we learn from the reference data:

- AX350i printer line speeds range from 0 to 638 units with binary printing/not-printing states
- R-Series vision inspection shows 85.6% fail rates during idle periods (no-read failures, not quality failures)
- IOLink BCM0002 sensors report humidity (15-80%), contact temperature (20-40C), vibration RMS (0-50 mm/s), and barometric pressure (990-1030 hPa)
- IOLink BNI0042 static charge sensors report 0-5 kV charged potential
- Digital press print head temperatures cluster at 41-42C with 1C standard deviation
- Pneumatic fill tank levels are the highest-volume signal with cyclic fill/drain patterns
- Ink pump speeds are bimodal: 0 RPM (idle) or 200-500 RPM (active)
- Lung pressure sits at 830-840 mbar during normal operation with 60 mbar standard deviation
- Counter values wrap at specific thresholds (PrintedTotal wraps at 999)
- Temperature sensor Temperatur1 reports 6553.5 (uint16 max / 10) when disconnected, a classic sensor fault pattern
- Main board temperatures are stored in tenths of degrees (divide by 10)
- The customer site had a 6-day duplicate insertion bug producing 190x row duplication
- Camera clock timezones drifted from UTC to BST to US Eastern during the trial

These patterns inform our synthetic generators without including the data itself.

---

## 2. Simulated Factory Layout

### 2.1 Factory Overview

The simulator models a single packaging production line with seven equipment groups. This matches the 40-signal demo spec from the target customer profiles research. The equipment represents what a typical UK flexible packaging converter operates.

```
Raw Material    +-----------+    +----------+    +---------+    +----------+    Finished
   Input   ---->|  Flexo    |--->| Laminator|--->| Slitter |--->| Coding & |---> Product
                |  Press    |    |          |    |         |    | Marking  |     Output
                +-----------+    +----------+    +---------+    +----------+
                     |                |               |              |
                     v                v               v              v
                +-----------+   +-----------+   +-----------+  +-----------+
                | Energy    |   | Environ.  |   | Vibration |  | Vision    |
                | Monitor   |   | Sensors   |   | Monitor   |  | Inspect   |
                +-----------+   +-----------+   +-----------+  +-----------+
```

### 2.2 Equipment: Flexographic Press

The flexographic press is the primary machine. It produces 21 of the 40 signals. It represents a central impression (CI) flexographic press from vendors like BOBST, Soma, or W&H. The PLC is a Siemens S7-1500 serving data over OPC-UA and Modbus TCP.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 1 | `press.line_speed` | Web speed through press | 0-400 | m/min | 1s | Modbus HR + OPC-UA |
| 2 | `press.web_tension` | Web tension at infeed | 20-500 | N | 500ms | OPC-UA |
| 3 | `press.registration_error_x` | Cross-web registration error | -0.5 to +0.5 | mm | 500ms | OPC-UA |
| 4 | `press.registration_error_y` | Around-web registration error | -0.5 to +0.5 | mm | 500ms | OPC-UA |
| 5 | `press.ink_viscosity` | Ink viscosity (Zahn cup equivalent) | 15-60 | seconds | 30s | Modbus HR |
| 6 | `press.ink_temperature` | Ink reservoir temperature | 18-35 | C | 10s | Modbus HR |
| 7 | `press.dryer_temp_zone_1` | Dryer zone 1 actual temp | 40-120 | C | 5s | Modbus HR |
| 8 | `press.dryer_temp_zone_2` | Dryer zone 2 actual temp | 40-120 | C | 5s | Modbus HR |
| 9 | `press.dryer_temp_zone_3` | Dryer zone 3 actual temp | 40-120 | C | 5s | Modbus HR |
| 10 | `press.dryer_setpoint_zone_1` | Dryer zone 1 setpoint | 40-120 | C | event | Modbus HR |
| 11 | `press.dryer_setpoint_zone_2` | Dryer zone 2 setpoint | 40-120 | C | event | Modbus HR |
| 12 | `press.dryer_setpoint_zone_3` | Dryer zone 3 setpoint | 40-120 | C | event | Modbus HR |
| 13 | `press.impression_count` | Total impressions since reset | 0-999,999,999 | count | 1s | Modbus HR |
| 14 | `press.good_count` | Good impressions since reset | 0-999,999,999 | count | 1s | Modbus HR |
| 15 | `press.waste_count` | Waste impressions since reset | 0-99,999 | count | 1s | Modbus HR |
| 16 | `press.machine_state` | Machine operating state | 0-5 | enum | event | OPC-UA + Modbus HR |
| 17 | `press.main_drive_current` | Main drive motor current | 0-200 | A | 1s | Modbus HR |
| 18 | `press.main_drive_speed` | Main drive motor speed | 0-3000 | RPM | 1s | Modbus HR |
| 19 | `press.nip_pressure` | Impression roller nip pressure | 0-10 | bar | 5s | Modbus HR |
| 20 | `press.unwind_diameter` | Unwind reel diameter | 50-1500 | mm | 10s | Modbus HR |
| 21 | `press.rewind_diameter` | Rewind reel diameter | 50-1500 | mm | 10s | Modbus HR |

**Machine state enum:**

| Value | State | Description |
|-------|-------|-------------|
| 0 | Off | Press powered down |
| 1 | Setup | Job changeover, threading, registration alignment |
| 2 | Running | Normal production |
| 3 | Idle | Press stopped, no active job, not in fault |
| 4 | Fault | Active fault condition |
| 5 | Maintenance | Scheduled maintenance activity |

**How reference data informs this equipment:**

The AX350i printer data shows binary printing/not-printing states with `wasPrinted` flags. The press simulator extends this to a full 6-state model reflecting the richer state machine of a flexographic press. The line speed signal in the AX data (`currentLineSpeed`) showed step-function behaviour between 0 and operating speed. The press simulator adds realistic ramp-up curves (0 to target speed over 2-5 minutes) because flexo presses cannot start at full speed. The dryer temperature model draws on the Eurotherm controller patterns described in the DAMADICS actuator benchmark research: PV tracks SP with first-order lag and overshoot.

### 2.3 Equipment: Laminator

The laminator bonds two web materials using adhesive. It produces 5 signals. It represents a solvent-free laminator from vendors like Nordmeccanica or Comexi. Controlled by a Siemens S7-1500 or Schneider Modicon PLC.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 22 | `laminator.nip_temp` | Nip roller temperature | 30-80 | C | 5s | Modbus HR |
| 23 | `laminator.nip_pressure` | Nip roller pressure | 1-8 | bar | 5s | Modbus HR |
| 24 | `laminator.oven_temp` | Adhesive drying oven temp | 40-100 | C | 5s | Modbus HR |
| 25 | `laminator.web_speed` | Laminator web speed | 50-400 | m/min | 1s | Modbus HR |
| 26 | `laminator.adhesive_weight` | Adhesive coat weight | 1.0-5.0 | g/m2 | 30s | Modbus HR |

The laminator web speed tracks the press line speed with a small offset (the laminator processes material after the press). When the press stops, the laminator continues briefly to clear its own web path, then stops.

### 2.4 Equipment: Slitter

The slitter cuts wide rolls into narrow reels. It produces 3 signals.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 27 | `slitter.speed` | Slitting speed | 100-800 | m/min | 1s | Modbus HR |
| 28 | `slitter.web_tension` | Slitter web tension | 10-200 | N | 500ms | OPC-UA |
| 29 | `slitter.reel_count` | Completed reels | 0-9999 | count | event | Modbus HR |

The slitter operates independently from the press. It runs faster (up to 800 m/min vs 400 m/min for the press). It processes rolls that the press produced earlier. Its schedule is offset from press production by hours or shifts.

### 2.5 Equipment: Coding and Marking

The coder is a continuous inkjet printer (modeled on industrial AX-series CIJ patterns). It prints date codes, batch numbers, and barcodes onto the packaging material. It produces 4 signals.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 30 | `coder.state` | Printer operating state | 0-4 | enum | event | MQTT |
| 31 | `coder.prints_total` | Total prints since power-on | 0-999,999,999 | count | event | MQTT |
| 32 | `coder.ink_level` | Ink cartridge level | 0-100 | % | 60s | MQTT |
| 33 | `coder.printhead_temp` | Printhead temperature | 25-50 | C | 30s | MQTT |

**Coder state enum:**

| Value | State |
|-------|-------|
| 0 | Off |
| 1 | Ready |
| 2 | Printing |
| 3 | Fault |
| 4 | Standby |

**How reference data informs this equipment:**

The AX350i data shows `wasPrinted` boolean toggling with production. The print head temperature from IoT platform data (PS_Head_TempFirepulse) clusters at 52C with 2.8C standard deviation and a clean 33-82C range. Our simulator uses a narrower 25-50C range because a CIJ coder runs cooler than a digital press head. The ink level depletes as a function of prints_total. The reference data showed PS_Pnm_InkConsumptionMl accumulating to 4909 ml over production runs. We model ink depletion as a linear function of print count with small random variation in consumption rate per print.

The coder state machine transitions match patterns observed in the AX data. The printer spends most of its time in Ready (1) or Printing (2). It enters Standby (4) during press idle periods. It enters Fault (3) rarely. The reference data showed error rows when the device was unreachable, and the real printer had clear on/off cycling aligned with the production line state.

### 2.6 Equipment: Vision Inspection

The vision inspection system is not one of the 40 primary signals but its behaviour informs the coder and press quality signals. It is modeled on R-Series patterns from the reference data.

The reference data showed a critical pattern: 85.6% fail rate in the vision stream during a typical month. This is not a quality problem. The vision system reports F (Fail) for every read attempt when the line is idle or no product is present. The camera sees nothing, reads nothing, and reports "fail." During active production, the pass rate rises to 80-95%.

This pattern informs the `press.waste_count` signal. When the press is Running (state 2), waste increments slowly (0.5-2% of impressions). When the press transitions to Idle (state 3), no waste is generated. The vision fail rate pattern shows that data from inspection systems requires context to interpret correctly.

### 2.7 Equipment: Environmental Sensors

Environmental sensors monitor the factory floor conditions. They produce 2 signals. They represent Balluff IOLink sensors connected via an IOLink master, communicating over MQTT.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 34 | `env.ambient_temp` | Factory floor temperature | 15-35 | C | 60s | MQTT |
| 35 | `env.ambient_humidity` | Factory floor humidity | 30-80 | %RH | 60s | MQTT |

**How reference data informs this equipment:**

The IOLink BCM0002 sensor data showed humidity ranging from 15-80% and contact temperature from 20-40C. The simulator uses these ranges directly. The reference data also showed ambient pressure (990-1030 hPa) and vibration RMS (0-50 mm/s) on the same sensor. The simulator separates vibration into its own equipment group.

The BNI0042 static charge sensor data (0-5 kV) is not included in the 40-signal spec but could be added as a future extension. Static charge is relevant for packaging materials that generate electrostatic buildup during unwinding.

Environmental signals follow a slow sinusoidal daily pattern. Temperature peaks in the afternoon. Humidity inversely correlates with temperature. These patterns are well-established in the Appliances Energy dataset from the public datasets research.

### 2.8 Equipment: Energy Monitoring

Energy monitoring tracks power consumption for the entire line. It produces 2 signals. It represents a Schneider PM5xxx smart power meter connected via Modbus TCP.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 36 | `energy.line_power` | Instantaneous line power | 0-200 | kW | 1s | Modbus HR |
| 37 | `energy.cumulative_kwh` | Cumulative energy consumption | 0-999,999 | kWh | 60s | Modbus HR |

Energy consumption correlates with press operating state. Base load when idle is 5-15 kW (electronics, lighting, HVAC). Running load is 60-150 kW depending on speed. Cold start produces a 50% inrush spike lasting 2-5 seconds as motors energize. The Steel Industry Energy dataset from the public datasets research showed daily and weekly load patterns with clear shift changes. The simulator replicates these patterns.

### 2.9 Equipment: Vibration Monitoring

Vibration sensors monitor the press main drive motor. They produce 3 signals. They represent a retrofit wireless vibration sensor (Banner, Pepperl+Fuchs, or similar) communicating over MQTT.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 38 | `vibration.main_drive_x` | X-axis vibration RMS | 0-50 | mm/s | 1s | MQTT |
| 39 | `vibration.main_drive_y` | Y-axis vibration RMS | 0-50 | mm/s | 1s | MQTT |
| 40 | `vibration.main_drive_z` | Z-axis vibration RMS | 0-50 | mm/s | 1s | MQTT |

**How reference data informs this equipment:**

The IOLink BCM0002 sensor data included `v_rms_magnitude` with a range of 0-50 mm/s. The SKAB benchmark dataset (from the public datasets research) provides 8 channels at 1-second resolution from a real testbed including vibration RMS, current, pressure, and temperature. The IMS/NASA bearing dataset provides 35-day run-to-failure vibration data at 10-minute intervals.

Normal vibration for a healthy motor at operating speed is 2-8 mm/s RMS. Bearing wear causes a gradual increase to 15-25 mm/s over weeks. Imbalance or misalignment causes periodic spikes. The simulator models both healthy baseline and degradation trends.

### 2.10 Signal Summary

| Protocol | Signal Count | Signals |
|----------|-------------|---------|
| Modbus TCP only | 19 | press.line_speed, press.ink_viscosity, press.ink_temperature, press.dryer_temp_zone_1/2/3, press.dryer_setpoint_zone_1/2/3, press.impression_count, press.good_count, press.waste_count, press.main_drive_current, press.main_drive_speed, press.nip_pressure, press.unwind_diameter, press.rewind_diameter, energy.line_power, energy.cumulative_kwh |
| OPC-UA only | 4 | press.web_tension, press.registration_error_x, press.registration_error_y, slitter.web_tension |
| Modbus TCP + OPC-UA | 7 | press.machine_state, laminator.nip_temp, laminator.nip_pressure, laminator.oven_temp, laminator.web_speed, laminator.adhesive_weight, slitter.speed |
| MQTT only | 9 | coder.state, coder.prints_total, coder.ink_level, coder.printhead_temp, env.ambient_temp, env.ambient_humidity, vibration.main_drive_x/y/z |
| Event + counter | 1 | slitter.reel_count (Modbus) |

Total: 40 signals across 7 equipment groups.

Average aggregate sample rate: approximately 2 samples per second across all signals. Data volume: approximately 7,200 data points per hour, 172,800 per day, 5.2 million per month.

---

## 3. Protocol Endpoints

### 3.1 Modbus TCP

**Server address:** `0.0.0.0:502` (configurable)
**Unit ID:** 1 (configurable, additional unit IDs for multi-slave simulation)
**Byte order:** Big-endian (ABCD) for Siemens-style registers. Configurable to CDAB (word-swapped) for Allen-Bradley emulation.

The Modbus server exposes four register types: holding registers (FC03/FC06/FC16), input registers (FC04), coils (FC01/FC05/FC15), and discrete inputs (FC02).

#### 3.1.1 Holding Registers (FC03 Read, FC06/FC16 Write)

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

#### 3.1.2 Input Registers (FC04 Read-Only)

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

#### 3.1.3 Coils (FC01 Read, FC05/FC15 Write)

Coils represent boolean states.

| Address | Signal | Description |
|---------|--------|-------------|
| 0 | press.running | True when machine_state = 2 (Running) |
| 1 | press.fault_active | True when machine_state = 4 (Fault) |
| 2 | press.emergency_stop | True during e-stop condition |
| 3 | press.web_break | True during web break event |
| 4 | laminator.running | True when laminator is running |
| 5 | slitter.running | True when slitter is running |

#### 3.1.4 Discrete Inputs (FC02 Read-Only)

Discrete inputs represent physical sensor states.

| Address | Signal | Description |
|---------|--------|-------------|
| 0 | press.guard_door_open | Safety guard door state |
| 1 | press.material_present | Web material detected at infeed |
| 2 | press.cycle_complete | Toggles each impression cycle |

#### 3.1.5 Modbus Error Simulation

The server supports configurable error injection:

- **Exception response on specific registers.** Configure register addresses that return Modbus exception code 0x02 (Illegal Data Address) or 0x04 (Slave Device Failure) at a configurable probability (default: 0.1% of reads).
- **Timeout simulation.** Configure a probability of not responding at all (default: 0.05% of reads), forcing the client to handle timeouts.
- **Slow response.** Configure a response delay range (default: 0-50ms, configurable to 0-2000ms) to simulate network latency or slow PLC scan cycles.

### 3.2 OPC-UA

**Server endpoint:** `opc.tcp://0.0.0.0:4840` (configurable)
**Security:** Accept all client certificates (development mode). Configurable to require authentication.
**Authentication:** Anonymous access enabled. Optional username/password: `collatr` / `collatr123`.

#### 3.2.1 Namespace Structure

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

#### 3.2.2 Node Data Types

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

#### 3.2.3 Status Codes

Nodes normally report `StatusCode.Good`. Under error injection scenarios:

- `StatusCode.BadCommunicationError` when simulating a PLC communication drop
- `StatusCode.BadSensorFailure` on specific nodes during sensor fault scenarios
- `StatusCode.UncertainLastUsableValue` when the data engine pauses updates to a node (stale data)

The Microsoft OPC PLC Server (documented in the public datasources research) generates alternating Good/Bad/Uncertain status codes. Our simulator replicates this capability for testing CollatrEdge status code handling.

#### 3.2.4 OPC-UA Server Implementation

Two options were evaluated:

**Option A: Extend Microsoft OPC PLC Server.** The OPC PLC server (Docker: `mcr.microsoft.com/iotedge/opc-plc`) already generates anomaly patterns, status code changes, and configurable nodes. We would add our factory-specific node structure via its JSON configuration interface.

**Option B: Build custom server using node-opcua or opcua-asyncio.**

We choose Option B. The OPC PLC server is designed for Azure IoT Edge testing and its configuration model does not support the correlated signal generation our factory model requires. A custom server gives full control over node structure, value updates, and status code behaviour.

### 3.3 MQTT

**Broker:** The simulator runs an embedded MQTT broker on `0.0.0.0:1883` (configurable). Alternatively, it publishes to an external broker.
**Protocol version:** MQTT 3.1.1. Optional MQTT 5.0 support.
**Authentication:** Anonymous by default. Configurable username/password.

#### 3.3.1 Topic Structure

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

#### 3.3.2 Payload Format

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

#### 3.3.3 QoS Levels

| Topic Pattern | QoS | Rationale |
|---------------|-----|-----------|
| `*/coder/*` | 1 (at least once) | Coder state changes must not be lost |
| `*/env/*` | 0 (at most once) | Environmental data is low-value, high-frequency |
| `*/vibration/*` | 0 (at most once) | Vibration data is high-frequency, loss-tolerant |

#### 3.3.4 Sparkplug B Support

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

#### 3.3.5 Retained Messages

The most recent message on each topic is published with the retained flag set. This means a new CollatrEdge subscriber immediately receives the latest value for every signal without waiting for the next publish cycle. This matches common industrial MQTT gateway behaviour.

---

## 4. Data Generation Engine

### 4.1 Design Principles

The data generation engine produces parametric synthetic data. It does not replay recorded timeseries. Every run generates unique data from configurable models. The engine runs at a configurable time scale (1x, 10x, 100x real-time).

Key principles:

1. **Correlations over individual signals.** Signals do not vary independently. When line speed changes, motor current changes, web tension fluctuates, dryer temperatures respond, and waste rate shifts. The engine models these dependencies explicitly.

2. **State drives everything.** The machine state (Off, Setup, Running, Idle, Fault, Maintenance) determines the behaviour of all signals. A signal generator does not produce values in isolation. It asks "what state is the machine in?" and generates accordingly.

3. **Noise is not optional.** Every analog signal includes Gaussian noise at a configurable magnitude. Real sensors are noisy. Clean signals look fake. The noise magnitude is calibrated from studying the reference data. Print head temperature had 2.8C standard deviation. Lung pressure had 60 mbar standard deviation. We tune noise per signal.

4. **Time is the independent variable.** The engine maintains a simulation clock. At each tick, it advances the clock, evaluates active scenarios, updates the machine state, and generates new values for all signals. The tick rate matches the fastest signal (500ms for web tension and registration error). Slower signals update only on their configured interval.

### 4.2 Signal Models

Each signal uses one of the following generator models:

#### 4.2.1 Steady State with Noise

The simplest model. The signal stays near a target value with Gaussian noise.

```
value = target + noise(0, sigma)
```

Used for: `press.nip_pressure`, `laminator.nip_pressure`, `laminator.adhesive_weight`, `env.ambient_temp` (within each hour), `coder.printhead_temp` (during printing).

Parameters: `target`, `sigma`, `min_clamp`, `max_clamp`.

#### 4.2.2 Sinusoidal with Noise

The signal follows a sine wave with noise. Models signals with periodic behaviour.

```
value = center + amplitude * sin(2 * pi * t / period + phase) + noise(0, sigma)
```

Used for: `env.ambient_temp` (daily cycle, period=24h), `env.ambient_humidity` (daily cycle, inverted phase).

Parameters: `center`, `amplitude`, `period`, `phase`, `sigma`.

#### 4.2.3 First-Order Lag (Setpoint Tracking)

The signal tracks a setpoint with exponential lag. Models temperature controllers.

```
value = value + (setpoint - value) * (1 - exp(-dt / tau)) + noise(0, sigma)
```

Used for: `press.dryer_temp_zone_1/2/3` tracking their setpoints, `laminator.nip_temp`, `laminator.oven_temp`.

Parameters: `tau` (time constant, seconds), `sigma`, `overshoot_factor` (optional, for initial response).

This model directly reflects the Eurotherm controller pattern documented in the customer profiles research: process variable (PV) tracks setpoint (SP) with first-order dynamics. The time constant tau models the thermal mass of the dryer. Typical tau for an industrial dryer: 30-120 seconds.

#### 4.2.4 Ramp Up / Ramp Down

The signal moves linearly from one value to another over a specified duration.

```
value = start + (end - start) * (elapsed / duration) + noise(0, sigma)
```

Used for: `press.line_speed` during startup (0 to target over 2-5 minutes), `press.line_speed` during shutdown (target to 0 over 30-60 seconds).

Parameters: `start`, `end`, `duration`, `sigma`.

#### 4.2.5 Random Walk with Mean Reversion

The signal drifts randomly but tends to return to a center value. Models signals with slow drift.

```
delta = drift_rate * noise(0, 1) - reversion_rate * (value - center)
value = value + delta * dt
```

Used for: `press.ink_viscosity`, `press.registration_error_x/y`.

Parameters: `center`, `drift_rate`, `reversion_rate`, `min_clamp`, `max_clamp`.

#### 4.2.6 Counter Increment

The signal increments at a rate proportional to machine speed.

```
value = value + rate * line_speed * dt
```

Used for: `press.impression_count`, `press.good_count`, `press.waste_count`, `coder.prints_total`, `energy.cumulative_kwh`.

Parameters: `rate` (increments per m/min per second), `rollover_value` (for counter wrap simulation).

The reference data showed `FPGA_Head_PrintedTotal` wrapping at 999. The press counters use uint32 (max 4,294,967,295) so wrapping is rare but the simulator supports configurable rollover for testing.

#### 4.2.7 Depletion Curve

The signal decreases over time proportional to usage. Models consumable levels.

```
value = value - consumption_rate * prints_delta
```

Used for: `coder.ink_level`, `press.unwind_diameter`.

Parameters: `consumption_rate`, `refill_threshold`, `refill_value`.

When `coder.ink_level` drops below `refill_threshold` (default: 10%), a refill event occurs: the value jumps to `refill_value` (default: 100%). The reference data showed `PS_Pnm_InkConsumptionMl` accumulating linearly during production.

#### 4.2.8 Correlated Follower

The signal derives from another signal with a transformation.

```
value = f(parent_value) + noise(0, sigma)
```

Used for: `press.main_drive_current` follows `press.line_speed` (linear relationship: current = base_current + k * speed). `press.main_drive_speed` follows `press.line_speed` (gear ratio). `laminator.web_speed` follows `press.line_speed` with offset and lag. `press.rewind_diameter` inversely derives from `press.unwind_diameter`.

Parameters: `parent_signal`, `transform_function`, `sigma`, `lag` (optional delay).

#### 4.2.9 State Machine

The signal transitions between discrete states based on rules and probabilities.

```
state = transition(current_state, triggers, probabilities)
```

Used for: `press.machine_state`, `coder.state`.

Parameters: `states[]`, `transitions[]` (each with `from`, `to`, `trigger`, `probability`, `min_duration`, `max_duration`).

### 4.3 Correlation Model

The correlation model defines how signals interact. The machine state is the root driver. All other signals respond to state transitions.

**State transition cascade:**

```
press.machine_state changes to Running
  -> press.line_speed ramps from 0 to target (120-250 m/min) over 2-5 min
    -> press.main_drive_speed follows with gear ratio
    -> press.main_drive_current follows with linear relationship
    -> press.web_tension fluctuates during ramp, stabilizes at steady state
    -> press.registration_error_x/y increases during ramp, decreases at steady state
    -> press.dryer_temp_zone_* already at setpoint (pre-heated during Setup)
    -> press.impression_count starts incrementing
    -> press.good_count increments at (1 - waste_rate) * impression_rate
    -> press.waste_count increments at waste_rate * impression_rate
    -> energy.line_power jumps to running load (60-150 kW)
    -> energy.cumulative_kwh increments proportionally
    -> coder.state transitions to Printing
    -> coder.prints_total starts incrementing
    -> coder.ink_level starts depleting
    -> vibration.main_drive_x/y/z increases from idle (0.5-1 mm/s) to running (3-8 mm/s)
    -> laminator.web_speed follows press speed with lag
    -> press.unwind_diameter decreases
    -> press.rewind_diameter increases
```

**State transition: Running to Fault (web break):**

```
press.machine_state changes to Fault
  -> press.web_tension spikes to >600 N then drops to 0
  -> press.line_speed drops to 0 over 5-10 seconds (emergency deceleration)
  -> press.main_drive_current spikes then drops
  -> coil 3 (web_break) sets to true
  -> coil 1 (fault_active) sets to true
  -> coder.state transitions to Standby
  -> all counters freeze
  -> energy.line_power drops to base load
  -> vibration drops to idle levels
```

**Speed change during Running:**

```
press.line_speed changes (operator adjusts target speed)
  -> press.main_drive_current changes proportionally
  -> press.main_drive_speed changes proportionally
  -> press.web_tension fluctuates for 5-15 seconds then stabilizes
  -> press.registration_error_x/y increases briefly (0.1-0.3 mm)
  -> press.waste_count increment rate increases briefly
  -> energy.line_power changes proportionally
```

**Temperature-viscosity coupling:**

```
env.ambient_temp increases (afternoon warming)
  -> press.ink_temperature increases (ambient drives ink reservoir temp)
  -> press.ink_viscosity decreases (viscosity inversely correlates with temperature)
  -> press.registration_error increases slightly (lower viscosity affects print transfer)
```

### 4.4 Time Compression

The simulation clock advances at a configurable multiple of real time:

| Mode | Clock Rate | 1 Real Hour = | Use Case |
|------|-----------|---------------|----------|
| 1x | Real-time | 1 sim hour | Integration testing, demos |
| 10x | 10x | 10 sim hours | Quick scenario walkthroughs |
| 100x | 100x | ~4 sim days | Long-term trend testing |

At higher compression rates, the data generation engine produces values at the same simulated intervals but publishes them more frequently. A 1-second signal at 100x publishes 100 values per real second. Protocol adapters batch these if the client cannot keep up.

At 100x, the aggregate data rate across 40 signals is approximately 200 values per real second. This is within the throughput capacity of Modbus TCP, OPC-UA, and MQTT on localhost.

### 4.5 Random Seed

The engine accepts an optional random seed. With the same seed and configuration, the engine produces identical output. This enables reproducible test scenarios. Without a seed, the engine uses a time-based seed for unique runs.

---

## 5. Scenario System

### 5.1 Overview

Scenarios are time-bounded events that override normal signal generation. They inject anomalies, operational events, and degradation patterns into the data stream. Scenarios can be scheduled (recurring on a pattern) or triggered (fired by a condition or manual command).

### 5.2 Job Changeover

**Frequency:** 3-6 per 8-hour shift.
**Duration:** 10-30 minutes per changeover.

Sequence:
1. `press.machine_state` transitions from Running (2) to Setup (1).
2. `press.line_speed` ramps down to 0 over 30-60 seconds.
3. All production counters stop incrementing.
4. `coder.state` transitions to Standby (4).
5. After setup duration (10-30 minutes, configurable):
   - `press.dryer_setpoint_zone_*` may change (new product requires different temperature).
   - `press.dryer_temp_zone_*` begins tracking new setpoint.
6. `press.machine_state` transitions to Running (2).
7. `press.line_speed` ramps from 0 to new target speed over 2-5 minutes.
8. Counters may reset to 0 (new job) or continue (same batch).
9. `press.waste_count` increments faster during the first 2-3 minutes (startup waste).

The changeover frequency and duration are drawn from uniform random distributions within the configured ranges. This matches the pattern described in the customer profiles research: 3-6 changeovers per shift with 10-30 minute duration.

### 5.3 Web Break

**Frequency:** 1-2 per week (configurable).
**Duration:** 15-60 minutes recovery.

Sequence:
1. `press.web_tension` spikes above 600 N for 100-500 milliseconds.
2. `press.web_tension` drops to 0 within 1 second.
3. `press.machine_state` transitions to Fault (4).
4. `press.line_speed` drops to 0 via emergency deceleration (5-10 seconds).
5. Coil 3 (`web_break`) sets to true.
6. Coil 1 (`fault_active`) sets to true.
7. After recovery duration (15-60 minutes):
   - Coils clear.
   - `press.machine_state` transitions to Setup (1), then Running (2).
   - Normal startup sequence follows.

The web tension spike before the break is the key diagnostic signal. It lasts less than 1 second. CollatrEdge must sample fast enough to capture it (the 500ms OPC-UA polling rate should catch it in most cases).

### 5.4 Dryer Temperature Drift

**Frequency:** 1-2 per shift (configurable).
**Duration:** 30-120 minutes.

Sequence:
1. One dryer zone's actual temperature begins drifting above its setpoint.
2. Drift rate: 0.05-0.2 C per minute.
3. Over 30-120 minutes, the zone drifts 5-15C above setpoint.
4. `press.waste_count` increment rate increases by 20-50% during drift (quality impact).
5. After drift duration, temperature returns to setpoint (simulates operator correction or auto-correction).

The drift is subtle. It does not trigger a fault state. It causes increased waste. This is the type of anomaly that data analytics should detect. The DAMADICS actuator benchmark (from the datasets research) showed similar gradual control loop degradation patterns.

### 5.5 Motor Bearing Wear

**Frequency:** One event over 2-6 weeks (configurable).
**Duration:** Gradual degradation.

Sequence:
1. `vibration.main_drive_x/y/z` baseline increases by 0.01-0.05 mm/s per hour.
2. After 1-2 weeks, vibration reaches 15-20 mm/s (warning threshold).
3. After 3-5 weeks, vibration reaches 25-40 mm/s (alarm threshold).
4. `press.main_drive_current` increases by 1-5% at constant speed (bearing friction).
5. If the scenario is configured to culminate in failure: `press.machine_state` transitions to Fault (4) with vibration spike to 40-50 mm/s.

The IMS/NASA bearing run-to-failure dataset (from the datasets research) showed exactly this pattern over 35 days. The Paderborn bearing dataset added motor current increase as a correlated signal. Our simulator reproduces both.

This scenario operates at a different timescale than other scenarios. At 1x speed, the full degradation plays out over weeks. At 100x speed, it plays out over hours. The bearing wear scenario is the primary test for CollatrEdge's ability to detect slow trends.

### 5.6 Ink Viscosity Excursion

**Frequency:** 2-3 per shift.
**Duration:** 5-30 minutes per excursion.

Sequence:
1. `press.ink_viscosity` drifts below 18 seconds (too thin) or above 45 seconds (too thick).
2. `press.registration_error_x/y` increases during the excursion.
3. `press.waste_count` increment rate increases by 10-30%.
4. After excursion duration, viscosity returns to normal range (simulates operator adding solvent or ink concentrate).

The customer profiles research identified ink viscosity excursions as a key analytics use case for packaging converters. Viscosity correlates with temperature (lower temp = higher viscosity). The simulator couples these: an ambient temperature drop triggers higher viscosity, which triggers more waste.

### 5.7 Registration Drift

**Frequency:** Random, 1-3 per shift.
**Duration:** 2-10 minutes.

Sequence:
1. `press.registration_error_x` or `press.registration_error_y` drifts beyond +/-0.3 mm.
2. Drift is gradual: 0.01-0.05 mm per second.
3. Often triggered by a speed change or temperature shift.
4. `press.waste_count` increment rate increases while error exceeds 0.2 mm.
5. Returns to center after auto-correction or operator intervention.

### 5.8 Unplanned Stop

**Frequency:** 1-2 per shift.
**Duration:** 5-60 minutes.

Sequence:
1. `press.machine_state` transitions to Fault (4).
2. `press.line_speed` drops to 0.
3. Coil 1 (`fault_active`) sets to true.
4. A fault code is written to holding register 210 as a secondary uint16 value. The simulator maintains a set of realistic fault codes:

| Code | Description |
|------|-------------|
| 101 | Motor overload |
| 102 | Inverter fault |
| 201 | Ink system pressure low |
| 202 | Ink pump failure |
| 301 | Registration sensor error |
| 302 | Web guide sensor error |
| 401 | Safety guard opened |
| 402 | Emergency stop pressed |
| 501 | Dryer overheat |
| 502 | Dryer fan failure |

5. After stop duration, fault clears. Normal startup sequence follows.

### 5.9 Shift Change

**Frequency:** 3 per day. Fixed times: 06:00, 14:00, 22:00 (configurable).
**Duration:** 5-15 minutes.

Sequence:
1. `press.machine_state` transitions to Idle (3) for 5-15 minutes.
2. `press.line_speed` drops to 0.
3. `energy.line_power` drops to base load.
4. After changeover:
   - `press.machine_state` transitions to Running (2).
   - New shift may run at slightly different speed (shift-to-shift operator preference).
   - Night shift (22:00-06:00) runs 5-10% slower.
   - Weekend shifts may not run at all (configurable).

The customer profiles research identified shift-to-shift performance variation as a key OEE analytics use case. The simulator makes this visible by varying the target speed and waste rate between shifts.

### 5.10 Energy Spike on Cold Start

**Frequency:** 1-2 per day (each time the line starts from cold).
**Duration:** 2-5 seconds.

Sequence:
1. When `press.machine_state` transitions from Off (0) or Idle (3) to Setup (1) or Running (2) after being idle for more than 30 minutes:
   - `energy.line_power` spikes to 150-200% of normal running power for 2-5 seconds.
   - `press.main_drive_current` spikes to 150-300% of running current (motor inrush).
2. After the spike, power settles to normal running level.

The Steel Industry Energy dataset (from the datasets research) showed clear cold start spikes. The customer profiles research identified energy-per-impression monitoring as a key use case.

### 5.11 Vision Inspection Fail Rate Patterns

This scenario does not directly produce one of the 40 signals but influences `press.waste_count` and informs the coder behaviour.

When the press is Idle (3) or Off (0), the vision inspection system (if it were a signal) would report near-100% fail rates. The R-Series reference data showed 85.6% fail rate in a typical month because the camera reports "fail" for no-read events during idle periods.

The simulator uses this insight: `press.waste_count` only increments when `press.machine_state` is Running (2). During idle, the waste rate is exactly 0. When the press starts running, waste rate begins at 3-5% (startup waste) and decreases to 0.5-2% (steady state) over 2-3 minutes.

### 5.12 Coder State Transitions and Consumable Depletion

**Coder state machine:**

```
Off (0) <-> Ready (1) <-> Printing (2) <-> Standby (4)
               |                |
               v                v
            Fault (3) <----  Fault (3)
```

Transitions:
- Off to Ready: When the press powers up.
- Ready to Printing: When press.machine_state enters Running (2).
- Printing to Standby: When press.machine_state leaves Running.
- Standby to Printing: When press.machine_state returns to Running.
- Any to Fault: Random (MTBF = 200-500 hours of printing time).
- Fault to Ready: After 5-30 minutes.
- Ready/Standby to Off: When press is powered down.

**Ink depletion:**
- Full cartridge: 100%
- Depletion rate: 0.001-0.003% per 1000 prints (configurable).
- At 10% level: Coder publishes a low-ink warning (quality flag changes to "uncertain").
- At 2% level: Coder enters Fault (3) state (ink empty).
- Operator intervention: Ink level resets to 100% (simulates cartridge replacement).
- Time between replacements at typical speeds: 8-24 hours.

The reference data showed `PS_Pnm_InkConsumptionMl` climbing to 4909 ml. The cleaning station data showed `PS_Cleaning_WasteContainer` and `PS_Cleaning_WashBottle` as consumable depletion curves. Our coder model is simpler: a single ink level that depletes linearly with print count.

### 5.13 Scenario Scheduling

Scenarios are scheduled via a scenario timeline. The timeline is a list of scenario instances with start times and parameters. The engine can also generate a random timeline from a statistical profile:

```yaml
scenarios:
  job_changeover:
    frequency: "3-6 per 8h shift"
    duration_range: [600, 1800]  # 10-30 minutes in seconds
    speed_change_probability: 0.3
    counter_reset_probability: 0.7

  web_break:
    frequency: "1-2 per 168h week"
    recovery_range: [900, 3600]  # 15-60 minutes

  dryer_drift:
    frequency: "1-2 per 8h shift"
    drift_range: [5, 15]  # degrees C
    duration_range: [1800, 7200]  # 30-120 minutes

  bearing_wear:
    enabled: true
    start_after_hours: 48  # start degradation after 48 hours
    duration_hours: 336  # 2 weeks to reach warning level
    culminate_in_failure: false

  unplanned_stop:
    frequency: "1-2 per 8h shift"
    duration_range: [300, 3600]  # 5-60 minutes
```

---

## 6. Configuration

### 6.1 Configuration File Format

The simulator uses YAML configuration files. YAML was chosen over TOML for its better support of nested structures, arrays of objects, and multi-line strings. The configuration is hierarchical: factory > equipment > signals > parameters.

### 6.2 Main Configuration File

File: `config/factory.yaml`

```yaml
# Collatr Factory Simulator Configuration
# Version: 1.0

factory:
  name: "Demo Packaging Factory"
  site_id: "demo"
  timezone: "Europe/London"

simulation:
  time_scale: 1.0          # 1.0 = real-time, 10.0 = 10x speed
  random_seed: null         # null = time-based, integer = deterministic
  tick_interval_ms: 100     # Internal engine tick rate
  start_time: null          # null = now, ISO8601 = specific start

protocols:
  modbus:
    enabled: true
    bind_address: "0.0.0.0"
    port: 502
    unit_id: 1
    byte_order: "ABCD"     # ABCD (big-endian) or CDAB (word-swapped)
    error_injection:
      exception_probability: 0.001
      timeout_probability: 0.0005
      response_delay_ms: [0, 50]

  opcua:
    enabled: true
    bind_address: "0.0.0.0"
    port: 4840
    server_name: "Collatr Factory Simulator"
    namespace_uri: "urn:collatr:factory-simulator"
    security_mode: "None"   # None, Sign, SignAndEncrypt
    anonymous_access: true
    users:
      - username: "collatr"
        password: "collatr123"

  mqtt:
    enabled: true
    mode: "embedded"        # embedded or external
    bind_address: "0.0.0.0"
    port: 1883
    external_broker: null   # "broker.example.com:1883" if mode=external
    topic_prefix: "collatr/factory"
    sparkplug_b: false      # Phase 2
    retain: true
    username: null
    password: null

equipment:
  press:
    enabled: true
    type: "flexographic_press"
    model: "CI-8"
    target_speed: 200       # m/min, normal operating speed
    speed_range: [50, 400]
    signals:
      line_speed:
        model: "ramp"
        noise_sigma: 0.5
        modbus_hr: [100, 101]
        modbus_type: "float32"
        opcua_node: "PackagingLine.Press1.LineSpeed"
        opcua_type: "Double"
      web_tension:
        model: "correlated_follower"
        parent: "press.line_speed"
        transform: "linear"
        params:
          base: 80
          factor: 0.5
          sigma: 5.0
        opcua_node: "PackagingLine.Press1.WebTension"
        opcua_type: "Double"
        sample_rate_ms: 500
      # ... (remaining signals follow same pattern)

  laminator:
    enabled: true
    type: "solvent_free_laminator"
    signals:
      # ... signal definitions

  slitter:
    enabled: true
    type: "slitter_rewinder"
    signals:
      # ... signal definitions

  coder:
    enabled: true
    type: "cij_printer"
    signals:
      # ... signal definitions

  environment:
    enabled: true
    type: "iolink_sensor"
    signals:
      # ... signal definitions

  energy:
    enabled: true
    type: "power_meter"
    signals:
      # ... signal definitions

  vibration:
    enabled: true
    type: "wireless_vibration"
    signals:
      # ... signal definitions

scenarios:
  job_changeover:
    enabled: true
    frequency_per_shift: [3, 6]
    duration_seconds: [600, 1800]
    speed_change_probability: 0.3
    counter_reset_probability: 0.7

  web_break:
    enabled: true
    frequency_per_week: [1, 2]
    recovery_seconds: [900, 3600]

  dryer_drift:
    enabled: true
    frequency_per_shift: [1, 2]
    drift_degrees: [5, 15]
    duration_seconds: [1800, 7200]

  bearing_wear:
    enabled: true
    start_after_hours: 48
    duration_hours: 336
    culminate_in_failure: false

  ink_viscosity_excursion:
    enabled: true
    frequency_per_shift: [2, 3]
    duration_seconds: [300, 1800]

  registration_drift:
    enabled: true
    frequency_per_shift: [1, 3]
    duration_seconds: [120, 600]

  unplanned_stop:
    enabled: true
    frequency_per_shift: [1, 2]
    duration_seconds: [300, 3600]

  shift_change:
    enabled: true
    times: ["06:00", "14:00", "22:00"]
    changeover_seconds: [300, 900]
    night_shift_speed_factor: 0.9
    weekend_enabled: false

  cold_start_spike:
    enabled: true
    idle_threshold_minutes: 30
    spike_duration_seconds: [2, 5]
    spike_magnitude: [1.5, 2.0]

shifts:
  pattern: "3x8"           # 3 shifts of 8 hours
  day_start: "06:00"
  operators:
    morning:
      speed_bias: 1.0
      waste_rate_bias: 1.0
    afternoon:
      speed_bias: 0.95
      waste_rate_bias: 1.05
    night:
      speed_bias: 0.90
      waste_rate_bias: 1.10
```

### 6.3 Docker Compose Deployment

File: `docker-compose.yaml`

```yaml
version: "3.8"

services:
  factory-simulator:
    build:
      context: .
      dockerfile: Dockerfile
    image: collatr/factory-simulator:latest
    container_name: factory-simulator
    ports:
      - "502:502"       # Modbus TCP
      - "4840:4840"     # OPC-UA
      - "1883:1883"     # MQTT
      - "8080:8080"     # Web dashboard / health check
    volumes:
      - ./config:/app/config:ro
    environment:
      - SIM_TIME_SCALE=1.0
      - SIM_RANDOM_SEED=
      - SIM_LOG_LEVEL=info
      - MODBUS_ENABLED=true
      - MODBUS_PORT=502
      - OPCUA_ENABLED=true
      - OPCUA_PORT=4840
      - MQTT_ENABLED=true
      - MQTT_PORT=1883
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

### 6.4 Environment Variables

Environment variables override configuration file values. All environment variables are prefixed with `SIM_`.

| Variable | Default | Description |
|----------|---------|-------------|
| `SIM_TIME_SCALE` | `1.0` | Simulation speed multiplier |
| `SIM_RANDOM_SEED` | (empty) | Random seed for deterministic runs |
| `SIM_LOG_LEVEL` | `info` | Log level: debug, info, warn, error |
| `SIM_CONFIG_PATH` | `/app/config/factory.yaml` | Path to main config file |
| `MODBUS_ENABLED` | `true` | Enable Modbus TCP server |
| `MODBUS_PORT` | `502` | Modbus TCP port |
| `MODBUS_BYTE_ORDER` | `ABCD` | Register byte order |
| `OPCUA_ENABLED` | `true` | Enable OPC-UA server |
| `OPCUA_PORT` | `4840` | OPC-UA port |
| `MQTT_ENABLED` | `true` | Enable MQTT broker |
| `MQTT_PORT` | `1883` | MQTT port |
| `MQTT_EXTERNAL_BROKER` | (empty) | External broker address |
| `MQTT_TOPIC_PREFIX` | `collatr/factory` | MQTT topic prefix |

### 6.5 Quick Start

```bash
# Start with defaults (all protocols, real-time, random seed)
docker compose up -d

# Start at 10x speed with deterministic seed
SIM_TIME_SCALE=10 SIM_RANDOM_SEED=42 docker compose up -d

# Start with only Modbus enabled
OPCUA_ENABLED=false MQTT_ENABLED=false docker compose up -d

# Verify Modbus is serving data
modbus read -a localhost -p 502 -t hr -s 100 -c 4

# Verify OPC-UA is serving data
# (use any OPC-UA client, e.g., UaExpert)
# Connect to opc.tcp://localhost:4840

# Verify MQTT is publishing
mosquitto_sub -h localhost -t "collatr/factory/#" -v
```

---

## 7. Technology Stack

### 7.1 Language Decision

Two candidates:

**Python.** Strengths: `pymodbus` is the most mature Modbus TCP library. `opcua-asyncio` (formerly python-opcua) is well-maintained and supports server mode. `paho-mqtt` is the standard MQTT client. `aedes` equivalent (HBMQTT or aMQTT) exists for embedded broker. NumPy for efficient signal generation. Strong ecosystem for scientific computing and signal processing.

**Bun/TypeScript.** Strengths: Same language as CollatrEdge. `node-opcua` is the most feature-complete OPC-UA implementation in any language. `modbus-serial` or `jsmodbus` for Modbus. `aedes` for embedded MQTT broker. Shared types and configuration models with CollatrEdge. Single deployment stack.

**Decision: Python.**

Rationale:

1. **Protocol library maturity.** `pymodbus` handles all four Modbus function codes, configurable byte ordering, error injection, and slave simulation out of the box. The Node.js Modbus server libraries are thinner. `opcua-asyncio` is mature for server-side use. `node-opcua` is excellent but its documentation is oriented toward client use. Server creation in `node-opcua` requires more boilerplate.

2. **Signal generation.** NumPy array operations generate 40 signals per tick faster than per-value JavaScript loops. The correlation model involves matrix operations (applying transforms across signal vectors) that NumPy handles natively.

3. **The simulator is not CollatrEdge.** The simulator is a test tool. It does not ship to customers. It does not need to share CollatrEdge's runtime. Choosing the best tool for the job is more important than language consistency. CollatrEdge collects data. The simulator generates data. Different jobs.

4. **Deployment isolation.** The simulator runs in Docker. The language inside the container is invisible to CollatrEdge. They communicate over network protocols. Language alignment across the network boundary has no engineering benefit.

5. **Development speed.** Python prototyping is faster for this type of tool. The signal models, correlation engine, and scenario system are computationally straightforward. Python's expressiveness reduces boilerplate.

### 7.2 Dependencies

**Core:**

| Package | Version | Purpose |
|---------|---------|---------|
| `pymodbus` | >=3.6 | Modbus TCP server |
| `asyncua` (opcua-asyncio) | >=1.1 | OPC-UA server |
| `paho-mqtt` | >=2.0 | MQTT client (for external broker mode) |
| `amqtt` | >=0.11 | Embedded MQTT broker |
| `numpy` | >=1.26 | Signal generation, noise, correlation |
| `pyyaml` | >=6.0 | Configuration file parsing |
| `uvloop` | >=0.19 | Fast asyncio event loop (Linux) |

**Optional:**

| Package | Purpose |
|---------|---------|
| `sparkplug-b` | Sparkplug B payload encoding (Phase 2) |
| `rich` | Terminal UI for development monitoring |
| `prometheus-client` | Metrics export for monitoring simulator health |
| `fastapi` + `uvicorn` | Health check and web dashboard endpoint |

### 7.3 Python Version

Python 3.12 or later. The `asyncio` improvements in 3.12 (TaskGroup, ExceptionGroup) simplify the concurrent protocol server management.

### 7.4 Docker Base Image

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/

EXPOSE 502 4840 1883 8080

CMD ["python", "-m", "src.main"]
```

### 7.5 Development Environment

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # adds pytest, ruff, mypy

# Run locally (no Docker)
python -m src.main --config config/factory.yaml

# Run tests
pytest tests/

# Type checking
mypy src/

# Linting
ruff check src/
```

---

## 8. Architecture

### 8.1 Component Diagram

```
+--------------------------------------------------------------------+
|                     Collatr Factory Simulator                       |
|                                                                     |
|  +-------------------+     +-------------------+                    |
|  |   Configuration   |     |  Scenario Engine  |                    |
|  |   (YAML loader)   |     |  (event scheduler)|                    |
|  +--------+----------+     +--------+----------+                    |
|           |                          |                               |
|           v                          v                               |
|  +--------------------------------------------------+               |
|  |              Simulation Clock                     |               |
|  |   (manages sim time, tick rate, compression)      |               |
|  +---------------------------+----------------------+               |
|                              |                                      |
|                              v                                      |
|  +--------------------------------------------------+               |
|  |              Machine State Engine                 |               |
|  |   (state machine per equipment, transition logic) |               |
|  +---------------------------+----------------------+               |
|                              |                                      |
|                              v                                      |
|  +--------------------------------------------------+               |
|  |           Signal Generation Engine                |               |
|  |                                                    |               |
|  |  +----------+ +----------+ +----------+           |               |
|  |  | Press    | | Lam.     | | Slitter  |           |               |
|  |  | Generator| | Generator| | Generator|           |               |
|  |  +----------+ +----------+ +----------+           |               |
|  |  +----------+ +----------+ +----------+           |               |
|  |  | Coder    | | Env      | | Energy   |           |               |
|  |  | Generator| | Generator| | Generator|           |               |
|  |  +----------+ +----------+ +----------+           |               |
|  |  +----------+                                     |               |
|  |  | Vibration|    (correlation model links         |               |
|  |  | Generator|     generators together)            |               |
|  |  +----------+                                     |               |
|  +---------------------------+----------------------+               |
|                              |                                      |
|                              v                                      |
|  +--------------------------------------------------+               |
|  |              Signal Value Store                   |               |
|  |   (current value of all 40 signals + metadata)    |               |
|  +------+------------------+------------------+-----+               |
|         |                  |                  |                      |
|         v                  v                  v                      |
|  +-----------+    +-------------+    +------------+                 |
|  | Modbus    |    | OPC-UA      |    | MQTT       |                 |
|  | Adapter   |    | Adapter     |    | Adapter    |                 |
|  |           |    |             |    |            |                 |
|  | Reads from|    | Reads from  |    | Reads from |                 |
|  | store,    |    | store,      |    | store,     |                 |
|  | encodes   |    | updates     |    | publishes  |                 |
|  | registers |    | node values |    | messages   |                 |
|  +-----+-----+   +------+------+   +------+------+                 |
|        |                 |                 |                         |
+--------|-----------------|-----------------|-------------------------+
         |                 |                 |
         v                 v                 v
    Port 502          Port 4840         Port 1883
   (Modbus TCP)      (OPC-UA TCP)     (MQTT TCP)
```

### 8.2 Data Flow

1. **Configuration loads.** The YAML config is parsed into typed configuration objects. Signal definitions, protocol mappings, and scenario schedules are validated.

2. **Simulation clock starts.** The clock ticks at `tick_interval_ms` (default: 100ms). At each tick, the clock advances by `tick_interval_ms * time_scale` simulated milliseconds.

3. **Scenario engine evaluates.** The scenario engine checks if any scheduled scenario should start, advance, or end at the current simulation time. Active scenarios modify machine state or signal parameters.

4. **Machine state engine evaluates.** Each equipment group's state machine processes pending transitions. State changes cascade: press.machine_state changing to Running triggers coder.state changing to Printing.

5. **Signal generators produce values.** Each generator runs only if its sample interval has elapsed. A 1-second signal generates a new value every 1 simulated second. A 500ms signal generates every 500 simulated milliseconds. Generators read the current machine state and other signal values (for correlations) from the signal store.

6. **Signal store updates.** New values are written to the central signal store. Each value has: signal ID, timestamp, numeric value, quality flag.

7. **Protocol adapters read the store.** Each adapter runs independently.
   - **Modbus adapter:** On each client read request, the adapter reads the latest value from the store, encodes it according to the register map (float32, uint32, uint16, etc.), and returns it in the Modbus response.
   - **OPC-UA adapter:** At each tick, the adapter updates OPC-UA node values from the store. Subscribed clients receive data change notifications.
   - **MQTT adapter:** At each signal's publish interval, the adapter reads the store, formats a JSON payload, and publishes to the topic.

### 8.3 Concurrency Model

The simulator uses Python `asyncio` for concurrency. Each protocol server runs as an async task.

```python
async def main():
    config = load_config()
    store = SignalStore()
    clock = SimulationClock(config.simulation)
    
    engine = DataEngine(config, store, clock)
    
    tasks = [
        engine.run(),                           # Signal generation loop
        ModbusServer(config.protocols.modbus, store).run(),
        OpcuaServer(config.protocols.opcua, store).run(),
        MqttBroker(config.protocols.mqtt, store).run(),
        HealthServer(config).run(),             # HTTP health check
    ]
    
    async with asyncio.TaskGroup() as tg:
        for task in tasks:
            tg.create_task(task)
```

The signal store uses no locks. The engine is the sole writer. Protocol adapters are readers. In Python's asyncio single-threaded model, there are no race conditions. Values are eventually consistent within one tick (100ms).

### 8.4 Plugin Architecture

New equipment types can be added by implementing the `EquipmentGenerator` interface:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class SignalValue:
    signal_id: str
    timestamp: float
    value: float
    quality: str  # "good", "uncertain", "bad"

class EquipmentGenerator(ABC):
    @abstractmethod
    def configure(self, config: dict) -> None:
        """Load equipment-specific configuration."""
        pass
    
    @abstractmethod
    def get_signal_ids(self) -> list[str]:
        """Return list of signal IDs this equipment produces."""
        pass
    
    @abstractmethod
    def generate(self, sim_time: float, machine_state: int, 
                 store: SignalStore) -> list[SignalValue]:
        """Generate new signal values for the current tick."""
        pass
    
    @abstractmethod
    def get_protocol_mappings(self) -> dict:
        """Return Modbus/OPC-UA/MQTT mappings for each signal."""
        pass
```

Adding a new equipment type requires:
1. Create a new generator class implementing `EquipmentGenerator`.
2. Add the equipment section to the YAML config.
3. Register the generator in the equipment factory.

No changes to protocol adapters or the simulation engine are needed.

### 8.5 Health Check and Monitoring

The simulator exposes an HTTP endpoint on port 8080:

```
GET /health -> 200 OK {"status": "running", "sim_time": "...", "signals": 40}
GET /metrics -> Prometheus metrics (optional)
GET /status -> Detailed status of all signals and their current values
```

The `/status` endpoint returns a JSON object with current values for all 40 signals. This is useful for debugging and for building a simple web dashboard.

---

## 9. Non-Goals and Out of Scope

### 9.1 What This Is Not

**Not a replay of actual customer data.** The simulator generates original synthetic data. No rows from the reference database are included. No data from Site A, Site B, or any CIJ vendor customer site is embedded in the simulator or its configuration. The reference data informed the models. The models produce new data.

**Not a digital twin.** A digital twin models a specific physical asset with bidirectional data flow. The simulator models a generic packaging line. It does not represent any specific factory. It does not receive commands from a real control system. Data flows one direction: out.

**Not intended for production monitoring.** The simulator is a development and testing tool. It does not connect to real equipment. It does not process real production data. It does not generate alerts or reports for factory operators.

**Not a general-purpose protocol simulator.** The simulator serves a specific set of signals for a packaging line. It is not a configurable OPC-UA server for arbitrary data, a Modbus slave emulator for testing register maps, or an MQTT broker for general use. Those tools exist (Microsoft OPC PLC, oitc/modbus-server, Mosquitto).

### 9.2 Phase 2 and Beyond

The following items are explicitly deferred:

**Food and beverage overlay.** Add oven temperature zones, fill weight signals, cold room monitoring, and CIP cycle simulation. This extends the simulator to address the food manufacturing prospect list (Compleat Food Group, Warburtons, etc.). The research identified a complete data gap for food manufacturing in public datasets. All signals must be synthesized.

**CNC machine cell.** Add spindle speed, spindle load, feed rate, axis positions, and tool wear signals. The CNC datasets from the Round 2 research (Hannover, Bosch, MU-TCM) provide reference patterns. This addresses the automotive and aerospace prospect list (Mettis Aerospace, Sertec, ASG Group).

**Pharma tablet press.** Add compression force, turret speed, tablet weight, and cleanroom environmental monitoring. The Lek Pharmaceuticals tablet compression dataset provides direct reference data. This addresses the pharmaceutical prospect list (Sterling Pharma Solutions, Almac Group).

**Sparkplug B support.** Add protobuf-encoded MQTT payloads in the Sparkplug B namespace. This is a protocol feature, not a factory feature.

**Historical data access.** Add OPC-UA Historical Access (HA) support so clients can query past values. The current design serves only current values via subscriptions and polling.

**Multi-line simulation.** Run two or more packaging lines simultaneously with shared environmental conditions but independent production schedules.

**EtherNet/IP support.** Add Allen-Bradley native protocol. This is relevant for food and beverage sites using Rockwell PLCs. The customer profiles research showed Allen-Bradley CompactLogix using CDAB byte order.

**MTConnect support.** Add MTConnect agent for CNC machine data. This is relevant for the CNC machine cell phase.

**Web dashboard.** Add a browser-based UI showing real-time signal values, machine state, and scenario status. The health check endpoint provides raw data. A dashboard adds visualization.

---

## 10. Data Quality Realism

### 10.1 Why Messy Data Matters

Real industrial data is messy. Sensors drift. Networks drop packets. PLCs restart. Timestamps have timezone bugs. Counters wrap. Duplicate rows appear. The reference data from the public schema demonstrated all of these issues. A simulator that produces clean, perfect data fails to test CollatrEdge's robustness.

The simulator produces intentionally imperfect data. The imperfections are configurable and documented so engineers know what to test for.

### 10.2 Communication Drops

The simulator periodically stops responding on one protocol for a configurable duration.

**Modbus drops:** The server stops responding to requests for 1-10 seconds. The client times out. When the server resumes, the next response contains the current value (not the value at the time of the request). Frequency: configurable, default 1-2 per hour.

**OPC-UA drops:** Node values freeze (stop updating) for 5-30 seconds. The status code changes to `UncertainLastUsableValue`. After the drop, values resume updating and status returns to `Good`. Frequency: configurable, default 1-2 per hour.

**MQTT drops:** The broker stops publishing to specific topics for 5-30 seconds. No messages are queued during the drop (QoS 0 topics). QoS 1 topics (coder state) are delivered when publishing resumes. Frequency: configurable, default 1-2 per hour.

The reference data showed one site agent having extended connectivity issues (97.9% error rate in December 2024 during initial setup). Another site agent had intermittent drops (0-11.6% error rate in typical months). Our simulator models the normal-operations case: brief drops, not extended outages.

### 10.3 Sensor Noise

Every analog signal includes Gaussian noise. The noise magnitude (sigma) is configured per signal.

| Signal | Noise Sigma | Rationale |
|--------|-------------|-----------|
| press.line_speed | 0.5 m/min | Encoder resolution + motor controller jitter |
| press.web_tension | 5.0 N | Load cell noise, typical for web handling |
| press.registration_error_x/y | 0.01 mm | Camera resolution limit |
| press.ink_viscosity | 0.5 s | Measurement method variability |
| press.ink_temperature | 0.2 C | Thermocouple noise |
| press.dryer_temp_zone_* | 0.3 C | Thermocouple noise + control oscillation |
| press.main_drive_current | 0.5 A | CT clamp resolution |
| press.main_drive_speed | 2.0 RPM | Encoder resolution |
| press.nip_pressure | 0.05 bar | Pressure transducer noise |
| laminator.* | similar to press | Same sensor types |
| coder.printhead_temp | 0.5 C | Reference: PS_Head_TempFirepulse sigma=2.8C |
| env.ambient_temp | 0.1 C | IOLink sensor resolution |
| env.ambient_humidity | 0.5 %RH | IOLink sensor resolution |
| energy.line_power | 0.2 kW | Power meter resolution |
| vibration.main_drive_* | 0.3 mm/s | Accelerometer noise floor |

### 10.4 Counter Rollovers

The `press.impression_count`, `press.good_count`, and `energy.cumulative_kwh` counters are stored as uint32 in Modbus registers. At maximum value (4,294,967,295), the counter wraps to 0.

In normal operation at 200 m/min, the impression counter increments at roughly 200 counts per minute (assuming 1 impression per meter). It takes approximately 14,889 days (40.8 years) to wrap a uint32 counter at this rate. Counter wrap is unrealistic at normal speed.

However, the simulator supports a configurable `rollover_value` for testing purposes. Set `rollover_value: 10000` and the counter wraps at 10,000 instead of 4,294,967,295. This lets engineers test CollatrEdge's counter wrap detection in minutes instead of decades.

The reference data showed `FPGA_Head_PrintedTotal` wrapping at 999. This is an unusually low rollover value, likely a per-head counter with limited register width. The simulator's configurable rollover replicates this behaviour.

### 10.5 Duplicate Timestamps

The reference data contained a severe duplicate insertion bug at one customer site: 190x row duplication over 6 days. The simulator replicates a milder version.

At a configurable probability (default: 0.01%), a Modbus read returns the same value with the same internal timestamp as the previous read. This simulates a PLC that has not completed its scan cycle between two consecutive client reads. The value is not stale (it is legitimately the same) but the identical timestamps can confuse naive analytics that assume strictly monotonic timestamps.

For MQTT, the simulator occasionally publishes two messages to the same topic within 1 millisecond (configurable probability, default: 0.005%). This simulates the edge case where a sensor gateway double-publishes.

### 10.6 Modbus Exception Responses

Real Modbus devices return exception responses for various reasons: register not implemented, device busy, slave device failure. The simulator injects exception responses at configurable probability.

| Exception Code | Name | When Injected |
|---------------|------|---------------|
| 0x01 | Illegal Function | Reading coils with FC03 (wrong function code) |
| 0x02 | Illegal Data Address | Reading beyond the implemented register range |
| 0x04 | Slave Device Failure | Random injection at configured probability |
| 0x06 | Slave Device Busy | During machine state transitions |

### 10.7 Timezone Issues

The reference data showed camera timestamps drifting between UTC, BST, and US Eastern timezone during the trial. This is a real problem in manufacturing. Many PLCs and industrial devices do not implement NTP and their clocks drift or are set to incorrect timezones.

The simulator's OPC-UA server timestamps are always in UTC (this is the OPC-UA specification requirement). The MQTT JSON payloads use ISO 8601 UTC timestamps. The Modbus protocol has no timestamps.

To test timezone handling, the MQTT adapter accepts a configuration option `timestamp_offset_hours` (default: 0). Setting this to 1 simulates a device reporting BST timestamps as if they were UTC. Setting it to -5 simulates the camera clock timezone drift issue. CollatrEdge must handle these correctly.

### 10.8 Stale and Missing Values

Some signals occasionally report stale values. The OPC-UA adapter marks these with `UncertainLastUsableValue` status. The MQTT adapter sets the quality field to `"uncertain"`.

Stale values occur when:
- A sensor communication drop prevents a fresh read.
- The PLC scan cycle is slower than the client polling rate (the same value is reported twice).
- A counter stops incrementing during idle periods (legitimately unchanged, not stale, but can look stale to a system that expects change).

The press counters are the primary example. When `press.machine_state` is Idle (3), `press.impression_count`, `press.good_count`, and `press.waste_count` do not increment. They report the same value every second. This is correct behaviour. CollatrEdge must distinguish between "counter is stale" and "counter is not incrementing because the machine is idle."

---

## 11. Success Criteria

### 11.1 Protocol Connectivity

CollatrEdge connects to the simulator via all three protocols and collects data continuously for 24 hours with zero configuration changes to CollatrEdge beyond specifying the endpoint addresses.

**Modbus TCP:** CollatrEdge reads holding registers, input registers, coils, and discrete inputs at configured poll intervals. All register addresses in the map return valid data. Float32 and uint32 values decode correctly with ABCD byte order. Int16 values in input registers decode correctly with x10 scaling.

**OPC-UA:** CollatrEdge browses the node tree, subscribes to all nodes under `PackagingLine`, and receives data change notifications. All node values update at their configured rates. Status codes are correctly propagated.

**MQTT:** CollatrEdge subscribes to `collatr/factory/#` and receives JSON messages on all 9 MQTT topics. Payloads parse correctly. QoS 0 and QoS 1 messages are both handled.

### 11.2 Data Realism

A packaging industry professional (or someone with equivalent domain knowledge) reviews 24 hours of simulator output in a time-series chart and cannot distinguish it from real factory data based on signal shapes, value ranges, noise characteristics, and correlation patterns.

Specific checks:
- Line speed shows realistic ramp-up profiles, not step functions.
- Dryer temperatures track setpoints with thermal lag, not instant response.
- Web tension fluctuates during speed changes and stabilizes during steady state.
- Counters increment smoothly during running and freeze during idle.
- Energy consumption correlates with machine state.
- Vibration levels increase when the machine is running.
- Environmental temperature follows a daily cycle.
- Ink level depletes over hours, not minutes.

### 11.3 Anomaly Detection

Each scenario type produces a pattern that is detectable by a basic anomaly detection algorithm (3-sigma, CUSUM, or simple threshold).

| Scenario | Detection Method | Expected Signal |
|----------|-----------------|-----------------|
| Web break | Threshold on web tension | Spike > 600 N followed by drop to 0 |
| Dryer drift | CUSUM on (dryer_temp - setpoint) | Sustained positive deviation over 30+ minutes |
| Bearing wear | Trend on vibration RMS | Linear increase over days/weeks |
| Ink excursion | Threshold on ink viscosity | Value outside 18-45 range |
| Registration drift | Threshold on registration error | Value outside +/-0.3 mm |
| Cold start spike | Threshold on line power | Spike > 150% of running average |
| Shift change | Pattern in machine state | Regular 8-hour gaps in Running state |

### 11.4 Continuous Operation

The simulator runs for 7 consecutive days without:
- Memory leaks (RSS stays within 2x of initial).
- CPU runaway (stays below 20% of one core at 1x speed).
- Protocol server crashes or unhandled exceptions.
- Divergent signal values (no NaN, no infinity, no values outside configured ranges).
- Counter overflows (unless configured for rollover testing).

### 11.5 Time Compression

At 100x speed, all 40 signals produce values at 100x their configured rate. The protocol servers can keep up. No data is dropped due to throughput limits. CollatrEdge collects data at the compressed rate.

### 11.6 Reproducibility

With the same random seed and configuration, two independent runs of the simulator produce byte-identical signal sequences for the first 1 million data points.

---

## Appendix A: Full Modbus Register Map

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
| 600-601 | energy.line_power | float32 | ABCD | 1.0 | kW | No |
| 602-603 | energy.cumulative_kwh | float32 | ABCD | 1.0 | kWh | No |

Addresses 0-99 and 700+ are reserved for future equipment.
Addresses 900-999 are reserved for simulator control registers (e.g., trigger scenario, set time scale).

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

## Appendix B: Full OPC-UA Node Tree

Namespace URI: `urn:collatr:factory-simulator`
Namespace index: 2

```
Root (i=84)
  Objects (i=85)
    Server (i=2253)
    PackagingLine (ns=2;s=PackagingLine)
    |
    +-- Press1 (ns=2;s=PackagingLine.Press1)
    |   +-- LineSpeed             (ns=2;s=PackagingLine.Press1.LineSpeed)              Double, m/min
    |   +-- WebTension            (ns=2;s=PackagingLine.Press1.WebTension)             Double, N
    |   +-- State                 (ns=2;s=PackagingLine.Press1.State)                  UInt16, enum
    |   +-- FaultCode             (ns=2;s=PackagingLine.Press1.FaultCode)              UInt16
    |   +-- ImpressionCount       (ns=2;s=PackagingLine.Press1.ImpressionCount)        UInt32
    |   +-- GoodCount             (ns=2;s=PackagingLine.Press1.GoodCount)              UInt32
    |   +-- WasteCount            (ns=2;s=PackagingLine.Press1.WasteCount)             UInt32
    |   +-- Registration
    |   |   +-- ErrorX            (ns=2;s=PackagingLine.Press1.Registration.ErrorX)    Double, mm
    |   |   +-- ErrorY            (ns=2;s=PackagingLine.Press1.Registration.ErrorY)    Double, mm
    |   +-- Ink
    |   |   +-- Viscosity         (ns=2;s=PackagingLine.Press1.Ink.Viscosity)          Double, seconds
    |   |   +-- Temperature       (ns=2;s=PackagingLine.Press1.Ink.Temperature)        Double, C
    |   +-- Dryer
    |   |   +-- Zone1
    |   |   |   +-- Temperature   (ns=2;s=PackagingLine.Press1.Dryer.Zone1.Temperature) Double, C
    |   |   |   +-- Setpoint      (ns=2;s=PackagingLine.Press1.Dryer.Zone1.Setpoint)    Double, C
    |   |   +-- Zone2
    |   |   |   +-- Temperature   (ns=2;s=PackagingLine.Press1.Dryer.Zone2.Temperature) Double, C
    |   |   |   +-- Setpoint      (ns=2;s=PackagingLine.Press1.Dryer.Zone2.Setpoint)    Double, C
    |   |   +-- Zone3
    |   |       +-- Temperature   (ns=2;s=PackagingLine.Press1.Dryer.Zone3.Temperature) Double, C
    |   |       +-- Setpoint      (ns=2;s=PackagingLine.Press1.Dryer.Zone3.Setpoint)    Double, C
    |   +-- MainDrive
    |   |   +-- Current           (ns=2;s=PackagingLine.Press1.MainDrive.Current)      Double, A
    |   |   +-- Speed             (ns=2;s=PackagingLine.Press1.MainDrive.Speed)        Double, RPM
    |   +-- NipPressure           (ns=2;s=PackagingLine.Press1.NipPressure)            Double, bar
    |   +-- Unwind
    |   |   +-- Diameter          (ns=2;s=PackagingLine.Press1.Unwind.Diameter)        Double, mm
    |   +-- Rewind
    |       +-- Diameter          (ns=2;s=PackagingLine.Press1.Rewind.Diameter)        Double, mm
    |
    +-- Laminator1 (ns=2;s=PackagingLine.Laminator1)
    |   +-- NipTemperature        (ns=2;s=PackagingLine.Laminator1.NipTemperature)     Double, C
    |   +-- NipPressure           (ns=2;s=PackagingLine.Laminator1.NipPressure)        Double, bar
    |   +-- OvenTemperature       (ns=2;s=PackagingLine.Laminator1.OvenTemperature)    Double, C
    |   +-- WebSpeed              (ns=2;s=PackagingLine.Laminator1.WebSpeed)           Double, m/min
    |   +-- AdhesiveWeight        (ns=2;s=PackagingLine.Laminator1.AdhesiveWeight)     Double, g/m2
    |
    +-- Slitter1 (ns=2;s=PackagingLine.Slitter1)
    |   +-- Speed                 (ns=2;s=PackagingLine.Slitter1.Speed)                Double, m/min
    |   +-- WebTension            (ns=2;s=PackagingLine.Slitter1.WebTension)           Double, N
    |   +-- ReelCount             (ns=2;s=PackagingLine.Slitter1.ReelCount)            UInt32
    |
    +-- Energy (ns=2;s=PackagingLine.Energy)
        +-- LinePower             (ns=2;s=PackagingLine.Energy.LinePower)              Double, kW
        +-- CumulativeKwh         (ns=2;s=PackagingLine.Energy.CumulativeKwh)          Double, kWh
```

All leaf nodes have the following OPC-UA attributes:
- `AccessLevel`: Read-only (except setpoint nodes which are Read/Write)
- `MinimumSamplingInterval`: Matches the signal's configured sample rate in milliseconds
- `EURange`: Set to the signal's configured min/max range
- `EngineeringUnits`: Set to the signal's unit string

---

## Appendix C: Full MQTT Topic Map

### Plain JSON Topics

| Topic | Signal | QoS | Retain | Publish Rate |
|-------|--------|-----|--------|-------------|
| `collatr/factory/demo/line3/coder/state` | coder.state | 1 | Yes | Event-driven |
| `collatr/factory/demo/line3/coder/prints_total` | coder.prints_total | 1 | Yes | Event-driven |
| `collatr/factory/demo/line3/coder/ink_level` | coder.ink_level | 0 | Yes | 60s |
| `collatr/factory/demo/line3/coder/printhead_temp` | coder.printhead_temp | 0 | Yes | 30s |
| `collatr/factory/demo/line3/env/ambient_temp` | env.ambient_temp | 0 | Yes | 60s |
| `collatr/factory/demo/line3/env/ambient_humidity` | env.ambient_humidity | 0 | Yes | 60s |
| `collatr/factory/demo/line3/vibration/main_drive_x` | vibration.main_drive_x | 0 | No | 1s |
| `collatr/factory/demo/line3/vibration/main_drive_y` | vibration.main_drive_y | 0 | No | 1s |
| `collatr/factory/demo/line3/vibration/main_drive_z` | vibration.main_drive_z | 0 | No | 1s |

### JSON Payload Schema

```json
{
  "timestamp": "2026-03-01T14:30:00.000Z",
  "value": 42.7,
  "unit": "C",
  "quality": "good"
}
```

Field types:
- `timestamp`: string (ISO 8601 with milliseconds, UTC)
- `value`: number (float64 JSON number, no string encoding)
- `unit`: string (engineering unit abbreviation)
- `quality`: string, one of: `"good"`, `"uncertain"`, `"bad"`

### Batch Vibration Topic (Alternative)

For high-frequency vibration data, an alternative batch topic publishes all three axes in one message:

```
collatr/factory/demo/line3/vibration/main_drive
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

---

## Appendix D: Configuration Reference

### Signal Model Parameters

#### steady_state

```yaml
model: "steady_state"
params:
  target: 85.0          # Target value
  sigma: 0.3            # Gaussian noise standard deviation
  min_clamp: 40.0       # Minimum allowed value
  max_clamp: 120.0      # Maximum allowed value
```

#### sinusoidal

```yaml
model: "sinusoidal"
params:
  center: 22.0          # Center of oscillation
  amplitude: 5.0        # Peak deviation from center
  period_hours: 24.0    # Full cycle period in hours
  phase_hours: 6.0      # Phase offset in hours (0 = peak at midnight)
  sigma: 0.1            # Gaussian noise
```

#### first_order_lag

```yaml
model: "first_order_lag"
params:
  tau_seconds: 60.0     # Time constant in seconds
  sigma: 0.3            # Gaussian noise
  overshoot: 0.05       # Overshoot factor (0 = no overshoot)
  setpoint_signal: "press.dryer_setpoint_zone_1"  # Signal to track
```

#### ramp

```yaml
model: "ramp"
params:
  ramp_up_seconds: 180  # Seconds to ramp from 0 to target
  ramp_down_seconds: 30 # Seconds to ramp from target to 0
  sigma: 0.5            # Gaussian noise during ramp and steady state
```

#### random_walk

```yaml
model: "random_walk"
params:
  center: 28.0          # Mean reversion target
  drift_rate: 0.1       # Random walk step magnitude
  reversion_rate: 0.01  # Mean reversion strength (0-1)
  min_clamp: 15.0
  max_clamp: 60.0
```

#### counter

```yaml
model: "counter"
params:
  rate_per_speed_unit: 1.0    # Increments per (m/min * second)
  speed_signal: "press.line_speed"  # Signal that drives increment rate
  rollover_value: 4294967295  # Counter wrap value (uint32 max)
  reset_on_job_change: true   # Reset to 0 on job changeover
```

#### depletion

```yaml
model: "depletion"
params:
  initial_value: 100.0      # Starting value
  consumption_rate: 0.002   # Depletion per counter increment
  counter_signal: "coder.prints_total"  # Signal that drives depletion
  refill_threshold: 10.0    # Trigger refill at this value
  refill_value: 100.0       # Value after refill
  refill_delay_seconds: 300 # Time for refill operation
```

#### correlated_follower

```yaml
model: "correlated_follower"
params:
  parent_signal: "press.line_speed"
  transform: "linear"       # linear, quadratic, or custom
  base: 15.0                # Output when parent = 0
  factor: 0.5               # Output = base + factor * parent
  sigma: 0.5                # Additional Gaussian noise
  lag_seconds: 0            # Delay following parent changes
```

#### state_machine

```yaml
model: "state_machine"
params:
  initial_state: 0
  transitions:
    - from: 0    # Off
      to: 1      # Setup
      trigger: "press_power_on"
      min_duration_seconds: 0
    - from: 1    # Setup
      to: 2      # Running
      trigger: "setup_complete"
      min_duration_seconds: 600
      max_duration_seconds: 1800
    - from: 2    # Running
      to: 1      # Setup (changeover)
      trigger: "job_changeover"
      probability_per_hour: 0.5
    - from: 2    # Running
      to: 4      # Fault
      trigger: "random_fault"
      probability_per_hour: 0.25
    - from: 4    # Fault
      to: 1      # Setup (recovery)
      trigger: "fault_cleared"
      min_duration_seconds: 300
      max_duration_seconds: 3600
    - from: 2    # Running
      to: 3      # Idle (shift change)
      trigger: "shift_change"
    - from: 3    # Idle
      to: 1      # Setup (new shift)
      trigger: "new_shift_start"
      min_duration_seconds: 300
      max_duration_seconds: 900
```

### Protocol Mapping Reference

Each signal can specify its protocol mappings inline:

```yaml
signals:
  line_speed:
    model: "ramp"
    params:
      ramp_up_seconds: 180
      ramp_down_seconds: 30
      sigma: 0.5
    sample_rate_ms: 1000
    protocols:
      modbus:
        register_type: "holding"
        address: 100
        data_type: "float32"
        byte_order: "ABCD"
      opcua:
        node_id: "PackagingLine.Press1.LineSpeed"
        data_type: "Double"
      mqtt:
        topic: "press/line_speed"
        qos: 0
        retain: true
```

### Scenario Parameters Reference

```yaml
scenarios:
  # Job changeover: production stops, setup, restart
  job_changeover:
    enabled: true
    frequency_per_shift: [3, 6]       # Uniform random in range
    duration_seconds: [600, 1800]     # Setup time range
    speed_change_probability: 0.3     # Chance new job uses different speed
    new_speed_range: [100, 350]       # Speed range for new job
    counter_reset_probability: 0.7    # Chance counters reset for new job
    dryer_setpoint_change_probability: 0.2  # Chance dryer setpoint changes
    startup_waste_rate: 0.05          # 5% waste during first 3 minutes

  # Web break: sudden fault condition
  web_break:
    enabled: true
    frequency_per_week: [1, 2]
    tension_spike_n: [600, 800]       # Tension spike magnitude in Newtons
    spike_duration_ms: [100, 500]     # Spike duration
    recovery_seconds: [900, 3600]     # Recovery time

  # Dryer temperature drift: gradual quality issue
  dryer_drift:
    enabled: true
    frequency_per_shift: [1, 2]
    affected_zone: "random"           # random, 1, 2, or 3
    drift_rate_c_per_min: [0.05, 0.2]
    max_drift_c: [5, 15]
    duration_seconds: [1800, 7200]
    waste_rate_increase_percent: [20, 50]

  # Bearing wear: long-term degradation
  bearing_wear:
    enabled: true
    start_after_hours: 48
    vibration_increase_per_hour: [0.01, 0.05]  # mm/s per hour
    warning_threshold: 15.0           # mm/s
    alarm_threshold: 25.0             # mm/s
    current_increase_percent: [1, 5]  # Motor current increase
    culminate_in_failure: false
    failure_vibration: [40, 50]       # mm/s at failure

  # Ink viscosity excursion
  ink_viscosity_excursion:
    enabled: true
    frequency_per_shift: [2, 3]
    excursion_type: "random"          # low, high, or random
    low_threshold: 18.0               # seconds
    high_threshold: 45.0              # seconds
    drift_rate: [0.1, 0.5]           # seconds per minute
    duration_seconds: [300, 1800]
    waste_rate_increase_percent: [10, 30]

  # Registration drift
  registration_drift:
    enabled: true
    frequency_per_shift: [1, 3]
    affected_axis: "random"           # x, y, or random
    drift_rate_mm_per_sec: [0.01, 0.05]
    max_drift_mm: [0.3, 0.5]
    duration_seconds: [120, 600]
    trigger_on_speed_change: true

  # Unplanned stop
  unplanned_stop:
    enabled: true
    frequency_per_shift: [1, 2]
    duration_seconds: [300, 3600]
    fault_codes: [101, 102, 201, 202, 301, 302, 401, 402, 501, 502]
    fault_code_weights: [0.15, 0.10, 0.15, 0.05, 0.10, 0.10, 0.15, 0.10, 0.05, 0.05]

  # Shift change
  shift_change:
    enabled: true
    times: ["06:00", "14:00", "22:00"]
    changeover_seconds: [300, 900]
    night_shift_speed_factor: 0.90
    night_shift_waste_factor: 1.10
    weekend_enabled: false
    weekend_shutdown_hours: [22, 6]   # Friday 22:00 to Monday 06:00

  # Cold start energy spike
  cold_start_spike:
    enabled: true
    idle_threshold_minutes: 30
    spike_magnitude: [1.5, 2.0]       # Multiplier on running power
    spike_duration_seconds: [2, 5]
    current_spike_magnitude: [2.0, 3.0]  # Multiplier on running current

  # Coder consumable depletion
  coder_depletion:
    enabled: true
    ink_consumption_per_1000_prints: [1.0, 3.0]  # % per 1000 prints
    low_ink_warning_percent: 10.0
    empty_fault_percent: 2.0
    refill_delay_seconds: [60, 300]
```

### Data Quality Injection Parameters

```yaml
data_quality:
  # Communication drops
  modbus_drop:
    enabled: true
    frequency_per_hour: [1, 2]
    duration_seconds: [1, 10]
  
  opcua_stale:
    enabled: true
    frequency_per_hour: [1, 2]
    duration_seconds: [5, 30]
  
  mqtt_drop:
    enabled: true
    frequency_per_hour: [1, 2]
    duration_seconds: [5, 30]
  
  # Sensor noise (per-signal sigma values override defaults)
  noise:
    enabled: true
    global_sigma_multiplier: 1.0      # Scale all sigma values
  
  # Duplicate timestamps
  duplicate_probability: 0.0001       # Per read/publish
  
  # Modbus exceptions
  exception_probability: 0.001        # Per read request
  timeout_probability: 0.0005         # Per read request
  response_delay_ms: [0, 50]          # Uniform random range
  
  # Counter rollover (for testing)
  counter_rollover:
    press.impression_count: 4294967295  # uint32 max (default)
    press.good_count: 4294967295
    press.waste_count: 4294967295
    coder.prints_total: 4294967295
    energy.cumulative_kwh: 999999.0
  
  # Timezone offset (for MQTT timestamps)
  mqtt_timestamp_offset_hours: 0      # 0 = UTC, 1 = BST, -5 = US Eastern
```

---

## Appendix E: Project Structure

```
collatr-factory-simulator/
  config/
    factory.yaml              # Main configuration
    scenarios/
      packaging-line.yaml     # Default packaging line scenarios
      bearing-failure.yaml    # Long-duration bearing failure scenario
      stress-test.yaml        # High-rate stress test configuration
  
  src/
    __init__.py
    main.py                   # Entry point
    config.py                 # Configuration loading and validation
    clock.py                  # Simulation clock (time management)
    store.py                  # Signal value store
    
    engine/
      __init__.py
      data_engine.py          # Main generation loop
      scenario_engine.py      # Scenario scheduling and execution
      state_machine.py        # Equipment state machine logic
      correlation.py          # Cross-signal correlation model
    
    generators/
      __init__.py
      base.py                 # EquipmentGenerator ABC
      press.py                # Flexographic press signals
      laminator.py            # Laminator signals
      slitter.py              # Slitter signals
      coder.py                # Coding and marking signals
      environment.py          # Environmental sensors
      energy.py               # Energy monitoring
      vibration.py            # Vibration monitoring
    
    models/
      __init__.py
      steady_state.py         # Steady state with noise
      sinusoidal.py           # Sinusoidal with noise
      first_order_lag.py      # Setpoint tracking
      ramp.py                 # Ramp up/down
      random_walk.py          # Random walk with mean reversion
      counter.py              # Counter increment
      depletion.py            # Consumable depletion
      correlated.py           # Correlated follower
      state.py                # State machine
    
    protocols/
      __init__.py
      modbus_server.py        # Modbus TCP server adapter
      opcua_server.py         # OPC-UA server adapter
      mqtt_adapter.py         # MQTT broker/publisher adapter
    
    scenarios/
      __init__.py
      job_changeover.py
      web_break.py
      dryer_drift.py
      bearing_wear.py
      ink_excursion.py
      registration_drift.py
      unplanned_stop.py
      shift_change.py
      cold_start.py
      coder_depletion.py
    
    health/
      __init__.py
      server.py               # HTTP health check and status endpoint
  
  tests/
    test_config.py
    test_clock.py
    test_store.py
    test_generators/
      test_press.py
      test_laminator.py
      test_coder.py
    test_models/
      test_steady_state.py
      test_first_order_lag.py
      test_counter.py
      test_random_walk.py
    test_protocols/
      test_modbus.py
      test_opcua.py
      test_mqtt.py
    test_scenarios/
      test_web_break.py
      test_bearing_wear.py
      test_shift_change.py
    test_integration/
      test_full_run.py        # Spin up simulator, connect clients, verify data
  
  Dockerfile
  docker-compose.yaml
  requirements.txt
  requirements-dev.txt
  pyproject.toml
  README.md
  LICENSE
```

---

## Appendix F: Implementation Phases

### Phase 1: Core Engine and Modbus (Weeks 1-3)

**Goal:** Simulator starts, generates all 40 signals, serves them over Modbus TCP.

- Configuration loader (YAML parsing, validation).
- Simulation clock with time compression.
- Signal value store.
- All 9 signal models (steady_state through state_machine).
- All 7 equipment generators.
- Correlation model linking generators.
- Modbus TCP server with full register map.
- Basic scenario support (job changeover, shift change, unplanned stop).
- Docker container.
- Integration test: pymodbus client reads all registers and verifies value ranges.

**Exit criteria:** CollatrEdge connects via Modbus TCP and collects data from all holding registers, input registers, coils, and discrete inputs for 1 hour. Values are within expected ranges. Counters increment. State transitions occur.

### Phase 2: OPC-UA and MQTT (Weeks 4-5)

**Goal:** All three protocols serve data simultaneously.

- OPC-UA server with full node tree.
- OPC-UA subscriptions and data change notifications.
- Embedded MQTT broker.
- MQTT publishing with JSON payloads.
- Retained messages.
- QoS 0 and QoS 1 support.
- Integration test: CollatrEdge connects to all three protocols simultaneously.

**Exit criteria:** CollatrEdge collects data from Modbus, OPC-UA, and MQTT simultaneously for 24 hours. No protocol server crashes. Data from all three protocols correlates (same machine state, same line speed across protocols).

### Phase 3: Full Scenario System (Weeks 6-7)

**Goal:** All 10 scenario types operational with configurable scheduling.

- Web break scenario with tension spike.
- Dryer temperature drift.
- Motor bearing wear (long-term degradation).
- Ink viscosity excursion.
- Registration drift.
- Cold start energy spike.
- Coder consumable depletion.
- Data quality injection (communication drops, sensor noise, exceptions).
- Scenario scheduling engine (statistical profiles).
- Random seed support for reproducible runs.

**Exit criteria:** Run the simulator for 7 days at 100x speed (1.68 real hours). All scenario types fire at least once. Anomaly patterns are detectable by threshold-based checks. No divergent values. Memory stable.

### Phase 4: Polish and Documentation (Week 8)

**Goal:** Ready for engineering team use and demo deployment.

- README with quick start guide.
- Configuration documentation.
- Example CollatrEdge configuration files for connecting to the simulator.
- Docker Compose with health checks.
- CI pipeline running integration tests against the simulator.
- Performance profiling at 100x speed.
- Web dashboard showing current signal values and active scenarios (optional).

**Exit criteria:** A new engineer can clone the repo, run `docker compose up`, and connect CollatrEdge within 15 minutes following the README.

---

## Appendix G: Reference Documents

| Document | Path | Relevance |
|----------|------|-----------|
| Target Customer Data Profiles | `research/research-target-customer-data-profiles.md` | 40-signal demo spec, register maps, protocol patterns, anomaly definitions |
| VisionLog Trial Schema | *(internal reference, not distributed)* | AX350i, R-Series, IOLink sensor patterns, data quality issues |
| Equipment Telemetry Analysis | *(internal reference, not distributed)* | Digital press telemetry, print head temps, pneumatics, ink pumps |
| Public Data Sources | `research/research-public-datasources-for-test-and-demo.md` | Microsoft OPC PLC, Modbus simulators, MQTT brokers |
| Real-World Industrial Datasets | `research/research-real-world-industrial-datasets.md` | SKAB, DAMADICS, MetroPT, Hydraulic Systems benchmarks |
| Round 2 Dataset Research | `research/research-targeted-datasets-round2.md` | Packaging data gap, layered simulator architecture, phase plan |
| IoT Platform Analysis | *(internal reference, not distributed)* | Industrial IoT platform context |

---

*End of document.*