# Data Generation Engine

## 4.1 Design Principles

The data generation engine produces parametric synthetic data. It does not replay recorded timeseries. Every run generates unique data from configurable models. The engine runs at a configurable time scale (1x to 10x for live protocol serving, 100x+ for batch generation).

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

**Optional long-term calibration drift.** Real sensors drift over months. A thermocouple in a dryer zone might drift 1-2 C per year. This is a slow persistent bias that accumulates over simulated days and weeks. Unlike within-regime drift (which mean-reverts over hours), calibration drift does not revert. It represents physical sensor degradation.

```
calibration_bias += calibration_drift_rate * dt
value = value + calibration_bias
```

The `calibration_drift_rate` parameter specifies the drift in signal units per simulated hour. Default: 0 (disabled). Typical values for thermocouples: 0.001 to 0.01 C/hour, producing 0.5 to 5 C of drift over a simulated month. The drift is linear over the simulated time horizon. For runs shorter than a simulated week, the effect is negligible. For multi-week runs at 100x compression (batch mode), the drift becomes visible.

The ground truth event log does not record calibration drift as an event. It is a continuous process, not a discrete event. The configuration documents the drift rate per signal.

Calibration drift parameters: `calibration_drift_rate` (float, units per simulated hour, default 0).

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

Used for: `press.dryer_temp_zone_1/2/3` tracking their setpoints, `laminator.nip_temp`, `laminator.tunnel_temp`.

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

The implementation resets `t` to zero on each setpoint change. The amplitude `A` is recomputed as the difference between the new setpoint and the current value at the moment of change.

Default `damping_ratio` = 1.0 (critically damped). At this value, the model reduces to the existing first-order lag with no oscillation. Typical industrial PID tuning produces a damping ratio of 0.5 to 0.8. Lower values produce more overshoot and longer ringing.

When `damping_ratio` >= 1.0, the model behaves exactly as the first-order lag described above. When `damping_ratio` < 1.0, the model produces the characteristic overshoot and ringing that real temperature controllers exhibit.

Used for: `press.dryer_temp_zone_*` (damping_ratio ~0.6), `oven.zone_*_temp` (damping_ratio ~0.5), `laminator.nip_temp` (damping_ratio ~0.7), `laminator.tunnel_temp` (damping_ratio ~0.7).

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

Under time compression, counters increment faster but the rollover and reset logic is unchanged. At 100x speed (batch mode), a counter incrementing at 200/minute in simulated time reaches 99,999 in approximately 8 real minutes. This is expected behaviour. CollatrEdge must handle counter rollovers at any speed.

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

The model uses a truncated Fourier series solution for 1D heat conduction in a slab. The full series for the volume-averaged temperature is:

```
T(t) = T_oven - (T_oven - T_initial) * SUM_{n=0}^{N} [ C_n * exp(-(2n+1)^2 * pi^2 * alpha * t / L^2) ]
```

Where `C_n = 8 / ((2n+1)^2 * pi^2)`, `alpha` is thermal diffusivity (m^2/s), and `L` is the product half-thickness (m). The first three terms (n=0,1,2) have coefficients:

| Term | Coefficient | Approximate Value |
|------|------------|-------------------|
| n=0 | 8 / pi^2 | 0.8106 |
| n=1 | 8 / (9 * pi^2) | 0.0901 |
| n=2 | 8 / (25 * pi^2) | 0.0324 |

The three-term sum is 0.9331. At t=0 with T_initial=4C and T_oven=180C, T(0) = 180 - 0.9331 * 176 = 15.8C. The full infinite series sums to 1.0, giving T(0) = T_initial exactly. Each added term improves the initial condition. Five terms sum to 0.9638. Ten terms sum to 0.9818.

The PRD shows three terms for clarity. Implementations must sum terms until T(0) falls within 1C of T_initial. The number of terms depends on the temperature difference. For small differences (T_oven - T_initial < 50C), 10 terms suffice. For large differences (176C for a 4C product entering a 180C oven), 20 to 30 terms are needed. The convergence check is:

```
if abs(T(0) - T_initial) > 1.0:
    add next term and recheck
```

As `t` increases, the higher-order terms decay fast. After a few seconds only the n=0 term matters. The extra terms correct the initial condition without affecting the long-term profile.

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

