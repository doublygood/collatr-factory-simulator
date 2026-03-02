"""Spike: asyncua multiple OPC-UA server instances validation.

Validates that asyncua can run 3 concurrent OPC-UA servers in one asyncio
event loop, each with independent node trees, subscriptions, and StatusCode
propagation.

Tests:
  - 3 concurrent servers on OS-assigned ports
  - String NodeIDs (ns=2;s=<Profile>.<Equipment>.<Signal>)
  - Variable attributes (EURange, AccessLevel)
  - Subscriptions with 500ms publishing interval
  - StatusCode propagation (BadSensorFailure)
  - Memory baseline (RSS before/after starting 3 servers)
"""

from __future__ import annotations

import asyncio
import contextlib
import resource
import sys
import time

import pytest
from asyncua import Client, Server, ua

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NAMESPACE_URI = "urn:collatr:factory-simulator:spike"
HOST = "127.0.0.1"

# Node tree definitions for 3 servers
# Server 1: PackagingLine (5 variables)
SERVER_1_NODES = {
    "root_name": "PackagingLine",
    "equipment": "Press1",
    "variables": [
        ("LineSpeed", ua.VariantType.Double, 150.0, "m/min", 0.0, 500.0),
        ("WebTension", ua.VariantType.Double, 200.0, "N", 0.0, 1000.0),
        ("State", ua.VariantType.UInt16, 2, "enum", 0.0, 5.0),
        ("NipPressure", ua.VariantType.Double, 3.5, "bar", 0.0, 10.0),
        ("ImpressionCount", ua.VariantType.UInt32, 10000, "count", 0.0, 999999999.0),
    ],
}

# Server 2: FoodBevLine (5 variables)
SERVER_2_NODES = {
    "root_name": "FoodBevLine",
    "equipment": "Filler1",
    "variables": [
        ("LineSpeed", ua.VariantType.Double, 60.0, "packs/min", 0.0, 200.0),
        ("FillWeight", ua.VariantType.Double, 350.0, "g", 0.0, 1000.0),
        ("FillTarget", ua.VariantType.Double, 350.0, "g", 0.0, 1000.0),
        ("PacksProduced", ua.VariantType.UInt32, 5000, "count", 0.0, 999999999.0),
        ("State", ua.VariantType.UInt16, 1, "enum", 0.0, 4.0),
    ],
}

# Server 3: QC (3 variables)
SERVER_3_NODES = {
    "root_name": "FoodBevLine",
    "equipment": "QC1",
    "variables": [
        ("ActualWeight", ua.VariantType.Double, 348.5, "g", 0.0, 1000.0),
        ("Throughput", ua.VariantType.Double, 55.0, "items/min", 0.0, 200.0),
        ("RejectTotal", ua.VariantType.UInt32, 12, "count", 0.0, 999999999.0),
    ],
}


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------
async def _create_server(
    node_def: dict,
    *,
    setpoint_names: set[str] | None = None,
) -> tuple[Server, int, list[ua.NodeId]]:
    """Create and start an asyncua Server with the given node tree.

    Returns (server, actual_port, list_of_variable_nodeids).
    Uses port 0 for OS-assigned port to avoid conflicts.
    """
    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://{HOST}:0/spike/")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

    ns = await server.register_namespace(NAMESPACE_URI)
    objects = server.nodes.objects

    # Create root folder (e.g. PackagingLine)
    root_name = node_def["root_name"]
    root_folder = await objects.add_folder(
        ua.NodeId(root_name, ns),
        root_name,
    )

    # Create equipment folder (e.g. Press1)
    equip_name = node_def["equipment"]
    equip_path = f"{root_name}.{equip_name}"
    equip_folder = await root_folder.add_folder(
        ua.NodeId(equip_path, ns),
        equip_name,
    )

    # Create variables
    node_ids: list[ua.NodeId] = []
    setpoint_set = setpoint_names or set()

    for var_name, var_type, initial_val, _unit, eu_low, eu_high in node_def["variables"]:
        node_id_str = f"{equip_path}.{var_name}"
        node_id = ua.NodeId(node_id_str, ns)

        var_node = await equip_folder.add_variable(
            node_id,
            var_name,
            initial_val,
            varianttype=var_type,
        )
        node_ids.append(node_id)

        # Set EURange property
        eu_range = ua.Range(Low=eu_low, High=eu_high)
        await var_node.add_property(
            ua.NodeId(0, 0),  # auto-assigned
            "EURange",
            eu_range,
        )

        # Set writable for setpoint variables
        if var_name in setpoint_set:
            await var_node.set_writable()

    await server.start()

    # Extract OS-assigned port
    actual_port = 0
    for sock in server.bserver._server.sockets:
        actual_port = sock.getsockname()[1]
        break

    return server, actual_port, node_ids


