# Phase 6c: Validation & Protocol Polish — Progress

## Status: IN PROGRESS

## Tasks
- [x] 6c.1: SignalConfig min_clamp <= max_clamp Validator (Y9)
- [ ] 6c.2: ClockDriftConfig Allow Negative Values (Y10)
- [ ] 6c.3: Fix Calibration Drift Rate Docstring (Y11)
- [ ] 6c.4: Fix Random Walk Docstring (Y12)
- [ ] 6c.5: Dryer Zone Cholesky Correlation (Y13)
- [ ] 6c.6: Oven Zone Cholesky Correlation (Y13)
- [ ] 6c.7: Coil 4 Derivation Fix (Y14)
- [ ] 6c.8: OPC-UA MinimumSamplingInterval (Y15)
- [ ] 6c.9: Validate All Fixes — Full Suite

## Notes

Tasks 6c.1-6c.8 are all independent (no dependencies between them). Task 6c.9 depends on all others.

Tasks 6c.5 and 6c.6 share the same pattern (Cholesky noise correlation for zone temperatures). Do 6c.5 first; 6c.6 follows the same approach.

Tasks 6c.3 and 6c.4 are documentation-only (no logic changes).

## Task 6c.1 — SignalConfig min_clamp <= max_clamp Validator

**Completed.** Added `@model_validator(mode="after")` `_clamp_order` to `SignalConfig` (config.py line 302). Raises `ValueError` when both `min_clamp` and `max_clamp` are set and `min_clamp > max_clamp`. Single-sided clamps, equal values, and both-None all pass validation.

6 new tests added to `TestSignalConfig` in `tests/unit/test_config.py`: valid ordering, equal, reversed (rejected), min-only, max-only, neither.

Suite: 3030 passed, ruff + mypy clean.
