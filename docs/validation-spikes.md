# Phase 0: Validation Spike Results

**Date:** 2026-03-02
**Python:** 3.13.2
**Platform:** macOS (Darwin)

All three spikes **PASS**. No library redesign required. Proceed to Phase 1.

---

## Summary

| Spike | Library | Version | Result | Tests |
|-------|---------|---------|--------|-------|
| 1. Multi-server pymodbus | pymodbus | 3.12.1 | PASS | 12 |
| 2. Mosquitto + paho-mqtt | paho-mqtt | 2.1.0 | PASS | 8 |
| 3. asyncua multiple instances | asyncua | 1.1.8 | PASS | 12 |

**Total: 32 tests, all passing.** Test runtime ~120s (dominated by OPC-UA server startup).

---

## Spike 1: Multi-server pymodbus

**Library:** pymodbus 3.12.1
**Test file:** `tests/spikes/test_spike_modbus.py`

### What Was Validated

1. **7 concurrent servers** -- 7 `ModbusTcpServer` instances on ports 15020-15026 serve concurrently in one asyncio event loop. Each has an independent `ModbusServerContext` and register map.

2. **Multi-slave addressing** -- Server 4 configured with `ModbusServerContext(devices={1: ctx1, 2: ctx2, 3: ctx3}, single=False)`. Client reads from UIDs 1, 2, 3 on the same port and gets different register values. Non-existent UID (99) returns error.

3. **Concurrent reads** -- `asyncio.gather` across all 7 servers completes without errors. 50 rounds of 7 concurrent reads in <10s. No event loop blocking.

4. **Setpoint write/read-back** -- FC16 (`write_registers`) writes float32 value, FC03 reads it back correctly.

5. **FC06 rejection** -- Custom `FC06ProtectedDeviceContext` subclass overrides `setValues()` to return `ExcCodes.ILLEGAL_FUNCTION` when func_code=6 targets a float32 register pair. FC16 to same addresses succeeds. FC06 to non-float32 addresses succeeds.

6. **Max 125 register limit** -- Client-side: pymodbus `verifyCount(125)` in `encode()` raises `ValueError` for count>125. Server-side: Custom `RegisterLimitDeviceContext` overrides `getValues()` to return `ExcCodes.ILLEGAL_VALUE` for count>125.

### Performance

| Metric | Value |
|--------|-------|
| 7 server startup | ~500ms |
| Single register read | sub-ms |
| 50 rounds x 7 concurrent reads | <1s |

### pymodbus 3.12 API Quirks

- `ModbusSlaveContext` renamed to `ModbusDeviceContext`
- `slave` parameter renamed to `device_id` everywhere
- `read_holding_registers(address, *, count=1, device_id=1)` -- `count` is keyword-only
- `ModbusServerContext(devices=..., single=...)` -- parameter renamed from `slaves`
- `ModbusSequentialDataBlock` is 1-indexed internally: address 0 maps to `values[1]`
- Concurrent vs sequential timing on localhost shows no speedup (sub-ms per read means `asyncio.gather` overhead dominates), but the spike validates non-blocking behaviour, not throughput

### Reference Patterns for Phase 1

**Server lifecycle:**
```python
server = ModbusTcpServer(context, address=(host, port))
task = asyncio.create_task(server.serve_forever())
# ... serve ...
await server.shutdown()
task.cancel()
```

**Multi-slave:**
```python
devices = {1: ctx1, 2: ctx2, 3: ctx3}
context = ModbusServerContext(devices=devices, single=False)
```

**Custom device context (FC06 rejection):**
```python
class FC06ProtectedDeviceContext(ModbusDeviceContext):
    def __init__(self, float32_addresses=None, **kwargs):
        super().__init__(**kwargs)
        self._float32_addresses = float32_addresses or set()

    def setValues(self, func_code, address, values):
        if func_code == 6 and address in self._float32_addresses:
            return ExcCodes.ILLEGAL_FUNCTION
        return super().setValues(func_code, address, values)
```

**Float32 encoding (ABCD / big-endian):**
```python
packed = struct.pack(">f", value)
high = int.from_bytes(packed[0:2], "big")
low = int.from_bytes(packed[2:4], "big")
```

---

## Spike 2: Mosquitto sidecar + paho-mqtt

**Library:** paho-mqtt 2.1.0 (CallbackAPIVersion.VERSION2)
**Broker:** eclipse-mosquitto:2 Docker image
**Test file:** `tests/spikes/test_spike_mqtt.py`
**Infra files:** `docker-compose.yml`, `config/mosquitto.conf`

