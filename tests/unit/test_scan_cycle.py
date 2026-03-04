"""Tests for scan cycle quantisation and phase jitter (task 5.4).

Verifies:
- ScanCycleModel basic operation: values update at boundary, stale between
- Phase jitter: actual cycle varies within [cycle_ms, cycle_ms*(1+jitter_pct)]
- First prepare_tick always crosses boundary (next_boundary starts at 0)
- Multiple signals tracked independently with per-signal cache
- Integration with ModbusServer in realistic mode (HR and IR quantisation)
- Collapsed mode: no quantisation (direct passthrough, scan_cycle_model=None)
- DataEngine creates ScanCycleModel per endpoint in realistic mode
- Secondary slave (Eurotherm) IR values are also quantised

PRD Reference: Section 3a.8
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from factory_simulator.config import (
    NetworkConfig,
    ScanCycleConfig,
    load_config,
)
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.protocols.modbus_server import ModbusServer
from factory_simulator.store import SignalStore
from factory_simulator.topology import (
    ModbusEndpointSpec,
    NetworkTopologyManager,
    ScanCycleModel,
)

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
_PACKAGING_CONFIG = _CONFIG_DIR / "factory.yaml"
_FOODBEV_CONFIG = _CONFIG_DIR / "factory-foodbev.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scan_model(
    cycle_ms: float = 10.0,
    jitter_pct: float = 0.0,
    seed: int = 0,
) -> ScanCycleModel:
    """Create a ScanCycleModel with deterministic RNG."""
    cfg = ScanCycleConfig(cycle_ms=cycle_ms, jitter_pct=jitter_pct)
    rng = np.random.default_rng(seed)
    return ScanCycleModel(cfg, rng)


def _make_server_with_scan(
    config_path: Path,
    endpoint: ModbusEndpointSpec,
    seed: int = 42,
) -> tuple[ModbusServer, SignalStore]:
    """Create a ModbusServer with scan cycle model and deterministic RNGs."""
    from factory_simulator.config import load_config

    config = load_config(config_path)
    store = SignalStore()
    rng = np.random.default_rng(seed)
    scan_cfg = endpoint.scan_cycle
    scan_model = ScanCycleModel(scan_cfg, np.random.default_rng(seed + 1))
    server = ModbusServer(
        config,
        store,
        port=0,
        endpoint=endpoint,
        comm_drop_rng=rng,
        exception_rng=rng,
        duplicate_rng=None,
        scan_cycle_model=scan_model,
    )
    return server, store


# ---------------------------------------------------------------------------
# ScanCycleModel unit tests
# ---------------------------------------------------------------------------


class TestScanCycleModelBasic:
    """Basic ScanCycleModel operation."""

    def test_first_tick_always_active(self) -> None:
        """First prepare_tick always crosses the boundary (starts at 0)."""
        model = _make_scan_model(cycle_ms=10.0)
        model.prepare_tick(0.0)
        assert model.scan_active is True

    def test_scan_active_at_boundary(self) -> None:
        """scan_active is True when sim_time_ms >= next_boundary_ms."""
        model = _make_scan_model(cycle_ms=10.0, jitter_pct=0.0)
        model.prepare_tick(0.0)  # boundary advances to 10ms
        assert model.scan_active is True

    def test_stale_between_boundaries(self) -> None:
        """scan_active is False when sim_time_ms < next_boundary_ms."""
        model = _make_scan_model(cycle_ms=10.0, jitter_pct=0.0)
        model.prepare_tick(0.0)   # crosses: next_boundary = 10ms
        model.prepare_tick(0.005)  # sim_time=5ms < 10ms → stale
        assert model.scan_active is False

    def test_active_again_at_next_boundary(self) -> None:
        """scan_active returns True when second boundary is crossed."""
        model = _make_scan_model(cycle_ms=10.0, jitter_pct=0.0)
        model.prepare_tick(0.0)    # crosses: next_boundary = 10ms
        model.prepare_tick(0.005)  # stale
        model.prepare_tick(0.010)  # crosses: next_boundary = 20ms
        assert model.scan_active is True

    def test_boundary_advances_per_cycle(self) -> None:
        """next_boundary_ms advances by cycle_ms per crossing (no jitter)."""
        model = _make_scan_model(cycle_ms=20.0, jitter_pct=0.0)
        model.prepare_tick(0.0)  # crosses: next_boundary = 20ms
        assert model.next_boundary_ms == pytest.approx(20.0)
        model.prepare_tick(0.020)  # crosses: next_boundary = 40ms
        assert model.next_boundary_ms == pytest.approx(40.0)


class TestScanCycleModelGetValue:
    """ScanCycleModel.get_value returns correct quantised values."""

    def test_active_returns_current_value(self) -> None:
        """When scan active, get_value returns the current (fresh) value."""
        model = _make_scan_model()
        model.prepare_tick(0.0)  # active
        result = model.get_value("sig.speed", 42.5)
        assert result == pytest.approx(42.5)

    def test_stale_returns_last_scan_output(self) -> None:
        """When scan inactive, get_value returns the cached stale value."""
        model = _make_scan_model(cycle_ms=10.0, jitter_pct=0.0)
        model.prepare_tick(0.0)       # active
        model.get_value("sig.speed", 100.0)  # cached = 100.0
        model.prepare_tick(0.005)     # stale (5ms < 10ms boundary)
        result = model.get_value("sig.speed", 999.0)  # fresh=999 but stale
        assert result == pytest.approx(100.0)

    def test_stale_default_to_current_if_no_cache(self) -> None:
        """First stale read with no prior active scan returns current_value."""
        cfg = ScanCycleConfig(cycle_ms=10.0, jitter_pct=0.0)
        rng = np.random.default_rng(0)
        model = ScanCycleModel(cfg, rng)
        # Manually move the boundary past 0 without calling prepare_tick
        # to test edge case: empty cache on stale read.
        # Use a non-zero starting boundary by calling twice to exhaust first.
        model.prepare_tick(0.0)      # active at 0ms
        model.prepare_tick(0.001)    # stale
        # Signal never seen before in cache
        result = model.get_value("new.signal", 77.0)
        assert result == pytest.approx(77.0)

    def test_multiple_signals_tracked_independently(self) -> None:
        """Each signal has its own cache entry."""
        model = _make_scan_model(cycle_ms=10.0, jitter_pct=0.0)
        model.prepare_tick(0.0)              # active
        model.get_value("sig.a", 1.0)        # cache a=1.0
        model.get_value("sig.b", 2.0)        # cache b=2.0
        model.prepare_tick(0.005)            # stale
        assert model.get_value("sig.a", 99.0) == pytest.approx(1.0)
        assert model.get_value("sig.b", 99.0) == pytest.approx(2.0)

    def test_cache_updates_on_each_active_scan(self) -> None:
        """Cache updates to the new value on each active scan crossing."""
        model = _make_scan_model(cycle_ms=10.0, jitter_pct=0.0)
        model.prepare_tick(0.0)       # active
        model.get_value("sig.x", 10.0)  # cache = 10.0
        model.prepare_tick(0.010)     # active again
        model.get_value("sig.x", 20.0)  # cache updates to 20.0
        model.prepare_tick(0.015)     # stale
        assert model.get_value("sig.x", 99.0) == pytest.approx(20.0)


class TestScanCycleModelJitter:
    """Phase jitter: actual cycle varies within [cycle_ms, cycle_ms*(1+jitter)]."""

    def test_zero_jitter_exact_cycle(self) -> None:
        """With jitter_pct=0, cycle is exactly cycle_ms each time."""
        model = _make_scan_model(cycle_ms=10.0, jitter_pct=0.0)
        boundaries = []
        for i in range(5):
            model.prepare_tick(i * 0.010)
            boundaries.append(model.next_boundary_ms)
        # boundaries should be 10, 20, 30, 40, 50 (all exact)
        for j, b in enumerate(boundaries, 1):
            assert b == pytest.approx(j * 10.0)

    def test_jitter_varies_cycle_within_range(self) -> None:
        """With jitter_pct=0.1, actual cycle is in [cycle_ms, cycle_ms*1.1]."""
        cycle_ms = 100.0
        jitter_pct = 0.1
        cfg = ScanCycleConfig(cycle_ms=cycle_ms, jitter_pct=jitter_pct)
        rng = np.random.default_rng(12345)
        model = ScanCycleModel(cfg, rng)

        prev_boundary = 0.0
        for _ in range(20):
            # Advance to just past current boundary
            model.prepare_tick(model.next_boundary_ms / 1000.0)
            actual_cycle = model.next_boundary_ms - prev_boundary
            prev_boundary = model.next_boundary_ms
            assert cycle_ms <= actual_cycle <= cycle_ms * (1.0 + jitter_pct) + 1e-9

    def test_jitter_deterministic_with_seed(self) -> None:
        """Same seed produces identical jitter sequence."""
        def _run(seed: int) -> list[float]:
            cfg = ScanCycleConfig(cycle_ms=10.0, jitter_pct=0.05)
            rng = np.random.default_rng(seed)
            model = ScanCycleModel(cfg, rng)
            boundaries = []
            for i in range(10):
                model.prepare_tick(i * 0.010)
                boundaries.append(model.next_boundary_ms)
            return boundaries

        assert _run(7) == _run(7)
        assert _run(7) != _run(8)


class TestScanCyclePerControllerDefaults:
    """PRD 3a.8 per-controller scan cycle defaults in topology."""

    @pytest.mark.parametrize(
        "controller_type, expected_cycle_ms, expected_jitter_pct",
        [
            ("S7-1500", 10.0, 0.05),
            ("S7-1200", 20.0, 0.08),
            ("CompactLogix", 15.0, 0.06),
            ("Eurotherm", 100.0, 0.10),
            ("Danfoss", 100.0, 0.10),
        ],
    )
    def test_default_scan_config(
        self,
        controller_type: str,
        expected_cycle_ms: float,
        expected_jitter_pct: float,
    ) -> None:
        """Each controller type gets correct default scan cycle config."""
        from factory_simulator.topology import _DEFAULT_SCAN_CYCLE

        cfg = _DEFAULT_SCAN_CYCLE[controller_type]
        assert cfg.cycle_ms == pytest.approx(expected_cycle_ms)
        assert cfg.jitter_pct == pytest.approx(expected_jitter_pct)

    def test_packaging_press_plc_scan_cycle(self) -> None:
        """Press PLC (S7-1500) endpoint has 10ms scan cycle."""
        mgr = NetworkTopologyManager(
            config=NetworkConfig(mode="realistic"),
            profile="packaging",
        )
        endpoints = mgr.modbus_endpoints()
        press_ep = next(ep for ep in endpoints if ep.port == 5020)
        assert press_ep.scan_cycle.cycle_ms == pytest.approx(10.0)
        assert press_ep.scan_cycle.jitter_pct == pytest.approx(0.05)

    def test_foodbev_oven_gateway_scan_cycle(self) -> None:
        """Oven gateway (Eurotherm) endpoint has 100ms scan cycle."""
        mgr = NetworkTopologyManager(
            config=NetworkConfig(mode="realistic"),
            profile="food_bev",
        )
        endpoints = mgr.modbus_endpoints()
        oven_ep = next(ep for ep in endpoints if ep.port == 5031)
        assert oven_ep.scan_cycle.cycle_ms == pytest.approx(100.0)
        assert oven_ep.scan_cycle.jitter_pct == pytest.approx(0.10)

    def test_foodbev_chiller_scan_cycle(self) -> None:
        """Chiller (Danfoss) endpoint has 100ms scan cycle."""
        mgr = NetworkTopologyManager(
            config=NetworkConfig(mode="realistic"),
            profile="food_bev",
        )
        endpoints = mgr.modbus_endpoints()
        chiller_ep = next(ep for ep in endpoints if ep.port == 5034)
        assert chiller_ep.scan_cycle.cycle_ms == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# ModbusServer integration with ScanCycleModel
# ---------------------------------------------------------------------------


class TestModbusServerScanCycleIntegration:
    """ScanCycleModel wired into ModbusServer register sync."""

    def _make_server(
        self,
        with_scan_model: bool = True,
        cycle_ms: float = 10.0,
        jitter_pct: float = 0.0,
    ) -> tuple[ModbusServer, SignalStore]:
        """Helper: packaging config server with optional scan model."""
        config = load_config(_PACKAGING_CONFIG)
        store = SignalStore()
        rng = np.random.default_rng(0)

        scan_model: ScanCycleModel | None = None
        if with_scan_model:
            cfg = ScanCycleConfig(cycle_ms=cycle_ms, jitter_pct=jitter_pct)
            scan_model = ScanCycleModel(cfg, np.random.default_rng(1))

        server = ModbusServer(
            config,
            store,
            port=0,
            comm_drop_rng=rng,
            exception_rng=rng,
            duplicate_rng=None,
            scan_cycle_model=scan_model,
        )
        return server, store

    def _read_hr_float32(self, server: ModbusServer, address: int) -> float:
        """Read a float32 HR value back from the data block."""
        from factory_simulator.protocols.modbus_server import decode_float32_abcd

        block = server._hr_block
        addr = address + 1  # +1: ModbusDeviceContext offset
        raw = block.getValues(addr, 2)
        return decode_float32_abcd(list(raw))  # type: ignore[arg-type]

    def _find_press_line_speed_hr_address(self, server: ModbusServer) -> int | None:
        """Find the HR address for press.line_speed."""
        for entry in server.register_map.hr_entries:
            if entry.signal_id == "press.line_speed":
                return entry.address
        return None

    def test_no_scan_model_direct_passthrough(self) -> None:
        """Collapsed mode (no scan model): register always reflects store value."""
        server, store = self._make_server(with_scan_model=False)
        addr = self._find_press_line_speed_hr_address(server)
        assert addr is not None

        store.set("press.line_speed", 100.0, 0.0)
        server.sync_registers(0.0)
        v1 = self._read_hr_float32(server, addr)

        store.set("press.line_speed", 200.0, 0.05)
        server.sync_registers(0.05)  # sim_time=50ms, still within first scan if model existed
        v2 = self._read_hr_float32(server, addr)

        # Without scan model: always passes through
        assert v1 == pytest.approx(100.0, abs=0.5)
        assert v2 == pytest.approx(200.0, abs=0.5)

    def test_with_scan_model_first_tick_active(self) -> None:
        """First sync_registers call always writes fresh value (boundary at 0)."""
        server, store = self._make_server(cycle_ms=50.0, jitter_pct=0.0)
        addr = self._find_press_line_speed_hr_address(server)
        assert addr is not None

        store.set("press.line_speed", 150.0, 0.0)
        server.sync_registers(0.0)  # sim_time=0ms >= boundary=0ms → active
        v = self._read_hr_float32(server, addr)
        assert v == pytest.approx(150.0, abs=0.5)

    def test_stale_value_between_boundaries(self) -> None:
        """Between scan boundaries, register holds stale value."""
        server, store = self._make_server(cycle_ms=50.0, jitter_pct=0.0)
        addr = self._find_press_line_speed_hr_address(server)
        assert addr is not None

        # First sync: boundary 0ms → active, cache = 100.0
        store.set("press.line_speed", 100.0, 0.0)
        server.sync_registers(0.0)  # boundary advances to 50ms

        # Second sync: sim_time=10ms < 50ms → stale
        store.set("press.line_speed", 999.0, 0.01)
        server.sync_registers(0.01)  # 10ms < 50ms → stale
        v_stale = self._read_hr_float32(server, addr)
        assert v_stale == pytest.approx(100.0, abs=0.5)

    def test_fresh_value_at_next_boundary(self) -> None:
        """At next boundary crossing, register updates to fresh value."""
        server, store = self._make_server(cycle_ms=50.0, jitter_pct=0.0)
        addr = self._find_press_line_speed_hr_address(server)
        assert addr is not None

        # First sync: cache = 100
        store.set("press.line_speed", 100.0, 0.0)
        server.sync_registers(0.0)   # boundary→50ms

        # Stale: sim=10ms
        store.set("press.line_speed", 200.0, 0.01)
        server.sync_registers(0.01)  # 10ms < 50ms → stale

        # Active: sim=50ms >= 50ms boundary
        store.set("press.line_speed", 200.0, 0.05)
        server.sync_registers(0.05)  # 50ms >= 50ms → active
        v_fresh = self._read_hr_float32(server, addr)
        assert v_fresh == pytest.approx(200.0, abs=0.5)

    def test_scan_model_property_is_set(self) -> None:
        """scan_cycle_model is stored on the server instance."""
        server, _ = self._make_server(with_scan_model=True)
        assert server._scan_cycle_model is not None

    def test_no_scan_model_attribute_is_none(self) -> None:
        """Collapsed mode: _scan_cycle_model is None."""
        server, _ = self._make_server(with_scan_model=False)
        assert server._scan_cycle_model is None

    def test_sync_registers_accepts_sim_time_parameter(self) -> None:
        """sync_registers(sim_time=X) does not raise."""
        server, store = self._make_server(with_scan_model=False)
        store.set("press.line_speed", 50.0, 0.0)
        server.sync_registers(0.0)   # explicit sim_time
        server.sync_registers()      # default sim_time=0.0
        server.sync_registers(1.5)   # another explicit value


# ---------------------------------------------------------------------------
# DataEngine creates ScanCycleModel per endpoint
# ---------------------------------------------------------------------------


class TestDataEngineScanCycleCreation:
    """DataEngine.create_modbus_servers wires ScanCycleModel in realistic mode."""

    def test_collapsed_mode_no_scan_models(self) -> None:
        """Collapsed mode: single server, no scan cycle model."""
        from factory_simulator.clock import SimulationClock

        config = load_config(_PACKAGING_CONFIG)
        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)
        servers = engine.create_modbus_servers()
        assert len(servers) == 1
        assert servers[0]._scan_cycle_model is None

    def test_realistic_mode_packaging_has_scan_models(self) -> None:
        """Realistic mode (packaging): 3 servers, each with a ScanCycleModel."""
        from factory_simulator.clock import SimulationClock

        config = load_config(_PACKAGING_CONFIG)
        config = config.model_copy(
            update={"network": NetworkConfig(mode="realistic")}
        )
        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        topology = NetworkTopologyManager(config.network, profile="packaging")
        engine = DataEngine(config, store, clock, topology=topology)
        servers = engine.create_modbus_servers()
        assert len(servers) == 3
        for server in servers:
            assert server._scan_cycle_model is not None

    def test_realistic_mode_foodbev_has_scan_models(self) -> None:
        """Realistic mode (F&B): 6 servers, each with a ScanCycleModel."""
        from factory_simulator.clock import SimulationClock

        config = load_config(_FOODBEV_CONFIG)
        config = config.model_copy(
            update={"network": NetworkConfig(mode="realistic")}
        )
        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        topology = NetworkTopologyManager(config.network, profile="food_bev")
        engine = DataEngine(config, store, clock, topology=topology)
        servers = engine.create_modbus_servers()
        assert len(servers) == 6
        for server in servers:
            assert server._scan_cycle_model is not None

    def test_each_server_has_independent_scan_model(self) -> None:
        """Each server's scan model is a distinct instance."""
        from factory_simulator.clock import SimulationClock

        config = load_config(_PACKAGING_CONFIG)
        config = config.model_copy(
            update={"network": NetworkConfig(mode="realistic")}
        )
        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        topology = NetworkTopologyManager(config.network, profile="packaging")
        engine = DataEngine(config, store, clock, topology=topology)
        servers = engine.create_modbus_servers()
        models = [s._scan_cycle_model for s in servers]
        # All should be distinct objects
        for i, m in enumerate(models):
            for j, other in enumerate(models):
                if i != j:
                    assert m is not other

    def test_press_plc_scan_model_cycle_ms(self) -> None:
        """Press PLC server (S7-1500) has 10ms scan cycle model."""
        from factory_simulator.clock import SimulationClock

        config = load_config(_PACKAGING_CONFIG)
        config = config.model_copy(
            update={"network": NetworkConfig(mode="realistic")}
        )
        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        topology = NetworkTopologyManager(config.network, profile="packaging")
        engine = DataEngine(config, store, clock, topology=topology)
        servers = engine.create_modbus_servers()
        # Port 5020 = press (S7-1500)
        press_server = next(s for s in servers if s.port == 5020)
        model = press_server._scan_cycle_model
        assert model is not None
        assert model._cycle_ms == pytest.approx(10.0)

    def test_laminator_scan_model_cycle_ms(self) -> None:
        """Laminator server (S7-1200) has 20ms scan cycle model."""
        from factory_simulator.clock import SimulationClock

        config = load_config(_PACKAGING_CONFIG)
        config = config.model_copy(
            update={"network": NetworkConfig(mode="realistic")}
        )
        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        topology = NetworkTopologyManager(config.network, profile="packaging")
        engine = DataEngine(config, store, clock, topology=topology)
        servers = engine.create_modbus_servers()
        # Port 5021 = laminator (S7-1200)
        lam_server = next(s for s in servers if s.port == 5021)
        model = lam_server._scan_cycle_model
        assert model is not None
        assert model._cycle_ms == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Stale read produces constant output between boundaries (integration)
