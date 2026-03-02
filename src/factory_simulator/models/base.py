"""Abstract base class for signal models.

Every signal model implements this interface.  The engine calls
``generate()`` each tick, passing simulated time and dt.  Models
use a :class:`NoiseGenerator` (injected at construction) for their
noise -- keeping distribution selection at the config level.

PRD Reference: Section 4.2 (Signal Models)
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

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
