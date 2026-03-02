"""Equipment generators for the Collatr Factory Simulator.

Each generator owns a set of signal models for one equipment group
and produces SignalValue entries for the signal store.

PRD Reference: Section 8.4 (Plugin Architecture)
"""

from factory_simulator.generators.base import (
    EquipmentGenerator,
    ModbusMapping,
    MqttMapping,
    OpcuaMapping,
    ProtocolMapping,
)
from factory_simulator.generators.press import PressGenerator

__all__ = [
    "EquipmentGenerator",
    "ModbusMapping",
    "MqttMapping",
    "OpcuaMapping",
    "PressGenerator",
    "ProtocolMapping",
]
