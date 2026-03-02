# Data Generation Engine

## 4.1 Design Principles

The data generation engine produces parametric synthetic data. It does not replay recorded timeseries. Every run generates unique data from configurable models. The engine runs at a configurable time scale (1x, 10x, 100x real-time).

Key principles:

1. **Correlations over individual signals.** Signals do not vary independently. When line speed changes, motor current changes, web tension fluctuates, dryer temperatures respond, and waste rate shifts. The engine models these dependencies explicitly.

2. **State drives everything.** The machine state (Off, Setup, Running, Idle, Fault, Maintenance) determines the behaviour of all signals. A signal generator does not produce values in isolation. It asks "what state is the machine in?" and generates accordingly.

3. **Noise is not optional.** Every analog signal includes noise at a configurable magnitude and distribution. Real sensors are noisy. Clean signals look fake. The noise magnitude is calibrated from the reference data. Print head temperature had 2.8C standard deviation. Lung pressure had 60 mbar standard deviation. We tune noise per signal. The noise distribution is also configurable per signal. Gaussian is the default. Signals with heavy-tailed behaviour (vibration, pressure) use Student-t. Signals with autocorrelated residuals (PID-controlled temperatures) use AR(1). See Section 4.2.11 for supported distributions.

4. **Time is the independent variable.** The engine maintains a simulation clock. At each tick, it advances the clock, evaluates active scenarios, updates the machine state, and generates new values for all signals. The tick rate matches the fastest signal (500ms for web tension and registration error). Slower signals update only on their configured interval.

5. **All signal models use simulated time.** The time variable `t` and time delta `dt` in every generator formula refer to the simulation clock, not wall-clock time. This invariant ensures that compressed runs (10x, 100x) produce statistically identical output to real-time runs, just faster. Implementers must never substitute wall-clock time for simulated time in any signal model. This matters most for the random walk model (Section 4.2.5), where using wall-clock `dt` at 100x compression would inflate drift rates by a factor of 10 due to `sqrt(dt)` scaling.

## 4.2 Signal Models

Each signal uses one of the following generator models:

### 4.2.1 Steady State with Noise

The simplest model. The signal stays near a target value with Gaussian noise.

```
value = target + noise(0, sigma)
```

Used for: `press.nip_pressure`, `laminator.nip_pressure`, `laminator.adhesive_weight`, `env.ambient_temp` (within each hour), `coder.printhead_temp` (during printing), `coder.ink_pressure` (lung pressure, target ~835 mbar, sigma ~60 mbar, range 0-900 mbar), `coder.supply_voltage` (target 24V, sigma 0.1V).

Parameters: `target`, `sigma`, `min_clamp`, `max_clamp`.

**Optional within-regime drift.** During long production runs (8-12 hours of continuous Running state), some signals drift slowly. This is normal operational behaviour, not a scenario or anomaly. Bearings warm up. Ink properties change. Mechanical components settle. The drift is modelled as a slow random walk layered onto the steady-state target:

```
effective_target = target + drift_offset
drift_offset += drift_rate * noise(0, 1) * sqrt(dt) - reversion_rate * drift_offset * dt
value = effective_target + noise(0, sigma)
```

The drift is imperceptible over minutes. Over hours, it shifts the signal baseline by 1-3% of the nominal value. This prevents the "too-clean" appearance that steady-state signals exhibit during long simulated production runs. The `reversion_rate` pulls the drift back toward zero with a time constant of several hours, preventing unbounded wander.

Drift parameters: `drift_rate` (magnitude of slow walk, default 0.001), `reversion_rate` (pull back toward zero, default 0.0001), `max_drift` (clamp on drift_offset, default 3% of target).

Used for: `press.nip_pressure`, `press.web_tension` (baseline), `press.ink_temperature`, `press.main_drive_current`. NOT used for: `press.line_speed` (operator-controlled), counters, setpoints, or state signals. Enable per signal by setting `drift_rate` > 0.

### 4.2.2 Sinusoidal with Noise

The signal follows a sine wave with noise. Models signals with periodic behaviour.

