"""Tests for OPC-UA inactive profile node behaviour (PRD 3.2.1).

In collapsed mode, OpcuaServer creates nodes for the inactive profile
with AccessLevel=0 and StatusCode.BadNotReadable.  These nodes are
browseable but not readable, and the sync loop never updates them.

PRD Reference: Section 3.2.1
"""

from __future__ import annotations

from pathlib import Path

import pytest
from asyncua import Client, ua
from asyncua.ua.uaerrors import BadNodeIdUnknown

from factory_simulator.config import load_config
from factory_simulator.protocols.opcua_server import NAMESPACE_INDEX, OpcuaServer
from factory_simulator.store import SignalStore

_PACKAGING_CONFIG = Path(__file__).resolve().parents[3] / "config" / "factory.yaml"
_FOODBEV_CONFIG = Path(__file__).resolve().parents[3] / "config" / "factory-foodbev.yaml"
_HOST = "127.0.0.1"

# A node known to be in the F&B profile (inactive when packaging is active)
_INACTIVE_NODE = "FoodBevLine.Mixer1.State"

# A node known to be in the packaging profile (active)
_ACTIVE_NODE = "PackagingLine.Press1.LineSpeed"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def system_with_inactive(  # type: ignore[override]
) -> tuple[OpcuaServer, Client]:
    """Packaging config active, F&B config as inactive_config."""
    packaging_cfg = load_config(_PACKAGING_CONFIG, apply_env=False)
    foodbev_cfg = load_config(_FOODBEV_CONFIG, apply_env=False)
    store = SignalStore()
    server = OpcuaServer(
        packaging_cfg, store,
        host=_HOST, port=0,
        inactive_config=foodbev_cfg,
    )
    await server.start()
    port = server.actual_port
    assert port > 0

    client = Client(f"opc.tcp://{_HOST}:{port}/")
    await client.connect()

    yield server, client

    await client.disconnect()
    await server.stop()


@pytest.fixture
async def system_no_inactive(  # type: ignore[override]
) -> tuple[OpcuaServer, Client]:
    """Packaging config active, no inactive_config."""
    packaging_cfg = load_config(_PACKAGING_CONFIG, apply_env=False)
    store = SignalStore()
    server = OpcuaServer(packaging_cfg, store, host=_HOST, port=0)
    await server.start()
    port = server.actual_port
    assert port > 0

    client = Client(f"opc.tcp://{_HOST}:{port}/")
    await client.connect()

    yield server, client

    await client.disconnect()
    await server.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_id(path: str) -> ua.NodeId:
    return ua.NodeId(path, NAMESPACE_INDEX)


async def _read_access_level(client: Client, path: str) -> int:
    node = client.get_node(_node_id(path))
    result = await node.read_attribute(ua.AttributeIds.AccessLevel)
    return int(result.Value.Value)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInactiveNodesExist:
    """Inactive nodes are present in the address space."""

    @pytest.mark.asyncio
    async def test_inactive_node_exists(
        self, system_with_inactive: tuple[OpcuaServer, Client]
    ) -> None:
        """An F&B node exists when packaging is active with F&B as inactive."""
        _server, client = system_with_inactive
        node = client.get_node(_node_id(_INACTIVE_NODE))
        # read_browse_name raises if node does not exist
        bname = await node.read_browse_name()
        assert bname.Name == "State"

    @pytest.mark.asyncio
    async def test_active_node_still_exists(
        self, system_with_inactive: tuple[OpcuaServer, Client]
    ) -> None:
        """Active packaging nodes are present alongside inactive F&B nodes."""
        _server, client = system_with_inactive
        node = client.get_node(_node_id(_ACTIVE_NODE))
        bname = await node.read_browse_name()
        assert bname.Name == "LineSpeed"


class TestInactiveAccessLevel:
    """Inactive nodes have AccessLevel=0."""

    @pytest.mark.asyncio
    async def test_inactive_access_level_zero(
        self, system_with_inactive: tuple[OpcuaServer, Client]
    ) -> None:
        level = await _read_access_level(client=system_with_inactive[1], path=_INACTIVE_NODE)
        assert level == 0

    @pytest.mark.asyncio
    async def test_active_access_level_nonzero(
        self, system_with_inactive: tuple[OpcuaServer, Client]
    ) -> None:
        """Active nodes have AccessLevel >= 1 (readable)."""
        level = await _read_access_level(client=system_with_inactive[1], path=_ACTIVE_NODE)
        assert level >= 1


