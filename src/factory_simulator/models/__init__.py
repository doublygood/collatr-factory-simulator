"""Signal models for the Collatr Factory Simulator.

This package contains the SignalModel ABC, noise pipeline, post-processing
utilities, and all concrete signal model implementations.

PRD Reference: Section 4.2 (Signal Models), Section 4.3 (Correlation Model)
"""

from factory_simulator.models.base import SignalModel, clamp, quantise
from factory_simulator.models.correlated import CorrelatedFollowerModel
from factory_simulator.models.counter import CounterModel
from factory_simulator.models.depletion import DepletionModel
from factory_simulator.models.first_order_lag import FirstOrderLagModel
from factory_simulator.models.noise import (
    CholeskyCorrelator,
    NoiseGenerator,
)
from factory_simulator.models.ramp import RampModel
from factory_simulator.models.random_walk import RandomWalkModel
from factory_simulator.models.sinusoidal import SinusoidalModel
from factory_simulator.models.steady_state import SteadyStateModel

__all__ = [
    "CholeskyCorrelator",
    "CorrelatedFollowerModel",
    "CounterModel",
    "DepletionModel",
    "FirstOrderLagModel",
    "NoiseGenerator",
    "RampModel",
    "RandomWalkModel",
    "SignalModel",
    "SinusoidalModel",
    "SteadyStateModel",
    "clamp",
    "quantise",
]