# ---------------------------------------------------------------------------


class TestStaleReadBehaviour:
    """Multiple consecutive stale reads return the same cached value."""

    def test_consecutive_stale_reads_identical(self) -> None:
        """Multiple stale sync calls produce identical register values."""
        config = load_config(_PACKAGING_CONFIG)
        store = SignalStore()
        rng = np.random.default_rng(0)
        cfg = ScanCycleConfig(cycle_ms=100.0, jitter_pct=0.0)
        scan_model = ScanCycleModel(cfg, np.random.default_rng(1))

        server = ModbusServer(
            config,
            store,
            port=0,
            comm_drop_rng=rng,
            exception_rng=rng,
            duplicate_rng=None,
            scan_cycle_model=scan_model,
        )

        # Find press.line_speed HR address
        addr = None
        for entry in server.register_map.hr_entries:
            if entry.signal_id == "press.line_speed":
                addr = entry.address
                break
        assert addr is not None

        # Active: write 50.0 at sim_time=0
        store.set("press.line_speed", 50.0, 0.0)
        server.sync_registers(0.0)  # boundary → 100ms

        # Now advance store but stay stale (10ms, 20ms, 30ms)
        for t_ms in [10, 20, 30, 40, 50]:
            store.set("press.line_speed", 999.0, t_ms / 1000.0)
            server.sync_registers(t_ms / 1000.0)
            raw = server._hr_block.getValues(addr + 1, 2)
            from factory_simulator.protocols.modbus_server import decode_float32_abcd
            v = decode_float32_abcd(list(raw))  # type: ignore[arg-type]
            assert v == pytest.approx(50.0, abs=0.5), (
                f"At sim_time={t_ms}ms expected stale=50.0, got {v}"
            )

    def test_update_occurs_at_boundary(self) -> None:
        """Value updates precisely at the 100ms boundary crossing."""
        config = load_config(_PACKAGING_CONFIG)
        store = SignalStore()
        rng = np.random.default_rng(0)
        cfg = ScanCycleConfig(cycle_ms=100.0, jitter_pct=0.0)
        scan_model = ScanCycleModel(cfg, np.random.default_rng(1))

        server = ModbusServer(
            config,
            store,
            port=0,
            comm_drop_rng=rng,
            exception_rng=rng,
            duplicate_rng=None,
            scan_cycle_model=scan_model,
        )

        addr = None
        for entry in server.register_map.hr_entries:
            if entry.signal_id == "press.line_speed":
                addr = entry.address
                break
        assert addr is not None

        from factory_simulator.protocols.modbus_server import decode_float32_abcd

        # Initial active sync
        store.set("press.line_speed", 10.0, 0.0)
        server.sync_registers(0.0)  # → boundary=100ms

        # Just before boundary (99ms): stale
        store.set("press.line_speed", 20.0, 0.099)
        server.sync_registers(0.099)
        raw = server._hr_block.getValues(addr + 1, 2)
        v_before = decode_float32_abcd(list(raw))  # type: ignore[arg-type]
        assert v_before == pytest.approx(10.0, abs=0.5)

        # At boundary (100ms): fresh
        store.set("press.line_speed", 20.0, 0.100)
        server.sync_registers(0.100)
        raw = server._hr_block.getValues(addr + 1, 2)
        v_at = decode_float32_abcd(list(raw))  # type: ignore[arg-type]
        assert v_at == pytest.approx(20.0, abs=0.5)
