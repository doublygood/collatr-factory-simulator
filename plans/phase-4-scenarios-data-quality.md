# Phase 4: Full Scenario System and Data Quality

**Timeline:** Weeks 9-11 (3 weeks, expanded per PRD Appendix F)
**Goal:** All scenarios operational for both profiles. Data quality injection active. Scenario scheduling engine complete.

## Overview

Phase 4 completes the scenario system and adds data quality realism. This is the phase that turns the simulator from "correct synthetic data" into "realistic messy industrial data."

Three major workstreams:

1. **Advanced scenarios** (bearing wear, micro-stops, contextual anomalies, intermittent faults)
2. **Scenario scheduling engine** (Poisson inter-arrival, priority rules, conflict resolution)
3. **Data quality injection** (comm drops, noise calibration, sensor failures, protocol-level defects)

By end of Phase 4, both profiles produce data that exercises CollatrEdge's robustness against the full range of industrial data quality issues.

## PRD References

| Group | PRD Sections |
|-------|-------------|
| **A: Scheduling Engine** | `prd/05-scenario-system.md` (5.13, 5.15), `prd/appendix-d-configuration-reference.md` (Scenario Parameters) |
| **B: Advanced Scenarios** | `prd/05-scenario-system.md` (5.5, 5.15, 5.16, 5.17), `prd/appendix-d-configuration-reference.md` |
| **C: Data Quality** | `prd/10-data-quality-realism.md` (ALL sections 10.1-10.11), `prd/appendix-d-configuration-reference.md` (Data Quality Injection Parameters) |
| **D: Noise Calibration** | `prd/10-data-quality-realism.md` (10.3), `prd/appendix-d-configuration-reference.md` (Signal Model Parameters) |

## Carried Forward Items

| ID | Source | Description | Disposition |
|----|--------|-------------|-------------|
| Y1 (Phase 2) | `_spawn_rng` uses `integers()` not `SeedSequence.spawn()` | **Fix in Task 4.1** alongside scheduling engine RNG refactor |
| Y3 (Phase 2.1) | DataEngine doesn't pass `sim_duration_s` to ScenarioEngine | **Fix in Task 4.1** — DataEngine should pass `config.simulation.sim_duration_s` or default to shift length |
| gutter_fault | Phase 1 review | Probability 18x too high | **Fix in Task 4.13** during noise calibration pass |

## Task Groups

### Group A: Scheduling Engine (Tasks 4.1-4.3)

Replace the current uniform-random scheduling with Poisson inter-arrival times and add scenario priority/conflict rules.

### Group B: Advanced Packaging Scenarios (Tasks 4.4-4.7)

New scenario types that require the Poisson scheduling engine: bearing wear (long-term), micro-stops, contextual anomalies, intermittent faults.

### Group C: Data Quality Injection (Tasks 4.8-4.12)

Protocol-level data quality defects: communication drops, sensor disconnects, stuck sensors, Modbus exceptions, partial responses, duplicate timestamps, timezone offsets.

### Group D: Noise Calibration and Polish (Tasks 4.13-4.16)

Signal noise calibration for both profiles, counter rollover testing support, reproducibility verification, and final integration tests.

---

## Task Details

### 4.1 — Poisson Scheduling Engine

**Group:** A: Scheduling Engine
**File:** `src/factory_simulator/engine/scenario_engine.py`
**PRD refs:** `prd/05-scenario-system.md` (5.13), `prd/appendix-f-implementation-phases.md` (Phase 4)

Replace all `_schedule_*` methods with Poisson inter-arrival time generation:

1. Compute the mean inter-arrival time from the configured frequency range: `mean_interval = period / mean(freq_range)`
2. Draw inter-arrival times from an exponential distribution: `rng.exponential(mean_interval)`
3. Enforce minimum gap = scenario minimum duration (prevents overlapping instances of the same type)
4. Scenarios crossing shift boundaries continue into the next shift (no forced truncation)

Also fix carried-forward items:
- **Y1**: Use `SeedSequence.spawn()` instead of `integers()` for child RNG creation
- **Y3**: Accept `sim_duration_s` parameter (default: `_SHIFT_SECONDS`), DataEngine passes it from config

Keep backward compatibility: existing scheduling tests should still pass (frequencies will be statistically similar, not identical, so relax exact-count assertions to range checks).

