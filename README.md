# Collatr Factory Simulator

A standalone Python simulator for manufacturing factories over industrial protocols (Modbus TCP, OPC-UA, MQTT). Generates realistic signal data with configurable noise, scenarios, and data quality issues for integration testing, demos, and development of CollatrEdge.

## Quick Start (15 minutes)

**Prerequisites:** Docker, Docker Compose v2, `git`

```bash
# Clone the repository
git clone https://github.com/doubly-good/collatr-factory-simulator.git
cd collatr-factory-simulator

# Start the simulator (packaging profile, collapsed mode)
docker compose up -d

# Verify the simulator is healthy
curl http://localhost:8080/health
# Expected: {"status": "running", "profile": "packaging", "signals": 47, ...}

# Connect CollatrEdge using the example config
collatr-edge --config configs/collatr-edge-packaging.toml
```

Data starts flowing immediately. The health endpoint responds within 15 seconds of `docker compose up`.

### Verify Protocol Connectivity

```bash
# Modbus: read press line speed (HR 100, float32 ABCD)
python3 -c "
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient('localhost', port=502)
c.connect()
r = c.read_holding_registers(100, 2, slave=1)
import struct
val = struct.unpack('>f', struct.pack('>HH', r.registers[0], r.registers[1]))[0]
print(f'line_speed = {val:.1f} m/min')
c.close()
"

# OPC-UA: browse root node
python3 -c "
import asyncio
from asyncua import Client
async def main():
    async with Client('opc.tcp://localhost:4840') as c:
        root = await c.get_objects_node()
        children = await root.get_children()
        print([str(c) for c in children])
asyncio.run(main())
"

# MQTT: subscribe to all topics
mosquitto_sub -h localhost -p 1883 -t 'collatr/factory/#' -v
```

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────────┐
│                  Factory Simulator Process                 │
│                                                            │
│  ┌──────────────┐   ┌──────────────────────────────────┐   │
│  │ Signal Engine│──▶│ Signal Store (thread-safe read)  │   │
│  │ (asyncio,    │   │ 47 (packaging) / 68 (F&B) signals│   │
│  │  100ms tick) │   └───────────┬──────────────────────┘   │
│  └──────────────┘               │                          │
│         │                       ▼                          │
│  ┌──────┴───────┐  ┌─────────────────────────────────┐     │
│  │ Scenario Eng.│  │ Protocol Adapters               │     │
│  │ (Poisson,    │  │ ┌──────────┐ ┌───────┐ ┌──────┐ │     │
│  │  priority)   │  │ │ Modbus   │ │OPC-UA │ │ MQTT │ │     │
│  └──────────────┘  │ │ TCP      │ │Server │ │ Pub. │ │     │
│                    │ │ (pymod.) │ │(async │ │(paho)│ │     │
│  ┌──────────────┐  │ │          │ │  ua)  │ │      │ │     │
│  │ Ground Truth │  │ └──────────┘ └───────┘ └──────┘ │     │
│  │ JSONL Logger │  └─────────────────────────────────┘     │
│  └──────────────┘                                          │
│                    ┌──────────────────────────────────┐    │
│                    │ Health Server (port 8080)        │    │
│                    │ GET /health  GET /status         │    │
│                    └──────────────────────────────────┘    │
└────────────────────────────────────────────────────────────┘
         ▲                    ▲                   ▲
         │ Modbus TCP         │ OPC-UA            │ MQTT
      :502 / 5020-5035     :4840-4842          :1883 (Mosquitto)
