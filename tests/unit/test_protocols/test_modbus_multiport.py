"""Tests for multi-port Modbus servers in realistic mode (task 5.2).

Verifies:
- Collapsed mode: single server serves all registers (backward-compatible)
- Realistic mode: per-endpoint servers serve only their equipment registers
- Out-of-range reads return Modbus exception 0x02 (Illegal Data Address)
- CDAB byte order on mixer endpoint
- Multi-slave UID routing on shared ports (press + energy, oven gateway)
- Connection limit and response latency configs are wired
- DataEngine creates correct number of servers per topology mode

PRD Reference: Section 3a.2, 3a.3, 3a.4, 3a.5
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from pymodbus.pdu.register_message import ExcCodes

from factory_simulator.config import (
    FactoryConfig,
    NetworkConfig,
    load_config,
)
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.protocols.modbus_server import (
    FactoryDeviceContext,
    ModbusServer,
    build_register_map,
)
from factory_simulator.store import SignalStore
from factory_simulator.topology import (
    ModbusEndpointSpec,
    NetworkTopologyManager,
)

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
_PACKAGING_CONFIG = _CONFIG_DIR / "factory.yaml"
_FOODBEV_CONFIG = _CONFIG_DIR / "factory-foodbev.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_packaging_config() -> FactoryConfig:
    return load_config(_PACKAGING_CONFIG)


def _load_foodbev_config() -> FactoryConfig:
    return load_config(_FOODBEV_CONFIG)


def _make_deterministic_server(
    config: FactoryConfig,
    store: SignalStore,
    endpoint: ModbusEndpointSpec | None = None,
    port: int = 0,
) -> ModbusServer:
    """Create a ModbusServer with deterministic RNGs and disabled injection."""
    rng = np.random.default_rng(42)
    return ModbusServer(
        config,
        store,
        port=port,
        endpoint=endpoint,
        comm_drop_rng=rng,
        exception_rng=rng,
        duplicate_rng=None,
    )


# ---------------------------------------------------------------------------
# Equipment filter in build_register_map
# ---------------------------------------------------------------------------


class TestRegisterMapFiltering:
    """Test that build_register_map respects equipment_filter."""

    def test_no_filter_includes_all(self) -> None:
        config = _load_packaging_config()
        rmap = build_register_map(config)
        # Should include press, laminator, slitter, and energy HR entries
        signal_prefixes = {e.signal_id.split(".")[0] for e in rmap.hr_entries}
        assert "press" in signal_prefixes
        assert "laminator" in signal_prefixes
        assert "slitter" in signal_prefixes
        assert "energy" in signal_prefixes

    def test_filter_press_only(self) -> None:
        config = _load_packaging_config()
        rmap = build_register_map(config, equipment_filter={"press"})
        signal_prefixes = {e.signal_id.split(".")[0] for e in rmap.hr_entries}
        assert "press" in signal_prefixes
        assert "laminator" not in signal_prefixes
        assert "slitter" not in signal_prefixes
        assert "energy" not in signal_prefixes

    def test_filter_laminator_only(self) -> None:
        config = _load_packaging_config()
        rmap = build_register_map(config, equipment_filter={"laminator"})
        signal_prefixes = {e.signal_id.split(".")[0] for e in rmap.hr_entries}
        assert "laminator" in signal_prefixes
        assert "press" not in signal_prefixes

    def test_filter_multiple_equipment(self) -> None:
        config = _load_packaging_config()
        rmap = build_register_map(config, equipment_filter={"press", "energy"})
        signal_prefixes = {e.signal_id.split(".")[0] for e in rmap.hr_entries}
        assert "press" in signal_prefixes
        assert "energy" in signal_prefixes
        assert "laminator" not in signal_prefixes

    def test_filter_foodbev_mixer(self) -> None:
        config = _load_foodbev_config()
        rmap = build_register_map(config, equipment_filter={"mixer"})
        signal_prefixes = {e.signal_id.split(".")[0] for e in rmap.hr_entries}
        assert "mixer" in signal_prefixes
        assert "oven" not in signal_prefixes
        assert "sealer" not in signal_prefixes

    def test_filter_foodbev_oven_and_energy(self) -> None:
        config = _load_foodbev_config()
        rmap = build_register_map(config, equipment_filter={"oven", "energy"})
        signal_prefixes = {e.signal_id.split(".")[0] for e in rmap.hr_entries}
        assert "oven" in signal_prefixes
        assert "energy" in signal_prefixes
        assert "mixer" not in signal_prefixes


# ---------------------------------------------------------------------------
# FactoryDeviceContext address validation (0x02 enforcement)
# ---------------------------------------------------------------------------


class TestAddressValidation:
    """Test that FactoryDeviceContext rejects out-of-range addresses with 0x02."""

    def _make_context(
        self,
        valid_hr: set[int] | None = None,
        valid_ir: set[int] | None = None,
    ) -> FactoryDeviceContext:
        from pymodbus.datastore import ModbusSequentialDataBlock

        hr = ModbusSequentialDataBlock(0, [0] * 100)  # type: ignore[no-untyped-call]
        ir = ModbusSequentialDataBlock(0, [0] * 100)  # type: ignore[no-untyped-call]
        co = ModbusSequentialDataBlock(0, [False] * 16)  # type: ignore[no-untyped-call]
        di = ModbusSequentialDataBlock(0, [False] * 16)  # type: ignore[no-untyped-call]
        return FactoryDeviceContext(
            valid_hr_addresses=valid_hr,
            valid_ir_addresses=valid_ir,
            hr=hr,
            ir=ir,
            co=co,
            di=di,
        )

    def test_no_restriction_allows_all(self) -> None:
        """Without valid_addresses, all addresses are served (collapsed mode)."""
        ctx = self._make_context()
        result = ctx.getValues(3, 10, 1)  # FC03 HR read
        assert result != ExcCodes.ILLEGAL_ADDRESS

    def test_valid_hr_address_allowed(self) -> None:
        ctx = self._make_context(valid_hr={10, 11})
        result = ctx.getValues(3, 10, 2)  # FC03 HR read addresses 10-11
        assert result != ExcCodes.ILLEGAL_ADDRESS

    def test_invalid_hr_address_returns_0x02(self) -> None:
        ctx = self._make_context(valid_hr={10, 11})
        result = ctx.getValues(3, 50, 1)  # FC03 HR read address 50
        assert result == ExcCodes.ILLEGAL_ADDRESS

    def test_partial_overlap_hr_returns_0x02(self) -> None:
        """If any address in the range is invalid, return 0x02."""
        ctx = self._make_context(valid_hr={10, 11})
        result = ctx.getValues(3, 10, 3)  # addresses 10, 11, 12 — 12 is invalid
        assert result == ExcCodes.ILLEGAL_ADDRESS

    def test_valid_ir_address_allowed(self) -> None:
        ctx = self._make_context(valid_ir={5})
        result = ctx.getValues(4, 5, 1)  # FC04 IR read address 5
        assert result != ExcCodes.ILLEGAL_ADDRESS

    def test_invalid_ir_address_returns_0x02(self) -> None:
        ctx = self._make_context(valid_ir={5})
        result = ctx.getValues(4, 20, 1)  # FC04 IR read address 20
        assert result == ExcCodes.ILLEGAL_ADDRESS

    def test_coils_not_restricted(self) -> None:
        """Coil reads (FC01) are not restricted by valid_hr/valid_ir."""
        ctx = self._make_context(valid_hr={10})
        result = ctx.getValues(1, 0, 1)  # FC01 coil read
        assert result != ExcCodes.ILLEGAL_ADDRESS


# ---------------------------------------------------------------------------
# ModbusServer with endpoint (realistic mode)
# ---------------------------------------------------------------------------


class TestModbusServerWithEndpoint:
    """Test ModbusServer constructed with a ModbusEndpointSpec."""

    def test_endpoint_overrides_port(self) -> None:
        config = _load_packaging_config()
        store = SignalStore()
        ep = ModbusEndpointSpec(
            port=5021,
            unit_ids=[1],
            equipment_ids=["laminator"],
            uid_equipment_map={1: ["laminator"]},
            controller_type="S7-1200",
            controller_name="laminator_plc",
        )
        server = _make_deterministic_server(config, store, endpoint=ep)
        assert server.port == 5021

    def test_endpoint_filters_register_map(self) -> None:
        config = _load_packaging_config()
        store = SignalStore()
        ep = ModbusEndpointSpec(
            port=5021,
            unit_ids=[1],
            equipment_ids=["laminator"],
            uid_equipment_map={1: ["laminator"]},
            controller_type="S7-1200",
            controller_name="laminator_plc",
        )
        server = _make_deterministic_server(config, store, endpoint=ep)
        rmap = server.register_map

        # Should only have laminator HR entries
        signal_prefixes = {e.signal_id.split(".")[0] for e in rmap.hr_entries}
        assert signal_prefixes == {"laminator"}

    def test_endpoint_press_energy_has_both(self) -> None:
        config = _load_packaging_config()
        store = SignalStore()
        ep = ModbusEndpointSpec(
            port=5020,
            unit_ids=[1, 5],
            equipment_ids=["press", "energy"],
            uid_equipment_map={1: ["press"], 5: ["energy"]},
            controller_type="S7-1500",
            controller_name="press_plc",
        )
        server = _make_deterministic_server(config, store, endpoint=ep)
        rmap = server.register_map

        signal_prefixes = {e.signal_id.split(".")[0] for e in rmap.hr_entries}
        assert "press" in signal_prefixes
        assert "energy" in signal_prefixes
        assert "laminator" not in signal_prefixes

    def test_endpoint_stores_response_latency(self) -> None:
        config = _load_packaging_config()
        store = SignalStore()
        from factory_simulator.config import ConnectionLimitConfig

        ep = ModbusEndpointSpec(
            port=5021,
            unit_ids=[1],
            equipment_ids=["laminator"],
            controller_type="S7-1200",
            controller_name="laminator_plc",
            connection_limit=ConnectionLimitConfig(
                max_connections=3,
                response_timeout_ms_typical=100.0,
                response_timeout_ms_max=500.0,
            ),
        )
        server = _make_deterministic_server(config, store, endpoint=ep)
        assert server.response_latency_ms == 100.0

    def test_no_endpoint_has_zero_latency(self) -> None:
        config = _load_packaging_config()
        store = SignalStore()
        server = _make_deterministic_server(config, store)
        assert server.response_latency_ms == 0.0

    def test_out_of_range_hr_returns_0x02(self) -> None:
        """Laminator server rejects reads to press register addresses."""
        config = _load_packaging_config()
        store = SignalStore()
        ep = ModbusEndpointSpec(
            port=5021,
            unit_ids=[1],
            equipment_ids=["laminator"],
            uid_equipment_map={1: ["laminator"]},
            controller_type="S7-1200",
            controller_name="laminator_plc",
        )
        server = _make_deterministic_server(config, store, endpoint=ep)

        # The laminator HR entries are at addresses 400-409.
        # Address 100 (press) should be invalid and return 0x02.
        # We access via the device context directly.
        result = server._device_context.getValues(3, 100, 1)
        assert result == ExcCodes.ILLEGAL_ADDRESS

    def test_in_range_hr_succeeds(self) -> None:
        """Laminator server accepts reads to its own register addresses."""
        config = _load_packaging_config()
        store = SignalStore()
        ep = ModbusEndpointSpec(
            port=5021,
            unit_ids=[1],
            equipment_ids=["laminator"],
            uid_equipment_map={1: ["laminator"]},
            controller_type="S7-1200",
            controller_name="laminator_plc",
        )
        server = _make_deterministic_server(config, store, endpoint=ep)

        # Laminator HR entries are at addresses 400+. Find one.
        rmap = server.register_map
        assert len(rmap.hr_entries) > 0
        addr = rmap.hr_entries[0].address
        result = server._device_context.getValues(3, addr, 1)
        assert result != ExcCodes.ILLEGAL_ADDRESS


# ---------------------------------------------------------------------------
# CDAB byte order on mixer endpoint
# ---------------------------------------------------------------------------


class TestMixerCdabByteOrder:
    """Test that mixer endpoint correctly uses CDAB byte order."""

    def test_mixer_endpoint_has_cdab(self) -> None:
        topo = NetworkTopologyManager(
            NetworkConfig(mode="realistic"), "food_bev",
        )
        endpoints = topo.modbus_endpoints()
        mixer_eps = [ep for ep in endpoints if ep.controller_name == "mixer_plc"]
        assert len(mixer_eps) == 1
        assert mixer_eps[0].byte_order == "CDAB"

    def test_mixer_register_map_has_cdab_entries(self) -> None:
        config = _load_foodbev_config()
        rmap = build_register_map(config, equipment_filter={"mixer"})
        # Mixer HR entries should have CDAB byte order
        for entry in rmap.hr_entries:
            if entry.data_type in ("float32", "uint32"):
                assert entry.byte_order == "CDAB", (
                    f"Mixer signal {entry.signal_id} should use CDAB byte order"
                )


# ---------------------------------------------------------------------------
# Multi-slave UID routing
# ---------------------------------------------------------------------------


class TestMultiSlaveUidRouting:
    """Test UID routing on shared-port endpoints."""

    def test_press_port_has_two_uids(self) -> None:
        topo = NetworkTopologyManager(
            NetworkConfig(mode="realistic"), "packaging",
        )
        endpoints = topo.modbus_endpoints()
        press_eps = [ep for ep in endpoints if ep.port == 5020]
        assert len(press_eps) == 1
        assert sorted(press_eps[0].unit_ids) == [1, 5]

    def test_oven_gateway_has_four_uids(self) -> None:
        topo = NetworkTopologyManager(
            NetworkConfig(mode="realistic"), "food_bev",
        )
        endpoints = topo.modbus_endpoints()
        oven_eps = [ep for ep in endpoints if ep.port == 5031]
        assert len(oven_eps) == 1
        assert sorted(oven_eps[0].unit_ids) == [1, 2, 3, 10]

    def test_press_uid_equipment_map(self) -> None:
        topo = NetworkTopologyManager(
            NetworkConfig(mode="realistic"), "packaging",
        )
        endpoints = topo.modbus_endpoints()
        press_ep = next(ep for ep in endpoints if ep.port == 5020)
        assert press_ep.uid_equipment_map[1] == ["press"]
        assert press_ep.uid_equipment_map[5] == ["energy"]

    def test_oven_uid_equipment_map(self) -> None:
        topo = NetworkTopologyManager(
            NetworkConfig(mode="realistic"), "food_bev",
        )
        endpoints = topo.modbus_endpoints()
        oven_ep = next(ep for ep in endpoints if ep.port == 5031)
        assert oven_ep.uid_equipment_map[1] == ["oven"]
        assert oven_ep.uid_equipment_map[2] == ["oven"]
        assert oven_ep.uid_equipment_map[3] == ["oven"]
        assert oven_ep.uid_equipment_map[10] == ["energy"]


# ---------------------------------------------------------------------------
# Connection limits and response latency
# ---------------------------------------------------------------------------


class TestConnectionConfig:
    """Test that endpoint configs carry connection limit and latency."""

    def test_s7_1500_max_16_connections(self) -> None:
        topo = NetworkTopologyManager(
            NetworkConfig(mode="realistic"), "packaging",
        )
        endpoints = topo.modbus_endpoints()
        press_ep = next(ep for ep in endpoints if ep.port == 5020)
        assert press_ep.connection_limit.max_connections == 16

    def test_s7_1200_max_3_connections(self) -> None:
        topo = NetworkTopologyManager(
            NetworkConfig(mode="realistic"), "packaging",
        )
        endpoints = topo.modbus_endpoints()
        lam_ep = next(ep for ep in endpoints if ep.port == 5021)
        assert lam_ep.connection_limit.max_connections == 3

    def test_eurotherm_typical_latency_150ms(self) -> None:
        topo = NetworkTopologyManager(
            NetworkConfig(mode="realistic"), "food_bev",
        )
        endpoints = topo.modbus_endpoints()
        oven_ep = next(ep for ep in endpoints if ep.port == 5031)
        assert oven_ep.connection_limit.response_timeout_ms_typical == 150.0

    def test_compactlogix_max_8_connections(self) -> None:
        topo = NetworkTopologyManager(
            NetworkConfig(mode="realistic"), "food_bev",
        )
        endpoints = topo.modbus_endpoints()
        mixer_ep = next(ep for ep in endpoints if ep.port == 5030)
        assert mixer_ep.connection_limit.max_connections == 8


# ---------------------------------------------------------------------------
# DataEngine creates correct number of servers
# ---------------------------------------------------------------------------


class TestDataEngineModbusCreation:
    """Test that DataEngine.create_modbus_servers() works correctly."""

    def test_no_topology_creates_single_server(self) -> None:
        config = _load_packaging_config()
        store = SignalStore()
        engine = DataEngine(config, store)
        servers = engine.create_modbus_servers()
        assert len(servers) == 1
        assert servers[0].endpoint is None

    def test_collapsed_topology_creates_single_server(self) -> None:
        config = _load_packaging_config()
        store = SignalStore()
        topo = NetworkTopologyManager(
            NetworkConfig(mode="collapsed"), "packaging",
        )
        engine = DataEngine(config, store, topology=topo)
        servers = engine.create_modbus_servers()
        assert len(servers) == 1

    def test_realistic_packaging_creates_three_servers(self) -> None:
        config = _load_packaging_config()
        store = SignalStore()
        topo = NetworkTopologyManager(
            NetworkConfig(mode="realistic"), "packaging",
        )
        engine = DataEngine(config, store, topology=topo)
        servers = engine.create_modbus_servers()
        assert len(servers) == 3
        ports = {s.port for s in servers}
        assert ports == {5020, 5021, 5022}

    def test_realistic_foodbev_creates_six_servers(self) -> None:
        config = _load_foodbev_config()
        store = SignalStore()
        topo = NetworkTopologyManager(
            NetworkConfig(mode="realistic"), "food_bev",
        )
        engine = DataEngine(config, store, topology=topo)
        servers = engine.create_modbus_servers()
        assert len(servers) == 6
        ports = {s.port for s in servers}
        assert ports == {5030, 5031, 5032, 5033, 5034, 5035}

    def test_realistic_servers_have_endpoints(self) -> None:
        config = _load_packaging_config()
        store = SignalStore()
        topo = NetworkTopologyManager(
            NetworkConfig(mode="realistic"), "packaging",
        )
        engine = DataEngine(config, store, topology=topo)
        servers = engine.create_modbus_servers()
        for server in servers:
            assert server.endpoint is not None

    def test_topology_property(self) -> None:
        config = _load_packaging_config()
        store = SignalStore()
        topo = NetworkTopologyManager(
            NetworkConfig(mode="realistic"), "packaging",
        )
        engine = DataEngine(config, store, topology=topo)
        assert engine.topology is topo


# ---------------------------------------------------------------------------
# Backward compatibility: collapsed mode tests
# ---------------------------------------------------------------------------


class TestCollapsedModeBackwardCompat:
    """Verify that collapsed mode (no endpoint) works exactly as before."""

    def test_no_endpoint_serves_all_registers(self) -> None:
        config = _load_packaging_config()
        store = SignalStore()
        server = _make_deterministic_server(config, store)

        # All equipment should be present in the register map
        rmap = server.register_map
        signal_prefixes = {e.signal_id.split(".")[0] for e in rmap.hr_entries}
        assert "press" in signal_prefixes
        assert "laminator" in signal_prefixes
        assert "slitter" in signal_prefixes
        assert "energy" in signal_prefixes

    def test_no_endpoint_has_no_address_restriction(self) -> None:
        config = _load_packaging_config()
        store = SignalStore()
        server = _make_deterministic_server(config, store)

        # Any valid block address should succeed (no 0x02)
        result = server._device_context.getValues(3, 10, 1)
        assert result != ExcCodes.ILLEGAL_ADDRESS

    def test_no_endpoint_uses_config_port(self) -> None:
        config = _load_packaging_config()
        store = SignalStore()
        # Port 0 means "use config default"
        server = ModbusServer(
            config,
            store,
            comm_drop_rng=np.random.default_rng(42),
            exception_rng=np.random.default_rng(42),
        )
        assert server.port == config.protocols.modbus.port
