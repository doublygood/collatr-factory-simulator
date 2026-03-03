# Phase 2 Independent Code Review

> **Reviewer:** Independent Review Agent (subagent)
> **Date:** 2026-03-03
> **Scope:** 13 source files, 6+ test files, 5 PRD sections, all 13 CLAUDE.md rules
> **Basis:** Full read of all listed source, test, PRD, and internal review files

---

## 1. Internal Review Assessment

### Overall Completeness

The internal review (`plans/phase-2-review.md`) is **thorough and well-structured**. It covers:

- PRD compliance across all 5 major sections (OPC-UA, MQTT, scenarios, ground truth, environment)
- CLAUDE.md rule-by-rule verification (7 rules checked, all relevant ones)
- RED/YELLOW/GREEN categorisation with specific file and line references
- Test gap analysis identifying 5 concrete gaps
- 11 positive GREEN observations with supporting evidence

### Correctness of Findings

**R1 (Ground Truth header missing scenarios):** Correctly identified. The fix was verified — `coder_depletion` and `material_splice` are now included in `write_header()` enumeration (lines 100-103 of ground_truth.py) and in `_AFFECTED_SIGNALS` dict in scenario_engine.py.

**R2 (No intermediate ground truth events):** Correctly identified as a significant gap. The fix was implemented across all 7 scenarios. I verified the fix in the source code — each scenario now calls `engine.ground_truth.log_signal_anomaly()`, `log_state_change()`, or `log_consumable()` at the appropriate lifecycle points. Tests in `TestScenarioIntermediateEvents` verify all 7 scenarios.

**Y1 (MQTT wall-clock timestamps):** Correctly identified. Still deferred — I discuss this further below.

**Y2 (OPC-UA uncertain quality):** Correctly identified and fixed. Lines 358-365 of `opcua_server.py` now map `"uncertain"` to `ua.StatusCodes.UncertainLastUsableValue`.

**Y3 (Test threading.Lock):** Correctly identified as acceptable test-only pattern. The comment in `test_mqtt_integration.py` line ~73 explicitly acknowledges Rule 9 does not apply to test-infrastructure paho callbacks.

**Y4 (Inconsistent complete() guard):** Correctly identified as a fragile pattern.

### Grading of Internal Review Findings

The grading was **mostly correct**, with one dispute:

- **R2 was correctly graded RED.** Missing intermediate ground truth events means the JSONL file lacks the detail needed for training label generation — the primary purpose of the simulator.
- **Y1 (MQTT timestamps) should arguably be RED, not YELLOW.** See my RED findings below.
- **Y3 (test Lock) was correctly YELLOW.** It's test infrastructure, not production code.

### What the Internal Review Missed

1. **MQTT timestamp uses wall clock — deeper Rule 6 analysis needed** (see R1 below)
2. **Ground truth `_format_time` docstring/code mismatch** (minor)
3. **Scenario `_spawn_rng` uses `integers()` instead of proper `SeedSequence.spawn()`** (see Y-NEW-1 below)
4. **DryerDrift signal name mismatch in `_AFFECTED_SIGNALS`** (see R2 below)
5. **RegistrationDrift `_AFFECTED_SIGNALS` lists both axes but the scenario only affects one** (minor)
6. **ColdStart `_start_spike` references `spike_power` variable potentially unbound** (see Y-NEW-2 below)
7. **No scheduling of Phase 2 scenarios in `ScenarioEngine._generate_timeline()`** (see R3 below)

### Internal Review Grade: **B+**

Thorough, well-referenced, caught the two most important issues (R1 and R2). Missed a few things, but nothing that a single pass wouldn't find. The fix implementation was clean.

---

## 2. RED Findings

### R1. MQTT Payload Timestamps Use Wall Clock, Violating Rule 6

**File:** `src/factory_simulator/protocols/mqtt_publisher.py`, lines 159-160 and lines 190-191
**PRD Ref:** Section 3.3.4 (payload format), Section 4.1 Principle 5 (simulated time invariant)
**CLAUDE.md:** Rule 6

