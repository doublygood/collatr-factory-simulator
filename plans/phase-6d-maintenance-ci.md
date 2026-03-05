# Phase 6d: Maintenance & CI

**Scope:** YELLOW issues Y16-Y24, Y27 from the three-reviewer code review. Y24 (Dockerfile editable install) was already fixed in Phase 6a — skipped. Y25 (inactive profile nodes) and Y26 (LWT topic) moved to Phase 6e (protocol polish).
**Depends on:** Phase 6c complete.

---

## Task 6d.1 — Shared Reference Epoch Constant

**Review ref:** Y18 (review-architecture.md §6.2)

**Problem:** `_REFERENCE_EPOCH_TS` is defined identically in 3 files:
- `src/factory_simulator/protocols/mqtt_publisher.py:52`
- `src/factory_simulator/protocols/opcua_server.py:53`
- `src/factory_simulator/health/server.py:42`

All compute `datetime(2026, 1, 1, tzinfo=UTC).timestamp()`. A fourth instance exists in `engine/ground_truth.py:441` where `_REFERENCE_EPOCH` is created inside `_format_time()` on every call (also addressed in 6d.2).

Additionally, `_sim_time_to_iso()` in `mqtt_publisher.py:150` and `_sim_time_to_datetime()` in `opcua_server.py:56` are separate implementations of essentially the same sim-time-to-wall-time conversion.

**Fix:**

1. Create a new module `src/factory_simulator/time_utils.py`:
   ```python
   """Shared time constants and conversion utilities.

   All simulation timestamps are offsets from the reference epoch
   (2026-01-01T00:00:00Z).  These utilities convert between sim_time
   floats and wall-clock datetime/ISO representations.
   """
   from __future__ import annotations

   from datetime import UTC, datetime

   # Reference epoch: 2026-01-01T00:00:00Z
   # All sim_time values are seconds from this epoch.
   REFERENCE_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
   REFERENCE_EPOCH_TS: float = REFERENCE_EPOCH.timestamp()


   def sim_time_to_datetime(sim_time: float, offset_s: float = 0.0) -> datetime:
       """Convert sim_time to a timezone-aware datetime.

       Parameters
       ----------
       sim_time:
           Seconds from the reference epoch.
       offset_s:
           Optional offset in seconds (e.g. clock drift).
       """
       return datetime.fromtimestamp(
           REFERENCE_EPOCH_TS + sim_time + offset_s, tz=UTC,
       )


   def sim_time_to_iso(sim_time: float, offset_s: float = 0.0) -> str:
       """Convert sim_time to ISO 8601 string with millisecond precision.

       Returns format: ``2026-01-01T00:00:00.000Z``
       """
       dt = sim_time_to_datetime(sim_time, offset_s)
       return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
   ```

2. Update `mqtt_publisher.py`:
   - Remove `_REFERENCE_EPOCH_TS` (line 52) and `_sim_time_to_iso()` (lines 150-170).
   - Import `from factory_simulator.time_utils import REFERENCE_EPOCH_TS, sim_time_to_iso`.
   - Replace `_sim_time_to_iso(sim_time, offset_hours)` calls with `sim_time_to_iso(sim_time, offset_hours * 3600.0)` (note: the current function takes `offset_hours`, the new one takes `offset_s` — convert at call site).

3. Update `opcua_server.py`:
   - Remove `_REFERENCE_EPOCH_TS` (line 53) and `_sim_time_to_datetime()` (lines 56-62).
   - Import `from factory_simulator.time_utils import sim_time_to_datetime`.
   - Replace `_sim_time_to_datetime(sim_time)` calls.

4. Update `health/server.py`:
   - Remove `_REFERENCE_EPOCH_TS` (line 42).
   - Import `from factory_simulator.time_utils import REFERENCE_EPOCH_TS`.
   - Replace all `_REFERENCE_EPOCH_TS` references.

