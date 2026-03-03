"""Tests for scenario engine auto-scheduling (Phase 2.1, Phase 3, Phase 4).

Verifies:
- All packaging signal IDs in _AFFECTED_SIGNALS match valid store keys.
- All F&B signal IDs in _AFFECTED_SIGNALS match valid F&B store keys.
- ScenarioEngine auto-schedules all 10 packaging scenario types.
- ScenarioEngine auto-schedules all 7 F&B scenario types when F&B config used.
- Poisson inter-arrival times are exponentially distributed (KS test).
- Minimum gap enforcement prevents overlapping same-type scenarios.
- Cross-shift continuation works.
- SeedSequence.spawn() produces deterministic child RNGs.
- sim_duration_s parameter is respected.

PRD Reference: Section 4.7, 5.13 (Scenario Scheduling), 5.14 (F&B Scenarios)
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, cast

import numpy as np
from scipy.stats import expon, kstest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import ScenariosConfig, ShiftsConfig, load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.engine.scenario_engine import (
    _AFFECTED_SIGNALS,
    _PRIORITY_ORDER,
    _SHIFT_SECONDS,
    ScenarioEngine,
)
from factory_simulator.scenarios.base import Scenario, ScenarioPhase
from factory_simulator.store import SignalStore

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory.yaml"
_FNB_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "factory-foodbev.yaml"


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


def _make_fnb_engine(seed: int = 42) -> tuple[DataEngine, SignalStore]:
    """Create a DataEngine with F&B config (all scenarios disabled)."""
    config = load_config(_FNB_CONFIG_PATH, apply_env=False)
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
    if config.scenarios.batch_cycle is not None:
        config.scenarios.batch_cycle.enabled = False
    if config.scenarios.oven_thermal_excursion is not None:
        config.scenarios.oven_thermal_excursion.enabled = False
    if config.scenarios.fill_weight_drift is not None:
        config.scenarios.fill_weight_drift.enabled = False
    if config.scenarios.seal_integrity_failure is not None:
        config.scenarios.seal_integrity_failure.enabled = False
    if config.scenarios.chiller_door_alarm is not None:
        config.scenarios.chiller_door_alarm.enabled = False
    if config.scenarios.cip_cycle is not None:
        config.scenarios.cip_cycle.enabled = False
    if config.scenarios.cold_chain_break is not None:
        config.scenarios.cold_chain_break.enabled = False
    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    return engine, store


class TestAffectedSignalsValid:
    """Verify _AFFECTED_SIGNALS entries match real store keys."""

    # Signals derived from coil/state logic that may not have direct store
    # entries as regular signal IDs.
    _KNOWN_DERIVED: ClassVar[set[str]] = {"press.web_break", "press.fault_active"}

    # Scenario types that belong to the F&B profile only (not in packaging store).
    # These are validated separately against the F&B config store.
    _FNB_ONLY_SCENARIOS: ClassVar[set[str]] = {
        "BatchCycle",
        "OvenThermalExcursion",
        "FillWeightDrift",
        "SealIntegrityFailure",
        "ChillerDoorAlarm",
        "CipCycle",
        "ColdChainBreak",
    }

    def test_all_affected_signal_ids_in_store(self) -> None:
        """Every packaging signal ID in _AFFECTED_SIGNALS must exist in the
        packaging store after one engine tick (except known derived signals).
        F&B-only scenario types are validated by a separate test."""
        engine, store = _make_engine()
        engine.tick()  # populate store

        store_keys = set(store.signal_ids())

        missing: list[str] = []
        for scenario_type, signal_ids in _AFFECTED_SIGNALS.items():
            if scenario_type in self._FNB_ONLY_SCENARIOS:
                continue  # validated by test_fnb_affected_signal_ids_in_store
            for sig_id in signal_ids:
                if sig_id in self._KNOWN_DERIVED:
                    continue
                if sig_id not in store_keys:
                    missing.append(f"{scenario_type}: {sig_id}")

        assert missing == [], (
            "Signal IDs in _AFFECTED_SIGNALS not found in packaging store:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )

    def test_fnb_affected_signal_ids_in_store(self) -> None:
        """Every F&B signal ID in _AFFECTED_SIGNALS must exist in the F&B
        store after one engine tick."""
        engine, store = _make_fnb_engine()
        engine.tick()  # populate store

        store_keys = set(store.signal_ids())

        missing: list[str] = []
        for scenario_type, signal_ids in _AFFECTED_SIGNALS.items():
            if scenario_type not in self._FNB_ONLY_SCENARIOS:
                continue
            for sig_id in signal_ids:
                if sig_id not in store_keys:
                    missing.append(f"{scenario_type}: {sig_id}")

        assert missing == [], (
            "Signal IDs in _AFFECTED_SIGNALS not found in F&B store:\n"
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


# All 7 F&B scenario types expected in a full F&B auto-scheduled run.
_ALL_FNB_SCENARIO_TYPES = {
    "BatchCycle",
    "OvenThermalExcursion",
    "FillWeightDrift",
    "SealIntegrityFailure",
    "ChillerDoorAlarm",
    "CipCycle",
    "ColdChainBreak",
}


class TestFnbAutoSchedulingIntegration:
    """Verify F&B scenario auto-scheduling produces all 7 F&B scenario types."""

    def _make_fnb_scenarios_config(self) -> ScenariosConfig:
        """Load F&B ScenariosConfig with all F&B scenarios enabled."""
        from factory_simulator.config import load_config
        config = load_config(_FNB_CONFIG_PATH, apply_env=False)
        # Disable packaging scenarios (they have no generators in F&B profile)
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
        return config.scenarios

    def test_all_fnb_scenario_types_scheduled(self) -> None:
        """A 1-month sim with all F&B scenarios enabled should produce
        at least one instance of each of the 7 F&B scenario types."""
        rng = np.random.default_rng(42)
        scenarios_cfg = self._make_fnb_scenarios_config()
        shifts_cfg = ShiftsConfig()

        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
            sim_duration_s=30 * 86400,  # 1 month
        )

        scheduled_types = {type(s).__name__ for s in se.scenarios}
        missing = _ALL_FNB_SCENARIO_TYPES - scheduled_types
        assert missing == set(), (
            f"Missing F&B scenario types in auto-scheduled timeline: {missing}"
        )

    def test_fnb_scenarios_not_scheduled_without_fnb_config(self) -> None:
        """F&B scenarios must NOT be scheduled when packaging config is used
        (F&B config fields are None in packaging ScenariosConfig)."""
        rng = np.random.default_rng(42)
        scenarios_cfg = ScenariosConfig()  # packaging defaults — F&B fields are None
        shifts_cfg = ShiftsConfig()

        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
            sim_duration_s=7 * 86400,
        )

        scheduled_types = {type(s).__name__ for s in se.scenarios}
        unexpected = _ALL_FNB_SCENARIO_TYPES & scheduled_types
        assert unexpected == set(), (
            f"F&B scenario types unexpectedly scheduled with packaging config: "
            f"{unexpected}"
        )


class TestPoissonScheduling:
    """Phase 4: Poisson inter-arrival time scheduling tests."""

    def test_poisson_interarrival_exponentially_distributed(self) -> None:
        """Inter-arrival times from _poisson_starts must follow an exponential
        distribution (KS test, p > 0.01)."""
        # Use _poisson_starts directly with known parameters
        rng2 = np.random.default_rng(99)
        se2 = ScenarioEngine.__new__(ScenarioEngine)
        se2._rng = rng2
        se2._sim_duration_s = 365 * 86400  # 1 year for lots of data
        se2._seed_seq = rng2.bit_generator.seed_seq

        freq_range = [3, 6]  # 3-6 per shift
        period_s = float(_SHIFT_SECONDS)
        min_gap_s = 60.0  # small min gap to not distort distribution
        starts = se2._poisson_starts(freq_range, period_s, min_gap_s)

        assert len(starts) > 50, f"Too few starts for KS test: {len(starts)}"

        # Compute inter-arrival times
        sorted_starts = sorted(starts)
        interarrivals = np.diff(sorted_starts)

        # Expected mean interval = period / mean_freq
        mean_freq = (freq_range[0] + freq_range[1]) / 2.0
        expected_mean = period_s / mean_freq

        # KS test against exponential distribution with expected scale
        # Note: min_gap enforcement right-censors small values, so we
        # test that the distribution is "close enough" — p > 0.01
        stat, p_value = kstest(interarrivals, expon(scale=expected_mean).cdf)
        assert p_value > 0.01, (
            f"KS test failed: inter-arrival times are not exponentially "
            f"distributed (stat={stat:.4f}, p={p_value:.4f}). "
            f"Expected scale={expected_mean:.1f}s, "
            f"actual mean={float(np.mean(interarrivals)):.1f}s"
        )

    def test_minimum_gap_enforcement(self) -> None:
        """No two consecutive starts from _poisson_starts should be closer
        than min_gap_s."""
        rng = np.random.default_rng(123)
        se = ScenarioEngine.__new__(ScenarioEngine)
        se._rng = rng
        se._sim_duration_s = 7 * 86400
        se._seed_seq = rng.bit_generator.seed_seq

        min_gap_s = 600.0  # 10 minutes
        starts = se._poisson_starts([3, 6], float(_SHIFT_SECONDS), min_gap_s)

        sorted_starts = sorted(starts)
        for i in range(1, len(sorted_starts)):
            gap = sorted_starts[i] - sorted_starts[i - 1]
            assert gap >= min_gap_s, (
                f"Gap between starts [{i-1}] and [{i}] is {gap:.1f}s, "
                f"less than min_gap_s={min_gap_s:.1f}s"
            )

    def test_poisson_starts_within_sim_duration(self) -> None:
        """All starts from _poisson_starts must be in [0, sim_duration_s)."""
        rng = np.random.default_rng(77)
        se = ScenarioEngine.__new__(ScenarioEngine)
        se._rng = rng
        sim_dur = 3 * 86400.0
        se._sim_duration_s = sim_dur
        se._seed_seq = rng.bit_generator.seed_seq

        starts = se._poisson_starts([2, 4], float(_SHIFT_SECONDS), 300.0)

        for t in starts:
            assert 0.0 < t < sim_dur, (
                f"Start time {t:.1f}s outside (0, {sim_dur:.1f})"
            )

    def test_poisson_starts_empty_for_zero_frequency(self) -> None:
        """Zero-frequency range should produce no starts."""
        rng = np.random.default_rng(42)
        se = ScenarioEngine.__new__(ScenarioEngine)
        se._rng = rng
        se._sim_duration_s = 86400.0
        se._seed_seq = rng.bit_generator.seed_seq

        starts = se._poisson_starts([0, 0], float(_SHIFT_SECONDS), 60.0)
        assert starts == []

    def test_cross_shift_scheduling(self) -> None:
        """Scenarios can be scheduled near the end of a shift period.

        With Poisson scheduling across the full sim_duration, starts
        near shift boundaries (e.g. 7h55m into an 8h shift) are valid
        and the scenario runs past the shift boundary.
        """
        rng = np.random.default_rng(42)
        scenarios_cfg = ScenariosConfig()
        shifts_cfg = ShiftsConfig()

        # 3 shifts = exactly 24h
        se = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng,
            sim_duration_s=3 * _SHIFT_SECONDS,
        )

        # Check that some scenarios are scheduled in the last 5 minutes
        # of any shift period
        shift_boundaries = [i * _SHIFT_SECONDS for i in range(1, 3)]
        near_boundary = []
        for s in se.scenarios:
            for boundary in shift_boundaries:
                # Within 5 minutes before or after a shift boundary
                if abs(s.start_time - boundary) < 300:
                    near_boundary.append(s)
                    break

        # With many scenarios over 24h, statistically some must fall
        # near shift boundaries
        assert len(near_boundary) > 0, (
            "No scenarios scheduled near shift boundaries "
            f"(total scenarios: {len(se.scenarios)})"
        )


class TestSeedSequenceDeterminism:
    """Phase 4: Verify SeedSequence.spawn produces deterministic schedules."""

    def test_same_seed_same_schedule(self) -> None:
        """Two ScenarioEngines with the same seed must produce identical
        scenario timelines."""
        scenarios_cfg = ScenariosConfig()
        shifts_cfg = ShiftsConfig()

        rng1 = np.random.default_rng(42)
        se1 = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng1,
            sim_duration_s=7 * 86400,
        )

        rng2 = np.random.default_rng(42)
        se2 = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng2,
            sim_duration_s=7 * 86400,
        )

        # Same number of scenarios
        assert len(se1.scenarios) == len(se2.scenarios), (
            f"Different scenario counts: {len(se1.scenarios)} vs {len(se2.scenarios)}"
        )

        # Same types and start times
        for s1, s2 in zip(se1.scenarios, se2.scenarios, strict=True):
            assert type(s1).__name__ == type(s2).__name__, (
                f"Type mismatch: {type(s1).__name__} vs {type(s2).__name__}"
            )
            assert s1.start_time == s2.start_time, (
                f"Start time mismatch for {type(s1).__name__}: "
                f"{s1.start_time} vs {s2.start_time}"
            )

    def test_different_seed_different_schedule(self) -> None:
        """Two ScenarioEngines with different seeds must produce different
        scenario timelines."""
        scenarios_cfg = ScenariosConfig()
        shifts_cfg = ShiftsConfig()

        rng1 = np.random.default_rng(42)
        se1 = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng1,
            sim_duration_s=7 * 86400,
        )

        rng2 = np.random.default_rng(99)
        se2 = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng2,
            sim_duration_s=7 * 86400,
        )

        # Start times should differ (extremely unlikely to be identical)
        times1 = [s.start_time for s in se1.scenarios]
        times2 = [s.start_time for s in se2.scenarios]
        assert times1 != times2, "Different seeds produced identical timelines"

    def test_spawn_rng_produces_isolated_generators(self) -> None:
        """Child RNGs from _spawn_rng should be independent of the
        parent RNG state."""
        rng = np.random.default_rng(42)
        se = ScenarioEngine.__new__(ScenarioEngine)
        se._rng = rng
        se._seed_seq = rng.bit_generator.seed_seq

        child1 = se._spawn_rng()
        child2 = se._spawn_rng()

        # Each child should produce different values
        v1 = child1.random()
        v2 = child2.random()
        assert v1 != v2, "Two spawned child RNGs produced the same value"


class TestSimDurationParameter:
    """Phase 4: Verify sim_duration_s parameter controls scheduling."""

    def test_short_duration_fewer_scenarios(self) -> None:
        """A shorter sim_duration_s should produce fewer scenarios."""
        scenarios_cfg = ScenariosConfig()
        shifts_cfg = ShiftsConfig()

        rng1 = np.random.default_rng(42)
        se_short = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng1,
            sim_duration_s=_SHIFT_SECONDS,  # 8h
        )

        rng2 = np.random.default_rng(42)
        se_long = ScenarioEngine(
            scenarios_config=scenarios_cfg,
            shifts_config=shifts_cfg,
            rng=rng2,
            sim_duration_s=7 * 86400,  # 1 week
        )

        assert len(se_short.scenarios) < len(se_long.scenarios), (
            f"Short ({len(se_short.scenarios)}) should have fewer "
            f"scenarios than long ({len(se_long.scenarios)})"
        )

    def test_sim_duration_from_data_engine(self) -> None:
        """DataEngine should pass sim_duration_s from config to ScenarioEngine."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 42
        config.simulation.sim_duration_s = 3600.0  # 1 hour
        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        se = engine.scenario_engine
        # All scenarios should have start_time < 3600
        for s in se.scenarios:
            assert s.start_time < 3600.0, (
                f"{type(s).__name__} start_time={s.start_time:.1f}s "
                f"exceeds sim_duration_s=3600s"
            )

    def test_default_sim_duration_one_shift(self) -> None:
        """When sim_duration_s is not set in config, default to one shift."""
        config = load_config(_CONFIG_PATH, apply_env=False)
        config.simulation.random_seed = 42
        config.simulation.sim_duration_s = None  # not set
        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        se = engine.scenario_engine
        # All scenarios should have start_time < 8h
        for s in se.scenarios:
            assert s.start_time < _SHIFT_SECONDS, (
                f"{type(s).__name__} start_time={s.start_time:.1f}s "
                f"exceeds default one-shift duration "
                f"({_SHIFT_SECONDS}s)"
            )


