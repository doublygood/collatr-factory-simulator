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

Sequence:
1. `vibration.main_drive_x/y/z` baseline increases by 0.01-0.05 mm/s per hour.
2. After 1-2 weeks, vibration reaches 15-20 mm/s (warning threshold).
3. After 3-5 weeks, vibration reaches 25-40 mm/s (alarm threshold).
4. `press.main_drive_current` increases by 1-5% at constant speed (bearing friction).
5. If the scenario is configured to culminate in failure: `press.machine_state` transitions to Fault (4) with vibration spike to 40-50 mm/s.

The IMS/NASA bearing run-to-failure dataset (from the datasets research) showed exactly this pattern over 35 days. The Paderborn bearing dataset added motor current increase as a correlated signal. Our simulator reproduces both.

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

This scenario does not directly produce one of the 40 signals but influences `press.waste_count` and informs the coder behaviour.

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
```
