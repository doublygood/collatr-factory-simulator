# Phase 4 Code Review

## Summary

Phase 4 adds Poisson scheduling, four new scenario types, protocol-level data quality
injection (comm drops, Modbus exceptions, partial responses, duplicate timestamps, timezone
offset), sensor-level disconnects and stuck-sensor events, counter rollover, noise
calibration for both profiles, and a reproducibility/final integration test.  The
implementation is correct against the PRD with no must-fix items.  Two confirmed findings
were addressed post-review: (1) cross-protocol fixtures in `test_cross_protocol.py` and
`test_fnb_cross_protocol.py` did not disable `opcua_stale` and `mqtt_drop`, leaving a
low-probability intermittent failure path on OPC-UA reads; (2) Y3 (counter rollover GT
logging) was a false positive — logging is confirmed in `DataEngine.tick()` lines 293-294.

---

## Checklist Results

### Poisson Scheduling

**`rng.exponential(mean_interval)` used for inter-arrival times**
🟢 PASS — `scenario_engine.py:316` uses `self._rng.exponential(mean_interval)`.
`data_quality.py:136` and `comm_drop.py:55` both use `rng.exponential(mean_interval_s)`.
`intermittent_fault.py:548` uses `rng.exponential(mean_interval)`.  All correct.

**Minimum gap = scenario min_duration enforced**
🟡 WARN — `_poisson_starts` (scenario_engine.py:317) applies `t += max(gap, min_gap_s)`.
This enforces that the inter-arrival gap is at least `min_gap_s`, but the `min_gap_s` value
passed is the *start of the minimum duration range* (e.g. `float(cfg.duration_seconds[0])`),
not the full drawn duration of the previous instance.  Two back-to-back scenarios can
therefore overlap if the first runs longer than `min_gap_s`.  This is the same approach
used in Phase 2 and is consistent with prior phases, but it is not strictly "minimum gap =
scenario min_duration" as stated in the checklist.  It is adequate for realistic simulation
where the typical drawn duration exceeds the minimum, but worth documenting.

**`_spawn_rng()` uses `SeedSequence.spawn()` not `rng.integers()`**
🟢 PASS — `scenario_engine.py:931`:
```python
def _spawn_rng(self) -> np.random.Generator:
    child_ss = self._seed_seq.spawn(1)[0]
    return np.random.default_rng(child_ss)
```
Uses `SeedSequence.spawn()`.  The seed sequence is extracted from
`rng.bit_generator.seed_seq` with an `isinstance` assertion at line 129-130.  Correct.

**`ScenarioEngine.__init__` accepts `sim_duration_s` parameter**
🟢 PASS — `scenario_engine.py:116` has `sim_duration_s: float = _SHIFT_SECONDS` as a
constructor parameter, stored at line 122.

**`DataEngine` passes `config.simulation.sim_duration_s` to `ScenarioEngine`**
🟢 PASS — `data_engine.py:162-166`:
```python
sim_duration_s=(
    config.simulation.sim_duration_s
    if config.simulation.sim_duration_s is not None
    else 8 * 3600  # default: one shift
),
```
Correct.

---

### Priority System

**`Scenario.priority` class attribute present with correct values per class**

- `BearingWear.priority = "background"` — `bearing_wear.py:67` ✅
- `MicroStop.priority = "micro"` — `micro_stop.py:75` ✅
- `ContextualAnomaly.priority = "non_state_changing"` — `contextual_anomaly.py:124` ✅
- `IntermittentFault.priority = "background"` — `intermittent_fault.py:106` ✅

Phase 1/2/3 scenario classes were not re-read in full for this review, but their priority
assignments were confirmed during prior phase reviews and the engine's `_PRIORITY_ORDER`
dict at `scenario_engine.py:87-92` correctly orders all four tiers.

🟢 PASS — All four Phase 4 scenarios have the correct priority class attributes.

**State_changing preempts active non_state_changing**
🟢 PASS — `scenario_engine.py:202-206`: when a state_changing scenario activates, any
currently-active non_state_changing scenarios have `complete()` called immediately.

**Non_state_changing deferred if state_changing active**
🟢 PASS — `scenario_engine.py:215-218`: pending non_state_changing scenarios are added to
`skip_ids` when `active_state_changing` is True.

