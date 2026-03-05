"""Tests for task 5.3: Multi-Port OPC-UA Servers and Clock Drift.

Covers:
- ClockDriftModel formula correctness
- OPC-UA server node tree filtering by endpoint subtree root
- Multi-port OPC-UA servers (collapsed vs realistic mode)
- Clock drift in OPC-UA SourceTimestamp
- Clock drift in MQTT JSON timestamps
- Ground truth uses true sim_time (not drifted)
- DataEngine.create_opcua_servers() for both modes

PRD Reference: Section 3a.2, 3a.3, 3a.5
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from asyncua import Client, ua

from factory_simulator.config import (
    ClockDriftConfig,
    NetworkConfig,
    load_config,
)
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.protocols.mqtt_publisher import MqttPublisher
from factory_simulator.protocols.opcua_server import (
    NAMESPACE_INDEX,
    OpcuaServer,
    _sim_time_to_datetime,
)
from factory_simulator.store import SignalStore
from factory_simulator.topology import (
    ClockDriftModel,
    NetworkTopologyManager,
    OpcuaEndpointSpec,
)

# Config paths
_PKG_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory.yaml"
_FNB_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "factory-foodbev.yaml"
)
_HOST = "127.0.0.1"

# Reference epoch matching mqtt_publisher and opcua_server
_REFERENCE_EPOCH_TS: float = datetime(2026, 1, 1, tzinfo=UTC).timestamp()


# ---------------------------------------------------------------------------
# ClockDriftModel unit tests
# ---------------------------------------------------------------------------


class TestClockDriftModel:
    """Test ClockDriftModel formula correctness."""

    def test_zero_drift_returns_sim_time(self) -> None:
        """With zero offset and zero drift rate, drifted_time == sim_time."""
        model = ClockDriftModel(ClockDriftConfig())
        assert model.drifted_time(100.0) == 100.0

    def test_initial_offset_only(self) -> None:
        """With only initial offset, drift is constant."""
        model = ClockDriftModel(
            ClockDriftConfig(initial_offset_ms=5000.0, drift_rate_s_per_day=0.0)
        )
        # 5000 ms = 5.0 s offset
        assert model.drifted_time(0.0) == pytest.approx(5.0)
        assert model.drifted_time(100.0) == pytest.approx(105.0)

    def test_drift_rate_only(self) -> None:
        """With only drift rate, offset grows linearly with time."""
        model = ClockDriftModel(
            ClockDriftConfig(initial_offset_ms=0.0, drift_rate_s_per_day=24.0)
        )
        # At sim_time=3600s (1 hour): drift = 24.0 * 1.0 / 24.0 = 1.0 s
        assert model.drifted_time(3600.0) == pytest.approx(3601.0)
        # At sim_time=86400s (24 hours): drift = 24.0 * 24.0 / 24.0 = 24.0 s
        assert model.drifted_time(86400.0) == pytest.approx(86424.0)

    def test_combined_offset_and_drift(self) -> None:
        """Initial offset + drift rate both contribute."""
        model = ClockDriftModel(
            ClockDriftConfig(initial_offset_ms=5000.0, drift_rate_s_per_day=5.0)
        )
        # At 24h: drifted = 86400 + 5.0 + 5.0*24/24 = 86400 + 5.0 + 5.0 = 86410.0
        assert model.drifted_time(86400.0) == pytest.approx(86410.0)

    def test_eurotherm_drift_24h(self) -> None:
        """Eurotherm default (5000ms initial, 5 s/day) visible after 24h."""
        model = ClockDriftModel(
            ClockDriftConfig(initial_offset_ms=5000.0, drift_rate_s_per_day=5.0)
        )
        offset_at_24h = model.drift_offset(86400.0)
        # Initial: 5.0s + drift: 5.0 * 24/24 = 5.0s = total 10.0s
        assert offset_at_24h == pytest.approx(10.0)
        # This is clearly visible (PRD requirement)
        assert offset_at_24h > 1.0

    def test_s7_1500_drift_24h(self) -> None:
        """S7-1500 (200ms initial, 0.3 s/day) small but nonzero after 24h."""
        model = ClockDriftModel(
            ClockDriftConfig(initial_offset_ms=200.0, drift_rate_s_per_day=0.3)
        )
        offset_at_24h = model.drift_offset(86400.0)
        # Initial: 0.2s + drift: 0.3 * 24/24 = 0.3s = total 0.5s
        assert offset_at_24h == pytest.approx(0.5)

    def test_drift_offset_zero_at_start(self) -> None:
        """At sim_time=0, drift_offset is just the initial offset."""
        model = ClockDriftModel(
            ClockDriftConfig(initial_offset_ms=1000.0, drift_rate_s_per_day=10.0)
        )
        assert model.drift_offset(0.0) == pytest.approx(1.0)

    def test_properties(self) -> None:
        model = ClockDriftModel(
            ClockDriftConfig(initial_offset_ms=5000.0, drift_rate_s_per_day=5.0)
        )
        assert model.initial_offset_s == pytest.approx(5.0)
        assert model.drift_rate_s_per_day == pytest.approx(5.0)

    def test_drifted_time_always_ge_sim_time(self) -> None:
        """Drifted time is always >= sim_time (offset/drift are non-negative)."""
        model = ClockDriftModel(
            ClockDriftConfig(initial_offset_ms=100.0, drift_rate_s_per_day=1.0)
        )
        for t in [0.0, 100.0, 3600.0, 86400.0]:
            assert model.drifted_time(t) >= t


# ---------------------------------------------------------------------------
# _sim_time_to_datetime helper
# ---------------------------------------------------------------------------


class TestSimTimeToDatetime:
    def test_zero_returns_reference_epoch(self) -> None:
        dt = _sim_time_to_datetime(0.0)
        assert dt == datetime(2026, 1, 1, tzinfo=UTC)

    def test_one_hour(self) -> None:
        dt = _sim_time_to_datetime(3600.0)
        assert dt == datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# OPC-UA server: node tree filtering
# ---------------------------------------------------------------------------


@pytest.fixture
async def fnb_opcua_filler_server() -> (  # type: ignore[override]
    tuple[OpcuaServer, Client, int]
):
    """Start a filler-only OPC-UA server (realistic mode F&B: port 4841)."""
    config = load_config(str(_FNB_CONFIG_PATH), apply_env=False)
    store = SignalStore()
    endpoint = OpcuaEndpointSpec(
        port=0,  # OS-assigned
        node_tree_root="FoodBevLine.Filler1",
        controller_type="S7-1200",
        controller_name="filler_plc",
    )
    server = OpcuaServer(
        config, store, host=_HOST, port=0, endpoint=endpoint,
        comm_drop_rng=np.random.default_rng(42),
    )
    await server.start()

    port = server.actual_port
    assert port > 0

    client = Client(f"opc.tcp://{_HOST}:{port}/")
    await client.connect()

    yield server, client, NAMESPACE_INDEX

    await client.disconnect()
    await server.stop()


@pytest.fixture
async def fnb_opcua_qc_server() -> (  # type: ignore[override]
    tuple[OpcuaServer, Client, int]
):
    """Start a QC-only OPC-UA server (realistic mode F&B: port 4842)."""
    config = load_config(str(_FNB_CONFIG_PATH), apply_env=False)
    store = SignalStore()
    endpoint = OpcuaEndpointSpec(
        port=0,
        node_tree_root="FoodBevLine.QC1",
        controller_type="S7-1200",
        controller_name="qc_station",
    )
    server = OpcuaServer(
        config, store, host=_HOST, port=0, endpoint=endpoint,
        comm_drop_rng=np.random.default_rng(42),
    )
    await server.start()

    port = server.actual_port
    assert port > 0

    client = Client(f"opc.tcp://{_HOST}:{port}/")
    await client.connect()

    yield server, client, NAMESPACE_INDEX

    await client.disconnect()
    await server.stop()


@pytest.fixture
async def pkg_opcua_full_server() -> (  # type: ignore[override]
    tuple[OpcuaServer, Client, int]
):
    """Start a full packaging OPC-UA server (collapsed or realistic — same tree)."""
    config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
    store = SignalStore()
    # No endpoint filter — full tree
    server = OpcuaServer(
        config, store, host=_HOST, port=0,
        comm_drop_rng=np.random.default_rng(42),
    )
    await server.start()

    port = server.actual_port
    assert port > 0

    client = Client(f"opc.tcp://{_HOST}:{port}/")
    await client.connect()

    yield server, client, NAMESPACE_INDEX

    await client.disconnect()
    await server.stop()


# Expected F&B filler nodes (7 nodes per PRD Appendix B)
_FILLER_NODES = [
    "FoodBevLine.Filler1.LineSpeed",
    "FoodBevLine.Filler1.FillWeight",
    "FoodBevLine.Filler1.FillTarget",
    "FoodBevLine.Filler1.FillDeviation",
    "FoodBevLine.Filler1.PacksProduced",
    "FoodBevLine.Filler1.RejectCount",
    "FoodBevLine.Filler1.State",
]

# Expected F&B QC nodes (6 nodes per PRD Appendix B)
_QC_NODES = [
    "FoodBevLine.QC1.ActualWeight",
    "FoodBevLine.QC1.OverweightCount",
    "FoodBevLine.QC1.UnderweightCount",
    "FoodBevLine.QC1.MetalDetectTrips",
    "FoodBevLine.QC1.Throughput",
    "FoodBevLine.QC1.RejectTotal",
]


@pytest.mark.asyncio
async def test_filler_server_node_count(
    fnb_opcua_filler_server: tuple[OpcuaServer, Client, int],
) -> None:
    """Filler-only server has exactly 7 variable nodes."""
    server, _client, _ns = fnb_opcua_filler_server
    assert len(server.nodes) == 7


@pytest.mark.asyncio
async def test_filler_server_has_expected_nodes(
    fnb_opcua_filler_server: tuple[OpcuaServer, Client, int],
) -> None:
    """All filler nodes are present."""
    server, _client, _ns = fnb_opcua_filler_server
    for node_path in _FILLER_NODES:
        assert node_path in server.nodes, f"Missing: {node_path}"


@pytest.mark.asyncio
async def test_filler_server_excludes_other_nodes(
    fnb_opcua_filler_server: tuple[OpcuaServer, Client, int],
) -> None:
    """Filler server does not contain QC, Mixer, or Oven nodes."""
    server, _client, _ns = fnb_opcua_filler_server
    for node_path in server.nodes:
        assert node_path.startswith("FoodBevLine.Filler1"), (
            f"Unexpected node: {node_path}"
        )


@pytest.mark.asyncio
async def test_filler_nodes_readable_via_client(
    fnb_opcua_filler_server: tuple[OpcuaServer, Client, int],
) -> None:
    """Filler nodes are readable via OPC-UA client."""
    _server, client, ns = fnb_opcua_filler_server
    node = client.get_node(
        ua.NodeId("FoodBevLine.Filler1.LineSpeed", ns)
    )
    val = await node.read_value()
    # Initial value is 0.0 (Double type)
    assert isinstance(val, float)


@pytest.mark.asyncio
async def test_qc_server_node_count(
    fnb_opcua_qc_server: tuple[OpcuaServer, Client, int],
) -> None:
    """QC-only server has exactly 6 variable nodes."""
    server, _client, _ns = fnb_opcua_qc_server
    assert len(server.nodes) == 6


@pytest.mark.asyncio
async def test_qc_server_has_expected_nodes(
    fnb_opcua_qc_server: tuple[OpcuaServer, Client, int],
) -> None:
    """All QC nodes are present."""
    server, _client, _ns = fnb_opcua_qc_server
    for node_path in _QC_NODES:
        assert node_path in server.nodes, f"Missing: {node_path}"


@pytest.mark.asyncio
async def test_qc_server_excludes_filler_nodes(
    fnb_opcua_qc_server: tuple[OpcuaServer, Client, int],
) -> None:
    """QC server does not contain Filler nodes."""
    server, _client, _ns = fnb_opcua_qc_server
    for node_path in server.nodes:
        assert node_path.startswith("FoodBevLine.QC1"), (
            f"Unexpected node: {node_path}"
        )


@pytest.mark.asyncio
async def test_packaging_full_tree_node_count(
    pkg_opcua_full_server: tuple[OpcuaServer, Client, int],
) -> None:
    """Full packaging tree has 32 variable nodes (PRD Appendix B)."""
    server, _client, _ns = pkg_opcua_full_server
    assert len(server.nodes) == 32


@pytest.mark.asyncio
async def test_no_endpoint_serves_full_tree(
    pkg_opcua_full_server: tuple[OpcuaServer, Client, int],
) -> None:
    """Without endpoint, all node prefixes are present."""
    server, _client, _ns = pkg_opcua_full_server
    prefixes = {p.split(".")[1] for p in server.nodes if "." in p}
    # PackagingLine has Press1, Laminator1, Slitter1, Energy
    assert "Press1" in prefixes
    assert "Laminator1" in prefixes
    assert "Slitter1" in prefixes
    assert "Energy" in prefixes


# ---------------------------------------------------------------------------
# OPC-UA server: clock drift in SourceTimestamp
# ---------------------------------------------------------------------------


@pytest.fixture
async def opcua_with_drift() -> (  # type: ignore[override]
    tuple[OpcuaServer, Client, int, SignalStore, ClockDriftModel]
):
    """Start packaging OPC-UA server with Eurotherm-level clock drift."""
    config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
    store = SignalStore()
    drift_config = ClockDriftConfig(
        initial_offset_ms=5000.0, drift_rate_s_per_day=5.0,
    )
    drift = ClockDriftModel(drift_config)
    server = OpcuaServer(
        config, store, host=_HOST, port=0,
        clock_drift=drift,
        comm_drop_rng=np.random.default_rng(42),
    )
    await server.start()

    port = server.actual_port
    assert port > 0

    client = Client(f"opc.tcp://{_HOST}:{port}/")
    await client.connect()

    yield server, client, NAMESPACE_INDEX, store, drift

    await client.disconnect()
    await server.stop()


@pytest.mark.asyncio
async def test_source_timestamp_has_drift(
    opcua_with_drift: tuple[OpcuaServer, Client, int, SignalStore, ClockDriftModel],
) -> None:
    """SourceTimestamp includes clock drift offset."""
    _server, client, ns, store, drift = opcua_with_drift

    # Seed a signal value at sim_time = 3600.0 (1 hour)
    sim_time = 3600.0
    store.set("press.line_speed", 150.0, sim_time, "good")

    # Wait for a sync cycle
    import asyncio
    await asyncio.sleep(0.7)

    # Read DataValue
    node = client.get_node(
        ua.NodeId("PackagingLine.Press1.LineSpeed", ns)
    )
    dv = await node.read_data_value()

    assert dv.SourceTimestamp is not None
    # Expected: reference_epoch + drifted_time(3600.0)
    # drifted_time = 3600 + 5.0 + 5.0 * 1.0 / 24.0
    expected_drifted = drift.drifted_time(sim_time)
    expected_dt = _sim_time_to_datetime(expected_drifted)

    # Allow 1 second tolerance for timing (sync cycle granularity)
    actual_ts = dv.SourceTimestamp.replace(tzinfo=UTC)
    delta = abs((actual_ts - expected_dt).total_seconds())
    assert delta < 1.0, (
        f"SourceTimestamp {actual_ts} differs from expected {expected_dt} "
        f"by {delta:.3f}s"
    )


@pytest.mark.asyncio
async def test_no_drift_no_source_timestamp(
    pkg_opcua_full_server: tuple[OpcuaServer, Client, int],
) -> None:
    """Without clock drift, SourceTimestamp is server-default (not set by us)."""
    # This test just verifies no crash when clock_drift is None.
    _server, _client, _ns = pkg_opcua_full_server
    # Server started successfully without drift — that's the test.


# ---------------------------------------------------------------------------
# MQTT: clock drift in JSON timestamps
# ---------------------------------------------------------------------------


class TestMqttClockDrift:
    """Test MQTT publisher clock drift integration."""

    def test_publish_with_drift(self) -> None:
        """MQTT payload timestamp includes clock drift offset."""
        config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
        store = SignalStore()

        drift = ClockDriftModel(
            ClockDriftConfig(initial_offset_ms=5000.0, drift_rate_s_per_day=0.0)
        )

        mock_client = MagicMock()
        # Disable connection attempts
        mock_client.connect = MagicMock()
        mock_client.loop_start = MagicMock()
        mock_client.loop_stop = MagicMock()
        mock_client.disconnect = MagicMock()

        publisher = MqttPublisher(
            config, store,
            client=mock_client,
            clock_drift=drift,
            comm_drop_rng=np.random.default_rng(42),
        )

        # Find a timed topic entry
        entry = None
        for te in publisher.topic_entries:
            if te.interval_s > 0:
                entry = te
                break
        assert entry is not None

        # Seed the signal at sim_time = 100.0
        store.set(entry.signal_id, 42.0, 100.0, "good")

        # Publish via the entry
        sv = store.get(entry.signal_id)
        assert sv is not None
        publisher._publish_entry(entry, sv)

        # Check what was published
        assert mock_client.publish.called
        call_kwargs = mock_client.publish.call_args
        payload_bytes = call_kwargs[1].get("payload") or call_kwargs[0][1]
        payload = json.loads(payload_bytes)

        # Expected timestamp: sim_time 100 + 5.0s drift = 105.0 from reference
        # ISO: 2026-01-01T00:01:45.000Z
        expected_iso = "2026-01-01T00:01:45.000Z"
        assert payload["timestamp"] == expected_iso

    def test_no_drift_no_offset(self) -> None:
        """Without drift, MQTT timestamp matches sim_time exactly."""
        config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
        store = SignalStore()

        mock_client = MagicMock()
        mock_client.connect = MagicMock()
        mock_client.loop_start = MagicMock()
        mock_client.loop_stop = MagicMock()
        mock_client.disconnect = MagicMock()

        publisher = MqttPublisher(
            config, store,
            client=mock_client,
            comm_drop_rng=np.random.default_rng(42),
        )

        entry = None
        for te in publisher.topic_entries:
            if te.interval_s > 0:
                entry = te
                break
        assert entry is not None

        store.set(entry.signal_id, 42.0, 100.0, "good")
        sv = store.get(entry.signal_id)
        assert sv is not None
        publisher._publish_entry(entry, sv)

        call_kwargs = mock_client.publish.call_args
        payload_bytes = call_kwargs[1].get("payload") or call_kwargs[0][1]
        payload = json.loads(payload_bytes)

        # No drift: sim_time=100 → 2026-01-01T00:01:40.000Z
        expected_iso = "2026-01-01T00:01:40.000Z"
        assert payload["timestamp"] == expected_iso


# ---------------------------------------------------------------------------
# Ground truth: always uses true sim_time
# ---------------------------------------------------------------------------


class TestGroundTruthNoDrift:
    """Ground truth logger must not be affected by clock drift."""

    def test_ground_truth_format_time_ignores_drift(self) -> None:
        """Ground truth _format_time uses sim_time directly, no drift applied.

        This is verified by construction: GroundTruthLogger does not accept
        a ClockDriftModel, so it cannot drift timestamps. This test documents
        the design invariant.
        """
        import inspect

        from factory_simulator.engine.ground_truth import GroundTruthLogger

        # GroundTruthLogger.__init__ signature does not accept clock_drift
        sig = inspect.signature(GroundTruthLogger.__init__)
        params = list(sig.parameters.keys())
        assert "clock_drift" not in params


# ---------------------------------------------------------------------------
# DataEngine.create_opcua_servers()
# ---------------------------------------------------------------------------


class TestDataEngineOpcuaCreation:
    """Test DataEngine.create_opcua_servers() for collapsed and realistic modes."""

    def test_collapsed_mode_single_server(self) -> None:
        """Collapsed mode creates a single OPC-UA server."""
        config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        engine = DataEngine(config, store)
        servers = engine.create_opcua_servers()
        assert len(servers) == 1

    def test_collapsed_mode_no_topology(self) -> None:
        """No topology (None) creates a single OPC-UA server."""
        config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        engine = DataEngine(config, store, topology=None)
        servers = engine.create_opcua_servers()
        assert len(servers) == 1

    def test_realistic_packaging_single_opcua(self) -> None:
        """Realistic packaging: 1 OPC-UA server on port 4840."""
        config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        topology = NetworkTopologyManager(
            config=NetworkConfig(mode="realistic"), profile="packaging"
        )
        engine = DataEngine(config, store, topology=topology)
        servers = engine.create_opcua_servers()
        assert len(servers) == 1
        assert servers[0]._port == 4840

    def test_realistic_foodbev_two_opcua(self) -> None:
        """Realistic F&B: 2 OPC-UA servers on ports 4841 and 4842."""
        config = load_config(str(_FNB_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        topology = NetworkTopologyManager(
            config=NetworkConfig(mode="realistic"), profile="food_bev"
        )
        engine = DataEngine(config, store, topology=topology)
        servers = engine.create_opcua_servers()
        assert len(servers) == 2
        ports = {s._port for s in servers}
        assert ports == {4841, 4842}

    def test_realistic_servers_have_clock_drift(self) -> None:
        """Realistic mode servers have clock drift models."""
        config = load_config(str(_FNB_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        topology = NetworkTopologyManager(
            config=NetworkConfig(mode="realistic"), profile="food_bev"
        )
        engine = DataEngine(config, store, topology=topology)
        servers = engine.create_opcua_servers()
        for server in servers:
            assert server._clock_drift is not None

    def test_realistic_filler_server_has_subtree_root(self) -> None:
        """Realistic filler server filters to FoodBevLine.Filler1 subtree."""
        config = load_config(str(_FNB_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        topology = NetworkTopologyManager(
            config=NetworkConfig(mode="realistic"), profile="food_bev"
        )
        engine = DataEngine(config, store, topology=topology)
        servers = engine.create_opcua_servers()
        filler_server = next(s for s in servers if s._port == 4841)
        assert filler_server._node_tree_root == "FoodBevLine.Filler1"


# ---------------------------------------------------------------------------
# OPC-UA construction with endpoint
# ---------------------------------------------------------------------------


class TestOpcuaServerConstruction:
    """Test OpcuaServer construction with endpoint parameter."""

    def test_endpoint_sets_port(self) -> None:
        """Endpoint port overrides config port."""
        config = load_config(str(_FNB_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        endpoint = OpcuaEndpointSpec(port=4841)
        server = OpcuaServer(
            config, store, endpoint=endpoint,
            comm_drop_rng=np.random.default_rng(42),
        )
        assert server.port == 4841

    def test_explicit_port_overrides_endpoint(self) -> None:
        """Explicit port=0 overrides endpoint port."""
        config = load_config(str(_FNB_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        endpoint = OpcuaEndpointSpec(port=4841)
        server = OpcuaServer(
            config, store, port=0, endpoint=endpoint,
            comm_drop_rng=np.random.default_rng(42),
        )
        assert server.port == 0

    def test_no_endpoint_uses_config_port(self) -> None:
        """Without endpoint, port comes from config."""
        config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        server = OpcuaServer(
            config, store,
            comm_drop_rng=np.random.default_rng(42),
        )
        assert server.port == config.protocols.opcua.port

    def test_node_tree_root_from_endpoint(self) -> None:
        """node_tree_root is set from endpoint."""
        config = load_config(str(_FNB_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        endpoint = OpcuaEndpointSpec(
            port=4841, node_tree_root="FoodBevLine.Filler1"
        )
        server = OpcuaServer(
            config, store, endpoint=endpoint,
            comm_drop_rng=np.random.default_rng(42),
        )
        assert server._node_tree_root == "FoodBevLine.Filler1"

    def test_no_endpoint_empty_tree_root(self) -> None:
        """Without endpoint, node_tree_root is empty (serve all nodes)."""
        config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        server = OpcuaServer(
            config, store,
            comm_drop_rng=np.random.default_rng(42),
        )
        assert server._node_tree_root == ""

    def test_clock_drift_stored(self) -> None:
        """Clock drift model is stored on server."""
        config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        drift = ClockDriftModel(
            ClockDriftConfig(initial_offset_ms=1000.0, drift_rate_s_per_day=1.0)
        )
        server = OpcuaServer(
            config, store, clock_drift=drift,
            comm_drop_rng=np.random.default_rng(42),
        )
        assert server._clock_drift is drift

    def test_no_clock_drift_default(self) -> None:
        """Without clock_drift, _clock_drift is None."""
        config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        server = OpcuaServer(
            config, store,
            comm_drop_rng=np.random.default_rng(42),
        )
        assert server._clock_drift is None


# ---------------------------------------------------------------------------
# DataEngine.create_mqtt_publishers()
# ---------------------------------------------------------------------------


class TestDataEngineMqttPublisherCreation:
    """Test DataEngine.create_mqtt_publishers() for collapsed and realistic modes."""

    def test_collapsed_mode_single_publisher_no_drift(self) -> None:
        """Collapsed mode creates a single publisher with no clock drift."""
        config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        engine = DataEngine(config, store)
        publishers = engine.create_mqtt_publishers()
        assert len(publishers) == 1
        assert publishers[0]._clock_drift is None

    def test_no_topology_single_publisher_no_drift(self) -> None:
        """No topology (None) creates a single publisher with no drift."""
        config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        engine = DataEngine(config, store, topology=None)
        publishers = engine.create_mqtt_publishers()
        assert len(publishers) == 1
        assert publishers[0]._clock_drift is None

    def test_realistic_mode_single_publisher_with_drift(self) -> None:
        """Realistic mode creates a publisher with non-None clock drift."""
        config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        topology = NetworkTopologyManager(
            config=NetworkConfig(mode="realistic"), profile="packaging"
        )
        engine = DataEngine(config, store, topology=topology)
        publishers = engine.create_mqtt_publishers()
        assert len(publishers) == 1
        assert publishers[0]._clock_drift is not None

    def test_realistic_drift_values_from_topology(self) -> None:
        """Realistic publisher drift matches topology MQTT endpoint config."""
        config = load_config(str(_PKG_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        topology = NetworkTopologyManager(
            config=NetworkConfig(mode="realistic"), profile="packaging"
        )
        engine = DataEngine(config, store, topology=topology)
        publishers = engine.create_mqtt_publishers()
        drift = publishers[0]._clock_drift
        assert drift is not None
        ep = topology.mqtt_endpoint()
        assert drift.initial_offset_s == ep.clock_drift.initial_offset_ms / 1000.0
        assert drift.drift_rate_s_per_day == ep.clock_drift.drift_rate_s_per_day

    def test_realistic_foodbev_single_publisher_with_drift(self) -> None:
        """Realistic F&B mode creates a single publisher with drift."""
        config = load_config(str(_FNB_CONFIG_PATH), apply_env=False)
        store = SignalStore()
        topology = NetworkTopologyManager(
            config=NetworkConfig(mode="realistic"), profile="food_bev"
        )
        engine = DataEngine(config, store, topology=topology)
        publishers = engine.create_mqtt_publishers()
        assert len(publishers) == 1
        assert publishers[0]._clock_drift is not None
