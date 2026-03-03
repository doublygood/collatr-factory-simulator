"""Unit tests for the SealerGenerator (PRD 2b.5).

Tests verify:
- All 6 signals are produced with correct IDs
- seal_temp converges toward target when filler is Running
- seal_temp decays toward ambient when filler is not Running
- seal_pressure is nominal when active, 0.0 when inactive
- seal_dwell is always generated at target
- gas_co2_pct / gas_n2_pct are always generated (hold when inactive)
- vacuum_level is nominal when active, 0.0 when inactive
- Signal values respect min/max clamps
- Determinism (same seed → same output)

Task 3.7
"""

from __future__ import annotations

import numpy as np

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.sealer import (
    _AMBIENT_TEMP_C,
    _DEFAULT_GAS_CO2_PCT,
    _DEFAULT_GAS_N2_PCT,
    _DEFAULT_SEAL_DWELL_S,
    _DEFAULT_SEAL_PRESSURE_BAR,
    _DEFAULT_SEAL_TEMP_C,
    _DEFAULT_VACUUM_BAR,
    SealerGenerator,
)
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sealer_config(
    *,
    seal_temp_target: float = _DEFAULT_SEAL_TEMP_C,
    seal_pressure_target: float = _DEFAULT_SEAL_PRESSURE_BAR,
    seal_dwell_target: float = _DEFAULT_SEAL_DWELL_S,
    gas_co2_target: float = _DEFAULT_GAS_CO2_PCT,
    gas_n2_target: float = _DEFAULT_GAS_N2_PCT,
    vacuum_target: float = _DEFAULT_VACUUM_BAR,
) -> EquipmentConfig:
    """Create a minimal sealer config for testing."""
    signals: dict[str, SignalConfig] = {}

    signals["seal_temp"] = SignalConfig(
        model="steady_state",
        noise_sigma=1.5,
        noise_type="ar1",
        noise_phi=0.6,
        sample_rate_ms=5000,
        min_clamp=100.0,
        max_clamp=250.0,
        units="C",
        params={"target": seal_temp_target},
    )
    signals["seal_pressure"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.1,
        sample_rate_ms=5000,
        min_clamp=1.0,
        max_clamp=6.0,
        units="bar",
        params={"target": seal_pressure_target},
    )
    signals["seal_dwell"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.05,
        sample_rate_ms=5000,
        min_clamp=0.5,
        max_clamp=5.0,
        units="s",
        params={"target": seal_dwell_target},
    )
    signals["gas_co2_pct"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.5,
        sample_rate_ms=10000,
        min_clamp=20.0,
        max_clamp=80.0,
        units="%",
        params={"target": gas_co2_target},
    )
    signals["gas_n2_pct"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.5,
        sample_rate_ms=10000,
        min_clamp=20.0,
        max_clamp=80.0,
        units="%",
        params={"target": gas_n2_target},
    )
    signals["vacuum_level"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.02,
        sample_rate_ms=5000,
        min_clamp=-0.9,
        max_clamp=0.0,
        units="bar",
        params={"target": vacuum_target},
    )

    return EquipmentConfig(enabled=True, type="tray_sealer", signals=signals)


def _make_store(filler_state: float | None) -> SignalStore:
    """Create a store with an optional filler.state value."""
    store = SignalStore()
    if filler_state is not None:
        store.set("filler.state", filler_state, 0.0, "good")
    return store


def _make_sealer(seed: int = 42) -> SealerGenerator:
    rng = np.random.default_rng(seed)
    return SealerGenerator("sealer", _make_sealer_config(), rng)


def _tick(
    gen: SealerGenerator, store: SignalStore, n: int = 1, dt: float = 0.1
) -> list[SignalValue]:
    """Run n ticks and return results from the last tick."""
    results: list[SignalValue] = []
    for i in range(n):
        results = gen.generate(float(i) * dt, dt, store)
    return results


def _sv_dict(results: list[SignalValue]) -> dict[str, float]:
    return {sv.signal_id: sv.value for sv in results}


# ---------------------------------------------------------------------------
# Signal identity
# ---------------------------------------------------------------------------