**Tests:**
- Poisson inter-arrival times are exponentially distributed (KS test)
- Minimum gap enforcement prevents overlapping same-type scenarios
- Cross-shift continuation works (scenario starting at 7:55 in an 8h shift runs past 8:00)
- `SeedSequence.spawn()` produces deterministic child RNGs
- `sim_duration_s` parameter is respected

---

### 4.2 — Scenario Priority and Conflict Resolution

**Group:** A: Scheduling Engine
**File:** `src/factory_simulator/engine/scenario_engine.py`
**PRD refs:** `prd/05-scenario-system.md` (5.13), `prd/appendix-f-implementation-phases.md` (Phase 4)

Implement the scenario priority system:

1. **State-changing scenarios** (web break, unplanned stop, job changeover, CIP cycle, cold chain break, seal integrity) preempt non-state-changing scenarios (dryer drift, ink excursion, bearing wear, registration drift, fill weight drift, oven thermal excursion)
2. **Non-state-changing scenarios can overlap** — multiple drift/excursion scenarios can run simultaneously
3. **Contextual anomaly timeout**: cancel if the target machine state does not occur within 2x the scheduled window

Add a `priority` attribute to the Scenario base class:
- `"state_changing"` — can preempt, cannot be preempted
- `"non_state_changing"` — can overlap, can be preempted
- `"background"` — runs independently (bearing wear, intermittent faults)
- `"micro"` — independent, no interaction (micro-stops)

In `ScenarioEngine.tick()`, before activating a pending scenario:
- If it's state-changing, check for active non-state-changing scenarios and complete them early
- If it's non-state-changing, check for active state-changing scenarios and defer activation

**Tests:**
- State-changing preempts non-state-changing
- Non-state-changing scenarios overlap correctly
- Background scenarios are never preempted
- Micro-stops run independently of all other scenarios
- Contextual anomaly timeout fires at 2x window

---

### 4.3 — Scenario Conflict Resolution Config Models

**Group:** A: Scheduling Engine
**File:** `src/factory_simulator/config.py`
**PRD refs:** `prd/appendix-d-configuration-reference.md`

Add config models for new scenario types that will be implemented in Group B:

- `MicroStopConfig` (frequency_per_shift, duration_seconds, speed_drop_percent, ramp_down_seconds, ramp_up_seconds, mean_interval_minutes)
- `BearingWearConfig` updates: add `acceleration_k`, `warning_threshold`, `alarm_threshold`, `current_increase_percent`, `failure_vibration` fields (some may already exist from Phase 1)
- `ContextualAnomalyConfig` with nested type configs (heater_stuck, pressure_bleed, counter_false_trigger, hot_during_maintenance, vibration_during_off)
- `IntermittentFaultConfig` with nested fault configs (bearing_intermittent, electrical_intermittent, sensor_intermittent, pneumatic_intermittent)
- `DataQualityConfig` with all sub-sections from PRD Appendix D

Add all new config fields to `ScenariosConfig` and create a new `DataQualityConfig` on `FactoryConfig`.

Update both `config/factory.yaml` and `config/factory-foodbev.yaml` with the new config sections (use PRD Appendix D defaults).

**Tests:**
- All new config models validate correctly with default values
- Invalid configs (negative durations, probabilities > 1) are rejected
- Both factory configs load successfully with the new sections

---

### 4.4 — Motor Bearing Wear Scenario

**Group:** B: Advanced Packaging Scenarios
**File:** `src/factory_simulator/scenarios/bearing_wear.py`
**PRD refs:** `prd/05-scenario-system.md` (5.5)

Implement the exponential degradation model:

```
vibration_increase = base_rate * exp(k * elapsed_hours)
```

- `base_rate`: 0.001-0.005 mm/s per hour (configurable)
- `k`: 0.005-0.01 acceleration constant (configurable)
- Affects `vibration.main_drive_x/y/z` and `press.main_drive_current`
- Warning threshold at 15-20 mm/s, alarm at 25-40 mm/s
- Optional culmination in Fault state with vibration spike to 40-50 mm/s
- Operates on long timescale: weeks at 1x, hours at 100x batch mode

This is a `"background"` priority scenario — never preempted by other scenarios.

