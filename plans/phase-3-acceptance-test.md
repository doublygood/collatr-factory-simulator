# Phase 3: Acceptance Test Procedure

**Purpose:** Verify the factory simulator serves correct data across all three protocols for BOTH profiles (packaging + F&B). Phase 3 gate.
**When:** After Phase 3 independent review: GO.
**Where:** Lee's dev machine. Not CI. Not the OpenClaw container.
**Who:** Lee, or Claude Code on the dev machine.

## What Changed Since Phase 2

Phase 3 added the complete F&B profile. Acceptance testing must now cover:

1. **Packaging profile** (47 signals) — regression check, same as Phase 2
2. **F&B profile** (68 signals) — new: CDAB encoding, multi-slave Modbus, per-item filler, 7 new scenarios
3. **Profile switching** — both profiles load and run without errors

## Prerequisites

### Software

1. Python 3.12+ with the simulator installed: `pip install -e ".[dev]"` from repo root
2. CollatrEdge binary (or `bun run src/main.ts` from `repos/collatr-edge`)
3. Docker (for Mosquitto broker only)

### Infrastructure

The simulator has no Dockerfile yet (Phase 5). For now, run the simulator directly via the runner script described below. Only the MQTT broker runs in Docker.

```bash
# Start Mosquitto only
cd repos/collatr-factory-simulator
docker compose up -d

# Verify Mosquitto is healthy
docker compose ps
# mqtt-broker should be "healthy"
```

### Runner Script

The simulator has no CLI entry point yet (Phase 5). Create `scripts/run-simulator.py`:

```python
#!/usr/bin/env python3
"""Run the factory simulator with protocol servers.

Usage:
    # Packaging profile, real-time
    python3 scripts/run-simulator.py

    # F&B profile, 10x speed, fixed seed
    python3 scripts/run-simulator.py --config config/factory-foodbev.yaml --time-scale 10 --seed 42

    # Packaging, 10x speed
    python3 scripts/run-simulator.py --time-scale 10 --seed 42
"""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.protocols.modbus_server import ModbusServer
from factory_simulator.protocols.opcua_server import OpcuaServer
from factory_simulator.protocols.mqtt_publisher import MqttPublisher


async def main(config_path: str, time_scale: float, seed: int | None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    log = logging.getLogger("simulator")

    # Load config
    cfg = load_config(Path(config_path))
    log.info("Loaded config: %s (%s)", cfg.factory.name, config_path)

    # Override time_scale and seed if provided
    if time_scale != 1.0:
        cfg.simulation.time_scale = time_scale
        log.info("Time scale override: %.1fx", time_scale)
    if seed is not None:
        cfg.simulation.random_seed = seed
        log.info("Random seed override: %d", seed)

    # Build engine
    engine = DataEngine(cfg)
    store = engine.store

    # Build protocol servers
    modbus = ModbusServer(cfg, store)
    opcua = OpcuaServer(cfg, store)
    mqtt = MqttPublisher(cfg, store)

    # Graceful shutdown
    stop_event = asyncio.Event()

    def handle_signal(sig, frame):
        log.info("Received %s, shutting down...", signal.Signals(sig).name)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Start everything
    log.info("Starting protocol servers...")
    await modbus.start()
    await opcua.start()
    await mqtt.start()

    log.info("Starting simulation engine...")
    engine_task = asyncio.create_task(engine.run())

    log.info("Simulator running. Press Ctrl+C to stop.")

    # Wait for shutdown signal
    await stop_event.wait()

    # Shutdown
    log.info("Stopping engine...")
    engine.stop()
    await engine_task

    log.info("Stopping protocol servers...")
    await mqtt.stop()
    await opcua.stop()
    await modbus.stop()

    log.info("Shutdown complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the factory simulator")
    parser.add_argument(
        "--config", default="config/factory.yaml",
        help="Config file path (default: config/factory.yaml)"
    )
    parser.add_argument(
        "--time-scale", type=float, default=1.0,
        help="Simulation speed multiplier (default: 1.0)"
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for deterministic runs"
    )
    args = parser.parse_args()
    asyncio.run(main(args.config, args.time_scale, args.seed))
```