# ---------------------------------------------------------------------------
# Priority and conflict resolution tests (Task 4.2)
# ---------------------------------------------------------------------------


class _StateChangingMock(Scenario):
    """Mock scenario with state_changing priority for conflict resolution tests."""

    priority: ClassVar[str] = "state_changing"

    def _on_activate(self, sim_time: float, engine: DataEngine) -> None:  # type: ignore[override]
        pass

    def _on_tick(self, sim_time: float, dt: float, engine: DataEngine) -> None:  # type: ignore[override]
        pass

    def _on_complete(self, sim_time: float, engine: DataEngine) -> None:  # type: ignore[override]
        pass

    def duration(self) -> float:
        return 3600.0


class _NonStateChangingMock(Scenario):
    """Mock scenario with non_state_changing priority for conflict resolution tests."""

    priority: ClassVar[str] = "non_state_changing"

    def _on_activate(self, sim_time: float, engine: DataEngine) -> None:  # type: ignore[override]
        pass

    def _on_tick(self, sim_time: float, dt: float, engine: DataEngine) -> None:  # type: ignore[override]
        pass

    def _on_complete(self, sim_time: float, engine: DataEngine) -> None:  # type: ignore[override]
        pass

    def duration(self) -> float:
        return 1800.0


class _BackgroundMock(Scenario):
    """Mock scenario with background priority."""

    priority: ClassVar[str] = "background"

    def _on_activate(self, sim_time: float, engine: DataEngine) -> None:  # type: ignore[override]
        pass

    def _on_tick(self, sim_time: float, dt: float, engine: DataEngine) -> None:  # type: ignore[override]
        pass

    def _on_complete(self, sim_time: float, engine: DataEngine) -> None:  # type: ignore[override]
        pass

    def duration(self) -> float:
        return 7200.0


