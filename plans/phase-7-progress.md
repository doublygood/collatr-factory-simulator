# Phase 7: Polish — Progress

## Status: NOT STARTED

## Tasks
- [ ] 7.1: Fix MQTT Retry Delays Tuple (CQ-Y1)
- [ ] 7.2: Extract SIGTERM Handler Context Manager (CQ-Y2)
- [ ] 7.3: Extract OPC-UA Node Creation Helper (CQ-Y3)
- [ ] 7.4: Guard Overlapping OPC-UA Node Paths + Test (CQ-Y4) — depends on 7.3
- [ ] 7.5: Remove Dead FactoryInfo.timezone Field (G-Arch21)
- [ ] 7.6: Elevate OPC-UA Error Log Levels (G-Arch23)
- [ ] 7.7: Return Defensive Copy from store.get_all() (G-Arch24)
- [ ] 7.8: Add I/O Error Handling in Ground Truth _write_line (G-Arch26)
- [ ] 7.9: Rename float32_hr_addresses to dual_register_hr_addresses (G-Proto8)
- [ ] 7.10: Derive Modbus Update Interval from Config (G-Proto10)
- [ ] 7.11: Improve _compute_block_size Documentation (G-Proto13)
- [ ] 7.12: Explicit line_id + ShiftChange HH:MM Validator (G-Proto14 + G-Arch-ShiftChange)
- [ ] 7.13: CI fail-fast: false + Validate All Fixes

## Notes

Phase 7 addresses 4 new YELLOWs from the post-Phase 6 code quality review plus 9 actionable GREENs from the original three-reviewer deep review.

12 GREEN items were deliberately skipped as not worth the effort or risk:
- G-Arch22 (start_time vs protocol epoch) — confusing but correct
- G-Arch25 (clock default 2024 vs epoch 2026) — documenting is enough
- G-Proto9 (OPC-UA namespace assertion) — extremely unlikely edge case
- G-Proto11 (MQTT 100ms sleep granularity) — acceptable real-world jitter
- G-Proto12 (clock drift direction) — config-dependent, works correctly
- G-Sig-G1 through G-Sig-G8 — all working as designed or negligible impact
- CQ-G5 through CQ-G10 — all correct or low-value documentation changes (except CQ-G9 which is task 7.9)