```

The signal engine runs a single asyncio event loop. It updates **all** signals for one 100ms tick before any protocol adapter reads the store — ensuring clients always see a consistent snapshot. The engine is the sole writer; protocol adapters are read-only.

For a deeper architectural reference see `prd/08-architecture.md`.

---

## Two Profiles

### Packaging and Printing

47 signals across 7 equipment groups. Simulates a flexographic printing and lamination line.

| Equipment | Signals | Protocols |
|-----------|---------|-----------|
| Flexographic Press | 21 | Modbus HR, OPC-UA |
| Laminator | 5 | Modbus HR, OPC-UA |
| Slitter | 3 | Modbus HR, OPC-UA |
| CIJ Coder | 11 | MQTT |
| Environmental | 2 | MQTT |
| Energy Meter | 2 | Modbus HR, OPC-UA |
| Vibration | 3 | MQTT |

Start with the packaging profile (default):

```bash
docker compose up -d
# or
python -m factory_simulator run --config config/factory.yaml --profile packaging
```

### Food and Beverage (Chilled Ready Meal)

68 signals across 10 equipment groups. Simulates a ready meal production line with chilled distribution.

| Equipment | Signals | Protocols |
|-----------|---------|-----------|
| Batch Mixer | 6 | Modbus HR (CDAB) |
| Tunnel Oven (3 zones) | 12 | Modbus HR, OPC-UA |
| Filler / Depositor | 9 | Modbus HR, OPC-UA |
| Heat Sealer | 7 | Modbus HR, OPC-UA |
| Checkweigher / QC | 7 | OPC-UA |
| Chiller Room | 9 | Modbus HR |
| CIP System | 8 | Modbus HR |
| Environmental | 2 | MQTT |
| Energy Meters (×2) | 4 | Modbus HR |
| MQTT (traceability) | 4 | MQTT |

Start with the F&B profile:

```bash
docker compose up -d -e SIM_CONFIG_PATH=config/factory-foodbev.yaml
# or
python -m factory_simulator run --config config/factory-foodbev.yaml --profile foodbev
```

---

## Protocol Endpoints

### Collapsed Mode (default, development)

Single port per protocol. All controllers share one address.

| Protocol | Address | Notes |
|----------|---------|-------|
| Modbus TCP | `localhost:502` | Unit ID 1 (press/oven/etc), UID 5 (energy) |
| OPC-UA | `opc.tcp://localhost:4840` | Full node tree |
| MQTT | `tcp://localhost:1883` | Anonymous, no TLS |
| Health | `http://localhost:8080/health` | JSON status |

### Realistic Mode (integration testing)

Per-controller ports simulating real factory network fragmentation.

**Packaging profile realistic endpoints:**

| Controller | Port | UIDs | Byte Order | Controller Type |
|------------|------|------|------------|-----------------|
| Press PLC (S7-1500) | 5020 | 1 (press), 5 (energy) | ABCD | Siemens S7-1500 |
| Laminator PLC (S7-1200) | 5021 | 1 | ABCD | Siemens S7-1200 |
| Slitter PLC (S7-1200) | 5022 | 1 | ABCD | Siemens S7-1200 |
| OPC-UA (Press) | 4840 | n/a | n/a | Full PackagingLine tree |

**F&B profile realistic endpoints:**

| Controller | Port | UIDs | Byte Order | Controller Type |
|------------|------|------|------------|-----------------|
| Mixer (CompactLogix) | 5030 | 1 | CDAB | Allen-Bradley |
| Oven Gateway (Eurotherm) | 5031 | 1,2,3 (zones), 10 (energy) | ABCD | Eurotherm 3504 |
| Filler (S7-1200) | 5032 | 1 | ABCD | Siemens S7-1200 |
| Sealer (S7-1200) | 5033 | 1 | ABCD | Siemens S7-1200 |
| Chiller (Danfoss) | 5034 | 1 | ABCD | Danfoss |
| CIP (S7-1200) | 5035 | 1 | ABCD | Siemens S7-1200 |
| OPC-UA Filler | 4841 | n/a | n/a | FoodBevLine.Filler1 subtree |
| OPC-UA QC | 4842 | n/a | n/a | FoodBevLine.QC1 subtree |

Start realistic mode:

```bash
docker compose -f docker-compose.yml -f docker-compose.realistic.yaml up -d
```

Use `configs/collatr-edge-realistic.toml` to connect CollatrEdge to all per-controller endpoints simultaneously.

### MQTT Topics

**Packaging:**
```
collatr/factory/demo/packaging1/coder/#      (11 topics, QoS 0/1)
collatr/factory/demo/packaging1/env/#        (2 topics, QoS 0)
collatr/factory/demo/packaging1/vibration/#  (3 topics, QoS 0)
```

**F&B:**
```
collatr/factory/demo/foodbev1/filler/#       (4 topics, QoS 1)
collatr/factory/demo/foodbev1/env/#          (2 topics, QoS 0)
collatr/factory/demo/foodbev1/chiller/#      (3 topics, QoS 0)
collatr/factory/demo/foodbev1/cip/#          (4 topics, QoS 1)
```

Full topic listings: `prd/appendix-c-mqtt-topic-map.md`

---

## Configuration Reference

Configuration is YAML. Two factory configs are provided:

- `config/factory.yaml` — packaging profile (default)
- `config/factory-foodbev.yaml` — F&B profile

### Key Parameters

