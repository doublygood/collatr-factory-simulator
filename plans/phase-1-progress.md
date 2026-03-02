# Phase 1: Core Engine, Modbus, and Test Infrastructure - Progress

## Status: In Progress

## Tasks
- [x] 1.1: Configuration Models
- [x] 1.2: Simulation Clock
- [x] 1.3: Signal Value Store
- [x] 1.4: Signal Model Base + Noise Pipeline
- [x] 1.5: Steady State Model
- [x] 1.6: Sinusoidal Model
- [x] 1.7: First-Order Lag Model
- [x] 1.8: Ramp Model
- [x] 1.9: Random Walk Model
- [x] 1.10: Counter Model
- [x] 1.11: Depletion Model
- [x] 1.12: Correlated Follower Model
- [x] 1.13: State Machine Model
- [x] 1.14: Thermal Diffusion Model
- [x] 1.15: Bang-Bang Hysteresis + String Generator
- [ ] 1.16: Equipment Generator Base + Press Generator
- [ ] 1.17: Remaining Packaging Generators
- [ ] 1.18: Data Engine
- [ ] 1.19: Basic Scenarios
- [ ] 1.20: Modbus TCP Server + Integration Tests

## Notes

### Task 1.1: Configuration Models (completed)

**Files created:**
- `src/factory_simulator/config.py` -- Pydantic v2 models for full config schema
- `config/factory.yaml` -- Default packaging profile with all 47 signals
- `tests/unit/test_config.py` -- 69 tests covering validation, loading, env overrides

**Pydantic models implemented:**
- `FactoryInfo`, `SimulationConfig`, `ErrorInjectionConfig`
- `ModbusProtocolConfig`, `OpcuaProtocolConfig`, `MqttProtocolConfig`, `ProtocolsConfig`
- `SignalConfig` (extra="allow" for model-specific params), `EquipmentConfig` (extra="allow" for equipment-specific fields)
- 9 scenario config models (one per PRD scenario type): `JobChangoverConfig`, `WebBreakConfig`, `DryerDriftConfig`, `BearingWearConfig`, `InkViscosityExcursionConfig`, `RegistrationDriftConfig`, `UnplannedStopConfig`, `ShiftChangeConfig`, `ColdStartSpikeConfig`
- `ShiftOperatorConfig`, `ShiftsConfig`
- `FactoryConfig` (top-level root model)

**Key design decisions:**
- `SignalConfig` and `EquipmentConfig` use `extra="allow"` for forward compatibility. Equipment-specific fields (target_speed, schedule_offset_hours) and signal-specific extra fields are captured via `model_extra`. Model-specific parameters go in `params` dict.
- Environment variable overrides applied after YAML loading via `_apply_env_overrides()`. Maps SIM_* and MODBUS_*/OPCUA_*/MQTT_* env vars to nested config paths per PRD Section 6.4.
- Range pair validation: all [min, max] fields validated for correct ordering.
- Noise config: sigma >= 0, Student-t df >= 3, AR(1) phi in (-1, 1).
- Installed `types-PyYAML` for mypy strict mode compatibility.
- Installed package in editable mode (`pip install -e .`).

**Validation coverage:**
- Positive time_scale and tick_interval_ms
- Valid port numbers (1-65535)
- Valid byte_order (ABCD/CDAB), security_mode, QoS, buffer_overflow
- Probability fields in [0, 1]
- Range pair min <= max for all scenario configs
- All 47 signals present with correct register addresses per Appendix A

### Task 1.2: Simulation Clock (completed)

**Files created:**
- `src/factory_simulator/clock.py` -- SimulationClock class
- `tests/unit/test_clock.py` -- 30 tests

**SimulationClock implementation:**
- `tick()` advances sim_time by `(tick_interval_ms / 1000) * time_scale` seconds
- `dt` property returns the per-tick delta in seconds (used by all signal models)
- `elapsed_seconds()`, `sim_datetime()`, `sim_time_iso()` helpers
- `reset()` zeroes sim_time and tick_count
- `from_config(SimulationConfig)` factory method (duck-typed to avoid circular imports)

**Key design decisions:**
- Clock is purely deterministic: no asyncio, no wall-clock references. It ticks when told to tick (Rule 6).
- `dt` is constant across all ticks (function of config only), ensuring signal models produce identical output regardless of wall-clock speed.
- Start time defaults to `2024-01-15T06:00:00+00:00` (Monday morning shift start) if not configured.
- `from_config()` uses duck-typing (getattr) to accept SimulationConfig without importing it, avoiding circular dependency with config.py.

**Test coverage:**
- Construction and validation (positive tick_interval_ms, positive time_scale)
- Tick mechanics at 1x, 10x, 100x (single tick and multi-tick)
- Simulated time invariant: two clocks with same config produce identical sim_time after same ticks
- Time helpers: elapsed_seconds, sim_datetime, ISO format, timezone preservation
- Reset behaviour
- from_config factory with SimulationConfig
- Large runs: 1 hour at 100x (360 ticks), 1 day at 1000x (864 ticks)
- Floating-point accumulation over 100k ticks (< 1e-9 relative error)

### Task 1.3: Signal Value Store (completed)

**Files created:**
- `src/factory_simulator/store.py` -- SignalValue dataclass + SignalStore class
- `tests/unit/test_store.py` -- 40 tests

**SignalValue dataclass:**
- `signal_id` (str), `value` (float | str), `timestamp` (float), `quality` (str, default "good")
- Uses `@dataclass(slots=True)` for memory efficiency during rapid updates

**SignalStore implementation:**
- `set(signal_id, value, timestamp, quality)` -- creates or updates in place (reuses existing SignalValue object to avoid allocation)
- `get(signal_id) -> SignalValue | None` -- returns None for missing signals
- `get_value(signal_id, default) -> float | str` -- convenience accessor for just the value
- `get_all() -> dict[str, SignalValue]` -- returns internal dict directly (no copy) for protocol adapter performance
- `signal_ids() -> list[str]` -- sorted list of all registered IDs
- Container protocol: `__len__`, `__contains__`, `__iter__`, `clear()`
- Quality flag validation: rejects anything not in {"good", "uncertain", "bad"}

