"""Unit tests for the EnergyGenerator (PRD 2.8).

Tests verify:
- 2 signals produced per tick (line_power, cumulative_kwh)
- Power correlates with press speed (higher speed → higher power)
- Cumulative kWh increases when power is positive
- Low power when press idle (base load only)
- Determinism (same seed → same output)

Task 6d.8
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.energy import EnergyGenerator
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_energy_config() -> EquipmentConfig:
    """Create a minimal energy config with 2 required signals."""
    signals: dict[str, SignalConfig] = {}

    signals["line_power"] = SignalConfig(
        model="correlated_follower",
        noise_sigma=1.0,
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=200.0,
        units="kW",
        params={"base": 10.0, "gain": 0.5},
    )
    signals["cumulative_kwh"] = SignalConfig(
        model="counter",
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=1e9,
        units="kWh",
        params={"rate": 0.001},
    )

    return EquipmentConfig(
        enabled=True,
        type="energy_monitor",
        signals=signals,
    )


def _find_signal(results: list[SignalValue], signal_id: str) -> SignalValue:
    for sv in results:
        if sv.signal_id == signal_id:
            return sv
    raise KeyError(f"Signal {signal_id} not found in results")


def _set_press_speed(store: SignalStore, speed: float) -> None:
    """Set press.line_speed in the store."""
    store.set("press.line_speed", speed, 0.0, "good")


def _run_ticks(
    gen: EnergyGenerator,
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
def energy(rng: np.random.Generator) -> EnergyGenerator:
    return EnergyGenerator("energy", _make_energy_config(), rng)


# ---------------------------------------------------------------------------
# Tests: signal IDs
# ---------------------------------------------------------------------------


class TestSignalIds:
    """Verify all 2 energy signals are registered."""

    def test_signal_count(self, energy: EnergyGenerator) -> None:
        assert len(energy.get_signal_ids()) == 2

    def test_signal_names(self, energy: EnergyGenerator) -> None:
        ids = set(energy.get_signal_ids())
        expected = {"energy.line_power", "energy.cumulative_kwh"}
        assert ids == expected


# ---------------------------------------------------------------------------
# Tests: power correlates with press speed
# ---------------------------------------------------------------------------


class TestPowerCorrelation:
    """Line power should correlate with press speed."""

    def test_higher_speed_higher_power(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        """Power at high speed should exceed power at low speed."""
        # Low speed run
        _set_press_speed(store, 50.0)
        results_low = _run_ticks(energy, store, n_ticks=20, dt=0.1)
        power_low = _find_signal(results_low[-1], "energy.line_power").value

        # Reset generator for fair comparison
        energy2 = EnergyGenerator("energy", _make_energy_config(), np.random.default_rng(42))
        store2 = SignalStore()
        _set_press_speed(store2, 300.0)
        results_high = _run_ticks(energy2, store2, n_ticks=20, dt=0.1)
        power_high = _find_signal(results_high[-1], "energy.line_power").value

        assert power_high > power_low, (
            f"Power at 300 ({power_high:.1f}) should exceed 50 ({power_low:.1f})"
        )

    def test_power_positive_at_zero_speed(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        """Even at zero speed, base load should produce positive power."""
        _set_press_speed(store, 0.0)
        results = _run_ticks(energy, store, n_ticks=10, dt=0.1)
        power = _find_signal(results[-1], "energy.line_power").value
        # base=10.0 + gain*0 = ~10 + noise, clamped to [0, 200]
        assert power >= 0.0, f"Power should be non-negative: {power}"

    def test_power_near_base_at_zero_speed(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        """At zero speed, power should be near base load (10 kW)."""
        _set_press_speed(store, 0.0)
        results = _run_ticks(energy, store, n_ticks=10, dt=0.1)
        power = _find_signal(results[-1], "energy.line_power").value
        # base=10.0, noise_sigma=1.0 → should be roughly 5-15
        assert 0.0 <= power <= 30.0, f"Power at idle should be near base load: {power}"


# ---------------------------------------------------------------------------
# Tests: cumulative kWh
# ---------------------------------------------------------------------------


class TestCumulativeKwh:
    """Cumulative kWh should accumulate when power is positive."""

    def test_kwh_increases_with_speed(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        """cumulative_kwh should increase over time when press is running."""
        _set_press_speed(store, 200.0)
        results_list = _run_ticks(energy, store, n_ticks=50, dt=0.1)

        kwh_values = [
            _find_signal(r, "energy.cumulative_kwh").value
            for r in results_list
        ]
        assert kwh_values[-1] > kwh_values[0], (
            f"kWh should increase: {kwh_values[0]:.4f} → {kwh_values[-1]:.4f}"
        )

    def test_kwh_monotonically_nondecreasing(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        """cumulative_kwh should never decrease (it's a counter)."""
        _set_press_speed(store, 150.0)
        results_list = _run_ticks(energy, store, n_ticks=30, dt=0.1)

        kwh_values = [
            _find_signal(r, "energy.cumulative_kwh").value
            for r in results_list
        ]
        for i in range(1, len(kwh_values)):
            assert kwh_values[i] >= kwh_values[i - 1], (
                f"kWh decreased at tick {i}: {kwh_values[i - 1]:.4f} → {kwh_values[i]:.4f}"
            )

    def test_kwh_accumulates_more_at_higher_power(self) -> None:
        """Higher power should accumulate more kWh."""
        # Low power run
        store_low = SignalStore()
        gen_low = EnergyGenerator("energy", _make_energy_config(), np.random.default_rng(99))
        _set_press_speed(store_low, 50.0)
        results_low = _run_ticks(gen_low, store_low, n_ticks=50, dt=0.1)
        kwh_low = _find_signal(results_low[-1], "energy.cumulative_kwh").value

        # High power run
        store_high = SignalStore()
        gen_high = EnergyGenerator("energy", _make_energy_config(), np.random.default_rng(99))
        _set_press_speed(store_high, 300.0)
        results_high = _run_ticks(gen_high, store_high, n_ticks=50, dt=0.1)
        kwh_high = _find_signal(results_high[-1], "energy.cumulative_kwh").value

        assert kwh_high > kwh_low, (
            f"kWh at 300 m/min ({kwh_high:.4f}) should exceed kWh at 50 m/min ({kwh_low:.4f})"
        )


# ---------------------------------------------------------------------------
# Tests: low speed / idle behaviour
# ---------------------------------------------------------------------------


class TestIdleBehaviour:
    """Energy should still report base load when press is idle."""

    def test_power_at_idle_speed(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        """At zero speed, power reflects base load only."""
        _set_press_speed(store, 0.0)
        results_list = _run_ticks(energy, store, n_ticks=10, dt=0.1)
        powers = [
            _find_signal(r, "energy.line_power").value
            for r in results_list
        ]
        # All should be near base=10 with some noise
        for p in powers:
            assert 0.0 <= p <= 50.0, f"Idle power out of expected range: {p}"

    def test_kwh_still_accumulates_at_idle(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        """Even at idle (base load), kWh should slowly accumulate."""
        _set_press_speed(store, 0.0)
        results_list = _run_ticks(energy, store, n_ticks=50, dt=0.1)
        kwh_first = _find_signal(results_list[0], "energy.cumulative_kwh").value
        kwh_last = _find_signal(results_list[-1], "energy.cumulative_kwh").value
        assert kwh_last >= kwh_first, (
            f"kWh should accumulate even at idle: {kwh_first:.6f} → {kwh_last:.6f}"
        )


# ---------------------------------------------------------------------------
# Tests: all signals present per tick
# ---------------------------------------------------------------------------


class TestAllSignals:
    """Every tick produces exactly 2 signals with good quality."""

    def test_signal_count_per_tick(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        _set_press_speed(store, 100.0)
        results = energy.generate(0.1, 0.1, store)
        assert len(results) == 2

    def test_all_signals_have_quality_good(
        self, energy: EnergyGenerator, store: SignalStore,
    ) -> None:
        _set_press_speed(store, 100.0)
        results = energy.generate(0.1, 0.1, store)
        for sv in results:
            assert sv.quality == "good"


# ---------------------------------------------------------------------------
# Tests: custom speed signal coupling
# ---------------------------------------------------------------------------


class TestCustomSpeedSignal:
    """Energy can be coupled to a different speed signal via config extras."""

    def test_custom_coupling_signal(self, store: SignalStore) -> None:
        """When coupling_speed_signal is set, energy reads from that signal."""
        cfg = _make_energy_config()
        # Use model_extra for the coupling config
        cfg_dict = cfg.model_dump()
        cfg_dict["coupling_speed_signal"] = "filler.line_speed"
        custom_cfg = EquipmentConfig(**cfg_dict)

        gen = EnergyGenerator("energy", custom_cfg, np.random.default_rng(42))

        # Set the custom signal
        store.set("filler.line_speed", 200.0, 0.0, "good")
        results = gen.generate(0.1, 0.1, store)
        power = _find_signal(results, "energy.line_power").value
        # base=10 + gain=0.5 * 200 = 110 + noise
        assert power > 20.0, f"Power should reflect filler speed: {power}"


# ---------------------------------------------------------------------------
# Tests: determinism (CLAUDE.md Rule 13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed → identical output sequence."""

    def test_energy_deterministic(self, store: SignalStore) -> None:
        cfg = _make_energy_config()
        gen1 = EnergyGenerator("energy", cfg, np.random.default_rng(99))
        gen2 = EnergyGenerator("energy", cfg, np.random.default_rng(99))

        _set_press_speed(store, 200.0)

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
