"""Tests for independent per-controller connection drops (Task 5.5).

Each controller endpoint in realistic mode gets its own CommDropScheduler,
configured from the endpoint's ConnectionDropConfig (MTBF-based).
One controller dropping does not affect others.

PRD Reference: Section 3a.5 (Connection Behaviour)
CLAUDE.md Rule 14: all fixtures explicitly configure injectable behaviour.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from factory_simulator.config import (
    ClockDriftConfig,
    CommDropConfig,
    ConnectionDropConfig,
    ConnectionLimitConfig,
    FactoryConfig,
    ScanCycleConfig,
    load_config,
)
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.protocols.modbus_server import (
    ModbusServer,
    _connection_drop_to_comm_drop,
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


def _load_packaging() -> FactoryConfig:
    return load_config(_PACKAGING_CONFIG)


def _load_foodbev() -> FactoryConfig:
    return load_config(_FOODBEV_CONFIG)


def _make_server(
    config: FactoryConfig,
    store: SignalStore,
    *,
    endpoint: ModbusEndpointSpec | None = None,
    comm_drop_rng_seed: int = 0,
) -> ModbusServer:
    """Create a ModbusServer with fully deterministic, explicitly configured RNGs."""
    return ModbusServer(
        config,
        store,
        endpoint=endpoint,
        comm_drop_rng=np.random.default_rng(comm_drop_rng_seed),
        exception_rng=np.random.default_rng(comm_drop_rng_seed),
        duplicate_rng=np.random.default_rng(comm_drop_rng_seed),
    )


def _make_endpoint(
    port: int,
    mtbf_min: float,
    mtbf_max: float,
    reconnect_min: float = 1.0,
    reconnect_max: float = 3.0,
    controller_type: str = "generic",
    controller_name: str = "ctrl",
) -> ModbusEndpointSpec:
    """Helper: create an endpoint with specific connection drop MTBF."""
    return ModbusEndpointSpec(
        port=port,
        unit_ids=[1],
        controller_type=controller_type,
        controller_name=controller_name,
        connection_drop=ConnectionDropConfig(
            mtbf_hours_min=mtbf_min,
            mtbf_hours_max=mtbf_max,
            reconnection_delay_s_min=reconnect_min,
            reconnection_delay_s_max=reconnect_max,
        ),
        clock_drift=ClockDriftConfig(),
        scan_cycle=ScanCycleConfig(),
        connection_limit=ConnectionLimitConfig(),
    )


# ---------------------------------------------------------------------------
# _connection_drop_to_comm_drop conversion
# ---------------------------------------------------------------------------


class TestConnectionDropConversion:
    """Unit tests for the MTBF → CommDropConfig conversion helper."""

    def test_eurotherm_frequency_range(self):
        """Eurotherm 8-24h MTBF → frequency 1/24 to 1/8 per hour."""
        conn_drop = ConnectionDropConfig(
            mtbf_hours_min=8.0,
            mtbf_hours_max=24.0,
            reconnection_delay_s_min=5.0,
            reconnection_delay_s_max=15.0,
        )
        result = _connection_drop_to_comm_drop(conn_drop)

        # Min freq = 1 / mtbf_max = 1/24
        assert result.frequency_per_hour[0] == pytest.approx(1.0 / 24.0)
        # Max freq = 1 / mtbf_min = 1/8
        assert result.frequency_per_hour[1] == pytest.approx(1.0 / 8.0)

    def test_s7_1500_frequency_range(self):
        """S7-1500 72-168h MTBF → frequency 1/168 to 1/72 per hour."""
        conn_drop = ConnectionDropConfig(
            mtbf_hours_min=72.0,
            mtbf_hours_max=168.0,
            reconnection_delay_s_min=1.0,
            reconnection_delay_s_max=3.0,
        )
        result = _connection_drop_to_comm_drop(conn_drop)

        assert result.frequency_per_hour[0] == pytest.approx(1.0 / 168.0)
        assert result.frequency_per_hour[1] == pytest.approx(1.0 / 72.0)

    def test_duration_maps_to_reconnection_delay(self):
        """Reconnection delay range maps directly to duration_seconds."""
        conn_drop = ConnectionDropConfig(
            mtbf_hours_min=24.0,
            mtbf_hours_max=48.0,
            reconnection_delay_s_min=3.0,
            reconnection_delay_s_max=10.0,
        )
        result = _connection_drop_to_comm_drop(conn_drop)

        assert result.duration_seconds[0] == pytest.approx(3.0)
        assert result.duration_seconds[1] == pytest.approx(10.0)

    def test_result_enabled(self):
        """Converted CommDropConfig is always enabled."""
        conn_drop = ConnectionDropConfig(
            mtbf_hours_min=8.0,
            mtbf_hours_max=24.0,
            reconnection_delay_s_min=5.0,
            reconnection_delay_s_max=15.0,
        )
        result = _connection_drop_to_comm_drop(conn_drop)
        assert result.enabled is True

    def test_returns_comm_drop_config_type(self):
        """Returns a CommDropConfig instance."""
        conn_drop = ConnectionDropConfig(
            mtbf_hours_min=48.0,
            mtbf_hours_max=72.0,
            reconnection_delay_s_min=2.0,
            reconnection_delay_s_max=5.0,
        )
        result = _connection_drop_to_comm_drop(conn_drop)
        assert isinstance(result, CommDropConfig)

    def test_eurotherm_drops_more_frequently_than_s7_1500(self):
        """Eurotherm max freq > S7-1500 max freq (shorter MTBF = more drops)."""
        eurotherm = _connection_drop_to_comm_drop(
            ConnectionDropConfig(
                mtbf_hours_min=8.0, mtbf_hours_max=24.0,
                reconnection_delay_s_min=5.0, reconnection_delay_s_max=15.0,
            )
        )
        s7_1500 = _connection_drop_to_comm_drop(
            ConnectionDropConfig(
                mtbf_hours_min=72.0, mtbf_hours_max=168.0,
                reconnection_delay_s_min=1.0, reconnection_delay_s_max=3.0,
            )
        )

        assert eurotherm.frequency_per_hour[0] > s7_1500.frequency_per_hour[0]
        assert eurotherm.frequency_per_hour[1] > s7_1500.frequency_per_hour[1]

    def test_min_freq_less_than_max_freq(self):
        """Converted frequency range is valid: min < max when mtbf_min < mtbf_max."""
        result = _connection_drop_to_comm_drop(
            ConnectionDropConfig(
                mtbf_hours_min=10.0, mtbf_hours_max=20.0,
                reconnection_delay_s_min=1.0, reconnection_delay_s_max=5.0,
            )
        )
        assert result.frequency_per_hour[0] < result.frequency_per_hour[1]

    def test_equal_mtbf_gives_equal_frequencies(self):
        """When mtbf_min == mtbf_max, both frequencies are equal."""
        result = _connection_drop_to_comm_drop(
            ConnectionDropConfig(
                mtbf_hours_min=24.0, mtbf_hours_max=24.0,
                reconnection_delay_s_min=5.0, reconnection_delay_s_max=5.0,
            )
        )
        assert result.frequency_per_hour[0] == pytest.approx(result.frequency_per_hour[1])

    def test_danfoss_drop_config(self):
        """Danfoss 24-48h MTBF converts correctly."""
        result = _connection_drop_to_comm_drop(
            ConnectionDropConfig(
                mtbf_hours_min=24.0, mtbf_hours_max=48.0,
                reconnection_delay_s_min=3.0, reconnection_delay_s_max=10.0,
            )
        )
        assert result.frequency_per_hour[0] == pytest.approx(1.0 / 48.0)
        assert result.frequency_per_hour[1] == pytest.approx(1.0 / 24.0)
        assert result.duration_seconds == [3.0, 10.0]


# ---------------------------------------------------------------------------
# ModbusServer drop scheduler configuration
# ---------------------------------------------------------------------------


class TestModbusServerDropConfig:
    """ModbusServer uses the correct drop config per mode."""

    def test_collapsed_mode_uses_global_modbus_drop(self):
        """Collapsed mode (no endpoint): uses config.data_quality.modbus_drop."""
        d = _load_packaging().model_dump()
        d["data_quality"]["modbus_drop"] = {
            "enabled": True,
            "frequency_per_hour": [5.0, 10.0],
            "duration_seconds": [2.0, 4.0],
        }
        cfg = FactoryConfig.model_validate(d)

        server = _make_server(cfg, SignalStore())

        assert server._drop_scheduler._cfg.frequency_per_hour == [5.0, 10.0]
        assert server._drop_scheduler._cfg.duration_seconds == [2.0, 4.0]

    def test_realistic_mode_uses_endpoint_connection_drop(self):
        """Realistic mode (endpoint set): uses endpoint.connection_drop, not global config."""
        cfg = _load_packaging()
        ep = _make_endpoint(
            port=5020, mtbf_min=8.0, mtbf_max=24.0,
            reconnect_min=5.0, reconnect_max=15.0,
            controller_type="Eurotherm", controller_name="test_eurotherm",
        )

        server = _make_server(cfg, SignalStore(), endpoint=ep)

        # Converted from Eurotherm MTBF 8-24h
        assert server._drop_scheduler._cfg.frequency_per_hour[0] == pytest.approx(1.0 / 24.0)
        assert server._drop_scheduler._cfg.frequency_per_hour[1] == pytest.approx(1.0 / 8.0)
        assert server._drop_scheduler._cfg.duration_seconds == [5.0, 15.0]

    def test_realistic_mode_does_not_use_global_modbus_drop(self):
        """Endpoint config overrides global modbus_drop config."""
        d = _load_packaging().model_dump()
        # Set global drop to very high frequency
        d["data_quality"]["modbus_drop"] = {
            "enabled": True,
            "frequency_per_hour": [100.0, 200.0],
            "duration_seconds": [30.0, 60.0],
        }
        cfg = FactoryConfig.model_validate(d)

        # Endpoint with low frequency (long MTBF S7-1500)
        ep = _make_endpoint(
            port=5020, mtbf_min=72.0, mtbf_max=168.0,
            controller_type="S7-1500", controller_name="press_plc",
        )
        server = _make_server(cfg, SignalStore(), endpoint=ep)

        # Must NOT use global [100, 200]; must use S7-1500 MTBF-derived values
        assert server._drop_scheduler._cfg.frequency_per_hour[1] < 1.0
        assert server._drop_scheduler._cfg.frequency_per_hour[0] != pytest.approx(100.0)

    def test_collapsed_server_endpoint_is_none(self):
        """Collapsed server (no endpoint) has endpoint attribute as None."""
        server = _make_server(_load_packaging(), SignalStore())
        assert server.endpoint is None

    def test_realistic_server_endpoint_is_set(self):
        """Realistic server (endpoint provided) has endpoint attribute set."""
        ep = _make_endpoint(port=5020, mtbf_min=72.0, mtbf_max=168.0)
        server = _make_server(_load_packaging(), SignalStore(), endpoint=ep)
        assert server.endpoint is ep


# ---------------------------------------------------------------------------
# Independence: one controller dropping does not affect others
# ---------------------------------------------------------------------------


class TestDropIndependence:
    """One controller's drop scheduler does not affect other controllers."""

    def test_two_servers_have_independent_schedulers(self):
        """Two servers in realistic mode each own a separate CommDropScheduler."""
        cfg = _load_packaging()
        store = SignalStore()

        server_a = _make_server(
            cfg, store,
            endpoint=_make_endpoint(port=5020, mtbf_min=8.0, mtbf_max=24.0),
            comm_drop_rng_seed=1,
        )
        server_b = _make_server(
            cfg, store,
            endpoint=_make_endpoint(port=5021, mtbf_min=72.0, mtbf_max=168.0),
            comm_drop_rng_seed=2,
        )

        assert server_a._drop_scheduler is not server_b._drop_scheduler

    def test_two_servers_have_different_drop_configs(self):
        """Eurotherm and S7-1500 servers have different frequency configs."""
        cfg = _load_packaging()
        store = SignalStore()

        eurotherm_server = _make_server(
            cfg, store,
            endpoint=_make_endpoint(
                port=5031, mtbf_min=8.0, mtbf_max=24.0, controller_type="Eurotherm",
            ),
            comm_drop_rng_seed=1,
        )
        s7_server = _make_server(
            cfg, store,
            endpoint=_make_endpoint(
                port=5020, mtbf_min=72.0, mtbf_max=168.0, controller_type="S7-1500",
            ),
            comm_drop_rng_seed=2,
        )

        eur_max = eurotherm_server._drop_scheduler._cfg.frequency_per_hour[1]
        s7_max = s7_server._drop_scheduler._cfg.frequency_per_hour[1]
        assert eur_max > s7_max

    def test_drop_on_one_server_does_not_affect_other(self):
        """Forcing a drop on server_a does not affect server_b."""
        cfg = _load_packaging()
        store = SignalStore()

        server_a = _make_server(
            cfg, store,
            endpoint=_make_endpoint(port=5020, mtbf_min=8.0, mtbf_max=24.0),
            comm_drop_rng_seed=10,
        )
        server_b = _make_server(
            cfg, store,
            endpoint=_make_endpoint(port=5021, mtbf_min=72.0, mtbf_max=168.0),
            comm_drop_rng_seed=20,
        )

        # Force server_a into a drop by setting drop_end far in the future
        server_a._drop_scheduler._drop_end = time.monotonic() + 3600.0
        server_a._drop_scheduler._initialized = True

        now = time.monotonic()
        assert server_a._drop_scheduler.is_active(now)

        # server_b's scheduler is untouched
        server_b._drop_scheduler.update(now)
        assert not server_b._drop_scheduler.is_active(now)

    def test_comm_drop_active_property_independent(self):
        """comm_drop_active property is independent per server."""
        cfg = _load_packaging()
        store = SignalStore()

        server_a = _make_server(
            cfg, store,
            endpoint=_make_endpoint(port=5020, mtbf_min=8.0, mtbf_max=24.0),
            comm_drop_rng_seed=10,
        )
        server_b = _make_server(
            cfg, store,
            endpoint=_make_endpoint(port=5021, mtbf_min=72.0, mtbf_max=168.0),
            comm_drop_rng_seed=20,
        )

        # Force server_a drop
        server_a._drop_scheduler._drop_end = time.monotonic() + 3600.0
        server_a._drop_scheduler._initialized = True

        assert server_a.comm_drop_active is True
        assert server_b.comm_drop_active is False

    def test_three_servers_all_independent(self):
        """Three servers each have distinct schedulers and ordered frequencies."""
        cfg = _load_packaging()
        store = SignalStore()

        s1 = _make_server(
            cfg, store,
            endpoint=_make_endpoint(port=5020, mtbf_min=8.0, mtbf_max=24.0),
            comm_drop_rng_seed=1,
        )
        s2 = _make_server(
            cfg, store,
            endpoint=_make_endpoint(port=5021, mtbf_min=48.0, mtbf_max=72.0),
            comm_drop_rng_seed=2,
        )
        s3 = _make_server(
            cfg, store,
            endpoint=_make_endpoint(port=5022, mtbf_min=72.0, mtbf_max=168.0),
            comm_drop_rng_seed=3,
        )

        schedulers = [s1._drop_scheduler, s2._drop_scheduler, s3._drop_scheduler]
        # All three are distinct objects
        assert schedulers[0] is not schedulers[1]
        assert schedulers[1] is not schedulers[2]
        assert schedulers[0] is not schedulers[2]

        # Max frequencies are in descending order (shorter MTBF = higher freq)
        freqs = [s._cfg.frequency_per_hour[1] for s in schedulers]
        assert freqs[0] > freqs[1] > freqs[2]

    def test_drop_independence_with_deterministic_rng(self):
        """Drop state is not shared between servers even with same seed."""
        cfg = _load_packaging()
        store = SignalStore()

        # Same seed — but different SeedSequence children produce different streams
        server_a = _make_server(
            cfg, store,
            endpoint=_make_endpoint(port=5020, mtbf_min=8.0, mtbf_max=24.0),
            comm_drop_rng_seed=42,
        )
        server_b = _make_server(
            cfg, store,
            endpoint=_make_endpoint(port=5021, mtbf_min=8.0, mtbf_max=24.0),
            comm_drop_rng_seed=43,
        )

        # They share the same config shape but are distinct objects
        assert server_a._drop_scheduler is not server_b._drop_scheduler
        # Can manipulate one without affecting the other
        server_a._drop_scheduler._drop_end = time.monotonic() + 3600.0
        server_a._drop_scheduler._initialized = True
        assert server_b._drop_scheduler.is_active(time.monotonic()) is False


