"""Vibration monitoring equipment generator.

Vibration sensors monitor the press main drive motor across 3 axes.
Normal vibration for a healthy motor at operating speed is 2-8 mm/s
RMS.  The three axes are correlated (same mechanical source).

PRD Reference: Section 2.9 (Vibration Monitoring)
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
CLAUDE.md Rule 12: No global state.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

import numpy as np

from factory_simulator.config import EquipmentConfig
from factory_simulator.generators.base import EquipmentGenerator
from factory_simulator.models.base import clamp, quantise
from factory_simulator.models.noise import NoiseGenerator
from factory_simulator.models.steady_state import SteadyStateModel
from factory_simulator.store import SignalStore, SignalValue


class VibrationGenerator(EquipmentGenerator):
    """Vibration monitoring generator -- 3 signals, correlated axes.

    Signals:
    - main_drive_x: X-axis vibration RMS (mm/s)
    - main_drive_y: Y-axis vibration RMS (mm/s)
    - main_drive_z: Z-axis vibration RMS (mm/s)

    When the press is running (speed > 0), vibration is at its
    baseline level with noise.  When the press is stopped, vibration
    drops to near-zero.

    The three axes share a common noise component (Cholesky correlation)
    to model the fact that they're measuring the same mechanical source.
    The correlation is implemented by generating one shared noise draw
    and mixing it across the three axes.
    """

    # Default inter-axis correlation coefficient
    _DEFAULT_CORRELATION = 0.6

    def __init__(
        self,
        equipment_id: str,
        config: EquipmentConfig,
        rng: np.random.Generator,
    ) -> None:
        super().__init__(equipment_id, config, rng)

        extras = config.model_extra or {}
        self._correlation: float = float(
            extras.get("axis_correlation", self._DEFAULT_CORRELATION)
        )

        # Pre-compute Cholesky factor for 3x3 correlation matrix
        # [[1, r, r], [r, 1, r], [r, r, 1]]
        r = self._correlation
        corr_matrix = np.array([
            [1.0, r, r],
            [r, 1.0, r],
            [r, r, 1.0],
        ])
        self._cholesky_l = np.linalg.cholesky(corr_matrix)

        self._build_models()

    def _build_models(self) -> None:
        sigs = self._signal_configs

        self._models: dict[str, SteadyStateModel] = {}
        self._noises: dict[str, NoiseGenerator | None] = {}
        self._axis_names: list[str] = []

        for name in ("main_drive_x", "main_drive_y", "main_drive_z"):
            sig_cfg = sigs.get(name)
            params: dict[str, object] = {"target": 4.0}
            noise = None
            if sig_cfg is not None:
                params.update(sig_cfg.params)
                noise = self._make_noise(sig_cfg)
            self._models[name] = SteadyStateModel(params, self._spawn_rng())
            self._noises[name] = noise
            self._axis_names.append(name)

    # -- Public interface ---

    def get_signal_ids(self) -> list[str]:
        return [self._signal_id(name) for name in self._signal_configs]

    def generate(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> list[SignalValue]:
        results: list[SignalValue] = []

        # Read press speed to determine if motor is running
        press_speed = float(store.get_value("press.line_speed", 0.0))
        is_running = press_speed > 1.0  # small threshold to avoid noise

        if is_running:
            # Generate correlated noise for the three axes
            # Step 1: Generate 3 independent N(0,1) draws
            z = self._rng.standard_normal(3)

            # Step 2: Apply Cholesky L to introduce correlation
            correlated_z = self._cholesky_l @ z

            for i, name in enumerate(self._axis_names):
                # Base value from steady state model (target)
                raw = self._models[name].generate(sim_time, dt)

                # Replace independent noise with correlated noise
                noise_gen = self._noises[name]
                if noise_gen is not None:
                    # Scale correlated draw by the noise sigma
                    raw += noise_gen.sigma * float(correlated_z[i])

                value = self._post_process(name, raw)
                results.append(self._make_sv(name, value, sim_time))
        else:
            # Motor stopped: vibration near zero
            for name in self._axis_names:
                # Small residual vibration (ambient floor vibration)
                residual = float(self._rng.normal(0.2, 0.05))
                value = self._post_process(name, max(residual, 0.0))
                results.append(self._make_sv(name, value, sim_time))

        return results

    # -- Helpers ---

    def _post_process(self, signal_name: str, raw_value: float) -> float:
        value = raw_value
        sig_cfg = self._signal_configs.get(signal_name)
        if sig_cfg is not None:
            value = quantise(value, sig_cfg.resolution)
            value = clamp(value, sig_cfg.min_clamp, sig_cfg.max_clamp)
        return value

    def _make_sv(
        self, signal_name: str, value: float, sim_time: float,
    ) -> SignalValue:
        return SignalValue(
            signal_id=self._signal_id(signal_name),
            value=value,
            timestamp=sim_time,
            quality="good",
        )
