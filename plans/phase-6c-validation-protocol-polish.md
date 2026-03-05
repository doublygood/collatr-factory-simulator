# Phase 6c: Validation & Protocol Polish

**Scope:** YELLOW issues Y9-Y15 from the three-reviewer code review.
**Depends on:** Phase 6b complete (all 6 tasks, 3024 tests passing).

---

## Task 6c.1 — SignalConfig `min_clamp <= max_clamp` Validator

**Review ref:** Y9 (review-architecture.md §4.1)

**Problem:** `SignalConfig` (config.py lines 242-243) defines `min_clamp` and `max_clamp` as optional floats but has no validator ensuring `min_clamp <= max_clamp` when both are set. A misconfigured signal with `min_clamp=100, max_clamp=50` would silently produce clamped values at the lower bound.

**Fix:**

Add a `@model_validator(mode="after")` to `SignalConfig` that checks:
```python
@model_validator(mode="after")
def _clamp_order(self) -> "SignalConfig":
    if (
        self.min_clamp is not None
        and self.max_clamp is not None
        and self.min_clamp > self.max_clamp
    ):
        raise ValueError(
            f"min_clamp ({self.min_clamp}) must be <= max_clamp ({self.max_clamp})"
        )
    return self
```

If only one is set, skip the check (single-sided clamp is valid).

**Tests:**
- `test_signal_config_clamp_order_valid` — both set, min < max: passes
- `test_signal_config_clamp_equal` — min == max: passes (degenerate but valid)
- `test_signal_config_clamp_reversed` — min > max: raises `ValidationError`
- `test_signal_config_clamp_one_sided` — only min or only max: passes
- `test_signal_config_clamp_neither` — both None: passes

**Files:** `src/factory_simulator/config.py`, `tests/unit/test_config.py`

---

## Task 6c.2 — ClockDriftConfig Allow Negative Values

**Review ref:** Y10 (review-architecture.md §4.1)

**Problem:** `ClockDriftConfig` validators (config.py lines 1274-1285) reject negative `initial_offset_ms` and `drift_rate_s_per_day`. Negative values are valid: a controller clock running behind real time (`initial_offset_ms < 0`) or losing time (`drift_rate_s_per_day < 0`) are real-world scenarios.

**Fix:**

1. Remove the `_offset_non_negative` validator (or change it to accept any finite float).
2. Remove the `_drift_non_negative` validator (or change it to accept any finite float).
3. Optionally add a validator that rejects `NaN` or `Inf` if not already guarded.

The `ClockDriftModel` in `topology.py` already computes `drifted_time = sim_time + initial_offset_s + drift_rate * elapsed_hours / 24`. Negative values flow through correctly — no code changes needed outside config.

**Tests:**
- `test_clock_drift_negative_offset` — `initial_offset_ms=-500.0`: valid, no error
- `test_clock_drift_negative_drift_rate` — `drift_rate_s_per_day=-0.5`: valid, no error
- `test_clock_drift_positive_values` — existing positive values still work
- `test_clock_drift_zero_values` — defaults (both 0.0) still work

**Files:** `src/factory_simulator/config.py`, `tests/unit/test_config.py`

---

## Task 6c.3 — Fix Calibration Drift Rate Docstring

**Review ref:** Y11 (review-signal-integrity.md §2.1)

**Problem:** The `SteadyStateModel` docstring (steady_state.py line 54) says `calibration_drift_rate` is "persistent drift in signal units per simulated second". The PRD (Section 4.2.1, Appendix D) says the parameter is "units per simulated hour". The code applies `calibration_drift_rate * dt` where `dt` is in seconds.

**Current docstring (lines 54-58):**
```
calibration_drift_rate : float, optional
    Persistent drift in signal units per simulated second (default 0.0).
    PRD specifies units per hour; the config loader should convert or
    the caller should pass seconds.  We accept per-second here for
    consistency with the engine's dt (seconds).
```

The docstring already acknowledges the discrepancy and documents the design decision: the model accepts per-second values and expects the caller to convert if needed. No config currently sets this value (defaults to 0.0), so there is no active bug.

**Fix:**

Clarify the docstring to be unambiguous:
```
calibration_drift_rate : float, optional
    Persistent calibration drift rate in signal units per simulated
    second (default 0.0 = disabled).  The PRD specifies this parameter
    in units per simulated hour (Section 4.2.1, Appendix D line
    ``calibration_drift_rate``).  Callers passing PRD-sourced values
    must divide by 3600 before constructing this model.

    Example: PRD value 0.01 C/hour → pass 0.01/3600 ≈ 2.78e-6 C/s.
```