**Variance note.** The Student-t distribution with df degrees of freedom has variance `sigma^2 * df / (df - 2)`. At df=5, the effective standard deviation is 1.29 times sigma. Student-t signals have 29% higher RMS noise than Gaussian signals with the same sigma parameter. This is intentional. The heavier tails and higher variance together model the real behaviour of vibration and pressure sensors, where both outlier frequency and baseline variability exceed Gaussian predictions. To match RMS noise exactly between distributions, scale the Student-t sigma by `sqrt((df - 2) / df)`. The default configuration does not apply this correction.

**AR(1) autocorrelated noise.** First-order autoregressive noise. Each sample depends on the previous sample. Produces the smooth, correlated residuals that real PID-controlled temperatures exhibit.

```
noise_t = phi * noise_(t-1) + sigma * sqrt(1 - phi^2) * N(0, 1)
```

The autocorrelation coefficient (phi) controls how strongly consecutive samples correlate. At phi=0, this reduces to white noise. At phi=0.9, consecutive samples are strongly correlated. The `sqrt(1 - phi^2)` scaling ensures the marginal variance stays at sigma^2 regardless of phi.

Suitable for temperature signals controlled by PID loops (`press.dryer_temp_zone_*`, `oven.zone_*_temp`, `laminator.nip_temp`, `coder.printhead_temp`). These signals have correlated noise because the controller continuously adjusts the output. The result is smooth oscillations around the setpoint rather than independent jumps.

Parameters: `noise_distribution: "ar1"`, `noise_phi: 0.7` (autocorrelation coefficient, range 0 to 0.99).

During a controller connection drop (Section 4.8), the AR(1) noise process continues generating internally. The autocorrelation state is maintained across the gap. When the connection resumes, the noise sequence is continuous from the engine's perspective even though the client saw no updates during the drop.

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

**Known limitation: no 1/f (pink) noise.** The noise models do not include a 1/f component. Real industrial environments exhibit 1/f spectral characteristics from building vibrations, electrical interference, and thermal fluctuations. At the 1-second to 60-second sampling rates used by this simulator, the 1/f component is weak but detectable in multi-day power spectral density analysis. An analyst computing the PSD of 7 days of ambient temperature data will see a flat spectrum instead of the expected 1/f slope. This limitation does not affect time-domain visual inspection or short-duration evaluation runs. A fractional Gaussian noise generator can be added in a future phase for research benchmarking use cases.

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

### 4.2.13 Sensor Quantisation

Real ADCs produce quantised values. A 12-bit ADC on a 0-100 C range gives 0.024 C resolution. A 16-bit Eurotherm input register with x10 scaling gives 0.1 C resolution. The simulator produces continuous floating-point values by default. At low signal levels, real data shows visible quantisation steps.

After noise generation, an optional quantisation step rounds the output to the nearest multiple of the sensor's resolution:

```
quantised_value = round(value / resolution) * resolution
```

The `quantisation_resolution` parameter (float, optional, default: disabled) sets the step size. When set, the output snaps to multiples of this value.

Typical resolutions:

| Sensor Type | Resolution | Source |
|---|---|---|
| Eurotherm int16 x10 | 0.1 C | 16-bit register, x10 scaling |
| 12-bit ADC, 0-100 C | 0.024 C | 4096 steps over 100 C range |
| 16-bit ADC, 0-50 mm/s | 0.00076 mm/s | 65536 steps over 50 range |
| Schneider PM5560 power | 0.01 kW | Meter display resolution |

Quantisation is most visible on slow-changing signals at low levels: idle vibration, ambient temperature at night. At high signal levels or fast-changing signals, noise masks the quantisation. Enable selectively for signals where it adds realism.

Parameters: `quantisation_resolution` (float, optional, default: disabled).

### 4.2.14 String Generator

The string generator produces formatted identifier strings. It does not generate numeric values. It assembles strings from a template with dynamic components.

```
batch_id = format_template.format(
    date=sim_date,
    line=line_id,
    seq=batch_sequence_number
)
```

Default template: `"{date:%y%m%d}-{line}-{seq:03d}"`. Example output: `"260302-L1-007"` (2 March 2026, Line 1, batch 7 of the day).

The sequence number increments each time a new batch starts. The mixer state machine drives this: each transition to a new batch increments the counter. The sequence resets to 001 at each simulated midnight.

Parameters: `template` (format string), `line_id` (string, default from profile config), `reset_at` (time of day for sequence reset, default `"00:00"`).

Used for: `mixer.batch_id` in the F&B profile.

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

### 4.3.1 Peer Correlation via Cholesky Decomposition

