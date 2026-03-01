# Appendix D: Configuration Reference

## Signal Model Parameters

### steady_state

```yaml
model: "steady_state"
params:
  target: 85.0          # Target value
  sigma: 0.3            # Gaussian noise standard deviation
  min_clamp: 40.0       # Minimum allowed value
  max_clamp: 120.0      # Maximum allowed value
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
  sigma: 0.3            # Gaussian noise
  overshoot: 0.05       # Overshoot factor (0 = no overshoot)
  setpoint_signal: "press.dryer_setpoint_zone_1"  # Signal to track
```

### ramp

```yaml
model: "ramp"
params:
  ramp_up_seconds: 180  # Seconds to ramp from 0 to target
  ramp_down_seconds: 30 # Seconds to ramp from target to 0
  sigma: 0.5            # Gaussian noise during ramp and steady state
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
  sigma: 0.5                # Additional Gaussian noise
  lag_seconds: 0            # Delay following parent changes
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
  
  # Timezone offset (for MQTT timestamps)
  mqtt_timestamp_offset_hours: 0      # 0 = UTC, 1 = BST, -5 = US Eastern
```
