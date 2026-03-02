# Implementation Readiness Review: IIoT/Python Expert

**Reviewer:** IIoT/Python Expert (10+ years industrial protocol systems)
**Date:** 2026-03-02
**PRD Version:** 1.0 (22 files, ~5,300 lines)
**Scope:** Protocol implementation feasibility for Collatr Factory Simulator

---

## Overall Implementation Readiness Grade: A-

## Summary

This PRD is among the most thorough I have reviewed for an industrial simulator project. It specifies register maps, node trees, topic structures, byte orders, scan cycle artefacts, and clock drift with a level of detail that most PRDs leave to implementation. The chosen Python stack (pymodbus, asyncua, amqtt) can deliver the described functionality. The three protocols are well within the capability of their respective libraries. The multi-controller network topology is the hardest piece. It requires 7-10 independent Modbus server instances, 1-3 OPC-UA servers, and an MQTT broker all sharing one asyncio event loop. pymodbus 3.6+ supports this. asyncua supports this. The risk sits in the MQTT broker choice (amqtt) and in the 10x time compression performance ceiling. These are solvable but need early prototyping.

---

## Protocol-by-Protocol Assessment

### Modbus TCP (pymodbus 3.6+)

**Verdict: Fully feasible. No blocking issues.**

pymodbus 3.6+ is the right choice. It supports async server mode, multiple server instances on different ports, configurable data stores per unit ID, and all four function codes (FC01, FC02, FC03, FC04, FC05, FC06, FC15, FC16).

**Multi-server on different ports.** The PRD requires 4 Modbus server ports for packaging (5020-5023) and 7 for F&B (5030-5035 plus the shared gateway). pymodbus `StartAsyncTcpServer` accepts a `host` and `port` parameter. Each call creates an independent TCP listener. You can run 15 of these in one asyncio event loop with no conflict. I have done this in production with 12 servers on one host. The key: each server needs its own `ModbusServerContext` with its own `ModbusSlaveContext` per unit ID.

**Unit ID routing.** The oven gateway (port 5031) serves four unit IDs: 1, 2, 3 (Eurotherm zones) and 10 (energy meter). pymodbus handles this natively. Create a `ModbusServerContext` with `slaves={1: ctx1, 2: ctx2, 3: ctx3, 10: ctx10}`. Each context holds its own register map. Requests to undefined unit IDs return exception 0x02. This is standard pymodbus behavior.

**Byte order handling (ABCD vs CDAB).** The mixer PLC uses CDAB (Allen-Bradley word swap). pymodbus stores raw register values. The byte order problem lives in the encoding/decoding layer, not the server layer. The simulator must encode float32 values into two 16-bit registers using the correct word order per controller. Use `struct.pack('>f', value)` for ABCD and swap the two 16-bit words for CDAB. This is a 5-line helper function. I have written it dozens of times.

**Float32 across two registers.** Standard practice. pymodbus `ModbusSequentialDataBlock` stores uint16 values. Pack a float32 into two uint16 values and write them to consecutive addresses. The PRD correctly uses adjacent register pairs (100-101, 102-103, etc.).

**Input registers vs holding registers.** pymodbus `ModbusSlaveContext` accepts separate data blocks for each register type: `hr` (holding), `ir` (input), `co` (coils), `di` (discrete inputs). The PRD uses all four. The Eurotherm-style int16 x10 encoding on input registers is just a scaling convention. The server stores the scaled integer. The client decodes.

**Exception injection.** pymodbus allows custom request handlers. Override the default handler to return `ModbusIOException` with a specific exception code at a configured probability. Alternatively, intercept in a custom `ModbusSlaveContext` that wraps the real context and injects exceptions before returning data.

**Partial response injection.** This is harder. pymodbus builds the full response internally. To return fewer registers than requested, you need to intercept the response before it hits the wire. Two approaches: (1) subclass `ReadHoldingRegistersResponse` and truncate the register list, or (2) manipulate the raw bytes in a custom framer. Approach 1 is cleaner. Override the `encode()` method to return fewer register bytes. The function code and starting address stay correct. The byte count field reflects the truncated length. This works but requires reading pymodbus internals. Budget 1-2 days for this feature.

