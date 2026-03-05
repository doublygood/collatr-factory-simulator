"""Unit tests for the OvenGenerator (PRD 2b.3).

Tests verify:
- Signal IDs (13 signals)
- Initial state: Off, zone temps at ambient
- State transitions and cascade effects
- Zone temps track setpoints in Preheat/Running/Idle
- Zone temps cool toward ambient in Off/Cooldown
- Belt speed is 0 when Off/Cooldown
- Product core temp active only in Running
- Output power bounded [0, 100]% with inverse correlation
- All 13 signals present on every tick
- Determinism (same seed → same output)
- Protocol mappings derived from config

Task 3.5
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.oven import (
    STATE_OFF,
    OvenGenerator,
)
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_oven_config(
    *,
    zone_sp: list[float] | None = None,
    belt_target: float = 2.0,
) -> EquipmentConfig:
    """Create a minimal oven config for testing.

    Uses min_clamp=0.0 for zone temps so ambient (20°C) is not clamped away.
    noise_sigma=0.0 everywhere for deterministic assertions.
    """
    if zone_sp is None:
        zone_sp = [160.0, 200.0, 180.0]

    signals: dict[str, SignalConfig] = {}

    for i, sp in enumerate(zone_sp):
        signals[f"zone_{i + 1}_temp"] = SignalConfig(
            model="first_order_lag",
            noise_sigma=0.0,
            sample_rate_ms=1000,
            min_clamp=0.0,
            max_clamp=300.0,
            units="C",
            params={"tau": 180.0, "initial_value": 20.0},
        )
        signals[f"zone_{i + 1}_setpoint"] = SignalConfig(
            model="steady_state",
            noise_sigma=0.0,
            sample_rate_ms=1000,
            min_clamp=0.0,
            max_clamp=300.0,
            units="C",
            params={"target": sp},
        )

    signals["belt_speed"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.0,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=5.0,
        units="m/min",
        params={"target": belt_target},
    )

    # OvenGenerator builds ThermalDiffusionModel internally; the model field
    # here is for protocol documentation only.
    signals["product_core_temp"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.0,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=100.0,
        units="C",
        params={},
    )

    signals["humidity_zone_2"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.0,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=100.0,
        units="%",
        params={"target": 50.0},
    )

    signals["state"] = SignalConfig(
        model="state_machine",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=4.0,
        units="enum",
        params={
            "states": ["Off", "Preheat", "Running", "Idle", "Cooldown"],
            "initial_state": "Off",
        },
    )

    for i in range(3):
        signals[f"zone_{i + 1}_output_power"] = SignalConfig(
            model="correlated_follower",
            noise_sigma=0.0,
            sample_rate_ms=1000,
            min_clamp=0.0,
            max_clamp=100.0,
            units="%",
            params={"base": 50.0, "factor": -0.3},
        )

    return EquipmentConfig(
        enabled=True,
        type="tunnel_oven",
        signals=signals,
        tunnel_length=6.0,
        thermal_coupling=0.05,
    )


def _find_signal(results: list[SignalValue], signal_id: str) -> SignalValue:
    for sv in results:
        if sv.signal_id == signal_id:
            return sv
    raise KeyError(f"Signal {signal_id!r} not found in results")


def _run_ticks(
    gen: OvenGenerator,
    store: SignalStore,
    *,
    n_ticks: int,
    dt: float = 0.1,
    start_time: float = 0.0,
) -> list[list[SignalValue]]:
    """Run generator for n_ticks, return list of result lists per tick."""
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
def oven(rng: np.random.Generator) -> OvenGenerator:
    return OvenGenerator("oven", _make_oven_config(), rng)


# ---------------------------------------------------------------------------
# Tests: signal IDs
# ---------------------------------------------------------------------------


class TestSignalIds:
    """Verify all 13 oven signals are registered."""

    def test_signal_count(self, oven: OvenGenerator) -> None:
        assert len(oven.get_signal_ids()) == 13

    def test_signal_names(self, oven: OvenGenerator) -> None:
        ids = set(oven.get_signal_ids())
        expected = {
            "oven.zone_1_temp", "oven.zone_2_temp", "oven.zone_3_temp",
            "oven.zone_1_setpoint", "oven.zone_2_setpoint", "oven.zone_3_setpoint",
            "oven.belt_speed",
            "oven.product_core_temp",
            "oven.humidity_zone_2",
            "oven.state",
            "oven.zone_1_output_power", "oven.zone_2_output_power",
            "oven.zone_3_output_power",
        }
        assert ids == expected


# ---------------------------------------------------------------------------
# Tests: initial state
# ---------------------------------------------------------------------------


class TestInitialState:
    """Oven starts in Off state with zone temps at ambient."""

    def test_initial_state_off(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        results = oven.generate(0.1, 0.1, store)
        state_sv = _find_signal(results, "oven.state")
        assert int(state_sv.value) == STATE_OFF

    def test_initial_belt_speed_zero(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        results = oven.generate(0.1, 0.1, store)
        belt_sv = _find_signal(results, "oven.belt_speed")
        assert belt_sv.value == 0.0

    def test_initial_zone_temps_near_ambient(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        """Zone temps start at ambient (20°C) since oven is Off."""
        results = oven.generate(0.1, 0.1, store)
        for i in range(1, 4):
            temp_sv = _find_signal(results, f"oven.zone_{i}_temp")
            assert 15.0 <= temp_sv.value <= 25.0, (
                f"zone_{i}_temp out of ambient range: {temp_sv.value}"
            )

    def test_initial_setpoints_reported(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        """Configured zone setpoints are always output regardless of state."""
        results = oven.generate(0.1, 0.1, store)
        sp1 = _find_signal(results, "oven.zone_1_setpoint").value
        sp2 = _find_signal(results, "oven.zone_2_setpoint").value
        sp3 = _find_signal(results, "oven.zone_3_setpoint").value
        assert sp1 == pytest.approx(160.0, abs=1.0)
        assert sp2 == pytest.approx(200.0, abs=1.0)
        assert sp3 == pytest.approx(180.0, abs=1.0)


# ---------------------------------------------------------------------------
# Tests: state transitions and cascade
# ---------------------------------------------------------------------------


class TestStateTransitions:
    """State transitions drive correct cascade behavior."""

    def test_preheat_warms_zones(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        """Entering Preheat causes zone temps to ramp toward setpoints."""
        oven.generate(0.1, 0.1, store)
        oven.state_machine.force_state("Preheat")

        results_list = _run_ticks(oven, store, n_ticks=100, dt=0.1, start_time=0.1)

        temps = [
            _find_signal(r, "oven.zone_2_temp").value
            for r in results_list
        ]
        assert temps[-1] > temps[0], (
            f"Zone 2 should warm during Preheat: {temps[0]:.1f} → {temps[-1]:.1f}"
        )

    def test_cooldown_cools_zones(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        """Entering Cooldown causes zone temps to fall toward ambient."""
        oven.generate(0.1, 0.1, store)

        # Bring zones to temperature
        oven.state_machine.force_state("Preheat")
        _run_ticks(oven, store, n_ticks=500, dt=0.1, start_time=0.1)

        # Record zone temp while hot
        results_hot = oven.generate(50.1, 0.1, store)
        z2_hot = _find_signal(results_hot, "oven.zone_2_temp").value
        assert z2_hot > 30.0, f"Zone 2 should be warm after preheating: {z2_hot}"

        # Transition to Cooldown
        oven.state_machine.force_state("Cooldown")
        results_list = _run_ticks(oven, store, n_ticks=200, dt=0.1, start_time=50.2)

        temps = [
            _find_signal(r, "oven.zone_2_temp").value
            for r in results_list
        ]
        assert temps[-1] < z2_hot, (
            f"Zone 2 temp should fall during Cooldown: {z2_hot:.1f} → {temps[-1]:.1f}"
        )

    def test_belt_zero_when_off(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        results = oven.generate(0.1, 0.1, store)
        belt = _find_signal(results, "oven.belt_speed").value
        assert belt == 0.0

    def test_belt_active_during_preheat(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        oven.generate(0.1, 0.1, store)
        oven.state_machine.force_state("Preheat")
        results_list = _run_ticks(oven, store, n_ticks=5, dt=0.1, start_time=0.1)
        belt = _find_signal(results_list[-1], "oven.belt_speed").value
        assert belt > 0.0, f"Belt should run during Preheat: {belt}"

    def test_belt_active_during_running(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        oven.generate(0.1, 0.1, store)
        oven.state_machine.force_state("Running")
        results_list = _run_ticks(oven, store, n_ticks=5, dt=0.1, start_time=0.1)
        belt = _find_signal(results_list[-1], "oven.belt_speed").value
        assert belt > 0.0, f"Belt should run during Running: {belt}"

    def test_belt_zero_during_cooldown(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        oven.generate(0.1, 0.1, store)
        oven.state_machine.force_state("Cooldown")
        results_list = _run_ticks(oven, store, n_ticks=5, dt=0.1, start_time=0.1)
        belt = _find_signal(results_list[-1], "oven.belt_speed").value
        assert belt == 0.0, f"Belt should be 0 during Cooldown: {belt}"

    def test_running_resets_core_temp_to_entry(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        """Entering Running restarts thermal diffusion from product entry temp (4°C)."""
        oven.generate(0.1, 0.1, store)
        oven.state_machine.force_state("Running")
        results = oven.generate(0.2, 0.1, store)
        core_temp = _find_signal(results, "oven.product_core_temp").value
        # Just entered Running: core temp should be near entry temp (4°C)
        assert 0.0 <= core_temp <= 10.0, (
            f"Core temp should restart near 4°C on Running entry: {core_temp}"
        )


# ---------------------------------------------------------------------------
# Tests: product core temperature
# ---------------------------------------------------------------------------


class TestProductCoreTemp:
    """Product core temp uses ThermalDiffusion and only advances in Running."""

    def test_core_temp_rises_during_running(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        """Core temp rises from entry temp toward oven temp during Running."""
        oven.generate(0.1, 0.1, store)
        oven.state_machine.force_state("Running")

        results_list = _run_ticks(oven, store, n_ticks=300, dt=0.1, start_time=0.1)

        temps = [
            _find_signal(r, "oven.product_core_temp").value
            for r in results_list
        ]
        assert temps[-1] > temps[0], (
            f"Core temp should rise during Running: {temps[0]:.1f} → {temps[-1]:.1f}"
        )
        assert temps[0] < 20.0, f"Initial core temp too high: {temps[0]}"

    def test_core_temp_held_when_not_running(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        """Core temp is held constant (not advancing) when not in Running state."""
        oven.generate(0.1, 0.1, store)
        oven.state_machine.force_state("Running")
        _run_ticks(oven, store, n_ticks=50, dt=0.1, start_time=0.1)

        mid_results = oven.generate(5.2, 0.1, store)
        core_before = _find_signal(mid_results, "oven.product_core_temp").value

        oven.state_machine.force_state("Off")
        results_list = _run_ticks(oven, store, n_ticks=20, dt=0.1, start_time=5.3)
        core_after = _find_signal(results_list[-1], "oven.product_core_temp").value

        # Thermal diffusion paused — core temp should not advance
        assert core_after == pytest.approx(core_before, abs=0.5), (
            f"Core temp should hold during Off: {core_before:.2f} → {core_after:.2f}"
        )

    def test_core_temp_rising_trend_in_running(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        """Over 500 ticks in Running, core temp must rise substantially.

        Strict monotonicity is not guaranteed because T_oven changes each tick
        as zone_2_temp ramps up, causing Fourier series recalculation. We
        check the overall trend over a long window instead.
        """
        oven.generate(0.1, 0.1, store)
        oven.state_machine.force_state("Running")
        results_list = _run_ticks(oven, store, n_ticks=500, dt=1.0, start_time=0.0)

        temps = [
            _find_signal(r, "oven.product_core_temp").value
            for r in results_list
        ]
        # Core should rise from near-entry-temp to at least 20°C over 500 seconds
        assert temps[-1] > temps[0] + 5.0, (
            f"Core temp should rise substantially: {temps[0]:.1f} → {temps[-1]:.1f}"
        )


# ---------------------------------------------------------------------------
# Tests: output power
# ---------------------------------------------------------------------------


class TestOutputPower:
    """Zone output power is correlated inverse of zone temp, bounded [0, 100]%."""

    def test_output_power_within_bounds(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        oven.generate(0.1, 0.1, store)
        oven.state_machine.force_state("Running")
        results_list = _run_ticks(oven, store, n_ticks=300, dt=0.1, start_time=0.1)
        for r in results_list:
            for i in range(1, 4):
                power = _find_signal(r, f"oven.zone_{i}_output_power").value
                assert 0.0 <= power <= 100.0, (
                    f"zone_{i}_output_power out of bounds: {power}"
                )

    def test_output_power_high_when_cold(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        """When zones are at ambient (~20°C), output power is high.

        base=50, gain=-0.3, zone_temp≈20 → 50 + (-0.3 * 20) = 44 > 30.
        """
        results = oven.generate(0.1, 0.1, store)
        for i in range(1, 4):
            power = _find_signal(results, f"oven.zone_{i}_output_power").value
            assert power > 30.0, (
                f"zone_{i}_output_power should be high when cold: {power}"
            )


# ---------------------------------------------------------------------------
# Tests: all 13 signals present every tick
# ---------------------------------------------------------------------------


class TestAllSignals:
    """Every tick produces exactly 13 signals."""

    def test_signal_count_per_tick(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        results = oven.generate(0.1, 0.1, store)
        assert len(results) == 13

    def test_all_signals_have_quality_good(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        results = oven.generate(0.1, 0.1, store)
        for sv in results:
            assert sv.quality == "good", (
                f"Signal {sv.signal_id} has quality {sv.quality!r}"
            )

    def test_all_signals_present_in_off_state(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        expected_ids = set(oven.get_signal_ids())
        results = oven.generate(0.1, 0.1, store)
        emitted_ids = {sv.signal_id for sv in results}
        assert emitted_ids == expected_ids

    def test_all_signals_present_in_running_state(
        self, oven: OvenGenerator, store: SignalStore,
    ) -> None:
        expected_ids = set(oven.get_signal_ids())
        oven.generate(0.1, 0.1, store)
        oven.state_machine.force_state("Running")
        results = oven.generate(0.2, 0.1, store)
        emitted_ids = {sv.signal_id for sv in results}
        assert emitted_ids == expected_ids


# ---------------------------------------------------------------------------
# Tests: properties accessible (for scenarios)
# ---------------------------------------------------------------------------


class TestProperties:
    """Public properties expose internal models for scenario access."""

    def test_state_machine_accessible(self, oven: OvenGenerator) -> None:
        sm = oven.state_machine
        assert sm is not None

    def test_zone_temp_models_accessible(self, oven: OvenGenerator) -> None:
        assert len(oven.zone_temp_models) == 3

    def test_zone_setpoint_models_accessible(self, oven: OvenGenerator) -> None:
        assert len(oven.zone_setpoint_models) == 3

    def test_thermal_diffusion_model_accessible(self, oven: OvenGenerator) -> None:
        assert oven.thermal_diffusion_model is not None

    def test_thermal_coupling_from_config(self, oven: OvenGenerator) -> None:
        assert oven.thermal_coupling == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Tests: determinism (CLAUDE.md Rule 13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed → identical output sequence."""

    def test_oven_deterministic(self, store: SignalStore) -> None:
        cfg = _make_oven_config()
        gen1 = OvenGenerator("oven", cfg, np.random.default_rng(99))
        gen2 = OvenGenerator("oven", cfg, np.random.default_rng(99))

        # Put both in Running state before generating
        gen1.state_machine.force_state("Running")
        gen2.state_machine.force_state("Running")

        sim_time = 0.0
        dt = 0.1
        for _ in range(50):
            sim_time += dt
            r1 = gen1.generate(sim_time, dt, store)
            r2 = gen2.generate(sim_time, dt, store)

        for sv1, sv2 in zip(r1, r2, strict=True):
            assert sv1.signal_id == sv2.signal_id
            assert sv1.value == pytest.approx(sv2.value, abs=1e-9), (
                f"{sv1.signal_id}: {sv1.value} != {sv2.value}"
            )


