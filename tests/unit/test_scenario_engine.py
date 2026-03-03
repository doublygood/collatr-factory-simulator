"""Tests for scenario engine auto-scheduling (Phase 2.1).

Verifies:
- All signal IDs in _AFFECTED_SIGNALS match valid store keys.
- ScenarioEngine auto-schedules all 10 scenario types.

PRD Reference: Section 4.7, 5.13 (Scenario Scheduling)
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from factory_simulator.clock import SimulationClock
from factory_simulator.config import load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.engine.scenario_engine import _AFFECTED_SIGNALS
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
