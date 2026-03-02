"""Laminator equipment generator.

The laminator bonds two web materials using adhesive.  It produces
5 signals.  Its web speed tracks the press line speed; when the press
stops the laminator continues briefly to clear its web path, then stops.

PRD Reference: Section 2.3 (Laminator equipment)
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
from factory_simulator.models.first_order_lag import FirstOrderLagModel
from factory_simulator.models.steady_state import SteadyStateModel
from factory_simulator.store import SignalStore, SignalValue

# Ambient temperature for cool-down (PRD Section 2.7)
_AMBIENT_TEMP_C = 20.0


def _float_param(params: dict[str, object], key: str, default: float) -> float:
    raw = params.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


class LaminatorGenerator(EquipmentGenerator):
    """Laminator generator -- 5 signals, follows press speed.

    Signals:
    - nip_temp: first-order lag tracking a setpoint
    - nip_pressure: steady state
    - tunnel_temp: first-order lag tracking a setpoint
    - web_speed: correlated follower of press.line_speed
    - adhesive_weight: steady state

    The laminator web speed tracks the press line speed.  Thermal
    signals (nip_temp, tunnel_temp) approach their setpoints when
    the laminator is active (press running) and cool toward ambient
    when the press is stopped.
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

        # Nip temperature (first-order lag)
        self._nip_temp = self._build_first_order_lag(sigs.get("nip_temp"))

        # Nip pressure (steady state)
        self._nip_pressure = self._build_steady_state(sigs.get("nip_pressure"))

        # Tunnel temperature (first-order lag)
        self._tunnel_temp = self._build_first_order_lag(sigs.get("tunnel_temp"))

        # Web speed (correlated follower of press.line_speed)
        self._web_speed = self._build_correlated(sigs.get("web_speed"))

        # Adhesive weight (steady state)
        self._adhesive_weight = self._build_steady_state(sigs.get("adhesive_weight"))

    def _build_first_order_lag(
        self, sig_cfg: SignalConfig | None,
    ) -> FirstOrderLagModel:
        params: dict[str, object] = {
            "setpoint": _AMBIENT_TEMP_C, "tau": 120.0,
            "initial_value": _AMBIENT_TEMP_C,
        }
        noise = None
        if sig_cfg is not None:
            params.update(sig_cfg.params)
            noise = self._make_noise(sig_cfg)
        return FirstOrderLagModel(params, self._spawn_rng(), noise=noise)

    def _build_steady_state(
        self, sig_cfg: SignalConfig | None,
    ) -> SteadyStateModel:
        params: dict[str, object] = {"target": 0.0}
        noise = None
        if sig_cfg is not None:
            params.update(sig_cfg.params)
            noise = self._make_noise(sig_cfg)
        return SteadyStateModel(params, self._spawn_rng(), noise=noise)

    def _build_correlated(
        self, sig_cfg: SignalConfig | None,
    ) -> CorrelatedFollowerModel:
        params: dict[str, object] = {"base": 0.0, "gain": 1.0}
        noise = None
        if sig_cfg is not None:
            p = sig_cfg.params
            params["base"] = p.get("base", 0.0)
            params["gain"] = p.get("factor", p.get("gain", 1.0))
            noise = self._make_noise(sig_cfg)
        return CorrelatedFollowerModel(params, self._spawn_rng(), noise=noise)

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

        # Read press line speed from the store for correlation
        press_speed = float(store.get_value("press.line_speed", 0.0))
        is_active = press_speed > 0.0

        # 1. Web speed (follows press line speed)
        self._web_speed.set_parent_value(press_speed)
        raw_web_speed = self._web_speed.generate(sim_time, dt)
        web_speed = self._post_process("web_speed", raw_web_speed)
        results.append(self._make_sv("web_speed", web_speed, sim_time))

        # 2. Nip temperature -- track setpoint when active, cool when inactive
        if is_active:
            nip_cfg = self._signal_configs.get("nip_temp")
            if nip_cfg is not None:
                sp = _float_param(nip_cfg.params, "setpoint",
                                  _float_param(nip_cfg.params, "target", 55.0))
                self._nip_temp.set_setpoint(sp)
        else:
            self._nip_temp.set_setpoint(_AMBIENT_TEMP_C)  # cool toward ambient

        raw_nip_temp = self._nip_temp.generate(sim_time, dt)
        nip_temp = self._post_process("nip_temp", raw_nip_temp)
        results.append(self._make_sv("nip_temp", nip_temp, sim_time))

        # 3. Nip pressure -- active only when laminator running
        raw_nip_p = self._nip_pressure.generate(sim_time, dt) if is_active else 0.0
        nip_p = self._post_process("nip_pressure", raw_nip_p)
        results.append(self._make_sv("nip_pressure", nip_p, sim_time))

        # 4. Tunnel temperature -- similar to nip temp
        if is_active:
            tunnel_cfg = self._signal_configs.get("tunnel_temp")
            if tunnel_cfg is not None:
                sp = _float_param(tunnel_cfg.params, "setpoint",
                                  _float_param(tunnel_cfg.params, "target", 65.0))
                self._tunnel_temp.set_setpoint(sp)
        else:
            self._tunnel_temp.set_setpoint(_AMBIENT_TEMP_C)

        raw_tunnel = self._tunnel_temp.generate(sim_time, dt)
        tunnel = self._post_process("tunnel_temp", raw_tunnel)
        results.append(self._make_sv("tunnel_temp", tunnel, sim_time))

        # 5. Adhesive weight -- only meaningful when running
        raw_adhesive = self._adhesive_weight.generate(sim_time, dt) if is_active else 0.0
        adhesive = self._post_process("adhesive_weight", raw_adhesive)
        results.append(self._make_sv("adhesive_weight", adhesive, sim_time))

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