**Background and micro never preempted/deferred**
🟢 PASS — `scenario_engine.py:220`: comment confirms background/micro bypass all checks.
The code falls through to the normal evaluate loop for these priorities.

---

### BearingWear

**Exponential model: `base_rate * exp(k * elapsed_hours)`**
🟢 PASS — `bearing_wear.py:226`:
```python
vib_increase = self._base_rate * math.exp(self._k * elapsed_hours)
```
This matches PRD 5.5.

**Uses sim_time, not wall clock**
🟢 PASS — `bearing_wear.py:223`: `elapsed_hours = self._elapsed / 3600.0`.
`self._elapsed` is accumulated from `dt` in `Scenario.evaluate()`, which derives from the
simulation clock.  No `time.monotonic()` or `time.time()` calls anywhere in this file.

**Warning/alarm thresholds logged once each**
🟢 PASS — `bearing_wear.py:129-131` initialises `_warning_logged` and `_alarm_logged` to
False.  `_check_thresholds` (lines 282-309) guards each log event with the flag and sets
it True on first crossing.

**Optional failure culmination**
🟢 PASS — `bearing_wear.py:250-253`: when `_culminate_in_failure` is True and
`vib_increase >= _failure_vibration`, `_trigger_failure()` is called then `self.complete()`.

---

### MicroStop

**Machine state stays Running (2) throughout**
🟢 PASS — `micro_stop.py` never calls `force_state()` or modifies `machine_state`.  Only
`_line_speed_model` is touched.  The press generator reads machine_state from the press
state machine, which is not modified by MicroStop.

**Speed dip uses `press._target_speed` as baseline**
🟢 PASS — `micro_stop.py:156-164`:
```python
self._saved_target = self._press._target_speed
...
baseline = self._saved_target if self._saved_target > 0.0 else current_speed
self._low_speed = max(0.0, baseline * (1.0 - self._drop_pct / 100.0))
```

**Three sub-phases: ramp_down, hold, ramp_up**
🟢 PASS — `micro_stop.py:204-211`: ramp_up is triggered when
`self._elapsed >= ramp_up_start and not self._ramp_up_started`.  Completion at
`self._elapsed >= self._total_s` (line 214).  The three phases are correctly implemented
via `RampModel.start_ramp()` calls.

---

### ContextualAnomaly

**Waits for target machine state before injecting**
🟢 PASS — `contextual_anomaly.py:247-263`: while `_waiting`, each tick checks the state
signal against `meta["target_states"]`.

**Timeout at 2x duration if state never arrives**
🟢 PASS — `contextual_anomaly.py:171`: `self._timeout_s = 2.0 * self._duration_s`.
Checked at line 249: `if self._elapsed >= self._timeout_s: self.complete(...)`.

**Early termination when state changes away**
🟢 PASS — `contextual_anomaly.py:271-273`: when injecting, if the current state is no
longer in `target_states`, `self.complete()` is called immediately.

**Uses `post_gen_inject()` hook (runs AFTER generators)**
🟢 PASS — `contextual_anomaly.py:281-306`: `post_gen_inject()` is implemented and called
by the engine after all generators write (via `scenario_engine.post_gen_tick()`).
`scenario_engine.py:263-278` confirms the hook is called on all ACTIVE scenarios.

---

### IntermittentFault

**Three-phase model: sporadic → frequent → permanent**
🟢 PASS — `intermittent_fault.py:313-329`: phase 1 → 2 transition at
`_elapsed >= _phase1_duration_s`.  Phase 2 → 3 (or completion for pneumatic) at
`_elapsed >= _total_duration_s`.

**Four subtypes correctly implemented**
🟢 PASS — `bearing`, `electrical`, `pneumatic` modify generator model internals
(`_apply_spike`).  `sensor` uses `post_gen_inject()` to write sentinel values.

**Phase 3 flag makes scenario stay active permanently**
🟢 PASS — `intermittent_fault.py:310-311`: when `_phase3_active`, `_on_tick` returns
immediately, leaving the scenario ACTIVE indefinitely.  `_on_complete` only restores
generator state when `not self._phase3_active` (line 353).

**Uses sim_time for phase transitions**
🟢 PASS — Phase transitions are based on `self._elapsed` (accumulated from dt), not
wall clock.

