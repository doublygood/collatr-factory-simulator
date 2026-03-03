"""Scenario engine -- schedules and evaluates scenarios per tick.

The ScenarioEngine owns the scenario timeline.  On construction it
auto-schedules all enabled scenario types using config-driven frequency
profiles, so a default simulator run produces realistic scenario data
without manual ``add_scenario()`` calls.

On each tick it:

1. Checks whether any pending scenarios should activate.
2. Advances active scenarios (calling their ``evaluate()``).
3. Removes completed scenarios from the active set.

Ten scenario types are auto-scheduled across two categories:

**Phase 1 (time-based):**
  UnplannedStop, JobChangeover, ShiftChange

**Phase 2 time-based:**
  WebBreak, DryerDrift, InkExcursion, RegistrationDrift, ColdStart

**Phase 2 condition-triggered:**
  CoderDepletion (monitors ink level), MaterialSplice (monitors unwind diameter)

Scheduling uses simple frequency-based uniform-random start times.
Full Poisson inter-arrival times with priority rules are deferred to
Phase 4 per PRD Appendix F.

The DataEngine calls ``scenario_engine.tick()`` *before* running
generators so that state changes from scenarios are visible to the
current tick's signal generation (PRD 8.2 step 3).

PRD Reference: Section 5.13 (Scenario Scheduling)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from factory_simulator.scenarios.base import Scenario, ScenarioPhase
from factory_simulator.scenarios.coder_depletion import CoderDepletion
from factory_simulator.scenarios.cold_start import ColdStart
from factory_simulator.scenarios.dryer_drift import DryerDrift
from factory_simulator.scenarios.ink_excursion import InkExcursion
from factory_simulator.scenarios.job_changeover import JobChangeover
from factory_simulator.scenarios.material_splice import MaterialSplice
from factory_simulator.scenarios.registration_drift import RegistrationDrift
from factory_simulator.scenarios.shift_change import ShiftChange
from factory_simulator.scenarios.unplanned_stop import UnplannedStop
from factory_simulator.scenarios.web_break import WebBreak

if TYPE_CHECKING:
    from factory_simulator.config import ScenariosConfig, ShiftsConfig
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.engine.ground_truth import GroundTruthLogger

logger = logging.getLogger(__name__)

# 8-hour shift in seconds
_SHIFT_SECONDS = 8 * 3600


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

    # -- Timeline generation ---------------------------------------------------

    def _generate_timeline(self) -> None:
        """Generate a random scenario timeline from config.

        Schedules scenarios based on their configured frequency and
        duration ranges.  Scenarios are spread across the simulation
        duration with random spacing.

        Phase 1 scenarios: unplanned stops, job changeovers, shift changes.
        Phase 2 scenarios: WebBreak, DryerDrift, InkExcursion,
        RegistrationDrift, ColdStart, CoderDepletion, MaterialSplice.

        Uses simple frequency-based scheduling with uniform-random start
        times.  Full Poisson inter-arrival times and priority rules are
        deferred to Phase 4 per PRD Appendix F.
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

        # Phase 2 condition-triggered scenarios
        self._schedule_coder_depletions()
        self._schedule_material_splices()

        # Sort by start time for orderly evaluation
        self._scenarios.sort(key=lambda s: s.start_time)

        logger.info(
            "Scenario timeline generated: %d scenarios over %.0f seconds",
            len(self._scenarios),
            self._sim_duration_s,
        )

    def _schedule_unplanned_stops(self) -> None:
        """Schedule unplanned stops based on config frequency."""
        cfg = self._config.unplanned_stop
        if not cfg.enabled:
            return

        n_shifts = max(1, self._sim_duration_s / _SHIFT_SECONDS)

        # Draw number of stops per shift
        min_f, max_f = cfg.frequency_per_shift
        n_stops = round(self._rng.uniform(min_f, max_f) * n_shifts)

        for _ in range(n_stops):
            start = float(self._rng.uniform(0, self._sim_duration_s))
            params: dict[str, object] = {
                "duration_seconds": list(cfg.duration_seconds),
            }
            scenario = UnplannedStop(
                start_time=start,
                rng=self._spawn_rng(),
                params=params,
            )
            self._scenarios.append(scenario)

    def _schedule_job_changeovers(self) -> None:
        """Schedule job changeovers based on config frequency."""
        cfg = self._config.job_changeover
        if not cfg.enabled:
            return

        n_shifts = max(1, self._sim_duration_s / _SHIFT_SECONDS)

        min_f, max_f = cfg.frequency_per_shift
        n_changeovers = round(self._rng.uniform(min_f, max_f) * n_shifts)

        for _ in range(n_changeovers):
            start = float(self._rng.uniform(0, self._sim_duration_s))
            params = {
                "duration_seconds": list(cfg.duration_seconds),
                "speed_change_probability": cfg.speed_change_probability,
                "counter_reset_probability": cfg.counter_reset_probability,
            }
            scenario = JobChangeover(
                start_time=start,
                rng=self._spawn_rng(),
                params=params,
            )
            self._scenarios.append(scenario)

    def _schedule_shift_changes(self) -> None:
        """Schedule shift changes at configured times with jitter.

        Shift changes happen 3x per day at configured times with
        ±10 min jitter (PRD 5.9).
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
        """Schedule web breaks based on config frequency (PRD 5.3)."""
        cfg = self._config.web_break
        if not cfg.enabled:
            return

        _WEEK_SECONDS = 7 * 86400
        n_weeks = max(1.0, self._sim_duration_s / _WEEK_SECONDS)

        min_f, max_f = cfg.frequency_per_week
        n_breaks = round(self._rng.uniform(min_f, max_f) * n_weeks)

        for _ in range(n_breaks):
            start = float(self._rng.uniform(0, self._sim_duration_s))
            params: dict[str, object] = {
                "recovery_seconds": list(cfg.recovery_seconds),
            }
            self._scenarios.append(
                WebBreak(start_time=start, rng=self._spawn_rng(), params=params)
            )

    def _schedule_dryer_drifts(self) -> None:
        """Schedule dryer temperature drifts based on config frequency (PRD 5.4)."""
        cfg = self._config.dryer_drift
        if not cfg.enabled:
            return

        n_shifts = max(1.0, self._sim_duration_s / _SHIFT_SECONDS)

        min_f, max_f = cfg.frequency_per_shift
        n_drifts = round(self._rng.uniform(min_f, max_f) * n_shifts)

        for _ in range(n_drifts):
            start = float(self._rng.uniform(0, self._sim_duration_s))
            params: dict[str, object] = {
                "drift_duration_range": list(cfg.duration_seconds),
                "drift_range": list(cfg.max_drift_c),
            }
            self._scenarios.append(
                DryerDrift(start_time=start, rng=self._spawn_rng(), params=params)
            )

    def _schedule_ink_excursions(self) -> None:
        """Schedule ink viscosity excursions based on config frequency (PRD 5.6)."""
        cfg = self._config.ink_viscosity_excursion
        if not cfg.enabled:
            return

        n_shifts = max(1.0, self._sim_duration_s / _SHIFT_SECONDS)

        min_f, max_f = cfg.frequency_per_shift
        n_excursions = round(self._rng.uniform(min_f, max_f) * n_shifts)

        for _ in range(n_excursions):
            start = float(self._rng.uniform(0, self._sim_duration_s))
            params: dict[str, object] = {
                "duration_range": list(cfg.duration_seconds),
            }
            self._scenarios.append(
                InkExcursion(start_time=start, rng=self._spawn_rng(), params=params)
            )

    def _schedule_registration_drifts(self) -> None:
        """Schedule registration drifts based on config frequency (PRD 5.7)."""
        cfg = self._config.registration_drift
        if not cfg.enabled:
            return

        n_shifts = max(1.0, self._sim_duration_s / _SHIFT_SECONDS)

        min_f, max_f = cfg.frequency_per_shift
        n_drifts = round(self._rng.uniform(min_f, max_f) * n_shifts)

        for _ in range(n_drifts):
            start = float(self._rng.uniform(0, self._sim_duration_s))
            params: dict[str, object] = {
                "duration_range": list(cfg.duration_seconds),
            }
            self._scenarios.append(
                RegistrationDrift(
                    start_time=start, rng=self._spawn_rng(), params=params
                )
            )

    def _schedule_cold_starts(self) -> None:
        """Schedule cold start monitoring instances (PRD 5.10).

        ColdStart is reactive -- it monitors press state for idle-to-active
        transitions.  We schedule monitoring instances spread across the
        simulation; each watches for one qualifying trigger.
        """
        cfg = self._config.cold_start_spike
        if not cfg.enabled:
            return

        n_days = max(1.0, self._sim_duration_s / 86400)
        n_instances = round(self._rng.uniform(1, 2) * n_days)

        for _ in range(n_instances):
            start = float(self._rng.uniform(0, self._sim_duration_s))
            params: dict[str, object] = {
                "spike_duration_range": list(cfg.spike_duration_seconds),
                "power_multiplier_range": list(cfg.spike_magnitude),
            }
            self._scenarios.append(
                ColdStart(start_time=start, rng=self._spawn_rng(), params=params)
            )

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

    def _spawn_rng(self) -> np.random.Generator:
        """Create a child RNG from the parent (Rule 13)."""
        return np.random.default_rng(self._rng.integers(0, 2**63))


# ---------------------------------------------------------------------------
# Ground truth helpers -- extract metadata from scenario instances
# ---------------------------------------------------------------------------

# Scenario class name -> list of affected signal IDs.
# These are the signals the PRD says each scenario type modifies.
_AFFECTED_SIGNALS: dict[str, list[str]] = {
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
