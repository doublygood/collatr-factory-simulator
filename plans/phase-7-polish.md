# Phase 7: Polish

**Scope:** 4 new YELLOW issues from post-Phase 6 code quality review + 9 actionable GREEN items from the original three-reviewer deep review and the post-Phase 6 reviews.
**Depends on:** Phase 6e complete.

---

## Task 7.1 — Fix MQTT Retry Delays Tuple (CQ-Y1)

**Review ref:** review-post-phase6-code-quality.md Y1

**Problem:** `_delays = (1.0, 2.0, 4.0)` has 3 elements but the retry loop only makes 3 attempts (indices 0, 1, then raises). The `4.0` is dead data.

**Fix:**

In `src/factory_simulator/protocols/mqtt_publisher.py` around line 652:

Option A (simplest — trim the tuple):
```python
_delays = (1.0, 2.0)
```

Option B (use all 3 — 4 total attempts): Restructure the loop to use `len(_delays) + 1` attempts, sleeping `_delays[attempt]` for attempts 0..N-1.

**Choose Option B** — more retries is better for robustness. Restructure the loop:
```python
_delays = (1.0, 2.0, 4.0)
_max_attempts = len(_delays) + 1  # 4 attempts total

for attempt in range(_max_attempts):
    try:
        ...connect...
        return
    except (ConnectionRefusedError, OSError) as exc:
        last_exc = exc
        if attempt < len(_delays):
            await asyncio.sleep(_delays[attempt])
raise last_exc
```

**Tests:**
- Verify existing retry tests still pass (they test 3 failures → raise).
- Add a test verifying 4th attempt succeeds after 3 failures.

**Files:** `src/factory_simulator/protocols/mqtt_publisher.py`, `tests/unit/test_protocols/test_mqtt.py`

---

## Task 7.2 — Extract SIGTERM Handler Context Manager (CQ-Y2)

**Review ref:** review-post-phase6-code-quality.md Y2

**Problem:** `_run_batch` (line 387-397) and `_run_realtime` (line 450-461) both contain identical SIGTERM handler setup code.

**Fix:**

Create a context manager in `cli.py`:

```python
@contextlib.contextmanager
def _sigterm_cancels_current_task() -> Iterator[None]:
    """Register a SIGTERM handler that cancels the current asyncio task."""
    loop = asyncio.get_running_loop()
    this_task = asyncio.current_task()

    def _handle() -> None:
        if this_task is not None and not this_task.done():
            this_task.cancel()

    try:
        loop.add_signal_handler(signal.SIGTERM, _handle)
    except NotImplementedError:
        pass  # Windows — Docker targets Linux
    try:
        yield
    finally:
        try:
            loop.remove_signal_handler(signal.SIGTERM)
        except (NotImplementedError, ValueError):
            pass
```

Then in both `_run_batch` and `_run_realtime`, replace the duplicated blocks with:
```python
with _sigterm_cancels_current_task():
    ...
```

**Tests:**
- Existing SIGTERM tests should still pass.
- Add test that signal handler is removed after context manager exits.

**Files:** `src/factory_simulator/cli.py`, `tests/unit/test_cli.py`

---

## Task 7.3 — Extract OPC-UA Node Creation Helper (CQ-Y3)

**Review ref:** review-post-phase6-code-quality.md Y3

**Problem:** `_build_inactive_nodes` (lines 415-510) duplicates ~40 lines of folder hierarchy, EURange, EngineeringUnits, and MinimumSamplingInterval creation from `_build_node_tree` (lines 279-407).

**Fix:**

Extract a shared helper method:

```python
async def _create_variable_node(
    self,
    ns: int,
    folder_cache: dict[str, Node],
    parent_folder: Node,
    sig: SignalConfig,
    *,
    access_level: int | None = None,
    status_code: ua.StatusCode | None = None,
) -> Node:
    """Create an OPC-UA variable node with standard properties.

    Shared between active and inactive node creation to avoid duplication.
    Handles: folder hierarchy, variable creation, EURange, EngineeringUnits,
    MinimumSamplingInterval.

    Parameters
    ----------
    access_level:
        If set, override the default AccessLevel (e.g., 0 for inactive nodes).
    status_code:
        If set, write this StatusCode on the initial value.
    """
```

