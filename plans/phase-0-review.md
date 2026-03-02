# Phase 0 Review: Validation Spikes

**Reviewer:** Quality Engineer (automated)
**Date:** 2026-03-02
**Scope:** All Phase 0 deliverables -- 3 spike test files, documentation, Docker config, project scaffolding

**Verdict:** Phase 0 is solid. The spikes validate the three protocol libraries thoroughly and the documentation is accurate. There are a few issues that should be addressed before Phase 1, primarily around test reliability and a missing `conftest.py` marker for MQTT integration tests.

---

## Summary

| Severity | Count | Description |
|----------|-------|-------------|
| RED (Must Fix) | 3 | Test reliability risks, missing integration marker propagation, Lock usage rationale |
| YELLOW (Should Fix) | 8 | Code quality, missing edge cases, documentation gaps |
| GREEN (Suggestion) | 5 | Minor style improvements, optional hardening |

---

## RED Findings (Must Fix)

### R1. MQTT LWT test accesses private `_sock` attribute -- fragile across paho-mqtt versions

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/tests/spikes/test_spike_mqtt.py`, lines 243-244
**What:** The LWT test forces an unclean disconnect by accessing `lwt_pub._sock.close()`.
**Why it matters:** `_sock` is a private attribute. paho-mqtt 2.x has already undergone significant API changes (the entire callback API was versioned). A point release could rename or remove `_sock`, breaking this test silently. The documentation in `docs/validation-spikes.md` (line 135) acknowledges "no public API for this" but does not flag it as a risk.
**Impact:** Test breakage on paho-mqtt upgrade. This is the only test validating LWT behaviour, which is critical for the simulator's health reporting.
**Suggested fix:** Add an explicit comment marking this as a known fragility, and pin the paho-mqtt version more tightly in `requirements.txt` (e.g., `paho-mqtt>=2.0,<2.2`). Alternatively, wrap the `_sock` access in a try/except with a clear skip message:

```python
sock = getattr(lwt_pub, "_sock", None)
if sock is None:
    pytest.skip("paho-mqtt _sock attribute not found -- API may have changed")
sock.close()
```

### R2. MQTT throughput test has timing-dependent assertions that may flake under CI load

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/tests/spikes/test_spike_mqtt.py`, lines 259-326
**What:** `test_50_msgs_per_second_mixed_qos` publishes 500 messages over 10 seconds using `time.sleep()` for pacing, then asserts `actual_rate >= 40` and QoS 0 loss `< 1%`. The 2-second drain wait (line 299) is a fixed sleep.
**Why it matters:** On a heavily loaded CI machine (shared runners, Docker overhead, macOS Sequoia memory pressure), `time.sleep(0.02)` can drift significantly. The actual rate could drop below 40 msg/s. The 2-second drain wait may be insufficient if the broker is under load, causing QoS 0 messages to appear lost when they are merely delayed. This violates **CLAUDE.md Rule 1: No Hand-Waving** -- if this test fails in CI, the failure message ("Rate too slow" or "QoS 0 excessive loss") will be misleading because the root cause is CI load, not a library defect.
**Impact:** Flaky test in CI environments. This is the kind of test failure that leads to "tests failed due to timing issues... moving on" which Rule 1 explicitly prohibits.
**Suggested fix:** (a) Increase the rate threshold margin: assert `actual_rate >= 25` instead of 40 (the spike already proved ~42 msg/s works). (b) Replace the fixed 2-second drain sleep with `collector.done.wait(timeout=15)` to wait for at least the QoS 1 target count. (c) Add a retry or wider tolerance comment explaining the CI sensitivity.

