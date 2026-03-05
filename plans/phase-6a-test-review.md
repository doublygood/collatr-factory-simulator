# Plan: Faster Test Suite Execution

## Context

The test suite has grown to 2,998 tests and now takes ~16 minutes for pytest alone (~32 minutes
wall time including sub-agent overhead). CLAUDE.md originally estimated 15 minutes — that
estimate is stale. The suite runs entirely sequentially; no parallelization plugin is installed.

**Root causes of current slowness:**
- 2,793 unit tests run one-at-a-time (most are fast, but the volume adds up)
- 205 integration tests start real servers (Modbus TCP, OPC-UA, MQTT) — inherently slower
- Hypothesis property tests run 100 examples each by default
- No pytest-xdist or equivalent installed

---

## Options

### Option A — pytest-xdist parallel workers ⭐ Highest impact

Install `pytest-xdist` and run workers in parallel (`-n auto` uses all CPU cores).

**What breaks today (must fix first):**
- `test_cross_protocol.py` and `test_modbus_fnb_integration.py` both bind to port **15503**. With parallel workers, two tests could try to bind to the same port simultaneously → `EADDRINUSE`. One port must be changed (e.g. 15503 → 15521 in `test_cross_protocol.py`).

**Files to change:**
- `requirements-dev.txt` — add `pytest-xdist>=3.5`
- `pyproject.toml` — add `addopts = "-n auto"` (or `-n 4` for a fixed count)
- `tests/integration/test_cross_protocol.py` — change `_MODBUS_PORT = 15503` → `15521`

**Compatibility note:** pytest-asyncio 1.3.0 with `asyncio_mode = "auto"` and function-scoped
event loops is xdist-compatible. Each worker gets its own Python interpreter and event loop.
Use `--dist=loadfile` (keep tests from the same file on the same worker) to prevent any
intra-file port conflicts inside fixtures with setup/teardown.

**Expected speedup:** ~3–4× on a 4-core machine → ~4–5 min (was ~16 min).

**Recommended config:**
```toml
[tool.pytest.ini_options]
addopts = "-n auto --dist=loadfile"
```

**Risk:** Low. The only concrete issue is the port 15503 conflict (easy to fix). No
shared mutable state exists between test files (Rule 12 in CLAUDE.md guarantees this;
confirmed by the empty `conftest.py`).

---

### Option B — Split unit vs integration into separate runs

Run unit tests and integration tests as two separate `pytest` invocations and execute them
concurrently (two terminal windows, or two CI jobs):

```bash
# Terminal 1 (~8-10 min)
pytest tests/unit --tb=short -q

# Terminal 2 (~3-5 min, overlaps with unit run)
pytest tests/integration --tb=short -q
```

**Files to change:** None — `tests/unit/` and `tests/integration/` are already separate directories.

**Expected speedup:** Effective wall time drops to max(unit_time, integration_time) ≈ 8–10 min.
Worse than Option A but zero code changes and zero compatibility risk.

**Limitation:** Requires two terminal processes or a CI matrix. The sub-agent pattern in
CLAUDE.md would need to spawn two agents in parallel.

---

### Option C — Hypothesis settings (secondary, combine with A or B)

Hypothesis defaults to 100 examples per property test. The unit suite has many Hypothesis
tests (`test_models/`, `test_scenarios/`). Halving the examples gives ~10–15% speedup
for those files.

**File to add:** `tests/conftest.py` (currently empty)

```python
# tests/conftest.py
from hypothesis import HealthCheck, settings

settings.register_profile("ci", max_examples=50, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("ci")
```

Or simpler — pass `--hypothesis-seed=0` on the pytest command line (deterministic, skips
database lookup overhead).

**Expected speedup:** ~5–15% on property-heavy test files (modest, but free when combined
with A or B).

---

### Option D — Marker-based fast feedback for development (no code changes)

For the everyday development loop (not CI), just run unit tests only:

```bash
pytest tests/unit --tb=short -q          # ~8-10 min, no servers needed
pytest tests/unit/test_models/ -q         # ~2-3 min (just signal math)
pytest tests/unit/test_scenarios/ -q      # ~1-2 min (just scenario logic)
```

**Files to change:** None — useful as documentation / CLAUDE.md update only.

---

## Recommended Approach

**Do Option A + C together.** Option A gives the most speedup with acceptable effort.
Option C is a free 5–15% extra. Option B can be done as a CI strategy on top.

### Implementation steps

1. **Fix the port conflict** (`tests/integration/test_cross_protocol.py`):
   ```python
   _MODBUS_PORT = 15521   # was 15503 (conflict with test_modbus_fnb_integration.py)
   ```

2. **Add pytest-xdist to dev dependencies** (`requirements-dev.txt`):
   ```
   pytest-xdist>=3.5
   ```

3. **Enable parallel execution** (`pyproject.toml`):
   ```toml
   [tool.pytest.ini_options]
   testpaths = ["tests"]
   asyncio_mode = "auto"
   addopts = "-n auto --dist=loadfile"   # ← add this line
   markers = [...]
   ```