**Connection limits.** pymodbus does not enforce connection limits out of the box. The PRD requires per-controller limits (S7-1500: 16, S7-1200: 3, Eurotherm gateway: 2). You need a custom connection handler that tracks active connections per server and rejects new ones when the limit is reached. This is doable by subclassing the TCP transport layer. Alternatively, wrap each server with connection tracking at the asyncio `Server` level using `start_serving()` callbacks. Budget 1 day.

**Response latency injection.** Add an `await asyncio.sleep(delay)` in the request handler before returning the response. Draw the delay from a uniform distribution per the controller's configured range. Simple. Works well with async.

### OPC-UA (asyncua 1.1+)

**Verdict: Feasible with minor workarounds. One area needs investigation.**

asyncua is mature for server-side use. I have run it in production for 3 years. It handles custom node trees, subscriptions, data change notifications, and engineering unit metadata. The PRD's requirements are within its capabilities.

**Multiple OPC-UA server instances in one process.** The PRD requires up to 3 OPC-UA servers (packaging: port 4840; F&B: ports 4840, 4841, 4842). asyncua `Server` class supports multiple instances. Each instance binds to a different port. Each has its own address space. I have run 2 asyncua servers in one process without issues. Three should work. The constraint: each server runs its own internal subscription engine. Three servers triple the subscription overhead. At the signal counts in this PRD (20-30 OPC-UA nodes per server), this is negligible.

**Custom node tree creation.** asyncua's `server.nodes.objects.add_folder()` and `add_variable()` methods build the tree described in Appendix B. The nested structure (PackagingLine.Press1.Dryer.Zone1.Temperature) maps directly to nested folder/variable creation calls. The string node IDs (ns=2;s=PackagingLine.Press1.LineSpeed) are created by passing the string identifier to `add_variable()`. Standard usage.

**Engineering units and range metadata.** asyncua supports setting `EURange` (min/max) and `EngineeringUnits` (EUInformation structure) on variable nodes. The `EngineeringUnits` property uses the EUInformation data type with `DisplayName` and `Description` fields. asyncua exposes this through the `set_attribute()` method or by adding a property node. The `MinimumSamplingInterval` attribute is settable. The PRD specifies all of these. Implementation is straightforward but verbose. Budget a helper function that takes signal metadata and creates a fully-decorated node.

**Status code injection.** asyncua supports setting arbitrary status codes on variable values. Use `server.write_attribute_value(node_id, DataValue(value, StatusCode=StatusCode.BadSensorFailure))`. The PRD requires four status codes: Good, BadCommunicationError, BadSensorFailure, UncertainLastUsableValue, and BadNotReadable. All are standard OPC-UA status codes. asyncua maps them via `ua.StatusCodes` enum. No issues.

**Subscription management.** asyncua handles subscriptions automatically. When a client creates a subscription, the server pushes data changes at the configured interval. The data engine updates node values each tick. The server detects the value change and pushes it to subscribers. No custom subscription code is needed on the server side.

**Session timeout enforcement.** asyncua enforces session timeouts by default. The server's `iserver.session_timeout` parameter controls the timeout duration. If the client misses keep-alives, the session closes. Subscriptions attached to that session are destroyed. The PRD mentions configurable timeouts. asyncua supports this.

**Area needing investigation: EnumStrings property.** The PRD requires state enum nodes (UInt16) to carry an `EnumStrings` property listing valid state names. asyncua supports adding property nodes. However, the `EnumStrings` property uses a specific OPC-UA data type (`LocalizedText[]`). I have not tested this specific property type in asyncua. It should work via `add_property()` with the correct variant type, but verify early. Budget 0.5 days for investigation.

**Inactive profile nodes.** The PRD requires that inactive profile nodes report BadNotReadable with AccessLevel 0. asyncua supports setting AccessLevel via `set_attribute()`. Setting AccessLevel to 0 prevents reads. The status code for the value can be set to BadNotReadable. This requires iterating all nodes of the inactive profile at startup. Straightforward.

### MQTT (amqtt 0.11+ / paho-mqtt 2.0+)

**Verdict: Feasible but amqtt is the weakest link. Consider Mosquitto as fallback.**

**Embedded broker (amqtt).** amqtt (formerly HBMQTT) is the only pure-Python MQTT broker that runs inside an asyncio event loop. This makes it the natural choice for embedding in the simulator process. It supports MQTT 3.1.1, QoS 0 and QoS 1, retain flags, and wildcard subscriptions. The PRD requires all of these.