```python
# Line 159-160 (make_payload):
now = datetime.now(UTC)
ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
```

```python
# Line 190-191 (make_batch_vibration_payload):
now = datetime.now(UTC)
ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
```

**Issue:** Both `make_payload()` and `make_batch_vibration_payload()` use `datetime.now(UTC)` — wall clock time. PRD Section 4.1 Principle 5 states: "All signal models use simulated time. The time variable `t` and time delta `dt` in every generator formula refer to the simulation clock, not wall-clock time. This invariant ensures that compressed runs (10x, 100x) produce statistically identical output to real-time runs."

Section 3.3.4 defines the timestamp as "ISO 8601 UTC timestamp of generation." At time_scale=10x, the MQTT timestamps will be wall-clock-spaced (100ms apart) while the simulated signals represent 1-second intervals. An MQTT subscriber correlating timestamps with signal values will see incorrect timing. At 100x (batch mode), MQTT is disabled per PRD 4.4, but at 10x it's still active.

The internal review flagged this as YELLOW. I'm upgrading to **RED** because:
1. The PRD explicitly says protocol adapters serve data at up to 10x.
2. At 10x, MQTT payloads carry wall-clock timestamps while OPC-UA `SourceTimestamp` uses drifted clock and `ServerTimestamp` uses simulation clock (PRD 3.2). This creates cross-protocol timestamp inconsistency.
3. The ground truth logger uses sim_time-based timestamps. An evaluation comparing MQTT payloads against ground truth timestamps will have mismatched time axes at any time_scale != 1.0.

**Fix:** Pass `sim_time` from the publish loop into `make_payload()` and `make_batch_vibration_payload()`. Convert using the same reference epoch pattern as `GroundTruthLogger._format_time()`. The `_publish_loop` has access to the store; the store entries carry `timestamp` (sim_time). Use `sv.timestamp` from the `SignalValue` as the payload timestamp instead of `datetime.now(UTC)`.

### R2. DryerDrift Uses Wrong Signal Names in Ground Truth (Two Locations)

**Files:**
- `src/factory_simulator/engine/scenario_engine.py`, lines 277-280 (`_AFFECTED_SIGNALS` dict)
- `src/factory_simulator/scenarios/dryer_drift.py`, line ~173 (`log_signal_anomaly` call)

**PRD Ref:** Section 4.7 (ground truth affected_signals, signal_anomaly events)

**Location 1 — `_AFFECTED_SIGNALS` in scenario_engine.py:**
```python
"DryerDrift": [
    "press.dryer_zone1_temp", "press.dryer_zone2_temp",
    "press.dryer_zone3_temp", "press.waste_count",
],
```

**Location 2 — `dryer_drift.py` `_on_activate()`:**
```python
signal = f"press.dryer_zone{self._zone}_temp"
gt.log_signal_anomaly(
    sim_time, signal, "drift",
    self._original_setpoint,
    [self._original_setpoint - 5.0, self._original_setpoint + 5.0],
)
```

The actual signal IDs in the store are `press.dryer_temp_zone_1`, `press.dryer_temp_zone_2`, `press.dryer_temp_zone_3` (confirmed from `factory.yaml` signal keys: `dryer_temp_zone_1`, `dryer_temp_zone_2`, `dryer_temp_zone_3` under the `press` equipment group). Both locations use `press.dryer_zone{N}_temp` — wrong naming pattern (zone number before `_temp` rather than after `zone_`).

This means:
1. Ground truth `scenario_start` events for DryerDrift will record incorrect signal IDs in `affected_signals`
2. Ground truth `signal_anomaly` events for DryerDrift will record the wrong `signal` field
3. A downstream consumer trying to correlate ground truth events with actual signal data will fail to match on both event types