**IMPORTANT:** This runner script is a best-effort starting point. The actual `load_config()`, `DataEngine()`, `ModbusServer()`, `OpcuaServer()`, and `MqttPublisher()` APIs may differ from what is shown. Check the actual constructor signatures in the source code and adjust the script accordingly before running. The integration tests (e.g., `tests/integration/test_modbus_fnb_integration.py`) show the correct instantiation patterns.

---

## Test Tiers

| Tier | Simulated Duration | Real Duration (at 10x) | Purpose |
|------|-------------------|----------------------|---------|
| Smoke | 10 minutes | 1 minute | Does it connect? Does data flow? |
| Medium | 1 hour | 6 minutes | Do scenarios fire? State transitions? |
| Full | 8 hours | 48 minutes | All scenario types fire, memory stable |

Run Smoke first. If it passes, run Medium. Full is the sign-off.

---

## Procedure

### Step 0: Verify Mosquitto Is Running

```bash
cd repos/collatr-factory-simulator
docker compose up -d
docker compose ps
# mqtt-broker should show "healthy"
```

### Step 1: Smoke Test — Packaging Profile

```bash
# Terminal 1: Start the simulator (packaging, 10x speed, fixed seed)
cd repos/collatr-factory-simulator
python3 scripts/run-simulator.py --time-scale 10 --seed 42

# Terminal 2: Quick protocol checks (while simulator is running)

# Modbus: read machine state (HR 210, uint16)
python3 -c "
import asyncio
from pymodbus.client import AsyncModbusTcpClient
async def check():
    c = AsyncModbusTcpClient('localhost', port=502)
    await c.connect()
    r = await c.read_holding_registers(210, count=1, slave=1)
    print(f'Machine state (HR 210): {r.registers[0]}')
    r2 = await c.read_holding_registers(100, count=2, slave=1)
    import struct
    speed = struct.unpack('>f', struct.pack('>HH', r2.registers[0], r2.registers[1]))[0]
    print(f'Line speed (HR 100-101): {speed:.1f} m/min')
    c.close()
asyncio.run(check())
"

# OPC-UA: read a node
python3 -c "
import asyncio
from asyncua import Client
async def check():
    c = Client('opc.tcp://localhost:4840')
    await c.connect()
    node = c.get_node('ns=2;s=PackagingLine.Press1.LineSpeed')
    val = await node.read_value()
    print(f'OPC-UA Press1.LineSpeed: {val}')
    await c.disconnect()
asyncio.run(check())
"

# MQTT: subscribe for 5 seconds
mosquitto_sub -h localhost -t "collatr/factory/packaging1/#" -v -W 5
```

**Pass criteria:** All three protocols responding with non-zero values when machine is Running.

### Step 2: Smoke Test — F&B Profile

