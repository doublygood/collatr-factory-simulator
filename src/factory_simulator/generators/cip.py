"""CIP (Clean-in-Place) equipment generator.

Models a CIP skid cycling through a 6-state phase sequence:
Idle → Pre-rinse → Caustic → Intermediate rinse → Acid wash → Final rinse → Idle.

The generator normally sits in Idle.  The CIP cycle scenario (Task 3.20) kicks
it into Pre-rinse by calling ``force_state("Pre_rinse")``.  From there the
generator auto-advances through each phase based on internal timers, then
returns to Idle when the final rinse completes.

5 signals:
- ``cip.state``              CIP cycle phase (0-5 enum), OPC-UA
- ``cip.wash_temp``          Wash solution temperature (15-85 °C), Modbus HR
- ``cip.flow_rate``          Wash flow rate (0-100 L/min), Modbus HR
- ``cip.conductivity``       Chemical concentration proxy (0-200 mS/cm), Modbus HR
- ``cip.cycle_time_elapsed`` Time since cycle start (0-7200 s), Modbus HR

Conductivity profile (PRD 2b.8):
- Idle / Pre-rinse / rinse phases: low, tracking 0-1 mS/cm
- Caustic wash: rises to 80-150 mS/cm (first-order lag, fast rise tau=60 s)
- Rinse phases: exponential decay back toward 0 (tau=120 s)
- Acid wash: moderate, ~40 mS/cm
- Final rinse must drop below 5 mS/cm to confirm clean

PRD Reference: Section 2b.8 (CIP equipment), Section 4.6 (F&B signal models)
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
CLAUDE.md Rule 9: No locks (single-threaded asyncio).
CLAUDE.md Rule 12: No global state.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

import math

import numpy as np

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.base import EquipmentGenerator
from factory_simulator.models.base import clamp
from factory_simulator.models.noise import NoiseGenerator
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# CIP states (PRD 2b.8)
# ---------------------------------------------------------------------------

STATE_IDLE = 0
STATE_PRE_RINSE = 1
STATE_CAUSTIC = 2
STATE_INTERMEDIATE = 3
STATE_ACID = 4
STATE_FINAL_RINSE = 5

_STATE_NAMES = ["Idle", "Pre_rinse", "Caustic", "Intermediate", "Acid", "Final_rinse"]

# Phase auto-advance sequence: each phase → next phase (Final → Idle)
_NEXT_PHASE: dict[int, int] = {
    STATE_PRE_RINSE: STATE_CAUSTIC,
    STATE_CAUSTIC: STATE_INTERMEDIATE,
    STATE_INTERMEDIATE: STATE_ACID,
    STATE_ACID: STATE_FINAL_RINSE,
    STATE_FINAL_RINSE: STATE_IDLE,
}

# ---------------------------------------------------------------------------
# Phase durations (seconds) — PRD 2b.8
# ---------------------------------------------------------------------------

# These are the *default* durations. The CIP scenario can override them.
_PHASE_DURATIONS: dict[int, float] = {
    STATE_PRE_RINSE: 300.0,    # 5 min
    STATE_CAUSTIC: 1080.0,     # 18 min  (PRD: 15-20 min)
    STATE_INTERMEDIATE: 300.0, # 5 min
    STATE_ACID: 750.0,         # 12.5 min (PRD: 10-15 min)
    STATE_FINAL_RINSE: 420.0,  # 7 min  (PRD: 5-10 min)
}

# ---------------------------------------------------------------------------
# Phase-specific signal targets
# ---------------------------------------------------------------------------

# Wash temperature setpoints per phase (°C)
_TEMP_TARGETS: dict[int, float] = {
    STATE_IDLE: 20.0,
    STATE_PRE_RINSE: 45.0,
    STATE_CAUSTIC: 75.0,
    STATE_INTERMEDIATE: 45.0,
    STATE_ACID: 65.0,
    STATE_FINAL_RINSE: 45.0,
}

# Flow rate targets per phase (L/min)
_FLOW_TARGETS: dict[int, float] = {
    STATE_IDLE: 0.0,
    STATE_PRE_RINSE: 60.0,
    STATE_CAUSTIC: 80.0,
    STATE_INTERMEDIATE: 60.0,
    STATE_ACID: 70.0,
    STATE_FINAL_RINSE: 60.0,
}

# Conductivity setpoints per phase (mS/cm)
_CONDUCTIVITY_TARGETS: dict[int, float] = {
    STATE_IDLE: 0.0,
    STATE_PRE_RINSE: 0.5,
    STATE_CAUSTIC: 120.0,
    STATE_INTERMEDIATE: 0.0,
    STATE_ACID: 40.0,
    STATE_FINAL_RINSE: 0.0,
}

# Temperature time constant (first-order lag, s)
_TEMP_TAU: float = 90.0

# Flow rate time constant (fast response, s)
_FLOW_TAU: float = 15.0

# Conductivity time constants
_CONDUCTIVITY_TAU_RISE: float = 60.0   # fast rise during caustic injection
_CONDUCTIVITY_TAU_DECAY: float = 120.0  # slower decay during rinse phases

# Conductivity threshold below which final rinse is considered passing
CONDUCTIVITY_RINSE_PASS_THRESHOLD: float = 5.0


class CipGenerator(EquipmentGenerator):
    """CIP skid generator -- 5 signals, 6-state phase sequence.

    The generator starts in Idle.  Call ``force_state("Pre_rinse")`` to
    begin a CIP cycle.  The generator then auto-advances through phases
    based on internal timers and returns to Idle after Final rinse.

    Public attributes for scenarios
    ---------------------------------
    state : int
        Current phase as integer 0-5.
    cycle_time_elapsed : float
        Seconds elapsed since the current cycle started.
    conductivity : float
        Current conductivity model value (mS/cm), noise-free.
    final_rinse_passed : bool
        True if the most recent final rinse achieved conductivity below
        CONDUCTIVITY_RINSE_PASS_THRESHOLD before completing.

    Parameters
    ----------
    equipment_id:
        Equipment prefix, typically ``"cip"``.
    config:
        CIP equipment config from YAML.
    rng:
        numpy random Generator (from SeedSequence).
    """

    def __init__(
        self,
        equipment_id: str,
        config: EquipmentConfig,
        rng: np.random.Generator,
    ) -> None:
        super().__init__(equipment_id, config, rng)

        # Current phase
        self._state: int = STATE_IDLE

        # Phase elapsed timer (seconds in current phase)
        self._phase_elapsed: float = 0.0

        # Total cycle elapsed timer (seconds since cycle started, Idle=0)
        self._cycle_elapsed: float = 0.0

        # Whether the current final rinse passed the conductivity check
        self._final_rinse_passed: bool = False

        # Wash temperature: internal value (first-order lag)
        self._wash_temp: float = _TEMP_TARGETS[STATE_IDLE]

        # Flow rate: internal value (first-order lag)
        self._flow_rate: float = 0.0

        # Conductivity: internal value (first-order lag with asymmetric tau)
        self._conductivity: float = 0.0

        # Noise generators
        self._temp_noise: NoiseGenerator | None = self._make_noise(
            config.signals["wash_temp"]
        ) if "wash_temp" in config.signals else None

        self._flow_noise: NoiseGenerator | None = self._make_noise(
            config.signals["flow_rate"]
        ) if "flow_rate" in config.signals else None

        self._conductivity_noise: NoiseGenerator | None = self._make_noise(
            config.signals["conductivity"]
        ) if "conductivity" in config.signals else None

        # Signal configs for clamp values
        self._wash_temp_cfg: SignalConfig | None = config.signals.get("wash_temp")
        self._flow_cfg: SignalConfig | None = config.signals.get("flow_rate")
        self._conductivity_cfg: SignalConfig | None = config.signals.get("conductivity")
        self._cycle_time_cfg: SignalConfig | None = config.signals.get(
            "cycle_time_elapsed"
        )

    # -- Public properties (for scenarios and tests) --------------------------

    @property
    def state(self) -> int:
        """Current CIP phase (0=Idle … 5=Final_rinse)."""
        return self._state

    @property
    def cycle_time_elapsed(self) -> float:
        """Seconds elapsed since the current CIP cycle started."""
        return self._cycle_elapsed

    @property
    def conductivity(self) -> float:
        """Current conductivity (mS/cm), noise-free internal value."""
        return self._conductivity

    @property
    def wash_temp(self) -> float:
        """Current wash temperature (°C), noise-free internal value."""
        return self._wash_temp

    @property
    def flow_rate(self) -> float:
        """Current flow rate (L/min), noise-free internal value."""
        return self._flow_rate

    @property
    def final_rinse_passed(self) -> bool:
        """True if last final-rinse phase achieved conductivity < 5 mS/cm."""
        return self._final_rinse_passed

    def force_state(self, state_name: str) -> None:
        """Force the CIP generator into a specific phase.

        Called by the CIP scenario (Task 3.20) to initiate a cycle.
        Resets the phase elapsed timer.  Entering any active phase from
        Idle also resets the cycle elapsed timer.

        Parameters
        ----------
        state_name:
            Phase name (case-insensitive): "Idle", "Pre_rinse", "Caustic",
            "Intermediate", "Acid", "Final_rinse".
        """
        target = _parse_state(state_name)
        if target == self._state:
            return

        # Entering a new cycle
        if self._state == STATE_IDLE and target != STATE_IDLE:
            self._cycle_elapsed = 0.0

        self._state = target
        self._phase_elapsed = 0.0

        # Reset pass flag when a new final rinse starts
        if target == STATE_FINAL_RINSE:
            self._final_rinse_passed = False

        # Immediately target correct wash temp and flow setpoints
        # (physical transition; values converge via lag)

    # -- EquipmentGenerator interface -----------------------------------------

    def get_signal_ids(self) -> list[str]:
        """Return all 5 CIP signal IDs."""
        return [self._signal_id(name) for name in self._signal_configs]

    def generate(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> list[SignalValue]:
        """Generate all CIP signals for one tick.

        Generation order:
        1. Advance phase elapsed timer; check for auto-phase transition.
        2. Update wash_temp via first-order lag to phase setpoint.
        3. Update flow_rate via first-order lag to phase setpoint.
        4. Update conductivity via asymmetric first-order lag.
        5. Increment cycle_time_elapsed (zero when Idle).
        6. Build and return SignalValue list.
        """
        # --- 1. Phase auto-advancement ---
        if self._state != STATE_IDLE:
            self._phase_elapsed += dt
            self._cycle_elapsed += dt

            phase_duration = _PHASE_DURATIONS.get(self._state, 300.0)
            if self._phase_elapsed >= phase_duration:
                next_state = _NEXT_PHASE.get(self._state, STATE_IDLE)
                # Track whether final rinse passed before transitioning
                if self._state == STATE_FINAL_RINSE:
                    self._final_rinse_passed = (
                        self._conductivity < CONDUCTIVITY_RINSE_PASS_THRESHOLD
                    )
                self._state = next_state
                self._phase_elapsed = 0.0
                if self._state == STATE_IDLE:
                    # Cycle complete; keep cycle_elapsed for last reading
                    pass

        # --- 2. Wash temperature (first-order lag) ---
        temp_setpoint = _TEMP_TARGETS[self._state]
        alpha_temp = 1.0 - math.exp(-dt / _TEMP_TAU)
        self._wash_temp += (temp_setpoint - self._wash_temp) * alpha_temp

        wash_temp_out = self._wash_temp
        if self._temp_noise is not None:
            wash_temp_out += self._temp_noise.sample()
        if self._wash_temp_cfg is not None:
            wash_temp_out = clamp(
                wash_temp_out,
                self._wash_temp_cfg.min_clamp,
                self._wash_temp_cfg.max_clamp,
            )

        # --- 3. Flow rate (first-order lag, fast) ---
        flow_setpoint = _FLOW_TARGETS[self._state]
        alpha_flow = 1.0 - math.exp(-dt / _FLOW_TAU)
        self._flow_rate += (flow_setpoint - self._flow_rate) * alpha_flow

        flow_out = self._flow_rate
        if self._flow_noise is not None:
            flow_out += self._flow_noise.sample()
        if self._flow_cfg is not None:
            flow_out = clamp(
                flow_out,
                self._flow_cfg.min_clamp,
                self._flow_cfg.max_clamp,
            )

        # --- 4. Conductivity (asymmetric lag) ---
        # Rising (caustic injection, acid) uses fast tau.
        # Decaying (rinse phases) uses slow tau.
        cond_setpoint = _CONDUCTIVITY_TARGETS[self._state]
        if cond_setpoint > self._conductivity:
            tau_cond = _CONDUCTIVITY_TAU_RISE
        else:
            tau_cond = _CONDUCTIVITY_TAU_DECAY
        alpha_cond = 1.0 - math.exp(-dt / tau_cond)
        self._conductivity += (cond_setpoint - self._conductivity) * alpha_cond
        # Clamp to >= 0
        if self._conductivity < 0.0:
            self._conductivity = 0.0

        conductivity_out = self._conductivity
        if self._conductivity_noise is not None:
            conductivity_out += self._conductivity_noise.sample()
        if self._conductivity_cfg is not None:
            conductivity_out = clamp(
                conductivity_out,
                self._conductivity_cfg.min_clamp,
                self._conductivity_cfg.max_clamp,
            )

        # --- 5. Cycle time elapsed ---
        # Resets to 0 while Idle; holds last value when transitioning to Idle
        cycle_time_out: float
        if self._state == STATE_IDLE:
            # Emit 0.0 when truly idle (cycle not running)
            # Note: _cycle_elapsed retains value for 1 tick after completion,
            # then resets next tick once state is confirmed Idle.
            self._cycle_elapsed = 0.0
            cycle_time_out = 0.0
        else:
            cycle_time_out = self._cycle_elapsed

        if self._cycle_time_cfg is not None:
            cycle_time_out = clamp(
                cycle_time_out,
                self._cycle_time_cfg.min_clamp,
                self._cycle_time_cfg.max_clamp,
            )

        # --- 6. Build results ---
        return [
            self._make_sv("state", float(self._state), sim_time),
            self._make_sv("wash_temp", wash_temp_out, sim_time),
            self._make_sv("flow_rate", flow_out, sim_time),
            self._make_sv("conductivity", conductivity_out, sim_time),
            self._make_sv("cycle_time_elapsed", cycle_time_out, sim_time),
        ]

    # -- Helper ---------------------------------------------------------------

    def _make_sv(
        self,
        signal_name: str,
        value: float,
        sim_time: float,
    ) -> SignalValue:
        """Create a SignalValue with fully qualified signal ID."""
        return SignalValue(
            signal_id=self._signal_id(signal_name),
            value=value,
            timestamp=sim_time,
            quality="good",
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_state(name: str) -> int:
    """Convert a state name string to integer state constant.

    Case-insensitive.  Accepts both ``"Pre_rinse"`` and ``"pre_rinse"``.

    Raises
    ------
    ValueError
        If *name* does not match any known CIP state.
    """
    normalised = name.lower().replace(" ", "_").replace("-", "_")
    lookup = {
        "idle": STATE_IDLE,
        "pre_rinse": STATE_PRE_RINSE,
        "caustic": STATE_CAUSTIC,
        "caustic_wash": STATE_CAUSTIC,
        "intermediate": STATE_INTERMEDIATE,
        "intermediate_rinse": STATE_INTERMEDIATE,
        "acid": STATE_ACID,
        "acid_wash": STATE_ACID,
        "final_rinse": STATE_FINAL_RINSE,
        "final": STATE_FINAL_RINSE,
    }
    if normalised not in lookup:
        raise ValueError(
            f"Unknown CIP state: {name!r}. "
            f"Valid names: {list(lookup.keys())}"
        )
    return lookup[normalised]
