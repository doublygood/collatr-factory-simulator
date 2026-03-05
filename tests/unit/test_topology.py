"""Tests for network topology manager and config models.

Task 5.1: Network Topology Manager and Config
PRD Reference: Section 3a.4
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from factory_simulator.config import (
    ClockDriftConfig,
    ConnectionDropConfig,
    ConnectionLimitConfig,
    NetworkConfig,
    ScanCycleConfig,
    load_config,
)
from factory_simulator.topology import (
    ModbusEndpointSpec,
    MqttEndpointSpec,
    NetworkTopologyManager,
    OpcuaEndpointSpec,
)

# ---------------------------------------------------------------------------
# Config model validation
# ---------------------------------------------------------------------------


class TestClockDriftConfig:
    def test_defaults(self) -> None:
        cfg = ClockDriftConfig()
        assert cfg.initial_offset_ms == 0.0
        assert cfg.drift_rate_s_per_day == 0.0

    def test_valid_values(self) -> None:
        cfg = ClockDriftConfig(initial_offset_ms=5000.0, drift_rate_s_per_day=5.0)
        assert cfg.initial_offset_ms == 5000.0
        assert cfg.drift_rate_s_per_day == 5.0

    def test_rejects_negative_offset(self) -> None:
        with pytest.raises(ValidationError, match="non-negative"):
            ClockDriftConfig(initial_offset_ms=-100.0)

    def test_rejects_negative_drift_rate(self) -> None:
        with pytest.raises(ValidationError, match="non-negative"):
            ClockDriftConfig(drift_rate_s_per_day=-0.5)


class TestScanCycleConfig:
    def test_defaults(self) -> None:
        cfg = ScanCycleConfig()
        assert cfg.cycle_ms == 10.0
        assert cfg.jitter_pct == 0.05

    def test_valid_values(self) -> None:
        cfg = ScanCycleConfig(cycle_ms=100.0, jitter_pct=0.10)
        assert cfg.cycle_ms == 100.0
        assert cfg.jitter_pct == 0.10

    def test_rejects_zero_cycle(self) -> None:
        with pytest.raises(ValidationError, match="positive"):
            ScanCycleConfig(cycle_ms=0.0)

    def test_rejects_negative_cycle(self) -> None:
        with pytest.raises(ValidationError, match="positive"):
            ScanCycleConfig(cycle_ms=-10.0)

    def test_rejects_negative_jitter(self) -> None:
        with pytest.raises(ValidationError, match=r"between 0.0 and 1.0"):
            ScanCycleConfig(jitter_pct=-0.1)

    def test_rejects_jitter_over_one(self) -> None:
        with pytest.raises(ValidationError, match=r"between 0.0 and 1.0"):
            ScanCycleConfig(jitter_pct=1.5)


class TestConnectionLimitConfig:
    def test_defaults(self) -> None:
        cfg = ConnectionLimitConfig()
        assert cfg.max_connections == 16
        assert cfg.response_timeout_ms_typical == 50.0

    def test_rejects_zero_connections(self) -> None:
        with pytest.raises(ValidationError, match="positive"):
            ConnectionLimitConfig(max_connections=0)

    def test_rejects_negative_timeout(self) -> None:
        with pytest.raises(ValidationError, match="non-negative"):
            ConnectionLimitConfig(response_timeout_ms_typical=-10.0)


class TestConnectionDropConfig:
    def test_defaults(self) -> None:
        cfg = ConnectionDropConfig()
        assert cfg.mtbf_hours_min == 72.0
        assert cfg.mtbf_hours_max == 168.0

    def test_rejects_zero_mtbf(self) -> None:
        with pytest.raises(ValidationError, match="positive"):
            ConnectionDropConfig(mtbf_hours_min=0.0)

    def test_rejects_inverted_mtbf_range(self) -> None:
        with pytest.raises(ValidationError, match="mtbf_hours_min must be"):
            ConnectionDropConfig(mtbf_hours_min=100.0, mtbf_hours_max=10.0)

    def test_rejects_inverted_delay_range(self) -> None:
        with pytest.raises(ValidationError, match="reconnection_delay_s_min must be"):
            ConnectionDropConfig(
                reconnection_delay_s_min=10.0, reconnection_delay_s_max=1.0
            )


class TestNetworkConfig:
    def test_defaults(self) -> None:
        cfg = NetworkConfig()
        assert cfg.mode == "collapsed"
        assert cfg.clock_drift == {}
        assert cfg.scan_cycle == {}
        assert cfg.connection_limits == {}
        assert cfg.connection_drops == {}

    def test_realistic_mode(self) -> None:
        cfg = NetworkConfig(mode="realistic")
        assert cfg.mode == "realistic"

    def test_rejects_invalid_mode(self) -> None:
        with pytest.raises(ValidationError):
            NetworkConfig(mode="invalid")  # type: ignore[arg-type]

    def test_with_clock_drift_overrides(self) -> None:
        cfg = NetworkConfig(
            clock_drift={
                "press_plc": ClockDriftConfig(
                    initial_offset_ms=100.0, drift_rate_s_per_day=0.1
                )
            }
        )
        assert "press_plc" in cfg.clock_drift
        assert cfg.clock_drift["press_plc"].initial_offset_ms == 100.0


# ---------------------------------------------------------------------------
# Topology manager: collapsed mode
# ---------------------------------------------------------------------------


class TestCollapsedModePackaging:
    def setup_method(self) -> None:
        self.mgr = NetworkTopologyManager(
            config=NetworkConfig(mode="collapsed"), profile="packaging"
        )

    def test_mode_and_profile(self) -> None:
        assert self.mgr.mode == "collapsed"
        assert self.mgr.profile == "packaging"

    def test_single_modbus_endpoint(self) -> None:
        endpoints = self.mgr.modbus_endpoints()
        assert len(endpoints) == 1
        assert isinstance(endpoints[0], ModbusEndpointSpec)
        assert endpoints[0].port == 502

    def test_single_opcua_endpoint(self) -> None:
        endpoints = self.mgr.opcua_endpoints()
        assert len(endpoints) == 1
        assert isinstance(endpoints[0], OpcuaEndpointSpec)
        assert endpoints[0].port == 4840

    def test_mqtt_endpoint(self) -> None:
        ep = self.mgr.mqtt_endpoint()
        assert isinstance(ep, MqttEndpointSpec)
        assert ep.broker_port == 1883
        # Collapsed mode: no clock drift
        assert ep.clock_drift.initial_offset_ms == 0.0
        assert ep.clock_drift.drift_rate_s_per_day == 0.0

    def test_mqtt_endpoint_realistic_has_drift(self) -> None:
        """Realistic mode MQTT endpoint has non-zero clock drift."""
        mgr_realistic = NetworkTopologyManager(
            config=NetworkConfig(mode="realistic"), profile="packaging"
        )
        ep = mgr_realistic.mqtt_endpoint()
        assert ep.clock_drift.initial_offset_ms > 0.0
        assert ep.clock_drift.drift_rate_s_per_day > 0.0


class TestCollapsedModeFoodBev:
    def setup_method(self) -> None:
        self.mgr = NetworkTopologyManager(
            config=NetworkConfig(mode="collapsed"), profile="food_bev"
        )

    def test_single_modbus_endpoint(self) -> None:
        endpoints = self.mgr.modbus_endpoints()
        assert len(endpoints) == 1
        assert endpoints[0].port == 502

    def test_single_opcua_endpoint(self) -> None:
        endpoints = self.mgr.opcua_endpoints()
        assert len(endpoints) == 1
        assert endpoints[0].port == 4840


# ---------------------------------------------------------------------------
# Topology manager: realistic mode — packaging
# ---------------------------------------------------------------------------


class TestRealisticModePackaging:
    def setup_method(self) -> None:
        self.mgr = NetworkTopologyManager(
            config=NetworkConfig(mode="realistic"), profile="packaging"
        )

    def test_modbus_endpoint_count(self) -> None:
        """Packaging: 3 Modbus endpoints (press+energy, laminator, slitter).

        Energy meter is on the press port as UID 5, so 3 server endpoints total.
        PRD 3a.4: 4 Modbus TCP connections from CollatrEdge's perspective,
        but the press and energy meter share the same port.
        """
        endpoints = self.mgr.modbus_endpoints()
        assert len(endpoints) == 3

    def test_modbus_port_mapping(self) -> None:
        """Verify port assignments match PRD 3a.4 table."""
        endpoints = self.mgr.modbus_endpoints()
        ports = {ep.port for ep in endpoints}
        assert ports == {5020, 5021, 5022}

    def test_press_port_serves_two_uids(self) -> None:
        """Press port 5020 serves UID 1 (press) and UID 5 (energy meter)."""
        endpoints = self.mgr.modbus_endpoints()
        press_ep = next(ep for ep in endpoints if ep.port == 5020)
        assert 1 in press_ep.unit_ids
        assert 5 in press_ep.unit_ids

    def test_press_controller_type(self) -> None:
        endpoints = self.mgr.modbus_endpoints()
        press_ep = next(ep for ep in endpoints if ep.port == 5020)
        assert press_ep.controller_type == "S7-1500"

    def test_laminator_controller_type(self) -> None:
        endpoints = self.mgr.modbus_endpoints()
        lam_ep = next(ep for ep in endpoints if ep.port == 5021)
        assert lam_ep.controller_type == "S7-1200"

    def test_all_abcd_byte_order(self) -> None:
        """All packaging endpoints use ABCD byte order."""
        endpoints = self.mgr.modbus_endpoints()
        for ep in endpoints:
            assert ep.byte_order == "ABCD"

    def test_opcua_endpoint_count(self) -> None:
        """Packaging: 1 OPC-UA endpoint."""
        endpoints = self.mgr.opcua_endpoints()
        assert len(endpoints) == 1

    def test_opcua_port_4840(self) -> None:
        endpoints = self.mgr.opcua_endpoints()
        assert endpoints[0].port == 4840

    def test_opcua_node_tree_root(self) -> None:
        endpoints = self.mgr.opcua_endpoints()
        assert endpoints[0].node_tree_root == "PackagingLine"

    def test_clock_drift_populated(self) -> None:
        """Realistic endpoints get default clock drift from controller type."""
        endpoints = self.mgr.modbus_endpoints()
        press_ep = next(ep for ep in endpoints if ep.port == 5020)
        # S7-1500 defaults: 200ms offset, 0.3 s/day drift
        assert press_ep.clock_drift.initial_offset_ms == 200.0
        assert press_ep.clock_drift.drift_rate_s_per_day == 0.3

    def test_scan_cycle_populated(self) -> None:
        endpoints = self.mgr.modbus_endpoints()
        press_ep = next(ep for ep in endpoints if ep.port == 5020)
        # S7-1500: 10ms cycle
        assert press_ep.scan_cycle.cycle_ms == 10.0

    def test_connection_limit_populated(self) -> None:
        endpoints = self.mgr.modbus_endpoints()
        press_ep = next(ep for ep in endpoints if ep.port == 5020)
        # S7-1500: 16 connections
        assert press_ep.connection_limit.max_connections == 16


# ---------------------------------------------------------------------------
# Topology manager: realistic mode — F&B
# ---------------------------------------------------------------------------


class TestRealisticModeFoodBev:
    def setup_method(self) -> None:
        self.mgr = NetworkTopologyManager(
            config=NetworkConfig(mode="realistic"), profile="food_bev"
        )

    def test_modbus_endpoint_count(self) -> None:
        """F&B: 6 Modbus endpoints (mixer, oven_gw, filler, sealer, chiller, CIP).

        Oven gateway serves UIDs 1/2/3 (zones) and UID 10 (energy).
        PRD 3a.4: 7 Modbus TCP connections from CollatrEdge (3 multi-slave on oven GW).
        """
        endpoints = self.mgr.modbus_endpoints()
        assert len(endpoints) == 6

    def test_modbus_port_mapping(self) -> None:
        endpoints = self.mgr.modbus_endpoints()
        ports = {ep.port for ep in endpoints}
        assert ports == {5030, 5031, 5032, 5033, 5034, 5035}

    def test_mixer_cdab_byte_order(self) -> None:
        """Mixer (CompactLogix) uses CDAB byte order."""
        endpoints = self.mgr.modbus_endpoints()
        mixer_ep = next(ep for ep in endpoints if ep.port == 5030)
        assert mixer_ep.byte_order == "CDAB"
        assert mixer_ep.controller_type == "CompactLogix"

    def test_oven_gateway_multi_slave(self) -> None:
        """Oven gateway serves UIDs 1, 2, 3 (zones) and 10 (energy)."""
        endpoints = self.mgr.modbus_endpoints()
        oven_ep = next(ep for ep in endpoints if ep.port == 5031)
        assert set(oven_ep.unit_ids) == {1, 2, 3, 10}
        assert oven_ep.controller_type == "Eurotherm"

    def test_opcua_endpoint_count(self) -> None:
        """F&B: 2 OPC-UA endpoints (filler + QC station)."""
        endpoints = self.mgr.opcua_endpoints()
        assert len(endpoints) == 2

    def test_opcua_port_mapping(self) -> None:
        endpoints = self.mgr.opcua_endpoints()
        ports = {ep.port for ep in endpoints}
        assert ports == {4841, 4842}

    def test_opcua_node_tree_roots(self) -> None:
        endpoints = self.mgr.opcua_endpoints()
        roots = {ep.node_tree_root for ep in endpoints}
        assert "FoodBevLine.Filler1" in roots
        assert "FoodBevLine.QC1" in roots

    def test_eurotherm_clock_drift(self) -> None:
        """Eurotherm default: 5000ms offset, 5.0 s/day drift."""
        endpoints = self.mgr.modbus_endpoints()
        oven_ep = next(ep for ep in endpoints if ep.port == 5031)
        assert oven_ep.clock_drift.initial_offset_ms == 5000.0
        assert oven_ep.clock_drift.drift_rate_s_per_day == 5.0

    def test_eurotherm_connection_limit(self) -> None:
        """Eurotherm gateway: max 2 connections."""
        endpoints = self.mgr.modbus_endpoints()
        oven_ep = next(ep for ep in endpoints if ep.port == 5031)
        assert oven_ep.connection_limit.max_connections == 2

    def test_eurotherm_scan_cycle(self) -> None:
        """Eurotherm: 100ms scan cycle."""
        endpoints = self.mgr.modbus_endpoints()
        oven_ep = next(ep for ep in endpoints if ep.port == 5031)
        assert oven_ep.scan_cycle.cycle_ms == 100.0

    def test_danfoss_connection_drop(self) -> None:
        """Danfoss chiller: MTBF 24-48h."""
        endpoints = self.mgr.modbus_endpoints()
        chiller_ep = next(ep for ep in endpoints if ep.port == 5034)
        assert chiller_ep.connection_drop.mtbf_hours_min == 24.0
        assert chiller_ep.connection_drop.mtbf_hours_max == 48.0


# ---------------------------------------------------------------------------
# Config override resolution
# ---------------------------------------------------------------------------


class TestConfigOverrides:
    def test_clock_drift_override(self) -> None:
        """User-provided clock drift override takes precedence over defaults."""
        custom_drift = ClockDriftConfig(
            initial_offset_ms=999.0, drift_rate_s_per_day=9.9
        )
        cfg = NetworkConfig(
            mode="realistic",
            clock_drift={"press_plc": custom_drift},
        )
        mgr = NetworkTopologyManager(config=cfg, profile="packaging")
        endpoints = mgr.modbus_endpoints()
        press_ep = next(ep for ep in endpoints if ep.port == 5020)
        assert press_ep.clock_drift.initial_offset_ms == 999.0
        assert press_ep.clock_drift.drift_rate_s_per_day == 9.9

    def test_scan_cycle_override(self) -> None:
        custom_scan = ScanCycleConfig(cycle_ms=50.0, jitter_pct=0.02)
        cfg = NetworkConfig(
            mode="realistic",
            scan_cycle={"mixer_plc": custom_scan},
        )
        mgr = NetworkTopologyManager(config=cfg, profile="food_bev")
        endpoints = mgr.modbus_endpoints()
        mixer_ep = next(ep for ep in endpoints if ep.port == 5030)
        assert mixer_ep.scan_cycle.cycle_ms == 50.0
        assert mixer_ep.scan_cycle.jitter_pct == 0.02

    def test_connection_limit_override(self) -> None:
        custom_limit = ConnectionLimitConfig(
            max_connections=4,
            response_timeout_ms_typical=25.0,
            response_timeout_ms_max=100.0,
        )
        cfg = NetworkConfig(
            mode="realistic",
            connection_limits={"oven_gateway": custom_limit},
        )
        mgr = NetworkTopologyManager(config=cfg, profile="food_bev")
        endpoints = mgr.modbus_endpoints()
        oven_ep = next(ep for ep in endpoints if ep.port == 5031)
        assert oven_ep.connection_limit.max_connections == 4


# ---------------------------------------------------------------------------
# Default config (None) behaviour
# ---------------------------------------------------------------------------


class TestNoneConfig:
    def test_none_config_uses_collapsed(self) -> None:
        """When config is None, default to collapsed mode."""
        mgr = NetworkTopologyManager(config=None, profile="packaging")
        assert mgr.mode == "collapsed"
        endpoints = mgr.modbus_endpoints()
        assert len(endpoints) == 1


# ---------------------------------------------------------------------------
# YAML config loading with network section
# ---------------------------------------------------------------------------


class TestYamlConfigWithNetwork:
    def test_packaging_yaml_loads(self) -> None:
        """Packaging YAML loads successfully with network section commented out."""
        cfg = load_config("config/factory.yaml", apply_env=False)
        # network is None when section is commented out
        assert cfg.network is None

    def test_foodbev_yaml_loads(self) -> None:
        """F&B YAML loads successfully with network section commented out."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        assert cfg.network is None

    def test_network_none_means_collapsed(self) -> None:
        """When network is None, topology manager defaults to collapsed."""
        cfg = load_config("config/factory.yaml", apply_env=False)
        mgr = NetworkTopologyManager(config=cfg.network, profile="packaging")
        assert mgr.mode == "collapsed"
