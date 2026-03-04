# Phase 5 Independent Code Review

**Reviewer:** Independent subagent  
**Date:** 2026-03-04  
**Scope:** Phase 5 (Network Topology, Evaluation, and Polish) — all 13 tasks  
**Codebase state:** Post-local-agent self-review (plans/phase-5-review.md)

---

## 1. Executive Summary

**Verdict: CONDITIONAL GO**

Phase 5 is substantially well-implemented. The core architecture — topology manager, multi-port Modbus servers, OPC-UA node tree filtering, scan cycle quantisation, clock drift modelling, evaluation framework, batch output, CLI, and Docker infrastructure — is correct and well-tested (2963 tests, ruff clean, mypy clean).

However, I found **2 RED issues** and **5 YELLOW issues** that the local agent's self-review did not identify. The most significant is that MQTT clock drift is never wired in the real-time CLI path, meaning MQTT timestamps in realistic mode will never show controller-specific clock offsets. The second RED issue is that connection limit enforcement (PRD 3a.5: "The simulator enforces these limits per endpoint") and response latency injection are config-only — stored but not enforced at runtime.

The local agent's self-review identified 2 YELLOW issues (health server sim_time epoch, Dockerfile EXPOSE 1883). I confirm the sim_time issue was already fixed (the code uses `_REFERENCE_EPOCH_TS + max_ts`), and the Dockerfile now includes port 1883 in EXPOSE. The self-review was thorough on the things it checked, but missed several integration-level gaps.

---

## 2. Local Agent Review Assessment

**Grade: B+**

### What the self-review did well:
- Thorough file-by-file walkthrough with specific line references
- Correct verification of all PRD port assignments, clock drift formula, scan cycle formula
- Correctly identified the backward compatibility path (topology=None bypasses topology)
- Verified all register encoding paths (ABCD/CDAB, float32, uint32, int16_x10)
- Confirmed ground truth never receives ClockDriftModel
- Validated all evaluation formulas (precision/recall/F1/weighted/CI)
- Ran the full test suite and linters

### What the self-review missed:

1. **MQTT clock drift not wired in CLI** (RED) — The self-review verified that `MqttPublisher._publish_entry()` applies clock drift (line 508-509) and that the constructor accepts `clock_drift`, but did not check whether `cli.py:_run_realtime()` actually passes clock drift when creating the MqttPublisher. It doesn't — line 416 creates `MqttPublisher(config, engine.store)` with no `clock_drift=` argument.

2. **Connection limits not enforced** (RED) — The self-review section 7 verifies MTBF values and drop scheduler independence (correct), but fails to note that `ConnectionLimitConfig.max_connections` is stored but never enforced. The PRD 3a.5 states: "The simulator enforces these limits per endpoint. If CollatrEdge opens too many connections to a single controller, the simulator rejects the excess." The code stores `max_connections` in config but pymodbus `ModbusTcpServer` does not limit concurrent connections.

3. **Response latency not injected** (YELLOW) — The self-review notes `response_latency_ms` as a property but does not flag that it's never actually used to inject per-request delays. The value is stored from the endpoint config but no code path reads it to add latency.

4. **Collapsed mode port inconsistency in topology manager** (YELLOW) — `_collapsed_modbus()` returns port 5020/5030, not 502. DataEngine bypasses this (correct), but the topology manager API is misleading if anyone calls `modbus_endpoints()` in collapsed mode.

5. **No `create_mqtt_publishers()` method on DataEngine** (YELLOW) — Unlike `create_modbus_servers()` and `create_opcua_servers()`, MQTT publisher creation is not in DataEngine. This breaks the pattern and is why clock drift isn't wired for MQTT.

---

## 3. Independent Findings

### A. Network Topology

