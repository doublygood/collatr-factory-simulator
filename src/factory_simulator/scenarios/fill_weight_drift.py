"""Fill weight drift scenario (F&B filler).

Simulates a worn or miscalibrated volumetric valve causing the mean fill
weight to drift away from target.  As the mean drifts, more fills fall
outside the acceptable range and ``filler.reject_count`` increases.
After the drift duration an operator recalibrates and the mean returns to
target.

Sequence (PRD 5.14.3):
1. ``filler.fill_weight`` mean drifts from target at 0.05-0.2 g per minute.
2. As the mean drifts, more fills fall outside the acceptable range.
3. ``filler.reject_count`` increment rate increases proportionally.
4. After drift duration, the mean returns to target (operator recalibrates).

Frequency: 1-3 per shift.
Duration: 10-60 minutes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.filler import FillerGenerator


class FillWeightDrift(Scenario):
    """Fill weight mean drifts from target due to valve wear/miscalibration.

    The scenario modifies ``filler._fill_giveaway`` each tick to introduce
    a growing offset.  Because the per-item weight is drawn from
    ``Normal(fill_target + fill_giveaway, sigma)``, drifting the giveaway
    shifts the distribution mean, causing more fills to fall outside
    tolerance and raising ``reject_count``.

    On completion the saved giveaway is restored (operator recalibration).

    Parameters (via ``params`` dict)
    ---------------------------------
    drift_duration_range : list[float]
        [min, max] drift duration in seconds (default [600, 3600]
        = 10-60 minutes per PRD 5.14.3).
    drift_rate_range : list[float]
        [min, max] drift rate in g per minute (default [0.05, 0.2]
        per PRD 5.14.3).
    max_drift_range : list[float]
        [min, max] maximum total drift in grams (default [1.0, 8.0]).
    direction : int | None
        +1 for over-weight drift, -1 for under-weight, None = random
        (default None).
    """

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Drift duration (PRD: 10-60 min)
        dur_range = p.get("drift_duration_range", [600.0, 3600.0])
        if isinstance(dur_range, list) and len(dur_range) == 2:
            self._drift_duration = float(
                rng.uniform(float(dur_range[0]), float(dur_range[1]))
            )
        else:
            self._drift_duration = float(dur_range)  # type: ignore[arg-type]

        # Drift rate (PRD: 0.05-0.2 g/min)
        rate_range = p.get("drift_rate_range", [0.05, 0.2])
        if isinstance(rate_range, list) and len(rate_range) == 2:
            self._drift_rate = float(
                rng.uniform(float(rate_range[0]), float(rate_range[1]))
            )
        else:
            self._drift_rate = float(rate_range)  # type: ignore[arg-type]

        # Max drift (avoid drifting into physically impossible territory)
        max_range = p.get("max_drift_range", [1.0, 8.0])
        if isinstance(max_range, list) and len(max_range) == 2:
            self._max_drift = float(
                rng.uniform(float(max_range[0]), float(max_range[1]))
            )
        else:
            self._max_drift = float(max_range)  # type: ignore[arg-type]

        # Drift direction (+1 over-weight, -1 under-weight)
        dir_param = p.get("direction")
        if dir_param is not None:
            self._direction: int = int(dir_param)  # type: ignore[call-overload]
        else:
            self._direction = int(rng.choice([-1, 1]))

        # Saved state for restore on completion
        self._filler: FillerGenerator | None = None
        self._saved_giveaway: float = 0.0

    # -- Public properties for testing -----------------------------------------

    @property
    def drift_duration(self) -> float:
        """Duration of the drift period in seconds."""
        return self._drift_duration

    @property
    def drift_rate(self) -> float:
        """Drift rate in g per minute."""
        return self._drift_rate

    @property
    def max_drift(self) -> float:
        """Maximum total drift in grams."""
        return self._max_drift

    @property
    def direction(self) -> int:
        """Drift direction: +1 (over-weight) or -1 (under-weight)."""
        return self._direction

    def duration(self) -> float:
        """Total planned duration of the drift scenario."""
        return self._drift_duration

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Begin fill weight drift: save original giveaway, log ground truth."""
        filler = self._find_filler(engine)
        if filler is None:
            self.complete(sim_time, engine)
            return

        self._filler = filler
        self._saved_giveaway = filler._fill_giveaway

        # Ground truth: fill weight drift anomaly (PRD 4.7)
        gt = engine.ground_truth
        if gt is not None:
            fill_target = filler.fill_target
            gt.log_signal_anomaly(
                sim_time,
                "filler.fill_weight",
                "drift",
                fill_target + self._saved_giveaway,
                [fill_target - 20.0, fill_target + 20.0],
            )

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Apply growing fill weight offset each tick."""
        if self._filler is None:
            self.complete(sim_time, engine)
            return

        if self._elapsed > self._drift_duration:
            self.complete(sim_time, engine)
            return

        # Linear ramp (drift_rate in g/min, elapsed in seconds)
        drift_offset = min(
            self._drift_rate * self._elapsed / 60.0,
            self._max_drift,
        )

        # Shift the giveaway to move the fill weight distribution mean
        self._filler._fill_giveaway = (
            self._saved_giveaway + self._direction * drift_offset
        )

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Restore original giveaway (operator recalibration)."""
        filler = self._filler or self._find_filler(engine)
        if filler is not None:
            filler._fill_giveaway = self._saved_giveaway

    # -- Helpers ---------------------------------------------------------------

    def _find_filler(self, engine: DataEngine) -> FillerGenerator | None:
        """Find the filler generator in the engine."""
        from factory_simulator.generators.filler import FillerGenerator as _FG

        for gen in engine.generators:
            if isinstance(gen, _FG):
                return gen
        return None
