"""Material splice scenario.

Simulates a flying splice (zero-speed splicer) when the unwind reel is
nearly exhausted.  The machine stays Running throughout.  The splice
produces a brief tension spike, registration disturbance, waste increase,
and an optional speed dip.

Sequence (PRD 5.13a):
1. press.web_tension spikes 50-100 N above normal for 1-3 seconds.
2. press.registration_error_x and _y increase by 0.1-0.3 mm for 10-20 s.
3. press.waste_count increments faster during the splice window.
4. press.unwind_diameter resets to full reel size (1500 mm).
5. press.line_speed may dip 5-10% during the splice, recovers 5-10 s.

Trigger: press.unwind_diameter drops below 150 mm.
Frequency: 2-4 per shift.
Duration: 10-30 seconds of disturbance.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING, Any

import numpy as np

from factory_simulator.scenarios.base import Scenario

if TYPE_CHECKING:
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.generators.press import PressGenerator


class _Phase(Enum):
    """Internal sub-phases of the material splice event."""

    MONITORING = auto()   # Watching unwind_diameter for trigger
    SPLICE = auto()       # Splice active: tension spike + reg error + waste


class MaterialSplice(Scenario):
    """Material splice: flying splice with tension spike and registration hit.

    The scenario monitors ``press._unwind_diameter.value``.  When it drops
    below the trigger threshold (default 150 mm), the splice fires.  The
    machine stays Running (no state change).

    During the splice window, multiple effects operate on different
    timelines:

    - **Tension spike** (1-3 s): ``CorrelatedFollowerModel._base`` on
      ``press._web_tension`` is increased to produce a spike above normal.
    - **Registration error** (10-20 s): ``RandomWalkModel._value`` on
      both X and Y axes is offset by 0.1-0.3 mm.
    - **Waste increase** (full splice duration): ``CounterModel._rate``
      on ``press._waste_count`` is multiplied.
    - **Unwind reset** (immediate): ``DepletionModel.refill(1500.0)``
      resets the reel diameter.
    - **Speed dip** (5-10 s): ``RampModel.start_ramp()`` drops speed
      by 5-10%, then ramps back to target.

    Parameters (via ``params`` dict)
    ---------------------------------
    trigger_diameter : float
        Unwind diameter (mm) that triggers the splice (default 150.0).
    refill_diameter : float
        Diameter (mm) after reel change (default 1500.0).
    splice_duration_range : list[float]
        [min, max] total splice disturbance duration in seconds
        (default [10.0, 30.0]).
    tension_spike_range : list[float]
        [min, max] tension increase above normal in Newtons
        (default [50.0, 100.0]).
    tension_spike_duration_range : list[float]
        [min, max] tension spike duration in seconds (default [1.0, 3.0]).
    reg_error_increase_range : list[float]
        [min, max] registration error offset in mm (default [0.1, 0.3]).
    reg_error_duration_range : list[float]
        [min, max] registration error duration in seconds
        (default [10.0, 20.0]).
    waste_multiplier_range : list[float]
        [min, max] waste rate multiplier during splice
        (default [1.5, 2.5]).
    speed_dip_pct_range : list[float]
        [min, max] speed dip as fraction (default [0.05, 0.10] = 5-10%).
    speed_recovery_range : list[float]
        [min, max] speed recovery ramp duration in seconds
        (default [5.0, 10.0]).
    """

    def __init__(
        self,
        start_time: float,
        rng: np.random.Generator,
        params: dict[str, object] | None = None,
    ) -> None:
        super().__init__(start_time, rng, params)

        p = self._params

        # Trigger threshold (PRD 5.13a: 150 mm)
        self._trigger_diameter = _float_param(p, "trigger_diameter", 150.0)

        # Refill diameter (PRD 5.13a: 1500 mm)
        self._refill_diameter = _float_param(p, "refill_diameter", 1500.0)

        # Splice duration (PRD 5.13a: 10-30 s)
        self._splice_duration = _uniform_param(
            rng, p, "splice_duration_range", [10.0, 30.0],
        )

        # Tension spike magnitude (PRD 5.13a: 50-100 N above normal)
        self._tension_spike = _uniform_param(
            rng, p, "tension_spike_range", [50.0, 100.0],
        )

        # Tension spike duration (PRD 5.13a: 1-3 s)
        self._tension_spike_duration = _uniform_param(
            rng, p, "tension_spike_duration_range", [1.0, 3.0],
        )

        # Registration error increase (PRD 5.13a: 0.1-0.3 mm)
        self._reg_error_increase = _uniform_param(
            rng, p, "reg_error_increase_range", [0.1, 0.3],
        )

        # Registration error duration (PRD 5.13a: 10-20 s)
        self._reg_error_duration = _uniform_param(
            rng, p, "reg_error_duration_range", [10.0, 20.0],
        )

        # Waste rate multiplier during splice
        self._waste_multiplier = _uniform_param(
            rng, p, "waste_multiplier_range", [1.5, 2.5],
        )

        # Speed dip fraction (PRD 5.13a: 5-10%)
        self._speed_dip_pct = _uniform_param(
            rng, p, "speed_dip_pct_range", [0.05, 0.10],
        )

        # Speed recovery ramp duration (PRD 5.13a: 5-10 s)
        self._speed_recovery_s = _uniform_param(
            rng, p, "speed_recovery_range", [5.0, 10.0],
        )

        # Internal state
        self._internal_phase = _Phase.MONITORING
        self._splice_elapsed: float = 0.0
        self._tension_restored: bool = False
        self._reg_restored: bool = False
        self._speed_dip_started: bool = False
        self._speed_restored: bool = False

        # Saved generator state for restore on completion
        self._press: PressGenerator | None = None
        self._saved_tension_base: float = 0.0
        self._saved_tension_max_clamp: float | None = None
        self._saved_waste_rate: float = 0.0
        self._saved_reg_x_value: float = 0.0
        self._saved_reg_y_value: float = 0.0
        self._saved_reg_x_reversion: float = 0.0
        self._saved_reg_y_reversion: float = 0.0

    # -- Public properties for testing -----------------------------------------

    @property
    def trigger_diameter(self) -> float:
        """Unwind diameter that triggers the splice (mm)."""
        return self._trigger_diameter

    @property
    def refill_diameter(self) -> float:
        """Diameter after reel change (mm)."""
        return self._refill_diameter

    @property
    def splice_duration(self) -> float:
        """Total splice disturbance duration (seconds)."""
        return self._splice_duration

    @property
    def tension_spike(self) -> float:
        """Tension spike magnitude above normal (N)."""
        return self._tension_spike

    @property
    def tension_spike_duration(self) -> float:
        """Duration of tension spike (seconds)."""
        return self._tension_spike_duration

    @property
    def reg_error_increase(self) -> float:
        """Registration error offset during splice (mm)."""
        return self._reg_error_increase

    @property
    def reg_error_duration(self) -> float:
        """Duration of registration error increase (seconds)."""
        return self._reg_error_duration

    @property
    def waste_multiplier(self) -> float:
        """Waste rate multiplier during splice."""
        return self._waste_multiplier

    @property
    def speed_dip_pct(self) -> float:
        """Speed dip fraction (0.05 = 5%)."""
        return self._speed_dip_pct

    @property
    def speed_recovery_s(self) -> float:
        """Speed recovery ramp duration (seconds)."""
        return self._speed_recovery_s

    @property
    def internal_phase(self) -> _Phase:
        """Current internal sub-phase."""
        return self._internal_phase

    def duration(self) -> float:
        """Total planned duration of the splice effect."""
        return self._splice_duration

    # -- Lifecycle hooks -------------------------------------------------------

    def _on_activate(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Enter monitoring mode.  Watch unwind diameter for trigger."""
        self._internal_phase = _Phase.MONITORING

    def _on_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Dispatch to monitoring or splice handler."""
        if self._internal_phase == _Phase.MONITORING:
            self._monitor_tick(sim_time, dt, engine)
        elif self._internal_phase == _Phase.SPLICE:
            self._splice_tick(sim_time, dt, engine)

    def _on_complete(
        self, sim_time: float, engine: DataEngine,
    ) -> None:
        """Restore all saved state on completion."""
        press = self._press or self._find_press(engine)
        if press is None:
            return

        # Restore tension model
        if not self._tension_restored:
            self._restore_tension(press)

        # Restore registration models
        if not self._reg_restored:
            self._restore_registration(press)

        # Restore waste rate
        press._waste_count._rate = self._saved_waste_rate

    # -- Internal phase handlers -----------------------------------------------

    def _monitor_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Watch unwind diameter for trigger condition."""
        press = self._find_press(engine)
        if press is None:
            self.complete(sim_time, engine)
            return

        # Only trigger during Running state (flying splice requires running)
        from factory_simulator.generators.press import STATE_RUNNING
        current_state = int(press.state_machine.current_value)
        if current_state != STATE_RUNNING:
            return

        # Check unwind diameter
        unwind_value = press._unwind_diameter.value
        if unwind_value <= self._trigger_diameter:
            self._start_splice(sim_time, engine, press)

    def _splice_tick(
        self, sim_time: float, dt: float, engine: DataEngine,
    ) -> None:
        """Advance splice effects.  Each sub-effect has its own timer."""
        self._splice_elapsed += dt
        press = self._press
        if press is None:
            self.complete(sim_time, engine)
            return

        # --- Tension spike: restore after tension_spike_duration ---
        if (
            not self._tension_restored
            and self._splice_elapsed > self._tension_spike_duration
        ):
            self._restore_tension(press)
            self._tension_restored = True

        # --- Registration error: maintain offset, restore after reg_error_duration ---
        if not self._reg_restored:
            if self._splice_elapsed > self._reg_error_duration:
                self._restore_registration(press)
                self._reg_restored = True
            else:
                # Re-apply offset each tick (generator may overwrite _value)
                press._reg_error_x._value = (
                    self._saved_reg_x_value + self._reg_error_increase
                )
                press._reg_error_y._value = (
                    self._saved_reg_y_value + self._reg_error_increase
                )

        # --- Speed recovery: start ramp back up after speed dip completes ---
        # The speed dip ramp lasts ~2 seconds.  Once elapsed, start recovery.
        if (
            self._speed_dip_started
            and not self._speed_restored
            and self._splice_elapsed > 2.0
        ):
            current_speed = press._line_speed_model.value
            press._line_speed_model.start_ramp(
                start=current_speed,
                end=press.target_speed,
                duration=self._speed_recovery_s,
            )
            self._speed_restored = True

        # --- Check for overall splice completion ---
        if self._splice_elapsed > self._splice_duration:
            self.complete(sim_time, engine)

    def _start_splice(
        self,
        sim_time: float,
        engine: DataEngine,
        press: PressGenerator,
    ) -> None:
        """Transition from MONITORING to SPLICE.

        Applies all splice effects simultaneously:
        1. Tension spike: increase _base on web_tension model.
        2. Registration error offset on both axes.
        3. Waste rate increase.
        4. Unwind diameter refill to 1500 mm.
        5. Speed dip via ramp.
        """
        self._internal_phase = _Phase.SPLICE
        self._splice_elapsed = 0.0
        self._press = press

        # --- 1. Tension spike ---
        tension_model: Any = press._web_tension
        self._saved_tension_base = tension_model._base
        # Add spike above normal base (speed-correlated output adds _base + _gain * speed)
        tension_model._base = self._saved_tension_base + self._tension_spike

        # Raise max_clamp if spike would exceed it
        sig_cfg = press._signal_configs.get("web_tension")
        if sig_cfg is not None:
            self._saved_tension_max_clamp = sig_cfg.max_clamp
            expected_peak = tension_model._base + tension_model._gain * press.target_speed
            if sig_cfg.max_clamp is not None and expected_peak > sig_cfg.max_clamp:
                sig_cfg.max_clamp = expected_peak * 1.2

        # --- 2. Registration error offset ---
        self._saved_reg_x_value = press._reg_error_x._value
        self._saved_reg_y_value = press._reg_error_y._value
        self._saved_reg_x_reversion = press._reg_error_x._reversion_rate
        self._saved_reg_y_reversion = press._reg_error_y._reversion_rate

        # Suppress mean-reversion during the disturbance
        press._reg_error_x._reversion_rate = 0.0
        press._reg_error_y._reversion_rate = 0.0

        # Apply initial offset
        press._reg_error_x._value = self._saved_reg_x_value + self._reg_error_increase
        press._reg_error_y._value = self._saved_reg_y_value + self._reg_error_increase

        # --- 3. Waste rate increase ---
        self._saved_waste_rate = press._waste_count._rate
        press._waste_count._rate = self._saved_waste_rate * self._waste_multiplier

        # --- 4. Unwind diameter reset to full reel ---
        press._unwind_diameter.refill(self._refill_diameter)

        # --- 5. Speed dip ---
        current_speed = press._line_speed_model.value
        if current_speed > 0.0:
            dipped_speed = current_speed * (1.0 - self._speed_dip_pct)
            # Brief ramp down over ~2 seconds (operator slows for splice)
            press._line_speed_model.start_ramp(
                start=current_speed,
                end=dipped_speed,
                duration=2.0,
            )
            self._speed_dip_started = True

    # -- Restore helpers -------------------------------------------------------

    def _restore_tension(self, press: PressGenerator) -> None:
        """Restore web tension model to pre-splice state."""
        tension_model: Any = press._web_tension
        tension_model._base = self._saved_tension_base

        sig_cfg = press._signal_configs.get("web_tension")
        if sig_cfg is not None and self._saved_tension_max_clamp is not None:
            sig_cfg.max_clamp = self._saved_tension_max_clamp

    def _restore_registration(self, press: PressGenerator) -> None:
        """Restore registration error models to pre-splice state."""
        press._reg_error_x._reversion_rate = self._saved_reg_x_reversion
        press._reg_error_y._reversion_rate = self._saved_reg_y_reversion
        # Don't restore _value -- let the model's mean-reversion pull back naturally

    # -- Helpers ---------------------------------------------------------------

    def _find_press(self, engine: DataEngine) -> PressGenerator | None:
        """Find the press generator in the engine."""
        from factory_simulator.generators.press import PressGenerator as _PG

        for gen in engine.generators:
            if isinstance(gen, _PG):
                return gen
        return None


# -- Module-level helpers -----------------------------------------------------


def _float_param(params: dict[str, object], key: str, default: float) -> float:
    """Extract a float parameter from params dict."""
    raw = params.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


def _uniform_param(
    rng: np.random.Generator,
    params: dict[str, object],
    key: str,
    default: list[float],
) -> float:
    """Extract a [min, max] range param and sample uniformly."""
    raw = params.get(key, default)
    if isinstance(raw, list) and len(raw) == 2:
        return float(rng.uniform(float(raw[0]), float(raw[1])))
    return float(raw)  # type: ignore[arg-type]