Similarly, the `RegistrationDrift` entry lists both `"press.registration_error_x"` and `"press.registration_error_y"`, but the scenario only affects one axis per instance. This is a minor overstatement but not a data correctness issue — it's better to list both potential signals.

**Fix:**

In `scenario_engine.py`:
```python
"DryerDrift": [
    "press.dryer_temp_zone_1", "press.dryer_temp_zone_2",
    "press.dryer_temp_zone_3", "press.waste_count",
],
```

In `dryer_drift.py`:
```python
signal = f"press.dryer_temp_zone_{self._zone}"
```

Verify all `_AFFECTED_SIGNALS` entries against actual signal IDs from `factory.yaml`.

### R3. Phase 2 Scenarios Are Never Auto-Scheduled

**File:** `src/factory_simulator/engine/scenario_engine.py`, lines 127-155 (`_generate_timeline`)
**PRD Ref:** Section 5.13 (scenario scheduling), Sections 5.3-5.13a (scenario frequencies)

```python
def _generate_timeline(self) -> None:
    self._schedule_unplanned_stops()
    self._schedule_job_changeovers()
    self._schedule_shift_changes()
    # Sort by start time for orderly evaluation
    self._scenarios.sort(key=lambda s: s.start_time)
```

**Issue:** The `_generate_timeline()` method only schedules Phase 1 scenarios (unplanned stops, job changeovers, shift changes). None of the 7 Phase 2 scenarios (WebBreak, DryerDrift, InkExcursion, RegistrationDrift, ColdStart, CoderDepletion, MaterialSplice) are auto-scheduled. The PRD specifies frequencies for each:

- WebBreak: 1-2 per week (PRD 5.3)
- DryerDrift: 1-2 per shift (PRD 5.4)
- InkExcursion: 2-3 per shift (PRD 5.6)
- RegistrationDrift: 1-3 per shift (PRD 5.7)
- ColdStart: 1-2 per day (PRD 5.10)
- CoderDepletion: frequency depends on consumption (PRD 5.12)
- MaterialSplice: 2-4 per shift (PRD 5.13a)

While all 7 scenarios can be manually added via `add_scenario()` (as tests do), a user running the simulator with default config will never see these scenarios fire. This means the simulator doesn't produce its Phase 2 scenarios unless the user manually schedules them.

**Severity assessment:** This is RED because it means the default simulator run produces no Phase 2 scenario data, defeating the purpose of implementing them. However, it could also be argued this is intentional for Phase 2 (implement scenarios, schedule them in Phase 3). The phase plan should clarify.

**Fix:** Add scheduling methods for each Phase 2 scenario type, or document that auto-scheduling is deferred to Phase 3.

---

## 3. YELLOW Findings

### Y1. Scenario `_spawn_rng` Uses `integers()` Instead of `SeedSequence.spawn()`

**Files:** `src/factory_simulator/engine/scenario_engine.py` line 264, `src/factory_simulator/generators/base.py` line 183
**CLAUDE.md:** Rule 13

```python
def _spawn_rng(self) -> np.random.Generator:
    """Create a child RNG from the parent (Rule 13)."""
    return np.random.default_rng(self._rng.integers(0, 2**63))
```

Rule 13 says: "Use numpy.random.Generator with SeedSequence. Each subsystem gets an isolated Generator spawned from the root SeedSequence." The correct numpy pattern for spawning child generators is:

```python
# Proper SeedSequence spawning:
child_ss = self._rng.bit_generator.seed_seq.spawn(1)[0]
return np.random.default_rng(child_ss)
```

The `integers()` approach works for practical reproducibility, but it does not use the SeedSequence tree structure. With `integers()`, the child seed depends on the parent's current state, which means the child seed changes if the parent draws any additional random numbers before spawning. With proper `SeedSequence.spawn()`, child streams are deterministic regardless of parent usage order.

This is YELLOW because:
- Reproducibility still works when the same sequence of operations occurs (which it does in practice)
- The issue only manifests if operations reorder between runs, which doesn't happen in the deterministic tick loop
- But it's a deviation from the CLAUDE.md-specified pattern

