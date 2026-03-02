# Phase 1: Core Engine, Modbus, and Test Infrastructure - Progress

## Status: In Progress

## Tasks
- [x] 1.1: Configuration Models
- [x] 1.2: Simulation Clock
- [x] 1.3: Signal Value Store
- [x] 1.4: Signal Model Base + Noise Pipeline
- [x] 1.5: Steady State Model
- [ ] 1.6: Sinusoidal Model
- [ ] 1.7: First-Order Lag Model
- [ ] 1.8: Ramp Model
- [ ] 1.9: Random Walk Model
- [ ] 1.10: Counter Model
- [ ] 1.11: Depletion Model
- [ ] 1.12: Correlated Follower Model
- [ ] 1.13: State Machine Model
- [ ] 1.14: Thermal Diffusion Model
- [ ] 1.15: Bang-Bang Hysteresis + String Generator
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
