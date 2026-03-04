"""Slitter equipment generator.

The slitter cuts wide rolls into narrow reels.  It produces 3 signals
and operates independently from the press on a scheduled basis.  It
starts at a configurable offset from shift start and runs for a
configurable duration per shift.

PRD Reference: Section 2.4 (Slitter equipment)
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
from factory_simulator.models.noise import NoiseGenerator
from factory_simulator.models.ramp import RampModel
from factory_simulator.store import SignalStore, SignalValue


def _float_param(params: dict[str, object], key: str, default: float) -> float:
    raw = params.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


# Default shift duration in seconds (8 hours)
_SHIFT_DURATION_S = 8.0 * 3600.0

# Default target speed for the slitter (m/min)
_DEFAULT_TARGET_SPEED = 500.0


class SlitterGenerator(EquipmentGenerator):
    """Slitter generator -- 3 signals, scheduled operation.

    Signals:
    - speed: ramp model (ramps up when scheduled, 0 otherwise)
    - web_tension: correlated follower of slitter.speed
    - reel_count: counter (increments proportional to speed)

    The slitter operates on a configurable schedule:
    - ``schedule_offset_hours``: delay from shift start (default 2.0)
    - ``run_duration_hours``: how long it runs per shift (default 4.0)

    When outside its scheduled window, speed is 0 and reel_count
    does not increment.
    """

    def __init__(
        self,
        equipment_id: str,
        config: EquipmentConfig,
        rng: np.random.Generator,
    ) -> None:
        super().__init__(equipment_id, config, rng)

        # Schedule config from equipment extras
        extras = config.model_extra or {}
        self._schedule_offset_s: float = (
            float(extras.get("schedule_offset_hours", 2.0)) * 3600.0
        )
        self._run_duration_s: float = (
            float(extras.get("run_duration_hours", 4.0)) * 3600.0
        )
        self._target_speed: float = float(extras.get("target_speed", _DEFAULT_TARGET_SPEED))

        # Track scheduling state
        self._is_running: bool = False
        self._was_running: bool = False

        self._build_models()

    def _build_models(self) -> None:
        sigs = self._signal_configs

        # Speed (ramp)
        self._speed_model = self._build_ramp(sigs.get("speed"))
        self._speed_noise = (
            self._make_noise(sigs["speed"]) if "speed" in sigs else None
        )

        # Web tension (correlated follower of speed)
        self._web_tension = self._build_correlated(sigs.get("web_tension"))

        # Reel count (counter)
        self._reel_count = self._build_counter(sigs.get("reel_count"))

    def _build_ramp(self, sig_cfg: SignalConfig | None) -> RampModel:
        ramp_params: dict[str, object] = {
            "start": 0.0, "end": 0.0, "duration": 60.0, "steps": 1,
        }
        if sig_cfg is not None:
            dur = sig_cfg.params.get("ramp_duration_s", 60.0)
            ramp_params["duration"] = float(dur)
        return RampModel(ramp_params, self._spawn_rng())

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

    def _build_counter(self, sig_cfg: SignalConfig | None) -> CounterModel:
        params: dict[str, object] = {"rate": 1.0}
        if sig_cfg is not None:
            params.update(sig_cfg.params)
        return CounterModel(params, self._spawn_rng())

    # -- Schedule ---

    def _is_scheduled(self, sim_time: float) -> bool:
        """Determine if the slitter should be active at this sim_time.

        The slitter runs within each shift window after schedule_offset
        for run_duration.
        """
        time_in_shift = sim_time % _SHIFT_DURATION_S
        start = self._schedule_offset_s
        end = start + self._run_duration_s
        return start <= time_in_shift < end

    @property
    def is_running(self) -> bool:
        """Whether the slitter is currently in its scheduled run window."""
        return self._is_running

    # -- Public interface ---

    def get_signal_ids(self) -> list[str]:
        return [self._signal_id(name) for name in self._signal_configs]

    def get_counter_models(self) -> dict[str, CounterModel]:
        """Return counter models keyed by fully-qualified signal ID."""
        return {self._signal_id("reel_count"): self._reel_count}

    def generate(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> list[SignalValue]:
        results: list[SignalValue] = []

        # Determine if slitter should be running
        self._is_running = self._is_scheduled(sim_time)

        # Handle transitions
        if self._is_running and not self._was_running:
            # Starting: ramp up to target speed
            current_speed = self._speed_model.value
            ramp_cfg = self._signal_configs.get("speed")
            duration = 60.0
            if ramp_cfg is not None:
                duration = float(ramp_cfg.params.get("ramp_duration_s", 60.0))
            self._speed_model.start_ramp(
                start=current_speed,
                end=self._target_speed,
                duration=duration,
            )
        elif not self._is_running and self._was_running:
            # Stopping: ramp down
            current_speed = self._speed_model.value
            if current_speed > 0.0:
                self._speed_model.start_ramp(
                    start=current_speed, end=0.0, duration=30.0,
                )

        self._was_running = self._is_running

        # 1. Speed
        raw_speed = self._speed_model.generate(sim_time, dt)
        noise_for_speed = self._speed_noise if self._is_running else None
        speed = self._post_process("speed", raw_speed, noise_for_speed)
        results.append(self._make_sv("speed", speed, sim_time))

        # 2. Web tension (follows slitter speed)
        self._web_tension.set_parent_value(speed)
        raw_tension = self._web_tension.generate(sim_time, dt)
        tension = self._post_process("web_tension", raw_tension)
        results.append(self._make_sv("web_tension", tension, sim_time))

        # 3. Reel count (increments proportional to speed)
        self._reel_count.set_speed(speed)
        raw_reel = self._reel_count.generate(sim_time, dt)
        reel = self._post_process("reel_count", raw_reel)
        results.append(self._make_sv("reel_count", reel, sim_time))

        return results

    # -- Helpers ---

    def _post_process(
        self,
        signal_name: str,
        raw_value: float,
        noise: NoiseGenerator | None = None,
    ) -> float:
        value = raw_value
        if noise is not None:
            value += noise.sample()
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