Also add a brief comment at line 143 where the drift is applied:
```python
# calibration_drift_rate is in units/second; dt is in seconds
self._calibration_bias += self._calibration_drift_rate * dt
```

**Tests:** No new tests needed (documentation-only change). Verify existing tests pass.

**Files:** `src/factory_simulator/models/steady_state.py`

---

## Task 6c.4 — Fix Random Walk Docstring

**Review ref:** Y12 (review-signal-integrity.md §2.8)

**Problem:** The `RandomWalkModel` docstring (random_walk.py lines 41-43) claims:
```
The ``drift_rate`` controls how fast the signal wanders (units per
sqrt-second -- scaled by ``sqrt(dt)`` implicitly through the discrete
Euler step).
```

This is misleading. The code (lines 109-112) computes:
```python
innovation = self._drift_rate * self._rng.standard_normal()
delta = innovation - reversion
self._value += delta * dt
```

The innovation scales linearly with `dt`, NOT `sqrt(dt)`. The PRD formula (Section 4.2.5) also uses linear dt. The docstring is wrong about sqrt(dt) scaling.

**Fix:**

Replace the misleading docstring text (lines 41-43) with:
```
The ``drift_rate`` controls how fast the signal wanders.  Each tick
the innovation is ``drift_rate * N(0,1)`` and the full delta
(innovation minus reversion) is scaled by ``dt``.  This matches the
PRD Section 4.2.5 formula exactly.  Note: unlike the steady-state
Ornstein-Uhlenbeck drift (Section 4.2.1) which uses ``sqrt(dt)``
scaling, this model uses linear ``dt`` scaling per the PRD spec.
```

**Tests:** No new tests needed (documentation-only change). Verify existing tests pass.

**Files:** `src/factory_simulator/models/random_walk.py`

---

## Task 6c.5 — Dryer Zone Cholesky Correlation

**Review ref:** Y13 (review-signal-integrity.md §4.5)

**Problem:** PRD Section 4.3.1 specifies a 3x3 correlation matrix for dryer zones:
```
R = [[1.0,  0.1,  0.02],
     [0.1,  1.0,  0.1 ],
     [0.02, 0.1,  1.0 ]]
```

The vibration generator implements Cholesky correlation (vibration.py), but the press generator (press.py) generates dryer zone temperatures with independent noise. The three dryer zones share thermal mass and should exhibit correlated noise.

**Fix:**

In `PressGenerator.__init__()`:

1. Import `CholeskyCorrelator` from `factory_simulator.models.noise`.
2. Define the PRD dryer zone correlation matrix as a class constant:
   ```python
   _DRYER_ZONE_CORRELATION = np.array([
       [1.0,  0.1,  0.02],
       [0.1,  1.0,  0.1 ],
       [0.02, 0.1,  1.0 ],
   ])
   ```
3. Create a `CholeskyCorrelator` instance from this matrix.
4. For each dryer zone signal, extract the `NoiseGenerator` (call `self._make_noise(sig_cfg)`) and store it separately — these NoiseGenerators will provide `effective_sigma()` but will NOT be passed to the underlying `FirstOrderLagModel` (to avoid double-noising, same pattern as vibration).
5. **Crucially:** the dryer zone signals use `FirstOrderLagModel`, not `SteadyStateModel`. The noise injection point is DIFFERENT from vibration. The lag model produces a deterministic response to setpoint changes; we need to add correlated noise ON TOP of the lag model output, not inside it.

In `PressGenerator.tick()` (the dryer temperature section, around lines 479-497):

1. After generating raw temps from the three lag models (`raw_t1, raw_t2, raw_t3`), generate 3 independent N(0,1) draws.
2. Apply the Cholesky factor via `correlator.correlate(z)`.
3. For each zone, compute `effective_sigma` from the noise generator (using press speed or None), then add `sigma * correlated_z[i]` to the raw temperature.
4. Then apply `_post_process` (clamping/quantisation) as before.