```
value = center + amplitude * sin(2 * pi * t / period + phase) + noise(0, sigma)
```

Used for: `env.ambient_temp` (daily cycle, period=24h), `env.ambient_humidity` (daily cycle, inverted phase).

Parameters: `center`, `amplitude`, `period`, `phase`, `sigma`.

### 4.2.3 First-Order Lag (Setpoint Tracking)

The signal tracks a setpoint with exponential lag. Models temperature controllers.

```
value = value + (setpoint - value) * (1 - exp(-dt / tau)) + noise(0, sigma)
```

Used for: `press.dryer_temp_zone_1/2/3` tracking their setpoints, `laminator.nip_temp`, `laminator.oven_temp`.

Parameters: `tau` (time constant, seconds), `sigma`, `overshoot_factor` (optional, for initial response).

This model directly reflects the Eurotherm controller pattern documented in the customer profiles research: process variable (PV) tracks setpoint (SP) with first-order dynamics. The time constant tau models the thermal mass of the dryer. Typical tau for an industrial dryer: 30-120 seconds.

### 4.2.4 Ramp Up / Ramp Down

The base ramp produces a smooth linear trajectory from one value to another over a specified duration:

```
value = start + (end - start) * (elapsed / duration) + noise(0, sigma)
```

An optional step quantisation layer simulates operator behaviour during manual speed-up. Real press startups are not smooth. The operator adjusts speed in discrete steps. The drive controller overshoots slightly at each step. The quantisation layer:

1. Divides the ramp range into N steps (configurable, default 4).
2. At each step boundary, the output jumps to the next step value.
3. Each jump triggers a small overshoot (configurable, default 3% of step size) that decays exponentially over 5-10 seconds.
4. The dwell time at each step is drawn from a uniform distribution (configurable, default 15-45 seconds).

This produces the jerky, stepped acceleration that a real press operator creates when manually ramping speed. The step count, overshoot magnitude, and dwell time are configurable per equipment type. Set `steps=1` to disable quantisation and produce a smooth ramp.

Used for: `press.line_speed` during startup (0 to target over 2-5 minutes, stepped). `press.line_speed` during shutdown uses a smooth ramp (target to 0 over 30-60 seconds) because emergency stops and controlled shutdowns do not have operator stepping.

Parameters: `start`, `end`, `duration`, `sigma`, `steps` (integer, default 4), `step_overshoot_pct` (float, default 0.03), `step_overshoot_decay_s` (float, default 7.0), `step_dwell_range` (tuple, default [15, 45]).

### 4.2.5 Random Walk with Mean Reversion

The signal drifts randomly but tends to return to a center value. Models signals with slow drift.

```
delta = drift_rate * noise(0, 1) - reversion_rate * (value - center)
value = value + delta * dt
```

Used for: `press.ink_viscosity`, `press.registration_error_x/y`, `coder.ink_viscosity_actual` (mean reversion around target viscosity from reference data, sigma 0.3 cP).

Parameters: `center`, `drift_rate`, `reversion_rate`, `min_clamp`, `max_clamp`.

### 4.2.6 Counter Increment

The signal increments at a rate proportional to machine speed.

```
value = value + rate * line_speed * dt
```

Used for: `press.impression_count`, `press.good_count`, `press.waste_count`, `coder.prints_total`, `energy.cumulative_kwh`, `coder.ink_consumption_ml` (accumulates linearly during printing, rate proportional to ink pump speed).

Parameters: `rate` (increments per m/min per second), `rollover_value` (for counter wrap simulation).

The reference data showed `FPGA_Head_PrintedTotal` wrapping at 999. The press counters use uint32 (max 4,294,967,295) so wrapping is rare but the simulator supports configurable rollover for testing.

### 4.2.7 Depletion Curve

The signal decreases over time proportional to usage. Models consumable levels.

```
value = value - consumption_rate * prints_delta
```

Used for: `coder.ink_level`, `press.unwind_diameter`.

Parameters: `consumption_rate`, `refill_threshold`, `refill_value`.

When `coder.ink_level` drops below `refill_threshold` (default: 10%), a refill event occurs: the value jumps to `refill_value` (default: 100%). The reference data showed `PS_Pnm_InkConsumptionMl` accumulating linearly during production.