### R3. `threading.Lock` usage in MQTT test conflicts with CLAUDE.md Rule 9 spirit

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/tests/spikes/test_spike_mqtt.py`, lines 121-122, 131-132, 143-145, 148-149, 152-154
**What:** `MessageCollector` uses `threading.Lock` to protect `self.messages`.
**Why it matters:** CLAUDE.md Rule 9 says "No locks" and "If you think you need a lock, you have a design problem." However, paho-mqtt's callback API uses a background thread (`loop_start()` spawns a thread), which means the `on_message` callback runs on a different thread than the test assertions. The Lock is genuinely necessary here because this is threaded code, not asyncio code. **This is actually correct**, but it needs a comment explaining why it does not violate Rule 9.
**Impact:** A future agent may see the Lock, reference Rule 9, and remove it -- causing a race condition.
**Suggested fix:** Add a comment above the Lock:

```python
# NOTE: Lock is required here because paho-mqtt's loop_start() runs
# callbacks on a background thread. CLAUDE.md Rule 9 (no locks) applies
# to the asyncio signal engine, not to threaded paho-mqtt callbacks.
self._lock = Lock()
```

---

## YELLOW Findings (Should Fix)

### Y1. Modbus test FLOAT32_ADDRESSES uses spike-local addresses, not PRD addresses

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/tests/spikes/test_spike_modbus.py`, line 42
**What:** `FLOAT32_ADDRESSES = {0, 2, 10, 12, 20, 22, 24, 40, 42, 44}` -- these are arbitrary test addresses for the spike's register layout (200 registers starting at 0).
**Why it matters:** This is acceptable for a spike, but the documented "Reference Patterns for Phase 1" in `docs/validation-spikes.md` (lines 76-87) show the FC06 protection pattern without noting that the addresses will change to PRD register addresses (100-101, 102-103, etc. per Appendix A). A Phase 1 implementer could copy the spike pattern and use incorrect addresses.
**Suggested fix:** Add a note in `docs/validation-spikes.md` under the FC06 rejection reference pattern:

```
**Note:** The spike uses test addresses 0-44. Phase 1 must use the actual
PRD register addresses from Appendix A (e.g., 100, 102, 110, 112, 120,
122, 124 for press float32 registers).
```

### Y2. Modbus server fixture uses fixed ports -- potential conflict in parallel test runs

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/tests/spikes/test_spike_modbus.py`, lines 36-37
**What:** `BASE_PORT = 15020` with 7 servers on 15020-15026. These are hardcoded.
**Why it matters:** If two test runs execute concurrently (e.g., a developer runs tests while CI is also running), port conflicts will cause `OSError: Address already in use`. The OPC-UA spike (line 87) correctly uses port 0 for OS-assigned ports. The Modbus spike should follow the same pattern.
**Suggested fix:** This is acceptable for Phase 0 spikes since they run in isolation, but document the limitation. For Phase 1, the Modbus test infrastructure should either use port 0 (if pymodbus supports it) or use a port allocation fixture that checks availability.

### Y3. OPC-UA server port extraction accesses private `bserver._server` attribute

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/tests/spikes/test_spike_opcua.py`, lines 140-142
**What:** Port extraction uses `server.bserver._server.sockets` which reaches into asyncua internals.
**Why it matters:** Same fragility concern as R1 (`_sock`). If asyncua restructures its internals, this breaks. The documentation (line 219) does note this pattern but does not flag the risk.
**Suggested fix:** Add a defensive check:

```python
# asyncua internal: extract OS-assigned port. If this breaks on
# upgrade, fall back to parsing server.endpoint.
actual_port = 0
if hasattr(server, "bserver") and hasattr(server.bserver, "_server"):
    for sock in server.bserver._server.sockets:
        actual_port = sock.getsockname()[1]
        break
assert actual_port != 0, "Could not extract OS-assigned port from asyncua server"
```

### Y4. OPC-UA DataChangeHandler is not thread-safe but does not need to be -- missing clarity

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/tests/spikes/test_spike_opcua.py`, lines 215-244
**What:** `DataChangeHandler` uses `asyncio.Event()` and a plain list without locks. Since asyncua runs in the same asyncio event loop, this is correct. But there is no comment explaining why locks are unnecessary here (contrasting with the MQTT `MessageCollector` which needs locks).
**Why it matters:** Consistency and clarity. A future agent reading both spike files should understand why one uses Lock and the other does not.
**Suggested fix:** Add a comment:

```python
class DataChangeHandler:
    """Collects data change notifications from OPC-UA subscriptions.

    No locks needed: asyncua delivers callbacks on the asyncio event loop
    (same thread as the test), unlike paho-mqtt which uses a background thread.
    """
