# Phase 2 Code Review

> Reviewed: 2026-03-03
> Reviewer: Quality Engineer Agent
> Scope: 11 source files, 10 test files, 5 PRD sections, 7 CLAUDE.md rules
> All 1371 unit tests passing.

---

## 1. PRD Compliance

### 1.1 OPC-UA Node Tree vs Appendix B

**Status: PASS**

The OPC-UA server (`src/factory_simulator/protocols/opcua_server.py`) builds the node tree dynamically from signal config `opcua_node` fields. Test file `tests/unit/test_protocols/test_opcua.py` defines an `EXPECTED_NODES` list of exactly 32 string NodeIDs matching Appendix B.

Tests verify:
- 32 leaf nodes with correct string NodeIDs (e.g. `ns=2;s=PackagingLine.Press1.LineSpeed`)
- Data types: Double, UInt32, UInt16 matching Appendix B per signal
- EURange property present on all variable nodes with correct min/max values
- AccessLevel: read-only by default, writable for setpoints (`modbus_writable=True`)
- Namespace URI `urn:collatr:factory-simulator`, ns=2
- Value sync from SignalStore every 500ms (MIN_PUBLISHING_INTERVAL_MS)
- Setpoint write-back detection and propagation to store

### 1.2 MQTT Topics vs Appendix C

**Status: PASS with one YELLOW finding**

The MQTT publisher (`src/factory_simulator/protocols/mqtt_publisher.py`) via `build_topic_map()` produces 16 per-axis topic entries for the packaging profile. The batch vibration topic (`vibration/main_drive`) is built separately via `build_batch_vibration_entry()`. This matches Appendix C: 16 per-axis + 1 batch = 17 total.

Tests verify:
- 16 topic entries from `build_topic_map()` (11 coder + 2 env + 3 vibration)
- QoS 1 for: `coder/state`, `coder/prints_total`, `coder/nozzle_health`, `coder/gutter_fault`
- QoS 0 for all other topics
- Retain=False for `vibration/*` topics
- Retain=True for all non-vibration topics
- Event-driven publishing for QoS-1 topics (same 4 critical signals)
- Timed publishing from `sample_rate_ms` for analog signals
- Batch vibration topic: QoS 0, no retain, 1s interval, x/y/z fields
- JSON payload structure: `{timestamp, value, unit, quality}`
- Topic prefix: `collatr/factory/{factory_id}/{line_id}`

### 1.3 Scenario Sequences vs Section 5.x

**Status: PASS**

All 7 Phase 2 scenarios implemented and tested:

**WebBreak (PRD 5.3)** -- `src/factory_simulator/scenarios/web_break.py`
- 3-phase: SPIKE -> DECELERATION -> RECOVERY
- Tension spikes >600N (test: `test_tension_exceeds_600n_during_spike`)
- Tension drops to ~0 after spike (test: `test_tension_drops_after_spike`)
- Fault state forced (test: `test_forces_fault_state`)
- Coils set: `press.web_break`, `press.fault_active` (tests verify)
- Emergency deceleration 5-10s (test: `test_emergency_deceleration`)
- Recovery clears coils, restores Setup state
- Model params and max_clamp restored on completion
- 22 tests total

**DryerDrift (PRD 5.4)** -- `src/factory_simulator/scenarios/dryer_drift.py`
- Zone drift above setpoint, drift rate 0.05-0.2 C/min
- Drift capped at max_drift (test: `test_drift_capped_at_max_drift`)
- Waste rate increased 20-50% (test: `test_waste_rate_increased_during_drift`)
- No fault state triggered (test: `test_no_fault_state_during_drift`)
- Setpoint unchanged during drift (test: `test_setpoint_unchanged_during_drift`)
- Zone selection 1-3, correct model targeted
- Recovery: temperature returns to setpoint via lag model
- 18 tests total

**InkExcursion (PRD 5.6)** -- `src/factory_simulator/scenarios/ink_excursion.py`
- Direction: THIN (<18s) or THICK (>45s) with ramp+hold phases
- Registration error drift rate multiplied 3-5x
- Waste rate increased 10-30%
- All params restored on completion
- 20 tests total

**RegistrationDrift (PRD 5.7)** -- `src/factory_simulator/scenarios/registration_drift.py`
- Drift rate 0.01-0.05 mm/s, exceeds 0.3mm
- Mean-reversion suppressed during drift
- Waste increase when error > 0.2mm threshold
- All params restored on completion
- 20 tests total

