# Phase 6b: Robustness

**Source:** `plans/consolidated-review-action-plan.md` (Batch 3 — Y4-Y8)
**Scope:** 5 YELLOW issues affecting resilience, error handling, and config completeness
**Goal:** Make the simulator robust to real-world operational conditions (broker delays, double-close, Docker stop, F&B exception injection, config wiring)

---

## Context

Phase 6a fixed all 6 RED issues and 3 high-priority YELLOWs. Phase 6b addresses the next tier: robustness issues that won't produce incorrect data but will cause crashes, silent failures, or missing functionality under real deployment conditions.

Review files with full detail:
- `plans/review-architecture.md` (Y4, Y5, Y6, Y8)
- `plans/review-protocol-fidelity.md` (Y7)

---

## Tasks

### Task 6b.1: MQTT Publisher Startup Retry and Disconnect Monitoring

**Issue:** Y4 (Architecture review) — No reconnection logic around initial `connect()`. No `on_disconnect` callback.
**File:** `src/factory_simulator/protocols/mqtt_publisher.py`

The `MqttPublisher.start()` method calls `self._client.connect()` once. If the MQTT broker hasn't started yet (common with Docker Compose startup ordering), this throws `ConnectionRefusedError` and crashes the simulator. There is also no `on_disconnect` callback to log or monitor mid-run broker restarts.

