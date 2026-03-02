# Appendix D: Configuration Reference

## Profile Selection

The simulator supports two factory profiles. Set the active profile in the top-level configuration:

```yaml
profile: "packaging"          # "packaging" or "food_bev"
```

The packaging profile activates press, laminator, slitter, coder, environment, energy, and vibration generators (47 signals). The food_bev profile activates mixer, oven, filler, sealer, chiller, CIP, coder, environment, and energy generators (65 signals). Only one profile runs at a time.

```yaml
ground_truth_log: "output/ground_truth.jsonl"  # Path for ground truth event log (Section 4.7)
```

## Signal Model Parameters

### Noise Distribution Parameters

Every signal model that includes noise accepts optional distribution parameters. Omitting these parameters produces the default Gaussian white noise. See Section 4.2.11 for distribution definitions.

**Gaussian (default).** No additional parameters required. Omit `noise_distribution` or set it to `"gaussian"`.

```yaml
# Gaussian noise (default, explicit)
noise_distribution: "gaussian"
sigma: 0.3
```

**Student-t (heavy tails).** Set `noise_distribution` to `"student_t"` and specify degrees of freedom.

```yaml
# Student-t noise for vibration signals
noise_distribution: "student_t"
noise_df: 5              # Degrees of freedom (lower = heavier tails)
sigma: 0.3               # Scale parameter (same role as Gaussian sigma)
```

**AR(1) autocorrelated noise.** Set `noise_distribution` to `"ar1"` and specify the autocorrelation coefficient.

```yaml
# AR(1) noise for PID-controlled temperatures
noise_distribution: "ar1"
noise_phi: 0.7            # Autocorrelation coefficient (0 to 0.99)
sigma: 0.3                # Marginal standard deviation
```

**Speed-dependent sigma.** Any noise distribution supports optional speed-dependent sigma. See Section 4.2.11 for the formula and default assignments.

```yaml
# Speed-dependent noise for vibration signal
noise_distribution: "student_t"
noise_df: 5
sigma: 0.3                # Used when sigma_scale = 0 (fallback)
sigma_parent: "press.line_speed"   # Parent signal driving noise magnitude
sigma_base: 0.2                     # Minimum noise floor (mm/s)
sigma_scale: 0.015                  # Proportional component (mm/s per m/min)
```

When `sigma_parent` is set and `sigma_scale` > 0, the effective sigma is `sigma_base + sigma_scale * abs(parent_value)`. When `sigma_parent` is omitted or `sigma_scale` = 0, the static `sigma` parameter applies.

These parameters appear inside the `params` block of any signal model. The examples below show the default Gaussian noise. Override with the parameters above as needed per signal.

### steady_state

```yaml
model: "steady_state"
params:
  target: 85.0          # Target value
  sigma: 0.3            # Gaussian noise standard deviation
  min_clamp: 40.0       # Minimum allowed value
  max_clamp: 120.0      # Maximum allowed value
  # Optional within-regime drift (set drift_rate > 0 to enable)
  drift_rate: 0.0       # Slow walk magnitude (default 0 = disabled)
  reversion_rate: 0.0001  # Pull back toward zero (time constant of hours)
  max_drift: 2.55       # Clamp on drift_offset (default 3% of target)
  # Optional long-term calibration drift (Section 4.2.1)
  calibration_drift_rate: 0.0  # Units per simulated hour (default 0 = disabled)
                               # Typical thermocouple: 0.001-0.01 C/hour
                               # Produces 0.5-5 C drift over a simulated month
  # Optional quantisation (Section 4.2.13)
  quantisation_resolution: null  # Snap to multiples of this value (default null = disabled)
                                 # Example: 0.1 for Eurotherm int16 x10
                                 # Example: 0.024 for 12-bit ADC on 0-100 C range
```

### sinusoidal

```yaml
model: "sinusoidal"
params:
  center: 22.0          # Center of oscillation
  amplitude: 5.0        # Peak deviation from center
  period_hours: 24.0    # Full cycle period in hours
  phase_hours: 6.0      # Phase offset in hours (0 = peak at midnight)
  sigma: 0.1            # Gaussian noise
```

### first_order_lag