# ---------------------------------------------------------------------------
# DataEngine realistic mode: independent RNGs and drop configs
# ---------------------------------------------------------------------------


class TestDataEngineIndependentDrops:
    """DataEngine creates per-controller servers with independent drop configs."""

    def test_realistic_packaging_creates_three_servers(self):
        """DataEngine realistic mode (packaging): 3 Modbus servers."""
        d = _load_packaging().model_dump()
        d["network"] = {"mode": "realistic"}
        cfg = FactoryConfig.model_validate(d)

        engine = DataEngine(cfg, SignalStore())
        engine._topology = NetworkTopologyManager(cfg.network, profile="packaging")

        servers = engine.create_modbus_servers()
        assert len(servers) == 3

    def test_realistic_packaging_servers_have_independent_schedulers(self):
        """DataEngine realistic packaging: each server has its own CommDropScheduler."""
        d = _load_packaging().model_dump()
        d["network"] = {"mode": "realistic"}
        cfg = FactoryConfig.model_validate(d)

        engine = DataEngine(cfg, SignalStore())
        engine._topology = NetworkTopologyManager(cfg.network, profile="packaging")

        servers = engine.create_modbus_servers()
        schedulers = [s._drop_scheduler for s in servers]

        assert schedulers[0] is not schedulers[1]
        assert schedulers[1] is not schedulers[2]
        assert schedulers[0] is not schedulers[2]

    def test_realistic_packaging_press_uses_s7_1500_drop_rate(self):
        """Press endpoint (S7-1500) has low drop frequency (MTBF 72-168h)."""
        d = _load_packaging().model_dump()
        d["network"] = {"mode": "realistic"}
        cfg = FactoryConfig.model_validate(d)

        engine = DataEngine(cfg, SignalStore())
        engine._topology = NetworkTopologyManager(cfg.network, profile="packaging")

        servers = engine.create_modbus_servers()
        # Port 5020 = press PLC (S7-1500), MTBF 72-168h
        press_server = next(s for s in servers if s.port == 5020)

        # S7-1500: max freq = 1/72 ≈ 0.014/h
        max_freq = press_server._drop_scheduler._cfg.frequency_per_hour[1]
        assert max_freq == pytest.approx(1.0 / 72.0)

    def test_realistic_packaging_laminator_uses_s7_1200_drop_rate(self):
        """Laminator endpoint (S7-1200) has correct drop frequency (MTBF 48-168h)."""
        d = _load_packaging().model_dump()
        d["network"] = {"mode": "realistic"}
        cfg = FactoryConfig.model_validate(d)

        engine = DataEngine(cfg, SignalStore())
        engine._topology = NetworkTopologyManager(cfg.network, profile="packaging")

        servers = engine.create_modbus_servers()
        # Port 5021 = laminator PLC (S7-1200), MTBF 48-168h
        lam_server = next(s for s in servers if s.port == 5021)

        # S7-1200: max freq = 1/48 ≈ 0.021/h
        max_freq = lam_server._drop_scheduler._cfg.frequency_per_hour[1]
        assert max_freq == pytest.approx(1.0 / 48.0)

    def test_realistic_foodbev_creates_six_servers(self):
        """DataEngine realistic mode (F&B): 6 Modbus servers."""
        d = _load_foodbev().model_dump()
        d["network"] = {"mode": "realistic"}
        cfg = FactoryConfig.model_validate(d)

        engine = DataEngine(cfg, SignalStore())
        engine._topology = NetworkTopologyManager(cfg.network, profile="food_bev")

        servers = engine.create_modbus_servers()
        assert len(servers) == 6

    def test_realistic_foodbev_oven_uses_eurotherm_drop_rate(self):
        """Oven gateway (Eurotherm) has high drop frequency (MTBF 8-24h)."""
        d = _load_foodbev().model_dump()
        d["network"] = {"mode": "realistic"}
        cfg = FactoryConfig.model_validate(d)

        engine = DataEngine(cfg, SignalStore())
        engine._topology = NetworkTopologyManager(cfg.network, profile="food_bev")

        servers = engine.create_modbus_servers()
        # Port 5031 = oven gateway (Eurotherm), MTBF 8-24h
        oven_server = next(s for s in servers if s.port == 5031)

        # Eurotherm: max freq = 1/8 = 0.125/h
        max_freq = oven_server._drop_scheduler._cfg.frequency_per_hour[1]
        assert max_freq == pytest.approx(1.0 / 8.0)

    def test_realistic_foodbev_chiller_uses_danfoss_drop_rate(self):
        """Chiller (Danfoss) has correct drop frequency (MTBF 24-48h)."""
        d = _load_foodbev().model_dump()
        d["network"] = {"mode": "realistic"}
        cfg = FactoryConfig.model_validate(d)

        engine = DataEngine(cfg, SignalStore())
        engine._topology = NetworkTopologyManager(cfg.network, profile="food_bev")

        servers = engine.create_modbus_servers()
        # Port 5034 = chiller (Danfoss), MTBF 24-48h
        chiller_server = next(s for s in servers if s.port == 5034)

        # Danfoss: max freq = 1/24 ≈ 0.042/h
        max_freq = chiller_server._drop_scheduler._cfg.frequency_per_hour[1]
        assert max_freq == pytest.approx(1.0 / 24.0)

    def test_realistic_foodbev_servers_all_independent(self):
        """F&B realistic mode: 6 servers each with distinct schedulers."""
        d = _load_foodbev().model_dump()
        d["network"] = {"mode": "realistic"}
        cfg = FactoryConfig.model_validate(d)

        engine = DataEngine(cfg, SignalStore())
        engine._topology = NetworkTopologyManager(cfg.network, profile="food_bev")

        servers = engine.create_modbus_servers()
        assert len(servers) == 6

        schedulers = [s._drop_scheduler for s in servers]
        for i, a in enumerate(schedulers):
            for j, b in enumerate(schedulers):
                if i != j:
                    assert a is not b, f"Schedulers {i} and {j} are the same object"

    def test_collapsed_mode_uses_global_config(self):
        """Collapsed mode: single server uses config.data_quality.modbus_drop."""
        cfg = _load_packaging()
        engine = DataEngine(cfg, SignalStore())

        servers = engine.create_modbus_servers()
        assert len(servers) == 1

        server = servers[0]
        assert server._drop_scheduler._cfg is cfg.data_quality.modbus_drop

    def test_rng_isolation_realistic_mode(self):
        """Each server in realistic mode gets its own RNG instance."""
        d = _load_packaging().model_dump()
        d["network"] = {"mode": "realistic"}
        cfg = FactoryConfig.model_validate(d)

        engine = DataEngine(cfg, SignalStore())
        engine._topology = NetworkTopologyManager(cfg.network, profile="packaging")

        servers = engine.create_modbus_servers()
        rngs = [s._drop_scheduler._rng for s in servers]

        assert rngs[0] is not rngs[1]
        assert rngs[1] is not rngs[2]
        assert rngs[0] is not rngs[2]

    def test_oven_drops_more_frequently_than_press(self):
        """F&B oven (Eurotherm) drops more often than press-equivalent (S7-1500)."""
        # Use packaging to get two servers with different types for comparison
        d = _load_packaging().model_dump()
        d["network"] = {"mode": "realistic"}
        cfg = FactoryConfig.model_validate(d)

        engine = DataEngine(cfg, SignalStore())
        engine._topology = NetworkTopologyManager(cfg.network, profile="packaging")

        servers = engine.create_modbus_servers()
        press_server = next(s for s in servers if s.port == 5020)   # S7-1500
        lam_server = next(s for s in servers if s.port == 5021)     # S7-1200

        # S7-1200 (laminator) has shorter MTBF than S7-1500 (press) → higher freq
        press_max = press_server._drop_scheduler._cfg.frequency_per_hour[1]
        lam_max = lam_server._drop_scheduler._cfg.frequency_per_hour[1]
        assert lam_max > press_max


