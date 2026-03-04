# Collatr Factory Simulator — Docker image
# Base: python:3.12-slim for minimal footprint
#
# Build:  docker build -t collatr-factory-simulator .
# Run:    docker run --rm -p 502:502 -p 4840:4840 -p 8080:8080 collatr-factory-simulator
#
# PRD Reference: Section 6.3 (Docker Compose), Task 5.10

FROM python:3.12-slim

# Install curl for the Docker health check; keep layer small
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install Python dependencies first (layer cache optimisation)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source tree and config
COPY src/ src/
COPY config/ config/
COPY pyproject.toml .

# Install the package in editable mode so imports resolve correctly
RUN pip install --no-cache-dir -e .

# Expose ports used in collapsed mode
#   502  — Modbus TCP
#   1883 — MQTT (Mosquitto sidecar; exposed here for standalone operation)
#   4840 — OPC-UA
#   8080 — Health / status endpoint
EXPOSE 502 1883 4840 8080

# Docker health check — polls /health every 10 s after a 15 s grace period
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Default entry point: real-time collapsed mode, packaging profile
ENTRYPOINT ["python", "-m", "factory_simulator", "run"]
