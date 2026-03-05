Read CLAUDE.md for project rules and conventions.

You are implementing Phase 6b (Robustness) of the Collatr Factory Simulator.

## CONTEXT

Phases 0-5 are complete (feature-complete simulator). Phase 6a fixed all RED issues and high-priority data correctness YELLOWs from the three-reviewer code review.

Current state:
- **Packaging**: 47 signals, 7 equipment generators, 17 scenario types, all 3 protocols
- **F&B**: 68 signals, 10 equipment generators, 7 F&B scenarios, CDAB + multi-slave Modbus
- **Network topology**: collapsed + realistic modes, multi-port Modbus/OPC-UA, scan cycle quantisation, clock drift
- **Ground truth**: logger wired into CLI, header complete (all scenarios), double-logging fixed
- **Evaluation framework**: severity weights normalised (PascalCase→snake_case), open scenarios handled
- **Docker**: `.dockerignore`, non-root user, OPC-UA EngineeringUnits, oven UID routing fixed
- 3100+ tests passing, ruff + mypy clean

**Phase 6b addresses 5 robustness YELLOW issues (Y4-Y8) from the code review.**

The full review reports are in:
- `plans/review-architecture.md`
- `plans/review-protocol-fidelity.md`
- `plans/consolidated-review-action-plan.md`

The Phase 6b plan with detailed per-task instructions is in `plans/phase-6b-robustness.md`. Read it.

## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read `plans/phase-6b-robustness.md` for the full plan
2. Read `plans/phase-6b-tasks.json` to find the **first** task with `"passes": false`
3. Check `depends_on` — if any dependency has `"passes": false`, skip to the next eligible task
4. Read the relevant review file for full context on the issue (the `review_ref` field tells you which)
5. Read the relevant source files before changing anything
6. Implement ONLY that single task's fix
7. Run the new/modified test file alone first: `ruff check src tests && pytest tests/path/to/test.py -v --tb=short`
8. Run ALL tests: `ruff check src tests && mypy src && pytest` — ALL must pass
9. Update `plans/phase-6b-tasks.json`: set `"passes": true` for your completed task
10. Update `plans/phase-6b-progress.md` with what you fixed and any decisions
11. Commit: `phase-6b: <what> (task 6b.X)`
12. Do NOT push. Pushing is handled externally.
13. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## PHASE-SPECIFIC NOTES

### MQTT Startup Retry (Task 6b.1)

The `MqttPublisher.start()` method at line 595 calls `self._client.connect()` once with no error handling. Docker Compose starts containers in parallel, so the Mosquitto sidecar may not be ready when the simulator starts.

Key points:
- **Retry the initial `connect()` only** — 3 attempts with exponential backoff (1s, 2s, 4s)
- **Do NOT add custom reconnection logic** for mid-run drops. Paho-mqtt's `loop_start()` already handles automatic reconnection internally.
- **Add `on_connect` and `on_disconnect` callbacks** for logging/visibility only
- **Paho-mqtt v2 callback signatures** differ from v1. Check the installed version. For v2: `on_connect(client, userdata, flags, reason_code, properties)` and `on_disconnect(client, userdata, flags, reason_code, properties)`. The `reason_code` is a `ReasonCode` object, not an int.
- If all 3 retries fail, let the exception propagate (don't swallow it)
- The retry should be async-friendly: use `await asyncio.sleep(delay)` between attempts

### CsvWriter Double-Close (Task 6b.2)

`CsvWriter.close()` at line 140 flushes the buffer then closes the file handle. A second call tries to flush to a closed file → `ValueError`.

Simple fix: check `self._file.closed` at the top of `close()`. Also check `ParquetWriter.close()` for the same pattern.

Decide what happens if `write_tick()` is called after `close()` — either raise `RuntimeError("Writer is closed")` or silently skip. Document the choice in the docstring.

### SIGTERM Handler (Task 6b.3)

Docker sends SIGTERM when stopping containers. `asyncio.run()` only handles SIGINT by default.

The pattern:
```python
loop = asyncio.get_running_loop()
loop.add_signal_handler(signal.SIGTERM, _request_shutdown)
```

Where `_request_shutdown` cancels the main task to trigger the existing `finally` cleanup. Apply to both real-time and batch paths.

**Platform note:** `add_signal_handler` is Linux/macOS only. On Windows it raises `NotImplementedError`. Guard with a try/except if you want cross-platform safety, but this is a Docker-first project so Linux is the primary target.

### Profile-Aware 0x06 (Task 6b.4)

The `_check_machine_state_transition()` method hardcodes `press.machine_state`. For F&B, there's no `press.machine_state` signal — the store returns `None` and 0x06 never fires.

The fix is to make the state signal ID configurable:
1. Add `state_signal_id: str | None` parameter to the `ModbusServer` constructor (or to the class managing transition tracking)
2. `_check_machine_state_transition()` uses the configured signal instead of hardcoded `"press.machine_state"`
3. In collapsed mode: packaging uses `"press.machine_state"`, F&B uses a suitable state signal (check the F&B config — equipment like mixer, oven, filler each have a `state` signal)
4. In realistic mode: the topology manager should pass the appropriate state signal per endpoint

Check how the `ModbusServer` is created in `data_engine.py` and `topology.py` to find the right injection point.

### Wire EvaluationConfig (Task 6b.5)

`EvaluationConfig` exists in `config.py` (line 1160) with all fields defined. `FactoryConfig` (line 1384) has no `evaluation` field.

Steps:
1. Add `evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)` to `FactoryConfig`
2. Add `evaluation:` section to both YAML configs (optional — Pydantic defaults work fine, but explicit is better)
3. In the evaluate CLI path (`_async_run` or the evaluate subcommand handler), pass `config.evaluation` settings to the evaluator
4. The evaluator currently hardcodes `pre_margin=30.0`, `post_margin=60.0`, etc. — these should come from config when available
5. Keep backward compat: absent `evaluation:` section → Pydantic defaults (identical to current hardcoded values)

## STOPPING RULES

**After completing ONE task:** Output `TASK_COMPLETE` and stop immediately.
Do not look for the next task. Do not start another task.

**If a test cannot pass after 3 genuine attempts:** STOP. Document the issue in `plans/phase-6b-progress.md`. Output `TASK_BLOCKED: <reason>` and stop.

**Dependency check:** If the first `"passes": false` task has unsatisfied dependencies, find the next task whose dependencies are all satisfied. If NO tasks are eligible, output `PHASE_BLOCKED: waiting on <task IDs>` and stop.

## COMPLETION

When ALL tasks in the task JSON have `"passes": true`:
1. Push all commits.
2. Output: PHASE_COMPLETE
