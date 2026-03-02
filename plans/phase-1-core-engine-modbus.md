# Phase 1: Core Engine, Modbus, and Test Infrastructure

**Timeline:** Weeks 1-3 (after Phase 0 spikes)
**Goal:** Simulator starts, generates all 47 packaging signals, serves them over Modbus TCP. Test infrastructure established from day one.

## Overview

Phase 1 builds the simulator's foundation: configuration loading, simulation clock, signal value store, all 12 signal model types, all 7 packaging equipment generators (47 signals total), the Cholesky correlation pipeline, noise distributions, basic scenario support, and the Modbus TCP server with the full packaging register map.

By the end of Phase 1, a pymodbus client can connect to the simulator and read realistic data from every holding register, input register, coil, and discrete input defined in Appendix A for the packaging profile.

## PRD References

Read these sections before starting any task:

- **Section 2** (`prd/02-simulated-factory-layout.md`): Packaging equipment, 47 signals, signal models, ranges
- **Section 4** (`prd/04-data-generation-engine.md`): All 12 signal models, noise distributions, Cholesky pipeline, time compression
- **Section 5** (`prd/05-scenario-system.md`): Job changeover (5.2), shift change (5.9), unplanned stop (5.8)
- **Section 6** (`prd/06-configuration.md`): YAML config structure, Pydantic validation, Docker Compose
- **Section 8** (`prd/08-architecture.md`): Component diagram, data flow, concurrency model, EquipmentGenerator interface
- **Section 13** (`prd/13-test-strategy.md`): Test philosophy, test pyramid, property-based testing guidance
- **Appendix A** (`prd/appendix-a-modbus-register-map.md`): Complete register map
- **Appendix E** (`prd/appendix-e-project-structure.md`): Directory layout

## Task Breakdown

Phase 1 is broken into 20 tasks across 5 groups. Tasks within a group are ordered by dependency. Tasks across groups are largely independent after the foundational tasks (1.1-1.4).

### Group A: Foundation (Tasks 1.1-1.4)

These build the core infrastructure everything else depends on.

**Task 1.1: Configuration Models**
- Create `src/factory_simulator/config.py`
- Pydantic v2 models for: SimulationConfig, ModbusProtocolConfig, OpcuaProtocolConfig, MqttProtocolConfig, ProtocolsConfig, SignalConfig, EquipmentConfig, ScenarioConfig, ShiftConfig, FactoryConfig (top-level)
- YAML loader: load file, validate against Pydantic model, return typed config
- Environment variable overrides: SIM_TIME_SCALE, SIM_RANDOM_SEED, SIM_LOG_LEVEL, MODBUS_ENABLED, MODBUS_PORT, etc. (Section 6.4)
- Validation rules: no negative sigma, no negative time_scale, min <= max for all range pairs, Student-t df >= 3
- Create `config/factory.yaml` with packaging profile defaults matching Section 6.2
- Tests: valid config loads, invalid configs rejected with clear errors, env vars override file values
- PRD: Section 6

**Task 1.2: Simulation Clock**
- Create `src/factory_simulator/clock.py`
- SimulationClock class: tick_interval_ms, time_scale, current_sim_time, start_time
- `tick()` method advances sim time by `tick_interval_ms * time_scale` milliseconds
- `elapsed_seconds()`, `sim_time_iso()` helpers
- Simulated time invariant (Rule 6): clock state is deterministic regardless of wall-clock speed
- Tests: tick advances correctly, time_scale 1x/10x/100x produce correct simulated elapsed time, ISO format output
- PRD: Section 4.1, 4.4

**Task 1.3: Signal Value Store**
- Create `src/factory_simulator/store.py`
- SignalStore class: stores current value + metadata for all signals
- Signal entry: signal_id (str), value (float | str), timestamp (float, sim time), quality (str: "good"/"uncertain"/"bad")
- `set(signal_id, value, timestamp, quality)` and `get(signal_id) -> SignalValue`
- `get_all() -> dict[str, SignalValue]` for protocol adapters
- No locks (Rule 9 -- single writer, asyncio single-threaded)
- Support float and string values (mixer.batch_id is a string in F&B profile)
- Tests: set/get round-trip, missing key returns None or raises, quality flag preserved, string values work
- PRD: Section 8.2, 8.3