**ColdStart (PRD 5.10)** -- `src/factory_simulator/scenarios/cold_start.py`
- Trigger: Off/Idle -> Setup/Running after >30min idle
- energy.line_power: 150-200% spike for 2-5s
- press.main_drive_current: 150-300% spike
- Max clamp raised during spike, restored after
- No trigger from Fault state, no trigger if idle too short
- 21 tests total

**CoderDepletion (PRD 5.12)** -- `src/factory_simulator/scenarios/coder_depletion.py`
- At 10%: quality flag -> "uncertain"
- At 2%: coder -> Fault state
- Auto-refill disabled during scenario
- Recovery: ink refilled to 100%, coder -> Ready
- Quality override and fault timer restored
- 21 tests total

**MaterialSplice (PRD 5.13a)** -- `src/factory_simulator/scenarios/material_splice.py`
- Trigger: unwind_diameter <= 150mm during Running
- Tension spike 50-100N for 1-3s
- Registration error increase 0.1-0.3mm for 10-20s
- Waste rate multiplied during splice window
- Unwind diameter refilled to 1500mm
- Speed dip 5-10% with recovery
- Machine state stays Running throughout
- 25 tests total

### 1.4 Ground Truth JSON Schema vs Section 4.7

**Status: PASS with one RED finding**

The ground truth logger (`src/factory_simulator/engine/ground_truth.py`) implements:
- Config header record with `sim_version`, `seed`, `profile`, `signals`, `scenarios`
- 10 event types: `scenario_start`, `scenario_end`, `state_change`, `signal_anomaly`, `data_quality`, `micro_stop`, `shift_change`, `consumable`, `sensor_disconnect`, `stuck_sensor`, `connection_drop`
- JSONL format (one JSON object per line, newline terminated)
- Compact JSON serialization (`separators=(',', ':')`)
- ISO 8601 timestamps with millisecond precision
- ScenarioEngine integration: automatic `scenario_start`/`scenario_end` logging

Test coverage: 25 tests covering all event types, header format, JSONL validity, lifecycle, and engine integration.

### 1.5 Environment Composite Model vs Section 4.2.2

**Status: PASS**

The environment generator (`src/factory_simulator/generators/environment.py`) implements:
- Layer 1: Daily sinusoidal (24h period) via SinusoidalModel
- Layer 2: HVAC bang-bang cycling via BangBangModel (15-30 min period, 0.5-1.5 C)
- Layer 3: Random perturbations via Poisson process (3-8/shift, 1-3 C) with exponential decay
- Final: Gaussian noise layer
- Formula: `value = daily_sine(t) + hvac_cycle(t) + perturbation(t) + noise(0, sigma)`
- Humidity inversely correlated with temperature via `_humidity_ratio`
- Output clamped to min/max bounds

Test coverage: 10 tests including variance analysis, HVAC zero-crossing detection, perturbation rate verification, humidity inverse correlation, determinism, and bounds checking.

---

## 2. CLAUDE.md Rules Compliance

### Rule 5: Signal Models Are Mathematical

**Status: PASS**

All signal models follow mathematical formulas defined in PRD Section 4.2:
- `SinusoidalModel`: center + amplitude * sin(2*pi*t/period + phase) + noise
- `BangBangModel`: symmetric rate-based HVAC cycling
- `FirstOrderLagModel`: exponential approach to setpoint
- `RampModel`: linear interpolation from start to end value
- `CorrelatedFollowerModel`: base + gain * input + noise
- `RandomWalkModel`: mean-reverting random walk with drift
- `DepletionModel`: linear consumption with configurable refill
- Environment composite: layered sum of sine + bang-bang + Poisson-decay + noise

### Rule 6: Simulated Time Invariant

**Status: PASS with one YELLOW finding**

All signal generators use `sim_time` exclusively for value computation. The environment generator uses `sim_time` for daily sine phase. Scenario elapsed time tracking uses `dt` from the simulation clock.

### Rule 8: Engine Atomicity

**Status: PASS**

The DataEngine `tick()` method updates all signals for one tick before yielding. No `await` between individual signal updates within a tick. Scenario evaluation runs before generators per tick, ensuring consistent snapshots.

### Rule 9: No Locks

**Status: PASS**

No `asyncio.Lock` or `threading.Lock` usage in any source file under `src/factory_simulator/`. The signal store follows the single-writer (engine), multiple-reader (protocol adapters) pattern. The asyncio event loop provides implicit synchronization.