| # | Check | Status | Notes |
|---|-------|--------|-------|
| A1 | Port assignments match PRD 3a.4 | 🟢 GREEN | Verified in `topology.py:485-668`. Packaging: 5020/5021/5022/4840. F&B: 5030/5031/5032/5033/5034/5035/4841/4842. All correct. |
| A2 | Collapsed mode backward compatible | 🟢 GREEN | `DataEngine.create_modbus_servers()` (line 255-257) and `create_opcua_servers()` (line 299-301) bypass topology when `self._topology is None or self._topology.mode == "collapsed"`. Creates bare server with no endpoint = Phase 4 behaviour. |
| A3 | Register range enforcement (0x02) | 🟢 GREEN | `FactoryDeviceContext.getValues()` (modbus_server.py:393-401): FC03 checks `valid_hr_addresses`, FC04 checks `valid_ir_addresses`. Returns `ExcCodes.ILLEGAL_ADDRESS` (=0x02). Both words of 2-register entries are in the valid set. |
| A4 | Multi-slave UID routing | 🟢 GREEN | `ModbusServer.start()` (modbus_server.py:1157-1185): press+energy on 5020 (UIDs 1,5) both map to same context. Oven gateway on 5031 (UIDs 1,2,3,10) all map to primary context; secondary slave contexts for zones added via `devices.update(self._secondary_contexts)`. |
| A5 | CDAB byte order on mixer 5030 | 🟢 GREEN | `_foodbev_modbus()` (topology.py:552) sets `byte_order="CDAB"` on mixer endpoint. `_sync_holding_registers()` (modbus_server.py:992) checks `entry.byte_order == "CDAB"` and calls `encode_float32_cdab()`. |
| A6 | Connection limits match PRD 3a.5 | 🔴 RED | Values are correct in `_DEFAULT_CONNECTION_LIMITS` (topology.py:262-287): S7-1500=16, S7-1200=3, CompactLogix=8, Eurotherm=2, Danfoss=2, PM5560=4. **But limits are never enforced at runtime.** pymodbus `ModbusTcpServer` does not restrict concurrent TCP connections. PRD says "the simulator enforces these limits per endpoint." See R1. |
| A7 | Response latency configurable | 🟡 YELLOW | Config values stored correctly in `_DEFAULT_CONNECTION_LIMITS` and `ModbusServer._response_latency_ms`. **But never injected into request handling.** The property `response_latency_ms` is read-only and never consumed. See Y3. |

### B. Scan Cycle Quantisation

| # | Check | Status | Notes |
|---|-------|--------|-------|
| B1 | Formula correct | 🟢 GREEN | `ScanCycleModel.prepare_tick()` (topology.py:215-221): `actual_cycle = self._cycle_ms * (1.0 + self._rng.uniform(0.0, self._jitter_pct))`. Matches PRD 3a.8 exactly. |
| B2 | Stale values between boundaries | 🟢 GREEN | `get_value()` (topology.py:238-250): returns `self._last_outputs.get(signal_id, current_value)` when `scan_active=False`. Correct stale behaviour. |
| B3 | Per-controller cycle times | 🟢 GREEN | `_DEFAULT_SCAN_CYCLE` (topology.py:344-352): S7-1500=10ms/5%, S7-1200=20ms/8%, CompactLogix=15ms/6%, Eurotherm=100ms/10%, Danfoss=100ms/10%. All match PRD 3a.8. |
| B4 | Only in realistic mode | 🟢 GREEN | `DataEngine.create_modbus_servers()`: collapsed mode creates `ModbusServer` with no `scan_cycle_model` kwarg, so `self._scan_cycle_model` is None. Realistic mode creates `ScanCycleModel` per endpoint. |
| B5 | Deterministic with seeded RNG | 🟢 GREEN | Each `ScanCycleModel` gets its own RNG spawned from `self._root_ss.spawn(1)[0]` (data_engine.py:266). Rule 13 satisfied. |

### C. Clock Drift