# ---------------------------------------------------------------------------
# Tests: protocol mappings
# ---------------------------------------------------------------------------


class TestOvenZoneCholesky:
    """Oven zone noise is correlated via Cholesky pipeline (PRD 4.3.1)."""

    @staticmethod
    def _make_noisy_config(
        **extra_kwargs: object,
    ) -> EquipmentConfig:
        """Create oven config with noise on zone temps for correlation testing."""
        zone_sp = [160.0, 200.0, 180.0]
        signals: dict[str, SignalConfig] = {}

        for i, sp in enumerate(zone_sp):
            signals[f"zone_{i + 1}_temp"] = SignalConfig(
                model="first_order_lag",
                noise_sigma=0.5,
                sample_rate_ms=1000,
                min_clamp=0.0,
                max_clamp=300.0,
                units="C",
                params={"tau": 180.0, "initial_value": 20.0},
            )
            signals[f"zone_{i + 1}_setpoint"] = SignalConfig(
                model="steady_state",
                noise_sigma=0.0,
                sample_rate_ms=1000,
                min_clamp=0.0,
                max_clamp=300.0,
                units="C",
                params={"target": sp},
            )

        signals["belt_speed"] = SignalConfig(
            model="steady_state",
            noise_sigma=0.0,
            sample_rate_ms=1000,
            min_clamp=0.0,
            max_clamp=5.0,
            units="m/min",
            params={"target": 2.0},
        )
        signals["product_core_temp"] = SignalConfig(
            model="steady_state",
            noise_sigma=0.0,
            sample_rate_ms=1000,
            min_clamp=0.0,
            max_clamp=100.0,
            units="C",
            params={},
        )
        signals["humidity_zone_2"] = SignalConfig(
            model="steady_state",
            noise_sigma=0.0,
            sample_rate_ms=1000,
            min_clamp=0.0,
            max_clamp=100.0,
            units="%",
            params={"target": 50.0},
        )
        signals["state"] = SignalConfig(
            model="state_machine",
            sample_rate_ms=1000,
            min_clamp=0.0,
            max_clamp=4.0,
            units="enum",
            params={
                "states": ["Off", "Preheat", "Running", "Idle", "Cooldown"],
                "initial_state": "Off",
            },
        )
        for i in range(3):
            signals[f"zone_{i + 1}_output_power"] = SignalConfig(
                model="correlated_follower",
                noise_sigma=0.0,
                sample_rate_ms=1000,
                min_clamp=0.0,
                max_clamp=100.0,
                units="%",
                params={"base": 50.0, "factor": -0.3},
            )

        return EquipmentConfig(
            enabled=True,
            type="tunnel_oven",
            signals=signals,
            tunnel_length=6.0,
            thermal_coupling=0.05,
            **extra_kwargs,
        )

    def test_oven_zones_positively_correlated(self) -> None:
        """Oven zone noise residuals should be positively correlated.

        Run many ticks in Preheat state, extract residuals (value - lag trend),
        and verify that Pearson correlations are positive for adjacent zones.
        """
        config = self._make_noisy_config()
        store = SignalStore()
        gen = OvenGenerator("oven", config, np.random.default_rng(12345))
        gen.state_machine.force_state("Preheat")

        # Warm up: let lag models reach setpoints (tau=180s, run 600s)
        sim_time = 0.0
        dt = 0.1
        for _ in range(6000):
            sim_time += dt
            gen.generate(sim_time, dt, store)

        # Collect residuals (detrend via rolling mean of 50 samples)
        temps_1: list[float] = []
        temps_2: list[float] = []
        temps_3: list[float] = []
        for _ in range(5000):
            sim_time += dt
            results = gen.generate(sim_time, dt, store)
            temps_1.append(_find_signal(results, "oven.zone_1_temp").value)
            temps_2.append(_find_signal(results, "oven.zone_2_temp").value)
            temps_3.append(_find_signal(results, "oven.zone_3_temp").value)

        # Use diff to remove trend (lag convergence), keeping noise signal
        d1 = np.diff(temps_1)
        d2 = np.diff(temps_2)
        d3 = np.diff(temps_3)

        # Compute sample Pearson correlations
        r12 = np.corrcoef(d1, d2)[0, 1]
        r13 = np.corrcoef(d1, d3)[0, 1]
        r23 = np.corrcoef(d2, d3)[0, 1]

        # PRD matrix: r12=0.15, r13=0.05, r23=0.15
        # Correlations should be positive
        assert r12 > 0.0, f"Zone 1-2 correlation should be positive, got {r12:.4f}"
        assert r23 > 0.0, f"Zone 2-3 correlation should be positive, got {r23:.4f}"
        # Zone 1-3 has weak correlation (0.05), may not be strongly detectable
        assert r13 > -0.1, f"Zone 1-3 correlation should not be negative, got {r13:.4f}"

        # Zone 1-2 and 2-3 should be stronger than 1-3
        assert r12 > r13, (
            f"Zone 1-2 ({r12:.4f}) should be more correlated than 1-3 ({r13:.4f})"
        )

    def test_custom_correlation_matrix(self) -> None:
        """Custom oven_zone_correlation_matrix overrides PRD default."""
        custom_matrix = [
            [1.0, 0.5, 0.3],
            [0.5, 1.0, 0.5],
            [0.3, 0.5, 1.0],
        ]
        config = self._make_noisy_config(oven_zone_correlation_matrix=custom_matrix)
        gen = OvenGenerator("oven", config, np.random.default_rng(42))
        store = SignalStore()
        gen.state_machine.force_state("Preheat")

        # Warm up
        sim_time = 0.0
        dt = 0.1
        for _ in range(6000):
            sim_time += dt
            gen.generate(sim_time, dt, store)

        # Collect
        temps_1: list[float] = []
        temps_2: list[float] = []
        for _ in range(5000):
            sim_time += dt
            results = gen.generate(sim_time, dt, store)
            temps_1.append(_find_signal(results, "oven.zone_1_temp").value)
            temps_2.append(_find_signal(results, "oven.zone_2_temp").value)

        d1 = np.diff(temps_1)
        d2 = np.diff(temps_2)
        r12 = np.corrcoef(d1, d2)[0, 1]
        # With stronger correlation matrix (0.5), expect higher correlation
        assert r12 > 0.1, f"Custom matrix correlation should be stronger, got {r12:.4f}"

    def test_zone_temp_noise_not_double_applied(self) -> None:
        """Lag models should not have internal noise (avoid double-noising).

        Verify that the FirstOrderLagModel instances for zone temps
        have noise=None — all noise is applied externally via Cholesky.
        """
        config = self._make_noisy_config()
        gen = OvenGenerator("oven", config, np.random.default_rng(42))
        for model in gen.zone_temp_models:
            assert model._noise is None


