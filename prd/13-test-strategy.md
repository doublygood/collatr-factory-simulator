# Test Strategy

## 13.1 Philosophy

Tests exist to build confidence in the code. Not to hit a coverage number. Not to tick a box.

The simulator is a test tool. Its output must be trustworthy. If the signal models produce wrong values, every downstream test that relies on the simulator is invalid. If the protocol adapters encode data incorrectly, CollatrEdge integration tests pass against broken data. The test strategy focuses on the paths where errors cause the most damage.

Three priorities:

1. **Signal model correctness.** The mathematical models are the foundation. If a first-order lag formula is wrong, every temperature signal in both profiles is wrong. These get the most thorough testing.

2. **Protocol encoding fidelity.** Modbus register encoding, OPC-UA node values, and MQTT payloads must match the PRD specification exactly. A byte-order error in the Modbus adapter silently corrupts every float32 reading.

3. **Scenario state transitions.** The scenario engine drives the simulator through realistic operating patterns. A broken state machine produces unrealistic data that defeats the purpose of the simulator.

No coverage target. The exit criterion is: a developer can change any signal model, protocol adapter, or scenario and know within 60 seconds whether the change broke something.

## 13.2 Test Pyramid

### Unit Tests

**Signal models (highest priority).** Each of the 12 signal model types gets a dedicated test module. Tests verify:

- Output stays within physical bounds for all valid parameter combinations.
- Deterministic output: same seed, same parameters, same sequence.
- Edge cases: zero speed, zero sigma, minimum/maximum parameter values.
- Time invariance: model uses simulated time, not wall-clock time.
- Mathematical correctness: first-order lag converges to setpoint, counters increment monotonically, random walk mean-reverts toward centre.

Property-based testing with Hypothesis is the right tool for signal models. Generate random valid parameters and assert invariants:

```python
@given(
    setpoint=st.floats(min_value=0, max_value=500),
    tau=st.floats(min_value=0.1, max_value=100),
    dt=st.floats(min_value=0.01, max_value=1.0),
)
def test_first_order_lag_converges(setpoint, tau, dt):
    """After sufficient ticks, value approaches setpoint."""
    model = FirstOrderLag(setpoint=setpoint, tau=tau)
    for _ in range(10000):
        model.tick(dt)
    assert abs(model.value - setpoint) < setpoint * 0.01 + 0.01
```

**Noise pipeline.** Test the Cholesky correlation pipeline in isolation:

- Correlated samples have the expected correlation coefficient (within statistical tolerance over N=10,000 samples).
- Cholesky decomposition of all specified matrices succeeds.
- Speed-dependent sigma scales correctly.
- AR(1) autocorrelation matches configured phi.
- Student-t samples have heavier tails than Gaussian (kurtosis test).

**Scenario state machines.** Each scenario type gets tests for:

- Valid state transitions fire in the correct order.
- Invalid transitions are rejected.
- Duration constraints are respected (min/max).
- Trigger conditions activate the scenario correctly.

**Configuration validation.** Test that invalid configurations are rejected with clear error messages:

- Negative sigma, negative time_scale, min > max ranges.
- Missing required fields.
- Unknown signal model types.
- Invalid correlation matrices (not positive-definite).
- Student-t df < 3.

### Integration Tests

**Protocol adapter tests.** Each protocol adapter is tested with a real client library:

- **Modbus:** `pymodbus` client connects, reads all holding registers, input registers, coils, and discrete inputs. Verify value ranges, float32 encoding, byte ordering (ABCD and CDAB), multi-slave unit IDs.
- **OPC-UA:** `asyncua` client connects, browses the node tree, reads values, creates subscriptions, receives data change notifications. Verify node structure matches Appendix B, engineering units are present, status codes are correct.
- **MQTT:** `paho-mqtt` client subscribes to all topics, receives messages, parses JSON payloads. Verify topic structure matches Appendix C, retain flags are set, QoS levels are correct.

Each test spins up the relevant protocol adapter with a pre-populated signal store. The store contains known values. The test verifies the client reads those exact values through the protocol layer. This isolates protocol encoding from signal generation.

**Cross-protocol consistency.** Start all three protocol adapters against the same signal store. Read the same signal via Modbus, OPC-UA, and MQTT. Verify all three return the same value (accounting for encoding precision differences between float32 Modbus and float64 OPC-UA).

**Network topology tests.** In realistic mode:

- Multiple Modbus servers bind to different ports.
- Each server serves only its own controller's registers.
- Out-of-range register reads return Modbus exception 0x02.
- Connection limits are enforced per controller.

### End-to-End Tests

**Smoke test.** Start the full simulator with a known seed. Let it run for 60 simulated seconds. Verify:

- All signals produce values within expected ranges.
- At least one scenario fires (job changeover with high frequency config).
- All three protocols serve data.
- Ground truth log is written.
- No errors in logs.

**Reproducibility test.** Run twice with the same seed and config. Compare the first 10,000 signal values. They must be identical.

**Long-run stability.** Run for 24 simulated hours at 10x (2.4 real hours). Verify:

- Memory (RSS) stays within 2x of initial.
- No protocol server crashes.
- No NaN or infinity values in the signal store.
- Ground truth log is well-formed JSONL.

This test runs in CI on a nightly schedule, not on every commit.

## 13.3 Test Tooling

| Tool | Purpose |
|------|---------|
| `pytest` | Test runner |
| `hypothesis` | Property-based testing for signal models |
| `pytest-asyncio` | Async test support for protocol adapters |
| `pymodbus` | Modbus client for integration tests |
| `asyncua` | OPC-UA client for integration tests |
| `paho-mqtt` | MQTT client for integration tests |
| `ruff` | Linting |
| `mypy` | Type checking |

## 13.4 What We Do Not Test

- **Mosquitto broker internals.** We test that our MQTT adapter publishes correctly. We do not test that Mosquitto routes messages. That is Eclipse Foundation's job.
- **pymodbus/asyncua library correctness.** We test our adapter code. We do not write tests for third-party library bugs.
- **UI rendering.** The optional web dashboard, if built, is not tested automatically. It is a development convenience, not a product feature.
- **Performance benchmarks in CI.** Performance profiling happens manually before releases. Flaky timing-dependent tests in CI cause more harm than good.

## 13.5 CI Pipeline

Every push runs:

1. `ruff check src/` (lint, fast)
2. `mypy src/` (type check, fast)
3. Unit tests (signal models, noise pipeline, scenario state machines, config validation)
4. Integration tests (protocol adapters with real clients)
5. Smoke test (60-second full simulator run)

Total CI time target: under 5 minutes.

Nightly: long-run stability test (24 simulated hours).