```bash
# Stop the packaging simulator (Ctrl+C in Terminal 1)

# Start F&B profile
python3 scripts/run-simulator.py --config config/factory-foodbev.yaml --time-scale 10 --seed 42

# Terminal 2: F&B protocol checks

# Modbus: Read mixer speed (HR 1000-1001, CDAB float32)
python3 -c "
import asyncio, struct
from pymodbus.client import AsyncModbusTcpClient
async def check():
    c = AsyncModbusTcpClient('localhost', port=502)
    await c.connect()

    # Mixer speed: HR 1000-1001 (CDAB)
    r = await c.read_holding_registers(1000, count=2, slave=1)
    # CDAB: r[0]=CD, r[1]=AB -> reassemble as AB CD
    raw = struct.pack('>HH', r.registers[1], r.registers[0])
    speed = struct.unpack('>f', raw)[0]
    print(f'Mixer speed (HR 1000-1001, CDAB): {speed:.2f} RPM')

    # Oven zone 1 temp: HR 1100-1101 (ABCD)
    r2 = await c.read_holding_registers(1100, count=2, slave=1)
    raw2 = struct.pack('>HH', r2.registers[0], r2.registers[1])
    temp = struct.unpack('>f', raw2)[0]
    print(f'Oven zone 1 temp (HR 1100-1101, ABCD): {temp:.1f} C')

    # Multi-slave: Eurotherm UID 11, IR 0 (zone 1 PV)
    r3 = await c.read_input_registers(0, count=3, slave=11)
    pv = r3.registers[0] / 10.0  # int16 x10
    sp = r3.registers[1] / 10.0
    power = r3.registers[2] / 10.0
    print(f'Eurotherm UID 11: PV={pv:.1f}C, SP={sp:.1f}C, Power={power:.1f}%')

    # Chiller coils and DI
    r4 = await c.read_coils(101, count=2, slave=1)  # compressor, defrost
    print(f'Chiller compressor (coil 101): {r4.bits[0]}')
    print(f'Chiller defrost (coil 102): {r4.bits[1]}')

    r5 = await c.read_discrete_inputs(100, count=1, slave=1)
    print(f'Chiller door_open (DI 100): {r5.bits[0]}')

    c.close()
asyncio.run(check())
"

# OPC-UA: Read F&B nodes
python3 -c "
import asyncio
from asyncua import Client
async def check():
    c = Client('opc.tcp://localhost:4840')
    await c.connect()
    nodes = [
        'ns=2;s=FoodBevLine.Mixer1.State',
        'ns=2;s=FoodBevLine.Mixer1.BatchId',
        'ns=2;s=FoodBevLine.Oven1.State',
        'ns=2;s=FoodBevLine.Filler1.LineSpeed',
        'ns=2;s=FoodBevLine.Filler1.FillWeight',
        'ns=2;s=FoodBevLine.QC1.ActualWeight',
        'ns=2;s=FoodBevLine.CIP1.State',
    ]
    for nid in nodes:
        node = c.get_node(nid)
        val = await node.read_value()
        print(f'{nid.split(\".\", 1)[1]}: {val}')
    await c.disconnect()
asyncio.run(check())
"

# MQTT: Subscribe to F&B topics
mosquitto_sub -h localhost -t "collatr/factory/foodbev1/#" -v -W 5
```

**Pass criteria:**
- CDAB-decoded mixer values are physically reasonable (not garbage)
- Multi-slave UIDs 11-13 respond with oven temperatures (not Modbus exceptions)
- OPC-UA FoodBevLine nodes exist and return values
- MQTT publishes on `foodbev1/` prefix (not `packaging1/`)
- No vibration topics on F&B

### Step 3: CollatrEdge Integration — Packaging

```bash
# Ensure packaging simulator is running (10x, seed 42)
python3 scripts/run-simulator.py --time-scale 10 --seed 42

# Start CollatrEdge with packaging config
cd repos/collatr-edge
bun run src/main.ts --config ../collatr-factory-simulator/configs/collatr-edge-packaging.toml

# Watch logs for:
# - "Connected to Modbus TCP at localhost:502"
# - "Connected to OPC-UA at opc.tcp://localhost:4840"
# - "Connected to MQTT broker at tcp://localhost:1883"
# - Data flowing on all three protocols
# - No persistent errors after initial connection

# Let it run for 1 minute (= 10 sim minutes at 10x)
# Then Ctrl+C CollatrEdge

# Run verification
python3 repos/collatr-factory-simulator/scripts/verify-collection.py \
  --data-dir ./data/factory-sim-packaging \
  --tier smoke
```

### Step 4: CollatrEdge Integration — F&B

**NOTE:** A CollatrEdge config for the F&B profile (`configs/collatr-edge-foodbev.toml`) does not exist yet. It needs to be created before this step. The config must:

- Map all 68 F&B signals across Modbus, OPC-UA, MQTT
- Use CDAB byte order for mixer Modbus registers (HR 1000-1011)
- Include multi-slave reads for UIDs 11, 12, 13 (Eurotherm oven controllers)
- Read coils 100-102 and DI 100 for chiller/mixer discrete signals
- Subscribe to `collatr/factory/foodbev1/#` MQTT topics
- Connect OPC-UA to `FoodBevLine.*` nodes