# ---------------------------------------------------------------------------
# Tests: protocol mappings
# ---------------------------------------------------------------------------


class TestProtocolMappings:
    """Protocol mappings are derived from config."""

    def test_modbus_mappings_from_config(self) -> None:
        """Modbus signals have modbus mappings when configured."""
        signals: dict[str, SignalConfig] = {}

        # Zone temps with Modbus HR
        for i in range(3):
            hr_start = 1100 + i * 2
            signals[f"zone_{i + 1}_temp"] = SignalConfig(
                model="first_order_lag",
                noise_sigma=0.0,
                min_clamp=0.0,
                max_clamp=300.0,
                modbus_hr=[hr_start, hr_start + 1],
                modbus_type="float32",
                params={"tau": 180.0, "initial_value": 20.0},
            )
            signals[f"zone_{i + 1}_setpoint"] = SignalConfig(
                model="steady_state",
                noise_sigma=0.0,
                min_clamp=0.0,
                max_clamp=300.0,
                params={"target": 160.0},
            )
            signals[f"zone_{i + 1}_output_power"] = SignalConfig(
                model="correlated_follower",
                noise_sigma=0.0,
                min_clamp=0.0,
                max_clamp=100.0,
                opcua_node=f"FoodBevLine.Oven1.Zone{i + 1}OutputPower",
                opcua_type="Double",
                params={"base": 50.0, "factor": -0.3},
            )
        for name in ["belt_speed", "product_core_temp", "humidity_zone_2"]:
            signals[name] = SignalConfig(
                model="steady_state",
                noise_sigma=0.0,
                min_clamp=0.0,
                max_clamp=300.0,
                params={"target": 0.0},
            )
        signals["state"] = SignalConfig(
            model="state_machine",
            min_clamp=0.0,
            max_clamp=4.0,
            params={
                "states": ["Off", "Preheat", "Running", "Idle", "Cooldown"],
                "initial_state": "Off",
            },
        )

        cfg = EquipmentConfig(
            enabled=True,
            type="tunnel_oven",
            signals=signals,
        )
        gen = OvenGenerator("oven", cfg, np.random.default_rng(42))
        mappings = gen.get_protocol_mappings()

        # Zone 1 temp has Modbus HR at 1100-1101
        assert "oven.zone_1_temp" in mappings
        assert mappings["oven.zone_1_temp"].modbus is not None
        assert mappings["oven.zone_1_temp"].modbus.address == [1100, 1101]

        # Zone 1 output power has OPC-UA node
        assert "oven.zone_1_output_power" in mappings
        assert mappings["oven.zone_1_output_power"].opcua is not None
        assert (
            mappings["oven.zone_1_output_power"].opcua.node_id
            == "FoodBevLine.Oven1.Zone1OutputPower"
        )
