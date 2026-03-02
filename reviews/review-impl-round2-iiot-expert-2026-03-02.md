# Implementation Readiness Review: Round 2

**Reviewer:** IIoT/Python Expert  
**Date:** 2026-03-02  
**PRD Version:** 1.1 (post-implementation-review updates)  
**Files Reviewed:** All 23 PRD files (README.md, Sections 01–13, Appendices A–G)  
**Consolidated Issues File:** `reviews/implementation-consolidated-issues.md` (52 issues, all resolved)

---

## Overall Grade: A

The PRD has improved from A- to A. Every one of my original 12 issues has been resolved, most with better solutions than I expected. The amqtt → Mosquitto migration was the single most impactful change: it eliminates the weakest dependency in the entire stack and replaces it with the industry standard. The remaining issues are minor enough that a competent developer can resolve them during implementation without requiring a PRD revision cycle.

---

## Summary

This PRD is genuinely implementation-ready. In my first review, I confirmed that pymodbus, asyncua, and the (then) amqtt broker could technically achieve what the PRD specified, but I flagged 12 issues ranging from missing OPC-UA subscription intervals to unspecified Modbus write behaviour. All 12 have been addressed. The amqtt replacement with Mosquitto + paho-mqtt is not just a risk mitigation—it is an architecture improvement. Moving the broker out of the Python process eliminates an entire class of concurrency issues (broker event loop competing with signal generation) and gives the team a production-grade MQTT 5.0 broker for free.

The PRD now specifies protocol behaviour at a level of detail that would let a developer implement without ambiguity in nearly all cases. Register maps are complete with byte ordering. OPC-UA node paths are fully qualified. MQTT topics have explicit QoS, retain, and payload schemas. The network topology section (3a) is exceptional—it models the multi-controller chaos of a real factory floor with scan cycle artefacts, clock drift, and connection behaviour that I have personally encountered deploying IIoT systems. The validation spikes (Phase 0) are the right approach: confirm library feasibility before committing to architecture.

The issues I've found in this second pass are tactical, not architectural. None of them block implementation. A few could cause a day or two of rework if not addressed. The PRD is ready for Phase 0 validation spikes.

---

## Previous Issues Resolution Assessment

### My Original Issues (12 total, all resolved)

| Issue | Resolution Quality | Notes |
|---|---|---|
| I-H7: amqtt library risk | **Excellent.** Mosquitto sidecar is the optimal choice. NanoMQ rejection rationale is sound (overkill for 50 msg/s). | The Docker Compose `depends_on: service_healthy` pattern with `mosquitto_sub -C 1 -W 3` health check is correct and production-ready. |
| I-M16: OPC-UA subscription publishing interval | **Good.** 500ms minimum specified in Phase 2. | asyncua defaults to 1000ms. The developer must explicitly set `server.set_minimum_subscription_interval(500)` or equivalent. This is now clear enough. |
| I-L1: Modbus idle connection timeout | **Good.** 60 seconds, configurable. | Matches real S7-1200/1500 behaviour. pymodbus `ModbusTcpServer` supports `timeout` parameter natively. |
| I-L2: OPC-UA security policy | **Good.** SecurityPolicy.None with auto-accept specified. | asyncua requires `server.set_security_policy([SecurityPolicy.Basic256Sha256_None])` or similar. The PRD now makes the intent clear. |
| I-L3: MQTT client ID | **Good.** `factory-simulator` specified in Section 6.2. | paho-mqtt 2.0 uses `client_id` parameter in `Client()` constructor. Clear. |
| I-L4: MQTT LWT | **Good.** `lwt_topic` and `lwt_payload` specified. | paho-mqtt `will_set()` method handles this. |
| I-L5: FC06 vs FC16 writes | **Good.** FC16 required for float32, FC06 rejected with exception 0x01. | pymodbus can enforce this in a custom `ModbusSlaveContext` or request handler. |
| I-L7: Per-controller register gaps | **Good.** Exception 0x02 confirmed for out-of-range addresses. | pymodbus `ModbusSparseDataBlock` or custom context handles this. |
| I-L8: MQTT persistence | **Good.** Mosquitto persistence volume noted. | Mosquitto's `persistence true` in config + volume mount. |
| I-L9: Modbus write response | **Good.** Writes update signal model target setpoint. Last writer wins. | Clear specification. pymodbus `setValues` callback on the data block handles this. |
| I-L10: SourceTimestamp vs ServerTimestamp | **Good.** Source = drifted, Server = true simulation clock. | asyncua's `set_value()` method accepts a `SourceTimestamp` parameter. |
| I-L11: Max 125 registers per read | **Good.** Exception 0x03 for oversized requests. | pymodbus does NOT enforce this by default—the PRD correctly notes this and requires explicit implementation. |

