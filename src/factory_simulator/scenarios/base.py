"""Base class for all scenarios.

Each scenario goes through a lifecycle:
  pending -> active (with internal phases) -> completed

Scenarios modify equipment state and signal parameters through the
signal store and equipment generators.

PRD Reference: Section 5.1, Section 5.13
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import TYPE_CHECKING, ClassVar

import numpy as np

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.store import SignalStore


class ScenarioPhase(Enum):
    """Lifecycle phase of a scenario instance."""

    PENDING = auto()
    ACTIVE = auto()
    COMPLETED = auto()


class Scenario(ABC):
    """Abstract base for all scenario types.

    Parameters
    ----------
    start_time:
        Simulated time (seconds) when the scenario should activate.
    rng:
        numpy random Generator for stochastic parameters.
    params:
        Scenario-specific parameters from config.
    """

    #: Scheduling priority for conflict resolution (PRD 5.13, Task 4.2).
    #: Values: "state_changing", "non_state_changing", "background", "micro".
    priority: ClassVar[str] = "non_state_changing"

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        self._start_time = start_time
        self._rng = rng
        self._params = params or {}
        self._phase = ScenarioPhase.PENDING
        self._elapsed: float = 0.0

    @property
    def start_time(self) -> float:
        """Simulated time when this scenario should start."""
        return self._start_time

    @property
    def phase(self) -> ScenarioPhase:
        """Current lifecycle phase."""
        return self._phase

    @property
    def elapsed(self) -> float:
        """Time elapsed since activation (seconds)."""
        return self._elapsed

    @property
    def is_active(self) -> bool:
        """Whether the scenario is currently active."""
        return self._phase == ScenarioPhase.ACTIVE

    @property
    def is_completed(self) -> bool:
        """Whether the scenario has finished."""
        return self._phase == ScenarioPhase.COMPLETED

    def evaluate(
        self,
        sim_time: float,
        dt: float,
        engine: DataEngine,
    ) -> None:
        """Evaluate the scenario at the current tick.

        Called by the ScenarioEngine each tick.  Handles lifecycle
        transitions and delegates to subclass ``_on_activate`` /
        ``_on_tick`` / ``_on_complete`` hooks.
        """
        if self._phase == ScenarioPhase.COMPLETED:
            return

        if self._phase == ScenarioPhase.PENDING and sim_time >= self._start_time:
            self._phase = ScenarioPhase.ACTIVE
            self._elapsed = 0.0
            self._on_activate(sim_time, engine)

        if self._phase == ScenarioPhase.ACTIVE:
            self._elapsed += dt
            self._on_tick(sim_time, dt, engine)

    def complete(self, sim_time: float, engine: DataEngine) -> None:
        """Mark the scenario as completed and run cleanup."""
        if self._phase != ScenarioPhase.COMPLETED:
            self._phase = ScenarioPhase.COMPLETED
            self._on_complete(sim_time, engine)

    @abstractmethod
    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Called when the scenario transitions from PENDING to ACTIVE."""

    @abstractmethod
    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Called every tick while the scenario is ACTIVE."""

    @abstractmethod
    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Called when the scenario completes (cleanup / restore)."""

    def post_gen_inject(  # noqa: B027
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> None:
        """Post-generator injection hook (PRD 5.16, Task 4.6).

        Called by the ScenarioEngine AFTER all generators have written to
        the store, BEFORE protocol adapters read it.  Override in scenarios
        that need to inject values directly into the store (e.g., contextual
        anomalies).  Default is a no-op.
        """

    @abstractmethod
    def duration(self) -> float:
        """Total planned duration of this scenario in seconds."""
