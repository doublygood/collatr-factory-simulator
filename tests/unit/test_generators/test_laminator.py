"""Unit tests for the LaminatorGenerator (PRD 2.3).

Tests verify:
- 5 signals produced per tick
- web_speed tracks press.line_speed via correlated follower
- nip_temp tracks setpoint when active, cools toward ambient when inactive
- nip_pressure active only when running, zero when stopped
- tunnel_temp tracks setpoint when active, cools toward ambient when inactive
- adhesive_weight active only when running, zero when stopped
- Off state produces zeros/ambient for inactive signals
- Determinism (same seed -> same output)

Task 6d.9
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.laminator import LaminatorGenerator
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_laminator_config() -> EquipmentConfig:
    """Create a minimal laminator config with all 5 required signals."""
    signals: dict[str, SignalConfig] = {}

    signals["nip_temp"] = SignalConfig(
        model="first_order_lag",
        noise_sigma=0.3,
        sample_rate_ms=500,
        min_clamp=15.0,
        max_clamp=120.0,
        units="C",
        params={"setpoint": 55.0, "tau": 120.0, "initial_value": 20.0},
    )
    signals["nip_pressure"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.1,
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=10.0,
        units="bar",
        params={"target": 4.0},
    )
    signals["tunnel_temp"] = SignalConfig(
        model="first_order_lag",
        noise_sigma=0.3,
        sample_rate_ms=500,
        min_clamp=15.0,
        max_clamp=150.0,
        units="C",
        params={"setpoint": 65.0, "tau": 120.0, "initial_value": 20.0},
    )
    signals["web_speed"] = SignalConfig(
        model="correlated_follower",
        noise_sigma=1.0,
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=600.0,
        units="m/min",
        params={"base": 0.0, "factor": 1.0},
    )
    signals["adhesive_weight"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.05,
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=50.0,
        units="g/m2",
        params={"target": 5.0},
    )

    return EquipmentConfig(
        enabled=True,
        type="laminator",
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
    gen: LaminatorGenerator,
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
def laminator(rng: np.random.Generator) -> LaminatorGenerator:
    return LaminatorGenerator("laminator", _make_laminator_config(), rng)


# ---------------------------------------------------------------------------
# Tests: signal IDs
# ---------------------------------------------------------------------------


class TestSignalIds:
    """Verify all 5 laminator signals are registered."""

    def test_signal_count(self, laminator: LaminatorGenerator) -> None:
        assert len(laminator.get_signal_ids()) == 5

    def test_signal_names(self, laminator: LaminatorGenerator) -> None:
        ids = set(laminator.get_signal_ids())
        expected = {
            "laminator.nip_temp",
            "laminator.nip_pressure",
            "laminator.tunnel_temp",
            "laminator.web_speed",
            "laminator.adhesive_weight",
        }
        assert ids == expected


# ---------------------------------------------------------------------------
# Tests: off state (press speed = 0)
# ---------------------------------------------------------------------------


class TestOffState:
    """When press is stopped (speed=0), laminator produces inactive values."""

    def test_web_speed_near_zero_when_stopped(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        """Web speed should be near base (0) when press stopped."""
        _set_press_speed(store, 0.0)
        results = laminator.generate(0.1, 0.1, store)
        ws = _find_signal(results, "laminator.web_speed").value
        # base=0, gain*0=0, plus noise — clamped to min_clamp=0
        assert ws == pytest.approx(0.0, abs=5.0)

    def test_nip_pressure_zero_when_stopped(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        """Nip pressure should be 0 when press stopped."""
        _set_press_speed(store, 0.0)
        results = laminator.generate(0.1, 0.1, store)
        np_ = _find_signal(results, "laminator.nip_pressure").value
        assert np_ == 0.0

    def test_adhesive_weight_zero_when_stopped(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        """Adhesive weight should be 0 when press stopped."""
        _set_press_speed(store, 0.0)
        results = laminator.generate(0.1, 0.1, store)
        aw = _find_signal(results, "laminator.adhesive_weight").value
        assert aw == 0.0

    def test_nip_temp_cools_toward_ambient_when_stopped(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        """Nip temp should approach ambient (20) when press stopped."""
        _set_press_speed(store, 0.0)
        # Run many ticks — first-order lag starts at initial_value=20 and targets ambient=20
        results_list = _run_ticks(laminator, store, n_ticks=50, dt=0.1)
        nip = _find_signal(results_list[-1], "laminator.nip_temp").value
        # Should be near ambient (20) since setpoint=ambient and initial=20
        assert 15.0 <= nip <= 30.0, f"Nip temp should be near ambient when stopped: {nip}"

    def test_tunnel_temp_cools_toward_ambient_when_stopped(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        """Tunnel temp should approach ambient (20) when press stopped."""
        _set_press_speed(store, 0.0)
        results_list = _run_ticks(laminator, store, n_ticks=50, dt=0.1)
        tunnel = _find_signal(results_list[-1], "laminator.tunnel_temp").value
        assert 15.0 <= tunnel <= 30.0, f"Tunnel temp should be near ambient when stopped: {tunnel}"


# ---------------------------------------------------------------------------
# Tests: active state (press speed > 0)
# ---------------------------------------------------------------------------


class TestActiveState:
    """When press is running (speed > 0), laminator produces active values."""

    def test_web_speed_tracks_press(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        """Web speed should correlate with press line speed."""
        _set_press_speed(store, 200.0)
        results_list = _run_ticks(laminator, store, n_ticks=10, dt=0.1)
        ws = _find_signal(results_list[-1], "laminator.web_speed").value
        # base=0, gain=1.0 * 200 = 200, plus noise
        assert ws > 100.0, f"Web speed should follow press speed: {ws}"

    def test_web_speed_higher_at_higher_press_speed(
        self, store: SignalStore, rng: np.random.Generator,
    ) -> None:
        """Higher press speed -> higher web speed."""
        cfg = _make_laminator_config()

        # Run at 100 m/min
        gen_slow = LaminatorGenerator("laminator", cfg, np.random.default_rng(42))
        _set_press_speed(store, 100.0)
        results_slow = _run_ticks(gen_slow, store, n_ticks=10, dt=0.1)
        ws_slow = _find_signal(results_slow[-1], "laminator.web_speed").value

        # Fresh store for second run
        store2 = SignalStore()
        gen_fast = LaminatorGenerator("laminator", cfg, np.random.default_rng(42))
        _set_press_speed(store2, 400.0)
        results_fast = _run_ticks(gen_fast, store2, n_ticks=10, dt=0.1)
        ws_fast = _find_signal(results_fast[-1], "laminator.web_speed").value

        assert ws_fast > ws_slow, (
            f"Higher press speed should give higher web speed: {ws_slow} vs {ws_fast}"
        )

    def test_nip_temp_approaches_setpoint_when_active(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        """Nip temp should approach setpoint (55) when active."""
        _set_press_speed(store, 200.0)
        # Run many ticks for first-order lag to approach setpoint
        results_list = _run_ticks(laminator, store, n_ticks=200, dt=0.1)
        nip = _find_signal(results_list[-1], "laminator.nip_temp").value
        # After 20s of lag with tau=120, value should have risen above ambient
        assert nip > 22.0, f"Nip temp should rise toward setpoint when active: {nip}"

    def test_nip_pressure_active_when_running(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        """Nip pressure should be near target (4.0) when running."""
        _set_press_speed(store, 200.0)
        results_list = _run_ticks(laminator, store, n_ticks=10, dt=0.1)
        np_ = _find_signal(results_list[-1], "laminator.nip_pressure").value
        assert 2.0 <= np_ <= 6.0, f"Nip pressure should be near target when running: {np_}"

    def test_tunnel_temp_approaches_setpoint_when_active(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        """Tunnel temp should approach setpoint (65) when active."""
        _set_press_speed(store, 200.0)
        results_list = _run_ticks(laminator, store, n_ticks=200, dt=0.1)
        tunnel = _find_signal(results_list[-1], "laminator.tunnel_temp").value
        assert tunnel > 22.0, f"Tunnel temp should rise toward setpoint when active: {tunnel}"

    def test_adhesive_weight_active_when_running(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        """Adhesive weight should be near target (5.0) when running."""
        _set_press_speed(store, 200.0)
        results_list = _run_ticks(laminator, store, n_ticks=10, dt=0.1)
        aw = _find_signal(results_list[-1], "laminator.adhesive_weight").value
        assert 2.0 <= aw <= 8.0, f"Adhesive weight should be near target when running: {aw}"


# ---------------------------------------------------------------------------
# Tests: all signals present per tick
# ---------------------------------------------------------------------------


class TestAllSignals:
    """Every tick produces exactly 5 signals."""

    def test_signal_count_per_tick(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        _set_press_speed(store, 200.0)
        results = laminator.generate(0.1, 0.1, store)
        assert len(results) == 5

    def test_all_signals_have_quality_good(
        self, laminator: LaminatorGenerator, store: SignalStore,
    ) -> None:
        _set_press_speed(store, 200.0)
        results = laminator.generate(0.1, 0.1, store)
        for sv in results:
            assert sv.quality == "good"


# ---------------------------------------------------------------------------
# Tests: determinism (CLAUDE.md Rule 13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed -> identical output sequence."""

    def test_laminator_deterministic(self, store: SignalStore) -> None:
        cfg = _make_laminator_config()
        gen1 = LaminatorGenerator("laminator", cfg, np.random.default_rng(99))
        gen2 = LaminatorGenerator("laminator", cfg, np.random.default_rng(99))

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
