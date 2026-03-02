"""OPC-UA server for the Collatr Factory Simulator.

Reads signal values from the SignalStore and serves them via asyncua.
Builds the packaging profile node tree per PRD Appendix B from the
signal configs (opcua_node / opcua_type fields).

Features:
- Full PackagingLine node tree (Press1, Laminator1, Slitter1, Energy)
- String NodeIDs: ns=2;s=PackagingLine.Press1.LineSpeed etc.
- EURange property on every variable node
- Read-only access by default; setpoints (modbus_writable=True) are writable
- Periodic value sync from SignalStore (task 2.2, placeholder here)

PRD Reference: Section 3.2, Appendix B (OPC-UA Node Tree)
CLAUDE.md Rule 9: No locks (single writer, asyncio single-threaded).
CLAUDE.md Rule 10: Configuration via Pydantic.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from asyncua import Server, ua

if TYPE_CHECKING:
    from factory_simulator.config import FactoryConfig
    from factory_simulator.store import SignalStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (PRD Section 3.2, Appendix B)
# ---------------------------------------------------------------------------

NAMESPACE_URI = "urn:collatr:factory-simulator"
NAMESPACE_INDEX = 2  # PRD specifies ns=2

# PRD 3.2: minimum server-side publishing interval
MIN_PUBLISHING_INTERVAL_MS = 500

# Config opcua_type string → asyncua VariantType
_VARIANT_TYPE_MAP: dict[str, Any] = {
    "Double": ua.VariantType.Double,
    "UInt32": ua.VariantType.UInt32,
    "UInt16": ua.VariantType.UInt16,
    "String": ua.VariantType.String,
}


def _initial_value(vtype: Any) -> float | int | str:
    """Return a safe zero initial value for the given VariantType."""
    if vtype == ua.VariantType.Double:
        return 0.0
    if vtype in (ua.VariantType.UInt32, ua.VariantType.UInt16):
        return 0
    if vtype == ua.VariantType.String:
        return ""
    return 0.0


# ---------------------------------------------------------------------------
# OpcuaServer
# ---------------------------------------------------------------------------


class OpcuaServer:
    """OPC-UA server that serves signal values from SignalStore.

    Builds the packaging profile node tree per PRD Appendix B from
    signal configs (``opcua_node`` / ``opcua_type`` fields), then
    periodically syncs signal values from the store to OPC-UA nodes.

    Parameters
    ----------
    config:
        Validated :class:`~factory_simulator.config.FactoryConfig`.
    store:
        Shared :class:`~factory_simulator.store.SignalStore` instance.
    host:
        Bind address override (for testing).  Defaults to config value.
    port:
        Port override (for testing).  Use 0 for OS-assigned port.
        Defaults to config value.
    """

    def __init__(
        self,
        config: FactoryConfig,
        store: SignalStore,
        *,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._opcua_cfg = config.protocols.opcua
        self._host = host or self._opcua_cfg.bind_address
        self._port = port if port is not None else self._opcua_cfg.port

        # asyncua Server instance — created in start()
        self._server: Any | None = None
        self._update_task: asyncio.Task[None] | None = None

        # node_path → asyncua variable node (populated by _build_node_tree)
        self._nodes: dict[str, Any] = {}
        # node_path → signal_id (for value sync in task 2.2)
        self._node_to_signal: dict[str, str] = {}
        # node_path → VariantType (for encoding in task 2.2)
        self._node_types: dict[str, Any] = {}
        # signal_id → node_path (for setpoint write-back in task 2.2)
        self._signal_to_node: dict[str, str] = {}

    # -- Properties -----------------------------------------------------------

    @property
    def host(self) -> str:
        """Server bind address."""
        return self._host

    @property
    def port(self) -> int:
        """Configured port.

        May be 0 if OS-assigned; use :attr:`actual_port` after :meth:`start`.
        """
        return self._port

    @property
    def actual_port(self) -> int:
        """Actual bound port after :meth:`start` has been called.

        Resolves OS-assigned port (port=0).  Falls back to the configured
        port if the server has not been started yet.
        """
        if self._server is None:
            return self._port
        try:
            sockets = self._server.bserver._server.sockets
            if sockets:
                addr: tuple[str, int] = sockets[0].getsockname()
                return addr[1]
        except AttributeError:
            pass
        return self._port

    @property
    def nodes(self) -> dict[str, Any]:
        """Variable nodes keyed by node path string (for testing)."""
        return self._nodes

    @property
    def node_to_signal(self) -> dict[str, str]:
        """Mapping from node path to signal ID."""
        return self._node_to_signal

    # -- Async lifecycle ------------------------------------------------------

    async def start(self) -> None:
        """Start the OPC-UA server and value update loop."""
        self._server = Server()
        await self._server.init()
        self._server.set_endpoint(f"opc.tcp://{self._host}:{self._port}/")
        self._server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

        ns = await self._server.register_namespace(NAMESPACE_URI)
        if ns != NAMESPACE_INDEX:
            logger.warning(
                "OPC-UA namespace index %d != expected %d (ns=2)",
                ns,
                NAMESPACE_INDEX,
            )

        await self._build_node_tree(ns)
        await self._server.start()

        self._update_task = asyncio.create_task(self._update_loop())
        logger.info("OPC-UA server started on %s:%d", self._host, self.actual_port)

    async def stop(self) -> None:
        """Stop the OPC-UA server and update loop."""
        if self._update_task is not None:
            self._update_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._update_task
            self._update_task = None

        if self._server is not None:
            await self._server.stop()
            self._server = None

        logger.info("OPC-UA server stopped")

    # -- Node tree ------------------------------------------------------------

    async def _build_node_tree(self, ns: int) -> None:
        """Create all OPC-UA variable nodes from signal config.

        Parses ``opcua_node`` paths (e.g. ``PackagingLine.Press1.LineSpeed``)
        to build the folder hierarchy dynamically.  Intermediate path
        segments become OPC-UA folder nodes; the final segment becomes a
        variable node.

        Setpoints (``modbus_writable=True``) receive AccessLevel read-write
        (3).  All other nodes are read-only (AccessLevel 1).

        PRD Reference: Appendix B (full node tree), Section 3.2.
        """
        assert self._server is not None
        objects = self._server.nodes.objects
        folder_cache: dict[str, Any] = {}

        for equip_key, equip_cfg in self._config.equipment.items():
            if not equip_cfg.enabled:
                continue
            for sig_key, sig_cfg in equip_cfg.signals.items():
                if sig_cfg.opcua_node is None:
                    continue

                signal_id = f"{equip_key}.{sig_key}"
                node_path = sig_cfg.opcua_node
                parts = node_path.split(".")

                # -- Folder hierarchy for intermediate path segments ----------
                parent: Any = objects
                accumulated_path = ""
                for part in parts[:-1]:
                    accumulated_path = (
                        f"{accumulated_path}.{part}" if accumulated_path else part
                    )
                    if accumulated_path not in folder_cache:
                        folder_node = await parent.add_folder(
                            ua.NodeId(accumulated_path, ns),
                            part,
                        )
                        folder_cache[accumulated_path] = folder_node
                    parent = folder_cache[accumulated_path]

                # -- Variable node --------------------------------------------
                var_name = parts[-1]
                type_str = sig_cfg.opcua_type or "Double"
                vtype = _VARIANT_TYPE_MAP.get(type_str, ua.VariantType.Double)
                init_val = _initial_value(vtype)

                var_node = await parent.add_variable(
                    ua.NodeId(node_path, ns),
                    var_name,
                    init_val,
                    varianttype=vtype,
                )

                # EURange property (PRD Appendix B attribute convention)
                eu_low = (
                    float(sig_cfg.min_clamp) if sig_cfg.min_clamp is not None else 0.0
                )
                eu_high = (
                    float(sig_cfg.max_clamp) if sig_cfg.max_clamp is not None else 0.0
                )
                await var_node.add_property(
                    ua.NodeId(0, 0),
                    "EURange",
                    ua.Range(Low=eu_low, High=eu_high),
                )

                # Writable for setpoints (PRD 3.2)
                if sig_cfg.modbus_writable:
                    await var_node.set_writable()

                # Register for value sync (task 2.2)
                self._nodes[node_path] = var_node
                self._node_to_signal[node_path] = signal_id
                self._node_types[node_path] = vtype
                self._signal_to_node[signal_id] = node_path

        logger.info(
            "OPC-UA node tree built: %d variable nodes, %d folder nodes",
            len(self._nodes),
            len(folder_cache),
        )

    # -- Value sync (task 2.2) -----------------------------------------------

    async def _update_loop(self) -> None:
        """Periodically sync signal values from SignalStore to OPC-UA nodes.

        Full sync implementation is task 2.2.  This placeholder keeps the
        server alive at the correct publishing interval.

        PRD Reference: Section 3.2 (minimum 500ms publishing interval)
        """
        try:
            while True:
                await asyncio.sleep(MIN_PUBLISHING_INTERVAL_MS / 1000.0)
        except asyncio.CancelledError:
            pass
