Read CLAUDE.md for project rules and conventions.

You are implementing Phase 2 (OPC-UA, MQTT, and Packaging Scenarios) of the Collatr Factory Simulator.

## CONTEXT

Phase 1 is complete. The simulator has: configuration loading (Pydantic), simulation clock, signal value store, all 12 signal models, all 7 packaging equipment generators (47 signals), Cholesky correlation pipeline, 3 noise distributions (Gaussian, Student-t, AR(1)), speed-dependent sigma, basic scenarios (job changeover, shift change, unplanned stop), and a full Modbus TCP server with the packaging register map.

Phase 2 adds OPC-UA and MQTT protocol adapters so all 47 signals are accessible on all three protocols. It adds 7 more packaging scenarios (web break, dryer drift, ink viscosity excursion, registration drift, cold start spike, coder depletion, material splice). It adds the ground truth event log (JSONL). It also fixes the environment generator to use the composite model (daily sine + HVAC + perturbations).

The PRD is in `prd/` (23 files, ~5,700 lines). Read the relevant sections referenced in each task.

Reference patterns from Phase 0 spikes are in `docs/validation-spikes.md`. These contain asyncua and paho-mqtt 2.0 API patterns.

## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read `plans/phase-2-opcua-mqtt-scenarios.md` for the full plan
2. Read `plans/phase-2-tasks.json` to find the **first** task with `"passes": false`
3. Read the relevant PRD sections referenced in that task
4. Implement ONLY that single task
5. Run tests: `ruff check src tests && mypy src && pytest` -- ALL must pass
6. Update `plans/phase-2-tasks.json`: set `"passes": true` for your completed task
7. Update `plans/phase-2-progress.md` with what you built and any decisions
8. Commit: `phase-2: <what> (task 2.X)`
9. Do NOT push. Pushing is handled externally.
10. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## PHASE-SPECIFIC NOTES

### Group A: OPC-UA Server (Tasks 2.1-2.3)

- **Task 2.1 (Node Tree):** Build the full `PackagingLine` node tree per Appendix B. Use string NodeIDs like `ns=2;s=PackagingLine.Press1.LineSpeed`. Reference the asyncua spike patterns in `docs/validation-spikes.md` for server setup, node creation, EURange properties, and access levels. The namespace URI is `urn:collatr:factory-simulator`. Try EnumStrings on State nodes; if asyncua makes this difficult, skip and document.

- **Task 2.2 (Value Sync):** The OPC-UA adapter reads from SignalStore and updates node values. Subscriptions at 500ms+ intervals should deliver data change notifications. Setpoint writes (Dryer Zone setpoints) must propagate back to the store. Follow the same adapter pattern as ModbusServer: constructor takes config + store, `run()` is an async task.

- **Task 2.3 (Integration Tests):** Connect an asyncua client, browse the tree, read values, create subscriptions, write setpoints. This mirrors `tests/integration/test_modbus_integration.py` but for OPC-UA. The OPC-UA spike tests show the client API patterns.

### Group B: MQTT Publisher (Tasks 2.4-2.6)

- **Task 2.4 (Publisher):** The MQTT adapter is a publisher, not a server. It reads from SignalStore and publishes JSON to Mosquitto. Use paho-mqtt 2.0 `CallbackAPIVersion.VERSION2` from the spike patterns. Key details: per-signal publish intervals from config, QoS per topic per Appendix C (QoS 1 for state/fault/counter, QoS 0 for analogs), retain per Appendix C (Yes for all except vibration), LWT on the status topic. Client-side buffer: 1000 messages max, drop oldest. The publisher runs as an async task, not blocking the event loop.

- **Task 2.5 (Batch Vibration):** Simple addition: publish a combined `{"x": ..., "y": ..., "z": ...}` payload alongside per-axis topics. Both fire by default; per-axis configurable off.

- **Task 2.6 (Integration Tests):** Requires Docker (Mosquitto sidecar). Subscribe to all topics, verify payloads. Mark with `@pytest.mark.integration`. Patterns from `tests/spikes/test_spike_mqtt.py`.

### Group C: Packaging Scenarios (Tasks 2.7-2.13)

All scenarios follow the base pattern in `src/factory_simulator/scenarios/base.py`. Each scenario:
1. Has a trigger condition (scheduled time, signal threshold, state transition)
2. Modifies signals in the store or parameters on generators
3. Has a duration and recovery phase
4. Returns the system to normal operation when done

Key implementation notes:
- **Web break (2.7):** The tension spike is 100-500ms. At 100ms engine tick, this is 1-5 ticks. Coil 3 must be set to true. This is the most complex scenario -- it has a multi-phase sequence (spike, deceleration, fault, recovery).
- **Dryer drift (2.8):** Modifies the actual temperature, not the setpoint. The PID loop (first_order_lag) keeps trying to reach setpoint but the drift adds an offset.
- **Cold start (2.11):** Trigger is state transition after idle threshold. The spike affects energy.line_power and press.main_drive_current simultaneously.
- **Material splice (2.13):** Machine stays Running. This is a flying splice. Trigger: unwind_diameter < 150mm. Resets unwind to 1500mm.
- **Coder depletion (2.12):** Also fix G5: gutter_fault MTBF should be 500+ hours. Adjust probability to ~0.000000556/s.

### Group D: Ground Truth + Environment + Cross-Protocol (Tasks 2.14-2.16)

- **Task 2.14 (Ground Truth):** JSONL file, append-only. First line is config header. Every scenario start/end, state change, and anomaly gets a record. Wire into the scenario engine -- scenarios should call the logger. Section 4.7 has the exact JSON schema.

- **Task 2.15 (Environment Composite):** This is a Phase 1 carry-forward (Y1). Replace the plain sinusoidal model with 3 layers: (1) existing daily sine, (2) HVAC cycling via BangBangModel with ~20-min period, (3) random perturbations as a Poisson process with first_order_lag decay. The BangBangModel and FirstOrderLagModel already exist. Compose them.

- **Task 2.16 (Cross-Protocol):** The capstone integration test. All three protocols running, same engine, same store. Read press.line_speed via Modbus (float32), OPC-UA (Double), and MQTT (JSON value). They must match within float32 precision.

## STOPPING RULES

**After completing ONE task:** Output `TASK_COMPLETE` and stop immediately.
Do not look for the next task. Do not start another task.
The ralph.sh loop will call you again for the next iteration.

**When ALL tasks in the task JSON have "passes": true:**
1. Do NOT output PHASE_COMPLETE yet.
2. Spawn a sub-agent code review.
3. Write the review to `plans/phase-2-review.md`
4. Review checks: PRD compliance (node tree matches Appendix B, topics match Appendix C, scenario sequences match Section 5), CLAUDE.md rules, error handling (broker disconnect, asyncua server crash), test coverage of scenario sequences, cross-protocol consistency.
5. Address all RED Must Fix findings. Re-run `ruff check src tests && mypy src && pytest` after each fix.
6. Commit fixes: `phase-2: address code review findings`
7. Push all commits.
8. THEN output: PHASE_COMPLETE