**Key design decisions:**
- No locks (Rule 9): single-writer (engine), multiple-reader (protocol adapters) in asyncio single-threaded model.
- `set()` mutates existing SignalValue in place rather than creating a new object each tick. This avoids GC pressure across 47-68 signals x thousands of ticks.
- `get_all()` returns the internal dict without copying for performance -- protocol adapters must not mutate it.
- Supports both float and string values (F&B profile has `mixer.batch_id` as string).
- `QUALITY_FLAGS` exported as a frozenset constant for use by other modules.

**Test coverage:**
- SignalValue construction with float, string, and all quality variants
- set/get round-trip for float and string values
- Quality flag validation (valid flags preserved, invalid rejected)
- Missing signal returns None / default
- Update in place: no duplicates, identity preserved, quality changes, type changes
- get_value with default for missing signals
- get_all returns all entries and reflects updates
- signal_ids returns sorted list
- Container protocol: len, contains, iter
- clear empties store and allows reuse
- Realistic scale: 47 packaging signals, 68 F&B signals, 1000 rapid update ticks

### Task 1.4: Signal Model Base + Noise Pipeline (completed)

**Files created:**
- `src/factory_simulator/models/__init__.py` -- package init, exports SignalModel, NoiseGenerator, CholeskyCorrelator
- `src/factory_simulator/models/base.py` -- SignalModel ABC
- `src/factory_simulator/models/noise.py` -- NoiseGenerator + CholeskyCorrelator
- `tests/unit/test_noise.py` -- 59 tests (property-based with Hypothesis)

**SignalModel ABC:**
- `__init__(params, rng)` -- stores model-specific params dict and numpy Generator
- `generate(sim_time, dt) -> float` -- abstract method, produces raw signal value
- `reset()` -- optional override for stateful models (no-op by default)

**NoiseGenerator implementation (PRD 4.2.11):**
- Three distributions: Gaussian, Student-t, AR(1)
- Gaussian: `sigma * rng.standard_normal()`
- Student-t: `sigma * rng.standard_t(df)` -- intentionally higher RMS per PRD variance note
- AR(1): `phi * prev + sigma * sqrt(1 - phi^2) * N(0,1)` -- maintains internal state, `sqrt(1-phi^2)` scaling preserves marginal variance at sigma^2
- Speed-dependent sigma: `effective_sigma = sigma_base + sigma_scale * |parent_value|`
- `sample(parent_value=None) -> float` -- draws one noise sample
- `effective_sigma(parent_value=None) -> float` -- computes sigma
- `reset()` -- clears AR(1) state
- `from_config()` factory maps config field names to constructor params

**CholeskyCorrelator implementation (PRD 4.3.1):**
- Validates correlation matrix: square, symmetric, unit diagonal, positive definite
- Computes lower-triangular Cholesky factor L at construction via `np.linalg.cholesky`
- `correlate(independent)` -- applies L to N independent N(0,1) samples
- `generate_correlated(rng, sigmas=None)` -- full pipeline: generate N(0,1), apply L, scale by sigma
- Pipeline order enforced: generate -> correlate -> scale (PRD 4.3.1 step order)

**Key design decisions:**
- NoiseGenerator is per-signal; distribution selection is at config level, not hardcoded in models
- CholeskyCorrelator is per-group (vibration axes, dryer zones, etc.)
- Speed-dependent sigma falls back to base sigma when no parent_value is provided or sigma_base is not configured
- AR(1) innovation scaling sqrt(1-phi^2) ensures marginal variance stays at sigma^2 regardless of phi
- No scipy dependency -- kurtosis computed with numpy in tests

**Test coverage (59 tests, property-based with Hypothesis):**
- Construction validation: sigma >= 0, valid distribution, Student-t df >= 3, AR(1) phi in (-1,1)
- Gaussian: mean ~0 over 10k samples, stddev ~sigma, scales with sigma (Hypothesis), excess kurtosis ~0
- Student-t: mean ~0, heavier tails (excess kurtosis > 1), df=3 extreme tails (kurtosis > 2), 29% higher RMS at df=5
- AR(1): mean ~0, marginal variance matches sigma, lag-1 autocorrelation matches phi (tested at 0.1, 0.7, 0.95), reset clears state
- Speed-dependent sigma: formula correctness, affects sample variance, non-negative (Hypothesis), fallback behaviour
- Determinism: same seed produces identical sequences for all three distributions
- from_config factory: maps noise_type/noise_df/noise_phi/sigma_base/sigma_scale correctly
- Cholesky construction: identity, valid R, rejects non-square/non-symmetric/non-unit-diagonal/non-positive-definite
- Cholesky correlation: vibration R matches empirically, dryer zone R matches, identity produces uncorrelated, unit variance preserved
- generate_correlated: with/without sigmas, sigma scaling preserves correlation but changes variance, deterministic
- SignalModel ABC: cannot instantiate, concrete subclass works, reset is no-op
- Package imports: all exports available

### Task 1.5: Steady State Model (completed)

**Files created:**
- `src/factory_simulator/models/steady_state.py` -- SteadyStateModel class
- `tests/unit/test_models/__init__.py` -- test package init
- `tests/unit/test_models/test_steady_state.py` -- 55 tests (property-based with Hypothesis)

**Files modified:**
- `src/factory_simulator/models/base.py` -- added `quantise()` and `clamp()` post-processing utilities
- `src/factory_simulator/models/__init__.py` -- exports SteadyStateModel, quantise, clamp