**Fix:** Replace `_spawn_rng()` implementations with `SeedSequence.spawn()` pattern.

### Y2. ColdStart `_start_spike` May Reference Unbound `spike_power`

**File:** `src/factory_simulator/scenarios/cold_start.py`, lines 259-262

```python
# Line 254-258:
if energy is not None:
    self._saved_power_base = energy._line_power._base
    normal_power = (...)
    spike_power = normal_power * self._power_multiplier
    energy._line_power._base = spike_power

# Line 268-271 (ground truth logging):
gt = engine.ground_truth
if gt is not None:
    gt.log_signal_anomaly(
        sim_time, "energy.line_power", "spike",
        spike_power, [0.0, normal_power],    # <-- spike_power may be unbound
    )
```

If `energy is None` (the energy generator is not found), the code skips lines 254-258 where `spike_power` and `normal_power` are defined. But the ground truth logging at line 268 references `spike_power` unconditionally. If `energy` is None but `gt` is not None, this will raise `UnboundLocalError`.

In practice this shouldn't happen because the energy generator always exists in the packaging profile. But it's a latent bug.

**Fix:** Move the ground truth logging inside the `if energy is not None:` block, or add a guard.

### Y3. WebBreak Tension Spike Logs Anomaly Value Before Generator Fires

**File:** `src/factory_simulator/scenarios/web_break.py`, lines 170-176

```python
# Ground truth: tension spike anomaly (PRD 4.7)
gt = engine.ground_truth
if gt is not None:
    gt.log_signal_anomaly(
        sim_time, "press.web_tension", "spike",
        self._spike_tension, [60.0, 400.0],
    )
```

The anomaly is logged with `self._spike_tension` (the configured spike value) at the moment of activation. At this point, the scenario has set `tension_model._base = self._spike_tension` but the generator has not yet fired to produce the actual store value. The logged anomaly value is the *intended* spike, not the *actual* generated value (which would include noise).

This is a minor fidelity issue — the PRD shows the anomaly value as `720.3` (with noise-like precision), not a round number. Using the actual store value after the generator fires would be more accurate ground truth.

**Fix:** Consider deferring the anomaly log to the first `_on_tick` call after the generator has produced the spiked value, using `store.get("press.web_tension").value`.

### Y4. `_format_time` Docstring Contradicts Implementation

**File:** `src/factory_simulator/engine/ground_truth.py`, lines 308-323

The docstring says: "If sim_time is small... treat it as seconds from a reference epoch. Otherwise treat as absolute epoch seconds."

The implementation always adds `sim_time` to the reference epoch:
```python
dt_obj = _REFERENCE_EPOCH.timestamp() + sim_time
```

There is no conditional branch. The docstring describes planned behavior that wasn't implemented. This won't cause bugs (all sim_times are relative), but the misleading docstring could confuse future developers.

**Fix:** Update docstring to match implementation: "Always treats sim_time as seconds from the reference epoch (2026-01-01T00:00:00Z)."

### Y5. Environment Generator `_update_perturbation` Accumulates Without Bound

**File:** `src/factory_simulator/generators/environment.py`, lines 183-196

```python
def _update_perturbation(self, real_dt: float) -> None:
    if abs(self._perturb_offset) > 1e-12:
        self._perturb_offset *= math.exp(-real_dt / self._perturb_tau)

    n_events = int(self._perturb_rng.poisson(self._perturb_lambda * real_dt))
    for _ in range(n_events):
        sign = 1.0 if self._perturb_rng.random() > 0.5 else -1.0
        mag = float(self._perturb_rng.uniform(
            0.5 * self._perturb_magnitude,
            1.5 * self._perturb_magnitude,
        ))
        self._perturb_offset += sign * mag
```

