"""Unit tests for the CoderGenerator (PRD 2.5).

Tests verify:
- 11 signals produced per tick
- State follows press: Printing when press Running, Ready when press Setup
- Prints counter increments when Printing
- Ink level depletes when Printing
- Ink viscosity generates values when active
- Off state produces zeros for inactive signals
- Determinism (same seed → same output)

Task 6d.7
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.coder import (
    CODER_PRINTING,
    CODER_READY,
    CoderGenerator,
)
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Press state constants (from press generator)
PRESS_OFF = 0
PRESS_SETUP = 1
PRESS_RUNNING = 2
PRESS_IDLE = 3


def _make_coder_config() -> EquipmentConfig:
    """Create a minimal coder config with all 11 required signals."""
    signals: dict[str, SignalConfig] = {}

    signals["state"] = SignalConfig(
        model="state_machine",
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=4.0,
        units="enum",
        params={
            "states": ["Off", "Ready", "Printing", "Fault", "Standby"],
            "initial_state": "Ready",
        },
    )
    signals["prints_total"] = SignalConfig(
        model="counter",
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=1e9,
        units="count",
        params={"rate": 10.0, "rollover": 1000000000},
    )
    signals["ink_level"] = SignalConfig(
        model="depletion",
        noise_sigma=0.1,
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=100.0,
        units="%",
        params={"initial_value": 100.0, "consumption_rate": 0.05},
    )
    signals["printhead_temp"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.5,
        sample_rate_ms=500,
        min_clamp=20.0,
        max_clamp=80.0,
        units="C",
        params={"target": 45.0},
    )
    signals["ink_pump_speed"] = SignalConfig(
        model="correlated_follower",
        noise_sigma=2.0,
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=5000.0,
        units="RPM",
        params={"base": 100.0, "factor": 2.0},
    )
    signals["ink_pressure"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.2,
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=5.0,
        units="bar",
        params={"target": 2.5},
    )
    signals["ink_viscosity_actual"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.3,
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=20.0,
        units="cP",
        params={"target": 10.0},
    )
    signals["supply_voltage"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.05,
        sample_rate_ms=500,
        min_clamp=22.0,
        max_clamp=26.0,
        units="V",
        params={"target": 24.0},
    )
    signals["ink_consumption_ml"] = SignalConfig(
        model="counter",
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=1e9,
        units="ml",
        params={"rate": 0.5, "rollover": 1000000000},
    )
    signals["nozzle_health"] = SignalConfig(
        model="depletion",
        noise_sigma=0.1,
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=100.0,
        units="%",
        params={"initial_value": 100.0, "consumption_rate": 0.001},
    )
    signals["gutter_fault"] = SignalConfig(
        model="state_machine",
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=1.0,
        units="bool",
        params={"states": ["Clear", "Fault"], "initial_state": "Clear"},
    )

    return EquipmentConfig(
        enabled=True,
        type="cij_coder",
        signals=signals,
    )


def _find_signal(results: list[SignalValue], signal_id: str) -> SignalValue:
    for sv in results:
        if sv.signal_id == signal_id:
            return sv
    raise KeyError(f"Signal {signal_id} not found in results")


def _set_press_state(store: SignalStore, state: int, speed: float) -> None:
    """Set press.machine_state and press.line_speed in the store."""
    store.set("press.machine_state", float(state), 0.0, "good")
    store.set("press.line_speed", speed, 0.0, "good")


def _run_ticks(
    gen: CoderGenerator,
    store: SignalStore,
    *,
    n_ticks: int,
    dt: float = 0.1,
    start_time: float = 0.0,
) -> list[list[SignalValue]]:
    """Run generator for n_ticks, return list of result lists."""
    all_results: list[list[SignalValue]] = []
    sim_time = start_time
    for _ in range(n_ticks):
        sim_time += dt
        results = gen.generate(sim_time, dt, store)
        for sv in results:
            store.set(sv.signal_id, sv.value, sv.timestamp, sv.quality)
        all_results.append(results)
    return all_results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(42)


@pytest.fixture
def store() -> SignalStore:
    return SignalStore()


@pytest.fixture
def coder(rng: np.random.Generator) -> CoderGenerator:
    return CoderGenerator("coder", _make_coder_config(), rng)


# ---------------------------------------------------------------------------
# Tests: signal IDs
# ---------------------------------------------------------------------------


class TestSignalIds:
    """Verify all 11 coder signals are registered."""

    def test_signal_count(self, coder: CoderGenerator) -> None:
        assert len(coder.get_signal_ids()) == 11

    def test_signal_names(self, coder: CoderGenerator) -> None:
        ids = set(coder.get_signal_ids())
        expected = {
            "coder.state",
            "coder.prints_total",
            "coder.ink_level",
            "coder.printhead_temp",
            "coder.ink_pump_speed",
            "coder.ink_pressure",
            "coder.ink_viscosity_actual",
            "coder.supply_voltage",
            "coder.ink_consumption_ml",
            "coder.nozzle_health",
            "coder.gutter_fault",
        }
        assert ids == expected


# ---------------------------------------------------------------------------
# Tests: off state
# ---------------------------------------------------------------------------


class TestOffState:
    """When press is Off, coder should produce minimal/zero values for inactive signals."""

    def test_off_state_steady_state_signals_at_minimum(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """When coder is Off, steady-state signals get raw=0.0, then clamped to min_clamp."""
        _set_press_state(store, PRESS_OFF, 0.0)
        coder.state_machine.force_state("Off")
        results = coder.generate(0.1, 0.1, store)

        # pressure raw=0.0, min_clamp=0.0 → 0.0
        pressure = _find_signal(results, "coder.ink_pressure").value
        assert pressure == 0.0, f"Pressure should be 0 when Off: {pressure}"

        # viscosity raw=0.0, min_clamp=0.0 → 0.0
        viscosity = _find_signal(results, "coder.ink_viscosity_actual").value
        assert viscosity == 0.0, f"Viscosity should be 0 when Off: {viscosity}"

        # voltage raw=0.0, but min_clamp=22.0 → clamped to 22.0
        voltage = _find_signal(results, "coder.supply_voltage").value
        assert voltage == 22.0, f"Voltage should be clamped to min when Off: {voltage}"

    def test_off_state_pump_near_base(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """Pump uses correlated follower — with parent=0, value is near base (100)."""
        _set_press_state(store, PRESS_OFF, 0.0)
        coder.state_machine.force_state("Off")
        results = coder.generate(0.1, 0.1, store)
        pump = _find_signal(results, "coder.ink_pump_speed").value
        # base=100, gain*0=0, so should be near 100 + noise
        assert 80.0 <= pump <= 120.0, f"Pump should be near base when Off: {pump}"

    def test_off_state_printhead_ambient(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """Printhead temp should be 25.0 (ambient) when Off."""
        _set_press_state(store, PRESS_OFF, 0.0)
        coder.state_machine.force_state("Off")
        results = coder.generate(0.1, 0.1, store)
        pht = _find_signal(results, "coder.printhead_temp").value
        assert pht == pytest.approx(25.0, abs=0.1), f"Printhead temp should be ~25 when Off: {pht}"


# ---------------------------------------------------------------------------
# Tests: printing state
# ---------------------------------------------------------------------------


class TestPrintingState:
    """When press is Running, coder should be Printing with active signals."""

    def test_printing_state_when_press_running(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """Coder transitions to Printing when press is Running."""
        _set_press_state(store, PRESS_RUNNING, 200.0)
        # Run enough ticks for state transition
        results_list = _run_ticks(coder, store, n_ticks=20, dt=0.1)
        last_state = _find_signal(results_list[-1], "coder.state").value
        assert int(last_state) == CODER_PRINTING, (
            f"Coder should be Printing when press Running: state={int(last_state)}"
        )

    def test_printhead_temp_active_when_printing(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """Printhead temp should be near target (45) when active."""
        _set_press_state(store, PRESS_RUNNING, 200.0)
        results_list = _run_ticks(coder, store, n_ticks=20, dt=0.1)
        pht = _find_signal(results_list[-1], "coder.printhead_temp").value
        # target=45.0, should be in reasonable range
        assert 30.0 <= pht <= 60.0, f"Printhead temp out of range when active: {pht}"

    def test_ink_pump_follows_speed(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """Ink pump speed should correlate with press speed when Printing."""
        _set_press_state(store, PRESS_RUNNING, 200.0)
        results_list = _run_ticks(coder, store, n_ticks=20, dt=0.1)
        pump = _find_signal(results_list[-1], "coder.ink_pump_speed").value
        # base=100 + factor=2.0 * 200 = 500, plus noise
        assert pump > 50.0, f"Pump speed should be positive when Printing: {pump}"


# ---------------------------------------------------------------------------
# Tests: prints counter
# ---------------------------------------------------------------------------


class TestPrintsCounter:
    """Prints counter increments only when Printing."""

    def test_counter_increments_when_printing(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """prints_total should increase when Printing."""
        _set_press_state(store, PRESS_RUNNING, 200.0)
        results_list = _run_ticks(coder, store, n_ticks=30, dt=0.1)

        counts = [
            _find_signal(r, "coder.prints_total").value
            for r in results_list
        ]
        # Counter should increase (rate=10 * speed=200 * dt=0.1)
        assert counts[-1] > counts[0], (
            f"prints_total should increase: {counts[0]} → {counts[-1]}"
        )

    def test_counter_does_not_increment_when_off(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """prints_total should not increase when Off."""
        _set_press_state(store, PRESS_OFF, 0.0)
        coder.state_machine.force_state("Off")
        results_list = _run_ticks(coder, store, n_ticks=10, dt=0.1)

        counts = [
            _find_signal(r, "coder.prints_total").value
            for r in results_list
        ]
        assert counts[-1] == counts[0] == 0.0


# ---------------------------------------------------------------------------
# Tests: ink depletion
# ---------------------------------------------------------------------------


class TestInkDepletion:
    """Ink level depletes when Printing."""

    def test_ink_depletes_when_printing(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """ink_level should decrease over time when Printing."""
        _set_press_state(store, PRESS_RUNNING, 200.0)
        results_list = _run_ticks(coder, store, n_ticks=50, dt=0.1)

        ink_levels = [
            _find_signal(r, "coder.ink_level").value
            for r in results_list
        ]
        assert ink_levels[-1] < ink_levels[0], (
            f"Ink should deplete: {ink_levels[0]:.2f} → {ink_levels[-1]:.2f}"
        )

    def test_ink_stable_when_off(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """ink_level should remain near initial value when Off."""
        _set_press_state(store, PRESS_OFF, 0.0)
        coder.state_machine.force_state("Off")
        results_list = _run_ticks(coder, store, n_ticks=20, dt=0.1)

        first_ink = _find_signal(results_list[0], "coder.ink_level").value
        last_ink = _find_signal(results_list[-1], "coder.ink_level").value
        # Should be near initial_value=100 with minimal change
        assert abs(last_ink - first_ink) < 1.0, (
            f"Ink should be stable when Off: {first_ink:.2f} → {last_ink:.2f}"
        )


# ---------------------------------------------------------------------------
# Tests: ink viscosity
# ---------------------------------------------------------------------------


class TestInkViscosity:
    """Ink viscosity generates values when active."""

    def test_viscosity_near_target_when_active(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """Viscosity should be near target (10) when coder is active."""
        _set_press_state(store, PRESS_RUNNING, 200.0)
        results_list = _run_ticks(coder, store, n_ticks=20, dt=0.1)
        visc = _find_signal(results_list[-1], "coder.ink_viscosity_actual").value
        assert 5.0 <= visc <= 15.0, f"Viscosity out of range: {visc}"

    def test_viscosity_zero_when_off(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """Viscosity should be 0 when coder is Off."""
        _set_press_state(store, PRESS_OFF, 0.0)
        coder.state_machine.force_state("Off")
        results = coder.generate(0.1, 0.1, store)
        visc = _find_signal(results, "coder.ink_viscosity_actual").value
        assert visc == 0.0


# ---------------------------------------------------------------------------
# Tests: all signals present
# ---------------------------------------------------------------------------


class TestAllSignals:
    """Every tick produces exactly 11 signals."""

    def test_signal_count_per_tick(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, PRESS_RUNNING, 200.0)
        results = coder.generate(0.1, 0.1, store)
        assert len(results) == 11

    def test_all_signals_have_quality_good(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, PRESS_RUNNING, 200.0)
        results = coder.generate(0.1, 0.1, store)
        for sv in results:
            assert sv.quality == "good"


# ---------------------------------------------------------------------------
# Tests: nozzle health
# ---------------------------------------------------------------------------


class TestNozzleHealth:
    """Nozzle health slowly degrades when Printing."""

    def test_nozzle_degrades_when_printing(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, PRESS_RUNNING, 200.0)
        results_list = _run_ticks(coder, store, n_ticks=100, dt=0.1)
        first_health = _find_signal(results_list[0], "coder.nozzle_health").value
        last_health = _find_signal(results_list[-1], "coder.nozzle_health").value
        assert last_health < first_health, (
            f"Nozzle health should degrade: {first_health:.2f} → {last_health:.2f}"
        )


# ---------------------------------------------------------------------------
# Tests: gutter fault
# ---------------------------------------------------------------------------


class TestGutterFault:
    """Gutter fault starts Clear."""

    def test_gutter_starts_clear(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, PRESS_RUNNING, 200.0)
        results = coder.generate(0.1, 0.1, store)
        gutter = _find_signal(results, "coder.gutter_fault").value
        assert gutter == 0.0, f"Gutter should start Clear (0): {gutter}"


# ---------------------------------------------------------------------------
# Tests: ready state from press setup
# ---------------------------------------------------------------------------


class TestReadyState:
    """Coder transitions to Ready when press is in Setup."""

    def test_ready_when_press_setup(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, PRESS_SETUP, 0.0)
        results_list = _run_ticks(coder, store, n_ticks=20, dt=0.1)
        state = _find_signal(results_list[-1], "coder.state").value
        assert int(state) == CODER_READY, (
            f"Coder should be Ready when press in Setup: state={int(state)}"
        )


# ---------------------------------------------------------------------------
# Tests: determinism (CLAUDE.md Rule 13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed → identical output sequence."""

    def test_coder_deterministic(self, store: SignalStore) -> None:
        cfg = _make_coder_config()
        gen1 = CoderGenerator("coder", cfg, np.random.default_rng(99))
        gen2 = CoderGenerator("coder", cfg, np.random.default_rng(99))

        _set_press_state(store, PRESS_RUNNING, 200.0)

        sim_time = 0.0
        dt = 0.1
        r1: list[SignalValue] = []
        r2: list[SignalValue] = []
        for _ in range(30):
            sim_time += dt
            r1 = gen1.generate(sim_time, dt, store)
            r2 = gen2.generate(sim_time, dt, store)

        for sv1, sv2 in zip(r1, r2, strict=True):
            assert sv1.signal_id == sv2.signal_id
            assert sv1.value == sv2.value, (
                f"{sv1.signal_id}: {sv1.value} != {sv2.value}"
            )
