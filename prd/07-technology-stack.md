# Technology Stack

## 7.1 Language Decision

Two candidates:

**Python.** Strengths: `pymodbus` is the most mature Modbus TCP library. `opcua-asyncio` (formerly python-opcua) is well-maintained and supports server mode. `paho-mqtt` is the standard MQTT client. `aedes` equivalent (HBMQTT or aMQTT) exists for embedded broker. NumPy for efficient signal generation. Strong ecosystem for scientific computing and signal processing.

**Bun/TypeScript.** Strengths: Same language as CollatrEdge. `node-opcua` is the most feature-complete OPC-UA implementation in any language. `modbus-serial` or `jsmodbus` for Modbus. `aedes` for embedded MQTT broker. Shared types and configuration models with CollatrEdge. Single deployment stack.

**Decision: Python.**

Rationale:

1. **Protocol library maturity.** `pymodbus` handles all four Modbus function codes, configurable byte ordering, error injection, and slave simulation out of the box. The Node.js Modbus server libraries are thinner. `opcua-asyncio` is mature for server-side use. `node-opcua` is excellent but its documentation is oriented toward client use. Server creation in `node-opcua` requires more boilerplate.

2. **Signal generation.** NumPy array operations generate 40 signals per tick faster than per-value JavaScript loops. The correlation model involves matrix operations (applying transforms across signal vectors) that NumPy handles natively.

3. **The simulator is not CollatrEdge.** The simulator is a test tool. It does not ship to customers. It does not need to share CollatrEdge's runtime. Choosing the best tool for the job is more important than language consistency. CollatrEdge collects data. The simulator generates data. Different jobs.

4. **Deployment isolation.** The simulator runs in Docker. The language inside the container is invisible to CollatrEdge. They communicate over network protocols. Language alignment across the network boundary has no engineering benefit.

5. **Development speed.** Python prototyping is faster for this type of tool. The signal models, correlation engine, and scenario system are computationally straightforward. Python's expressiveness reduces boilerplate.

## 7.2 Dependencies

**Core:**

| Package | Version | Purpose |
|---------|---------|---------|
| `pymodbus` | >=3.6 | Modbus TCP server |
| `asyncua` (opcua-asyncio) | >=1.1 | OPC-UA server |
| `paho-mqtt` | >=2.0 | MQTT client (for external broker mode) |
| `amqtt` | >=0.11 | Embedded MQTT broker |
| `numpy` | >=1.26 | Signal generation, noise, correlation |
| `pyyaml` | >=6.0 | Configuration file parsing |
| `uvloop` | >=0.19 | Fast asyncio event loop (Linux) |

**Optional:**

| Package | Purpose |
|---------|---------|
| `sparkplug-b` | Sparkplug B payload encoding (Phase 2) |
| `rich` | Terminal UI for development monitoring |
| `prometheus-client` | Metrics export for monitoring simulator health |
| `fastapi` + `uvicorn` | Health check and web dashboard endpoint |

## 7.3 Python Version

Python 3.12 or later. The `asyncio` improvements in 3.12 (TaskGroup, ExceptionGroup) simplify the concurrent protocol server management.

## 7.4 Docker Base Image

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/

EXPOSE 502 4840 1883 8080

CMD ["python", "-m", "src.main"]
```

## 7.5 Development Environment

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