```yaml
model: "first_order_lag"
params:
  tau_seconds: 60.0     # Time constant in seconds
  sigma: 0.3            # Noise magnitude
  overshoot: 0.05       # Overshoot factor (0 = no overshoot)
  damping_ratio: 0.7    # Second-order response (0.1-2.0, default 1.0 = no oscillation)
                         # < 1.0: underdamped, produces overshoot and ringing
                         # = 1.0: critically damped, reduces to first-order lag
                         # > 1.0: overdamped, slower approach with no oscillation
                         # Typical industrial PID: 0.5-0.8
  setpoint_signal: "press.dryer_setpoint_zone_1"  # Signal to track
  # AR(1) noise for PID-controlled temperatures
  noise_distribution: "ar1"
  noise_phi: 0.7        # Autocorrelation coefficient
```

### ramp

```yaml
model: "ramp"
params:
  ramp_up_seconds: 180    # Seconds to ramp from 0 to target
  ramp_down_seconds: 30   # Seconds to ramp from target to 0
  sigma: 0.5              # Gaussian noise during ramp and steady state
  steps: 4                # Step quantisation count (1 = smooth ramp)
  step_overshoot_pct: 0.03      # Overshoot as fraction of step size
  step_overshoot_decay_s: 7.0   # Overshoot decay time constant (seconds)
  step_dwell_range: [15, 45]    # Dwell time per step (seconds, uniform random)
```

### random_walk

```yaml
model: "random_walk"
params:
  center: 28.0          # Mean reversion target
  drift_rate: 0.1       # Random walk step magnitude
  reversion_rate: 0.01  # Mean reversion strength (0-1)
  min_clamp: 15.0
  max_clamp: 60.0
```

### counter

```yaml
model: "counter"
params:
  rate_per_speed_unit: 1.0    # Increments per (m/min * second)
  speed_signal: "press.line_speed"  # Signal that drives increment rate
  rollover_value: 4294967295  # Counter wrap value (uint32 max)
  reset_on_job_change: true   # Reset to 0 on job changeover
  max_before_reset: null      # Optional: reset to 0 at this value (default null = disabled)
                              # Useful under time compression to simulate operator resets
```

### depletion

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

### correlated_follower

```yaml
model: "correlated_follower"
params:
  parent_signal: "press.line_speed"
  transform: "linear"       # linear, quadratic, or custom
  base: 15.0                # Output when parent = 0
  factor: 0.5               # Output = base + factor * parent
  sigma: 0.5                # Additional noise (Gaussian default)
  lag_seconds: 0            # Fixed delay (used when lag.mode is omitted or "fixed")
  # Time-varying covariance (Section 4.3.2)
  gain_drift_volatility: 0.0     # Multiplicative drift on gain parameter (default 0 = fixed gain)
                                  # Typical: 0.001-0.004. Higher = faster gain wander.
  gain_drift_reversion: 0.02     # Mean-reversion rate pulling gain back to nominal
                                  # Typical: 0.01-0.05. Higher = tighter reversion.
  # Example: motor current vs line speed with 8-12% gain variation over 24h
  # gain_drift_volatility: 0.003
  # gain_drift_reversion: 0.02
  # Example: Student-t noise for motor current
  # noise_distribution: "student_t"
  # noise_df: 8
```

**Transport lag example.** For signals that follow material flow between machines, use dynamic transport lag instead of a fixed delay. The lag recalculates each tick based on line speed. See Section 4.2.8 for the formula and distance table.

```yaml
# Laminator web speed follows press speed with transport lag
model: "correlated_follower"
params:
  parent_signal: "press.line_speed"
  transform: "linear"
  base: 0.0
  factor: 1.0
  sigma: 0.3
  lag:
    mode: "transport"                # "fixed" or "transport"
    distance_m: 4.0                  # meters between press and laminator
    speed_signal: "press.line_speed" # signal providing current line speed
  # At 200 m/min: lag = 4.0 / (200/60) = 1.2 seconds
  # At 120 m/min: lag = 4.0 / (120/60) = 2.0 seconds
  # At 0 m/min: downstream freezes at last value
```

### state_machine

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

### thermal_diffusion

```yaml
model: "thermal_diffusion"
params:
  T_initial: 4.0            # Product entry temperature (C)
  T_oven: 180.0             # Oven zone temperature (C)
  alpha: 1.4e-7             # Thermal diffusivity (m^2/s)
  L: 0.025                  # Product half-thickness (m)
  sigma: 0.3                # Measurement noise
```

### bang_bang_hysteresis