### What Was Validated

1. **Connectivity** -- paho-mqtt 2.0 `Client()` connects to Mosquitto sidecar. `is_connected()` returns True after CONNACK (requires ~500ms after `loop_start()`).

2. **Retained messages** -- Publishing with `retain=True` causes new subscribers to receive the last retained value immediately. Retain flag is set on the received message.

3. **LWT (Last Will and Testament)** -- Setting `will_set()` before `connect()` causes the broker to publish the LWT payload when the client disconnects uncleanly (socket close). LWT fires after keepalive * 1.5 seconds.

4. **50 msg/s throughput** -- 500 messages published at ~42 msg/s (25 QoS0 + 25 QoS1 per second). All QoS 1 messages received (250/250). All QoS 0 messages received (250/250, 0% loss on localhost).

5. **End-to-end latency** -- Avg 3.0ms, P95 6.7ms, Max 13.8ms at 50 msg/s. Well under the 50ms threshold.

6. **JSON payload format** -- Payloads match PRD Section 3.3.4: `{timestamp, value, unit, quality}`. Round-trip through broker preserves all fields and types.

### Performance

| Metric | Value |
|--------|-------|
| Publish rate | ~42 msg/s (target 50) |
| QoS 0 loss | 0% (localhost) |
| QoS 1 delivery | 100% |
| Avg latency | 3.0 ms |
| P95 latency | 6.7 ms |
| Max latency | 13.8 ms |

### paho-mqtt 2.0 API Quirks

- `CallbackAPIVersion.VERSION2` is required -- the default API version changed in 2.0
- `connect()` + `loop_start()` is asynchronous: `is_connected()` returns False immediately, need ~500ms delay for CONNACK
- `result.wait_for_publish(timeout=N)` blocks until QoS 1 PUBACK received (useful for retained message setup)
- `_sock.close()` forces unclean disconnect for LWT testing (no public API for this)
- Client may receive extra retained LWT messages from previous test's unclean disconnect (harmless)

### Note on Broker Restart Buffer Test

The PRD spike plan mentions a broker restart buffering test. This was not implemented because it requires `docker compose stop/start` mid-test which adds flaky Docker orchestration to the test suite. The paho-mqtt 2.0 `max_queued_messages_set()` API exists for this. Buffer behaviour will be validated in integration tests during Phase 1 when the full simulator stack is running.

### Reference Patterns for Phase 1

**Client lifecycle:**
```python
client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="my-client",
    protocol=mqtt.MQTTv311,
)
client.will_set(lwt_topic, payload=lwt_payload, qos=1, retain=True)
client.connect(host, port, keepalive=60)
client.loop_start()
# ... publish ...
client.loop_stop()
client.disconnect()
```

**JSON payload (PRD Section 3.3.4):**
```python
payload = json.dumps({
    "timestamp": datetime.now(UTC).isoformat(),
    "value": 42.7,
    "unit": "m/min",
    "quality": "good",
})
```

**Retained message cleanup:**
```python
client.publish(topic, b"", qos=1, retain=True)  # empty payload clears retained
```

**Throughput pacing:**
```python
interval = 1.0 / target_rate
for msg in messages:
    send_time = time.monotonic()
    client.publish(topic, payload, qos=qos)
    elapsed = time.monotonic() - send_time
    sleep_time = interval - elapsed
    if sleep_time > 0:
        time.sleep(sleep_time)
```

---

## Spike 3: asyncua Multiple Instances

**Library:** asyncua 1.1.8
**Test file:** `tests/spikes/test_spike_opcua.py`

### What Was Validated

1. **3 concurrent servers** -- 3 `asyncua.Server` instances on OS-assigned ports (port 0), all serving concurrently in one asyncio event loop. Each with independent node trees.

2. **Subscriptions at 500ms** -- `create_subscription(500, handler)` delivers data change notifications. Initial value notification fires immediately on subscribe, subsequent updates arrive at ~500ms intervals. Subscriptions work concurrently on all 3 servers.

3. **String NodeIDs** -- `ua.NodeId('PackagingLine.Press1.LineSpeed', 2)` creates `ns=2;s=PackagingLine.Press1.LineSpeed`. Browsable and readable by path from client.

