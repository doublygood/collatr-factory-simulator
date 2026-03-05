"""Network topology manager for multi-controller simulation.

Resolves logical controller endpoints to simulator port bindings in both
collapsed (single port per protocol) and realistic (per-controller ports)
modes.

Also provides:
- :class:`ClockDriftModel` for per-controller timestamp drift in OPC-UA
  SourceTimestamp and MQTT JSON payloads.
- :class:`ScanCycleModel` for PLC scan cycle quantisation of Modbus register
  values (PRD 3a.8).

PRD Reference: Section 3a.4, 3a.5 (clock drift), 3a.8 (scan cycle)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from factory_simulator.config import (
    ClockDriftConfig,
    ConnectionDropConfig,
    ConnectionLimitConfig,
    NetworkConfig,
    ScanCycleConfig,
)

# ---------------------------------------------------------------------------
# Endpoint spec dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModbusEndpointSpec:
    """Specification for a single Modbus TCP server endpoint."""

    port: int
    unit_ids: list[int] = field(default_factory=lambda: [1])
    register_range: tuple[int, int] | None = None  # (start, end) inclusive; None = all
    byte_order: str = "ABCD"
    controller_type: str = "generic"
    controller_name: str = ""
    equipment_ids: list[str] = field(default_factory=list)
    uid_equipment_map: dict[int, list[str]] = field(default_factory=dict)
    # Remaps secondary slave IDs to realistic-mode UIDs.  In realistic mode,
    # a key slave_id is exposed as the value UID instead of the original
    # slave_id.  Empty dict (default) preserves collapsed-mode behaviour where
    # secondary slaves are accessible under their configured slave_ids.
    # Example: {11: 1, 12: 2, 13: 3} maps Eurotherm zone controllers
    # (slave_ids 11-13) to UIDs 1-3 per PRD 03a oven gateway topology.
    secondary_uid_remap: dict[int, int] = field(default_factory=dict)
    # Signal ID to monitor for state transitions that trigger 0x06 exceptions.
    # None disables 0x06 injection for endpoints without a suitable state signal.
    state_signal_id: str | None = None
    clock_drift: ClockDriftConfig = field(default_factory=ClockDriftConfig)
    scan_cycle: ScanCycleConfig = field(default_factory=ScanCycleConfig)
    connection_limit: ConnectionLimitConfig = field(
        default_factory=ConnectionLimitConfig
    )
    connection_drop: ConnectionDropConfig = field(default_factory=ConnectionDropConfig)


@dataclass(frozen=True)
class OpcuaEndpointSpec:
    """Specification for a single OPC-UA server endpoint."""

    port: int
    node_tree_root: str = ""
    controller_type: str = "generic"
    controller_name: str = ""
    clock_drift: ClockDriftConfig = field(default_factory=ClockDriftConfig)
    connection_limit: ConnectionLimitConfig = field(
        default_factory=ConnectionLimitConfig
    )
    connection_drop: ConnectionDropConfig = field(default_factory=ConnectionDropConfig)


@dataclass(frozen=True)
class MqttEndpointSpec:
    """Specification for the MQTT broker endpoint (shared across profiles)."""

    broker_host: str = "mqtt-broker"
    broker_port: int = 1883
    clock_drift: ClockDriftConfig = field(default_factory=ClockDriftConfig)


# ---------------------------------------------------------------------------
# Clock drift model (PRD 3a.5)
# ---------------------------------------------------------------------------


class ClockDriftModel:
    """Per-controller clock drift for OPC-UA SourceTimestamp and MQTT payloads.

    Formula (PRD 3a.5):
        ``drifted_time = sim_time + initial_offset_ms/1000
                         + drift_rate_s_per_day * elapsed_hours / 24``

    Ground truth always uses true ``sim_time``, never drifted time.

    Parameters
    ----------
    config:
        :class:`ClockDriftConfig` with initial offset and daily drift rate.
    """

    def __init__(self, config: ClockDriftConfig) -> None:
        self._initial_offset_s = config.initial_offset_ms / 1000.0
        self._drift_rate_s_per_day = config.drift_rate_s_per_day

    @property
    def initial_offset_s(self) -> float:
        """Initial clock offset in seconds."""
        return self._initial_offset_s

    @property
    def drift_rate_s_per_day(self) -> float:
        """Clock drift rate in seconds per day."""
        return self._drift_rate_s_per_day

    def drifted_time(self, sim_time: float) -> float:
        """Return the drifted timestamp for a given sim_time.

        Parameters
        ----------
        sim_time:
            Simulation time in seconds from start (0.0 at start).

        Returns
        -------
        float
            Drifted timestamp in seconds (always >= sim_time).
        """
        elapsed_hours = sim_time / 3600.0
        return (
            sim_time
            + self._initial_offset_s
            + self._drift_rate_s_per_day * elapsed_hours / 24.0
        )

    def drift_offset(self, sim_time: float) -> float:
        """Return the total drift offset (without sim_time itself).

        Parameters
        ----------
        sim_time:
            Simulation time in seconds from start.

        Returns
        -------
        float
            Total offset in seconds (initial + accumulated drift).
        """
        elapsed_hours = sim_time / 3600.0
        return self._initial_offset_s + self._drift_rate_s_per_day * elapsed_hours / 24.0


# ---------------------------------------------------------------------------
# Scan cycle model (PRD 3a.8)
# ---------------------------------------------------------------------------


class ScanCycleModel:
    """Quantises Modbus register values to PLC scan cycle boundaries.

    Real PLCs update registers once per scan cycle. Between scans, register
    values are stale. This model tracks the next scan boundary and returns
    the last-snapped value when the boundary has not yet been crossed.

    Usage::

        model = ScanCycleModel(config, rng)
        # Once per update loop iteration (before any get_value calls):
        model.prepare_tick(sim_time)
        # For each signal value written to Modbus registers:
        quantised = model.get_value(signal_id, current_value)

    Formula (PRD 3a.8)::

        actual_cycle = cycle_ms * (1.0 + uniform(0, jitter_pct))

    Parameters
    ----------
    config:
        :class:`ScanCycleConfig` with ``cycle_ms`` and ``jitter_pct``.
    rng:
        Numpy random generator for jitter sampling (Rule 13: isolated RNG).
    """

    def __init__(self, config: ScanCycleConfig, rng: np.random.Generator) -> None:
        self._cycle_ms = config.cycle_ms
        self._jitter_pct = config.jitter_pct
        self._rng = rng
        # Start at 0.0 so the first prepare_tick call always crosses the boundary.
        self._next_boundary_ms: float = 0.0
        self._scan_active: bool = True
        self._last_outputs: dict[str, float] = {}

    @property
    def scan_active(self) -> bool:
        """True if the scan boundary was crossed on the last :meth:`prepare_tick` call."""
        return self._scan_active

    @property
    def next_boundary_ms(self) -> float:
        """Next scan boundary in milliseconds (from sim_time=0)."""
        return self._next_boundary_ms

    def prepare_tick(self, sim_time: float) -> None:
        """Determine scan state for the current tick.

        Must be called exactly once per update loop iteration, before any
        :meth:`get_value` calls for that iteration.

        If ``sim_time`` (converted to milliseconds) is at or past the next
        scan boundary, the boundary advances by one jittered cycle and all
        subsequent :meth:`get_value` calls for this tick will return the fresh
        (current) value.  Otherwise they return the cached stale value.

        Parameters
        ----------
        sim_time:
            Current simulation time in seconds.
        """
        sim_time_ms = sim_time * 1000.0
        if sim_time_ms >= self._next_boundary_ms:
            self._scan_active = True
            actual_cycle = self._cycle_ms * (
                1.0 + self._rng.uniform(0.0, self._jitter_pct)
            )
            self._next_boundary_ms += actual_cycle
        else:
            self._scan_active = False

    def get_value(self, signal_id: str, current_value: float) -> float:
        """Return the scan-cycle-quantised value for a signal.

        If the scan boundary was just crossed (:attr:`scan_active` is True),
        the current value is cached and returned.  Otherwise the stale cached
        value from the last boundary crossing is returned.

        :meth:`prepare_tick` must have been called at least once before this.

        Parameters
        ----------
        signal_id:
            Unique signal identifier for per-signal cache lookup.
        current_value:
            Current value from the signal store.

        Returns
        -------
        float
            Quantised value: ``current_value`` if scan active, else stale.
        """
        if self._scan_active:
            self._last_outputs[signal_id] = current_value
            return current_value
        return self._last_outputs.get(signal_id, current_value)


# ---------------------------------------------------------------------------
# Default per-controller-type configurations from PRD 3a.5 / 3a.8
# ---------------------------------------------------------------------------

# Connection limits (PRD 3a.5)
_DEFAULT_CONNECTION_LIMITS: dict[str, ConnectionLimitConfig] = {
    "S7-1500": ConnectionLimitConfig(
        max_connections=16,
        response_timeout_ms_typical=50.0,
        response_timeout_ms_max=200.0,
    ),
    "S7-1200": ConnectionLimitConfig(
        max_connections=3,
        response_timeout_ms_typical=100.0,
        response_timeout_ms_max=500.0,
    ),
    "CompactLogix": ConnectionLimitConfig(
        max_connections=8,
        response_timeout_ms_typical=75.0,
        response_timeout_ms_max=300.0,
    ),
    "Eurotherm": ConnectionLimitConfig(
        max_connections=2,
        response_timeout_ms_typical=150.0,
        response_timeout_ms_max=1000.0,
    ),
    "Danfoss": ConnectionLimitConfig(
        max_connections=2,
        response_timeout_ms_typical=200.0,
        response_timeout_ms_max=1000.0,
    ),
    "PM5560": ConnectionLimitConfig(
        max_connections=4,
        response_timeout_ms_typical=50.0,
        response_timeout_ms_max=100.0,
    ),
}

# Connection drops (PRD 3a.5)
_DEFAULT_CONNECTION_DROPS: dict[str, ConnectionDropConfig] = {
    "S7-1500": ConnectionDropConfig(
        mtbf_hours_min=72.0,
        mtbf_hours_max=168.0,
        reconnection_delay_s_min=1.0,
        reconnection_delay_s_max=3.0,
    ),
    "S7-1200": ConnectionDropConfig(
        mtbf_hours_min=48.0,
        mtbf_hours_max=168.0,
        reconnection_delay_s_min=2.0,
        reconnection_delay_s_max=5.0,
    ),
    "CompactLogix": ConnectionDropConfig(
        mtbf_hours_min=48.0,
        mtbf_hours_max=168.0,
        reconnection_delay_s_min=2.0,
        reconnection_delay_s_max=5.0,
    ),
    "Eurotherm": ConnectionDropConfig(
        mtbf_hours_min=8.0,
        mtbf_hours_max=24.0,
        reconnection_delay_s_min=5.0,
        reconnection_delay_s_max=15.0,
    ),
    "Danfoss": ConnectionDropConfig(
        mtbf_hours_min=24.0,
        mtbf_hours_max=48.0,
        reconnection_delay_s_min=3.0,
        reconnection_delay_s_max=10.0,
    ),
    "PM5560": ConnectionDropConfig(
        mtbf_hours_min=72.0,
        mtbf_hours_max=168.0,
        reconnection_delay_s_min=1.0,
        reconnection_delay_s_max=2.0,
    ),
}

# Clock drift (PRD 3a.5)
_DEFAULT_CLOCK_DRIFT: dict[str, ClockDriftConfig] = {
    "S7-1500": ClockDriftConfig(initial_offset_ms=200.0, drift_rate_s_per_day=0.3),
    "S7-1200": ClockDriftConfig(initial_offset_ms=1500.0, drift_rate_s_per_day=1.0),
    "CompactLogix": ClockDriftConfig(
        initial_offset_ms=500.0, drift_rate_s_per_day=0.5
    ),
    "Eurotherm": ClockDriftConfig(initial_offset_ms=5000.0, drift_rate_s_per_day=5.0),
    "Danfoss": ClockDriftConfig(initial_offset_ms=3000.0, drift_rate_s_per_day=2.5),
    "PM5560": ClockDriftConfig(initial_offset_ms=100.0, drift_rate_s_per_day=0.2),
}

# Scan cycle (PRD 3a.8)
_DEFAULT_SCAN_CYCLE: dict[str, ScanCycleConfig] = {
    "S7-1500": ScanCycleConfig(cycle_ms=10.0, jitter_pct=0.05),
    "S7-1200": ScanCycleConfig(cycle_ms=20.0, jitter_pct=0.08),
    "CompactLogix": ScanCycleConfig(cycle_ms=15.0, jitter_pct=0.06),
    "Eurotherm": ScanCycleConfig(cycle_ms=100.0, jitter_pct=0.10),
    "Danfoss": ScanCycleConfig(cycle_ms=100.0, jitter_pct=0.10),
    "PM5560": ScanCycleConfig(cycle_ms=50.0, jitter_pct=0.05),
}


# ---------------------------------------------------------------------------
# Network Topology Manager
# ---------------------------------------------------------------------------


class NetworkTopologyManager:
    """Resolves controller endpoints based on mode and profile.

    In collapsed mode: single Modbus port, single OPC-UA port, single MQTT
    broker — current behaviour preserved.

    In realistic mode: per-controller ports per PRD 3a.4 table.

    Parameters
    ----------
    config:
        Network configuration. If *None*, collapsed defaults are used.
    profile:
        Active factory profile (``"packaging"`` or ``"food_bev"``).
    """

    def __init__(
        self,
        config: NetworkConfig | None = None,
        profile: Literal["packaging", "food_bev"] = "packaging",
    ) -> None:
        self._config = config or NetworkConfig()
        self._profile = profile

    @property
    def mode(self) -> str:
        return self._config.mode

    @property
    def profile(self) -> str:
        return self._profile

    # ---- helper to resolve per-controller overrides -----------------------

    def _get_clock_drift(self, controller_name: str, controller_type: str) -> ClockDriftConfig:
        if controller_name in self._config.clock_drift:
            return self._config.clock_drift[controller_name]
        return _DEFAULT_CLOCK_DRIFT.get(controller_type, ClockDriftConfig())

    def _get_scan_cycle(self, controller_name: str, controller_type: str) -> ScanCycleConfig:
        if controller_name in self._config.scan_cycle:
            return self._config.scan_cycle[controller_name]
        return _DEFAULT_SCAN_CYCLE.get(controller_type, ScanCycleConfig())

    def _get_connection_limit(
        self, controller_name: str, controller_type: str,
    ) -> ConnectionLimitConfig:
        if controller_name in self._config.connection_limits:
            return self._config.connection_limits[controller_name]
        return _DEFAULT_CONNECTION_LIMITS.get(
            controller_type, ConnectionLimitConfig()
        )

    def _get_connection_drop(
        self, controller_name: str, controller_type: str,
    ) -> ConnectionDropConfig:
        if controller_name in self._config.connection_drops:
            return self._config.connection_drops[controller_name]
        return _DEFAULT_CONNECTION_DROPS.get(
            controller_type, ConnectionDropConfig()
        )

    # ---- endpoint resolution methods --------------------------------------

    def modbus_endpoints(self) -> list[ModbusEndpointSpec]:
        """Return Modbus TCP endpoint specs for the active profile and mode."""
        if self._config.mode == "collapsed":
            return self._collapsed_modbus()
        return self._realistic_modbus()

    def opcua_endpoints(self) -> list[OpcuaEndpointSpec]:
        """Return OPC-UA endpoint specs for the active profile and mode."""
        if self._config.mode == "collapsed":
            return self._collapsed_opcua()
        return self._realistic_opcua()

    def mqtt_endpoint(self) -> MqttEndpointSpec:
        """Return MQTT broker spec (shared across modes and profiles).

        In realistic mode, a representative clock drift is applied to MQTT
        timestamps (500 ms initial offset, 0.5 s/day drift), simulating
        a typical SCADA gateway clock.  Collapsed mode has no drift.
        """
        if self.mode == "realistic":
            drift = ClockDriftConfig(
                initial_offset_ms=500.0,
                drift_rate_s_per_day=0.5,
            )
            return MqttEndpointSpec(clock_drift=drift)
        return MqttEndpointSpec()

    # ---- collapsed mode ---------------------------------------------------

    def _collapsed_modbus(self) -> list[ModbusEndpointSpec]:
        """Single Modbus server serving all registers (standard port 502)."""
        return [
            ModbusEndpointSpec(
                port=502,
                unit_ids=[1],
                register_range=None,
                byte_order="ABCD",
                controller_type="generic",
                controller_name="collapsed",
            )
        ]

    def _collapsed_opcua(self) -> list[OpcuaEndpointSpec]:
        """Single OPC-UA server serving full node tree."""
        return [
            OpcuaEndpointSpec(
                port=4840,
                node_tree_root="",
                controller_type="generic",
                controller_name="collapsed",
            )
        ]

    # ---- realistic mode: packaging ----------------------------------------

    def _packaging_modbus(self) -> list[ModbusEndpointSpec]:
        """Packaging profile: 3 Modbus endpoints per PRD 3a.4.

        Press PLC + Energy meter share port 5020 (UIDs 1 and 5).
        Laminator and slitter each get their own port.
        """
        return [
            # Press PLC + Energy meter share port 5020 (UIDs 1 and 5)
            ModbusEndpointSpec(
                port=5020,
                unit_ids=[1, 5],
                register_range=None,
                byte_order="ABCD",
                controller_type="S7-1500",
                controller_name="press_plc",
                equipment_ids=["press", "energy"],
                uid_equipment_map={1: ["press"], 5: ["energy"]},
                state_signal_id="press.machine_state",
                clock_drift=self._get_clock_drift("press_plc", "S7-1500"),
                scan_cycle=self._get_scan_cycle("press_plc", "S7-1500"),
                connection_limit=self._get_connection_limit("press_plc", "S7-1500"),
                connection_drop=self._get_connection_drop("press_plc", "S7-1500"),
            ),
            # Laminator PLC — no state signal, 0x06 disabled for this endpoint
            ModbusEndpointSpec(
                port=5021,
                unit_ids=[1],
                register_range=None,
                byte_order="ABCD",
                controller_type="S7-1200",
                controller_name="laminator_plc",
                equipment_ids=["laminator"],
                uid_equipment_map={1: ["laminator"]},
                state_signal_id=None,
                clock_drift=self._get_clock_drift("laminator_plc", "S7-1200"),
                scan_cycle=self._get_scan_cycle("laminator_plc", "S7-1200"),
                connection_limit=self._get_connection_limit("laminator_plc", "S7-1200"),
                connection_drop=self._get_connection_drop("laminator_plc", "S7-1200"),
            ),
            # Slitter PLC — no state signal, 0x06 disabled for this endpoint
            ModbusEndpointSpec(
                port=5022,
                unit_ids=[1],
                register_range=None,
                byte_order="ABCD",
                controller_type="S7-1200",
                controller_name="slitter_plc",
                equipment_ids=["slitter"],
                uid_equipment_map={1: ["slitter"]},
                state_signal_id=None,
                clock_drift=self._get_clock_drift("slitter_plc", "S7-1200"),
                scan_cycle=self._get_scan_cycle("slitter_plc", "S7-1200"),
                connection_limit=self._get_connection_limit("slitter_plc", "S7-1200"),
                connection_drop=self._get_connection_drop("slitter_plc", "S7-1200"),
            ),
        ]

    def _packaging_opcua(self) -> list[OpcuaEndpointSpec]:
        """Packaging profile: 1 OPC-UA endpoint on port 4840."""
        return [
            OpcuaEndpointSpec(
                port=4840,
                node_tree_root="PackagingLine",
                controller_type="S7-1500",
                controller_name="press_plc",
                clock_drift=self._get_clock_drift("press_plc", "S7-1500"),
                connection_limit=self._get_connection_limit("press_plc", "S7-1500"),
                connection_drop=self._get_connection_drop("press_plc", "S7-1500"),
            )
        ]

    # ---- realistic mode: F&B ----------------------------------------------

    def _foodbev_modbus(self) -> list[ModbusEndpointSpec]:
        """F&B profile: 6 Modbus endpoints per PRD 3a.4.

        Oven gateway shares port 5031 for zones (UIDs 1,2,3) and energy
        meter (UID 10).
        """
        return [
            # Mixer PLC (CompactLogix, CDAB)
            ModbusEndpointSpec(
                port=5030,
                unit_ids=[1],
                register_range=None,
                byte_order="CDAB",
                controller_type="CompactLogix",
                controller_name="mixer_plc",
                equipment_ids=["mixer"],
                uid_equipment_map={1: ["mixer"]},
                state_signal_id="mixer.state",
                clock_drift=self._get_clock_drift("mixer_plc", "CompactLogix"),
                scan_cycle=self._get_scan_cycle("mixer_plc", "CompactLogix"),
                connection_limit=self._get_connection_limit(
                    "mixer_plc", "CompactLogix"
                ),
                connection_drop=self._get_connection_drop(
                    "mixer_plc", "CompactLogix"
                ),
            ),
            # Oven gateway: UIDs 1,2,3 (zones) + UID 10 (energy meter)
            # secondary_uid_remap remaps collapsed-mode slave IDs 11/12/13 to
            # realistic-mode UIDs 1/2/3 per PRD 03a Section 3a.2 topology table.
            ModbusEndpointSpec(
                port=5031,
                unit_ids=[1, 2, 3, 10],
                register_range=None,
                byte_order="ABCD",
                controller_type="Eurotherm",
                controller_name="oven_gateway",
                equipment_ids=["oven", "energy"],
                uid_equipment_map={
                    1: ["oven"],
                    2: ["oven"],
                    3: ["oven"],
                    10: ["energy"],
                },
                secondary_uid_remap={11: 1, 12: 2, 13: 3},
                state_signal_id="oven.state",
                clock_drift=self._get_clock_drift("oven_gateway", "Eurotherm"),
                scan_cycle=self._get_scan_cycle("oven_gateway", "Eurotherm"),
                connection_limit=self._get_connection_limit(
                    "oven_gateway", "Eurotherm"
                ),
                connection_drop=self._get_connection_drop(
                    "oven_gateway", "Eurotherm"
                ),
            ),
            # Filler PLC (Modbus side)
            ModbusEndpointSpec(
                port=5032,
                unit_ids=[1],
                register_range=None,
                byte_order="ABCD",
                controller_type="S7-1200",
                controller_name="filler_plc",
                equipment_ids=["filler"],
                uid_equipment_map={1: ["filler"]},
                state_signal_id="filler.state",
                clock_drift=self._get_clock_drift("filler_plc", "S7-1200"),
                scan_cycle=self._get_scan_cycle("filler_plc", "S7-1200"),
                connection_limit=self._get_connection_limit("filler_plc", "S7-1200"),
                connection_drop=self._get_connection_drop("filler_plc", "S7-1200"),
            ),
            # Sealer PLC — no state signal (sealer has no machine state enum)
            ModbusEndpointSpec(
                port=5033,
                unit_ids=[1],
                register_range=None,
                byte_order="ABCD",
                controller_type="S7-1200",
                controller_name="sealer_plc",
                equipment_ids=["sealer"],
                uid_equipment_map={1: ["sealer"]},
                state_signal_id=None,
                clock_drift=self._get_clock_drift("sealer_plc", "S7-1200"),
                scan_cycle=self._get_scan_cycle("sealer_plc", "S7-1200"),
                connection_limit=self._get_connection_limit("sealer_plc", "S7-1200"),
                connection_drop=self._get_connection_drop("sealer_plc", "S7-1200"),
            ),
            # Chiller (Danfoss)
            ModbusEndpointSpec(
                port=5034,
                unit_ids=[1],
                register_range=None,
                byte_order="ABCD",
                controller_type="Danfoss",
                controller_name="chiller",
                equipment_ids=["chiller"],
                uid_equipment_map={1: ["chiller"]},
                state_signal_id="chiller.compressor_state",
                clock_drift=self._get_clock_drift("chiller", "Danfoss"),
                scan_cycle=self._get_scan_cycle("chiller", "Danfoss"),
                connection_limit=self._get_connection_limit("chiller", "Danfoss"),
                connection_drop=self._get_connection_drop("chiller", "Danfoss"),
            ),
            # CIP controller (S7-1200)
            ModbusEndpointSpec(
                port=5035,
                unit_ids=[1],
                register_range=None,
                byte_order="ABCD",
                controller_type="S7-1200",
                controller_name="cip_controller",
                equipment_ids=["cip"],
                uid_equipment_map={1: ["cip"]},
                state_signal_id="cip.state",
                clock_drift=self._get_clock_drift("cip_controller", "S7-1200"),
                scan_cycle=self._get_scan_cycle("cip_controller", "S7-1200"),
                connection_limit=self._get_connection_limit(
                    "cip_controller", "S7-1200"
                ),
                connection_drop=self._get_connection_drop(
                    "cip_controller", "S7-1200"
                ),
            ),
        ]

    def _foodbev_opcua(self) -> list[OpcuaEndpointSpec]:
        """F&B profile: 2 OPC-UA endpoints per PRD 3a.4."""
        return [
            # Filler OPC-UA
            OpcuaEndpointSpec(
                port=4841,
                node_tree_root="FoodBevLine.Filler1",
                controller_type="S7-1200",
                controller_name="filler_plc",
                clock_drift=self._get_clock_drift("filler_plc", "S7-1200"),
                connection_limit=self._get_connection_limit("filler_plc", "S7-1200"),
                connection_drop=self._get_connection_drop("filler_plc", "S7-1200"),
            ),
            # QC station OPC-UA (Mettler Toledo)
            OpcuaEndpointSpec(
                port=4842,
                node_tree_root="FoodBevLine.QC1",
                controller_type="S7-1200",
                controller_name="qc_station",
                clock_drift=self._get_clock_drift("qc_station", "S7-1200"),
                connection_limit=self._get_connection_limit("qc_station", "S7-1200"),
                connection_drop=self._get_connection_drop("qc_station", "S7-1200"),
            ),
        ]

    # ---- realistic dispatcher ---------------------------------------------

    def _realistic_modbus(self) -> list[ModbusEndpointSpec]:
        if self._profile == "packaging":
            return self._packaging_modbus()
        return self._foodbev_modbus()

    def _realistic_opcua(self) -> list[OpcuaEndpointSpec]:
        if self._profile == "packaging":
            return self._packaging_opcua()
        return self._foodbev_opcua()