```yaml
model: "bang_bang_hysteresis"
params:
  setpoint: 2.0              # Target temperature (C)
  dead_band_high: 1.0        # Offset above setpoint to turn ON (C)
  dead_band_low: 1.0         # Offset below setpoint to turn OFF (C)
  cooling_rate: 0.5          # C per minute when compressor ON
  heat_gain_rate: 0.2        # C per minute when compressor OFF
  sigma: 0.1                 # Noise on process variable
  initial_state: "OFF"       # Starting state: "ON" or "OFF"
```

### string_generator

```yaml
model: "string_generator"
params:
  template: "{date:%y%m%d}-{line}-{seq:03d}"  # Format string
  line_id: "L1"                                 # Line identifier
  reset_at: "00:00"                             # Time of day to reset sequence number
  # Example output: "260302-L1-007"
  # Sequence increments on each new batch (mixer state machine transition)
  # Resets to 001 at the configured reset time
```

### peer_correlation

Peer correlation matrices are configured per signal group. Each matrix R is the desired correlation matrix: symmetric, positive definite, unit diagonal. The implementation computes the Cholesky factor L = cholesky(R) at startup and applies it each tick. See Section 4.3.1 for the decomposition formula and signal generation pipeline.

```yaml
peer_groups:
  vibration_axes:
    signals: ["vibration.main_drive_x", "vibration.main_drive_y", "vibration.main_drive_z"]
    correlation_matrix:
      - [1.0,  0.2,  0.15]
      - [0.2,  1.0,  0.2 ]
      - [0.15, 0.2,  1.0 ]
  dryer_zones:
    signals: ["press.dryer_temp_zone_1", "press.dryer_temp_zone_2", "press.dryer_temp_zone_3"]
    correlation_matrix:
      - [1.0,  0.1,  0.02]
      - [0.1,  1.0,  0.1 ]
      - [0.02, 0.1,  1.0 ]
  oven_zones:
    signals: ["oven.zone_1_temp", "oven.zone_2_temp", "oven.zone_3_temp"]
    correlation_matrix:
      - [1.0,  0.15, 0.05]
      - [0.15, 1.0,  0.15]
      - [0.05, 0.15, 1.0 ]
```

## Network Topology Parameters

### Clock Drift

Per-controller clock drift offsets. See Section 3a.5 for the formula and typical values.

```yaml
network:
  clock_drift:
    press_plc:
      initial_offset_ms: 200        # Starting offset in milliseconds
      drift_rate_s_per_day: 0.3     # Seconds of drift per simulated day
    laminator_plc:
      initial_offset_ms: 1500
      drift_rate_s_per_day: 1.0
    oven_zone_1:
      initial_offset_ms: 5000       # Eurotherm, notorious drifter
      drift_rate_s_per_day: 5.0
    oven_zone_2:
      initial_offset_ms: 6000
      drift_rate_s_per_day: 4.5
    oven_zone_3:
      initial_offset_ms: 7000
      drift_rate_s_per_day: 6.0
    chiller:
      initial_offset_ms: 3000
      drift_rate_s_per_day: 2.5
    energy_meter:
      initial_offset_ms: 100
      drift_rate_s_per_day: 0.2
```

Set `drift_rate_s_per_day` to 0 for a controller with perfect time sync. The initial offset is applied at simulation start. The drift accumulates linearly from there.

### Scan Cycle Artefacts

Per-controller scan cycle times and phase jitter. See Section 3a.8 for behaviour description.

```yaml
network:
  scan_cycle:
    press_plc:
      cycle_ms: 10               # Siemens S7-1500
      jitter_pct: 0.05           # 0-5% variation per cycle
    laminator_plc:
      cycle_ms: 20               # Siemens S7-1200
      jitter_pct: 0.08
    slitter_plc:
      cycle_ms: 20               # Siemens S7-1200
      jitter_pct: 0.08
    mixer_plc:
      cycle_ms: 15               # Allen-Bradley CompactLogix
      jitter_pct: 0.06
    oven_zone_1:
      cycle_ms: 100              # Eurotherm 3504
      jitter_pct: 0.10
    oven_zone_2:
      cycle_ms: 100              # Eurotherm 3504
      jitter_pct: 0.10
    oven_zone_3:
      cycle_ms: 100              # Eurotherm 3504
      jitter_pct: 0.10
    filler_plc:
      cycle_ms: 20               # Siemens S7-1200
      jitter_pct: 0.08
    sealer_plc:
      cycle_ms: 20               # Siemens S7-1200
      jitter_pct: 0.08
    chiller:
      cycle_ms: 100              # Danfoss AK-CC 550
      jitter_pct: 0.10
    cip_controller:
      cycle_ms: 20               # Siemens S7-1200
      jitter_pct: 0.08
```

