"""Lightweight asyncio HTTP health server for the factory simulator.

Provides two endpoints:
- ``GET /health``  — JSON status dict (status, profile, sim_time, signals, protocols)
- ``GET /status``  — JSON map of all current signal values from the signal store

Designed to be started as a background asyncio task alongside the main simulator
loop.  The ``update()`` method lets the CLI update static fields (profile,
protocol up/down status) without blocking the event loop.

Dynamic fields (``sim_time``, signal count) are computed on demand from the
:class:`~factory_simulator.store.SignalStore` when a request arrives, so they
always reflect the latest engine tick.

PRD Reference: Section 6.3 (Docker Compose — health endpoint), Task 5.10
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from factory_simulator.store import SignalStore

logger = logging.getLogger(__name__)

_DEFAULT_STATE: dict[str, Any] = {
    "status": "starting",
    "profile": "packaging",
    "sim_time": "2026-01-01T00:00:00Z",
    "signals": 0,
    "modbus": "down",
    "opcua": "down",
    "mqtt": "down",
}

# Simulation wall-clock reference: sim_time=0 corresponds to 2026-01-01T00:00:00Z
_REFERENCE_EPOCH_TS: float = datetime(2026, 1, 1, tzinfo=UTC).timestamp()


class HealthServer:
    """Asyncio HTTP server serving ``/health`` and ``/status`` on a TCP port.

    Parameters
    ----------
    port:
        TCP port to bind.  Pass ``0`` to let the OS assign a free port
        (useful for tests).  Defaults to ``8080``.
    store:
        Optional reference to the :class:`~factory_simulator.store.SignalStore`.
        When provided, ``/health`` reports the live signal count and current
        ``sim_time``; ``/status`` returns all current signal values.
    """

    def __init__(self, port: int = 8080, store: SignalStore | None = None) -> None:
        self._port = port
        self._store = store
        self._server: asyncio.Server | None = None
        # Mutable state updated by the CLI as the simulator starts up
        self._state: dict[str, Any] = dict(_DEFAULT_STATE)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def actual_port(self) -> int:
        """Actual bound port (resolves OS-assigned port when ``port=0``)."""
        if self._server is not None:
            return int(self._server.sockets[0].getsockname()[1])
        return self._port

    def update(
        self,
        *,
        status: str | None = None,
        profile: str | None = None,
        sim_time: float | None = None,
        signals: int | None = None,
        modbus: str | None = None,
        opcua: str | None = None,
        mqtt: str | None = None,
    ) -> None:
        """Update static health fields.

        Only non-``None`` arguments are applied.  ``sim_time`` is converted
        from a UNIX float to an ISO-8601 UTC string.
        """
        if status is not None:
            self._state["status"] = status
        if profile is not None:
            self._state["profile"] = profile
        if sim_time is not None:
            self._state["sim_time"] = (
                datetime.fromtimestamp(sim_time, tz=UTC).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            )
        if signals is not None:
            self._state["signals"] = signals
        if modbus is not None:
            self._state["modbus"] = modbus
        if opcua is not None:
            self._state["opcua"] = opcua
        if mqtt is not None:
            self._state["mqtt"] = mqtt

    async def start(self) -> None:
        """Start the TCP server and serve requests until cancelled."""
        self._server = await asyncio.start_server(
            self._handle, "0.0.0.0", self._port
        )
        logger.info("Health server listening on port %d", self.actual_port)
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Close the TCP server and wait for pending connections to drain."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_health_payload(self) -> dict[str, Any]:
        """Build the /health response dict with live sim_time and signal count."""
        payload: dict[str, Any] = dict(self._state)
        if self._store is not None:
            all_signals = self._store.get_all()
            payload["signals"] = len(all_signals)
            if all_signals:
                max_ts = max(sv.timestamp for sv in all_signals.values())
                payload["sim_time"] = datetime.fromtimestamp(
                    _REFERENCE_EPOCH_TS + max_ts, tz=UTC
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
        return payload

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Dispatch a single HTTP/1.1 request."""
        try:
            # Read request line
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not request_line:
                return

            parts = request_line.decode("ascii", errors="replace").split()
            if len(parts) < 2:
                await _send(writer, 400, b"Bad Request")
                return

            method, raw_path = parts[0], parts[1]
            path = raw_path.split("?")[0]

            # Drain headers (we don't use them but must consume them)
            while True:
                header_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if header_line in (b"\r\n", b"\n", b""):
                    break

            if method != "GET":
                await _send(writer, 405, b"Method Not Allowed")
                return

            if path == "/health":
                body = json.dumps(self._build_health_payload()).encode()
                await _send(writer, 200, body, content_type="application/json")

            elif path == "/status":
                values: dict[str, float | str] = {}
                if self._store is not None:
                    for sv in self._store.get_all().values():
                        values[sv.signal_id] = sv.value
                body = json.dumps(values).encode()
                await _send(writer, 200, body, content_type="application/json")

            else:
                await _send(writer, 404, b"Not Found")

        except (TimeoutError, ConnectionResetError, OSError):
            pass
        finally:
            writer.close()


async def _send(
    writer: asyncio.StreamWriter,
    status: int,
    body: bytes,
    content_type: str = "text/plain",
) -> None:
    """Write an HTTP/1.1 response and drain the write buffer."""
    status_text = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}.get(
        status, "Error"
    )
    header = (
        f"HTTP/1.1 {status} {status_text}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode()
    writer.write(header + body)
    await writer.drain()
