"""Vibration monitoring equipment generator.

Vibration sensors monitor the press main drive motor across 3 axes.
Normal vibration for a healthy motor at operating speed is 2-8 mm/s
RMS.  The three axes are correlated (same mechanical source).

PRD Reference: Section 2.9 (Vibration Monitoring), Section 4.3.1 (Cholesky)
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

# Residual floor vibration when motor is stopped (PRD 2.9)
_IDLE_VIBRATION_MEAN = 0.2   # mm/s
_IDLE_VIBRATION_STD = 0.05   # mm/s


class VibrationGenerator(EquipmentGenerator):
    """Vibration monitoring generator -- 3 signals, correlated axes.

    Signals:
    - main_drive_x: X-axis vibration RMS (mm/s)
    - main_drive_y: Y-axis vibration RMS (mm/s)
    - main_drive_z: Z-axis vibration RMS (mm/s)

    When the press is running (speed > 0), vibration is at its
    baseline level with Cholesky-correlated noise across axes.
    When the press is stopped, vibration drops to near-zero.

    The noise pipeline follows PRD 4.3.1:
    1. Generate 3 independent N(0,1) draws.
    2. Apply Cholesky factor L to introduce correlation.
    3. Scale by per-signal effective sigma.

    Noise is applied entirely via the Cholesky pipeline, NOT via
    the SteadyStateModel's internal noise, to avoid double-noising.
    """

    # PRD 4.3.1: asymmetric vibration axes correlation matrix
    # X-Y: 0.2, X-Z: 0.15, Y-Z: 0.2 (reflects mechanical coupling)
    _PRD_CORRELATION_MATRIX = np.array([
        [1.0,  0.2,  0.15],
        [0.2,  1.0,  0.2],
        [0.15, 0.2,  1.0],
    ])

    def __init__(
        self,
        equipment_id: str,
        config: EquipmentConfig,
        rng: np.random.Generator,
    ) -> None:
        super().__init__(equipment_id, config, rng)

        extras = config.model_extra or {}

        # Use PRD-specified asymmetric correlation matrix by default.
        # Allow config override via axis_correlation_matrix for non-default profiles.
        custom_matrix = extras.get("axis_correlation_matrix")
        if custom_matrix is not None:
            corr_matrix = np.array(custom_matrix, dtype=np.float64)
        else:
            corr_matrix = self._PRD_CORRELATION_MATRIX.copy()

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
            # Do NOT pass noise to the model -- all noise is applied via
            # the Cholesky pipeline externally (PRD 4.3.1, avoids double-noising).
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
            # PRD 4.3.1 pipeline:
            # Step 1: Generate 3 independent N(0,1) draws
            z = self._rng.standard_normal(3)

            # Step 2: Apply Cholesky L to introduce correlation
            correlated_z = self._cholesky_l @ z

            for i, name in enumerate(self._axis_names):
                # Base target value from steady state model (no internal noise)
                raw = self._models[name].generate(sim_time, dt)

                # Step 3: Scale correlated draw by effective sigma
                noise_gen = self._noises[name]
                if noise_gen is not None:
                    sigma = noise_gen.effective_sigma(press_speed)
                    raw += sigma * float(correlated_z[i])

                value = self._post_process(name, raw)
                results.append(self._make_sv(name, value, sim_time))
        else:
            # Motor stopped: vibration near zero (ambient floor vibration)
            for name in self._axis_names:
                residual = float(
                    self._rng.normal(_IDLE_VIBRATION_MEAN, _IDLE_VIBRATION_STD)
                )
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
