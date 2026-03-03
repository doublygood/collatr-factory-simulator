"""Energy monitoring equipment generator.

Energy monitoring tracks power consumption for the entire line.
It produces 2 signals.  Line power correlates with press operating
state: base load when idle (5-15 kW), running load proportional
to speed (60-150 kW).

PRD Reference: Section 2.8 (Energy Monitoring)
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
CLAUDE.md Rule 12: No global state.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

import numpy as np

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.base import EquipmentGenerator
from factory_simulator.models.base import clamp, quantise
from factory_simulator.models.correlated import CorrelatedFollowerModel
from factory_simulator.models.counter import CounterModel
from factory_simulator.store import SignalStore, SignalValue


class EnergyGenerator(EquipmentGenerator):
    """Energy monitoring generator -- 2 signals, correlated with line speed.

    Signals:
    - line_power: correlated follower of the configured speed signal
      (base load + proportional load)
    - cumulative_kwh: counter that accumulates based on line_power

    The parent speed signal is configurable via EquipmentConfig.model_extra:
    - Packaging profile: press.line_speed (default)
    - F&B profile: filler.line_speed

    Config extra fields (EquipmentConfig.model_extra):
    - coupling_speed_signal: str  (default "press.line_speed")
    """

    def __init__(
        self,
        equipment_id: str,
        config: EquipmentConfig,
        rng: np.random.Generator,
    ) -> None:
        super().__init__(equipment_id, config, rng)
        extras = config.model_extra or {}
        self._speed_signal: str = str(
            extras.get("coupling_speed_signal", "press.line_speed")
        )
        self._build_models()

    def _build_models(self) -> None:
        sigs = self._signal_configs

        # Line power (correlated with press.line_speed)
        self._line_power = self._build_correlated(sigs.get("line_power"))

        # Cumulative kWh (counter driven by power level)
        self._cumulative_kwh = self._build_counter(sigs.get("cumulative_kwh"))

    def _build_correlated(
        self, sig_cfg: SignalConfig | None,
    ) -> CorrelatedFollowerModel:
        params: dict[str, object] = {"base": 10.0, "gain": 0.5}
        noise = None
        if sig_cfg is not None:
            p = sig_cfg.params
            params["base"] = p.get("base", 10.0)
            params["gain"] = p.get("factor", p.get("gain", 0.5))
            noise = self._make_noise(sig_cfg)
        return CorrelatedFollowerModel(params, self._spawn_rng(), noise=noise)

    def _build_counter(self, sig_cfg: SignalConfig | None) -> CounterModel:
        params: dict[str, object] = {"rate": 0.001}
        if sig_cfg is not None:
            params.update(sig_cfg.params)
        return CounterModel(params, self._spawn_rng())

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

        # Read configured speed signal for correlation
        press_speed = float(store.get_value(self._speed_signal, 0.0))

        # 1. Line power (follows press speed, with base load)
        self._line_power.set_parent_value(press_speed)
        raw_power = self._line_power.generate(sim_time, dt)
        power = self._post_process("line_power", raw_power)
        results.append(self._make_sv("line_power", power, sim_time))

        # 2. Cumulative kWh (accumulates based on current power)
        # Convert kW to kWh rate: power * (dt / 3600)
        # CounterModel uses rate * speed * dt, so set speed = power / 3600
        # and the configured rate acts as a multiplier.
        self._cumulative_kwh.set_speed(power)
        raw_kwh = self._cumulative_kwh.generate(sim_time, dt)
        kwh = self._post_process("cumulative_kwh", raw_kwh)
        results.append(self._make_sv("cumulative_kwh", kwh, sim_time))

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