4. **Add Hypothesis CI profile** (`tests/conftest.py`):
   ```python
   from hypothesis import HealthCheck, settings
   settings.register_profile("ci", max_examples=50, suppress_health_check=[HealthCheck.too_slow])
   settings.load_profile("ci")
   ```

5. **Update CLAUDE.md** — lower estimated suite time to ~5 min and update
   the sub-agent timeout to 300000ms (5 min + headroom).

### Files to modify
| File | Change |
|------|--------|
| `requirements-dev.txt` | Add `pytest-xdist>=3.5` |
| `pyproject.toml` | Add `addopts = "-n auto --dist=loadfile"` |
| `tests/integration/test_cross_protocol.py` | `_MODBUS_PORT = 15521` |
| `tests/conftest.py` (new) | Hypothesis CI profile (50 examples) |
| `CLAUDE.md` | Update timing estimate and sub-agent timeout |

---

## Verification

1. Run the new test file first with xdist to confirm no port conflicts:
   ```bash
   pytest tests/integration/test_cross_protocol.py tests/integration/test_modbus_fnb_integration.py -v --tb=short
   ```

2. Run the full suite and measure:
   ```bash
   time pytest --tb=short -q
   ```
   Target: < 6 min (vs ~16 min baseline).

3. Confirm test count is still 2,998 (no tests dropped).

4. Verify Hypothesis tests still cover meaningful examples:
   ```bash
   pytest tests/unit/test_models/ -v --tb=short
   ```
   (Should still pass; Hypothesis failure messages include the counterexample.)

---

## Trade-offs & Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| asyncio + xdist incompatibility | Low | function-scoped event loops are xdist-safe; `--dist=loadfile` keeps fixtures together |
| Previously-passing test breaks with parallelism | Low | All tests construct own `SignalStore`/`DataEngine`; no shared mutable state (Rule 12) |
| Hypothesis misses a real bug with 50 examples | Low | Hypothesis shrinks counterexamples; 50 examples still covers the typical failure modes |
| MQTT spike tests conflict on broker | Low | Spike tests share a single Docker broker which handles concurrent clients fine |
| Performance tests flakier under parallel load | Possible | Run performance tests in a separate non-parallel invocation: `pytest tests/performance -p no:xdist` |

---

## Implementation (completed)

**Date**: 2026-03-05 | **Branch**: main

### What was actually implemented (Option A + C)

| File | Change |
|------|--------|
| `requirements-dev.txt` | Added `pytest-xdist>=3.5` |
| `pyproject.toml` | Added `addopts = "-n auto --dist=loadfile --ignore=tests/performance"` |
| `tests/integration/test_cross_protocol.py` | Changed `_MODBUS_PORT = 15503` → `15521` |
| `tests/conftest.py` | Added Hypothesis CI profile (`max_examples=50`) |
| `tests/integration/test_mqtt_integration.py` | Extended vibration publish-rate window from 4s → 5s |
| `CLAUDE.md` | Updated timing to ~4–5 min; sub-agent timeout 1200000ms → 360000ms |

### Deviation from plan

`--ignore=tests/performance` added to `addopts` (not in original plan). Reason: `_update_results()` in `test_performance.py` does an unguarded read-modify-write on `performance-results.json` — a race condition under parallel workers — and wall-time assertions are meaningless under CPU contention. The performance test docstring itself says "Skip benchmarks in normal test runs". Explicit path `pytest tests/performance -p no:xdist` still works.

### MQTT timing fix

`test_vibration_publishes_approximately_every_1s` failed intermittently under parallel load: 4s window was tight when publisher startup took ~1s, leaving only 2 full 1s intervals. Extended to 5s; ≥3 assertion unchanged. This was a pre-existing fragility exposed by parallelism, not introduced by it.

### Results

| Metric | Before | After |
|--------|--------|-------|
| pytest runtime | ~16 min | **3:54** |
| Tests executed | 2,998 | 2,992 (6 performance excluded from default run) |
| Failures | 0 | 0 |
| Workers | 1 (sequential) | auto (`-n auto`) |

---

## CI Coverage Gap (not yet implemented)

`.github/workflows/ci.yml` runs two pytest steps:
- `pytest tests/unit` — full unit suite ✅ (now benefits from xdist automatically via addopts)
- `pytest tests/integration/test_acceptance.py -m "acceptance and not slow"` — acceptance only

The rest of `tests/integration/` (Modbus, OPC-UA, MQTT, cross-protocol, oven UID routing, F&B protocols) is **never run in CI**. It requires a live Mosquitto broker which the workflow does not provision.

To fix, add a `services:` block to the integration job in `.github/workflows/ci.yml`:

```yaml
integration-tests:
  services:
    mosquitto:
      image: eclipse-mosquitto:2
      ports:
        - 1883:1883
      volumes:
        - ./config/mosquitto.conf:/mosquitto/config/mosquitto.conf
  steps:
    ...
    - name: Run full integration suite
      run: pytest tests/integration --tb=short -q
      timeout-minutes: 5
```

This is a pre-existing gap — not introduced by the xdist changes — but worth closing in a future task.
