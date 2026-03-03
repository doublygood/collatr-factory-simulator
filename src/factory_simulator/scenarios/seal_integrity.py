"""Seal integrity failure scenario (F&B sealer).

Simulates heater element degradation or controller fault causing the seal
bar temperature to drop below the minimum sealing threshold (~170 C).
As temperature drops, the weakened seal bar fails to compress properly
(pressure decreases) and poor seal geometry allows gas leakage (vacuum
degrades).  The downstream QC station detects failed seals and the reject
rate spikes.  On scenario completion the line is stopped for seal bar
replacement and all conditions are restored.

Sequence (PRD 5.14.4):
1. ``sealer.seal_temp`` drops below minimum threshold (e.g. 170 C).
2. ``sealer.seal_pressure`` decreases as weakened seal bar can't compress.
3. ``sealer.vacuum_level`` degrades as poor seal geometry allows gas leakage.
4. ``qc.reject_total`` spikes as QC station detects failed seals.
5. On completion: restore original values (line stopped for seal bar repair).

Frequency: 1-2 per week.
Duration: 5-30 minutes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.checkweigher import CheckweigherGenerator
    from factory_simulator.generators.sealer import SealerGenerator


class SealIntegrityFailure(Scenario):
    """Seal bar failure: temperature drop, pressure/vacuum degradation, QC rejects.

    The scenario directly overrides ``sealer._seal_temp_current`` each tick to
    drive seal temperature below the normal operating threshold.  Pressure and
    vacuum are degraded by modifying the respective SteadyStateModel targets.
    Extra QC rejects accumulate in ``qc._reject_total`` at an elevated rate.

    On completion all original model state is restored, representing the line
    stopping for seal bar replacement.

    Parameters (via ``params`` dict)
    ---------------------------------
    duration_range : list[float]
        [min, max] scenario duration in seconds
        (default [300.0, 1800.0] = 5-30 minutes per PRD 5.14.4).
    temp_drop_range : list[float]
        [min, max] total seal temperature drop in degrees C
        (default [15.0, 30.0]).  Drop target ~170 C: 180 - 15 = 165 C.
    pressure_drop_fraction : list[float]
        [min, max] fraction of seal_pressure target to remove
        (default [0.2, 0.5]).
    vacuum_fraction_lost : list[float]
        [min, max] fraction of vacuum_level magnitude to lose
        (default [0.3, 0.6]).  Vacuum is negative; losing 30% of -0.7 → -0.49.
    extra_reject_rate : list[float]
        [min, max] extra QC rejects per minute from failed seals
        (default [5.0, 20.0]).
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

        # Duration (PRD: 5-30 min)
        dur_range = p.get("duration_range", [300.0, 1800.0])
        if isinstance(dur_range, list) and len(dur_range) == 2:
            self._duration = float(
                rng.uniform(float(dur_range[0]), float(dur_range[1]))
            )
        else:
            self._duration = float(dur_range)  # type: ignore[arg-type]

        # Temperature drop (default: 15-30 C drop from ~180 C target)
        temp_drop = p.get("temp_drop_range", [15.0, 30.0])
        if isinstance(temp_drop, list) and len(temp_drop) == 2:
            self._temp_drop = float(
                rng.uniform(float(temp_drop[0]), float(temp_drop[1]))
            )
        else:
            self._temp_drop = float(temp_drop)  # type: ignore[arg-type]

        # Pressure drop fraction (default: 20-50% of target pressure)
        pressure_frac = p.get("pressure_drop_fraction", [0.2, 0.5])
        if isinstance(pressure_frac, list) and len(pressure_frac) == 2:
            self._pressure_drop_fraction = float(
                rng.uniform(float(pressure_frac[0]), float(pressure_frac[1]))
            )
        else:
            self._pressure_drop_fraction = float(pressure_frac)  # type: ignore[arg-type]

        # Vacuum fraction lost (default: 30-60% of magnitude)
        vac_frac = p.get("vacuum_fraction_lost", [0.3, 0.6])
        if isinstance(vac_frac, list) and len(vac_frac) == 2:
            self._vacuum_fraction_lost = float(
                rng.uniform(float(vac_frac[0]), float(vac_frac[1]))
            )
        else:
            self._vacuum_fraction_lost = float(vac_frac)  # type: ignore[arg-type]

        # Extra reject rate (default: 5-20 rejects per minute)
        reject_rate = p.get("extra_reject_rate", [5.0, 20.0])
        if isinstance(reject_rate, list) and len(reject_rate) == 2:
            self._extra_reject_rate = float(
                rng.uniform(float(reject_rate[0]), float(reject_rate[1]))
            )
        else:
            self._extra_reject_rate = float(reject_rate)  # type: ignore[arg-type]

        # Saved generator references and original state
        self._sealer: SealerGenerator | None = None
        self._qc: CheckweigherGenerator | None = None

        # Original steady-state model targets
        self._saved_seal_temp_current: float = 0.0
        self._saved_pressure_target: float = 0.0
        self._saved_vacuum_target: float = 0.0

        # Accumulated fractional reject increments (to handle sub-1 rejects per tick)
        self._reject_accumulator: float = 0.0

    # -- Public properties for testing -----------------------------------------

    @property
    def scenario_duration(self) -> float:
        """Total planned duration of the scenario in seconds."""
        return self._duration

    @property
    def temp_drop(self) -> float:
        """Total seal temperature drop in degrees C."""
        return self._temp_drop

    @property
    def pressure_drop_fraction(self) -> float:
        """Fraction of seal pressure target to remove."""
        return self._pressure_drop_fraction

    @property
    def vacuum_fraction_lost(self) -> float:
        """Fraction of vacuum magnitude to lose."""
        return self._vacuum_fraction_lost

    @property
    def extra_reject_rate(self) -> float:
        """Extra QC rejects per minute from failed seals."""
        return self._extra_reject_rate

    def duration(self) -> float:
        """Total planned duration of this scenario in seconds."""
        return self._duration

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Begin seal integrity failure: save original state, log ground truth."""
        sealer = self._find_sealer(engine)
        qc = self._find_qc(engine)

        if sealer is None:
            self.complete(sim_time, engine)
            return

        self._sealer = sealer
        self._qc = qc

        # Save original state
        self._saved_seal_temp_current = sealer._seal_temp_current
        self._saved_pressure_target = sealer._seal_pressure_model._target
        self._saved_vacuum_target = sealer._vacuum_model._target

        # Ground truth: seal integrity anomaly (PRD 4.7)
        gt = engine.ground_truth
        if gt is not None:
            gt.log_signal_anomaly(
                sim_time,
                "sealer.seal_temp",
                "degradation",
                sealer._seal_temp_current,
                [sealer._seal_temp_current - self._temp_drop - 5.0,
                 sealer._seal_temp_current],
            )
            gt.log_signal_anomaly(
                sim_time,
                "qc.reject_total",
                "spike",
                0.0,
                [0.0, self._extra_reject_rate * self._duration / 60.0],
            )

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Apply seal degradation each tick."""
        if self._sealer is None:
            self.complete(sim_time, engine)
            return

        if self._elapsed > self._duration:
            self.complete(sim_time, engine)
            return

        # --- Seal temperature: ramp down linearly to target drop ---
        # Ramp over first 20% of duration, hold at max drop for remainder.
        ramp_duration = self._duration * 0.2
        if self._elapsed <= ramp_duration:
            progress = self._elapsed / ramp_duration if ramp_duration > 0.0 else 1.0
        else:
            progress = 1.0

        drop_now = self._temp_drop * progress
        # Force seal_temp_current to degraded level (overrides generator lag)
        self._sealer._seal_temp_current = (
            self._saved_seal_temp_current - drop_now
        )

        # --- Seal pressure: lower the steady-state model target ---
        self._sealer._seal_pressure_model._target = (
            self._saved_pressure_target * (1.0 - self._pressure_drop_fraction * progress)
        )

        # --- Vacuum level: degrade toward zero (less negative = less vacuum) ---
        # vacuum_fraction_lost fraction of magnitude is lost
        # saved_vacuum_target is negative (e.g. -0.7 bar)
        self._sealer._vacuum_model._target = (
            self._saved_vacuum_target * (1.0 - self._vacuum_fraction_lost * progress)
        )

        # --- QC rejects: accumulate extra rejects per tick ---
        if self._qc is not None:
            # extra_reject_rate in rejects/minute; convert to rejects/tick
            rejects_this_tick = self._extra_reject_rate * dt / 60.0
            self._reject_accumulator += rejects_this_tick
            if self._reject_accumulator >= 1.0:
                whole_rejects = int(self._reject_accumulator)
                self._reject_accumulator -= whole_rejects
                from factory_simulator.generators.checkweigher import (
                    _REJECT_TOTAL_MAX,
                )
                self._qc._reject_total = min(
                    self._qc._reject_total + whole_rejects,
                    _REJECT_TOTAL_MAX,
                )

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Restore original sealer state (line stopped for seal bar repair)."""
        sealer = self._sealer or self._find_sealer(engine)
        if sealer is not None:
            sealer._seal_temp_current = self._saved_seal_temp_current
            sealer._seal_pressure_model._target = self._saved_pressure_target
            sealer._vacuum_model._target = self._saved_vacuum_target

    # -- Helpers ---------------------------------------------------------------

    def _find_sealer(self, engine: DataEngine) -> SealerGenerator | None:
        """Find the sealer generator in the engine."""
        from factory_simulator.generators.sealer import SealerGenerator as _SG

        for gen in engine.generators:
            if isinstance(gen, _SG):
                return gen
        return None

    def _find_qc(self, engine: DataEngine) -> CheckweigherGenerator | None:
        """Find the QC/checkweigher generator in the engine."""
        from factory_simulator.generators.checkweigher import (
            CheckweigherGenerator as _CG,
        )

        for gen in engine.generators:
            if isinstance(gen, _CG):
                return gen
        return None