Creating this config is a significant task (see Appendix A below for a starter). For the smoke test, the manual protocol checks in Step 2 are sufficient to validate the simulator serves correct data.

### Step 5: Ground Truth Verification

After any Medium or Full tier run:

```bash
# Check ground truth log exists
ls -la /tmp/factory-sim-ground-truth.jsonl  # or wherever the engine writes it

# Count scenario events by type
python3 -c "
import json, sys
from collections import Counter
counts = Counter()
with open('/tmp/factory-sim-ground-truth.jsonl') as f:
    for line in f:
        evt = json.loads(line)
        counts[evt.get('scenario_type', 'unknown')] += 1
for k, v in sorted(counts.items()):
    print(f'  {k}: {v}')
print(f'Total events: {sum(counts.values())}')
"
```

**Expected F&B scenario events in a Medium run (1 sim hour at 10x):**

| Scenario | Frequency | Expected in 1h |
|----------|-----------|-----------------|
| BatchCycle | 8-16/shift | 1-2 |
| OvenThermalExcursion | 1-2/shift | 0-1 |
| FillWeightDrift | 1-3/shift | 0-1 |
| SealIntegrityFailure | 1-2/week | unlikely |
| ChillerDoorAlarm | 1-3/week | unlikely |
| CipCycle | 1-3/day | 0-1 |
| ColdChainBreak | 1-2/month | very unlikely |

**Expected packaging scenario events in a Medium run:**

| Scenario | Frequency | Expected in 1h |
|----------|-----------|-----------------|
| ShiftChange | 3/day | 0-1 |
| JobChangeover | 3-6/shift | 1-2 |
| WebBreak | 1-2/week | unlikely |
| DryerDrift | 1-2/shift | 0-1 |
| InkExcursion | 2-3/shift | 0-1 |
| RegistrationDrift | 1-3/shift | 0-1 |
| ColdStart | per idle→active | possible |
| CoderDepletion | ~24h cycle | unlikely |
| MaterialSplice | ~3h cycle | possible |

---

## Pass/Fail Criteria

### Smoke (10 simulated minutes per profile)