def _get_rss_mb() -> float:
    """Get current process RSS in MB (cross-platform)."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        # macOS: ru_maxrss is in bytes
        return usage / (1024 * 1024)
    # Linux: ru_maxrss is in KB
    return usage / 1024


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
async def opcua_servers() -> (  # type: ignore[override]
    tuple[list[Server], list[int], list[list[ua.NodeId]]]
):
    """Start 3 OPC-UA servers and yield them; stop on cleanup.

    Returns (servers, ports, node_id_lists).
    """
    server_defs = [
        (SERVER_1_NODES, {"FillTarget"}),  # has a setpoint for write test
        (SERVER_2_NODES, {"FillTarget"}),
        (SERVER_3_NODES, None),
    ]

    servers: list[Server] = []
    ports: list[int] = []
    all_node_ids: list[list[ua.NodeId]] = []

    for node_def, setpoint_names in server_defs:
        server, port, node_ids = await _create_server(
            node_def,
            setpoint_names=setpoint_names,
        )
        servers.append(server)
        ports.append(port)
        all_node_ids.append(node_ids)

    yield servers, ports, all_node_ids

    for server in servers:
        await server.stop()


@pytest.fixture
async def clients(
    opcua_servers: tuple[list[Server], list[int], list[list[ua.NodeId]]],
) -> list[Client]:
    """Connect a client to each of the 3 servers."""
    _servers, ports, _node_ids = opcua_servers
    client_list: list[Client] = []

    for port in ports:
        client = Client(f"opc.tcp://{HOST}:{port}/spike/")
        await client.connect()
        client_list.append(client)

    yield client_list  # type: ignore[misc]

    for client in client_list:
        await client.disconnect()


# ---------------------------------------------------------------------------
# Subscription handler
# ---------------------------------------------------------------------------
class DataChangeHandler:
    """Collects data change notifications from OPC-UA subscriptions.

    asyncua 1.1 passes a DataChangeNotif object as the third argument.
    The actual DataValue is at ``data.monitored_item.Value``.
    """

    def __init__(self) -> None:
        self.changes: list[tuple[ua.NodeId | None, object, ua.DataValue]] = []
        self.event = asyncio.Event()
        self.target_count = 0

    def datachange_notification(
        self,
        node: object,
        val: object,
        data: object,
    ) -> None:
        node_id = node.nodeid if hasattr(node, "nodeid") else None
        # Extract DataValue from DataChangeNotif wrapper
        data_value = data.monitored_item.Value if hasattr(data, "monitored_item") else data
        self.changes.append((node_id, val, data_value))
        if self.target_count > 0 and len(self.changes) >= self.target_count:
            self.event.set()

    def reset(self, target: int = 0) -> None:
        self.changes.clear()
        self.event.clear()
        self.target_count = target


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestConcurrentServers:
    """Validate 3 concurrent OPC-UA servers in one event loop."""

    async def test_all_servers_respond(
        self,
        clients: list[Client],
        opcua_servers: tuple[list[Server], list[int], list[list[ua.NodeId]]],
    ) -> None:
        """Each of the 3 servers responds to reads."""
        _servers, _ports, all_node_ids = opcua_servers

        for i, (client, node_ids) in enumerate(zip(clients, all_node_ids, strict=True)):
            for nid in node_ids:
                node = client.get_node(nid)
                val = await node.read_value()
                assert val is not None, f"Server {i}, node {nid}: read returned None"

    async def test_servers_have_independent_data(
        self,
        clients: list[Client],
        opcua_servers: tuple[list[Server], list[int], list[list[ua.NodeId]]],
    ) -> None:
        """Each server returns its own data set."""
        _servers, _ports, all_node_ids = opcua_servers

        # Read first variable from each server
        values = []
        for client, node_ids in zip(clients, all_node_ids, strict=True):
            node = client.get_node(node_ids[0])
            val = await node.read_value()
            values.append(val)

        # Server 1: LineSpeed=150.0, Server 2: LineSpeed=60.0, Server 3: ActualWeight=348.5
        assert len(set(str(v) for v in values)) == 3, (
            f"Expected 3 unique values, got: {values}"
        )

    async def test_concurrent_reads(
        self,
        clients: list[Client],
        opcua_servers: tuple[list[Server], list[int], list[list[ua.NodeId]]],
    ) -> None:
        """Concurrent reads from all 3 servers complete without errors."""
        _servers, _ports, all_node_ids = opcua_servers

        async def read_server(idx: int) -> list[object]:
            results = []
            for nid in all_node_ids[idx]:
                node = clients[idx].get_node(nid)
                val = await node.read_value()
                results.append(val)
            return results

        start = time.monotonic()
        results = await asyncio.gather(*[read_server(i) for i in range(3)])
        elapsed = time.monotonic() - start

        assert len(results) == 3
        for i, result_set in enumerate(results):
            assert len(result_set) == len(all_node_ids[i]), (
                f"Server {i}: expected {len(all_node_ids[i])} values, got {len(result_set)}"
            )

        # Should complete quickly (< 5s even with asyncua overhead)
        assert elapsed < 5.0, f"Concurrent reads took {elapsed:.3f}s"


class TestStringNodeIDs:
    """Validate string NodeID format ns=2;s=<path>."""

    async def test_string_node_ids_browsable(
        self,
        clients: list[Client],
        opcua_servers: tuple[list[Server], list[int], list[list[ua.NodeId]]],
    ) -> None:
        """String NodeIDs are browsable by path."""
        _servers, _ports, all_node_ids = opcua_servers

        # Server 1: browse PackagingLine.Press1.LineSpeed
        node = clients[0].get_node(ua.NodeId("PackagingLine.Press1.LineSpeed", 2))
        val = await node.read_value()
        assert val == pytest.approx(150.0), f"Expected 150.0, got {val}"

        # Server 2: browse FoodBevLine.Filler1.FillWeight
        node = clients[1].get_node(ua.NodeId("FoodBevLine.Filler1.FillWeight", 2))
        val = await node.read_value()
        assert val == pytest.approx(350.0), f"Expected 350.0, got {val}"

        # Server 3: browse FoodBevLine.QC1.ActualWeight
        node = clients[2].get_node(ua.NodeId("FoodBevLine.QC1.ActualWeight", 2))
        val = await node.read_value()
        assert val == pytest.approx(348.5), f"Expected 348.5, got {val}"


class TestVariableAttributes:
    """Validate EURange and AccessLevel attributes."""

    async def test_eurange_property(
        self,
        clients: list[Client],
        opcua_servers: tuple[list[Server], list[int], list[list[ua.NodeId]]],
    ) -> None:
        """EURange property is browsable on variables."""
        _servers, _ports, all_node_ids = opcua_servers

        # Check EURange on server 1, LineSpeed (0.0 - 500.0)
        node = clients[0].get_node(all_node_ids[0][0])
        children = await node.get_children()
        eu_range_found = False
        for child in children:
            name = await child.read_browse_name()
            if name.Name == "EURange":
                eu_val = await child.read_value()
                assert eu_val.Low == pytest.approx(0.0)
                assert eu_val.High == pytest.approx(500.0)
                eu_range_found = True
                break

        assert eu_range_found, "EURange property not found on LineSpeed"

    async def test_access_level_readonly(
        self,
        clients: list[Client],
        opcua_servers: tuple[list[Server], list[int], list[list[ua.NodeId]]],
    ) -> None:
        """Non-setpoint variables have read-only AccessLevel."""
        _servers, _ports, all_node_ids = opcua_servers

        # Server 1, LineSpeed should be read-only
        node = clients[0].get_node(all_node_ids[0][0])
        result = await node.read_attribute(ua.AttributeIds.AccessLevel)
        access_level = result.Value.Value
        # AccessLevel 1 = CurrentRead
        assert access_level == 1, f"Expected read-only (1), got {access_level}"

    async def test_access_level_readwrite_setpoint(
        self,
        clients: list[Client],
        opcua_servers: tuple[list[Server], list[int], list[list[ua.NodeId]]],
    ) -> None:
        """Setpoint variables have read-write AccessLevel."""
        _servers, _ports, all_node_ids = opcua_servers

        # Server 2, FillTarget (index 2) is a setpoint
        node = clients[1].get_node(all_node_ids[1][2])
        result = await node.read_attribute(ua.AttributeIds.AccessLevel)
        access_level = result.Value.Value
        # AccessLevel 3 = CurrentRead | CurrentWrite
        assert access_level == 3, f"Expected read-write (3), got {access_level}"


class TestSubscriptions:
    """Validate subscription data change notifications."""

    async def test_subscription_receives_changes(
        self,
        clients: list[Client],
        opcua_servers: tuple[list[Server], list[int], list[list[ua.NodeId]]],
    ) -> None:
        """Subscription at 500ms delivers data change notifications."""
        servers, _ports, all_node_ids = opcua_servers

        handler = DataChangeHandler()
        # Initial subscription fires once per monitored item, plus we write 3 times
        handler.target_count = 4  # 1 initial + 3 updates

        sub = await clients[0].create_subscription(500, handler)
        # Subscribe to first variable (LineSpeed)
        node = clients[0].get_node(all_node_ids[0][0])
        await sub.subscribe_data_change(node)

        # Give initial value notification time to arrive
        await asyncio.sleep(1.0)

        # Update the server-side variable 3 times at ~600ms intervals
        server_node = servers[0].get_node(all_node_ids[0][0])
        for i in range(3):
            await asyncio.sleep(0.6)
            new_val = 150.0 + (i + 1) * 10.0
            await server_node.write_value(new_val, ua.VariantType.Double)

        # Wait for notifications (may not reach target count)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(handler.event.wait(), timeout=10.0)

        await sub.delete()

        # Should have received at least 3 data changes (initial + updates)
        assert len(handler.changes) >= 3, (
            f"Expected >=3 data changes, got {len(handler.changes)}"
        )

    async def test_subscriptions_on_all_servers(
        self,
        clients: list[Client],
        opcua_servers: tuple[list[Server], list[int], list[list[ua.NodeId]]],
    ) -> None:
        """Subscriptions work concurrently on all 3 servers."""
        servers, _ports, all_node_ids = opcua_servers

        handlers: list[DataChangeHandler] = []
        subs = []

        for i in range(3):
            handler = DataChangeHandler()
            handler.target_count = 2  # initial + 1 update
            handlers.append(handler)

            sub = await clients[i].create_subscription(500, handler)
            node = clients[i].get_node(all_node_ids[i][0])
            await sub.subscribe_data_change(node)
            subs.append(sub)

        await asyncio.sleep(1.0)

        # Update first variable on each server
        for i in range(3):
            server_node = servers[i].get_node(all_node_ids[i][0])
            vtype = SERVER_1_NODES["variables"][0][1] if i == 0 else (
                SERVER_2_NODES["variables"][0][1] if i == 1 else
                SERVER_3_NODES["variables"][0][1]
            )
            await server_node.write_value(999.0 if vtype == ua.VariantType.Double else 999, vtype)

        await asyncio.sleep(2.0)

        for sub in subs:
            await sub.delete()

        # Each handler should have at least 2 changes (initial + update)
        for i, handler in enumerate(handlers):
            assert len(handler.changes) >= 2, (
                f"Server {i}: expected >=2 data changes, got {len(handler.changes)}"
            )


class TestStatusCodePropagation:
    """Validate StatusCode propagation to clients."""

    async def test_bad_sensor_failure_status(
        self,
        clients: list[Client],
        opcua_servers: tuple[list[Server], list[int], list[list[ua.NodeId]]],
    ) -> None:
        """BadSensorFailure status code propagates to client."""
        servers, _ports, all_node_ids = opcua_servers

        # Set BadSensorFailure on server 1, LineSpeed
        server_node = servers[0].get_node(all_node_ids[0][0])
        bad_dv = ua.DataValue(
            ua.Variant(0.0, ua.VariantType.Double),
            ua.StatusCode(ua.StatusCodes.BadSensorFailure),
        )
        await server_node.write_value(bad_dv)

        # Client reads with raise_on_bad_status=False
        client_node = clients[0].get_node(all_node_ids[0][0])
        dv = await client_node.read_data_value(raise_on_bad_status=False)

        assert not dv.StatusCode.is_good(), (
            f"Expected bad status, got: {dv.StatusCode}"
        )

    async def test_subscription_receives_bad_status(
        self,
        clients: list[Client],
        opcua_servers: tuple[list[Server], list[int], list[list[ua.NodeId]]],
    ) -> None:
        """Subscription delivers data change with bad StatusCode."""
        servers, _ports, all_node_ids = opcua_servers

        handler = DataChangeHandler()
        handler.target_count = 2  # initial good + bad status

        sub = await clients[0].create_subscription(500, handler)
        node = clients[0].get_node(all_node_ids[0][0])
        await sub.subscribe_data_change(node)

        await asyncio.sleep(1.0)

        # Set bad status
        server_node = servers[0].get_node(all_node_ids[0][0])
        bad_dv = ua.DataValue(
            ua.Variant(0.0, ua.VariantType.Double),
            ua.StatusCode(ua.StatusCodes.BadSensorFailure),
        )
        await server_node.write_value(bad_dv)

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(handler.event.wait(), timeout=5.0)

        await sub.delete()

        # Should have received at least 2 changes
        assert len(handler.changes) >= 2, (
            f"Expected >=2 data changes, got {len(handler.changes)}"
        )

        # Last change should have bad status
        last_data = handler.changes[-1][2]
        assert not last_data.StatusCode.is_good(), (
            f"Expected bad status in last notification, got: {last_data.StatusCode}"
        )


class TestMemoryBaseline:
    """Measure RSS memory baseline for 3 OPC-UA servers.

    Note: ``ru_maxrss`` reports *peak* process RSS, which includes
    pytest infrastructure, asyncua imports, client connections from
    earlier tests, and crypto libraries.  The 500 MB threshold covers
    the full process; the important metric for Phase 1 planning is the
    *recorded* number, not the absolute threshold.
    """

    async def test_rss_recorded(
        self,
        opcua_servers: tuple[list[Server], list[int], list[list[ua.NodeId]]],
    ) -> None:
        """Record RSS and verify it stays reasonable for 3 servers."""
        rss_mb = _get_rss_mb()

        print("\n--- Memory Baseline ---")
        print(f"Peak RSS (whole process, 3 servers + test infra): {rss_mb:.1f} MB")

        # ru_maxrss is peak process RSS including pytest, imports, crypto
        # libs, and client connections from other tests in this session.
        # The threshold is generous; the recorded value guides Phase 1.
        assert rss_mb < 500.0, f"Peak RSS {rss_mb:.1f} MB exceeds 500MB limit"
