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

Used for: `env.ambient_humidity` (daily cycle, inverted phase). Also used as the base layer for `env.ambient_temp` (see composite environmental model below).

Parameters: `center`, `amplitude`, `period`, `phase`, `sigma`.

**Composite environmental model.** A pure sine wave for ambient temperature is immediately identifiable as synthetic. Real ambient temperature has weather-driven irregularity, HVAC cycling, and door-open step changes. The `env.ambient_temp` signal combines three layers built from existing model primitives:

1. **Daily cycle.** Sinusoidal with 24-hour period. Center 20-22 C, amplitude 3-5 C. Peak in mid-afternoon, trough at dawn.

2. **HVAC cycling.** A secondary oscillation with 15-30 minute period and 0.5-1.5 C amplitude. This represents the factory HVAC system cycling on and off. It uses the bang-bang hysteresis model (Section 4.2.12) with a fast time constant. The HVAC cycle is superimposed on the daily cycle.

3. **Random perturbations.** Occasional step changes of 1-3 C lasting 5-30 minutes. These represent factory doors opening, nearby process heat sources, or HVAC mode changes. Modelled as a Poisson process with 3-8 events per shift. Each event adds a temporary offset that decays back to zero via first-order lag (tau = 5-10 minutes).

The combined model:

```
value = daily_sine(t) + hvac_cycle(t) + perturbation(t) + noise(0, sigma)
```

Ambient humidity follows the same layered pattern but inverted. Humidity drops when temperature rises (HVAC dehumidifies). Humidity spikes when doors open to a humid environment.

Additional parameters for composite environmental model: `hvac_period_minutes` (15-30, default 20), `hvac_amplitude_c` (0.5-1.5, default 1.0), `perturbation_rate_per_shift` (3-8, default 5), `perturbation_magnitude_c` (1-3, default 2.0), `perturbation_decay_tau_minutes` (5-10, default 7).

### 4.2.3 First-Order Lag (Setpoint Tracking)

The signal tracks a setpoint with exponential lag. Models temperature controllers.

```
value = value + (setpoint - value) * (1 - exp(-dt / tau)) + noise(0, sigma)
```

Used for: `press.dryer_temp_zone_1/2/3` tracking their setpoints, `laminator.nip_temp`, `laminator.oven_temp`.

Parameters: `tau` (time constant, seconds), `sigma`, `overshoot_factor` (optional, for initial response), `damping_ratio` (optional, for second-order response).

This model directly reflects the Eurotherm controller pattern documented in the customer profiles research: process variable (PV) tracks setpoint (SP) with first-order dynamics. The time constant tau models the thermal mass of the dryer. Typical tau for an industrial dryer: 30-120 seconds.

**Optional second-order response.** Real PID controllers produce overshoot on setpoint changes. Underdamped loops produce decaying oscillation. Anyone who has tuned a Eurotherm knows the ringing after a step change. The first-order lag alone cannot reproduce this.

When `damping_ratio` is specified with a value less than 1.0, the model adds a second-order response on setpoint changes:

```
value = setpoint + A * exp(-zeta * omega_n * t) * sin(omega_d * t + phase) + noise(0, sigma)
```

Where:

- `omega_n` = natural frequency = `1 / tau` (derived from the existing time constant).
- `omega_d` = damped frequency = `omega_n * sqrt(1 - zeta^2)`.
- `A` = initial amplitude, derived from step size and damping ratio.
- `t` = time since the last setpoint change.

Default `damping_ratio` = 1.0 (critically damped). At this value, the model reduces to the existing first-order lag with no oscillation. Typical industrial PID tuning produces a damping ratio of 0.5 to 0.8. Lower values produce more overshoot and longer ringing.

When `damping_ratio` >= 1.0, the model behaves exactly as the first-order lag described above. When `damping_ratio` < 1.0, the model produces the characteristic overshoot and ringing that real temperature controllers exhibit.

Used for: `press.dryer_temp_zone_*` (damping_ratio ~0.6), `oven.zone_*_temp` (damping_ratio ~0.5), `laminator.nip_temp` (damping_ratio ~0.7), `laminator.oven_temp` (damping_ratio ~0.7).

