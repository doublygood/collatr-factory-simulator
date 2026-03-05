# Phase 6b: Robustness — Progress

## Status: IN PROGRESS

## Tasks
- [x] 6b.1: MQTT Publisher Startup Retry and Disconnect Monitoring (Y4)
- [x] 6b.2: CsvWriter Idempotent Close (Y5)
- [x] 6b.3: SIGTERM Handler for Graceful Docker Shutdown (Y6)
- [ ] 6b.4: Profile-Aware 0x06 Device Busy Exception (Y7)
- [ ] 6b.5: Wire EvaluationConfig into FactoryConfig (Y8)
- [ ] 6b.6: Validate All Fixes — Full Suite

## Notes

Tasks 6b.1-6b.5 are all independent (no dependencies between them). Task 6b.6 depends on all others.

---

## Task 6b.1: MQTT Publisher Startup Retry (DONE)

**Files changed:**
- `src/factory_simulator/protocols/mqtt_publisher.py`
- `tests/unit/test_protocols/test_mqtt.py`

**What was done:**
1. Added `_on_connect` and `_on_disconnect` methods to `MqttPublisher` with paho v2 signatures `(client, userdata, flags, reason_code, properties)`. Both are registered on `self._client` in `__init__` immediately after the client is assigned.
2. Modified `start()` to retry the initial `connect()` up to 3 times with exponential backoff (delays 1 s, 2 s, 4 s). Logs WARNING on each retry and ERROR if all fail. If all 3 fail, the last exception is re-raised. Paho's `loop_start()` handles mid-run reconnection — no additional logic added.
3. Added 6 new tests: retry succeeds on second attempt, raises after all 3 fail, succeeds on third attempt, callbacks are callable, callbacks are registered on the real paho client.

**Decisions:**
- Callbacks registered in `__init__` (not `_create_client`) so they're always applied regardless of whether the client is injected or created.
- Used `getattr(reason_code, "is_failure", False)` to avoid hard dependency on paho `ReasonCode` type.
- `# type: ignore[assignment]` not needed — mypy accepts the assignment without it.
- Test for callback registration used `_ClientSpy` (plain object) + `==` comparison (bound method equality, not identity, since Python creates new bound method objects on each attribute access).

---

## Task 6b.2: CsvWriter Idempotent Close (DONE)

**Files changed:**
- `src/factory_simulator/output/writer.py`
- `tests/unit/test_batch_output.py`

**What was done:**
1. `CsvWriter.close()`: added `if self._file.closed: return` guard at the top — second call is a no-op.
2. `CsvWriter.write_tick()`: added `if self._file.closed: raise RuntimeError(...)` guard — calling after close raises with a clear message.
3. `ParquetWriter.__init__()`: added `self._closed: bool = False` flag (pyarrow's writer object has no `.closed` attribute).
4. `ParquetWriter.close()`: added `if self._closed: return` + `self._closed = True` — idempotent.
5. `ParquetWriter.write_tick()`: added `if self._closed: raise RuntimeError(...)` guard.
6. Added 5 new tests: `TestCsvIdempotentClose` (3 tests) and 2 Parquet tests in `TestParquetWriter`.

**Decisions:**
- Chose `raise RuntimeError` over silent skip for `write_tick()` after close. This makes programming errors visible rather than silently losing data. Documented in docstring.
- `CsvWriter` uses `self._file.closed` (built-in Python file attribute); `ParquetWriter` uses an explicit `_closed` flag since `pq.ParquetWriter` has no `.closed` attribute.

---

## Task 6b.3: SIGTERM Handler for Graceful Docker Shutdown (DONE)

**Files changed:**
- `src/factory_simulator/cli.py`
- `tests/unit/test_cli.py`

**What was done:**
1. Added `import signal` to top-level imports.
2. In `_run_batch()`: before the `try` block, registers a SIGTERM handler via `loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)`. The handler captures `asyncio.current_task()` at registration time and calls `task.cancel()` when SIGTERM arrives. Guarded with `try/except (NotImplementedError, OSError)` for platform safety (Windows).
3. In `_run_realtime()`: same pattern registered before the `servers`/`tasks` lists are created.
4. In `run_command()`: added `except asyncio.CancelledError: return 0`. When SIGTERM cancels the task, the existing `finally` blocks run cleanup, then `asyncio.run()` raises `CancelledError` (with `_interrupt_count == 0`). This catch converts it to exit code 0.
5. Added `TestSigtermHandling` class with two tests: source-code check for `signal.SIGTERM` and `add_signal_handler`; subprocess test that sends SIGTERM during a batch run and verifies exit code 0.

**Decisions:**
- SIGTERM handler registered in both `_run_batch` and `_run_realtime` (not in `_async_run`) so the intent is explicit in each execution path, matching the plan's description.
- Task captured at registration time (closure over `_this_task`) rather than using `asyncio.current_task()` inside the handler itself — handlers run as event-loop callbacks where `current_task()` returns `None`.
- Added `except asyncio.CancelledError` in `run_command()` because when `task.cancel()` is called and the coroutine suppresses `CancelledError` internally (returning normally), Python 3.12's Task marks itself as cancelled on StopIteration (since `_must_cancel` stays True). `asyncio.run()` then raises `CancelledError`. This is different from the SIGINT path where `asyncio.run()` converts it to `KeyboardInterrupt`.
- The existing `finally` block cleanup in `_run_realtime` may be partially interrupted at `await srv.stop()` (a CancelledError is re-thrown there by the task machinery) — this is a pre-existing limitation with the same behaviour as SIGINT. The important thing is that `engine.stop()` and `health.update(status="stopping")` still run before the interrupt.