```yaml
simulation:
  time_scale: 1.0       # 1.0 = real-time, 10.0 = 10x speed, 100.0 = batch
  random_seed: null     # null = time-based; integer = deterministic
  tick_interval_ms: 100 # internal engine tick rate

protocols:
  modbus:
    enabled: true
    port: 502
  opcua:
    enabled: true
    port: 4840
  mqtt:
    broker_host: "mqtt-broker"
    broker_port: 1883

network:
  mode: "collapsed"     # "collapsed" (default) or "realistic"

batch_output:
  format: "none"        # "none", "csv", or "parquet"
  path: "./output"
  buffer_size: 10000
```

Full parameter reference: `prd/appendix-d-configuration-reference.md`

### Environment Variable Overrides

| Variable | Description | Example |
|----------|-------------|---------|
| `SIM_CONFIG_PATH` | Config file path | `config/factory-foodbev.yaml` |
| `SIM_SEED` | Random seed | `42` |
| `SIM_TIME_SCALE` | Time compression | `10` |
| `SIM_LOG_LEVEL` | Log level | `debug` |
| `SIM_NETWORK_MODE` | Network mode | `realistic` |
| `MQTT_BROKER_HOST` | MQTT broker hostname | `localhost` |
| `MQTT_BROKER_PORT` | MQTT broker port | `1883` |

---

## Batch Mode

Batch mode runs the signal engine without live protocol servers, writing output to CSV or Parquet files. Use for generating training data, replaying through CollatrEdge offline, or evaluation runs.

```bash
# 24-hour simulation at 10x, CSV output (~2.4 real minutes)
python -m factory_simulator run \
  --batch-output ./output/run1 \
  --batch-duration 24h \
  --batch-format csv \
  --time-scale 10 \
  --seed 42

# 7-day simulation at 100x, Parquet output (~1.7 real hours)
python -m factory_simulator run \
  --config config/factory.yaml \
  --batch-output ./output/run2 \
  --batch-duration 7d \
  --batch-format parquet \
  --time-scale 100 \
  --seed 42
```

### Output Files

| File | Format | Description |
|------|--------|-------------|
| `signals.csv` | CSV (long) | `timestamp, signal_id, value, quality` |
| `signals.parquet` | Parquet (wide) | One column per signal, timestamp index |
| `ground_truth.jsonl` | JSONL | Scenario events with start/end times and severity |

Event-driven signals (`machine_state`, `fault_code`) appear only on state transitions in CSV output. In Parquet, they appear every row with a companion `_changed` boolean column.

---

## Evaluation Framework

The evaluation framework measures how well an anomaly detection system performs against the simulator's ground truth.

### Basic Usage

```bash
# Step 1: Generate simulation data with ground truth
python -m factory_simulator run \
  --config config/scenarios/normal-operations.yaml \
  --batch-output ./output/run_a \
  --batch-format csv \
  --seed 1

# Step 2: Run your anomaly detector against the CSV output
# (produces detections.csv with columns: timestamp, alert_type, signal_id, confidence)

# Step 3: Evaluate detector performance
python -m factory_simulator evaluate \
  --ground-truth ./output/run_a/ground_truth.jsonl \
  --detections ./output/run_a/detections.csv
```

### Example Output

```
Evaluation Results
==================
Overall:  precision=0.72  recall=0.68  F1=0.70
Weighted: recall=0.61  F1=0.65
Latency:  median=12s  p90=45s

Per-scenario breakdown:
  web_break         TP=3  FP=0  FN=1  precision=1.00  recall=0.75
  dryer_drift       TP=8  FP=2  FN=0  precision=0.80  recall=1.00
  bearing_wear      TP=1  FP=0  FN=0  precision=1.00  recall=1.00
  micro_stop        TP=2  FP=1  FN=3  precision=0.67  recall=0.40

Random baseline:  precision=0.04  recall=0.31  F1=0.07
```

### Standard Evaluation Runs

Three standard configs from PRD Section 12.5:

| Run | Config | Duration | Time Scale | Purpose |
|-----|--------|----------|------------|---------|
| Run A | `config/scenarios/normal-operations.yaml` | 24h | 10x | False positive rate |
| Run B | `config/scenarios/heavy-anomaly.yaml` | 24h | 10x | Detection rate |
| Run C | `config/scenarios/long-term-degradation.yaml` | 7d | 100x | Trend detection |

### Multi-Seed Evaluation

```bash
# Run evaluation across 10 seeds for confidence intervals
python -m factory_simulator evaluate \
  --ground-truth output/run_a_seed1/ground_truth.jsonl,output/run_a_seed2/ground_truth.jsonl \
  --detections output/run_a_seed1/detections.csv,output/run_a_seed2/detections.csv \
  --multi-seed
```

Full evaluation protocol: `prd/12-evaluation-protocol.md`

---

## Development Setup