Second-order parameters: `damping_ratio` (float, default 1.0, range 0.1 to 2.0).

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

**Rollover and reset behaviour.** Counters that reach their configured maximum (`rollover_value` or range maximum) wrap to zero. Counters configured with `reset_on_job_change: true` reset to zero at each job changeover (Section 5.2).

Under time compression, counters increment faster but the rollover and reset logic is unchanged. At 100x speed, a counter incrementing at 200/minute in simulated time reaches 99,999 in approximately 8 real minutes. This is expected behaviour. CollatrEdge must handle counter rollovers at any speed.

To prevent counters from dominating the value range during compressed runs, the simulator supports an optional `max_before_reset` parameter. When set, the counter resets to zero after reaching this value. This simulates the real-world practice of operators resetting counters at shift changes or job starts. Default: disabled (counter wraps at `rollover_value`).

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

Used for: `press.main_drive_current` follows `press.line_speed` (linear relationship: current = base_current + k * speed). `press.main_drive_speed` follows `press.line_speed` (gear ratio). `laminator.web_speed` follows `press.line_speed` with transport lag (see below). `press.rewind_diameter` inversely derives from `press.unwind_diameter`. `coder.ink_pump_speed` follows `coder.state` (steady RPM during Printing, 0 during idle states).

Parameters: `parent_signal`, `transform_function`, `sigma`, `lag` (optional delay, fixed or transport mode).

**Speed-dependent transport lag.** When material moves between two points on the line, the correlation lag depends on distance and current line speed:

```
lag_seconds = distance_meters / (line_speed_m_per_min / 60)
```

The `lag` parameter accepts either a fixed value in seconds or a dynamic transport configuration:

```yaml
lag:
  mode: "transport"          # "fixed" or "transport"
  distance_m: 4.0            # meters between equipment
  speed_signal: "press.line_speed"
```

When mode is "transport", the lag recalculates each tick based on the current speed. At zero speed, no material transport occurs. The model freezes the downstream signal at its last value until the upstream speed resumes.

Key transport distances for the packaging line:

| Path | Distance | Lag at 120 m/min | Lag at 250 m/min |
|---|---|---|---|
| Press to laminator | 3-5 m | 1.5-2.5 s | 0.7-1.2 s |
| Laminator to slitter | 2-3 m | 1.0-1.5 s | 0.5-0.7 s |
| Press to coder (if inline) | 1-2 m | 0.5-1.0 s | 0.2-0.5 s |

See Appendix D for transport lag configuration examples.

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

**Speed-dependent sigma.** All three noise distributions support an optional speed-dependent sigma. Constant sigma produces an unnaturally uniform noise envelope. Real noise characteristics change with operating conditions. Vibration noise is lower at low speed and higher at high speed. Tension noise increases with speed. Registration error noise scales with both speed and substrate properties.

When enabled, the effective sigma scales with a parent signal:

```
effective_sigma = sigma_base + sigma_scale * abs(parent_value)
```

`sigma_base` is the minimum noise floor. `sigma_scale` is the proportional component. When `sigma_scale` = 0 (the default), noise sigma is constant and equals the per-signal `sigma` parameter.

Default assignments for speed-dependent noise:

| Signal | Parent | sigma_base | sigma_scale | Effect |
|---|---|---|---|---|
| vibration.main_drive_x/y/z | press.line_speed | 0.2 mm/s | 0.015 mm/s per m/min | Vibration increases with speed |
| press.web_tension | press.line_speed | 2.0 N | 0.02 N per m/min | Tension noise increases at speed |
| press.registration_error_x/y | press.line_speed | 0.005 mm | 0.00005 mm per m/min | Registration harder at speed |
| press.main_drive_current | press.line_speed | 0.3 A | 0.002 A per m/min | Current ripple scales with load |

Parameters: `sigma_parent` (signal ID, optional), `sigma_base` (float), `sigma_scale` (float, default 0.0). These parameters appear inside any signal's `params` block alongside the existing `sigma`, `noise_distribution`, and distribution-specific parameters. See Appendix D for a configuration example.