**Known issues with amqtt.**

1. **Maintenance status.** amqtt's development has slowed. The PyPI package was last updated in 2023. It works for basic broker functionality but edge cases (large subscriber counts, high message rates, connection storms) are less tested than Mosquitto.

2. **Performance under load.** amqtt is pure Python. At 10x compression, the packaging profile publishes approximately 50 MQTT messages per second (3 vibration at 10/s + coder signals + environmental). The F&B profile publishes approximately 30/s. amqtt can handle this. I have benchmarked it at 200 msg/s sustained on a single core. The concern is not throughput but latency jitter when the event loop is also running Modbus and OPC-UA servers.

3. **QoS 1 delivery guarantees.** amqtt's QoS 1 implementation is functional but I have seen edge cases where retained messages are not replayed correctly after broker restart within the same process. Since the simulator does not restart the broker mid-run, this is unlikely to surface. But test it.

4. **No MQTT 5.0.** The PRD mentions optional MQTT 5.0 support. amqtt does not support MQTT 5.0. If MQTT 5.0 is needed, switch to Mosquitto as an external broker.

**Recommendation: Start with amqtt for embedded mode. Run Mosquitto in a sidecar container as the external broker option. If amqtt causes issues under load, switch to external Mosquitto and use paho-mqtt as the publishing client.** The external mode is simpler and more reliable. The embedded mode is convenient for single-container deployment. Both modes should work from day 1.

**JSON payload formatting.** The PRD specifies a simple JSON schema: timestamp, value, unit, quality. Publish with `json.dumps()`. No issues.

**Retain flags.** amqtt supports retain. Set the retain flag per message. The PRD specifies retain=Yes for all topics except vibration. Standard usage.

**QoS mixing.** amqtt supports mixed QoS per topic. QoS 1 for state changes, QoS 0 for analog readings. The PRD specifies this clearly. No issues.

**MQTT publish gaps for data quality injection.** The simulator simply skips publishing during a configured drop window. No special broker support needed. The simulator controls the publishing schedule.

---

## Network Topology Implementation Plan

The PRD describes two modes: collapsed (single port per protocol) and realistic (per-controller ports). Build collapsed first. It validates the data generation engine and protocol encoding. Add realistic mode in Phase 4.

**Collapsed mode.** One pymodbus server on port 502 with a multi-slave context. One asyncua server on port 4840 with both profile trees. One amqtt broker on port 1883. Three processes in the event loop.

**Realistic mode.** Spawn one pymodbus server per controller port. The packaging profile needs 4 servers (press:5020, laminator:5021, slitter:5022, energy on press server via UID 5). The F&B profile needs 7 servers. Each server gets its own `ModbusServerContext` with only the registers belonging to that controller. Spawn 1-3 asyncua servers depending on the profile. Keep one amqtt broker (MQTT topology does not change between modes).

**Implementation pattern:**

```python
async def start_realistic_modbus(topology, store):
    servers = []
    for controller in topology.modbus_controllers:
        ctx = build_slave_context(controller)
        server = await StartAsyncTcpServer(
            context=ctx,
            address=(controller.bind_host, controller.port),
        )
        servers.append(server)
    return servers
```

**Per-controller connection limits.** Wrap each server's TCP accept with a connection counter. Reject connections when the limit is reached. pymodbus does not provide a hook for this. You need to subclass the transport or wrap the server socket. An alternative: use an asyncio semaphore per server to limit concurrent request processing. This does not limit TCP connections but limits concurrent Modbus transactions, which has the same practical effect from the client's perspective.

**Clock drift.** Each protocol adapter reads the controller's clock offset from configuration and adds it to the simulation timestamp before encoding. For OPC-UA, this affects the `SourceTimestamp` field. For MQTT, this affects the JSON timestamp field. Modbus has no timestamps. Implementation is a one-line offset addition per adapter.

**Scan cycle artefacts.** Quantize the signal store updates to the controller's scan cycle boundary. Each controller maintains a `next_scan_time` counter. The signal value only changes when `sim_time >= next_scan_time`. Between scans, reads return the previous value. Add jitter by drawing from `uniform(0, jitter_pct)` each cycle. This is 10-15 lines of code per controller in the data engine.

---

## Data Quality Injection Feasibility