```

### Y5. Modbus `_make_server` function has a subtle off-by-one in float32 pre-population

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/tests/spikes/test_spike_modbus.py`, lines 134-139
**What:** The comment says "ModbusSequentialDataBlock is 1-indexed internally" and offsets the float32 value by +1:
```python
hr_values[1] = hi  # +1 because ModbusSequentialDataBlock is 1-indexed internally
hr_values[2] = lo
```
This means address 0 maps to `hr_values[1]`. The float32 at address 0-1 is stored at indices 1-2.
**Why it matters:** This is correct for the test, and the off-by-one quirk is well-documented in both the test and the docs. However, the pre-populated float32 value (150.0) is never actually read or validated in any test. The FC06/FC16 tests write new values. The pre-population is dead setup code -- it adds complexity without test coverage.
**Suggested fix:** Either (a) add a test that reads address 0-1 and verifies 150.0 to validate the 1-indexed offset understanding, or (b) remove the pre-population and simplify `hr_values = [0] * 200`.

### Y6. MQTT test does not clean up retained LWT message from `test_lwt_fires_on_unclean_disconnect`

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/tests/spikes/test_spike_mqtt.py`, lines 220-253
**What:** The LWT test sets `retain=True` on the LWT message (line 237). When the broker publishes the LWT on unclean disconnect, the retained LWT message persists in the broker. The test does not clear it.
**Why it matters:** The retained LWT message leaks across tests. The `test_retained_message_arrives_on_new_subscription` test (line 175) cleans up its retained message (line 215), setting a good pattern. But the LWT test does not. If other tests subscribe to `factory/spike/status`, they will receive the stale LWT. The progress doc (line 94) notes "the extra message is the retained LWT from a previous test's unclean disconnect (harmless)" -- but "harmless" is hand-waving. It is a test isolation issue.
**Suggested fix:** Add cleanup at the end of `test_lwt_fires_on_unclean_disconnect`:

```python
# Clear retained LWT message
subscriber.publish(LWT_TOPIC, b"", qos=1, retain=True)
time.sleep(0.5)
```

### Y7. `docker-compose.yml` is missing the `version` field deprecation note and has no simulator service placeholder

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/docker-compose.yml`
**What:** The file defines only the `mqtt-broker` service. There is no placeholder or comment for the simulator service that will be added in Phase 1.
**Why it matters:** Minor, but CLAUDE.md Rule 11 states "simulator + Mosquitto sidecar". A Phase 1 agent needs to know this file exists and will be extended.
**Suggested fix:** Add a comment:

```yaml
# Phase 1 will add the 'simulator' service here.
# See PRD Section 6.3 for the full Docker Compose configuration.
```

### Y8. `pyproject.toml` build backend uses deprecated `_legacy` module

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/pyproject.toml`, line 3
**What:** `build-backend = "setuptools.backends._legacy:_Backend"` uses a private/legacy backend.
**Why it matters:** The standard setuptools build backend is `setuptools.build_meta`. The `_legacy` backend exists for backward compatibility but may be removed in future setuptools versions. This could cause build failures on `pip install -e .`.
**Suggested fix:**

```toml
build-backend = "setuptools.build_meta"
```

---

## GREEN Findings (Suggestions)

### G1. Modbus concurrent read timing assertions could use `pytest.approx` or wider margins

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/tests/spikes/test_spike_modbus.py`, lines 287, 325-327
**What:** `assert elapsed < 2.0` and `assert elapsed < 10.0` are generous bounds but arbitrary.
**Why it matters:** These will always pass on any reasonable machine, so the assertions are not really testing anything useful. They are documenting expectations, which is fine for a spike.
**Suggested fix:** Consider adding `print()` output for the timing (like the MQTT throughput test does) so the spike documentation captures actual performance numbers. The Modbus tests currently do not print timing data.