Multiple Poisson events within a single large `real_dt` (which can be 60 seconds) could stack additively before any decay occurs. The decay is applied once at the start, then all new events are added. If 3 events all fire with the same sign (probability ~12.5%), the offset could spike to 3 * 3.0 = 9.0 C, which combined with the daily amplitude and HVAC cycling could push the temperature well outside the configured clamp range.

The `_post_process` method does apply clamping, so the final signal value is bounded. But the raw offset accumulating without per-event decay means the perturbation dynamics are slightly inaccurate — events that arrive simultaneously should theoretically each trigger independent decay processes, not stack linearly then decay as one sum.

This is YELLOW because clamping prevents out-of-range values, and the effect is small in practice.

### Y6. Phase 2 Scenarios Access Generator Private Attributes Directly

**Files:** All 7 scenario files
**Pattern:** `press._web_tension._base`, `press._waste_count._rate`, `press._dryer_temp_1`, etc.

All scenarios directly manipulate private (`_`-prefixed) attributes of generator and model classes. This creates tight coupling between scenarios and generator internals. If a generator restructures its internal models (e.g., renaming `_web_tension` or changing the model type), all scenarios that touch it will break silently.

This is a design issue that will compound as more scenarios are added in Phases 3-5. Consider adding a public API on generators for scenario interaction:

```python
class PressGenerator:
    def override_tension_base(self, value: float) -> None: ...
    def get_tension_model(self) -> CorrelatedFollowerModel: ...
```

This is YELLOW because the current approach works and the coupling is documented in test restoration assertions, but it will make Phase 3 implementation more fragile.

---

## 4. GREEN Observations

### G1. Exceptional Test Quality

All 1481 tests pass. The test suite covers:
- All 7 scenario lifecycle phases (pending → active → internal phases → completed)
- Parameter restoration on completion (every scenario)
- Edge cases (press not found, early completion, boundary values)
- Deterministic parameter sampling with fixed seeds
- Ground truth intermediate events (7 new tests from code review)

The `TestScenarioIntermediateEvents` class in `test_ground_truth.py` is particularly well-designed — it creates a full DataEngine, injects known state, and verifies that ground truth events contain the correct signal names, anomaly types, and values.

### G2. OPC-UA Implementation Matches PRD Appendix B Exactly

The OPC-UA server dynamically builds 32 leaf nodes from signal config, with:
- Correct string NodeIDs (`ns=2;s=PackagingLine.Press1.LineSpeed`)
- Correct data types (Double, UInt32, UInt16) matching Appendix B
- EURange properties on all nodes
- Read-only by default, writable for 3 dryer zone setpoints
- Setpoint write-back detection via "last written" tracking
- Three-quality StatusCode mapping (Good, UncertainLastUsableValue, BadSensorFailure)

The `EXPECTED_NODES` list in `test_opcua_integration.py` is a 32-entry complete enumeration that exactly matches Appendix B.

### G3. MQTT Publisher Design is Clean and Extensible

The separation of concerns is excellent:
- `TopicEntry` dataclass captures per-signal MQTT config
- `BatchVibrationEntry` handles the non-standard x/y/z payload separately
- `build_topic_map()` derives QoS/retain/event-driven from topic suffix rules
- `make_payload()` and `make_batch_vibration_payload()` are pure functions
- Client injection via constructor enables unit testing without a broker
- Publish scheduling uses monotonic time for loop timing (correct for wall-clock scheduling)

### G4. Scenario Base Class Lifecycle Pattern

The `Scenario` base class provides a clean, testable lifecycle:
```
PENDING → (sim_time >= start_time) → ACTIVE → (complete()) → COMPLETED
```

Hook methods (`_on_activate`, `_on_tick`, `_on_complete`) keep subclass code focused on behavior. The `evaluate()` method handles all phase transitions. The `elapsed` tracker is updated in the base class. Subclasses never need to manage phase transitions directly.

### G5. Environment Composite Model is Faithful to PRD 4.2.2

The three-layer model exactly matches the PRD formula:
```
value = daily_sine(t) + hvac_cycle(t) + perturbation(t) + noise(0, sigma)
```

