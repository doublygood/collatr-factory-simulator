Read CLAUDE.md for project rules and conventions.

You are implementing Phase 5 (Network Topology, Evaluation, and Polish) of the Collatr Factory Simulator.

## CONTEXT

Phases 0-4 are complete. Both profiles are fully operational:
- **Packaging**: 47 signals, 7 equipment generators, 17 scenario types, all 3 protocols
- **F&B**: 68 signals, 10 equipment generators (6 new + 4 shared), 7 F&B scenarios, CDAB + multi-slave Modbus
- 2459+ tests passing, ruff + mypy clean
- Ground truth JSONL logging operational
- Poisson scheduling, priority/conflict resolution, 4 advanced scenarios (bearing wear, micro-stops, contextual anomalies, intermittent faults)
- Full data quality injection: comm drops (3 protocols), sensor disconnect/stuck, Modbus exceptions/partial, duplicate timestamps, timezone offset
- Noise calibration for both profiles (AR(1), Student-t, Gaussian per PRD 10.3)
- Reproducibility verified (seed=42 → identical output)

Phase 5 adds three major workstreams:
1. **Network topology** — Multi-controller Modbus/OPC-UA servers with per-controller connection behaviour
2. **Evaluation framework** — Event-level anomaly detection metrics per PRD Section 12
3. **Productisation** — CLI, batch output (CSV/Parquet), Docker Compose, README, example configs

The full plan is in `plans/phase-5-topology-eval-polish.md`. Read it.

## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read `plans/phase-5-topology-eval-polish.md` for the full plan
2. Read `plans/phase-5-tasks.json` to find the **first** task with `"passes": false`
3. Check `depends_on` — if any dependency has `"passes": false`, skip to the next eligible task
4. Read the relevant source files and PRD sections referenced in that task
5. Implement ONLY that single task
6. Run tests: `ruff check src tests && mypy src && pytest` — ALL must pass
7. Update `plans/phase-5-tasks.json`: set `"passes": true` for your completed task
8. Update `plans/phase-5-progress.md` with what you built and any decisions
9. Commit: `phase-5: <what> (task 5.X)`
10. Do NOT push. Pushing is handled externally.
11. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## PHASE-SPECIFIC NOTES

### Network Topology Manager (Task 5.1)

The topology manager is the foundation for realistic mode. Key design points:

- **Collapsed mode** is the current behaviour — single port per protocol. This MUST remain the default and continue to work exactly as it does now. Do NOT break existing tests.
- **Realistic mode** spawns per-controller servers per PRD Section 3a.4. Port mappings:
  - Packaging Modbus: 5020 (press+energy), 5021 (laminator), 5022 (slitter)
  - F&B Modbus: 5030 (mixer), 5031 (oven+energy, UIDs 1/2/3/10), 5032 (filler), 5033 (sealer), 5034 (chiller), 5035 (CIP)
  - Packaging OPC-UA: 4840 (full tree)
  - F&B OPC-UA: 4841 (filler), 4842 (QC/checkweigher)
- Config models go in `config.py`. Topology manager goes in a new `topology.py`.
- Add `network: NetworkConfig | None = None` to `FactoryConfig`. When None, use collapsed defaults.

### Multi-Port Modbus Servers (Task 5.2)

Key points for realistic mode:

- Each `ModbusServer` instance gets an `endpoint` spec defining its port, unit_id(s), register range, byte_order, and controller type
- **Out-of-range reads must return Modbus exception 0x02** (Illegal Data Address). This is how real PLCs behave.
- **Multi-slave on shared ports**: oven gateway at 5031 serves UIDs 1,2,3 (zones) and UID 10 (energy). Press port at 5020 serves UID 1 (press) and UID 5 (energy). Each UID maps to different register ranges.
- **Connection limits**: S7-1500 max 16 TCP connections, S7-1200 max 3, CompactLogix max 8, Eurotherm gateway max 2, Danfoss max 2, PM5560 max 4
- **Response latency**: inject configurable delay per read per controller type (S7-1500 50ms typical, Eurotherm 150ms typical)
- `DataEngine` must accept `NetworkTopologyManager` and create servers accordingly
- **Do not break collapsed mode.** All existing tests must still pass.

### Multi-Port OPC-UA and Clock Drift (Task 5.3)

- Packaging: 1 OPC-UA server on 4840 (no change from current)
- F&B: 2 OPC-UA servers — filler on 4841, QC on 4842
- **Clock drift model**: `drifted_time = sim_time + initial_offset_ms/1000 + drift_rate_s_per_day * elapsed_hours / 24`
- Each controller gets a `ClockDriftModel` instance
- OPC-UA `SourceTimestamp` uses drifted time. MQTT JSON timestamp uses drifted time.
- Ground truth ALWAYS uses true `sim_time` — never drifted time
- Modbus has no timestamps — drift does not apply
- Eurotherm controllers drift 5-10 s/day. Siemens S7-1500 drifts 0.1-0.5 s/day.

### Scan Cycle Quantisation (Task 5.4)

This was listed in Phase 4 PRD but correctly deferred to Phase 5 (requires per-controller topology).

The key concept: real PLCs update registers once per scan cycle. Between scans, values are stale.

```python
if sim_time >= next_scan_boundary:
    register_value = current_generated_value
    next_scan_boundary += scan_cycle_ms * (1.0 + rng.uniform(0, jitter_pct))
else:
    register_value = last_scan_output  # stale
```

