# Phase 6d: Maintenance & CI — Progress

## Status: NOT STARTED

## Tasks
- [ ] 6d.1: Shared Reference Epoch Constant (Y18)
- [ ] 6d.2: _format_time() Performance Fix (Y17) — depends on 6d.1
- [ ] 6d.3: Configurable Health Server Port (Y16)
- [ ] 6d.4: Server Task Verification After Startup (Y20)
- [ ] 6d.5: Narrow Exception Suppression During Shutdown (Y27)
- [ ] 6d.6: Dead Config Cleanup — sparkplug_b, retain (Y22+Y23)
- [ ] 6d.7: Generator Tests: Coder (Y19)
- [ ] 6d.8: Generator Tests: Energy (Y19)
- [ ] 6d.9: Generator Tests: Laminator (Y19)
- [ ] 6d.10: Generator Tests: Slitter (Y19)
- [ ] 6d.11: Generator Tests: Vibration (Y19)
- [ ] 6d.12: CI Matrix: Python 3.13 + Integration Tests (Y21)
- [ ] 6d.13: Validate All Fixes — Full Suite

## Notes

Only dependency: 6d.2 depends on 6d.1 (shared epoch must exist before ground_truth uses it).
All other tasks are fully independent.

Y24 (Dockerfile editable install) was already fixed in Phase 6a — skipped.
Y25 (inactive profile nodes) and Y26 (LWT topic) moved to Phase 6e.

Generator test files (6d.7-6d.11) follow the existing pattern in test_mixer.py, test_press.py:
helpers to create minimal config, run N ticks, assert expected behaviour.
