# Implementation Review Round 2 - Consolidated Issues

**Date:** 2026-03-02
**Reviewers:** Lead Python Developer (A-), IIoT/Python Expert (A)
**PRD Version:** 1.1 (post-implementation-review updates)
**Previous Round:** 52 issues, all resolved

---

## Summary

| Severity | Lead Python Dev | IIoT Expert | Unique Total | Resolved |
|----------|----------------|-------------|--------------|----------|
| HIGH     | 0              | 0           | 0            | 0        |
| MEDIUM   | 3 (2 retracted) + 2 | 4      | 7            | 7        |
| LOW      | 9              | 7           | 12 (unique)  | 12       |
| **Total** | **14**        | **11**      | **19**       | **19**   |

Both reviewers: **Ship it.** All issues resolved in this pass.

---

## MEDIUM Issues

### R2-M1: Oven output power signal missing (BOTH reviewers)
**Lead Dev NEW-M3, IIoT M1**
Section 3.1.6 defines IR 2 = output power for Eurotherm zones, Appendix F Phase 3 references it, but no signal definition, range, or register map entry existed.
**Resolution:** Added `oven.zone_1/2/3_output_power` to Section 2b (F&B signal list), Appendix A (register map), updated Appendix F Phase 3. Correlated follower of temperature error, 0-100%, int16 x10 scaling matching Eurotherm convention.

### R2-M2: mixer.mix_time_elapsed / cip.cycle_time_elapsed data type conflict (Lead Dev NEW-M1)
Section 3.1.2 said float32, Appendix A said uint32.
**Resolution:** Aligned to uint32 in Section 3.1.2. Elapsed time is a monotonic counter, not a continuous measurement.

### R2-M3: Signal summary table Section 2.11 miscounts (Lead Dev NEW-M4/M5)
press.web_tension and slitter.web_tension listed as "OPC-UA only" but both have Modbus HR mappings. slitter.reel_count also dual-protocol.
**Resolution:** Updated Section 2.2 protocol column and Section 2.11 summary: OPC-UA only 4->2, Modbus+OPC-UA 8->11.

### R2-M4: Mosquitto config file content missing (IIoT M2)
Mosquitto 2.x defaults to rejecting anonymous connections and binding to localhost only. No sample config in PRD.
**Resolution:** Added Mosquitto configuration section to Section 6 with minimal config (listener, allow_anonymous, persistence, log_dest).

### R2-M5: paho-mqtt client-side buffering ambiguity (IIoT M3)
Section 4.8 said "if the broker session persists" which could be read as broker-side persistence.
**Resolution:** Clarified in Section 4.8: buffer is explicitly client-side in simulator, clean_session=True, broker-side persistence not required.

### R2-M6: pymodbus version pinning too loose (IIoT M4)
`>=3.6` risks breaking changes from 3.7+ API churn.
**Resolution:** Pinned to `>=3.6,<4.0` in Section 7.3. Exact version pinned in requirements.txt during Phase 1.

### R2-M7: Phantom chiller.compressor_power signal (Lead Dev NEW-L7, elevated to medium)
Referenced in Sections 4.6, 5.14.5, 5.14.7 but doesn't exist in signal list. Only chiller.compressor_state (bool) exists.
**Resolution:** All references updated to use chiller.compressor_state. Compressor effort communicated via cycle frequency, not continuous power signal.

---

## LOW Issues

### R2-L1: Section 3.3 stale embedded broker language (BOTH)
**Resolution:** Updated to reference Mosquitto sidecar via paho-mqtt.

### R2-L2: Docker Compose version: "3.8" deprecated (BOTH)
**Resolution:** Removed version key from Docker Compose example.

### R2-L3: Appendix E missing model files (BOTH)
Missing thermal_diffusion.py, bang_bang.py, string_generator.py.
**Resolution:** Added all three to project structure listing.

### R2-L4: Appendix E missing scenario files (IIoT L5)
Missing micro_stop.py, material_splice.py, cold_chain_break.py.
**Resolution:** Added all three to scenarios directory listing.

### R2-L5: Dockerfile EXPOSE 1883 (IIoT L2)
Port 1883 is now in Mosquitto sidecar, not simulator container.
**Resolution:** Removed 1883 from EXPOSE line.

### R2-L6: Vibration retain flag blanket statement (IIoT L6)
Section 3.3.8 said all topics use retained flag, contradicting vibration topics.
**Resolution:** Added exception clause for vibration topics.

### R2-L7: asyncua version lower bound (IIoT L7)
`>=1.1` too low; Python 3.12 stability from 1.1.5.
**Resolution:** Updated to `>=1.1.5`.

### R2-L8: sealer.reject_count phantom signal (Lead Dev NEW-L8)
Referenced in Section 5.14.4 but doesn't exist.
**Resolution:** Changed to qc.reject_total (downstream quality station).

### R2-L9: Slitter scheduling params missing from Appendix D (Lead Dev NEW-L6)
schedule_offset_hours and run_duration_hours not in config reference.
**Resolution:** Added Equipment Scheduling section to Appendix D.

### R2-L10: EnumStrings SHOULD vs MUST (Lead Dev NEW-L3)
Appendix B stated definitively but I-L20 said contingent on asyncua.
**Resolution:** Changed to SHOULD with fallback note and 0.5 day budget.

### R2-L11: README version/status (Lead Dev NEW-L9)
Still said Version 1.0, Status Draft.
**Resolution:** Updated to Version 1.1, Status Implementation-Ready.

### R2-L12: Appendix F stale oven output power note
Referenced "to be updated" but now fully specified.
**Resolution:** Updated to reference the new signal definitions.

---

## Grand Total (All Rounds)

| Round | Issues | Resolved |
|-------|--------|----------|
| Expert R1 | 32 | 32 |
| Expert R2 | 27 | 27 |
| Expert R3 | 4 | 4 |
| Implementation R1 | 52 | 52 |
| Implementation R2 | 19 | 19 |
| **Total** | **134** | **134** |
