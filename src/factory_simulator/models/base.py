"""Abstract base class for signal models and post-processing utilities.

Every signal model implements this interface.  The engine calls
``generate()`` each tick, passing simulated time and dt.  Models
use a :class:`NoiseGenerator` (injected at construction) for their
noise -- keeping distribution selection at the config level.

Post-processing functions (``quantise``, ``clamp``) are applied by the
engine after ``generate()`` + noise.  They are implemented once here
rather than in every model.

PRD Reference: Section 4.2 (Signal Models), Section 4.2.13 (Quantisation)
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod

import numpy as np


class SignalModel(ABC):
    """Abstract base class for all signal models.

    Parameters
    ----------
    params:
        Model-specific parameters from the signal config ``params`` dict.
    rng:
        numpy random Generator for any stochastic behaviour in the model
        itself (distinct from noise which is handled by NoiseGenerator).
    """

    def __init__(self, params: dict[str, object], rng: np.random.Generator) -> None:
        self._params = params
        self._rng = rng

    @abstractmethod
    def generate(self, sim_time: float, dt: float) -> float:
        """Produce the next signal value.

        Parameters
        ----------
        sim_time:
            Current simulated time in seconds since start.
        dt:
            Simulated time delta for this tick in seconds.

        Returns
        -------
        float
            The raw signal value (before noise addition and quantisation).
        """

    def reset(self) -> None:  # noqa: B027
        """Reset any internal state.  Override in stateful models."""


def quantise(value: float, resolution: float | None) -> float:
    """Apply sensor quantisation (PRD 4.2.13).

    Rounds the value to the nearest multiple of *resolution*.
    Returns the value unchanged if resolution is None or <= 0.

    Parameters
    ----------
    value:
        Signal value after noise addition.
    resolution:
        Quantisation step size (e.g. 0.1 for Eurotherm int16 x10).
        *None* or <= 0 disables quantisation.

    Returns
    -------
    float
        Quantised signal value.
    """
    if resolution is None or resolution <= 0.0:
        return value
    return round(value / resolution) * resolution


def clamp(value: float, min_clamp: float | None, max_clamp: float | None) -> float:
    """Clamp value to physical bounds.

    Parameters
    ----------
    value:
        Signal value after noise and quantisation.
    min_clamp:
        Lower physical bound.  *None* means no lower bound.
    max_clamp:
        Upper physical bound.  *None* means no upper bound.

    Returns
    -------
    float
        Clamped signal value.
    """
    # NaN propagates through IEEE 754 comparisons as False, so guard
    # explicitly to prevent NaN leaking into protocol registers.
    if math.isnan(value):
        if min_clamp is not None:
            return min_clamp
        if max_clamp is not None:
            return max_clamp
        return 0.0
    if min_clamp is not None and value < min_clamp:
        return min_clamp
    if max_clamp is not None and value > max_clamp:
        return max_clamp
    return value