**Important implementation details:**
- The dryer temp noise generators (`NoiseGenerator` instances) should NOT be passed to the `FirstOrderLagModel` constructor. Build the lag models WITHOUT noise, then apply correlated noise externally. Check how the lag models are currently built in `_build_first_order_lag()` — if they already receive noise, strip it.
- If `_build_first_order_lag()` already passes noise to the model internally, you'll need to either: (a) add a `skip_noise=True` parameter, or (b) build the noise separately and pass `noise=None` to the lag model. Option (b) is cleaner and matches the vibration pattern exactly.
- Allow config override via `dryer_zone_correlation_matrix` in the equipment extras (same pattern as vibration's `axis_correlation_matrix`).

**Tests:**
- `test_dryer_zone_correlation_positive` — run N ticks, compute sample correlation between zones 1-2. Assert > 0 (should be near 0.1). Use a known seed.
- `test_dryer_zone_correlation_matrix_matches_prd` — verify the class constant matches the PRD matrix.
- `test_dryer_zone_correlation_custom_matrix` — pass a custom matrix via config extras, verify it's used.
- `test_dryer_zone_independent_without_correlation` — pass identity matrix, verify near-zero correlation.

**Files:** `src/factory_simulator/generators/press.py`, `tests/unit/test_generators/test_press_dryer_correlation.py` (new file)

---

## Task 6c.6 — Oven Zone Cholesky Correlation

**Review ref:** Y13 (review-signal-integrity.md §4.5)

**Problem:** PRD Section 4.3.1 specifies a 3x3 correlation matrix for oven zones:
```
R = [[1.0,  0.15, 0.05],
     [0.15, 1.0,  0.15],
     [0.05, 0.15, 1.0 ]]
```

The oven generator (oven.py) generates zone temperatures with independent noise, same issue as dryer zones.

**Fix:**

Same approach as Task 6c.5 but in `OvenGenerator`:

1. Import `CholeskyCorrelator`.
2. Define the PRD oven zone correlation matrix:
   ```python
   _OVEN_ZONE_CORRELATION = np.array([
       [1.0,  0.15, 0.05],
       [0.15, 1.0,  0.15],
       [0.05, 0.15, 1.0 ],
   ])
   ```
3. Create a `CholeskyCorrelator` instance.
4. Extract noise generators for zone temp signals; do NOT pass them to the `FirstOrderLagModel`.
5. In `generate()` (zone temperature section, around lines 359-370), apply correlated noise after raw generation:
   - Generate 3 N(0,1) draws, apply Cholesky, scale by sigma, add to raw zone temps.

**Additional complexity:** The oven generator already has inter-zone thermal coupling (adjacent zone drift influence). This is a physical-model coupling at the setpoint/lag level — it is SEPARATE from the Cholesky noise correlation. The Cholesky correlation is on the NOISE component only. Both should coexist.

Check how `_build_zone_temp()` works:
- If it creates a `FirstOrderLagModel` with noise passed in, refactor to build WITHOUT noise.
- Store the noise generators in `self._zone_temp_noises: list[NoiseGenerator | None]`.

Allow config override via `oven_zone_correlation_matrix` in equipment extras.

**Tests:**
- `test_oven_zone_correlation_positive` — run N ticks, assert positive sample correlation between zones.
- `test_oven_zone_correlation_matrix_matches_prd` — verify class constant.
- `test_oven_zone_higher_than_dryer` — oven correlation (0.15) should be higher than dryer (0.1). Run both, compare sample correlations.

**Files:** `src/factory_simulator/generators/oven.py`, `tests/unit/test_generators/test_oven_zone_correlation.py` (new file)

---

## Task 6c.7 — Coil 4 Derivation Fix

**Review ref:** Y14 (review-protocol-fidelity.md §2.8)

**Problem:** Coil 4 (laminator.running) at `modbus_server.py:539` is derived from `press.machine_state == 2` rather than `laminator.web_speed > 0`. This means the laminator coil tracks the press state, not the laminator's own operating status. Coil 5 (slitter.running) correctly uses `slitter.speed > 0` with `mode="gt_zero"`.

**Current code (line 539):**
```python
CoilDefinition(4, "press.machine_state", derive_value=2),   # laminator.running
```

**Fix:**

Change to:
```python
CoilDefinition(4, "laminator.web_speed", mode="gt_zero"),    # laminator.running
```

This matches the Coil 5 pattern and correctly derives the laminator running state from the laminator's own speed signal rather than the press state.

**Edge case:** Verify that `laminator.web_speed` exists in the store by the time coils are derived. The laminator generator produces this signal, and generators run before protocol sync, so this should be fine. Check the signal name matches the store key — it should be `laminator.web_speed` (with equipment prefix).

**Tests:**
- `test_coil_4_laminator_running_from_speed` — set `laminator.web_speed > 0`, verify coil 4 is True.
- `test_coil_4_laminator_stopped` — set `laminator.web_speed = 0`, verify coil 4 is False.
- `test_coil_4_independent_of_press_state` — set press to Fault (state 4) but laminator.web_speed > 0, verify coil 4 is still True. (This scenario shouldn't happen in practice but tests the derivation independence.)

**Files:** `src/factory_simulator/protocols/modbus_server.py`, `tests/unit/test_protocols/test_modbus_coils.py` (new or existing)

---

## Task 6c.8 — OPC-UA MinimumSamplingInterval

**Review ref:** Y15 (review-protocol-fidelity.md §3.4)

**Problem:** PRD Appendix B states `MinimumSamplingInterval` should be set on all variable nodes to match the signal's configured sample rate in milliseconds. asyncua defaults this to 0 (fastest possible), which misrepresents the signal's actual update rate.

**Fix:**

In `_build_node_tree()` (opcua_server.py), after creating the variable node and adding EURange/EngineeringUnits properties, set `MinimumSamplingInterval`:

```python
# MinimumSamplingInterval (PRD Appendix B)
# Use the signal's sample_rate_ms if configured, otherwise fall back
# to the simulation tick interval (the fastest any signal can update).
min_sampling_ms = float(
    sig_cfg.sample_rate_ms
    if sig_cfg.sample_rate_ms is not None
    else self._config.simulation.tick_interval_ms
)
await var_node.set_attr_bit(
    ua.AttributeIds.MinimumSamplingInterval,
    ua.DataValue(ua.Variant(min_sampling_ms, ua.VariantType.Double)),
)
```

**asyncua API note:** In asyncua, `MinimumSamplingInterval` is an attribute of variable nodes, not a property. Use `write_attribute()`:
```python
await var_node.write_attribute(
    ua.AttributeIds.MinimumSamplingInterval,
    ua.DataValue(ua.Variant(min_sampling_ms, ua.VariantType.Double)),
)
```

Check the asyncua API for the correct method — it may be `set_attribute()` or `write_attribute()`. Look at how EURange is set for reference. If asyncua doesn't support direct MinimumSamplingInterval writes on the server side, an alternative is to set it during node creation via `add_variable()` kwargs or post-creation attribute write.

**Fallback approach:** If asyncua's server-side API doesn't expose a clean way to set `MinimumSamplingInterval`, it may need to be set via the node's internal attributes. Research `asyncua.server.Server` node attribute APIs.

**Tests:**
- `test_minimum_sampling_interval_set` — read `MinimumSamplingInterval` from a node, verify it equals tick_interval_ms.
- `test_minimum_sampling_interval_custom` — configure a signal with `sample_rate_ms: 500`, verify its node has `MinimumSamplingInterval = 500.0`.
- `test_minimum_sampling_interval_default` — signal without `sample_rate_ms`, verify falls back to tick_interval_ms.

**Files:** `src/factory_simulator/protocols/opcua_server.py`, `tests/unit/test_protocols/test_opcua.py`

---

## Task 6c.9 — Validate All Fixes — Full Suite

**Depends on:** Tasks 6c.1-6c.8

**Steps:**
1. Run `ruff check src tests` — must be clean.
2. Run `mypy src` — must pass.
3. Run `pytest` — ALL tests must pass.
4. Run a batch simulation with both profiles to verify no regressions:
   - Packaging: `python -m factory_simulator run --batch-output /tmp/test-pkg --batch-duration 1h --seed 42`
   - F&B: `python -m factory_simulator run --config config/factory-foodbev.yaml --batch-output /tmp/test-fnb --batch-duration 1h --seed 42`
5. Verify both complete without error.
6. Fix any failures.

**Files:** None (validation only).

---

## Dependencies

```
6c.1 (clamp validator)     → independent
6c.2 (clock drift)         → independent
6c.3 (calibration docstring) → independent
6c.4 (random walk docstring) → independent
6c.5 (dryer Cholesky)      → independent
6c.6 (oven Cholesky)       → independent (but similar pattern to 6c.5)
6c.7 (Coil 4)              → independent
6c.8 (MinimumSamplingInterval) → independent
6c.9 (validation)          → depends on ALL of 6c.1-6c.8
```

All tasks except 6c.9 are independent and can be done in any order.

## Effort Estimate

- 6c.1-6c.4: ~15 min each (config validators + docstring fixes)
- 6c.5-6c.6: ~45-60 min each (Cholesky integration, significant refactoring of noise injection)
- 6c.7: ~15 min (one-line fix + tests)
- 6c.8: ~30 min (OPC-UA attribute, need to verify asyncua API)
- 6c.9: ~15 min (run suite)
- **Total: ~4 hours**