The method should:
1. Create/reuse folder hierarchy via `folder_cache`.
2. Create the variable node with correct data type.
3. Set EURange if min_clamp/max_clamp defined.
4. Set EngineeringUnits from signal units.
5. Set MinimumSamplingInterval.
6. If `access_level` is not None, write AccessLevel attribute.
7. If `status_code` is not None, write initial value with that StatusCode.
8. Return the variable node.

Then `_build_node_tree` calls it with defaults (no access_level/status_code overrides), and `_build_inactive_nodes` calls it with `access_level=0` and `status_code=BadNotReadable`.

**Tests:**
- All existing OPC-UA tests (active + inactive) must still pass.
- No new tests needed — this is a pure refactor.

**Files:** `src/factory_simulator/protocols/opcua_server.py`

---

## Task 7.4 — Guard Overlapping OPC-UA Node Paths + Test (CQ-Y4)

**Review ref:** review-post-phase6-code-quality.md Y4

**Problem:** If a custom config uses the same `opcua_node` path in both active and inactive profiles, `_build_inactive_nodes` would crash with a `BadNodeIdAlreadyExists` exception.

**Fix:**

In `_build_inactive_nodes`, before creating each variable node, check if the node path already exists in the active node set:

```python
if sig.opcua_node in self._node_to_signal:
    logger.warning(
        "Inactive node %s conflicts with active node — skipping",
        sig.opcua_node,
    )
    continue
```

**Tests:**
- `test_overlapping_opcua_node_skipped` — Create configs where one signal has the same `opcua_node` path in both profiles. Verify the server starts without error and the node remains active (not overwritten).
- `test_overlapping_opcua_node_logged` — Verify a warning is logged for the skipped node.

**Files:** `src/factory_simulator/protocols/opcua_server.py`, `tests/unit/test_protocols/test_opcua_inactive.py`

---

## Task 7.5 — Remove Dead `FactoryInfo.timezone` Field (G-Arch21)

**Review ref:** review-architecture.md #21

**Problem:** `FactoryInfo.timezone` is defined (default "Europe/London") but never read by any code. All timestamps use UTC via `time_utils`.

**Fix:**

Remove the `timezone` field from `FactoryInfo` in `config.py` line 28. Check both YAML configs for a `timezone` key and remove if present. Search for any references and update.

**Tests:**
- Existing config tests should still pass.
- If any test references `factory_info.timezone`, update it.

**Files:** `src/factory_simulator/config.py`, `config/factory.yaml`, `config/factory-foodbev.yaml`

---

## Task 7.6 — Elevate OPC-UA Error Log Levels (G-Arch23)

**Review ref:** review-architecture.md #23

**Problem:** OPC-UA sync errors (freeze failures, write failures) are logged at DEBUG level. In production, these indicate real connectivity issues and should be visible at INFO or WARNING.

**Fix:**

In `src/factory_simulator/protocols/opcua_server.py`:
- Line 556: `logger.debug("OPC-UA freeze failed...")` → `logger.warning(...)`
- Line 640: `logger.debug(...)` → check context and elevate if appropriate
- Line 697: `logger.debug("OPC-UA write failed...")` → `logger.warning(...)`

Use `logger.warning` for operational issues (connection failures, write errors). Keep `logger.debug` for expected/routine messages.

**Tests:**
- No new tests needed — log level changes.

**Files:** `src/factory_simulator/protocols/opcua_server.py`

---

## Task 7.7 — Return Defensive Copy from `store.get_all()` (G-Arch24)

**Review ref:** review-architecture.md #24

**Problem:** `store.get_all()` returns the internal `self._signals` dict directly. A caller mutating it would corrupt the store.

**Fix:**

Use `types.MappingProxyType` to return a read-only view (zero-copy, prevents mutation):

```python
from types import MappingProxyType

def get_all(self) -> Mapping[str, SignalValue]:
    """Return a read-only view of all signal values."""
    return MappingProxyType(self._signals)
```

Update the return type annotation to `Mapping[str, SignalValue]`. This is safe because callers only iterate/read.

Check all callers of `get_all()` to ensure none mutate the result. If any do, they need to be updated to work with a read-only view.

**Tests:**
- Add `test_get_all_not_mutable` — verify that `store.get_all()[key] = ...` raises `TypeError`.
- Existing tests should pass since they only read.