### G2. OPC-UA subscription test uses magic number 500 for publishing interval

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/tests/spikes/test_spike_opcua.py`, lines 415, 457
**What:** `create_subscription(500, handler)` -- the 500ms interval is hardcoded in two places.
**Why it matters:** Minor DRY violation. If the subscription interval needs to change for debugging, it must be changed in two places.
**Suggested fix:** Extract to a constant: `SUBSCRIPTION_INTERVAL_MS = 500`.

### G3. MQTT `_make_payload` helper could validate quality enum values

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/tests/spikes/test_spike_mqtt.py`, lines 67-74
**What:** `_make_payload` accepts any string for `quality`. The PRD (Section 3.3.4) specifies only `"good"`, `"uncertain"`, `"bad"`.
**Why it matters:** A typo in a test (`quality="Good"` with capital G) would pass silently.
**Suggested fix:**

```python
VALID_QUALITIES = {"good", "uncertain", "bad"}

def _make_payload(value: float, unit: str = "m/min", quality: str = "good") -> str:
    assert quality in VALID_QUALITIES, f"Invalid quality: {quality}"
    ...
```

### G4. OPC-UA memory test threshold of 500MB is very generous

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/tests/spikes/test_spike_opcua.py`, lines 554-577
**What:** The test asserts `rss_mb < 500.0`. The class docstring explains that `ru_maxrss` reports peak process RSS including pytest infrastructure. The actual value is ~400MB.
**Why it matters:** A 500MB threshold will not detect memory regressions. If asyncua starts leaking and peak RSS goes from 400MB to 490MB, the test still passes.
**Suggested fix:** Tighten the threshold to 450MB, or better yet, record the value and let Phase 1 establish the proper baseline. The test already prints the value, which is the right approach for a spike. This is informational only.

### G5. `requirements.txt` dependency ranges could be documented with rationale

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/requirements.txt`
**What:** Versions match CLAUDE.md exactly (`pymodbus>=3.6,<4.0`, etc.) but there are no inline comments explaining the upper bounds.
**Why it matters:** Very minor. CLAUDE.md already documents the version rationale. But `requirements.txt` is often read without CLAUDE.md context.
**Suggested fix:** Add inline comments:

```
pymodbus>=3.6,<4.0  # 4.0 may break API (ModbusDeviceContext etc.)
asyncua>=1.1.5      # 1.1.8 tested in spike; <2.0 implicit
```

---

## CLAUDE.md Rule Compliance Checklist

| Rule | Status | Notes |
|------|--------|-------|
| Rule 1: No Hand-Waving | PASS | No dismissed test failures. R2 is a preventive finding. |
| Rule 2: Tests Prove Behaviour | PASS | Spike tests validate protocol fidelity and failure modes well. |
| Rule 3: Small, Verified Steps | PASS | Each task has its own commit per git log. |
| Rule 4: PRD Is Canon | PASS | Spikes reference PRD sections. Addresses are spike-local (acceptable). |
| Rule 5: Signal Models | N/A | No signal models in Phase 0. |
| Rule 6: Simulated Time | N/A | No simulation clock in Phase 0. `time.monotonic()` usage is for test timing only. |
| Rule 7: YAGNI | PASS | No over-engineering. Spikes validate only what is needed. |
| Rule 8: Engine Atomicity | N/A | No engine in Phase 0. |
| Rule 9: Single Writer, No Locks | WARN | `threading.Lock` in MQTT test is correct but needs comment (R3). |
| Rule 10: Configuration via Pydantic | N/A | No config models in Phase 0. |
| Rule 11: Docker First | PASS | `docker-compose.yml` and `mosquitto.conf` exist. |
| Rule 12: No Global State | PASS | No module-level mutable state. Constants only. |
| Rule 13: Reproducible Runs | N/A | No `random` module used. No signal generation in Phase 0. |

---

