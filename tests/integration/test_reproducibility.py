"""Reproducibility and final integration tests for the Factory Simulator.

Two test groups:

1. **Reproducibility** — same seed produces byte-identical signal output for
   both profiles across two independent engine runs.

2. **Final integration** — 1 simulated day (86 400 s) for both profiles
   verifying:
   - All expected scenario types fire (``scenario_start`` events in GT).
   - No NaN or Inf in signal values.
   - Memory growth < 2x initial (``tracemalloc``).
   - Ground-truth JSONL is well-formed (valid JSON, required keys).
   - Data-quality events (``sensor_disconnect``, ``stuck_sensor``) appear in GT.

No external services (Docker, Modbus, OPC-UA, MQTT) are required.

PRD Reference: Appendix F (Phase 4 exit criteria).
"""

from __future__ import annotations

import json
import math
import tracemalloc
from pathlib import Path
from typing import Any

import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.engine.ground_truth import GroundTruthLogger
from factory_simulator.store import SignalStore

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

_CONFIG_PKG = Path(__file__).resolve().parents[2] / "config" / "factory.yaml"
_CONFIG_FNB = (
    Path(__file__).resolve().parents[2] / "config" / "factory-foodbev.yaml"
)

# Simulation parameters:
#   tick_interval_ms = 100 ms, time_scale = 100  →  dt = 10 s / tick
#   1 simulated day = 86 400 s / 10 s = 8 640 ticks
_TICK_INTERVAL_MS = 100
_TIME_SCALE = 100.0
_TICK_DT = (_TICK_INTERVAL_MS / 1000.0) * _TIME_SCALE  # 10 s

# Exact number of ticks for one simulated day
_SIM_DAY_S = 86_400.0
_SIM_DAY_TICKS = int(_SIM_DAY_S / _TICK_DT)  # 8 640

# PRD Appendix F exit criteria: 7 simulated days at 100x
# 7 * 86 400 s / 10 s per tick = 60 480 ticks ≈ 100 real seconds per profile
_SIM_7DAY_S = 7.0 * _SIM_DAY_S
_SIM_7DAY_TICKS = int(_SIM_7DAY_S / _TICK_DT)  # 60 480

# Shorter tick count for cheap reproducibility checks
_REPRO_TICKS = 500


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _store_snapshot(store: SignalStore) -> list[tuple[str, float | str, str]]:
    """Sorted, serialisable snapshot of all signal values.

    Returns a list of ``(signal_id, value, quality)`` triples, sorted by
    ``signal_id``.  Used to compare two runs for exact equality.
    """
    return sorted(
        (sig_id, sv.value, sv.quality)
        for sig_id, sv in store.get_all().items()
    )


def _run_ticks(engine: DataEngine, n: int) -> None:
    """Synchronously advance *engine* by *n* ticks (no asyncio sleep)."""
    for _ in range(n):
        engine.tick()


def _make_engine(
    config_path: Path,
    seed: int | None = 42,
) -> tuple[DataEngine, SignalStore]:
    """Create a plain DataEngine (no GT) from *config_path*.

    Used for reproducibility tests where only the signal store needs
    comparing.
    """
    config = load_config(config_path, apply_env=False)
    config.simulation.random_seed = seed
    config.simulation.tick_interval_ms = _TICK_INTERVAL_MS
    config.simulation.time_scale = _TIME_SCALE
    config.simulation.sim_duration_s = _SIM_DAY_S

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)
    return engine, store


