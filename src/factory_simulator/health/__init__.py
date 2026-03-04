"""Health endpoint for the Collatr Factory Simulator.

Provides a lightweight asyncio HTTP server with /health and /status endpoints
for Docker health checks and observability.

PRD Reference: Section 6.3 (Docker Compose with health checks), Task 5.10
"""

from factory_simulator.health.server import HealthServer

__all__ = ["HealthServer"]
