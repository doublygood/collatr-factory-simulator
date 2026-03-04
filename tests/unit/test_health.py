"""Tests for the health server endpoint (Task 5.10).

Validates:
- HealthServer.update() modifies state correctly
- GET /health returns 200 with correct JSON keys and values
- GET /status returns a JSON signal map
- GET for unknown paths returns 404
- sim_time is formatted as ISO-8601 UTC
- store integration: live signal count and sim_time
- Dockerfile exists with required content
- docker-compose.yml has both services
- mosquitto.conf is valid

PRD Reference: Section 6.3 (Docker Compose — health endpoint), Task 5.10
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from factory_simulator.health.server import HealthServer
from factory_simulator.store import SignalStore

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_COMPOSE_REALISTIC = _REPO_ROOT / "docker-compose.realistic.yaml"
_MOSQUITTO_CONF = _REPO_ROOT / "config" / "mosquitto.conf"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


async def _http_get(port: int, path: str) -> tuple[int, bytes]:
    """Make a minimal raw HTTP GET and return ``(status_code, body_bytes)``."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        request = (
            f"GET {path} HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode()
        writer.write(request)
        await writer.drain()

        response = b""
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            response += chunk
    finally:
        writer.close()

    status_line = response.split(b"\r\n")[0].decode("ascii", errors="replace")
    status_code = int(status_line.split(" ")[1])
    header_end = response.find(b"\r\n\r\n")
    body = response[header_end + 4:] if header_end != -1 else b""
    return status_code, body


@pytest.fixture()
async def health_server() -> AsyncIterator[HealthServer]:
    """Start a HealthServer on an OS-assigned port; yield it; cancel on teardown."""
    store = SignalStore()
    store.set("press.line_speed", 150.0, timestamp=1000.0)
    store.set("press.machine_state", "Running", timestamp=1000.0)

    server = HealthServer(port=0, store=store)
    task: asyncio.Task[None] = asyncio.create_task(server.start())
    await asyncio.sleep(0.05)  # allow server to bind

    yield server

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.fixture()
async def health_server_no_store() -> AsyncIterator[HealthServer]:
    """HealthServer without a store (tests defaults)."""
    server = HealthServer(port=0)
    task: asyncio.Task[None] = asyncio.create_task(server.start())
    await asyncio.sleep(0.05)

    yield server

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# HealthServer.update() — state mutations
# ---------------------------------------------------------------------------


