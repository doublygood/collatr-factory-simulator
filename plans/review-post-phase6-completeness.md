# Phase 6 Completeness Audit — Final Report

**Date:** 2026-03-05 · **Reviewer:** Independent sub-agent (Opus)

## Overall Completeness Score: **33/33 issues fully VERIFIED**

All 6 RED issues (R1–R6) and all 27 YELLOW issues (Y1–Y27) have been properly addressed in the source code. Every fix is:

1. **Present** in the correct source file
2. **Correct** in implementation approach
3. **Complete** — no partial or missing components
4. **Non-regressive** — no evidence of new issues introduced

---

## Verification Table

| Issue ID | Original Problem | Fix Status | Evidence (file:line) | Notes |
|----------|-----------------|------------|---------------------|-------|
| **R1** | Ground truth logger never instantiated in CLI | **VERIFIED** | `cli.py:522,564-574,591,604` | `GroundTruthLogger` created in `_async_run()`, opened, header written, passed to `DataEngine`, closed in `finally` |
| **R2** | Ground truth header omits Phase 4 + F&B scenarios | **VERIFIED** | `ground_truth.py:112-133` | All 10 missing scenarios added with proper `is not None and .enabled` guards |
| **R3** | No `.dockerignore` | **VERIFIED** | `.dockerignore` (root) | Excludes `.git`, tests, `__pycache__`, plans, prd, `*.md` (except README.md) |
| **R4** | Container runs as root | **VERIFIED** | `Dockerfile:31,35` | `useradd -m -r simulator` + `USER simulator` directive before ENTRYPOINT |
| **R5** | Missing OPC-UA `EngineeringUnits` property | **VERIFIED** | `opcua_server.py:363-375` | `ua.EUInformation` with UNECE namespace URI, `UnitId=-1`, DisplayName from signal units |
| **R6** | Oven gateway UID routing mismatch | **VERIFIED** | `topology.py:54,607`, `modbus_server.py:1182-1197` | `secondary_uid_remap={11:1, 12:2, 13:3}` on oven endpoint; realistic-mode routing applies remap |
| **Y1** | Severity weight keys don't match ground truth names | **VERIFIED** | `evaluator.py:112,385-390` | `_pascal_to_snake()` normalises PascalCase GT event types to snake_case weight keys |
| **Y2** | Double-logging of GT events | **VERIFIED** | `scenarios/*.py` (no log_scenario_start/end found) | Duplicate calls removed from all 7 scenarios; only `engine/scenario_engine.py:222,259,273` logs start/end |
| **Y3** | Evaluator drops open scenarios | **VERIFIED** | `evaluator.py:56,241-242,281-297` | Open scenarios emitted with `end_time=last_t`, `open=True` field on `GroundTruthEvent` |
| **Y4** | MQTT publisher no reconnection logic | **VERIFIED** | `mqtt_publisher.py:421-422,585-623,643-680` | `_on_connect`/`_on_disconnect` callbacks; startup retry up to 3 times with exponential backoff |
| **Y5** | CsvWriter.close() not idempotent | **VERIFIED** | `writer.py:121-122,153,216,229-230,289-291` | Both `CsvWriter` and `ParquetWriter` guard against double-close; write-after-close raises `RuntimeError` |
| **Y6** | No SIGTERM handler | **VERIFIED** | `cli.py:383-398,446-461,620` | `loop.add_signal_handler(signal.SIGTERM, ...)` in both `_run_batch` and `_run_realtime`; `CancelledError` → exit 0 |
| **Y7** | 0x06 Device Busy only fires on press | **VERIFIED** | `modbus_server.py:695,740,898-907`, `topology.py:57,507-676` | `state_signal_id` parameter per endpoint; F&B endpoints get mixer/oven/filler/chiller/cip state signals |
| **Y8** | EvaluationConfig never wired into FactoryConfig | **VERIFIED** | `config.py:1410`, `cli.py` (evaluate), `evaluation/cli.py` | `evaluation: EvaluationConfig` field on `FactoryConfig`; evaluate command loads and uses it |
| **Y9** | SignalConfig missing min_clamp ≤ max_clamp validator | **VERIFIED** | `config.py:310-317` | `@model_validator(mode="after")` `_clamp_order` raises `ValueError` when `min_clamp > max_clamp` |
| **Y10** | ClockDriftConfig rejects negative offsets | **VERIFIED** | `config.py:1295-1296` | Old `_offset_non_negative`/`_drift_non_negative` replaced with `_must_be_finite` (rejects NaN/Inf only) |
| **Y11** | Calibration drift rate units mismatch (docstring) | **VERIFIED** | `steady_state.py:55` | Docstring clarified: "per simulated second" internally; PRD says per hour, callers divide by 3600 |
| **Y12** | Random walk docstring claims sqrt(dt) but code uses linear dt | **VERIFIED** | `random_walk.py:42-44` | Docstring corrected to describe `drift_rate * N(0,1) * dt` — linear dt scaling |
| **Y13** | Dryer/oven zone Cholesky correlation not implemented | **VERIFIED** | `press.py:26,168,203,533-535`, `oven.py:44,159,183,402-404` | Both use `CholeskyCorrelator` with PRD-specified matrices; noise extracted from lag models, applied externally |
| **Y14** | Coil 4 derived from press state not laminator speed | **VERIFIED** | `modbus_server.py:539` | `CoilDefinition(4, "laminator.web_speed", mode="gt_zero")` — derives from laminator's own speed |
| **Y15** | Missing OPC-UA MinimumSamplingInterval | **VERIFIED** | `opcua_server.py:377-384,515-522` | `MinimumSamplingInterval` attribute written on all nodes; uses `sample_rate_ms` or falls back to `tick_interval_ms` |
| **Y16** | Health server port hardcoded | **VERIFIED** | `config.py:44,68-72,1436`, `cli.py:466` | `health_port: int = 8080` on `SimulationConfig` with 0-65535 validator; `SIM_HEALTH_PORT` env override; CLI uses it |
| **Y17** | `_format_time()` creates datetime per call | **VERIFIED** | `ground_truth.py:20`, `time_utils.py:14-15` | `_format_time` delegates to `sim_time_to_iso()` using module-level `REFERENCE_EPOCH_TS` constant |
| **Y18** | `_REFERENCE_EPOCH_TS` duplicated in 3 files | **VERIFIED** | `time_utils.py` (canonical), 4 importers | All 3 duplicates removed; `mqtt_publisher.py`, `opcua_server.py`, `health/server.py`, `ground_truth.py` import from `time_utils` |
| **Y19** | 5 generator modules lack dedicated tests | **VERIFIED** | `tests/unit/test_generators/test_{coder,energy,laminator,slitter,vibration}.py` | All 5 test files created: 20+14+16+14+15 = 79 new tests |
| **Y20** | Server tasks not verified after startup | **VERIFIED** | `cli.py:416-427,468,473,480,487` | `_start_server()` helper awaits settle_time, checks `task.done()`, raises `RuntimeError` on early failure |
| **Y21** | CI only tests Python 3.12, integration tests not run | **VERIFIED** | `.github/workflows/ci.yml:48,63-83` | Matrix includes `3.13`; dedicated `integration-tests` job runs `tests/integration/` |
| **Y22** | sparkplug_b defined but never implemented | **VERIFIED** | `config.py:165-182` (MqttProtocolConfig) | Field removed; no `sparkplug_b` found in config class or YAML files |
| **Y23** | retain global flag overridden by per-topic logic | **VERIFIED** | `config.py:165-182` (MqttProtocolConfig) | Field removed; no global `retain` found in config class or YAML files |
| **Y24** | Editable pip install in Dockerfile | **VERIFIED** | `Dockerfile:28` | `pip install --no-cache-dir .` (regular install, no `-e` flag) |
| **Y25** | No AccessLevel=0 for inactive profile OPC-UA nodes | **VERIFIED** | `opcua_server.py:144,406-407,415-485`, `cli.py:579-584`, `data_engine.py:123,317` | `_build_inactive_nodes` creates nodes with AccessLevel=0 and BadNotReadable; wired from CLI through DataEngine |
| **Y26** | LWT topic not profile-specific | **VERIFIED** | `mqtt_publisher.py:145-155,473`, `config.py:180` | `resolve_lwt_topic()` auto-generates `{topic_prefix}/{line_id}/status`; `lwt_topic` defaults to `""` |
| **Y27** | contextlib.suppress(Exception) too broad | **VERIFIED** | `cli.py:507` | Narrowed to `suppress(asyncio.CancelledError, OSError, ConnectionError)` |

---

## Key Highlights

- The test suite grew from ~2996 to 3166 tests across phases 6a–6e
- All fixes follow consistent patterns (validators, guards, delegates, tests)
- Cross-cutting concerns properly wired (inactive_config flows CLI→DataEngine→OpcuaServer, time_utils centralises epoch)
- Dead config fields fully removed (no orphaned references)
- Dockerfile hardened on both security (non-root) and build efficiency (.dockerignore, non-editable install)