Register in `ScenarioEngine` with Poisson scheduling from `BearingWearConfig`.

**Tests:**
- Exponential curve shape (vibration increase follows exp model)
- Warning and alarm thresholds trigger at correct vibration levels
- Motor current increase follows same exponential curve at smaller magnitude
- Failure mode transitions machine to Fault state
- Scenario runs for full configured duration at various time scales
- Background priority: not preempted by state-changing scenarios

---

### 4.5 — Micro-Stops Scenario

**Group:** B: Advanced Packaging Scenarios
**File:** `src/factory_simulator/scenarios/micro_stop.py`
**PRD refs:** `prd/05-scenario-system.md` (5.15)

Micro-stops are brief speed dips (5-30s) that do NOT change machine state:

1. `press.line_speed` drops by 30-80% over 2-5 seconds
2. `press.web_tension` fluctuates during deceleration
3. `press.waste_count` increment rate increases briefly
4. Speed ramps back to previous target over 5-15 seconds
5. `press.machine_state` stays Running (2) throughout

Poisson process with configurable mean interval (10-50 minutes). 10-50 per shift.

This is a `"micro"` priority scenario — runs independently, no interaction with other scenarios.

**Tests:**
- Speed dip magnitude within configured range
- Machine state does NOT change during micro-stop
- Waste count increases during the dip
- Recovery ramp timing correct
- Poisson inter-arrival times (statistical test)
- Micro-stops only fire when machine is Running

---

### 4.6 — Contextual Anomalies

**Group:** B: Advanced Packaging Scenarios
**File:** `src/factory_simulator/scenarios/contextual_anomaly.py`
**PRD refs:** `prd/05-scenario-system.md` (5.16)

Five contextual anomaly types — normal values appearing in wrong machine states:

1. **Heater stuck on**: `coder.printhead_temp` stays at 40-42C during Off/Standby
2. **Pressure bleed**: `coder.ink_pressure` stays at 800-850 mbar during Off
3. **Counter false trigger**: `press.impression_count` increments during Idle
4. **Temperature during maintenance**: `press.dryer_temp_zone_1` at 100C during Maintenance (5)
5. **Vibration during off**: `vibration.main_drive_x` at 3-5 mm/s during Off

Scheduling: 2-5 events per simulated week. Each type has a probability weight. The engine picks a type, waits for the required machine state, then injects the anomalous value for configured duration.

Timeout: if target state doesn't occur within 2x scheduled window, cancel the anomaly.

**Tests:**
- Each anomaly type injects correct signal value during correct machine state
- Anomaly ends when machine state changes (early termination)
- Timeout cancellation at 2x window
- Probability weights produce expected distribution over many runs
- Ground truth logs each anomaly with expected_state and actual_state

---

### 4.7 — Intermittent Faults

**Group:** B: Advanced Packaging Scenarios
**File:** `src/factory_simulator/scenarios/intermittent_fault.py`
**PRD refs:** `prd/05-scenario-system.md` (5.17)

Three-phase progression model with four fault subtypes:

**Phase 1 (Sporadic):** 1-3 per day, 10-60s duration, weeks long
**Phase 2 (Frequent):** 5-20 per day, 30-300s duration, days long
**Phase 3 (Permanent):** Continuous. May trigger Fault state.

Four subtypes:
1. **Bearing intermittent**: vibration spikes to 15-25 mm/s. Precedes bearing wear scenario.
2. **Electrical intermittent**: `press.main_drive_current` spikes 20-50%. May cause motor overload (code 101).
3. **Sensor intermittent**: Any analog signal briefly reports sentinel value (Section 10.9).
4. **Pneumatic intermittent**: `coder.ink_pressure` drops to 0 for 2-30s.

This is a `"background"` priority scenario — runs on long timescales.

Each subtype has independent phase timing and transition config. The sensor intermittent subtype is disabled by default (enable per signal).

**Tests:**
- Phase 1 → Phase 2 → Phase 3 transition at correct elapsed times
- Spike frequency increases between phases
- Spike duration increases between phases
- Phase 3 produces continuous fault (permanent)
- Phase 3 transition to Fault state (when configured)
- Each subtype affects the correct signals
- Ground truth logs phase transitions and individual spike events

---

### 4.8 — Communication Drop Injection

