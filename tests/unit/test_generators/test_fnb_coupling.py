"""Tests for shared generator F&B coupling (task 3.11).

Verifies that CoderGenerator and EnergyGenerator support configurable
coupling signals so they can follow filler.state / filler.line_speed
for the F&B profile while remaining backward-compatible with the
packaging profile (press.machine_state / press.line_speed).

PRD Reference: Sections 2b.9 (coder F&B), 2b.11 (energy F&B)
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.coder import (
    CODER_PRINTING,
    CoderGenerator,
)
from factory_simulator.generators.energy import EnergyGenerator
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------


def _make_coder_signals() -> dict[str, SignalConfig]:
    return {
        "state": SignalConfig(
            model="state_machine",
            sample_rate_ms=100,
            min_clamp=0.0,
            max_clamp=4.0,
            params={
                "states": ["off", "ready", "printing", "fault", "standby"],
                "initial_state": "ready",
            },
        ),
        "prints_total": SignalConfig(
            model="counter",
            sample_rate_ms=100,
            min_clamp=0.0,
            max_clamp=999999999.0,
            params={"rate": 1.0},
        ),
        "ink_level": SignalConfig(
            model="depletion",
            sample_rate_ms=100,
            min_clamp=0.0,
            max_clamp=100.0,
            params={"initial_value": 100.0, "consumption_rate": 0.005},
        ),
        "printhead_temp": SignalConfig(
            model="steady_state",
            sample_rate_ms=100,
            min_clamp=25.0,
            max_clamp=50.0,
            params={"target": 35.0},
        ),
        "ink_pump_speed": SignalConfig(
            model="correlated_follower",
            sample_rate_ms=100,
            min_clamp=0.0,
            max_clamp=500.0,
            params={"base": 50.0, "factor": 3.0},
        ),
        "ink_pressure": SignalConfig(
            model="steady_state",
            sample_rate_ms=100,
            min_clamp=0.0,
            max_clamp=900.0,
            params={"target": 835.0},
        ),
        "ink_viscosity_actual": SignalConfig(
            model="steady_state",
            sample_rate_ms=100,
            min_clamp=2.0,
            max_clamp=15.0,
            params={"target": 8.0},
        ),
        "supply_voltage": SignalConfig(
            model="steady_state",
            sample_rate_ms=100,
            min_clamp=22.0,
            max_clamp=26.0,
            params={"target": 24.0},
        ),
        "ink_consumption_ml": SignalConfig(
            model="counter",
            sample_rate_ms=100,
            min_clamp=0.0,
            max_clamp=999999.0,
            params={"rate": 0.01},
        ),
        "nozzle_health": SignalConfig(
            model="depletion",
            sample_rate_ms=100,
            min_clamp=0.0,
            max_clamp=100.0,
            params={"initial_value": 100.0, "consumption_rate": 0.001},
        ),
        "gutter_fault": SignalConfig(
            model="state_machine",
            sample_rate_ms=100,
            min_clamp=0.0,
            max_clamp=1.0,
            params={"states": ["clear", "fault"], "initial_state": "clear"},
        ),
    }


def _make_packaging_coder_config() -> EquipmentConfig:
    """Packaging coder: default coupling (press.machine_state / press.line_speed)."""
    return EquipmentConfig(
        enabled=True,
        type="cij_printer",
        signals=_make_coder_signals(),
    )


def _make_fnb_coder_config() -> EquipmentConfig:
    """F&B coder: follows filler.state / filler.line_speed."""
    return EquipmentConfig(
        enabled=True,
        type="cij_printer",
        coupling_state_signal="filler.state",
        coupling_speed_signal="filler.line_speed",
        signals=_make_coder_signals(),
    )


def _make_energy_signals() -> dict[str, SignalConfig]:
    return {
        "line_power": SignalConfig(
            model="correlated_follower",
            sample_rate_ms=100,
            min_clamp=0.0,
            max_clamp=500.0,
            params={"base": 10.0, "factor": 0.5},
        ),
        "cumulative_kwh": SignalConfig(
            model="counter",
            sample_rate_ms=100,
            min_clamp=0.0,
            max_clamp=999999.0,
            params={"rate": 0.001},
        ),
    }


def _make_packaging_energy_config() -> EquipmentConfig:
    """Packaging energy: default coupling (press.line_speed)."""
    return EquipmentConfig(
        enabled=True,
        type="power_meter",
        signals=_make_energy_signals(),
    )


def _make_fnb_energy_config() -> EquipmentConfig:
    """F&B energy: follows filler.line_speed (higher base from refrigeration)."""
    return EquipmentConfig(
        enabled=True,
        type="power_meter",
        coupling_speed_signal="filler.line_speed",
        signals=_make_energy_signals(),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(99)


@pytest.fixture
def store() -> SignalStore:
    return SignalStore()


@pytest.fixture
def pkg_coder(rng: np.random.Generator) -> CoderGenerator:
    return CoderGenerator("coder", _make_packaging_coder_config(), rng)


@pytest.fixture
def fnb_coder(rng: np.random.Generator) -> CoderGenerator:
    return CoderGenerator("coder", _make_fnb_coder_config(), rng)


@pytest.fixture
def pkg_energy(rng: np.random.Generator) -> EnergyGenerator:
    return EnergyGenerator("energy", _make_packaging_energy_config(), rng)


@pytest.fixture
def fnb_energy(rng: np.random.Generator) -> EnergyGenerator:
    return EnergyGenerator("energy", _make_fnb_energy_config(), rng)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_signal(results: list[SignalValue], signal_id: str) -> SignalValue:
    for sv in results:
        if sv.signal_id == signal_id:
            return sv
    raise AssertionError(f"Signal {signal_id} not found in results")


def _set_press_state(store: SignalStore, speed: float, state: int) -> None:
    store.set("press.line_speed", speed, 0.0)
    store.set("press.machine_state", float(state), 0.0)


def _set_filler_state(store: SignalStore, speed: float, state: int) -> None:
    store.set("filler.line_speed", speed, 0.0)
    store.set("filler.state", float(state), 0.0)


# ===========================================================================
# CODER COUPLING TESTS
# ===========================================================================


class TestCoderDefaultCoupling:
    """Packaging coder (no config) defaults to press signals."""

    def test_default_state_signal(self, pkg_coder: CoderGenerator) -> None:
        assert pkg_coder._state_signal == "press.machine_state"

    def test_default_speed_signal(self, pkg_coder: CoderGenerator) -> None:
        assert pkg_coder._speed_signal == "press.line_speed"

    def test_follows_press_running(
        self, pkg_coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """Coder enters Printing when press is Running (state=2)."""
        _set_press_state(store, speed=200.0, state=2)
        sim_time = 0.0
        dt = 0.1
        for _ in range(10):
            sim_time += dt
            results = pkg_coder.generate(sim_time, dt, store)
        state = _find_signal(results, "coder.state")
        assert int(state.value) == CODER_PRINTING

    def test_standby_when_press_idle(
        self, pkg_coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """Coder enters Standby when press is Idle (state=3)."""
        _set_press_state(store, speed=0.0, state=3)
        results = pkg_coder.generate(0.0, 0.1, store)
        state = _find_signal(results, "coder.state")
        # Ready -> Standby transition: press_idle fires from the start
        assert int(state.value) == 4  # Standby

    def test_not_driven_by_filler(
        self, pkg_coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """Packaging coder ignores filler.state."""
        # Set filler running but press idle — coder should NOT print
        _set_filler_state(store, speed=60.0, state=2)
        _set_press_state(store, speed=0.0, state=3)
        sim_time = 0.0
        dt = 0.1
        for _ in range(10):
            sim_time += dt
            results = pkg_coder.generate(sim_time, dt, store)
        state = _find_signal(results, "coder.state")
        assert int(state.value) != CODER_PRINTING


class TestCoderFnbCoupling:
    """F&B coder config follows filler signals."""

    def test_state_signal_is_filler(self, fnb_coder: CoderGenerator) -> None:
        assert fnb_coder._state_signal == "filler.state"

    def test_speed_signal_is_filler(self, fnb_coder: CoderGenerator) -> None:
        assert fnb_coder._speed_signal == "filler.line_speed"

    def test_follows_filler_running(
        self, fnb_coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """F&B coder enters Printing when filler is Running (state=2)."""
        _set_filler_state(store, speed=60.0, state=2)
        sim_time = 0.0
        dt = 0.1
        for _ in range(10):
            sim_time += dt
            results = fnb_coder.generate(sim_time, dt, store)
        state = _find_signal(results, "coder.state")
        assert int(state.value) == CODER_PRINTING

    def test_standby_when_filler_starved(
        self, fnb_coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """F&B coder enters Standby when filler is Starved (state=3)."""
        _set_filler_state(store, speed=0.0, state=3)
        results = fnb_coder.generate(0.0, 0.1, store)
        state = _find_signal(results, "coder.state")
        # press_idle fires when state==3 → Standby
        assert int(state.value) == 4  # Standby

    def test_not_driven_by_press(
        self, fnb_coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """F&B coder ignores press.machine_state."""
        # Set press running but filler starved — coder should NOT print
        _set_press_state(store, speed=200.0, state=2)
        _set_filler_state(store, speed=0.0, state=3)
        sim_time = 0.0
        dt = 0.1
        for _ in range(10):
            sim_time += dt
            results = fnb_coder.generate(sim_time, dt, store)
        state = _find_signal(results, "coder.state")
        assert int(state.value) != CODER_PRINTING

    def test_pump_speed_follows_filler_speed(
        self, fnb_coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """Ink pump speed correlates with filler.line_speed."""
        _set_filler_state(store, speed=60.0, state=2)
        sim_time = 0.0
        dt = 0.1
        for _ in range(10):
            sim_time += dt
            results = fnb_coder.generate(sim_time, dt, store)
        pump = _find_signal(results, "coder.ink_pump_speed")
        # base=50 + factor=3 * speed=60 = 230; with noise, > 50
        assert pump.value > 50.0

    def test_ink_pump_at_base_when_filler_off(
        self, fnb_coder: CoderGenerator, store: SignalStore,
    ) -> None:
        """Ink pump runs at base speed (50 RPM) when filler is Off (not tracking speed)."""
        _set_filler_state(store, speed=0.0, state=0)
        results = fnb_coder.generate(0.0, 0.1, store)
        pump = _find_signal(results, "coder.ink_pump_speed")
        # base=50, factor=3, filler_speed=0 → pump ~50 (not tracking filler speed)
        # Contrast with filler running at 60: pump would be ~230
        assert pump.value == pytest.approx(50.0, abs=10.0)


# ===========================================================================
# ENERGY COUPLING TESTS
# ===========================================================================


class TestEnergyDefaultCoupling:
    """Packaging energy (no config) defaults to press.line_speed."""

    def test_default_speed_signal(self, pkg_energy: EnergyGenerator) -> None:
        assert pkg_energy._speed_signal == "press.line_speed"

    def test_power_follows_press(
        self, pkg_energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        """Line power scales with press.line_speed."""
        _set_press_state(store, speed=200.0, state=2)
        results = pkg_energy.generate(0.0, 0.1, store)
        power = _find_signal(results, "energy.line_power")
        # base=10 + factor=0.5 * 200 = 110 ± noise
        assert power.value > 50.0

    def test_base_load_when_press_idle(
        self, pkg_energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        """Base load present even when press is idle."""
        _set_press_state(store, speed=0.0, state=3)
        results = pkg_energy.generate(0.0, 0.1, store)
        power = _find_signal(results, "energy.line_power")
        # base=10, speed=0 → power ~10
        assert power.value == pytest.approx(10.0, abs=10.0)

    def test_not_driven_by_filler(
        self, pkg_energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        """Packaging energy ignores filler.line_speed."""
        _set_filler_state(store, speed=200.0, state=2)
        _set_press_state(store, speed=0.0, state=3)
        results = pkg_energy.generate(0.0, 0.1, store)
        power = _find_signal(results, "energy.line_power")
        # filler running at 200, press at 0 → power should be low (~base=10)
        assert power.value < 50.0


class TestEnergyFnbCoupling:
    """F&B energy config follows filler.line_speed."""

    def test_speed_signal_is_filler(self, fnb_energy: EnergyGenerator) -> None:
        assert fnb_energy._speed_signal == "filler.line_speed"

    def test_power_follows_filler(
        self, fnb_energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        """F&B line power scales with filler.line_speed."""
        _set_filler_state(store, speed=60.0, state=2)
        results = fnb_energy.generate(0.0, 0.1, store)
        power = _find_signal(results, "energy.line_power")
        # base=10 + factor=0.5 * 60 = 40 ± noise
        assert power.value > 20.0

    def test_base_load_when_filler_off(
        self, fnb_energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        """F&B base load from refrigeration even when filler is off."""
        _set_filler_state(store, speed=0.0, state=0)
        results = fnb_energy.generate(0.0, 0.1, store)
        power = _find_signal(results, "energy.line_power")
        # base=10, speed=0 → power ~10 (refrigeration base load)
        assert power.value == pytest.approx(10.0, abs=10.0)

    def test_not_driven_by_press(
        self, fnb_energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        """F&B energy ignores press.line_speed."""
        _set_press_state(store, speed=200.0, state=2)
        _set_filler_state(store, speed=0.0, state=0)
        results = fnb_energy.generate(0.0, 0.1, store)
        power = _find_signal(results, "energy.line_power")
        # press running at 200, filler off → power should be low (~base=10)
        assert power.value < 50.0