5. Update `engine/ground_truth.py`:
   - Remove the per-call `_REFERENCE_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)` from `_format_time()`.
   - Import `from factory_simulator.time_utils import sim_time_to_iso`.
   - Replace the body of `_format_time()` with a call to `sim_time_to_iso(sim_time)`. This also fixes the Y17 performance issue (deferred to 6d.2 for the explicit perf fix, but the deduplication handles it implicitly).

**Tests:**
- `tests/unit/test_time_utils.py` (new file):
  - `test_reference_epoch_value` — verify it matches `datetime(2026, 1, 1, UTC).timestamp()`.
  - `test_sim_time_to_datetime_zero` — sim_time=0 returns 2026-01-01T00:00:00Z.
  - `test_sim_time_to_datetime_offset` — with offset_s, datetime shifts by that amount.
  - `test_sim_time_to_iso_format` — verify ISO 8601 format with milliseconds and Z suffix.
  - `test_sim_time_to_iso_offset` — verify offset is applied.
- Verify all existing tests still pass (the module-level constants were test-visible).

**Files:** New: `src/factory_simulator/time_utils.py`, `tests/unit/test_time_utils.py`. Modified: `mqtt_publisher.py`, `opcua_server.py`, `health/server.py`, `engine/ground_truth.py`.

---

## Task 6d.2 — `_format_time()` Performance Fix

**Review ref:** Y17 (review-architecture.md §6.7)

**Problem:** `GroundTruthLogger._format_time()` creates a new `datetime(2026, 1, 1, tzinfo=UTC)` object on every call. At 10 Hz tick rate × 100x time scale = 1000 calls/sec, this is wasteful.

**Fix:**

This is largely solved by Task 6d.1 if `_format_time()` delegates to `sim_time_to_iso()` from `time_utils.py` (which uses the module-level `REFERENCE_EPOCH_TS` constant).

If 6d.1 is already done:
- Verify that `_format_time()` now delegates to `sim_time_to_iso()` and no longer creates a datetime per call.
- If it still has the per-call pattern, replace with the import.

