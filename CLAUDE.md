# CLAUDE.md -- Factory Simulator Agent Instructions

> This file is the single source of truth for AI agents working on the Collatr Factory Simulator. It is symlinked as AGENTS.md.

---

## What This Project Is

The Collatr Factory Simulator is a standalone Python tool that simulates a manufacturing factory over industrial protocols (Modbus TCP, OPC-UA, MQTT). It generates realistic signal data with configurable noise, scenarios, and data quality issues for integration testing, demos, and development of CollatrEdge.

**Runtime:** Python 3.12+ with asyncio
**Architecture:** Single-process signal engine + protocol adapters + Mosquitto sidecar
**Two profiles:** Packaging/printing (47 signals, 7 controllers) and F&B chilled ready meal (68 signals, 10 controllers)

---

## Before You Write Any Code

### 1. Read the PRD

The PRD is in `prd/`. It is comprehensive (23 files, ~5,700 lines). **Read the relevant sections before implementing anything.** The PRD contains exact register maps, signal definitions, noise models, and scenario specifications. Do not guess. Look it up.

Key files you will reference constantly:
- `prd/README.md` -- table of contents
- `prd/02-simulated-factory-layout.md` -- packaging signals and equipment
- `prd/02b-factory-layout-food-and-beverage.md` -- F&B signals and equipment
- `prd/03-protocol-endpoints.md` -- Modbus registers, OPC-UA nodes, MQTT topics
- `prd/04-data-generation-engine.md` -- signal models, noise, correlation
- `prd/05-scenario-system.md` -- all scenario types and their effects
- `prd/06-configuration.md` -- YAML config structure, Docker Compose
- `prd/08-architecture.md` -- component design, concurrency model
- `prd/appendix-a-modbus-register-map.md` -- complete register maps
- `prd/appendix-b-opcua-node-tree.md` -- complete OPC-UA node tree
- `prd/appendix-f-implementation-phases.md` -- phased build plan

### 2. Check the Phase Plan

Work is organised into phases (0-5). Each phase has a plan document in `plans/`. **Never start a phase without a plan. Never start implementing without reading the plan.**

### 3. Check Current Status

Before doing anything, understand where we are:
- `git log --oneline -20`
- `plans/phase-N-progress.md` -- current phase progress
- Run existing tests: `pytest` -- everything must pass before you start

---

## The Rules

These are non-negotiable. Every agent working on this project must follow them.

### Rule 1: No Hand-Waving

Never dismiss a test failure. Never write "tests failed due to timing issues... moving on." If a test fails, understand why, fix the root cause, or stop and document.

**"Flaky test" is not a valid diagnosis.** A test that passes alone but fails in the full suite is a signal that something is wrong: different RNG state, unseeded random generator, shared port, leaked resource, or a feature bleeding across test boundaries. Identify the specific mechanism. Do not document the failure as "known flaky" and move on. The correct response is always to find the cause and fix it.

### Rule 2: Tests Prove Behaviour, Not Coverage

Test priority order:
1. **Signal correctness** -- does the right data come out? Ranges, distributions, correlations.
2. **Protocol fidelity** -- do Modbus registers encode correctly? OPC-UA types right? MQTT payloads valid?
3. **Failure modes** -- what happens when things break? Connection loss, invalid config, edge cases.
4. **Contracts** -- does this module honour its interface?
5. **Edge cases** -- boundaries, empty inputs, concurrent access.

Use property-based testing (Hypothesis) for signal models. Use real protocol client libraries (pymodbus client, asyncua client, paho-mqtt subscriber) for integration tests.

### Rule 3: Small, Verified Steps

One task per session. Each task produces one module + tests + one commit. Run ALL tests after each change. Do not accumulate uncommitted work.

### Rule 4: PRD Is Canon

The PRD is the specification. If the code does not match the PRD, the code is wrong. If you find an ambiguity, check the appendices. If still ambiguous, document the assumption in the progress file.

### Rule 5: Signal Models Are Mathematical

Signal models must match the formulas in Section 4.2. The Cholesky pipeline ordering (Section 4.3.1) is: generate N(0,1), apply Cholesky L, scale by effective sigma. Do not reorder. Do not simplify.

### Rule 6: Simulated Time Invariant

All signal generation must produce identical output regardless of wall clock speed. Section 4.1 Principle 5. A simulation at 1x and 10x must produce the same signal values for the same simulated timestamps. Use the simulation clock, never wall clock, for signal generation.

### Rule 7: YAGNI

Do not build features not in the current phase plan. No hot reload. No Prometheus metrics. No Sparkplug B (deferred). No write-back from Modbus to signal models beyond setpoint registers explicitly specified in the PRD.

### Rule 8: Engine Atomicity

The engine updates ALL signals for one tick before yielding. No await between individual signal updates within a tick. This ensures protocol readers see a consistent snapshot. Section 8.3.

### Rule 9: Single Writer, No Locks

The signal store has one writer (the engine) and multiple readers (protocol adapters). Python asyncio is single-threaded. No locks needed. Do not add locks. If you think you need a lock, you have a design problem.

### Rule 10: Configuration via Pydantic

All configuration flows through Pydantic validation models. No hardcoded values that should come from config. No `magic numbers` without a config parameter or a named constant with a PRD reference.

### Rule 11: Docker First

The simulator runs in Docker Compose (simulator + Mosquitto sidecar). Integration tests should work against the Docker stack. The `config/mosquitto.conf` must exist. See Section 6.3.

### Rule 12: No Global State

Equipment generators, signal models, and scenarios are instantiated per-profile. No module-level mutable state. No singletons. Each component receives its dependencies via constructor injection.

### Rule 13: Reproducible Runs

