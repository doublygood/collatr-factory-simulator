# Simulated Factory Layout: Packaging and Printing

> **Companion profile:** [02b-factory-layout-food-and-beverage.md](02b-factory-layout-food-and-beverage.md) defines a second factory profile for UK chilled food manufacturing. Both profiles share the same data generation engine, scenario system, and protocol endpoints. The factory profile is selected via configuration.

## 2.1 Factory Overview

The simulator models a single packaging production line with seven equipment groups. The equipment represents what a typical UK flexible packaging converter operates. The line produces 47 signals across three protocols.

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

**Potential future addition: Compressed air system.** Most packaging factories have a central compressor supplying pneumatic actuators across the line. A compressor monitoring station (pressure, temperature, motor current, flow rate, dew point) would add a different equipment archetype and exercise different anomaly signatures. The MetroPT dataset (15 signals, 1s resolution, 6 months, real compressor with ground-truth failures) provides an excellent public calibration source. This is deferred from the initial 47-signal set but would be a strong addition for demonstrating predictive maintenance capabilities.

## 2.2 Equipment: Flexographic Press

The flexographic press is the primary machine. It produces 21 of the 47 signals. It represents a central impression (CI) flexographic press from vendors like BOBST, Soma, or W&H. The PLC is a Siemens S7-1500 serving data over OPC-UA and Modbus TCP.

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

## 2.3 Equipment: Laminator

The laminator bonds two web materials using adhesive. It produces 5 signals. It represents a solvent-free laminator from vendors like Nordmeccanica or Comexi. Controlled by a Siemens S7-1500 or Schneider Modicon PLC.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 22 | `laminator.nip_temp` | Nip roller temperature | 30-80 | C | 5s | Modbus HR |
| 23 | `laminator.nip_pressure` | Nip roller pressure | 1-8 | bar | 5s | Modbus HR |
| 24 | `laminator.tunnel_temp` | Conditioning tunnel temp | 40-100 | C | 5s | Modbus HR |
| 25 | `laminator.web_speed` | Laminator web speed | 50-400 | m/min | 1s | Modbus HR |
| 26 | `laminator.adhesive_weight` | Adhesive coat weight | 1.0-5.0 | g/m2 | 30s | Modbus HR |

The laminator web speed tracks the press line speed with a small offset (the laminator processes material after the press). When the press stops, the laminator continues briefly to clear its own web path, then stops.

## 2.4 Equipment: Slitter

The slitter cuts wide rolls into narrow reels. It produces 3 signals.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 27 | `slitter.speed` | Slitting speed | 100-800 | m/min | 1s | Modbus HR |
| 28 | `slitter.web_tension` | Slitter web tension | 10-200 | N | 500ms | OPC-UA |
| 29 | `slitter.reel_count` | Completed reels | 0-9999 | count | event | Modbus HR |

The slitter operates independently from the press. It runs faster (up to 800 m/min vs 400 m/min for the press). It processes rolls that the press produced earlier. Its schedule is offset from press production by hours or shifts.

## 2.5 Equipment: Coding and Marking

The coder is a continuous inkjet printer (modeled on industrial AX-series CIJ patterns). It prints date codes, batch numbers, and barcodes onto the packaging material. It produces 11 signals. The private reference data contains extensive telemetry from industrial digital presses with pneumatic ink systems, pump speeds, tank levels, and head temperatures that inform the signal set for this equipment group.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 30 | `coder.state` | Printer operating state | 0-4 | enum | event | MQTT |
| 31 | `coder.prints_total` | Total prints since power-on | 0-999,999,999 | count | event | MQTT |
| 32 | `coder.ink_level` | Ink cartridge level | 0-100 | % | 60s | MQTT |
| 33 | `coder.printhead_temp` | Printhead temperature | 25-50 | C | 30s | MQTT |
| 34 | `coder.ink_pump_speed` | Ink supply pump speed | 0-500 | RPM | 5s | MQTT |
| 35 | `coder.ink_pressure` | Ink system pressure (lung) | 0-900 | mbar | 5s | MQTT |
| 36 | `coder.ink_viscosity_actual` | Measured ink viscosity | 2-15 | cP | 30s | MQTT |
| 37 | `coder.supply_voltage` | Head power supply voltage | 22-26 | V | 60s | MQTT |
| 38 | `coder.ink_consumption_ml` | Cumulative ink consumption | 0-999,999 | ml | 60s | MQTT |
| 39 | `coder.nozzle_health` | Nozzle check pass rate | 0-100 | % | event | MQTT |
| 40 | `coder.gutter_fault` | Gutter fault detection | 0-1 | bool | event | MQTT |