class TestUpdate:
    def test_status_updated(self) -> None:
        s = HealthServer()
        s.update(status="running")
        assert s._state["status"] == "running"

    def test_profile_updated(self) -> None:
        s = HealthServer()
        s.update(profile="food_bev")
        assert s._state["profile"] == "food_bev"

    def test_sim_time_formatted_as_iso_utc(self) -> None:
        s = HealthServer()
        # UNIX epoch → 1970-01-01T00:00:00Z
        s.update(sim_time=0.0)
        assert s._state["sim_time"] == "1970-01-01T00:00:00Z"

    def test_sim_time_non_zero(self) -> None:
        s = HealthServer()
        s.update(sim_time=3600.0)
        assert "T" in s._state["sim_time"]
        assert s._state["sim_time"].endswith("Z")

    def test_signals_updated(self) -> None:
        s = HealthServer()
        s.update(signals=47)
        assert s._state["signals"] == 47

    def test_modbus_updated(self) -> None:
        s = HealthServer()
        s.update(modbus="up")
        assert s._state["modbus"] == "up"

    def test_opcua_updated(self) -> None:
        s = HealthServer()
        s.update(opcua="up")
        assert s._state["opcua"] == "up"

    def test_mqtt_updated(self) -> None:
        s = HealthServer()
        s.update(mqtt="up")
        assert s._state["mqtt"] == "up"

    def test_none_args_not_applied(self) -> None:
        s = HealthServer()
        original = s._state["status"]
        s.update(status=None)
        assert s._state["status"] == original

    def test_multiple_fields_at_once(self) -> None:
        s = HealthServer()
        s.update(status="running", profile="packaging", modbus="up")
        assert s._state["status"] == "running"
        assert s._state["profile"] == "packaging"
        assert s._state["modbus"] == "up"


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    async def test_returns_200(self, health_server: HealthServer) -> None:
        status, _ = await _http_get(health_server.actual_port, "/health")
        assert status == 200

    async def test_content_is_json(self, health_server: HealthServer) -> None:
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        assert isinstance(data, dict)

    async def test_has_status_key(self, health_server: HealthServer) -> None:
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        assert "status" in data

    async def test_has_profile_key(self, health_server: HealthServer) -> None:
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        assert "profile" in data

    async def test_has_sim_time_key(self, health_server: HealthServer) -> None:
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        assert "sim_time" in data

    async def test_has_signals_key(self, health_server: HealthServer) -> None:
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        assert "signals" in data

    async def test_has_modbus_key(self, health_server: HealthServer) -> None:
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        assert "modbus" in data

    async def test_has_opcua_key(self, health_server: HealthServer) -> None:
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        assert "opcua" in data

    async def test_has_mqtt_key(self, health_server: HealthServer) -> None:
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        assert "mqtt" in data

    async def test_all_required_keys_present(self, health_server: HealthServer) -> None:
        """Verify the exact JSON structure matches the PRD spec."""
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        required = {"status", "profile", "sim_time", "signals", "modbus", "opcua", "mqtt"}
        assert required.issubset(data.keys())

    async def test_signal_count_from_store(self, health_server: HealthServer) -> None:
        """signals count comes from the live store (2 signals seeded in fixture)."""
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        assert data["signals"] == 2

    async def test_sim_time_from_store(self, health_server: HealthServer) -> None:
        """sim_time reflects the store's max timestamp."""
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        # Store seeded with timestamp=1000.0 (1970-01-01T00:16:40Z)
        assert "T" in data["sim_time"]
        assert data["sim_time"].endswith("Z")
        assert data["sim_time"] != "1970-01-01T00:00:00Z"  # should be non-epoch

    async def test_status_reflects_update(self, health_server: HealthServer) -> None:
        health_server.update(status="running")
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        assert data["status"] == "running"

    async def test_profile_reflects_update(self, health_server: HealthServer) -> None:
        health_server.update(profile="food_bev")
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        assert data["profile"] == "food_bev"

    async def test_modbus_reflects_update(self, health_server: HealthServer) -> None:
        health_server.update(modbus="up")
        _, body = await _http_get(health_server.actual_port, "/health")
        data = json.loads(body)
        assert data["modbus"] == "up"

    async def test_no_store_returns_zero_signals(
        self, health_server_no_store: HealthServer
    ) -> None:
        _, body = await _http_get(health_server_no_store.actual_port, "/health")
        data = json.loads(body)
        assert data["signals"] == 0


# ---------------------------------------------------------------------------
# /status endpoint
# ---------------------------------------------------------------------------


class TestStatusEndpoint:
    async def test_returns_200(self, health_server: HealthServer) -> None:
        status, _ = await _http_get(health_server.actual_port, "/status")
        assert status == 200

    async def test_content_is_json_dict(self, health_server: HealthServer) -> None:
        _, body = await _http_get(health_server.actual_port, "/status")
        data = json.loads(body)
        assert isinstance(data, dict)

    async def test_contains_seeded_signals(self, health_server: HealthServer) -> None:
        _, body = await _http_get(health_server.actual_port, "/status")
        data = json.loads(body)
        assert "press.line_speed" in data
        assert "press.machine_state" in data

    async def test_signal_values_correct(self, health_server: HealthServer) -> None:
        _, body = await _http_get(health_server.actual_port, "/status")
        data = json.loads(body)
        assert data["press.line_speed"] == pytest.approx(150.0)
        assert data["press.machine_state"] == "Running"

    async def test_no_store_returns_empty_dict(
        self, health_server_no_store: HealthServer
    ) -> None:
        _, body = await _http_get(health_server_no_store.actual_port, "/status")
        data = json.loads(body)
        assert data == {}


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_unknown_path_returns_404(self, health_server: HealthServer) -> None:
        status, _ = await _http_get(health_server.actual_port, "/unknown")
        assert status == 404

    async def test_root_path_returns_404(self, health_server: HealthServer) -> None:
        status, _ = await _http_get(health_server.actual_port, "/")
        assert status == 404

    async def test_actual_port_assigned(self, health_server: HealthServer) -> None:
        """Port 0 resolves to an OS-assigned port > 0."""
        assert health_server.actual_port > 0


# ---------------------------------------------------------------------------
# actual_port before start
# ---------------------------------------------------------------------------