**Group:** C: Data Quality Injection
**File:** `src/factory_simulator/protocols/modbus_server.py`, `src/factory_simulator/protocols/opcua_server.py`, `src/factory_simulator/protocols/mqtt_publisher.py`
**PRD refs:** `prd/10-data-quality-realism.md` (10.2)

Inject configurable communication drops into each protocol:

- **Modbus**: Server stops responding for 1-10 seconds. Client times out. 1-2 per hour.
- **OPC-UA**: Node values freeze, status code changes to `UncertainLastUsableValue`. 5-30 seconds. 1-2 per hour.
- **MQTT**: Publisher stops publishing to specific topics for 5-30 seconds. QoS 0 messages lost; QoS 1 delivered on resume. 1-2 per hour.

Each protocol adapter reads `DataQualityConfig` and schedules drops using the engine's RNG. Drop timing is deterministic for a given seed.

**Tests:**
- Modbus: client read times out during drop, resumes after
- OPC-UA: status code changes to Uncertain during freeze
- MQTT: no messages published during drop, messages resume after
- Drop frequency matches config
- Drops are deterministic for same seed

---

### 4.9 — Sensor Disconnect and Stuck Sensor

**Group:** C: Data Quality Injection
**Files:** `src/factory_simulator/engine/data_engine.py` (or new `src/factory_simulator/engine/data_quality.py`)
**PRD refs:** `prd/10-data-quality-realism.md` (10.9, 10.10)

**Sensor disconnect:**
1. Signal jumps to configured sentinel value (temp: 6553.5, pressure: 0.0, voltage: -32768)
2. OPC-UA status → `BadSensorFailure`, MQTT quality → `"bad"`
3. Duration: 30s-5min, frequency: 0-1 per 24h per signal