def test_signal_count():
    gen = _make_sealer()
    store = _make_store(2.0)  # Running
    results = gen.generate(0.0, 0.1, store)
    assert len(results) == 6


def test_signal_ids():
    gen = _make_sealer()
    ids = gen.get_signal_ids()
    assert set(ids) == {
        "sealer.seal_temp",
        "sealer.seal_pressure",
        "sealer.seal_dwell",
        "sealer.gas_co2_pct",
        "sealer.gas_n2_pct",
        "sealer.vacuum_level",
    }


def test_signal_ids_match_generate():
    gen = _make_sealer()
    store = _make_store(2.0)
    results = gen.generate(0.0, 0.1, store)
    produced_ids = {sv.signal_id for sv in results}
    assert produced_ids == set(gen.get_signal_ids())


# ---------------------------------------------------------------------------
# Seal temp behaviour
# ---------------------------------------------------------------------------


def test_seal_temp_converges_when_active():
    """seal_temp should increase toward target when filler is Running."""
    gen = _make_sealer()
    store = _make_store(2.0)  # Running
    # Run many ticks; temp should move from ambient toward target
    for i in range(200):
        results = gen.generate(float(i) * 0.1, 0.1, store)
    values = _sv_dict(results)
    # After 20 s at τ=180 s, should have moved more than halfway from ambient
    assert values["sealer.seal_temp"] > _AMBIENT_TEMP_C + 10.0


def test_seal_temp_decays_when_inactive():
    """seal_temp internal state should decay toward ambient when filler is not Running."""
    gen = _make_sealer()

    # First warm up the sealer (500 ticks x 0.1 s = 50 s, tau=180 s -> ~39 C above ambient)
    store_running = _make_store(2.0)
    for i in range(500):
        gen.generate(float(i) * 0.1, 0.1, store_running)
    warmed_temp = gen._seal_temp_current
    assert warmed_temp > _AMBIENT_TEMP_C + 20.0  # Should be meaningfully above ambient

    # Now switch to inactive for another 50 s
    store_off = _make_store(0.0)  # Off
    for i in range(500):
        gen.generate(float(i) * 0.1, 0.1, store_off)
    cooled_temp = gen._seal_temp_current
    # Internal state should have decayed from warmed value
    assert cooled_temp < warmed_temp


def test_seal_temp_approaches_ambient_from_cold():
    """When started cold (inactive), seal_temp internal state stays near ambient."""
    gen = _make_sealer()
    store = _make_store(0.0)  # Off
    # Initial internal state is ambient; one inactive tick keeps it near ambient
    gen.generate(0.0, 0.1, store)
    # Check the internal continuous state (not the clamped output value)
    assert gen._seal_temp_current < _AMBIENT_TEMP_C + 5.0


# ---------------------------------------------------------------------------
# Pressure and vacuum: active vs inactive
# ---------------------------------------------------------------------------


def test_seal_pressure_is_nonzero_when_active():
    gen = _make_sealer()
    store = _make_store(2.0)  # Running
    # Run a few ticks to let the generator stabilise
    results = _tick(gen, store, n=5)
    values = _sv_dict(results)
    assert values["sealer.seal_pressure"] > 0.0


def test_seal_pressure_is_zero_when_inactive():
    gen = _make_sealer()
    store = _make_store(0.0)  # Off
    results = gen.generate(0.0, 0.1, store)
    values = _sv_dict(results)
    assert values["sealer.seal_pressure"] == 0.0


def test_vacuum_level_is_negative_when_active():
    gen = _make_sealer()
    store = _make_store(2.0)  # Running
    results = _tick(gen, store, n=5)
    values = _sv_dict(results)
    assert values["sealer.vacuum_level"] < 0.0


def test_vacuum_level_is_zero_when_inactive():
    gen = _make_sealer()
    store = _make_store(0.0)  # Off
    results = gen.generate(0.0, 0.1, store)
    values = _sv_dict(results)
    assert values["sealer.vacuum_level"] == 0.0


# ---------------------------------------------------------------------------
# Dwell time: always generated
# ---------------------------------------------------------------------------


