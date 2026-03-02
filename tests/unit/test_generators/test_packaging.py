"""Tests for remaining packaging generators (task 1.17).

Covers: Laminator (5 signals), Slitter (3 signals), Coder (11 signals),
Environment (2 signals), Energy (2 signals), Vibration (3 signals).

Verifies:
- Correct signal IDs produced by each generator.
- Signal values within expected ranges.
- Cross-equipment correlations (laminator follows press, etc.).
- Scheduled operation (slitter).
- State-driven behaviour (coder follows press state).
- Deterministic output with same seed.

PRD Reference: Sections 2.3-2.9
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.coder import CoderGenerator
from factory_simulator.generators.energy import EnergyGenerator
from factory_simulator.generators.environment import EnvironmentGenerator
from factory_simulator.generators.laminator import LaminatorGenerator
from factory_simulator.generators.slitter import SlitterGenerator
from factory_simulator.generators.vibration import VibrationGenerator
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------


def _make_laminator_config() -> EquipmentConfig:
    signals: dict[str, SignalConfig] = {}

    signals["nip_temp"] = SignalConfig(
        model="first_order_lag",
        noise_sigma=0.5,
        sample_rate_ms=5000,
        min_clamp=20.0,
        max_clamp=100.0,
        params={"tau": 10.0, "initial_value": 20.0, "setpoint": 55.0},
    )
    signals["nip_pressure"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.15,
        sample_rate_ms=5000,
        min_clamp=1.0,
        max_clamp=8.0,
        params={"target": 4.0},
    )
    signals["tunnel_temp"] = SignalConfig(
        model="first_order_lag",
        noise_sigma=0.8,
        sample_rate_ms=5000,
        min_clamp=20.0,
        max_clamp=120.0,
        params={"tau": 10.0, "initial_value": 20.0, "setpoint": 65.0},
    )
    signals["web_speed"] = SignalConfig(
        model="correlated_follower",
        parent="press.line_speed",
        noise_sigma=0.3,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=400.0,
        params={"base": 0.0, "factor": 1.0},
    )
    signals["adhesive_weight"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.15,
        sample_rate_ms=30000,
        min_clamp=1.0,
        max_clamp=5.0,
        params={"target": 2.5},
    )

    return EquipmentConfig(
        enabled=True,
        type="solvent_free_laminator",
        signals=signals,
    )


def _make_slitter_config() -> EquipmentConfig:
    signals: dict[str, SignalConfig] = {}

    signals["speed"] = SignalConfig(
        model="ramp",
        noise_sigma=1.0,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=800.0,
        params={"ramp_duration_s": 5.0},  # short for tests
    )
    signals["web_tension"] = SignalConfig(
        model="correlated_follower",
        parent="slitter.speed",
        noise_sigma=3.0,
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=200.0,
        params={"base": 20.0, "factor": 0.15},
    )
    signals["reel_count"] = SignalConfig(
        model="counter",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=9999.0,
        params={"rate": 0.005, "rollover": 9999},
    )

    return EquipmentConfig(
        enabled=True,
        type="slitter_rewinder",
        signals=signals,
        schedule_offset_hours=2.0,
        run_duration_hours=4.0,
    )


def _make_coder_config() -> EquipmentConfig:
    signals: dict[str, SignalConfig] = {}

    signals["state"] = SignalConfig(
        model="state_machine",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=4.0,
        params={
            "states": ["off", "ready", "printing", "fault", "standby"],
            "initial_state": "ready",
        },
    )
    signals["prints_total"] = SignalConfig(
        model="counter",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=999999999.0,
        params={"rate": 1.0, "rollover": 999999999},
    )
    signals["ink_level"] = SignalConfig(
        model="depletion",
        sample_rate_ms=60000,
        min_clamp=0.0,
        max_clamp=100.0,
        params={
            "initial_value": 100.0,
            "consumption_rate": 0.005,
            "refill_threshold": 5.0,
            "refill_value": 100.0,
        },
    )
    signals["printhead_temp"] = SignalConfig(
        model="steady_state",
        noise_sigma=2.0,
        sample_rate_ms=30000,
        min_clamp=25.0,
        max_clamp=50.0,
        params={"target": 35.0},
    )
    signals["ink_pump_speed"] = SignalConfig(
        model="correlated_follower",
        parent="press.line_speed",
        noise_sigma=10.0,
        sample_rate_ms=5000,
        min_clamp=0.0,
        max_clamp=500.0,
        params={"base": 50.0, "factor": 1.0},
    )
    signals["ink_pressure"] = SignalConfig(
        model="steady_state",
        noise_sigma=10.0,
        sample_rate_ms=5000,
        min_clamp=0.0,
        max_clamp=900.0,
        params={"target": 835.0},
    )
    signals["ink_viscosity_actual"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.5,
        sample_rate_ms=30000,
        min_clamp=2.0,
        max_clamp=15.0,
        params={"target": 8.0},
    )
    signals["supply_voltage"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.2,
        sample_rate_ms=60000,
        min_clamp=22.0,
        max_clamp=26.0,
        params={"target": 24.0},
    )
    signals["ink_consumption_ml"] = SignalConfig(
        model="counter",
        sample_rate_ms=60000,
        min_clamp=0.0,
        max_clamp=999999.0,
        params={"rate": 0.01, "rollover": 999999},
    )
    signals["nozzle_health"] = SignalConfig(
        model="depletion",
        sample_rate_ms=60000,
        min_clamp=0.0,
        max_clamp=100.0,
        params={
            "initial_value": 100.0,
            "consumption_rate": 0.001,
            "refill_threshold": 70.0,
            "refill_value": 100.0,
        },
    )
    signals["gutter_fault"] = SignalConfig(
        model="state_machine",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=1.0,
        params={
            "states": ["clear", "fault"],
            "initial_state": "clear",
        },
    )

    return EquipmentConfig(
        enabled=True,
        type="cij_printer",
        signals=signals,
    )


def _make_environment_config() -> EquipmentConfig:
    signals: dict[str, SignalConfig] = {}

    signals["ambient_temp"] = SignalConfig(
        model="sinusoidal",
        noise_sigma=0.3,
        sample_rate_ms=60000,
        min_clamp=15.0,
        max_clamp=35.0,
        params={"center": 22.0, "amplitude": 3.0, "period": 86400.0},
    )
    signals["ambient_humidity"] = SignalConfig(
        model="sinusoidal",
        noise_sigma=1.0,
        sample_rate_ms=60000,
        min_clamp=30.0,
        max_clamp=80.0,
        params={
            "center": 55.0,
            "amplitude": 10.0,
            "period": 86400.0,
            "phase": 3.14159,
        },
    )

    return EquipmentConfig(
        enabled=True,
        type="iolink_sensor",
        signals=signals,
    )


def _make_energy_config() -> EquipmentConfig:
    signals: dict[str, SignalConfig] = {}

    signals["line_power"] = SignalConfig(
        model="correlated_follower",
        parent="press.line_speed",
        noise_sigma=2.0,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=200.0,
        params={"base": 10.0, "factor": 0.5},
    )
    signals["cumulative_kwh"] = SignalConfig(
        model="counter",
        sample_rate_ms=60000,
        min_clamp=0.0,
        max_clamp=999999.0,
        params={"rate": 0.001, "rollover": 999999},
    )

    return EquipmentConfig(
        enabled=True,
        type="power_meter",
        signals=signals,
    )


def _make_vibration_config() -> EquipmentConfig:
    signals: dict[str, SignalConfig] = {}

    signals["main_drive_x"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.5,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=50.0,
        params={"target": 4.0},
    )
    signals["main_drive_y"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.5,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=50.0,
        params={"target": 3.5},
    )
    signals["main_drive_z"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.8,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=50.0,
        params={"target": 5.0},
    )

    return EquipmentConfig(
        enabled=True,
        type="wireless_vibration",
        signals=signals,
    )


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
def laminator(rng: np.random.Generator) -> LaminatorGenerator:
    return LaminatorGenerator("laminator", _make_laminator_config(), rng)


@pytest.fixture
def slitter(rng: np.random.Generator) -> SlitterGenerator:
    return SlitterGenerator("slitter", _make_slitter_config(), rng)


@pytest.fixture
def coder(rng: np.random.Generator) -> CoderGenerator:
    return CoderGenerator("coder", _make_coder_config(), rng)


@pytest.fixture
def environment(rng: np.random.Generator) -> EnvironmentGenerator:
    return EnvironmentGenerator("env", _make_environment_config(), rng)


@pytest.fixture
def energy(rng: np.random.Generator) -> EnergyGenerator:
    return EnergyGenerator("energy", _make_energy_config(), rng)


@pytest.fixture
def vibration(rng: np.random.Generator) -> VibrationGenerator:
    return VibrationGenerator("vibration", _make_vibration_config(), rng)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_signal(results: list[SignalValue], signal_id: str) -> SignalValue:
    for sv in results:
        if sv.signal_id == signal_id:
            return sv
    raise AssertionError(f"Signal {signal_id} not found in results")


def _set_press_state(
    store: SignalStore,
    speed: float = 0.0,
    state: int = 3,
    sim_time: float = 0.0,
) -> None:
    """Write press signals to store for cross-equipment correlation."""
    store.set("press.line_speed", speed, sim_time)
    store.set("press.machine_state", float(state), sim_time)


# ===========================================================================
# LAMINATOR TESTS
# ===========================================================================


LAMINATOR_SIGNAL_IDS = sorted([
    "laminator.nip_temp",
    "laminator.nip_pressure",
    "laminator.tunnel_temp",
    "laminator.web_speed",
    "laminator.adhesive_weight",
])


class TestLaminatorSignalIds:
    """Laminator produces all 5 signal IDs."""

    def test_signal_id_count(self, laminator: LaminatorGenerator) -> None:
        assert len(laminator.get_signal_ids()) == 5

    def test_signal_ids_complete(self, laminator: LaminatorGenerator) -> None:
        assert sorted(laminator.get_signal_ids()) == LAMINATOR_SIGNAL_IDS

    def test_generate_produces_all(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        results = laminator.generate(0.0, 0.1, store)
        produced = sorted(sv.signal_id for sv in results)
        assert produced == LAMINATOR_SIGNAL_IDS


class TestLaminatorBehaviour:
    """Laminator follows press speed, temps track setpoints."""

    def test_web_speed_follows_press(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=200.0, state=2)
        results = laminator.generate(0.0, 0.1, store)
        ws = _find_signal(results, "laminator.web_speed")
        # base=0, factor=1.0, speed=200 -> web_speed ~200 ± noise
        assert ws.value == pytest.approx(200.0, abs=5.0)

    def test_web_speed_zero_when_press_stopped(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=0.0, state=3)
        results = laminator.generate(0.0, 0.1, store)
        ws = _find_signal(results, "laminator.web_speed")
        assert ws.value == pytest.approx(0.0, abs=1.0)

    def test_nip_temp_heats_when_active(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=200.0, state=2)
        sim_time = 0.0
        dt = 0.1
        for _ in range(1000):  # 100s, many tau
            sim_time += dt
            results = laminator.generate(sim_time, dt, store)
        nip_temp = _find_signal(results, "laminator.nip_temp")
        # Should approach setpoint of 55C
        assert nip_temp.value > 40.0, "Nip temp should heat toward setpoint"

    def test_nip_temp_cools_when_stopped(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        # First heat up
        _set_press_state(store, speed=200.0, state=2)
        sim_time = 0.0
        dt = 0.1
        for _ in range(1000):
            sim_time += dt
            laminator.generate(sim_time, dt, store)

        # Now stop
        _set_press_state(store, speed=0.0, state=3)
        for _ in range(1000):
            sim_time += dt
            results = laminator.generate(sim_time, dt, store)
        nip_temp = _find_signal(results, "laminator.nip_temp")
        assert nip_temp.value < 30.0, "Nip temp should cool toward ambient"

    def test_nip_pressure_zero_when_stopped(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=0.0, state=3)
        results = laminator.generate(0.0, 0.1, store)
        nip_p = _find_signal(results, "laminator.nip_pressure")
        # nip_pressure min_clamp=1.0, so when inactive raw=0 -> clamped to 1.0
        assert nip_p.value == pytest.approx(1.0, abs=0.01)


# ===========================================================================
# SLITTER TESTS
# ===========================================================================


SLITTER_SIGNAL_IDS = sorted([
    "slitter.speed",
    "slitter.web_tension",
    "slitter.reel_count",
])


class TestSlitterSignalIds:
    """Slitter produces all 3 signal IDs."""

    def test_signal_id_count(self, slitter: SlitterGenerator) -> None:
        assert len(slitter.get_signal_ids()) == 3

    def test_signal_ids_complete(self, slitter: SlitterGenerator) -> None:
        assert sorted(slitter.get_signal_ids()) == SLITTER_SIGNAL_IDS

    def test_generate_produces_all(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        results = slitter.generate(0.0, 0.1, store)
        produced = sorted(sv.signal_id for sv in results)
        assert produced == SLITTER_SIGNAL_IDS


class TestSlitterSchedule:
    """Slitter operates on a schedule."""

    def test_speed_zero_before_schedule(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        """At sim_time=0 (start of shift), offset=2h: slitter should be off."""
        results = slitter.generate(0.0, 0.1, store)
        speed = _find_signal(results, "slitter.speed")
        assert speed.value == pytest.approx(0.0, abs=1.0)

    def test_speed_nonzero_during_schedule(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        """At sim_time=2h+5min: slitter should be running (within schedule)."""
        # 2 hours offset + 5 minutes into run
        start_time = 2.0 * 3600.0 + 300.0
        sim_time = start_time
        dt = 0.1
        # Run enough ticks to complete the ramp (5s ramp in test config)
        for _ in range(200):
            sim_time += dt
            results = slitter.generate(sim_time, dt, store)
        speed = _find_signal(results, "slitter.speed")
        assert speed.value > 100.0, "Slitter should be running during schedule"

    def test_speed_zero_after_schedule(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        """At sim_time=6h+30min: slitter should be stopped (past 2h+4h=6h)."""
        # First ramp up in schedule window
        sim_time = 2.0 * 3600.0 + 60.0
        dt = 0.1
        for _ in range(100):
            sim_time += dt
            slitter.generate(sim_time, dt, store)

        # Jump to after schedule ends and run enough for ramp down
        sim_time = 6.0 * 3600.0 + 1800.0
        for _ in range(500):
            sim_time += dt
            results = slitter.generate(sim_time, dt, store)
        speed = _find_signal(results, "slitter.speed")
        assert speed.value == pytest.approx(0.0, abs=1.0)

    def test_reel_count_increments_during_schedule(
        self, slitter: SlitterGenerator, store: SignalStore,
    ) -> None:
        sim_time = 2.0 * 3600.0 + 60.0
        dt = 0.1
        for _ in range(300):
            sim_time += dt
            results = slitter.generate(sim_time, dt, store)
        reel = _find_signal(results, "slitter.reel_count")
        assert reel.value > 0.0, "Reel count should increment when running"


# ===========================================================================
# CODER TESTS
# ===========================================================================


CODER_SIGNAL_IDS = sorted([
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
])


class TestCoderSignalIds:
    """Coder produces all 11 signal IDs."""

    def test_signal_id_count(self, coder: CoderGenerator) -> None:
        assert len(coder.get_signal_ids()) == 11

    def test_signal_ids_complete(self, coder: CoderGenerator) -> None:
        assert sorted(coder.get_signal_ids()) == CODER_SIGNAL_IDS

    def test_generate_produces_all(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=0.0, state=3)
        results = coder.generate(0.0, 0.1, store)
        produced = sorted(sv.signal_id for sv in results)
        assert produced == CODER_SIGNAL_IDS


class TestCoderBehaviour:
    """Coder follows press state."""

    def test_standby_when_press_idle(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        # PRD 2.5: coder enters Standby when press is idle
        _set_press_state(store, speed=0.0, state=3)
        results = coder.generate(0.0, 0.1, store)
        state = _find_signal(results, "coder.state")
        assert int(state.value) == 4  # Standby

    def test_transitions_to_printing_when_press_running(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=200.0, state=2)
        sim_time = 0.0
        dt = 0.1
        for _ in range(10):
            sim_time += dt
            results = coder.generate(sim_time, dt, store)
        state = _find_signal(results, "coder.state")
        assert int(state.value) == 2  # Printing

    def test_prints_total_increments_when_printing(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=200.0, state=2)
        sim_time = 0.0
        dt = 0.1
        for _ in range(100):
            sim_time += dt
            results = coder.generate(sim_time, dt, store)
        prints = _find_signal(results, "coder.prints_total")
        assert prints.value > 0.0, "Prints should increment when printing"

    def test_ink_level_depletes_when_printing(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=200.0, state=2)
        sim_time = 0.0
        dt = 0.1
        for _ in range(100):
            sim_time += dt
            results = coder.generate(sim_time, dt, store)
        ink = _find_signal(results, "coder.ink_level")
        assert ink.value < 100.0, "Ink level should deplete when printing"

    def test_gutter_fault_is_binary(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=0.0, state=3)
        results = coder.generate(0.0, 0.1, store)
        gutter = _find_signal(results, "coder.gutter_fault")
        assert gutter.value in (0.0, 1.0)


# ===========================================================================
# ENVIRONMENT TESTS
# ===========================================================================


ENV_SIGNAL_IDS = sorted([
    "env.ambient_temp",
    "env.ambient_humidity",
])


class TestEnvironmentSignalIds:
    """Environment produces all 2 signal IDs."""

    def test_signal_id_count(self, environment: EnvironmentGenerator) -> None:
        assert len(environment.get_signal_ids()) == 2

    def test_signal_ids_complete(self, environment: EnvironmentGenerator) -> None:
        assert sorted(environment.get_signal_ids()) == ENV_SIGNAL_IDS

    def test_generate_produces_all(
        self, environment: EnvironmentGenerator, store: SignalStore,
    ) -> None:
        results = environment.generate(0.0, 0.1, store)
        produced = sorted(sv.signal_id for sv in results)
        assert produced == ENV_SIGNAL_IDS


class TestEnvironmentBehaviour:
    """Environment follows sinusoidal daily patterns."""

    def test_temp_within_bounds(
        self, environment: EnvironmentGenerator, store: SignalStore,
    ) -> None:
        sim_time = 0.0
        dt = 60.0  # 1 minute steps
        for _ in range(1440):  # Full day
            sim_time += dt
            results = environment.generate(sim_time, dt, store)
            temp = _find_signal(results, "env.ambient_temp")
            assert 15.0 <= temp.value <= 35.0

    def test_humidity_within_bounds(
        self, environment: EnvironmentGenerator, store: SignalStore,
    ) -> None:
        sim_time = 0.0
        dt = 60.0
        for _ in range(1440):
            sim_time += dt
            results = environment.generate(sim_time, dt, store)
            humid = _find_signal(results, "env.ambient_humidity")
            assert 30.0 <= humid.value <= 80.0

    def test_temp_varies_over_day(
        self, environment: EnvironmentGenerator, store: SignalStore,
    ) -> None:
        """Temperature should show variation over a full day."""
        temps = []
        sim_time = 0.0
        dt = 3600.0  # 1 hour steps
        for _ in range(24):
            sim_time += dt
            results = environment.generate(sim_time, dt, store)
            temp = _find_signal(results, "env.ambient_temp")
            temps.append(temp.value)
        # Range should show sinusoidal variation (amplitude=3, so ~6C range)
        temp_range = max(temps) - min(temps)
        assert temp_range > 2.0, "Temperature should vary over a day"


# ===========================================================================
# ENERGY TESTS
# ===========================================================================


ENERGY_SIGNAL_IDS = sorted([
    "energy.line_power",
    "energy.cumulative_kwh",
])


class TestEnergySignalIds:
    """Energy produces all 2 signal IDs."""

    def test_signal_id_count(self, energy: EnergyGenerator) -> None:
        assert len(energy.get_signal_ids()) == 2

    def test_signal_ids_complete(self, energy: EnergyGenerator) -> None:
        assert sorted(energy.get_signal_ids()) == ENERGY_SIGNAL_IDS

    def test_generate_produces_all(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        results = energy.generate(0.0, 0.1, store)
        produced = sorted(sv.signal_id for sv in results)
        assert produced == ENERGY_SIGNAL_IDS


class TestEnergyBehaviour:
    """Energy correlates with press speed."""

    def test_base_load_when_idle(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=0.0, state=3)
        results = energy.generate(0.0, 0.1, store)
        power = _find_signal(results, "energy.line_power")
        # base=10, factor=0.5, speed=0 -> power ~10 ± noise
        assert power.value == pytest.approx(10.0, abs=10.0)

    def test_high_load_when_running(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=200.0, state=2)
        results = energy.generate(0.0, 0.1, store)
        power = _find_signal(results, "energy.line_power")
        # base=10, factor=0.5, speed=200 -> power ~110 ± noise
        assert power.value > 50.0, "Power should increase with press speed"

    def test_cumulative_kwh_accumulates(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=200.0, state=2)
        sim_time = 0.0
        dt = 0.1
        for _ in range(100):
            sim_time += dt
            results = energy.generate(sim_time, dt, store)
        kwh = _find_signal(results, "energy.cumulative_kwh")
        assert kwh.value > 0.0, "Cumulative kWh should accumulate"


# ===========================================================================
# VIBRATION TESTS
# ===========================================================================


VIB_SIGNAL_IDS = sorted([
    "vibration.main_drive_x",
    "vibration.main_drive_y",
    "vibration.main_drive_z",
])


class TestVibrationSignalIds:
    """Vibration produces all 3 signal IDs."""

    def test_signal_id_count(self, vibration: VibrationGenerator) -> None:
        assert len(vibration.get_signal_ids()) == 3

    def test_signal_ids_complete(self, vibration: VibrationGenerator) -> None:
        assert sorted(vibration.get_signal_ids()) == VIB_SIGNAL_IDS

    def test_generate_produces_all(
        self, vibration: VibrationGenerator, store: SignalStore,
    ) -> None:
        results = vibration.generate(0.0, 0.1, store)
        produced = sorted(sv.signal_id for sv in results)
        assert produced == VIB_SIGNAL_IDS


class TestVibrationBehaviour:
    """Vibration active when press running, low when stopped."""

    def test_vibration_active_when_running(
        self, vibration: VibrationGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=200.0, state=2)
        results = vibration.generate(0.0, 0.1, store)
        x = _find_signal(results, "vibration.main_drive_x")
        y = _find_signal(results, "vibration.main_drive_y")
        z = _find_signal(results, "vibration.main_drive_z")
        # Should be near their target values (4, 3.5, 5) with noise
        assert x.value > 1.0, "X vibration should be active when running"
        assert y.value > 1.0, "Y vibration should be active when running"
        assert z.value > 1.0, "Z vibration should be active when running"

    def test_vibration_low_when_stopped(
        self, vibration: VibrationGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=0.0, state=3)
        results = vibration.generate(0.0, 0.1, store)
        x = _find_signal(results, "vibration.main_drive_x")
        y = _find_signal(results, "vibration.main_drive_y")
        z = _find_signal(results, "vibration.main_drive_z")
        # Should be near-zero (residual floor vibration)
        assert x.value < 2.0, "X vibration should be low when stopped"
        assert y.value < 2.0, "Y vibration should be low when stopped"
        assert z.value < 2.0, "Z vibration should be low when stopped"

    def test_vibration_axes_correlated(
        self, store: SignalStore,
    ) -> None:
        """Three axes should show correlation (same mechanical source)."""
        _set_press_state(store, speed=200.0, state=2)

        # Run many ticks and collect values
        x_vals: list[float] = []
        y_vals: list[float] = []

        # Use fixed seed for reproducibility
        vib = VibrationGenerator(
            "vibration", _make_vibration_config(),
            np.random.default_rng(123),
        )

        sim_time = 0.0
        dt = 0.1
        for _ in range(500):
            sim_time += dt
            results = vib.generate(sim_time, dt, store)
            x_vals.append(_find_signal(results, "vibration.main_drive_x").value)
            y_vals.append(_find_signal(results, "vibration.main_drive_y").value)

        # Compute Pearson correlation
        x_arr = np.array(x_vals)
        y_arr = np.array(y_vals)
        corr = float(np.corrcoef(x_arr, y_arr)[0, 1])
        # Should show positive correlation (configured at 0.6)
        assert corr > 0.2, f"Axes should be correlated, got r={corr:.3f}"

    def test_all_signals_within_bounds(
        self, vibration: VibrationGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=200.0, state=2)
        sim_time = 0.0
        dt = 0.1
        for _ in range(100):
            sim_time += dt
            results = vibration.generate(sim_time, dt, store)
            for sv in results:
                assert 0.0 <= sv.value <= 50.0, (
                    f"{sv.signal_id} = {sv.value} out of bounds"
                )


# ===========================================================================
# DETERMINISM (all generators)
# ===========================================================================


class TestDeterminism:
    """Same seed produces identical output for all generators."""

    def test_laminator_deterministic(self, store: SignalStore) -> None:
        _set_press_state(store, speed=200.0, state=2)
        self._check_deterministic(
            lambda rng: LaminatorGenerator("laminator", _make_laminator_config(), rng),
            store,
        )

    def test_slitter_deterministic(self, store: SignalStore) -> None:
        # Run at a time within the schedule window
        self._check_deterministic(
            lambda rng: SlitterGenerator("slitter", _make_slitter_config(), rng),
            store,
            start_time=2.0 * 3600.0 + 300.0,
        )

    def test_coder_deterministic(self, store: SignalStore) -> None:
        _set_press_state(store, speed=200.0, state=2)
        self._check_deterministic(
            lambda rng: CoderGenerator("coder", _make_coder_config(), rng),
            store,
        )

    def test_environment_deterministic(self, store: SignalStore) -> None:
        self._check_deterministic(
            lambda rng: EnvironmentGenerator("env", _make_environment_config(), rng),
            store,
        )

    def test_energy_deterministic(self, store: SignalStore) -> None:
        _set_press_state(store, speed=200.0, state=2)
        self._check_deterministic(
            lambda rng: EnergyGenerator("energy", _make_energy_config(), rng),
            store,
        )

    def test_vibration_deterministic(self, store: SignalStore) -> None:
        _set_press_state(store, speed=200.0, state=2)
        self._check_deterministic(
            lambda rng: VibrationGenerator("vibration", _make_vibration_config(), rng),
            store,
        )

    @staticmethod
    def _check_deterministic(
        factory: object,
        store: SignalStore,
        start_time: float = 0.0,
    ) -> None:
        gen1 = factory(np.random.default_rng(99))  # type: ignore[operator]
        gen2 = factory(np.random.default_rng(99))  # type: ignore[operator]

        sim_time = start_time
        dt = 0.1
        r1: list[SignalValue] = []
        r2: list[SignalValue] = []
        for _ in range(50):
            sim_time += dt
            r1 = gen1.generate(sim_time, dt, store)
            r2 = gen2.generate(sim_time, dt, store)

        for sv1, sv2 in zip(r1, r2, strict=True):
            assert sv1.signal_id == sv2.signal_id
            assert sv1.value == sv2.value, (
                f"{sv1.signal_id}: {sv1.value} != {sv2.value}"
            )


# ===========================================================================
# BOUNDS (all generators)
# ===========================================================================


class TestBounds:
    """All signals respect their physical bounds."""

    def test_laminator_bounds(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=200.0, state=2)
        self._check_bounds(laminator, store)

    def test_coder_bounds(
        self, coder: CoderGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=200.0, state=2)
        self._check_bounds(coder, store)

    def test_environment_bounds(
        self, environment: EnvironmentGenerator, store: SignalStore,
    ) -> None:
        self._check_bounds(environment, store)

    def test_energy_bounds(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        _set_press_state(store, speed=200.0, state=2)
        self._check_bounds(energy, store)

    @staticmethod
    def _check_bounds(gen: object, store: SignalStore) -> None:
        sim_time = 0.0
        dt = 0.1
        for _ in range(100):
            sim_time += dt
            results = gen.generate(sim_time, dt, store)  # type: ignore[union-attr]

        for sv in results:  # type: ignore[possibly-undefined]
            sig_name = sv.signal_id.split(".", 1)[1]
            sig_cfg = gen._signal_configs.get(sig_name)  # type: ignore[union-attr]
            if sig_cfg is not None and sig_cfg.min_clamp is not None:
                assert sv.value >= sig_cfg.min_clamp, (
                    f"{sv.signal_id} = {sv.value} < min {sig_cfg.min_clamp}"
                )
            if sig_cfg is not None and sig_cfg.max_clamp is not None:
                assert sv.value <= sig_cfg.max_clamp, (
                    f"{sv.signal_id} = {sv.value} > max {sig_cfg.max_clamp}"
                )