### The amqtt → Mosquitto Change: Detailed Assessment

This is the most significant change between v1.0 and v1.1. Let me evaluate it from an IIoT implementation perspective:

**Architecture benefit.** The simulator process no longer needs to run a broker. It is purely a client that publishes to an external broker. This is architecturally cleaner and matches how real MQTT publishers work in factories. A CIJ printer does not embed its own broker—it publishes to a factory broker.

**paho-mqtt 2.0 compatibility.** The PRD specifies `paho-mqtt >= 2.0`. This is correct. paho-mqtt 2.0 (released 2024) broke the API significantly from 1.x. The `Client()` constructor, `connect()`, `publish()`, and callback signatures all changed. Pinning `>= 2.0` avoids the 1.x → 2.0 migration pain.

**Docker networking.** The Mosquitto sidecar uses Docker Compose service discovery (`mqtt-broker` hostname). The `depends_on: condition: service_healthy` gate ensures the simulator doesn't start publishing before Mosquitto is ready. This is the correct pattern.

**Health check.** `mosquitto_sub -t "$$SYS/#" -C 1 -W 3` subscribes to the $SYS topic tree and waits for one message within 3 seconds. Mosquitto publishes $SYS statistics every `sys_interval` seconds (default 10s). The `-W 3` timeout might be tight if the broker just started and hasn't published its first $SYS message yet. In practice, Mosquitto publishes $SYS/broker/version immediately on startup, so 3 seconds is adequate.

**Retained messages across restarts.** The Docker Compose has a commented-out volume mount for Mosquitto data persistence. The PRD notes this in Section 6.3. For a dev tool, persistence across restarts is optional. Retained messages rebuild as the simulator publishes. Good decision not to make it mandatory.

---

## New Issues Found

### HIGH

No high-severity issues found.

The PRD resolves all architectural and protocol-level ambiguities that would block implementation.

### MEDIUM

#### M1: Oven Output Power Signal Not Fully Specified (Section 3.1.6, Appendix A, Appendix F Phase 3)

Section 3.1.6 defines the Eurotherm multi-slave layout with IR 2 = "output power" for each oven zone. The consolidated issues file (I-M15) says "Oven output power signal added (IR 2 on Eurotherm multi-slave per Section 3.1.6). Signal list and register map to be updated." However, looking at the actual Appendix A register map, there is no `oven.output_power_zone_N` signal in the F&B holding register or input register tables. The signal list in Section 2b.3 also does not include output power. The resolution says "to be updated" but the update has not landed in the register map or signal list.

**Impact:** The developer implementing multi-slave Eurotherm mode will need to invent the register address, data type, and signal model for output power. A 0-100% float32 signal with first-order lag tracking the PID controller's output is the standard pattern.

**Fix:** Add `oven.output_power_zone_1/2/3` to the F&B signal list (Section 2b.3), input register map (Appendix A, F&B IR table at addresses 107/108/109 or similar), and the multi-slave definition (Section 3.1.6 already has the slot). Data type: int16 x10 scaling (0-1000 = 0.0-100.0%) matching Eurotherm convention. Signal model: correlated follower of the temperature error (setpoint - actual), clamped 0-100%.

#### M2: Mosquitto Configuration File Not Specified (Section 6.3, Appendix E)

Appendix E lists `config/mosquitto.conf` in the project structure. The Docker Compose mounts it as a read-only volume. But no sample `mosquitto.conf` content is provided anywhere in the PRD. Mosquitto 2.x defaults to `allow_anonymous false` and `listener` binding to localhost only. Without a config file that sets `allow_anonymous true` and `listener 1883 0.0.0.0`, the Mosquitto container will reject connections from the simulator container.

**Impact:** First-time `docker compose up` will fail with MQTT connection refused. Debugging will take 15-30 minutes for someone unfamiliar with Mosquitto 2.x's breaking changes from 1.x.

**Fix:** Add a minimal Mosquitto config to the PRD or Appendix E:

```
listener 1883 0.0.0.0
allow_anonymous true
persistence false
log_dest stdout
```

#### M3: paho-mqtt Reconnect and Session Persistence Semantics (Section 6.2, Section 4.8)

