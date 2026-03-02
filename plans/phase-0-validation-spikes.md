# Phase 0: Validation Spikes

**Duration:** 2 days (first 2 days of Week 1)
**Goal:** Confirm library feasibility before committing to architecture.

---

## Context

The Factory Simulator's architecture depends on three assumptions that have not been validated:

1. **pymodbus** can run 7+ async Modbus TCP servers on different ports in a single asyncio event loop
2. **Mosquitto** sidecar + paho-mqtt can handle 50 msg/s publish with retained messages in Docker Compose
3. **asyncua** can run 3+ OPC-UA servers on different ports in a single asyncio event loop

If any of these fail, the architecture must be redesigned before Phase 1. Each spike is 2-3 hours. All three must pass.

---

## Spike 1: Multi-Server pymodbus (2-3 hours)

**Question:** Can pymodbus run 7+ async Modbus TCP servers on different ports in one asyncio event loop, each with independent register maps?

**What to build:**
```
spike_modbus/
  spike_modbus.py      # Server: 7 async ModbusTcpServer instances
  test_spike_modbus.py # Client: reads from all 7 servers concurrently
```

**Server setup:**
- 7 `StartAsyncModbusTcpServer` instances on ports 5020-5026
- Each server has its own `ModbusSlaveContext` with a different register map
- Server 1 (port 5020): HR 0-99, simulating press (ABCD byte order)
- Server 2 (port 5021): HR 100-199, simulating laminator
- Server 3 (port 5022): HR 200-299, simulating slitter
- Server 4 (port 5023): HR 300-399, simulating coder
- Server 5 (port 5024): HR 400-499, simulating oven gateway with 3 unit IDs (multi-slave)
- Server 6 (port 5025): HR 500-599, simulating energy meter (unit ID 5)
- Server 7 (port 5026): HR 600-699, simulating environment

**Multi-slave test (Server 5):**
- `ModbusServerContext(slaves={1: zone1_ctx, 2: zone2_ctx, 3: zone3_ctx})`
- Client reads from UID 1, 2, 3 on same port and gets different register values

**Client test:**
- Use pymodbus `AsyncModbusTcpClient` to connect to all 7 servers
- Read HR from each server and verify correct values
- Read from different UIDs on the multi-slave server
- Write to a setpoint register on one server and read back
- Concurrent reads from all 7 servers simultaneously (asyncio.gather)

**Validation (FC06 rejection):**
- Attempt FC06 write to a float32 register pair (should fail with exception 0x01)
- This requires a custom request handler. Test that the handler works.

**Max register limit:**
- Attempt to read 126 registers in one request (should fail with exception 0x03)
- Verify 125 registers succeeds

**Pass criteria:**
- All 7 servers start and serve concurrently in one event loop
- Multi-slave addressing works (different data per UID on same port)
- Concurrent reads from all servers complete without errors
- Custom FC06 rejection works
- Max register limit enforced
- No event loop blocking (measure time for concurrent reads, should be << 7x sequential)

**Fail action:** If multi-server fails, fallback to single server with UID-based routing (collapsed mode only). This loses the realistic multi-controller topology but is architecturally simpler.

---

## Spike 2: Mosquitto Sidecar Integration (2-3 hours)

**Question:** Can a Python paho-mqtt 2.0 client publish 50 msg/s to a Mosquitto Docker sidecar with mixed QoS, retained messages, and LWT?

**What to build:**
```
spike_mqtt/
  docker-compose.yml     # Mosquitto sidecar
  config/mosquitto.conf  # Minimal config
  spike_mqtt_pub.py      # Publisher: 50 msg/s mixed QoS
  spike_mqtt_sub.py      # Subscriber: verify messages
  test_spike_mqtt.py     # Automated test
```

**Docker Compose:**
```yaml
services:
  mqtt-broker:
    image: eclipse-mosquitto:2
    ports:
      - "1883:1883"
    volumes:
      - ./config/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
    healthcheck:
      test: ["CMD", "mosquitto_sub", "-t", "$$SYS/#", "-C", "1", "-W", "3"]
      interval: 5s
      timeout: 3s
      retries: 3
```

**Mosquitto config:**
```
listener 1883 0.0.0.0
allow_anonymous true
persistence false
log_dest stdout
```