When a seed is configured, the simulation must produce byte-identical output on the same platform. Use numpy.random.Generator with SeedSequence. Never use the random module. Each subsystem gets an isolated Generator spawned from the root SeedSequence.

### Rule 14: Test Fixtures Must Explicitly Control All Injectable Behaviour

When a component has optional injectable behaviour (exception injection, comm drops, noise, data quality), every test fixture that creates that component must explicitly decide whether the behaviour is on or off — never rely on defaults. Specifically:

- If a fixture is testing X (e.g. register encoding), it must disable everything that is not X (e.g. set `exception_probability=0.0`, `modbus_drop.enabled=False`).
- If a new feature is added that affects all instances of a component (e.g. Modbus exception injection added to `ModbusServer`), audit every existing test fixture that creates that component and update it to either opt in or opt out explicitly.
- An unseeded `np.random.default_rng()` in a constructor is a red flag: if a test fixture does not supply the RNG, the behaviour is non-deterministic and will eventually cause failures.
- **Before writing a new test file, read at least one existing test file in the same directory.** This project has no shared pytest fixtures in `conftest.py`; tests load configs via `load_config(path)` and construct `SignalStore()` directly. Assuming fixtures exist without checking will cause a rewrite.

---

## Phase Work Pattern

Each phase follows this pattern. See `plans/WORKFLOW.md` for the full workflow.

1. **Read the plan** in `plans/phase-N-<name>.md`
2. **Find the first failing task** in `plans/phase-N-tasks.json`
3. **Implement one task**: code + tests + commit
4. **Run the new test file alone first**: `ruff check src tests && pytest tests/path/to/new_test.py -v --tb=short` -- catches import errors, wrong fixtures, and lint issues in seconds before the expensive full suite
5. **Run ALL tests** (`pytest` via sub-agent, 6-min timeout) -- every test must pass
6. **Update progress** in `plans/phase-N-progress.md`
7. **Output TASK_COMPLETE and STOP**
8. When all tasks pass: spawn internal review sub-agent, fix findings, then PHASE_COMPLETE

---

## Technical Standards

### Python Conventions
- Python 3.12+ (use modern syntax: match statements, type union `X | Y`, etc.)
- Type hints on all public functions and methods
- Async/await for all I/O (asyncio)
- Pydantic v2 for config validation
- numpy for signal generation (vectorised where possible)
- Structured logging via Python `logging` module (JSON format)

### Dependencies (pinned)
- `pymodbus>=3.6,<4.0` -- Modbus TCP server
- `asyncua>=1.1.5` -- OPC-UA server
- `paho-mqtt>=2.0` -- MQTT publisher
- `numpy>=1.26` -- signal generation
- `pydantic>=2.0` -- config validation
- `pyyaml>=6.0` -- YAML parsing
- `hypothesis>=6.0` -- property-based testing
- `pytest>=8.0` -- test runner
- `pytest-asyncio>=0.23` -- async test support
- `pytest-xdist>=3.5` -- parallel test workers
- `ruff>=0.3` -- linting
- `mypy>=1.8` -- type checking
- `uvloop>=0.19` -- Linux only, conditional import

### Test Commands
```bash
# Full CI pipeline
ruff check src tests && mypy src && pytest

# Quick unit tests only
pytest tests/unit

# Integration tests (requires Docker Compose up)
pytest tests/integration

# Specific test file
pytest tests/unit/test_steady_state.py -v
```

### Running the Full Test Suite

The full test suite takes **~4–5 minutes** with pytest-xdist parallel workers (installed in phase 6a). Follow these rules every time:

**Rule: Always run the full test suite via a sub-agent.**

Never run `pytest` (the full suite) inline in the main agent — the log volume wastes context window. Instead, delegate to a `general-purpose` sub-agent with this pattern:

```
Run: ruff check src tests && mypy src && pytest --tb=short -q
Timeout: 360000ms (6 minutes — increase if suite grows)
Report: on SUCCESS print only the summary line (N passed, N warnings, time).
        on FAILURE print the full failure output verbatim so the error is visible.
```

- Performance tests are excluded from the default parallel run (`--ignore=tests/performance` in addopts). Run them separately: `pytest tests/performance -p no:xdist`.
- Increase the timeout if the suite consistently approaches 6 minutes.
- Never skip the full suite before committing. ruff + mypy + pytest must all pass.

### Project Structure
See `prd/appendix-e-project-structure.md` for the full layout.

### Commit Format
```
phase-N: <what was done> (task N.X)
```

---

## Key Architecture Decisions

These are settled. Do not revisit.

- **Mosquitto sidecar** for MQTT (not embedded broker). eclipse-mosquitto:2 Docker image.
- **asyncua** for OPC-UA server (not open62541). Pure Python, async-native.
- **pymodbus** for Modbus TCP server. Custom request handlers for FC06 rejection, max register limits, connection limits.
- **Single asyncio event loop** for all protocol servers and the signal engine.
- **Cholesky decomposition** for peer correlation (not raw mixing matrix).
- **Pydantic** for config validation (not raw YAML parsing).
- **Hypothesis** for property-based testing of signal models.
- **No hot reload.** Restart the container.
- **No Prometheus.** Health endpoint is sufficient for MVP.

---

## What NOT To Do

- Do not use the `random` module. Use `numpy.random.Generator`.
- Do not add `asyncio.Lock`. Single-threaded event loop means no locks needed.
- Do not use `time.time()` for signal generation. Use the simulation clock.
- Do not hardcode register addresses. They come from config/profile data.
- Do not spawn parallel sub-agents that push to the same repo.
- Do not push from the ralph.sh loop on machines where git tokens expire. Push is handled externally.