Key implementation details that show careful PRD reading:
- Daily sine built WITHOUT noise (noise is the final separate layer)
- HVAC uses BangBangModel centered at 0 (oscillation, not absolute temp)
- Poisson process for perturbation events uses `numpy.poisson()` for correct statistics
- Humidity inverted via `_humidity_ratio` scaling
- `real_dt` tracking compensates for the generator firing every 60s vs tick dt of 0.1s

### G6. Cross-Protocol Integration Test is Genuine End-to-End

`test_cross_protocol.py` starts all three protocol adapters simultaneously against one store, connects real protocol clients, and verifies consistency. The `_float32_roundtrip()` helper correctly accounts for Modbus float32 precision loss when comparing with OPC-UA Double values. The `_wait_for_topics()` async helper avoids blocking the event loop. This test would catch real cross-protocol regressions.

### G7. Scenario Restoration is Systematically Verified

Every scenario test file contains explicit tests like:
- `test_tension_base_restored_on_completion`
- `test_waste_rate_restored`
- `test_reversion_rate_restored`

These capture original values before activation and assert exact restoration after completion. This pattern prevents scenarios from "leaking" modified state — a critical correctness property.

### G8. Configuration Models are Comprehensive

The `config.py` additions for Phase 2 include:
- `CoderDepletionConfig` with threshold and recovery range validation
- `MaterialSpliceConfig` with trigger diameter and splice duration validation
- All range pairs validated via `_validate_range_pair()`
- All probability fields validated to [0, 1]
- All positive-required fields validated via field validators

### G9. Ground Truth Logger Design

The logger is append-only, write-only, with `flush()` after every line — crash-safe JSONL output. Graceful degradation when logger is not opened (all writes are no-ops). The `_format_time()` produces consistent ISO 8601 with millisecond precision. All 10 PRD-specified event types have dedicated methods.

### G10. Consistent Code Style Across All Phase 2 Files

All 13 source files follow the same patterns:
- Module docstrings with PRD references and CLAUDE.md rule citations
- Type hints on all public methods
- Private attributes with single underscore
- PascalCase classes, snake_case functions, _UPPER_SNAKE constants
- `TYPE_CHECKING` guards for forward references
- No unused imports

---

## 5. Test Gap Analysis

### 5.1 Missing Tests

1. **No test for MQTT timestamp accuracy at time_scale != 1.0** — The MQTT integration tests run at 1x speed only. No test verifies that payload timestamps correspond to simulation time when `time_scale > 1.0`. This gap relates to R1.

