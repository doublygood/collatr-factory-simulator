"""Tests for scenario engine auto-scheduling (Phase 2.1).

Verifies:
- All signal IDs in _AFFECTED_SIGNALS match valid store keys.
- ScenarioEngine auto-schedules all 10 scenario types.

PRD Reference: Section 4.7, 5.13 (Scenario Scheduling)
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import numpy as np

from factory_simulator.clock import SimulationClock
from factory_simulator.config import ScenariosConfig, ShiftsConfig, load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.engine.scenario_engine import _AFFECTED_SIGNALS, ScenarioEngine
from factory_simulator.store import SignalStore

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory.yaml"


def _make_engine(seed: int = 42) -> tuple[DataEngine, SignalStore]:
    """Create a DataEngine with packaging config (all scenarios disabled)."""
    config = load_config(_CONFIG_PATH, apply_env=False)
    config.simulation.random_seed = seed
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = 1.0
    # Disable all auto-scheduled scenarios
    config.scenarios.job_changeover.enabled = False
    config.scenarios.unplanned_stop.enabled = False
    config.scenarios.shift_change.enabled = False
    config.scenarios.web_break.enabled = False
    config.scenarios.dryer_drift.enabled = False
    config.scenarios.ink_viscosity_excursion.enabled = False
    config.scenarios.registration_drift.enabled = False
    config.scenarios.cold_start_spike.enabled = False
    config.scenarios.coder_depletion.enabled = False
    config.scenarios.material_splice.enabled = False
    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    return engine, store


class TestAffectedSignalsValid:
    """Verify _AFFECTED_SIGNALS entries match real store keys."""

    # Signals derived from coil/state logic that may not have direct store
    # entries as regular signal IDs.
    _KNOWN_DERIVED: ClassVar[set[str]] = {"press.web_break", "press.fault_active"}

    def test_all_affected_signal_ids_in_store(self) -> None:
        """Every signal ID in _AFFECTED_SIGNALS must exist in the store
        after one engine tick (except known derived signals)."""
        engine, store = _make_engine()
        engine.tick()  # populate store

        store_keys = set(store.signal_ids())

        missing: list[str] = []
        for scenario_type, signal_ids in _AFFECTED_SIGNALS.items():
            for sig_id in signal_ids:
                if sig_id in self._KNOWN_DERIVED:
                    continue
                if sig_id not in store_keys:
                    missing.append(f"{scenario_type}: {sig_id}")

        assert missing == [], (
            "Signal IDs in _AFFECTED_SIGNALS not found in store:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )

    def test_affected_signals_not_empty(self) -> None:
        """Each scenario type must list at least one affected signal."""
        for scenario_type, signal_ids in _AFFECTED_SIGNALS.items():
            assert len(signal_ids) > 0, (
                f"{scenario_type} has empty _AFFECTED_SIGNALS list"
            )

    def test_no_duplicate_signal_ids(self) -> None:
        """No scenario type should list the same signal twice."""
        for scenario_type, signal_ids in _AFFECTED_SIGNALS.items():
            assert len(signal_ids) == len(set(signal_ids)), (
                f"{scenario_type} has duplicate signal IDs: {signal_ids}"
            )


# All 10 scenario types expected in a full auto-scheduled run.
_ALL_SCENARIO_TYPES = {
    # Phase 1
    "UnplannedStop",
    "JobChangeover",
    "ShiftChange",
    # Phase 2
    "WebBreak",
    "DryerDrift",
    "InkExcursion",
    "RegistrationDrift",
    "ColdStart",
    "CoderDepletion",
    "MaterialSplice",
}


class TestAutoSchedulingIntegration:
    """Verify auto-scheduling produces all 10 scenario types."""

    def test_all_scenario_types_scheduled(self) -> None:
        """A 1-week sim with all scenarios enabled should produce
        at least one instance of each of the 10 scenario types."""
        rng = np.random.default_rng(42)
        scenarios_cfg = ScenariosConfig()  # all enabled by default
        shifts_cfg = ShiftsConfig()

        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
            sim_duration_s=7 * 86400,  # 1 week
        )

        scheduled_types = {type(s).__name__ for s in se.scenarios}
        missing = _ALL_SCENARIO_TYPES - scheduled_types
        assert missing == set(), (
            f"Missing scenario types in auto-scheduled timeline: {missing}"
        )

    def test_reasonable_scenario_count(self) -> None:
        """Total scheduled scenarios should be reasonable (not 0, not 10000)."""
        rng = np.random.default_rng(42)
        scenarios_cfg = ScenariosConfig()
        shifts_cfg = ShiftsConfig()

        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
            sim_duration_s=7 * 86400,
        )

        count = len(se.scenarios)
        assert count > 10, f"Too few scenarios scheduled: {count}"
        assert count < 5000, f"Too many scenarios scheduled: {count}"

    def test_scenarios_sorted_by_start_time(self) -> None:
        """All auto-scheduled scenarios must be sorted by start_time."""
        rng = np.random.default_rng(42)
        scenarios_cfg = ScenariosConfig()
        shifts_cfg = ShiftsConfig()

        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
            sim_duration_s=7 * 86400,
        )

        times = [s.start_time for s in se.scenarios]
        assert times == sorted(times), "Scenarios are not sorted by start_time"

    def test_start_times_within_sim_duration(self) -> None:
        """All scenario start times must be within [0, sim_duration_s)."""
        sim_duration = 7 * 86400
        rng = np.random.default_rng(42)
        scenarios_cfg = ScenariosConfig()
        shifts_cfg = ShiftsConfig()

        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
            sim_duration_s=sim_duration,
        )

        for s in se.scenarios:
            assert 0.0 <= s.start_time < sim_duration, (
                f"{type(s).__name__} has start_time={s.start_time} "
                f"outside [0, {sim_duration})"
            )