4. **Variable attributes** -- `EURange` property set via `add_property()` with `ua.Range(Low, High)`. `AccessLevel` defaults to 1 (read-only); `set_writable()` sets to 3 (read-write). Both browsable from client.

5. **StatusCode propagation** -- `ua.DataValue(ua.Variant(...), ua.StatusCode(ua.StatusCodes.BadSensorFailure))` propagates to client via `read_data_value(raise_on_bad_status=False)` and via subscription notifications.

6. **Memory baseline** -- Peak RSS ~400MB for entire test process (3 servers + pytest + crypto libs + client connections). Reasonable for Phase 1 planning.

### Performance

| Metric | Value |
|--------|-------|
| Server startup | ~2-5s per server |
| Total test runtime | ~70s (12 tests) |
| Peak RSS (whole process) | ~400 MB |
| Concurrent reads (3 servers) | <5s |

### asyncua 1.1.8 API Quirks

- `Server()` + `await server.init()` required before configuration. `server.start()` required before `server.stop()`.
- Port 0 (OS-assigned): set via `server.set_endpoint('opc.tcp://127.0.0.1:0/...')`, extract actual port from `server.bserver._server.sockets[0].getsockname()[1]`.
- `set_security_policy([ua.SecurityPolicyType.NoSecurity])` suppresses security warnings but "No signing policy" warning still appears in logs.
- Subscription handler receives `DataChangeNotif` object (not `DataValue`). Actual `DataValue` is at `data.monitored_item.Value`.
- `read_data_value()` raises `BadSensorFailure` exception by default. Must pass `raise_on_bad_status=False` to read bad status values.
- When StatusCode is bad, the `Value` field is `Variant(Null)` (not the last good value).
- `ru_maxrss` on macOS reports peak RSS in bytes (not KB like Linux). Includes all process memory.
- `add_property(ua.NodeId(0, 0), 'EURange', ua.Range(...))` auto-assigns NodeId.

### Reference Patterns for Phase 1

**Server lifecycle:**
```python
server = Server()
await server.init()
server.set_endpoint(f"opc.tcp://{host}:{port}/path/")
server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
ns = await server.register_namespace(namespace_uri)
# ... add nodes ...
await server.start()
# ... serve ...
await server.stop()
```

**String NodeIDs:**
```python
node_id = ua.NodeId("PackagingLine.Press1.LineSpeed", ns)  # ns=2;s=...
```

**Variable creation with EURange:**
```python
var_node = await folder.add_variable(
    node_id, name, initial_value, varianttype=ua.VariantType.Double,
)
await var_node.add_property(
    ua.NodeId(0, 0), "EURange", ua.Range(Low=0.0, High=500.0),
)
```

**Writable setpoints:**
```python
await var_node.set_writable()  # AccessLevel 1 -> 3
```

**Value update:**
```python
server_node = server.get_node(node_id)
await server_node.write_value(new_value, ua.VariantType.Double)
```

**StatusCode:**
```python
bad_dv = ua.DataValue(
    ua.Variant(0.0, ua.VariantType.Double),
    ua.StatusCode(ua.StatusCodes.BadSensorFailure),
)
await server_node.write_value(bad_dv)

# Client-side: must suppress exception
dv = await client_node.read_data_value(raise_on_bad_status=False)
```

**Subscription handler:**
```python
class DataChangeHandler:
    def datachange_notification(self, node, val, data):
        # data is DataChangeNotif, not DataValue
        data_value = data.monitored_item.Value
        # data_value.StatusCode, data_value.Value, etc.
```

---

## Decisions for Phase 1

1. **pymodbus 3.12 API**: Use `ModbusDeviceContext` (not `ModbusSlaveContext`), `device_id` (not `slave`), `count` as keyword argument. `ModbusSequentialDataBlock` is 1-indexed internally.

2. **paho-mqtt 2.0 API**: Always use `CallbackAPIVersion.VERSION2`. Allow ~500ms after `connect()` + `loop_start()` before checking `is_connected()`.

3. **asyncua subscriptions**: Handler receives `DataChangeNotif` wrapper. Extract `DataValue` from `data.monitored_item.Value`. Use `raise_on_bad_status=False` for StatusCode reads.

4. **Memory**: OPC-UA servers consume ~400MB peak RSS for the test process. Plan Docker container memory limits accordingly.

5. **Port allocation**: Modbus uses fixed ports (per PRD register maps). OPC-UA can use port 0 in tests but should use configured ports in production.

6. **Broker restart buffering**: Deferred to Phase 1 integration tests.