**Coder state enum:**

| Value | State |
|-------|-------|
| 0 | Off |
| 1 | Ready |
| 2 | Printing |
| 3 | Fault |
| 4 | Standby |

**How reference data informs this equipment:**

The AX350i data (public schema) shows `wasPrinted` boolean toggling with production and `currentLineSpeed` tracking the line state. The private reference data provides deep telemetry from industrial digital presses that informs the pneumatic and ink system signals:

- `PS_Pnm_FillInkPumpSpeed` (0-5128 RPM, bimodal: 0 idle or 200-500 active) and `PS_Pnm_DrainInkPumpSpeed` (0-769 RPM, mostly idle) inform `coder.ink_pump_speed`. The CIJ coder uses a single supply pump rather than the fill/drain pair on a digital press, so we combine these into one signal with a narrower 0-500 RPM range.
- `PS_Pnm_LungPressure` (830-840 mbar normal, 60 mbar stddev) informs `coder.ink_pressure`. Lung pressure is the back-pressure that drives ink through the nozzle. Stable pressure means consistent drop formation.
- `PS_Pnm_InkConsumptionMl` (accumulating to 4909 ml over production runs) informs `coder.ink_consumption_ml`. We model ink depletion as a linear function of print count with small random variation in consumption rate per print.
- `PS_Head_TempFirepulse` (52C mean, 2.8C stddev, 33-82C range) informs `coder.printhead_temp`. Our simulator uses a narrower 25-50C range because a CIJ coder runs cooler than a digital press fire pulse.
- `PS_Head_PowerSupply24V` (23-25V, very tight range) informs `coder.supply_voltage`. A stable 24V supply is expected. Drift outside 23-25V indicates a power issue.
- `PS_Pnm_FillTank` and `PS_Pnm_DrainTank` (the highest-volume signals in the dataset, with cyclic fill/drain patterns) inform the relationship between pump speed and ink level. Tank levels show sawtooth patterns as ink is consumed and replenished.

The coder state machine transitions match patterns observed in the AX data. The printer spends most of its time in Ready (1) or Printing (2). It enters Standby (4) during press idle periods. It enters Fault (3) rarely. The reference data showed error rows when the device was unreachable, and the real printer had clear on/off cycling aligned with the production line state.

`coder.nozzle_health` and `coder.gutter_fault` are CIJ-specific signals. Nozzle health degrades gradually over time (ink buildup on the nozzle plate) and recovers after cleaning cycles. Gutter faults occur when ink drops miss the gutter catch, typically caused by pressure instability or contamination. These are the two most common CIJ fault modes and provide clear predictive maintenance signals.

## 2.6 Equipment: Vision Inspection

The vision inspection system is not one of the primary signals but its behaviour informs the coder and press quality signals. It is modeled on industrial vision inspection systems patterns from the reference data.

The reference data showed an 85.6% fail rate in the vision stream. This is not representative of normal operation and should not be replicated in the simulator. The high fail rate had two causes: the vision system reports F (Fail) for every read attempt when the line is idle or no product is present (camera sees nothing, reads nothing, reports fail), and line operators were not adequately trained on R-Series use. The camera was sometimes not pointing at the line. The "read" is triggered by an optical sensor on the line firing the R-Series camera, so a misaligned camera produces no-read failures rather than quality failures. This is an operator training problem, not an equipment defect.

The simulator should model a properly operated vision system with realistic failure rates: low single-digit percent during production (1-3% reject rate from genuine print quality issues), higher during startup and changeover where you would expect them (5-10% as the line stabilises), and zero during idle (no reads attempted). This reflects a factory where operators know how to use the equipment.