- Only applies in realistic mode. Collapsed mode: direct passthrough (no quantisation).
- Wire into `ModbusServer.sync_registers()` — pass values through `ScanCycleModel.tick()` before writing to context.
- Per-controller scan times from PRD 3a.8: S7-1500=10ms, S7-1200=20ms, CompactLogix=15ms, Eurotherm=100ms, Danfoss=100ms

### Independent Connection Drops (Task 5.5)

- In realistic mode, each controller endpoint gets its own `CommDropScheduler` (reuse Phase 4 class from `comm_drop.py`)
- Controller-specific MTBF: Eurotherm gateway 8-24h (drops frequently), S7-1500 72h+ (very stable)
- Key test: drop one controller, verify ALL other controllers continue serving data
- In collapsed mode, keep existing Phase 4 behaviour (single comm drop per protocol)

### Evaluation Framework (Tasks 5.6-5.7)

The evaluator consumes two inputs:
1. **Ground truth JSONL** — the sidecar file the simulator already produces
2. **Detection alerts CSV** — produced by whatever anomaly detection system is being evaluated (not by the simulator)

The evaluator matches alerts to ground truth events and computes metrics. It does NOT run anomaly detection itself.

Key matching rules:
- An event is detected if at least one alert falls within `[start - pre_margin, end + post_margin]`
- Multiple alerts in the same window count as one TP
- Alerts outside all windows are FP
- If two events have overlapping windows, alert goes to the nearest event by start time
- **Random baseline**: compute anomaly density (total anomaly ticks / total ticks), generate random alerts at that rate, compute baseline metrics. Any useful detector must beat this baseline.

### Batch Output (Task 5.8)

- CSV column order: `timestamp, signal_id, value, quality`
- Parquet: columnar per-signal layout (each signal is a column, timestamps are the index)
- Event-driven signals (machine_state, fault_code) have a `changed` boolean column — only rows where `changed=True` represent actual state transitions
- Wire into `DataEngine`: when `batch_output.format != "none"`, call `writer.write_tick()` after each engine tick
- `pyarrow` is an optional dependency (only needed for Parquet output)

### CLI Entry Point (Task 5.9)

Use `argparse` (no external dependency). Subcommands: `run`, `evaluate`, `version`.

```bash
# Start simulator (default: collapsed, real-time, packaging)
python -m factory_simulator run

# Batch mode (7 days, 100x, CSV output)
python -m factory_simulator run --batch-output ./output --batch-duration 7d --batch-format csv --time-scale 100 --seed 42

# Evaluate detections against ground truth
python -m factory_simulator evaluate --ground-truth output/ground_truth.jsonl --detections output/detections.csv

# Print version
python -m factory_simulator version
```

### Docker Compose (Task 5.10)

The health endpoint is a simple `asyncio`-based HTTP server on port 8080:

```python
# GET /health
{"status": "running", "profile": "packaging", "sim_time": "2026-01-01T08:30:00Z", "signals": 47, "modbus": "up", "opcua": "up", "mqtt": "up"}
```

Port mappings in `docker-compose.yaml` (collapsed mode):
- 502:502 (Modbus), 4840:4840 (OPC-UA), 1883:1883 (MQTT), 8080:8080 (health)

Realistic mode override (`docker-compose.realistic.yaml`):
- 5020-5035:5020-5035 (Modbus), 4840-4842:4840-4842 (OPC-UA)

### Performance Profiling (Task 5.12)

These are benchmarks, not functional tests. Use `@pytest.mark.performance` marker.

Key targets (not hard assertions — just measure and record):
- 10x protocol serving: tick latency < 100ms target (engine generates + serves data fast enough)
- 100x batch: 24h simulation completes in < 15 minutes wall time
- Realistic mode overhead: < 2x slowdown vs collapsed
- 7-day memory: RSS < 2x initial

### Acceptance Test (Task 5.13)

The acceptance test is the "done" gate. It verifies everything from PRD Section 11.

Key check: a fresh engineer experience. The README must be accurate enough that someone who has never seen the project can get data flowing in 15 minutes.

## STOPPING RULES

**After completing ONE task:** Output `TASK_COMPLETE` and stop immediately.
Do not look for the next task. Do not start another task.
The ralph.sh loop will call you again for the next iteration.

**If a test cannot pass after 3 genuine attempts:** STOP. Document the issue in `plans/phase-5-progress.md`. Output `TASK_BLOCKED: <reason>` and stop.

**Dependency check:** If the first `"passes": false` task has unsatisfied dependencies, find the next task whose dependencies are all satisfied. If NO tasks are eligible, output `PHASE_BLOCKED: waiting on <task IDs>` and stop.

## COMPLETION

When ALL tasks in the task JSON have `"passes": true`:
1. Do NOT output PHASE_COMPLETE yet.
2. Spawn a sub-agent code review.
3. Write the review to `plans/phase-5-review.md`
4. Review checks:
   - Collapsed mode fully backward-compatible (no existing test regressions)
   - Realistic mode: correct ports, register ranges, UIDs per PRD 3a.4
   - Scan cycle quantisation produces stale reads between boundaries
   - Clock drift offsets visible in OPC-UA timestamps
   - One controller drop does not affect others
   - Evaluation framework metrics correct for known test data
   - Batch output produces valid CSV/Parquet
   - CLI subcommands work
   - Docker Compose builds and starts
   - README is accurate and complete
5. Address all RED Must Fix findings. Re-run `ruff check src tests && mypy src && pytest` after each fix.
6. Commit fixes: `phase-5: address code review findings`
7. Push all commits.
8. THEN output: PHASE_COMPLETE