```bash
# Python 3.12+ required
python3 --version

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e .

# Run tests
pytest tests/unit -v

# Run full test suite (takes up to 15 minutes)
ruff check src tests && mypy src && pytest

# Run integration tests (requires Docker Compose running)
pytest tests/integration -m integration -v

# Lint and type check
ruff check src tests
mypy src
```

### Project Structure

```
collatr-factory-simulator/
├── config/
│   ├── factory.yaml               # Packaging profile config
│   ├── factory-foodbev.yaml       # F&B profile config
│   ├── mosquitto.conf             # Mosquitto broker config
│   └── scenarios/                 # Standard evaluation run configs
│       ├── normal-operations.yaml  # Run A
│       ├── heavy-anomaly.yaml      # Run B
│       └── long-term-degradation.yaml  # Run C
├── configs/                       # CollatrEdge example configs (TOML)
│   ├── collatr-edge-packaging.toml   # Packaging profile, collapsed mode
│   ├── collatr-edge-foodbev.toml     # F&B profile, collapsed mode
│   └── collatr-edge-realistic.toml   # Packaging profile, realistic mode
├── src/factory_simulator/
│   ├── cli.py                     # CLI entry point
│   ├── config.py                  # Pydantic config models
│   ├── topology.py                # Network topology manager
│   ├── engine/                    # Signal engine
│   ├── generators/                # Equipment signal generators
│   ├── scenarios/                 # Scenario implementations
│   ├── protocols/                 # Modbus, OPC-UA, MQTT adapters
│   ├── evaluation/                # Evaluation framework
│   ├── output/                    # Batch CSV/Parquet writers
│   └── health/                    # Health endpoint server
├── prd/                           # Product requirements (23 files)
├── plans/                         # Phase implementation plans
├── tests/
│   ├── unit/                      # Unit tests (no external deps)
│   └── integration/               # Integration tests (Docker required)
├── examples/evaluation/           # PRD 12.5 run configs
├── docker-compose.yml             # Collapsed mode deployment
├── docker-compose.realistic.yaml  # Realistic mode port overrides
└── Dockerfile
```

---

## CLI Reference

```bash
# Start simulator (default: packaging profile, real-time, collapsed)
python -m factory_simulator run

# Start with F&B profile
python -m factory_simulator run --config config/factory-foodbev.yaml

# Start in realistic mode
python -m factory_simulator run --network-mode realistic

# Batch mode (7 days, 100x, CSV)
python -m factory_simulator run \
  --batch-output ./output \
  --batch-duration 7d \
  --batch-format csv \
  --time-scale 100 \
  --seed 42

# Evaluate detections against ground truth
python -m factory_simulator evaluate \
  --ground-truth output/ground_truth.jsonl \
  --detections output/detections.csv

# Print version
python -m factory_simulator version

# Full help
python -m factory_simulator --help
python -m factory_simulator run --help
python -m factory_simulator evaluate --help
```

### All Run Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | `config/factory.yaml` | YAML config file path |
| `--profile` | from config | `packaging` or `foodbev` |
| `--seed` | from config | Random seed (integer) |
| `--time-scale` | from config | Time compression factor |
| `--network-mode` | `collapsed` | `collapsed` or `realistic` |
| `--batch-output` | disabled | Output directory for batch mode |
| `--batch-duration` | unlimited | Simulation duration (`7d`, `24h`, `30m`, `3600s`) |
| `--batch-format` | `csv` | `csv` or `parquet` |
| `--log-level` | `info` | `debug`, `info`, `warning`, `error` |

---

## Docker Reference

### Collapsed Mode (default)

```bash
# Start
docker compose up -d

# Stop
docker compose down

# View logs
docker compose logs -f factory-simulator

# Health check
curl http://localhost:8080/health
curl http://localhost:8080/status    # all current signal values
```

**Port mappings (collapsed mode):**

| Port | Protocol | Description |
|------|----------|-------------|
| 502 | Modbus TCP | Single server, all controllers |
| 4840 | OPC-UA | Full node tree |
| 1883 | MQTT | Mosquitto broker |
| 8080 | HTTP | Health and status endpoints |

### Realistic Mode

```bash
docker compose -f docker-compose.yml -f docker-compose.realistic.yaml up -d
```

**Additional port mappings (realistic mode):**

| Port Range | Protocol | Description |
|-----------|----------|-------------|
| 5020–5022 | Modbus TCP | Packaging: press, laminator, slitter |
| 5030–5035 | Modbus TCP | F&B: mixer, oven gateway, filler, sealer, chiller, CIP |
| 4841–4842 | OPC-UA | F&B: filler, QC/checkweigher |

