Read CLAUDE.md for project rules and conventions.

You are implementing Phase 1 (Core Engine, Modbus, and Test Infrastructure) of the Collatr Factory Simulator.

## CONTEXT

Phase 0 (validation spikes) is complete. The project scaffolding exists: pyproject.toml, requirements.txt, test directories, Docker Compose, Mosquitto config. Spike tests in `tests/spikes/` validated pymodbus, asyncua, and paho-mqtt. Reference patterns from the spikes are documented in `docs/validation-spikes.md`.

Phase 1 builds the simulator's core: configuration loading, simulation clock, signal value store, all 12 signal model types, all 7 packaging equipment generators (47 signals), the Cholesky correlation pipeline, noise distributions, basic scenario support, and the Modbus TCP server with the full packaging register map.

The PRD is in `prd/` (23 files, ~5,700 lines). Read the relevant sections referenced in each task.

## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read `plans/phase-1-core-engine-modbus.md` for the full plan
2. Read `plans/phase-1-tasks.json` to find the **first** task with `"passes": false`
3. Read the relevant PRD sections referenced in that task
4. Implement ONLY that single task
5. Run tests: `ruff check src tests && mypy src && pytest` -- ALL must pass
6. Update `plans/phase-1-tasks.json`: set `"passes": true` for your completed task
7. Update `plans/phase-1-progress.md` with what you built and any decisions
8. Commit: `phase-1: <what> (task 1.X)`
9. Do NOT push. Pushing is handled externally.
10. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## PHASE-SPECIFIC NOTES

### Group A: Foundation (Tasks 1.1-1.4)

- **Task 1.1 (Configuration):** Create Pydantic v2 models for the entire config schema. Start with the structure in PRD Section 6.2. Create a default `config/factory.yaml` for the packaging profile. Validation must reject bad configs with clear messages. Env var overrides follow the SIM_ prefix convention.

- **Task 1.2 (Clock):** The clock is simple but critical. Rule 6: simulated time must be deterministic regardless of wall-clock speed. The clock does not use asyncio or wall time. It ticks when told to tick.

- **Task 1.3 (Store):** The signal store is a dict-like container. No locks (Rule 9). Support float and string values. Quality flags are strings: "good", "uncertain", "bad".

- **Task 1.4 (Noise Pipeline):** This is the mathematically dense task. Implement all three noise distributions (Gaussian, Student-t, AR(1)), speed-dependent sigma, and the Cholesky correlation pipeline. The pipeline order matters (Section 4.3.1): generate N(0,1) -> apply L -> scale by sigma. Use property-based testing with Hypothesis to validate statistical properties. Use `numpy.random.Generator` with `SeedSequence` (Rule 13).

### Group B: Signal Models (Tasks 1.5-1.12)

Each task produces one signal model module + tests. Models are independent of each other. Each model implements the SignalModel interface from Task 1.4.

- Models should accept a NoiseGenerator (from Task 1.4) for their noise. This keeps noise distribution selection at the config level, not hardcoded in models.
- All models use sim_time and dt, never wall-clock time (Rule 6).
- Use Hypothesis property-based testing where applicable. Good properties to test: output within physical bounds, determinism with same seed, convergence behaviour over many ticks.
- Sensor quantisation (Section 4.2.13) applies as a post-processing step. Implement it once in the base or as a wrapper, not in every model.

### Group C: Remaining Models (Tasks 1.13-1.15)

- **Task 1.13 (State Machine):** This is the most complex model. The press state machine drives everything. Design the transition system carefully -- it needs to support timer-based, condition-based, and probability-based triggers.
- **Task 1.14 (Thermal Diffusion):** The Fourier series must converge (T(0) within 1C of T_initial). Add terms dynamically. This model is F&B-specific but implement it now -- it's part of the 12 signal model types.
- **Task 1.15 (Bang-Bang + String):** Two small models in one task. Bang-bang is the chiller compressor. String generator is for batch IDs (F&B). Both simple.

### Group D: Equipment Generators + Engine (Tasks 1.16-1.19)

- **Task 1.16 (Press Generator):** The press is the hardest generator -- 21 signals, complex state machine, state cascade. It is the template all other generators follow. Get the EquipmentGenerator ABC right here. The generate() method reads state from the store (Section 8.4) -- no machine_state parameter.
- **Task 1.17 (Other Generators):** Six generators in one task. Each is simpler than the press. Laminator (5 signals), slitter (3, scheduled), coder (11, follows press), environment (2, composite sinusoidal+HVAC+perturbations), energy (2, follows press power), vibration (3, Cholesky-correlated).
- **Task 1.18 (Data Engine):** Wires everything together. The engine owns the clock, store, and generators. tick() is atomic (Rule 8). Sample rate enforcement per signal.
- **Task 1.19 (Scenarios):** Three basic scenarios. The scenario engine schedules them and modifies machine state / signal parameters. Job changeover is the most complex (ramp down, setup, ramp up with waste spike).

### Group E: Modbus + Integration (Task 1.20)

- **Task 1.20 (Modbus Server):** Use patterns from `tests/spikes/test_spike_modbus.py` and `docs/validation-spikes.md`. The server reads from the SignalStore and encodes registers per Appendix A. Float32 ABCD encoding for HR, int16 x10 for IR temperature signals, coils reflect machine state. Integration test: start engine + server, connect pymodbus client, verify every register address.

## STOPPING RULES

**After completing ONE task:** Output `TASK_COMPLETE` and stop immediately.
Do not look for the next task. Do not start another task.
The ralph.sh loop will call you again for the next iteration.

**When ALL tasks in the task JSON have "passes": true:**
1. Do NOT output PHASE_COMPLETE yet.
2. Spawn a sub-agent code review.
3. Write the review to `plans/phase-1-review.md`
4. Review checks: PRD compliance (signal models match formulas, register addresses match Appendix A), CLAUDE.md rules, error handling, test coverage of hard paths, concurrency correctness (no await mid-tick), config wiring (no magic numbers).
5. Address all RED Must Fix findings. Re-run `ruff check src tests && mypy src && pytest` after each fix.
6. Commit fixes: `phase-1: address code review findings`
7. Push all commits.
8. THEN output: PHASE_COMPLETE