If 6d.1 is NOT done yet (dependency not met — but it should be, they're ordered):
- Promote the `_REFERENCE_EPOCH` and its `.timestamp()` to class-level constants:
  ```python
  _REFERENCE_EPOCH_TS = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
  ```
  Then use the constant in `_format_time()`.

**Tests:**
- No new tests needed — the format output is unchanged. Verify existing ground truth tests pass.
- Optionally: a micro-benchmark is overkill for this, but you can verify the constant is module-level.

**Files:** `src/factory_simulator/engine/ground_truth.py`

---

## Task 6d.3 — Configurable Health Server Port

**Review ref:** Y16 (review-architecture.md §6.3)

**Problem:** Health server port 8080 is hardcoded in `cli.py:446`. Not configurable via config file or environment variable.

**Fix:**

1. Add `health_port` field to `SimulationConfig` in `config.py`:
   ```python
   health_port: int = 8080

   @field_validator("health_port")
   @classmethod
   def _health_port_range(cls, v: int) -> int:
       if not (0 <= v <= 65535):
           raise ValueError("health_port must be 0-65535")
       return v
   ```

2. Add `SIM_HEALTH_PORT` to the environment variable override map (wherever env overrides are processed — check `_load_config()` or the env override section in config.py).

3. Update `_run_realtime()` in `cli.py`:
   ```python
   health = HealthServer(port=config.simulation.health_port, store=engine.store)
   ```

4. Update both YAML config files with an explicit comment:
   ```yaml
   simulation:
     # health_port: 8080  # HTTP health check port (default 8080)
   ```

**Tests:**
- `test_health_port_default` — SimulationConfig with no health_port → 8080.
- `test_health_port_custom` — SimulationConfig(health_port=9090) → 9090.
- `test_health_port_invalid` — SimulationConfig(health_port=70000) → ValidationError.
- `test_health_port_zero` — SimulationConfig(health_port=0) → valid (OS-assigned).

**Files:** `src/factory_simulator/config.py`, `src/factory_simulator/cli.py`, `tests/unit/test_config.py`

---

## Task 6d.4 — Server Task Verification After Startup

**Review ref:** Y20 (review-architecture.md §3.2)

**Problem:** In `_run_realtime()`, after each `asyncio.create_task(srv.start())`, the code calls `await asyncio.sleep(0.05)` to allow binding but never checks `task.done()`. If the server fails to bind (port already in use), the exception is deferred and only surfaces later (or never, if swallowed).

**Fix:**

After each `await asyncio.sleep(0.05)` following a server start, check if the task failed:

```python
task = asyncio.create_task(srv.start())
tasks.append(task)
servers.append(srv)
await asyncio.sleep(0.05)  # allow server to bind

# Verify the server started successfully
if task.done():
    # Task completed or failed already — check for exceptions
    exc = task.exception()  # raises CancelledError if cancelled
    if exc is not None:
        raise RuntimeError(
            f"Server failed to start: {exc}"
        ) from exc
```

Apply this pattern to:
- Each Modbus server creation (lines ~455-459)
- Each OPC-UA server creation (lines ~462-466)
- MQTT publishers (lines ~469-472) — note MQTT tasks may not fail immediately due to the retry logic added in 6b.1, but check anyway.
- Health server (line ~448)

**Implementation note:** Extract a helper to avoid code repetition:
```python
async def _start_server(
    srv: Any, tasks: list[asyncio.Task[None]], servers: list[Any], label: str,
) -> None:
    task = asyncio.create_task(srv.start())
    tasks.append(task)
    servers.append(srv)
    await asyncio.sleep(0.05)
    if task.done() and not task.cancelled():
        exc = task.exception()
        if exc is not None:
            raise RuntimeError(f"{label} failed to start: {exc}") from exc
```

**Tests:**
- `test_server_startup_failure_detected` — mock a server whose `start()` raises `OSError("Address in use")` immediately. Verify `RuntimeError` propagates with the original exception.
- `test_server_startup_success` — normal startup proceeds without error.

**Files:** `src/factory_simulator/cli.py`, `tests/unit/test_cli.py`

---

## Task 6d.5 — Narrow Exception Suppression During Shutdown

**Review ref:** Y27 (review-architecture.md §2.2)

**Problem:** `contextlib.suppress(Exception)` at `cli.py:490` swallows ALL exceptions during server shutdown, including `RuntimeError` or unexpected errors that indicate a real problem. This masks bugs.

**Current code (line 490):**
```python
with contextlib.suppress(Exception):
    await srv.stop()
```

**Fix:**

Narrow the suppression to the specific exception types expected during shutdown:
```python
with contextlib.suppress(asyncio.CancelledError, OSError, ConnectionError):
    await srv.stop()
```

- `asyncio.CancelledError` — task was cancelled (normal during shutdown).
- `OSError` — socket already closed, port issues, etc.
- `ConnectionError` — connection already dropped (MQTT broker gone).

Any other exception (e.g. `RuntimeError`, `TypeError`, `AttributeError`) should propagate so bugs are visible.

**Tests:**
- `test_shutdown_suppresses_cancelled_error` — verify CancelledError during stop is suppressed.
- `test_shutdown_suppresses_oserror` — verify OSError during stop is suppressed.
- `test_shutdown_propagates_runtime_error` — verify RuntimeError during stop is NOT suppressed.

**Files:** `src/factory_simulator/cli.py`, `tests/unit/test_cli.py`

---

## Task 6d.6 — Dead Config Cleanup (sparkplug_b, retain)

**Review ref:** Y22 + Y23 (review-architecture.md §6.1)

**Problem:**
- `MqttProtocolConfig.sparkplug_b` (config.py line 165): defined as `bool = False` but no code in the MQTT publisher reads it. Sparkplug B is not implemented and won't be in MVP.
- `MqttProtocolConfig.retain` (config.py line 166): defined as `bool = True` but `_retain_for_topic()` derives retain per-topic from topic prefixes, ignoring this global flag. The field is misleading.

**Fix:**

**Option A (remove both):** Delete both fields. Clean and honest. Any existing YAML configs with `sparkplug_b:` or `retain:` keys will raise `ValidationError` due to Pydantic's `extra="forbid"` (if enabled) or silently ignore (if `extra="allow"` or `extra="ignore"`).

**Option B (deprecate with comments):** Keep both fields but add clear docstring comments marking them as unused/deprecated. Minimal disruption.

**Go with Option A** — remove both fields. Check the Pydantic model's `model_config` for the `extra` setting:
- If `extra="forbid"`: removing the fields means existing YAML configs with these keys will fail validation. Check both YAML config files and remove the keys from there too.
- If `extra="allow"` or `extra="ignore"`: existing YAML configs with these keys will be silently ignored. Still remove from YAML for cleanliness.

**Steps:**
1. Remove `sparkplug_b: bool = False` from `MqttProtocolConfig`.
2. Remove `retain: bool = True` from `MqttProtocolConfig`.
3. Check `config/factory.yaml` and `config/factory-foodbev.yaml` — remove `sparkplug_b` and `retain` keys from the `mqtt` section if present.
4. Grep the codebase for any references to `sparkplug_b` or `.retain` on the config object (not the `TopicEntry.retain` field or `_retain_for_topic()` — those are different). Remove or update.

**Tests:**
- `test_mqtt_config_no_sparkplug_field` — verify `MqttProtocolConfig` does not have a `sparkplug_b` attribute.
- `test_mqtt_config_no_retain_field` — verify `MqttProtocolConfig` does not have a `retain` attribute.
- Verify both YAML configs still load without error.
- Verify MQTT publisher tests still pass.

**Files:** `src/factory_simulator/config.py`, `config/factory.yaml`, `config/factory-foodbev.yaml`, `tests/unit/test_config.py`

---

## Task 6d.7 — Generator Test Files (Coder)

**Review ref:** Y19 (review-architecture.md §5.1)

**Problem:** 5 generator modules lack dedicated test files: coder, energy, laminator, slitter, vibration. This task covers the **coder** generator.

The coder is the most complex of the 5 untested generators (390 lines, 11 signals, state machine, multiple model types). It follows the press state (Printing when press is Running) and produces:
- `state` — coder state enum (Off/Ready/Printing/Fault/Standby)
- `prints_total` — counter (cumulative)
- `ink_level` — depletion model
- `printhead_temp` — steady state with noise
- `ink_pump_speed` — correlated with line speed
- `ink_pressure` — correlated with pump speed
- `ink_viscosity_actual` — random walk with mean reversion
- `supply_voltage` — steady state with noise
- `ink_consumption_ml` — counter
- `nozzle_health` — steady state, slow degradation
- `gutter_fault` — binary, rare event

**Test file:** `tests/unit/test_generators/test_coder.py`

Follow the existing test patterns (see `test_mixer.py`, `test_press.py`):
1. Helper `_make_coder_config()` creates a minimal `EquipmentConfig` with all 11 signals.
2. Helper `_run_ticks()` generates N ticks with a given press state in the store.

**Required tests:**
- `test_coder_off_state` — all signals at off/zero values when press is Off.
- `test_coder_printing_state` — signals active when press is Running (state=2). Printhead temp near target, pump speed > 0, ink level decreasing.
- `test_prints_counter_increments` — prints_total increases during Printing state.
- `test_ink_depletion` — ink_level decreases over time during Printing.
- `test_ink_viscosity_mean_reversion` — viscosity stays near center value over many ticks.
- `test_coder_deterministic` — same seed produces same output across two runs.
- `test_coder_signal_count` — generate() returns the expected number of SignalValues (11 signals).

**Files:** `tests/unit/test_generators/test_coder.py` (new)

---

## Task 6d.8 — Generator Test Files (Energy)

**Review ref:** Y19 (review-architecture.md §5.1)

The energy generator is simple (137 lines, 2 signals): `line_power` (correlated with press speed) and `cumulative_kwh` (counter).

**Test file:** `tests/unit/test_generators/test_energy.py`

**Required tests:**
- `test_energy_power_correlates_with_speed` — line_power increases when press speed increases.
- `test_energy_cumulative_increases` — cumulative_kwh increases monotonically during Running.
- `test_energy_idle_low_power` — line_power near baseline when press is Idle.
- `test_energy_deterministic` — same seed, same output.
- `test_energy_signal_count` — generate() returns 2 SignalValues.

**Files:** `tests/unit/test_generators/test_energy.py` (new)

---

## Task 6d.9 — Generator Test Files (Laminator)

**Review ref:** Y19 (review-architecture.md §5.1)

The laminator generator (195 lines, 5 signals): `nip_temp`, `nip_pressure`, `tunnel_temp`, `web_speed` (correlated with press speed), `adhesive_weight` (depletion model).

**Test file:** `tests/unit/test_generators/test_laminator.py`

**Required tests:**
- `test_laminator_follows_press` — web_speed tracks press line_speed (correlated follower).
- `test_laminator_nip_temp_tracks_setpoint` — nip_temp follows setpoint when Running.
- `test_laminator_adhesive_depletes` — adhesive_weight decreases during Running.
- `test_laminator_off_state` — signals at off/zero when press is Off.
- `test_laminator_deterministic` — same seed, same output.
- `test_laminator_signal_count` — generate() returns 5 SignalValues.

**Files:** `tests/unit/test_generators/test_laminator.py` (new)

---

## Task 6d.10 — Generator Test Files (Slitter)

**Review ref:** Y19 (review-architecture.md §5.1)

The slitter generator (230 lines, 3 signals): `speed` (follows press with lag), `web_tension` (correlated with speed), `reel_count` (counter).

**Test file:** `tests/unit/test_generators/test_slitter.py`

**Required tests:**
- `test_slitter_speed_follows_press` — speed tracks press line_speed with lag.
- `test_slitter_tension_correlates_with_speed` — web_tension changes with speed.
- `test_slitter_reel_count_increments` — reel_count increases during Running.
- `test_slitter_off_state` — all signals zero when press is Off.
- `test_slitter_deterministic` — same seed, same output.
- `test_slitter_signal_count` — generate() returns 3 SignalValues.

**Files:** `tests/unit/test_generators/test_slitter.py` (new)

---

## Task 6d.11 — Generator Test Files (Vibration)

**Review ref:** Y19 (review-architecture.md §5.1)

The vibration generator (167 lines, 3 signals): `main_drive_x`, `main_drive_y`, `main_drive_z` with Cholesky-correlated noise.

**Test file:** `tests/unit/test_generators/test_vibration.py`

**Required tests:**
- `test_vibration_running_nonzero` — all 3 axes produce non-zero values when press is Running.
- `test_vibration_stopped_near_zero` — all 3 axes near zero when press speed = 0.
- `test_vibration_axes_correlated` — run N ticks, compute pairwise sample correlations. Assert X-Y correlation > 0, X-Z > 0 (should be near 0.2 and 0.15 respectively).
- `test_vibration_cholesky_matrix_matches_prd` — class constant matches PRD 4.3.1 matrix.
- `test_vibration_deterministic` — same seed, same output.
- `test_vibration_signal_count` — generate() returns 3 SignalValues.

**Files:** `tests/unit/test_generators/test_vibration.py` (new)

---

## Task 6d.12 — CI Matrix: Python 3.13 + Integration Tests

**Review ref:** Y21 (review-architecture.md §5.5)

**Problem:**
- CI only tests on Python 3.12. The project specifies `requires-python >= "3.12"`, so 3.13 should be in the matrix for forward compatibility.
- Only `test_acceptance.py` runs in CI integration tests. 9 other integration test files are never validated.

**Fix:**

Update `.github/workflows/ci.yml`:

1. **Python version matrix** — add 3.13 to unit tests:
   ```yaml
   strategy:
     matrix:
       python-version: ["3.12", "3.13"]
   ```

2. **Integration tests** — run more integration test files. Some integration tests require a running Mosquitto broker (MQTT tests) and will self-skip when no broker is available. Others (Modbus, OPC-UA, cross-protocol, reproducibility) run standalone.

   Replace the current integration-tests job's pytest command with:
   ```yaml
   - name: Run integration tests (non-slow, no external broker)
     run: >-
       pytest
       tests/integration/
       -m "not slow"
       --ignore=tests/integration/test_mqtt_integration.py
       --tb=short -q
     timeout-minutes: 10
   ```

   This runs all integration tests EXCEPT:
   - `test_mqtt_integration.py` (requires Mosquitto broker — skip entirely in CI)
   - Tests marked `slow` (already excluded)

   **Alternative:** If some integration tests are too flaky for CI, add them to the ignore list. The key files to include:
   - `test_acceptance.py` (already there)
   - `test_cross_protocol.py`
   - `test_fnb_cross_protocol.py`
   - `test_modbus_integration.py`
   - `test_modbus_fnb_integration.py`
   - `test_opcua_integration.py`
   - `test_fnb_opcua_mqtt_integration.py` — check if this needs an MQTT broker; if yes, ignore it too
   - `test_oven_uid_routing_realistic.py`
   - `test_reproducibility.py`

3. **Lint and typecheck** — keep on 3.12 only (no need to lint twice).

4. **Add `cache-dependency-path`** for pip caching:
   ```yaml
   cache: "pip"
   cache-dependency-path: "requirements-dev.txt"
   ```

**Tests:** No new tests. CI changes are verified by the workflow running on the next push.

**Files:** `.github/workflows/ci.yml`

---

## Task 6d.13 — Validate All Fixes — Full Suite

**Depends on:** Tasks 6d.1-6d.12

**Steps:**
1. Run `ruff check src tests` — must be clean.
2. Run `mypy src` — must pass.
3. Run `pytest` — ALL tests must pass.
4. Run a batch simulation with both profiles to verify no regressions:
   - Packaging: `python -m factory_simulator run --batch-output /tmp/test-pkg --batch-duration 1h --seed 42`
   - F&B: `python -m factory_simulator run --config config/factory-foodbev.yaml --batch-output /tmp/test-fnb --batch-duration 1h --seed 42`
5. Verify both complete without error.
6. Fix any failures.

**Files:** None (validation only).

---

## Dependencies

```
6d.1 (shared epoch constant)  → independent
6d.2 (_format_time perf)      → depends on 6d.1
6d.3 (health port)            → independent
6d.4 (server task verify)     → independent
6d.5 (narrow suppress)        → independent
6d.6 (dead config cleanup)    → independent
6d.7 (test: coder)            → independent
6d.8 (test: energy)           → independent
6d.9 (test: laminator)        → independent
6d.10 (test: slitter)         → independent
6d.11 (test: vibration)       → independent
6d.12 (CI matrix)             → independent
6d.13 (validation)            → depends on ALL of 6d.1-6d.12
```

Only 6d.2 depends on 6d.1. All others are independent.

## Effort Estimate

- 6d.1: ~45 min (new module, update 4 files, tests)
- 6d.2: ~10 min (verify 6d.1 handled it, or small fixup)
- 6d.3: ~20 min (config field + CLI wiring + tests)
- 6d.4: ~25 min (helper function + apply to all server starts + tests)
- 6d.5: ~10 min (narrow suppression list + tests)
- 6d.6: ~15 min (remove 2 fields + config cleanup + tests)
- 6d.7-6d.11: ~30 min each (5 generator test files × 30 min = 2.5 hours)
- 6d.12: ~20 min (CI YAML updates)
- 6d.13: ~15 min (run suite)
- **Total: ~5-6 hours**