2. **No test that Phase 2 scenarios are auto-scheduled** — All scenario tests manually `add_scenario()`. No test creates a `ScenarioEngine` with Phase 2 scenario configs enabled and verifies that instances are created in the timeline. This relates to R3 (they aren't scheduled).

3. **No test for `_AFFECTED_SIGNALS` signal name correctness** — No test verifies that the signal names in `_AFFECTED_SIGNALS` match actual signal IDs in the store. The DryerDrift signal name bug (R2) would have been caught by such a test.

4. **No test for scenario interruption mid-phase** — No test calls `engine.stop()` or forces scenario completion during an intermediate phase (e.g., during WebBreak DECELERATION) and verifies clean model restoration.

5. **No concurrent scenario interaction test** — Tests run individual scenarios in isolation. No test verifies behavior when two scenarios overlap (e.g., DryerDrift + InkExcursion simultaneously modifying `press._waste_count._rate`). The waste rate restoration uses simple save/restore — concurrent scenarios would restore the other scenario's modified rate, not the original.

6. **No property-based test for scenario parameter ranges** — CLAUDE.md Rule 2 says to use Hypothesis for signal models. No Hypothesis tests verify that scenario parameters sampled from their configured ranges always produce valid behavior (e.g., drift never exceeds max_drift, spike tension always > 600N).

### 5.2 Weak Coverage Areas

1. **ColdStart trigger detection edge cases** — Only one test verifies the idle threshold timing. Edge cases like: press transitions from Idle → Fault → Idle → Running (second idle period shorter than threshold) are not tested.

2. **MaterialSplice multi-effect timing** — The splice has 5 simultaneous effects with independent timers. Tests verify individual effects but no test verifies the exact timing interaction (e.g., tension spike ending while registration error is still active).

3. **CoderDepletion Fault→Ready timer locking** — The scenario sets `min_duration = 1e9` to prevent auto-recovery. No test verifies what happens if the coder state machine has additional transitions from Fault that aren't to Ready.

---

## 6. Phase 2 Grade and Verdict

### Code Quality Grade: **A-**

**Strengths:**
- Excellent test coverage (1481 tests, all passing)
- Clean architecture with consistent patterns
- PRD compliance on all major specifications
- Comprehensive scenario implementations with proper state save/restore
- Good separation of concerns in protocol adapters

**Weaknesses:**
- MQTT timestamps use wall clock (R1)
- DryerDrift affected signal names are wrong (R2)
- Phase 2 scenarios not auto-scheduled (R3)
- Direct private attribute access in scenarios creates coupling (Y6)

### PRD Compliance Grade: **B+**

All specified behaviors are implemented correctly. The signal models, noise, and correlations match PRD formulas. The OPC-UA node tree and MQTT topic map exactly match Appendices B and C. The three findings (R1-R3) are implementation gaps rather than fundamental misunderstandings of the PRD.

### Verdict: **CONDITIONAL GO for Phase 3**

**Conditions (must fix before starting Phase 3):**

1. **Fix R1 (MQTT wall-clock timestamps)** — Change `make_payload()` and `make_batch_vibration_payload()` to use sim_time from SignalValue.timestamp. This is a ~10-line change in `mqtt_publisher.py` plus adding `sim_time` parameter threading.

2. **Fix R2 (DryerDrift signal names in `_AFFECTED_SIGNALS`)** — Correct the signal IDs to match actual store keys. Verify all entries in the dict against factory.yaml. This is a ~5-line change.

3. **Document R3 decision (scenario auto-scheduling)** — Either add scheduling methods for Phase 2 scenarios or explicitly document that auto-scheduling is deferred to Phase 3 with a task in the Phase 3 plan. If deferred, add a YAML config example showing how to manually enable Phase 2 scenarios.

**Rationale:** The codebase is solid. The architecture scales well for Phase 3 (F&B profile, batch processing, more scenarios). The three RED findings are all fixable in under a day. The conditional items prevent data correctness issues from propagating into Phase 3 work.

---

## 7. Deferred Items

### Track for Phase 3

1. **Concurrent scenario interaction** — When Phase 3 adds more scenarios running simultaneously, the simple save/restore pattern for shared model attributes (e.g., `_waste_count._rate`) will break. Design a stacking/priority system for concurrent model modifications.

2. **Scenario auto-scheduling for Phase 2 types** — Unless addressed before Phase 3 start, add scheduling methods for WebBreak, DryerDrift, InkExcursion, RegistrationDrift, ColdStart, CoderDepletion, and MaterialSplice.

3. **Public API for scenario→generator interaction** (Y6) — As more scenarios and generators are added, direct private attribute access becomes increasingly fragile. Consider adding accessor methods on generators for model manipulation.

### Track for Phase 4

4. **SeedSequence.spawn() for child RNGs** (Y1) — Replace `integers()`-based RNG spawning with proper `SeedSequence.spawn()` across generators and scenario engine.

5. **Hypothesis property-based tests for scenario parameters** — Verify that all parameter ranges produce valid behavior.

6. **Scenario interruption/cancellation tests** — Verify clean model restoration when scenarios are interrupted mid-phase.

### Track for Phase 5

7. **MQTT payload timestamp from store value** — If R1 is fixed with sim_time from SignalValue, verify that the timestamp matches the simulated data generation time, not the last-tick time.