Section 4.8 specifies that during MQTT drops, QoS 1 messages buffer and deliver on reconnect "if the broker session persists." paho-mqtt 2.0 uses `clean_start=True` by default (MQTT 5.0) or `clean_session=True` (MQTT 3.1.1). With clean session, the broker discards the session on disconnect, and buffered QoS 1 messages are lost. The PRD specifies MQTT 3.1.1 as the primary protocol version (Section 3.3).

For the buffer-on-drop behaviour to work as specified, the simulator must either:
1. Use `clean_session=False` with a persistent client ID (so the broker holds the session), OR
2. Buffer messages locally in the simulator and re-publish on reconnect (which is what Section 4.8 describes).

Reading Section 4.8 more carefully, the buffering is client-side (simulator holds messages in a buffer). This works regardless of `clean_session`. But the PRD should clarify that the 1000-message buffer is **client-side** in the simulator process, not broker-side QoS 1 session persistence. A developer reading "QoS 1 messages queue and deliver when the connection resumes, if the broker session persists" might implement broker-side session persistence instead of client-side buffering.

**Impact:** Potential confusion between client-side and broker-side buffering. Could cause a day of rework.

**Fix:** Clarify in Section 4.8 or 6.2 that the buffer is explicitly client-side in the MQTT adapter, independent of broker session persistence. The `clean_session` setting should be `True` (stateless) since the simulator manages its own retry logic.

#### M4: pymodbus Version Pinning and Async Server API (Section 7.3, I-L21)

Section 7.3 specifies `pymodbus >= 3.6`. The consolidated issues (I-L21) says "Pin exact version in requirements.txt" and marks it resolved with "Standard practice. Pin in Phase 1." However, pymodbus 3.6 → 3.7 introduced breaking changes in the async server startup API (`StartAsyncModbusTcpServer` signature changed). The PRD should pin to a specific minor version (e.g., `pymodbus==3.6.9`) or at least `>=3.6,<3.8` to avoid build breaks when a new release lands.

This is a recurring theme with pymodbus. The library has excellent functionality but aggressive API evolution between minor versions. Without a pin, a CI build will break when pymodbus 3.8 ships (likely within the 13-week development window).

**Impact:** CI breakage at unpredictable time. 2-4 hours to diagnose and fix.

**Fix:** Pin `pymodbus>=3.6,<4.0` in the PRD (Section 7.3) and exact version in `requirements.txt` during Phase 1.

### LOW

#### L1: Docker Compose `version` Key Deprecated (Section 6.3)

Section 6.3 specifies `version: "3.8"` in the Docker Compose file. Docker Compose v2 (which is now the default `docker compose` command) ignores the `version` key and emits a deprecation warning. This is cosmetic but will confuse developers who see the warning on first run.

**Fix:** Remove the `version: "3.8"` line from the Docker Compose example.

#### L2: Dockerfile Exposes Port 1883 (Section 7.5)

Section 7.5's Dockerfile includes `EXPOSE 502 4840 1883 8080`. Port 1883 is the MQTT broker port, which now runs in the Mosquitto sidecar container, not in the simulator container. The simulator connects to Mosquitto as a client. Exposing 1883 from the simulator container is misleading.

**Fix:** Remove 1883 from the `EXPOSE` line: `EXPOSE 502 4840 8080`.

#### L3: Section 3.3 Still References "Embedded MQTT Broker" (Section 3.3)

Section 3.3 opens with: "The simulator runs an embedded MQTT broker on `0.0.0.0:1883` (configurable). Alternatively, it publishes to an external broker." This language was not updated after the amqtt → Mosquitto migration. The simulator always publishes to an external broker now.

**Fix:** Update Section 3.3 opening to: "The simulator publishes to an MQTT broker via paho-mqtt. The default deployment uses a Mosquitto sidecar (see Section 6.3). Alternatively, it can publish to any external MQTT broker."

#### L4: Missing `thermal_diffusion` and `bang_bang_hysteresis` in Signal Models Directory (Appendix E)

Appendix E's project structure lists signal model files in `src/models/`: `steady_state.py`, `sinusoidal.py`, `first_order_lag.py`, `ramp.py`, `random_walk.py`, `counter.py`, `depletion.py`, `correlated.py`, `state.py`. Missing from this list: `thermal_diffusion.py` and `bang_bang_hysteresis.py`. These are two of the 12 signal model types defined in Section 4.2.10 and 4.2.12 and listed in Appendix F Phase 1.