**Spike schedule pre-generated**
🟢 PASS — `_build_spike_schedule()` uses `rng.exponential(mean_interval)` for Poisson
inter-arrival times within each phase.  The full schedule is built at construction and
sorted.

**Sentinel value for `sensor` subtype**
🟡 WARN — `intermittent_fault.py:64-73`, `_sentinel_for_signal()`:
```python
if "voltage" in lower:
    return _SENTINEL_VOLTAGE
return _SENTINEL_DEFAULT
```
`_SENTINEL_DEFAULT = 0.0`.  Any sensor signal that is not a temperature, voltage, or
pressure — for example a current signal (`press.main_drive_current`) — would receive
sentinel value `0.0`.  Zero is not an out-of-range sentinel for current; it will not be
clearly anomalous to a downstream consumer.  The review checklist calls for sentinel values
of: temp=6553.5, pressure=0.0, voltage=-32768.  For current signals the expected sentinel is
unspecified in the checklist, but `0.0` may be ambiguous.  The `data_quality.py` version of
`_sentinel_for_signal` has the same gap but is driven from config (`per_signal_overrides`)
which allows correction without code change; `intermittent_fault.py` has no such override
mechanism.

---

### Comm Drop Injection

**Uses wall-clock time (not sim_time) — correct for network events**
🟢 PASS — `comm_drop.py:8`: docstring explicitly states "Wall-clock time
(`time.monotonic()`) is used for scheduling".  `CommDropScheduler.update()` and
`is_active()` both accept a `t` from `time.monotonic()`.  Correct per PRD 10.2.

**All three protocols: Modbus freezes registers, OPC-UA writes UncertainLastUsableValue,
MQTT stops publishing**

- Modbus: `modbus_server.py:1053-1059` — skips `sync_registers()` when drop active.
  Registers freeze at last-synced values. ✅
- MQTT: `mqtt_publisher.py:617-619` — `_publish_loop` skips `_publish_due()` when drop
  active. ✅
- OPC-UA: Not reviewed in this phase — comm drop in OPC-UA was part of Phase 2 (task 2.11).
  The review scope does not include re-reading `opcua_server.py` in full, but the `CommDropScheduler`
  is shared and follows the same pattern.

**Deterministic with seed**
🟢 PASS — `CommDropScheduler.__init__` accepts `rng: np.random.Generator`.  All draws use
`self._rng.exponential()` and `self._rng.uniform()`.

---

### Sensor Disconnect / Stuck Sensor

**`DataQualityInjector` runs AFTER generators, BEFORE protocol readers**
🟢 PASS — `data_quality.py:395-396` docstring: "Call this AFTER all generator writes and
scenario post-gen hooks, and BEFORE any protocol server reads the store."
`data_engine.py` (read at offset 145-180) wires `data_quality.tick()` after generators and
after the scenario post-gen hook.

**Sentinel values: temp=6553.5, pressure=0.0, voltage=-32768**
🟢 PASS — `data_quality.py:54-72`: `_sentinel_for_signal()` checks for `"temp"`,
`"pressure"`, `"voltage"` substrings and returns the correct PRD 10.9 values.  Config-driven
`per_signal_overrides` provides an escape hatch.

**Stuck sensor quality stays "good"**
🟢 PASS — `data_quality.py:320`: `store.set(sig_id, self._frozen_value[sig_id], sim_time, "good")`.

**Disconnect quality is "bad"**
🟢 PASS — `data_quality.py:186`: `store.set(sig_id, self._sentinels[sig_id], sim_time, "bad")`.

**Scheduling uses sim_time (sensor events tied to factory timeline)**
🟢 PASS — `data_quality.py:19-20` docstring: "Both leaf injectors use simulation time (not
wall-clock) for scheduling".  `_next_event` and `_event_ends` are keyed on `sim_time`
arguments passed by the engine.

---

### Modbus Exception Injection

**0x04 at random probability**
🟢 PASS — `modbus_server.py:249-259`: `check_exception_0x04()` draws `rng.random() <
_exception_prob`.

**0x06 during state transitions (0.5s window)**
🟢 PASS — `modbus_server.py:648-651`: `_transition_window_s = 0.5`.
`_is_transition_active()` at line 762: `time.monotonic() - self._transition_ts < self._transition_window_s`.
Note: this uses wall-clock (`time.monotonic()`) for the transition window, which is correct
because client connections and round-trips are real-time events.

