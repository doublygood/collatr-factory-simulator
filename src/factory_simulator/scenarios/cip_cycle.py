"""CIP (Clean-in-Place) cycle scenario (F&B).

Triggers a full CIP cycle on the CIP skid generator, stopping production
(mixer → Cip state, filler → Off) for the duration and resuming once the
cycle completes.

Sequence (PRD 5.14.6):
1. Production stops.  Mixer is placed in Cip state.  Filler is set to Off.
2. CIP generator is kicked into Pre-rinse via ``force_state("Pre_rinse")``.
3. CIP generator auto-advances: Pre-rinse → Caustic → Intermediate →
   Acid wash → Final rinse → Idle (driven by internal phase timers,
   total ~47.5 minutes).
4. Scenario completes when CIP generator returns to Idle state, OR when
   the ``cycle_duration_range`` timeout expires (whichever comes first).
5. Production resumes: mixer and filler return to Off / ready state.

Frequency: 1-3 per day.
Duration: 30-60 minutes.

PRD Reference: Section 5.14.6
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
CLAUDE.md Rule 12: No global state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.cip import CipGenerator
    from factory_simulator.generators.filler import FillerGenerator
    from factory_simulator.generators.mixer import MixerGenerator


class CipCycle(Scenario):
    """Full CIP cycle: production stops, CIP runs all 5 phases, production resumes.

    The scenario triggers the CipGenerator (which auto-advances through
    phases internally) and monitors for completion when the CIP generator
    returns to Idle.  A maximum timeout drawn from ``cycle_duration_range``
    guards against the cycle running indefinitely.

    Parameters (via ``params`` dict)
    ---------------------------------
    cycle_duration_range : list[float]
        [min, max] maximum allowed cycle duration in seconds.
        (default [1800.0, 3600.0] = 30-60 minutes per PRD 5.14.6).
        The scenario completes when CIP returns to Idle OR this timeout
        expires, whichever comes first.
    """

    priority: ClassVar[str] = "state_changing"

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Maximum allowed cycle duration (timeout guard)
        dur_range = p.get("cycle_duration_range", [1800.0, 3600.0])
        if isinstance(dur_range, list) and len(dur_range) == 2:
            self._max_duration = float(
                rng.uniform(float(dur_range[0]), float(dur_range[1]))
            )
        else:
            self._max_duration = float(dur_range)  # type: ignore[arg-type]

        # Saved generator references
        self._cip: CipGenerator | None = None
        self._mixer: MixerGenerator | None = None
        self._filler: FillerGenerator | None = None

        # Saved production states (state machine string names) for ground truth logging
        self._saved_mixer_state: str = "Off"
        self._saved_filler_state: str = "Off"

    # -- Public properties for testing -----------------------------------------

    @property
    def max_duration(self) -> float:
        """Maximum allowed cycle duration in seconds (timeout guard)."""
        return self._max_duration

    def duration(self) -> float:
        """Nominal duration: maximum allowed cycle duration in seconds."""
        return self._max_duration

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Stop production, trigger CIP generator, log ground truth."""
        cip = self._find_cip(engine)

        if cip is None:
            # No CIP generator (e.g. packaging profile) — complete immediately
            self.complete(sim_time, engine)
            return

        self._cip = cip

        # Find optional mixer and filler; not required for CIP to run
        self._mixer = self._find_mixer(engine)
        self._filler = self._find_filler(engine)

        # Save current production states for ground truth logging
        if self._mixer is not None:
            self._saved_mixer_state = self._mixer.state_machine.current_state
        if self._filler is not None:
            self._saved_filler_state = self._filler.state_machine.current_state

        # Stop production: move mixer to CIP-ready state, filler to Off
        if self._mixer is not None:
            self._mixer.state_machine.force_state("Cip")

        if self._filler is not None:
            self._filler.state_machine.force_state("Off")

        # Kick the CIP generator into the first active phase
        cip.force_state("Pre_rinse")

        # Ground truth: state changes on activation
        gt = engine.ground_truth
        if gt is not None:
            gt.log_state_change(sim_time, "cip.state", "Idle", "Pre_rinse")
            if self._mixer is not None:
                gt.log_state_change(
                    sim_time,
                    "mixer.state",
                    self._saved_mixer_state,
                    "Cip",
                )
            if self._filler is not None:
                gt.log_state_change(
                    sim_time,
                    "filler.state",
                    self._saved_filler_state,
                    "Off",
                )

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Complete when CIP generator returns to Idle or timeout expires."""
        if self._cip is None:
            self.complete(sim_time, engine)
            return

        from factory_simulator.generators.cip import STATE_IDLE

        # CIP has returned to Idle naturally — cycle complete
        # Guard _elapsed > 0 prevents immediate completion on the activation tick
        if self._cip.state == STATE_IDLE and self._elapsed > 0.0:
            self.complete(sim_time, engine)
            return

        # Timeout guard: force completion if cycle takes too long
        if self._elapsed >= self._max_duration:
            self.complete(sim_time, engine)

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Resume production after CIP cycle completes."""
        cip = self._cip or self._find_cip(engine)

        # Ensure CIP generator returns to Idle
        if cip is not None:
            from factory_simulator.generators.cip import STATE_IDLE as _IDLE
            if cip.state != _IDLE:
                cip.force_state("Idle")

        # Return production equipment to Off / ready state
        mixer = self._mixer or self._find_mixer(engine)
        if mixer is not None:
            mixer.state_machine.force_state("Off")

        filler = self._filler or self._find_filler(engine)
        if filler is not None:
            filler.state_machine.force_state("Off")

        # Ground truth: state changes on completion
        gt = engine.ground_truth
        if gt is not None:
            if cip is not None:
                gt.log_state_change(sim_time, "cip.state", "Final_rinse", "Idle")
            if mixer is not None:
                gt.log_state_change(sim_time, "mixer.state", "Cip", "Off")
            if filler is not None:
                gt.log_state_change(sim_time, "filler.state", "Off", "Off")

    # -- Helpers ---------------------------------------------------------------

    def _find_cip(self, engine: DataEngine) -> CipGenerator | None:
        """Find the CipGenerator in the engine."""
        from factory_simulator.generators.cip import CipGenerator as _CG

        for gen in engine.generators:
            if isinstance(gen, _CG):
                return gen
        return None

    def _find_mixer(self, engine: DataEngine) -> MixerGenerator | None:
        """Find the MixerGenerator in the engine."""
        from factory_simulator.generators.mixer import MixerGenerator as _MG

        for gen in engine.generators:
            if isinstance(gen, _MG):
                return gen
        return None

    def _find_filler(self, engine: DataEngine) -> FillerGenerator | None:
        """Find the FillerGenerator in the engine."""
        from factory_simulator.generators.filler import FillerGenerator as _FG

        for gen in engine.generators:
            if isinstance(gen, _FG):
                return gen
        return None
