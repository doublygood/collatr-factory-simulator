Read CLAUDE.md for project rules and conventions.

You are implementing Phase 0 (Validation Spikes) of the Collatr Factory Simulator.

## CONTEXT

This is a brand new project. No code exists yet. Phase 0 establishes the project scaffolding and validates three critical library assumptions before committing to architecture:

1. pymodbus can run 7+ async Modbus TCP servers concurrently in one event loop
2. Mosquitto sidecar + paho-mqtt handles 50 msg/s with retained messages and LWT
3. asyncua can run 3+ OPC-UA servers concurrently in one event loop

The PRD is in `prd/` (23 files, ~5,700 lines). Read the relevant sections referenced in each task.

## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read `plans/phase-0-validation-spikes.md` for the full plan
2. Read `plans/phase-0-tasks.json` to find the **first** task with `"passes": false`
3. Read the relevant PRD sections referenced in that task
4. Implement ONLY that single task
5. Run tests: `ruff check src tests && pytest` -- ALL must pass
6. Update `plans/phase-0-tasks.json`: set `"passes": true` for your completed task
7. Update `plans/phase-0-progress.md` with what you built and any decisions
8. Commit: `phase-0: <what> (task 0.X)`
9. Do NOT push. Pushing is handled externally.
10. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## PHASE-SPECIFIC NOTES

- **Task 0.1 (scaffolding):** Create the full project structure. Use `pyproject.toml` for project metadata, pytest config, ruff config, and mypy config. Create `requirements.txt` (production deps) and `requirements-dev.txt` (test/lint deps). Create `src/factory_simulator/__init__.py` and `tests/` directories. Add a `.gitignore` for Python. Verify: `ruff check src tests` passes, `mypy src` passes, `pytest` discovers test directories (even with no tests yet). Do NOT install packages -- just create the files.

- **Task 0.2 (Modbus spike):** The spike code goes in `tests/spikes/`. This is exploratory code that validates library capabilities. Write it as a pytest test file (`test_spike_modbus.py`) so it runs in CI. Use ports 15020-15026 (high ports to avoid conflicts). Each test should be async (`pytest-asyncio`). Keep server lifecycle within the test (start in fixture, stop in cleanup). Test multi-slave, FC06 rejection, and max register limit.

- **Task 0.3 (MQTT spike):** Requires Docker. Create `docker-compose.yml` at project root and `config/mosquitto.conf`. The spike test may need to be marked with `@pytest.mark.integration` if it requires Docker to be running. Write the test in `tests/spikes/test_spike_mqtt.py`. Use paho-mqtt 2.0 API (not 1.x -- the API changed significantly).

- **Task 0.4 (OPC-UA spike):** Write in `tests/spikes/test_spike_opcua.py`. asyncua server startup is slow (2-5 seconds). Use generous timeouts in fixtures. Use port 0 (OS-assigned) to avoid conflicts, extract actual port after server start. Test subscriptions, StatusCode propagation, and measure RSS.

- **Task 0.5 (documentation):** Consolidate spike results into `docs/validation-spikes.md`. Include: pass/fail, performance numbers, library versions, quirks found, and reference code patterns for Phase 1. Ensure all spike tests still pass.

## STOPPING RULES

**After completing ONE task:** Output `TASK_COMPLETE` and stop immediately.
Do not look for the next task. Do not start another task.
The ralph.sh loop will call you again for the next iteration.

**When ALL tasks in the task JSON have "passes": true:**
1. Do NOT output PHASE_COMPLETE yet.
2. Spawn a sub-agent code review.
3. Write the review to `plans/phase-0-review.md`
4. Address all red Must Fix findings. Re-run `ruff check src tests && pytest` after each fix.
5. Commit fixes: `phase-0: address code review findings`
6. Push all commits.
7. THEN output: PHASE_COMPLETE