| # | Check | Status | Notes |
|---|-------|--------|-------|
| C1 | Formula correct | 🟢 GREEN | `ClockDriftModel.drifted_time()` (topology.py:121-128): `sim_time + self._initial_offset_s + self._drift_rate_s_per_day * elapsed_hours / 24.0`. Matches PRD 3a.5 formula exactly. |
| C2 | Applied to OPC-UA SourceTimestamp | 🟢 GREEN | `OpcuaServer._sync_values()` (opcua_server.py:518-522): `drifted = self._clock_drift.drifted_time(sv.timestamp)` → `source_ts = _sim_time_to_datetime(drifted)`. Applied as `SourceTimestamp` in `ua.DataValue`. |
| C3 | Applied to MQTT JSON timestamps | 🔴 RED | The `MqttPublisher._publish_entry()` code (mqtt_publisher.py:508-509) correctly applies drift **when `self._clock_drift is not None`**. However, `cli.py:416` creates `MqttPublisher(config, engine.store)` without passing `clock_drift=...`. In realistic mode, MQTT timestamps will **never** have clock drift applied. See R2. |
| C4 | Ground truth uses true sim_time | 🟢 GREEN | `GroundTruthLogger.__init__()` (ground_truth.py:39) takes no clock drift parameter. Confirmed by construction — no `ClockDriftModel` reference anywhere in ground_truth.py. |
| C5 | Default drift rates match PRD 3a.5 | 🟢 GREEN | `_DEFAULT_CLOCK_DRIFT` (topology.py:334-341): S7-1500=200ms/0.3s, S7-1200=1500ms/1.0s, CompactLogix=500ms/0.5s, Eurotherm=5000ms/5.0s, Danfoss=3000ms/2.5s, PM5560=100ms/0.2s. All within PRD ranges (values are mid-range defaults). |

### D. Independent Connection Drops

| # | Check | Status | Notes |
|---|-------|--------|-------|
| D1 | Each endpoint has own CommDropScheduler | 🟢 GREEN | `data_engine.py:270-271`: `drop_rng = np.random.default_rng(self._root_ss.spawn(1)[0])` per endpoint. `modbus_server.py:712-716`: `endpoint is not None` → `drop_cfg = _connection_drop_to_comm_drop(endpoint.connection_drop)`. Each server gets its own scheduler instance. |
| D2 | MTBF values match PRD 3a.5 | 🟢 GREEN | `_DEFAULT_CONNECTION_DROPS` (topology.py:293-329): Eurotherm 8-24h ✓, S7-1500 72-168h ✓, Danfoss 24-48h ✓, S7-1200 48-168h ✓, CompactLogix 48-168h ✓, PM5560 72-168h ✓. |
| D3 | One drop doesn't affect others | 🟢 GREEN | Each `ModbusServer` has its own `CommDropScheduler` instance with isolated RNG. No shared state between schedulers. Verified in test `test_independent_comm_drops.py`. |
| D4 | Collapsed mode unchanged | 🟢 GREEN | When `endpoint is None`, `drop_cfg = config.data_quality.modbus_drop` (modbus_server.py:714-715) — uses global config exactly as Phase 4. |

### E. Evaluation Framework

| # | Check | Status | Notes |
|---|-------|--------|-------|
| E1 | Event matching with tolerance windows | 🟢 GREEN | `match_events()` (evaluator.py:129-133): `windows.append((ev.start_time - pre_margin, ev.end_time + post_margin, i))`. Default pre=30s, post=60s per PRD 12.4. |
| E2 | Overlapping windows: nearest start | 🟢 GREEN | evaluator.py:141-148: `dist = abs(t - events[ev_idx].start_time)`, `candidates.sort()`, first element selected. Ties broken by absolute distance to start. |
| E3 | Precision/recall/F1 correct | 🟢 GREEN | evaluator.py:205-211: `precision = tp / (tp + fp)`, `recall = tp / (tp + fn)`, `f1 = 2 * precision * recall / (precision + recall)`. Standard formulas with zero-division guards. |
| E4 | Severity-weighted recall per PRD 12.4 | 🟢 GREEN | evaluator.py:214-225: `weighted_recall = detected_weight / total_weight`. Uses `DEFAULT_SEVERITY_WEIGHTS` (metrics.py:14-29) which covers all 15 scenario types per PRD 12.4 table. `weighted_f1 = 2 * precision * weighted_recall / (precision + weighted_recall)`. |
| E5 | Detection latency median and p90 | 🟢 GREEN | evaluator.py:228-230: Uses `_percentile()` helper with linear interpolation. Latency = `first_detection_time - event.start_time` (negative = early detection, reported as-is per PRD 12.4). |
| E6 | Random baseline computation | 🟢 GREEN | evaluator.py:280-335: Anomaly density = total_event_time / total_duration. Seeded RNG for reproducibility. Fires at anomaly_density probability per tick. Runs through `match_events()` for consistent scoring. |
| E7 | Per-scenario breakdown | 🟢 GREEN | `_per_scenario_metrics()` (evaluator.py:247-266): Groups by `event_type`, computes recall and collects latencies per type. |
| E8 | Multi-seed CI formula | 🟢 GREEN | `_ci()` (cli.py:417-428): `variance = sum((x - mean) ** 2 for x in values) / (n - 1)` (sample std), `margin = 1.96 * std / math.sqrt(n)`. Matches PRD 12.4: `CI = mean ± 1.96 * std / sqrt(N)`. |