### 4.2.12 Bang-Bang with Hysteresis

Models an on/off controller with dead band. The output oscillates between two states based on a process variable crossing upper and lower thresholds.

```
if state == OFF and process_variable > setpoint + dead_band_high:
    state = ON
if state == ON and process_variable < setpoint - dead_band_low:
    state = OFF
```

When ON, the process variable decreases at a configurable cooling rate. When OFF, the process variable increases at a configurable heat gain rate (from the environment and door openings). This produces the characteristic sawtooth temperature pattern in cold rooms.

Used for: `chiller.compressor_state` driving `chiller.room_temp`. The compressor coil (FC01) reflects the ON/OFF state. Room temperature follows a sawtooth pattern between setpoint +/- dead band.

Parameters: `setpoint` (target temperature), `dead_band_high` (offset above setpoint to turn ON, default 1.0 C), `dead_band_low` (offset below setpoint to turn OFF, default 1.0 C), `cooling_rate` (C per minute when ON, default 0.5), `heat_gain_rate` (C per minute when OFF, default 0.2), `sigma` (noise on the process variable).

Typical chiller configuration: setpoint 2 C, dead band +/- 1 C. Temperature oscillates between 1 C and 3 C with a cycle time of about 8-12 minutes. The Danfoss controller turns the compressor on when room temperature exceeds setpoint + dead_band_high, and off when it drops below setpoint - dead_band_low.

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
    -> laminator.web_speed follows press speed with transport lag (3-5 m distance, Section 4.2.8)
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

The `cip.conductivity` signal follows a specific profile through each CIP cycle:

1. Pre-rinse: conductivity near 0 mS/cm (fresh water).
2. Caustic dose: conductivity ramps up to 80-150 mS/cm over 1-2 minutes as chemical is dosed into the circulation loop. This uses a first-order lag with setpoint = target concentration.
3. Caustic hold: conductivity holds steady at target for 10-20 minutes.
4. Rinse: conductivity follows a first-order lag with setpoint = 0 mS/cm and tau = 30-60 seconds. This produces the exponential decay from caustic concentration toward zero that real CIP rinse cycles exhibit. Water dilutes chemical residue in a first-order process, not a linear ramp.
5. Final rinse: conductivity below 5 mS/cm for 2+ minutes confirms the system is clean.

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
| `stuck_sensor` | Stuck sensor (frozen value) injection. Includes signal, frozen value, and duration. |

**Connection drop events.** The log also records controller connection drops with controller ID, protocol, start time, duration, and affected signals. See Section 4.8 for signal behaviour during drops.

**Purpose.** The log enables post-hoc evaluation. Compare CollatrEdge alerts against ground truth to compute detection rates, false positive rates, and detection latency. The primary use case remains demos and integration testing. The ground truth log adds benchmarking capability at minimal implementation cost.

## 4.8 Signal Behaviour During Controller Connection Drops

When a simulated controller drops its connection (Section 3a.5), the data generation engine continues generating values internally. The signals do not freeze. The machine does not stop. A real factory keeps running even when a PLC loses its network connection. The PLC continues executing its control program. It just stops responding to external queries.

When the connection recovers, the protocol adapter serves the current generated value. This creates a gap in the collected data followed by a step change. CollatrEdge sees the last value it received before the drop, then a jump to the current value when the connection resumes.

The gap size depends on the drop duration and polling rate. A 5-second Modbus drop at 1-second polling produces 5 missing samples. A 30-second OPC-UA stale period produces values marked `UncertainLastUsableValue` for 30 seconds.

**MQTT behaviour.** A broker-side drop means messages are not published during the drop. The behaviour depends on QoS level. QoS 0 messages are lost. The simulator silently discards them. QoS 1 messages queue and deliver when the connection resumes, if the broker session persists. The simulator models this: during an MQTT drop, QoS 1 messages accumulate in a buffer (configurable limit, default 1000 messages). On recovery, buffered messages publish in a burst. QoS 0 messages are silently discarded.

**Ground truth.** The ground truth event log (Section 4.7) records each connection drop with: controller ID, protocol, start time, duration, and list of affected signals.