Set `jitter_pct` to 0 for a perfectly periodic scan cycle (useful for debugging). The jitter is drawn from a uniform distribution on each cycle: `actual_cycle = cycle_ms * (1.0 + uniform(0, jitter_pct))`.

## Protocol Mapping Reference

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

## Scenario Parameters Reference

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

  # Bearing wear: long-term exponential degradation (Section 5.5)
  bearing_wear:
    enabled: true
    start_after_hours: 48
    base_rate: [0.001, 0.005]         # Initial mm/s increase per hour
    acceleration_k: [0.005, 0.01]     # Exponential acceleration constant
    warning_threshold: 15.0           # mm/s
    alarm_threshold: 25.0             # mm/s
    current_increase_percent: [1, 5]  # Motor current increase (follows same exponential curve)
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

  # Micro-stops (brief speed dips, no state change)
  micro_stop:
    enabled: true
    frequency_per_shift: [10, 50]
    duration_seconds: [5, 30]
    speed_drop_percent: [30, 80]          # Percentage of current speed
    ramp_down_seconds: [2, 5]
    ramp_up_seconds: [5, 15]
    mean_interval_minutes: [10, 50]       # Poisson process mean

  # Contextual anomalies: normal values in wrong machine state
  contextual_anomaly:
    enabled: true
    frequency_per_week: [2, 5]
    types:
      heater_stuck:
        probability: 0.3
        duration_seconds: [300, 3600]
      pressure_bleed:
        probability: 0.2
        duration_seconds: [600, 7200]
      counter_false_trigger:
        probability: 0.2
        duration_seconds: [60, 600]
        increment_rate: 0.1              # increments per second
      hot_during_maintenance:
        probability: 0.15
        duration_seconds: [1800, 7200]
      vibration_during_off:
        probability: 0.15
        duration_seconds: [300, 1800]

  # Intermittent faults: appear, disappear, reappear before becoming permanent
  intermittent_fault:
    enabled: true
    faults:
      bearing_intermittent:
        enabled: true
        start_after_hours: 24
        phase1_duration_hours: [168, 336]      # 1-2 weeks sporadic
        phase1_frequency_per_day: [1, 3]
        phase1_spike_duration_s: [10, 60]
        phase2_duration_hours: [48, 168]       # 2-7 days frequent
        phase2_frequency_per_day: [5, 20]
        phase2_spike_duration_s: [30, 300]
        phase3_transition: true                # become permanent
        affected_signals: ["vibration.main_drive_x", "vibration.main_drive_y", "vibration.main_drive_z"]
        spike_magnitude: [15, 25]              # mm/s during spike
      electrical_intermittent:
        enabled: true
        start_after_hours: 48
        phase1_duration_hours: [72, 168]
        phase1_frequency_per_day: [1, 2]
        phase1_spike_duration_s: [1, 10]
        phase2_duration_hours: [24, 72]
        phase2_frequency_per_day: [5, 15]
        phase2_spike_duration_s: [2, 30]
        phase3_transition: true
        affected_signals: ["press.main_drive_current"]
        spike_magnitude_pct: [20, 50]
      sensor_intermittent:
        enabled: false                         # enable per signal
        phase1_duration_hours: [48, 168]
        phase1_frequency_per_day: [1, 3]
        phase1_spike_duration_s: [1, 5]
        phase2_duration_hours: [24, 72]
        phase2_frequency_per_day: [5, 15]
        phase2_spike_duration_s: [2, 10]
        phase3_transition: true                # permanent disconnect
      pneumatic_intermittent:
        enabled: true
        start_after_hours: 72
        phase1_duration_hours: [168, 504]
        phase1_frequency_per_day: [1, 2]
        phase1_spike_duration_s: [2, 30]
        phase2_duration_hours: [48, 168]
        phase2_frequency_per_day: [3, 10]
        phase2_spike_duration_s: [5, 60]
        phase3_transition: false               # valve replaced before permanent failure
        affected_signals: ["coder.ink_pressure"]

  # --- F&B Profile Scenarios ---

  # Batch cycle (mixer)
  batch_cycle:
    enabled: true
    batch_duration_seconds: [1200, 2700]   # 20-45 minutes
    loading_seconds: [120, 300]            # 2-5 minutes
    mixing_seconds: [600, 1500]            # 10-25 minutes
    hold_seconds: [300, 600]               # 5-10 minutes
    target_temp_c: [65, 85]                # Batch temperature range
    mixer_speed_rpm: [1000, 2500]           # Production mixing speed (loading is 50-100 RPM)

  # Oven thermal excursion
  oven_excursion:
    enabled: true
    frequency_per_shift: [1, 2]
    affected_zone: "random"                # random, 1, 2, or 3
    drift_rate_c_per_min: [0.1, 0.3]
    max_drift_c: [5, 20]
    duration_seconds: [1800, 5400]         # 30-90 minutes
    coupling_factor: 0.05                  # Thermal coupling to adjacent zones

  # Fill weight drift
  fill_weight_drift:
    enabled: true
    frequency_per_shift: [1, 3]
    drift_rate_g_per_min: [0.05, 0.2]
    max_drift_g: [3, 8]
    duration_seconds: [600, 3600]          # 10-60 minutes
    target_weight_g: 350.0
    acceptable_range_g: 5.0

  # Seal integrity failure
  seal_failure:
    enabled: true
    frequency_per_week: [1, 2]
    temp_drop_rate_c_per_min: [0.5, 2.0]
    min_seal_temp_c: 170.0
    duration_seconds: [300, 1800]          # 5-30 minutes
    recovery_seconds: [600, 1800]          # Line stop for repair

  # Chiller door alarm
  chiller_door:
    enabled: true
    frequency_per_week: [1, 3]
    temp_rise_rate_c_per_min: [0.5, 2.0]
    door_open_seconds: [300, 1200]         # 5-20 minutes
    recovery_tau_seconds: 120              # First-order recovery time constant

  # CIP cycle
  cip_cycle:
    enabled: true
    frequency_per_day: [1, 3]
    total_duration_seconds: [1800, 3600]   # 30-60 minutes
    pre_rinse_seconds: [180, 300]
    caustic_temp_c: [70, 80]
    acid_wash_seconds: [300, 600]
    final_rinse_conductivity_max: 5.0       # mS/cm acceptance threshold (must fall below 5 mS/cm)

  # Cold chain break
  cold_chain_break:
    enabled: true
    frequency_per_month: [1, 2]
    temp_rise_rate_c_per_min: [0.5, 1.5]
    alarm_threshold_c: 8.0
    duration_seconds: [1800, 7200]         # 30-120 minutes
    compressor_restart_delay_seconds: [60, 300]