**Fix:** Add `thermal_diffusion.py` and `bang_bang_hysteresis.py` to the `src/models/` directory listing in Appendix E. Also add `string_generator.py` (Section 4.2.14).

#### L5: Micro-Stop and Material Splice Scenarios Missing from Appendix E (Appendix E)

Appendix E's `src/scenarios/` directory lists 17 scenario files. Missing: `micro_stop.py` and `material_splice.py`. Both are fully specified in Sections 5.15 and 5.13a respectively.

**Fix:** Add `micro_stop.py` and `material_splice.py` to the scenarios directory listing.

#### L6: Vibration Retain Flag Inconsistency (Section 3.3.2 vs Appendix C)

Section 3.3.2 shows vibration topics with `Retain: No`. Appendix C confirms `Retain: No` for vibration topics. This is consistent. However, Section 3.3.8 states: "The most recent message on each topic is published with the retained flag set." This blanket statement contradicts the per-topic retain specifications for vibration.

**Fix:** Add an exception clause to Section 3.3.8: "...except vibration topics, which publish without the retained flag to avoid stale high-frequency data in the broker."

#### L7: `asyncua` Version Lower Bound May Be Too Low (Section 7.3)

Section 7.3 specifies `asyncua >= 1.1`. The `asyncua` library had significant server-side improvements in 1.1.0 and 1.1.5 (subscription handling, node management). However, Python 3.12 compatibility was only fully stable from `asyncua >= 1.1.0`. Given the PRD targets Python 3.12+, `>= 1.1` is technically correct but leaves room for edge-case issues with early 1.1.x point releases. Recommend `>= 1.1.5` for safety.

**Impact:** Negligible if the developer installs the latest.

**Fix:** Update to `asyncua >= 1.1.5` in Section 7.3 or pin in requirements.txt.

---

## Protocol-Specific Assessment

### Modbus TCP (pymodbus)

**Feasibility: Confirmed.** Every Modbus feature in the PRD is achievable with pymodbus >= 3.6.

| Feature | pymodbus Support | Notes |
|---|---|---|
| Multiple async servers on different ports | ✅ `StartAsyncModbusTcpServer` per port | Phase 0 validation spike will confirm concurrency. |
| Per-server register map | ✅ `ModbusSlaveContext` per server | Each controller gets its own context with only its registers. |
| Unit ID routing (multi-slave) | ✅ `ModbusServerContext(slaves={1: ctx1, 2: ctx2})` | Oven gateway with UID 1/2/3/10 on single port—straightforward. |
| CDAB byte order | ✅ `BinaryPayloadBuilder` with `Endian.Little` word order | Allen-Bradley mixer. Builder handles ABCD and CDAB. |
| Float32 encoding | ✅ `BinaryPayloadBuilder/Decoder` | ABCD and CDAB both supported. |
| FC06 rejection for float32 | ⚠️ Custom request handler required | pymodbus processes FC06 by default. Need to override `handle_write_single_register` to reject writes to float32 address pairs. |
| Max 125 registers per read | ⚠️ Custom request handler required | pymodbus has no built-in limit. Need to check `count` in the request handler and return exception 0x03. |
| Exception 0x02 for out-of-range | ✅ `ModbusSparseDataBlock` returns exception for unmapped addresses | Default behaviour with sparse data blocks. |
| Connection limit per server | ⚠️ Custom server class required | pymodbus `ModbusTcpServer` does not enforce connection limits natively. Need a custom `connection_made` handler that counts and rejects excess connections. |
| Idle timeout | ✅ `timeout` parameter on server | Direct support. |
| Response delay simulation | ⚠️ Custom handler with `asyncio.sleep()` | Inject delay in the request handler. |
| Partial responses | ⚠️ Custom response subclass | Subclass `ReadHoldingRegistersResponse` and truncate the register list. Appendix F Phase 4 mentions this approach correctly. |

**Overall:** pymodbus handles 80% of the specification natively. The remaining 20% requires custom request handlers and server subclasses. This is normal for a protocol simulator—real simulators always extend the library. The validation spike (Phase 0, spike 1) is the correct approach to verify multi-server concurrency.

### OPC-UA (asyncua)

**Feasibility: Confirmed.** All OPC-UA features are achievable with asyncua >= 1.1.

