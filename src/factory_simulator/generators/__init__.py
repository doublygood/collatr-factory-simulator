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
from factory_simulator.generators.coder import CoderGenerator
from factory_simulator.generators.energy import EnergyGenerator
from factory_simulator.generators.environment import EnvironmentGenerator
from factory_simulator.generators.laminator import LaminatorGenerator
from factory_simulator.generators.press import PressGenerator
from factory_simulator.generators.slitter import SlitterGenerator
from factory_simulator.generators.vibration import VibrationGenerator

__all__ = [
    "CoderGenerator",
    "EnergyGenerator",
    "EnvironmentGenerator",
    "EquipmentGenerator",
    "LaminatorGenerator",
    "ModbusMapping",
    "MqttMapping",
    "OpcuaMapping",
    "PressGenerator",
    "ProtocolMapping",
    "SlitterGenerator",
    "VibrationGenerator",
]