### 4.2.8 Correlated Follower

The signal derives from another signal with a transformation.

```
value = f(parent_value) + noise(0, sigma)
```

Used for: `press.main_drive_current` follows `press.line_speed` (linear relationship: current = base_current + k * speed). `press.main_drive_speed` follows `press.line_speed` (gear ratio). `laminator.web_speed` follows `press.line_speed` with offset and lag. `press.rewind_diameter` inversely derives from `press.unwind_diameter`. `coder.ink_pump_speed` follows `coder.state` (steady RPM during Printing, 0 during idle states).

Parameters: `parent_signal`, `transform_function`, `sigma`, `lag` (optional delay).

### 4.2.9 State Machine

The signal transitions between discrete states based on rules and probabilities.

```
state = transition(current_state, triggers, probabilities)
```

Used for: `press.machine_state`, `coder.state`, `coder.nozzle_health` (states: Good/Degraded/Blocked, event-driven transitions over hours of printing), `coder.gutter_fault` (states: OK/Fault, rare event, MTBF 500+ hours).

Parameters: `states[]`, `transitions[]` (each with `from`, `to`, `trigger`, `probability`, `min_duration`, `max_duration`).

### 4.2.10 Thermal Diffusion (Sigmoid)

This model simulates heat penetration into a solid food product. The temperature profile follows an S-curve: slow start as the surface heats, rapid middle as heat conducts inward, slow asymptotic approach to equilibrium. A first-order lag would produce a pure exponential approach with no slow-start phase, which looks wrong to anyone familiar with food thermal processing.

The model uses the first term of the Fourier series solution for 1D heat conduction in a slab:

```
T(t) = T_oven - (T_oven - T_initial) * (8 / pi^2) * exp(-pi^2 * alpha * t / L^2)
```

Where `alpha` is thermal diffusivity (m^2/s) and `L` is the product half-thickness (m). The factor `8/pi^2` (approximately 0.81) ensures the initial temperature starts near `T_initial`. As `t` increases, the exponential term decays and `T(t)` approaches `T_oven`.

Typical values for a chilled ready meal: half-thickness ~25 mm, thermal diffusivity ~1.4e-7 m^2/s (meat-based product). At 180C oven temperature, the core reaches 72C from 4C in approximately 15-20 minutes. BRC requires that product core temperature reaches 72C for 2 minutes, so the model must produce this profile accurately.

Parameters: `T_initial` (product entry temperature, typically 2-8C from chiller), `T_oven` (oven zone temperature), `alpha` (thermal diffusivity, m^2/s), `L` (product half-thickness, m), `sigma` (measurement noise).

Used for: `oven.product_core_temp` in the F&B profile.

This model is simplified. Real products have non-uniform geometry, variable moisture content, and phase changes (ice melting, protein denaturation). The simplified model produces the characteristic S-curve that food manufacturing engineers expect to see. It is sufficient for demo and integration testing purposes.

### 4.2.11 Noise Distribution Models

The preceding signal models all reference `noise(0, sigma)`. By default this is Gaussian white noise. Real industrial sensors do not all produce Gaussian noise. Vibration sensors exhibit heavy tails from mechanical impulses. PID-controlled temperatures exhibit autocorrelated residuals from the control loop. The noise distribution is configurable per signal. Three distributions are supported.

**Gaussian (default).** Standard normal distribution scaled by sigma. Independent samples. No autocorrelation.

```
noise = sigma * N(0, 1)
```

Suitable for environmental sensors, energy meters, fill weight, and any signal where measurement noise dominates. This is the correct choice when the sensor noise is independent sample to sample and has no significant outlier mechanism.

**Student-t (heavy tails).** Produces occasional large outliers that Gaussian cannot. Configurable degrees of freedom (df). Lower df produces heavier tails. At df=3, roughly 1 in 50 samples exceeds 3 sigma. For comparison, Gaussian produces 1 in 370. At df=5, tails are moderately heavy. At df=30 or above, the distribution approaches Gaussian.