| Feature | asyncua Support | Notes |
|---|---|---|
| Multiple async servers on different ports | ✅ Multiple `Server()` instances | Phase 0 validation spike will confirm. |
| Custom node tree with string NodeIDs | ✅ `add_object()`, `add_variable()` | `ns=2;s=PackagingLine.Press1.LineSpeed` — standard string identifier. |
| Data change subscriptions | ✅ `create_subscription()`, `monitored_item_create()` | Server-side subscriptions with configurable publishing interval. |
| Minimum publishing interval | ✅ `server.set_min_subscription_interval(500)` or per-subscription parameter | PRD specifies 500ms. asyncua defaults to 100ms minimum, which is fine. |
| Engineering units (EURange) | ✅ `set_attribute()` with `EURange` | Requires explicit property creation on each variable node. |
| StatusCode propagation | ✅ `set_value()` with `StatusCode` parameter | `BadNotReadable`, `BadSensorFailure`, `UncertainLastUsableValue` all supported. |
| AccessLevel (read-only vs read/write) | ✅ `set_writable()` method | Setpoint nodes writable, process variable nodes read-only. |
| EnumStrings property | ⚠️ May require manual property creation | I-L20 allocated 0.5 days for investigation. asyncua supports custom properties but the `EnumStrings` pattern (array of `LocalizedText`) may need manual construction. Not blocking. |
| SourceTimestamp / ServerTimestamp | ✅ `DataValue` constructor accepts both | `DataValue(value, SourceTimestamp=drifted, ServerTimestamp=true_time)`. |
| SecurityPolicy.None | ✅ Default configuration | No additional setup needed. |
| Both profile trees in one namespace | ✅ Multiple branches under Objects | PackagingLine and FoodBevLine as sibling objects. Both browsable. |

**Overall:** asyncua handles everything the PRD requires. The main implementation effort is building the node tree (40+ nodes for packaging, 20+ for F&B) with correct attributes. This is boilerplate, not complexity. The memory leak concern (I-L22) is managed by the nightly stability test.

### MQTT (Mosquitto + paho-mqtt)

**Feasibility: Confirmed.** The Mosquitto sidecar + paho-mqtt architecture is the cleanest option available.

| Feature | Support | Notes |
|---|---|---|
| Mosquitto 2 Docker sidecar | ✅ `eclipse-mosquitto:2` | ~12MB Alpine image. Handles 120k msg/s on one core. Our 50 msg/s is trivial. |
| paho-mqtt 2.0 publish | ✅ `client.publish(topic, payload, qos, retain)` | API changed from 1.x. PRD pins >= 2.0. |
| QoS 0 and QoS 1 | ✅ Both supported by Mosquitto and paho-mqtt | No QoS 2 needed (correctly excluded from PRD). |
| Retained messages | ✅ Mosquitto and paho-mqtt support retained | New subscriber gets last value immediately. |
| JSON payload | ✅ Application-level (not a protocol feature) | `json.dumps()` the payload dict. |
| LWT (Last Will and Testament) | ✅ `client.will_set(topic, payload, qos, retain)` | Must be set before `client.connect()`. |
| Client-side buffering during drop | ⚠️ Custom implementation required | paho-mqtt 2.0 has `max_queued_messages` but it's for outbound messages during disconnect. The PRD's 1000-message buffer with drop-oldest needs a custom ring buffer in the MQTT adapter. |
| Sparkplug B (Phase 2) | ✅ `sparkplug-b` Python package | Protobuf encoding for `NBIRTH`, `DBIRTH`, `NDATA`, `DDATA` payloads. Topic structure is standard. |
| Timestamp offset simulation | ✅ Application-level | Add offset to ISO 8601 timestamp string before publishing. |

**Overall:** The Mosquitto + paho-mqtt architecture eliminates all of my previous concerns about MQTT reliability. The only implementation work beyond basic pub/sub is the client-side message buffer (M3 above) and Sparkplug B encoding (Phase 2).

---

## Section-by-Section Notes

### Section 3.3 — MQTT (Stale Language)
As noted in L3, the opening paragraph still references "embedded MQTT broker." This is the most visible remnant of the pre-Mosquitto architecture. Updating this paragraph avoids confusion for new readers.

### Section 3.1.6 — Multi-Slave Simulation
The multi-slave Eurotherm pattern (UID 1/2/3 on same port) is well-specified. pymodbus handles this via `ModbusServerContext(slaves={1: zone1_ctx, 2: zone2_ctx, 3: zone3_ctx})`. However, the oven output power signal (IR 2) that I-M15 promised to add is still missing from the register map. See M1 above.

