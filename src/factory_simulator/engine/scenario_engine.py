"""Scenario engine -- schedules and evaluates scenarios per tick.

The ScenarioEngine owns the scenario timeline.  On construction it
auto-schedules all enabled scenario types using config-driven frequency
profiles, so a default simulator run produces realistic scenario data
without manual ``add_scenario()`` calls.

On each tick it:

1. Checks whether any pending scenarios should activate.
2. Advances active scenarios (calling their ``evaluate()``).
3. Removes completed scenarios from the active set.

Ten packaging scenario types are auto-scheduled across two categories:

**Phase 1 (time-based):**
  UnplannedStop, JobChangeover, ShiftChange

**Phase 2 time-based:**
  WebBreak, DryerDrift, InkExcursion, RegistrationDrift, ColdStart

**Phase 2 condition-triggered:**
  CoderDepletion (monitors ink level), MaterialSplice (monitors unwind diameter)

Seven F&B scenario types are auto-scheduled (Phase 3):
  BatchCycle, OvenThermalExcursion, FillWeightDrift, SealIntegrityFailure,
  ChillerDoorAlarm, CipCycle, ColdChainBreak

Scheduling uses Poisson inter-arrival times for frequency-based scenarios
(Phase 4).  Shift changes use fixed times with jitter; condition-triggered
scenarios (CoderDepletion, MaterialSplice) use evenly-spaced monitoring
windows.

The DataEngine calls ``scenario_engine.tick()`` *before* running
generators so that state changes from scenarios are visible to the
current tick's signal generation (PRD 8.2 step 3).

PRD Reference: Section 5.13 (Scenario Scheduling), 5.14 (F&B Scenarios)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from factory_simulator.scenarios.base import Scenario, ScenarioPhase
from factory_simulator.scenarios.batch_cycle import BatchCycle
from factory_simulator.scenarios.chiller_door_alarm import ChillerDoorAlarm
from factory_simulator.scenarios.cip_cycle import CipCycle
from factory_simulator.scenarios.coder_depletion import CoderDepletion
from factory_simulator.scenarios.cold_chain_break import ColdChainBreak
from factory_simulator.scenarios.cold_start import ColdStart
from factory_simulator.scenarios.dryer_drift import DryerDrift
from factory_simulator.scenarios.fill_weight_drift import FillWeightDrift
from factory_simulator.scenarios.ink_excursion import InkExcursion
from factory_simulator.scenarios.job_changeover import JobChangeover
from factory_simulator.scenarios.material_splice import MaterialSplice
from factory_simulator.scenarios.oven_thermal_excursion import OvenThermalExcursion
from factory_simulator.scenarios.registration_drift import RegistrationDrift
from factory_simulator.scenarios.seal_integrity import SealIntegrityFailure
from factory_simulator.scenarios.shift_change import ShiftChange
from factory_simulator.scenarios.unplanned_stop import UnplannedStop
from factory_simulator.scenarios.web_break import WebBreak

if TYPE_CHECKING:
    from factory_simulator.config import ScenariosConfig, ShiftsConfig
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.engine.ground_truth import GroundTruthLogger

logger = logging.getLogger(__name__)

# Time constants used for scheduling frequency calculations
_SHIFT_SECONDS = 8 * 3600       # 8-hour shift
_DAY_SECONDS = 86400             # 24-hour day
_WEEK_SECONDS = 7 * _DAY_SECONDS  # 7-day week
_MONTH_SECONDS = 30 * _DAY_SECONDS  # 30-day month (approximate)


class ScenarioEngine:
    """Schedules and evaluates scenarios per simulation tick.

    Parameters
    ----------
    scenarios_config:
        Scenario configuration from the YAML config.
    shifts_config:
        Shift configuration for shift-change scheduling.
    rng:
        numpy random Generator (from SeedSequence, Rule 13).
    sim_duration_s:
        Total planned simulation duration in seconds (default: 1 shift).
        Used to generate the scenario timeline.
    """

    def __init__(
        self,
        scenarios_config: ScenariosConfig,
        shifts_config: ShiftsConfig,
        rng: np.random.Generator,
        sim_duration_s: float = _SHIFT_SECONDS,
        ground_truth: GroundTruthLogger | None = None,
    ) -> None:
        self._config = scenarios_config
        self._shifts = shifts_config
        self._rng = rng
        self._sim_duration_s = sim_duration_s
        self._ground_truth = ground_truth

        # Y1 fix: use SeedSequence.spawn() for child RNG creation
        # bit_generator.seed_seq is typed as ISeedSequence; we know it's
        # a concrete SeedSequence because we create our RNGs via default_rng.
        seed_seq = rng.bit_generator.seed_seq
        assert isinstance(seed_seq, np.random.SeedSequence)
        self._seed_seq: np.random.SeedSequence = seed_seq

        self._scenarios: list[Scenario] = []
        self._generate_timeline()

    @property
    def scenarios(self) -> list[Scenario]:
        """All scheduled scenarios (pending, active, and completed)."""
        return list(self._scenarios)

    @property
    def active_scenarios(self) -> list[Scenario]:
        """Currently active scenarios."""
        return [s for s in self._scenarios if s.phase == ScenarioPhase.ACTIVE]

    @property
    def pending_scenarios(self) -> list[Scenario]:
        """Scenarios waiting to start."""
        return [s for s in self._scenarios if s.phase == ScenarioPhase.PENDING]

    @property
    def completed_scenarios(self) -> list[Scenario]:
        """Scenarios that have finished."""
        return [s for s in self._scenarios if s.phase == ScenarioPhase.COMPLETED]

    def add_scenario(self, scenario: Scenario) -> None:
        """Add a scenario to the timeline (for manual scheduling)."""
        self._scenarios.append(scenario)

    def tick(self, sim_time: float, dt: float, engine: DataEngine) -> None:
        """Evaluate all scenarios for the current tick.

        Called by the DataEngine before running generators.
        Logs scenario_start and scenario_end events to ground truth.
        """
        for scenario in self._scenarios:
            if scenario.phase == ScenarioPhase.COMPLETED:
                continue

            phase_before = scenario.phase
            scenario.evaluate(sim_time, dt, engine)
            # evaluate() may mutate phase; re-read and annotate to
            # prevent mypy from narrowing based on the COMPLETED guard.
            phase_after: ScenarioPhase = scenario.phase

            # Detect PENDING -> ACTIVE transition (scenario_start)
            if (
                phase_before == ScenarioPhase.PENDING
                and phase_after in (ScenarioPhase.ACTIVE, ScenarioPhase.COMPLETED)
                and self._ground_truth is not None
            ):
                self._ground_truth.log_scenario_start(
                    sim_time=sim_time,
                    scenario_name=type(scenario).__name__,
                    affected_signals=_get_affected_signals(scenario),
                    parameters=_get_scenario_params(scenario),
                )

            # Detect -> COMPLETED transition (scenario_end).
            # phase_before is always PENDING or ACTIVE here (COMPLETED
            # scenarios are skipped by the ``continue`` guard above).
            if (
                phase_after == ScenarioPhase.COMPLETED
                and self._ground_truth is not None
            ):
                self._ground_truth.log_scenario_end(
                    sim_time=sim_time,
                    scenario_name=type(scenario).__name__,
                )

    # -- Poisson scheduling helper --------------------------------------------

    def _poisson_starts(
        self,
        freq_range: list[int] | list[float],
        period_s: float,
        min_gap_s: float,
    ) -> list[float]:
        """Generate Poisson inter-arrival start times.

        Parameters
        ----------
        freq_range:
            ``[min_freq, max_freq]`` average events per *period_s*.
        period_s:
            Length of the frequency period in seconds (e.g. shift, week).
        min_gap_s:
            Minimum time between consecutive instances (prevents overlap
            of the same scenario type).

        Returns
        -------
        list[float]
            Start times within ``[0, sim_duration_s)``, unsorted.
        """
        min_f = float(freq_range[0])
        max_f = float(freq_range[1])
        mean_freq = (min_f + max_f) / 2.0
        if mean_freq <= 0:
            return []

        mean_interval = period_s / mean_freq

        starts: list[float] = []
        t = 0.0
        while t < self._sim_duration_s:
            gap = float(self._rng.exponential(mean_interval))
            t += max(gap, min_gap_s)
            if t < self._sim_duration_s:
                starts.append(t)
        return starts

    # -- Timeline generation ---------------------------------------------------

    def _generate_timeline(self) -> None:
        """Generate a random scenario timeline from config.

        Frequency-based scenarios use Poisson inter-arrival times (PRD 5.13).
        Shift changes use fixed times with jitter (PRD 5.9).
        Condition-triggered scenarios use evenly-spaced monitoring windows.
        """
        # Phase 1 scenarios
        self._schedule_unplanned_stops()
        self._schedule_job_changeovers()
        self._schedule_shift_changes()

        # Phase 2 time-based scenarios
        self._schedule_web_breaks()
        self._schedule_dryer_drifts()
        self._schedule_ink_excursions()
        self._schedule_registration_drifts()
        self._schedule_cold_starts()

        # Phase 2 condition-triggered scenarios (monitoring windows, not Poisson)
        self._schedule_coder_depletions()
        self._schedule_material_splices()

        # Phase 3 F&B time-based scenarios (only scheduled when F&B config present)
        self._schedule_batch_cycles()
        self._schedule_oven_thermal_excursions()
        self._schedule_fill_weight_drifts()
        self._schedule_seal_integrity_failures()
        self._schedule_chiller_door_alarms()
        self._schedule_cip_cycles()
        self._schedule_cold_chain_breaks()

        # Sort by start time for orderly evaluation
        self._scenarios.sort(key=lambda s: s.start_time)

        logger.info(
            "Scenario timeline generated: %d scenarios over %.0f seconds",
            len(self._scenarios),
            self._sim_duration_s,
        )

    # -- Packaging scenario scheduling (Poisson) --------------------------------

    def _schedule_unplanned_stops(self) -> None:
        """Schedule unplanned stops using Poisson inter-arrival (PRD 5.8)."""
        cfg = self._config.unplanned_stop
        if not cfg.enabled:
            return

        min_gap = float(cfg.duration_seconds[0])
        for start in self._poisson_starts(cfg.frequency_per_shift, _SHIFT_SECONDS, min_gap):
            params: dict[str, object] = {
                "duration_seconds": list(cfg.duration_seconds),
            }
            self._scenarios.append(
                UnplannedStop(start_time=start, rng=self._spawn_rng(), params=params)
            )

    def _schedule_job_changeovers(self) -> None:
        """Schedule job changeovers using Poisson inter-arrival (PRD 5.2)."""
        cfg = self._config.job_changeover
        if not cfg.enabled:
            return

        min_gap = float(cfg.duration_seconds[0])
        for start in self._poisson_starts(cfg.frequency_per_shift, _SHIFT_SECONDS, min_gap):
            params: dict[str, object] = {
                "duration_seconds": list(cfg.duration_seconds),
                "speed_change_probability": cfg.speed_change_probability,
                "counter_reset_probability": cfg.counter_reset_probability,
            }
            self._scenarios.append(
                JobChangeover(start_time=start, rng=self._spawn_rng(), params=params)
            )

    def _schedule_shift_changes(self) -> None:
        """Schedule shift changes at configured times with jitter.

        Shift changes happen 3x per day at configured times with
        ±10 min jitter (PRD 5.9).  Not Poisson — fixed schedule.
        """
        cfg = self._config.shift_change
        if not cfg.enabled:
            return

        # Convert shift times to seconds-of-day
        shift_times_s: list[float] = []
        for time_str in cfg.times:
            parts = time_str.split(":")
            h, m = int(parts[0]), int(parts[1])
            shift_times_s.append(h * 3600 + m * 60)

        # Determine operator biases per shift
        operators = self._shifts.operators
        shift_names = list(operators.keys())

        # Schedule for each day in the simulation
        n_days = max(1, int(self._sim_duration_s / 86400) + 1)
        for day in range(n_days):
            for i, base_time_s in enumerate(shift_times_s):
                # Apply ±10 minute jitter (PRD 5.9)
                jitter = float(self._rng.uniform(-600, 600))
                start = day * 86400 + base_time_s + jitter

                if start < 0 or start >= self._sim_duration_s:
                    continue

                # Determine which shift is being entered
                shift_idx = i % len(shift_names)
                shift_name = shift_names[shift_idx]
                op = operators[shift_name]

                params: dict[str, object] = {
                    "changeover_seconds": list(cfg.changeover_seconds),
                    "speed_bias": op.speed_bias,
                    "waste_rate_bias": op.waste_rate_bias,
                    "shift_name": shift_name,
                }

                scenario = ShiftChange(
                    start_time=start,
                    rng=self._spawn_rng(),
                    params=params,
                )
                self._scenarios.append(scenario)

    def _schedule_web_breaks(self) -> None:
        """Schedule web breaks using Poisson inter-arrival (PRD 5.3)."""
        cfg = self._config.web_break
        if not cfg.enabled:
            return

        min_gap = float(cfg.recovery_seconds[0])
        for start in self._poisson_starts(cfg.frequency_per_week, _WEEK_SECONDS, min_gap):
            params: dict[str, object] = {
                "recovery_seconds": list(cfg.recovery_seconds),
            }
            self._scenarios.append(
                WebBreak(start_time=start, rng=self._spawn_rng(), params=params)
            )

    def _schedule_dryer_drifts(self) -> None:
        """Schedule dryer temperature drifts using Poisson inter-arrival (PRD 5.4)."""
        cfg = self._config.dryer_drift
        if not cfg.enabled:
            return

        min_gap = float(cfg.duration_seconds[0])
        for start in self._poisson_starts(cfg.frequency_per_shift, _SHIFT_SECONDS, min_gap):
            params: dict[str, object] = {
                "drift_duration_range": list(cfg.duration_seconds),
                "drift_range": list(cfg.max_drift_c),
            }
            self._scenarios.append(
                DryerDrift(start_time=start, rng=self._spawn_rng(), params=params)
            )

    def _schedule_ink_excursions(self) -> None:
        """Schedule ink viscosity excursions using Poisson inter-arrival (PRD 5.6)."""
        cfg = self._config.ink_viscosity_excursion
        if not cfg.enabled:
            return

        min_gap = float(cfg.duration_seconds[0])
        for start in self._poisson_starts(cfg.frequency_per_shift, _SHIFT_SECONDS, min_gap):
            params: dict[str, object] = {
                "duration_range": list(cfg.duration_seconds),
            }
            self._scenarios.append(
                InkExcursion(start_time=start, rng=self._spawn_rng(), params=params)
            )

    def _schedule_registration_drifts(self) -> None:
        """Schedule registration drifts using Poisson inter-arrival (PRD 5.7)."""
        cfg = self._config.registration_drift
        if not cfg.enabled:
            return

        min_gap = float(cfg.duration_seconds[0])
        for start in self._poisson_starts(cfg.frequency_per_shift, _SHIFT_SECONDS, min_gap):
            params: dict[str, object] = {
                "duration_range": list(cfg.duration_seconds),
            }
            self._scenarios.append(
                RegistrationDrift(
                    start_time=start, rng=self._spawn_rng(), params=params
                )
            )

    def _schedule_cold_starts(self) -> None:
        """Schedule cold start monitoring using Poisson inter-arrival (PRD 5.10).

        ColdStart is reactive -- it monitors press state for idle-to-active
        transitions.  We schedule monitoring instances using Poisson
        inter-arrival; each watches for one qualifying trigger.
        """
        cfg = self._config.cold_start_spike
        if not cfg.enabled:
            return

        # 1-2 per day → Poisson with mean_interval ~ 12-24h
        min_gap = float(cfg.spike_duration_seconds[0])
        for start in self._poisson_starts([1, 2], _DAY_SECONDS, min_gap):
            params: dict[str, object] = {
                "spike_duration_range": list(cfg.spike_duration_seconds),
                "power_multiplier_range": list(cfg.spike_magnitude),
                "idle_threshold_s": cfg.idle_threshold_minutes * 60.0,
            }
            self._scenarios.append(
                ColdStart(start_time=start, rng=self._spawn_rng(), params=params)
            )

    # -- Condition-triggered scenarios (monitoring windows, not Poisson) --------

    def _schedule_coder_depletions(self) -> None:
        """Schedule coder depletion monitoring instances (PRD 5.12).

        CoderDepletion monitors ink level continuously.  We schedule one
        monitoring instance per ~24h of sim time at evenly spaced start
        times; each watches for one depletion-refill cycle.
        """
        cfg = self._config.coder_depletion
        if not cfg.enabled:
            return

        n_instances = max(1, round(self._sim_duration_s / 86400))

        for i in range(n_instances):
            start = float(i * self._sim_duration_s / n_instances)
            params: dict[str, object] = {
                "low_ink_threshold": cfg.low_ink_threshold,
                "empty_threshold": cfg.empty_threshold,
                "recovery_duration_range": list(cfg.recovery_duration_seconds),
            }
            self._scenarios.append(
                CoderDepletion(
                    start_time=start, rng=self._spawn_rng(), params=params
                )
            )

    def _schedule_material_splices(self) -> None:
        """Schedule material splice monitoring instances (PRD 5.13a).

        MaterialSplice monitors unwind diameter.  We schedule one
        monitoring instance per ~3h of sim time at evenly spaced start
        times; each watches for one splice event.
        """
        cfg = self._config.material_splice
        if not cfg.enabled:
            return

        _SPLICE_INTERVAL_S = 3 * 3600
        n_instances = max(1, round(self._sim_duration_s / _SPLICE_INTERVAL_S))

        for i in range(n_instances):
            start = float(i * self._sim_duration_s / n_instances)
            params: dict[str, object] = {
                "trigger_diameter": cfg.trigger_diameter_mm,
                "splice_duration_range": list(cfg.splice_duration_seconds),
            }
            self._scenarios.append(
                MaterialSplice(
                    start_time=start, rng=self._spawn_rng(), params=params
                )
            )

    # -- Phase 3: F&B scenario scheduling (Poisson) ----------------------------

    def _schedule_batch_cycles(self) -> None:
        """Schedule batch cycles using Poisson inter-arrival (PRD 5.14.1)."""
        cfg = self._config.batch_cycle
        if cfg is None or not cfg.enabled:
            return

        min_gap = float(cfg.batch_duration_seconds[0])
        for start in self._poisson_starts(cfg.frequency_per_shift, _SHIFT_SECONDS, min_gap):
            params: dict[str, object] = {
                "batch_duration_range": list(cfg.batch_duration_seconds),
            }
            self._scenarios.append(
                BatchCycle(start_time=start, rng=self._spawn_rng(), params=params)
            )

    def _schedule_oven_thermal_excursions(self) -> None:
        """Schedule oven thermal excursions using Poisson inter-arrival (PRD 5.14.2)."""
        cfg = self._config.oven_thermal_excursion
        if cfg is None or not cfg.enabled:
            return

        min_gap = float(cfg.duration_seconds[0])
        for start in self._poisson_starts(cfg.frequency_per_shift, _SHIFT_SECONDS, min_gap):
            params: dict[str, object] = {
                "drift_duration_range": list(cfg.duration_seconds),
                "drift_range": list(cfg.max_drift_c),
            }
            self._scenarios.append(
                OvenThermalExcursion(
                    start_time=start, rng=self._spawn_rng(), params=params
                )
            )

    def _schedule_fill_weight_drifts(self) -> None:
        """Schedule fill weight drifts using Poisson inter-arrival (PRD 5.14.3)."""
        cfg = self._config.fill_weight_drift
        if cfg is None or not cfg.enabled:
            return

        min_gap = float(cfg.duration_seconds[0])
        for start in self._poisson_starts(cfg.frequency_per_shift, _SHIFT_SECONDS, min_gap):
            params: dict[str, object] = {
                "drift_duration_range": list(cfg.duration_seconds),
                "drift_rate_range": list(cfg.drift_rate),
            }
            self._scenarios.append(
                FillWeightDrift(
                    start_time=start, rng=self._spawn_rng(), params=params
                )
            )

    def _schedule_seal_integrity_failures(self) -> None:
        """Schedule seal integrity failures using Poisson inter-arrival (PRD 5.14.4)."""
        cfg = self._config.seal_integrity_failure
        if cfg is None or not cfg.enabled:
            return

        min_gap = float(cfg.duration_seconds[0])
        for start in self._poisson_starts(cfg.frequency_per_week, _WEEK_SECONDS, min_gap):
            params: dict[str, object] = {
                "duration_range": list(cfg.duration_seconds),
            }
            self._scenarios.append(
                SealIntegrityFailure(
                    start_time=start, rng=self._spawn_rng(), params=params
                )
            )

    def _schedule_chiller_door_alarms(self) -> None:
        """Schedule chiller door alarms using Poisson inter-arrival (PRD 5.14.5)."""
        cfg = self._config.chiller_door_alarm
        if cfg is None or not cfg.enabled:
            return

        min_gap = float(cfg.duration_seconds[0])
        for start in self._poisson_starts(cfg.frequency_per_week, _WEEK_SECONDS, min_gap):
            params: dict[str, object] = {
                "duration_range": list(cfg.duration_seconds),
            }
            self._scenarios.append(
                ChillerDoorAlarm(
                    start_time=start, rng=self._spawn_rng(), params=params
                )
            )

    def _schedule_cip_cycles(self) -> None:
        """Schedule CIP cycles using Poisson inter-arrival (PRD 5.14.6)."""
        cfg = self._config.cip_cycle
        if cfg is None or not cfg.enabled:
            return

        min_gap = float(cfg.cycle_duration_seconds[0])
        for start in self._poisson_starts(cfg.frequency_per_day, _DAY_SECONDS, min_gap):
            params: dict[str, object] = {
                "cycle_duration_range": list(cfg.cycle_duration_seconds),
            }
            self._scenarios.append(
                CipCycle(start_time=start, rng=self._spawn_rng(), params=params)
            )

    def _schedule_cold_chain_breaks(self) -> None:
        """Schedule cold chain breaks using Poisson inter-arrival (PRD 5.14.7)."""
        cfg = self._config.cold_chain_break
        if cfg is None or not cfg.enabled:
            return

        min_gap = float(cfg.duration_seconds[0])
        for start in self._poisson_starts(cfg.frequency_per_month, _MONTH_SECONDS, min_gap):
            params: dict[str, object] = {
                "duration_range": list(cfg.duration_seconds),
            }
            self._scenarios.append(
                ColdChainBreak(
                    start_time=start, rng=self._spawn_rng(), params=params
                )
            )

    # -- Child RNG creation ----------------------------------------------------

    def _spawn_rng(self) -> np.random.Generator:
        """Create a child RNG using SeedSequence.spawn (Rule 13, Y1 fix)."""
        child_ss = self._seed_seq.spawn(1)[0]
        return np.random.default_rng(child_ss)


# ---------------------------------------------------------------------------
# Ground truth helpers -- extract metadata from scenario instances
# ---------------------------------------------------------------------------

# Scenario class name -> list of affected signal IDs.
# These are the signals the PRD says each scenario type modifies.
_AFFECTED_SIGNALS: dict[str, list[str]] = {
    # -- Packaging scenarios (Phase 1 & 2) ------------------------------------
    "WebBreak": [
        "press.web_tension", "press.line_speed",
        "press.machine_state", "press.web_break", "press.fault_active",
    ],
    "DryerDrift": [
        "press.dryer_temp_zone_1", "press.dryer_temp_zone_2",
        "press.dryer_temp_zone_3", "press.waste_count",
    ],
    "InkExcursion": [
        "press.ink_viscosity", "press.registration_error_x",
        "press.registration_error_y", "press.waste_count",
    ],
    "RegistrationDrift": [
        "press.registration_error_x", "press.registration_error_y",
        "press.waste_count",
    ],
    "ColdStart": [
        "energy.line_power", "press.main_drive_current",
    ],
    "CoderDepletion": [
        "coder.ink_level", "coder.state",
    ],
    "MaterialSplice": [
        "press.web_tension", "press.registration_error_x",
        "press.registration_error_y", "press.unwind_diameter",
        "press.line_speed", "press.waste_count",
    ],
    "UnplannedStop": [
        "press.machine_state", "press.line_speed",
    ],
    "JobChangeover": [
        "press.machine_state", "press.line_speed",
        "press.impression_count", "press.good_count", "press.waste_count",
    ],
    "ShiftChange": [
        "press.machine_state", "press.line_speed",
    ],
    # -- F&B scenarios (Phase 3) ----------------------------------------------
    "BatchCycle": [
        "mixer.state", "mixer.speed", "mixer.torque",
        "mixer.batch_temp", "mixer.batch_weight", "mixer.batch_id",
        "mixer.mix_time_elapsed", "mixer.lid_closed",
    ],
    "OvenThermalExcursion": [
        "oven.zone_1_temp", "oven.zone_2_temp", "oven.zone_3_temp",
        "oven.product_core_temp",
    ],
    "FillWeightDrift": [
        "filler.fill_weight", "filler.fill_deviation", "filler.reject_count",
    ],
    "SealIntegrityFailure": [
        "sealer.seal_temp", "sealer.seal_pressure",
        "sealer.vacuum_level", "qc.reject_total",
    ],
    "ChillerDoorAlarm": [
        "chiller.door_open", "chiller.room_temp", "chiller.compressor_state",
    ],
    "CipCycle": [
        "cip.state", "cip.wash_temp", "cip.conductivity",
        "cip.flow_rate", "cip.cycle_time_elapsed",
        "mixer.state", "filler.state",
    ],
    "ColdChainBreak": [
        "chiller.compressor_state", "chiller.room_temp",
    ],
}


def _get_affected_signals(scenario: Scenario) -> list[str]:
    """Return the list of signals affected by *scenario*."""
    name = type(scenario).__name__
    return list(_AFFECTED_SIGNALS.get(name, []))


def _get_scenario_params(scenario: Scenario) -> dict[str, object]:
    """Extract loggable parameters from *scenario*.

    Returns a flat dict of key numeric/string parameters suitable for
    JSON serialisation.  Uses duck-typing to pull common attributes.
    """
    params: dict[str, object] = {}
    params["duration"] = scenario.duration()

    # Scenario-specific attributes (best-effort extraction)
    for attr in (
        "recovery_duration", "spike_tension", "spike_duration",
        "decel_duration", "shift_name",
    ):
        val = getattr(scenario, attr, None)
        if val is not None:
            params[attr] = val

    return params