```
noise = sigma * T(df)
```

Suitable for vibration signals (`vibration.main_drive_x/y/z`), pressure signals (`coder.ink_pressure`), and motor current (`press.main_drive_current`). These sensors experience occasional mechanical impulses that produce genuine outlier readings even during normal operation. The IMS/NASA and Paderborn bearing datasets both show kurtosis well above 3 in vibration channels.

Parameters: `noise_distribution: "student_t"`, `noise_df: 5` (degrees of freedom).

**AR(1) autocorrelated noise.** First-order autoregressive noise. Each sample depends on the previous sample. Produces the smooth, correlated residuals that real PID-controlled temperatures exhibit.

```
noise_t = phi * noise_(t-1) + sigma * sqrt(1 - phi^2) * N(0, 1)
```

The autocorrelation coefficient (phi) controls how strongly consecutive samples correlate. At phi=0, this reduces to white noise. At phi=0.9, consecutive samples are strongly correlated. The `sqrt(1 - phi^2)` scaling ensures the marginal variance stays at sigma^2 regardless of phi.

Suitable for temperature signals controlled by PID loops (`press.dryer_temp_zone_*`, `oven.zone_*_temp`, `laminator.nip_temp`, `coder.printhead_temp`). These signals have correlated noise because the controller continuously adjusts the output. The result is smooth oscillations around the setpoint rather than independent jumps.

Parameters: `noise_distribution: "ar1"`, `noise_phi: 0.7` (autocorrelation coefficient, range 0 to 0.99).

**Default noise distribution assignments:**

| Signal Category | Distribution | Parameters | Rationale |
|---|---|---|---|
| Vibration (all axes) | Student-t | df=5 | Mechanical impulse outliers |
| Motor current | Student-t | df=8 | Occasional load spikes |
| Ink/lung pressure | Student-t | df=6 | Pneumatic transient outliers |
| PID-controlled temperatures | AR(1) | phi=0.7 | Control loop autocorrelation |
| Environmental sensors | Gaussian | (default) | Measurement noise dominates |
| Fill weight | Gaussian | (default) | CLT applies to multi-head weigher |
| Energy/power | Gaussian | (default) | Power meter averaging |
| Counters | n/a | n/a | Integer increment, no continuous noise |

Each signal inherits the default for its category. Per-signal overrides are supported in the configuration (Appendix D). Setting `noise_distribution` to `"gaussian"` or omitting it produces the default Gaussian behaviour.

## 4.3 Correlation Model

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
    -> coder.ink_pump_speed ramps to operating RPM
    -> coder.ink_pressure stabilizes at ~835 mbar
    -> coder.ink_consumption_ml starts accumulating
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

## 4.4 Time Compression

The simulation clock advances at a configurable multiple of real time:

| Mode | Clock Rate | 1 Real Hour = | Use Case |
|------|-----------|---------------|----------|
| 1x | Real-time | 1 sim hour | Integration testing, demos |
| 10x | 10x | 10 sim hours | Quick scenario walkthroughs |
| 100x | 100x | ~4 sim days | Long-term trend testing |

At higher compression rates, the data generation engine produces values at the same simulated intervals but publishes them more frequently. A 1-second signal at 100x publishes 100 values per real second. Protocol adapters batch these if the client cannot keep up.

At 100x with the packaging profile (47 signals), the aggregate data rate is approximately 235 values per real second. The F&B profile (65 signals) produces approximately 325 values per real second. Both are within the throughput capacity of Modbus TCP, OPC-UA, and MQTT on localhost.

## 4.5 Random Seed

The engine accepts an optional random seed. With the same seed and configuration, the engine produces identical output. This enables reproducible test scenarios. Without a seed, the engine uses a time-based seed for unique runs.

## 4.6 F&B Profile Signal Models

The F&B profile (65 signals) uses the same signal model types as the packaging profile. The parameters differ to match food and beverage equipment. Key model patterns unique to the F&B profile:

**Batch process.** The mixer operates in discrete batch cycles: ingredient addition, mixing, temperature hold, discharge. Each cycle runs 20-45 minutes. The `state_machine` model drives the batch phase. Mixer speed, torque, and batch temperature change with each phase transition. This contrasts with the packaging press, which runs continuously.