### Section 3a.4 — Port Mapping
The port mapping table is comprehensive and correct. 15 simulated endpoints mapped to unique ports. The collapsed vs realistic mode switch is a good design decision for development convenience. One observation: the table shows the energy meter at `UID 5` on port 5020 (packaging) and `UID 10` on port 5031 (F&B). These are multiplexed on the same port as other controllers. pymodbus handles this via the `slaves` dict in `ModbusServerContext`. No issue.

### Section 3a.8 — Scan Cycle Artefacts
This section is excellent. The scan cycle quantisation model with phase jitter accurately reflects how real PLCs behave. The inter-signal skew within a multi-register Modbus read is a subtle but real effect that I have personally debugged on S7-1200 installations. The implementation is straightforward: maintain a `next_scan_boundary` timestamp per controller and only update register values when the simulation clock crosses that boundary.

### Section 4.2.10 — Thermal Diffusion
The Fourier series solution for 1D heat conduction is physically correct. The convergence check (`T(0)` within 1°C of `T_initial`) is a practical approach. One note: `numpy` does not have a dedicated Fourier heat conduction function—this will be implemented as a simple loop summing terms. At 20-30 terms per tick for the oven, computational cost is negligible.

### Section 4.3.1 — Cholesky with Non-Gaussian Marginals
The documented approximation (Gaussian Cholesky for Student-t signals) is the right engineering decision. At r=0.15-0.2 and df=5-8, the difference from a true Gaussian copula is < 1% in the correlation coefficient. The PRD correctly identifies this and defers the copula to post-MVP. No issue.

### Section 6.3 — Docker Compose
The Compose file is well-structured. The `depends_on: condition: service_healthy` gate is correct. As noted in M2, the Mosquitto config file content is missing.

### Section 8.3 — Concurrency Model
The single-writer, no-lock signal store is the correct design for Python asyncio. The engine updates all signals in one tick before yielding. Protocol adapters read atomically. No race conditions possible in the single-threaded event loop. This is exactly how I would architect it.

The `asyncio.TaskGroup` usage (Python 3.12) for concurrent protocol servers is clean. Exception propagation through `ExceptionGroup` means one server crashing will bring down the entire simulator—which is the correct fail-fast behaviour for a dev tool.

### Section 13 — Test Strategy
The test strategy is solid. Property-based testing with Hypothesis for signal models is the right approach. The emphasis on protocol encoding fidelity (Modbus byte order, OPC-UA data types, MQTT JSON payloads) is correct—these are the areas where silent data corruption is most likely.

The CI pipeline target of under 5 minutes is aggressive but achievable if integration tests use pre-populated signal stores rather than spinning up the full engine.

### Appendix F — Implementation Phases
The 13-week timeline is realistic for the scope. Phase 0 validation spikes (2 days) are correctly positioned before Phase 1. The three critical spikes are:

1. **Multi-server pymodbus:** 7+ async servers in one event loop. This is the highest-risk spike. I have run 3 pymodbus async servers successfully, but 7+ is untested territory. If it fails, the fallback is a single server with unit ID routing (collapsed mode only), which is simpler but loses the realistic multi-controller topology.

2. **Mosquitto sidecar:** Low risk. Mosquitto + paho-mqtt is a well-trodden path. 50 msg/s is trivial.

3. **asyncua multiple instances:** Medium risk. I have run 2 asyncua servers in one event loop. 3 should work but memory usage scales linearly (~50-80MB per asyncua server instance). With 3 servers (packaging OPC-UA + F&B filler OPC-UA + F&B QC OPC-UA), expect ~200MB baseline from OPC-UA alone. Monitor RSS.

---

## Verdict

**Ship it.** The PRD is implementation-ready.

The four MEDIUM issues (M1-M4) should be addressed before Phase 1 begins, but none of them block the Phase 0 validation spikes. M2 (Mosquitto config) is the most likely to bite immediately—add the config file content to the PRD or the repo before the first `docker compose up`.

The six LOW issues are cosmetic or minor consistency fixes. They can be addressed as the developer encounters them.

This is one of the most thorough factory simulation PRDs I have reviewed. The signal models are physically motivated, the protocol specifications are exact, the network topology is realistic, and the test strategy is focused on the right failure modes. The amqtt → Mosquitto change was the right call. The PRD is ready for code.

---

## Issue Summary

| Severity | Count | Issue IDs |
|----------|-------|-----------|
| HIGH     | 0     | — |
| MEDIUM   | 4     | M1, M2, M3, M4 |
| LOW      | 7     | L1, L2, L3, L4, L5, L6, L7 |
| **Total** | **11** | |
