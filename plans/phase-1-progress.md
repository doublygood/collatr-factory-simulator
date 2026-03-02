# Phase 1: Core Engine, Modbus, and Test Infrastructure - Progress

## Status: In Progress

## Tasks
- [x] 1.1: Configuration Models
- [x] 1.2: Simulation Clock
- [x] 1.3: Signal Value Store
- [ ] 1.4: Signal Model Base + Noise Pipeline
- [ ] 1.5: Steady State Model
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
