"""Integration tests for the OPC-UA server adapter.

Starts DataEngine + OpcuaServer, connects a real asyncua client, and
verifies the full packaging-profile OPC-UA stack end-to-end.

Two fixtures are used:

``opcua_static``
    Pre-populated :class:`~factory_simulator.store.SignalStore` with no
    running engine.  Used for node access, range, and setpoint write tests.

``opcua_live``
    :class:`~factory_simulator.engine.data_engine.DataEngine` running as an
    asyncio task.  Used for subscription delivery tests.

Unlike the unit tests in ``tests/unit/test_protocols/test_opcua.py`` (which
also use a live server and client), these integration tests exercise the
*engine → store → OPC-UA sync* path: the DataEngine is the source of signal
values rather than direct :meth:`~factory_simulator.store.SignalStore.set`
calls.

PRD Reference: Section 3.2, Appendix B (OPC-UA Node Tree), Section 13.2
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from pathlib import Path

import pytest
from asyncua import Client, ua

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.protocols.opcua_server import (
    NAMESPACE_INDEX,
    NAMESPACE_URI,
    OpcuaServer,
)
from factory_simulator.store import SignalStore

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory.yaml"
_HOST = "127.0.0.1"

# ---------------------------------------------------------------------------
# Leaf nodes per PRD Appendix B — packaging profile
# (node_path, opcua_type_str, is_writable)
# ---------------------------------------------------------------------------

EXPECTED_NODES: list[tuple[str, str, bool]] = [
    # Press1 direct children
    ("PackagingLine.Press1.LineSpeed",               "Double",  False),
    ("PackagingLine.Press1.WebTension",              "Double",  False),
    ("PackagingLine.Press1.State",                   "UInt16",  False),
    ("PackagingLine.Press1.FaultCode",               "UInt16",  False),
    ("PackagingLine.Press1.ImpressionCount",         "UInt32",  False),
    ("PackagingLine.Press1.GoodCount",               "UInt32",  False),
    ("PackagingLine.Press1.WasteCount",              "UInt32",  False),
    ("PackagingLine.Press1.NipPressure",             "Double",  False),
    # Registration sub-folder
    ("PackagingLine.Press1.Registration.ErrorX",     "Double",  False),
    ("PackagingLine.Press1.Registration.ErrorY",     "Double",  False),
    # Ink sub-folder
    ("PackagingLine.Press1.Ink.Viscosity",           "Double",  False),
    ("PackagingLine.Press1.Ink.Temperature",         "Double",  False),
    # Dryer zones (temperatures read-only; setpoints writable)
    ("PackagingLine.Press1.Dryer.Zone1.Temperature", "Double",  False),
    ("PackagingLine.Press1.Dryer.Zone1.Setpoint",    "Double",  True),
    ("PackagingLine.Press1.Dryer.Zone2.Temperature", "Double",  False),
    ("PackagingLine.Press1.Dryer.Zone2.Setpoint",    "Double",  True),
    ("PackagingLine.Press1.Dryer.Zone3.Temperature", "Double",  False),
    ("PackagingLine.Press1.Dryer.Zone3.Setpoint",    "Double",  True),
    # MainDrive sub-folder
    ("PackagingLine.Press1.MainDrive.Current",       "Double",  False),
    ("PackagingLine.Press1.MainDrive.Speed",         "Double",  False),
    # Unwind / Rewind
    ("PackagingLine.Press1.Unwind.Diameter",         "Double",  False),
    ("PackagingLine.Press1.Rewind.Diameter",         "Double",  False),
    # Laminator1
    ("PackagingLine.Laminator1.NipTemperature",      "Double",  False),
    ("PackagingLine.Laminator1.NipPressure",         "Double",  False),
    ("PackagingLine.Laminator1.TunnelTemperature",   "Double",  False),
    ("PackagingLine.Laminator1.WebSpeed",            "Double",  False),
    ("PackagingLine.Laminator1.AdhesiveWeight",      "Double",  False),
    # Slitter1
    ("PackagingLine.Slitter1.Speed",                 "Double",  False),
    ("PackagingLine.Slitter1.WebTension",            "Double",  False),
    ("PackagingLine.Slitter1.ReelCount",             "UInt32",  False),
    # Energy
    ("PackagingLine.Energy.LinePower",               "Double",  False),
    ("PackagingLine.Energy.CumulativeKwh",           "Double",  False),
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _base_config() -> tuple[object, SignalStore, SimulationClock, DataEngine]:
    """Create config/store/clock/engine with fixed seed."""
    config = load_config(_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = 42
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = 1.0
    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    return config, store, clock, engine


@pytest.fixture
async def opcua_static() -> (  # type: ignore[override]
    tuple[OpcuaServer, Client, SignalStore, int]
):
    """OpcuaServer with pre-populated store; engine NOT running asynchronously.

    Synchronous engine ticks populate every signal ID, then explicit store
    values are injected so assertions can check exact values.  The server
    is started and allowed one sync cycle before the client connects.

    Yields ``(server, client, store, namespace_index)``.
    """
    config, store, clock, engine = _base_config()

    # Run synchronous ticks to ensure every signal ID exists in the store.
    for _ in range(5):
        engine.tick()

    # Inject known, in-range test values for all 32 OPC-UA signals.
    t = clock.sim_time
    store.set("press.machine_state",         2.0,     t)   # Running (2)
    store.set("press.line_speed",            150.0,   t)
    store.set("press.web_tension",           50.0,    t)
    store.set("press.fault_code",            0.0,     t)
    store.set("press.impression_count",      1000.0,  t)
    store.set("press.good_count",            5000.0,  t)
    store.set("press.waste_count",           50.0,    t)
    store.set("press.nip_pressure",          3.5,     t)
    store.set("press.registration_error_x",  0.02,    t)
    store.set("press.registration_error_y",  0.01,    t)
    store.set("press.ink_viscosity",         28.0,    t)
    store.set("press.ink_temperature",       25.0,    t)
    store.set("press.dryer_temp_zone_1",     75.0,    t)
    store.set("press.dryer_setpoint_zone_1", 75.0,    t)
    store.set("press.dryer_temp_zone_2",     80.0,    t)
    store.set("press.dryer_setpoint_zone_2", 80.0,    t)
    store.set("press.dryer_temp_zone_3",     85.0,    t)
    store.set("press.dryer_setpoint_zone_3", 85.0,    t)
    store.set("press.main_drive_current",    65.0,    t)
    store.set("press.main_drive_speed",      1200.0,  t)
    store.set("press.unwind_diameter",       800.0,   t)
    store.set("press.rewind_diameter",       400.0,   t)
    store.set("laminator.nip_temp",          85.0,    t)
    store.set("laminator.nip_pressure",      4.0,     t)
    store.set("laminator.tunnel_temp",       60.0,    t)
    store.set("laminator.web_speed",         140.0,   t)
    store.set("laminator.adhesive_weight",   2.5,     t)
    store.set("slitter.speed",               145.0,   t)
    store.set("slitter.web_tension",         45.0,    t)
    store.set("slitter.reel_count",          100.0,   t)
    store.set("energy.line_power",           85.0,    t)
    store.set("energy.cumulative_kwh",       12000.0, t)

    server = OpcuaServer(config, store, host=_HOST, port=0)
    await server.start()
    # Allow one full sync cycle before client connects.
    await asyncio.sleep(0.6)

    port = server.actual_port
    assert port > 0, "OPC-UA server did not bind to a port"
    client = Client(f"opc.tcp://{_HOST}:{port}/")
    await client.connect()

    yield server, client, store, NAMESPACE_INDEX

    await client.disconnect()
    await server.stop()


@pytest.fixture
async def opcua_live() -> (  # type: ignore[override]
    tuple[DataEngine, OpcuaServer, Client, SignalStore, int]
):
    """OpcuaServer with DataEngine running as an async task.

    Used for subscription tests where value changes must come from the
    live engine rather than manual store injection.

    Yields ``(engine, server, client, store, namespace_index)``.
    """
    config, store, clock, engine = _base_config()

    # Pre-prime store before server starts.
    for _ in range(10):
        engine.tick()

    server = OpcuaServer(config, store, host=_HOST, port=0)
    await server.start()

    engine_task = asyncio.create_task(engine.run())
    # Wait for at least one complete OPC-UA sync cycle (500ms).
    await asyncio.sleep(0.8)

    port = server.actual_port
    assert port > 0, "OPC-UA server did not bind to a port"
    client = Client(f"opc.tcp://{_HOST}:{port}/")
    await client.connect()

    yield engine, server, client, store, NAMESPACE_INDEX

    await client.disconnect()
    engine_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await engine_task
    await server.stop()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _read_eurange(client: Client, node_id: ua.NodeId) -> ua.Range:
    """Return the EURange property value of a variable node."""
    node = client.get_node(node_id)
    children = await node.get_children()
    for child in children:
        bname = await child.read_browse_name()
        if bname.Name == "EURange":
            return await child.read_value()  # type: ignore[no-any-return]
    raise AssertionError(f"EURange property not found on {node_id}")


# ---------------------------------------------------------------------------
# Tests: hierarchical tree browse
# ---------------------------------------------------------------------------


class TestHierarchicalBrowse:
    """Browse the PackagingLine folder hierarchy from the Objects node."""

    async def test_packagingline_in_objects_folder(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """PackagingLine folder is a direct child of the OPC-UA Objects folder."""
        _, client, _, ns = opcua_static
        children = await client.nodes.objects.get_children()
        names = [(await c.read_browse_name()).Name for c in children]
        assert "PackagingLine" in names, (
            f"PackagingLine missing from Objects children: {names}"
        )

    async def test_equipment_folders_under_packagingline(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Press1, Laminator1, Slitter1, Energy are children of PackagingLine."""
        _, client, _, ns = opcua_static
        pl_node = client.get_node(ua.NodeId("PackagingLine", ns))
        children = await pl_node.get_children()
        names = {(await c.read_browse_name()).Name for c in children}
        expected = {"Press1", "Laminator1", "Slitter1", "Energy"}
        missing = expected - names
        assert not missing, (
            f"Equipment folders missing under PackagingLine: {missing}. "
            f"Found: {names}"
        )

    async def test_press1_sub_folders_present(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Registration, Ink, Dryer, MainDrive, Unwind, Rewind are under Press1."""
        _, client, _, ns = opcua_static
        press1_node = client.get_node(ua.NodeId("PackagingLine.Press1", ns))
        children = await press1_node.get_children()
        names = {(await c.read_browse_name()).Name for c in children}
        expected_folders = {"Registration", "Ink", "Dryer", "MainDrive", "Unwind", "Rewind"}
        missing = expected_folders - names
        assert not missing, (
            f"Sub-folders missing under Press1: {missing}. Found: {names}"
        )

    async def test_dryer_zone_sub_folders(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Dryer has Zone1, Zone2, Zone3 sub-folders per PRD Appendix B."""
        _, client, _, ns = opcua_static
        dryer_node = client.get_node(ua.NodeId("PackagingLine.Press1.Dryer", ns))
        children = await dryer_node.get_children()
        names = {(await c.read_browse_name()).Name for c in children}
        assert {"Zone1", "Zone2", "Zone3"} <= names, (
            f"Dryer zone sub-folders missing: {names}"
        )

    async def test_energy_leaf_nodes_browsable(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """LinePower and CumulativeKwh are browsable children of Energy."""
        _, client, _, ns = opcua_static
        energy_node = client.get_node(ua.NodeId("PackagingLine.Energy", ns))
        children = await energy_node.get_children()
        names = {(await c.read_browse_name()).Name for c in children}
        assert {"LinePower", "CumulativeKwh"} <= names, (
            f"Energy leaf nodes missing: {names}"
        )


# ---------------------------------------------------------------------------
# Tests: all nodes accessible and values within expected ranges
# ---------------------------------------------------------------------------


class TestAllNodesAccessible:
    """All 32 Appendix B nodes are accessible and carry valid values."""

    async def test_all_nodes_readable_by_string_nodeid(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Every Appendix B leaf node is readable via its ns=2;s=… NodeID."""
        server, client, _, ns = opcua_static
        errors: list[str] = []
        for node_path, _type_str, _ in EXPECTED_NODES:
            try:
                node = client.get_node(ua.NodeId(node_path, ns))
                val = await node.read_value()
                if val is None:
                    errors.append(f"{node_path}: read_value() returned None")
            except Exception as exc:
                errors.append(f"{node_path}: {exc}")
        assert not errors, "Read errors:\n" + "\n".join(errors)

    async def test_node_count_matches_appendix_b(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """32 registered leaf nodes — exactly as specified in Appendix B."""
        server, _, _, _ = opcua_static
        assert len(server.nodes) == len(EXPECTED_NODES), (
            f"Expected {len(EXPECTED_NODES)} nodes, got {len(server.nodes)}: "
            f"{sorted(server.nodes.keys())}"
        )

    async def test_all_double_nodes_finite_and_in_eurange(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """All Double nodes return finite values within their configured EURange."""
        _, client, _, ns = opcua_static
        errors: list[str] = []
        for node_path, type_str, _ in EXPECTED_NODES:
            if type_str != "Double":
                continue
            node = client.get_node(ua.NodeId(node_path, ns))
            dv = await node.read_data_value(raise_on_bad_status=False)
            if dv.Value is None or dv.Value.Value is None:
                errors.append(f"{node_path}: value is None")
                continue
            raw = dv.Value.Value
            if not isinstance(raw, int | float):
                errors.append(f"{node_path}: unexpected type {type(raw).__name__}")
                continue
            fval = float(raw)
            if not math.isfinite(fval):
                errors.append(f"{node_path}: non-finite value {fval}")
                continue
            eu = await _read_eurange(client, ua.NodeId(node_path, ns))
            if eu.Low == 0.0 and eu.High == 0.0:
                continue  # EURange not configured; skip range check
            if fval < eu.Low - 1e-4 or fval > eu.High + 1e-4:
                errors.append(
                    f"{node_path}: {fval} outside EURange [{eu.Low}, {eu.High}]"
                )
        assert not errors, "Out-of-range or non-finite Double nodes:\n" + "\n".join(errors)

    async def test_uint16_nodes_within_clamp_range(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """UInt16 nodes (State, FaultCode) are within their EURange bounds."""
        _, client, _, ns = opcua_static
        errors: list[str] = []
        for node_path, type_str, _ in EXPECTED_NODES:
            if type_str != "UInt16":
                continue
            eu = await _read_eurange(client, ua.NodeId(node_path, ns))
            node = client.get_node(ua.NodeId(node_path, ns))
            val = await node.read_value()
            lo = int(eu.Low)
            hi = int(eu.High)
            if hi > 0 and not (lo <= val <= hi):
                errors.append(f"{node_path}: value={val} outside [{lo}, {hi}]")
        assert not errors, "UInt16 nodes out of range:\n" + "\n".join(errors)

    async def test_counter_nodes_non_negative(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """UInt32 counter nodes carry non-negative values."""
        _, client, _, ns = opcua_static
        for node_path, type_str, _ in EXPECTED_NODES:
            if type_str != "UInt32":
                continue
            node = client.get_node(ua.NodeId(node_path, ns))
            val = await node.read_value()
            assert val >= 0, f"{node_path}: counter value {val} is negative"

    async def test_key_signals_reflect_injected_values(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Key Double nodes read back the exact values injected into the store.

        This validates the engine → store → OPC-UA sync path end-to-end
        against known reference values.
        """
        _, client, _, ns = opcua_static
        # (node_path, expected_value, tolerance)
        checks: list[tuple[str, float, float]] = [
            ("PackagingLine.Press1.LineSpeed",            150.0,   0.01),
            ("PackagingLine.Press1.WebTension",            50.0,   0.01),
            ("PackagingLine.Press1.Ink.Viscosity",         28.0,   0.01),
            ("PackagingLine.Press1.Ink.Temperature",       25.0,   0.01),
            ("PackagingLine.Press1.Dryer.Zone1.Temperature", 75.0,  0.01),
            ("PackagingLine.Press1.Dryer.Zone1.Setpoint",  75.0,   0.01),
            ("PackagingLine.Laminator1.NipTemperature",    85.0,   0.01),
            ("PackagingLine.Laminator1.WebSpeed",         140.0,   0.01),
            ("PackagingLine.Energy.LinePower",             85.0,   0.01),
            ("PackagingLine.Energy.CumulativeKwh",      12000.0,   0.01),
        ]
        errors: list[str] = []
        for node_path, expected, tol in checks:
            node = client.get_node(ua.NodeId(node_path, ns))
            val = await node.read_value()
            if abs(float(val) - expected) > tol:
                errors.append(f"{node_path}: expected {expected}, got {val}")
        assert not errors, "\n".join(errors)

    async def test_status_codes_are_good_for_good_quality_signals(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """All injected values have quality='good'; OPC-UA StatusCode must be Good."""
        _, client, _, ns = opcua_static
        errors: list[str] = []
        for node_path, type_str, _ in EXPECTED_NODES:
            if type_str != "Double":
                continue
            node = client.get_node(ua.NodeId(node_path, ns))
            dv = await node.read_data_value()
            if not dv.StatusCode.is_good():
                errors.append(
                    f"{node_path}: StatusCode={dv.StatusCode} is not Good"
                )
        assert not errors, "Non-Good status codes:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# Tests: setpoint write propagation (no running engine)
# ---------------------------------------------------------------------------


class TestSetpointWrite:
    """OPC-UA setpoint writes propagate to SignalStore (no competing engine)."""

    async def test_single_setpoint_propagates_to_store(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Write Zone1.Setpoint via OPC-UA; SignalStore gets the new value."""
        _, client, store, ns = opcua_static
        node_path = "PackagingLine.Press1.Dryer.Zone1.Setpoint"
        signal_id = "press.dryer_setpoint_zone_1"

        new_sp = 92.0
        node = client.get_node(ua.NodeId(node_path, ns))
        await node.write_value(new_sp)

        # Wait for the update loop (500ms) to detect and propagate.
        await asyncio.sleep(0.8)

        sv = store.get(signal_id)
        assert sv is not None, f"{signal_id} not in store after setpoint write"
        assert abs(float(sv.value) - new_sp) < 0.01, (
            f"Store {signal_id}={sv.value}, expected {new_sp}"
        )

    async def test_all_three_zone_setpoints_independent(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """All three dryer zone setpoints write and propagate independently."""
        _, client, store, ns = opcua_static
        writes: dict[str, tuple[str, float]] = {
            "PackagingLine.Press1.Dryer.Zone1.Setpoint": (
                "press.dryer_setpoint_zone_1", 82.0,
            ),
            "PackagingLine.Press1.Dryer.Zone2.Setpoint": (
                "press.dryer_setpoint_zone_2", 87.0,
            ),
            "PackagingLine.Press1.Dryer.Zone3.Setpoint": (
                "press.dryer_setpoint_zone_3", 92.0,
            ),
        }
        for node_path, (_, val) in writes.items():
            node = client.get_node(ua.NodeId(node_path, ns))
            await node.write_value(val)

        await asyncio.sleep(0.8)

        errors: list[str] = []
        for _, (signal_id, expected) in writes.items():
            sv = store.get(signal_id)
            if sv is None:
                errors.append(f"{signal_id} not in store")
            elif abs(float(sv.value) - expected) > 0.01:
                errors.append(f"{signal_id}: got {sv.value}, expected {expected}")
        assert not errors, "\n".join(errors)

    async def test_readonly_node_write_rejected(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Writing to a read-only node (LineSpeed) raises an OPC-UA error."""
        _, client, _, ns = opcua_static
        node = client.get_node(ua.NodeId("PackagingLine.Press1.LineSpeed", ns))
        try:
            await node.write_value(999.0)
            pytest.fail("Expected an exception writing to a read-only node")
        except Exception:
            pass  # Any OPC-UA error is acceptable here


# ---------------------------------------------------------------------------
# Subscription handler
# ---------------------------------------------------------------------------


class _ChangeHandler:
    """Collects OPC-UA data change notifications for subscription tests."""

    def __init__(self) -> None:
        self.values: list[object] = []

    def datachange_notification(
        self,
        node: object,
        val: object,
        data: object,
    ) -> None:
        self.values.append(val)


# ---------------------------------------------------------------------------
# Tests: subscriptions with live engine
# ---------------------------------------------------------------------------


class TestSubscriptionsWithLiveEngine:
    """OPC-UA subscriptions deliver data change notifications from the engine."""

    async def test_subscription_receives_initial_notification(
        self,
        opcua_live: tuple[DataEngine, OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Creating a subscription triggers at least the initial current-value notification."""
        _, _, client, _, ns = opcua_live
        handler = _ChangeHandler()
        sub = await client.create_subscription(500, handler)
        node = client.get_node(ua.NodeId("PackagingLine.Press1.LineSpeed", ns))
        await sub.subscribe_data_change(node)

        # Allow time for the initial notification to arrive.
        await asyncio.sleep(1.5)
        await sub.delete()

        assert len(handler.values) >= 1, (
            "Expected at least the initial subscription notification, got none"
        )

    async def test_subscription_reflects_store_value_change(
        self,
        opcua_live: tuple[DataEngine, OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """A store change injected after subscribing appears in subscription events."""
        _, _, client, store, ns = opcua_live
        signal_id = "press.line_speed"
        node_path = "PackagingLine.Press1.LineSpeed"

        handler = _ChangeHandler()
        sub = await client.create_subscription(500, handler)
        node = client.get_node(ua.NodeId(node_path, ns))
        await sub.subscribe_data_change(node)

        # Wait for initial notification.
        await asyncio.sleep(1.0)
        count_before = len(handler.values)

        # Inject a value change.
        store.set(signal_id, 357.0, 0.0, "good")

        # Wait for update loop (500ms) + subscription publish (500ms) + buffer.
        await asyncio.sleep(2.0)
        await sub.delete()

        assert len(handler.values) > count_before, (
            f"No new notifications after injecting value "
            f"(before={count_before}, after={len(handler.values)})"
        )

    async def test_multiple_node_subscriptions_all_notify(
        self,
        opcua_live: tuple[DataEngine, OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Subscriptions on three nodes all deliver change notifications."""
        _, _, client, store, ns = opcua_live
        handler = _ChangeHandler()
        sub = await client.create_subscription(500, handler)

        node_paths = [
            "PackagingLine.Press1.LineSpeed",
            "PackagingLine.Press1.WebTension",
            "PackagingLine.Energy.LinePower",
        ]
        for path in node_paths:
            await sub.subscribe_data_change(client.get_node(ua.NodeId(path, ns)))

        # Force value changes on all three subscribed signals.
        store.set("press.line_speed",  175.0, 0.0, "good")
        store.set("press.web_tension",  48.0, 0.0, "good")
        store.set("energy.line_power",  92.0, 0.0, "good")

        await asyncio.sleep(2.5)
        await sub.delete()

        # Expect at least 3 notifications (one per subscribed node).
        assert len(handler.values) >= 3, (
            f"Expected ≥3 subscription events for 3 nodes, got {len(handler.values)}"
        )


# ---------------------------------------------------------------------------
# Tests: namespace configuration
# ---------------------------------------------------------------------------


class TestNamespaceConfiguration:
    """OPC-UA namespace matches PRD Section 3.2 specification."""

    async def test_namespace_uri_registered_at_index_2(
        self,
        opcua_static: tuple[OpcuaServer, Client, SignalStore, int],
    ) -> None:
        """Namespace URI 'urn:collatr:factory-simulator' is registered at ns=2."""
        _, client, _, _ = opcua_static
        ns_array = await client.get_namespace_array()
        assert NAMESPACE_URI in ns_array, (
            f"{NAMESPACE_URI!r} not found in namespace array: {ns_array}"
        )
        idx = ns_array.index(NAMESPACE_URI)
        assert idx == NAMESPACE_INDEX, (
            f"Expected ns={NAMESPACE_INDEX} for {NAMESPACE_URI!r}, got ns={idx}"
        )