class _MicroMock(Scenario):
    """Mock scenario with micro priority."""

    priority: ClassVar[str] = "micro"

    def _on_activate(self, sim_time: float, engine: DataEngine) -> None:  # type: ignore[override]
        pass

    def _on_tick(self, sim_time: float, dt: float, engine: DataEngine) -> None:  # type: ignore[override]
        pass

    def _on_complete(self, sim_time: float, engine: DataEngine) -> None:  # type: ignore[override]
        pass

    def duration(self) -> float:
        return 15.0


def _make_priority_engine(scenarios: list[Scenario]) -> ScenarioEngine:
    """Create a minimal ScenarioEngine with given scenarios (no auto-scheduling)."""
    se = ScenarioEngine.__new__(ScenarioEngine)
    se._scenarios = list(scenarios)
    se._ground_truth = None
    return se


class TestScenarioPriority:
    """Phase 4.2: Scenario priority attributes and conflict resolution."""

    # ------------------------------------------------------------------
    # Priority attribute tests
    # ------------------------------------------------------------------

    def test_base_class_default_priority(self) -> None:
        """Base Scenario class has default priority 'non_state_changing'."""
        assert Scenario.priority == "non_state_changing"

    def test_state_changing_priorities(self) -> None:
        """state_changing scenarios have the correct priority attribute."""
        from factory_simulator.scenarios.cip_cycle import CipCycle
        from factory_simulator.scenarios.cold_chain_break import ColdChainBreak
        from factory_simulator.scenarios.job_changeover import JobChangeover
        from factory_simulator.scenarios.seal_integrity import SealIntegrityFailure
        from factory_simulator.scenarios.unplanned_stop import UnplannedStop
        from factory_simulator.scenarios.web_break import WebBreak

        for cls in (WebBreak, UnplannedStop, JobChangeover, CipCycle,
                    ColdChainBreak, SealIntegrityFailure):
            assert cls.priority == "state_changing", (
                f"{cls.__name__}.priority should be 'state_changing', "
                f"got '{cls.priority}'"
            )

    def test_non_state_changing_priorities(self) -> None:
        """non_state_changing scenarios inherit default or are explicitly set."""
        from factory_simulator.scenarios.batch_cycle import BatchCycle
        from factory_simulator.scenarios.chiller_door_alarm import ChillerDoorAlarm
        from factory_simulator.scenarios.dryer_drift import DryerDrift
        from factory_simulator.scenarios.fill_weight_drift import FillWeightDrift
        from factory_simulator.scenarios.ink_excursion import InkExcursion
        from factory_simulator.scenarios.oven_thermal_excursion import OvenThermalExcursion
        from factory_simulator.scenarios.registration_drift import RegistrationDrift

        for cls in (DryerDrift, InkExcursion, RegistrationDrift, BatchCycle,
                    OvenThermalExcursion, FillWeightDrift, ChillerDoorAlarm):
            assert cls.priority == "non_state_changing", (
                f"{cls.__name__}.priority should be 'non_state_changing', "
                f"got '{cls.priority}'"
            )

    def test_priority_order_dict(self) -> None:
        """_PRIORITY_ORDER assigns state_changing the lowest (highest priority) number."""
        assert _PRIORITY_ORDER["state_changing"] < _PRIORITY_ORDER["non_state_changing"]
        assert _PRIORITY_ORDER["non_state_changing"] < _PRIORITY_ORDER["background"]
        assert _PRIORITY_ORDER["background"] < _PRIORITY_ORDER["micro"]

    # ------------------------------------------------------------------
    # Conflict resolution tests
    # ------------------------------------------------------------------

    def test_state_changing_preempts_active_non_state_changing(self) -> None:
        """When a state_changing scenario activates, active non_state_changing
        scenarios are immediately completed (preempted)."""
        rng = np.random.default_rng(42)
        # non_state_changing is already active (start_time=0, sim_time=10)
        nsc = _NonStateChangingMock(start_time=0.0, rng=rng)
        # state_changing activates at t=10
        sc = _StateChangingMock(start_time=10.0, rng=rng)

        se = _make_priority_engine([nsc, sc])

        # Manually activate the non_state_changing scenario (t=0)
        mock_engine = cast(DataEngine, None)
        se.tick(sim_time=0.0, dt=0.1, engine=mock_engine)
        assert nsc.phase == ScenarioPhase.ACTIVE, (
            "non_state_changing should be ACTIVE after t=0 tick"
        )

        # At t=10, state_changing activates and should preempt nsc
        se.tick(sim_time=10.0, dt=0.1, engine=mock_engine)
        assert sc.phase == ScenarioPhase.ACTIVE, (
            "state_changing should be ACTIVE at t=10"
        )
        assert nsc.phase == ScenarioPhase.COMPLETED, (
            "non_state_changing should be COMPLETED (preempted) when "
            "state_changing activates"
        )

    def test_non_state_changing_defers_when_state_changing_active(self) -> None:
        """A pending non_state_changing scenario stays PENDING while a
        state_changing scenario is active."""
        rng = np.random.default_rng(42)
        # state_changing is already active (start_time=0)
        sc = _StateChangingMock(start_time=0.0, rng=rng)
        # non_state_changing also scheduled at t=0
        nsc = _NonStateChangingMock(start_time=0.0, rng=rng)

        se = _make_priority_engine([sc, nsc])

        mock_engine = cast(DataEngine, None)
        # First tick: sc activates, nsc should be deferred
        se.tick(sim_time=0.0, dt=0.1, engine=mock_engine)
        assert sc.phase == ScenarioPhase.ACTIVE, (
            "state_changing should activate at t=0"
        )
        assert nsc.phase == ScenarioPhase.PENDING, (
            "non_state_changing should remain PENDING while state_changing is active"
        )

        # Second tick: sc still active, nsc still deferred
        se.tick(sim_time=0.1, dt=0.1, engine=mock_engine)
        assert nsc.phase == ScenarioPhase.PENDING, (
            "non_state_changing should still be PENDING on tick 2"
        )

    def test_non_state_changing_activates_after_state_changing_completes(
        self,
    ) -> None:
        """A deferred non_state_changing scenario activates once the
        state_changing scenario completes."""
        rng = np.random.default_rng(42)
        sc = _StateChangingMock(start_time=0.0, rng=rng)
        nsc = _NonStateChangingMock(start_time=0.0, rng=rng)

        se = _make_priority_engine([sc, nsc])

        mock_engine = cast(DataEngine, None)
        # t=0: sc activates, nsc deferred
        se.tick(sim_time=0.0, dt=0.1, engine=mock_engine)
        assert nsc.phase == ScenarioPhase.PENDING

        # Manually complete the state_changing scenario
        sc.complete(sim_time=1.0, engine=mock_engine)
        assert sc.phase == ScenarioPhase.COMPLETED

        # t=1.1: no state_changing active → nsc should now activate
        se.tick(sim_time=1.1, dt=0.1, engine=mock_engine)
        assert nsc.phase == ScenarioPhase.ACTIVE, (
            "non_state_changing should activate once state_changing completes"
        )

    def test_background_activates_regardless_of_state_changing(self) -> None:
        """background scenarios always activate, even when a state_changing
        is active."""
        rng = np.random.default_rng(42)
        sc = _StateChangingMock(start_time=0.0, rng=rng)
        bg = _BackgroundMock(start_time=0.0, rng=rng)

        se = _make_priority_engine([sc, bg])

        mock_engine = cast(DataEngine, None)
        se.tick(sim_time=0.0, dt=0.1, engine=mock_engine)
        assert sc.phase == ScenarioPhase.ACTIVE
        assert bg.phase == ScenarioPhase.ACTIVE, (
            "background scenario should activate even when state_changing is active"
        )

    def test_micro_activates_regardless_of_state_changing(self) -> None:
        """micro scenarios always activate, even when a state_changing is active."""
        rng = np.random.default_rng(42)
        sc = _StateChangingMock(start_time=0.0, rng=rng)
        micro = _MicroMock(start_time=0.0, rng=rng)

        se = _make_priority_engine([sc, micro])

        mock_engine = cast(DataEngine, None)
        se.tick(sim_time=0.0, dt=0.1, engine=mock_engine)
        assert sc.phase == ScenarioPhase.ACTIVE
        assert micro.phase == ScenarioPhase.ACTIVE, (
            "micro scenario should activate even when state_changing is active"
        )

    def test_multiple_non_state_changing_all_preempted(self) -> None:
        """All active non_state_changing scenarios are preempted when a
        state_changing scenario activates."""
        rng = np.random.default_rng(42)
        nsc1 = _NonStateChangingMock(start_time=0.0, rng=rng)
        nsc2 = _NonStateChangingMock(start_time=0.0, rng=rng)
        nsc3 = _NonStateChangingMock(start_time=0.0, rng=rng)
        sc = _StateChangingMock(start_time=5.0, rng=rng)

        se = _make_priority_engine([nsc1, nsc2, nsc3, sc])

        mock_engine = cast(DataEngine, None)
        # t=0: all three non_state_changing activate
        se.tick(sim_time=0.0, dt=0.1, engine=mock_engine)
        assert nsc1.phase == ScenarioPhase.ACTIVE
        assert nsc2.phase == ScenarioPhase.ACTIVE
        assert nsc3.phase == ScenarioPhase.ACTIVE

        # t=5: state_changing activates, preempts all three
        se.tick(sim_time=5.0, dt=0.1, engine=mock_engine)
        assert sc.phase == ScenarioPhase.ACTIVE
        assert nsc1.phase == ScenarioPhase.COMPLETED, "nsc1 should be preempted"
        assert nsc2.phase == ScenarioPhase.COMPLETED, "nsc2 should be preempted"
        assert nsc3.phase == ScenarioPhase.COMPLETED, "nsc3 should be preempted"

    def test_background_not_preempted_by_state_changing(self) -> None:
        """background scenarios are NOT preempted when a state_changing activates."""
        rng = np.random.default_rng(42)
        bg = _BackgroundMock(start_time=0.0, rng=rng)
        sc = _StateChangingMock(start_time=5.0, rng=rng)

        se = _make_priority_engine([bg, sc])

        mock_engine = cast(DataEngine, None)
        # t=0: bg activates
        se.tick(sim_time=0.0, dt=0.1, engine=mock_engine)
        assert bg.phase == ScenarioPhase.ACTIVE

        # t=5: sc activates, bg should remain active
        se.tick(sim_time=5.0, dt=0.1, engine=mock_engine)
        assert sc.phase == ScenarioPhase.ACTIVE
        assert bg.phase == ScenarioPhase.ACTIVE, (
            "background scenario should NOT be preempted by state_changing"
        )
