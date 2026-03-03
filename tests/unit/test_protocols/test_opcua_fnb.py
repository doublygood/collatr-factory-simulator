"""Unit tests for F&B OPC-UA node tree and MQTT topic map.

Validates that the OPC-UA server builds the FoodBevLine node tree correctly
from ``config/factory-foodbev.yaml``, and that the MQTT publisher produces
exactly 13 topics with the ``foodbev1`` line-id prefix and no vibration topics.

PRD Reference: Section 3.2, 3.3, Appendix B (FoodBevLine), Appendix C (F&B)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from asyncua import Client, ua

from factory_simulator.config import load_config
from factory_simulator.protocols.mqtt_publisher import (
    build_batch_vibration_entry,
    build_topic_map,
)
from factory_simulator.protocols.opcua_server import (
    _VARIANT_TYPE_MAP,
    NAMESPACE_INDEX,
    OpcuaServer,
)
from factory_simulator.store import SignalStore

# Paths
_FNB_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "factory-foodbev.yaml"
)
_HOST = "127.0.0.1"


# ---------------------------------------------------------------------------
# Expected F&B OPC-UA nodes per PRD Appendix B (FoodBevLine section)
#
# Each entry: (node_path, opcua_type_str, is_writable)
# All F&B setpoints are accessed via Modbus only (Eurotherm / Siemens),
# so all 19 OPC-UA nodes are read-only.
# ---------------------------------------------------------------------------

EXPECTED_FNB_NODES: list[tuple[str, str, bool]] = [
    # Mixer1
    ("FoodBevLine.Mixer1.State",               "UInt16", False),
    ("FoodBevLine.Mixer1.BatchId",             "String", False),
    # Oven1
    ("FoodBevLine.Oven1.State",                "UInt16", False),
    # Filler1
    ("FoodBevLine.Filler1.LineSpeed",          "Double", False),
    ("FoodBevLine.Filler1.FillWeight",         "Double", False),
    ("FoodBevLine.Filler1.FillTarget",         "Double", False),
    ("FoodBevLine.Filler1.FillDeviation",      "Double", False),
    ("FoodBevLine.Filler1.PacksProduced",      "UInt32", False),
    ("FoodBevLine.Filler1.RejectCount",        "UInt32", False),
    ("FoodBevLine.Filler1.State",              "UInt16", False),
    # QC1
    ("FoodBevLine.QC1.ActualWeight",           "Double", False),
    ("FoodBevLine.QC1.OverweightCount",        "UInt32", False),
    ("FoodBevLine.QC1.UnderweightCount",       "UInt32", False),
    ("FoodBevLine.QC1.MetalDetectTrips",       "UInt32", False),
    ("FoodBevLine.QC1.Throughput",             "Double", False),
    ("FoodBevLine.QC1.RejectTotal",            "UInt32", False),
    # CIP1
    ("FoodBevLine.CIP1.State",                 "UInt16", False),
    # Energy
    ("FoodBevLine.Energy.LinePower",           "Double", False),
    ("FoodBevLine.Energy.CumulativeKwh",       "Double", False),
]

# OPC-UA DataType NodeIds for type checking
_DATATYPE_NODEID: dict[str, ua.NodeId] = {
    "Double": ua.NodeId(ua.ObjectIds.Double),
    "UInt32": ua.NodeId(ua.ObjectIds.UInt32),
    "UInt16": ua.NodeId(ua.ObjectIds.UInt16),
}


# ---------------------------------------------------------------------------
# Fixtures (function-scoped per project memory: avoid asyncio loop mismatch)
# ---------------------------------------------------------------------------


@pytest.fixture
async def fnb_opcua_system() -> (  # type: ignore[override]
    tuple[OpcuaServer, Client, int]
):
    """Start OpcuaServer with F&B config on OS-assigned port, connect client.

    Function-scoped so each test gets its own event loop, server, and client.
    Yields (server, client, namespace_index).
    """
    config = load_config(_FNB_CONFIG_PATH, apply_env=False)
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


@pytest.fixture
def fnb_config() -> object:
    """Load the F&B factory config."""
    return load_config(_FNB_CONFIG_PATH, apply_env=False)


# ---------------------------------------------------------------------------
# Tests: OPC-UA node tree structure (Appendix B, FoodBevLine section)
# ---------------------------------------------------------------------------


class TestFnbNodeTreeStructure:
    """Verify FoodBevLine node tree matches PRD Appendix B (F&B section)."""

    async def test_all_fnb_nodes_registered(
        self,
        fnb_opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """All 19 leaf nodes from Appendix B (FoodBevLine) are in server.nodes."""
        server, _client, _ns = fnb_opcua_system
        missing = [
            path for path, _, _ in EXPECTED_FNB_NODES if path not in server.nodes
        ]
        assert not missing, f"Missing FoodBevLine nodes in server.nodes: {missing}"

    async def test_node_count_matches_appendix_b(
        self,
        fnb_opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Total node count matches 19 nodes from Appendix B (FoodBevLine section)."""
        server, _client, _ns = fnb_opcua_system
        assert len(server.nodes) == len(EXPECTED_FNB_NODES), (
            f"Expected {len(EXPECTED_FNB_NODES)} nodes, got {len(server.nodes)}: "
            f"{sorted(server.nodes.keys())}"
        )

    async def test_all_nodes_browsable_by_string_nodeid(
        self,
        fnb_opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Every Appendix B (F&B) node is readable via its string NodeID."""
        _server, client, ns = fnb_opcua_system
        errors: list[str] = []
        for node_path, _type_str, _writable in EXPECTED_FNB_NODES:
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
        fnb_opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Every registered node maps to a valid 'equip.signal' signal_id."""
        server, _client, _ns = fnb_opcua_system
        for node_path in server.nodes:
            assert node_path in server.node_to_signal, (
                f"Node {node_path} missing from node_to_signal"
            )
            signal_id = server.node_to_signal[node_path]
            assert "." in signal_id, (
                f"signal_id {signal_id!r} must be 'equip.signal' form"
            )

    async def test_no_packaging_line_nodes_in_fnb_server(
        self,
        fnb_opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """F&B config does not build PackagingLine nodes."""
        server, _client, _ns = fnb_opcua_system
        packaging_nodes = [p for p in server.nodes if p.startswith("PackagingLine")]
        assert not packaging_nodes, (
            f"PackagingLine nodes found in F&B server: {packaging_nodes}"
        )


# ---------------------------------------------------------------------------
# Tests: OPC-UA data types (Appendix B type column)
# ---------------------------------------------------------------------------


class TestFnbNodeDataTypes:
    """Verify F&B OPC-UA data types match PRD Appendix B specification."""

    async def test_all_node_data_types(
        self,
        fnb_opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Every non-String node has the data type specified in Appendix B."""
        _server, client, ns = fnb_opcua_system
        errors: list[str] = []
        for node_path, type_str, _writable in EXPECTED_FNB_NODES:
            expected_dt = _DATATYPE_NODEID.get(type_str)
            if expected_dt is None:
                continue  # Skip "String" — no OPC-UA DataType NodeId comparison
            node = client.get_node(ua.NodeId(node_path, ns))
            result = await node.read_attribute(ua.AttributeIds.DataType)
            actual_dt = result.Value.Value
            if actual_dt != expected_dt:
                errors.append(
                    f"{node_path}: expected {type_str} ({expected_dt}), "
                    f"got {actual_dt}"
                )
        assert not errors, "Data type mismatches:\n" + "\n".join(errors)

    async def test_state_nodes_are_uint16(
        self,
        fnb_opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """State enum nodes use UInt16 per Appendix B."""
        _server, client, ns = fnb_opcua_system
        state_nodes = [
            "FoodBevLine.Mixer1.State",
            "FoodBevLine.Oven1.State",
            "FoodBevLine.Filler1.State",
            "FoodBevLine.CIP1.State",
        ]
        expected_dt = _DATATYPE_NODEID["UInt16"]
        for path in state_nodes:
            node = client.get_node(ua.NodeId(path, ns))
            result = await node.read_attribute(ua.AttributeIds.DataType)
            assert result.Value.Value == expected_dt, (
                f"{path}: expected UInt16, got {result.Value.Value}"
            )

    async def test_counter_nodes_are_uint32(
        self,
        fnb_opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Counter nodes use UInt32 per Appendix B."""
        _server, client, ns = fnb_opcua_system
        counter_nodes = [
            "FoodBevLine.Filler1.PacksProduced",
            "FoodBevLine.Filler1.RejectCount",
            "FoodBevLine.QC1.OverweightCount",
            "FoodBevLine.QC1.UnderweightCount",
            "FoodBevLine.QC1.MetalDetectTrips",
            "FoodBevLine.QC1.RejectTotal",
        ]
        expected_dt = _DATATYPE_NODEID["UInt32"]
        for path in counter_nodes:
            node = client.get_node(ua.NodeId(path, ns))
            result = await node.read_attribute(ua.AttributeIds.DataType)
            assert result.Value.Value == expected_dt, (
                f"{path}: expected UInt32, got {result.Value.Value}"
            )

    async def test_batch_id_initial_value_is_string(
        self,
        fnb_opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """BatchId node reads as a Python str (OPC-UA String type)."""
        _server, client, ns = fnb_opcua_system
        node = client.get_node(ua.NodeId("FoodBevLine.Mixer1.BatchId", ns))
        val = await node.read_value()
        assert isinstance(val, str), (
            f"BatchId expected str (String node), got {type(val)}: {val!r}"
        )


# ---------------------------------------------------------------------------
# Tests: EURange attribute (PRD Appendix B attribute conventions)
# ---------------------------------------------------------------------------


class TestFnbEURangeAttribute:
    """Verify EURange is present and correct on F&B variable nodes."""

    async def test_eurange_present_on_all_nodes(
        self,
        fnb_opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Every leaf node (including String/enum) has an EURange property child."""
        _server, client, ns = fnb_opcua_system
        errors: list[str] = []
        for node_path, _type_str, _writable in EXPECTED_FNB_NODES:
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
        fnb_opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """EURange Low/High match signal config min/max_clamp from factory-foodbev.yaml."""
        _server, client, ns = fnb_opcua_system
        # (node_path, expected_low, expected_high)
        checks: list[tuple[str, float, float]] = [
            ("FoodBevLine.Filler1.LineSpeed",    10.0,  120.0),
            ("FoodBevLine.Filler1.FillWeight",  200.0,  800.0),
            ("FoodBevLine.QC1.ActualWeight",    100.0, 1000.0),
            ("FoodBevLine.Energy.LinePower",      0.0,  300.0),
        ]
        errors: list[str] = []
        for node_path, expected_low, expected_high in checks:
            node = client.get_node(ua.NodeId(node_path, ns))
            children = await node.get_children()
            eu = None
            for child in children:
                bname = await child.read_browse_name()
                if bname.Name == "EURange":
                    eu = await child.read_value()
                    break
            assert eu is not None, f"{node_path}: EURange not found"
            if abs(eu.Low - expected_low) > 1e-4:
                errors.append(f"{node_path}: EURange.Low {eu.Low} != {expected_low}")
            if abs(eu.High - expected_high) > 1e-4:
                errors.append(f"{node_path}: EURange.High {eu.High} != {expected_high}")
        assert not errors, "EURange mismatches:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# Tests: AccessLevel (all F&B OPC-UA nodes are read-only)
# ---------------------------------------------------------------------------


class TestFnbAccessLevel:
    """Verify all 19 F&B OPC-UA nodes have AccessLevel 1 (read-only).

    F&B setpoints (oven zone setpoints, chiller setpoint) are accessed via
    Modbus only (Eurotherm controllers, Danfoss controller) and are not
    exposed as writable OPC-UA nodes.
    """

    async def test_all_fnb_nodes_read_only(
        self,
        fnb_opcua_system: tuple[OpcuaServer, Client, int],
    ) -> None:
        """Every F&B OPC-UA node has AccessLevel 1 (read-only)."""
        _server, client, ns = fnb_opcua_system
        errors: list[str] = []
        for node_path, _type_str, writable in EXPECTED_FNB_NODES:
            expected = 3 if writable else 1
            node = client.get_node(ua.NodeId(node_path, ns))
            result = await node.read_attribute(ua.AttributeIds.AccessLevel)
            actual = int(result.Value.Value)
            if actual != expected:
                errors.append(
                    f"{node_path}: expected AccessLevel {expected}, got {actual}"
                )
        assert not errors, "AccessLevel mismatches:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# Tests: OpcuaServer construction with F&B config (no server start needed)
# ---------------------------------------------------------------------------


class TestFnbServerConstruction:
    """Test OpcuaServer construction with F&B config — no server start required."""

    def test_nodes_empty_before_start(self) -> None:
        """server.nodes is empty before start()."""
        config = load_config(_FNB_CONFIG_PATH, apply_env=False)
        server = OpcuaServer(config, SignalStore())
        assert len(server.nodes) == 0

    def test_port_matches_config(self) -> None:
        """Server port matches config.protocols.opcua.port."""
        config = load_config(_FNB_CONFIG_PATH, apply_env=False)
        server = OpcuaServer(config, SignalStore())
        assert server.port == config.protocols.opcua.port

    def test_variant_type_map_includes_string(self) -> None:
        """String OPC-UA type is supported (required for BatchId node)."""
        assert "String" in _VARIANT_TYPE_MAP

    async def test_nodes_populated_after_start(self) -> None:
        """server.nodes has exactly 19 nodes after start()."""
        config = load_config(_FNB_CONFIG_PATH, apply_env=False)
        server = OpcuaServer(config, SignalStore(), host=_HOST, port=0)

        assert len(server.nodes) == 0
        await server.start()
        assert len(server.nodes) == len(EXPECTED_FNB_NODES)
        await server.stop()


# ---------------------------------------------------------------------------
# Tests: MQTT topic map for F&B profile (Appendix C)
# ---------------------------------------------------------------------------


class TestFnbMqttTopicMap:
    """Verify F&B MQTT topic map matches PRD Appendix C (13 topics, no vibration)."""

    def test_exactly_13_topics(self, fnb_config: object) -> None:
        """F&B profile produces exactly 13 MQTT topics per Appendix C."""
        entries = build_topic_map(fnb_config)  # type: ignore[arg-type]
        assert len(entries) == 13, (
            f"Expected 13 topics, got {len(entries)}: "
            f"{[e.topic for e in entries]}"
        )

    def test_no_vibration_topics(self, fnb_config: object) -> None:
        """F&B profile must not include vibration/* topics (Appendix C note)."""
        entries = build_topic_map(fnb_config)  # type: ignore[arg-type]
        vib_topics = [e.topic for e in entries if "vibration/" in e.topic]
        assert not vib_topics, (
            f"Vibration topics found in F&B profile: {vib_topics}"
        )

    def test_all_topics_use_foodbev1_prefix(self, fnb_config: object) -> None:
        """All F&B topics use 'foodbev1' line_id in the topic prefix (Appendix C)."""
        from factory_simulator.config import FactoryConfig

        cfg = fnb_config  # type: ignore[assignment]
        assert isinstance(cfg, FactoryConfig)
        entries = build_topic_map(cfg)
        expected_prefix = (
            f"{cfg.protocols.mqtt.topic_prefix}/"
            f"{cfg.factory.site_id}/"
            f"{cfg.protocols.mqtt.line_id}/"
        )
        for entry in entries:
            assert entry.topic.startswith(expected_prefix), (
                f"Topic {entry.topic!r} missing prefix {expected_prefix!r}"
            )

    def test_coder_state_topic_path(self, fnb_config: object) -> None:
        """coder.state maps to correct F&B topic path (foodbev1 prefix)."""
        entries = build_topic_map(fnb_config)  # type: ignore[arg-type]
        by_sig = {e.signal_id: e for e in entries}
        entry = by_sig["coder.state"]
        assert entry.topic == "collatr/factory/demo/foodbev1/coder/state"

    def test_env_ambient_temp_topic_path(self, fnb_config: object) -> None:
        """environment.ambient_temp maps to env/ sub-path (not environment/)."""
        entries = build_topic_map(fnb_config)  # type: ignore[arg-type]
        by_sig = {e.signal_id: e for e in entries}
        entry = by_sig["environment.ambient_temp"]
        assert entry.topic == "collatr/factory/demo/foodbev1/env/ambient_temp"

    def test_env_ambient_humidity_topic_path(self, fnb_config: object) -> None:
        """environment.ambient_humidity maps to env/ sub-path."""
        entries = build_topic_map(fnb_config)  # type: ignore[arg-type]
        by_sig = {e.signal_id: e for e in entries}
        entry = by_sig["environment.ambient_humidity"]
        assert entry.topic == "collatr/factory/demo/foodbev1/env/ambient_humidity"

    def test_11_coder_topics_present(self, fnb_config: object) -> None:
        """F&B profile has all 11 coder topics."""
        entries = build_topic_map(fnb_config)  # type: ignore[arg-type]
        coder_topics = [e for e in entries if "/coder/" in e.topic]
        assert len(coder_topics) == 11, (
            f"Expected 11 coder topics, got {len(coder_topics)}"
        )

    def test_2_env_topics_present(self, fnb_config: object) -> None:
        """F&B profile has exactly 2 env topics."""
        entries = build_topic_map(fnb_config)  # type: ignore[arg-type]
        env_topics = [e for e in entries if "/env/" in e.topic]
        assert len(env_topics) == 2, (
            f"Expected 2 env topics, got {len(env_topics)}"
        )

    def test_qos1_for_critical_coder_topics(self, fnb_config: object) -> None:
        """coder/state, prints_total, nozzle_health, gutter_fault are QoS 1."""
        entries = build_topic_map(fnb_config)  # type: ignore[arg-type]
        by_sig = {e.signal_id: e for e in entries}
        for sig in [
            "coder.state",
            "coder.prints_total",
            "coder.nozzle_health",
            "coder.gutter_fault",
        ]:
            assert by_sig[sig].qos == 1, f"{sig} should be QoS 1"

    def test_qos0_for_analog_and_env_topics(self, fnb_config: object) -> None:
        """Analog coder and env topics are QoS 0."""
        entries = build_topic_map(fnb_config)  # type: ignore[arg-type]
        by_sig = {e.signal_id: e for e in entries}
        for sig in [
            "coder.ink_level",
            "coder.printhead_temp",
            "coder.ink_pump_speed",
            "coder.ink_pressure",
            "coder.ink_viscosity_actual",
            "coder.supply_voltage",
            "coder.ink_consumption_ml",
            "environment.ambient_temp",
            "environment.ambient_humidity",
        ]:
            assert by_sig[sig].qos == 0, f"{sig} should be QoS 0"

    def test_all_13_topics_are_retained(self, fnb_config: object) -> None:
        """All 13 F&B topics have retain=True (no vibration, all topics retained)."""
        entries = build_topic_map(fnb_config)  # type: ignore[arg-type]
        for entry in entries:
            assert entry.retain is True, (
                f"Topic {entry.topic} should have retain=True"
            )

    def test_event_driven_topics_have_zero_interval(self, fnb_config: object) -> None:
        """State and fault coder topics are event-driven (interval_s == 0.0)."""
        entries = build_topic_map(fnb_config)  # type: ignore[arg-type]
        by_sig = {e.signal_id: e for e in entries}
        for sig in [
            "coder.state",
            "coder.prints_total",
            "coder.nozzle_health",
            "coder.gutter_fault",
        ]:
            assert by_sig[sig].interval_s == 0.0, f"{sig} should be event-driven"

    def test_timed_topics_have_correct_intervals(self, fnb_config: object) -> None:
        """Timed topics use sample_rate_ms from config converted to seconds."""
        entries = build_topic_map(fnb_config)  # type: ignore[arg-type]
        by_sig = {e.signal_id: e for e in entries}
        assert by_sig["coder.ink_level"].interval_s == pytest.approx(60.0)
        assert by_sig["coder.printhead_temp"].interval_s == pytest.approx(30.0)
        assert by_sig["coder.ink_pump_speed"].interval_s == pytest.approx(5.0)
        assert by_sig["coder.ink_pressure"].interval_s == pytest.approx(5.0)
        assert by_sig["coder.ink_viscosity_actual"].interval_s == pytest.approx(30.0)
        assert by_sig["environment.ambient_temp"].interval_s == pytest.approx(60.0)
        assert by_sig["environment.ambient_humidity"].interval_s == pytest.approx(60.0)

    def test_no_batch_vibration_entry(self, fnb_config: object) -> None:
        """F&B profile has no batch vibration entry (no vibration equipment)."""
        entry = build_batch_vibration_entry(fnb_config)  # type: ignore[arg-type]
        assert entry is None, (
            f"Expected None batch vibration entry for F&B, got {entry}"
        )

    def test_no_fnb_signals_without_mqtt_topic_in_map(self, fnb_config: object) -> None:
        """F&B non-MQTT signals (Modbus-only) must not appear in topic map."""
        entries = build_topic_map(fnb_config)  # type: ignore[arg-type]
        signal_ids = {e.signal_id for e in entries}
        # These F&B signals have no mqtt_topic and must not be in the map
        assert "mixer.speed" not in signal_ids
        assert "oven.zone_1_temp" not in signal_ids
        assert "filler.hopper_level" not in signal_ids
        assert "chiller.room_temp" not in signal_ids
        assert "energy.line_power" not in signal_ids