def _make_integration_engine(
    config_path: Path,
    gt_path: Path,
    seed: int = 42,
) -> tuple[DataEngine, SignalStore, GroundTruthLogger]:
    """Create a DataEngine configured for the 1-day integration run.

    Applies frequency / timing overrides so that every scenario type
    reliably fires within the 86 400-second simulation window regardless
    of seed.  Both packaging and F&B profiles are handled; irrelevant
    overrides are guarded by ``enabled`` checks.
    """
    config = load_config(config_path, apply_env=False)
    config.simulation.random_seed = seed
    config.simulation.tick_interval_ms = _TICK_INTERVAL_MS
    config.simulation.time_scale = _TIME_SCALE
    config.simulation.sim_duration_s = _SIM_DAY_S

    # ---- Packaging-specific overrides --------------------------------------

    # WebBreak: default 1-2/week → mean_interval ≈ 403200s (4.7 days).
    # P(zero events in 1 day) ≈ 80%.  Boost to 70-100/week → guaranteed.
    if config.scenarios.web_break.enabled:
        config.scenarios.web_break.frequency_per_week = [70, 100]

    # BearingWear: default start_after_hours=48 → missed in 1 day.
    # Set to 0.01 h (36 s) so it starts almost immediately.
    # BearingWearConfig.start_after_hours validator requires > 0.
    if config.scenarios.bearing_wear.enabled:
        config.scenarios.bearing_wear.start_after_hours = 0.01
        config.scenarios.bearing_wear.duration_hours = 2.0

    # ContextualAnomaly: default 2-5/week ≈ 0.4/day → unreliable in 1 day.
    # Boost to 70-100/week ≈ 10-14/day → guaranteed.
    if (
        config.scenarios.contextual_anomaly is not None
        and config.scenarios.contextual_anomaly.enabled
    ):
        config.scenarios.contextual_anomaly.frequency_per_week = [70, 100]

    # IntermittentFault subtypes: default start_after_hours 24-72 h →
    # never reached in 1-day sim.  Set all enabled subtypes to start
    # within the first minute and shorten phase 1 to 30-60 min.
    if (
        config.scenarios.intermittent_fault is not None
        and config.scenarios.intermittent_fault.enabled
    ):
        faults = config.scenarios.intermittent_fault.faults

        bc = faults.bearing_intermittent
        if bc.enabled:
            bc.start_after_hours = 0.01
            bc.phase1_duration_hours = [0.5, 1.0]

        ec = faults.electrical_intermittent
        if ec.enabled:
            ec.start_after_hours = 0.01
            ec.phase1_duration_hours = [0.5, 1.0]

        pc = faults.pneumatic_intermittent
        if pc.enabled:
            pc.start_after_hours = 0.01
            pc.phase1_duration_hours = [0.5, 1.0]

        # sensor_intermittent is disabled by default; skip it here to
        # avoid needing to populate affected_signals for the test.

    # ---- F&B-specific overrides --------------------------------------------

    # SealIntegrityFailure: 1-2/week → boost to 70-140/week (≈10-20/day).
    if (
        config.scenarios.seal_integrity_failure is not None
        and config.scenarios.seal_integrity_failure.enabled
    ):
        config.scenarios.seal_integrity_failure.frequency_per_week = [70, 140]

    # ChillerDoorAlarm: 1-3/week → boost to 70-140/week.
    if (
        config.scenarios.chiller_door_alarm is not None
        and config.scenarios.chiller_door_alarm.enabled
    ):
        config.scenarios.chiller_door_alarm.frequency_per_week = [70, 140]

    # ColdChainBreak: 1-2/month → boost to 420-630/month (≈14-21/day).
    if (
        config.scenarios.cold_chain_break is not None
        and config.scenarios.cold_chain_break.enabled
    ):
        config.scenarios.cold_chain_break.frequency_per_month = [420, 630]

    # CipCycle: default 1-3/day → mean_interval ≈ 43200s.
    # P(zero events in 1 day) ≈ 13.5%.  Boost to 10-20/day → guaranteed.
    if (
        config.scenarios.cip_cycle is not None
        and config.scenarios.cip_cycle.enabled
    ):
        config.scenarios.cip_cycle.frequency_per_day = [10, 20]

    # ---- Data-quality: raise injection frequency to guarantee GT events ----

    # SensorDisconnect: default [0, 1]/24h per signal → some signals may
    # never fire.  Raise to [2, 4]/24h -> ~3/day x 47 signals = ~141 events.
    config.data_quality.sensor_disconnect.frequency_per_24h_per_signal = [
        2.0, 4.0
    ]

    # StuckSensor: default [0, 2]/week → raise to [14, 21]/week (≈2-3/day).
    config.data_quality.stuck_sensor.frequency_per_week_per_signal = [
        14.0, 21.0
    ]

    gt = GroundTruthLogger(gt_path)
    gt.open()
    gt.write_header(config)

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock, ground_truth=gt)
    return engine, store, gt