### Environment Variable Overrides (Docker)

```bash
# Run F&B profile
docker compose up -d -e SIM_CONFIG_PATH=config/factory-foodbev.yaml

# Run with fixed seed and 10x speed
docker compose up -d -e SIM_SEED=42 -e SIM_TIME_SCALE=10

# Run in realistic mode
docker compose up -d -e SIM_NETWORK_MODE=realistic
```

---

## Scenarios

The simulator includes the following scenario types. See `prd/05-scenario-system.md` for full specifications.

| Scenario | Profile | Description | Severity |
|----------|---------|-------------|----------|
| `web_break` | Packaging | Web tension spike then drop to zero | 10 |
| `dryer_drift` | Packaging | Sustained dryer temperature deviation | 3 |
| `bearing_wear` | Packaging | Progressive vibration increase | 8 |
| `ink_viscosity_excursion` | Packaging | Ink viscosity out of range | 2 |
| `registration_drift` | Packaging | Print registration error growing | 2 |
| `unplanned_stop` | Both | Unexpected machine stop | 5 |
| `micro_stop` | Both | Sub-30s speed dip | 1 |
| `contextual_anomaly` | Both | Normal signal + wrong context | 5 |
| `intermittent_fault` | Both | Phase 1→2→3 escalating fault | 4 |
| `seal_integrity_failure` | F&B | Seal temperature below threshold | 8 |
| `cold_chain_break` | F&B | Chiller room temperature excursion | 10 |
| `oven_excursion` | F&B | Oven zone temperature deviation | 3 |
| `fill_weight_drift` | F&B | Fill weight bias drift | 3 |
| `job_changeover` | Both | Planned speed/counter reset | operational |
| `shift_change` | Both | 8-hour shift boundary | operational |

---

## Ground Truth Format

The simulator writes a `ground_truth.jsonl` sidecar during every run (batch and real-time). Each line is a JSON object:

```json
{"event": "start", "scenario": "web_break", "severity": 10, "sim_time": 3612.4, "timestamp": "2026-01-01T01:00:12.400Z", "affected_signals": ["press.web_tension"]}
{"event": "end",   "scenario": "web_break", "severity": 10, "sim_time": 3665.1, "timestamp": "2026-01-01T01:01:05.100Z"}
```

Fields:
- `event`: `"start"` or `"end"`
- `scenario`: scenario type name
- `severity`: PRD 12.4 severity weight (1-10)
- `sim_time`: simulated time in seconds since epoch start
- `timestamp`: UTC ISO 8601
- `affected_signals`: list of signal IDs (start events only)

The ground truth always uses true `sim_time`, never clock-drifted time.

---

## Known Limitations

The following PRD requirements are partially implemented or deferred:

| Limitation | Status | Detail |
|-----------|--------|--------|
| **Connection limit enforcement** | Config-only | `max_connections` per controller (PRD 3a.5) is stored in config and surfaced in the topology model, but pymodbus and asyncua do not natively support per-port TCP connection limits. Connections above `max_connections` are not rejected. A future implementation would require a custom server wrapper that tracks and rejects excess TCP connections. |
| **Response latency injection** | Config-only | `response_timeout_ms_typical` per controller (PRD 3a.5) is stored in config, but no per-request delay is injected into Modbus or OPC-UA read handlers. Adding latency would require a custom pymodbus request handler with `asyncio.sleep`. |

---

## PRD Reference

The full product requirements document is in `prd/`. Key files:

| File | Contents |
|------|----------|
| `prd/README.md` | Table of contents |
| `prd/02-simulated-factory-layout.md` | Packaging signals and equipment |
| `prd/02b-factory-layout-food-and-beverage.md` | F&B signals and equipment |
| `prd/03-protocol-endpoints.md` | Modbus registers, OPC-UA nodes, MQTT topics |
| `prd/03a-network-topology.md` | Per-controller network layout |
| `prd/04-data-generation-engine.md` | Signal models and noise |
| `prd/05-scenario-system.md` | All scenario types and effects |
| `prd/06-configuration.md` | YAML config structure |
| `prd/08-architecture.md` | Component design and concurrency |
| `prd/12-evaluation-protocol.md` | Evaluation framework |
| `prd/appendix-a-modbus-register-map.md` | Complete register maps |
| `prd/appendix-b-opcua-node-tree.md` | Complete OPC-UA node tree |
| `prd/appendix-c-mqtt-topic-map.md` | Complete MQTT topic map |
| `prd/appendix-d-configuration-reference.md` | All config parameters |