**Task 1.4: Signal Model Base + Noise Pipeline**
- Create `src/factory_simulator/models/__init__.py`, `src/factory_simulator/models/base.py`
- SignalModel ABC with: `configure(params)`, `generate(sim_time, dt, rng) -> float`
- NoiseGenerator class with three distributions:
  - Gaussian: `sigma * rng.standard_normal()`
  - Student-t: `sigma * rng.standard_t(df)` (Section 4.2.11 variance note: intentionally higher RMS)
  - AR(1): `phi * prev + sigma * sqrt(1 - phi^2) * rng.standard_normal()` (maintains state)
- Speed-dependent sigma: `effective_sigma = sigma_base + sigma_scale * abs(parent_value)`
- Cholesky correlation pipeline (Section 4.3.1):
  1. Generate N independent N(0,1) samples
  2. Apply Cholesky factor L: `noise_correlated = L @ noise_independent`
  3. Scale by effective sigma per signal
- numpy.random.Generator with SeedSequence (Rule 13)
- Tests (property-based with Hypothesis):
  - Gaussian noise has mean ~0 and stddev ~sigma over N=10000 samples
  - Student-t has heavier tails than Gaussian (kurtosis > 3)
  - AR(1) autocorrelation at lag-1 matches phi within tolerance
  - Cholesky pipeline produces correct correlations over N=10000 samples
  - Speed-dependent sigma scales correctly
  - Deterministic with same seed
- PRD: Section 4.2.11, 4.3.1

### Group B: Signal Models (Tasks 1.5-1.12)

All 12 signal model types. Each task produces one model + tests.

**Task 1.5: Steady State Model**
- Create `src/factory_simulator/models/steady_state.py`
- `value = target + noise(0, sigma)`
- Optional within-regime drift (Section 4.2.1):
  `drift_offset += drift_rate * noise(0,1) * sqrt(dt) - reversion_rate * drift_offset * dt`
- Optional long-term calibration drift: `calibration_bias += calibration_drift_rate * dt`
- Optional sensor quantisation (Section 4.2.13): `round(value / resolution) * resolution`
- Clamping: min_clamp, max_clamp
- Tests (Hypothesis): output within clamp range, drift stays bounded by max_drift, quantisation snaps to grid, deterministic with seed
- PRD: Section 4.2.1, 4.2.13

**Task 1.6: Sinusoidal Model**
- Create `src/factory_simulator/models/sinusoidal.py`
- `value = center + amplitude * sin(2 * pi * t / period + phase) + noise(0, sigma)`
- Tests: output bounded by center +/- amplitude +/- noise, correct period detection over many ticks, deterministic
- PRD: Section 4.2.2

**Task 1.7: First-Order Lag Model**
- Create `src/factory_simulator/models/first_order_lag.py`
- `value = value + (setpoint - value) * (1 - exp(-dt / tau)) + noise(0, sigma)`
- Optional second-order response when damping_ratio < 1.0 (Section 4.2.3):
  `value = setpoint + A * exp(-zeta * omega_n * t) * sin(omega_d * t + phase) + noise`
- Transient reset on setpoint change. No stacking of transients.
- Tests (Hypothesis): converges to setpoint after many ticks, underdamped response overshoots, overdamped does not, deterministic
- PRD: Section 4.2.3

**Task 1.8: Ramp Model**
- Create `src/factory_simulator/models/ramp.py`
- Base: `value = start + (end - start) * (elapsed / duration) + noise(0, sigma)`
- Optional step quantisation (Section 4.2.4): N steps, overshoot at step boundary, dwell time per step, total duration hard cap
- Tests: reaches end value at duration, smooth ramp when steps=1, stepped ramp has correct number of steps, dwell times fit within duration, overshoot decays
- PRD: Section 4.2.4