class TestInactiveStatusCode:
    """Inactive nodes have BadNotReadable status."""

    @pytest.mark.asyncio
    async def test_inactive_status_bad(
        self, system_with_inactive: tuple[OpcuaServer, Client]
    ) -> None:
        node = client = system_with_inactive[1]
        node = client.get_node(_node_id(_INACTIVE_NODE))
        dv = await node.read_data_value(raise_on_bad_status=False)
        # StatusCode should be bad (not Good = 0)
        assert dv.StatusCode_ is not None
        assert not dv.StatusCode_.is_good()

    @pytest.mark.asyncio
    async def test_inactive_status_is_bad_not_readable(
        self, system_with_inactive: tuple[OpcuaServer, Client]
    ) -> None:
        client = system_with_inactive[1]
        node = client.get_node(_node_id(_INACTIVE_NODE))
        dv = await node.read_data_value(raise_on_bad_status=False)
        assert dv.StatusCode_ is not None
        assert dv.StatusCode_.value == ua.StatusCodes.BadNotReadable


class TestInactiveNotSynced:
    """Inactive nodes are not updated by the sync loop."""

    @pytest.mark.asyncio
    async def test_inactive_nodes_not_in_sync_dict(
        self, system_with_inactive: tuple[OpcuaServer, Client]
    ) -> None:
        """Inactive nodes are NOT in server._nodes (so sync never touches them)."""
        server, _client = system_with_inactive
        assert _INACTIVE_NODE not in server.nodes
        assert _INACTIVE_NODE not in server.node_to_signal

    @pytest.mark.asyncio
    async def test_active_node_in_sync_dict(
        self, system_with_inactive: tuple[OpcuaServer, Client]
    ) -> None:
        """Active nodes ARE in server._nodes."""
        server, _client = system_with_inactive
        assert _ACTIVE_NODE in server.nodes


class TestNoInactiveWhenNone:
    """When inactive_config=None, no inactive nodes are created."""

    @pytest.mark.asyncio
    async def test_no_foodbev_nodes_without_inactive_config(
        self, system_no_inactive: tuple[OpcuaServer, Client]
    ) -> None:
        """Without inactive_config, F&B nodes do not exist."""
        _server, client = system_no_inactive
        node = client.get_node(_node_id(_INACTIVE_NODE))
        with pytest.raises(BadNodeIdUnknown):
            await node.read_browse_name()


class TestRealisticModeNoInactive:
    """Realistic mode (endpoint set) skips inactive node creation."""

    @pytest.mark.asyncio
    async def test_realistic_mode_skips_inactive(self) -> None:
        """With endpoint set, inactive nodes are not built even if inactive_config given."""
        from factory_simulator.topology import OpcuaEndpointSpec

        packaging_cfg = load_config(_PACKAGING_CONFIG, apply_env=False)
        foodbev_cfg = load_config(_FOODBEV_CONFIG, apply_env=False)
        store = SignalStore()

        # Create a minimal endpoint spec that scopes to PackagingLine.Press1
        endpoint = OpcuaEndpointSpec(
            node_tree_root="PackagingLine.Press1",
            port=0,
        )
        server = OpcuaServer(
            packaging_cfg, store,
            host=_HOST, port=0,
            endpoint=endpoint,
            inactive_config=foodbev_cfg,
        )
        await server.start()
        port = server.actual_port
        assert port > 0

        client = Client(f"opc.tcp://{_HOST}:{port}/")
        await client.connect()
        try:
            # Inactive nodes should NOT exist in realistic mode
            node = client.get_node(_node_id(_INACTIVE_NODE))
            with pytest.raises(BadNodeIdUnknown):
                await node.read_browse_name()
        finally:
            await client.disconnect()
            await server.stop()