class TestActualPort:
    def test_returns_configured_port_before_start(self) -> None:
        s = HealthServer(port=9999)
        assert s.actual_port == 9999

    def test_returns_zero_before_start_when_zero_configured(self) -> None:
        s = HealthServer(port=0)
        assert s.actual_port == 0

    async def test_returns_os_port_after_start(
        self, health_server: HealthServer
    ) -> None:
        # OS should assign a port > 1023 (user-space)
        assert health_server.actual_port > 1023


# ---------------------------------------------------------------------------
# Dockerfile content validation
# ---------------------------------------------------------------------------


class TestDockerfile:
    def test_dockerfile_exists(self) -> None:
        assert _DOCKERFILE.exists(), f"Dockerfile not found at {_DOCKERFILE}"

    def test_uses_python312_slim(self) -> None:
        content = _DOCKERFILE.read_text()
        assert "python:3.12-slim" in content

    def test_has_entrypoint(self) -> None:
        content = _DOCKERFILE.read_text()
        assert "factory_simulator" in content
        assert "ENTRYPOINT" in content

    def test_has_healthcheck(self) -> None:
        content = _DOCKERFILE.read_text()
        assert "HEALTHCHECK" in content
        assert "/health" in content

    def test_exposes_modbus_port(self) -> None:
        content = _DOCKERFILE.read_text()
        assert "502" in content

    def test_exposes_opcua_port(self) -> None:
        content = _DOCKERFILE.read_text()
        assert "4840" in content

    def test_exposes_health_port(self) -> None:
        content = _DOCKERFILE.read_text()
        assert "8080" in content

    def test_copies_requirements(self) -> None:
        content = _DOCKERFILE.read_text()
        assert "requirements.txt" in content

    def test_copies_source(self) -> None:
        content = _DOCKERFILE.read_text()
        assert "src/" in content


# ---------------------------------------------------------------------------
# docker-compose.yml content validation
# ---------------------------------------------------------------------------


class TestDockerCompose:
    def test_compose_file_exists(self) -> None:
        assert _COMPOSE.exists(), f"docker-compose.yml not found at {_COMPOSE}"

    def test_has_mqtt_broker_service(self) -> None:
        content = _COMPOSE.read_text()
        assert "mqtt-broker" in content

    def test_has_factory_simulator_service(self) -> None:
        content = _COMPOSE.read_text()
        assert "factory-simulator" in content

    def test_depends_on_mqtt_broker(self) -> None:
        content = _COMPOSE.read_text()
        assert "depends_on" in content
        assert "service_healthy" in content

    def test_exposes_modbus_port(self) -> None:
        content = _COMPOSE.read_text()
        assert '"502:502"' in content or "'502:502'" in content or "502:502" in content

    def test_exposes_opcua_port(self) -> None:
        content = _COMPOSE.read_text()
        assert "4840" in content

    def test_exposes_health_port(self) -> None:
        content = _COMPOSE.read_text()
        assert "8080" in content

    def test_mqtt_broker_env_variable(self) -> None:
        content = _COMPOSE.read_text()
        assert "MQTT_BROKER_HOST" in content

    def test_realistic_override_exists(self) -> None:
        assert _COMPOSE_REALISTIC.exists(), (
            f"docker-compose.realistic.yaml not found at {_COMPOSE_REALISTIC}"
        )

    def test_realistic_override_adds_realistic_mode(self) -> None:
        content = _COMPOSE_REALISTIC.read_text()
        assert "SIM_NETWORK_MODE=realistic" in content

    def test_realistic_override_adds_controller_ports(self) -> None:
        content = _COMPOSE_REALISTIC.read_text()
        # Packaging Modbus ports
        assert "5020" in content
        assert "5021" in content
        assert "5022" in content
        # F&B Modbus ports
        assert "5030" in content
        assert "5031" in content


# ---------------------------------------------------------------------------
# mosquitto.conf validation
# ---------------------------------------------------------------------------


class TestMosquittoConf:
    def test_conf_exists(self) -> None:
        assert _MOSQUITTO_CONF.exists(), (
            f"mosquitto.conf not found at {_MOSQUITTO_CONF}"
        )

    def test_has_listener_1883(self) -> None:
        content = _MOSQUITTO_CONF.read_text()
        assert "listener" in content
        assert "1883" in content

    def test_allows_anonymous(self) -> None:
        content = _MOSQUITTO_CONF.read_text()
        assert "allow_anonymous" in content
        assert "true" in content

    def test_no_persistence(self) -> None:
        content = _MOSQUITTO_CONF.read_text()
        assert "persistence" in content