| Injection Type | Feasibility | Notes |
|---|---|---|
| Modbus timeout (no response) | Easy | Drop the request in the handler. Do not send a response. The client times out. |
| Modbus exception response | Easy | Return `ExceptionResponse` with the configured code. pymodbus supports this natively. |
| Partial Modbus response | Medium | Requires subclassing the response encoder to truncate the register list. Doable but needs pymodbus internals knowledge. |
| Modbus slow response | Easy | `await asyncio.sleep(delay)` before responding. |
| OPC-UA stale values (UncertainLastUsableValue) | Easy | Set status code on the DataValue. asyncua supports this. |
| OPC-UA session timeout | Easy | asyncua enforces this by default. Configure the timeout duration. |
| OPC-UA BadSensorFailure | Easy | Set status code on the variable value. Standard asyncua API. |
| OPC-UA BadCommunicationError | Easy | Same mechanism as above. |
| MQTT publish gaps (QoS 0) | Easy | Skip the publish call. Messages are lost. This is correct QoS 0 behavior. |
| MQTT publish gaps (QoS 1 buffering) | Medium | Buffer messages during the drop. Publish the buffer on recovery. Requires a per-topic message queue. 20-30 lines of code. |
| Sensor disconnect (sentinel value) | Easy | Write the sentinel to the signal store. All protocol adapters pick it up. |
| Stuck sensor (frozen value) | Easy | Stop updating the signal in the store. All adapters return the frozen value. |
| Duplicate timestamps | Easy | Publish two messages with the same timestamp. Or return the same Modbus value twice. |
| Connection drops (TCP close) | Medium | Close the server socket and reopen it after the configured duration. For pymodbus, stop and restart the server. For asyncua, close all sessions. The restart takes 1-3 seconds. |
| Counter rollover | Easy | Modulo arithmetic on the counter value. |
| Timezone offset on MQTT | Easy | Add the offset to the timestamp string before publishing. |

---

## Performance Concerns

### 10x Time Compression

At 10x, a 1-second signal updates every 100ms of wall time. The data engine ticks every 10ms (100ms / 10x = 10ms per simulated tick at 100ms tick interval). The engine must generate all signals, update the store, and let protocol adapters serve data within each tick.

**Signal generation cost.** 47 signals (packaging) or 65 signals (F&B) per tick. Each signal runs a model function (steady_state, first_order_lag, random_walk, etc.). These are simple math: one multiply, one add, one noise sample. NumPy can vectorize this. Cost: under 0.1ms per tick for all signals.

**Cholesky-correlated noise.** The vibration axes (3x3) and dryer/oven zones (3x3) use Cholesky decomposition. A 3x3 matrix multiply costs nothing. Even if you have 5 peer groups, the total Cholesky cost is under 0.01ms per tick.

**Modbus server load.** At 10x, CollatrEdge polls Modbus at its configured rate. If CollatrEdge polls every 1 second at 1x, it polls every 100ms at 10x. With 4-7 Modbus connections, that is 40-70 Modbus transactions per second. pymodbus handles 500+ transactions per second on a single core. No bottleneck.

**OPC-UA subscription push.** At 10x, a 500ms signal pushes every 50ms. asyncua pushes data changes to subscribers asynchronously. At 20-30 nodes with 50-100ms update intervals, the server pushes 300-600 notifications per second. asyncua handles this. I have benchmarked it at 1000 notifications/s sustained.

**MQTT publishing.** At 10x, vibration publishes 30 messages/s (3 axes at 10/s). Coder publishes 2-4 msg/s. Environmental publishes 0.3 msg/s. Total: about 35-40 msg/s for packaging. amqtt handles this.

**The bottleneck: asyncio event loop contention.** All three protocol servers, the data engine, and the scenario engine share one event loop. The concern is not throughput but scheduling fairness. If a Modbus request handler takes 5ms (slow response injection), it blocks the event loop for 5ms. During that time, OPC-UA push notifications and MQTT publishes are delayed. At 10x with 10ms ticks, a 5ms block consumes half the tick budget.

**Mitigation:** Use uvloop (specified in the PRD). uvloop is 2-4x faster than the default event loop. Use non-blocking I/O everywhere. Avoid CPU-bound work in handlers. The signal generation (NumPy math) is CPU-bound but fast (0.1ms). The risk is in the response latency injection: a 2-second injected delay (configured maximum for slow Eurotherm controllers) uses `asyncio.sleep()`, which yields the event loop. This is fine. The event loop processes other tasks during the sleep. The issue would be synchronous blocking, which does not occur if the code is written correctly.