**SteadyStateModel implementation (PRD 4.2.1):**
- Core: `value = target + noise(0, sigma)`
- Within-regime drift: Ornstein-Uhlenbeck-like random walk with mean reversion. `drift_offset += drift_rate * N(0,1) * sqrt(dt) - reversion_rate * drift_offset * dt`. Clamped to `max_drift` (default 3% of |target|).
- Calibration drift: persistent linear bias. `calibration_bias += calibration_drift_rate * dt`. Does not revert.
- Accepts optional `NoiseGenerator` for noise injection (keeps distribution selection at config level per task 1.4 design)
- `reset()` clears drift_offset, calibration_bias, and noise state

**Post-processing utilities (PRD 4.2.13):**
- `quantise(value, resolution)` -- rounds to nearest multiple of resolution. Disabled when resolution is None or <= 0.
- `clamp(value, min_clamp, max_clamp)` -- enforces physical bounds. None means no bound.
- Both implemented once in `base.py`, not in every model. Applied by the engine after generate() + noise.

**Key design decisions:**
- `_float_param()` helper extracts float params from `dict[str, object]` with type-safe fallback, keeping mypy happy with strict mode.
- max_drift defaults to 3% of |target| with a 0.03 minimum floor for zero-target signals.
- calibration_drift_rate is in units per second (consistent with dt). Config loader should convert from per-hour if needed.
- Noise is injected via constructor, not created internally -- matches the task 1.4 design where distribution selection is at config level.

**Test coverage (55 tests):**
- Construction: default target, explicit target, drift defaults, max_drift calculation (3% default, zero target floor, explicit)
- Basic generation: target without noise across multiple ticks, negative target, zero target
- Noise: mean near target over 10k samples, stddev matches sigma, variation added, zero sigma produces clean signal
- Within-regime drift: disabled by default, accumulates over time, clamped to max_drift, affects output, reversion pulls back, slow over short time
- Calibration drift: disabled by default, accumulates linearly, affects output, does not revert, negative rate
- Reset: clears drift_offset, calibration_bias, and AR(1) noise state
- Determinism (Rule 13): same seed → identical sequences with and without drift, different seeds differ
- Quantisation: disabled (None/zero/negative), 0.1 resolution, 0.024 resolution, exact multiples, negative values, zero value, Hypothesis property (result is multiple of resolution)
- Clamp: no bounds, min only, max only, both bounds, at boundary, Hypothesis property (result within bounds)
- Property-based: output finite for arbitrary target/sigma, clamped output within bounds, determinism for any seed
- Full pipeline: generate → quantise → clamp, PRD ink pressure example (835 mbar, sigma 60, range 0-900), supply voltage (24V, sigma 0.1V)
- Package imports: SteadyStateModel, quantise, clamp all importable from models package

### Task 1.6: Sinusoidal Model (completed)

**Files created:**
- `src/factory_simulator/models/sinusoidal.py` -- SinusoidalModel class
- `tests/unit/test_models/test_sinusoidal.py` -- 35 tests (property-based with Hypothesis)

**Files modified:**
- `src/factory_simulator/models/__init__.py` -- exports SinusoidalModel

**SinusoidalModel implementation (PRD 4.2.2):**
- Core formula: `value = center + amplitude * sin(2 * pi * t / period + phase) + noise`
- Parameters: `center` (default 0.0), `amplitude` (default 1.0), `period` (default 86400.0 = 24h), `phase` (default 0.0 radians)
- Validates period > 0 at construction
- Accepts optional `NoiseGenerator` for noise injection (keeps distribution selection at config level per task 1.4 design)
- `reset()` clears noise state (AR(1) memory)
- No internal state beyond noise -- sinusoidal is a pure function of sim_time

**Key design decisions:**
- Same `_float_param()` helper pattern as SteadyStateModel for safe param extraction
- The sinusoidal model is stateless (no drift, no accumulation) -- output depends only on sim_time and noise
- Without noise, two models with different RNG seeds produce identical output (pure function of time)
- Phase offset in radians for maximum flexibility. PRD's humidity inversion uses phase=pi
- Period in seconds (not hours/minutes) to be consistent with sim_time units

**Test coverage (35 tests):**
- Construction: defaults, explicit params, invalid period (zero, negative)
- Basic generation: t=0, quarter/half/three-quarter/full period, negative/zero amplitude, negative center
- Phase offset: pi/2 shifts peak to t=0, pi inverts wave, humidity inverted phase example
- Periodicity: values repeat at t and t+period, short period (1s), long period (24h)
- Output range: without noise stays within [center-amplitude, center+amplitude], extremes reached
- Noise: mean near center over 100 periods, noise adds variation, zero sigma clean signal
- Reset: clears AR(1) state, no-op without noise
- Determinism (Rule 13): same seed → identical, different seeds differ, no noise always deterministic
- Property-based (Hypothesis): output finite, within bounds without noise, determinism any seed, periodic
- PRD examples: ambient humidity daily cycle (inverted phase), ambient temp daily base layer

### Task 1.7: First-Order Lag Model (completed)

**Files created:**
- `src/factory_simulator/models/first_order_lag.py` -- FirstOrderLagModel class
- `tests/unit/test_models/test_first_order_lag.py` -- 51 tests (property-based with Hypothesis)

**Files modified:**
- `src/factory_simulator/models/__init__.py` -- exports FirstOrderLagModel
- `tests/unit/test_models/test_steady_state.py` -- fixed flaky Hypothesis test (fp tolerance for large values)

**FirstOrderLagModel implementation (PRD 4.2.3):**
- Core formula: `value += (setpoint - value) * (1 - exp(-dt / tau)) + noise`
- Optional second-order underdamped response when `damping_ratio` < 1.0:
  `value = setpoint + A * exp(-zeta * omega_n * t) * sin(omega_d * t + phase)`
- omega_n = 1/tau, omega_d = omega_n * sqrt(1 - zeta^2)
- A = step_size / sqrt(1 - zeta^2), phase = arccos(zeta)
- Transient resets on each setpoint change. No stacking of transients (PRD requirement).
- `set_setpoint(new_setpoint)` for runtime setpoint changes (used by equipment generators)
- `reset()` restores initial value and restarts underdamped transient if applicable
- Transient auto-settles when envelope < 1e-9 * scale, switching to first-order lag branch

