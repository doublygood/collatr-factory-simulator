Read CLAUDE.md for project rules and conventions.

You are implementing Phase 3 (F&B Chilled Ready Meal Profile) of the Collatr Factory Simulator.

## CONTEXT

Phases 0-2.1 are complete. The packaging profile is fully operational:
- 47 signals across 7 equipment generators (press, laminator, slitter, coder, environment, energy, vibration)
- All 3 protocols: Modbus TCP, OPC-UA, MQTT
- 10 packaging scenarios with auto-scheduling
- Ground truth JSONL event logging
- 1490+ tests passing

Phase 3 adds the **F&B (Food & Beverage) chilled ready meal profile**: 68 signals, 6 new equipment generators, 3 shared generators adapted for F&B context, F&B-specific Modbus/OPC-UA/MQTT endpoints, and 7+ F&B scenarios.

The full plan is in `plans/phase-3-fnb-profile.md`. Read it.

## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read `plans/phase-3-fnb-profile.md` for the full plan
2. Read `plans/phase-3-tasks.json` to find the **first** task with `"passes": false`
3. Read the relevant source files and PRD sections referenced in that task
4. Implement ONLY that single task
5. Run tests: `ruff check src tests && mypy src && pytest` — ALL must pass
6. Update `plans/phase-3-tasks.json`: set `"passes": true` for your completed task
7. Update `plans/phase-3-progress.md` with what you built and any decisions
8. Commit: `phase-3: <what> (task 3.X)`
9. Do NOT push. Pushing is handled externally.
10. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## PHASE-SPECIFIC NOTES

### F&B Config (Task 3.2)

The F&B config file `config/factory-foodbev.yaml` defines all 68 signals. Cross-reference every signal against:
- `prd/appendix-a-modbus-register-map.md` — F&B register addresses, byte orders
- `prd/appendix-b-opcua-node-tree.md` — FoodBevLine node paths
- `prd/appendix-c-mqtt-topic-map.md` — F&B MQTT topics (13 total: 11 coder + 2 env)
- `prd/02b-factory-layout-food-and-beverage.md` — signal tables for each equipment group

The MQTT `line_id` must be `"foodbev1"`. The OPC-UA root is `FoodBevLine` (not `PackagingLine`).

### CDAB Byte Order (Tasks 3.4, 3.12)

The mixer uses an Allen-Bradley CompactLogix PLC with CDAB byte order. Registers HR 1000-1011 are CDAB. All other F&B registers are ABCD (Eurotherm/Siemens).

CDAB encoding: the two 16-bit words of a float32 are swapped:
```
ABCD: register[0] = high word, register[1] = low word
CDAB: register[0] = low word,  register[1] = high word
```

In the signal config YAML, set `modbus_byte_order: "CDAB"` on mixer signals. In `modbus_server.py`, read this field from config and use the appropriate encoder.

### Multi-Slave Modbus (Tasks 3.5, 3.13)

PRD Section 3.1.6: Oven zone controllers are Eurotherm units at UIDs 11, 12, 13. Each has:
- IR 0 = zone PV (int16 x10)
- IR 1 = zone SP (int16 x10)
- IR 2 = output power (int16 x10, 0-1000 = 0.0-100.0%)

The oven output power signals (`oven.zone_1/2/3_output_power`) are ONLY served via multi-slave IR (not on the main UID 1 HR block).

### Thermal Diffusion Model (Task 3.3)

PRD Section 4.2.10. Truncated Fourier series for 1D heat conduction in a slab:
```
T(t) = T_oven - (T_oven - T_initial) * SUM[C_n * exp(-(2n+1)^2 * pi^2 * alpha * t / L^2)]
```
Where C_n = 8 / ((2n+1)^2 * pi^2). Sum terms until |T(0) - T_initial| < 1.0°C.

Typical ready meal: half-thickness 25mm (L=0.025m), alpha=1.4e-7 m²/s, core reaches 72°C from 4°C in ~15-20 min at 180°C oven.

### Per-Item Filler Signals (Task 3.6)

`filler.fill_weight` generates ONE value per simulated item arrival, not every tick. Item interval = 60.0 / line_speed seconds. Between items, hold the last fill_weight. Similarly for `filler.fill_deviation`, `qc.actual_weight`.

### Shared Generator Coupling (Task 3.11)

The coder currently reads `press.machine_state` and `press.line_speed`. For F&B, it must read from configurable signals:

```yaml
coder:
  type: "cij_printer"
  coupling_state_signal: "filler.state"
  coupling_speed_signal: "filler.line_speed"
  coupling_running_state: 2
```

Use `config.model_extra` to read these (EquipmentConfig has `extra="allow"`). Default to packaging behaviour when not set.

### CIP Phase Sequence (Task 3.10, 3.20)