### Rule 10: Configuration via Pydantic

**Status: PASS**

All configuration flows through Pydantic v2 models defined in `src/factory_simulator/config.py`. Signal configs (`SignalConfig`), equipment configs (`EquipmentConfig`), scenario configs, and protocol configs all use Pydantic validation. No hardcoded values that should come from config.

### Rule 12: No Global State

**Status: PASS**

All generators, models, and scenarios are instantiated per-profile. No module-level mutable state. No singletons. Each component receives dependencies via constructor injection. The environment generator uses `_spawn_rng()` for child RNGs, maintaining isolation.

### Rule 13: Reproducible Runs

**Status: PASS**

All randomness uses `numpy.random.Generator` with `SeedSequence`. No usage of the Python `random` module anywhere in `src/factory_simulator/`. Each subsystem gets an isolated Generator spawned from the root SeedSequence. Test: `test_composite_deterministic` verifies same seed produces identical output. Test: `test_deterministic_scenario_timeline` verifies identical scenario timelines.

---

## 3. RED -- Must Fix

### R1. Ground Truth `write_header()` Missing Two Scenario Types

**File:** `src/factory_simulator/engine/ground_truth.py`, lines 87-104
**Issue:** The `write_header()` method explicitly enumerates 9 scenario types for the `scenarios` list in the config header record. It is missing `coder_depletion` and `material_splice` -- two scenarios added in Phase 2 tasks 2.12 and 2.13.

```python
# Lines 87-104: Only these 9 are checked:
#   job_changeover, web_break, dryer_drift, bearing_wear,
#   ink_viscosity_excursion, registration_drift, unplanned_stop,
#   shift_change, cold_start_spike
```

The `ScenariosConfig` Pydantic model likely has fields for `coder_depletion` and `material_splice`, but they are not checked in `write_header()`. When these scenarios are enabled, the ground truth config header will not list them in the `scenarios` array, producing an incomplete audit trail.

**Fix:** Add checks for `scfg.coder_depletion.enabled` and `scfg.material_splice.enabled` at lines 103-104.

### R2. No Scenarios Emit Ground Truth Events Beyond Start/End

**Files:** All 7 scenario files in `src/factory_simulator/scenarios/`
**Issue:** While the `ScenarioEngine` automatically logs `scenario_start` and `scenario_end` events, none of the Phase 2 scenarios directly call `GroundTruthLogger` methods for intermediate events. Per PRD Section 4.7, the ground truth log should capture:

- `state_change` events when scenarios force state transitions (e.g., WebBreak forcing Fault, ColdStart detecting idle->active)
- `signal_anomaly` events for excursion values (e.g., tension >600N during web break, temperature drift during dryer drift)
- `consumable` events when ink is refilled (CoderDepletion) or material reel is replaced (MaterialSplice)

The `GroundTruthLogger` has methods for all these (`log_state_change`, `log_signal_anomaly`, `log_consumable`) but they are never called by any scenario. This means the ground truth JSONL file will contain only `scenario_start`/`scenario_end` records for Phase 2 scenarios, without the intermediate detail needed for ground truth analysis.

**Fix:** Each scenario should call the appropriate `GroundTruthLogger` methods during phase transitions and significant events. Access the logger via `engine.ground_truth`.

---

## 4. YELLOW -- Should Fix

### Y1. MQTT `make_payload()` Uses Wall Clock for Timestamps

**File:** `src/factory_simulator/protocols/mqtt_publisher.py`, lines 159-160 and 190-191
**Issue:** Both `make_payload()` and `make_batch_vibration_payload()` use `datetime.now(UTC)` to generate the `timestamp` field in MQTT JSON payloads. This is wall-clock time, not simulation time.

```python
# Line 159:
now = datetime.now(UTC)
ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
```

PRD Section 3.3.4 defines the MQTT payload `timestamp` as "ISO 8601 UTC timestamp of generation." During normal 1x real-time operation this is acceptable, but at accelerated simulation speeds (e.g., 10x via `time_scale`), the MQTT timestamps will not correspond to simulated time. Per CLAUDE.md Rule 6, all signal generation must produce identical output regardless of wall clock speed. While this is arguably a protocol-layer concern (not signal generation), it creates inconsistency: the same scenario at 1x vs 10x will produce MQTT payloads with different timestamp spacing.