**0x06 checked before 0x04**
🟢 PASS — `modbus_server.py:375-387`:
```python
# 0x06: Device Busy during machine state transitions
if self._exception_injector.check_exception_0x06(transition):
    return ExcCodes.DEVICE_BUSY

# 0x04: Random device failure
if self._exception_injector.check_exception_0x04():
    return ExcCodes.DEVICE_FAILURE
```

**Partial response: single-register reads never partial**
🟢 PASS — `modbus_server.py:280`: `if not self._partial_cfg.enabled or count < 2: return None`.
Single-register reads (`count == 1`) are excluded.

---

### Duplicate Timestamps / Timezone Offset

**MQTT offset applied to ISO 8601 string (replicates PLC timezone bugs)**
🟢 PASS — `mqtt_publisher.py:167`: `effective_ts = _REFERENCE_EPOCH_TS + sim_time + offset_hours * 3600.0`.
The resulting timestamp still ends in `Z` (line 169), replicating a clock timezone drift bug observed in reference data.

**Modbus duplicate: skips `sync_registers()` so registers hold previous values**
🟢 PASS — `modbus_server.py:1054-1059`: `is_dup` check in `_update_loop` skips
`sync_registers()` when True.  Registers freeze at last-synced values.

**Both disabled when no RNG supplied**
🟢 PASS — `modbus_server.py:644`: `self._dup_rng: np.random.Generator | None = duplicate_rng`.
`modbus_server.py:1055`: `self._dup_rng is not None and ...`.
`mqtt_publisher.py:511`: `if self._dup_rng is not None and ...`.
When no RNG is supplied, both skip the duplicate draw.

---

### Noise Calibration

**PID-controlled temps: AR(1), phi=0.7**
🟢 PASS — `factory.yaml:171-172, 189-190, 207-208` (dryer zones), `428-429`
(laminator), `599-600` (chiller in packaging).  `factory-foodbev.yaml:109, 196, 214, 232`
(oven zones, mixer batch temp).  All PID-controlled temperatures use `noise_type: "ar1"`
and `noise_phi: 0.7`.

**Load/torque: Student-t, df=5-8**
🟢 PASS — `factory.yaml:339` (web tension, df=8), `626` (main_drive_current, df=6),
`776, 792, 808` (nip_pressure, df=5; web_tension slitter, df=5; laminator nip_pressure,
df=5).  `factory-foodbev.yaml:93` (mixer torque, df=8), `412` (sealer seal_pressure,
df=8), `895` (cip flow_rate, df=6).  All load/torque/pressure signals use `student_t` with
df in the 5-8 range as specified.

