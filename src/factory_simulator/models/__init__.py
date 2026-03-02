"""Signal models for the Collatr Factory Simulator.

This package contains the SignalModel ABC, noise pipeline, and all
concrete signal model implementations.

PRD Reference: Section 4.2 (Signal Models), Section 4.3 (Correlation Model)
"""

from factory_simulator.models.base import SignalModel
from factory_simulator.models.noise import (
    CholeskyCorrelator,
    NoiseGenerator,
)

__all__ = [
    "CholeskyCorrelator",
    "NoiseGenerator",
    "SignalModel",
]
