# Scenario System

## 5.1 Overview

Scenarios are time-bounded events that override normal signal generation. They inject anomalies, operational events, and degradation patterns into the data stream. Scenarios can be scheduled (recurring on a pattern) or triggered (fired by a condition or manual command).

## 5.2 Job Changeover

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

## 5.3 Web Break

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

## 5.4 Dryer Temperature Drift

**Frequency:** 1-2 per shift (configurable).
**Duration:** 30-120 minutes.

Sequence:
1. One dryer zone's actual temperature begins drifting above its setpoint.
2. Drift rate: 0.05-0.2 C per minute.
3. Over 30-120 minutes, the zone drifts 5-15C above setpoint.
4. `press.waste_count` increment rate increases by 20-50% during drift (quality impact).
5. After drift duration, temperature returns to setpoint (simulates operator correction or auto-correction).

The drift is subtle. It does not trigger a fault state. It causes increased waste. This is the type of anomaly that data analytics should detect. The DAMADICS actuator benchmark (from the datasets research) showed similar gradual control loop degradation patterns.

## 5.5 Motor Bearing Wear

**Frequency:** One event over 2-6 weeks (configurable).
**Duration:** Gradual degradation.

Real bearing degradation follows an exponential curve, not a linear one. The IMS/NASA bearing run-to-failure dataset shows slow, near-linear increase for most of the bearing life, then rapid acceleration in the final days before failure. This produces the characteristic hockey-stick shape.

The vibration increase uses an exponential model:

```
vibration_increase = base_rate * exp(k * elapsed_hours)
```

`base_rate` is the initial hourly increase (0.001-0.005 mm/s per hour). `k` is the acceleration constant (0.005-0.01). Together they produce the hockey-stick curve.

In practical terms:
- Week 1-2: vibration increases by 0.5-1.0 mm/s total. Barely perceptible.
- Week 3: vibration increases by 2-3 mm/s. Noticeable trend on a chart.
- Week 4: vibration increases by 5-10 mm/s. Warning threshold reached.
- Final days: rapid acceleration to alarm threshold and failure.

Sequence:
1. `vibration.main_drive_x/y/z` baseline increases following the exponential model above.
2. After 1-2 weeks, vibration reaches 15-20 mm/s (warning threshold).
3. After 3-5 weeks, vibration reaches 25-40 mm/s (alarm threshold).
4. `press.main_drive_current` increases by 1-5% at constant speed (bearing friction). The current increase follows the same exponential curve at smaller magnitude. Most of the current rise occurs in the final days alongside the vibration spike.
5. If the scenario is configured to culminate in failure: `press.machine_state` transitions to Fault (4) with vibration spike to 40-50 mm/s.

The IMS/NASA bearing run-to-failure dataset showed this pattern over 35 days. The Paderborn bearing dataset added motor current increase as a correlated signal. Our simulator reproduces both with the exponential degradation model.

This scenario operates at a different timescale than other scenarios. At 1x speed, the full degradation plays out over weeks. At 100x speed, it plays out over hours. The bearing wear scenario is the primary test for CollatrEdge's ability to detect slow trends.

## 5.6 Ink Viscosity Excursion

**Frequency:** 2-3 per shift.
**Duration:** 5-30 minutes per excursion.

Sequence:
1. `press.ink_viscosity` drifts below 18 seconds (too thin) or above 45 seconds (too thick).
2. `press.registration_error_x/y` increases during the excursion.
3. `press.waste_count` increment rate increases by 10-30%.
4. After excursion duration, viscosity returns to normal range (simulates operator adding solvent or ink concentrate).

The customer profiles research identified ink viscosity excursions as a key analytics use case for packaging converters. Viscosity correlates with temperature (lower temp = higher viscosity). The simulator couples these: an ambient temperature drop triggers higher viscosity, which triggers more waste.

## 5.7 Registration Drift

**Frequency:** Random, 1-3 per shift.
**Duration:** 2-10 minutes.

Sequence:
1. `press.registration_error_x` or `press.registration_error_y` drifts beyond +/-0.3 mm.
2. Drift is gradual: 0.01-0.05 mm per second.
3. Often triggered by a speed change or temperature shift.
4. `press.waste_count` increment rate increases while error exceeds 0.2 mm.
5. Returns to center after auto-correction or operator intervention.