**Publisher:**
- paho-mqtt 2.0 `Client()` with `client_id="factory-simulator"`
- LWT set before connect: topic `factory/status`, payload `offline`, QoS 1, retain True
- Publish 50 msg/s: 25 at QoS 0, 25 at QoS 1
- Topics: `factory/press/line_speed` (QoS 0), `factory/press/machine_state` (QoS 1, retained)
- JSON payloads with timestamp, value, quality
- Run for 10 seconds (500 messages)

**Subscriber test:**
- Subscribe before publisher starts
- Count messages received per QoS level
- Verify retained messages arrive on new subscription
- Verify LWT fires when publisher disconnects uncleanly
- Measure end-to-end latency (publisher timestamp vs subscriber receive time)

**Client-side buffer test:**
- Disconnect Mosquitto mid-publish (docker compose stop mqtt-broker)
- Verify publisher buffers QoS 1 messages
- Restart Mosquitto (docker compose start mqtt-broker)
- Verify buffered messages arrive after reconnection

**Pass criteria:**
- All 500 messages received (QoS 1 guaranteed, QoS 0 >= 99%)
- Retained messages work correctly on new subscriber
- LWT fires on unclean disconnect
- End-to-end latency < 50ms at 50 msg/s
- Client-side buffer survives broker restart
- Docker health check passes

**Fail action:** Mosquitto is the industry standard. If this fails, the problem is configuration, not the broker. Debug and fix.

---

## Spike 3: asyncua Multiple Instances (1-2 hours)

**Question:** Can asyncua run 3 OPC-UA server instances on different ports in one asyncio event loop with subscriptions?

**What to build:**
```
spike_opcua/
  spike_opcua.py      # 3 asyncua Server instances
  test_spike_opcua.py # Client: browse + subscribe to all 3
```

**Server setup:**
- Server 1 (port 4840): PackagingLine node tree (5 variables)
- Server 2 (port 4841): FoodBevLine node tree (5 variables)
- Server 3 (port 4842): QC node tree (3 variables)
- Each server has string NodeIDs: `ns=2;s=<Profile>.<Equipment>.<Signal>`
- Variables update every 500ms from a shared simulation tick

**Variable attributes:**
- AccessLevel: read-only for PV, read-write for SP
- EURange property set on each variable
- EngineeringUnits property (if asyncua supports it cleanly)
- SourceTimestamp and ServerTimestamp both set

**Client test:**
- Browse all 3 server node trees
- Create subscription on each server (publishing interval 500ms)
- Monitor 3 variables per server for data change notifications
- Verify data changes arrive at correct intervals
- Verify SourceTimestamp vs ServerTimestamp are different (simulate clock offset)

**StatusCode test:**
- Set a variable to BadSensorFailure status
- Verify client receives the status code

**Memory baseline:**
- Measure RSS before and after starting 3 servers
- Record baseline for future monitoring (expect ~150-240MB total for 3 asyncua servers)

**Pass criteria:**
- All 3 servers start and serve concurrently in one event loop
- Subscriptions deliver data change notifications at 500ms intervals
- String NodeIDs work correctly
- Variable attributes (EURange, AccessLevel) browsable
- StatusCode propagation works
- RSS < 300MB for 3 servers with small node trees

**Fail action:** If 3 servers fail, try 2 (one per profile). If subscriptions fail at 500ms, increase to 1000ms. If memory is excessive, investigate asyncua server pooling options.

---

## Exit Criteria

All three spikes must pass. Document results in `docs/validation-spikes.md` with:
- Pass/fail per spike
- Actual performance numbers (latency, memory, throughput)
- Any library quirks discovered
- Code samples that worked (these become reference implementations for Phase 1)
- Version numbers of all libraries tested

If any spike fails, document the failure mode and propose an alternative architecture before proceeding to Phase 1.

---

## Task List

| Task | Description | Est. Hours |
|---|---|---|
| 0.1 | Project scaffolding (pyproject.toml, requirements.txt, pytest config, ruff config, mypy config, .gitignore, src/ and tests/ directories) | 1 |
| 0.2 | Spike 1: Multi-server pymodbus | 2-3 |
| 0.3 | Spike 2: Mosquitto sidecar + paho-mqtt | 2-3 |
| 0.4 | Spike 3: asyncua multiple instances | 1-2 |
| 0.5 | Document results in docs/validation-spikes.md, cleanup spike code | 1 |