CIP runs through 5 active phases, each with defined temperature and conductivity profiles:

| Phase | Duration | Wash Temp | Conductivity |
|-------|----------|-----------|-------------|
| Pre-rinse | 5 min | 40-50°C | ~0 mS/cm |
| Caustic wash | 15-20 min | 70-80°C | 80-150 mS/cm |
| Intermediate rinse | 5 min | 40-50°C | decaying to ~5 mS/cm |
| Acid wash | 10-15 min | 60-70°C | moderate |
| Final rinse | 5-10 min | 40-50°C | must drop below 5 mS/cm |

The CIP generator is normally in Idle. The CIP scenario triggers transitions through the phase sequence.

### F&B Scenario Config-to-Param Mapping (Task 3.22)

When adding auto-scheduling methods for F&B scenarios, map config fields to scenario constructor params:

| Config Class | Config Field | Scenario Param |
|---|---|---|
| `BatchCycleConfig` | `batch_duration_seconds` | `batch_duration_range` |
| `BatchCycleConfig` | `frequency_per_shift` | (used for scheduling count) |
| `OvenThermalExcursionConfig` | `duration_seconds` | `drift_duration_range` |
| `OvenThermalExcursionConfig` | `max_drift_c` | `drift_range` |
| `FillWeightDriftConfig` | `duration_seconds` | `duration_range` |
| `FillWeightDriftConfig` | `drift_rate` | `drift_rate_range` |
| `SealIntegrityFailureConfig` | `duration_seconds` | `duration_range` |
| `ChillerDoorAlarmConfig` | `duration_seconds` | `duration_range` |
| `CipCycleConfig` | `cycle_duration_seconds` | `cycle_duration_range` |
| `ColdChainBreakConfig` | `duration_seconds` | `duration_range` |

Follow the existing `_schedule_unplanned_stops()` pattern exactly. Use `if cfg is None or not cfg.enabled: return` guard for optional F&B configs.

### Generator Patterns

Every F&B generator follows the existing `EquipmentGenerator` base class pattern:
- Constructor takes `(equipment_id, config, rng)`
- `get_signal_ids()` returns fully-qualified signal IDs
- `generate(sim_time, dt, store)` returns list of SignalValue
- `get_protocol_mappings()` returns per-signal protocol mappings

Reference generators:
- **Complex state machine**: `generators/press.py` (21 signals, state cascade)
- **Shared generator**: `generators/coder.py` (follows press state)
- **Simple correlated**: `generators/energy.py` (follows press speed)

### Scenario Patterns

Every F&B scenario follows the existing `Scenario` base class:
- Constructor takes `(start_time, rng, params)`
- Lifecycle hooks: `_on_activate()`, `_on_tick()`, `_on_complete()`
- Find generator: `for gen in engine.generators: if isinstance(gen, XyzGenerator)`
- Save/restore model state for cleanup
- Log ground truth events

Reference scenarios:
- **Temperature drift**: `scenarios/dryer_drift.py` (reference for oven thermal excursion)
- **State-changing**: `scenarios/job_changeover.py` (multi-phase with internal state)

### Known Pre-existing Limitation

**Y3 (DataEngine sim_duration_s)**: The DataEngine doesn't pass `sim_duration_s` to ScenarioEngine — defaults to 8 hours (one shift). This means scenarios beyond 8h simulated won't auto-schedule. This is pre-existing from Phase 1 and is NOT a Phase 3 task. Note it in progress file if encountered.

## STOPPING RULES

**After completing ONE task:** Output `TASK_COMPLETE` and stop immediately.
Do not look for the next task. Do not start another task.
The ralph.sh loop will call you again for the next iteration.

**If a test cannot pass after 3 genuine attempts:** STOP. Document the issue in `plans/phase-3-progress.md`. Output `TASK_BLOCKED: <reason>` and stop.

## COMPLETION

When ALL tasks in the task JSON have `"passes": true`:
1. Do NOT output PHASE_COMPLETE yet.
2. Spawn a sub-agent code review.
3. Write the review to `plans/phase-3-review.md`
4. Review checks:
   - All 68 F&B signals exist in the store when F&B config is loaded
   - All signal IDs in `_AFFECTED_SIGNALS` match actual store keys
   - CDAB encoding round-trips correctly for mixer registers
   - Multi-slave UIDs 11-13 return correct oven zone data
   - Per-item filler signal generation works at various line speeds
   - Shared generators (coder, energy) work with both packaging AND F&B configs
   - All F&B scenarios auto-schedule
   - No packaging test regressions
5. Address all RED Must Fix findings. Re-run `ruff check src tests && mypy src && pytest` after each fix.
6. Commit fixes: `phase-3: address code review findings`
7. Push all commits.
8. THEN output: PHASE_COMPLETE
