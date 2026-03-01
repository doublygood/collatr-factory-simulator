# Data Generation Engine

## 4.1 Design Principles

The data generation engine produces parametric synthetic data. It does not replay recorded timeseries. Every run generates unique data from configurable models. The engine runs at a configurable time scale (1x, 10x, 100x real-time).

Key principles:

1. **Correlations over individual signals.** Signals do not vary independently. When line speed changes, motor current changes, web tension fluctuates, dryer temperatures respond, and waste rate shifts. The engine models these dependencies explicitly.

2. **State drives everything.** The machine state (Off, Setup, Running, Idle, Fault, Maintenance) determines the behaviour of all signals. A signal generator does not produce values in isolation. It asks "what state is the machine in?" and generates accordingly.

3. **Noise is not optional.** Every analog signal includes Gaussian noise at a configurable magnitude. Real sensors are noisy. Clean signals look fake. The noise magnitude is calibrated from studying the reference data. Print head temperature had 2.8C standard deviation. Lung pressure had 60 mbar standard deviation. We tune noise per signal.

4. **Time is the independent variable.** The engine maintains a simulation clock. At each tick, it advances the clock, evaluates active scenarios, updates the machine state, and generates new values for all signals. The tick rate matches the fastest signal (500ms for web tension and registration error). Slower signals update only on their configured interval.

## 4.2 Signal Models

Each signal uses one of the following generator models:

### 4.2.1 Steady State with Noise

The simplest model. The signal stays near a target value with Gaussian noise.

```
value = target + noise(0, sigma)
```

Used for: `press.nip_pressure`, `laminator.nip_pressure`, `laminator.adhesive_weight`, `env.ambient_temp` (within each hour), `coder.printhead_temp` (during printing).

Parameters: `target`, `sigma`, `min_clamp`, `max_clamp`.

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

The signal moves linearly from one value to another over a specified duration.

```
value = start + (end - start) * (elapsed / duration) + noise(0, sigma)
```

Used for: `press.line_speed` during startup (0 to target over 2-5 minutes), `press.line_speed` during shutdown (target to 0 over 30-60 seconds).

Parameters: `start`, `end`, `duration`, `sigma`.

### 4.2.5 Random Walk with Mean Reversion

The signal drifts randomly but tends to return to a center value. Models signals with slow drift.

```
delta = drift_rate * noise(0, 1) - reversion_rate * (value - center)
value = value + delta * dt
```

Used for: `press.ink_viscosity`, `press.registration_error_x/y`.

Parameters: `center`, `drift_rate`, `reversion_rate`, `min_clamp`, `max_clamp`.

### 4.2.6 Counter Increment

The signal increments at a rate proportional to machine speed.

```
value = value + rate * line_speed * dt
```

Used for: `press.impression_count`, `press.good_count`, `press.waste_count`, `coder.prints_total`, `energy.cumulative_kwh`.

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

Used for: `press.main_drive_current` follows `press.line_speed` (linear relationship: current = base_current + k * speed). `press.main_drive_speed` follows `press.line_speed` (gear ratio). `laminator.web_speed` follows `press.line_speed` with offset and lag. `press.rewind_diameter` inversely derives from `press.unwind_diameter`.

Parameters: `parent_signal`, `transform_function`, `sigma`, `lag` (optional delay).

### 4.2.9 State Machine

The signal transitions between discrete states based on rules and probabilities.

```
state = transition(current_state, triggers, probabilities)
```

Used for: `press.machine_state`, `coder.state`.

Parameters: `states[]`, `transitions[]` (each with `from`, `to`, `trigger`, `probability`, `min_duration`, `max_duration`).

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

At 100x, the aggregate data rate across 40 signals is approximately 200 values per real second. This is within the throughput capacity of Modbus TCP, OPC-UA, and MQTT on localhost.

## 4.5 Random Seed

The engine accepts an optional random seed. With the same seed and configuration, the engine produces identical output. This enables reproducible test scenarios. Without a seed, the engine uses a time-based seed for unique runs.
