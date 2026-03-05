Read CLAUDE.md for project rules and conventions.

You are implementing Phase 6d (Maintenance & CI) of the Collatr Factory Simulator.

## CONTEXT

Phases 0-5 are complete (feature-complete simulator). Phase 6a fixed all RED issues and high-priority YELLOWs (Y1-Y3). Phase 6b fixed robustness YELLOWs (Y4-Y8). Phase 6c fixed validation and protocol polish YELLOWs (Y9-Y15).

Current state:
- **Packaging**: 47 signals, 7 equipment generators, 17 scenario types, all 3 protocols
- **F&B**: 68 signals, 10 equipment generators, 7 F&B scenarios, CDAB + multi-slave Modbus
- **Network topology**: collapsed + realistic modes, multi-port Modbus/OPC-UA, scan cycle quantisation, clock drift
- **Ground truth**: logger wired, header complete, double-logging fixed, open scenarios handled
- **Evaluation framework**: weights normalised, open scenarios handled, EvaluationConfig wired
- **Docker**: .dockerignore, non-root user, SIGTERM handler, regular install
- **OPC-UA**: EngineeringUnits, MinimumSamplingInterval
- **Robustness**: MQTT startup retry, CsvWriter idempotent close, profile-aware 0x06
- **Signal integrity**: dryer + oven zone Cholesky correlation, Coil 4 fix, docstring corrections
- **Config validation**: min_clamp <= max_clamp, negative clock drift allowed
- 3050+ tests passing, ruff + mypy clean

**Phase 6d addresses the remaining YELLOW issues (Y16-Y27) — maintenance, test coverage, CI, and protocol polish. Y24 already fixed in 6a.**

The full review reports are in:
- `plans/review-architecture.md`
- `plans/review-protocol-fidelity.md`
- `plans/review-signal-integrity.md`
- `plans/consolidated-review-action-plan.md`

The Phase 6d plan with detailed per-task instructions is in `plans/phase-6d-maintenance-ci.md`. Read it.

## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read `plans/phase-6d-maintenance-ci.md` for the full plan
2. Read `plans/phase-6d-tasks.json` to find the **first** task with `"passes": false`
3. Check `depends_on` — if any dependency has `"passes": false`, skip to the next eligible task
4. Read the relevant review file for full context on the issue (the `review_ref` field tells you which)
5. Read the relevant source files before changing anything
6. Implement ONLY that single task's fix
7. Run the new/modified test file alone first: `ruff check src tests && pytest tests/path/to/test.py -v --tb=short`
8. Run ALL tests: `ruff check src tests && mypy src && pytest` — ALL must pass
9. Update `plans/phase-6d-tasks.json`: set `"passes": true` for your completed task
10. Update `plans/phase-6d-progress.md` with what you fixed and any decisions
11. Commit: `phase-6d: <what> (task 6d.X)`
12. Do NOT push. Pushing is handled externally.
13. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## PHASE-SPECIFIC NOTES

### Shared Reference Epoch Constant (Task 6d.1)

`_REFERENCE_EPOCH_TS` is duplicated in 3 files (mqtt_publisher.py, opcua_server.py, health/server.py) and created per-call in ground_truth.py. Also, `_sim_time_to_iso()` and `_sim_time_to_datetime()` are separate implementations of the same conversion in mqtt_publisher.py and opcua_server.py.

Create `src/factory_simulator/time_utils.py` with shared constants and functions. Update all 4 files to import from it. Delete the duplicated definitions.

**MQTT offset conversion:** The current `_sim_time_to_iso(sim_time, offset_hours)` takes offset in hours. The new `sim_time_to_iso(sim_time, offset_s)` should take offset in seconds. Update call sites to multiply hours by 3600:
```python
# Before: _sim_time_to_iso(sim_time, offset_hours)
# After:  sim_time_to_iso(sim_time, offset_hours * 3600.0)
```

**Ground truth:** The `_format_time()` static method should delegate to `sim_time_to_iso()` — this also fixes the Y17 performance issue.

### _format_time() Performance Fix (Task 6d.2)

**Depends on 6d.1.** After 6d.1, verify that `_format_time()` in ground_truth.py no longer creates `datetime(2026, 1, 1, tzinfo=UTC)` per call. If 6d.1 handled it, this is a verify-and-mark-done task. If not, apply the fix.

### Configurable Health Server Port (Task 6d.3)

Add `health_port: int = 8080` to `SimulationConfig`. Add validator (0-65535). Add `SIM_HEALTH_PORT` to the env override map. Check where env overrides are processed — look for a section that reads `os.environ` and applies overrides to the config, or find if there's a pattern like `_apply_env_overrides()`.

Update `cli.py:446`:
```python
# Before: health = HealthServer(port=8080, store=engine.store)
# After:  health = HealthServer(port=config.simulation.health_port, store=engine.store)
```

### Server Task Verification (Task 6d.4)

After each `asyncio.create_task(srv.start())` + `await asyncio.sleep(0.05)`, check `task.done()`:

```python
if task.done() and not task.cancelled():
    exc = task.exception()
    if exc is not None:
        raise RuntimeError(f"Server failed to start: {exc}") from exc
```