**Files:** `src/factory_simulator/store.py`, `tests/unit/test_store.py`

---

## Task 7.8 — Add I/O Error Handling in Ground Truth `_write_line` (G-Arch26)

**Review ref:** review-architecture.md #26

**Problem:** `_write_line()` has no error handling for I/O errors (disk full, permission issues). A write failure would crash the simulation.

**Fix:**

In `src/factory_simulator/engine/ground_truth.py`, wrap the write in `_write_line`:

```python
def _write_line(self, record: dict[str, Any]) -> None:
    if self._fh is None:
        return
    try:
        line = json.dumps(record, separators=(",", ":"))
        self._fh.write(line + "\n")
        self._fh.flush()
    except OSError:
        logger.warning("Ground truth write failed — disabling logger")
        self._fh = None
```

This degrades gracefully: if a write fails, disable further writes but don't crash the simulation. Log a warning so the operator knows.

**Tests:**
- `test_write_line_io_error_disables_logger` — mock `_fh.write` to raise `OSError`. Verify subsequent calls are no-ops. Verify warning logged.

**Files:** `src/factory_simulator/engine/ground_truth.py`, `tests/unit/test_ground_truth.py`

---

## Task 7.9 — Rename `float32_hr_addresses` to `dual_register_hr_addresses` (G-Proto8 / CQ-G9)

**Review ref:** review-protocol-fidelity.md #8, review-post-phase6-code-quality.md G9

**Problem:** `float32_hr_addresses` also contains uint32 addresses. The name is misleading.

**Fix:**

In `src/factory_simulator/protocols/modbus_server.py`:
- Line 454: Rename `float32_hr_addresses` → `dual_register_hr_addresses` in `RegisterMap`
- Line 508-509: Update the `.add()` calls
- Line 799: Update the parameter pass-through

Also update any test references. Search for `float32_hr_addresses` across the entire codebase.

**Tests:**
- Existing Modbus tests pass (name change only, no logic change).

**Files:** `src/factory_simulator/protocols/modbus_server.py`, tests as needed

---

## Task 7.10 — Match Modbus Update Interval to Tick Interval (G-Proto10)

**Review ref:** review-protocol-fidelity.md #10

**Problem:** Modbus sync loop sleeps 50ms (line 1270) but new values only arrive every 100ms (`tick_interval_ms`). Half the syncs are wasted CPU cycles.

**Fix:**

Make the Modbus update interval configurable or derive it from `tick_interval_ms`. The simplest approach: read `tick_interval_ms` from the config (already available as `self._config`) and use it:

```python
await asyncio.sleep(self._config.simulation.tick_interval_ms / 1000.0)
```

**Note:** If the update interval equals the tick interval, there's a risk of always being slightly out of phase. Using `tick_interval_ms * 0.9` or `tick_interval_ms / 2` is a reasonable compromise. **Use `tick_interval_ms / 2`** — matches the current 50ms for 100ms ticks, but scales correctly for other tick rates. Add a comment explaining why.

Actually, re-reading the code: 50ms is half of 100ms, so this is already `tick_interval_ms / 2`. The issue is just that it's hardcoded. Make it derive from config:

```python
# Sync at half the tick interval to minimise staleness vs CPU waste.
update_s = self._config.simulation.tick_interval_ms / 2000.0
await asyncio.sleep(update_s)
```

**Tests:**
- No new tests needed — timing change. Existing integration tests verify correctness.

**Files:** `src/factory_simulator/protocols/modbus_server.py`

---

## Task 7.11 — Document `_compute_block_size` +3 Safety Margin (G-Proto13)

**Review ref:** review-protocol-fidelity.md #13

**Problem:** The `+3` in `_compute_block_size` is correct but could use a clearer inline explanation.

**Fix:**

The docstring already explains it. Improve the inline comment in the return statement:

```python
def _compute_block_size(addresses: list[int], min_size: int = 16) -> int:
    """Compute the data block size needed to hold all register addresses.

    pymodbus DataBlock stores values at index = address + 1 (1-based).
    A 32-bit value at address N occupies indices N+1 and N+2.
    Therefore the block needs at least max(address) + 3 entries.
    """
    if not addresses:
        return min_size
    # +3: pymodbus 1-based indexing (+1) + 32-bit value spans 2 registers (+2)
    return max(max(addresses) + 3, min_size)
```

