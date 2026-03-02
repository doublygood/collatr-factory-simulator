"""Environmental sensors equipment generator.

Environmental sensors monitor factory floor conditions.  They produce
2 signals following slow sinusoidal daily patterns.  Temperature peaks
in the afternoon; humidity inversely correlates with temperature.

PRD Reference: Section 2.7 (Environmental Sensors)
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
CLAUDE.md Rule 12: No global state.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

import numpy as np

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.base import EquipmentGenerator
from factory_simulator.models.base import clamp, quantise
from factory_simulator.models.sinusoidal import SinusoidalModel
from factory_simulator.store import SignalStore, SignalValue


class EnvironmentGenerator(EquipmentGenerator):
    """Environmental sensors generator -- 2 signals, sinusoidal cycles.

    Signals:
    - ambient_temp: sinusoidal with 24-hour period, peaks in afternoon
    - ambient_humidity: sinusoidal with 24-hour period, inversely
      correlated with temperature (pi phase offset)

    These are independent of press state -- the factory environment
    follows its own diurnal pattern regardless of production.
    """

    def __init__(
        self,
        equipment_id: str,
        config: EquipmentConfig,
        rng: np.random.Generator,
    ) -> None:
        super().__init__(equipment_id, config, rng)
        self._build_models()

    def _build_models(self) -> None:
        sigs = self._signal_configs

        self._ambient_temp = self._build_sinusoidal(sigs.get("ambient_temp"))
        self._ambient_humidity = self._build_sinusoidal(sigs.get("ambient_humidity"))

    def _build_sinusoidal(
        self, sig_cfg: SignalConfig | None,
    ) -> SinusoidalModel:
        params: dict[str, object] = {
            "center": 22.0,
            "amplitude": 3.0,
            "period": 86400.0,
            "phase": 0.0,
        }
        noise = None
        if sig_cfg is not None:
            params.update(sig_cfg.params)
            noise = self._make_noise(sig_cfg)
        return SinusoidalModel(params, self._spawn_rng(), noise=noise)

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

        # 1. Ambient temperature
        raw_temp = self._ambient_temp.generate(sim_time, dt)
        temp = self._post_process("ambient_temp", raw_temp)
        results.append(self._make_sv("ambient_temp", temp, sim_time))

        # 2. Ambient humidity
        raw_humid = self._ambient_humidity.generate(sim_time, dt)
        humid = self._post_process("ambient_humidity", raw_humid)
        results.append(self._make_sv("ambient_humidity", humid, sim_time))

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