### F. Batch Output

| # | Check | Status | Notes |
|---|-------|--------|-------|
| F1 | CSV columns correct | 🟢 GREEN | `CsvWriter._CSV_COLUMNS = ("timestamp", "signal_id", "value", "quality")` (writer.py:82). Written as header row in `__init__`. |
| F2 | Parquet columnar layout | 🟢 GREEN | `ParquetWriter.write_tick()` builds one row per tick, one column per signal. Wide format as specified. |
| F3 | Event-driven only on change | 🟢 GREEN | CSV: `if last is not None and sv.value == last: continue` (writer.py:118-119). Parquet: `_changed` boolean column set to `changed = last is None or sv.value != last` (writer.py:223-224). |
| F4 | NaN/Inf handling | 🟢 GREEN | CSV: `math.isnan(sv.value) or math.isinf(sv.value)` → skip row (writer.py:112-114). Parquet: stored as `None` to preserve alignment (writer.py:215-217). |
| F5 | Buffer flush at configured size | 🟢 GREEN | Both writers check `len(self._buffer) >= self._flush_size` after buffering. Default `buffer_size=10000` in `BatchOutputConfig`. |
| F6 | Wired after data quality injection | 🟢 GREEN | `DataEngine.tick()` (data_engine.py:409-410): `self._batch_writer.write_tick(sim_time, self._store)` is called after `self._data_quality.tick(...)` and `self._scenario_engine.post_gen_tick(...)`. |

### G. CLI

| # | Check | Status | Notes |
|---|-------|--------|-------|
| G1 | All required flags | 🟢 GREEN | `_add_run_subcommand()` (cli.py:156-209): `--config/-c`, `--profile`, `--seed`, `--time-scale`, `--batch-output`, `--batch-duration`, `--batch-format`, `--network-mode`, `--log-level`. All present. |
| G2 | Subcommands: run/evaluate/version | 🟢 GREEN | `build_parser()` (cli.py:134-141): calls `_add_run_subcommand`, `_add_evaluate_subcommand`, `_add_version_subcommand`. |
| G3 | `python -m factory_simulator` works | 🟢 GREEN | `__main__.py`: `from factory_simulator.cli import main; sys.exit(main())`. |
| G4 | Entry point in pyproject.toml | 🟢 GREEN | `[project.scripts] factory-simulator = "factory_simulator.cli:main"` |

### H. Docker & Health

| # | Check | Status | Notes |
|---|-------|--------|-------|
| H1 | Dockerfile python:3.12-slim | 🟢 GREEN | `FROM python:3.12-slim` (Dockerfile line 9). |
| H2 | Health check on 8080 | 🟢 GREEN | `HealthServer(port=8080, ...)` in cli.py:401. `_build_health_payload()` returns JSON with all 7 required keys. HEALTHCHECK in Dockerfile uses `curl -f http://localhost:8080/health`. |
| H3 | Docker Compose with Mosquitto | 🟢 GREEN | `docker-compose.yml`: `mqtt-broker` service using `eclipse-mosquitto:2`, healthcheck with `mosquitto_sub`, `depends_on: service_healthy`. |
| H4 | Realistic mode override | 🟢 GREEN | `docker-compose.realistic.yaml`: adds ports 5020-5035, 4841, 4842; sets `SIM_NETWORK_MODE=realistic`. |

### I. Cross-Cutting

