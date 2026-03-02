"""Environmental sensors equipment generator — composite model.

Environmental sensors monitor factory floor conditions.  They produce
2 signals using a 3-layer composite model per PRD Section 4.2.2:

  value = daily_sine(t) + hvac_cycle(t) + perturbation(t) + noise(0, sigma)

Layers:
1. Daily sinusoidal cycle (24-hour period).
2. HVAC cycling via BangBangModel (15-30 min period, 0.5-1.5 C amplitude).
3. Random perturbations — Poisson process (3-8 per shift, 1-3 C) with
   first-order-lag decay (tau 5-10 min).

Ambient humidity follows the same layered pattern but inverted: humidity
drops when temperature rises (HVAC dehumidifies), humidity spikes when
doors open.

PRD Reference: Section 4.2.2 (Composite environmental model)
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
CLAUDE.md Rule 12: No global state.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

import math

import numpy as np

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.base import EquipmentGenerator
from factory_simulator.models.bang_bang import BangBangModel
from factory_simulator.models.base import clamp, quantise
from factory_simulator.models.sinusoidal import SinusoidalModel
from factory_simulator.store import SignalStore, SignalValue


def _float_param(params: dict[str, object], key: str, default: float) -> float:
    raw = params.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


class EnvironmentGenerator(EquipmentGenerator):
    """Environmental sensors generator — 2 signals, composite model.

    Signals:
    - ambient_temp: 3-layer composite (daily sine + HVAC + perturbations)
    - ambient_humidity: same pattern, inverted (PRD 4.2.2)

    Independent of press state — the factory environment follows its own
    diurnal pattern regardless of production.

    Composite config parameters (on ambient_temp ``params`` dict):
    - hvac_period_minutes: HVAC cycle period (default 20, range 15-30)
    - hvac_amplitude_c: HVAC oscillation amplitude in C (default 1.0,
      range 0.5-1.5)
    - perturbation_rate_per_shift: Poisson event rate per 8-hour shift
      (default 5, range 3-8)
    - perturbation_magnitude_c: centre of event magnitude in C
      (default 2.0; each event draws from U[0.5*mag, 1.5*mag])
    - perturbation_decay_tau_minutes: exponential decay time constant
      in minutes (default 7, range 5-10)
    """

    def __init__(
        self,
        equipment_id: str,
        config: EquipmentConfig,
        rng: np.random.Generator,
    ) -> None:
        super().__init__(equipment_id, config, rng)
        self._last_sim_time: float | None = None
        self._build_models()

    def _build_models(self) -> None:
        sigs = self._signal_configs
        temp_cfg = sigs.get("ambient_temp")
        humid_cfg = sigs.get("ambient_humidity")

        temp_params: dict[str, object] = temp_cfg.params if temp_cfg else {}

        # Layer 1: Daily sinusoidal WITHOUT noise (noise is final layer)
        self._daily_temp = self._build_sinusoidal_base(temp_cfg)
        self._daily_humid = self._build_sinusoidal_base(humid_cfg)

        # Final-layer noise generators
        self._temp_noise = self._make_noise(temp_cfg) if temp_cfg else None
        self._humid_noise = self._make_noise(humid_cfg) if humid_cfg else None

        # Layer 2: HVAC cycling via BangBangModel (PRD 4.2.2)
        hvac_period_min = _float_param(temp_params, "hvac_period_minutes", 20.0)
        self._hvac_amplitude = _float_param(temp_params, "hvac_amplitude_c", 1.0)

        # Symmetric rate gives period = 4 * amplitude / rate  (in minutes)
        hvac_rate = 4.0 * self._hvac_amplitude / hvac_period_min  # C/min

        self._hvac = BangBangModel(
            params={
                "setpoint": 0.0,
                "dead_band_high": self._hvac_amplitude,
                "dead_band_low": self._hvac_amplitude,
                "cooling_rate": hvac_rate,
                "heat_gain_rate": hvac_rate,
                "initial_temp": 0.0,
                "initial_state": "off",
            },
            rng=self._spawn_rng(),
        )

        # Layer 3: Random perturbations (Poisson + exponential decay)
        perturb_rate = _float_param(
            temp_params, "perturbation_rate_per_shift", 5.0,
        )
        self._perturb_magnitude = _float_param(
            temp_params, "perturbation_magnitude_c", 2.0,
        )
        perturb_tau_min = _float_param(
            temp_params, "perturbation_decay_tau_minutes", 7.0,
        )

        shift_duration_s = 8.0 * 3600.0  # 8-hour shift
        self._perturb_lambda = perturb_rate / shift_duration_s  # events/s
        self._perturb_tau = perturb_tau_min * 60.0  # seconds
        self._perturb_offset: float = 0.0
        self._perturb_rng = self._spawn_rng()

        # Humidity scaling: maps C offsets → %RH offsets (inverted)
        temp_amp = _float_param(temp_params, "amplitude", 3.0)
        humid_params: dict[str, object] = humid_cfg.params if humid_cfg else {}
        humid_amp = _float_param(humid_params, "amplitude", 10.0)
        self._humidity_ratio = humid_amp / temp_amp if temp_amp > 0 else 3.33

    def _build_sinusoidal_base(
        self, sig_cfg: SignalConfig | None,
    ) -> SinusoidalModel:
        """Build a SinusoidalModel *without* noise.

        Noise is applied as a separate final layer so the composite
        formula ``daily_sine + hvac + perturbation + noise`` holds.
        """
        params: dict[str, object] = {
            "center": 22.0,
            "amplitude": 3.0,
            "period": 86400.0,
            "phase": 0.0,
        }
        if sig_cfg is not None:
            params.update(sig_cfg.params)
        return SinusoidalModel(params, self._spawn_rng())  # no noise

    # -- Public interface -------------------------------------------------------

    def get_signal_ids(self) -> list[str]:
        return [self._signal_id(name) for name in self._signal_configs]

    def generate(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> list[SignalValue]:
        # Compute real elapsed time for sub-models that use dt.
        # The data engine may call with a small dt (0.1 s) even though the
        # generator fires only every sample_rate_ms (60 s).
        real_dt = dt if self._last_sim_time is None else sim_time - self._last_sim_time
        self._last_sim_time = sim_time

        results: list[SignalValue] = []

        # Layer 1: Daily sinusoidal base (no noise)
        temp_base = self._daily_temp.generate(sim_time, real_dt)
        humid_base = self._daily_humid.generate(sim_time, real_dt)

        # Layer 2: HVAC cycling offset (oscillates around 0)
        hvac_offset = self._hvac.generate(sim_time, real_dt)

        # Layer 3: Random perturbations (Poisson + exponential decay)
        self._update_perturbation(real_dt)
        perturb_offset = self._perturb_offset

        # Combine layers for temperature
        temp_raw = temp_base + hvac_offset + perturb_offset
        if self._temp_noise is not None:
            temp_raw += self._temp_noise.sample()
        temp = self._post_process("ambient_temp", temp_raw)
        results.append(self._make_sv("ambient_temp", temp, sim_time))

        # Combine layers for humidity (inverted — PRD 4.2.2)
        humid_raw = (
            humid_base
            - self._humidity_ratio * (hvac_offset + perturb_offset)
        )
        if self._humid_noise is not None:
            humid_raw += self._humid_noise.sample()
        humid = self._post_process("ambient_humidity", humid_raw)
        results.append(self._make_sv("ambient_humidity", humid, sim_time))

        return results

    # -- Internal ---------------------------------------------------------------

    def _update_perturbation(self, real_dt: float) -> None:
        """Decay existing perturbation offset + add new Poisson events."""
        # Exponential decay toward zero
        if abs(self._perturb_offset) > 1e-12:
            self._perturb_offset *= math.exp(-real_dt / self._perturb_tau)

        # Poisson process: draw event count for this interval
        n_events = int(self._perturb_rng.poisson(self._perturb_lambda * real_dt))
        for _ in range(n_events):
            sign = 1.0 if self._perturb_rng.random() > 0.5 else -1.0
            # Magnitude from U[0.5*mag, 1.5*mag]
            # Default mag=2.0 → U[1.0, 3.0], matching PRD "1-3 C"
            mag = float(self._perturb_rng.uniform(
                0.5 * self._perturb_magnitude,
                1.5 * self._perturb_magnitude,
            ))
            self._perturb_offset += sign * mag

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
