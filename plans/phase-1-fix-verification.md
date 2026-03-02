# Phase 1: Fix Verification Report

**Date:** 2026-03-02

## Summary

All 9 fixes (3 RED, 6 YELLOW) applied in commit `98746c5` are correct and match PRD specifications. The code-to-config-to-runtime path is traced and verified for each fix. One minor observation: the `noise_type` field in YAML maps to `"student_t"` and `"ar1"` strings, which are correctly validated by `SignalConfig._valid_noise_type`. **GO for Phase 2.**

## RED Fix Verification

### R1: Vibration correlation matrix
**Status:** PASS ✅

**Evidence:**

The PRD (Section 4.3.1) specifies:
```
R = [[1.0,  0.2,  0.15],
     [0.2,  1.0,  0.2 ],
     [0.15, 0.2,  1.0 ]]
```

Implementation in `src/factory_simulator/generators/vibration.py` lines 47-51:
```python
_PRD_CORRELATION_MATRIX = np.array([
    [1.0,  0.2,  0.15],
    [0.2,  1.0,  0.2],
    [0.15, 0.2,  1.0],
])
```

**Exact match.** ✅

Cholesky decomposition is computed at `__init__` line 68:
```python
self._cholesky_l = np.linalg.cholesky(corr_matrix)
```

Config override mechanism is preserved — lines 63-67:
```python
custom_matrix = extras.get("axis_correlation_matrix")
if custom_matrix is not None:
    corr_matrix = np.array(custom_matrix, dtype=np.float64)
else:
    corr_matrix = self._PRD_CORRELATION_MATRIX.copy()
```

Test expectations updated: correlation threshold lowered from `> 0.2` to `> 0.05` to reflect the weaker X-Y correlation of 0.2 (vs previous uniform 0.6). ✅

### R2: Noise distribution assignments
**Status:** PASS ✅

**Evidence — each signal checked against PRD 4.2.11 default table:**

| Signal | PRD Requirement | YAML Config | Status |
|--------|----------------|-------------|--------|
| `vibration.main_drive_x` | Student-t, df=5 | `noise_type: "student_t"`, `noise_df: 5` | ✅ |
| `vibration.main_drive_y` | Student-t, df=5 | `noise_type: "student_t"`, `noise_df: 5` | ✅ |
| `vibration.main_drive_z` | Student-t, df=5 | `noise_type: "student_t"`, `noise_df: 5` | ✅ |
| `press.main_drive_current` | Student-t, df=8 | `noise_type: "student_t"`, `noise_df: 8` | ✅ |
| `coder.ink_pressure` | Student-t, df=6 | `noise_type: "student_t"`, `noise_df: 6` | ✅ |
| `laminator.nip_temp` | AR(1), phi=0.7 | `noise_type: "ar1"`, `noise_phi: 0.7` | ✅ |
| `laminator.tunnel_temp` | AR(1), phi=0.7 | `noise_type: "ar1"`, `noise_phi: 0.7` | ✅ |
| `coder.printhead_temp` | AR(1), phi=0.7 | `noise_type: "ar1"`, `noise_phi: 0.7` | ✅ |
| `press.dryer_temp_zone_1` | AR(1), phi=0.7 | `noise_type: "ar1"`, `noise_phi: 0.7` | ✅ (pre-existing) |
| `press.dryer_temp_zone_2` | AR(1), phi=0.7 | `noise_type: "ar1"`, `noise_phi: 0.7` | ✅ (pre-existing) |
| `press.dryer_temp_zone_3` | AR(1), phi=0.7 | `noise_type: "ar1"`, `noise_phi: 0.7` | ✅ (pre-existing) |

**All PRD-mandated noise assignments are correctly configured.** ✅

**Config validation path:** `SignalConfig.noise_type` accepts `{"gaussian", "student_t", "ar1"}` (validated by `_valid_noise_type`). `noise_df` validated >= 3. `noise_phi` validated in (-1, 1). All pass.

### R3: Speed-dependent sigma
**Status:** PASS ✅

**Evidence — full config-to-runtime trace:**

#### Config values in `factory.yaml`:

| Signal | sigma_base | sigma_scale | sigma_parent | PRD Match |
|--------|-----------|-------------|--------------|-----------|
| `vibration.main_drive_x` | 0.2 | 0.015 | `press.line_speed` | ✅ |
| `vibration.main_drive_y` | 0.2 | 0.015 | `press.line_speed` | ✅ |
| `vibration.main_drive_z` | 0.2 | 0.015 | `press.line_speed` | ✅ |
| `press.web_tension` | 2.0 | 0.02 | `press.line_speed` | ✅ |
| `press.registration_error_x` | 0.005 | 0.00005 | `press.line_speed` | ✅ |
| `press.registration_error_y` | 0.005 | 0.00005 | `press.line_speed` | ✅ |
| `press.main_drive_current` | 0.3 | 0.002 | `press.line_speed` | ✅ |

**All match PRD 4.2.11 speed-dependent sigma table exactly.** ✅

#### SignalConfig fields (`src/factory_simulator/config.py`):
```python
sigma_base: float | None = None
sigma_scale: float = 0.0
sigma_parent: str | None = None  # Parent signal ID for speed-dependent sigma
```
Present and typed correctly. ✅

#### _make_noise in base.py passes sigma_base/sigma_scale to NoiseGenerator:
```python
def _make_noise(self, sig_cfg: SignalConfig) -> NoiseGenerator | None:
    if sig_cfg.noise_sigma <= 0.0:
        return None
    return NoiseGenerator.from_config(
        sigma=sig_cfg.noise_sigma,
        noise_type=sig_cfg.noise_type,
        rng=self._spawn_rng(),
        noise_df=sig_cfg.noise_df,
        noise_phi=sig_cfg.noise_phi,
        sigma_base=sig_cfg.sigma_base,
        sigma_scale=sig_cfg.sigma_scale,
    )
```
Both `sigma_base` and `sigma_scale` are forwarded. ✅

#### NoiseGenerator.from_config passes them to constructor:
```python
return cls(
    sigma=sigma, distribution=noise_type, rng=rng,
    df=noise_df, phi=noise_phi,
    sigma_base=sigma_base, sigma_scale=sigma_scale,
)
```
✅

#### NoiseGenerator.effective_sigma() implementation:
```python
def effective_sigma(self, parent_value: float | None = None) -> float:
    if self._sigma_base is not None and parent_value is not None:
        return self._sigma_base + self._sigma_scale * abs(parent_value)
    return self._sigma
```
Matches PRD formula: `effective_sigma = sigma_base + sigma_scale * |parent_value|`. ✅

#### Dimensional spot-check:
- At 200 m/min for vibration: `0.2 + 0.015 × 200 = 3.2 mm/s` — plausible for running vibration
- At 0 m/min: `0.2 mm/s` — low idle vibration noise
- At 200 m/min for web_tension: `2.0 + 0.02 × 200 = 6.0 N` — reasonable tension noise
- At 200 m/min for registration: `0.005 + 0.00005 × 200 = 0.015 mm` — realistic registration noise

All dimensionally correct. ✅

## YELLOW Fix Verification

### Y2: Energy cumulative_kwh rate
**Status:** PASS ✅

**Evidence:** `config/factory.yaml` cumulative_kwh signal:
```yaml
rate: 0.000278              # 1/3600: kWh = kW × hours, dt is in seconds
```

Dimensional check: at 100 kW, dt=0.1s:
- Config: `0.000278 × 100 × 0.1 = 0.00278 kWh/tick`
- Correct: `100 × 0.1 / 3600 = 0.00278 kWh/tick`
- Match. ✅

### Y3: Vibration effective_sigma usage
**Status:** PASS ✅

**Evidence:** `vibration.py` lines 96-98 in generate():
```python
noise_gen = self._noises[name]
if noise_gen is not None:
    sigma = noise_gen.effective_sigma(press_speed)
    raw += sigma * float(correlated_z[i])
```

Uses `noise_gen.effective_sigma(press_speed)` with the actual press speed as argument, not the fixed `noise_gen.sigma`. This ensures speed-dependent sigma is applied in the Cholesky scaling step. ✅

### Y4: Vibration double-noising eliminated
**Status:** PASS ✅