| # | Check | Status | Notes |
|---|-------|--------|-------|
| I1 | Rule 6: no wall-clock in signal gen | 🟢 GREEN | All signal generation uses `sim_time` from the clock. Wall-clock (`time.monotonic()`) is only used in protocol server scheduling loops (`_update_loop`, `_publish_loop`) and drop scheduling — not in value generation. |
| I2 | Rule 13: SeedSequence spawning | 🟢 GREEN | `DataEngine.__init__`: `self._root_ss = np.random.SeedSequence(seed)`. Each generator, scenario engine, data quality injector, scan cycle model, and drop scheduler gets `self._root_ss.spawn(1)[0]`. Confirmed in all `create_*` methods. |
| I3 | Rule 14: explicit test fixtures | 🟢 GREEN | Test files use explicit config creation, mock stores, and injected RNGs. No implicit global state. |
| I4 | No Phase 0-4 regressions | 🟢 GREEN | 2963 total tests pass per self-review. Config changes (new fields) all have defaults that preserve existing behaviour (`network: None`, `batch_output.format: "none"`, new endpoint params optional). |

---

## 4. Issues Table

| ID | Severity | File | Description | Recommended Fix |
|----|----------|------|-------------|-----------------|
| R1 | 🔴 RED | `modbus_server.py`, `opcua_server.py` | **Connection limits not enforced.** PRD 3a.5: "The simulator enforces these limits per endpoint. If CollatrEdge opens too many connections to a single controller, the simulator rejects the excess." `ConnectionLimitConfig.max_connections` is stored but never enforced. pymodbus `ModbusTcpServer` and asyncua `Server` accept unlimited connections. | Implement a custom `ModbusTcpServer` subclass or connection handler that tracks connected clients per port and rejects new connections above `max_connections`. For asyncua, use `Server.set_policies()` or `set_security_policy()` with a connection wrapper. Alternatively, document this as a known limitation and file as a follow-up task. |
| R2 | 🔴 RED | `cli.py:416` | **MQTT clock drift not wired in realistic mode.** `_run_realtime()` creates `MqttPublisher(config, engine.store)` without passing `clock_drift=...`. The MqttPublisher constructor accepts and uses clock_drift (mqtt_publisher.py:417,451,508-509), but it's always None in production. MQTT timestamps will never show controller drift. | Add a `create_mqtt_publishers()` method to `DataEngine` (parallel to `create_modbus_servers()` and `create_opcua_servers()`). Wire clock drift from the topology's MQTT-adjacent controller config. Or, in `_run_realtime()`, construct a `ClockDriftModel` from the topology and pass it to `MqttPublisher`. |
| Y1 | 🟡 YELLOW | `modbus_server.py:739-742` | **Response latency stored but never injected.** `self._response_latency_ms` is set from `endpoint.connection_limit.response_timeout_ms_typical` but never used to add delay to read responses. PRD 3a.5 specifies per-controller response times. | Add `await asyncio.sleep(self._response_latency_ms / 1000.0)` in the request handler or update loop. Alternatively, document as deferred. |
| Y2 | 🟡 YELLOW | `topology.py:442-450` | **Collapsed mode ports inconsistent.** `_collapsed_modbus()` returns port 5020 (packaging) or 5030 (F&B), not 502. DataEngine bypasses this (correct), but the topology API is misleading. Anyone calling `topology.modbus_endpoints()` in collapsed mode gets non-standard ports. | Change `_collapsed_modbus()` to return port 502 (matching config default), or document that collapsed mode endpoints use different ports than config-default collapsed-mode server. |
| Y3 | 🟡 YELLOW | `data_engine.py` | **No `create_mqtt_publishers()` method.** DataEngine has `create_modbus_servers()` and `create_opcua_servers()` but not `create_mqtt_publishers()`. This asymmetry means MQTT server creation in `cli.py` cannot benefit from topology wiring (clock drift, per-controller drops). | Add `create_mqtt_publishers()` that returns `list[MqttPublisher]` with topology-derived clock drift, parallel to the existing methods. |
| Y4 | 🟡 YELLOW | `evaluator.py:280-335` | **Random baseline anomaly density uses event time, not tick time.** `total_anomaly_time = sum(ev.end_time - ev.start_time)` computes continuous time, while the random detector fires per-tick. When events are dense or overlapping, this can overcount anomaly time. Minor in practice since overlap is rare. | Account for event time overlap (merge overlapping intervals before summing). Low priority — current behaviour produces a slightly pessimistic baseline. |
| Y5 | 🟡 YELLOW | `health/server.py` (already fixed) | **Health server sim_time epoch — resolved.** The self-review identified this as a yellow issue, but the actual code (line 136-138) uses `_REFERENCE_EPOCH_TS + max_ts` (added at line 50-51). **This issue was already fixed before the self-review was written.** The self-review text (Y1) describes the bug as still present, but it references a fix that is already applied. This is a review accuracy issue, not a code issue. | No code fix needed. Self-review text is stale/inaccurate on this point. |