**Fix:** Pass `sim_time` to `make_payload()` and convert it to ISO 8601 using the same reference epoch as ground truth (`_REFERENCE_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)`). The `_publish_due()` method at line 527 already receives `now` from `time.monotonic()` for scheduling, which is appropriate for the publish loop; only the payload timestamp needs to use sim_time.

### Y2. OPC-UA Quality Mapping: "uncertain" Maps to StatusCode.Good

**File:** `src/factory_simulator/protocols/opcua_server.py`, line 363
**Issue:** The OPC-UA value sync maps signal quality as follows:
- `"good"` / `"uncertain"` -> `StatusCode.Good`
- `"bad"` -> `StatusCode.BadSensorFailure`

PRD Section 3.2.3 states: "UncertainLastUsableValue for stale data." The current implementation treats `"uncertain"` quality identically to `"good"`, losing the quality distinction in the OPC-UA representation. When the CoderDepletion scenario sets quality to `"uncertain"` at 10% ink level, OPC-UA clients will see `StatusCode.Good` and cannot detect the degraded quality.

The comment in the code explicitly acknowledges this: "Phase 4 adds more." This is intentional deferral, but it means Phase 2 scenarios that set quality="uncertain" (specifically CoderDepletion) lose quality information over OPC-UA.

**Fix:** Map `"uncertain"` to `ua.StatusCodes.UncertainLastUsableValue` instead of `StatusCode.Good`. This is a one-line change in the `_sync_values` method's quality mapping logic.

### Y3. Cross-Protocol Integration Test Uses `threading.Lock`

**File:** `tests/integration/test_cross_protocol.py`, lines 37, 96
**Issue:** The `_MqttCollector` class in the cross-protocol integration test uses `threading.Lock` for thread-safe message collection. While this is necessary in test code because the paho-mqtt subscriber callback runs on a separate thread, it should be noted that this is test infrastructure only -- not production code violating Rule 9.

This is acceptable as-is but worth flagging: the test fixture also uses `time.sleep()` calls (lines 242, 244, 264) for synchronization, which makes the test timing-sensitive. A more robust approach would use condition variables or event-based synchronization.

### Y4. Scenario Cleanup Pattern: Inconsistent `complete()` Guard

**Files:** Multiple scenario files
**Issue:** The `_on_complete` cleanup hook is the canonical place to restore modified model parameters. However, if the scenario completes abnormally (e.g., the press generator is not found, or the engine is in an unexpected state), some scenarios call `self.complete()` early from `_on_activate()` without having saved any parameters. This means `_on_complete` runs with default/zero saved values, which could corrupt model parameters.

For example, in `cold_start.py` line 168-169:
```python
if press is None:
    self.complete(sim_time, engine)
    return
```

If `press` is None, `_on_complete` runs and tries to restore `_saved_power_base` (which is 0.0 by default), potentially zeroing out the energy model's `_base`. The guard `if self._energy is not None` at line 190 prevents the worst case, but this pattern is fragile.

**Fix:** Add explicit "was_activated" guard in `_on_complete` methods, or ensure saved state is only restored when the scenario actually modified the models.

---

## 5. GREEN -- Observations

### G1. All 1371 Unit Tests Pass

All unit tests pass cleanly with no failures, errors, or skipped tests. The test suite runs in ~72 seconds. This is a strong indicator of code health.

### G2. Test Coverage Breadth

Phase 2 test files contain 437 tests across the reviewed modules:
- Scenario tests: 147 tests (7 scenario files) -- comprehensive phase coverage, parameter validation, model restoration, boundary conditions
- Protocol tests: ~200 tests (OPC-UA + MQTT) -- node tree, data types, QoS, retain, event-driven/timed, encoding, sync
- Ground truth tests: 25 tests -- all event types, JSONL format, lifecycle, engine integration
- Environment tests: 10 tests -- composite model variance, HVAC cycling, perturbations, determinism, bounds
- Cross-protocol integration: 12 tests -- Modbus/OPC-UA consistency, MQTT from same store, simultaneous operation

Each scenario test file follows a consistent pattern: lifecycle tests, behavioral tests per PRD step, recovery/cleanup tests, parameter default validation, and edge cases. This is thorough and methodical.

### G3. Scenario Model Restoration is Well-Tested

Every scenario that modifies generator model internals has explicit tests verifying that all modified parameters are restored to their original values on completion. Tests capture original values before scenario activation and assert exact restoration after completion. This pattern catches regression issues where scenarios "leak" modified state.

### G4. Type Hints Comprehensive

