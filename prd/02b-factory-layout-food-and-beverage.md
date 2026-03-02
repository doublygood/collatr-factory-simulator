# Factory Layout: Food and Beverage Production

> **Companion to:** [02-simulated-factory-layout.md](02-simulated-factory-layout.md) (Packaging & Printing line)
>
> This document defines a second factory profile for the simulator: a UK food manufacturing facility producing chilled ready meals. Both profiles share the same data generation engine, scenario system, and protocol endpoints. The difference is the equipment list, signal definitions, correlation models, and scenario scripts. See [Section 2.1](02-simulated-factory-layout.md#21-factory-overview) in the packaging layout for the design principle of configuration-level factory profiles.

---

## 2b.1 Factory Overview

The simulator models a chilled ready meal production line with nine equipment groups. The equipment represents what a typical UK chilled food manufacturer operates. Companies like Compleat Food Group, Greencore, Raynor Foods, and Samworth Brothers run factories built around this pattern. The line produces 65 signals across three protocols.

The ready meal line was chosen because it exercises equipment types absent from the packaging line (ovens, mixers, fillers, refrigeration, CIP) while sharing enough common ground (coding, environmental, energy, vibration) to validate the simulator's multi-profile architecture. It is also the strongest demo scenario for F&B prospects because ready meals touch every major food manufacturing process: ingredient handling, thermal processing, filling, sealing, coding, chilling, and inspection.

```
Ingredients     +-----------+    +----------+    +---------+    +----------+
   Input   ---->|  Mixing & |--->|  Oven /  |--->| Filling |--->| Sealing &|
                |  Blending |    |  Cooking |    | Station |    | Lidding  |
                +-----------+    +----------+    +---------+    +----------+
                                                      |              |
                +----------+    +----------+          v              v
                | Coding & |<---| Check-   |<--- +---------+   +-----------+
                | Marking  |    | weigher  |     | Metal   |   | Refriger- |
                +----------+    +----------+     | Detect  |   | ation     |
                     |                           +---------+   +-----------+
                     v
                +-----------+   +-----------+   +-----------+
                | Environ.  |   | Energy    |   | CIP       |
                | Sensors   |   | Monitor   |   | System    |
                +-----------+   +-----------+   +-----------+
```

**Shared equipment with the packaging line:** Coding and marking, environmental sensors, and energy monitoring use identical signal definitions to the packaging profile. This is intentional. A industrial CIJ coder on a food line behaves the same as a industrial CIJ coder on a packaging line. The simulator reuses these equipment modules across profiles.

**Equipment unique to F&B:** Mixing and blending, oven/cooking, filling station, sealing and lidding, checkweigher, metal detection, refrigeration, and CIP. These represent the core food manufacturing processes that have no equivalent on a packaging line.

## 2b.2 Equipment: Mixing and Blending

The mixer prepares sauce, filling, or component ingredients in batches. It produces 8 signals. It represents a high-shear mixer from vendors like Silverson or GEA, controlled by an Allen-Bradley CompactLogix PLC serving data over Modbus TCP (via gateway, CDAB byte order).

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 1 | `mixer.speed` | Agitator shaft speed | 0-3000 | RPM | 1s | Modbus HR |
| 2 | `mixer.torque` | Agitator torque load | 0-100 | % | 1s | Modbus HR |
| 3 | `mixer.batch_temp` | Batch temperature | -5 to 95 | C | 5s | Modbus HR |
| 4 | `mixer.batch_weight` | Vessel weight (load cells) | 0-2000 | kg | 5s | Modbus HR |
| 5 | `mixer.state` | Equipment operating state | 0-5 | enum | event | OPC-UA |
| 6 | `mixer.batch_id` | Current batch identifier | string | — | event | OPC-UA |
| 7 | `mixer.mix_time_elapsed` | Time since mix start | 0-3600 | s | 5s | Modbus HR |
| 8 | `mixer.lid_closed` | Safety interlock: lid state | 0-1 | bool | event | Modbus coil |

**Mixer state enum:**

| Value | State | Description |
|-------|-------|-------------|
| 0 | Off | Mixer powered down |
| 1 | Loading | Ingredients being added, agitator off or low speed |
| 2 | Mixing | Active mixing at target speed |
| 3 | Holding | Mix complete, holding at temperature |
| 4 | Discharging | Emptying vessel to next process |
| 5 | CIP | Clean-in-place cycle active |

**How the data behaves:**

Mixing is a batch process with distinct phases. During Loading (1), `batch_weight` increases in steps as ingredients are added. `mixer.speed` is 0 or low (50-100 RPM for gentle incorporation). During Mixing (2), speed ramps to the target (typically 1000-2500 RPM for high-shear), torque rises as the mixture thickens, and temperature may rise from friction or from a jacketed vessel heating. During Holding (3), speed drops to a low maintenance speed (100-200 RPM) and temperature holds at setpoint. During Discharging (4), weight decreases as the vessel empties.

Batch cycles are 15-45 minutes depending on the recipe. A typical shift runs 8-12 batches. Torque is the key predictive maintenance signal: gradually increasing torque at constant speed over weeks indicates bearing or seal wear.

The Tennessee Eastman Process dataset provides calibration data for batch process dynamics: correlated temperature/pressure/composition changes during multi-phase batch operations. The Condition Monitoring of Hydraulic Systems dataset provides motor torque/speed correlation patterns.

## 2b.3 Equipment: Oven / Cooking Line

The oven is the primary thermal processing equipment. It produces 10 signals. It represents a multi-zone tunnel oven with Eurotherm temperature controllers, typical of vendors like Baker Perkins, Spooner, or Rademaker. Each zone has an independent Eurotherm controller addressed as a separate Modbus slave.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 9 | `oven.zone_1_temp` | Zone 1 actual temperature | 80-280 | C | 5s | Modbus HR |
| 10 | `oven.zone_2_temp` | Zone 2 actual temperature | 80-280 | C | 5s | Modbus HR |
| 11 | `oven.zone_3_temp` | Zone 3 actual temperature | 80-280 | C | 5s | Modbus HR |
| 12 | `oven.zone_1_setpoint` | Zone 1 target temperature | 80-280 | C | event | Modbus HR |
| 13 | `oven.zone_2_setpoint` | Zone 2 target temperature | 80-280 | C | event | Modbus HR |
| 14 | `oven.zone_3_setpoint` | Zone 3 target temperature | 80-280 | C | event | Modbus HR |
| 15 | `oven.belt_speed` | Conveyor belt speed | 0.5-5.0 | m/min | 1s | Modbus HR |
| 16 | `oven.product_core_temp` | Product core temperature probe | -5 to 95 | C | 5s | Modbus HR |
| 17 | `oven.humidity_zone_2` | Mid-zone humidity | 30-90 | %RH | 10s | Modbus HR |
| 18 | `oven.state` | Oven operating state | 0-4 | enum | event | OPC-UA |

**Oven state enum:**

| Value | State | Description |
|-------|-------|-------------|
| 0 | Off | Oven powered down, cooling |
| 1 | Preheat | Heating to setpoints, no product |
| 2 | Running | Normal production, product on belt |
| 3 | Idle | At temperature but no product flowing |
| 4 | Cooldown | Controlled shutdown |

**How the data behaves:**

Oven temperature control follows PID dynamics with first-order lag. Zone temperatures track their setpoints with 2-5 minute response time and slight overshoot (1-3C) on step changes. The three zones typically run at different temperatures: zone 1 is the preheat zone (lower), zone 2 is the main cooking zone (highest), zone 3 is the finishing/holding zone (moderate). A typical ready meal oven profile: zone 1 at 160C, zone 2 at 200C, zone 3 at 180C.

Belt speed determines dwell time (time product spends in the oven). Slower belt = longer cook. The product core temperature probe measures actual food temperature at the oven exit. BRC requires that product core temperature reaches a validated minimum (typically 72C for 2 minutes or equivalent) to ensure food safety. This is the single most critical data point on a food production line.

Product changeovers require different oven profiles. A recipe change from chicken tikka (200C, 18 min) to a pasta bake (180C, 22 min) means setpoint changes and belt speed adjustment, with a 15-30 minute transition while zone temperatures stabilise.

The DAMADICS Actuator Benchmark dataset provides calibration for Eurotherm-style PID temperature control dynamics: actual vs setpoint tracking, output power, overshoot, and steady-state error at a real food-adjacent factory (sugar processing, same controller type).

## 2b.4 Equipment: Filling Station

The filler deposits measured portions of product into trays or containers. It produces 8 signals. It represents a multi-head volumetric or gravimetric filler from vendors like Ishida, Multipond, or Harpak-ULMA, controlled by a Siemens S7-1200 PLC.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 19 | `filler.line_speed` | Packs per minute | 10-120 | packs/min | 1s | OPC-UA |
| 20 | `filler.fill_weight` | Last measured fill weight | 200-800 | g | per item | OPC-UA |
| 21 | `filler.fill_target` | Target fill weight | 200-800 | g | event | OPC-UA |
| 22 | `filler.fill_deviation` | Deviation from target | -20 to +20 | g | per item | OPC-UA |
| 23 | `filler.packs_produced` | Total packs since reset | 0-999,999 | count | counter | OPC-UA |
| 24 | `filler.reject_count` | Rejected packs | 0-9999 | count | counter | OPC-UA |
| 25 | `filler.state` | Equipment operating state | 0-4 | enum | event | OPC-UA |
| 26 | `filler.hopper_level` | Product hopper level | 0-100 | % | 10s | Modbus HR |

**Filler state enum:**

| Value | State | Description |
|-------|-------|-------------|
| 0 | Off | Filler powered down |
| 1 | Setup | Recipe loaded, calibrating |
| 2 | Running | Normal production |
| 3 | Starved | Waiting for product from upstream |
| 4 | Fault | Active fault condition |

**How the data behaves:**

Fill weight follows a normal distribution centred slightly above the target weight. Food manufacturers intentionally overfill to avoid underweight packs (which are illegal). The mean overfill (giveaway) is typically 1-3% of target weight. Giveaway drift is the single largest controllable cost in food manufacturing. A 1% reduction in giveaway on a line producing 200,000 packs per day at £2 per pack saves £4,000 per day.

`fill_deviation` shows the distribution of fill accuracy. A well-tuned filler has a standard deviation of 2-4g. As components wear (seals, valves, pistons), standard deviation increases. A widening fill deviation distribution is a predictive maintenance signal for the filler mechanism.

The hopper level follows a sawtooth pattern: it depletes as packs are filled and refills in batches from the upstream cooking process. When hopper level hits 0, the filler enters Starved (3) state and the line stops. Hopper starvation events correlate with upstream oven or mixer issues.

No direct public dataset exists for filling line data. Signal generators use first-principles models calibrated against vendor specifications for Ishida and Multipond multi-head weighers.

## 2b.5 Equipment: Sealing and Lidding

The sealer applies lids to filled trays using heat sealing or modified atmosphere packaging (MAP). It produces 6 signals. It represents a tray sealer from vendors like Multivac, Proseal, or ULMA.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 27 | `sealer.seal_temp` | Seal bar temperature | 100-250 | C | 5s | Modbus HR |
| 28 | `sealer.seal_pressure` | Seal bar pressure | 1-6 | bar | 5s | Modbus HR |
| 29 | `sealer.seal_dwell` | Seal dwell time | 0.5-5.0 | s | 5s | Modbus HR |
| 30 | `sealer.gas_co2_pct` | MAP gas mix CO2 | 20-80 | % | 10s | Modbus HR |
| 31 | `sealer.gas_n2_pct` | MAP gas mix N2 | 20-80 | % | 10s | Modbus HR |
| 32 | `sealer.vacuum_level` | Thermoform vacuum | -0.9 to 0 | bar | 5s | Modbus HR |

**How the data behaves:**

Seal temperature, pressure, and dwell time form a critical triplet. All three must be within specification for a reliable seal. Seal bars degrade over time: the heating element develops hot spots (temperature uniformity degrades) and the silicone rubber face compresses (pressure distribution changes). These degradation patterns are visible as gradually increasing variability in seal temperature across cycles.

MAP gas ratios are critical for shelf life. A ready meal typically uses 30% CO2 / 70% N2. Gas mix drift indicates a supply issue (cylinder running low, regulator fault) or a leak in the gas delivery system. Sudden loss of gas mix correlation (CO2 and N2 not summing to expected levels) indicates a packaging integrity problem.

Seal quality failures are the most common cause of product recalls in chilled food. A failing seal bar produces intermittent weak seals that pass in-line testing but fail during distribution. Trending seal parameters against reject rates is a high-value predictive maintenance use case.

## 2b.6 Equipment: Checkweigher and Metal Detection

The checkweigher verifies every pack is within weight tolerance. The metal detector scans for contaminants. Combined, they produce 6 signals. They represent equipment from Ishida, Mettler Toledo, or Loma.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 33 | `qc.actual_weight` | Measured pack weight | 100-1000 | g | per item | OPC-UA |
| 34 | `qc.overweight_count` | Packs above upper limit | 0-9999 | count | counter | OPC-UA |
| 35 | `qc.underweight_count` | Packs below lower limit | 0-9999 | count | counter | OPC-UA |
| 36 | `qc.metal_detect_trips` | Metal detection rejects | 0-99 | count | counter | OPC-UA |
| 37 | `qc.throughput` | Items checked per minute | 10-120 | items/min | 1s | OPC-UA |
| 38 | `qc.reject_total` | Total QC rejects (all causes) | 0-9999 | count | counter | OPC-UA |

**How the data behaves:**

Pack weight follows a normal distribution that mirrors the fill weight distribution with a small offset (tray weight + lid weight added). The checkweigher enforces legal metrology requirements: the average weight must meet the nominal weight, and no individual pack may be more than twice the tolerable negative error below nominal (Weights and Measures Act 1985, the "e" mark rules).

Overweight packs are giveaway. Underweight packs are illegal. The ratio between them indicates filler calibration quality. A well-calibrated line has an overweight:underweight ratio of roughly 20:1 (biased high for safety). If the ratio drops toward 5:1, the filler needs recalibration.

Metal detector trips should be rare (less than 1 per 1000 packs). A sudden increase indicates contamination in the ingredient supply, equipment wear introducing metal particles, or a false-positive issue with the detector sensitivity.

## 2b.7 Equipment: Refrigeration

The refrigeration system maintains the chill chain after cooking. It produces 7 signals. It represents a cold room with an industrial refrigeration plant from Star Refrigeration or Johnson Controls, controlled by a Danfoss or Allen-Bradley controller.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 39 | `chiller.room_temp` | Cold room temperature | -5 to 15 | C | 30s | Modbus HR |
| 40 | `chiller.setpoint` | Target temperature | -5 to 15 | C | event | Modbus HR |
| 41 | `chiller.compressor_state` | Compressor on/off | 0-1 | bool | event | Modbus coil |
| 42 | `chiller.suction_pressure` | Compressor suction pressure | 0-10 | bar | 30s | Modbus HR |
| 43 | `chiller.discharge_pressure` | Compressor discharge pressure | 5-25 | bar | 30s | Modbus HR |
| 44 | `chiller.defrost_active` | Defrost cycle state | 0-1 | bool | event | Modbus coil |
| 45 | `chiller.door_open` | Cold room door state | 0-1 | bool | event | Modbus DI |

**How the data behaves:**

Room temperature oscillates around the setpoint in a sawtooth pattern as the compressor cycles on and off. Normal dead band is 1-2C. The compressor runs for 10-20 minutes, then rests for 5-15 minutes. This cycling pattern is the baseline. Increasing compressor run time relative to rest time indicates degrading cooling capacity (refrigerant loss, condenser fouling, evaporator icing).

Door open events cause temperature spikes. Frequent door openings during production (forklift traffic, operator access) create a characteristic temperature waveform with repeated 2-5C excursions. BRC requires that cold room temperatures remain below 5C for chilled product. Any excursion above 5C must be documented with duration, cause, and corrective action.

Defrost cycles occur 2-4 times per day. During defrost, the evaporator heaters activate for 15-30 minutes and room temperature may rise 3-5C. A well-timed defrost during low-traffic periods minimises impact. Defrost failures (evaporator icing over) cause gradual loss of cooling capacity visible as rising room temperature baseline and longer compressor run times.

Suction and discharge pressure together indicate compressor health. Rising discharge pressure at constant suction pressure suggests condenser fouling. Falling suction pressure suggests evaporator restriction or low refrigerant. The pressure ratio (discharge/suction) trending upward over weeks is a predictive maintenance indicator.

The Appliances Energy dataset provides calibration for temperature cycling patterns in cooled environments. The MetroPT dataset provides compressor motor behaviour patterns (pressure, temperature, current from a real compressor system with ground-truth failures).

## 2b.8 Equipment: CIP (Clean-in-Place)

The CIP system cleans production equipment between batches or at end of production. It produces 5 signals. It represents an automated CIP skid from Ecolab or GEA, controlled by a Siemens S7-1200 PLC.

**Signals:**

| # | Signal ID | Description | Range | Units | Rate | Protocol |
|---|-----------|-------------|-------|-------|------|----------|
| 46 | `cip.state` | CIP cycle phase | 0-5 | enum | event | OPC-UA |
| 47 | `cip.wash_temp` | Wash solution temperature | 15-85 | C | 5s | Modbus HR |
| 48 | `cip.flow_rate` | Wash flow rate | 0-100 | L/min | 5s | Modbus HR |
| 49 | `cip.conductivity` | Chemical concentration proxy | 0-200 | mS/cm | 10s | Modbus HR |
| 50 | `cip.cycle_time_elapsed` | Time since cycle start | 0-7200 | s | 5s | Modbus HR |

**CIP state enum:**

| Value | State | Description |
|-------|-------|-------------|
| 0 | Idle | No CIP in progress |
| 1 | Pre-rinse | Initial water rinse to remove bulk residue |
| 2 | Caustic wash | Hot caustic solution circulating |
| 3 | Intermediate rinse | Water rinse to remove caustic |
| 4 | Acid wash | Acid solution for mineral deposits (optional) |
| 5 | Final rinse | Final water rinse, conductivity check |

**How the data behaves:**

A CIP cycle is a fixed sequence of phases, each with defined time, temperature, and concentration parameters. The typical sequence: pre-rinse (5 min, ambient water), caustic wash (15-20 min, 70-80C, high conductivity), intermediate rinse (5 min, ambient water, conductivity dropping), optional acid wash (10-15 min, 60-70C), final rinse (5-10 min, ambient water, conductivity must return to baseline).

Total CIP cycle time is 40-60 minutes. A chilled food factory typically runs 2-3 CIP cycles per day: one between product changeovers and one at end of production. CIP time is planned downtime that directly reduces available production time. Reducing CIP cycle duration without compromising hygiene is a key OEE improvement.

Conductivity is the critical measurement. During caustic wash, conductivity rises to 80-150 mS/cm (indicating correct chemical concentration). During rinse phases, conductivity must fall below 5 mS/cm to confirm all chemical residue is removed. A final rinse that fails to reach baseline conductivity indicates an incomplete clean. This triggers a re-wash, extending downtime.

Wash temperature below specification during the caustic phase indicates a heater fault or insufficient steam supply. Temperature and conductivity together validate that the CIP cycle achieved the required conditions for food safety.

No public CIP datasets exist. Signal generators use first-principles models based on standard CIP cycle definitions from the European Hygienic Engineering & Design Group (EHEDG) guidelines.

## 2b.9 Equipment: Coding and Marking

The coder on the F&B line is identical to the packaging line coder. It is a continuous inkjet printer (industrial AX-series CIJ) printing date codes, batch numbers, and use-by dates onto sealed packs.

**Signals:** Same 11 signals as [Section 2.5](02-simulated-factory-layout.md#25-equipment-coding-and-marking) of the packaging layout (signals 51-61 in this profile, coder.state through coder.gutter_fault).

The only behavioural difference: the F&B coder prints use-by dates and allergen information rather than batch/lot codes. The data patterns are identical. The coder is coupled to the filling/sealing line rather than the flexo press, so its state machine tracks `filler.state` rather than `press.machine_state`.

## 2b.10 Equipment: Environmental Sensors

Environmental sensors on the F&B line are identical to the packaging line. They monitor factory floor conditions.

**Signals:** Same 2 signals as [Section 2.7](02-simulated-factory-layout.md#27-equipment-environmental-sensors) of the packaging layout (signals 62-63 in this profile, env.ambient_temp, env.ambient_humidity).

In a food factory, ambient temperature and humidity have stronger compliance significance. BRC requires documented environmental monitoring in production areas. The acceptable range is tighter: 12-18C for chilled food production areas (vs 15-35C for a packaging factory).

## 2b.11 Equipment: Energy Monitoring

Energy monitoring is identical to the packaging line.

**Signals:** Same 2 signals as [Section 2.8](02-simulated-factory-layout.md#28-equipment-energy-monitoring) of the packaging layout (signals 64-65 in this profile, energy.line_power, energy.cumulative_kwh).

In a food factory, the energy profile is different. Refrigeration is typically 40-60% of total energy consumption (vs near-zero for packaging). The oven is the second largest consumer. Energy per pack is the key metric for ESG reporting.

## 2b.12 Key Differences from Packaging Line

| Aspect | Packaging Line | F&B Line |
|---|---|---|
| **Primary machine** | Flexographic press | Oven / cooking line |
| **Process type** | Continuous web | Batch + continuous flow |
| **Critical measurement** | Registration error (print quality) | Product core temperature (food safety) |
| **Biggest cost driver** | Waste/scrap (material) | Giveaway (overfilling) |
| **Compliance** | BRC Packaging | BRC Food Safety, SALSA |
| **Downtime pattern** | Job changeover (10-30 min) | CIP cycles (40-60 min) |
| **Energy profile** | Motors dominate | Refrigeration + thermal dominate |
| **Predictive maintenance** | Bearing wear (vibration) | Seal bar degradation, compressor health |
| **Data shape** | Mostly continuous (web runs) | Mixed batch (mixer) + continuous (oven, filler) |
| **Unique equipment** | Laminator, slitter | Mixer, filler, sealer, checkweigher, metal detector, refrigeration, CIP |
| **Shared equipment** | Coder, environmental, energy | Coder, environmental, energy |

## 2b.13 Publicly Available Datasets for Equipment Calibration

| Equipment Group | Relevant Public Datasets | What They Provide |
|---|---|---|
| Oven / Cooking | DAMADICS Actuator Benchmark (32 signals, 1 Hz, 25 days, sugar factory) | Eurotherm-style PID temperature control dynamics. Actual vs setpoint tracking, output power, overshoot patterns. Direct match for oven zone controllers. |
| Mixing / Blending | Tennessee Eastman Process (52 variables, 22 fault types) | Batch process dynamics: correlated temperature, pressure, composition changes during multi-phase operations. |
| Mixing / Blending | Condition Monitoring of Hydraulic Systems (17 sensors, multi-rate) | Motor torque/speed correlation patterns, motor power consumption at varying loads. |
| Refrigeration | Appliances Energy Prediction (29 variables, 10-min, 4.5 months) | Temperature cycling in cooled environments, daily patterns, response to external events. |
| Refrigeration | MetroPT (15 signals, 1s, 6 months, real compressor) | Compressor pressure, temperature, motor current with ground-truth failures. Best available proxy for industrial refrigeration compressor monitoring. |
| Filling / Sealing | No direct public datasets | First-principles models from vendor specifications (Ishida, Multivac). Fill weight distributions from statistical process control literature. |
| Checkweigher | No direct public datasets | First-principles models based on UK Weights and Measures regulations and vendor accuracy specifications. |
| CIP | No direct public datasets | First-principles models based on EHEDG guidelines for CIP cycle parameters. |
| Coding & Marking | Private reference data | Same as packaging line. See [Section 2.10](02-simulated-factory-layout.md#210-publicly-available-datasets-for-equipment-calibration). |

## 2b.14 Signal Summary

| Protocol | Signal Count | Signals |
|----------|-------------|---------|
| Modbus HR | 31 | mixer.speed, mixer.torque, mixer.batch_temp, mixer.batch_weight, mixer.mix_time_elapsed, oven.zone_1/2/3_temp, oven.zone_1/2/3_setpoint, oven.belt_speed, oven.product_core_temp, oven.humidity_zone_2, filler.hopper_level, sealer.seal_temp, sealer.seal_pressure, sealer.seal_dwell, sealer.gas_co2_pct, sealer.gas_n2_pct, sealer.vacuum_level, chiller.room_temp, chiller.setpoint, chiller.suction_pressure, chiller.discharge_pressure, cip.wash_temp, cip.flow_rate, cip.conductivity, cip.cycle_time_elapsed, energy.line_power, energy.cumulative_kwh |
| OPC-UA | 17 | mixer.state, mixer.batch_id, oven.state, filler.line_speed, filler.fill_weight, filler.fill_target, filler.fill_deviation, filler.packs_produced, filler.reject_count, filler.state, qc.actual_weight, qc.overweight_count, qc.underweight_count, qc.metal_detect_trips, qc.throughput, qc.reject_total, cip.state |
| MQTT | 13 | coder.state, coder.prints_total, coder.ink_level, coder.printhead_temp, coder.ink_pump_speed, coder.ink_pressure, coder.ink_viscosity_actual, coder.supply_voltage, coder.ink_consumption_ml, coder.nozzle_health, coder.gutter_fault, env.ambient_temp, env.ambient_humidity |
| Modbus coils/DI | 4 | mixer.lid_closed, chiller.compressor_state, chiller.defrost_active, chiller.door_open |
| **Subtotals** | **Unique to F&B: 50** | **Shared with packaging: 15** (coder ×11, env ×2, energy ×2) |

Total: 65 signals across 9 equipment groups (3 shared with packaging line, 6 unique to F&B).

Average aggregate sample rate: approximately 4 samples per second across all signals. Data volume: approximately 14,400 data points per hour, 345,600 per day, 10.4 million per month.

## 2b.15 F&B Scenario Sketches

These scenarios are the F&B equivalents of the packaging line scenarios in [Section 5](05-scenario-system.md). Full scenario definitions will be added when the F&B profile is implemented.

| Scenario | Frequency | Key Signals | Pattern |
|---|---|---|---|
| **Recipe changeover** | 3-5 per shift | mixer, oven setpoints, filler target, sealer gas mix | 20-40 min: mixer empties, oven retemps, filler recalibrates. CIP may be required between allergen-containing recipes. |
| **CIP cycle** | 2-3 per day | cip.*, all production signals stop | 40-60 min planned downtime. Production counters freeze. All upstream equipment enters Idle. |
| **Cold chain excursion** | 1-2 per month | chiller.room_temp, chiller.door_open | Door held open during loading. Temperature rises 3-8C over 10-20 min. Compressor runs continuously to recover. |
| **Giveaway drift** | Continuous slow trend | filler.fill_weight, filler.fill_deviation | Mean fill weight drifts +2-5g above target over 4-8 hours. Standard deviation gradually widens. |
| **Seal bar degradation** | Gradual over 2-4 weeks | sealer.seal_temp variability | Increasing temperature standard deviation. Occasional weak seal rejects appear. |
| **Compressor degradation** | Gradual over weeks | chiller.suction_pressure, chiller.discharge_pressure, compressor run time | Pressure ratio increases. Run time extends. Room temperature baseline creeps up. |
| **Metal detector false positives** | 1-2 per week | qc.metal_detect_trips | Burst of 3-5 trips in quick succession. No actual contamination. Caused by electrical interference or sensitivity drift. |
| **Oven temperature fault** | 1-2 per month | oven.zone_N_temp diverges from setpoint | One zone fails to track setpoint. Element degradation or thermocouple drift. Product core temp may not reach safety threshold. |
| **Mixer overload** | Rare | mixer.torque spikes, mixer.state → Fault | Torque exceeds 95% during thick batch. Motor protection trips. |

## 2b.16 Key Analytics Use Cases

| Use Case | Signals Involved | What Collatr Does | £ Value |
|---|---|---|---|
| **Giveaway reduction** | filler.fill_weight, filler.fill_deviation, qc.actual_weight | SPC on fill weight distribution. Alert when mean overfill exceeds threshold. Quantify £ of giveaway per shift. | £500-5,000/day for a large line |
| **OEE per line** | filler.state, filler.packs_produced, filler.reject_count, cip downtime | Real-time OEE. Separate CIP downtime (planned) from faults (unplanned) in availability calculation. | OEE improvement of 2-5% = £100k-500k/year |
| **Cold chain compliance** | chiller.room_temp, chiller.door_open, chiller.defrost_active | Continuous BRC-compliant temperature logging. Automated excursion reports with context (door events, defrost cycles). | Audit pass/fail, recall prevention |
| **Seal integrity prediction** | sealer.seal_temp, sealer.seal_pressure, sealer.seal_dwell, reject rates | Trend seal parameters. Predict seal bar replacement before weak seals reach customers. | Recall prevention (£50k-500k per event) |
| **Energy per pack** | energy.line_power, energy.cumulative_kwh, filler.packs_produced, chiller energy | Track kWh per pack. Benchmark shifts. Identify energy waste during CIP and changeover. | ESG reporting + 5-15% energy reduction |
| **CIP optimisation** | cip.*, production downtime duration | Validate CIP cycles meet hygiene specs. Identify cycles that could be shortened. Reduce planned downtime. | 10-20 min/day recovered production time |
| **Bake quality correlation** | oven.zone_*_temp, oven.belt_speed, oven.product_core_temp, downstream rejects | Correlate oven parameters with quality. Identify optimal parameter windows per recipe. | Waste reduction + food safety |

---

> **Implementation note:** The F&B profile reuses the same data generation engine, protocol endpoints, scenario framework, and configuration system as the packaging profile. The factory profile is selected via the `factory` configuration key. Both profiles can run simultaneously on different ports for comparison testing.
>
> **Cross-reference:** Packaging line layout is defined in [02-simulated-factory-layout.md](02-simulated-factory-layout.md). Scenario system is defined in [05-scenario-system.md](05-scenario-system.md). Configuration for profile selection is defined in [06-configuration.md](06-configuration.md).