**Key design decisions:**
- Same `_float_param()` helper pattern as other signal models for safe param extraction
- `damping_ratio` validated in [0.1, 2.0] per PRD. Default 1.0 (critically damped = pure first-order lag).
- Underdamped closed-form trajectory avoids numerical drift from tick-by-tick accumulation
- `set_setpoint()` captures current value as starting point for new transient (mid-transient changes handled correctly)
- `initial_value` defaults to setpoint; if different, underdamped models start with a transient at construction
- `reset()` mirrors construction behaviour: restarts transient if initial_value != setpoint and damping < 1.0

**Test coverage (51 tests):**
- Construction: defaults, explicit params, initial_value defaults/explicit, invalid tau, invalid damping_ratio, boundary values
- First-order lag: at setpoint stays, converges to setpoint, monotonic from below/above, 1-tau (~63.2%), 5-tau (~99.3%), smaller tau faster, overdamped no overshoot, negative setpoint
- Setpoint changes: tracks new setpoint, multiple changes, no-op for same value, step down
- Underdamped: overshoot from below, undershoot from above, eventually settles, higher damping less overshoot, overshoot magnitude matches theory (~16.3% for zeta=0.5), mid-transient setpoint change, stable after settle, no transient when value=setpoint, new setpoint triggers new transient
- Noise: mean near setpoint after settling, adds variation, zero sigma clean signal
- Reset: restores initial_value, defaults to current setpoint, restarts underdamped transient, clears AR(1) state
- Determinism (Rule 13): same seed identical (first-order and underdamped), different seeds differ, no noise deterministic
- Property-based (Hypothesis): output finite, converges to setpoint, determinism any seed, underdamped overshoots, critically/overdamped no overshoot
- PRD examples: dryer temp zone (tau=60s, damping=0.6, overshoot verified), laminator nip temp (tau=45s, damping=0.7, less overshoot)

**Incidental fix:**
- Fixed pre-existing flaky Hypothesis test `test_quantised_is_multiple_of_resolution` in test_steady_state.py: widened fp tolerance from 1e-9 to 1e-6 for large value/small resolution combinations (732958/0.03 quotient ~24M)

### Task 1.8: Ramp Model (completed)

**Files created:**
- `src/factory_simulator/models/ramp.py` -- RampModel class
- `tests/unit/test_models/test_ramp.py` -- 50 tests (property-based with Hypothesis)

**Files modified:**
- `src/factory_simulator/models/__init__.py` -- exports RampModel

**RampModel implementation (PRD 4.2.4):**
- Smooth linear ramp: `value = start + (end - start) * (elapsed / duration)` when `steps=1`
- Stepped operator ramp when `steps > 1`:
  - Divides ramp range into N evenly-spaced step targets
  - Random dwell times per step drawn from `uniform(dwell_min, dwell_max)`
  - Dwell times compressed proportionally if total exceeds duration (hard cap)
  - Each step transition triggers overshoot: `overshoot_pct * step_size * exp(-t/decay_s)`
  - Overshoot direction follows ramp direction (positive for ramp up, negative for ramp down)
- After duration, value holds at end value (no overshoot)
- `start_ramp(start, end, duration)` for dynamic reconfiguration with fresh dwell times
- `reset()` restores start state but preserves existing step plan