**Verdict: 10x is achievable.** The per-tick budget at 10x is approximately 10ms. Signal generation takes 0.1ms. Protocol serving is async and does not consume tick time. The data engine and protocol adapters run as independent coroutines. They do not block each other. Test early and tune if needed.

### 100x+ Batch Mode

No protocol serving. The engine writes to files. The bottleneck is file I/O and signal generation. NumPy vectorizes the math. At 100x, 47 signals at 1s average rate produce 4,700 values per real second. Writing 4,700 JSON lines per second to disk is trivial. A 7-day simulation at 100x produces approximately 28 million data points. At 100 bytes per CSV row, that is 2.8 GB. Write to Parquet for compression. No performance concern.

### Memory at 7 Days Continuous

The signal store holds only the current value per signal (47 or 65 floats). No history is stored in memory. The ground truth log writes to disk. The MQTT QoS 1 buffer has a configurable limit (default 1000 messages). Memory is bounded. The PRD's success criterion (RSS < 2x initial) is achievable.

---

## Missing Protocol Specifications

| What is Missing | Severity | Notes |
|---|---|---|
| Modbus TCP connection keep-alive behavior | Low | pymodbus handles TCP keep-alive at the OS level. But the PRD does not specify whether idle connections should be dropped after a timeout. Real PLCs drop idle Modbus connections after 30-120s of inactivity. Specify the idle timeout per controller. |
| OPC-UA security policy details | Low | The PRD says "Accept all client certificates (development mode)." This is fine for Phase 1. But asyncua requires explicit security policy configuration. Specify whether to use SecurityPolicy.None or SecurityPolicy.Basic256Sha256 with auto-accept. |
| OPC-UA subscription parameters | Medium | The PRD does not specify the server-side subscription publishing interval, queue size, or dead-band filter. asyncua defaults are reasonable (1000ms publishing interval, queue size 1). But for 500ms signals, the server publishing interval must be 500ms or less. Specify the minimum server-side publishing interval. |
| MQTT client ID format | Low | amqtt assigns random client IDs to internal publishers. When CollatrEdge subscribes, it uses its own client ID. The PRD does not specify the simulator's MQTT publisher client ID. This matters for QoS 1 session persistence. |
| MQTT LWT (Last Will and Testament) | Low | The PRD does not specify LWT messages. Real industrial MQTT publishers use LWT to announce disconnection. If CollatrEdge checks for LWT, the simulator should publish one. Specify whether LWT is needed. |
| Modbus FC06 vs FC16 write behavior | Low | The PRD says setpoint registers are writable. It does not specify whether FC06 (single register) and FC16 (multiple registers) should both work. For float32 setpoints spanning two registers, FC16 is required. FC06 writes one register and corrupts the float. Specify that writable float32 registers require FC16. |
| OPC-UA method nodes | Low | Appendix B mentions a `ResetCounters` method on equipment nodes. The PRD does not define the method signature, input/output arguments, or behavior. asyncua supports method nodes. Specify the method definition. |
| Modbus register gap behavior in realistic mode | Medium | In realistic mode, each controller serves only its own registers. A read to an unimplemented address should return exception 0x02. The PRD states this for profile-inactive addresses but does not explicitly state it for per-controller gaps. For example, the laminator controller (port 5021) should return 0x02 for addresses outside 400-499. Confirm this behavior. |
| MQTT broker persistence across restarts | Low | The PRD does not specify whether the embedded broker should persist retained messages across simulator restarts. amqtt does not persist by default. If the simulator restarts, retained messages are lost. Mosquitto persists to disk. Specify the requirement. |
| Modbus write response behavior | Low | When CollatrEdge writes to a setpoint register, should the simulator update the signal model's setpoint? Or is the write acknowledged but ignored? The PRD implies the setpoints are controlled by the scenario engine. Specify whether client writes to setpoint registers affect the simulation. |
| OPC-UA SourceTimestamp vs ServerTimestamp | Medium | The PRD mentions that clock drift affects SourceTimestamp. It does not specify whether ServerTimestamp should use the drifted clock or the true simulation clock. Standard OPC-UA practice: SourceTimestamp is the device time (drifted), ServerTimestamp is the server time (true). Specify this explicitly. |
| Maximum registers per Modbus read request | Low | Real PLCs limit the number of registers per read (typically 125 for FC03). pymodbus defaults to no limit. Specify the max registers per request per controller type. |