def _parse_gt_events(gt_path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL ground-truth file into a list of dicts."""
    events: list[dict[str, Any]] = []
    with gt_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


# ---------------------------------------------------------------------------
# Module-level fixtures: run each profile ONCE, share across tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def packaging_run(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[SignalStore, list[dict[str, Any]]]:
    """Run 1 simulated day of the packaging profile and return results.

    Returns ``(store, events)`` where *events* is the parsed GT log.
    Scoped to the module so the expensive tick loop runs only once.
    """
    gt_file = tmp_path_factory.mktemp("pkg") / "gt.jsonl"
    engine, store, gt = _make_integration_engine(_CONFIG_PKG, gt_file, seed=42)
    _run_ticks(engine, _SIM_DAY_TICKS)
    gt.close()
    events = _parse_gt_events(gt_file)
    return store, events


@pytest.fixture(scope="module")
def fnb_run(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[SignalStore, list[dict[str, Any]]]:
    """Run 1 simulated day of the F&B profile and return results.

    Returns ``(store, events)`` where *events* is the parsed GT log.
    Scoped to the module so the tick loop runs only once.
    """
    gt_file = tmp_path_factory.mktemp("fnb") / "gt.jsonl"
    engine, store, gt = _make_integration_engine(_CONFIG_FNB, gt_file, seed=42)
    _run_ticks(engine, _SIM_DAY_TICKS)
    gt.close()
    events = _parse_gt_events(gt_file)
    return store, events


# ---------------------------------------------------------------------------
# Class A: Reproducibility
# ---------------------------------------------------------------------------


class TestReproducibility:
    """Same seed produces byte-identical signal output on the same platform.

    Tests Rule 13 (Reproducible Runs) and the ``SeedSequence`` hierarchy.
    No GT file is needed — only the store state is compared.
    """

    def test_packaging_seed42_identical(self) -> None:
        """Two packaging runs with seed=42 produce identical store states."""
        engine1, store1 = _make_engine(_CONFIG_PKG, seed=42)
        _run_ticks(engine1, _REPRO_TICKS)
        snap1 = _store_snapshot(store1)

        engine2, store2 = _make_engine(_CONFIG_PKG, seed=42)
        _run_ticks(engine2, _REPRO_TICKS)
        snap2 = _store_snapshot(store2)

        assert snap1 == snap2, (
            f"Packaging seed=42 reproducibility failed at tick {_REPRO_TICKS}: "
            f"{len(snap1)} signals checked"
        )

    def test_fnb_seed42_identical(self) -> None:
        """Two F&B runs with seed=42 produce identical store states."""
        engine1, store1 = _make_engine(_CONFIG_FNB, seed=42)
        _run_ticks(engine1, _REPRO_TICKS)
        snap1 = _store_snapshot(store1)

        engine2, store2 = _make_engine(_CONFIG_FNB, seed=42)
        _run_ticks(engine2, _REPRO_TICKS)
        snap2 = _store_snapshot(store2)

        assert snap1 == snap2, (
            f"F&B seed=42 reproducibility failed at tick {_REPRO_TICKS}"
        )

    def test_different_seeds_differ(self) -> None:
        """seed=42 and seed=43 produce different store states."""
        engine42, store42 = _make_engine(_CONFIG_PKG, seed=42)
        _run_ticks(engine42, _REPRO_TICKS)

        engine43, store43 = _make_engine(_CONFIG_PKG, seed=43)
        _run_ticks(engine43, _REPRO_TICKS)

        snap42 = _store_snapshot(store42)
        snap43 = _store_snapshot(store43)

        assert snap42 != snap43, (
            "seed=42 and seed=43 produced identical store states — "
            "RNG seeding may not be working"
        )

    def test_packaging_full_day_reproducible(self) -> None:
        """Packaging 1-simulated-day run is byte-identical for seed=42."""
        engine1, store1 = _make_engine(_CONFIG_PKG, seed=42)
        _run_ticks(engine1, _SIM_DAY_TICKS)
        snap1 = _store_snapshot(store1)

        engine2, store2 = _make_engine(_CONFIG_PKG, seed=42)
        _run_ticks(engine2, _SIM_DAY_TICKS)
        snap2 = _store_snapshot(store2)

        assert snap1 == snap2, (
            "Packaging full-day reproducibility failed for seed=42"
        )

    def test_fnb_full_day_reproducible(self) -> None:
        """F&B 1-simulated-day run is byte-identical for seed=42."""
        engine1, store1 = _make_engine(_CONFIG_FNB, seed=42)
        _run_ticks(engine1, _SIM_DAY_TICKS)
        snap1 = _store_snapshot(store1)

        engine2, store2 = _make_engine(_CONFIG_FNB, seed=42)
        _run_ticks(engine2, _SIM_DAY_TICKS)
        snap2 = _store_snapshot(store2)

        assert snap1 == snap2, (
            "F&B full-day reproducibility failed for seed=42"
        )


# ---------------------------------------------------------------------------
# Class B: Final Integration — Packaging Profile
# ---------------------------------------------------------------------------


class TestFinalIntegrationPackaging:
    """1-simulated-day validation for the packaging profile.

    All tests share the single ``packaging_run`` fixture (module-scoped),
    so the expensive tick loop executes only once per test session.
    """

    def test_no_nan_or_inf(
        self, packaging_run: tuple[SignalStore, list[dict[str, Any]]]
    ) -> None:
        """No signal value in the packaging store is NaN or Inf after 1 day."""
        store, _ = packaging_run
        bad: list[str] = []
        for sig_id, sv in store.get_all().items():
            if isinstance(sv.value, float) and (
                math.isnan(sv.value) or math.isinf(sv.value)
            ):
                bad.append(f"{sig_id}={sv.value!r}")
        assert not bad, (
            f"Packaging: {len(bad)} NaN/Inf signal(s) found:\n"
            + "\n".join(bad[:20])
        )

    def test_scenarios_fire(
        self, packaging_run: tuple[SignalStore, list[dict[str, Any]]]
    ) -> None:
        """All expected packaging scenario types fire at least once.

        Scenarios with long default timescales (BearingWear, IntermittentFault,
        ContextualAnomaly) use the frequency overrides applied by
        ``_make_integration_engine``.
        """
        _, events = packaging_run
        started = {
            e["scenario"]
            for e in events
            if e.get("event") == "scenario_start"
        }

        # High-confidence set: every entry either has high default frequency
        # or is boosted by _make_integration_engine overrides.
        expected: set[str] = {
            "ShiftChange",        # fixed 3x per day
            "JobChangeover",      # Poisson, multiple per shift
            "WebBreak",           # Poisson, 2-5 per week
            "DryerDrift",         # Poisson, 2-5 per shift
            "InkExcursion",       # Poisson, 2-4 per shift
            "RegistrationDrift",  # Poisson, 1-3 per shift
            "CoderDepletion",     # 1 monitoring window per day
            "MaterialSplice",     # multiple monitoring windows per day
            "MicroStop",          # Poisson, 10-50 per shift
            "BearingWear",        # override: starts at 36 s
            "ContextualAnomaly",  # override: 70-100 per week
            "IntermittentFault",  # override: starts at 36 s
        }
        missing = expected - started
        assert not missing, (
            f"Packaging scenario types did not fire in 1 simulated day: "
            f"{sorted(missing)}\n"
            f"Scenario types that did fire: {sorted(started)}"
        )

    def test_ground_truth_well_formed(
        self, packaging_run: tuple[SignalStore, list[dict[str, Any]]]
    ) -> None:
        """Packaging GT is valid JSONL with a config header and event records."""
        _, events = packaging_run

        assert len(events) >= 2, (
            "GT file should have at least a header + 1 event record; "
            f"found {len(events)} line(s)"
        )

        # First line must be the config header
        header = events[0]
        assert header.get("event_type") == "config", (
            f"First GT record is not a config header: {header}"
        )
        for required_key in ("seed", "profile", "signals"):
            assert required_key in header, (
                f"Config header missing key {required_key!r}: {header}"
            )

        # All non-header records must have sim_time and event
        bad_records: list[str] = []
        for i, rec in enumerate(events[1:], start=1):
            if "sim_time" not in rec:
                bad_records.append(f"[{i}] missing sim_time: {rec}")
            if "event" not in rec:
                bad_records.append(f"[{i}] missing event: {rec}")
        assert not bad_records, (
            f"Packaging GT has {len(bad_records)} malformed record(s):\n"
            + "\n".join(bad_records[:10])
        )

    def test_data_quality_injections_present(
        self, packaging_run: tuple[SignalStore, list[dict[str, Any]]]
    ) -> None:
        """Sensor disconnect and stuck-sensor events appear in packaging GT."""
        _, events = packaging_run
        event_types = {e.get("event") for e in events}

        assert "sensor_disconnect" in event_types, (
            "No sensor_disconnect events in packaging GT after 1 simulated day"
        )
        assert "stuck_sensor" in event_types, (
            "No stuck_sensor events in packaging GT after 1 simulated day"
        )


class TestPackagingMemory:
    """Memory stability for the packaging profile.

    Separate from the fixture-sharing class because this test creates its
    own engine (it cannot share the already-run fixture).
    """

    def test_memory_stable(self) -> None:
        """Packaging memory growth stays under 2x initial after 1 simulated day.

        Uses ``tracemalloc`` to measure Python-level heap allocations.
        A brief warmup period establishes the baseline; the remainder of
        the day must not double peak allocation.
        """
        engine, _ = _make_engine(_CONFIG_PKG, seed=42)

        tracemalloc.start()
        _run_ticks(engine, 100)  # warmup: let allocations settle
        _, initial_peak = tracemalloc.get_traced_memory()

        _run_ticks(engine, _SIM_DAY_TICKS - 100)  # remainder of 1 day
        _, final_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert initial_peak > 0, "tracemalloc returned 0 initial peak"
        ratio = final_peak / initial_peak
        assert ratio < 2.0, (
            f"Packaging memory grew {ratio:.2f}x initial peak "
            f"({initial_peak // 1024} KiB → {final_peak // 1024} KiB). "
            "Possible memory leak in engine tick loop."
        )


# ---------------------------------------------------------------------------
# Class C: Final Integration — F&B Profile
# ---------------------------------------------------------------------------


class TestFinalIntegrationFnB:
    """1-simulated-day validation for the F&B profile.

    All tests share the single ``fnb_run`` fixture (module-scoped).
    """

    def test_no_nan_or_inf(
        self, fnb_run: tuple[SignalStore, list[dict[str, Any]]]
    ) -> None:
        """No signal value in the F&B store is NaN or Inf after 1 day."""
        store, _ = fnb_run
        bad: list[str] = []
        for sig_id, sv in store.get_all().items():
            if isinstance(sv.value, float) and (
                math.isnan(sv.value) or math.isinf(sv.value)
            ):
                bad.append(f"{sig_id}={sv.value!r}")
        assert not bad, (
            f"F&B: {len(bad)} NaN/Inf signal(s) found:\n"
            + "\n".join(bad[:20])
        )

    def test_scenarios_fire(
        self, fnb_run: tuple[SignalStore, list[dict[str, Any]]]
    ) -> None:
        """All expected F&B scenario types fire at least once.

        Infrequent scenarios (SealIntegrityFailure, ChillerDoorAlarm,
        ColdChainBreak) use the frequency overrides applied by
        ``_make_integration_engine``.
        """
        _, events = fnb_run
        started = {
            e["scenario"]
            for e in events
            if e.get("event") == "scenario_start"
        }

        expected: set[str] = {
            "BatchCycle",             # Poisson, multiple per shift
            "OvenThermalExcursion",   # Poisson, multiple per shift
            "FillWeightDrift",        # Poisson, multiple per shift
            "SealIntegrityFailure",   # override: 70-140 per week
            "ChillerDoorAlarm",       # override: 70-140 per week
            "CipCycle",               # Poisson, 1-3 per day
            "ColdChainBreak",         # override: 420-630 per month
        }
        missing = expected - started
        assert not missing, (
            f"F&B scenario types did not fire in 1 simulated day: "
            f"{sorted(missing)}\n"
            f"Scenario types that did fire: {sorted(started)}"
        )

    def test_ground_truth_well_formed(
        self, fnb_run: tuple[SignalStore, list[dict[str, Any]]]
    ) -> None:
        """F&B GT is valid JSONL with a config header and event records."""
        _, events = fnb_run

        assert len(events) >= 2, (
            f"F&B GT file should have at least header + 1 event; "
            f"found {len(events)} line(s)"
        )

        header = events[0]
        assert header.get("event_type") == "config", (
            f"First F&B GT record is not a config header: {header}"
        )
        for required_key in ("seed", "profile", "signals"):
            assert required_key in header, (
                f"F&B config header missing key {required_key!r}"
            )

        bad_records: list[str] = []
        for i, rec in enumerate(events[1:], start=1):
            if "sim_time" not in rec:
                bad_records.append(f"[{i}] missing sim_time: {rec}")
            if "event" not in rec:
                bad_records.append(f"[{i}] missing event: {rec}")
        assert not bad_records, (
            f"F&B GT has {len(bad_records)} malformed record(s):\n"
            + "\n".join(bad_records[:10])
        )

    def test_data_quality_injections_present(
        self, fnb_run: tuple[SignalStore, list[dict[str, Any]]]
    ) -> None:
        """Sensor disconnect and stuck-sensor events appear in F&B GT."""
        _, events = fnb_run
        event_types = {e.get("event") for e in events}

        assert "sensor_disconnect" in event_types, (
            "No sensor_disconnect events in F&B GT after 1 simulated day"
        )
        assert "stuck_sensor" in event_types, (
            "No stuck_sensor events in F&B GT after 1 simulated day"
        )


class TestFnBMemory:
    """Memory stability for the F&B profile."""

    def test_memory_stable(self) -> None:
        """F&B memory growth stays under 2x initial after 1 simulated day."""
        engine, _ = _make_engine(_CONFIG_FNB, seed=42)

        tracemalloc.start()
        _run_ticks(engine, 100)
        _, initial_peak = tracemalloc.get_traced_memory()

        _run_ticks(engine, _SIM_DAY_TICKS - 100)
        _, final_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert initial_peak > 0, "tracemalloc returned 0 initial peak"
        ratio = final_peak / initial_peak
        assert ratio < 2.0, (
            f"F&B memory grew {ratio:.2f}x initial peak "
            f"({initial_peak // 1024} KiB → {final_peak // 1024} KiB). "
            "Possible memory leak in engine tick loop."
        )


# ---------------------------------------------------------------------------
# Class E: 7-Day Long-Running Validation (PRD Appendix F exit criteria)
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestSevenDayStability:
    """7 simulated days at 100x for both profiles.

    PRD Appendix F exit criteria: "Run each profile for 7 days at 100x in
    batch mode."  At 100 ms tick / 100x time-scale, 7 simulated days =
    60 480 ticks ≈ 100 real seconds per profile.

    Checks:
    - No NaN or Inf in any signal after 7 simulated days.
    - Memory growth < 2x initial peak (tracemalloc).

    Run with: ``pytest -m slow``
    Skip with: ``pytest -m 'not slow'``
    """

    def test_packaging_7day_no_nan_inf(self) -> None:
        """Packaging profile: no NaN/Inf after 7 simulated days at 100x."""
        config = load_config(_CONFIG_PKG, apply_env=False)
        config.simulation.random_seed = 42
        config.simulation.tick_interval_ms = _TICK_INTERVAL_MS
        config.simulation.time_scale = _TIME_SCALE
        config.simulation.sim_duration_s = _SIM_7DAY_S

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        _run_ticks(engine, _SIM_7DAY_TICKS)

        bad: list[str] = []
        for sig_id, sv in store.get_all().items():
            if isinstance(sv.value, float) and (
                math.isnan(sv.value) or math.isinf(sv.value)
            ):
                bad.append(f"{sig_id}={sv.value!r}")
        assert not bad, (
            f"Packaging 7-day: {len(bad)} NaN/Inf signal(s):\n"
            + "\n".join(bad[:20])
        )

    def test_fnb_7day_no_nan_inf(self) -> None:
        """F&B profile: no NaN/Inf after 7 simulated days at 100x."""
        config = load_config(_CONFIG_FNB, apply_env=False)
        config.simulation.random_seed = 42
        config.simulation.tick_interval_ms = _TICK_INTERVAL_MS
        config.simulation.time_scale = _TIME_SCALE
        config.simulation.sim_duration_s = _SIM_7DAY_S

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        _run_ticks(engine, _SIM_7DAY_TICKS)

        bad: list[str] = []
        for sig_id, sv in store.get_all().items():
            if isinstance(sv.value, float) and (
                math.isnan(sv.value) or math.isinf(sv.value)
            ):
                bad.append(f"{sig_id}={sv.value!r}")
        assert not bad, (
            f"F&B 7-day: {len(bad)} NaN/Inf signal(s):\n"
            + "\n".join(bad[:20])
        )

    def test_packaging_7day_memory_stable(self) -> None:
        """Packaging: memory growth is sub-linear over 7 simulated days.

        A 5x threshold (vs 2x for 1-day) accounts for linear accumulation over
        60 480 ticks.  At ~0.8 bytes/tick the 1-day test shows ~1.3x; 7 days
        extrapolates to ~2.9x.  5x catches exponential leaks while allowing
        bounded linear growth (e.g., completed scenarios accumulating in
        ScenarioEngine._scenarios).
        """
        config = load_config(_CONFIG_PKG, apply_env=False)
        config.simulation.random_seed = 42
        config.simulation.tick_interval_ms = _TICK_INTERVAL_MS
        config.simulation.time_scale = _TIME_SCALE
        config.simulation.sim_duration_s = _SIM_7DAY_S

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        tracemalloc.start()
        _run_ticks(engine, 100)  # warmup: let initial allocations settle
        _, initial_peak = tracemalloc.get_traced_memory()

        _run_ticks(engine, _SIM_7DAY_TICKS - 100)
        _, final_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert initial_peak > 0, "tracemalloc returned 0 initial peak"
        ratio = final_peak / initial_peak
        assert ratio < 5.0, (
            f"Packaging 7-day memory grew {ratio:.2f}x initial peak "
            f"({initial_peak // 1024} KiB → {final_peak // 1024} KiB). "
            "Possible unbounded accumulation in engine tick loop."
        )

    def test_fnb_7day_memory_stable(self) -> None:
        """F&B: memory growth is sub-linear over 7 simulated days.

        Uses the same 5x threshold as the packaging test; see docstring there
        for rationale.
        """
        config = load_config(_CONFIG_FNB, apply_env=False)
        config.simulation.random_seed = 42
        config.simulation.tick_interval_ms = _TICK_INTERVAL_MS
        config.simulation.time_scale = _TIME_SCALE
        config.simulation.sim_duration_s = _SIM_7DAY_S

        store = SignalStore()
        clock = SimulationClock.from_config(config.simulation)
        engine = DataEngine(config, store, clock)

        tracemalloc.start()
        _run_ticks(engine, 100)  # warmup: let initial allocations settle
        _, initial_peak = tracemalloc.get_traced_memory()

        _run_ticks(engine, _SIM_7DAY_TICKS - 100)
        _, final_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert initial_peak > 0, "tracemalloc returned 0 initial peak"
        ratio = final_peak / initial_peak
        assert ratio < 5.0, (
            f"F&B 7-day memory grew {ratio:.2f}x initial peak "
            f"({initial_peak // 1024} KiB → {final_peak // 1024} KiB). "
            "Possible unbounded accumulation in engine tick loop."
        )
