"""Batch cycle scenario (F&B mixer).

Drives the mixer through one complete batch:
  Loading → Mixing → Holding → Discharging

One scenario instance = one batch cycle.  The scheduler creates 8-16
per shift (PRD 5.14.1).  Batch-to-batch variation is achieved by drawing
phase durations from uniform distributions each time.

PRD Reference: Section 5.14.1
CLAUDE.md Rules: Rule 6 (sim_time), Rule 12 (no global state).
"""

from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.mixer import MixerGenerator


class _Phase(Enum):
    LOADING = auto()
    MIXING = auto()
    HOLDING = auto()
    DISCHARGING = auto()


def _uniform(rng: np.random.Generator, lo: float, hi: float) -> float:
    if lo >= hi:
        return lo
    return float(rng.uniform(lo, hi))


class BatchCycle(Scenario):
    """One batch cycle: Loading → Mixing → Holding → Discharging.

    Parameters (via ``params`` dict)
    ---------------------------------
    loading_duration_range : list[float]
        [min, max] loading phase duration in seconds (default [120, 300]
        = 2-5 minutes per PRD 5.14.1).
    mixing_duration_range : list[float]
        [min, max] mixing phase duration in seconds (default [600, 1500]
        = 10-25 minutes per PRD 5.14.1).
    holding_duration_range : list[float]
        [min, max] holding phase duration in seconds (default [300, 600]
        = 5-10 minutes per PRD 5.14.1).
    discharging_duration_range : list[float]
        [min, max] discharging phase duration in seconds (default [120, 300]
        = 2-5 minutes per PRD 5.14.1).
    """

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Draw phase durations once at construction (batch-to-batch variation)
        loading_range = p.get("loading_duration_range", [120.0, 300.0])
        mixing_range = p.get("mixing_duration_range", [600.0, 1500.0])
        holding_range = p.get("holding_duration_range", [300.0, 600.0])
        discharging_range = p.get("discharging_duration_range", [120.0, 300.0])

        def _r(val: object) -> tuple[float, float]:
            if isinstance(val, list) and len(val) == 2:
                return float(val[0]), float(val[1])
            f = float(val)  # type: ignore[arg-type]
            return f, f

        self._loading_duration = _uniform(rng, *_r(loading_range))
        self._mixing_duration = _uniform(rng, *_r(mixing_range))
        self._holding_duration = _uniform(rng, *_r(holding_range))
        self._discharging_duration = _uniform(rng, *_r(discharging_range))

        # Internal phase tracking
        self._internal_phase = _Phase.LOADING
        self._phase_elapsed: float = 0.0
        self._mixer: MixerGenerator | None = None

    # -- Public properties (for testing) ---------------------------------------

    @property
    def loading_duration(self) -> float:
        """Duration of the loading phase in seconds."""
        return self._loading_duration

    @property
    def mixing_duration(self) -> float:
        """Duration of the mixing phase in seconds."""
        return self._mixing_duration

    @property
    def holding_duration(self) -> float:
        """Duration of the holding phase in seconds."""
        return self._holding_duration

    @property
    def discharging_duration(self) -> float:
        """Duration of the discharging phase in seconds."""
        return self._discharging_duration

    @property
    def internal_phase(self) -> _Phase:
        """Current internal batch phase (for testing)."""
        return self._internal_phase

    def duration(self) -> float:
        """Total batch duration: sum of all phase durations."""
        return (
            self._loading_duration
            + self._mixing_duration
            + self._holding_duration
            + self._discharging_duration
        )

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Begin batch: force mixer to Loading, log ground truth start."""
        mixer = self._find_mixer(engine)
        if mixer is None:
            self.complete(sim_time, engine)
            return

        self._mixer = mixer
        self._internal_phase = _Phase.LOADING
        self._phase_elapsed = 0.0

        # Drive mixer into Loading state
        mixer.state_machine.force_state("Loading")

        # Ground truth: state change on activation
        gt = engine.ground_truth
        if gt is not None:
            gt.log_state_change(sim_time, "mixer.state", "Off", "Loading")

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Advance through batch phases based on elapsed time."""
        self._phase_elapsed += dt

        if self._mixer is None:
            self.complete(sim_time, engine)
            return

        if self._internal_phase == _Phase.LOADING:
            if self._phase_elapsed >= self._loading_duration:
                self._transition(engine, sim_time, _Phase.MIXING, "Loading", "Mixing")

        elif self._internal_phase == _Phase.MIXING:
            if self._phase_elapsed >= self._mixing_duration:
                self._transition(engine, sim_time, _Phase.HOLDING, "Mixing", "Holding")

        elif self._internal_phase == _Phase.HOLDING:
            if self._phase_elapsed >= self._holding_duration:
                self._transition(
                    engine, sim_time, _Phase.DISCHARGING, "Holding", "Discharging",
                )

        elif (
            self._internal_phase == _Phase.DISCHARGING
            and self._phase_elapsed >= self._discharging_duration
        ):
            self.complete(sim_time, engine)

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Discharge finished: return mixer to Off, log ground truth end."""
        if self._mixer is not None:
            self._mixer.state_machine.force_state("Off")
            gt = engine.ground_truth
            if gt is not None:
                gt.log_state_change(sim_time, "mixer.state", "Discharging", "Off")

    # -- Helpers ---------------------------------------------------------------

    def _transition(
        self,
        engine: DataEngine,
        sim_time: float,
        next_phase: _Phase,
        from_state: str,
        to_state: str,
    ) -> None:
        """Transition the mixer to the next state and update internal phase."""
        if self._mixer is None:
            return
        self._mixer.state_machine.force_state(to_state)
        self._internal_phase = next_phase
        self._phase_elapsed = 0.0

        gt = engine.ground_truth
        if gt is not None:
            gt.log_state_change(sim_time, "mixer.state", from_state, to_state)

    def _find_mixer(self, engine: DataEngine) -> MixerGenerator | None:
        """Find the mixer generator in the engine."""
        from factory_simulator.generators.mixer import MixerGenerator as _MG

        for gen in engine.generators:
            if isinstance(gen, _MG):
                return gen
        return None