**Multi-zone thermal control.** The oven uses three independent temperature zones. Each zone tracks its setpoint via `first_order_lag` with a time constant of 120-300 seconds. Adjacent zones have thermal coupling: a drift in zone 1 nudges zone 2. The coupling factor is configurable (default 0.05).

**Product core temperature.** The `oven.product_core_temp` signal uses the `thermal_diffusion` model (Section 4.2.10) rather than `first_order_lag`. Product core temperature follows an S-curve as heat penetrates from the surface inward. The model resets to `T_initial` each time a new product enters the oven (driven by belt speed and oven length). This is the single most important signal on a food production line. BRC auditors check it first.

**Fill weight distribution.** The filler produces a Gaussian distribution of fill weights around the target (e.g. 350g +/- 3g). The `steady_state` model drives each fill event. When the mean drifts from target, reject rate increases. This is a new application of the steady_state model at the event level rather than the continuous level.

**CIP wash cycles.** Clean-in-place runs between production batches. Wash temperature, flow rate, and conductivity follow a recipe curve over 30-60 minutes. The `ramp` and `first_order_lag` models combine to produce the CIP profile: rinse, caustic wash, rinse, acid wash, final rinse.

**F&B correlation model.** The correlation engine extends for F&B equipment:
- `mixer.torque` correlates with `mixer.speed` and `mixer.batch_temp` (viscosity changes with temperature).
- Oven zones have thermal coupling (zone 1 drift affects zone 2).
- `filler.reject_count` correlates with deviation of `filler.fill_weight` from target.
- `chiller.compressor_power` correlates inversely with `chiller.room_temp` delta from setpoint.

Full F&B signal definitions, register maps, and protocol mappings are in `02b-factory-layout-food-and-beverage.md`.

## 4.7 Ground Truth Event Log

The simulator emits a JSONL sidecar file alongside the data stream. Every scenario event, state transition, and data quality injection is logged with its simulated timestamp and metadata. The scenario engine already tracks all this state internally. The ground truth log writes it out.

**File path:** configurable, default `output/ground_truth.jsonl`.

**Format:** one JSON object per line.

```json
{"sim_time": "2026-03-01T14:30:00.000Z", "event": "scenario_start", "scenario": "web_break", "affected_signals": ["press.web_tension", "press.line_speed", "press.machine_state"], "parameters": {"tension_spike_n": 720, "recovery_seconds": 1200}}
{"sim_time": "2026-03-01T14:30:00.100Z", "event": "signal_anomaly", "signal": "press.web_tension", "anomaly_type": "spike", "value": 720.3, "normal_range": [60, 400]}
{"sim_time": "2026-03-01T14:30:01.000Z", "event": "state_change", "signal": "press.machine_state", "from": 2, "to": 4}
{"sim_time": "2026-03-01T14:50:00.000Z", "event": "scenario_end", "scenario": "web_break"}
```

**Event types:**

| Event | Description |
|---|---|
| `scenario_start` | Scenario begins. Includes scenario name, affected signals, and parameters. |
| `scenario_end` | Scenario completes. Includes scenario name. |
| `state_change` | Equipment state transition. Includes signal name, previous state, new state. |
| `signal_anomaly` | Individual signal anomaly: spike, drift, or excursion. Includes signal, type, value, normal range. |
| `data_quality` | Communication drop, stale value, duplicate, exception injection. Includes protocol and duration. |
| `micro_stop` | Brief speed dip. Includes duration and speed reduction percentage. |
| `shift_change` | Shift transition. Includes old shift, new shift, time. |
| `consumable` | Ink refill, material splice. Includes signal and new value. |
| `sensor_disconnect` | Sensor disconnect injection. Includes signal and sentinel value. |

**Purpose.** The log enables post-hoc evaluation. Compare CollatrEdge alerts against ground truth to compute detection rates, false positive rates, and detection latency. The primary use case remains demos and integration testing. The ground truth log adds benchmarking capability at minimal implementation cost.
