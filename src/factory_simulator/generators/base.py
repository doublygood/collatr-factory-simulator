"""Equipment generator base class and protocol mapping types.

New equipment types implement the EquipmentGenerator interface.
Each generator owns a set of signal models and produces SignalValue
entries for the signal store.

PRD Reference: Section 8.4 (Plugin Architecture)
CLAUDE.md Rule 12: No Global State -- generators are instantiated per-profile.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.models.noise import NoiseGenerator

if TYPE_CHECKING:
    from factory_simulator.store import SignalStore, SignalValue


# ---------------------------------------------------------------------------
# Protocol mapping types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ModbusMapping:
    """Modbus register mapping for a signal."""

    address: list[int]
    register_type: str  # "float32", "uint32", "uint16"
    byte_order: str = "ABCD"
    writable: bool = False


@dataclass(slots=True)
class OpcuaMapping:
    """OPC-UA node mapping for a signal."""

    node_id: str
    data_type: str


@dataclass(slots=True)
class MqttMapping:
    """MQTT topic mapping for a signal."""

    topic: str
    qos: int = 1
    retain: bool = True


@dataclass(slots=True)
class ProtocolMapping:
    """Protocol endpoint mappings for a single signal."""

    modbus: ModbusMapping | None = None
    opcua: OpcuaMapping | None = None
    mqtt: MqttMapping | None = None


# ---------------------------------------------------------------------------
# EquipmentGenerator ABC
# ---------------------------------------------------------------------------


class EquipmentGenerator(ABC):
    """Abstract base class for equipment generators.

    Each equipment group (press, laminator, coder, etc.) implements
    this interface.  The generator owns signal models for all signals
    in its group, reads cross-equipment state from the store, and
    produces a list of SignalValue entries per tick.

    The ``generate()`` method does NOT take a ``machine_state``
    parameter.  Each generator reads its own state from the store.
    This supports the F&B profile where multiple independent state
    machines run concurrently (PRD 8.4).

    Parameters
    ----------
    equipment_id:
        Equipment identifier prefix (e.g. ``"press"``, ``"laminator"``).
    config:
        Equipment-specific configuration from the YAML.
    rng:
        numpy random Generator (from SeedSequence, Rule 13).
    """

    def __init__(
        self,
        equipment_id: str,
        config: EquipmentConfig,
        rng: np.random.Generator,
    ) -> None:
        self._equipment_id = equipment_id
        self._config = config
        self._rng = rng
        self._signal_configs: dict[str, SignalConfig] = config.signals

    @property
    def equipment_id(self) -> str:
        """Equipment identifier prefix."""
        return self._equipment_id

    @abstractmethod
    def get_signal_ids(self) -> list[str]:
        """Return list of signal IDs this equipment produces.

        Signal IDs are fully qualified: ``"press.line_speed"``,
        ``"laminator.nip_temp"``, etc.
        """

    @abstractmethod
    def generate(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> list[SignalValue]:
        """Generate new signal values for the current tick.

        Each generator reads its own equipment state from the store.
        The store provides access to all signals across all equipment,
        enabling cross-equipment correlations.

        Parameters
        ----------
        sim_time:
            Current simulated time in seconds since start.
        dt:
            Simulated time delta for this tick in seconds.
        store:
            The central signal store for reading cross-equipment state.

        Returns
        -------
        list[SignalValue]
            New signal values to write to the store.
        """

    def get_protocol_mappings(self) -> dict[str, ProtocolMapping]:
        """Return protocol endpoint mappings for each signal.

        Default implementation reads mappings from the signal configs.
        Override for custom mapping logic.
        """
        mappings: dict[str, ProtocolMapping] = {}
        for name, sig_cfg in self._signal_configs.items():
            signal_id = f"{self._equipment_id}.{name}"
            mapping = ProtocolMapping()

            if sig_cfg.modbus_hr is not None:
                mapping.modbus = ModbusMapping(
                    address=sig_cfg.modbus_hr,
                    register_type=sig_cfg.modbus_type or "float32",
                    writable=sig_cfg.modbus_writable,
                )

            if sig_cfg.opcua_node is not None:
                mapping.opcua = OpcuaMapping(
                    node_id=sig_cfg.opcua_node,
                    data_type=sig_cfg.opcua_type or "Double",
                )

            if sig_cfg.mqtt_topic is not None:
                mapping.mqtt = MqttMapping(topic=sig_cfg.mqtt_topic)

            mappings[signal_id] = mapping

        return mappings

    # -- Helpers for subclasses -----------------------------------------------

    def _spawn_rng(self) -> np.random.Generator:
        """Create a child RNG from the parent (Rule 13: isolated generators)."""
        return np.random.default_rng(self._rng.integers(0, 2**63))

    def _make_noise(self, sig_cfg: SignalConfig) -> NoiseGenerator | None:
        """Create a NoiseGenerator from signal config, or None if sigma=0."""
        if sig_cfg.noise_sigma <= 0.0:
            return None
        return NoiseGenerator.from_config(
            sigma=sig_cfg.noise_sigma,
            noise_type=sig_cfg.noise_type,
            rng=self._spawn_rng(),
            noise_df=sig_cfg.noise_df,
            noise_phi=sig_cfg.noise_phi,
        )

    def _signal_id(self, name: str) -> str:
        """Build fully-qualified signal ID."""
        return f"{self._equipment_id}.{name}"