**Task 1.9: Random Walk Model**
- Create `src/factory_simulator/models/random_walk.py`
- `delta = drift_rate * rng.standard_normal() - reversion_rate * (value - center); value += delta * dt`
- Clamping: min_clamp, max_clamp
- Tests (Hypothesis): stays within clamp bounds, mean-reverts toward center (average value near center over long runs), deterministic
- PRD: Section 4.2.5

**Task 1.10: Counter Model**
- Create `src/factory_simulator/models/counter.py`
- `value = value + rate * line_speed * dt`
- Rollover at configured maximum
- Optional reset_on_job_change flag
- max_before_reset option
- Tests: increments monotonically, wraps at rollover, resets on job change when configured, deterministic
- PRD: Section 4.2.6

**Task 1.11: Depletion Model**
- Create `src/factory_simulator/models/depletion.py`
- `value = value - consumption_rate * usage_delta`
- Refill at threshold: jump to refill_value
- Tests: depletes over time, refill triggers at threshold, deterministic
- PRD: Section 4.2.7

**Task 1.12: Correlated Follower Model**
- Create `src/factory_simulator/models/correlated.py`
- `value = f(parent_value) + noise(0, sigma)` (linear: base + factor * parent)
- Transport lag with ring buffer (Section 4.2.8):
  - Fixed mode: constant delay
  - Transport mode: `lag = distance_m / (speed_m_per_min / 60)`, ring buffer sized at 2x max lag at min speed
- Time-varying covariance (Section 4.3.2): gain drift via multiplicative random walk on log scale
- Tests: output tracks parent linearly, transport lag delays correctly, gain drift stays bounded, zero speed freezes downstream, deterministic
- PRD: Section 4.2.8, 4.3.2

### Group C: Remaining Models + State Machine (Tasks 1.13-1.15)

**Task 1.13: State Machine Model**
- Create `src/factory_simulator/models/state.py`
- Discrete state transitions based on rules and probabilities
- States have min_duration and max_duration
- Transition triggers: timer, condition, probability
- Tests: valid transitions fire, invalid transitions rejected, duration constraints respected, deterministic
- PRD: Section 4.2.9

**Task 1.14: Thermal Diffusion Model**
- Create `src/factory_simulator/models/thermal_diffusion.py`
- Fourier series: `T(t) = T_oven - (T_oven - T_initial) * SUM[C_n * exp(-(2n+1)^2 * pi^2 * alpha * t / L^2)]`
- Convergence: add terms until T(0) within 1C of T_initial
- Tests: S-curve shape (slow start, rapid middle, slow approach), T(0) ~= T_initial, T(inf) ~= T_oven, correct food safety time at 72C for typical meat product, deterministic
- PRD: Section 4.2.10

**Task 1.15: Bang-Bang Hysteresis + String Generator**
- Create `src/factory_simulator/models/bang_bang.py`
- On/off controller: switches at setpoint +/- dead band, cooling/heat gain rates
- Create `src/factory_simulator/models/string_generator.py`
- Template-based string generation with date, line_id, sequence number, midnight reset
- Tests (bang_bang): oscillates between bounds, cycle time reasonable, state toggles at thresholds
- Tests (string_generator): format matches template, sequence increments, midnight reset
- PRD: Section 4.2.12, 4.2.14

### Group D: Equipment Generators + Engine (Tasks 1.16-1.19)

**Task 1.16: Equipment Generator Base + Press Generator**
- Create `src/factory_simulator/generators/base.py` with EquipmentGenerator ABC (Section 8.4)
- Create `src/factory_simulator/generators/press.py`
- PressGenerator: 21 signals, state machine (Off/Setup/Running/Idle/Fault/Maintenance)
- Wire signal models to press signals: line_speed (ramp), web_tension (correlated follower), dryer temps (first_order_lag), counters, etc.
- State cascade: Running activates counters, speed ramp, correlations
- Tests: all 21 signal IDs produced, state transitions cascade correctly, Running state increments counters, Fault state zeroes speed
- PRD: Section 2.2, 4.3