All public methods and functions have type hints. Return types are specified. Generic types use modern Python 3.12+ syntax (e.g., `list[str]`, `dict[str, object]`, `X | Y` union syntax). `TYPE_CHECKING` guards are used correctly for forward references to avoid circular imports.

### G5. No Unused Imports

No unused imports were detected across the reviewed source files. All imports are actively used. Test files import only what they need.

### G6. Consistent Naming Conventions

Source files follow snake_case for functions and variables, PascalCase for classes, _UPPER_SNAKE for module constants. Internal phase enums use `_Phase` prefix convention. Private attributes use single underscore prefix. Naming is consistent across all 11 source files.

### G7. Async/Await Patterns Correct

OPC-UA server uses `asyncio.CancelledError` handling correctly in the sync loop. MQTT publisher uses `asyncio.sleep()` for non-blocking waits. No blocking I/O calls in async methods. Protocol servers properly implement `start()`/`stop()` lifecycle with cleanup.

### G8. Scenario Base Class Design

The `Scenario` base class provides a clean lifecycle pattern (PENDING -> ACTIVE -> COMPLETED) with hook methods (`_on_activate`, `_on_tick`, `_on_complete`) that subclasses override. The `evaluate()` method handles phase transitions and elapsed time tracking. This design ensures consistent lifecycle management across all 7+ scenario types.

### G9. Environment Generator Layered Architecture

The environment generator's layered composition (daily sine + HVAC bang-bang + Poisson perturbations + noise) is clean and extensible. Each layer is independently configurable. The humidity inverse correlation via `_humidity_ratio` is a nice touch for realism.

### G10. Cross-Protocol Integration Test

The cross-protocol test (`tests/integration/test_cross_protocol.py`) is a genuine integration test that starts all three protocol adapters simultaneously against a real store, connects real clients, and verifies value consistency. The `_float32_roundtrip()` helper correctly accounts for Modbus float32 encoding precision when comparing with OPC-UA Double values. This test catches real cross-protocol issues that unit tests would miss.

### G11. Determinism Verified at Multiple Levels

Determinism (Rule 13) is verified at three levels:
1. Signal model level: `test_composite_deterministic` in environment tests
2. Scenario timeline level: `test_deterministic_scenario_timeline` in basic scenario tests
3. Parameter level: `test_fixed_params_are_deterministic` in every scenario test file

---

## 6. Test Gap Analysis

### Gaps Identified

1. **No test verifies ground truth `scenarios` list completeness.** The `test_header_scenarios_list` test in `test_ground_truth.py` tests with a specific config but does not assert that all enabled scenario types appear in the list. This gap allowed R1 to go undetected.

2. **No negative test for MQTT timestamp accuracy under time_scale != 1.0.** The MQTT tests verify payload structure but not that timestamps correspond to simulation time. Accelerated time scale testing would expose Y1.

3. **No test verifies OPC-UA StatusCode for "uncertain" quality.** The OPC-UA tests check "good" -> Good and "bad" -> BadSensorFailure, but there is no explicit test that "uncertain" quality signals produce the expected OPC-UA StatusCode. This gap is related to Y2.

4. **No test for scenario interruption/cancellation.** No test verifies what happens if the engine stops mid-scenario (e.g., during WebBreak DECELERATION phase). While the `_on_complete` cleanup runs, there is no test proving model parameters are correctly restored in all interruption scenarios.

5. **No concurrent scenario interaction test.** Tests run individual scenarios in isolation. No test verifies behavior when two scenarios overlap (e.g., DryerDrift + InkExcursion simultaneously modifying the same waste_count rate).

---

## Summary

| Category | RED | YELLOW | GREEN |
|----------|-----|--------|-------|
| PRD Compliance | 1 (R2) | 0 | 5 sections pass |
| CLAUDE.md Rules | 0 | 1 (Y1) | 7 rules pass |
| Error Handling | 0 | 1 (Y4) | - |
| Test Coverage | 0 | 1 (Y3) | 437 tests, all pass |
| Code Quality | 0 | 1 (Y2) | 6 observations |
| Ground Truth | 1 (R1) | 0 | - |

**Overall Assessment:** Phase 2 implementation is solid. The two RED findings are both related to ground truth completeness -- missing scenario types in the header (R1) and missing intermediate event logging (R2). These are functionally important for the simulator's purpose (providing labeled training data) but do not affect signal generation correctness. The YELLOW findings are quality improvements that should be addressed before Phase 3.