```

## Data Quality Injection Parameters

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
  
  # Sensor disconnect events
  sensor_disconnect:
    enabled: true
    frequency_per_24h_per_signal: [0, 1]   # Range for random frequency
    duration_seconds: [30, 300]             # 30 seconds to 5 minutes
    sentinel_defaults:
      temperature: 6553.5                  # Siemens wire break convention
      pressure: 0.0                        # 4-20mA open circuit
      voltage: -32768                      # int16 min, open circuit
    per_signal_overrides:                  # Override sentinel per signal
      # press.dryer_temp_zone_1: 9999.0    # Eurotherm convention
      # coder.ink_pressure: 0.0            # 4-20mA transmitter

  # Stuck sensor (frozen value) events (Section 10.10)
  stuck_sensor:
    enabled: true
    frequency_per_week_per_signal: [0, 2]  # Range for random frequency
    duration_seconds: [300, 14400]          # 5 minutes to 4 hours
    # Value freezes at last valid reading. Status codes remain Good.
    # Ground truth log records frozen value, start time, and duration.

  # Partial Modbus responses (Section 10.11)
  partial_modbus_response:
    enabled: true
    probability: 0.0001              # Per multi-register read (default 0.01%)
    # Only applies to requests for 2+ registers.
    # Returns first N registers (N random, 1 to requested-1).
    # Single-register reads are never partial.

  # Timezone offset (for MQTT timestamps)
  mqtt_timestamp_offset_hours: 0      # 0 = UTC, 1 = BST, -5 = US Eastern
```