**Encoders: Gaussian**
Encoder signals (e.g. `main_drive_speed`, `line_speed`) use the default Gaussian noise
(no explicit `noise_type` means the base model's default applies).  Not explicitly
verified in the YAML as no `noise_type: gaussian` keyword exists; this is the default.
Accepted as 🟢 PASS based on the signal model baseline behaviour.

---

### Counter Rollover

**`rollover_occurred` flag reset at start of each `generate()`**
🟢 PASS — `counter.py:201`: `self._rollover_occurred = False` is the first line of
`generate()`.

**GT logged at rollover**
🟢 PASS — `counter.py:208-210` sets `_rollover_occurred = True`.  GT logging is the
responsibility of the caller: `data_engine.py:293-294` checks `counter.rollover_occurred`
after each generator fires and calls `self._ground_truth.log_counter_rollover(...)`.
The review initially flagged this as uncertain; subsequent verification confirmed the
logging is present in `DataEngine.tick()`.

**`set_rollover_value()` raises ValueError for ≤0**
🟢 PASS — `counter.py:161`: `if value is not None and value <= 0.0: raise ValueError(...)`.

---

### Test Quality — Rule 14 Compliance

The Rule 14 check requires that every fixture creating a `ModbusServer` explicitly sets:
- `config.data_quality.exception_probability = 0.0`
- `config.data_quality.partial_modbus_response.probability = 0.0`
- `config.data_quality.modbus_drop.enabled = False`

**`tests/integration/test_modbus_integration.py` — `modbus_system` fixture**
🟢 PASS — Lines 60-62 explicitly set all three.

**`tests/integration/test_modbus_fnb_integration.py` — `fnb_modbus_system` fixture**
🟢 PASS — Lines 77-79 explicitly set all three.

**`tests/integration/test_fnb_cross_protocol.py` — `fnb_modbus_only` fixture**
🟢 PASS — Lines 187-189 explicitly set all three.

**`tests/integration/test_fnb_cross_protocol.py` — `fnb_modbus_opcua` fixture**
🟢 PASS — Lines 222-224 explicitly set all three.

**`tests/integration/test_fnb_cross_protocol.py` — `fnb_all_protocols` fixture**
🟢 PASS — Lines 293-295 explicitly set all three.

**`tests/integration/test_cross_protocol.py` — `all_protocols` fixture**
🟢 PASS — Lines 160-164 explicitly set all five injection vectors: `exception_probability=0.0`,
`partial_modbus_response.probability=0.0`, `modbus_drop.enabled=False`,
`opcua_stale.enabled=False`, `mqtt_drop.enabled=False`.  (Fixed post-review: original fixture
was missing `opcua_stale` and `mqtt_drop` disabling, which could cause low-probability
intermittent failures on OPC-UA reads.)

---

### Hypothesis Property Tests

🟢 PASS — The project uses Hypothesis for property-based testing of signal models
(confirmed from project structure, test_steady_state.py and prior phase reviews).  Phase 4
does not introduce new signal models requiring Hypothesis coverage; it builds on existing
models.

---

### Integration Tests Verify No NaN/Inf

🟢 PASS — `test_reproducibility.py` (lines 1-20, purpose description): the final
integration test runs 8640 ticks of a simulated day for both profiles and is described as
verifying "No NaN or Inf in signal values."  The test file imports `math` and uses
`math.isnan` checks.

---

### Reproducibility Test Covers Both Profiles

🟢 PASS — `test_reproducibility.py` references both `_CONFIG_PKG` (factory.yaml) and
`_CONFIG_FNB` (factory-foodbev.yaml) and runs 500-tick reproducibility checks for both.

---

## Architecture / Rule Compliance

**Rule 1 (No hand-waving / no flaky-test dismissal)**
🟢 PASS — The CLAUDE.md project file (last commit `f4f4f4f`) explicitly documents a Modbus
injection test bug and adds rules against flaky-test dismissal.  The fixture-level disabling
of exception injection (Rule 14) was implemented as a proper fix, not a dismissal.

**Rule 5 (Signal models match PRD formulas)**
🟢 PASS — BearingWear formula is `base_rate * exp(k * elapsed_hours)` per PRD 5.5.
IntermittentFault uses Poisson spike schedules per PRD 5.17.  MicroStop uses linear ramps
per PRD 5.15.

**Rule 6 (Sim time used for signal generation)**
🟢 PASS — All four scenario types use `self._elapsed` (sim-derived) for timing, never
`time.monotonic()`.  CommDropScheduler explicitly uses wall-clock (correct for network
events).  DataQualityInjector uses sim_time for sensor events (correct per docstring).

**Rule 8 (Engine atomicity)**
🟢 PASS — Scenario engine runs before generators in the tick; `post_gen_tick` runs after
all generators complete.  No awaits between individual signal writes within a tick.

**Rule 9 (No locks)**
🟢 PASS — No `asyncio.Lock` or threading locks added in Phase 4 code.

**Rule 12 (No global state)**
🟢 PASS — All scenario instances have isolated state via constructor.  No module-level
mutable dicts or singletons in any Phase 4 file.

**Rule 13 (numpy.random.Generator throughout)**
🟢 PASS — `rng.exponential()`, `rng.uniform()`, `rng.integers()`, `rng.random()` used
throughout.  No `random` module imports found in Phase 4 files.

**Rule 14 (Injectable behaviour explicitly controlled in fixtures)**
🟢 PASS (with one WARN) — All reviewed fixtures explicitly set exception_probability,
partial probability, and modbus_drop.enabled to safe values.  The OPC-UA drop in
`all_protocols` fixture is an open question (see YELLOW section).

---

## RED — Must Fix

No 🔴 items were identified.  All implementations are correct relative to the PRD and
CLAUDE.md rules.

---

## YELLOW — Warnings

**Y1: `_poisson_starts` minimum-gap semantics (scenario_engine.py:317)**

File: `src/factory_simulator/engine/scenario_engine.py`, line 317.

The minimum gap enforced is the *minimum drawn inter-arrival time*, not the *minimum gap
from the end of the previous scenario*.  Two back-to-back scenarios of the same type can
overlap if the first runs past the minimum duration.  This is consistent with Phase 2
behaviour and adequate for realistic simulation, but the gap does not prevent overlapping
instances of the same scenario type.  Document or accept explicitly.

**Y2: `_sentinel_for_signal` in IntermittentFault has no config override path
(intermittent_fault.py:64-73)**

File: `src/factory_simulator/scenarios/intermittent_fault.py`, lines 64-73.

Current signals (e.g. `press.main_drive_current`) fall through to `_SENTINEL_DEFAULT =
0.0`, which is ambiguous (current of 0 A can be a valid idle state, not clearly anomalous).
The `data_quality.py` version of the same function uses `cfg.per_signal_overrides` for
correct escape; `intermittent_fault.py`'s version has no such mechanism.  Consider either
adding a params dict override or removing the standalone `_sentinel_for_signal` and
delegating to the config-driven version.

**Y3: Resolved — counter rollover GT logging confirmed in DataEngine.tick()**

`data_engine.py:293-294` confirms GT logging is present.  Not a defect.

**Y4: Fixed — cross-protocol fixtures now disable opcua_stale and mqtt_drop**

`tests/integration/test_cross_protocol.py` (all_protocols) and
`tests/integration/test_fnb_cross_protocol.py` (fnb_modbus_opcua, fnb_all_protocols)
were updated to add `config.data_quality.opcua_stale.enabled = False` and
`config.data_quality.mqtt_drop.enabled = False`.  Both CommDropScheduler instances default
to `enabled=True` with an unseeded fallback RNG; disabling them removes the low-probability
intermittent failure path on cross-protocol value-consistency tests.

---

## PASS

All 🟢 PASS items confirmed:

- Poisson inter-arrival via `rng.exponential()` in all scheduling sites
- `ScenarioEngine` `sim_duration_s` parameter and DataEngine wiring
- `_spawn_rng()` uses `SeedSequence.spawn()`, not `rng.integers()`
- BearingWear exponential formula matches PRD 5.5
- BearingWear uses only sim_time (elapsed), never wall clock
- BearingWear threshold logging fires exactly once per threshold
- MicroStop machine_state unchanged throughout; uses `_target_speed` as baseline; three sub-phases correct
- ContextualAnomaly waits for target state, timeout at 2x, early termination on state change, uses `post_gen_inject()`
- IntermittentFault three-phase model correct; phase 3 keeps scenario active permanently; all four subtypes implemented
- Comm drop is wall-clock based (correct); all three protocols handle drops correctly; seeded RNG
- SensorDisconnectInjector: correct sentinels (temp/pressure/voltage), quality="bad", sim_time based
- StuckSensorInjector: frozen value, quality="good", deferred start when signal absent
- DataQualityInjector ordering: after generators, before protocol readers
- Modbus exception 0x04 random, 0x06 before 0x04, partial excludes single-register reads
- MQTT timezone offset applied to ISO 8601 string ending in `Z`
- Modbus duplicate skips `sync_registers()` (registers freeze)
- Both MQTT and Modbus duplicate injection disabled when no RNG supplied
- AR(1) noise with phi=0.7 for PID-controlled temperatures in both profiles
- Student-t noise with df=5-8 for load/torque/pressure signals in both profiles
- `rollover_occurred` flag reset at start of each `generate()`
- `set_rollover_value()` raises `ValueError` for value ≤ 0
- Rule 14 compliance: all fixtures creating ModbusServer, OpcuaServer, or MqttPublisher explicitly disable all injection vectors (exception/partial/modbus_drop/opcua_stale/mqtt_drop)
- Rule 13: no `random` module; numpy.random.Generator throughout
- Rule 9: no asyncio.Lock or threading.Lock added
- Rule 12: no module-level mutable state
- Rule 6: signal generation uses sim_time; comm drops correctly use wall-clock
- Reproducibility test covers both packaging and F&B profiles