Some signal groups exhibit peer correlations. These are signals that influence each other without a clear parent-child hierarchy. The three vibration axes share mechanical coupling. The three dryer zones share thermal mass. After removing state machine and parent-child effects, these residual correlations remain.

The engine supports peer correlation through Cholesky decomposition of a desired correlation matrix. For a group of N peer signals, the configuration specifies a correlation matrix R. R is symmetric, positive definite, and has unit diagonal. The implementation computes the lower-triangular Cholesky factor L at startup:

```
L = cholesky(R)
```

Each tick, the engine generates N independent samples from N(0, 1) and applies L:

```
noise_correlated = L @ noise_independent
```

The covariance of the output is L @ L^T = R. The correlations match the specification exactly. Use `numpy.linalg.cholesky` or `scipy.linalg.cholesky` for the decomposition. Both require R to be positive definite (all eigenvalues > 0). The matrices below satisfy this requirement.

**Vibration axes: correlation matrix R (3x3):**

```
R = [[1.0,  0.2,  0.15],
     [0.2,  1.0,  0.2 ],
     [0.15, 0.2,  1.0 ]]
```

Mechanical coupling between X, Y, Z axes. A bearing defect affects all three axes but with different magnitudes.

**Dryer zones: correlation matrix R (3x3):**

```
R = [[1.0,  0.1,  0.02],
     [0.1,  1.0,  0.1 ],
     [0.02, 0.1,  1.0 ]]
```

Thermal coupling between adjacent zones. Zone 2 correlates with both zone 1 and zone 3. Zones 1 and 3 have minimal direct coupling.

**Oven zones: correlation matrix R (3x3):**

```
R = [[1.0,  0.15, 0.05],
     [0.15, 1.0,  0.15],
     [0.05, 0.15, 1.0 ]]
```

Same structure as dryer zones but with higher coupling coefficients. The oven is a more enclosed thermal mass.

The correlation matrix R is configurable per group. Set all off-diagonal entries to 0 to disable peer correlation. The implementation derives L from R at startup. See Appendix D for configuration examples.

**Signal generation pipeline.** The order of operations matters for correctness:

1. Generate N independent samples from N(0, 1).
2. Apply the Cholesky factor: `noise_correlated = L @ noise_independent`. This introduces the desired correlations with unit variance.
3. Scale each signal by its effective sigma: `noise_final_i = sigma_i * noise_correlated_i`. The effective sigma may be speed-dependent (Section 4.2.11).

This order preserves the correlation structure. Scaling after correlation is correct because scaling is a diagonal transformation. It changes covariance magnitudes but does not change correlation coefficients. Reversing steps 2 and 3 would distort the correlations whenever signals have different sigma values.

### 4.3.2 Time-Varying Covariance

The gain parameter `k` in a correlated follower (`child = base + k * parent + noise`) is not constant in the real world. Load changes. Bearings warm up. Mechanical wear shifts friction coefficients. The relationship between motor current and line speed drifts over hours and days. A fixed linear transform produces scatter plots that are too tight and too stable.

The simulator models this by applying a multiplicative random walk to the gain parameter:

```
k_effective = k_nominal * gain_drift_factor
```

The drift factor evolves each tick:

```
log_drift += drift_volatility * noise(0, 1) * sqrt(dt) - reversion_rate * log_drift * dt
gain_drift_factor = exp(log_drift)
```

The multiplicative form ensures `k_effective` stays positive. The logarithmic mean-reversion pulls the drift factor back toward 1.0 over time. The `drift_volatility` parameter controls how fast the gain wanders. The `reversion_rate` parameter controls how strongly it snaps back.

Typical behaviour: over 24 simulated hours, the gain varies by 5-15% from its nominal value. Over a week, excursions of 20-25% are possible before mean-reversion pulls back. The drift is slow enough that minute-to-minute correlations look stable. Only hour-over-hour or shift-over-shift analysis reveals the changing relationship.

This matters for anomaly detection. A fixed-gain model trains a detector to expect a tight linear band. Real data has a wider, shifting band. Detectors trained on fixed-gain synthetic data produce false positives on real data where the gain has drifted. Time-varying covariance closes this realism gap.

**Default assignments:**

| Signal Pair | k_nominal | drift_volatility | reversion_rate | Typical 24h Variation |
|---|---|---|---|---|
| main_drive_current vs line_speed | 0.5 A per m/min | 0.003 | 0.02 | 8-12% |
| main_drive_speed vs line_speed | gear ratio | 0.001 | 0.05 | 3-5% |
| web_tension vs line_speed | varies | 0.004 | 0.015 | 10-15% |
| ink_viscosity vs ink_temperature | varies | 0.002 | 0.03 | 5-8% |