def test_seal_dwell_generated_when_active():
    gen = _make_sealer()
    store = _make_store(2.0)
    results = gen.generate(0.0, 0.1, store)
    values = _sv_dict(results)
    assert values["sealer.seal_dwell"] > 0.0


def test_seal_dwell_generated_when_inactive():
    gen = _make_sealer()
    store = _make_store(0.0)
    results = gen.generate(0.0, 0.1, store)
    values = _sv_dict(results)
    assert values["sealer.seal_dwell"] > 0.0


# ---------------------------------------------------------------------------
# Gas mix: always generated
# ---------------------------------------------------------------------------


def test_gas_always_generated():
    gen = _make_sealer()
    # Both active and inactive should produce gas mix values
    for filler_state in [0.0, 2.0]:
        store = _make_store(filler_state)
        results = gen.generate(0.0, 0.1, store)
        values = _sv_dict(results)
        # CO2 in range
        assert 20.0 <= values["sealer.gas_co2_pct"] <= 80.0
        # N2 in range
        assert 20.0 <= values["sealer.gas_n2_pct"] <= 80.0


def test_gas_co2_near_target():
    gen = _make_sealer()
    store = _make_store(2.0)
    # Run many ticks; mean should be close to target (30%)
    co2_vals = []
    for i in range(200):
        results = gen.generate(float(i) * 0.1, 0.1, store)
        values = _sv_dict(results)
        co2_vals.append(values["sealer.gas_co2_pct"])
    assert abs(np.mean(co2_vals) - _DEFAULT_GAS_CO2_PCT) < 5.0


# ---------------------------------------------------------------------------
# Clamping
# ---------------------------------------------------------------------------


def test_seal_pressure_respects_clamp():
    gen = _make_sealer()
    store = _make_store(2.0)
    for i in range(200):
        results = gen.generate(float(i) * 0.1, 0.1, store)
    values = _sv_dict(results)
    assert 1.0 <= values["sealer.seal_pressure"] <= 6.0


def test_vacuum_level_respects_clamp():
    gen = _make_sealer()
    store = _make_store(2.0)
    for i in range(200):
        results = gen.generate(float(i) * 0.1, 0.1, store)
    values = _sv_dict(results)
    assert -0.9 <= values["sealer.vacuum_level"] <= 0.0


def test_seal_temp_respects_clamp():
    gen = _make_sealer()
    store = _make_store(2.0)
    for i in range(1000):
        results = gen.generate(float(i) * 0.1, 0.1, store)
    values = _sv_dict(results)
    assert 100.0 <= values["sealer.seal_temp"] <= 250.0


# ---------------------------------------------------------------------------
# No filler state in store (graceful fallback)
# ---------------------------------------------------------------------------


def test_no_filler_state_in_store():
    """When filler.state is absent, sealer should behave as inactive."""
    gen = _make_sealer()
    store = _make_store(None)  # No filler state
    results = gen.generate(0.0, 0.1, store)
    values = _sv_dict(results)
    # Pressure and vacuum should be 0
    assert values["sealer.seal_pressure"] == 0.0
    assert values["sealer.vacuum_level"] == 0.0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_determinism():
    """Same seed → same output sequence."""
    store = _make_store(2.0)

    def run_ticks(seed: int) -> list[float]:
        rng = np.random.default_rng(seed)
        gen = SealerGenerator("sealer", _make_sealer_config(), rng)
        vals = []
        for i in range(20):
            results = gen.generate(float(i) * 0.1, 0.1, store)
            vals.extend(sv.value for sv in results)
        return vals

    run1 = run_ticks(99)
    run2 = run_ticks(99)
    assert run1 == run2


def test_different_seeds_differ():
    """Different seeds → different outputs."""
    store = _make_store(2.0)

    def run_ticks(seed: int) -> list[float]:
        rng = np.random.default_rng(seed)
        gen = SealerGenerator("sealer", _make_sealer_config(), rng)
        vals = []
        for i in range(20):
            results = gen.generate(float(i) * 0.1, 0.1, store)
            vals.extend(sv.value for sv in results)
        return vals

    run1 = run_ticks(1)
    run2 = run_ticks(2)
    assert run1 != run2