class TestBothConfigsDifferent:
    """Loading both YAML configs produces different node subtrees."""

    def test_packaging_and_foodbev_have_different_roots(self) -> None:
        packaging_cfg = load_config(_PACKAGING_CONFIG, apply_env=False)
        foodbev_cfg = load_config(_FOODBEV_CONFIG, apply_env=False)

        def _opcua_roots(cfg: object) -> set[str]:
            roots: set[str] = set()
            for eq in cfg.equipment.values():  # type: ignore[union-attr]
                if not eq.enabled:
                    continue
                for sig in eq.signals.values():
                    if sig.opcua_node:
                        root = sig.opcua_node.split(".")[0]
                        roots.add(root)
            return roots

        pkg_roots = _opcua_roots(packaging_cfg)
        fnb_roots = _opcua_roots(foodbev_cfg)
        # Must be disjoint — different profile, different subtree roots
        assert pkg_roots.isdisjoint(fnb_roots), (
            f"Profiles share OPC-UA roots: {pkg_roots & fnb_roots}"
        )


# ---------------------------------------------------------------------------
# Overlapping node path guard (task 7.4)
# ---------------------------------------------------------------------------

_OVERLAP_NODE = "PackagingLine.Press1.LineSpeed"  # same path in both configs


def _make_overlapping_configs() -> tuple:
    """Create active + inactive configs where one signal shares an opcua_node."""
    from factory_simulator.config import (
        EquipmentConfig,
        FactoryConfig,
        SignalConfig,
    )

    shared_signal = SignalConfig(
        model="steady_state",
        opcua_node=_OVERLAP_NODE,
        opcua_type="Float",
        min_clamp=0.0,
        max_clamp=100.0,
        units="m/min",
        params={"base": 50.0},
    )

    active_cfg = FactoryConfig(
        equipment={
            "press1": EquipmentConfig(
                enabled=True,
                type="press",
                signals={"line_speed": shared_signal},
            ),
        },
    )

    # Inactive config has a signal with the SAME opcua_node path
    inactive_cfg = FactoryConfig(
        equipment={
            "mixer1": EquipmentConfig(
                enabled=True,
                type="mixer",
                signals={
                    "speed": SignalConfig(
                        model="steady_state",
                        opcua_node=_OVERLAP_NODE,  # <-- overlap
                        opcua_type="Float",
                        min_clamp=0.0,
                        max_clamp=200.0,
                        units="rpm",
                        params={"base": 100.0},
                    ),
                },
            ),
        },
    )

    return active_cfg, inactive_cfg


class TestOverlappingNodeGuard:
    """Task 7.4: overlapping opcua_node paths are skipped with a warning."""

    @pytest.mark.asyncio
    async def test_overlapping_opcua_node_skipped(self) -> None:
        """Server starts OK; the active node remains, inactive duplicate is skipped."""
        active_cfg, inactive_cfg = _make_overlapping_configs()
        store = SignalStore()
        server = OpcuaServer(
            active_cfg, store,
            host=_HOST, port=0,
            inactive_config=inactive_cfg,
        )
        await server.start()
        try:
            port = server.actual_port
            assert port > 0

            client = Client(f"opc.tcp://{_HOST}:{port}/")
            await client.connect()
            try:
                # The node exists and is active (readable)
                node = client.get_node(_node_id(_OVERLAP_NODE))
                bname = await node.read_browse_name()
                assert bname.Name == "LineSpeed"

                level = await _read_access_level(client, _OVERLAP_NODE)
                assert level >= 1  # active, not overwritten by inactive
            finally:
                await client.disconnect()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_overlapping_opcua_node_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """A warning is logged when an inactive node overlaps an active node."""
        import logging

        active_cfg, inactive_cfg = _make_overlapping_configs()
        store = SignalStore()
        server = OpcuaServer(
            active_cfg, store,
            host=_HOST, port=0,
            inactive_config=inactive_cfg,
        )

        with caplog.at_level(logging.WARNING, logger="factory_simulator.protocols.opcua_server"):
            await server.start()

        try:
            assert any(
                "conflicts with active node" in rec.message
                and _OVERLAP_NODE in rec.message
                for rec in caplog.records
            ), (
                "Expected warning about overlapping node, got: "
                f"{[r.message for r in caplog.records]}"
            )
        finally:
            await server.stop()