**Task 1.17: Remaining Packaging Generators**
- Create `src/factory_simulator/generators/laminator.py` (5 signals)
- Create `src/factory_simulator/generators/slitter.py` (3 signals, scheduled operation)
- Create `src/factory_simulator/generators/coder.py` (11 signals, follows press state)
- Create `src/factory_simulator/generators/environment.py` (2 signals, composite model)
- Create `src/factory_simulator/generators/energy.py` (2 signals, correlated with press)
- Create `src/factory_simulator/generators/vibration.py` (3 signals, Cholesky-correlated)
- Tests: each generator produces correct signal IDs, signal values within expected ranges for known states
- PRD: Section 2.3-2.9

**Task 1.18: Data Engine**
- Create `src/factory_simulator/engine/data_engine.py`
- Create `src/factory_simulator/engine/__init__.py`
- DataEngine class: owns clock, store, generators, correlation pipeline
- `tick()` method: advance clock, update all generators, write to store (atomic -- no await mid-tick, Rule 8)
- Signal sample rate enforcement: only generate when interval elapsed
- Profile manager: selects packaging generators for now
- Tests: engine runs N ticks without error, all 47 signals in store after ticks, deterministic output with seed, atomic tick (no partial state)
- PRD: Section 8.2, 8.3

**Task 1.19: Basic Scenarios**
- Create `src/factory_simulator/engine/scenario_engine.py`
- Create `src/factory_simulator/scenarios/job_changeover.py`
- Create `src/factory_simulator/scenarios/shift_change.py`
- Create `src/factory_simulator/scenarios/unplanned_stop.py`
- ScenarioEngine: schedules and evaluates scenarios per tick
- Job changeover: ramp down, setup pause, optional setpoint change, ramp up, waste spike
- Shift change: operator speed/waste bias changes, brief speed adjustment
- Unplanned stop: immediate machine_state to Fault, recovery after duration
- Tests: scenarios fire at scheduled times, state transitions cascade correctly, scenario end restores normal operation
- PRD: Section 5.2, 5.8, 5.9

### Group E: Modbus Server + Integration (Task 1.20)

**Task 1.20: Modbus TCP Server + Integration Tests**
- Create `src/factory_simulator/protocols/modbus_server.py`
- ModbusServer adapter: reads from SignalStore, encodes to Modbus registers per Appendix A
- Full packaging register map: HR (all float32/uint32/uint16), IR (int16 x10 temps, float32 energy), coils, discrete inputs
- FC06 rejection on float32 registers (from spike patterns)
- Max 125 register limit (from spike patterns)
- Integration test: start engine + Modbus server, connect pymodbus client, read every register address from Appendix A, verify values within expected ranges and correct encoding
- Tests: float32 ABCD encoding, uint32 encoding, int16 x10 scaling, coil state reflects machine_state, discrete inputs reflect equipment state
- PRD: Appendix A, Section 3 (protocol endpoints)

## Exit Criteria

From PRD Appendix F:
1. CollatrEdge connects via Modbus TCP and collects data from all holding registers, input registers, coils, and discrete inputs for 1 hour (simulated).
2. All 47 packaging signals produce values within expected ranges.
3. Counters increment. State transitions occur.
4. All unit and integration tests pass.
5. CI pipeline (`ruff check && mypy src && pytest`) runs under 5 minutes.

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| 12 signal models is a lot of code | Each model is small and independent. Property-based tests catch edge cases early. |
| Equipment generators have complex cascading state | Press generator is the hardest. Do it first (Task 1.16). Others are simpler. |
| Modbus register map is large | Use Appendix A as a checklist. Integration test verifies every address. |
| Configuration schema is deep | Start with minimal viable config in Task 1.1. Generators add their own config sections. |
| Cholesky pipeline + noise is mathematically subtle | Implement in isolation (Task 1.4) with property-based tests before any generator touches it. |

## Notes for Implementation Agent

- All signal models use `numpy.random.Generator`, never `random` module (Rule 13).
- All time references use sim_time from the clock, never wall clock (Rule 6).
- No locks, no mutexes (Rule 9). The store is single-writer.
- Each task should produce ~1 source module + ~1 test module. Some tasks produce multiple small modules.
- Run `ruff check src tests && mypy src && pytest` after every change. All must pass.
- Commit format: `phase-1: <what> (task 1.X)`