# ---------------------------------------------------------------------------
# Backward compatibility: collapsed mode is unchanged
# ---------------------------------------------------------------------------


class TestCollapsedModeBackwardCompat:
    """Collapsed mode behaviour must be exactly as before task 5.5."""

    def test_collapsed_server_drop_scheduler_is_from_global_config(self):
        """No endpoint → scheduler config matches config.data_quality.modbus_drop."""
        cfg = _load_packaging()
        server = _make_server(cfg, SignalStore())
        assert server._drop_scheduler._cfg is cfg.data_quality.modbus_drop

    def test_collapsed_server_has_no_endpoint(self):
        """Collapsed server (no endpoint arg) has endpoint = None."""
        server = _make_server(_load_packaging(), SignalStore())
        assert server.endpoint is None

    def test_collapsed_drop_disabled_means_never_active(self):
        """With modbus_drop.enabled=False, comm drop is never active."""
        d = _load_packaging().model_dump()
        d["data_quality"]["modbus_drop"]["enabled"] = False
        cfg = FactoryConfig.model_validate(d)

        server = _make_server(cfg, SignalStore())

        now = time.monotonic()
        for _ in range(5):
            server._drop_scheduler.update(now)
        assert not server._drop_scheduler.is_active(now)

    def test_collapsed_mode_only_one_server(self):
        """DataEngine collapsed mode returns exactly one Modbus server."""
        cfg = _load_packaging()
        engine = DataEngine(cfg, SignalStore())
        servers = engine.create_modbus_servers()
        assert len(servers) == 1

    def test_collapsed_foodbev_only_one_server(self):
        """DataEngine collapsed mode (F&B) also returns exactly one Modbus server."""
        cfg = _load_foodbev()
        engine = DataEngine(cfg, SignalStore())
        servers = engine.create_modbus_servers()
        assert len(servers) == 1