## 5.8 Unplanned Stop

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

## 5.9 Shift Change

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

## 5.10 Energy Spike on Cold Start

**Frequency:** 1-2 per day (each time the line starts from cold).
**Duration:** 2-5 seconds.

Sequence:
1. When `press.machine_state` transitions from Off (0) or Idle (3) to Setup (1) or Running (2) after being idle for more than 30 minutes:
   - `energy.line_power` spikes to 150-200% of normal running power for 2-5 seconds.
   - `press.main_drive_current` spikes to 150-300% of running current (motor inrush).
2. After the spike, power settles to normal running level.

The Steel Industry Energy dataset (from the datasets research) showed clear cold start spikes. The customer profiles research identified energy-per-impression monitoring as a key use case.

## 5.11 Vision Inspection Fail Rate Patterns

This scenario does not directly produce one of the packaging profile signals but influences `press.waste_count` and informs the coder behaviour.

When the press is Idle (3) or Off (0), the vision inspection system (if it were a signal) would report near-100% fail rates. The R-Series reference data showed 85.6% fail rate in a typical month because the camera reports "fail" for no-read events during idle periods.

The simulator uses this insight: `press.waste_count` only increments when `press.machine_state` is Running (2). During idle, the waste rate is exactly 0. When the press starts running, waste rate begins at 3-5% (startup waste) and decreases to 0.5-2% (steady state) over 2-3 minutes.

## 5.12 Coder State Transitions and Consumable Depletion

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

## 5.13 Scenario Scheduling

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

  micro_stop:
    frequency: "10-50 per 8h shift"
    duration_range: [5, 30]             # seconds
    speed_drop_percent: [30, 80]
    ramp_down_seconds: [2, 5]
    ramp_up_seconds: [5, 15]
    mean_interval_minutes: [10, 50]     # Poisson process