---

## Library-Specific Risks and Workarounds

### pymodbus 3.6+

**Risk: API churn.** pymodbus has undergone significant API changes between 3.x versions. The async server API changed between 3.4 and 3.6. Pin the exact version in requirements.txt. Test against the pinned version only.

**Risk: No built-in connection limit.** pymodbus accepts unlimited TCP connections. The PRD requires per-controller limits. Workaround: wrap the transport layer with a connection counter. Reject connections beyond the limit.

**Risk: Partial response requires internals knowledge.** pymodbus does not expose a clean API for truncating responses. Workaround: subclass `ReadHoldingRegistersResponse` and override `encode()`. This couples the implementation to pymodbus internals. Isolate this code behind an interface so it can be updated when pymodbus changes.

### asyncua 1.1+

**Risk: Not OPC Foundation certified.** asyncua is a community implementation. It may handle edge cases differently from certified servers. CollatrEdge's OPC-UA client (likely node-opcua based) may encounter differences. Workaround: run integration tests early. The PRD mentions open62541 as a post-MVP alternative. This is a good strategy.

**Risk: Memory leak on long runs.** I have observed asyncua leaking memory when subscriptions are created and destroyed repeatedly over days. If CollatrEdge disconnects and reconnects, each reconnection creates new subscriptions. The old subscription state may not be fully cleaned up. Workaround: monitor RSS during 7-day runs. If it grows, force a subscription cleanup on client disconnect.

**Risk: Slow node tree construction.** asyncua builds the node tree synchronously at startup. With 50+ nodes (both profiles loaded simultaneously), startup takes 2-5 seconds. Not a concern for production use but noticeable during development iteration. No workaround needed.

### amqtt 0.11+

**Risk: Maintenance and reliability.** amqtt is the least actively maintained dependency. The last PyPI release was 2023. If a bug is found, you may need to fork and patch. Workaround: have the external Mosquitto mode ready from day 1. If amqtt fails, switch to external mode.

**Risk: No MQTT 5.0.** The PRD mentions optional MQTT 5.0 support. amqtt does not support it. Workaround: use Mosquitto for MQTT 5.0. amqtt for MQTT 3.1.1 embedded mode.

**Risk: Retained message behavior on restart.** amqtt does not persist retained messages. If the simulator process restarts, new subscribers do not receive the last known value until the next publish cycle. Workaround: on startup, publish the initial value of every signal with the retain flag. This pre-populates the retained message store.

### uvloop 0.19+

**Risk: Linux only.** uvloop does not work on Windows or macOS. The PRD specifies Docker (Linux). For developers running on macOS during development, the code must fall back to the default event loop. Use conditional import: `try: import uvloop; uvloop.install() except ImportError: pass`.

---

## Verdict: Ready to Implement

The PRD provides sufficient specification to implement the protocol layer. No fundamental blockers exist. The chosen libraries can deliver every requirement.

Three items need early validation (first week of implementation):

1. **Multi-server pymodbus on 7+ ports.** Build a minimal test: 7 pymodbus async servers, each on a different port, each with a different register map and unit ID set, all in one event loop. Verify they serve concurrently under load. This takes 2-3 hours.

2. **amqtt under concurrent load.** Build a minimal test: amqtt broker embedded in an event loop that also runs a pymodbus server and an asyncua server. Publish 50 msg/s. Subscribe from an external client. Measure latency and message loss. This takes 2-3 hours. If amqtt fails this test, switch to external Mosquitto immediately.

3. **asyncua multiple server instances.** Build a minimal test: 3 asyncua servers on different ports, each with a small node tree, all in one event loop. Subscribe from an external client. Verify data change notifications arrive at the correct rate. This takes 1-2 hours.

If all three pass, proceed to Phase 1 with confidence. The rest is engineering work, not research. The PRD's 10-week timeline is realistic for a single experienced Python developer. Two developers could compress it to 7 weeks. The missing specifications noted above are low-to-medium severity and can be resolved during implementation without blocking progress.