Extract a helper `_start_server()` to avoid repeating this 4 times. Apply to health server, Modbus servers, OPC-UA servers, and MQTT publishers.

### Narrow Exception Suppression (Task 6d.5)

Line 490 in cli.py:
```python
# Before:
with contextlib.suppress(Exception):
    await srv.stop()

# After:
with contextlib.suppress(asyncio.CancelledError, OSError, ConnectionError):
    await srv.stop()
```

### Dead Config Cleanup (Task 6d.6)

Remove `sparkplug_b` and `retain` from `MqttProtocolConfig`. Check the model's `model_config` for `extra` setting — if `extra="forbid"`, existing YAML configs with these keys will break. Remove the keys from both YAML files too.

**Do NOT remove** `TopicEntry.retain` or `_retain_for_topic()` — those are the per-topic retain logic that actually works. Only remove the unused GLOBAL fields on `MqttProtocolConfig`.

### Generator Test Files (Tasks 6d.7-6d.11)

Follow the existing pattern in `tests/unit/test_generators/test_mixer.py`:

1. `_make_<gen>_config()` — helper that creates a minimal `EquipmentConfig` with all required signals and their `SignalConfig` entries.
2. `_run_ticks(gen, store, n, sim_time_start, dt)` — helper that runs N ticks and returns the results.
3. Tests that create the generator, set up store state (press speed, machine state), run ticks, and assert expected behaviour.

**Key points for each generator:**
- **Coder (6d.7):** 11 signals. Follows press state. Has counter (prints_total), depletion (ink_level), random walk (ink_viscosity). Needs press.machine_state and press.line_speed in store.
- **Energy (6d.8):** 2 signals. Correlated with press speed. Needs press.line_speed in store.
- **Laminator (6d.9):** 5 signals. Follows press. Has depletion (adhesive_weight). Needs press.machine_state and press.line_speed in store.
- **Slitter (6d.10):** 3 signals. Follows press. Has counter (reel_count). Needs press.machine_state and press.line_speed in store.
- **Vibration (6d.11):** 3 signals. Cholesky-correlated noise. Needs press.line_speed in store. Test correlation by running many ticks and computing sample correlations.

**Read the generator source file first** to understand what signals it produces, what params it expects, and what store values it reads.

### CI Matrix (Task 6d.12)

Update `.github/workflows/ci.yml`:

1. Add `"3.13"` to unit test matrix `python-version`.
2. Expand integration test job to run `tests/integration/` instead of just `test_acceptance.py`. Exclude `test_mqtt_integration.py` (needs broker) and tests marked `slow`.
3. Add `cache-dependency-path: "requirements-dev.txt"` to pip cache config.
4. Keep lint and typecheck on 3.12 only.

**Check which integration tests need an MQTT broker** — look for `pytest.importorskip("paho")` or broker connectivity checks at module level. `test_mqtt_integration.py` and `test_fnb_opcua_mqtt_integration.py` likely need a broker. Exclude both if so.

### OPC-UA Inactive Profile Nodes (Task 6d.13)

PRD 3.2.1 says inactive profile nodes should exist with `AccessLevel=0` and `StatusCode.BadNotReadable`. Currently, in collapsed mode, only active profile nodes are created.

**Scope:** Collapsed mode only. In realistic mode, each OPC-UA server is scoped to its own equipment and there is no inactive concept.

**Approach:** Add `inactive_config: FactoryConfig | None` parameter to `OpcuaServer`. In `_build_node_tree()`, after building active nodes, iterate inactive config's equipment/signals and create nodes with:
- `AccessLevel = 0` (no read, no write)
- StatusCode `BadNotReadable`
- EURange, EngineeringUnits, MinimumSamplingInterval as for active nodes
- NOT added to `self._nodes` / `self._node_to_signal` (no sync)

In `cli.py` / `data_engine.py`, when creating OPC-UA servers in collapsed mode, load the other profile's config and pass as `inactive_config`.

### Profile-Specific LWT Topic (Task 6d.14)

Both profiles use `lwt_topic: "collatr/factory/status"`. If both ran simultaneously, they'd conflict.

**Fix:** Change `MqttProtocolConfig.lwt_topic` default to empty string `""`. When empty, auto-generate from `{topic_prefix}/{line_id}/status` (e.g. `collatr/factory/packaging1/status`). When explicitly set, use as-is (backward compat).

Update both YAML configs to remove the explicit `lwt_topic` (or update to profile-specific paths).

## STOPPING RULES

**After completing ONE task:** Output `TASK_COMPLETE` and stop immediately.
Do not look for the next task. Do not start another task.

**If a test cannot pass after 3 genuine attempts:** STOP. Document the issue in `plans/phase-6d-progress.md`. Output `TASK_BLOCKED: <reason>` and stop.

**Dependency check:** If the first `"passes": false` task has unsatisfied dependencies, find the next task whose dependencies are all satisfied. If NO tasks are eligible, output `PHASE_BLOCKED: waiting on <task IDs>` and stop.

## COMPLETION

When ALL tasks in the task JSON have `"passes": true`:
1. Push all commits.
2. Output: PHASE_COMPLETE