**Key design decisions:**
- Default `steps=4` per PRD 4.2.4 parameter specification. Set `steps=1` for smooth ramp.
- Step plan pre-computed at construction: step targets, dwell times, transition times. Deterministic given same rng seed.
- Elapsed time tracked by accumulating dt, not using sim_time (consistent with other models).
- `start_ramp()` re-draws random dwell times for fresh ramp; `reset()` keeps existing plan for replay.
- Overshoot uses `step_size` (signed) not `abs(step_size)`, so direction is automatic for ramp-up vs ramp-down.
- `step_overshoot_decay_s` defaults to 7.0 (midpoint of PRD's "5-10 seconds" range).

**Test coverage (50 tests):**
- Construction: defaults (steps=4 per PRD), explicit params, stepped params, validation errors (duration, steps, decay, dwell_range)
- Smooth ramp: linear progression, reaches end at duration, holds after duration, complete flag, monotonic up/down, 25/50/75% progress, negative range
- Stepped ramp: reaches end at duration, step count visible (4 distinct levels), dwell times fit in duration, dwell compression, overshoot at step boundary, overshoot decays within step, overshoot direction for ramp down, two steps, many steps (10)
- Noise: adds variation, zero sigma clean signal
- Reset: restores start, preserves step plan, clears AR(1) noise state
- start_ramp(): new params, partial update, invalid duration, reaches new end
- Determinism (Rule 13): same seed identical (smooth and stepped), different seeds differ, no noise deterministic
- Property-based (Hypothesis): output finite, reaches end (smooth and stepped), determinism any seed, smooth ramp monotonic, dwell sum within duration
- PRD examples: press startup stepped (0→200 m/min, 180s, 4 steps), press shutdown smooth (200→0, 45s), overshoot magnitude (3% of step size), overshoot decay time constant verification

### Task 1.9: Random Walk Model (completed)

**Files created:**
- `src/factory_simulator/models/random_walk.py` -- RandomWalkModel class
- `tests/unit/test_models/test_random_walk.py` -- 40 tests (property-based with Hypothesis)

**Files modified:**
- `src/factory_simulator/models/__init__.py` -- exports RandomWalkModel

**RandomWalkModel implementation (PRD 4.2.5):**
- Core formula: `delta = drift_rate * N(0,1) - reversion_rate * (value - center); value += delta * dt`
- Mean reversion via Ornstein-Uhlenbeck-like discrete Euler step
- Physical bounds via `min_clamp` / `max_clamp` (applied to walk state, not just output)
- `set_center(new_center)` for runtime target changes (e.g. ink viscosity target during job changeover)
- Accepts optional `NoiseGenerator` as observation noise on top of the walk process
- `reset()` restores initial value and clears noise AR(1) state

**Key design decisions:**
- Same `_float_param()` helper pattern as other signal models for safe param extraction
- `initial_value` defaults to `center` if not specified
- Clamping applied to the walk state itself (not just output), so the walk cannot exceed physical bounds even before noise
- Observation noise is additive on top of the clamped walk value -- this means returned values can slightly exceed clamp bounds when observation noise is present (intentional: the clamp models physical limits of the process, noise models sensor measurement)
- `set_center()` provided for scenario use (PRD 5.2 job changeover changes ink viscosity target)
- `drift_rate` validated >= 0, `reversion_rate` validated >= 0

**Test coverage (40 tests):**
- Construction: defaults, explicit params, initial_value defaults/explicit, clamp bounds, validation errors (negative drift_rate, negative reversion_rate), zero drift/reversion allowed
- Basic generation: no drift stays at center, values vary with nonzero drift, mean near center over long run, negative center, zero center
- Mean reversion: strong reversion lower variance than weak, reversion pulls back from displacement, pure reversion exponential decay (matches exp(-rate*t) within 5%), zero reversion pure random walk
- Clamping: min only, max only, both bounds, no bounds by default
- Noise: adds variation, zero sigma clean, noise does not affect walk state
- set_center: changes reversion target, walk moves toward new center
- Reset: restores initial value, defaults to center, clears AR(1) noise state
- Determinism (Rule 13): same seed identical, different seeds differ, no drift deterministic across seeds, noise+walk same seed identical
- Property-based (Hypothesis): output always finite, determinism any seed, clamped output within bounds
- PRD examples: ink viscosity (center 25 cP, bounded 15-35), registration error (center 0, bounded -0.5 to 0.5), coder ink viscosity (sigma 0.3 cP)

### Task 1.10: Counter Model (completed)

**Files created:**
- `src/factory_simulator/models/counter.py` -- CounterModel class
- `tests/unit/test_models/test_counter.py` -- 56 tests (property-based with Hypothesis)

**Files modified:**
- `src/factory_simulator/models/__init__.py` -- exports CounterModel

**CounterModel implementation (PRD 4.2.6):**
- Core formula: `value += rate * speed * dt`
- `rate` in units of "increments per speed-unit per second" (validated >= 0)
- `set_speed(speed)` for runtime speed input (called by equipment generator before generate())
- Rollover: `value = value % rollover_value` when counter reaches configured rollover. Supports modulo for multiple wraps in a single tick. Accepts both `rollover_value` and `rollover` as config keys (config uses `rollover`).
- Reset on job change: `reset_counter()` zeros counter value. `reset_on_job_change` flag is informational for the scenario engine to know which counters to reset.
- Max before reset: auto-resets to zero when counter reaches `max_before_reset` threshold. Applied after rollover.
- `reset()` restores initial value and zeros speed.

**Key design decisions:**
- Same `_float_param()` helper pattern as other signal models for safe param extraction from `dict[str, object]`.
- Counter has no stochastic component -- output is purely deterministic regardless of RNG seed. The counter model receives an RNG via the SignalModel interface but does not use it.
- `set_speed()` follows the same pattern as `FirstOrderLagModel.set_setpoint()` and `RandomWalkModel.set_center()` -- equipment generators set external dependencies before calling generate().
- Rollover uses modulo (`%`) which correctly handles cases where a large increment would exceed rollover multiple times in a single tick.
- `reset_counter()` is separate from `reset()`: reset_counter zeros the value (job changeover), while reset restores the initial construction state (full model reset).
- `initial_value` validated >= 0 since counters don't have negative values.

**Test coverage (56 tests):**
- Construction: defaults, explicit params, rollover alias, rollover_value precedence, invalid rate/rollover/max_before_reset/initial_value, zero rate allowed
- Basic increment: zero speed no increment, constant speed linear increment, rate scaling, speed scaling, dt scaling, accumulation across ticks, zero rate, initial value offset
- Speed changes: set_speed, mid-run speed change, speed-to-zero pauses
- Rollover: no rollover by default, wraps to zero, preserves remainder (modulo), multiple wraps, PRD 999 wrapping, large uint32 rollover
- Max before reset: auto-resets, continues after reset, disabled by default
- Rollover + max_before_reset interaction
- Reset on job change: zeros value, continues counting, works regardless of flag
- Reset: restores initial value, zeros speed, defaults to zero
- Determinism (Rule 13): same seed same output, deterministic regardless of seed
- Time compression (Rule 6): same total at different tick rates, compressed run high count
- PRD examples: impression_count (rate=1.0), good_count (rate=0.97), waste_count (rate=0.03), good+waste=impression, ink_consumption_ml (rate=0.01), cumulative_kwh (rate=0.001)
- Property-based (Hypothesis): output always finite, never negative from zero, monotonically increasing, rollover keeps value below threshold, determinism any seed
- Package imports: CounterModel importable from models package, in __all__

### Task 1.11: Depletion Model (completed)

**Files created:**
- `src/factory_simulator/models/depletion.py` -- DepletionModel class
- `tests/unit/test_models/test_depletion.py` -- 60 tests (property-based with Hypothesis)

**Files modified:**
- `src/factory_simulator/models/__init__.py` -- exports DepletionModel

**DepletionModel implementation (PRD 4.2.7):**
- Core formula: `value -= consumption_rate * speed * dt`
- `set_speed(speed)` for runtime usage driver input (called by equipment generator before generate())
- Auto-refill: when value drops to or below `refill_threshold`, jumps to `refill_value`. Both must be configured for refill to activate.
- Manual refill: `refill(level)` for scenario-driven refill (e.g. reel changeover)
- Optional `NoiseGenerator` for observation noise (measurement noise on top of level)
- `reset()` restores initial value, zeros speed, clears noise state

**Key design decisions:**
- Same `_float_param()` helper pattern as other signal models for safe param extraction.
- Follows the same `set_speed()` pattern as CounterModel -- the equipment generator provides the usage driver (line speed for unwind diameter, print rate for ink level).
- Refill is disabled by default (both `refill_threshold` and `refill_value` must be set). This allows the same model to serve ink_level (with refill), unwind_diameter (no refill, reel changeover is a scenario), and nozzle_health (no refill, degrades over time).
- Validation: `refill_threshold < refill_value` prevents nonsensical config, `refill_threshold >= 0` and `refill_value > 0` enforce physical constraints.
- Noise is observation noise -- it does not affect the internal level state, only the returned value. This preserves deterministic depletion tracking while adding realistic measurement variation.
- Without refill or external clamping, the value can go negative (the engine's `clamp()` post-processing handles physical bounds).

**Test coverage (60 tests):**
- Construction: defaults, explicit params, validation errors (negative consumption_rate, negative refill_threshold, zero/negative refill_value, threshold >= value), partial refill config (threshold-only, value-only)
- Basic depletion: zero speed no depletion, linear depletion, rate scaling, speed scaling, dt scaling, zero consumption rate, can go negative
- Speed changes: set_speed, mid-run change, speed-to-zero pauses depletion
- Auto-refill: triggers at threshold, below threshold, multiple cycles, disabled when both None, disabled when only one set, different refill_value from initial, zero threshold
- Manual refill: to specified level, to refill_value, defaults to initial_value, continues depletion after refill
- Noise: adds variation, mean near level, zero sigma clean, noise does not affect internal level, AR(1) noise resets
- Reset: restores initial value, zeros speed, defaults to configured initial, clears noise state
- Determinism (Rule 13): same seed identical, no noise deterministic regardless of seed, noise same seed identical, noise different seeds differ
- Time compression (Rule 6): same depletion at different tick rates, compressed run
- PRD examples: ink_level (refill cycle), ink_level multiple refills, unwind_diameter (no refill), nozzle_health (slow degradation)
- Property-based (Hypothesis): output finite, monotonically decreasing without refill, determinism any seed, depletion formula exact, refill keeps value above threshold
- Package imports: DepletionModel importable from models package, in __all__

### Task 1.12: Correlated Follower Model (completed)

**Files created:**
- `src/factory_simulator/models/correlated.py` -- CorrelatedFollowerModel class
- `tests/unit/test_models/test_correlated.py` -- 57 tests (property-based with Hypothesis)

**Files modified:**
- `src/factory_simulator/models/__init__.py` -- exports CorrelatedFollowerModel

**CorrelatedFollowerModel implementation (PRD 4.2.8, 4.3.2):**
- Core formula: `value = base + gain_effective * lagged_parent + noise`
- Linear transform: `base` (intercept) + `gain` (slope) * parent_value
- Three lag modes:
  - `"none"` (default): no delay
  - `"fixed"`: constant delay via ring buffer. `lag_ticks = ceil(lag_seconds / tick_interval)`. Buffer sized at `lag_ticks + 1`.
  - `"transport"`: speed-dependent delay. `lag = distance_m / (speed_m_per_min / 60)`. Ring buffer sized at `2 * max_lag_at_min_speed + 1` (PRD 4.2.8). Zero speed freezes output.
- Time-varying covariance (PRD 4.3.2): gain drifts via multiplicative random walk on log scale.
  `log_drift += volatility * N(0,1) * sqrt(dt) - reversion * log_drift * dt`
  `gain_effective = gain_nominal * exp(log_drift)`
  Ensures gain stays positive. Mean-reversion pulls toward nominal.
- `set_parent_value(value)` called by equipment generator before generate()
- `set_speed(speed)` for transport lag speed reference
- `reset()` clears drift, buffer, parent, speed, and noise state

**Ring buffer design:**
- Write-at-head, read-from-behind pattern: write current parent at `_buffer_pos`, read from `(_buffer_pos - lag_ticks) % buffer_size`.
- Buffer size is always `lag_ticks + 1` (fixed) or `2 * max_lag_ticks + 1` (transport) to avoid read/write collision.
- For transport lag: lag_ticks computed each tick from current speed. Clamped to [0, buffer_size-1].

**Key design decisions:**
- Same `_float_param()` helper pattern as other signal models.
- `set_parent_value()` follows the same external-input pattern as `CounterModel.set_speed()` and `FirstOrderLagModel.set_setpoint()`.
- Transport lag at zero speed: output freezes at its last computed value (PRD 4.2.8 requirement). The buffer still advances but reads return the frozen parent contribution.
- Gain drift uses the log-normal form from PRD 4.3.2: `exp(log_drift)` ensures the effective gain is always positive. Mean reversion operates in log space.
- `gain_drift_volatility` defaults to 0.0 (disabled) per PRD: "Set to 0 (the default) for a fixed gain."
- Noise injection via constructor, consistent with all other models.

**Test coverage (57 tests):**
- Construction: defaults, explicit params, fixed lag sizing, transport lag sizing, gain drift params, validation errors (invalid lag_mode, negative lag_seconds, zero distance, zero min_speed, zero tick_interval, negative volatility, negative reversion)
- Linear transform: identity, gain scaling, base offset, full transform, negative gain (inverse), zero parent, tracking parent changes, PRD motor current example, PRD gear ratio example
- Noise: adds variation, zero sigma clean, mean near transform over 10k samples
- Fixed lag: delays output by lag_seconds, preserves signal shape (ramp), buffer size, minimum buffer
- Transport lag: varies with speed, zero speed freezes output, buffer sizing (2x max), PRD press-to-laminator example
- Gain drift: no drift by default, causes variation, starts at 1.0, mean reverts, stays positive (exp form), PRD motor current drift example
- Reset: clears drift, parent, speed, buffer, AR(1) noise state
- Determinism (Rule 13): same seed identical, different seeds differ, no drift deterministic any seed, noise same seed identical, drift same seed identical, fixed lag deterministic
- Time compression (Rule 6): same output at different tick rates
- Property-based (Hypothesis): output always finite, linear transform exact without noise/drift, determinism any seed, gain drift stays positive, positive gain/parent produces positive output
- Package imports: CorrelatedFollowerModel importable from models package, in __all__

### Task 1.13: State Machine Model (completed)

**Files created:**
- `src/factory_simulator/models/state.py` -- StateMachineModel class
- `tests/unit/test_models/test_state.py` -- 74 tests (property-based with Hypothesis)

**Files modified:**
- `src/factory_simulator/models/__init__.py` -- exports StateMachineModel

**StateMachineModel implementation (PRD 4.2.9):**
- Discrete state transitions between named states with numeric values
- Three trigger types:
  - **timer**: Draws duration from `uniform(min_duration, max_duration)` on state entry. Fires when `time_in_state >= drawn_duration`.
  - **probability**: Per-second rate. Each tick: `p_tick = min(probability * dt, 1.0)`. Forced at `max_duration` if set.
  - **condition**: Fires when named external condition is `True`. Forced at `max_duration` if set.
- `min_duration` gate on all trigger types -- no transition before min_duration elapsed
- `max_duration` forced transition on probability and condition triggers
- Priority: first matching transition in list order wins (one transition per tick)
- `set_condition(name, value)` for external condition-based triggers (e.g., coder follows press state)
- `get_condition(name)` returns current condition value (False if unset)
- `force_state(state_name)` for scenario-driven transitions (bypasses all checks)
- `state_changed` property indicates whether state changed on last `generate()` call
- `reset()` restores initial state, clears conditions and time_in_state

**Internal data structures:**
- `_StateDefinition(name, value)` dataclass with slots for memory efficiency
- `_TransitionDefinition(from_state, to_state, trigger, probability, min_duration, max_duration, condition)` dataclass
- `_timer_durations: dict[int, float]` -- maps transition index to drawn duration, redrawn on state entry

**Key design decisions:**
- `probability` is a per-second rate, making the model time-scale invariant (Rule 6). Per-tick probability = `probability * dt`.
- Timer durations redrawn from RNG on each state entry -- supports competing timers from same state (first to expire wins).
- No noise -- state machine is a discrete model. The generate() return value is the numeric state value (float).
- Conditions are externally managed by the equipment generator, not by the state machine itself. This keeps the model decoupled from the signal store.
- Self-transitions are allowed (resets time_in_state and redraws timers).
- Only one transition fires per tick. Even with immediate transitions (duration 0), cascading through multiple states takes multiple ticks.

**Validation:**
- States must be a non-empty list; no duplicates allowed
- Transitions validated: known from/to states, valid trigger type, non-negative probability/durations
- max_duration >= min_duration when max_duration > 0
- Condition triggers require a condition name

**Test coverage (74 tests):**
- Construction: minimal, defaults, explicit initial_state, error cases (empty/non-list states, duplicates, unknown states, invalid trigger, negative probability/durations, max < min, missing condition name, unknown initial_state), no transitions stays, many states, zero probability, self-transition
- Timer transitions: fixed duration, random duration within range, respects min_duration, competing timers (shorter wins), redrawn on state entry, zero duration fires first tick, state_changed flag, only one transition per tick
- Probability transitions: fires eventually, respects min_duration, forced at max_duration, zero probability never fires, higher rate fires sooner, time-scale invariant (Rule 6)
- Condition transitions: fires when true, no fire when false, cleared condition prevents fire, respects min_duration, forced at max_duration, multiple conditions, get_condition default/set
- Force state: changes state, unknown state error, resets time_in_state, draws new timers, force same state resets timer
- Properties: current_state/value, time_in_state accumulates, state_names ordered, state_changed initially false, generate returns value
- Reset: restores initial state, clears conditions, clears time_in_state, clears state_changed, allows replaying
- Determinism (Rule 13): same seed same timer/probability sequences, different seeds differ, condition-only always deterministic
- PRD examples: press 6 states (values 0-5), Setup->Running timer (10-30 min), coder gutter fault MTBF (500h probability), coder nozzle health degradation (timer chain), coder follows press via conditions, unplanned stop scenario (Running->Fault->Setup->Running)
- Property-based (Hypothesis): output always valid state value, determinism any seed, min_duration always respected, timer fires within max_duration, output always finite
- Package imports: importable from models package, in __all__

### Task 1.14: Thermal Diffusion Model (completed)

**Files created:**
- `src/factory_simulator/models/thermal_diffusion.py` -- ThermalDiffusionModel class
- `tests/unit/test_models/test_thermal_diffusion.py` -- 62 tests (property-based with Hypothesis)

**Files modified:**
- `src/factory_simulator/models/__init__.py` -- exports ThermalDiffusionModel

**ThermalDiffusionModel implementation (PRD 4.2.10):**
- Core formula: `T(t) = T_oven - (T_oven - T_initial) * SUM C_n * exp(-decay_n * t)`
- Coefficients: `C_n = 8 / ((2n+1)^2 * pi^2)` (PRD Table values verified: n=0 -> 0.8106, n=1 -> 0.0901, n=2 -> 0.0324)
- Decay rates: `decay_n = (2n+1)^2 * pi^2 * alpha / (4 * L^2)` where L is half-thickness
- Dynamic term count: adds terms until `|T(0) - T_initial| <= 1.0 C` (PRD convergence requirement)
- Typical terms: 36 for T_oven=180, T_initial=4 (176C difference); ~5 for small differences
- `set_oven_temp(T)` for runtime oven temperature changes (recomputes terms, preserves elapsed time)
- `restart(T_initial, T_oven)` for new product cycle (resets elapsed, optionally updates temperatures)
- `reset()` restores elapsed to 0, clears noise state
- Accepts optional `NoiseGenerator` for measurement noise (PRD sigma=0.3)

**Key design decisions:**
- Same `_float_param()` helper pattern as other signal models for safe param extraction.
- **4*L^2 correction:** The PRD formula writes `L^2` in the decay denominator but defines L as "product half-thickness". The standard Fourier solution for a slab with half-thickness L uses `4*L^2`. With `L^2` the model reaches 72C in ~2 minutes (physically unrealistic); with `4*L^2` it reaches 72C in ~8.7 minutes. The PRD says "approximately 15-20 minutes" which describes real center-point temperature (the formula is volume-averaged, which is faster). The 4*L^2 form produces physically reasonable behavior.
- Elapsed time accumulated via `dt` (not sim_time), consistent with other models (RampModel, etc.).
- No internal noise -- noise is injected via constructor, same pattern as all other models.
- Safety limit of 500 terms in the Fourier expansion (more than enough; even 176C difference only needs 36 terms).
- Supports both heating (T_oven > T_initial) and cooling (T_oven < T_initial) cases.

**Test coverage (62 tests):**
- Construction: defaults, explicit params, custom params, validation errors (L=0, L<0, alpha=0, alpha<0)
- Convergence (PRD 4.2.10): large difference 176C (T(0) within 1C, 15+ terms), small difference 50C (<20 terms), tiny difference 5C (<5 terms), equal temperatures (1 term), cooling convergence
- PRD coefficient verification: three-term sum = 0.9331, individual C_n values match table, T(0) with 3 terms = 15.8C
- Temperature evolution: approaches T_oven, monotonic heating/cooling, equal temps constant, early values near T_initial, asymptotic approach (rate decreases), never exceeds T_oven (heating), never below T_oven (cooling)
- Physical correctness: reaches 72C in ~8-9 min (PRD ready meal), thinner product faster, higher diffusivity faster, higher oven temp faster
- Noise: adds variation, zero sigma clean, mean near theoretical at equilibrium, AR(1) noise resets
- Reset/Restart: clears elapsed, produces same sequence, restart updates params, recomputes terms
- set_oven_temp: changes T_oven, preserves elapsed, recomputes terms, affects subsequent values
- Determinism (Rule 13): same seed identical, different seeds same without noise, noise same seed identical, noise different seeds differ
- Time compression (Rule 6): same output at different tick rates, elapsed matches total dt
- Edge cases: very small alpha (slow), very large alpha (fast), negative T_initial, very thin/thick product
- Property-based (Hypothesis): output always finite, determinism any seed, convergence within 1C, monotonic for any heating/cooling, bounded between T_initial and T_oven
- Package imports: importable from models package, in __all__

### Task 1.15: Bang-Bang Hysteresis + String Generator (completed)

**Files created:**
- `src/factory_simulator/models/bang_bang.py` -- BangBangModel class
- `src/factory_simulator/models/string_generator.py` -- StringGeneratorModel class
- `tests/unit/test_models/test_bang_bang.py` -- 54 tests (property-based with Hypothesis)
- `tests/unit/test_models/test_string_generator.py` -- 35 tests

**Files modified:**
- `src/factory_simulator/models/__init__.py` -- exports BangBangModel, StringGeneratorModel

**BangBangModel implementation (PRD 4.2.12):**
- On/off controller with hysteresis dead band for chiller compressor simulation
- Transition logic: OFF->ON when `pv > setpoint + dead_band_high`; ON->OFF when `pv < setpoint - dead_band_low`
- When ON: `pv -= cooling_rate * dt / 60` (rates in C/min, dt in seconds)
- When OFF: `pv += heat_gain_rate * dt / 60`
- Produces characteristic sawtooth temperature pattern
- `compressor_on` property exposes binary state for equipment generator to write to coil signal
- `set_setpoint()` for runtime target changes
- `add_disturbance(delta)` for door-open events and external heat loads
- Noise is observation noise (does not affect internal PV state)
- `reset()` restores initial temperature and compressor state

**StringGeneratorModel implementation (PRD 4.2.14):**
- Does NOT extend SignalModel (produces `str`, not `float`)
- Assembles batch IDs from template: `"{date:%y%m%d}-{line}-{seq:03d}"`
- `new_batch()` increments sequence counter (called by equipment generator on batch transitions)
- Automatic sequence reset at configured time of day (default midnight)
- Reset boundary tracking uses `_compute_reset_boundary()` initialized at construction time to correctly detect day crossings
- Supports arbitrary format templates with `{date}`, `{line}`, `{seq}` placeholders

**Key design decisions:**
- BangBangModel follows same `_float_param()` helper and noise injection patterns as all other signal models
- BangBangModel rates are in C/min per PRD but internally converted to C/s via `dt/60`
- StringGeneratorModel is a separate class (not a SignalModel subclass) since it produces strings. The equipment generator handles it differently from numeric models.
- StringGeneratorModel midnight reset boundary is computed from start_time at construction, not lazily on first generate(). This ensures day crossings are always detected correctly regardless of when the first generate() is called.
- Both models use sim_time (Rule 6), never wall clock

**Test coverage (54 bang-bang + 35 string = 89 tests):**
- Bang-Bang: construction (defaults, explicit, initial_temp, initial_state, validation errors), sawtooth behaviour (heat gain when off, cooling when on, upper/lower threshold switching, full cycle, oscillation range), cycle timing (PRD ~8-12 min, cooling/heating phase duration), asymmetric dead band, setpoint changes, disturbance (warming/cooling, triggers compressor, PRD door event), noise (variation, zero sigma, state isolation, mean), negative setpoint (freezer), reset, determinism (Rule 13), time compression (Rule 6), PRD examples (chiller config, compressor state), edge cases (small/large dt, exact threshold), property-based (Hypothesis: finite output, determinism, boolean state)
- String Generator: construction (defaults, explicit, datetime start, invalid reset_at), basic generation (default format, PRD example, sequence increments, custom template, date changes), midnight reset (at midnight, once per day, custom time, multiple day crossings), new_batch, reset (sequence, value, midnight tracking), value property, template variations, edge cases (zero sim_time, large sim_time, high sequence, format widening, naive timezone), package imports