## Documentation Accuracy Check

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/docs/validation-spikes.md`

| Claim | Verified | Notes |
|-------|----------|-------|
| "32 tests, all passing" | VERIFY | 12 Modbus + 8 MQTT + 12 OPC-UA = 32. Correct. |
| pymodbus 3.12.1 | VERIFY | Version matches spike runtime. |
| paho-mqtt 2.1.0 | VERIFY | Version matches spike runtime. |
| asyncua 1.1.8 | VERIFY | Version matches spike runtime. |
| "7 concurrent servers on ports 15020-15026" | VERIFY | Matches `BASE_PORT` and `NUM_SERVERS` in test. |
| "50 rounds x 7 concurrent reads in <10s" | VERIFY | Matches `test_no_event_loop_blocking` with iterations=50 and assert <10s. |
| "Avg 3.0ms, P95 6.7ms, Max 13.8ms" | ACCEPT | These are point-in-time measurements from one run. Documented as such. |
| "Peak RSS ~400MB" | ACCEPT | Point-in-time measurement. Threshold is 500MB in test. |
| ModbusSequentialDataBlock "1-indexed internally" | VERIFY | Matches test observation at line 354-357 (results[1][0] == 1001). |
| DataChangeNotif handler pattern | VERIFY | Matches code at line 233-236 in OPC-UA test. |
| "Broker restart buffering test not implemented" | VERIFY | Acknowledged in docs and progress file. Reasonable deferral. |

**Documentation is accurate.** No factual errors found.

---

## Dependency Management Check

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/requirements.txt`

| Dependency | CLAUDE.md Spec | requirements.txt | Status |
|------------|---------------|-------------------|--------|
| pymodbus | >=3.6,<4.0 | >=3.6,<4.0 | MATCH |
| asyncua | >=1.1.5 | >=1.1.5 | MATCH |
| paho-mqtt | >=2.0 | >=2.0 | MATCH |
| numpy | >=1.26 | >=1.26 | MATCH |
| pydantic | >=2.0 | >=2.0 | MATCH |
| pyyaml | >=6.0 | >=6.0 | MATCH |
| uvloop | >=0.19 | >=0.19; sys_platform == "linux" | MATCH (platform conditional correct) |

**File:** `/Users/leemcneil/Projects/DoublyGood/collatr-factory-simulator/requirements-dev.txt`

| Dependency | CLAUDE.md Spec | requirements-dev.txt | Status |
|------------|---------------|----------------------|--------|
| pytest | >=8.0 | >=8.0 | MATCH |
| pytest-asyncio | >=0.23 | >=0.23 | MATCH |
| hypothesis | >=6.0 | >=6.0 | MATCH |
| ruff | >=0.3 | >=0.3 | MATCH |
| mypy | >=1.8 | >=1.8 | MATCH |

All dependencies match CLAUDE.md specifications.

---

## Recommended Action Plan

### Before Phase 1 (address RED findings):

1. **R3** -- Add comment explaining why `threading.Lock` is correct in MQTT `MessageCollector` (5 minutes)
2. **R1** -- Add defensive `getattr` check for `_sock` access in LWT test and pin paho-mqtt version (10 minutes)
3. **R2** -- Widen throughput rate assertion to `>= 25 msg/s` and use `collector.done.wait()` instead of fixed drain sleep (15 minutes)

### During Phase 1 (address YELLOW findings):

4. **Y1** -- Add note about PRD addresses in spike documentation
5. **Y5** -- Either add a read-back test for pre-populated float32 or remove dead setup code
6. **Y6** -- Clean up retained LWT message in test teardown
7. **Y8** -- Fix `pyproject.toml` build backend to `setuptools.build_meta`

### Optional improvements:

8. GREEN items can be addressed opportunistically during Phase 1 implementation.

---

## Final Assessment

Phase 0 achieved its goal: all three protocol libraries are validated for the project's requirements. The spike tests are well-structured, the documentation is accurate, and the reference patterns will be valuable for Phase 1 implementers. The RED findings are primarily about test reliability under CI conditions and defensive coding against library upgrades -- they do not indicate fundamental design problems.

**Recommendation:** Fix R1, R2, R3 (30 minutes total), then proceed to Phase 1.
