# Consolidated Code Review — Action Plan

**Date:** 2026-03-04
**Reviews:**
- Protocol Fidelity (14 issues: 2 RED, 5 YELLOW, 7 GREEN)
- Signal Integrity (14 issues: 0 RED, 6 YELLOW, 8 GREEN)
- Architecture (26 issues: 4 RED, 16 YELLOW, 6 GREEN)

**Totals:** 54 issues found: 6 RED, 27 YELLOW, 21 GREEN

---

## RED Issues (6) — Must Fix

| # | Source | Issue | File(s) | Fix |
|---|--------|-------|---------|-----|
| R1 | Arch | **Ground truth logger never instantiated in CLI** — no scenario events recorded in any mode | `cli.py:456` | Create `GroundTruthLogger` in `_async_run()` and pass to `DataEngine` |
| R2 | Arch | **Ground truth header omits Phase 4 + F&B scenarios** — only 11 packaging scenarios listed | `ground_truth.py:62-105` | Add all 10 missing scenario types to `write_header()` |
| R3 | Arch | **No `.dockerignore`** — `.git/`, tests, `__pycache__` included in Docker builds | Dockerfile context | Create `.dockerignore` |
| R4 | Arch | **Container runs as root** — no `USER` directive | `Dockerfile` | Add non-root user |
| R5 | Proto | **Missing OPC-UA `EngineeringUnits` property** — PRD requires it, no unit metadata available | `opcua_server.py:263-271` | Add `EUInformation` property per signal |
| R6 | Proto | **Oven gateway UID routing mismatch** — realistic mode UIDs 1,2,3 can't reach per-zone IRs (mapped to UIDs 11-13) | `topology.py:578-579` | Remap secondary slaves to UIDs 1/2/3 in realistic mode |

## YELLOW Issues (27) — Should Fix

### High Priority (data correctness / functionality)

| # | Source | Issue | File(s) |
|---|--------|-------|---------|
| Y1 | Signal | **Severity weight dict keys don't match ground truth names** — `"web_break"` vs `"WebBreak"`, weighted metrics always fall back to default 1.0 | `metrics.py:13-29`, `evaluator.py:201` |
| Y2 | Signal | **Potential double-logging of GT events** — ScenarioEngine AND individual scenarios both log start/end | `bearing_wear.py:126`, `scenario_engine.py:115-137` |
| Y3 | Signal | **Evaluator drops open scenarios** — bearing wear near sim end excluded from evaluation | `evaluator.py:142-149` |
| Y4 | Arch | **MQTT publisher no reconnection logic** — initial `connect()` crash if broker slow; no mid-run recovery monitoring | `mqtt_publisher.py:595` |
| Y5 | Arch | **CsvWriter.close() not idempotent** — double-close raises ValueError | `output/writer.py:143` |
| Y6 | Arch | **No SIGTERM handler** — Docker stop may not clean up protocol servers | `cli.py` |
| Y7 | Proto | **0x06 (Device Busy) only fires on `press.machine_state`** — F&B endpoints never trigger it | `modbus_server.py:887-896` |
| Y8 | Arch | **EvaluationConfig defined but never wired into FactoryConfig** | `config.py:1160-1207` |

### Medium Priority (validation / robustness)

| # | Source | Issue | File(s) |
|---|--------|-------|---------|
| Y9 | Arch | **SignalConfig missing `min_clamp <= max_clamp` validator** | `config.py:219-243` |
| Y10 | Arch | **ClockDriftConfig rejects negative offsets** — prevents valid "clock behind" scenario | `config.py:1303-1307` |
| Y11 | Signal | **Calibration drift rate units mismatch** — docstring says "per second", PRD says "per hour" | `steady_state.py:46` |
| Y12 | Signal | **Random walk docstring claims sqrt(dt) scaling** but code uses linear dt | `random_walk.py:61` |
| Y13 | Signal | **Dryer/oven zone Cholesky correlation not implemented** — PRD specifies matrices but only vibration uses Cholesky | Generator layer |
| Y14 | Proto | **Coil 4 (laminator.running) derived from press state** not laminator speed | `modbus_server.py:539` |
| Y15 | Proto | **Missing OPC-UA `MinimumSamplingInterval`** — defaults to 0 | `opcua_server.py:263-297` |

### Lower Priority (polish / maintenance)

| # | Source | Issue | File(s) |
|---|--------|-------|---------|
| Y16 | Arch | **Health server port 8080 hardcoded** — not configurable | `cli.py:391` |
| Y17 | Arch | **`_format_time()` creates datetime per call** — perf issue at scale | `ground_truth.py:408-420` |
| Y18 | Arch | **`_REFERENCE_EPOCH_TS` duplicated in 3 files** | `mqtt_publisher.py:52`, `opcua_server.py:53`, `health/server.py:42` |
| Y19 | Arch | **5 generator modules lack dedicated tests** (coder, energy, laminator, slitter, vibration) | `tests/` |
| Y20 | Arch | **Server tasks not verified after startup** — async `create_task` failures are delayed | `cli.py:430-440` |
| Y21 | Arch | **CI only tests Python 3.12** — 8 integration test files never run in CI | `ci.yml` |
| Y22 | Arch | **MqttProtocolConfig.sparkplug_b defined but never implemented** | `config.py:163` |
| Y23 | Arch | **MqttProtocolConfig.retain global flag overridden by per-topic logic** | `config.py:160` |
| Y24 | Arch | **Editable pip install in Dockerfile** — should be regular install | `Dockerfile:25` |
| Y25 | Proto | **No `AccessLevel=0` for inactive profile nodes** — OPC-UA | `opcua_server.py` |
| Y26 | Proto | **LWT topic not profile-specific** — both profiles share same topic | Config |
| Y27 | Arch | **`contextlib.suppress(Exception)` too broad** during server shutdown | `cli.py:435` |

## GREEN Issues (21) — Noted, defer

All GREEN items are documented in the individual review files. Summary: naming clarity (4), documentation suggestions (6), defensive code observations (5), feature gaps matching PRD design choices (4), timing observations (2).

---

## Recommended Fix Batches

### Batch 1: Critical (R1-R6) — Must do
Ground truth logger, header fix, Dockerfile hardening, OPC-UA metadata, oven UID routing.
**Estimated effort:** 1 session

### Batch 2: Data Correctness (Y1-Y3) — High value
Severity weight key normalisation, double-logging investigation, open scenario handling.
**Estimated effort:** 1 session

### Batch 3: Robustness (Y4-Y8) — Resilience
MQTT reconnection, CsvWriter idempotency, SIGTERM handler, 0x06 profile-aware, wire EvaluationConfig.
**Estimated effort:** 1 session

### Batch 4: Validation & Protocol Polish (Y9-Y15) — Quality
Config validators, docstring fixes, Cholesky zones, Coil 4, MinimumSamplingInterval.
**Estimated effort:** 1 session

### Batch 5: Maintenance & CI (Y16-Y27) — Nice to have
Shared constants, test gaps, CI improvements, dead config fields, Dockerfile polish.
**Estimated effort:** 1-2 sessions

---

## Review Files
- `plans/review-protocol-fidelity.md` — full protocol review
- `plans/review-signal-integrity.md` — full maths/signal review
- `plans/review-architecture.md` — full architecture review
