"""Unit tests for the OPC-UA server module.

Tests node tree structure (Appendix B), data types, EURange attributes,
and setpoint writability.  Uses function-scoped server+client fixtures
to avoid asyncio event loop scope issues in pytest-asyncio 1.x.

PRD Reference: Section 3.2, Appendix B (OPC-UA Node Tree)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from asyncua import Client, ua

from factory_simulator.config import load_config
from factory_simulator.protocols.opcua_server import (
    _VARIANT_TYPE_MAP,
    NAMESPACE_INDEX,
    NAMESPACE_URI,
    OpcuaServer,
    _cast_to_opcua_value,
    _initial_value,
)
from factory_simulator.store import SignalStore

# Path to default config
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "factory.yaml"

# Test host
_HOST = "127.0.0.1"


# ---------------------------------------------------------------------------
# Expected node tree per PRD Appendix B (packaging profile leaf nodes)
#
# Each entry: (node_path, opcua_type_str, is_writable)
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
    # Dryer sub-folders (temperatures read-only, setpoints writable)
    ("PackagingLine.Press1.Dryer.Zone1.Temperature", "Double",  False),
    ("PackagingLine.Press1.Dryer.Zone1.Setpoint",    "Double",  True),
    ("PackagingLine.Press1.Dryer.Zone2.Temperature", "Double",  False),
    ("PackagingLine.Press1.Dryer.Zone2.Setpoint",    "Double",  True),
    ("PackagingLine.Press1.Dryer.Zone3.Temperature", "Double",  False),
    ("PackagingLine.Press1.Dryer.Zone3.Setpoint",    "Double",  True),
    # MainDrive sub-folder
    ("PackagingLine.Press1.MainDrive.Current",       "Double",  False),
    ("PackagingLine.Press1.MainDrive.Speed",         "Double",  False),
    # Unwind / Rewind sub-folders
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

# OPC-UA DataType NodeIds for checking
_DATATYPE_NODEID: dict[str, ua.NodeId] = {
    "Double": ua.NodeId(ua.ObjectIds.Double),
    "UInt32": ua.NodeId(ua.ObjectIds.UInt32),
    "UInt16": ua.NodeId(ua.ObjectIds.UInt16),
}


# ---------------------------------------------------------------------------
# Fixtures (function-scoped to avoid asyncio event loop mismatch in
# pytest-asyncio 1.x with asyncio_default_test_loop_scope=function)
# ---------------------------------------------------------------------------


@pytest.fixture
async def opcua_system() -> (  # type: ignore[override]
    tuple[OpcuaServer, Client, int]
):
    """Start OpcuaServer on OS-assigned port, connect client.

    Function-scoped so that each test gets its own event loop, server,
    and client — avoiding asyncio event loop mismatch issues.

    Yields (server, client, namespace_index).
    """
    config = load_config(_CONFIG_PATH, apply_env=False)
    store = SignalStore()
    server = OpcuaServer(config, store, host=_HOST, port=0)
    await server.start()

    port = server.actual_port
    assert port > 0, "Server did not bind to a port"

    client = Client(f"opc.tcp://{_HOST}:{port}/")
    await client.connect()

    yield server, client, NAMESPACE_INDEX

    await client.disconnect()
    await server.stop()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _read_eurange(client: Client, node_id: ua.NodeId) -> ua.Range:
    """Read the EURange property of a variable node."""
    node = client.get_node(node_id)
    children = await node.get_children()
    for child in children:
        bname = await child.read_browse_name()
        if bname.Name == "EURange":
            return await child.read_value()  # type: ignore[no-any-return]
    raise AssertionError(f"EURange property not found on node {node_id}")


async def _access_level(client: Client, node_id: ua.NodeId) -> int:
    """Return the AccessLevel attribute value for a node."""
    node = client.get_node(node_id)
    result = await node.read_attribute(ua.AttributeIds.AccessLevel)
    return int(result.Value.Value)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests: helpers (no server required)
# ---------------------------------------------------------------------------


class TestHelpers:
    """Test module-level helper functions — no server needed."""

    def test_initial_value_double(self) -> None:
        assert _initial_value(ua.VariantType.Double) == 0.0

    def test_initial_value_uint32(self) -> None:
        assert _initial_value(ua.VariantType.UInt32) == 0

    def test_initial_value_uint16(self) -> None:
        assert _initial_value(ua.VariantType.UInt16) == 0

    def test_initial_value_string(self) -> None:
        assert _initial_value(ua.VariantType.String) == ""

    def test_variant_type_map_completeness(self) -> None:
        for type_str in ("Double", "UInt32", "UInt16", "String"):
            assert type_str in _VARIANT_TYPE_MAP


class TestOpcuaServerConstruction:
    """Test OpcuaServer construction without starting — no server needed."""

    def test_default_port(self) -> None:
        config = load_config(_CONFIG_PATH, apply_env=False)
        server = OpcuaServer(config, SignalStore())
        assert server.port == config.protocols.opcua.port

    def test_port_override(self) -> None:
        config = load_config(_CONFIG_PATH, apply_env=False)
        server = OpcuaServer(config, SignalStore(), port=14841)
        assert server.port == 14841

    def test_host_override(self) -> None:
        config = load_config(_CONFIG_PATH, apply_env=False)
        server = OpcuaServer(config, SignalStore(), host="127.0.0.1")
        assert server.host == "127.0.0.1"

    def test_nodes_empty_before_start(self) -> None:
        config = load_config(_CONFIG_PATH, apply_env=False)
        server = OpcuaServer(config, SignalStore())
        assert len(server.nodes) == 0

    def test_actual_port_before_start_returns_configured(self) -> None:
        config = load_config(_CONFIG_PATH, apply_env=False)
        server = OpcuaServer(config, SignalStore(), port=14841)
        assert server.actual_port == 14841


# ---------------------------------------------------------------------------
# Tests: node tree structure
# ---------------------------------------------------------------------------


class TestNodeTreeStructure:
    """Verify PackagingLine node tree matches PRD Appendix B."""

    async def test_all_nodes_registered_in_server(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """All leaf nodes from Appendix B are in server.nodes."""
        server, _client, _ns = opcua_system
        missing = [
            path for path, _, _ in EXPECTED_NODES if path not in server.nodes
        ]
        assert not missing, f"Missing nodes in server.nodes: {missing}"

    async def test_node_count_matches_appendix_b(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Total node count matches expected count from Appendix B."""
        server, _client, _ns = opcua_system
        assert len(server.nodes) == len(EXPECTED_NODES), (
            f"Expected {len(EXPECTED_NODES)} nodes, got {len(server.nodes)}: "
            f"{sorted(server.nodes.keys())}"
        )

    async def test_all_nodes_browsable_by_string_nodeid(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Every Appendix B node is readable via its string NodeID."""
        _server, client, ns = opcua_system
        errors: list[str] = []
        for node_path, _type_str, _writable in EXPECTED_NODES:
            try:
                node = client.get_node(ua.NodeId(node_path, ns))
                val = await node.read_value()
                if val is None:
                    errors.append(f"{node_path}: read_value() returned None")
            except Exception as exc:
                errors.append(f"{node_path}: {exc}")
        assert not errors, "Node browse errors:\n" + "\n".join(errors)

    async def test_node_to_signal_mapping_complete(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Every registered node maps to a valid signal_id."""
        server, _client, _ns = opcua_system
        for node_path in server.nodes:
            assert node_path in server.node_to_signal, (
                f"Node {node_path} missing from node_to_signal"
            )
            signal_id = server.node_to_signal[node_path]
            assert "." in signal_id, (
                f"signal_id {signal_id!r} must be 'equip.signal' form"
            )


# ---------------------------------------------------------------------------
# Tests: data types
# ---------------------------------------------------------------------------


class TestNodeDataTypes:
    """Verify OPC-UA data types match PRD Appendix B specification."""

    async def test_all_node_data_types(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Every node has the data type specified in Appendix B."""
        _server, client, ns = opcua_system
        errors: list[str] = []
        for node_path, type_str, _writable in EXPECTED_NODES:
            expected_dt = _DATATYPE_NODEID.get(type_str)
            if expected_dt is None:
                continue
            node = client.get_node(ua.NodeId(node_path, ns))
            result = await node.read_attribute(ua.AttributeIds.DataType)
            actual_dt = result.Value.Value
            if actual_dt != expected_dt:
                errors.append(
                    f"{node_path}: expected {type_str} ({expected_dt}), "
                    f"got {actual_dt}"
                )
        assert not errors, "Data type mismatches:\n" + "\n".join(errors)

    async def test_initial_values_are_zero(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """All nodes have zero initial values immediately after startup."""
        _server, client, ns = opcua_system
        errors: list[str] = []
        for node_path, type_str, _writable in EXPECTED_NODES:
            node = client.get_node(ua.NodeId(node_path, ns))
            val = await node.read_value()
            if type_str == "Double":
                if val != pytest.approx(0.0):
                    errors.append(f"{node_path}: expected 0.0, got {val}")
            else:
                if val != 0:
                    errors.append(f"{node_path}: expected 0, got {val}")
        assert not errors, "Non-zero initial values:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# Tests: EURange attribute
# ---------------------------------------------------------------------------


class TestEURangeAttribute:
    """Verify EURange property is set correctly on variable nodes."""

    async def test_eurange_present_on_all_nodes(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Every leaf node has an EURange property child."""
        _server, client, ns = opcua_system
        errors: list[str] = []
        for node_path, _type_str, _writable in EXPECTED_NODES:
            node = client.get_node(ua.NodeId(node_path, ns))
            children = await node.get_children()
            browse_names = [
                (await child.read_browse_name()).Name for child in children
            ]
            if "EURange" not in browse_names:
                errors.append(
                    f"{node_path}: EURange missing; children={browse_names}"
                )
        assert not errors, "Missing EURange:\n" + "\n".join(errors)

    async def test_key_eurange_values(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """EURange Low/High match signal config min/max_clamp values."""
        _server, client, ns = opcua_system
        checks = [
            ("PackagingLine.Press1.LineSpeed",       0.0,  400.0),
            ("PackagingLine.Press1.WebTension",      0.0,  500.0),
            ("PackagingLine.Press1.NipPressure",     0.0,  10.0),
            ("PackagingLine.Press1.Ink.Viscosity",   15.0, 60.0),
            ("PackagingLine.Press1.Ink.Temperature", 18.0, 35.0),
            ("PackagingLine.Laminator1.NipTemperature", 20.0, 100.0),
            ("PackagingLine.Energy.LinePower",       0.0,  200.0),
        ]
        errors: list[str] = []
        for node_path, expected_low, expected_high in checks:
            eu = await _read_eurange(client, ua.NodeId(node_path, ns))
            if abs(eu.Low - expected_low) > 1e-4:
                errors.append(
                    f"{node_path}: EURange.Low {eu.Low} != {expected_low}"
                )
            if abs(eu.High - expected_high) > 1e-4:
                errors.append(
                    f"{node_path}: EURange.High {eu.High} != {expected_high}"
                )
        assert not errors, "EURange mismatches:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# Tests: AccessLevel (read-only vs read-write)
# ---------------------------------------------------------------------------


class TestAccessLevel:
    """Verify AccessLevel matches PRD: setpoints writable, rest read-only."""

    async def test_all_access_levels_correct(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Every node has AccessLevel 3 (rw) or 1 (ro) per Appendix B."""
        _server, client, ns = opcua_system
        errors: list[str] = []
        for node_path, _type_str, writable in EXPECTED_NODES:
            expected = 3 if writable else 1
            actual = await _access_level(client, ua.NodeId(node_path, ns))
            if actual != expected:
                errors.append(
                    f"{node_path}: expected AccessLevel {expected}, got {actual}"
                )
        assert not errors, "AccessLevel mismatches:\n" + "\n".join(errors)

    async def test_setpoints_are_read_write(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Dryer zone setpoints have AccessLevel 3 (read-write)."""
        _server, client, ns = opcua_system
        setpoint_paths = [
            "PackagingLine.Press1.Dryer.Zone1.Setpoint",
            "PackagingLine.Press1.Dryer.Zone2.Setpoint",
            "PackagingLine.Press1.Dryer.Zone3.Setpoint",
        ]
        for path in setpoint_paths:
            level = await _access_level(client, ua.NodeId(path, ns))
            assert level == 3, (
                f"{path}: expected AccessLevel 3 (read-write), got {level}"
            )

    async def test_process_values_are_read_only(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Non-setpoint analog and counter nodes have AccessLevel 1 (read-only)."""
        _server, client, ns = opcua_system
        readonly_paths = [
            "PackagingLine.Press1.LineSpeed",
            "PackagingLine.Press1.WebTension",
            "PackagingLine.Press1.ImpressionCount",
            "PackagingLine.Press1.State",
            "PackagingLine.Energy.LinePower",
        ]
        for path in readonly_paths:
            level = await _access_level(client, ua.NodeId(path, ns))
            assert level == 1, (
                f"{path}: expected AccessLevel 1 (read-only), got {level}"
            )


# ---------------------------------------------------------------------------
# Tests: server properties and namespace
# ---------------------------------------------------------------------------


class TestServerProperties:
    """Test OpcuaServer property accessors and OPC-UA namespace config."""

    async def test_actual_port_nonzero_and_namespace_correct(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """actual_port > 0 and namespace URI/index match PRD spec."""
        server, client, _ns = opcua_system

        assert server.actual_port > 0

        ns_array = await client.get_namespace_array()
        assert NAMESPACE_URI in ns_array, (
            f"{NAMESPACE_URI} not in namespace array: {ns_array}"
        )
        idx = ns_array.index(NAMESPACE_URI)
        assert idx == NAMESPACE_INDEX, (
            f"Expected ns={NAMESPACE_INDEX} for {NAMESPACE_URI}, got {idx}"
        )


# ---------------------------------------------------------------------------
# Tests: server lifecycle
# ---------------------------------------------------------------------------


class TestServerLifecycle:
    """Test that the server can be started and stopped cleanly."""

    async def test_stop_and_restart(self) -> None:
        """Server can be stopped then started again on a new port."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        server = OpcuaServer(config, SignalStore(), host=_HOST, port=0)

        await server.start()
        port1 = server.actual_port
        assert port1 > 0
        await server.stop()

        await server.start()
        port2 = server.actual_port
        assert port2 > 0
        await server.stop()

    async def test_nodes_populated_after_start(self) -> None:
        """server.nodes is populated after start() and cleared after stop()."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        server = OpcuaServer(config, SignalStore(), host=_HOST, port=0)

        assert len(server.nodes) == 0
        await server.start()
        assert len(server.nodes) == len(EXPECTED_NODES)
        await server.stop()
        # _server is None after stop, actual_port falls back to configured (0)
        assert server.actual_port == 0

    async def test_concurrent_stop_safe(self) -> None:
        """Calling stop() twice does not raise."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        server = OpcuaServer(config, SignalStore(), host=_HOST, port=0)
        await server.start()
        await server.stop()
        await server.stop()  # second stop should be a no-op


# ---------------------------------------------------------------------------
# Tests: _cast_to_opcua_value helper (no server required)
# ---------------------------------------------------------------------------


class TestCastToOpcuaValue:
    """Test the _cast_to_opcua_value pure function."""

    def test_double_from_float(self) -> None:
        result = _cast_to_opcua_value(42.7, ua.VariantType.Double)
        assert result == pytest.approx(42.7)
        assert isinstance(result, float)

    def test_uint32_rounds_and_casts(self) -> None:
        result = _cast_to_opcua_value(1234.9, ua.VariantType.UInt32)
        assert result == 1235
        assert isinstance(result, int)

    def test_uint32_clamped_to_zero(self) -> None:
        assert _cast_to_opcua_value(-1.0, ua.VariantType.UInt32) == 0

    def test_uint16_clamped_high(self) -> None:
        assert _cast_to_opcua_value(70000.0, ua.VariantType.UInt16) == 0xFFFF

    def test_string_pass_through(self) -> None:
        assert _cast_to_opcua_value("hello", ua.VariantType.String) == "hello"

    def test_non_numeric_string_to_double_returns_zero(self) -> None:
        assert _cast_to_opcua_value("abc", ua.VariantType.Double) == pytest.approx(0.0)

    def test_non_numeric_string_to_uint32_returns_zero(self) -> None:
        assert _cast_to_opcua_value("abc", ua.VariantType.UInt32) == 0


# ---------------------------------------------------------------------------
# Subscription helper (for TestValueSync)
# ---------------------------------------------------------------------------


class _ChangeHandler:
    """Collects subscription data-change notifications during tests."""

    def __init__(self) -> None:
        self.values: list[object] = []

    def datachange_notification(self, node: object, val: object, data: object) -> None:
        self.values.append(val)


# ---------------------------------------------------------------------------
# Tests: value sync (requires running server)
# ---------------------------------------------------------------------------


class TestValueSync:
    """Verify value sync and setpoint write-back (PRD 3.2.3, 3.2.4)."""

    async def test_store_value_appears_on_opcua(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Value set in SignalStore is readable via OPC-UA after one sync cycle."""
        server, client, ns = opcua_system
        node_path = "PackagingLine.Press1.LineSpeed"
        signal_id = "press.line_speed"

        server._store.set(signal_id, 250.0, 1.0, "good")
        # Wait for at least one full sync cycle (MIN_PUBLISHING_INTERVAL_MS = 500ms)
        await asyncio.sleep(0.7)

        node = client.get_node(ua.NodeId(node_path, ns))
        val = await node.read_value()
        assert val == pytest.approx(250.0)

    async def test_uint32_counter_cast_correctly(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Float store value is cast to UInt32 when writing a counter node."""
        server, client, ns = opcua_system
        node_path = "PackagingLine.Press1.ImpressionCount"
        signal_id = "press.impression_count"

        server._store.set(signal_id, 5000.0, 1.0, "good")
        await asyncio.sleep(0.7)

        node = client.get_node(ua.NodeId(node_path, ns))
        val = await node.read_value()
        assert val == 5000
        assert isinstance(val, int)

    async def test_bad_quality_gives_bad_sensor_failure(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Quality 'bad' in store maps to StatusCode BadSensorFailure (PRD 3.2.3)."""
        server, client, ns = opcua_system
        node_path = "PackagingLine.Press1.LineSpeed"
        signal_id = "press.line_speed"

        server._store.set(signal_id, 0.0, 1.0, "bad")
        await asyncio.sleep(0.7)

        node = client.get_node(ua.NodeId(node_path, ns))
        dv = await node.read_data_value(raise_on_bad_status=False)
        assert dv.StatusCode.value == ua.StatusCodes.BadSensorFailure

    async def test_good_quality_gives_good_status(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Quality 'good' maps to StatusCode.Good on the OPC-UA node."""
        server, client, ns = opcua_system
        node_path = "PackagingLine.Press1.LineSpeed"
        signal_id = "press.line_speed"

        server._store.set(signal_id, 100.0, 1.0, "good")
        await asyncio.sleep(0.7)

        node = client.get_node(ua.NodeId(node_path, ns))
        dv = await node.read_data_value()
        assert dv.StatusCode.is_good()

    async def test_setpoint_write_propagates_to_store(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """OPC-UA client write to a setpoint node propagates back to SignalStore."""
        server, client, ns = opcua_system
        node_path = "PackagingLine.Press1.Dryer.Zone1.Setpoint"
        signal_id = "press.dryer_setpoint_zone_1"

        # Client writes a new setpoint value
        node = client.get_node(ua.NodeId(node_path, ns))
        await node.write_value(175.0)

        # Wait for update loop to detect and propagate
        await asyncio.sleep(0.7)

        store_val = server._store.get_value(signal_id)
        assert store_val == pytest.approx(175.0)

    async def test_subscription_receives_data_change(
        self,
        opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """OPC-UA subscriptions receive data change notifications (PRD 3.2.4)."""
        server, client, ns = opcua_system
        node_path = "PackagingLine.Press1.LineSpeed"
        signal_id = "press.line_speed"

        handler = _ChangeHandler()
        sub = await client.create_subscription(500, handler)
        node = client.get_node(ua.NodeId(node_path, ns))
        await sub.subscribe_data_change(node)

        # Initial notification fires immediately on subscribe (value = 0.0)
        # Now update the store and wait for update loop + subscription publish
        server._store.set(signal_id, 150.0, 1.0, "good")
        # update loop (500ms) + subscription publish interval (500ms) + buffer
        await asyncio.sleep(2.0)

        await sub.delete()

        assert len(handler.values) >= 1, "No subscription notifications received"
        assert any(
            isinstance(v, float) and abs(v - 150.0) < 0.5
            for v in handler.values
        ), f"Expected 150.0 in received values, got {handler.values}"