**Stuck sensor:**
1. Signal freezes at current value (stops changing)
2. Status codes remain `Good` (sensor thinks it's working)
3. Duration: 5min-4h, frequency: 0-2 per week per signal

Both are injected at the store/engine level — they override the generator output before protocol servers read it.

**Tests:**
- Disconnect: signal reports sentinel value for correct duration
- Disconnect: OPC-UA status code changes, MQTT quality changes
- Stuck: signal holds exact frozen value, no noise
- Stuck: status codes remain Good
- Both: ground truth logs record events
- Both: resume normal generation after duration expires

---

### 4.10 — Modbus Exception and Partial Response Injection

**Group:** C: Data Quality Injection
**File:** `src/factory_simulator/protocols/modbus_server.py`
**PRD refs:** `prd/10-data-quality-realism.md` (10.6, 10.11)

**Modbus exceptions:**
- Exception code 0x04 (Slave Device Failure): random at configured probability
- Exception code 0x06 (Slave Device Busy): during machine state transitions
- Configurable probability per read request (default: 0.001)

**Partial responses:**
- Multi-register reads occasionally return fewer registers than requested
- Single-register reads are never partial
- Return first N registers (N random, 1 to requested-1)
- Probability: 0.0001 per multi-register read

Both require subclassing or intercepting the pymodbus response handling.

**Tests:**
- Exception responses have correct Modbus exception codes
- Exception probability matches config
- Partial response byte count matches actual returned registers
- Single-register reads are never partial
- Ground truth logs record injection events

---

### 4.11 — Duplicate Timestamps and Timezone Offset

**Group:** C: Data Quality Injection
**Files:** `src/factory_simulator/protocols/modbus_server.py`, `src/factory_simulator/protocols/mqtt_publisher.py`
**PRD refs:** `prd/10-data-quality-realism.md` (10.5, 10.7)

**Duplicate timestamps:**
- Modbus: same value+timestamp on consecutive reads at configurable probability (0.01%)
- MQTT: two messages to same topic within 1ms at configurable probability (0.005%)

**Timezone offset:**
- MQTT: `timestamp_offset_hours` config shifts ISO 8601 timestamps
- OPC-UA: always UTC (spec requirement, no offset)
- Default: 0 (UTC). Set to 1 for BST, -5 for US Eastern.

**Tests:**
- Duplicate Modbus reads return identical values and timestamps
- Duplicate MQTT publishes arrive within 1ms of each other
- MQTT timestamps shift by configured offset
- OPC-UA timestamps are always UTC regardless of config

---

### 4.12 — Data Quality Config Integration

**Group:** C: Data Quality Injection
**File:** `src/factory_simulator/engine/data_engine.py`
**PRD refs:** `prd/10-data-quality-realism.md` (all), `prd/appendix-d-configuration-reference.md`

Wire up the DataQualityConfig to the engine tick loop:

1. Create a `DataQualityInjector` class that reads `DataQualityConfig`
2. On each tick, the injector decides whether to:
   - Start/stop a sensor disconnect
   - Start/stop a stuck sensor freeze
   - (Protocol-level injection is handled by the protocol adapters directly)
3. The injector modifies store values AFTER generators write but BEFORE protocol servers read
4. All injection events are logged to ground truth

The injector must be deterministic (uses engine's RNG with spawned child).

**Tests:**
- DataQualityInjector integrates with DataEngine tick loop
- Injections are deterministic for same seed
- Multiple quality injections can be active simultaneously
- Disabling `data_quality.enabled` globally disables all injection
- Per-section enable/disable works independently

---

### 4.13 — Noise Calibration (Packaging Profile)

**Group:** D: Noise Calibration and Polish
**Files:** `config/factory.yaml`
**PRD refs:** `prd/10-data-quality-realism.md` (10.3), `prd/appendix-d-configuration-reference.md`

Calibrate noise parameters for all 47 packaging signals against the PRD Section 10.3 table:

| Signal | Sigma | Distribution | Notes |
|--------|-------|-------------|-------|
| press.line_speed | 0.5 | Gaussian | Encoder resolution |
| press.web_tension | 5.0 | Gaussian | Load cell noise |
| press.registration_error_x/y | 0.01 | Gaussian | Camera resolution |
| press.ink_viscosity | 0.5 | Gaussian | Measurement variability |
| press.ink_temperature | 0.2 | Gaussian | Thermocouple |
| press.dryer_temp_zone_* | 0.3 | AR(1), phi=0.7 | PID autocorrelation |
| press.main_drive_current | 0.5 | Student-t, df=8 | CT clamp + load spikes |
| press.main_drive_speed | 2.0 | Gaussian | Encoder |
| press.nip_pressure | 0.05 | Gaussian | Transducer |
| coder.printhead_temp | 0.5 | AR(1), phi=0.7 | PID-controlled |
| coder.ink_pump_speed | 0.5 | Gaussian | Pump encoder |
| coder.ink_pressure | 60 | Student-t, df=6 | Pneumatic transients |
| coder.ink_viscosity_actual | 0.3 | Gaussian | Viscosity sensor |
| coder.supply_voltage | 0.1 | Gaussian | PSU ripple |
| env.ambient_temp | 0.1 | Gaussian | IOLink |
| env.ambient_humidity | 0.5 | Gaussian | IOLink |
| energy.line_power | 0.2 | Gaussian | Power meter |
| vibration.main_drive_* | 0.3 | Student-t, df=5 | Mechanical impulse |

Also fix the gutter_fault probability (carried forward from Phase 1 — currently 18x too high).

Update `config/factory.yaml` signal params to match. Add `noise_distribution`, `noise_df`, `noise_phi` fields where needed.

**Tests:**
- Each signal's noise distribution matches config after engine run
- AR(1) signals show positive autocorrelation (lag-1 > 0.5)
- Student-t signals show heavier tails than Gaussian (kurtosis > 3)
- gutter_fault probability is correct

---

### 4.14 — Noise Calibration (F&B Profile)

**Group:** D: Noise Calibration and Polish
**Files:** `config/factory-foodbev.yaml`
**PRD refs:** `prd/10-data-quality-realism.md` (10.3), `prd/appendix-d-configuration-reference.md`

Calibrate noise parameters for all 68 F&B signals. The PRD table covers packaging signals explicitly. For F&B signals, use analogous equipment types:

| F&B Signal | Sigma | Distribution | Basis |
|------------|-------|-------------|-------|
| mixer.speed | 1.0 | Gaussian | Motor encoder, lower resolution than press |
| mixer.torque | 5.0 | Student-t, df=8 | Load spikes during mixing |
| mixer.batch_temp | 0.3 | AR(1), phi=0.7 | PID-controlled |
| oven.zone_*_temp | 0.3 | AR(1), phi=0.7 | Same as press dryer zones |
| oven.product_core_temp | 0.5 | Gaussian | Core temp probe noise |
| filler.fill_weight | 0.5 | Gaussian | Load cell per-item |
| sealer.seal_temp | 0.3 | AR(1), phi=0.7 | PID-controlled |
| sealer.seal_pressure | 0.05 | Gaussian | Transducer |
| chiller.room_temp | 0.1 | AR(1), phi=0.8 | Bang-bang with slow dynamics |
| cip.wash_temp | 0.3 | AR(1), phi=0.7 | PID-controlled |
| cip.conductivity | 0.5 | Gaussian | Conductivity probe |

Update `config/factory-foodbev.yaml` signal params to match.

**Tests:**
- Same statistical checks as Task 4.13 but for F&B signals
- Both configs load without validation errors after noise param updates

---

### 4.15 — Counter Rollover Testing Support

**Group:** D: Noise Calibration and Polish
**Files:** `src/factory_simulator/models/counter.py`, `config/factory.yaml`, `config/factory-foodbev.yaml`
**PRD refs:** `prd/10-data-quality-realism.md` (10.4)

Add configurable `rollover_value` to counter models:

- Default: `4294967295` (uint32 max) — realistic but takes 40 years to wrap
- Testing: set `rollover_value: 10000` in config for quick wrap testing
- When counter reaches `rollover_value`, wrap to 0

The `counter` signal model should already support this via the `rollover_value` param. Verify it works correctly and add specific test coverage.

Add a `counter_rollover` section to `DataQualityConfig` with per-signal rollover overrides.

**Tests:**
- Counter wraps at configured rollover value
- Counter wraps at uint32 max by default
- Rollover produces a step from N to 0 (not N to 1)
- Ground truth logs rollover events
- Config overrides per signal work

---

### 4.16 — Reproducibility Test and Integration

**Group:** D: Noise Calibration and Polish
**File:** `tests/integration/test_reproducibility.py`
**PRD refs:** `prd/appendix-f-implementation-phases.md` (Phase 4 exit criteria)

Create a reproducibility integration test:

1. Run both profiles for 1 simulated hour (at 100x in engine ticks) with seed=42
2. Record all signal values from the store at each tick
3. Run again with the same seed
4. Assert byte-identical output

Also create a final integration test that runs both profiles for 1 simulated day at high speed and verifies:
- All scenario types fire at least once
- No NaN or Infinity values
- Memory stable (RSS check at start and end)
- Ground truth log is well-formed JSONL
- Data quality injections appear in ground truth

**Tests:**
- Byte-identical output for same seed on same platform
- All packaging scenario types present in ground truth after 24h sim
- All F&B scenario types present in ground truth after 24h sim
- Memory usage does not grow unbounded
- No divergent values (NaN/Inf)

---

## Exit Criteria (PRD Appendix F)

- Run each profile for 7 days at 100x in batch mode (under 2 real hours)
- All scenario types fire at least once (including intermittent fault Phase 3 which requires batch mode)
- Anomaly patterns are detectable by threshold-based checks
- No divergent values
- Memory stable (RSS < 2x initial)
- Reproducibility test passes (byte-identical output for same seed on same platform)

## Dependencies

```
4.1 (Poisson scheduling) ← no deps
4.2 (Priority/conflict) ← 4.1
4.3 (Config models) ← no deps (can parallel with 4.1)
4.4 (Bearing wear) ← 4.1, 4.2, 4.3
4.5 (Micro-stops) ← 4.1, 4.2, 4.3
4.6 (Contextual anomalies) ← 4.1, 4.2, 4.3
4.7 (Intermittent faults) ← 4.1, 4.2, 4.3
4.8 (Comm drops) ← 4.3
4.9 (Sensor disconnect/stuck) ← 4.3
4.10 (Modbus exceptions/partial) ← 4.3
4.11 (Duplicates/timezone) ← 4.3
4.12 (DQ integration) ← 4.8, 4.9, 4.10, 4.11
4.13 (Noise calibration packaging) ← no deps
4.14 (Noise calibration F&B) ← no deps
4.15 (Counter rollover) ← 4.3
4.16 (Reproducibility + integration) ← all above
```
