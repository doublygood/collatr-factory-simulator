# Phase 1 Code Review

**Date:** 2026-03-02
**Scope:** All 38 source files in `src/factory_simulator/`, all tests, config, PRD compliance
**Reviewers:** 4 parallel sub-agent reviewers (signal models, Modbus registers, CLAUDE.md rules, error handling)

## Summary

| Category | PASS | YELLOW | RED |
|----------|------|--------|-----|
| Signal Model PRD Compliance (12 models + noise + quantisation) | 13 | 1 | 1 |
| Modbus Register Map (Appendix A) | 26+ checks | 6 | 1 |
| CLAUDE.md Rules (7 rules audited) | 6 | 1 | 0 |
| Error Handling & Edge Cases | Most areas | 3 | 1 |
| **Total** | **~48** | **11** | **3** |

---

## RED Findings (Must Fix)

### R1: `press.fault_code` (HR 211) not in factory.yaml

**Location:** `config/factory.yaml` (press equipment signals section)
**Impact:** Fault codes written by the `unplanned_stop` scenario to the store key `press.fault_code` are never synced to Modbus HR 211. A client reading HR 211 always sees 0 regardless of fault state. The integration test passes only because the data block is zero-initialized, masking the bug.

**Fix:** Add `fault_code` as a signal in the press equipment config with `modbus_hr: [211]`, `modbus_type: "uint16"`.

### R2: Thermal diffusion decay denominator -- `4*L^2` vs PRD `L^2`

**Location:** `src/factory_simulator/models/thermal_diffusion.py` lines 120-121
**PRD formula:** `exp(-(2n+1)^2 * pi^2 * alpha * t / L^2)`
**Implementation:** `alpha_over_4L2 = self._alpha / (4.0 * self._L**2)`

**Analysis:** The PRD defines L as "half-thickness" (0.025m). The standard Fourier solution for a slab with symmetric BCs and half-thickness L has decay constant `(2n+1)^2 * pi^2 * alpha / (4 * L^2)`. The PRD formula omits the factor of 4, which would produce unrealistically fast heating (~4 min instead of ~15-20 min). The implementation is physically correct and matches the PRD's stated timing expectation.

**Resolution:** This is a PRD notation error, not a code bug. The implementation's docstring (lines 12-15) already documents the deviation. Per Rule 4, we document this as a known PRD clarification in the progress file. The code is correct. **No code change needed.**

### R3: `clamp()` does not handle NaN

**Location:** `src/factory_simulator/models/base.py` line 85
**Impact:** Under IEEE 754, `NaN < min_clamp` evaluates to `False`, so NaN passes through unclamped. If a signal generates NaN (e.g., from bad inputs), it would propagate to Modbus registers.

**Fix:** Add NaN check at the top of `clamp()`. Return `min_clamp` (or `0.0` if no bounds) when value is NaN.

---

## YELLOW Findings (Minor, Not Blocking)

### Signal Models
- **Y1:** Depletion model uses `speed * dt` instead of PRD's `prints_delta`. Mathematically equivalent -- generalises the driver. No fix needed.

### Modbus
- **Y2:** Coils 2 (emergency_stop) and 3 (web_break) always False. Acceptable Phase 1 simplification.
- **Y3:** Coils 4 (laminator.running) and 5 (slitter.running) derived from press state, not independent equipment state. Acceptable until independent state machines are added.
- **Y4:** Idle timeout (60s per PRD 3.1) not configured. Not in Phase 1 scope.
- **Y5:** Connection limits not enforced. Not in Phase 1 scope, relevant for F&B multi-slave.
- **Y6:** Profile address isolation (exception 0x02 for 1000-1999) not implemented. Not needed until F&B.
- **Y7:** No Hypothesis property-based tests for Modbus encoding functions.

### CLAUDE.md Rules
- **Y8:** Magic numbers: ambient temperature 20.0 hardcoded in press.py/laminator.py, Modbus sync interval 0.05s, vibration floor noise (0.2, 0.05), emergency ramp-down 30.0s. Should be named constants with PRD references.

### Error Handling
- **Y9:** Scenarios access generator private attributes directly (e.g., `gen._state_machine`).
- **Y10:** Missing NaN injection test for signal pipeline.

---

## PASS Highlights

- **All 12 signal model formulas** match PRD exactly (except thermal diffusion notation, documented above)
- **Cholesky pipeline** follows exact PRD 4.3.1 ordering: generate N(0,1) -> apply L -> scale by sigma
- **Sensor quantisation** implemented once in base.py, not duplicated per model
- **No wall clock usage** in any signal generation code (Rule 6)
- **Engine tick() is synchronous** -- no await between signal updates (Rule 8)
- **No locks anywhere** in src/ (Rule 9)
- **No Python `random` module** -- all randomness via numpy.random.Generator with SeedSequence (Rule 13)
- **No global mutable state** -- all components instantiated per-profile (Rule 12)
- **All 26 packaging HR addresses** correctly mapped with proper encoding
- **Float32 ABCD encoding** verified at byte level
- **FC06 rejection** implemented per PRD 3.1.2 with correct exception code
- **125-register read limit** implemented per PRD 3.1.7
- **1078 tests passing**, comprehensive Hypothesis property-based testing across all models

---

## Fixes Applied

- [x] R1: Added `fault_code` signal to press equipment in factory.yaml
- [x] R3: Added NaN guard to `clamp()` in base.py
- [ ] R2: Documented as PRD notation issue (no code change needed)
- [x] Y8 (partial): Extracted ambient temperature to named constant `_AMBIENT_TEMP_C`