**Tests:** None needed — documentation only.

**Files:** `src/factory_simulator/protocols/modbus_server.py`

---

## Task 7.12 — Add Explicit `line_id` to Packaging Config + Validate ShiftChange Times (G-Proto14 + G-Arch-ShiftChange)

**Review ref:** review-protocol-fidelity.md #14, review-architecture.md §4.1

**Problem 1:** Packaging config (`factory.yaml`) doesn't include an explicit `line_id` in the MQTT section — relies on Pydantic default `"packaging1"`.

**Problem 2:** `ShiftChangeConfig.times` values are not validated as HH:MM format. Invalid strings like `"25:99"` or `"abc"` would pass config loading but cause runtime errors.

**Fix 1:** Add explicit `line_id: packaging1` to `config/factory.yaml` in the MQTT section, matching how `factory-foodbev.yaml` specifies `line_id: foodbev1`.

**Fix 2:** Add a field validator to `ShiftChangeConfig` in `config.py`:

```python
@field_validator("times")
@classmethod
def _valid_hhmm(cls, v: list[str]) -> list[str]:
    import re
    pattern = re.compile(r"^\d{2}:\d{2}$")
    for t in v:
        if not pattern.match(t):
            raise ValueError(f"Shift time must be HH:MM format, got: {t!r}")
        hh, mm = int(t[:2]), int(t[3:])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(f"Invalid shift time: {t!r}")
    return v
```

**Tests:**
- `test_shift_change_valid_times` — valid HH:MM strings accepted.
- `test_shift_change_invalid_format` — non-HH:MM strings rejected.
- `test_shift_change_invalid_values` — `"25:00"`, `"12:60"` rejected.
- `test_packaging_config_has_line_id` — load factory.yaml, verify `mqtt.line_id == "packaging1"`.

**Files:** `src/factory_simulator/config.py`, `config/factory.yaml`, `tests/unit/test_config.py`

---

## Task 7.13 — CI `fail-fast: false` + Validate All Fixes

**Review ref:** review-post-phase6-test-ci.md recommendation #1

**Depends on:** Tasks 7.1-7.12

**Steps:**

1. In `.github/workflows/ci.yml`, add `fail-fast: false` to the unit test matrix strategy:
   ```yaml
   strategy:
     fail-fast: false
     matrix:
       python-version: ["3.12", "3.13"]
   ```

2. Run `ruff check src tests` — must be clean.
3. Run `mypy src` — must pass.
4. Run `pytest` — ALL tests must pass.
5. Fix any failures.

**Files:** `.github/workflows/ci.yml`

---

## Dependencies

```
7.1  (MQTT retry)           → independent
7.2  (SIGTERM ctx mgr)      → independent
7.3  (OPC-UA node helper)   → independent
7.4  (overlapping guard)    → depends on 7.3 (helper must exist first)
7.5  (dead timezone)        → independent
7.6  (log levels)           → independent
7.7  (store defensive copy) → independent
7.8  (GT I/O handling)      → independent
7.9  (rename addresses)     → independent
7.10 (Modbus interval)      → independent
7.11 (block size docs)      → independent
7.12 (line_id + HH:MM)     → independent
7.13 (CI + validation)     → depends on 7.1-7.12
```

## Effort Estimate

- 7.1: ~15 min (restructure retry loop + test)
- 7.2: ~20 min (extract context manager)
- 7.3: ~30 min (extract helper, refactor both callers)
- 7.4: ~15 min (guard + 2 tests)
- 7.5: ~5 min (remove field)
- 7.6: ~5 min (change log levels)
- 7.7: ~15 min (MappingProxyType + test)
- 7.8: ~15 min (try/except + test)
- 7.9: ~10 min (rename across codebase)
- 7.10: ~5 min (derive from config)
- 7.11: ~5 min (improve docstring/comment)
- 7.12: ~15 min (YAML edit + validator + tests)
- 7.13: ~15 min (CI edit + full suite run)
- **Total: ~2.5 hours**

## Completion Note

Phase 7 completes all actionable items from both the original three-reviewer deep review and the post-Phase 6 quality review. After completion, only genuinely cosmetic or by-design GREEN observations remain.