Enable per signal by setting `gain_drift_volatility` > 0 in the correlated follower configuration. Set to 0 (the default) for a fixed gain. See Appendix D for configuration parameters.

## 4.4 Time Compression

The simulation clock advances at a configurable multiple of real time:

| Mode | Clock Rate | 1 Real Hour = | Protocol Serving | Use Case |
|------|-----------|---------------|------------------|----------|
| 1x | Real-time | 1 sim hour | Yes | Integration testing, demos |
| 10x | 10x | 10 sim hours | Yes | Quick scenario walkthroughs |
| 100x | 100x | ~4 sim days | No (batch only) | Evaluation datasets, long-term trends |
| 1000x | 1000x | ~42 sim days | No (batch only) | Predictive modelling, schedule optimisation |

At 1x to 10x, protocol adapters serve data live. CollatrEdge connects and polls over Modbus TCP, subscribes via OPC-UA, and receives MQTT messages. At 10x, a 1-second signal updates every 100ms. Modbus controllers with 50-200ms response latency can just keep up. This is the protocol serving ceiling.

Above 10x, the simulator switches to batch generation mode. Protocol adapters are disabled. The engine writes signal data to CSV or Parquet files and the ground truth log to JSONL. The engine runs as fast as the CPU allows. A 24-hour simulation at 100x completes in minutes. A 7-day simulation at 100x completes in under 2 hours. Batch mode is used for evaluation datasets (Section 12), long-term degradation analysis, and predictive modelling (Section 9.4).

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

**Atomic recipe changes.** When a food line changes recipes, multiple setpoints change together. All three oven zone setpoints, belt speed, filler target weight, and sealer parameters update as a batch. On a real Eurotherm, a recipe change writes all parameters in a single Modbus transaction (milliseconds apart). The scenario engine groups related setpoints into a recipe:

```yaml
recipe:
  name: "Chicken Tikka 400g"
  setpoints:
    oven.zone_1_setpoint: 185.0
    oven.zone_2_setpoint: 190.0
    oven.zone_3_setpoint: 180.0
    oven.belt_speed: 2.5
    filler.fill_target: 400.0
    sealer.seal_temp: 175.0
    sealer.gas_co2_pct: 30.0
    sealer.gas_n2_pct: 70.0
```

All setpoints in a recipe update at the same simulation tick. The process variables then respond at their individual time constants. Zone 1 might reach its new setpoint in 90 seconds while the heavier zone 3 takes 180 seconds. The setpoint change is instant. The response is gradual. The key requirement is that setpoint writes happen simultaneously, not sequentially.

**F&B correlation model.** The correlation engine extends for F&B equipment:
- `mixer.torque` correlates with `mixer.speed` and `mixer.batch_temp` (viscosity changes with temperature).
- Oven zones have thermal coupling (zone 1 drift affects zone 2).
- `filler.reject_count` correlates with deviation of `filler.fill_weight` from target.
- `chiller.compressor_power` correlates inversely with `chiller.room_temp` delta from setpoint.

Full F&B signal definitions, register maps, and protocol mappings are in `02b-factory-layout-food-and-beverage.md`.

## 4.7 Ground Truth Event Log

The simulator emits a JSONL sidecar file alongside the data stream. Every scenario event, state transition, and data quality injection is logged with its simulated timestamp and metadata. The scenario engine already tracks all this state internally. The ground truth log writes it out.

**File path:** configurable, default `output/ground_truth.jsonl`.

**Format:** one JSON object per line. The first line is a configuration header record. All subsequent lines are event records.

**Header record.** The first line of the file has `event_type: "config"`. It contains the simulator version, random seed, profile name, per-signal noise parameters (distribution type, sigma, df, phi, speed-dependent parameters), and active scenario list. This makes the ground truth log self-contained. A researcher can run a KS test or spectral analysis against the output without consulting the original configuration file.

```json
{"event_type": "config", "sim_version": "1.0.0", "seed": 42, "profile": "packaging", "signals": {"press.line_speed": {"noise": "gaussian", "sigma": 0.5}, "vibration.main_drive_x": {"noise": "student_t", "sigma": 0.3, "df": 5}}, "scenarios": ["job_changeover", "web_break", "dryer_drift"]}
```

**Event records.** All subsequent lines are event records:

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