**What to do:**
1. Wrap the initial `connect()` in a retry loop with exponential backoff. Suggested: 3 attempts, delays of 1s → 2s → 4s. Log each retry at WARNING level. If all retries fail, raise the original exception (don't swallow it — the simulator should fail if the broker is genuinely unreachable).
2. Register `on_connect` and `on_disconnect` callbacks on the paho client:
   - `on_connect`: log at INFO level with result code. If `rc != 0`, log at ERROR.
   - `on_disconnect`: log at WARNING level with reason code. Note: paho-mqtt 2.0 changed the callback signature — use `on_disconnect(client, userdata, flags, rc, properties)` for paho v2.
3. Do NOT implement automatic recovery logic beyond what paho already provides internally via `loop_start()`. Paho's network loop handles reconnection automatically. The callbacks are for visibility.
4. **Test:** Mock the MQTT client's `connect()` to fail once then succeed. Verify the publisher starts after retry. Test `on_disconnect` callback is registered. Test that 3 consecutive failures raise the exception.

**PRD refs:** PRD 3.3 (MQTT publisher)

---

### Task 6b.2: CsvWriter Idempotent Close

**Issue:** Y5 (Architecture review) — `CsvWriter.close()` not idempotent; double-close raises `ValueError`.
**File:** `src/factory_simulator/output/writer.py`

`CsvWriter.close()` calls `self._file.close()` unconditionally. If called twice (e.g. once in `_run_batch` finally block, once elsewhere), the second call raises `ValueError: I/O operation on closed file` because `self._flush()` tries to write to the closed file handle.

**What to do:**
1. Add a guard: check `self._file.closed` before flushing and closing
2. Apply the same pattern to `ParquetWriter.close()` if it has the same issue
3. Make `close()` a no-op after the first call — this is standard resource cleanup behaviour
4. **Test:** Create a CsvWriter, close it twice, verify no exception. Also verify that `write_tick()` after `close()` either raises a clear error or is silently skipped (pick one and document the choice).

---

### Task 6b.3: SIGTERM Handler for Graceful Docker Shutdown

**Issue:** Y6 (Architecture review) — No explicit SIGTERM handler. Docker stop sends SIGTERM, which falls through to `asyncio.run()` default handling that may not clean up protocol servers.
**File:** `src/factory_simulator/cli.py`

`asyncio.run()` installs a default handler for SIGINT (KeyboardInterrupt) but does NOT handle SIGTERM. When Docker sends SIGTERM (the default stop signal), the process may be killed without running the `finally` blocks that stop protocol servers. After Docker's 10-second grace period, SIGKILL terminates the process forcefully.

**What to do:**
1. In `_run_realtime()`, before the `try` block, register a SIGTERM handler on the event loop:
```python
loop = asyncio.get_running_loop()
loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
```
2. The handler should cancel the engine's main task to trigger the existing cleanup in the `finally` block. A clean approach: store a reference to the current task and cancel it from the signal handler:
```python
def _handle_sigterm() -> None:
    logger.info("Received SIGTERM, initiating graceful shutdown")
    current_task = asyncio.current_task()
    if current_task is not None:
        current_task.cancel()
```
Or raise `SystemExit` / cancel the engine task. The goal is to flow through the existing `finally` cleanup.
3. Also handle SIGTERM in batch mode (`_run_batch` path) — same pattern.
4. **Test:** This is hard to unit test directly. At minimum, verify that `signal.SIGTERM` is referenced in the code. An integration test could send SIGTERM to a subprocess and verify clean shutdown, but this is optional for now — document it as a manual verification step.

**Note:** On Windows, `add_signal_handler` is not available. Use `signal.signal(signal.SIGTERM, handler)` as fallback, or guard with a platform check. Since the simulator targets Docker (Linux), the asyncio approach is preferred.

---

### Task 6b.4: Profile-Aware 0x06 (Device Busy) Exception Injection

**Issue:** Y7 (Protocol review) — 0x06 exceptions only fire on `press.machine_state`. F&B endpoints never trigger them.
**File:** `src/factory_simulator/protocols/modbus_server.py`

The `_check_machine_state_transition()` method (around line 887-906) hardcodes `press.machine_state` as the signal to monitor for state transitions. For F&B endpoints (mixer, oven, filler, etc.), this signal doesn't exist in the store, so `store.get()` returns `None` and 0x06 exceptions never fire.

Real PLCs DO return Device Busy during state transitions regardless of equipment type. The F&B profile has its own state signals (e.g. `mixer.state`, `oven.state`).

**What to do:**
1. Make the state signal configurable per `ModbusServer` instance. Add a `state_signal_id: str | None` parameter to `ModbusServer.__init__()` (or to the method/class that manages transition tracking).
2. When creating Modbus servers:
   - Packaging profile: use `"press.machine_state"` (existing behaviour)
   - F&B profile: use the appropriate state signal for each endpoint. Check the F&B equipment configs for the correct signal IDs. Each equipment group has a `state` signal.
3. In `_check_machine_state_transition()`, use the configured signal ID instead of hardcoded `"press.machine_state"`.
4. If no state signal is configured (None), skip transition checking entirely (current fallback behaviour — this is fine for equipment without state signals).
5. **Where to wire it:** The topology manager creates per-endpoint Modbus servers in realistic mode. Pass the appropriate state signal ID from the endpoint config. In collapsed mode, the single server uses `"press.machine_state"` for packaging and a suitable F&B state signal (e.g. `"mixer.state"` or `"oven.state"` — pick the one most likely to transition).
6. **Test:** Create a Modbus server with a F&B state signal, trigger a state transition, verify 0x06 exception fires. Verify existing packaging tests still pass.

**PRD refs:** PRD 3.1.7 (Modbus exception injection)

---

### Task 6b.5: Wire EvaluationConfig into FactoryConfig

**Issue:** Y8 (Architecture review) — `EvaluationConfig` is fully defined (~70 lines of Pydantic model with validators) but never added as a field on `FactoryConfig`.
**File:** `src/factory_simulator/config.py`

The evaluator currently uses its own hardcoded defaults in `metrics.py`. The config model exists but is orphaned — it's never loaded from YAML and never passed to the evaluator.

**What to do:**
1. Add `evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)` to `FactoryConfig`
2. Add an `evaluation:` section to both YAML config files with sensible defaults (can match the Pydantic defaults — this just makes the config explicit and discoverable)
3. Wire the loaded `EvaluationConfig` into the evaluator:
   - In the evaluate CLI path, load the config file (if provided) and pass `config.evaluation` to the evaluator
   - The evaluator should use `config.evaluation.pre_margin_seconds`, `.post_margin_seconds`, `.severity_weights`, `.latency_targets`, `.seeds` instead of its own hardcoded defaults
4. Maintain backward compatibility: if no `evaluation:` section is in the YAML, Pydantic defaults apply (existing behaviour preserved)
5. **Test:** Load a config with custom `evaluation.pre_margin_seconds`, verify the evaluator uses that value. Load a config without `evaluation:` section, verify defaults apply.

**PRD refs:** PRD 12.4 (evaluation configuration), PRD Appendix D (configuration reference)

---

### Task 6b.6: Validate All Fixes — Full Suite

**Depends on:** 6b.1-6b.5

No new code. Verify everything works together.

**What to do:**
1. Run `ruff check src tests && mypy src && pytest --tb=short -q` — ALL must pass
2. Verify no regressions in Phase 6a fixes
3. Verify both profiles load correctly with the new `evaluation:` config section
4. Fix any failures before committing

---

## Completion Criteria

All 6 tasks pass. Full test suite green. MQTT publisher handles broker startup delay. CsvWriter handles double-close. SIGTERM triggers graceful shutdown. F&B endpoints fire 0x06 exceptions. EvaluationConfig is loaded from YAML and used by the evaluator.
