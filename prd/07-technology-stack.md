# Technology Stack

## 7.1 Language Decision

Two candidates:

**Python.** Strengths: `pymodbus` is the most mature Modbus TCP library. `opcua-asyncio` (formerly python-opcua) is well-maintained and supports server mode. `paho-mqtt` is the standard MQTT client. `aedes` equivalent (HBMQTT or aMQTT) exists for embedded broker. NumPy for efficient signal generation. Strong ecosystem for scientific computing and signal processing.

**Bun/TypeScript.** Strengths: Same language as CollatrEdge. `node-opcua` is the most feature-complete OPC-UA implementation in any language. `modbus-serial` or `jsmodbus` for Modbus. `aedes` for embedded MQTT broker. Shared types and configuration models with CollatrEdge. Single deployment stack.

**Decision: Python.**

Rationale:

1. **Protocol library maturity.** `pymodbus` handles all four Modbus function codes, configurable byte ordering, error injection, and slave simulation out of the box. The Node.js Modbus server libraries are thinner. `opcua-asyncio` is mature for server-side use. `node-opcua` is excellent but its documentation is oriented toward client use. Server creation in `node-opcua` requires more boilerplate.

2. **Signal generation.** NumPy array operations generate 47 signals (packaging) or 65 signals (F&B) per tick faster than per-value JavaScript loops. The correlation model involves matrix operations (applying transforms across signal vectors) that NumPy handles natively.

3. **The simulator is not CollatrEdge.** The simulator is a test tool. It does not ship to customers. It does not need to share CollatrEdge's runtime. Choosing the best tool for the job is more important than language consistency. CollatrEdge collects data. The simulator generates data. Different jobs.

4. **Cross-stack testing finds more bugs.** Python on the server side exercises different protocol implementation code paths than Bun on the client side. Bugs masked by shared library behaviour in a same-stack test surface when the implementations differ. A `pymodbus` server and an `asyncua` OPC-UA server will encode registers, handle sessions, and manage subscriptions differently than the Node.js libraries CollatrEdge uses. This is a feature. The simulator should stress CollatrEdge's protocol handling, not confirm that one library talks to itself correctly.

5. **Deployment isolation.** The simulator runs in Docker. The language inside the container is invisible to CollatrEdge. They communicate over network protocols. Language alignment across the network boundary has no engineering benefit.

6. **Development speed.** Python prototyping is faster for this type of tool. The signal models, correlation engine, and scenario system are computationally straightforward. Python's expressiveness reduces boilerplate.

## 7.1.1 OPC-UA Server Choice

The OPC-UA server is `asyncua` (formerly `opcua-asyncio`). Three options were evaluated:

1. **`asyncua`** (chosen). Pure Python, async-native, mature server mode. Handles custom node trees, subscriptions, data change notifications, engineering units, and status codes. Active development. Not OPC Foundation certified. Sufficient for a test tool where we control both sides.

2. **`open62541`** (post-MVP option). The C reference implementation from the OPC Foundation. Python bindings exist via `python-open62541`. More compliant, faster, handles edge cases that `asyncua` may not. Harder to integrate with a pure-Python signal engine. Worth adding as an alternative backend after MVP to test CollatrEdge against a stricter OPC-UA server.

3. **Microsoft OPC PLC** (rejected). Azure IoT Edge demo tool. Generates canned signals from a black box. Cannot wire to a custom data generation engine with 65 signals, correlation models, and scenario injection. Wrong tool for the job.

The MVP uses `asyncua`. Post-MVP, the OPC-UA server layer should support swapping to `open62541` via configuration. This gives two levels of protocol compliance testing: `asyncua` for development speed, `open62541` for stricter conformance testing before releases.

## 7.2 MQTT Broker Decision

The simulator requires an MQTT broker. Three options were evaluated:

1. **Mosquitto sidecar** (chosen). The Eclipse Mosquitto broker runs as a separate Docker container alongside the simulator. The simulator publishes via `paho-mqtt` as a client. Mosquitto is the industry standard: 17 years old, Eclipse Foundation, 100+ contributors, actively maintained, MQTT 3.1.1 and 5.0 support, ~12MB Alpine Docker image. At our volumes (50 msg/s peak), Mosquitto uses negligible resources. Every IIoT tutorial, Sparkplug B example, and SCADA integration guide uses Mosquitto. Testing against the same broker that customers run in production is a feature.

2. **NanoMQ sidecar** (rejected). Multi-threaded C broker from EMQ Technologies, MIT licensed. Handles 1M+ msg/s QoS 0, multi-core scaling. Impressive engineering, but designed for edge gateways aggregating thousands of devices. Our 50 msg/s load does not justify the smaller community, fewer tutorials, and less universal tooling. Mosquitto handles 120k msg/s on a single core.

3. **amqtt embedded** (rejected). Pure-Python MQTT broker running inside the simulator process. The only option that avoids a sidecar container. Rejected because: beta release (0.11.0b1), last PyPI update 2023, 89 open issues, no MQTT 5.0, not actively maintained. Two independent implementation reviewers flagged it as the weakest dependency in the entire stack. The single-container convenience does not justify the risk.

The simulator always connects to Mosquitto as a client using `paho-mqtt`. The Docker Compose file includes the Mosquitto sidecar by default. For environments where an external MQTT broker already exists (e.g., a factory EMQX instance), the simulator can point to that broker instead.

## 7.3 Dependencies

**Core:**

| Package | Version | Purpose |
|---------|---------|---------|
| `pymodbus` | >=3.6,<4.0 | Modbus TCP server |
| `asyncua` (opcua-asyncio) | >=1.1.5 | OPC-UA server |
| `paho-mqtt` | >=2.0 | MQTT client (publishes to external broker) |
| `numpy` | >=1.26 | Signal generation, noise, correlation |
| `pyyaml` | >=6.0 | Configuration file parsing |
| `uvloop` | >=0.19 | Fast asyncio event loop (Linux only) |

**External services (Docker sidecar):**

| Service | Image | Purpose |
|---------|-------|---------|
| Mosquitto | `eclipse-mosquitto:2` | MQTT broker |

**Optional:**

| Package | Purpose |
|---------|---------|
| `sparkplug-b` | Sparkplug B payload encoding (Phase 2) |
| `rich` | Terminal UI for development monitoring |
| `prometheus-client` | Metrics export for monitoring simulator health |
| `fastapi` + `uvicorn` | Health check and web dashboard endpoint |

## 7.4 Python Version

Python 3.12 or later. The `asyncio` improvements in 3.12 (TaskGroup, ExceptionGroup) simplify the concurrent protocol server management.

## 7.5 Docker Base Image

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/

EXPOSE 502 4840 8080

CMD ["python", "-m", "src.main"]
```

## 7.6 Development Environment

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # adds pytest, ruff, mypy

# Run locally (no Docker)
python -m src.main --config config/factory.yaml

# Run tests
pytest tests/

# Type checking
mypy src/

# Linting
ruff check src/
```

**Platform note:** `uvloop` requires Linux. On macOS during development, the code falls back to the default asyncio event loop via conditional import: `try: import uvloop; uvloop.install() except ImportError: pass`. The default event loop is 2-4x slower. Signal generation at 10x compression should still work on the default loop (signal generation uses less than 1% of per-tick budget), but verify during development.
