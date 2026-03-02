"""Scenario engine -- schedules and evaluates scenarios per tick.

The ScenarioEngine owns the scenario timeline.  On each tick it:

1. Checks whether any pending scenarios should activate.
2. Advances active scenarios (calling their ``evaluate()``).
3. Removes completed scenarios from the active set.

It can also generate a random scenario timeline from config-driven
statistical profiles (frequency, duration ranges).

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
from factory_simulator.scenarios.job_changeover import JobChangeover
from factory_simulator.scenarios.shift_change import ShiftChange
from factory_simulator.scenarios.unplanned_stop import UnplannedStop

if TYPE_CHECKING:
    from factory_simulator.config import ScenariosConfig, ShiftsConfig
    from factory_simulator.engine.data_engine import DataEngine

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
    ) -> None:
        self._config = scenarios_config
        self._shifts = shifts_config
        self._rng = rng
        self._sim_duration_s = sim_duration_s

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
        """
        for scenario in self._scenarios:
            if scenario.phase == ScenarioPhase.COMPLETED:
                continue
            scenario.evaluate(sim_time, dt, engine)

    # -- Timeline generation ---------------------------------------------------

    def _generate_timeline(self) -> None:
        """Generate a random scenario timeline from config.

        Schedules scenarios based on their configured frequency and
        duration ranges.  Scenarios are spread across the simulation
        duration with random spacing.
        """
        self._schedule_unplanned_stops()
        self._schedule_job_changeovers()
        self._schedule_shift_changes()

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

    def _spawn_rng(self) -> np.random.Generator:
        """Create a child RNG from the parent (Rule 13)."""
        return np.random.default_rng(self._rng.integers(0, 2**63))