This pattern informs the `press.waste_count` signal. When the press is Running (state 2), waste increments at 1-3% of impressions reflecting genuine quality rejects. During startup (state 1 to state 2 transition), waste rate is higher for the first 2-5 minutes as registration stabilises. When the press is Idle (state 3), no waste is generated. The vision fail rate pattern shows that data from inspection systems requires context to interpret correctly.

## 2.7 Equipment: Environmental Sensors

Environmental sensors monitor the factory floor conditions. They produce 2 signals. They represent Balluff IOLink sensors connected via an IOLink master, communicating over MQTT.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 41 | `env.ambient_temp` | Factory floor temperature | 15-35 | C | 60s | MQTT |
| 42 | `env.ambient_humidity` | Factory floor humidity | 30-80 | %RH | 60s | MQTT |

**How reference data informs this equipment:**

The IOLink BCM0002 sensor data showed humidity ranging from 15-80% and contact temperature from 20-40C. The simulator uses these ranges directly. The reference data also showed ambient pressure (990-1030 hPa) and vibration RMS (0-50 mm/s) on the same sensor. The simulator separates vibration into its own equipment group.

The BNI0042 static charge sensor data (0-5 kV) is not included in the 47-signal spec but could be added as a future extension. Static charge is relevant for packaging materials that generate electrostatic buildup during unwinding.

Environmental signals follow a slow sinusoidal daily pattern. Temperature peaks in the afternoon. Humidity inversely correlates with temperature. These patterns are well-established in the Appliances Energy dataset from the public datasets research.

## 2.8 Equipment: Energy Monitoring

Energy monitoring tracks power consumption for the entire line. It produces 2 signals. It represents a Schneider PM5xxx smart power meter connected via Modbus TCP.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 43 | `energy.line_power` | Instantaneous line power | 0-200 | kW | 1s | Modbus HR |
| 44 | `energy.cumulative_kwh` | Cumulative energy consumption | 0-999,999 | kWh | 60s | Modbus HR |

Energy consumption correlates with press operating state. Base load when idle is 5-15 kW (electronics, lighting, HVAC). Running load is 60-150 kW depending on speed. Cold start produces a 50% inrush spike lasting 2-5 seconds as motors energize. The Steel Industry Energy dataset from the public datasets research showed daily and weekly load patterns with clear shift changes. The simulator replicates these patterns.

## 2.9 Equipment: Vibration Monitoring

Vibration sensors monitor the press main drive motor. They produce 3 signals. They represent a retrofit wireless vibration sensor (Banner, Pepperl+Fuchs, or similar) communicating over MQTT.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 45 | `vibration.main_drive_x` | X-axis vibration RMS | 0-50 | mm/s | 1s | MQTT |
| 46 | `vibration.main_drive_y` | Y-axis vibration RMS | 0-50 | mm/s | 1s | MQTT |
| 47 | `vibration.main_drive_z` | Z-axis vibration RMS | 0-50 | mm/s | 1s | MQTT |

**How reference data informs this equipment:**

The IOLink BCM0002 sensor data included `v_rms_magnitude` with a range of 0-50 mm/s. The SKAB benchmark dataset (from the public datasets research) provides 8 channels at 1-second resolution from a real testbed including vibration RMS, current, pressure, and temperature. The IMS/NASA bearing dataset provides 35-day run-to-failure vibration data at 10-minute intervals.

Normal vibration for a healthy motor at operating speed is 2-8 mm/s RMS. Bearing wear causes a gradual increase to 15-25 mm/s over weeks. Imbalance or misalignment causes periodic spikes. The simulator models both healthy baseline and degradation trends.

## 2.10 Publicly Available Datasets for Equipment Calibration

The following public datasets can calibrate the simulator's statistical models for equipment types that lack private reference data. These datasets provide real-world signal ranges, noise characteristics, correlation structures, and anomaly patterns.