| Criterion | Packaging | F&B |
|-----------|-----------|-----|
| Modbus HR readable | ✓ all 29 signals | ✓ all 31 HR signals |
| Modbus IR readable | ✓ | ✓ (including multi-slave UIDs 11-13) |
| Modbus coils/DI | N/A (packaging has none) | ✓ coils 100-102, DI 100 |
| CDAB decoding | N/A | ✓ mixer values are physically reasonable |
| OPC-UA nodes exist | ✓ all packaging nodes | ✓ all FoodBevLine nodes |
| MQTT topics published | ✓ packaging1/* topics | ✓ foodbev1/* topics |
| No vibration on F&B | N/A | ✓ no vibration/* topics |
| Values in range | ✓ | ✓ |
| No errors | ✓ no persistent errors | ✓ no persistent errors |

**PASS:** All cells are ✓.
**FAIL:** Any protocol fails to connect, values are garbage/NaN, or signals are missing.

### Medium (1 simulated hour per profile)

Smoke criteria PLUS:
- At least one state transition per profile
- Counters incremented during Running state
- Cross-protocol consistency (same value via Modbus and OPC-UA within float32 precision)
- At least one scenario event in ground truth log

### Full (8 simulated hours per profile)

Medium criteria PLUS:
- At least 3 distinct packaging scenario types fired
- At least 3 distinct F&B scenario types fired (BatchCycle is guaranteed; others depend on scheduling)
- Memory stable (RSS not growing unbounded over the run)
- No protocol server crashes or disconnects

---

## Troubleshooting

**Simulator won't start:**
- Check Python 3.12+: `python3 --version`
- Check package installed: `python3 -c "import factory_simulator"`
- Check config file exists: `ls config/factory.yaml config/factory-foodbev.yaml`

**Modbus connection refused:**
- Simulator must be running first
- Check port 502 is not already in use: `lsof -i :502`
- On macOS, port 502 may need sudo or use a higher port

**CDAB values look wrong:**
- Verify you're swapping registers correctly: `struct.pack('>HH', r[1], r[0])` not `struct.pack('>HH', r[0], r[1])`
- Only mixer signals use CDAB; all other F&B equipment uses standard ABCD

**Multi-slave Modbus returns exception:**
- Check slave ID is correct (11, 12, or 13 for oven zones)
- Secondary slaves only serve IR (input registers), not HR/coils/DI
- Reading HR from UID 11 will return a Modbus exception — this is correct

**OPC-UA connection timeout:**
- asyncua server takes 2-5 seconds to start
- Check: look for "OPC-UA server started" in simulator logs

**MQTT broker not reachable:**
- The simulator connects to `mqtt-broker` hostname by default (Docker Compose networking)
- When running the simulator outside Docker, override `MQTT_BROKER_HOST=localhost` or edit the config YAML to set `broker_host: "localhost"`

**No MQTT messages received:**
- Check you're subscribing to the right topic prefix: `collatr/factory/packaging1/#` or `collatr/factory/foodbev1/#`
- Check the simulator's MQTT publisher started without errors
- Try: `mosquitto_sub -h localhost -t "#" -v -W 5` to see ALL topics

---

## Appendix A: F&B CollatrEdge Config (Starter)

A full `collatr-edge-foodbev.toml` needs to be written. Key differences from the packaging config:

```toml
# Key mapping differences for F&B:

[global_tags]
  profile = "foodbev"

# Modbus: CDAB byte order for mixer (slave_id=1)
[[inputs.modbus]]
  alias = "fnb_mixer"
  controller = "tcp://localhost:502"
  slave_id = 1
  byte_order = "CDAB"         # Allen-Bradley byte order
  # Registers: HR 1000-1011

# Modbus: ABCD for everything else (slave_id=1)
[[inputs.modbus]]
  alias = "fnb_main"
  controller = "tcp://localhost:502"
  slave_id = 1
  byte_order = "ABCD"
  # Registers: HR 1100-1507, IR 100-121, coils 100-102, DI 100

# Modbus: Eurotherm oven zone controllers (slave_id=11,12,13)
[[inputs.modbus]]
  alias = "fnb_oven_zone1"
  controller = "tcp://localhost:502"
  slave_id = 11
  byte_order = "ABCD"
  # Registers: IR 0-2 (int16, scale /10)

[[inputs.modbus]]
  alias = "fnb_oven_zone2"
  controller = "tcp://localhost:502"
  slave_id = 12

[[inputs.modbus]]
  alias = "fnb_oven_zone3"
  controller = "tcp://localhost:502"
  slave_id = 13

# OPC-UA: FoodBevLine nodes
[[inputs.opcua]]
  endpoint = "opc.tcp://localhost:4840"
  # Nodes: FoodBevLine.Mixer1.*, FoodBevLine.Filler1.*, etc.

# MQTT: foodbev1 topics (no vibration)
[[inputs.mqtt_consumer]]
  servers = ["tcp://localhost:1883"]
  topics = ["collatr/factory/foodbev1/#"]
```

This is a starting point. The full config needs all 68 signal register/node/topic mappings. Use `configs/collatr-edge-packaging.toml` as the template and cross-reference with `config/factory-foodbev.yaml` for addresses.

---

## Appendix B: Verification Script Updates Needed

The existing `scripts/verify-collection.py` only knows packaging signals. For full F&B verification, it needs:

1. An `FNB_SIGNALS` dictionary with all 68 F&B signal ranges
2. F&B-specific `MODBUS_SIGNALS`, `OPCUA_SIGNALS`, `MQTT_SIGNALS` sets
3. A `--profile` flag to select packaging vs F&B signal definitions
4. F&B monotonic counters: `filler.packs_produced`, `filler.reject_count`, `qc.overweight_count`, `qc.underweight_count`, `qc.metal_detect_trips`, `qc.reject_total`, `coder.prints_total`, `coder.ink_consumption_ml`, `energy.cumulative_kwh`

These updates should be done as part of the acceptance test preparation, not as a Phase 3 implementation task.
