# Phase 6c: Validation & Protocol Polish — Progress

## Status: COMPLETE

## Tasks
- [x] 6c.1: SignalConfig min_clamp <= max_clamp Validator (Y9)
- [x] 6c.2: ClockDriftConfig Allow Negative Values (Y10)
- [x] 6c.3: Fix Calibration Drift Rate Docstring (Y11)
- [x] 6c.4: Fix Random Walk Docstring (Y12)
- [x] 6c.5: Dryer Zone Cholesky Correlation (Y13)
- [x] 6c.6: Oven Zone Cholesky Correlation (Y13)
- [x] 6c.7: Coil 4 Derivation Fix (Y14)
- [x] 6c.8: OPC-UA MinimumSamplingInterval (Y15)
- [x] 6c.9: Validate All Fixes — Full Suite

## Notes

Tasks 6c.1-6c.8 are all independent (no dependencies between them). Task 6c.9 depends on all others.

Tasks 6c.5 and 6c.6 share the same pattern (Cholesky noise correlation for zone temperatures). Do 6c.5 first; 6c.6 follows the same approach.

Tasks 6c.3 and 6c.4 are documentation-only (no logic changes).

## Task 6c.1 — SignalConfig min_clamp <= max_clamp Validator

**Completed.** Added `@model_validator(mode="after")` `_clamp_order` to `SignalConfig` (config.py line 302). Raises `ValueError` when both `min_clamp` and `max_clamp` are set and `min_clamp > max_clamp`. Single-sided clamps, equal values, and both-None all pass validation.

6 new tests added to `TestSignalConfig` in `tests/unit/test_config.py`: valid ordering, equal, reversed (rejected), min-only, max-only, neither.

Suite: 3030 passed, ruff + mypy clean.

## Task 6c.2 — ClockDriftConfig Allow Negative Values

**Completed.** Removed two `field_validator` methods (`_offset_non_negative`, `_drift_non_negative`) that rejected negative values for `initial_offset_ms` and `drift_rate_s_per_day`. Replaced with a single `_must_be_finite` validator on both fields that rejects NaN and Inf via `math.isfinite()`. Negative values are valid real-world scenarios (clock behind, clock losing time). The `ClockDriftModel` in `topology.py` already handles negative values correctly.

Updated `tests/unit/test_topology.py`: replaced `test_rejects_negative_offset` and `test_rejects_negative_drift_rate` with `test_accepts_negative_offset`, `test_accepts_negative_drift_rate`, `test_rejects_nan_offset`, and `test_rejects_inf_drift_rate`.

Suite: 3038 passed, ruff + mypy clean.

## Task 6c.3 — Fix Calibration Drift Rate Docstring

**Completed.** Documentation-only change. Clarified `SteadyStateModel` docstring for `calibration_drift_rate`: explicitly states units are per simulated **second** internally, PRD specifies per **hour**, callers must divide by 3600. Added inline comment at the application line (line 143) noting the unit convention. No logic change.

## Task 6c.4 — Fix Random Walk Docstring

**Completed.** Documentation-only change. Replaced incorrect claim that `drift_rate` is "units per sqrt-second -- scaled by `sqrt(dt)` implicitly" with accurate description: each tick applies `drift_rate * N(0,1) * dt` — linear `dt` scaling per PRD Section 4.2.5. Added note distinguishing this from the steady-state O-U drift model which uses `sqrt(dt)`. No logic change.

Suite: 3032 passed, ruff + mypy clean.

## Task 6c.5 — Dryer Zone Cholesky Correlation

**Completed.** Added PRD 4.3.1 Cholesky noise correlation for dryer zone temperatures in `PressGenerator`.

Changes to `src/factory_simulator/generators/press.py`:
- Imported `CholeskyCorrelator` from `models.noise`
- Extracted noise generators for dryer temp zones into `_dryer_temp_noises` list (built separately from models)
- Added `apply_noise=False` parameter to `_build_first_order_lag()` — dryer temp lag models no longer have internal noise
- Built `CholeskyCorrelator` with PRD dryer zone matrix: `[[1.0, 0.1, 0.02], [0.1, 1.0, 0.1], [0.02, 0.1, 1.0]]`
- In `generate()`, replaced individual post-process calls with Cholesky pipeline: generate N(0,1) draws → apply Cholesky L → scale by effective_sigma → add to raw lag values → clamp
- Custom matrix supported via `dryer_zone_correlation_matrix` in equipment extras