| Equipment Group | Relevant Public Datasets | What They Provide |
|---|---|---|
| Flexographic Press | DAMADICS Actuator Benchmark (32 signals, 1 Hz, 25 days, sugar factory) | Process control dynamics: valve positions, pressures, flows, temperatures. Informs dryer temperature PID behaviour and nip pressure control loops. |
| Flexographic Press | Condition Monitoring of Hydraulic Systems (17 sensors, multi-rate, 2205 cycles) | Motor current/power patterns, pressure dynamics, flow rates. Informs press main drive current and hydraulic subsystems. |
| Energy Monitoring | Steel Industry Energy Consumption (10 variables, 15-min, 1 year) | Real kWh, reactive power, power factor, CO2 from a steel plant. Daily and weekly load patterns with clear shift changes. Directly maps to energy meter Modbus registers. |
| Vibration Monitoring | IMS/NASA Bearing Run-to-Failure (4 bearings, 35 days, 10-min snapshots) | Real degradation from healthy to failure. Vibration signatures of inner race, outer race, and roller faults. |
| Vibration Monitoring | SKAB Anomaly Benchmark (8 sensors, 1s, 35 experiments) | Vibration RMS, current, pressure, temperature from a real testbed. Labelled anomalies and changepoints. |
| Vibration Monitoring | Paderborn University Bearing (vibration + motor current, 64 kHz) | Combined vibration and motor current signatures. Real wear damage alongside artificial damage for comparison. |
| Environmental Sensors | Appliances Energy Prediction (temp + humidity, 9 zones, 10-min, 4.5 months) | Daily temperature and humidity cycles from distributed sensors. Informs slow sinusoidal environmental patterns. |
| Coding & Marking | Private reference data | Pump speeds, tank levels, lung pressure, head temperatures, ink consumption. CIJ-specific signal patterns. |
| Vision Inspection | Private reference data | Vision pass/fail patterns, event-driven streams, idle vs production behaviour. |

For equipment without direct public counterparts (laminator, slitter), signal generators should use first-principles models calibrated against vendor specifications and published process engineering literature. The flexographic press benefits from multiple public datasets because its subsystems (motors, hydraulics, temperature control, vibration) are well-represented in the research literature even if complete press datasets are not.

Additional public datasets worth cataloguing for future use: CNC Mill Tool Wear (force, vibration, acoustic emission from machining), Tennessee Eastman Process (52-variable chemical process, 21 fault types), and Petrobras 3W (oil well pressure, temperature, flow with real anomalies). These become relevant when the simulator expands to food and beverage, CNC machining, and process industry overlays. See `research/research-real-world-industrial-datasets.md` for full catalogue.

## 2.11 Signal Summary

| Protocol | Signal Count | Signals |
|----------|-------------|---------|
| Modbus TCP only | 19 | press.line_speed, press.ink_viscosity, press.ink_temperature, press.dryer_temp_zone_1/2/3, press.dryer_setpoint_zone_1/2/3, press.impression_count, press.good_count, press.waste_count, press.main_drive_current, press.main_drive_speed, press.nip_pressure, press.unwind_diameter, press.rewind_diameter, energy.line_power, energy.cumulative_kwh |
| OPC-UA only | 4 | press.web_tension, press.registration_error_x, press.registration_error_y, slitter.web_tension |
| Modbus TCP + OPC-UA | 7 | press.machine_state, laminator.nip_temp, laminator.nip_pressure, laminator.tunnel_temp, laminator.web_speed, laminator.adhesive_weight, slitter.speed |
| MQTT only | 16 | coder.state, coder.prints_total, coder.ink_level, coder.printhead_temp, coder.ink_pump_speed, coder.ink_pressure, coder.ink_viscosity_actual, coder.supply_voltage, coder.ink_consumption_ml, coder.nozzle_health, coder.gutter_fault, env.ambient_temp, env.ambient_humidity, vibration.main_drive_x/y/z |
| Event + counter | 1 | slitter.reel_count (Modbus) |

Total: 47 signals across 7 equipment groups.

Average aggregate sample rate: approximately 3 samples per second across all signals. Data volume: approximately 10,800 data points per hour, 259,200 per day, 7.8 million per month.