```

## 5.14 F&B Profile Scenarios

The F&B profile adds seven scenarios specific to food and beverage production. Each scenario uses the same signal model types as packaging scenarios (first_order_lag, state_machine, ramp, counter) with F&B parameters.

### 5.14.1 Batch Cycle (Mixer)

**Frequency:** 8-16 per shift (continuous batch production).
**Duration:** 20-45 minutes per batch.

Sequence:
1. Mixer state transitions to Loading. Ingredient valves open.
2. After 2-5 minutes, mixer state transitions to Mixing. Mixer speed ramps to target RPM.
3. `mixer.torque` follows `mixer.speed` with load factor. `mixer.batch_temp` ramps toward setpoint.
4. After mixing duration (10-25 minutes), state transitions to Hold. Temperature holds at setpoint for 5-10 minutes.
5. State transitions to Discharge. Mixer speed drops to low RPM. Batch counter increments.
6. State returns to Loading for the next batch.

Batch-to-batch variation: each batch has slightly different ingredient volumes, target temperatures, and mixing times. This produces natural variation in torque profiles and cycle durations.

### 5.14.2 Oven Thermal Excursion

**Frequency:** 1-2 per shift.
**Duration:** 30-90 minutes.

Sequence:
1. One oven zone drifts from its setpoint. Drift rate: 0.1-0.3 C per minute.
2. Adjacent zones respond via thermal coupling (0.05 factor on the drifting zone's deviation).
3. Product temperature at the exit deviates from target.
4. After drift duration, the zone returns to setpoint (operator correction or controller recovery).

This scenario is analogous to the packaging dryer drift but operates at oven scale (setpoints 160-220 C instead of 50-80 C).

### 5.14.3 Fill Weight Drift

**Frequency:** 1-3 per shift.
**Duration:** 10-60 minutes.

Sequence:
1. `filler.fill_weight` mean drifts from target (e.g. 350g) at 0.05-0.2 g per minute.
2. As the mean drifts, more fills fall outside the acceptable range.
3. `filler.reject_count` increment rate increases proportionally to the deviation.
4. After drift duration, the mean returns to target (operator recalibrates).

### 5.14.4 Seal Integrity Failure

**Frequency:** 1-2 per week.
**Duration:** 5-30 minutes.

Sequence:
1. `sealer.seal_temp` drops below the minimum threshold (e.g. 170 C).
2. `sealer.seal_strength` decreases proportionally.
3. `sealer.gas_leak_rate` increases as seal quality degrades.
4. `sealer.reject_count` spikes.
5. After detection, the line stops for seal bar replacement or adjustment.

### 5.14.5 Chiller Door Alarm

**Frequency:** 1-3 per week.
**Duration:** 5-20 minutes.

Sequence:
1. `chiller.door_open` discrete input sets to true.
2. `chiller.room_temp` rises at 0.5-2 C per minute (warm air ingress).
3. `chiller.compressor_power` increases as the compressor works harder.
4. After door close, room temperature recovers via first_order_lag to setpoint.

### 5.14.6 CIP Cycle

**Frequency:** 1-3 per day (between production batches).
**Duration:** 30-60 minutes.

Sequence:
1. Production stops. CIP state transitions to Pre-Rinse.
2. `cip.wash_temp` ramps to rinse temperature (40-50 C). `cip.flow_rate` ramps to target.
3. State transitions to Caustic Wash. Temperature ramps to 70-80 C. `cip.conductivity` rises as caustic solution circulates.
4. State transitions to Rinse. Temperature drops. Conductivity drops toward zero.
5. State transitions to Acid Wash. Conductivity changes reflect acid concentration.
6. Final Rinse. Conductivity drops below acceptance threshold.
7. CIP state transitions to Complete. Production resumes.

Each phase has a defined duration and temperature/flow profile. The recipe curve is deterministic with minor noise.

### 5.14.7 Cold Chain Break

**Frequency:** Rare, 1-2 per month.
**Duration:** 30-120 minutes.

Sequence:
1. `chiller.compressor_power` drops to 0 (refrigeration failure).
2. `chiller.room_temp` rises from setpoint (2-4 C) toward ambient at 0.5-1.5 C per minute.
3. `chiller.room_temp` crosses the alarm threshold (8 C). Alarm activates.
4. Product in the chiller is at risk. The duration above threshold determines spoilage.
5. After repair, compressor restarts. Room temperature recovers via first_order_lag.

## 5.15 Micro-Stops

Micro-stops are brief interruptions lasting 5-30 seconds. They occur 10-50 times per 8-hour shift (configurable). They do not change the machine state register. The `press.machine_state` stays Running (2). Only the line speed dips.

**Frequency:** 10-50 per 8-hour shift (configurable). Inter-arrival time follows an exponential distribution with a configurable mean of 10-50 minutes (Poisson process).
**Duration:** 5-30 seconds per event.

**Causes:** operator inspecting a print, splice passing through, sensor false-trigger, brief material jam that clears itself.

Sequence:
1. `press.line_speed` drops by 30-80% over 2-5 seconds.
2. `press.web_tension` fluctuates during deceleration.
3. `press.waste_count` increment rate increases briefly (prints during deceleration are waste).
4. After 5-30 seconds, `press.line_speed` ramps back to the previous target over 5-15 seconds.
5. `press.registration_error_x/y` may increase briefly during recovery.
6. All other signals respond through existing correlations: motor current follows speed, energy follows speed, vibration tracks speed.

**Key distinction from unplanned stops.** Micro-stops do not trigger a state change. No fault code is written. No coil is set. The press is still "Running" from the PLC's perspective. This is the behaviour that OEE systems struggle to capture: production is nominally running but throughput drops. Detecting and quantifying micro-stops is a high-value analytics use case.

**Scheduling.** Micro-stops are independent of other scenarios. They can occur during any Running period. They do not interact with job changeovers, web breaks, or other scenario events. If a micro-stop coincides with a scenario start, the scenario takes priority.

## 5.16 Contextual Anomalies

Contextual anomalies test whether CollatrEdge can distinguish between values that are normal in one state and anomalous in another. A signal value may fall within the normal operating range for one machine state but indicate a fault when it appears during a different state. This is the hardest class of anomaly for detection algorithms because threshold-based methods cannot catch it.

The scenario engine injects contextual anomalies by holding a signal at its normal operating value during a state where that value should not appear.

**Types of contextual anomaly:**

1. **Heater stuck on.** `coder.printhead_temp` stays at printing temperature (40-42C) during Off or Standby state. Normal during Printing. Anomalous when the coder should be cooling down. Indicates a stuck relay or failed controller.

2. **Pressure bleed.** `coder.ink_pressure` stays at operating pressure (800-850 mbar) during Off state. Normal during Printing and Ready. Anomalous during Off. Indicates a valve not closing properly.

3. **Counter incrementing during idle.** `press.impression_count` increments while `press.machine_state` is Idle (3). Normal during Running. Anomalous during Idle. Indicates a sensor counting false triggers or a wiring fault.

4. **Temperature during maintenance.** `press.dryer_temp_zone_1` at 100C while `press.machine_state` is Maintenance (5). Normal during Running and Setup. Anomalous during Maintenance because dryers should be off for safe access.

5. **Vibration during off.** `vibration.main_drive_x` at 3-5 mm/s while `press.machine_state` is Off (0). Normal during Running. Anomalous during Off. Indicates external vibration source or sensor fault.

**Scheduling.** The engine selects 2-5 contextual anomaly events per simulated week (configurable). Each event type has a probability weight. The engine picks a type, waits for the required machine state, then injects the anomalous signal value for the configured duration. If the machine state changes before the duration expires, the anomaly ends early.

**Ground truth.** The ground truth event log (Section 4.7) records each contextual anomaly with: event type, affected signal, injected value, expected state (where the value would be normal), and actual state (where it is anomalous). This enables evaluation of context-aware anomaly detection.

## 5.17 Intermittent Faults

Intermittent faults appear, disappear, and reappear over days or weeks before becoming permanent. They are the hardest faults to diagnose in real factories because they do not persist long enough for simple threshold alarms to catch reliably.

The intermittent fault model has three phases.

**Phase 1: Sporadic.** The fault appears briefly (seconds to minutes), then disappears. Occurrences are rare at first (1-2 per day) and increase in frequency over time. The signal returns to normal between occurrences. This phase lasts days to weeks.

**Phase 2: Frequent.** The fault appears more often (5-20 per day) and lasts longer (minutes to hours). The signal still returns to normal between occurrences, but the "normal" baseline may begin to shift. This phase lasts days.

**Phase 3: Permanent.** The fault becomes continuous. The signal no longer returns to normal. This may trigger a state machine transition to Fault.

**Applicable signals and fault patterns:**

1. **Bearing vibration intermittent.** `vibration.main_drive_x/y/z` spikes to 15-25 mm/s for 10-60 seconds, then returns to normal baseline. Over 2-4 weeks (simulated), frequency increases from 1-2 per day to 10-20 per day. Finally becomes continuous elevation. This precedes the existing bearing wear degradation scenario (Section 5.5). The intermittent phase comes first. Configure `bearing_wear.start_after_hours` to begin after the intermittent fault reaches phase 3.

2. **Electrical intermittent.** `press.main_drive_current` spikes by 20-50% for 1-10 seconds. Returns to normal. Frequency increases over 1-2 weeks. Caused by loose connection, degrading contactor, or inverter fault. May culminate in motor overload fault (code 101).

3. **Sensor intermittent.** Any analog signal briefly reports the sentinel value (Section 10.9) for 1-5 seconds, then resumes normal reading. Frequency increases over days. Caused by intermittent wire break, corroded terminal, or failing sensor. Eventually becomes a permanent disconnect.

4. **Pneumatic intermittent.** `coder.ink_pressure` drops to 0 for 2-30 seconds, then recovers. Caused by sticking solenoid valve or air leak that opens under vibration. Frequency increases. May culminate in coder Fault state.

**Timescale.** Intermittent faults operate on long timescales. At 1x speed, the full sporadic-to-permanent progression takes weeks. At 100x, it takes hours. The bearing intermittent scenario should precede and connect to the existing bearing wear scenario (Section 5.5).

**Ground truth.** The ground truth event log (Section 4.7) records each intermittent fault occurrence with: phase (1, 2, or 3), affected signal, spike magnitude, duration, and whether it transitions to permanent.