3 new tests in `tests/unit/test_generators/test_press.py`:
- `test_dryer_zones_positively_correlated`: 5000-tick correlation analysis, verifies positive r12/r23 and weak r13
- `test_custom_correlation_matrix`: verifies custom matrix override via equipment extras
- `test_dryer_noise_not_double_applied`: verifies lag models have `_noise is None`

Suite: 3035 passed, ruff + mypy clean.

## Task 6c.6 — Oven Zone Cholesky Correlation

**Completed.** Added PRD 4.3.1 Cholesky noise correlation for oven zone temperatures in `OvenGenerator`. Same pattern as 6c.5 (dryer zones). Thermal coupling (physical model via `_update_zone_setpoints`) and Cholesky (noise correlation) coexist independently.

Changes to `src/factory_simulator/generators/oven.py`:
- Imported `CholeskyCorrelator` from `models.noise`
- Extracted noise generators for zone temps into `_zone_temp_noises` list (built separately from models)
- Added `apply_noise` keyword parameter to `_build_zone_temp()` — zone temp lag models now created with `noise=None`
- Built `CholeskyCorrelator` with PRD oven zone matrix: `[[1.0, 0.15, 0.05], [0.15, 1.0, 0.15], [0.05, 0.15, 1.0]]`
- In `generate()`, replaced per-zone post-process with Cholesky pipeline: generate N(0,1) draws → apply Cholesky L → scale by effective_sigma → add to raw lag values → clamp
- Custom matrix supported via `oven_zone_correlation_matrix` in equipment extras

3 new tests in `tests/unit/test_generators/test_oven.py`:
- `test_oven_zones_positively_correlated`: 5000-tick correlation analysis with diff detrending, verifies positive r12/r23 and weak r13
- `test_custom_correlation_matrix`: verifies custom matrix override via equipment extras
- `test_zone_temp_noise_not_double_applied`: verifies lag models have `_noise is None`

Suite: 3038 passed, ruff + mypy clean.

## Task 6c.7 — Coil 4 Derivation Fix

**Completed.** Changed Coil 4 (laminator.running) derivation from `CoilDefinition(4, "press.machine_state", derive_value=2)` to `CoilDefinition(4, "laminator.web_speed", mode="gt_zero")`. This derives the laminator's running state from its own speed signal rather than the press machine state, consistent with Coil 5 (`slitter.speed`, `mode="gt_zero"`).

3 new tests in `tests/unit/test_protocols/test_modbus.py::TestCoilSync`:
- `test_laminator_running_coil_true_when_speed_positive`: Coil 4 True when `laminator.web_speed > 0`
- `test_laminator_running_coil_false_when_speed_zero`: Coil 4 False when `laminator.web_speed == 0`
- `test_laminator_running_independent_of_press_state`: Coil 4 False when press is running but laminator speed is 0

Suite: 3041 passed, ruff + mypy clean.

## Task 6c.8 — OPC-UA MinimumSamplingInterval

**Completed.** Set `MinimumSamplingInterval` attribute on all OPC-UA variable nodes per PRD Appendix B.

Changes to `src/factory_simulator/protocols/opcua_server.py`:
- After creating each variable node (and its EURange/EngineeringUnits properties), write the `MinimumSamplingInterval` attribute using `write_attribute(ua.AttributeIds.MinimumSamplingInterval, ...)`
- Value priority: `sig_cfg.sample_rate_ms` if set, else `self._config.simulation.tick_interval_ms` (default 100ms)
- Value is a `Double` variant (OPC-UA Duration type)

4 new tests in `tests/unit/test_protocols/test_opcua.py::TestMinimumSamplingInterval`:
- `test_minimum_sampling_interval_on_all_nodes`: all 32 leaf nodes have MinimumSamplingInterval > 0
- `test_signal_specific_sample_rate`: LineSpeed reads 1000.0ms (from `sample_rate_ms=1000`)
- `test_default_tick_interval_fallback`: dryer setpoints read 100.0ms (no `sample_rate_ms`, falls back to `tick_interval_ms`)
- `test_web_tension_sample_rate`: WebTension reads 500.0ms (from `sample_rate_ms=500`)

Suite: 3045 passed, ruff + mypy clean.

## Task 6c.9 — Validate All Fixes — Full Suite

**Completed.** Final validation of all phase 6c fixes.

- `ruff check src tests` — clean
- `mypy src` — clean
- `pytest --tb=short -q` — 3045 passed in 3m 47s
- Packaging batch sim (`--batch-duration 1h --seed 42`) — completed successfully, 3600.1s simulated
- F&B batch sim (`--config config/factory-foodbev.yaml --batch-duration 1h --seed 42`) — completed successfully, 3600.1s simulated

No regressions. All 9 tasks pass.
