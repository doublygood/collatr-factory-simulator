"""Coder consumable depletion scenario.

Simulates the coder ink cartridge running out and being replaced.
The scenario monitors the existing DepletionModel on the coder
generator and adds the PRD-specified behaviour at threshold levels.

Sequence (PRD 5.12):
1. Ink level depletes naturally via DepletionModel (linear with
   print count).
2. At 10% level: quality flag changes to "uncertain" (low-ink warning).
3. At 2% level: coder enters Fault (3) state (ink empty).
4. After recovery duration (operator replaces cartridge): ink level
   resets to 100%, coder returns to Ready.

Frequency: depends on consumption rate and print speed.
Recovery duration: 5-30 minutes.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.coder import CoderGenerator
    from factory_simulator.models.state import _TransitionDefinition


class _Phase(Enum):
    """Internal phase of the coder depletion scenario."""

    MONITORING = auto()
    DEPLETED = auto()


class CoderDepletion(Scenario):
    """Coder ink consumable depletion: quality warning, fault, refill.

    The scenario monitors the coder's ink_level DepletionModel.
    It does NOT cause depletion -- that happens naturally via the
    generator.  Instead it reacts at threshold levels:

    - At ``low_ink_threshold`` (default 10%): sets quality to
      "uncertain" on the ink_level signal.
    - At ``empty_threshold`` (default 2%): forces the coder to Fault
      state and waits for the recovery duration.
    - After recovery: refills ink to 100%, clears quality override,
      and returns coder to Ready.

    The scenario also disables the DepletionModel's auto-refill
    during its active period so the scenario controls the refill
    timing (simulating operator intervention).

    Parameters (via ``params`` dict)
    ---------------------------------
    low_ink_threshold : float
        Ink level (%) at which quality becomes "uncertain"
        (default 10.0).
    empty_threshold : float
        Ink level (%) at which coder faults (default 2.0).
    recovery_duration_range : list[float]
        [min, max] recovery time in seconds
        (default [300, 1800] = 5-30 min, per PRD 5.12 Fault recovery).
    refill_level : float
        Ink level (%) after cartridge replacement (default 100.0).
    """

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Thresholds (PRD 5.12)
        raw_low = p.get("low_ink_threshold", 10.0)
        self._low_ink_threshold = float(raw_low)  # type: ignore[arg-type]

        raw_empty = p.get("empty_threshold", 2.0)
        self._empty_threshold = float(raw_empty)  # type: ignore[arg-type]

        # Recovery duration (PRD 5.12: Fault -> Ready = 5-30 min)
        rec_range = p.get("recovery_duration_range", [300.0, 1800.0])
        if isinstance(rec_range, list) and len(rec_range) == 2:
            self._recovery_duration = float(
                rng.uniform(float(rec_range[0]), float(rec_range[1]))
            )
        else:
            self._recovery_duration = float(rec_range)  # type: ignore[arg-type]

        # Refill level (PRD 5.12: 100%)
        raw_refill = p.get("refill_level", 100.0)
        self._refill_level = float(raw_refill)  # type: ignore[arg-type]

        # Internal state
        self._internal_phase = _Phase.MONITORING
        self._depleted_elapsed: float = 0.0
        self._low_ink_flagged: bool = False

        # Saved state for restore
        self._coder: CoderGenerator | None = None
        self._saved_refill_threshold: float | None = None
        self._saved_fault_min_dur: float = 0.0
        self._saved_fault_max_dur: float = 0.0
        self._fault_transition: _TransitionDefinition | None = None

    # -- Public properties for testing -----------------------------------------

    @property
    def low_ink_threshold(self) -> float:
        """Ink level (%) triggering quality="uncertain"."""
        return self._low_ink_threshold

    @property
    def empty_threshold(self) -> float:
        """Ink level (%) triggering Fault state."""
        return self._empty_threshold

    @property
    def recovery_duration(self) -> float:
        """Recovery time (seconds) simulating cartridge replacement."""
        return self._recovery_duration

    @property
    def refill_level(self) -> float:
        """Ink level (%) after replacement."""
        return self._refill_level

    @property
    def internal_phase(self) -> _Phase:
        """Current internal phase (MONITORING or DEPLETED)."""
        return self._internal_phase

    @property
    def low_ink_flagged(self) -> bool:
        """Whether the low-ink quality flag has been set."""
        return self._low_ink_flagged

    def duration(self) -> float:
        """Total planned duration of the recovery effect."""
        return self._recovery_duration

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Enter monitoring mode.  Disable auto-refill on ink_level."""
        self._internal_phase = _Phase.MONITORING
        coder = self._find_coder(engine)
        if coder is None:
            self.complete(sim_time, engine)
            return

        self._coder = coder

        # Disable auto-refill so scenario controls refill timing
        ink_model = coder._ink_level
        self._saved_refill_threshold = ink_model._refill_threshold
        ink_model._refill_threshold = None

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Dispatch to monitoring or depleted handler."""
        if self._internal_phase == _Phase.MONITORING:
            self._monitoring_tick(sim_time, dt, engine)
        elif self._internal_phase == _Phase.DEPLETED:
            self._depleted_tick(sim_time, dt, engine)

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Refill ink, clear quality override, restore all saved state."""
        if self._coder is None:
            return

        # Refill ink to configured level
        self._coder._ink_level.refill(self._refill_level)

        # Clear quality override
        self._coder._quality_overrides.pop("ink_level", None)

        # Restore auto-refill threshold
        self._coder._ink_level._refill_threshold = self._saved_refill_threshold

        # Restore Fault->Ready transition timer
        if self._fault_transition is not None:
            self._fault_transition.min_duration = self._saved_fault_min_dur
            self._fault_transition.max_duration = self._saved_fault_max_dur

        # Return coder to Ready (press conditions will then transition
        # to appropriate operational state)
        self._coder._state_machine.force_state("Ready")

    # -- Internal phase handlers -----------------------------------------------

    def _monitoring_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Watch ink level for threshold crossings."""
        if self._coder is None:
            self.complete(sim_time, engine)
            return

        ink_level = self._coder._ink_level.value

        # Check empty threshold first (level may drop fast)
        if ink_level <= self._empty_threshold:
            self._enter_depleted(sim_time, engine)
            return

        # Check low-ink threshold
        if ink_level <= self._low_ink_threshold and not self._low_ink_flagged:
            self._coder._quality_overrides["ink_level"] = "uncertain"
            self._low_ink_flagged = True

    def _depleted_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Wait for recovery duration, then complete."""
        self._depleted_elapsed += dt
        if self._depleted_elapsed > self._recovery_duration:
            self.complete(sim_time, engine)

    def _enter_depleted(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Transition to DEPLETED: fault coder, lock Fault state."""
        if self._coder is None:
            return

        self._internal_phase = _Phase.DEPLETED
        self._depleted_elapsed = 0.0

        # Ensure low-ink flag is set (in case level skipped 10% threshold)
        if not self._low_ink_flagged:
            self._coder._quality_overrides["ink_level"] = "uncertain"
            self._low_ink_flagged = True

        # Force coder to Fault state
        self._coder._state_machine.force_state("Fault")

        # Prevent the Fault->Ready timer from firing during recovery:
        # find the transition and set its min/max duration to a large value
        for t in self._coder._state_machine._transitions:
            if t.from_state == "Fault" and t.to_state == "Ready":
                self._fault_transition = t
                self._saved_fault_min_dur = t.min_duration
                self._saved_fault_max_dur = t.max_duration
                t.min_duration = 1e9
                t.max_duration = 0.0  # disable max_duration forced exit
                break

    # -- Helpers ---------------------------------------------------------------

    def _find_coder(self, engine: DataEngine) -> CoderGenerator | None:
        """Find the coder generator in the engine."""
        from factory_simulator.generators.coder import CoderGenerator as _CG

        for gen in engine.generators:
            if isinstance(gen, _CG):
                return gen
        return None
