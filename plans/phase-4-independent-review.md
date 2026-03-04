# Phase 4 Independent Code Review

**Reviewer:** Independent subagent (Opus 4)
**Date:** 2026-03-04
**Scope:** Phase 4 — Full Scenario System and Data Quality

---

## 1. Executive Summary

Phase 4 is a substantial, well-executed body of work (~12,100 lines across 58 files) implementing Poisson scheduling, four advanced scenario types, comprehensive data quality injection, noise calibration for both profiles, and a reproducibility test suite. The implementation is overwhelmingly correct against the PRD, with no critical defects found. I identified 0 RED issues, 4 YELLOW issues (two matching the local agent's findings, two new), and 15 GREEN checklist items. The code quality, documentation, and adherence to project rules (sim-time vs wall-clock, numpy.random.Generator, no locks, no global state) are exemplary.

---

## 2. Local Agent Review Assessment

### Grade: **A-**

The local agent's self-review (`plans/phase-4-review.md`) is thorough, well-structured, and accurate. It correctly identifies all major checklist items, provides precise file:line references, and demonstrates genuine code reading rather than surface-level checking.

**What it got right:**
- Correctly identified Y1 (Poisson min-gap semantics) as a genuine design trade-off
- Correctly identified Y2 (sentinel value for current signals in intermittent fault) as a real gap
- Accurately verified the exponential formula in BearingWear against PRD 5.5
- Correctly traced the `post_gen_inject` hook wiring for contextual anomalies
- Accurately confirmed wall-clock vs sim-time separation across all components
- Properly validated Rule 13/14 compliance across all test fixtures
- Identified and resolved Y4 (cross-protocol fixture comm drop disabling) during the review

**What it missed or understated:**
- **Missed:** The reproducibility test runs 1 simulated day, not the PRD-specified 7 days at 100x. The test compensates by boosting scenario frequencies, which is pragmatic but not equivalent to the exit criteria.
- **Missed:** No handling for two state_changing scenarios activating on the same tick — they both pass through the priority resolution loop and both activate. This is statistically rare with Poisson scheduling but the code has no guard against it.
- **Missed:** "Scan cycle quantisation and phase jitter per controller" listed in the Phase 4 PRD requirements is not implemented and has no corresponding task in `phase-4-tasks.json`. This is either deferred to Phase 5 or an omission.
- **Minor omission:** Did not verify the OPC-UA comm drop integration in detail (acknowledged in the review as "not reviewed in this phase" for OPC-UA but stated comm drop was "part of Phase 2"). Given that `CommDropScheduler` was Phase 4 code, the OPC-UA integration should have been fully verified.

The grade is A- rather than A because of the missed items above, particularly the 7-day vs 1-day test discrepancy which relates directly to exit criteria compliance.

---

## 3. Independent Findings

### 3.1 Poisson Scheduling — 🟢 GREEN

| Item | Verdict | Detail |
|------|---------|--------|
| `rng.exponential(mean_interval)` | ✅ | `scenario_engine.py:316` — correct exponential distribution |
| Minimum gap enforcement | ✅ | `scenario_engine.py:317` — `max(gap, min_gap_s)` applied |
| `SeedSequence.spawn()` | ✅ | `scenario_engine.py:931` — correct, no `rng.integers()` |
| `sim_duration_s` wired | ✅ | `data_engine.py:162-166` — passed from config with fallback |

**Note on min-gap semantics (echoing Y1):** The min_gap uses `cfg.duration_seconds[0]` (the minimum possible duration), not the actual drawn duration of the previous instance. This means two instances could theoretically overlap if the first runs longer than the minimum. This is acceptable for simulation purposes and consistent across all phases.

### 3.2 Priority System — 🟢 GREEN (with note)

| Item | Verdict | Detail |
|------|---------|--------|
| State_changing preempts non_state_changing | ✅ | `scenario_engine.py:202-206` |
| Non_state_changing deferred when state_changing active | ✅ | `scenario_engine.py:215-218` |
| Background/micro never blocked | ✅ | `scenario_engine.py:220` — falls through |
| Implementation in `tick()` | ✅ | Correct two-phase loop design |

**Edge case (not in local review):** When two state_changing scenarios are due on the same tick, both pass through the priority resolution loop (each preempts non_state_changing but not each other), and both activate in the evaluate loop. There is no mutual exclusion between simultaneous state_changing activations. With Poisson scheduling this is extremely unlikely (requires identical start times at tick granularity), but the code has no guard. This is a minor design gap, not a bug, since the simulation would still run — just with potentially conflicting state changes where the last-evaluated scenario "wins."

### 3.3 Bearing Wear — 🟢 GREEN

| Item | Verdict | Detail |
|------|---------|--------|
| Exponential formula: `base_rate * exp(k * elapsed_hours)` | ✅ | `bearing_wear.py:226` — exact PRD 5.5 match |
| Uses sim_time not wall clock | ✅ | `bearing_wear.py:223` — `self._elapsed / 3600.0` |
| Warning/alarm thresholds logged once | ✅ | `bearing_wear.py:282-309` — guarded by `_warning_logged`/`_alarm_logged` |
| Optional failure culmination | ✅ | `bearing_wear.py:250-253` |
| Background priority | ✅ | `bearing_wear.py:67` |
| Current increase follows same curve | ✅ | `bearing_wear.py:239-243` — `current_factor * exp(k * elapsed_hours)` |

### 3.4 Micro-Stops — 🟢 GREEN

| Item | Verdict | Detail |
|------|---------|--------|
| Machine state stays Running | ✅ | `micro_stop.py` never modifies machine_state |
| Speed dip 30-80% | ✅ | `micro_stop.py:160-163` — `baseline * (1 - drop_pct/100)` |
| Three sub-phases (ramp_down, hold, ramp_up) | ✅ | `micro_stop.py:204-214` |
| Poisson inter-arrival | ✅ | `scenario_engine.py:826-841` |
| Micro priority | ✅ | `micro_stop.py:75` |
| Recovery ramp restores saved target | ✅ | `micro_stop.py:207-211` and `_on_complete:219-225` |

### 3.5 Contextual Anomalies — 🟢 GREEN

| Item | Verdict | Detail |
|------|---------|--------|
| All 5 types implemented | ✅ | `contextual_anomaly.py:61-103` — heater_stuck, pressure_bleed, counter_false_trigger, hot_during_maintenance, vibration_during_off |
| Wait for target state | ✅ | `contextual_anomaly.py:247-263` |
| Timeout at 2x duration | ✅ | `contextual_anomaly.py:171` — `self._timeout_s = 2.0 * self._duration_s` |
| Early termination on state change | ✅ | `contextual_anomaly.py:271-273` |
| `post_gen_inject` hook | ✅ | `contextual_anomaly.py:281-306` — correctly writes after generators |
| Non_state_changing priority | ✅ | `contextual_anomaly.py:124` |
| Probability-weighted type selection | ✅ | `contextual_anomaly.py:137-152` — categorical draw |

All 5 contextual anomaly types match PRD 5.16:
1. heater_stuck → coder.printhead_temp at 40-42°C during Off/Standby ✅
2. pressure_bleed → coder.ink_pressure at 800-850 mbar during Off ✅
3. counter_false_trigger → press.impression_count increments during Idle ✅
4. hot_during_maintenance → press.dryer_temp_zone_1 at 100°C during Maintenance ✅
5. vibration_during_off → vibration.main_drive_x at 3-5 mm/s during Off ✅

### 3.6 Intermittent Faults — 🟢 GREEN

| Item | Verdict | Detail |
|------|---------|--------|
| 3-phase model: sporadic → frequent → permanent | ✅ | `intermittent_fault.py:313-329` |
| All 4 subtypes | ✅ | bearing, electrical, sensor, pneumatic all dispatched in `_apply_spike` |
| Phase 3 permanent | ✅ | `intermittent_fault.py:310-311` — `_phase3_active` prevents completion |
| Spike scheduling Poisson | ✅ | `intermittent_fault.py:531-570` — `rng.exponential(mean_interval)` |
| Ground truth logging | ✅ | Phase transitions and individual spikes logged |
| Background priority | ✅ | `intermittent_fault.py:106` |
| Pneumatic: no Phase 3 | ✅ | `intermittent_fault.py:325-328` — completes after Phase 2 when `not phase3_transition` |

All 4 subtypes match PRD 5.17:
1. Bearing vibration → vibration.main_drive_x/y/z spikes to 15-25 mm/s ✅
2. Electrical → press.main_drive_current spikes by 20-50% ✅
3. Sensor → any signal reports sentinel value ✅
4. Pneumatic → coder.ink_pressure drops to 0 ✅

### 3.7 Comm Drop Injection — 🟢 GREEN

| Item | Verdict | Detail |
|------|---------|--------|
| All 3 protocols | ✅ | Modbus: `modbus_server.py:1053-1059`, MQTT: `mqtt_publisher.py:617-619`, OPC-UA: `opcua_server.py:397-407` |
| Wall-clock usage | ✅ | `comm_drop.py:8` — docstring + `time.monotonic()` throughout |
| Deterministic with seed | ✅ | `CommDropScheduler.__init__` accepts `rng: np.random.Generator` |
| Modbus: registers freeze | ✅ | Skips `sync_registers()` during drop |
| OPC-UA: UncertainLastUsableValue | ✅ | `opcua_server.py:372` — writes `UncertainLastUsableValue` status |
| MQTT: stops publishing | ✅ | `mqtt_publisher.py:617` — skips `_publish_due()` |

### 3.8 Sensor Disconnect / Stuck — 🟢 GREEN

| Item | Verdict | Detail |
|------|---------|--------|
| Correct sentinels: temp=6553.5, pressure=0.0, voltage=-32768 | ✅ | `data_quality.py:54-72` |
| Disconnect quality = "bad" | ✅ | `data_quality.py:186` |
| Stuck quality = "good" | ✅ | `data_quality.py:320` |
| After generators, before protocols | ✅ | `data_engine.py:293-298` (post_gen_tick → data_quality.tick) |
| Config override via `per_signal_overrides` | ✅ | `data_quality.py:56-57` |
| Ground truth logging | ✅ | Both injectors log events |
| Deferred start when signal absent | ✅ | `data_quality.py:291-293` — re-schedules |

### 3.9 Modbus Exceptions / Partial — 🟢 GREEN

| Item | Verdict | Detail |
|------|---------|--------|
| 0x04 random probability | ✅ | `modbus_server.py:249-259` |
| 0x06 during state transitions | ✅ | `modbus_server.py:648-651` — 0.5s window |
| 0x06 checked before 0x04 | ✅ | `modbus_server.py:375-387` |
| Partial excludes single-register | ✅ | `modbus_server.py:280` — `count < 2` guard |
| Partial returns 1 to count-1 | ✅ | `modbus_server.py:284` — `rng.integers(1, count)` |
| Probabilities configurable | ✅ | Via `DataQualityConfig` Pydantic model |

### 3.10 Duplicate Timestamps / Timezone — 🟢 GREEN

| Item | Verdict | Detail |
|------|---------|--------|
| MQTT offset applied correctly | ✅ | `mqtt_publisher.py:167` — `offset_hours * 3600.0` added, `Z` suffix kept |
| Modbus duplicate freezes registers | ✅ | `modbus_server.py:1054-1059` — skips `sync_registers()` |
| MQTT duplicate: same payload within 1ms | ✅ | `mqtt_publisher.py:511-517` — second `publish()` call |
| Both disabled when no RNG | ✅ | Guarded by `self._dup_rng is not None` |
| MQTT probability = config/2 (0.005% vs 0.01%) | ✅ | `mqtt_publisher.py:443` — matches PRD 10.5 defaults |

### 3.11 Noise Calibration — 🟢 GREEN

| Item | Verdict | Detail |
|------|---------|--------|
| AR(1) phi=0.7 for PID temps | ✅ | `factory.yaml:171-172,189-190,207-208` (dryer zones), `428-429` (laminator), `599-600` (coder printhead) |
| Student-t df=5-8 for load/torque | ✅ | `factory.yaml:339` (web_tension df=8), `626` (current df=6), `776-808` (vibration df=5) |
| `factory.yaml` sigma values match PRD 10.3 | ✅ | Spot-checked: ink_pressure sigma=60 mbar ✅, web_tension sigma=5.0 N ✅, dryer_temp sigma=0.3 C ✅ |
| F&B profile calibrated | ✅ | `factory-foodbev.yaml` uses AR(1) for oven zones, Student-t for torque/pressure |

### 3.12 Counter Rollover — 🟢 GREEN

| Item | Verdict | Detail |
|------|---------|--------|
| Configurable `rollover_value` | ✅ | `counter.py:161` — `set_rollover_value()` |
| Wrap to 0 (via modulo) | ✅ | `counter.py:208` — `self._value % self._rollover_value` |
| Ground truth logging | ✅ | `data_engine.py:293-294` — checks `counter.rollover_occurred` |
| ValueError for ≤0 | ✅ | `counter.py:161` |
| Config overrides applied | ✅ | `data_engine.py:152-155` — iterates `config.data_quality.counter_rollover` |

### 3.13 Reproducibility — 🟡 YELLOW (Y3)

| Item | Verdict | Detail |
|------|---------|--------|
| Same seed → identical output | ✅ | `test_reproducibility.py` — both 500-tick and full-day tests |
| Both profiles tested | ✅ | Packaging and F&B each have seed=42 reproducibility tests |
| Different seeds differ | ✅ | seed=42 vs seed=43 comparison |
| **7-day run per PRD exit criteria** | ⚠️ | Test runs 1 simulated day, not 7 days as specified in Appendix F |

The PRD Phase 4 exit criteria states: "Run each profile for 7 days at 100x in batch mode (under 2 real hours)." The test runs 1 day at 100x (~1.4 real minutes for 8640 ticks). Scenario frequencies are boosted to compensate, which ensures all types fire, but this is not equivalent to verifying stability/correctness over 7 simulated days. The 7-day run would exercise more counter rollovers, intermittent fault phase transitions, and memory stability at longer timescales.

### 3.14 Data Quality Integration — 🟢 GREEN

| Item | Verdict | Detail |
|------|---------|--------|
| DataQualityInjector wired in tick | ✅ | `data_engine.py:298` |
| After generators, before protocols | ✅ | Ordering: scenarios → generators → post_gen_tick → data_quality.tick |
| Global disable via config | ✅ | Each sub-injector checks `cfg.enabled` |
| Per-section disable | ✅ | `sensor_disconnect.enabled`, `stuck_sensor.enabled` independently configurable |
| Spawned child RNGs | ✅ | `data_engine.py:180-181` — separate `disconnect_rng` and `stuck_rng` |

### 3.15 Config Completeness — 🟢 GREEN

| Item | Verdict | Detail |
|------|---------|--------|
| All new config models validate | ✅ | Pydantic v2 models with `field_validator` and `model_validator` |
| Range pair validation | ✅ | `_validate_range_pair()` on all `[min, max]` fields |
| Probability bounds checking | ✅ | `_prob_range` validator on all probability fields |
| Both YAMLs load | ✅ | Reproducibility test loads both without error |
| MicroStopConfig, ContextualAnomalyConfig, IntermittentFaultConfig | ✅ | All present with correct nesting |
| DataQualityConfig complete | ✅ | All PRD 10.x features represented |

---

## 4. Issues Table

| ID | Severity | File(s) | Description | Recommended Fix |
|----|----------|---------|-------------|-----------------|
| Y1 | 🟡 YELLOW | `scenario_engine.py:317` | Poisson min-gap uses `cfg.duration_seconds[0]` (minimum possible), not actual drawn duration. Two same-type instances can theoretically overlap. | Document as intentional or change gap to track actual duration of previous instance. Low real-world impact. |
| Y2 | 🟡 YELLOW | `intermittent_fault.py:64-73` | `_sentinel_for_signal()` returns `_SENTINEL_DEFAULT = 0.0` for current signals (e.g. `press.main_drive_current`). Zero current is not a clear fault indicator. No config override mechanism (unlike `data_quality.py`'s version). | Add a `"current"` check returning a distinct sentinel (e.g. `-1.0` or `6553.5`), or allow per-signal overrides via params dict, consistent with `data_quality.py`'s `per_signal_overrides`. |
| Y3 | 🟡 YELLOW | `tests/integration/test_reproducibility.py` | Integration test runs 1 simulated day, not the 7 days specified in PRD Appendix F exit criteria. Scenario frequencies are boosted to compensate, but long-duration stability (memory, counter rollovers, intermittent fault Phase 3 progression) is not fully exercised. | Add a 7-day integration test (possibly marked `@pytest.mark.slow`). At 100x with 100ms ticks, 7 days = 60,480 ticks ≈ 100 real seconds. Very feasible. |
| Y4 | 🟡 YELLOW | `scenario_engine.py:197-220` | No mutual exclusion between two state_changing scenarios activating on the same tick. Both activate and potentially issue conflicting state changes. Extremely unlikely with Poisson scheduling but code has no guard. | Add a check: once one state_changing scenario activates in a tick, defer subsequent state_changing activations (add them to `skip_ids`). Or document as accepted risk. |

---

## 5. Comparison: Issues Missed by Local Agent

| Issue | Local Agent Found? | Notes |
|-------|-------------------|-------|
| Y1 (Poisson min-gap) | ✅ Yes | Accurately described |
| Y2 (sentinel for current) | ✅ Yes | Accurately described |
| Y3 (1-day vs 7-day test) | ❌ No | Local agent said "Reproducibility: 🟢 PASS" without noting the exit criteria discrepancy |
| Y4 (simultaneous state_changing) | ❌ No | Local agent checked priority system thoroughly but didn't analyze this edge case |
| Scan cycle quantisation omission | ❌ No | Phase 4 PRD lists "Scan cycle quantisation and phase jitter per controller" but no task was created for it. This may be intentionally deferred to Phase 5 (which handles "per-controller" network topology), but neither the local review nor the task list addresses it. |

Note: The scan cycle item is not counted as a YELLOW issue because it appears to be a Phase 5 scope item based on context (Phase 5 handles per-controller networking). However, it is listed in the Phase 4 bullet points in Appendix F.

---

## 6. Verdict

### **CONDITIONAL GO**

**Conditions for full GO:**

1. **Y3 (7-day test):** Add a 7-day at 100x integration test for both profiles. This can be a slow-marked test. At 100ms tick intervals and 100x time scale, 7 simulated days = 60,480 ticks ≈ 101 real seconds per profile. Very feasible. This directly addresses the PRD exit criteria.

2. **Y4 (simultaneous state_changing):** Either add a one-line guard in `tick()` to skip subsequent state_changing activations once one activates in a given tick, or explicitly document this as accepted risk in a code comment.

**Accepted without blocking:**

- Y1 (Poisson min-gap semantics): Consistent with prior phases, adequate for simulation, low risk.
- Y2 (sentinel for current signals): Edge case only matters for the sensor intermittent subtype, which is disabled by default. Can be fixed in a follow-up.

**Overall assessment:** The implementation is high quality. The code is well-documented with PRD references, follows all project rules (Rule 6, 9, 12, 13, 14), has comprehensive test coverage, and correctly implements the PRD specifications. The two conditions are minor and can be resolved in a few hours. Phase 4 is essentially complete.