---

## 5. Comparison: Issues Missed by Local Agent

| Issue | Found by Local | Found by Independent | Impact |
|-------|---------------|---------------------|--------|
| R1: Connection limits not enforced | ❌ Noted in progress file as "deferred" but not flagged in review | ✅ | HIGH — PRD explicitly says "enforces" |
| R2: MQTT clock drift not wired | ❌ Missed entirely | ✅ | HIGH — Clock drift is a key Phase 5 feature |
| Y1: Response latency not injected | ❌ Noted in progress as "deferred" but not flagged | ✅ | MEDIUM — per-controller realism gap |
| Y2: Collapsed mode port inconsistency | ❌ | ✅ | LOW — dead code path in DataEngine |
| Y3: Missing create_mqtt_publishers() | ❌ | ✅ | MEDIUM — root cause of R2 |
| Y4: Random baseline overlap counting | ❌ | ✅ | LOW — minor statistical inaccuracy |
| Y5: Health sim_time already fixed | Self-review claims it's still broken | ✅ (code is correct) | N/A — self-review accuracy issue |

The local agent's progress file (plans/phase-5-progress.md) does note under Task 5.2 that "Response latency: config value stored from endpoint but actual per-request delay injection deferred" and "Connection limit enforcement: config stored on endpoint, actual TCP limiting deferred." However, these deferral decisions were not surfaced in the final review as issues or limitations. The self-review grades everything as GREEN/PASS when significant PRD requirements are acknowledged as deferred. A proper review should flag these as gaps.

---

## 6. Verdict

### CONDITIONAL GO

**Conditions for GO:**

1. **R2 must be fixed before release:** Wire MQTT clock drift in `cli.py:_run_realtime()`. This is a straightforward ~10-line fix — create a `ClockDriftModel` from the topology's MQTT endpoint config and pass it to `MqttPublisher`. Without this fix, a key Phase 5 feature (per-controller clock drift) is partially broken.

2. **R1 must be documented or fixed:** Either:
   - (a) Implement connection limit enforcement (requires custom server class or connection wrapper), OR
   - (b) Document it as a known limitation in the README and file a follow-up issue. The PRD says "enforces" but pymodbus/asyncua don't support this natively, so a pragmatic approach is to document the gap.

**Nice-to-have (not blocking GO):**

- Y1: Response latency injection — document as deferred
- Y2: Fix collapsed mode port in topology manager
- Y3: Add `create_mqtt_publishers()` to DataEngine (resolves R2 more elegantly)
- Y4: Fix overlapping event counting in random baseline

### Quality Assessment

The implementation quality is **high**. The architecture is clean, the code is well-documented, test coverage is comprehensive, and the core simulation correctness is solid. The gaps identified are integration-level wiring issues (MQTT clock drift) and deferred enforcement features (connection limits), not fundamental design flaws. The local agent did excellent work across all 13 tasks.

### Test Confidence

- 2963 tests pass (100% non-integration, 100% acceptance)
- ruff and mypy clean
- Performance benchmarks pass
- The test suite covers both collapsed and realistic modes comprehensively
- Evaluation framework has 58 unit tests covering all metric formulas

### Recommendation

Fix R2 (MQTT clock drift wiring — ~30 minutes), document R1 (connection limits as known limitation), and the phase is release-ready.