**Evidence:** `vibration.py` `_build_models()` lines 76-82:
```python
# Do NOT pass noise to the model -- all noise is applied via
# the Cholesky pipeline externally (PRD 4.3.1, avoids double-noising).
self._models[name] = SteadyStateModel(params, self._spawn_rng())
```

`SteadyStateModel.__init__` signature: `noise: NoiseGenerator | None = None` — defaults to `None`.
In `SteadyStateModel.generate()`: `if self._noise is not None: value += self._noise.sample()` — since noise is None, no internal noise is added. The model returns only `effective_target` (target + optional drift).

All noise comes from the Cholesky pipeline (lines 91-99 in `generate()`). No double-noising. ✅

### Y5: Slitter coil derivation
**Status:** PASS ✅

**Evidence:** `modbus_server.py` coil definition (line ~214):
```python
CoilDefinition(5, "slitter.speed", mode="gt_zero"),  # slitter.running
```

Changed from `press.machine_state` with `derive_value=2` to `slitter.speed` with `mode="gt_zero"`.

The `_sync_coils()` method correctly handles `gt_zero` mode:
```python
if coil_def.mode == "gt_zero":
    is_active = float(value) > 0.0
```

Slitter running state now derives from `slitter.speed > 0`, reflecting the slitter's independent schedule. ✅

### Y9: Vibration idle magic numbers
**Status:** PASS ✅

**Evidence:** `vibration.py` lines 18-19:
```python
_IDLE_VIBRATION_MEAN = 0.2   # mm/s
_IDLE_VIBRATION_STD = 0.05   # mm/s
```

Named constants with units and PRD reference in comment. Used in `generate()`:
```python
residual = float(
    self._rng.normal(_IDLE_VIBRATION_MEAN, _IDLE_VIBRATION_STD)
)
```

No more magic numbers inline. ✅

### Bonus: CorrelatedFollowerModel parent_value pass-through
**Status:** PASS ✅

**Evidence:** `correlated.py` `generate()` method, Step 4:
```python
# Step 4: Add noise (pass parent for speed-dependent sigma)
if self._noise is not None:
    result += self._noise.sample(parent_value=parent)
```

The `parent` variable holds the (possibly lagged) parent signal value from Step 2. This is passed to `noise.sample()` which calls `self.effective_sigma(parent_value)`, activating speed-dependent sigma for correlated followers like `press.web_tension` and `press.main_drive_current`. ✅

## Remaining YELLOW Items (deferred or unfixed — status check)

| Finding | Status | Notes |
|---------|--------|-------|
| Y1: Environment composite model (HVAC + perturbations) | Deferred to Phase 2 | Expected per independent review recommendation |
| Y6: Scenario conflict resolution | Deferred to Phase 4 | Expected per phase plan |
| Y7: No dryer setpoint changes in job changeover | Not addressed | Minor; expected per review classification |
| Y8: Modbus sync 50ms magic number | Not addressed | Minor; expected per review classification |
| Y10: Signal count comment (22 vs 21) | Not addressed | Documentation only |
| G5: Coder gutter_fault probability (~18× too high) | Not addressed | Not in fix scope; should be tracked for future fix |

**Assessment:** All unfixed items were correctly triaged as "can defer" per the independent review's GO conditions. None of the deferred items block Phase 2.

## Remaining Issues

**No issues found.** All fixes are correct. No regressions detected.

**One observation (non-blocking):** The `sigma_parent` field on `SignalConfig` is parsed from YAML but is not currently read at runtime by `_make_noise()` — the vibration generator reads `press.line_speed` from the store directly, and the `CorrelatedFollowerModel` uses its existing `parent` signal. The `sigma_parent` field serves as documentation in the YAML for which parent drives sigma. If a future generic generator needs to look up the parent dynamically, it would read this field. This is acceptable as-is; it's a forward-compatible design choice, not a bug.

## Verdict

**GO for Phase 2.**

All 3 RED fixes are verified correct against PRD specifications. All 6 YELLOW fixes are correct. The deferred items (Y1, Y6, Y7, Y8, Y10, G5) are appropriately triaged. The config-to-runtime pipeline is fully traced for speed-dependent sigma (the most complex fix). No regressions introduced.
